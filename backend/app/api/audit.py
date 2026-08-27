"""Audit trail endpoints.

Read-only. There is deliberately no POST, PUT, PATCH or DELETE: audit history is
append-only from the application side and immutable from a client's. An audit
trail a client could edit would be worth nothing.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.audit_log import AuditActor
from app.schemas.audit import AuditLogRead, AuditTrailResponse
from app.schemas.error import error_responses
from app.services import audit_service, order_service

router = APIRouter(prefix="/api/audit", tags=["audit"])


@router.get(
    "",
    response_model=list[AuditLogRead],
    summary="Read the audit trail",
    description=(
        "Newest first. Filter by `order_id`, `actor` (agent / human / system), "
        "or `action` (e.g. `PAYOUT_PROCESSED`).\n\n"
        "Agent entries carry the model original reasoning verbatim in "
        "`reasoning_text`. It is never regenerated, so a historical decision "
        "keeps the words it was actually made with."
    ),
)
def list_audit(
    order_id: int | None = Query(default=None),
    actor: AuditActor | None = Query(default=None),
    action: str | None = Query(
        default=None, description="Exact match on the canonical action name."
    ),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> list[AuditLogRead]:
    entries = audit_service.list_events(
        db,
        order_id=order_id,
        actor=actor,
        action=action,
        limit=limit,
        offset=offset,
    )
    return [AuditLogRead.model_validate(entry) for entry in entries]


@router.get(
    "/{order_id}",
    response_model=AuditTrailResponse,
    summary="Read the decision chain for one order",
    description=(
        "Every audit entry for an order, **oldest first**, so the chain reads in "
        "the order it happened: proposal, AI reasoning, human approval, payout "
        "request, payout result, inventory change."
    ),
    responses=error_responses((404, "Order not found")),
)
def get_order_trail(
    order_id: int,
    limit: int = Query(default=200, ge=1, le=500),
    db: Session = Depends(get_db),
) -> AuditTrailResponse:
    # 404 rather than an empty list, so a mistyped id is distinguishable from an
    # order that genuinely has no events yet.
    order_service.get_order(db, order_id)

    entries = audit_service.list_events(db, order_id=order_id, limit=limit)
    chronological = list(reversed(entries))

    return AuditTrailResponse(
        order_id=order_id,
        entries=[AuditLogRead.model_validate(entry) for entry in chronological],
        entry_count=len(chronological),
    )
