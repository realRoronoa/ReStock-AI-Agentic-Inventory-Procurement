"""Received webhook deliveries.

Why this table exists rather than relying on payout id alone:

Razorpay redelivers webhooks. Deduplicating on `payout_id` would be wrong,
because one payout legitimately produces *several* events
(`queued` -> `initiated` -> `processed`). Deduplicating on
`(payout_id, event_type)` would still be wrong, because Razorpay can send
`payout.updated` more than once for genuinely different updates.

Razorpay sends an `X-Razorpay-Event-Id` header that is unique per event and
stable across its retry attempts. That is the correct idempotency key, and a
UNIQUE constraint on it lets the database — not application logic — be the thing
that guarantees one-time processing under concurrent delivery.

This is the *first* of two independent layers protecting inventory from double
increments. The second is the order state machine, which refuses a second
transition into PAID even if a duplicate somehow arrives with a fresh event id.
"""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Enum as SAEnum,
    ForeignKey,
    Index,
    Integer,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, JSONType, UTCDateTime, utcnow


class WebhookOutcome(str, enum.Enum):
    """What processing did with a delivery.

    Persisted so that a redelivery can be answered *consistently*: the stored
    outcome is replayed rather than recomputed, so a caller cannot get a 200 on
    one attempt and a 404 on the next for the same event.
    """

    #: Drove a real order state transition.
    APPLIED = "applied"
    #: Valid and understood, but the order was already in the target state.
    #: The idempotent no-op case.
    ALREADY_APPLIED = "already_applied"
    #: A known intermediate event (queued/initiated/pending/updated). Recorded
    #: against the order but deliberately causes no transition.
    ACKNOWLEDGED = "acknowledged"
    #: Signature was valid but no local order owns this payout.
    UNKNOWN_PAYOUT = "unknown_payout"
    #: An event type this system does not act on (e.g. downtime notices).
    IGNORED = "ignored"
    #: The event contradicted the order's current state.
    CONFLICT = "conflict"


WebhookOutcomeType = SAEnum(
    WebhookOutcome,
    name="webhook_outcome",
    native_enum=False,
    length=24,
    # See app/models/order.py: without this the column is a bare VARCHAR.
    create_constraint=True,
    values_callable=lambda enum_cls: [member.value for member in enum_cls],
    validate_strings=True,
)


class WebhookEvent(Base):
    __tablename__ = "webhook_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    #: `X-Razorpay-Event-Id` when present, otherwise a content hash of the raw
    #: body (`sha256:<digest>`) so exact redeliveries still deduplicate.
    event_id: Mapped[str] = mapped_column(
        String(128), nullable=False, unique=True, index=True
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    payout_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    #: RazorpayX's own payout status from the payload, kept verbatim. Not mapped
    #: onto the local order status, which is a different vocabulary.
    payout_status: Mapped[str | None] = mapped_column(String(32), nullable=True)

    #: SET NULL, like audit_log: the delivery record outlives its order.
    related_order_id: Mapped[int | None] = mapped_column(
        ForeignKey("orders.id", ondelete="SET NULL"), nullable=True, index=True
    )

    outcome: Mapped[WebhookOutcome] = mapped_column(WebhookOutcomeType, nullable=False)

    received_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utcnow, index=True
    )

    #: The parsed payload, for reconciliation and debugging. Contains no
    #: credential: the signature lives in a header and is never stored.
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONType, nullable=True)

    __table_args__ = (
        Index("ix_webhook_events_payout_id_event_type", "payout_id", "event_type"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<WebhookEvent id={self.id} event_id={self.event_id!r} "
            f"type={self.event_type} outcome={self.outcome.value}>"
        )
