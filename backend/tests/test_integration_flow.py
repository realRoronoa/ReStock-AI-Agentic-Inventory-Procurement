"""End-to-end workflow tests — the most important tests in the project.

Each of these drives the whole system through the public HTTP API, using the
seeded demo dataset and test doubles for RazorpayX and the LLM. Nothing reaches
inside a service to nudge state along: if the flow works here, it works.

Three flows, matching the three ways a payout can end:

* `test_happy_path_*`      — processed  -> paid, stock +1x, full audit chain
* `test_failed_payment_*`  — failed     -> failed, stock unchanged
* `test_reversed_payment_*`— reversed   -> reversed, stock NOT adjusted

Plus the duplicate-webhook assertion, which is what stops inventory drifting
upward every time Razorpay retries a delivery.
"""

from __future__ import annotations

import json
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.audit_log import AuditAction, AuditActor, AuditLog
from app.models.order import Order, OrderStatus
from app.models.product import Product
from app.models.webhook_event import WebhookEvent
from app.seed.seed import seed
from tests.fakes import build_payout_webhook, sign_webhook

PAYOUT_ID = "pout_INTEGRATION0001"

pytestmark = pytest.mark.integration


@pytest.fixture
def seeded(db: Session) -> Session:
    """The real demo dataset, loaded through the real seed script."""
    seed(db, today=date(2025, 6, 1))
    return db


def _milk(db: Session) -> Product:
    db.expire_all()
    product = db.scalar(select(Product).where(Product.name == "Milk"))
    assert product is not None
    return product


def _send_webhook(
    client: TestClient, event: str, *, event_id: str, payout_id: str = PAYOUT_ID, **kwargs
):
    body = build_payout_webhook(event, payout_id, **kwargs)
    raw, signature = sign_webhook(body)
    return client.post(
        "/api/webhooks/razorpayx",
        content=raw,
        headers={
            "X-Razorpay-Signature": signature,
            "X-Razorpay-Event-Id": event_id,
            "Content-Type": "application/json",
        },
    )


def _trail(client: TestClient, order_id: int) -> list[str]:
    body = client.get(f"/api/audit/{order_id}").json()
    return [entry["action"] for entry in body["entries"]]


# ============================================================================
# FLOW 1 — the happy path, plus duplicate-webhook protection
# ============================================================================


