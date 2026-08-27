"""Supplier model.

The supplier row is the **authoritative source of price**. An LLM may suggest
which supplier to use, but it can never create a supplier, change a price, or
invent a delivery time — the backend always re-reads these values from here.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base, TimestampMixin
from app.core.money import paise_to_rupees

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.models.order import Order
    from app.models.product import Product


class Supplier(Base, TimestampMixin):
    __tablename__ = "suppliers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)

    #: Unit price in paise. See app/core/money.py for why this is an integer.
    price_per_unit_paise: Mapped[int] = mapped_column(BigInteger, nullable=False)
    delivery_days: Mapped[int] = mapped_column(Integer, nullable=False)

    #: RazorpayX fund account the payout is credited to. Nullable because a
    #: supplier can be catalogued before its banking details are onboarded;
    #: the approval path refuses to pay a supplier without one.
    razorpay_fund_account_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    product: Mapped["Product"] = relationship(back_populates="suppliers")
    orders: Mapped[list["Order"]] = relationship(back_populates="supplier")

    __table_args__ = (
        UniqueConstraint("product_id", "name", name="product_id_name"),
        CheckConstraint("price_per_unit_paise > 0", name="price_per_unit_positive"),
        CheckConstraint("delivery_days >= 0", name="delivery_days_non_negative"),
    )

    @property
    def price_per_unit(self) -> Decimal:
        """Unit price in rupees, for display only. Never used for arithmetic."""
        return paise_to_rupees(self.price_per_unit_paise)

    @property
    def has_fund_account(self) -> bool:
        return bool(self.razorpay_fund_account_id)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<Supplier id={self.id} product_id={self.product_id} "
            f"name={self.name!r} price_paise={self.price_per_unit_paise} "
            f"delivery_days={self.delivery_days}>"
        )
