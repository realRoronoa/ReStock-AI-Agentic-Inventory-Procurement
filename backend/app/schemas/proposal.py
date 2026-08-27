"""Proposal schemas.

A proposal *is* an order in `PROPOSED` state — there is no separate proposal
table. Duplicating the concept would mean two ids, two lifecycles, and a
synchronisation problem, all to model the same row twice.

`ProposalRead` carries the complete decision so a merchant can judge it without
another request: the shortfall that triggered it, the quantity and why, the
supplier and why, the authoritative amount, and the limits that were in force.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.core.money import paise_to_rupees
from app.models.order import OrderStatus


class ProposalRead(BaseModel):
    """A reorder proposal, with the full reasoning chain behind it."""

    model_config = ConfigDict(from_attributes=True)

    order_id: int
    status: OrderStatus = Field(description="Always 'proposed' on creation.")

    # --- what triggered it ---
    product_id: int
    product_name: str
    unit: str
    current_stock: int
    reorder_threshold: int
    shortfall: int = Field(description="Units below the reorder threshold.")

    # --- what the forecast agent decided ---
    recommended_quantity: int
    forecast_reasoning: str
    observed_daily_average: float = Field(
        description="Mean daily sales over the history window the model was shown."
    )
    history_days: int
    forecast_provider: str

    # --- what the supplier agent decided ---
    supplier_id: int
    supplier_name: str
    delivery_days: int
    supplier_reasoning: str
    supplier_provider: str
    options_considered: int = Field(
        description="Payable suppliers the model chose between."
    )

    # --- what the backend computed ---
    unit_price_paise: int = Field(
        description="From the database. Never from the model or the client."
    )
    unit_price: Decimal
    total_amount_paise: int = Field(
        description="quantity x unit_price_paise, computed by the backend."
    )
    total_amount: Decimal

    created_at: datetime

    next_step: str = Field(
        default=(
            "Nothing has been spent. Call POST /api/orders/{order_id}/approve to "
            "authorise this purchase. There is no automatic approval."
        )
    )

    @classmethod
    def from_proposal(cls, proposal) -> "ProposalRead":
        order = proposal.order
        return cls(
            order_id=order.id,
            status=order.status,
            product_id=order.product_id,
            product_name=order.product.name,
            unit=order.product.unit,
            current_stock=order.product.current_stock,
            reorder_threshold=order.product.reorder_threshold,
            shortfall=max(
                0, order.product.reorder_threshold - order.product.current_stock
            ),
            recommended_quantity=order.quantity,
            forecast_reasoning=proposal.forecast_reasoning,
            observed_daily_average=proposal.observed_daily_average,
            history_days=proposal.history_days,
            forecast_provider=proposal.forecast_provider,
            supplier_id=order.supplier_id,
            supplier_name=order.supplier.name,
            delivery_days=order.supplier.delivery_days,
            supplier_reasoning=proposal.supplier_reasoning,
            supplier_provider=proposal.supplier_provider,
            options_considered=proposal.options_considered,
            unit_price_paise=order.supplier.price_per_unit_paise,
            unit_price=paise_to_rupees(order.supplier.price_per_unit_paise),
            total_amount_paise=order.amount_paise,
            total_amount=paise_to_rupees(order.amount_paise),
            created_at=order.created_at,
        )