def test_happy_path_low_stock_to_paid_with_inventory_updated_once(
    wired_client: TestClient, seeded: Session, webhook_secret, providers
) -> None:
    """seed -> low stock -> forecast -> supplier -> proposal -> approval
    -> payout -> webhook -> paid -> stock +75 -> full audit trail,
    then the same webhook again and stock does NOT move.
    """
    providers.forecast.quantity = 75
    providers.payment.payout_id = PAYOUT_ID

    milk = _milk(seeded)
    starting_stock = milk.current_stock
    assert starting_stock == 42

    # --- 1. inventory check finds Milk ---
    check = wired_client.post("/api/inventory/check")
    assert check.status_code == 200
    low_names = {item["name"] for item in check.json()["low_stock_products"]}
    assert "Milk" in low_names

    # --- 2. proposal ---
    proposal = wired_client.post(f"/api/proposals/product/{milk.id}")
    assert proposal.status_code == 201
    body = proposal.json()
    order_id = body["order_id"]

    assert body["status"] == "proposed"
    assert body["recommended_quantity"] == 75
    assert body["forecast_reasoning"]
    assert body["supplier_reasoning"]
    # Amount computed by the backend from the database price.
    assert body["total_amount_paise"] == 75 * body["unit_price_paise"]

    # Nothing has been spent.
    assert providers.payment.call_count == 0
    assert _milk(seeded).current_stock == starting_stock

    # --- 3. human approval ---
    approval = wired_client.post(f"/api/orders/{order_id}/approve")
    assert approval.status_code == 200
    approved = approval.json()

    assert approved["order"]["status"] == "approved"
    assert approved["payout_id"] == PAYOUT_ID
    assert providers.payment.call_count == 1

    # Crucially NOT paid, and stock has not moved.
    assert approved["order"]["status"] != "paid"
    assert _milk(seeded).current_stock == starting_stock

    # --- 4. the payout progresses (must not settle) ---
    queued = _send_webhook(wired_client, "payout.queued", event_id="evt_q")
    assert queued.status_code == 200
    assert queued.json()["order_status"] == "approved"
    assert _milk(seeded).current_stock == starting_stock

    # --- 5. payout.processed settles it ---
    processed = _send_webhook(wired_client, "payout.processed", event_id="evt_p1")
    assert processed.status_code == 200
    assert processed.json()["outcome"] == "applied"
    assert processed.json()["order_status"] == "paid"

    seeded.expire_all()
    order = seeded.get(Order, order_id)
    assert order.status is OrderStatus.PAID
    assert _milk(seeded).current_stock == starting_stock + 75 == 117

    # --- 6. THE duplicate assertion ---
    duplicate = _send_webhook(wired_client, "payout.processed", event_id="evt_p1")
    assert duplicate.status_code == 200
    assert duplicate.json()["duplicate"] is True
    assert _milk(seeded).current_stock == 117, "stock must not increase twice"

    # And under a fresh event id, caught by the state machine instead.
    replay = _send_webhook(wired_client, "payout.processed", event_id="evt_p2")
    assert replay.json()["outcome"] == "already_applied"
    assert _milk(seeded).current_stock == 117

    inventory_events = seeded.scalar(
        select(func.count(AuditLog.id)).where(
            AuditLog.action == AuditAction.INVENTORY_UPDATED.value
        )
    )
    assert inventory_events == 1

    # --- 7. the audit trail reconstructs the whole decision ---
    trail = _trail(wired_client, order_id)
    for expected in (
        AuditAction.PROPOSAL_CREATED.value,
        AuditAction.ORDER_APPROVED.value,
        AuditAction.RAZORPAY_PAYOUT_REQUESTED.value,
        AuditAction.RAZORPAY_PAYOUT_CREATED.value,
        AuditAction.PAYOUT_PROCESSED.value,
        AuditAction.INVENTORY_UPDATED.value,
    ):
        assert expected in trail, f"missing audit event: {expected}"

    # Chronological order: proposal before approval before payment before stock.
    assert trail.index(AuditAction.PROPOSAL_CREATED.value) < trail.index(
        AuditAction.ORDER_APPROVED.value
    )
    assert trail.index(AuditAction.ORDER_APPROVED.value) < trail.index(
        AuditAction.RAZORPAY_PAYOUT_CREATED.value
    )
    assert trail.index(AuditAction.RAZORPAY_PAYOUT_CREATED.value) < trail.index(
        AuditAction.PAYOUT_PROCESSED.value
    )
    assert trail.index(AuditAction.PAYOUT_PROCESSED.value) < trail.index(
        AuditAction.INVENTORY_UPDATED.value
    )


def test_happy_path_audit_answers_why_did_this_order_happen(
    wired_client: TestClient, seeded: Session, webhook_secret, providers
) -> None:
    """The audit trail must be readable as an explanation, not just a log."""
    providers.forecast.quantity = 75
    providers.forecast.reasoning = "Demand is a steady 20 litres a day."
    providers.supplier.reasoning = "Two-day delivery is worth the 9% premium."
    providers.payment.payout_id = PAYOUT_ID

    milk = _milk(seeded)
    # The trigger event: the sweep is what records LOW_STOCK_DETECTED.
    wired_client.post("/api/inventory/check")
    order_id = wired_client.post(
        f"/api/proposals/product/{milk.id}"
    ).json()["order_id"]
    wired_client.post(f"/api/orders/{order_id}/approve")
    _send_webhook(wired_client, "payout.processed", event_id="evt_p")

    entries = wired_client.get(f"/api/audit/{order_id}").json()["entries"]
    by_action = {entry["action"]: entry for entry in entries}

    # The trigger, in the low-stock event for this product.
    global_trail = wired_client.get(
        "/api/audit", params={"action": AuditAction.LOW_STOCK_DETECTED.value}
    ).json()
    assert any(
        entry["metadata"]["product_id"] == milk.id for entry in global_trail
    )

    # The AI reasoning, verbatim, attributed to the agent.
    forecast_entry = wired_client.get(
        "/api/audit", params={"action": AuditAction.FORECAST_GENERATED.value}
    ).json()[0]
    assert forecast_entry["actor"] == "agent"
    assert forecast_entry["reasoning_text"] == "Demand is a steady 20 litres a day."

    supplier_entry = wired_client.get(
        "/api/audit", params={"action": AuditAction.SUPPLIER_SELECTED.value}
    ).json()[0]
    assert supplier_entry["actor"] == "agent"
    assert "9% premium" in supplier_entry["reasoning_text"]

    # The human decision.
    assert by_action[AuditAction.ORDER_APPROVED.value]["actor"] == "human"

    # The money, and the resulting stock change.
    assert by_action[AuditAction.RAZORPAY_PAYOUT_CREATED.value]["metadata"][
        "payout_id"
    ] == PAYOUT_ID
    inventory = by_action[AuditAction.INVENTORY_UPDATED.value]["metadata"]
    assert inventory["stock_before"] == 42
    assert inventory["stock_after"] == 117
    assert inventory["quantity_added"] == 75


