"""RazorpayX webhook processing.

This is the only path by which an order becomes PAID and the only path by which
inventory increases. It is also a publicly reachable endpoint, so the ordering
of steps below is a security property, not a style choice.

    raw bytes
        -> verify HMAC signature          (before the JSON is even parsed)
        -> parse JSON
        -> extract event type + payout id
        -> claim the delivery             (UNIQUE event id; duplicates replay)
        -> resolve the local order
        -> apply the state transition     (state machine refuses illegal moves)
        -> increment stock if processed
        -> audit
        -> single commit

Idempotency has two independent layers, deliberately:

1. `webhook_events.event_id` is UNIQUE. Razorpay's `X-Razorpay-Event-Id` is
   stable across its own retries, so a redelivery hits a constraint violation
   and is answered from the stored outcome.
2. The order state machine refuses a second transition into PAID. So even a
   duplicate arriving with a *fresh* event id — a different delivery carrying
   the same fact — cannot increment stock twice.

Layer 2 is the one that actually protects the money. Layer 1 keeps the audit
trail honest and the responses consistent.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import MalformedWebhookError, UnknownPayoutError
from app.core.security import verify_webhook_signature
from app.models.audit_log import AuditAction, AuditActor
from app.models.order import Order, OrderStatus
from app.models.webhook_event import WebhookEvent, WebhookOutcome
from app.services import audit_service, inventory_service, order_service

logger = logging.getLogger("restock.webhooks")

# --- event classification ----------------------------------------------------

#: Events that settle an order. The value is the local status to move to.
TERMINAL_EVENTS: dict[str, OrderStatus] = {
    "payout.processed": OrderStatus.PAID,
    "payout.failed": OrderStatus.FAILED,
    "payout.reversed": OrderStatus.REVERSED,
    # Deliberate extension beyond the three required events. Both mean the money
    # did not move, so leaving the order APPROVED forever would be wrong — a
    # merchant would keep waiting for a settlement that is never coming.
    # Mapped to FAILED, which is exactly "no money moved, decide what to do".
    "payout.rejected": OrderStatus.FAILED,
    "payout.cancelled": OrderStatus.FAILED,
}

#: Events that are real progress reports but must NEVER settle an order.
#: Recorded against the order so a merchant can see movement, with no
#: transition. This is the guard against the classic bug of treating
#: "accepted" as "paid".
INTERMEDIATE_EVENTS: frozenset[str] = frozenset(
    {
        "payout.queued",
        "payout.initiated",
        "payout.pending",
        "payout.processing",
        "payout.updated",
    }
)

#: Events with no payout entity at all; acknowledged and dropped.
IGNORED_EVENTS: frozenset[str] = frozenset(
    {"payout.downtime.started", "payout.downtime.resolved"}
)

#: Audit action per terminal event.
_TERMINAL_AUDIT_ACTION: dict[OrderStatus, AuditAction] = {
    OrderStatus.PAID: AuditAction.PAYOUT_PROCESSED,
    OrderStatus.FAILED: AuditAction.PAYOUT_FAILED,
    OrderStatus.REVERSED: AuditAction.PAYOUT_REVERSED,
}


@dataclass
class WebhookResult:
    """What processing did, for the HTTP layer to render."""

    event_id: str
    event_type: str
    outcome: WebhookOutcome
    payout_id: str | None = None
    order_id: int | None = None
    order_status: str | None = None
    duplicate: bool = False
    detail: str = ""


def _content_hash_event_id(raw_body: bytes) -> str:
    """Fallback idempotency key when no event-id header is present.

    Razorpay always sends `X-Razorpay-Event-Id`, but a proxy could strip it. A
    content hash still deduplicates byte-identical redeliveries, which is the
    common case, and is far better than processing blind.
    """
    return f"sha256:{hashlib.sha256(raw_body).hexdigest()}"


def _extract(body: Any) -> tuple[str, dict[str, Any]]:
    """Pull the event type and payout entity out of a verified payload."""
    if not isinstance(body, dict):
        raise MalformedWebhookError("Webhook body must be a JSON object.")

    event_type = body.get("event")
    if not isinstance(event_type, str) or not event_type:
        raise MalformedWebhookError("Webhook body has no 'event' field.")

    entity = (
        body.get("payload", {}).get("payout", {}).get("entity", {})
        if isinstance(body.get("payload"), dict)
        else {}
    )
    if not isinstance(entity, dict):
        entity = {}

    return event_type, entity


def _replay(db: Session, existing: WebhookEvent) -> WebhookResult:
    """Answer a redelivery from the stored outcome.

    Recomputing would risk a different answer on a second delivery — a 200 now
    and a 404 later for the same event — which makes a webhook endpoint
    impossible to reason about. The first decision stands.
    """
    logger.info(
        "webhook_duplicate event_id=%s type=%s stored_outcome=%s",
        existing.event_id,
        existing.event_type,
        existing.outcome.value,
    )
    audit_service.log_independently(
        db,
        actor=AuditActor.SYSTEM,
        action=AuditAction.WEBHOOK_DUPLICATE_IGNORED,
        reasoning_text=(
            f"Redelivery of webhook event {existing.event_id} "
            f"({existing.event_type}); already processed with outcome "
            f"'{existing.outcome.value}'. No state was changed."
        ),
        related_order_id=existing.related_order_id,
        metadata={
            "event_id": existing.event_id,
            "event_type": existing.event_type,
            "payout_id": existing.payout_id,
            "original_outcome": existing.outcome.value,
        },
    )

    if existing.outcome is WebhookOutcome.UNKNOWN_PAYOUT:
        # Stay consistent with the original answer.
        raise UnknownPayoutError(
            f"No local order matches payout {existing.payout_id}.",
            details={"payout_id": existing.payout_id, "duplicate": True},
        )

    order_status = None
    if existing.related_order_id is not None:
        order = db.get(Order, existing.related_order_id)
        order_status = order.status.value if order else None

    return WebhookResult(
        event_id=existing.event_id,
        event_type=existing.event_type,
        outcome=existing.outcome,
        payout_id=existing.payout_id,
        order_id=existing.related_order_id,
        order_status=order_status,
        duplicate=True,
        detail="Duplicate delivery; already processed. No state changed.",
    )


def process_webhook(
    db: Session,
    *,
    raw_body: bytes,
    signature: str | None,
    event_id_header: str | None = None,
) -> WebhookResult:
    """Verify and process one webhook delivery.

    Raises:
        WebhookSignatureError (401): signature absent, wrong, or unverifiable.
        MalformedWebhookError (400): verified but not a payload we understand.
        UnknownPayoutError (404): verified, but no local order owns the payout.
        ConflictError (409): the event contradicts the order's current state.
    """
    # 1. Authenticity first. Nothing below this line may run on unverified
    #    bytes — not even a lookup, which would otherwise let a stranger probe
    #    which payout ids exist.
    verify_webhook_signature(
        raw_body, signature, settings.RAZORPAY_WEBHOOK_SECRET
    )

    # 2. Only now is it safe to parse.
    try:
        body = json.loads(raw_body)
    except (ValueError, UnicodeDecodeError) as exc:
        logger.warning("webhook_malformed: body is not valid JSON")
        raise MalformedWebhookError(
            "Webhook signature was valid but the body is not valid JSON."
        ) from exc

    event_type, entity = _extract(body)
    event_id = event_id_header or _content_hash_event_id(raw_body)
    payout_id = entity.get("id")
    payout_status = entity.get("status")

    logger.info(
        "webhook_received event_id=%s type=%s payout_id=%s razorpay_status=%s",
        event_id,
        event_type,
        payout_id,
        payout_status,
    )

    # 3. Duplicate check before any work.
    existing = db.scalar(
        select(WebhookEvent).where(WebhookEvent.event_id == event_id)
    )
    if existing is not None:
        return _replay(db, existing)

    record = WebhookEvent(
        event_id=event_id,
        event_type=event_type,
        payout_id=payout_id,
        payout_status=payout_status,
        outcome=WebhookOutcome.IGNORED,
        payload=body,
    )
    db.add(record)

    try:
        result = _dispatch(
            db,
            record=record,
            event_type=event_type,
            payout_id=payout_id,
            payout_status=payout_status,
            entity=entity,
        )
        db.commit()
        return result
    except UnknownPayoutError:
        # Persist the delivery even though it resolved to nothing, so a
        # redelivery replays the same 404 instead of being reprocessed. The
        # record is written on a clean transaction, then the error re-raised.
        db.rollback()
        db.add(
            WebhookEvent(
                event_id=event_id,
                event_type=event_type,
                payout_id=payout_id,
                payout_status=payout_status,
                outcome=WebhookOutcome.UNKNOWN_PAYOUT,
                payload=body,
            )
        )
        audit_service.log(
            db,
            actor=AuditActor.SYSTEM,
            action=AuditAction.WEBHOOK_REJECTED,
            reasoning_text=(
                f"Webhook {event_type} referenced payout {payout_id}, which no "
                "local order owns. Signature was valid; nothing was changed."
            ),
            metadata={
                "event_id": event_id,
                "event_type": event_type,
                "payout_id": payout_id,
                "reason": "unknown_payout",
            },
        )
        db.commit()
        raise
    except IntegrityError:
        # Concurrent delivery of the same event won the race between our SELECT
        # and our INSERT. The UNIQUE constraint is the real guarantee; this
        # branch just turns it into the duplicate answer.
        db.rollback()
        concurrent = db.scalar(
            select(WebhookEvent).where(WebhookEvent.event_id == event_id)
        )
        if concurrent is not None:
            return _replay(db, concurrent)
        raise
    except Exception:
        db.rollback()
        raise


def _dispatch(
    db: Session,
    *,
    record: WebhookEvent,
    event_type: str,
    payout_id: str | None,
    payout_status: str | None,
    entity: dict[str, Any],
) -> WebhookResult:
    """Route a verified event. Adds to the transaction; never commits."""

    # --- events with no payout to act on ---
    if event_type in IGNORED_EVENTS or (
        event_type not in TERMINAL_EVENTS and event_type not in INTERMEDIATE_EVENTS
    ):
        record.outcome = WebhookOutcome.IGNORED
        audit_service.log(
            db,
            actor=AuditActor.SYSTEM,
            action=AuditAction.WEBHOOK_RECEIVED,
            reasoning_text=(
                f"Received '{event_type}', which this system does not act on. "
                "Acknowledged without changing any state."
            ),
            metadata={
                "event_id": record.event_id,
                "event_type": event_type,
                "payout_id": payout_id,
                "outcome": "ignored",
            },
        )
        return WebhookResult(
            event_id=record.event_id,
            event_type=event_type,
            outcome=WebhookOutcome.IGNORED,
            payout_id=payout_id,
            detail=f"Event '{event_type}' is not acted on by this system.",
        )

    if not payout_id:
        raise MalformedWebhookError(
            f"Webhook '{event_type}' contains no payout id at "
            "payload.payout.entity.id.",
            details={"event_type": event_type},
        )

    order = order_service.get_order_by_payout_id(db, payout_id)
    if order is None:
        raise UnknownPayoutError(
            f"No local order matches payout {payout_id}.",
            details={"payout_id": payout_id},
        )

    record.related_order_id = order.id
    # Always record RazorpayX's own view, even for intermediate events, so the
    # merchant can see progress without us pretending it means settlement.
    order.payout_status = payout_status

    if event_type in INTERMEDIATE_EVENTS:
        return _acknowledge_intermediate(
            db, record=record, order=order, event_type=event_type
        )

    return _settle(
        db,
        record=record,
        order=order,
        event_type=event_type,
        target=TERMINAL_EVENTS[event_type],
        entity=entity,
    )


def _acknowledge_intermediate(
    db: Session, *, record: WebhookEvent, order: Order, event_type: str
) -> WebhookResult:
    """Record progress without settling. The 'accepted is not paid' guard."""
    record.outcome = WebhookOutcome.ACKNOWLEDGED

    audit_service.log(
        db,
        actor=AuditActor.SYSTEM,
        action=AuditAction.WEBHOOK_RECEIVED,
        reasoning_text=(
            f"Payout {order.razorpay_payout_id} reported '{event_type}'. This is "
            f"an in-progress status, not settlement, so order {order.id} remains "
            f"{order.status.value}."
        ),
        related_order_id=order.id,
        metadata={
            "event_id": record.event_id,
            "event_type": event_type,
            "payout_id": order.razorpay_payout_id,
            "razorpay_status": record.payout_status,
            "order_status": order.status.value,
            "outcome": "acknowledged",
        },
    )

    logger.info(
        "webhook_intermediate order_id=%s event=%s order_status_unchanged=%s",
        order.id,
        event_type,
        order.status.value,
    )
    return WebhookResult(
        event_id=record.event_id,
        event_type=event_type,
        outcome=WebhookOutcome.ACKNOWLEDGED,
        payout_id=order.razorpay_payout_id,
        order_id=order.id,
        order_status=order.status.value,
        detail=(
            f"'{event_type}' is an intermediate status; the order was not settled."
        ),
    )


def _failure_reason(entity: dict[str, Any], event_type: str) -> str:
    """Merchant-readable explanation, assembled from RazorpayX's own fields."""
    for candidate in (
        entity.get("failure_reason"),
        (entity.get("status_details") or {}).get("description"),
        (entity.get("error") or {}).get("description"),
    ):
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return f"RazorpayX reported '{event_type}' with no stated reason."


