"""Product model."""

from __future__ import annotations

from typing import TYPE_CHECKING

from decimal import Decimal

from sqlalchemy import BigInteger, CheckConstraint, Integer, String
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base, TimestampMixin
from app.core.money import paise_to_rupees

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.models.order import Order
    from app.models.sales_history import SalesHistory
    from app.models.supplier import Supplier


class Product(Base, TimestampMixin):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False, unique=True, index=True)
    current_stock: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    unit: Mapped[str] = mapped_column(String(20), nullable=False)
    reorder_threshold: Mapped[int] = mapped_column(Integer, nullable=False)

    #: Retail price the merchant sells at, in paise. Distinct from a
    #: supplier's cost price: without it, sales *revenue* is not
    #: computable at all, only units moved. Nullable, so a product with no
    #: retail price simply contributes nothing to revenue rather than
    #: contributing a guess.
    selling_price_paise: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True
    )

    sales_history: Mapped[list["SalesHistory"]] = relationship(
        back_populates="product",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    suppliers: Mapped[list["Supplier"]] = relationship(
        back_populates="product",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    orders: Mapped[list["Order"]] = relationship(back_populates="product")

    __table_args__ = (
        CheckConstraint("current_stock >= 0", name="current_stock_non_negative"),
        CheckConstraint("reorder_threshold >= 0", name="reorder_threshold_non_negative"),
        CheckConstraint(
            "selling_price_paise IS NULL OR selling_price_paise > 0",
            name="selling_price_positive",
        ),
    )

    @property
    def selling_price(self) -> Decimal | None:
        """Retail price in rupees, for display only."""
        if self.selling_price_paise is None:
            return None
        return paise_to_rupees(self.selling_price_paise)

    @hybrid_property
    def is_low_stock(self) -> bool:
        """The entire low-stock rule. Deterministic, no LLM involvement.

        Declared as a hybrid so the same rule can be evaluated in Python on a
        loaded object *and* pushed into SQL by the inventory service, removing
        any chance of the two definitions drifting apart.
        """
        return self.current_stock < self.reorder_threshold

    @is_low_stock.expression  # type: ignore[no-redef]
    def is_low_stock(cls):  # noqa: N805
        return cls.current_stock < cls.reorder_threshold

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<Product id={self.id} name={self.name!r} "
            f"stock={self.current_stock} threshold={self.reorder_threshold}>"
        )