def test_reasoning_is_not_regenerated_when_reading_an_old_order(
    wired_client: TestClient, seeded: Session, webhook_secret, providers
) -> None:
    """A historical decision keeps the words it was actually made with."""
    providers.forecast.reasoning = "Original forecast reasoning."
    providers.supplier.reasoning = "Original supplier reasoning."

    milk = _milk(seeded)
    order_id = wired_client.post(
        f"/api/proposals/product/{milk.id}"
    ).json()["order_id"]

    # The model would now say something different.
    providers.forecast.reasoning = "Completely different later reasoning."
    providers.supplier.reasoning = "Different later supplier reasoning."

    detail = wired_client.get(f"/api/orders/{order_id}").json()

    assert detail["forecast_reasoning"] == "Original forecast reasoning."
    assert detail["supplier_reasoning"] == "Original supplier reasoning."
    assert providers.forecast.calls, "the provider was called once, at proposal time"
    assert len(providers.forecast.calls) == 1


# ============================================================================
# FLOW 2 — failed payment
# ============================================================================


def test_failed_payment_leaves_inventory_untouched(
    wired_client: TestClient, seeded: Session, webhook_secret, providers
) -> None:
    """proposal -> approval -> payout -> payout.failed
    -> order failed, stock unchanged, failure audited and readable.
    """
    providers.forecast.quantity = 75
    providers.payment.payout_id = PAYOUT_ID

    milk = _milk(seeded)
    order_id = wired_client.post(
        f"/api/proposals/product/{milk.id}"
    ).json()["order_id"]
    wired_client.post(f"/api/orders/{order_id}/approve")
    assert _milk(seeded).current_stock == 42

    failed = _send_webhook(
        wired_client,
        "payout.failed",
        event_id="evt_fail",
        failure_reason="Beneficiary bank declined the transfer.",
    )

    assert failed.status_code == 200
    assert failed.json()["order_status"] == "failed"

    seeded.expire_all()
    order = seeded.get(Order, order_id)
    assert order.status is OrderStatus.FAILED
    assert _milk(seeded).current_stock == 42, "stock must not change on failure"

    trail = _trail(wired_client, order_id)
    assert AuditAction.PAYOUT_FAILED.value in trail
    assert AuditAction.INVENTORY_UPDATED.value not in trail

    # The merchant can see the reason through the API.
    detail = wired_client.get(f"/api/orders/{order_id}").json()
    assert detail["status"] == "failed"
    assert detail["payment"]["failure_reason"] == (
        "Beneficiary bank declined the transfer."
    )


def test_failed_payment_is_not_silently_retried(
    wired_client: TestClient, seeded: Session, webhook_secret, providers
) -> None:
    """No automatic second payout, and no automatic supplier switch."""
    providers.payment.payout_id = PAYOUT_ID
    milk = _milk(seeded)
    order_id = wired_client.post(
        f"/api/proposals/product/{milk.id}"
    ).json()["order_id"]
    wired_client.post(f"/api/orders/{order_id}/approve")
    calls_after_approval = providers.payment.call_count

    _send_webhook(wired_client, "payout.failed", event_id="evt_fail")

    assert providers.payment.call_count == calls_after_approval

    # And the failed order cannot be approved again into a new payout.
    retry = wired_client.post(f"/api/orders/{order_id}/approve")
    assert retry.status_code == 409
    assert providers.payment.call_count == calls_after_approval


