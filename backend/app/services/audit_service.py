"""Append-only audit trail.

Transaction contract — this is the important part of the design:

* `log()` **adds to the session but does not commit.** The audit row therefore
  lands atomically with the state change it describes. An order can never be
  PAID with no record of why, and there can never be a record of a payment that
  did not happen.
* `log_independently()` commits on its own. Used only on failure paths, where
  the business transaction is being rolled back but the *failure itself* must
  survive. Rejecting a proposal and forgetting that it was rejected would be
  worse than either alone.

Nothing in this module accepts or stores a credential. Callers pass identifiers
and outcomes; `metadata` is JSON and is scrubbed of anything secret-shaped
before it is written.
"""

from __future__ import annotations

import logging
from typing import Any, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.audit_log import AuditAction, AuditActor, AuditLog

logger = logging.getLogger("restock.audit")

#: Metadata keys that must never be persisted, however they got there.
_FORBIDDEN_METADATA_KEYS = frozenset(
    {
        "authorization",
        "key_secret",
        "razorpay_key_secret",
        "webhook_secret",
        "razorpay_webhook_secret",
        "api_key",
        "openai_api_key",
        "account_number",
        "razorpay_account_number",
        "signature",
        "x-razorpay-signature",
        "password",
        "token",
    }
)

_REDACTED = "<redacted>"


def _scrub(value: Any, secrets: tuple[str, ...]) -> Any:
    """Recursively drop secret-shaped keys and any literal secret value.

    Defence in depth. No caller is *supposed* to pass a secret, but audit
    metadata is assembled from provider responses and request context, and a
    provider could echo something back. Better to redact here than to discover a
    key sitting in a database years later.
    """
    if isinstance(value, dict):
        return {
            key: (
                _REDACTED
                if str(key).lower() in _FORBIDDEN_METADATA_KEYS
                else _scrub(item, secrets)
            )
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_scrub(item, secrets) for item in value]
    if isinstance(value, str):
        for secret in secrets:
            if secret and secret in value:
                return _REDACTED
    return value


def log(
    db: Session,
    *,
    actor: AuditActor,
    action: AuditAction | str,
    reasoning_text: str | None = None,
    related_order_id: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> AuditLog:
    """Append an audit record to the current transaction (no commit).

    Returns the pending `AuditLog` so a caller can reference it, but the row is
    only durable once the caller's transaction commits — which is the point.
    """
    action_name = action.value if isinstance(action, AuditAction) else str(action)
    scrubbed = _scrub(metadata, settings.all_secrets) if metadata else None

    entry = AuditLog(
        actor=actor,
        action=action_name,
        reasoning_text=reasoning_text,
        related_order_id=related_order_id,
        event_metadata=scrubbed,
    )
    db.add(entry)

    logger.info(
        "audit action=%s actor=%s order_id=%s",
        action_name,
        actor.value,
        related_order_id,
    )
    return entry


def log_independently(
    db: Session,
    *,
    actor: AuditActor,
    action: AuditAction | str,
    reasoning_text: str | None = None,
    related_order_id: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> AuditLog:
    """Append an audit record and commit it immediately.

    For failure paths only. Rolls back whatever partial work is pending first,
    so the audit row is written against a clean transaction and does not drag
    half-finished business state along with it.
    """
    db.rollback()
    entry = log(
        db,
        actor=actor,
        action=action,
        reasoning_text=reasoning_text,
        related_order_id=related_order_id,
        metadata=metadata,
    )
    db.commit()
    return entry


def list_events(
    db: Session,
    *,
    order_id: int | None = None,
    actor: AuditActor | None = None,
    action: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> Sequence[AuditLog]:
    """Read the trail, newest first.

    Ordered by `(timestamp DESC, id DESC)`: several events can share a
    timestamp within one transaction, and the id tie-break keeps the ordering
    stable and reproducible rather than dependent on storage order.
    """
    stmt = select(AuditLog)

    if order_id is not None:
        stmt = stmt.where(AuditLog.related_order_id == order_id)
    if actor is not None:
        stmt = stmt.where(AuditLog.actor == actor)
    if action is not None:
        stmt = stmt.where(AuditLog.action == action)

    stmt = stmt.order_by(AuditLog.timestamp.desc(), AuditLog.id.desc())
    stmt = stmt.limit(limit).offset(offset)

    return db.scalars(stmt).all()


def latest_action_timestamps(
    db: Session,
    action: AuditAction | str,
    *,
    metadata_key: str = "product_id",
) -> dict[Any, Any]:
    """Map `metadata[metadata_key]` -> latest timestamp for a given action.

    Used by the inventory service to suppress repeated identical low-stock
    events. Filtering happens in Python rather than in SQL because JSON access
    syntax differs between SQLite and PostgreSQL, and pushing it down would make
    the query dialect-specific for no benefit at this scale — the query is
    already narrowed by the index on `audit_log.action`.
    """
    action_name = action.value if isinstance(action, AuditAction) else str(action)
    rows = db.scalars(
        select(AuditLog)
        .where(AuditLog.action == action_name)
        .order_by(AuditLog.timestamp.asc(), AuditLog.id.asc())
    ).all()

    latest: dict[Any, Any] = {}
    for row in rows:
        if not row.event_metadata:
            continue
        key = row.event_metadata.get(metadata_key)
        if key is not None:
            latest[key] = row.timestamp
    return latest
