"""Product / supplier / sales-history response schemas.

Money is exposed twice on purpose:

* ``*_paise`` — the exact integer the backend used for arithmetic. Clients that
  need to compute should use this.
* the rupee field — a ``Decimal`` rendered as a JSON *string* by Pydantic, so
  precision survives the wire and no client parses it into a float.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class SalesPoint(BaseModel):
    """One day of observed demand."""

    model_config = ConfigDict(from_attributes=True)

    date: date
    quantity_sold: int


class SupplierRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    product_id: int
    name: str
    price_per_unit_paise: int
    price_per_unit: Decimal = Field(description="Unit price in INR.")
    delivery_days: int
    razorpay_fund_account_id: str | None = Field(
        default=None,
        description=(
            "RazorpayX fund account credited by a payout. Not a credential; "
            "surfaced so a merchant can diagnose a supplier that cannot be paid."
        ),
    )
    has_fund_account: bool


class ProductRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    current_stock: int
    unit: str
    reorder_threshold: int
    is_low_stock: bool = Field(
        description="current_stock < reorder_threshold. Computed, never stored."
    )
    created_at: datetime
    updated_at: datetime


class ProductDetail(ProductRead):
    """A product plus everything needed to reason about reordering it."""

    suppliers: list[SupplierRead] = Field(default_factory=list)
    recent_sales: list[SalesPoint] = Field(
        default_factory=list,
        description="Most recent sales days first.",
    )