def test_merchant_can_propose_an_alternative_supplier_after_failure(
    wired_client: TestClient, seeded: Session, webhook_secret, providers
) -> None:
    """The alternative-supplier flow, driven entirely by a human.

    A new proposal is a new order with its own id, approval, payout and audit
    trail. Nothing about the failed order is reused or mutated.
    """
    providers.forecast.quantity = 75
    providers.payment.payout_id = PAYOUT_ID

    milk = _milk(seeded)
    suppliers = sorted(milk.suppliers, key=lambda s: s.price_per_unit_paise)
    cheap, fast = suppliers[0], suppliers[-1]

    # First attempt, via the cheaper supplier, fails.
    providers.supplier.supplier_id = cheap.id
    first_id = wired_client.post(
        f"/api/proposals/product/{milk.id}"
    ).json()["order_id"]
    wired_client.post(f"/api/orders/{first_id}/approve")
    _send_webhook(wired_client, "payout.failed", event_id="evt_fail")

    seeded.expire_all()
    assert seeded.get(Order, first_id).status is OrderStatus.FAILED

    # The merchant chooses again; the agent now recommends the faster supplier.
    providers.supplier.supplier_id = fast.id
    providers.payment.payout_id = "pout_SECONDATTEMPT"
    second = wired_client.post(f"/api/proposals/product/{milk.id}")
    assert second.status_code == 201
    second_id = second.json()["order_id"]

    assert second_id != first_id
    assert second.json()["supplier_id"] == fast.id

    approval = wired_client.post(f"/api/orders/{second_id}/approve")
    assert approval.status_code == 200
    assert approval.json()["payout_id"] == "pout_SECONDATTEMPT"

    # Two distinct payouts, two distinct orders, two distinct trails.
    assert providers.payment.call_count == 2
    assert len(providers.payment.distinct_payout_ids) == 2
    assert _trail(wired_client, first_id) != _trail(wired_client, second_id)

    # Settling the second one moves stock exactly once.
    _send_webhook(
        wired_client,
        "payout.processed",
        event_id="evt_second",
        payout_id="pout_SECONDATTEMPT",
    )
    assert _milk(seeded).current_stock == 117

    seeded.expire_all()
    assert seeded.get(Order, first_id).status is OrderStatus.FAILED
    assert seeded.get(Order, second_id).status is OrderStatus.PAID


def test_duplicate_failed_webhook_changes_nothing(
    wired_client: TestClient, seeded: Session, webhook_secret, providers
) -> None:
    providers.payment.payout_id = PAYOUT_ID
    milk = _milk(seeded)
    order_id = wired_client.post(
        f"/api/proposals/product/{milk.id}"
    ).json()["order_id"]
    wired_client.post(f"/api/orders/{order_id}/approve")

    _send_webhook(wired_client, "payout.failed", event_id="evt_f1")
    second = _send_webhook(wired_client, "payout.failed", event_id="evt_f2")

    assert second.json()["outcome"] == "already_applied"
    seeded.expire_all()
    assert seeded.get(Order, order_id).status is OrderStatus.FAILED
    assert _milk(seeded).current_stock == 42


# ============================================================================
# FLOW 3 — reversed payment
# ============================================================================


def test_reversed_payment_does_not_blindly_adjust_inventory(
    wired_client: TestClient, seeded: Session, webhook_secret, providers
) -> None:
    """proposal -> approval -> payout -> payout.reversed
    -> order reversed, stock NOT modified, reversal audited for follow-up.
    """
    providers.forecast.quantity = 75
    providers.payment.payout_id = PAYOUT_ID

    milk = _milk(seeded)
    order_id = wired_client.post(
        f"/api/proposals/product/{milk.id}"
    ).json()["order_id"]
    wired_client.post(f"/api/orders/{order_id}/approve")

    reversed_response = _send_webhook(
        wired_client,
        "payout.reversed",
        event_id="evt_rev",
        status_description="Payout reversed by the beneficiary bank.",
    )

    assert reversed_response.status_code == 200
    assert reversed_response.json()["order_status"] == "reversed"

    seeded.expire_all()
    order = seeded.get(Order, order_id)
    assert order.status is OrderStatus.REVERSED
    assert _milk(seeded).current_stock == 42

    trail = _trail(wired_client, order_id)
    assert AuditAction.PAYOUT_REVERSED.value in trail
    assert AuditAction.INVENTORY_UPDATED.value not in trail


