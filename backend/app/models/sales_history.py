"""Daily sales history — the factual input to demand forecasting."""

from __future__ import annotations

from datetime import date as date_type
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, Date, ForeignKey, Index, Integer, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base, UTCDateTime, utcnow

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.models.product import Product


class SalesHistory(Base):
    __tablename__ = "sales_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), nullable=False
    )
    date: Mapped[date_type] = mapped_column(Date, nullable=False)
    quantity_sold: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)

    product: Mapped["Product"] = relationship(back_populates="sales_history")

    __table_args__ = (
        # One row per product per day. Also makes the seed script naturally
        # idempotent and blocks double-counted demand.
        UniqueConstraint("product_id", "date", name="product_id_date"),
        CheckConstraint("quantity_sold >= 0", name="quantity_sold_non_negative"),
        # Supports the hot query: "last N days of sales for product X".
        Index("ix_sales_history_product_id_date", "product_id", "date"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<SalesHistory product_id={self.product_id} "
            f"date={self.date} qty={self.quantity_sold}>"
        )
