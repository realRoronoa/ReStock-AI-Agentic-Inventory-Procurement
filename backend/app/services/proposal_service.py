"""Proposal creation.

Orchestrates the full recommendation pipeline and produces an order in
`PROPOSED` state. A proposal is a *suggestion*: it commits no money, counts
against no budget, and expires only by being approved or ignored.

    load product
      -> verify it is actually low stock
      -> load 28 days of sales
      -> forecast agent            (LLM)
      -> validate quantity         (deterministic)
      -> load payable suppliers
      -> supplier agent            (LLM)
      -> validate supplier         (deterministic, re-read from DB)
      -> compute amount            (deterministic, DB price)
      -> guardrails                (deterministic)
      -> create PROPOSED order
      -> audit
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.agents.forecast_agent import ForecastProvider
from app.agents.supplier_agent import SupplierProvider
from app.core.config import Settings, settings as default_settings
from app.core.errors import (
    AppError,
    ProductNotFoundError,
    ProductNotLowStockError,
)
from app.core.limits import enforce_all, limits_snapshot
from app.core.money import format_inr
from app.models.audit_log import AuditAction, AuditActor
from app.models.order import Order, OrderStatus
from app.models.product import Product
from app.services import audit_service, forecast_service, supplier_service

logger = logging.getLogger("restock.proposals")


@dataclass
class Proposal:
    """A created proposal plus the reasoning that produced it."""

    order: Order
    forecast_reasoning: str
    supplier_reasoning: str
    forecast_provider: str
    supplier_provider: str
    observed_daily_average: float
    history_days: int
    options_considered: int


def get_product(db: Session, product_id: int) -> Product:
    product = db.scalar(
        select(Product)
        .where(Product.id == product_id)
        .options(joinedload(Product.suppliers))
    )
    if product is None:
        raise ProductNotFoundError(
            f"Product {product_id} not found.", details={"product_id": product_id}
        )
    return product


def list_proposals(db: Session, *, limit: int = 100, offset: int = 0):
    """Orders still awaiting a human decision, newest first."""
    from app.services import order_service

    return order_service.list_orders(
        db, status=OrderStatus.PROPOSED, limit=limit, offset=offset
    )


def create_proposal(
    db: Session,
    product_id: int,
    *,
    forecast_provider: ForecastProvider,
    supplier_provider: SupplierProvider,
    config: Settings | None = None,
) -> Proposal:
    """Run the pipeline and persist a PROPOSED order.

    Raises (never returns a partial proposal) on: unknown product, product not
    low stock, forecast unavailable or invalid, no payable supplier, supplier
    selection unavailable or invalid, or any guardrail breach. Every failure is
    audited before the exception leaves.
    """
    config = config or default_settings
    product = get_product(db, product_id)

    # --- 1. the product must actually need reordering ---
    if not product.is_low_stock:
        # Refused before any LLM call: proposing a reorder for well-stocked
        # goods would spend money and model budget on a decision nobody asked
        # for, and it is the check a client is most likely to skip.
        _audit_rejection(
            db,
            product,
            "not_low_stock",
            f"{product.name} has {product.current_stock} {product.unit} against a "
            f"threshold of {product.reorder_threshold}.",
        )
        raise ProductNotLowStockError(
            f"{product.name} is at {product.current_stock} {product.unit}, at or "
            f"above its reorder threshold of {product.reorder_threshold}. No "
            "proposal was created.",
            details={
                "product_id": product.id,
                "current_stock": product.current_stock,
                "reorder_threshold": product.reorder_threshold,
            },
        )

    # --- 2-4. forecast (LLM) and validate it (deterministic) ---
    forecast = forecast_service.generate_forecast(
        db, product, provider=forecast_provider, config=config
    )

    # --- 5-6. supplier (LLM) and validate it (deterministic) ---
    choice = supplier_service.select_supplier(
        db, product, forecast.quantity, provider=supplier_provider
    )
    supplier = choice.supplier

    # --- 7. authoritative amount, from the database price ---
    amount_paise = supplier_service.calculate_amount_paise(
        supplier, forecast.quantity
    )

    # --- 8. guardrails ---
    try:
        enforce_all(
            db,
            quantity=forecast.quantity,
            amount_paise=amount_paise,
            config=config,
        )
    except AppError as exc:
        _audit_rejection(
            db,
            product,
            "guardrail",
            exc.message,
            action=AuditAction.GUARDRAIL_VIOLATION,
            extra={
                "quantity": forecast.quantity,
                "amount_paise": amount_paise,
                "supplier_id": supplier.id,
                "guardrail_code": exc.code,
                "guardrail_details": exc.details,
                "limits": limits_snapshot(config),
                "forecast_reasoning": forecast.reasoning,
                "supplier_reasoning": choice.reasoning,
            },
        )
        raise

    # --- 9. persist the proposal ---
    order = Order(
        product_id=product.id,
        supplier_id=supplier.id,
        quantity=forecast.quantity,
        amount_paise=amount_paise,
        status=OrderStatus.PROPOSED,
        forecast_reasoning=forecast.reasoning,
        supplier_reasoning=choice.reasoning,
        unit_price_paise_at_proposal=supplier.price_per_unit_paise,
    )
    db.add(order)
    db.flush()  # assign order.id for the audit row below

    audit_service.log(
        db,
        actor=AuditActor.SYSTEM,
        action=AuditAction.PROPOSAL_CREATED,
        reasoning_text=(
            f"Proposed ordering {forecast.quantity} {product.unit} of "
            f"{product.name} from {supplier.name} at "
            f"{format_inr(supplier.price_per_unit_paise)}/{product.unit} for a "
            f"total of {format_inr(amount_paise)}, delivering in "
            f"{supplier.delivery_days} days. Awaiting human approval; no money "
            "has moved."
        ),
        related_order_id=order.id,
        metadata={
            "order_id": order.id,
            "product_id": product.id,
            "product_name": product.name,
            "current_stock": product.current_stock,
            "reorder_threshold": product.reorder_threshold,
            "supplier_id": supplier.id,
            "supplier_name": supplier.name,
            "quantity": forecast.quantity,
            "unit_price_paise": supplier.price_per_unit_paise,
            "amount_paise": amount_paise,
            "amount_display": format_inr(amount_paise),
            "delivery_days": supplier.delivery_days,
            "forecast_provider": forecast.provider,
            "supplier_provider": choice.provider,
            "observed_daily_average": forecast.observed_daily_average,
            "history_days": forecast.history_days,
            "options_considered": choice.options_considered,
            "limits_at_decision": limits_snapshot(config),
        },
    )

    db.commit()
    db.refresh(order)

    logger.info(
        "proposal_created order_id=%s product_id=%s supplier_id=%s qty=%d "
        "amount_paise=%d",
        order.id,
        product.id,
        supplier.id,
        forecast.quantity,
        amount_paise,
    )

    return Proposal(
        order=order,
        forecast_reasoning=forecast.reasoning,
        supplier_reasoning=choice.reasoning,
        forecast_provider=forecast.provider,
        supplier_provider=choice.provider,
        observed_daily_average=forecast.observed_daily_average,
        history_days=forecast.history_days,
        options_considered=choice.options_considered,
    )


def _audit_rejection(
    db: Session,
    product: Product,
    reason_code: str,
    detail: str,
    *,
    action: AuditAction = AuditAction.PROPOSAL_FAILED,
    extra: dict | None = None,
) -> None:
    logger.info(
        "proposal_rejected product_id=%s reason=%s", product.id, reason_code
    )
    audit_service.log_independently(
        db,
        actor=AuditActor.SYSTEM,
        action=action,
        reasoning_text=(
            f"No proposal created for {product.name} ({reason_code}): {detail}"
        ),
        metadata={
            "product_id": product.id,
            "product_name": product.name,
            "reason_code": reason_code,
            "detail": detail,
            **(extra or {}),
        },
    )