def test_reversal_after_a_processed_payout_keeps_the_stock_it_added(
    wired_client: TestClient, seeded: Session, webhook_secret, providers
) -> None:
    """Money came back; that is not evidence the goods did not arrive.

    Subtracting stock would be the system guessing about the physical world, so
    it is left alone and flagged for a human.
    """
    providers.forecast.quantity = 75
    providers.payment.payout_id = PAYOUT_ID

    milk = _milk(seeded)
    order_id = wired_client.post(
        f"/api/proposals/product/{milk.id}"
    ).json()["order_id"]
    wired_client.post(f"/api/orders/{order_id}/approve")

    _send_webhook(wired_client, "payout.processed", event_id="evt_p")
    assert _milk(seeded).current_stock == 117

    _send_webhook(wired_client, "payout.reversed", event_id="evt_r")

    seeded.expire_all()
    assert seeded.get(Order, order_id).status is OrderStatus.REVERSED
    assert _milk(seeded).current_stock == 117, "stock is not silently unwound"

    entry = seeded.scalar(
        select(AuditLog).where(
            AuditLog.action == AuditAction.PAYOUT_REVERSED.value
        )
    )
    assert "human follow-up" in entry.reasoning_text
    assert "NOT adjusted automatically" in entry.reasoning_text


def test_duplicate_reversal_changes_nothing(
    wired_client: TestClient, seeded: Session, webhook_secret, providers
) -> None:
    providers.payment.payout_id = PAYOUT_ID
    milk = _milk(seeded)
    order_id = wired_client.post(
        f"/api/proposals/product/{milk.id}"
    ).json()["order_id"]
    wired_client.post(f"/api/orders/{order_id}/approve")

    _send_webhook(wired_client, "payout.reversed", event_id="evt_r1")
    second = _send_webhook(wired_client, "payout.reversed", event_id="evt_r2")

    assert second.json()["outcome"] == "already_applied"
    seeded.expire_all()
    assert seeded.get(Order, order_id).status is OrderStatus.REVERSED


# ============================================================================
# Cross-cutting guarantees
# ============================================================================


def test_unsigned_webhook_cannot_settle_an_order(
    wired_client: TestClient, seeded: Session, webhook_secret, providers
) -> None:
    """The attack: forge a payout.processed to get free stock."""
    providers.payment.payout_id = PAYOUT_ID
    milk = _milk(seeded)
    order_id = wired_client.post(
        f"/api/proposals/product/{milk.id}"
    ).json()["order_id"]
    wired_client.post(f"/api/orders/{order_id}/approve")

    forged = json.dumps(
        build_payout_webhook("payout.processed", PAYOUT_ID)
    ).encode()

    for headers in (
        {},
        {"X-Razorpay-Signature": "0" * 64},
        {"X-Razorpay-Signature": ""},
    ):
        response = wired_client.post(
            "/api/webhooks/razorpayx", content=forged, headers=headers
        )
        assert response.status_code == 401

    seeded.expire_all()
    assert seeded.get(Order, order_id).status is OrderStatus.APPROVED
    assert _milk(seeded).current_stock == 42
    assert seeded.scalar(select(func.count(WebhookEvent.id))) == 0


