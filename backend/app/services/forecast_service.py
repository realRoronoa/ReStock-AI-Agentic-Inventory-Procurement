"""Forecast orchestration and validation.

Division of labour, which is the whole architecture in miniature:

* the **agent** judges how much to order;
* this **service** decides whether that judgement is safe to act on.

There is no fallback. If the model is unreachable or returns something invalid,
no order is created, the failure is audited, and the caller gets a clear error.
Substituting a hardcoded quantity would be worse than failing: it would put a
number nobody chose into an audit trail that says an agent recommended it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agents.forecast_agent import (
    ForecastProvider,
    ForecastRequest,
    ForecastResult,
    LLMForecastProvider,
    SalesObservation,
)
from app.agents.llm_client import (
    AgentMalformedOutputError,
    AgentNotConfigured,
    AgentTransportError,
)
from app.core.config import Settings, settings as default_settings
from app.core.errors import (
    AgentNotConfiguredError,
    ForecastInvalidError,
    ForecastUnavailableError,
    NoSalesHistoryError,
    QuantityLimitError,
)
from app.core.limits import check_quantity, limits_snapshot
from app.models.audit_log import AuditAction, AuditActor
from app.models.product import Product
from app.models.sales_history import SalesHistory
from app.models.supplier import Supplier
from app.services import audit_service

logger = logging.getLogger("restock.forecast")


@dataclass(frozen=True)
class ValidatedForecast:
    """A forecast the backend is willing to act on."""

    quantity: int
    reasoning: str
    provider: str
    history_days: int
    observed_daily_average: float
    lead_time_days: int


def load_sales_history(
    db: Session,
    product_id: int,
    *,
    days: int,
) -> list[SalesHistory]:
    """The most recent `days` of sales, oldest first.

    Bounded by `LIMIT` at the database rather than filtered in Python, so a
    product with years of history does not pull all of it into memory to show
    the model 28 days of it.
    """
    rows = db.scalars(
        select(SalesHistory)
        .where(SalesHistory.product_id == product_id)
        .order_by(SalesHistory.date.desc())
        .limit(days)
    ).all()
    return sorted(rows, key=lambda row: row.date)


def longest_lead_time(db: Session, product_id: int) -> int:
    """Worst-case delivery time across the product's suppliers.

    The forecast must cover the *slowest* option the merchant might choose,
    because the supplier is not selected until after the quantity is decided.
    Sizing to the fastest supplier and then choosing the cheap slow one is how a
    stockout happens.
    """
    value = db.scalar(
        select(func.max(Supplier.delivery_days)).where(
            Supplier.product_id == product_id
        )
    )
    return int(value or 0)


def build_request(
    db: Session,
    product: Product,
    *,
    config: Settings | None = None,
) -> ForecastRequest:
    """Assemble the agent input from database facts only."""
    config = config or default_settings
    history = load_sales_history(
        db, product.id, days=config.FORECAST_HISTORY_DAYS
    )

    if not history:
        # Nothing to reason from. Asking the model anyway would invite it to
        # invent a demand level, which is exactly the failure mode to avoid.
        raise NoSalesHistoryError(
            f"{product.name} has no recorded sales, so demand cannot be "
            "forecast. Record some sales history first.",
            details={"product_id": product.id},
        )

    return ForecastRequest(
        product_name=product.name,
        unit=product.unit,
        current_stock=product.current_stock,
        reorder_threshold=product.reorder_threshold,
        sales_history=tuple(
            SalesObservation(date=row.date, quantity_sold=row.quantity_sold)
            for row in history
        ),
        lead_time_days=longest_lead_time(db, product.id),
    )


def _daily_average(request: ForecastRequest) -> float:
    total = sum(observation.quantity_sold for observation in request.sales_history)
    return round(total / len(request.sales_history), 2)


def get_forecast_provider() -> ForecastProvider:
    """FastAPI dependency. One production implementation; tests override it."""
    return LLMForecastProvider()


def generate_forecast(
    db: Session,
    product: Product,
    *,
    provider: ForecastProvider,
    config: Settings | None = None,
) -> ValidatedForecast:
    """Get a forecast and validate it. Raises rather than guessing.

    Audit rows for the failure paths are committed independently, because the
    caller is about to abandon its transaction and the fact that an agent
    misbehaved must outlive the request.
    """
    config = config or default_settings
    request = build_request(db, product, config=config)

    audit_service.log(
        db,
        actor=AuditActor.SYSTEM,
        action=AuditAction.FORECAST_STARTED,
        reasoning_text=(
            f"Requesting a demand forecast for {product.name} from "
            f"{getattr(provider, 'name', type(provider).__name__)} using "
            f"{request.history_days} days of sales history."
        ),
        metadata={
            "product_id": product.id,
            "provider": getattr(provider, "name", type(provider).__name__),
            "history_days": request.history_days,
            "lead_time_days": request.lead_time_days,
        },
    )
    db.commit()

    # --- call the model ---
    try:
        result: ForecastResult = provider.forecast(request)
    except AgentNotConfigured as exc:
        _audit_failure(
            db, product, "provider_not_configured", str(exc), request=request
        )
        raise AgentNotConfiguredError(
            "No LLM provider is configured on this server, so a demand forecast "
            "cannot be generated. No order was created."
        ) from exc
    except AgentTransportError as exc:
        _audit_failure(db, product, "provider_unavailable", str(exc), request=request)
        raise ForecastUnavailableError(
            f"The forecasting model could not be reached: {exc} No order was "
            "created.",
            details={"product_id": product.id},
        ) from exc
    except AgentMalformedOutputError as exc:
        _audit_failure(db, product, "malformed_output", str(exc), request=request)
        raise ForecastInvalidError(
            f"The forecasting model returned output the backend refuses to act "
            f"on: {exc} No order was created.",
            details={"product_id": product.id},
        ) from exc
    except Exception as exc:  # provider bug, or a schema failure in a double
        logger.exception("forecast_provider_error product_id=%s", product.id)
        _audit_failure(
            db, product, "provider_error", type(exc).__name__, request=request
        )
        raise ForecastInvalidError(
            "The forecasting model returned output the backend refuses to act "
            "on. No order was created.",
            details={"product_id": product.id},
        ) from exc

    # --- validate the output ---
    # Non-positive is a model error; over-cap is a guardrail decision. Kept
    # distinct so the merchant is told which of the two actually happened.
    if result.recommended_quantity <= 0:
        _audit_failure(
            db,
            product,
            "non_positive_quantity",
            f"Model recommended {result.recommended_quantity}.",
            request=request,
            model_reasoning=result.reasoning,
            model_quantity=result.recommended_quantity,
        )
        raise ForecastInvalidError(
            f"The forecast returned a quantity of "
            f"{result.recommended_quantity}, which is not a valid order size. "
            "No order was created.",
            details={
                "product_id": product.id,
                "recommended_quantity": result.recommended_quantity,
            },
        )

    try:
        check_quantity(result.recommended_quantity, config=config)
    except QuantityLimitError as exc:
        _audit_failure(
            db,
            product,
            "quantity_over_limit",
            exc.message,
            request=request,
            model_reasoning=result.reasoning,
            model_quantity=result.recommended_quantity,
            action=AuditAction.GUARDRAIL_VIOLATION,
        )
        raise

    daily_average = _daily_average(request)
    validated = ValidatedForecast(
        quantity=result.recommended_quantity,
        reasoning=result.reasoning,
        provider=getattr(provider, "name", type(provider).__name__),
        history_days=request.history_days,
        observed_daily_average=daily_average,
        lead_time_days=request.lead_time_days,
    )

    audit_service.log(
        db,
        actor=AuditActor.AGENT,
        action=AuditAction.FORECAST_GENERATED,
        # The model's own words, stored verbatim.
        reasoning_text=result.reasoning,
        metadata={
            "product_id": product.id,
            "product_name": product.name,
            "recommended_quantity": validated.quantity,
            "provider": validated.provider,
            "history_days": validated.history_days,
            "observed_daily_average": daily_average,
            "lead_time_days": validated.lead_time_days,
            "current_stock": product.current_stock,
            "reorder_threshold": product.reorder_threshold,
            "limits_at_decision": limits_snapshot(config),
        },
    )
    db.commit()

    logger.info(
        "forecast_generated product_id=%s quantity=%d provider=%s daily_avg=%.2f",
        product.id,
        validated.quantity,
        validated.provider,
        daily_average,
    )
    return validated


def _audit_failure(
    db: Session,
    product: Product,
    reason_code: str,
    detail: str,
    *,
    request: ForecastRequest | None = None,
    model_reasoning: str | None = None,
    model_quantity: int | None = None,
    action: AuditAction = AuditAction.FORECAST_FAILED,
) -> None:
    """Record a forecast failure durably, then let the caller raise."""
    logger.warning(
        "forecast_failed product_id=%s reason=%s", product.id, reason_code
    )
    metadata = {
        "product_id": product.id,
        "product_name": product.name,
        "reason_code": reason_code,
        "detail": detail,
    }
    if request is not None:
        metadata["history_days"] = request.history_days
    if model_quantity is not None:
        metadata["rejected_quantity"] = model_quantity

    audit_service.log_independently(
        db,
        actor=AuditActor.SYSTEM,
        action=action,
        reasoning_text=(
            f"Forecast for {product.name} rejected ({reason_code}): {detail} "
            "No order was created."
            + (
                f" The model's stated reasoning was: {model_reasoning}"
                if model_reasoning
                else ""
            )
        ),
        metadata=metadata,
    )
