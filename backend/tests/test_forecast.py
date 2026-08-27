"""Forecast agent and validation tests.

The theme: LLM output is untrusted input. Every test here feeds the validation
layer something a real model plausibly might return, and asserts that the
backend either accepts it for good reason or rejects it without creating an
order.
"""

from __future__ import annotations

import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.forecast_agent import (
    ForecastRequest,
    ForecastResult,
    LLMForecastProvider,
    SalesObservation,
)
from app.agents.llm_client import (
    AgentMalformedOutputError,
    AgentNotConfigured,
    AgentTransportError,
    OpenAIJSONClient,
)
from app.core.errors import (
    AgentNotConfiguredError,
    ForecastInvalidError,
    ForecastUnavailableError,
    NoSalesHistoryError,
    QuantityLimitError,
)
from app.models.audit_log import AuditAction, AuditActor, AuditLog
from app.models.order import Order
from app.services import forecast_service
from tests.fakes import FakeForecastProvider


def _actions(db: Session) -> list[str]:
    return list(db.scalars(select(AuditLog.action).order_by(AuditLog.id)).all())


# --- schema validation ------------------------------------------------------


def test_valid_output_is_accepted() -> None:
    result = ForecastResult.model_validate(
        {"recommended_quantity": 75, "reasoning": "Demand averages 20/day."}
    )

    assert result.recommended_quantity == 75


def test_string_quantity_is_rejected_not_coerced() -> None:
    """`"75"` must not silently become 75.

    A model that returns the wrong type has misunderstood the contract; quietly
    fixing it up means tolerating drift in a value that becomes a bank transfer.
    """
    with pytest.raises(ValidationError):
        ForecastResult.model_validate(
            {"recommended_quantity": "75", "reasoning": "..."}
        )


def test_float_quantity_is_rejected() -> None:
    with pytest.raises(ValidationError):
        ForecastResult.model_validate(
            {"recommended_quantity": 75.5, "reasoning": "..."}
        )


def test_missing_quantity_is_rejected() -> None:
    with pytest.raises(ValidationError):
        ForecastResult.model_validate({"reasoning": "I forgot the number."})


def test_missing_reasoning_is_rejected() -> None:
    with pytest.raises(ValidationError):
        ForecastResult.model_validate({"recommended_quantity": 75})


def test_empty_reasoning_is_rejected() -> None:
    """An unexplained recommendation is not reviewable, so it is not usable."""
    with pytest.raises(ValidationError):
        ForecastResult.model_validate({"recommended_quantity": 75, "reasoning": ""})


def test_null_quantity_is_rejected() -> None:
    with pytest.raises(ValidationError):
        ForecastResult.model_validate(
            {"recommended_quantity": None, "reasoning": "..."}
        )


# --- service-level validation ----------------------------------------------


def test_valid_forecast_is_returned_and_audited(
    db: Session, low_stock_product
) -> None:
    provider = FakeForecastProvider(quantity=75, reasoning="Steady 20/day demand.")

    result = forecast_service.generate_forecast(
        db, low_stock_product, provider=provider
    )

    assert result.quantity == 75
    assert result.reasoning == "Steady 20/day demand."
    assert result.history_days == 28
    assert result.observed_daily_average == 20.0
    assert AuditAction.FORECAST_GENERATED.value in _actions(db)


def test_forecast_audit_records_the_model_reasoning_verbatim(
    db: Session, low_stock_product
) -> None:
    reasoning = "Weekend uplift of 35% justifies a larger buffer than usual."
    provider = FakeForecastProvider(quantity=80, reasoning=reasoning)

    forecast_service.generate_forecast(db, low_stock_product, provider=provider)

    entry = db.scalar(
        select(AuditLog).where(
            AuditLog.action == AuditAction.FORECAST_GENERATED.value
        )
    )
    assert entry is not None
    assert entry.actor is AuditActor.AGENT
    assert entry.reasoning_text == reasoning


def test_forecast_is_given_the_longest_supplier_lead_time(
    db: Session, low_stock_product
) -> None:
    """Sizing to the fastest supplier would cause a stockout.

    The supplier is not chosen until after the quantity is set, so the forecast
    has to cover the slowest option the merchant might pick.
    """
    provider = FakeForecastProvider()

    forecast_service.generate_forecast(db, low_stock_product, provider=provider)

    assert provider.calls[0].lead_time_days == 6


