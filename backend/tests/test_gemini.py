"""Tests for Gemini LLM provider integration.

Tests cover:
1. Gemini provider initialization.
2. Successful forecast response.
3. Successful supplier response.
4. Invalid JSON.
5. Invalid quantity.
6. Unknown supplier.
7. Provider timeout.
8. Provider error (HTTP 4xx / 5xx).
9. Missing Gemini API key.
10. Factory switching and OpenAI provider coexistence.
"""

from __future__ import annotations

from datetime import date
import json
import httpx
import pytest
from sqlalchemy.orm import Session

from app.agents.forecast_agent import (
    ForecastRequest,
    ForecastResult,
    LLMForecastProvider,
    SalesObservation,
)
from app.agents.supplier_agent import (
    SupplierOption,
    SupplierSelectionRequest,
    SupplierSelection,
    LLMSupplierProvider,
)
from app.agents.llm_client import (
    AgentMalformedOutputError,
    AgentNotConfigured,
    AgentTransportError,
    GeminiJSONClient,
    OpenAIJSONClient,
    get_llm_client,
)
from app.core.config import Settings
from app.core.errors import (
    AgentNotConfiguredError,
    ForecastInvalidError,
    ForecastUnavailableError,
    SupplierSelectionInvalidError,
)
from app.services import forecast_service, supplier_service


def _sample_forecast_request() -> ForecastRequest:
    return ForecastRequest(
        product_name="Milk",
        unit="litre",
        current_stock=42,
        reorder_threshold=60,
        sales_history=(
            SalesObservation(date=date(2026, 8, 1), quantity_sold=20),
            SalesObservation(date=date(2026, 8, 2), quantity_sold=25),
        ),
        lead_time_days=2,
    )


def _sample_supplier_request() -> SupplierSelectionRequest:
    return SupplierSelectionRequest(
        product_name="Milk",
        unit="litre",
        quantity=50,
        current_stock=42,
        reorder_threshold=60,
        suppliers=(
            SupplierOption(
                supplier_id=1,
                name="Dairy Fresh",
                price_per_unit_paise=6000,
                delivery_days=2,
            ),
            SupplierOption(
                supplier_id=2,
                name="Quick Milk Ltd",
                price_per_unit_paise=6500,
                delivery_days=1,
            ),
        ),
    )


def _gemini_client(
    response_text: str,
    status_code: int = 200,
    api_key: str = "test-gemini-key",
) -> GeminiJSONClient:
    def handler(request: httpx.Request) -> httpx.Response:
        # Verify headers
        assert request.headers.get("x-goog-api-key") == api_key
        assert request.headers.get("content-type") == "application/json"

        if status_code != 200:
            return httpx.Response(status_code, json={"error": {"message": "failed"}})

        # Standard Gemini API response envelope
        envelope = {
            "candidates": [
                {
                    "content": {
                        "parts": [{"text": response_text}],
                        "role": "model",
                    },
                    "finishReason": "STOP",
                }
            ]
        }
        return httpx.Response(200, json=envelope)

    settings = Settings(
        LLM_PROVIDER="gemini",
        GEMINI_API_KEY=api_key,
        GEMINI_MODEL="gemini-2.5-flash",
    )
    return GeminiJSONClient(
        settings, client=httpx.Client(transport=httpx.MockTransport(handler))
    )


# 1. Initialization and config checks
def test_gemini_client_initialization() -> None:
    settings = Settings(
        LLM_PROVIDER="gemini",
        GEMINI_API_KEY="test-key-123",
        GEMINI_MODEL="gemini-2.5-flash",
    )
    client = GeminiJSONClient(settings)
    assert client.model == "gemini-2.5-flash"
    assert settings.llm_configured is True
    assert "test-key-123" in settings.all_secrets


# 2. Successful forecast response
def test_gemini_successful_forecast() -> None:
    client = _gemini_client(
        '{"recommended_quantity": 40, "reasoning": "Recent sales average 22.5/day with 2 days lead time."}'
    )
    provider = LLMForecastProvider(client=client)
    result = provider.forecast(_sample_forecast_request())

    assert isinstance(result, ForecastResult)
    assert result.recommended_quantity == 40
    assert "sales average" in result.reasoning


