"""Order queries and the single gateway for order state changes.

`transition()` is the **only** place in the codebase that assigns
`Order.status`. Everything else — approval, webhook processing — goes through it.
That centralisation is the point: scattered `order.status = ...` assignments are
how a state machine quietly stops being one, and how an order ends up PAID
without a payout.

Two distinct outcomes are separated on purpose:

* asking for a transition the machine forbids (FAILED -> PAID) is a **conflict**
  and raises;
* asking for a transition to the state the order is *already in* is an
  **idempotent no-op** and returns False. That distinction is what lets a
  redelivered webhook be safely ignored instead of raising.
"""

from __future__ import annotations

import logging
from typing import Any, Sequence

from sqlalchemy import Select, select
from sqlalchemy.orm import Session, joinedload

from app.core.errors import InvalidStateTransitionError, OrderNotFoundError
from app.models.audit_log import AuditAction, AuditActor
from app.models.order import ALLOWED_TRANSITIONS, Order, OrderStatus
from app.services import audit_service

logger = logging.getLogger("restock.orders")


def _with_relations(stmt: Select) -> Select:
    """Eager-load product and supplier.

    Order responses always render both, so lazy loading would mean an N+1 on
    every list request.
    """
    return stmt.options(joinedload(Order.product), joinedload(Order.supplier))


def get_order(db: Session, order_id: int, *, with_relations: bool = True) -> Order:
    """Load an order or raise `OrderNotFoundError`."""
    stmt = select(Order).where(Order.id == order_id)
    if with_relations:
        stmt = _with_relations(stmt)

    order = db.scalar(stmt)
    if order is None:
        raise OrderNotFoundError(
            f"Order {order_id} not found.", details={"order_id": order_id}
        )
    return order


def get_order_by_payout_id(db: Session, payout_id: str) -> Order | None:
    """Resolve a RazorpayX payout id to a local order.

    Returns None rather than raising: an unknown payout is a normal thing for a
    public webhook endpoint to receive, not an exceptional one.
    """
    return db.scalar(
        _with_relations(select(Order)).where(Order.razorpay_payout_id == payout_id)
    )


def list_orders(
    db: Session,
    *,
    status: OrderStatus | None = None,
    product_id: int | None = None,
    supplier_id: int | None = None,
    limit: int = 100,
    offset: int = 0,
) -> Sequence[Order]:
    """List orders, newest first."""
    stmt = _with_relations(select(Order))

    if status is not None:
        stmt = stmt.where(Order.status == status)
    if product_id is not None:
        stmt = stmt.where(Order.product_id == product_id)
    if supplier_id is not None:
        stmt = stmt.where(Order.supplier_id == supplier_id)

    stmt = stmt.order_by(Order.created_at.desc(), Order.id.desc())
    stmt = stmt.limit(limit).offset(offset)

    return db.scalars(stmt).unique().all()


def transition(
    db: Session,
    order: Order,
    new_status: OrderStatus,
    *,
    actor: AuditActor,
    action: AuditAction | str,
    reason: str,
    metadata: dict[str, Any] | None = None,
) -> bool:
    """Move an order to `new_status`, auditing the change.

    Does **not** commit — the caller commits the transition together with
    whatever else belongs in the same transaction (a stock increment, a payout
    id). That is what keeps "order is PAID" and "stock went up" from ever
    disagreeing.

    Returns:
        True if the status changed; False if the order was already in
        `new_status` (an idempotent no-op).

    Raises:
        InvalidStateTransitionError: the transition is not in
            `ALLOWED_TRANSITIONS`.
    """
    previous = order.status

    if previous is new_status:
        logger.info(
            "transition_noop order_id=%s status=%s (already in target state)",
            order.id,
            new_status.value,
        )
        return False

    if new_status not in ALLOWED_TRANSITIONS[previous]:
        allowed = sorted(s.value for s in ALLOWED_TRANSITIONS[previous])
        logger.warning(
            "transition_rejected order_id=%s %s -> %s",
            order.id,
            previous.value,
            new_status.value,
        )
        raise InvalidStateTransitionError(
            f"Order {order.id} is {previous.value} and cannot become "
            f"{new_status.value}."
            + (
                f" Allowed from {previous.value}: {', '.join(allowed)}."
                if allowed
                else f" {previous.value} is a terminal state."
            ),
            details={
                "order_id": order.id,
                "current_status": previous.value,
                "requested_status": new_status.value,
                "allowed_transitions": allowed,
            },
        )

    order.status = new_status

    audit_metadata = {
        "order_id": order.id,
        "from_status": previous.value,
        "to_status": new_status.value,
        **(metadata or {}),
    }
    audit_service.log(
        db,
        actor=actor,
        action=action,
        reasoning_text=reason,
        related_order_id=order.id,
        metadata=audit_metadata,
    )

    logger.info(
        "transition order_id=%s %s -> %s", order.id, previous.value, new_status.value
    )
    return True
