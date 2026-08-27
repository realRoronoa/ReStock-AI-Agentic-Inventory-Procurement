"""Purchase order model and its state machine.

State machine (spec sections 20 and 39)::

    PROPOSED ──human approval──▶ APPROVED ──payout.processed──▶ PAID
       │                            │                             │
       │                            ├──payout.failed────▶ FAILED  │
       │                            │                             │
       │                            └──payout.reversed──▶ REVERSED ◀┘
       │
       └──human rejection──▶ REJECTED   (terminal; nothing was ever sent)

There is deliberately **no separate `payout_initiated` status**. The status
vocabulary is fixed at five values, so the payout lifecycle is carried by three
extra columns instead, which together express more than a sixth status could:

===========================================  ==================================
Column state                                 Meaning
===========================================  ==================================
``payout_attempted_at IS NULL``              no payout request sent yet
``attempted_at`` set, ``payout_id IS NULL``  request sent, outcome UNKNOWN
``attempted_at`` and ``payout_id`` set       payout created, awaiting webhook
===========================================  ==================================

``payout_attempted_at`` is claimed with a conditional UPDATE *before* the
RazorpayX call, which is what makes concurrent approvals safe: exactly one
request can win the claim, so a double-click cannot produce two payouts.

An order only ever becomes PAID from a signature-verified ``payout.processed``
webhook — never from a successful payout-creation HTTP response, which only
means RazorpayX accepted the request.
"""

from __future__ import annotations

import enum
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Enum as SAEnum,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base, TimestampMixin, UTCDateTime
from app.core.money import paise_to_rupees

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.models.supplier import Supplier
    from app.models.product import Product


class OrderStatus(str, enum.Enum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    PAID = "paid"
    FAILED = "failed"
    REVERSED = "reversed"
    #: A human looked at the proposal and declined it. Terminal.
    #:
    #: Deliberately distinct from FAILED: nothing was ever sent to RazorpayX, so
    #: conflating the two would make "the bank refused us" indistinguishable
    #: from "the merchant said no" in the audit trail and in reporting.
    REJECTED = "rejected"


#: The only legal status transitions. Enforced in code; anything absent here is
#: rejected rather than silently applied.
ALLOWED_TRANSITIONS: dict[OrderStatus, frozenset[OrderStatus]] = {
    OrderStatus.PROPOSED: frozenset({OrderStatus.APPROVED, OrderStatus.REJECTED}),
    OrderStatus.APPROVED: frozenset(
        {OrderStatus.PAID, OrderStatus.FAILED, OrderStatus.REVERSED}
    ),
    # A processed payout can still be clawed back by the bank.
    OrderStatus.PAID: frozenset({OrderStatus.REVERSED}),
    OrderStatus.FAILED: frozenset(),
    OrderStatus.REVERSED: frozenset(),
    OrderStatus.REJECTED: frozenset(),
}


OrderStatusType = SAEnum(
    OrderStatus,
    name="order_status",
    # VARCHAR + CHECK rather than a native PostgreSQL ENUM type: one migration
    # runs on both SQLite and PostgreSQL, and adding a status later needs no
    # type surgery — only a constraint rewrite.
    native_enum=False,
    length=20,
    # NOT the default. SQLAlchemy sets create_constraint=False, which would
    # leave this a bare VARCHAR validated only in Python — so a raw SQL write,
    # a migration bug, or another service could store any string at all.
    create_constraint=True,
    values_callable=lambda enum_cls: [member.value for member in enum_cls],
    validate_strings=True,
)


class Order(Base, TimestampMixin):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    supplier_id: Mapped[int] = mapped_column(
        ForeignKey("suppliers.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)

    #: Authoritative total in paise, always computed by the backend as
    #: ``quantity * supplier.price_per_unit_paise``. Never accepted from the
    #: LLM or from a client.
    amount_paise: Mapped[int] = mapped_column(BigInteger, nullable=False)

    status: Mapped[OrderStatus] = mapped_column(
        OrderStatusType, nullable=False, default=OrderStatus.PROPOSED, index=True
    )

    #: Primary payment identifier for the supplier transaction (RazorpayX
    #: payout). Unique so a payout can never be attached to two local orders.
    razorpay_payout_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True, unique=True
    )

    #: Value sent in the `X-Payout-Idempotency` header. Generated once, at
    #: approval time, and **persisted before the API call** so that any retry —
    #: by a user, a proxy, or a reconciliation job — reuses the same key and
    #: RazorpayX collapses it onto the original payout instead of creating a
    #: second one. Unique so two orders can never share a key.
    payout_idempotency_key: Mapped[str | None] = mapped_column(
        String(64), nullable=True, unique=True
    )

    #: Set the moment this process claims the right to call RazorpayX, before
    #: the call is made. `payout_attempted_at IS NOT NULL AND
    #: razorpay_payout_id IS NULL` is the "outcome unknown" state: a request went
    #: out and no response came back. Never retried automatically, because a
    #: lost response is not evidence that the money did not move.
    payout_attempted_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime, nullable=True
    )

    #: RazorpayX's own payout status, verbatim (queued, processing, processed,
    #: reversed, failed...). Kept separate from `status`, which is this system's
    #: five-value vocabulary, so the two are never conflated.
    payout_status: Mapped[str | None] = mapped_column(String(32), nullable=True)

    #: Why a payout failed or was reversed, in words a merchant can act on.
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    #: When a human authorised the spend. Also what the daily spend cap is
    #: attributed by: money is committed when it is approved, not when the agent
    #: drafted the proposal.
    approved_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    #: Not produced by the payout flow. Retained for a possible future
    #: collection-side flow; stays NULL rather than being faked.
    razorpay_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    razorpay_payment_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    #: The agents' own words, stored on the order rather than only in the audit
    #: trail. Two reasons: an order detail response is self-contained without a
    #: second query, and a historical decision keeps the reasoning it was
    #: actually made with. Reasoning is never regenerated for display.
    forecast_reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)
    supplier_reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)

    #: Snapshot of the supplier's unit price at proposal time. The authoritative
    #: price for a *new* calculation is always re-read from the supplier row;
    #: this exists so a later reader can see whether the price moved between
    #: proposal and approval.
    unit_price_paise_at_proposal: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True
    )

    product: Mapped["Product"] = relationship(back_populates="orders")
    supplier: Mapped["Supplier"] = relationship(back_populates="orders")

    __table_args__ = (
        CheckConstraint("quantity > 0", name="quantity_positive"),
        CheckConstraint("amount_paise > 0", name="amount_positive"),
    )

    @property
    def amount(self) -> Decimal:
        """Total in rupees, for display only."""
        return paise_to_rupees(self.amount_paise)

    @property
    def payout_initiated(self) -> bool:
        """A payout request has been sent for this order."""
        return self.payout_attempted_at is not None

    @property
    def payout_outcome_unknown(self) -> bool:
        """A payout was sent but no identifier came back.

        The one state that needs human or reconciliation attention: RazorpayX
        may or may not have created the payout.
        """
        return self.payout_attempted_at is not None and self.razorpay_payout_id is None

    @property
    def awaiting_settlement(self) -> bool:
        """Payout created, final outcome not yet reported by webhook."""
        return self.status is OrderStatus.APPROVED and self.razorpay_payout_id is not None

    def can_transition_to(self, new_status: OrderStatus) -> bool:
        return new_status in ALLOWED_TRANSITIONS[self.status]

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<Order id={self.id} product_id={self.product_id} "
            f"supplier_id={self.supplier_id} qty={self.quantity} "
            f"amount_paise={self.amount_paise} status={self.status.value}>"
        )
