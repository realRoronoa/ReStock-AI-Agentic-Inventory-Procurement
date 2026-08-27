"""RazorpayX webhook endpoint.

The only public, unauthenticated write surface in the system, and the only path
by which an order becomes paid and inventory increases.

Two things about this router are load-bearing:

1. It takes the **raw request body** and hands those exact bytes to signature
   verification. It never parses the JSON first. Parsing and re-serialising
   would change byte-level details (key order, whitespace, unicode escaping)
   and make an authentic signature fail to match.
2. It contains no business logic at all. Verification, idempotency, state
   transitions, and inventory all live in `webhook_service`, so the rules are
   the same whether they are reached over HTTP or from a test.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Header, Request, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.schemas.error import error_responses
from app.schemas.payment import WebhookAck
from app.services import webhook_service

logger = logging.getLogger("restock.api.webhooks")

router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])


@router.post(
    "/razorpayx",
    response_model=WebhookAck,
    status_code=status.HTTP_200_OK,
    summary="Receive a RazorpayX payout webhook",
    description=(
        "Verifies `X-Razorpay-Signature` as HMAC-SHA256 of the raw request "
        "body before parsing anything, then applies the event.\n\n"
        "**Events that settle an order:** `payout.processed` (order becomes "
        "`paid`, stock increases exactly once), `payout.failed` / "
        "`payout.rejected` / `payout.cancelled` (order becomes `failed`, stock "
        "unchanged), `payout.reversed` (order becomes `reversed`, stock **not** "
        "adjusted automatically).\n\n"
        "**Events acknowledged without settling:** `payout.queued`, "
        "`payout.initiated`, `payout.pending`, `payout.processing`, "
        "`payout.updated`. These never produce `paid`.\n\n"
        "**Idempotency:** deduplicated on the `X-Razorpay-Event-Id` header "
        "(content hash as fallback). A redelivery replays the original outcome "
        "and changes nothing."
    ),
    responses=error_responses(
        (400, "Signature valid but the body is malformed"),
        (401, "Missing, invalid, or unverifiable signature"),
        (404, "Signature valid but no local order owns this payout"),
        (409, "Event contradicts the order's current state"),
    ),
)
async def receive_razorpayx_webhook(
    request: Request,
    x_razorpay_signature: str | None = Header(default=None),
    x_razorpay_event_id: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> WebhookAck:
    # The exact bytes Razorpay signed. Do not touch before verification.
    raw_body = await request.body()

    result = webhook_service.process_webhook(
        db,
        raw_body=raw_body,
        signature=x_razorpay_signature,
        event_id_header=x_razorpay_event_id,
    )

    return WebhookAck(
        event_id=result.event_id,
        event_type=result.event_type,
        outcome=result.outcome.value,
        duplicate=result.duplicate,
        order_id=result.order_id,
        order_status=result.order_status,
        detail=result.detail,
    )
