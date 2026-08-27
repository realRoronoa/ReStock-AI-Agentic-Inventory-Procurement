"""Order endpoints, including the human approval gate.

Note what is absent: there is no `PATCH /orders/{id}/status`, and no endpoint
accepts an amount, a price, or a payment state. The only state change a client
can request is approval, by id. Everything else about an order is decided by the
backend or reported by a signature-verified webhook.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.order import OrderStatus
from app.schemas.error import error_responses
from app.schemas.order import (
    ApprovalResponse,
    OrderDetail,
    OrderRead,
    OrderRejectRequest,
)
from app.services import approval_service, order_service, payment_service
from app.services.payment_service import PaymentProvider

router = APIRouter(prefix="/api/orders", tags=["orders"])


@router.get(
    "",
    response_model=list[OrderRead],
    summary="List orders",
    description="Newest first. Filterable by status, product, or supplier.",
)
def list_orders(
    order_status: OrderStatus | None = Query(
        default=None,
        alias="status",
        description=(
            "proposed | approved | paid | failed | reversed | rejected"
        ),
    ),
    product_id: int | None = Query(default=None),
    supplier_id: int | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> list[OrderRead]:
    orders = order_service.list_orders(
        db,
        status=order_status,
        product_id=product_id,
        supplier_id=supplier_id,
        limit=limit,
        offset=offset,
    )
    return [OrderRead.from_order(order) for order in orders]


@router.get(
    "/{order_id}",
    response_model=OrderDetail,
    summary="Get one order in full",
    description=(
        "Everything needed to render an order: product, supplier, authoritative "
        "amounts, the original reasoning from both agents, and the payment "
        "state. One request, no follow-ups."
    ),
    responses=error_responses((404, "Order not found")),
)
def get_order(order_id: int, db: Session = Depends(get_db)) -> OrderDetail:
    order = order_service.get_order(db, order_id)
    return OrderDetail.from_order(order)


@router.post(
    "/{order_id}/approve",
    response_model=ApprovalResponse,
    status_code=status.HTTP_200_OK,
    summary="Approve an order and initiate its supplier payout",
    description=(
        "**The human authorisation gate.** This is the only path by which money "
        "leaves the account, and there is no automatic, agent-driven, or "
        "amount-based bypass at any value.\n\n"
        "The request body is empty on purpose: a client supplies an order id and "
        "nothing else. No amount, price, or status is accepted.\n\n"
        "At approval time the backend re-validates from scratch. The supplier "
        "must still exist, still supply this product, and still have a fund "
        "account; the amount is recomputed from the supplier current price; and "
        "the quantity, per-order and daily spend caps are all checked again.\n\n"
        "The order then becomes `approved` and a payout is requested. It does "
        "**not** become `paid`: only a signature-verified `payout.processed` "
        "webhook does that, and only then does stock increase.\n\n"
        "Safe against double submission. Two concurrent approvals cannot create "
        "two payouts, and the idempotency key is persisted before the RazorpayX "
        "call so a retry cannot duplicate one."
    ),
    responses=error_responses(
        (400, "Supplier is no longer valid or payable for this product"),
        (404, "Order not found"),
        (409, "Order is not proposed, or a payout was already requested"),
        (422, "A spend guardrail rejected the order at approval time"),
        (502, "RazorpayX rejected the payout request"),
        (503, "RazorpayX credentials are not configured on this server"),
        (504, "The payout request timed out; outcome unknown, not retried"),
    ),
)
def approve_order(
    order_id: int,
    db: Session = Depends(get_db),
    provider: PaymentProvider = Depends(payment_service.get_payment_provider),
) -> ApprovalResponse:
    result = approval_service.approve_order(db, order_id, payment_provider=provider)

    return ApprovalResponse(
        order=OrderDetail.from_order(result.order),
        payout_requested=True,
        payout_id=result.payout.payout_id if result.payout else None,
        payout_status=result.payout.raw_status if result.payout else None,
        amount_paise=result.revalidated_amount_paise,
        price_changed_since_proposal=result.price_changed_since_proposal,
    )


@router.post(
    "/{order_id}/reject",
    response_model=OrderDetail,
    status_code=status.HTTP_200_OK,
    summary="Decline a proposed order",
    description=(
        "The counterpart to approval, and equally a human decision. Nothing is "
        "sent to RazorpayX, no money moves, and no stock changes.\n\n"
        "Only `proposed` orders can be rejected, and the transition is "
        "terminal: a rejected order can never be revived. A merchant who "
        "changes their mind creates a fresh proposal, which gets its own id, "
        "approval and audit trail.\n\n"
        "`rejected` is deliberately a distinct status from `failed`. Conflating "
        "them would make \"the merchant said no\" indistinguishable from "
        "\"the bank refused the transfer\" in reporting and in the audit trail."
    ),
    responses=error_responses(
        (404, "Order not found"),
        (409, "Order is not proposed, so it cannot be rejected"),
    ),
)
def reject_order(
    order_id: int,
    payload: OrderRejectRequest | None = None,
    db: Session = Depends(get_db),
) -> OrderDetail:
    order = approval_service.reject_order(
        db, order_id, reason=payload.reason if payload else None
    )
    return OrderDetail.from_order(order)