# 3. Successful supplier response
def test_gemini_successful_supplier_selection() -> None:
    client = _gemini_client(
        '{"supplier_id": 1, "reasoning": "Dairy Fresh is cheaper and delivery time fits inventory buffer."}'
    )
    provider = LLMSupplierProvider(client=client)
    result = provider.select(_sample_supplier_request())

    assert isinstance(result, SupplierSelection)
    assert result.supplier_id == 1
    assert "Dairy Fresh" in result.reasoning


# 4. Invalid JSON from Gemini
def test_gemini_invalid_json_raises_malformed_error() -> None:
    client = _gemini_client("Here is the recommendation: 50 units because sales went up.")
    provider = LLMForecastProvider(client=client)

    with pytest.raises(AgentMalformedOutputError):
        provider.forecast(_sample_forecast_request())


# 5. Invalid quantity (e.g. string rather than int or missing fields)
def test_gemini_string_quantity_rejected() -> None:
    client = _gemini_client('{"recommended_quantity": "50", "reasoning": "bad type"}')
    provider = LLMForecastProvider(client=client)

    with pytest.raises(AgentMalformedOutputError):
        provider.forecast(_sample_forecast_request())


# 6. Unknown supplier ID rejected by domain service
def test_gemini_unknown_supplier_id_rejected_by_service(
    db: Session, low_stock_product
) -> None:
    client = _gemini_client('{"supplier_id": 99999, "reasoning": "Hallucinated supplier"}')
    provider = LLMSupplierProvider(client=client)

    with pytest.raises(SupplierSelectionInvalidError):
        supplier_service.select_supplier(
            db, low_stock_product, 50, provider=provider
        )


# 7. Provider timeout
def test_gemini_timeout_mapped_to_transport_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("Gemini connection timed out", request=request)

    settings = Settings(
        LLM_PROVIDER="gemini",
        GEMINI_API_KEY="test-key",
    )
    client = GeminiJSONClient(
        settings, client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    provider = LLMForecastProvider(client=client)

    with pytest.raises(AgentTransportError):
        provider.forecast(_sample_forecast_request())


# 8. Provider HTTP error (4xx / 5xx)
def test_gemini_http_error_mapped_to_transport_error() -> None:
    client = _gemini_client("", status_code=503)
    provider = LLMForecastProvider(client=client)

    with pytest.raises(AgentTransportError):
        provider.forecast(_sample_forecast_request())


# 9. Missing Gemini API key
def test_gemini_refuses_to_run_without_key() -> None:
    settings = Settings(LLM_PROVIDER="gemini", GEMINI_API_KEY="")
    client = GeminiJSONClient(settings)

    with pytest.raises(AgentNotConfigured):
        client.complete_json(system="sys", user="usr")


# 10. OpenAI provider still works
def test_openai_provider_coexistence() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": '{"recommended_quantity": 30, "reasoning": "OpenAI answer"}'
                        }
                    }
                ]
            },
        )

    settings = Settings(
        LLM_PROVIDER="openai",
        OPENAI_API_KEY="sk-test-key",
    )
    openai_client = OpenAIJSONClient(
        settings, client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    provider = LLMForecastProvider(client=openai_client)
    res = provider.forecast(_sample_forecast_request())
    assert res.recommended_quantity == 30


# 11. Factory switching
def test_get_llm_client_factory() -> None:
    gemini_cfg = Settings(LLM_PROVIDER="gemini", GEMINI_API_KEY="gem-key")
    openai_cfg = Settings(LLM_PROVIDER="openai", OPENAI_API_KEY="sk-key")

    client_g = get_llm_client(gemini_cfg)
    client_o = get_llm_client(openai_cfg)

    assert isinstance(client_g, GeminiJSONClient)
    assert isinstance(client_o, OpenAIJSONClient)