def test_forecast_prompt_never_sees_prices_or_limits(
    db: Session, low_stock_product
) -> None:
    """The model must not be able to tailor a quantity to fit a budget."""
    provider = FakeForecastProvider()

    forecast_service.generate_forecast(db, low_stock_product, provider=provider)

    request: ForecastRequest = provider.calls[0]
    fields = set(request.model_dump().keys())
    assert "price_per_unit_paise" not in fields
    assert not {field for field in fields if "limit" in field or "spend" in field}
    assert "supplier" not in str(fields)


@pytest.mark.parametrize("quantity", [0, -1, -20])
def test_non_positive_quantity_is_rejected(
    db: Session, low_stock_product, quantity: int
) -> None:
    provider = FakeForecastProvider(quantity=quantity)

    with pytest.raises(ForecastInvalidError) as exc:
        forecast_service.generate_forecast(db, low_stock_product, provider=provider)

    assert exc.value.code == "FORECAST_INVALID"
    assert AuditAction.FORECAST_FAILED.value in _actions(db)


def test_excessive_quantity_is_rejected_as_a_guardrail(
    db: Session, low_stock_product
) -> None:
    """500000 units is over the cap; reported as a guardrail, not a schema error."""
    provider = FakeForecastProvider(quantity=500_000)

    with pytest.raises(QuantityLimitError) as exc:
        forecast_service.generate_forecast(db, low_stock_product, provider=provider)

    assert exc.value.code == "QUANTITY_LIMIT_EXCEEDED"
    assert exc.value.details["limit"] == 500
    assert AuditAction.GUARDRAIL_VIOLATION.value in _actions(db)


def test_quantity_exactly_at_the_limit_is_accepted(
    db: Session, low_stock_product
) -> None:
    """Boundary: the cap is inclusive."""
    result = forecast_service.generate_forecast(
        db, low_stock_product, provider=FakeForecastProvider(quantity=500)
    )

    assert result.quantity == 500


def test_one_over_the_limit_is_rejected(db: Session, low_stock_product) -> None:
    with pytest.raises(QuantityLimitError):
        forecast_service.generate_forecast(
            db, low_stock_product, provider=FakeForecastProvider(quantity=501)
        )


def test_malformed_output_is_rejected(db: Session, low_stock_product) -> None:
    provider = FakeForecastProvider(
        raise_error=AgentMalformedOutputError("Model returned prose, not JSON.")
    )

    with pytest.raises(ForecastInvalidError):
        forecast_service.generate_forecast(db, low_stock_product, provider=provider)

    assert AuditAction.FORECAST_FAILED.value in _actions(db)


def test_provider_timeout_is_reported_as_unavailable(
    db: Session, low_stock_product
) -> None:
    provider = FakeForecastProvider(
        raise_error=AgentTransportError("Model did not respond within 30s.")
    )

    with pytest.raises(ForecastUnavailableError) as exc:
        forecast_service.generate_forecast(db, low_stock_product, provider=provider)

    assert exc.value.status_code == 502


def test_missing_credentials_are_reported_distinctly(
    db: Session, low_stock_product
) -> None:
    provider = FakeForecastProvider(
        raise_error=AgentNotConfigured("OPENAI_API_KEY is not set.")
    )

    with pytest.raises(AgentNotConfiguredError) as exc:
        forecast_service.generate_forecast(db, low_stock_product, provider=provider)

    assert exc.value.status_code == 503


def test_unexpected_provider_exception_does_not_leak(
    db: Session, low_stock_product
) -> None:
    """A provider bug must not become a 500 with an internal message."""
    provider = FakeForecastProvider(
        raise_error=ZeroDivisionError("division by zero in provider internals")
    )

    with pytest.raises(ForecastInvalidError) as exc:
        forecast_service.generate_forecast(db, low_stock_product, provider=provider)

    assert "division by zero" not in exc.value.message


def test_no_order_is_created_by_any_forecast_failure(
    db: Session, low_stock_product
) -> None:
    for provider in (
        FakeForecastProvider(quantity=0),
        FakeForecastProvider(quantity=-5),
        FakeForecastProvider(quantity=999_999),
        FakeForecastProvider(raise_error=AgentTransportError("down")),
        FakeForecastProvider(raise_error=AgentMalformedOutputError("garbage")),
    ):
        with pytest.raises(Exception):
            forecast_service.generate_forecast(
                db, low_stock_product, provider=provider
            )

    assert db.scalars(select(Order)).all() == []