def test_stock_only_ever_moves_through_the_webhook_path(
    wired_client: TestClient, seeded: Session, webhook_secret, providers
) -> None:
    """Every other endpoint is inert with respect to inventory."""
    providers.payment.payout_id = PAYOUT_ID
    milk = _milk(seeded)
    before = milk.current_stock

    wired_client.post("/api/inventory/check")
    wired_client.get("/api/inventory/low-stock")
    wired_client.get("/api/products")
    wired_client.get(f"/api/products/{milk.id}")
    order_id = wired_client.post(
        f"/api/proposals/product/{milk.id}"
    ).json()["order_id"]
    wired_client.get("/api/orders")
    wired_client.get(f"/api/orders/{order_id}")
    wired_client.get("/api/audit")
    wired_client.post(f"/api/orders/{order_id}/approve")
    wired_client.get(f"/api/audit/{order_id}")

    assert _milk(seeded).current_stock == before

    _send_webhook(wired_client, "payout.processed", event_id="evt_only")

    assert _milk(seeded).current_stock == before + 75


def test_full_order_detail_is_available_in_one_request(
    wired_client: TestClient, seeded: Session, webhook_secret, providers
) -> None:
    """A dashboard should not need four calls to render one order."""
    providers.payment.payout_id = PAYOUT_ID
    milk = _milk(seeded)
    order_id = wired_client.post(
        f"/api/proposals/product/{milk.id}"
    ).json()["order_id"]
    wired_client.post(f"/api/orders/{order_id}/approve")
    _send_webhook(wired_client, "payout.processed", event_id="evt_p")

    detail = wired_client.get(f"/api/orders/{order_id}").json()

    assert detail["status"] == "paid"
    assert detail["product"]["name"] == "Milk"
    assert detail["product"]["current_stock"] == 117
    assert detail["supplier"]["name"]
    assert detail["forecast_reasoning"]
    assert detail["supplier_reasoning"]
    assert detail["payment"]["payout_id"] == PAYOUT_ID
    assert detail["payment"]["payout_status"] == "processed"
    assert detail["payment"]["awaiting_settlement"] is False
    assert detail["amount_paise"] == detail["quantity"] * detail["unit_price_paise"]


def test_order_status_filter_reflects_the_lifecycle(
    wired_client: TestClient, seeded: Session, webhook_secret, providers
) -> None:
    providers.payment.payout_id = PAYOUT_ID
    milk = _milk(seeded)
    order_id = wired_client.post(
        f"/api/proposals/product/{milk.id}"
    ).json()["order_id"]

    assert [
        item["id"]
        for item in wired_client.get(
            "/api/orders", params={"status": "proposed"}
        ).json()
    ] == [order_id]

    wired_client.post(f"/api/orders/{order_id}/approve")
    assert [
        item["id"]
        for item in wired_client.get(
            "/api/orders", params={"status": "approved"}
        ).json()
    ] == [order_id]

    _send_webhook(wired_client, "payout.processed", event_id="evt_p")
    assert [
        item["id"]
        for item in wired_client.get("/api/orders", params={"status": "paid"}).json()
    ] == [order_id]
    assert wired_client.get("/api/orders", params={"status": "proposed"}).json() == []


def test_audit_trail_is_not_writable_through_the_api(
    wired_client: TestClient, seeded: Session
) -> None:
    for method in ("POST", "PUT", "PATCH", "DELETE"):
        response = wired_client.request(
            method,
            "/api/audit",
            json={"actor": "human", "action": "FORGED", "reasoning_text": "nope"},
        )
        assert response.status_code in (404, 405)

        response = wired_client.request(method, "/api/audit/1", json={})
        assert response.status_code in (404, 405)


def test_seeded_coffee_beans_demonstrates_the_spend_cap(
    wired_client: TestClient, seeded: Session, providers
) -> None:
    """The seed dataset is instrumented for this: high unit price, low cap."""
    coffee = seeded.scalar(select(Product).where(Product.name == "Coffee Beans"))
    assert coffee is not None
    providers.forecast.quantity = 30  # 30 kg x INR 640+ = over INR 10,000

    response = wired_client.post(f"/api/proposals/product/{coffee.id}")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "ORDER_SPEND_LIMIT_EXCEEDED"
    assert seeded.scalar(select(func.count(Order.id))) == 0


def test_seeded_rice_is_refused_as_not_low_stock(
    wired_client: TestClient, seeded: Session
) -> None:
    rice = seeded.scalar(select(Product).where(Product.name == "Rice"))

    response = wired_client.post(f"/api/proposals/product/{rice.id}")

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "PRODUCT_NOT_LOW_STOCK"
