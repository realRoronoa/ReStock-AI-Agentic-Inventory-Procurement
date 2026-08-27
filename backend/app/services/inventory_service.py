"""Low-stock detection and inventory mutation.

Three responsibilities, all deliberately free of any LLM involvement:

1. **Detection.** The entire rule is `current_stock < reorder_threshold`. It is
   a comparison, not a judgement, so asking a model would add cost, latency, and
   non-determinism in exchange for nothing.
2. **Receipt.** `apply_received_stock` is the only function that *increases*
   stock, called solely from webhook processing after a payout is confirmed
   processed.
3. **Consumption.** `record_sale` is the only function that *decreases* stock.

Keeping both directions in one module is deliberate: there is exactly one file
to read in order to know every way `products.current_stock` can change.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date as date_type
from datetime import datetime
from typing import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import InsufficientStockError, ProductNotFoundError
from app.models.audit_log import AuditAction, AuditActor
from app.models.order import Order
from app.models.product import Product
from app.models.sales_history import SalesHistory
from app.services import audit_service

logger = logging.getLogger("restock.inventory")


@dataclass
class InventoryCheckResult:
    """Outcome of one inventory sweep."""

    low_stock_products: Sequence[Product]
    products_checked: int
    checked_at: datetime
    newly_detected_product_ids: list[int] = field(default_factory=list)


def find_low_stock_products(db: Session) -> Sequence[Product]:
    """Products strictly below their reorder threshold.

    Uses the `Product.is_low_stock` hybrid so the rule has exactly one
    definition, evaluated here as SQL and elsewhere as Python. Note the strict
    `<`: stock exactly *at* the threshold is not low.
    """
    return db.scalars(
        select(Product).where(Product.is_low_stock).order_by(Product.name)
    ).all()


def run_inventory_check(db: Session) -> InventoryCheckResult:
    """Sweep every product, audit the result, and return what is low.

    Audit behaviour is intentionally not "one event per low product per check".
    A merchant polling this endpoint every minute would otherwise bury the trail
    in thousands of identical rows and make the genuinely interesting events
    unfindable.

    Instead `LOW_STOCK_DETECTED` is emitted for a product only when there is no
    such event recorded *since the product last changed*. Any movement in stock
    or threshold bumps `products.updated_at`, so a product that dips, is
    restocked, and dips again produces two events — while a product that simply
    stays low produces one. A per-check `INVENTORY_CHECK_COMPLETED` event always
    records that the sweep happened, so the absence of a detection event is
    never ambiguous.
    """
    products = db.scalars(select(Product).order_by(Product.name)).all()
    low_stock = [product for product in products if product.is_low_stock]

    last_detected = audit_service.latest_action_timestamps(
        db, AuditAction.LOW_STOCK_DETECTED
    )

    newly_detected: list[int] = []
    for product in low_stock:
        previous = last_detected.get(product.id)
        if previous is not None and previous >= product.updated_at:
            continue

        newly_detected.append(product.id)
        audit_service.log(
            db,
            actor=AuditActor.SYSTEM,
            action=AuditAction.LOW_STOCK_DETECTED,
            reasoning_text=(
                f"{product.name} is at {product.current_stock} {product.unit} "
                f"against a reorder threshold of {product.reorder_threshold}."
            ),
            metadata={
                "product_id": product.id,
                "product_name": product.name,
                "current_stock": product.current_stock,
                "reorder_threshold": product.reorder_threshold,
                "unit": product.unit,
                "shortfall": product.reorder_threshold - product.current_stock,
            },
        )

    checked_at = datetime.now().astimezone()
    audit_service.log(
        db,
        actor=AuditActor.SYSTEM,
        action=AuditAction.INVENTORY_CHECK_COMPLETED,
        reasoning_text=(
            f"Checked {len(products)} products; {len(low_stock)} below threshold "
            f"({len(newly_detected)} newly detected)."
        ),
        metadata={
            "products_checked": len(products),
            "low_stock_count": len(low_stock),
            "newly_detected_product_ids": newly_detected,
            "low_stock_product_ids": [product.id for product in low_stock],
        },
    )
    db.commit()

    logger.info(
        "inventory_check products=%d low_stock=%d newly_detected=%d",
        len(products),
        len(low_stock),
        len(newly_detected),
    )

    return InventoryCheckResult(
        low_stock_products=low_stock,
        products_checked=len(products),
        checked_at=checked_at,
        newly_detected_product_ids=newly_detected,
    )


def apply_received_stock(db: Session, order: Order) -> int:
    """Increase a product's stock by a paid order's quantity.

    Does **not** commit: the caller (webhook processing) commits the stock
    change, the order state change, and the audit rows as one transaction, so
    the three can never disagree.

    Callers must have already established that this is the first time the order
    has been settled — the state machine does that by refusing a second
    transition into PAID. This function deliberately does not re-check, so that
    there is exactly one place where "has this already been applied?" is
    decided.
    """
    product = order.product
    previous = product.current_stock
    product.current_stock = previous + order.quantity

    audit_service.log(
        db,
        actor=AuditActor.SYSTEM,
        action=AuditAction.INVENTORY_UPDATED,
        reasoning_text=(
            f"Received {order.quantity} {product.unit} of {product.name} against "
            f"order {order.id}: stock {previous} -> {product.current_stock}."
        ),
        related_order_id=order.id,
        metadata={
            "product_id": product.id,
            "product_name": product.name,
            "order_id": order.id,
            "quantity_added": order.quantity,
            "stock_before": previous,
            "stock_after": product.current_stock,
        },
    )

    logger.info(
        "inventory_updated order_id=%s product_id=%s stock=%d->%d",
        order.id,
        product.id,
        previous,
        product.current_stock,
    )
    return product.current_stock


@dataclass
class SaleResult:
    """Outcome of recording a sale."""

    product: Product
    quantity_sold: int
    stock_before: int
    stock_after: int
    sale_date: date_type
    #: True when this sale is what pushed the product below its threshold. Lets
    #: a client surface "this now needs reordering" without a second request.
    became_low_stock: bool


def _detect_low_stock_for(db: Session, product: Product) -> bool:
    """Emit LOW_STOCK_DETECTED for one product if it is newly low.

    Same suppression rule as the full sweep: only when no such event exists
    since the product last changed. A sale bumps `updated_at`, so a sale that
    crosses the threshold always produces a genuine new event.
    """
    if not product.is_low_stock:
        return False

    last_detected = audit_service.latest_action_timestamps(
        db, AuditAction.LOW_STOCK_DETECTED
    ).get(product.id)
    if last_detected is not None and last_detected >= product.updated_at:
        return False

    audit_service.log(
        db,
        actor=AuditActor.SYSTEM,
        action=AuditAction.LOW_STOCK_DETECTED,
        reasoning_text=(
            f"{product.name} fell to {product.current_stock} {product.unit}, "
            f"against a reorder threshold of {product.reorder_threshold}."
        ),
        metadata={
            "product_id": product.id,
            "product_name": product.name,
            "current_stock": product.current_stock,
            "reorder_threshold": product.reorder_threshold,
            "unit": product.unit,
            "shortfall": product.reorder_threshold - product.current_stock,
            "trigger": "sale",
        },
    )
    return True


def record_sale(
    db: Session,
    product_id: int,
    quantity: int,
    *,
    sale_date: date_type | None = None,
) -> SaleResult:
    """Record units sold: decrease stock and add to sales history.

    This is the point-of-sale side of the loop, and the event that eventually
    makes a product low and triggers a recommendation. It is the **only**
    function that decreases stock.

    Two things move together, in one transaction:

    * `products.current_stock` goes down, so the detector sees reality;
    * `sales_history` goes up, so the forecast agent sees the demand.

    `sales_history` has a UNIQUE(product_id, date), so a second sale on the same
    day **accumulates into the existing row** rather than inserting a duplicate.
    Sales of 3 and 4 on one day leave a single row of 7, which is what a
    forecast should see.

    Raises:
        ProductNotFoundError: unknown product.
        InsufficientStockError: the sale would drive stock negative. Refused
            rather than clamped to zero, because silently absorbing the
            difference would corrupt the demand signal the forecast depends on.
    """
    sale_date = sale_date or datetime.now().astimezone().date()

    product = db.get(Product, product_id)
    if product is None:
        raise ProductNotFoundError(
            f"Product {product_id} not found.", details={"product_id": product_id}
        )

    if quantity <= 0:
        # Defence in depth; the request schema enforces this too.
        raise InsufficientStockError(
            "Sale quantity must be greater than zero.",
            details={"quantity": quantity},
        )

    stock_before = product.current_stock
    if quantity > stock_before:
        raise InsufficientStockError(
            f"Cannot record a sale of {quantity} {product.unit} of "
            f"{product.name}: only {stock_before} in stock.",
            details={
                "product_id": product.id,
                "requested_quantity": quantity,
                "available_stock": stock_before,
            },
        )

    product.current_stock = stock_before - quantity

    existing = db.scalar(
        select(SalesHistory).where(
            SalesHistory.product_id == product.id, SalesHistory.date == sale_date
        )
    )
    if existing is None:
        db.add(
            SalesHistory(
                product_id=product.id, date=sale_date, quantity_sold=quantity
            )
        )
    else:
        existing.quantity_sold += quantity

    audit_service.log(
        db,
        actor=AuditActor.HUMAN,
        action=AuditAction.SALE_RECORDED,
        reasoning_text=(
            f"Sold {quantity} {product.unit} of {product.name} on "
            f"{sale_date.isoformat()}: stock {stock_before} to "
            f"{product.current_stock}."
        ),
        metadata={
            "product_id": product.id,
            "product_name": product.name,
            "quantity_sold": quantity,
            "sale_date": sale_date.isoformat(),
            "stock_before": stock_before,
            "stock_after": product.current_stock,
        },
    )

    # Flush so `updated_at` is written before the suppression check reads it.
    db.flush()
    db.refresh(product)
    became_low = _detect_low_stock_for(db, product)

    db.commit()
    db.refresh(product)

    logger.info(
        "sale_recorded product_id=%s qty=%d stock=%d->%d became_low=%s",
        product.id,
        quantity,
        stock_before,
        product.current_stock,
        became_low,
    )

    return SaleResult(
        product=product,
        quantity_sold=quantity,
        stock_before=stock_before,
        stock_after=product.current_stock,
        sale_date=sale_date,
        became_low_stock=became_low,
    )