def test_product_with_no_sales_history_is_refused(
    db: Session, make_product
) -> None:
    """Better to refuse than to let the model invent a demand level."""
    product = make_product("Ghost", current_stock=1, reorder_threshold=10)

    with pytest.raises(NoSalesHistoryError):
        forecast_service.generate_forecast(
            db, product, provider=FakeForecastProvider()
        )


def test_history_window_is_bounded_to_the_configured_days(
    db: Session, low_stock_product
) -> None:
    """A product with years of sales must not pull all of it into memory."""
    from datetime import date, timedelta

    from app.models.sales_history import SalesHistory

    start = date(2024, 1, 1)
    for offset in range(200):
        db.add(
            SalesHistory(
                product_id=low_stock_product.id,
                date=start + timedelta(days=offset),
                quantity_sold=5,
            )
        )
    db.commit()

    provider = FakeForecastProvider()
    forecast_service.generate_forecast(db, low_stock_product, provider=provider)

    assert provider.calls[0].history_days == 28


def test_history_is_passed_oldest_first(db: Session, low_stock_product) -> None:
    provider = FakeForecastProvider()

    forecast_service.generate_forecast(db, low_stock_product, provider=provider)

    dates = [observation.date for observation in provider.calls[0].sales_history]
    assert dates == sorted(dates)


# --- the real LLM provider, against a mocked transport ---------------------


def _client_returning(content: str, *, status_code: int = 200) -> OpenAIJSONClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if status_code != 200:
            return httpx.Response(status_code, json={"error": {"message": "nope"}})
        return httpx.Response(
            200, json={"choices": [{"message": {"content": content}}]}
        )

    from app.core.config import Settings

    config = Settings(OPENAI_API_KEY="sk-test-dummy")
    return OpenAIJSONClient(
        config, client=httpx.Client(transport=httpx.MockTransport(handler))
    )


def _request() -> ForecastRequest:
    from datetime import date

    return ForecastRequest(
        product_name="Milk",
        unit="litre",
        current_stock=42,
        reorder_threshold=60,
        sales_history=(SalesObservation(date=date(2025, 6, 1), quantity_sold=20),),
        lead_time_days=6,
    )


def test_llm_provider_parses_a_good_response() -> None:
    provider = LLMForecastProvider(
        client=_client_returning('{"recommended_quantity": 75, "reasoning": "ok"}')
    )

    result = provider.forecast(_request())

    assert result.recommended_quantity == 75


def test_llm_provider_rejects_non_json_content() -> None:
    provider = LLMForecastProvider(
        client=_client_returning("Sure! I think you should order about 75 litres.")
    )

    with pytest.raises(AgentMalformedOutputError):
        provider.forecast(_request())


def test_llm_provider_rejects_json_that_is_not_an_object() -> None:
    provider = LLMForecastProvider(client=_client_returning("[75]"))

    with pytest.raises(AgentMalformedOutputError):
        provider.forecast(_request())


def test_llm_provider_rejects_a_schema_mismatch() -> None:
    provider = LLMForecastProvider(
        client=_client_returning('{"qty": 75, "why": "wrong key names"}')
    )

    with pytest.raises(AgentMalformedOutputError):
        provider.forecast(_request())


def test_llm_provider_maps_http_error_to_transport_error() -> None:
    provider = LLMForecastProvider(client=_client_returning("", status_code=500))

    with pytest.raises(AgentTransportError):
        provider.forecast(_request())


def test_llm_provider_maps_timeout_to_transport_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("too slow", request=request)

    from app.core.config import Settings

    client = OpenAIJSONClient(
        Settings(OPENAI_API_KEY="sk-test-dummy"),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    provider = LLMForecastProvider(client=client)

    with pytest.raises(AgentTransportError):
        provider.forecast(_request())


def test_llm_client_refuses_to_run_without_a_key() -> None:
    from app.core.config import Settings

    client = OpenAIJSONClient(Settings(OPENAI_API_KEY=""))

    with pytest.raises(AgentNotConfigured):
        client.complete_json(system="s", user="u")


def test_llm_request_asks_for_json_output() -> None:
    """Server-side JSON mode removes the commonest failure, prose or fences."""
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json as json_module

        captured.update(json_module.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": '{"recommended_quantity":75,"reasoning":"x"}'}}
                ]
            },
        )

    from app.core.config import Settings

    client = OpenAIJSONClient(
        Settings(OPENAI_API_KEY="sk-test-dummy"),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    LLMForecastProvider(client=client).forecast(_request())

    assert captured["response_format"] == {"type": "json_object"}
