"""Order and approval schemas.

Design note: `OrderDetail` is deliberately fat. Rendering an order in a
dashboard needs the product, the supplier, the amounts, the AI reasoning, and
the payment state; splitting those across four endpoints would push joins into
the client for no benefit. One request, one complete picture.

There is no writable order schema anywhere in this module. A client can approve
an order by id and nothing else — it cannot submit an amount, a price, a status,
or a payout id.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.core.money import paise_to_rupees
from app.models.order import OrderStatus
from app.schemas.payment import PaymentRead


class OrderProductRead(BaseModel):
    """Product context embedded in an order."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    unit: str
    current_stock: int
    reorder_threshold: int


class OrderSupplierRead(BaseModel):
    """Supplier context embedded in an order."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    price_per_unit_paise: int
    price_per_unit: Decimal
    delivery_days: int
    has_fund_account: bool


class OrderRead(BaseModel):
    """Summary view, used in list responses."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    status: OrderStatus
    product_id: int
    product_name: str
    supplier_id: int
    supplier_name: str
    quantity: int
    unit: str
    amount_paise: int = Field(description="Authoritative total, integer paise.")
    amount: Decimal = Field(description="Same total in INR, as a string.")
    created_at: datetime
    approved_at: datetime | None = None

    @classmethod
    def from_order(cls, order) -> "OrderRead":
        return cls(
            id=order.id,
            status=order.status,
            product_id=order.product_id,
            product_name=order.product.name,
            supplier_id=order.supplier_id,
            supplier_name=order.supplier.name,
            quantity=order.quantity,
            unit=order.product.unit,
            amount_paise=order.amount_paise,
            amount=paise_to_rupees(order.amount_paise),
            created_at=order.created_at,
            approved_at=order.approved_at,
        )


class OrderDetail(BaseModel):
    """Everything about one order, including why it exists."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    status: OrderStatus
    quantity: int
    unit: str

    amount_paise: int
    amount: Decimal
    unit_price_paise: int = Field(
        description="Supplier's current unit price, integer paise."
    )
    unit_price: Decimal
    unit_price_paise_at_proposal: int | None = Field(
        default=None,
        description=(
            "Unit price when the proposal was created. Differs from "
            "unit_price_paise if the supplier changed price since."
        ),
    )

    product: OrderProductRead
    supplier: OrderSupplierRead

    forecast_reasoning: str | None = Field(
        default=None,
        description=(
            "The forecasting model's own words at proposal time, stored "
            "verbatim. Never regenerated, so a historical order keeps the "
            "reasoning it was actually made with."
        ),
    )
    supplier_reasoning: str | None = Field(
        default=None, description="The supplier-selection model's own words."
    )

    payment: PaymentRead

    created_at: datetime
    updated_at: datetime
    approved_at: datetime | None = None

    @classmethod
    def from_order(cls, order) -> "OrderDetail":
        return cls(
            id=order.id,
            status=order.status,
            quantity=order.quantity,
            unit=order.product.unit,
            amount_paise=order.amount_paise,
            amount=paise_to_rupees(order.amount_paise),
            unit_price_paise=order.supplier.price_per_unit_paise,
            unit_price=paise_to_rupees(order.supplier.price_per_unit_paise),
            unit_price_paise_at_proposal=order.unit_price_paise_at_proposal,
            product=OrderProductRead.model_validate(order.product),
            supplier=OrderSupplierRead.model_validate(order.supplier),
            forecast_reasoning=order.forecast_reasoning,
            supplier_reasoning=order.supplier_reasoning,
            payment=PaymentRead(
                payout_id=order.razorpay_payout_id,
                payout_status=order.payout_status,
                payout_requested=order.payout_initiated,
                outcome_unknown=order.payout_outcome_unknown,
                awaiting_settlement=order.awaiting_settlement,
                failure_reason=order.failure_reason,
            ),
            created_at=order.created_at,
            updated_at=order.updated_at,
            approved_at=order.approved_at,
        )


class ApprovalResponse(BaseModel):
    """Result of an approval.

    `order.status` is `approved`, never `paid`. The payout has been *requested*;
    settlement arrives later by webhook. `next_step` says so explicitly so a
    client does not render "paid" off the back of a successful approval.
    """

    order: OrderDetail
    payout_requested: bool
    payout_id: str | None
    payout_status: str | None = Field(
        default=None, description="RazorpayX status at creation, e.g. 'queued'."
    )
    amount_paise: int = Field(
        description=(
            "Amount actually authorised, recomputed from the supplier's current "
            "price at approval time."
        )
    )
    price_changed_since_proposal: bool = Field(
        description=(
            "True if the supplier's price moved between proposal and approval. "
            "The recomputed amount was used."
        )
    )
    next_step: str = Field(
        default=(
            "Payout requested. The order stays 'approved' until a "
            "signature-verified payout.processed webhook arrives, at which point "
            "it becomes 'paid' and stock increases."
        )
    )


class OrderRejectRequest(BaseModel):
    """Optional context for a rejection.

    The only writable field a client has on an order, and it is free text that
    affects no business logic — it is stored in the audit trail so a later
    reader knows why a human declined. Notably absent: any amount, price,
    status, or supplier.
    """

    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(
        default=None,
        max_length=500,
        description="Why the proposal was declined. Recorded in the audit trail.",
    )
