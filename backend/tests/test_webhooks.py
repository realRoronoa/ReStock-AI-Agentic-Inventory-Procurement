"""Webhook verification, event handling, and idempotency tests.

This is the endpoint an attacker can reach, and the only one that increases
inventory, so the coverage here is deliberately paranoid:

* signature absent / wrong / unverifiable -> 401, nothing touched
* intermediate events -> never `paid`
* duplicates -> stock increases exactly once
* illegal transitions -> refused, not silently applied
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import (
    InvalidStateTransitionError,
    MalformedWebhookError,
    UnknownPayoutError,
    WebhookSignatureError,
)
from app.core.security import compute_webhook_signature, verify_webhook_signature
from app.models.audit_log import AuditAction, AuditLog
from app.models.order import Order, OrderStatus
from app.models.product import Product
from app.models.webhook_event import WebhookEvent, WebhookOutcome
from app.services import webhook_service
from tests.fakes import TEST_WEBHOOK_SECRET, build_payout_webhook, sign_webhook

PAYOUT_ID = "pout_TESTPAYOUT000001"


@pytest.fixture
def approved_order(db: Session, low_stock_product) -> Order:
    """An order in APPROVED state with a payout id, awaiting settlement."""
    from datetime import datetime, timezone

    order = Order(
        product_id=low_stock_product.id,
        supplier_id=low_stock_product.suppliers[0].id,
        quantity=75,
        amount_paise=360_000,
        status=OrderStatus.APPROVED,
        razorpay_payout_id=PAYOUT_ID,
        payout_idempotency_key="key-0001",
        payout_attempted_at=datetime.now(timezone.utc),
        approved_at=datetime.now(timezone.utc),
        payout_status="queued",
    )
    db.add(order)
    db.commit()
    db.refresh(order)
    return order


def _post(client: TestClient, body, *, secret=TEST_WEBHOOK_SECRET, event_id="evt_001"):
    raw, signature = sign_webhook(body, secret)
    headers = {
        "X-Razorpay-Signature": signature,
        "Content-Type": "application/json",
    }
    if event_id is not None:
        headers["X-Razorpay-Event-Id"] = event_id
    return client.post("/api/webhooks/razorpayx", content=raw, headers=headers)


def _stock(db: Session, product_id: int) -> int:
    db.expire_all()
    product = db.get(Product, product_id)
    return product.current_stock


def _actions(db: Session) -> list[str]:
    return list(db.scalars(select(AuditLog.action).order_by(AuditLog.id)).all())


# --- signature verification -------------------------------------------------


def test_valid_signature_is_accepted() -> None:
    raw = b'{"event":"payout.processed"}'
    signature = compute_webhook_signature(raw, "secret")

    verify_webhook_signature(raw, signature, "secret")  # does not raise


def test_wrong_signature_is_rejected() -> None:
    raw = b'{"event":"payout.processed"}'

    with pytest.raises(WebhookSignatureError):
        verify_webhook_signature(raw, "deadbeef", "secret")


def test_missing_signature_is_rejected() -> None:
    with pytest.raises(WebhookSignatureError):
        verify_webhook_signature(b"{}", None, "secret")


def test_unconfigured_secret_rejects_rather_than_trusts() -> None:
    """Fails closed. An unverifiable webhook could move stock."""
    raw = b"{}"

    with pytest.raises(WebhookSignatureError):
        verify_webhook_signature(raw, "anything", "")


def test_signature_covers_the_exact_bytes() -> None:
    """Re-serialising the JSON changes the digest.

    This is why the endpoint verifies the raw body and never a re-encoded copy.
    """
    body = {"event": "payout.processed", "a": 1, "b": 2}
    original = json.dumps(body).encode()
    reserialised = json.dumps(body, separators=(",", ":"), sort_keys=True).encode()
    signature = compute_webhook_signature(original, "secret")

    verify_webhook_signature(original, signature, "secret")
    with pytest.raises(WebhookSignatureError):
        verify_webhook_signature(reserialised, signature, "secret")


def test_endpoint_rejects_an_invalid_signature_with_401(
    wired_client: TestClient, webhook_secret, approved_order, db: Session
) -> None:
    body = build_payout_webhook("payout.processed", PAYOUT_ID)
    raw = json.dumps(body).encode()

    response = wired_client.post(
        "/api/webhooks/razorpayx",
        content=raw,
        headers={"X-Razorpay-Signature": "0" * 64, "X-Razorpay-Event-Id": "evt_x"},
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "WEBHOOK_SIGNATURE_INVALID"
    # Nothing was touched.
    db.expire_all()
    assert db.get(Order, approved_order.id).status is OrderStatus.APPROVED
    assert _stock(db, approved_order.product_id) == 42
    assert db.scalar(select(func.count(WebhookEvent.id))) == 0


def test_endpoint_rejects_a_missing_signature_header(
    wired_client: TestClient, webhook_secret, approved_order
) -> None:
    body = build_payout_webhook("payout.processed", PAYOUT_ID)

    response = wired_client.post(
        "/api/webhooks/razorpayx", content=json.dumps(body).encode()
    )

    assert response.status_code == 401


def test_endpoint_refuses_when_no_secret_is_configured(
    wired_client: TestClient, approved_order
) -> None:
    """No `webhook_secret` fixture, so the server cannot verify anything."""
    body = build_payout_webhook("payout.processed", PAYOUT_ID)
    raw, signature = sign_webhook(body)

    response = wired_client.post(
        "/api/webhooks/razorpayx",
        content=raw,
        headers={"X-Razorpay-Signature": signature},
    )

    assert response.status_code == 401


# --- malformed payloads -----------------------------------------------------


def test_valid_signature_but_invalid_json_is_400(
    wired_client: TestClient, webhook_secret
) -> None:
    raw, signature = sign_webhook(b"this is not json")

    response = wired_client.post(
        "/api/webhooks/razorpayx",
        content=raw,
        headers={"X-Razorpay-Signature": signature},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "WEBHOOK_MALFORMED"


def test_json_without_an_event_field_is_400(
    wired_client: TestClient, webhook_secret
) -> None:
    response = _post(wired_client, {"payload": {}})

    assert response.status_code == 400


def test_json_array_body_is_400(wired_client: TestClient, webhook_secret) -> None:
    raw, signature = sign_webhook(b"[1,2,3]")

    response = wired_client.post(
        "/api/webhooks/razorpayx",
        content=raw,
        headers={"X-Razorpay-Signature": signature},
    )

    assert response.status_code == 400


def test_payout_event_without_a_payout_id_is_400(
    wired_client: TestClient, webhook_secret
) -> None:
    body = build_payout_webhook("payout.processed", PAYOUT_ID)
    del body["payload"]["payout"]["entity"]["id"]

    response = _post(wired_client, body)

    assert response.status_code == 400


# --- unknown payout ---------------------------------------------------------


def test_unknown_payout_is_404(wired_client: TestClient, webhook_secret) -> None:
    body = build_payout_webhook("payout.processed", "pout_NOSUCHPAYOUT")

    response = _post(wired_client, body)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "WEBHOOK_UNKNOWN_PAYOUT"


def test_unknown_payout_is_recorded_and_replays_consistently(
    wired_client: TestClient, webhook_secret, db: Session
) -> None:
    """A redelivery must get the same answer, not a different one."""
    body = build_payout_webhook("payout.processed", "pout_NOSUCHPAYOUT")

    first = _post(wired_client, body, event_id="evt_unknown")
    second = _post(wired_client, body, event_id="evt_unknown")

    assert first.status_code == 404
    assert second.status_code == 404
    db.expire_all()
    record = db.scalar(
        select(WebhookEvent).where(WebhookEvent.event_id == "evt_unknown")
    )
    assert record is not None
    assert record.outcome is WebhookOutcome.UNKNOWN_PAYOUT
    assert AuditAction.WEBHOOK_REJECTED.value in _actions(db)


# --- payout.processed -------------------------------------------------------


def test_processed_marks_paid_and_increases_stock(
    wired_client: TestClient, webhook_secret, approved_order, db: Session
) -> None:
    body = build_payout_webhook("payout.processed", PAYOUT_ID)

    response = _post(wired_client, body)

    assert response.status_code == 200
    assert response.json()["outcome"] == "applied"
    assert response.json()["order_status"] == "paid"

    db.expire_all()
    order = db.get(Order, approved_order.id)
    assert order.status is OrderStatus.PAID
    assert _stock(db, approved_order.product_id) == 42 + 75

    actions = _actions(db)
    assert AuditAction.PAYOUT_PROCESSED.value in actions
    assert AuditAction.INVENTORY_UPDATED.value in actions


def test_processed_records_the_utr(
    wired_client: TestClient, webhook_secret, approved_order, db: Session
) -> None:
    body = build_payout_webhook("payout.processed", PAYOUT_ID, utr="UTR123456789")

    _post(wired_client, body)

    entry = db.scalar(
        select(AuditLog).where(
            AuditLog.action == AuditAction.PAYOUT_PROCESSED.value
        )
    )
    assert entry is not None
    assert entry.event_metadata["utr"] == "UTR123456789"


# --- duplicate deliveries ---------------------------------------------------


def test_duplicate_processed_does_not_increase_stock_twice(
    wired_client: TestClient, webhook_secret, approved_order, db: Session
) -> None:
    """The single most important assertion in the webhook suite."""
    body = build_payout_webhook("payout.processed", PAYOUT_ID)

    first = _post(wired_client, body, event_id="evt_same")
    second = _post(wired_client, body, event_id="evt_same")

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["duplicate"] is False
    assert second.json()["duplicate"] is True

    assert _stock(db, approved_order.product_id) == 42 + 75  # 117, not 192

    inventory_events = [
        action
        for action in _actions(db)
        if action == AuditAction.INVENTORY_UPDATED.value
    ]
    assert len(inventory_events) == 1


def test_duplicate_with_a_different_event_id_still_does_not_double_increment(
    wired_client: TestClient, webhook_secret, approved_order, db: Session
) -> None:
    """Layer 2: the state machine, not just the event-id constraint.

    A redelivery under a fresh event id gets past the UNIQUE check, so the
    protection has to come from PAID refusing to become PAID again.
    """
    body = build_payout_webhook("payout.processed", PAYOUT_ID)

    first = _post(wired_client, body, event_id="evt_aaa")
    second = _post(wired_client, body, event_id="evt_bbb")

    assert first.json()["outcome"] == "applied"
    assert second.status_code == 200
    assert second.json()["outcome"] == "already_applied"
    assert second.json()["duplicate"] is False

    assert _stock(db, approved_order.product_id) == 117

    inventory_events = [
        action
        for action in _actions(db)
        if action == AuditAction.INVENTORY_UPDATED.value
    ]
    assert len(inventory_events) == 1


def test_three_deliveries_still_increment_once(
    wired_client: TestClient, webhook_secret, approved_order, db: Session
) -> None:
    body = build_payout_webhook("payout.processed", PAYOUT_ID)

    for index in range(3):
        _post(wired_client, body, event_id=f"evt_{index}")

    assert _stock(db, approved_order.product_id) == 117


def test_missing_event_id_header_falls_back_to_a_content_hash(
    wired_client: TestClient, webhook_secret, approved_order, db: Session
) -> None:
    """Byte-identical redeliveries still deduplicate without the header."""
    body = build_payout_webhook("payout.processed", PAYOUT_ID)

    first = _post(wired_client, body, event_id=None)
    second = _post(wired_client, body, event_id=None)

    assert first.json()["event_id"].startswith("sha256:")
    assert second.json()["duplicate"] is True
    assert _stock(db, approved_order.product_id) == 117


# --- payout.failed ----------------------------------------------------------


def test_failed_marks_failed_and_leaves_stock_alone(
    wired_client: TestClient, webhook_secret, approved_order, db: Session
) -> None:
    body = build_payout_webhook(
        "payout.failed",
        PAYOUT_ID,
        failure_reason="Beneficiary account is frozen.",
    )

    response = _post(wired_client, body)

    assert response.status_code == 200
    assert response.json()["order_status"] == "failed"

    db.expire_all()
    order = db.get(Order, approved_order.id)
    assert order.status is OrderStatus.FAILED
    assert order.failure_reason == "Beneficiary account is frozen."
    assert _stock(db, approved_order.product_id) == 42  # unchanged

    actions = _actions(db)
    assert AuditAction.PAYOUT_FAILED.value in actions
    assert AuditAction.INVENTORY_UPDATED.value not in actions


def test_duplicate_failed_keeps_the_order_failed(
    wired_client: TestClient, webhook_secret, approved_order, db: Session
) -> None:
    body = build_payout_webhook("payout.failed", PAYOUT_ID)

    _post(wired_client, body, event_id="evt_f1")
    second = _post(wired_client, body, event_id="evt_f2")

    db.expire_all()
    assert db.get(Order, approved_order.id).status is OrderStatus.FAILED
    assert second.json()["outcome"] == "already_applied"
    assert _stock(db, approved_order.product_id) == 42


def test_rejected_and_cancelled_also_mean_failed(
    wired_client: TestClient, webhook_secret, approved_order, db: Session
) -> None:
    """Documented extension: no money moved, so do not wait forever."""
    body = build_payout_webhook("payout.rejected", PAYOUT_ID)

    response = _post(wired_client, body)

    assert response.json()["order_status"] == "failed"
    assert _stock(db, approved_order.product_id) == 42


# --- payout.reversed --------------------------------------------------------


def test_reversed_marks_reversed_without_touching_stock(
    wired_client: TestClient, webhook_secret, approved_order, db: Session
) -> None:
    body = build_payout_webhook("payout.reversed", PAYOUT_ID)

    response = _post(wired_client, body)

    assert response.status_code == 200
    assert response.json()["order_status"] == "reversed"

    db.expire_all()
    assert db.get(Order, approved_order.id).status is OrderStatus.REVERSED
    assert _stock(db, approved_order.product_id) == 42
    assert AuditAction.PAYOUT_REVERSED.value in _actions(db)


def test_reversal_after_payment_does_not_subtract_stock(
    wired_client: TestClient, webhook_secret, approved_order, db: Session
) -> None:
    """A reversal means money came back, not that the goods did.

    Silently removing stock would be a guess about the physical world, so it
    needs a human.
    """
    _post(
        wired_client,
        build_payout_webhook("payout.processed", PAYOUT_ID),
        event_id="evt_p",
    )
    assert _stock(db, approved_order.product_id) == 117

    response = _post(
        wired_client,
        build_payout_webhook("payout.reversed", PAYOUT_ID),
        event_id="evt_r",
    )

    assert response.json()["order_status"] == "reversed"
    assert _stock(db, approved_order.product_id) == 117  # not decremented

    entry = db.scalar(
        select(AuditLog).where(
            AuditLog.action == AuditAction.PAYOUT_REVERSED.value
        )
    )
    assert "human follow-up" in entry.reasoning_text


def test_duplicate_reversal_is_a_no_op(
    wired_client: TestClient, webhook_secret, approved_order, db: Session
) -> None:
    body = build_payout_webhook("payout.reversed", PAYOUT_ID)

    _post(wired_client, body, event_id="evt_r1")
    second = _post(wired_client, body, event_id="evt_r2")

    assert second.json()["outcome"] == "already_applied"
    db.expire_all()
    assert db.get(Order, approved_order.id).status is OrderStatus.REVERSED


# --- intermediate events ----------------------------------------------------


@pytest.mark.parametrize(
    "event",
    [
        "payout.queued",
        "payout.initiated",
        "payout.pending",
        "payout.processing",
        "payout.updated",
    ],
)
def test_intermediate_events_never_mark_an_order_paid(
    wired_client: TestClient, webhook_secret, approved_order, db: Session, event: str
) -> None:
    """The classic bug: treating 'accepted' as 'paid'."""
    response = _post(wired_client, build_payout_webhook(event, PAYOUT_ID))

    assert response.status_code == 200
    assert response.json()["outcome"] == "acknowledged"
    assert response.json()["order_status"] == "approved"

    db.expire_all()
    order = db.get(Order, approved_order.id)
    assert order.status is OrderStatus.APPROVED
    assert _stock(db, approved_order.product_id) == 42


def test_intermediate_event_still_records_the_razorpay_status(
    wired_client: TestClient, webhook_secret, approved_order, db: Session
) -> None:
    """Progress is visible without being mistaken for settlement."""
    _post(
        wired_client,
        build_payout_webhook("payout.initiated", PAYOUT_ID, status="processing"),
    )

    db.expire_all()
    order = db.get(Order, approved_order.id)
    assert order.payout_status == "processing"
    assert order.status is OrderStatus.APPROVED


def test_queued_then_processed_settles_once(
    wired_client: TestClient, webhook_secret, approved_order, db: Session
) -> None:
    """The realistic sequence."""
    _post(
        wired_client,
        build_payout_webhook("payout.queued", PAYOUT_ID),
        event_id="evt_1",
    )
    _post(
        wired_client,
        build_payout_webhook("payout.initiated", PAYOUT_ID),
        event_id="evt_2",
    )
    _post(
        wired_client,
        build_payout_webhook("payout.processed", PAYOUT_ID),
        event_id="evt_3",
    )

    db.expire_all()
    assert db.get(Order, approved_order.id).status is OrderStatus.PAID
    assert _stock(db, approved_order.product_id) == 117


def test_unrelated_event_types_are_ignored(
    wired_client: TestClient, webhook_secret, approved_order, db: Session
) -> None:
    body = {
        "entity": "event",
        "event": "payout.downtime.started",
        "contains": ["payout.downtime"],
        "payload": {},
        "created_at": 1,
    }

    response = _post(wired_client, body)

    assert response.status_code == 200
    assert response.json()["outcome"] == "ignored"
    db.expire_all()
    assert db.get(Order, approved_order.id).status is OrderStatus.APPROVED


# --- illegal transitions ----------------------------------------------------


def test_processed_after_failed_is_a_conflict(
    wired_client: TestClient, webhook_secret, approved_order, db: Session
) -> None:
    """FAILED is terminal. A later 'processed' is a contradiction, not an update."""
    _post(
        wired_client,
        build_payout_webhook("payout.failed", PAYOUT_ID),
        event_id="evt_f",
    )

    response = _post(
        wired_client,
        build_payout_webhook("payout.processed", PAYOUT_ID),
        event_id="evt_p",
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "INVALID_STATE_TRANSITION"
    db.expire_all()
    assert db.get(Order, approved_order.id).status is OrderStatus.FAILED
    assert _stock(db, approved_order.product_id) == 42


def test_processed_on_a_proposed_order_is_a_conflict(
    wired_client: TestClient, webhook_secret, db: Session, low_stock_product
) -> None:
    """A payout cannot settle an order nobody approved."""
    order = Order(
        product_id=low_stock_product.id,
        supplier_id=low_stock_product.suppliers[0].id,
        quantity=75,
        amount_paise=360_000,
        status=OrderStatus.PROPOSED,
        razorpay_payout_id=PAYOUT_ID,
    )
    db.add(order)
    db.commit()

    response = _post(
        wired_client, build_payout_webhook("payout.processed", PAYOUT_ID)
    )

    assert response.status_code == 409
    db.expire_all()
    assert db.get(Order, order.id).status is OrderStatus.PROPOSED
    assert _stock(db, low_stock_product.id) == 42


# --- service-level behaviour ------------------------------------------------


def test_service_raises_before_parsing_on_a_bad_signature(db: Session) -> None:
    """Verification happens first, so a stranger cannot even probe payout ids."""
    with pytest.raises(WebhookSignatureError):
        webhook_service.process_webhook(
            db,
            raw_body=b'{"event":"payout.processed"}',
            signature="wrong",
        )

    assert db.scalar(select(func.count(WebhookEvent.id))) == 0


def test_terminal_and_intermediate_event_sets_do_not_overlap() -> None:
    """A typo here would let an intermediate event settle an order."""
    assert not (
        set(webhook_service.TERMINAL_EVENTS) & webhook_service.INTERMEDIATE_EVENTS
    )
    assert "payout.queued" not in webhook_service.TERMINAL_EVENTS
    assert "payout.initiated" not in webhook_service.TERMINAL_EVENTS


def test_only_processed_maps_to_paid() -> None:
    paid_events = [
        event
        for event, status in webhook_service.TERMINAL_EVENTS.items()
        if status is OrderStatus.PAID
    ]

    assert paid_events == ["payout.processed"]


def test_webhook_event_rows_are_persisted_with_the_payload(
    wired_client: TestClient, webhook_secret, approved_order, db: Session
) -> None:
    _post(
        wired_client,
        build_payout_webhook("payout.processed", PAYOUT_ID),
        event_id="evt_keep",
    )

    record = db.scalar(
        select(WebhookEvent).where(WebhookEvent.event_id == "evt_keep")
    )
    assert record is not None
    assert record.event_type == "payout.processed"
    assert record.payout_id == PAYOUT_ID
    assert record.related_order_id == approved_order.id
    assert record.outcome is WebhookOutcome.APPLIED
    assert record.payload["event"] == "payout.processed"
