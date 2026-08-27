"""Append-only audit log.

The audit trail must be sufficient to answer "why did this order happen?" by
reconstructing:

    trigger -> AI reasoning -> proposal -> human approval
            -> payment request -> payment result -> inventory change

Rows are never updated or deleted.
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
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, JSONType, UTCDateTime, utcnow


class AuditActor(str, enum.Enum):
    """Who caused the event.

    Small and stable, so it is enforced by a DB CHECK constraint.
    """

    AGENT = "agent"
    HUMAN = "human"
    SYSTEM = "system"


class AuditAction(str, enum.Enum):
    """Canonical action names.

    Intentionally stored as a plain VARCHAR rather than a constrained enum: the
    audit log is append-only history and the set of interesting events grows
    with the product. A DB-level CHECK would force a migration for every new
    event type and would make an old row unreadable if a name were retired.
    This class is the single source of truth for the names that code emits.
    """

    SALE_RECORDED = "SALE_RECORDED"
    LOW_STOCK_DETECTED = "LOW_STOCK_DETECTED"
    INVENTORY_CHECK_COMPLETED = "INVENTORY_CHECK_COMPLETED"

    FORECAST_STARTED = "FORECAST_STARTED"
    FORECAST_GENERATED = "FORECAST_GENERATED"
    FORECAST_FAILED = "FORECAST_FAILED"

    SUPPLIER_SELECTION_STARTED = "SUPPLIER_SELECTION_STARTED"
    SUPPLIER_SELECTED = "SUPPLIER_SELECTED"
    SUPPLIER_SELECTION_FAILED = "SUPPLIER_SELECTION_FAILED"

    PROPOSAL_CREATED = "PROPOSAL_CREATED"
    PROPOSAL_FAILED = "PROPOSAL_FAILED"

    ORDER_APPROVED = "ORDER_APPROVED"
    ORDER_APPROVAL_REJECTED = "ORDER_APPROVAL_REJECTED"
    #: A human declined the proposal. Distinct from ORDER_APPROVAL_REJECTED,
    #: which means the backend refused an approval attempt.
    ORDER_REJECTED_BY_HUMAN = "ORDER_REJECTED_BY_HUMAN"

    RAZORPAY_PAYOUT_REQUESTED = "RAZORPAY_PAYOUT_REQUESTED"
    RAZORPAY_PAYOUT_CREATED = "RAZORPAY_PAYOUT_CREATED"
    RAZORPAY_PAYOUT_FAILED = "RAZORPAY_PAYOUT_FAILED"
    RAZORPAY_PAYOUT_OUTCOME_UNKNOWN = "RAZORPAY_PAYOUT_OUTCOME_UNKNOWN"

    WEBHOOK_RECEIVED = "WEBHOOK_RECEIVED"
    WEBHOOK_REJECTED = "WEBHOOK_REJECTED"
    WEBHOOK_DUPLICATE_IGNORED = "WEBHOOK_DUPLICATE_IGNORED"

    PAYOUT_PROCESSED = "PAYOUT_PROCESSED"
    PAYOUT_FAILED = "PAYOUT_FAILED"
    PAYOUT_REVERSED = "PAYOUT_REVERSED"

    INVENTORY_UPDATED = "INVENTORY_UPDATED"

    GUARDRAIL_VIOLATION = "GUARDRAIL_VIOLATION"


AuditActorType = SAEnum(
    AuditActor,
    name="audit_actor",
    native_enum=False,
    length=16,
    # See app/models/order.py: without this the column is a bare VARCHAR.
    create_constraint=True,
    values_callable=lambda enum_cls: [member.value for member in enum_cls],
    validate_strings=True,
)


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utcnow, index=True
    )
    actor: Mapped[AuditActor] = mapped_column(AuditActorType, nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    #: Free-text explanation. For agent events this holds the model's own
    #: reasoning verbatim, so a human can later see what it argued.
    reasoning_text: Mapped[str | None] = mapped_column(Text, nullable=True)

    #: SET NULL rather than CASCADE: history must survive its subject.
    related_order_id: Mapped[int | None] = mapped_column(
        ForeignKey("orders.id", ondelete="SET NULL"), nullable=True, index=True
    )

    #: Structured context (product id, supplier id, amounts, payout ids, raw
    #: model output, error codes). Mapped to a column literally named
    #: "metadata"; the attribute is renamed because `metadata` is reserved on
    #: SQLAlchemy declarative classes.
    event_metadata: Mapped[dict[str, Any] | None] = mapped_column(
        "metadata", JSONType, nullable=True
    )

    __table_args__ = (
        # Supports the audit API's default "newest first, filtered by action".
        Index("ix_audit_log_action_timestamp", "action", "timestamp"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<AuditLog id={self.id} actor={self.actor.value} "
            f"action={self.action} order_id={self.related_order_id}>"
        )
