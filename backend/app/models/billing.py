"""Subscription billing.

Real tables, real state, and **no fabricated charge**.

Changing plan writes a `Subscription` row and issues an `Invoice`. What it does
*not* do is take money: collecting a subscription payment needs a
collection-side Razorpay integration (Payment Links or Subscriptions), which is
a different product surface from the payout side this system already implements.
Invoices are therefore issued as `due` unless explicitly marked paid — the same
rule that governs procurement orders applies here, so nothing is ever recorded
as paid because a button was clicked.

`PaymentMethod` stores brand, last four digits and expiry only. A card number
never reaches this backend; in a real integration the gateway returns a token
and these display fields.
"""

from __future__ import annotations

import enum
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Enum as SAEnum,
    ForeignKey,
    Integer,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base, JSONType, TimestampMixin, UTCDateTime
from app.core.money import paise_to_rupees

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.models.user import User


class BillingCycle(str, enum.Enum):
    MONTHLY = "monthly"
    ANNUAL = "annual"


class SubscriptionStatus(str, enum.Enum):
    ACTIVE = "active"
    CANCELLED = "cancelled"


class InvoiceStatus(str, enum.Enum):
    #: Issued, not collected. The honest default — see the module docstring.
    DUE = "due"
    PAID = "paid"
    VOID = "void"


def _portable_enum(enum_cls, name: str, length: int):
    """VARCHAR + CHECK, matching the convention in `app/models/order.py`."""
    return SAEnum(
        enum_cls,
        name=name,
        native_enum=False,
        length=length,
        create_constraint=True,
        values_callable=lambda cls: [member.value for member in cls],
        validate_strings=True,
    )


class Plan(Base, TimestampMixin):
    """A subscription tier. Seeded; not user-creatable."""

    __tablename__ = "plans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(32), nullable=False, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    tagline: Mapped[str] = mapped_column(String(255), nullable=False)

    price_monthly_paise: Mapped[int] = mapped_column(BigInteger, nullable=False)
    #: Per-month price when billed annually. The design shows both.
    price_annual_paise: Mapped[int] = mapped_column(BigInteger, nullable=False)

    #: Feature bullets, in display order.
    features: Mapped[list[str]] = mapped_column(JSONType, nullable=False)

    #: Drives the design's "Most popular" ribbon.
    is_popular: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    cta_label: Mapped[str] = mapped_column(String(64), nullable=False)
    icon: Mapped[str] = mapped_column(
        String(32), nullable=False, comment="Icon key from the design icon set."
    )
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (
        CheckConstraint("price_monthly_paise >= 0", name="price_monthly_non_negative"),
        CheckConstraint("price_annual_paise >= 0", name="price_annual_non_negative"),
    )

    def price_paise(self, cycle: BillingCycle) -> int:
        return (
            self.price_annual_paise
            if cycle is BillingCycle.ANNUAL
            else self.price_monthly_paise
        )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Plan {self.key} monthly={self.price_monthly_paise}>"


class Subscription(Base, TimestampMixin):
    """One per user."""

    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    plan_id: Mapped[int] = mapped_column(
        ForeignKey("plans.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    billing_cycle: Mapped[BillingCycle] = mapped_column(
        _portable_enum(BillingCycle, "billing_cycle", 16), nullable=False
    )
    status: Mapped[SubscriptionStatus] = mapped_column(
        _portable_enum(SubscriptionStatus, "subscription_status", 16),
        nullable=False,
        default=SubscriptionStatus.ACTIVE,
    )

    started_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    current_period_end: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)

    user: Mapped["User"] = relationship(back_populates="subscription")
    plan: Mapped["Plan"] = relationship()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Subscription user_id={self.user_id} plan_id={self.plan_id}>"


class Invoice(Base):
    """A billing document. Issued `due`; never marked paid by a UI action."""

    __tablename__ = "invoices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    #: Human-facing identifier, e.g. INV-1042. Unique so it can be quoted.
    number: Mapped[str] = mapped_column(
        String(32), nullable=False, unique=True, index=True
    )

    amount_paise: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[InvoiceStatus] = mapped_column(
        _portable_enum(InvoiceStatus, "invoice_status", 16),
        nullable=False,
        default=InvoiceStatus.DUE,
    )

    plan_key: Mapped[str] = mapped_column(String(32), nullable=False)
    billing_cycle: Mapped[BillingCycle] = mapped_column(
        _portable_enum(BillingCycle, "invoice_billing_cycle", 16), nullable=False
    )

    issued_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, index=True)
    paid_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)

    #: Free-form context, e.g. why it was issued.
    notes: Mapped[dict[str, Any] | None] = mapped_column(JSONType, nullable=True)

    user: Mapped["User"] = relationship(back_populates="invoices")

    __table_args__ = (
        CheckConstraint("amount_paise >= 0", name="invoice_amount_non_negative"),
    )

    @property
    def amount(self) -> Decimal:
        return paise_to_rupees(self.amount_paise)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Invoice {self.number} {self.status.value}>"


class PaymentMethod(Base, TimestampMixin):
    """Display details for a saved card. **Never a card number.**

    A real integration hands the card to the gateway from the browser and
    receives a token; the backend only ever stores what is needed to render
    "Visa •••• 4242, expires 09/28".
    """

    __tablename__ = "payment_methods"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    brand: Mapped[str] = mapped_column(String(32), nullable=False)
    #: Exactly four digits. Not sensitive on its own, and PCI-permissible.
    last4: Mapped[str] = mapped_column(String(4), nullable=False)
    exp_month: Mapped[int] = mapped_column(Integer, nullable=False)
    exp_year: Mapped[int] = mapped_column(Integer, nullable=False)

    #: Token from the gateway. Null here because no gateway is wired up yet —
    #: left nullable rather than populated with something invented.
    gateway_token: Mapped[str | None] = mapped_column(String(128), nullable=True)

    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    user: Mapped["User"] = relationship(back_populates="payment_methods")

    __table_args__ = (
        CheckConstraint("length(last4) = 4", name="last4_is_four_digits"),
        CheckConstraint("exp_month BETWEEN 1 AND 12", name="exp_month_valid"),
        CheckConstraint("exp_year >= 2000", name="exp_year_valid"),
    )

    @property
    def expiry_display(self) -> str:
        return f"{self.exp_month:02d}/{str(self.exp_year)[-2:]}"

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<PaymentMethod {self.brand} ****{self.last4}>"