def _settle(
    db: Session,
    *,
    record: WebhookEvent,
    order: Order,
    event_type: str,
    target: OrderStatus,
    entity: dict[str, Any],
) -> WebhookResult:
    """Apply a terminal event to the order, and stock if it was processed."""
    action = _TERMINAL_AUDIT_ACTION[target]

    if target is not OrderStatus.PAID:
        order.failure_reason = _failure_reason(entity, event_type)

    reason = {
        OrderStatus.PAID: (
            f"Payout {order.razorpay_payout_id} processed: RazorpayX confirmed "
            f"the supplier was credited. Order {order.id} is paid."
        ),
        OrderStatus.FAILED: (
            f"Payout {order.razorpay_payout_id} did not complete ({event_type}). "
            f"Inventory is unchanged. Reason: {order.failure_reason}"
        ),
        OrderStatus.REVERSED: (
            f"Payout {order.razorpay_payout_id} was reversed. Money that had "
            f"left has come back. Inventory is NOT adjusted automatically; this "
            f"needs human follow-up. Reason: {order.failure_reason}"
        ),
    }[target]

    metadata: dict[str, Any] = {
        "event_id": record.event_id,
        "event_type": event_type,
        "payout_id": order.razorpay_payout_id,
        "razorpay_status": record.payout_status,
        "utr": entity.get("utr"),
        "amount_paise": order.amount_paise,
    }

    try:
        changed = order_service.transition(
            db,
            order,
            target,
            actor=AuditActor.SYSTEM,
            action=action,
            reason=reason,
            metadata=metadata,
        )
    except Exception:
        # An illegal transition (e.g. FAILED -> PAID) is a genuine conflict.
        # Recorded, then surfaced as 409 so the delivery is visible rather than
        # silently swallowed.
        record.outcome = WebhookOutcome.CONFLICT
        logger.error(
            "webhook_conflict order_id=%s status=%s event=%s",
            order.id,
            order.status.value,
            event_type,
        )
        raise

    if not changed:
        # Already in the target state: a duplicate fact arriving under a new
        # event id. This is layer 2 of double-increment protection, and the
        # reason stock cannot go up twice.
        record.outcome = WebhookOutcome.ALREADY_APPLIED
        audit_service.log(
            db,
            actor=AuditActor.SYSTEM,
            action=AuditAction.WEBHOOK_DUPLICATE_IGNORED,
            reasoning_text=(
                f"Order {order.id} is already {order.status.value}; "
                f"'{event_type}' carried no new information. Inventory was not "
                "touched."
            ),
            related_order_id=order.id,
            metadata={**metadata, "outcome": "already_applied"},
        )
        logger.info(
            "webhook_already_applied order_id=%s status=%s event=%s",
            order.id,
            order.status.value,
            event_type,
        )
        return WebhookResult(
            event_id=record.event_id,
            event_type=event_type,
            outcome=WebhookOutcome.ALREADY_APPLIED,
            payout_id=order.razorpay_payout_id,
            order_id=order.id,
            order_status=order.status.value,
            detail=(
                f"Order was already {order.status.value}; no state changed and "
                "inventory was not touched."
            ),
        )

    record.outcome = WebhookOutcome.APPLIED

    # The single place stock ever increases, and only for a processed payout.
    if target is OrderStatus.PAID:
        inventory_service.apply_received_stock(db, order)

    logger.info(
        "webhook_settled order_id=%s event=%s order_status=%s",
        order.id,
        event_type,
        order.status.value,
    )
    return WebhookResult(
        event_id=record.event_id,
        event_type=event_type,
        outcome=WebhookOutcome.APPLIED,
        payout_id=order.razorpay_payout_id,
        order_id=order.id,
        order_status=order.status.value,
        detail=f"Order {order.id} is now {order.status.value}.",
    )
