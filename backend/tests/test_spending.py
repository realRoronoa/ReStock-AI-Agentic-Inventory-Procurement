"""Spend reporting, settings, and rejection tests.

The spend view must never disagree with the guardrails, because a merchant who
sees "you have budget left" and then gets a 422 has been misled. These tests pin
the two to the same numbers.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import OrderNotRejectableError
from app.core.limits import spend_day_bounds
from app.models.audit_log import AuditAction, AuditActor, AuditLog
from app.models.order import ALLOWED_TRANSITIONS, Order, OrderStatus
from app.services import approval_service, spending_service
from tests.fakes import FakePaymentProvider


def _order(
    db: Session,
    product,
    *,
    amount_paise: int,
    status: OrderStatus,
    approved_at: datetime | None,
) -> Order:
    order = Order(
        product_id=product.id,
        supplier_id=product.suppliers[0].id,
        quantity=10,
        amount_paise=amount_paise,
        status=status,
        approved_at=approved_at,
    )
    db.add(order)
    db.commit()
    return order


def _proposed(db: Session, product) -> Order:
    order = Order(
        product_id=product.id,
        supplier_id=product.suppliers[0].id,
        quantity=75,
        amount_paise=75 * product.suppliers[0].price_per_unit_paise,
        status=OrderStatus.PROPOSED,
        forecast_reasoning="Steady demand.",
        supplier_reasoning="Best trade-off.",
    )
    db.add(order)
    db.commit()
    db.refresh(order)
    return order


# --- spend summary ----------------------------------------------------------


def test_empty_summary_reports_the_full_budget(db: Session) -> None:
    summary = spending_service.get_spend_summary(db)

    assert summary.committed_today_paise == 0
    assert summary.daily_limit_paise == 2_500_000
    assert summary.remaining_today_paise == 2_500_000
    assert summary.utilisation_percent == 0.0


def test_summary_counts_approved_and_paid(db: Session, low_stock_product) -> None:
    now = datetime.now(timezone.utc)
    _order(
        db,
        low_stock_product,
        amount_paise=500_000,
        status=OrderStatus.APPROVED,
        approved_at=now,
    )
    _order(
        db,
        low_stock_product,
        amount_paise=300_000,
        status=OrderStatus.PAID,
        approved_at=now,
    )

    summary = spending_service.get_spend_summary(db)

    assert summary.committed_today_paise == 800_000
    assert summary.remaining_today_paise == 1_700_000
    assert summary.utilisation_percent == 32.0
    assert len(summary.orders_today) == 2


@pytest.mark.parametrize(
    "status",
    [
        OrderStatus.PROPOSED,
        OrderStatus.FAILED,
        OrderStatus.REVERSED,
        OrderStatus.REJECTED,
    ],
)
def test_summary_excludes_uncommitted_states(
    db: Session, low_stock_product, status: OrderStatus
) -> None:
    _order(
        db,
        low_stock_product,
        amount_paise=900_000,
        status=status,
        approved_at=datetime.now(timezone.utc),
    )

    assert spending_service.get_spend_summary(db).committed_today_paise == 0


def test_summary_matches_what_the_guardrail_enforces(
    db: Session, low_stock_product
) -> None:
    """The spend view and the approval gate must never disagree."""
    from app.core.limits import committed_spend_paise

    _order(
        db,
        low_stock_product,
        amount_paise=1_234_500,
        status=OrderStatus.PAID,
        approved_at=datetime.now(timezone.utc),
    )

    summary = spending_service.get_spend_summary(db)

    assert summary.committed_today_paise == committed_spend_paise(db)


def test_remaining_is_clamped_at_zero(db: Session, low_stock_product) -> None:
    """A limit lowered after approvals must not report a negative budget."""
    _order(
        db,
        low_stock_product,
        amount_paise=9_000_000,
        status=OrderStatus.PAID,
        approved_at=datetime.now(timezone.utc),
    )

    assert spending_service.get_spend_summary(db).remaining_today_paise == 0


def test_history_includes_zero_days(db: Session, low_stock_product) -> None:
    """A chart must not close gaps and imply spending that never happened."""
    summary = spending_service.get_spend_summary(db, history_days=7)

    assert len(summary.history) == 7
    assert all(day.committed_paise == 0 for day in summary.history)
    assert summary.history[-1].is_today is True
    assert sum(1 for day in summary.history if day.is_today) == 1


def test_history_is_chronological(db: Session) -> None:
    summary = spending_service.get_spend_summary(db, history_days=5)

    days = [day.day for day in summary.history]
    assert days == sorted(days)


def test_history_attributes_spend_to_the_right_day(
    db: Session, low_stock_product
) -> None:
    start, _ = spend_day_bounds()
    _order(
        db,
        low_stock_product,
        amount_paise=100_000,
        status=OrderStatus.PAID,
        approved_at=start + timedelta(hours=1),
    )
    _order(
        db,
        low_stock_product,
        amount_paise=200_000,
        status=OrderStatus.PAID,
        approved_at=start - timedelta(hours=2),  # yesterday
    )

    summary = spending_service.get_spend_summary(db, history_days=3)

    today = next(day for day in summary.history if day.is_today)
    yesterday = summary.history[-2]
    assert today.committed_paise == 100_000
    assert yesterday.committed_paise == 200_000
    # Only today counts against the cap.
    assert summary.committed_today_paise == 100_000


def test_history_window_is_capped(db: Session) -> None:
    summary = spending_service.get_spend_summary(db, history_days=10_000)

    assert len(summary.history) == spending_service.MAX_HISTORY_DAYS


# --- spending API -----------------------------------------------------------


def test_spending_endpoint_returns_paise_and_rupees(
    client: TestClient, db: Session, low_stock_product
) -> None:
    _order(
        db,
        low_stock_product,
        amount_paise=360_000,
        status=OrderStatus.PAID,
        approved_at=datetime.now(timezone.utc),
    )

    body = client.get("/api/spending").json()

    assert body["committed_today_paise"] == 360_000
    assert body["committed_today"] == "3600.00"
    assert body["daily_limit_paise"] == 2_500_000
    assert body["daily_limit"] == "25000.00"
    assert body["remaining_today_paise"] == 2_140_000
    assert body["timezone"] == "Asia/Kolkata"
    assert sorted(body["counted_statuses"]) == ["approved", "paid"]
    assert len(body["orders_today"]) == 1
    assert body["orders_today"][0]["amount"] == "3600.00"


def test_spending_endpoint_history_length_is_validated(
    client: TestClient,
) -> None:
    assert client.get("/api/spending", params={"history_days": 0}).status_code == 422
    assert client.get("/api/spending", params={"history_days": 999}).status_code == 422
    assert client.get("/api/spending", params={"history_days": 7}).status_code == 200


def test_spending_endpoint_is_read_only(client: TestClient) -> None:
    for method in ("POST", "PUT", "PATCH", "DELETE"):
        response = client.request(method, "/api/spending", json={})
        assert response.status_code in (404, 405)


# --- settings API -----------------------------------------------------------


def test_settings_endpoint_reports_limits(client: TestClient) -> None:
    body = client.get("/api/settings").json()

    assert body["guardrails"]["max_reorder_quantity"] == 500
    assert body["guardrails"]["max_order_spend_paise"] == 1_000_000
    assert body["guardrails"]["max_order_spend"] == "10000.00"
    assert body["guardrails"]["max_daily_spend"] == "25000.00"
    assert body["guardrails"]["spend_day_timezone"] == "Asia/Kolkata"
    assert body["forecast"]["forecast_history_days"] == 28
    assert body["editable"] is False


def test_settings_endpoint_leaks_no_credentials(client: TestClient) -> None:
    """Booleans and a model name only. No key, secret or account number."""
    response = client.get("/api/settings")
    raw = response.text.lower()

    for forbidden in ("secret", "key_id", "account_number", "api_key", "bearer"):
        assert forbidden not in raw, f"settings response mentions {forbidden}"

    integrations = response.json()["integrations"]
    for flag in (
        "razorpayx_payouts_configured",
        "razorpayx_webhooks_configured",
        "llm_configured",
    ):
        assert isinstance(integrations[flag], bool)


def test_settings_endpoint_is_read_only(client: TestClient) -> None:
    """A client that could raise its own spend cap would void the guardrails."""
    for method in ("POST", "PUT", "PATCH", "DELETE"):
        response = client.request(
            method, "/api/settings", json={"max_daily_spend_paise": 999_999_999}
        )
        assert response.status_code in (404, 405)

    body = client.get("/api/settings").json()
    assert body["guardrails"]["max_daily_spend_paise"] == 2_500_000


# --- rejection --------------------------------------------------------------


def test_rejected_is_reachable_only_from_proposed() -> None:
    assert OrderStatus.REJECTED in ALLOWED_TRANSITIONS[OrderStatus.PROPOSED]
    for status in (
        OrderStatus.APPROVED,
        OrderStatus.PAID,
        OrderStatus.FAILED,
        OrderStatus.REVERSED,
    ):
        assert OrderStatus.REJECTED not in ALLOWED_TRANSITIONS[status]


def test_rejected_is_terminal() -> None:
    assert ALLOWED_TRANSITIONS[OrderStatus.REJECTED] == frozenset()


def test_proposed_order_can_be_rejected(db: Session, low_stock_product) -> None:
    order = _proposed(db, low_stock_product)

    rejected = approval_service.reject_order(db, order.id, reason="Too expensive.")

    assert rejected.status is OrderStatus.REJECTED


def test_rejection_is_audited_as_a_human_decision(
    db: Session, low_stock_product
) -> None:
    order = _proposed(db, low_stock_product)

    approval_service.reject_order(db, order.id, reason="Found a cheaper source.")

    entry = db.scalar(
        select(AuditLog).where(
            AuditLog.action == AuditAction.ORDER_REJECTED_BY_HUMAN.value
        )
    )
    assert entry is not None
    assert entry.actor is AuditActor.HUMAN
    assert entry.related_order_id == order.id
    assert "Found a cheaper source." in entry.reasoning_text
    assert "no money moved" in entry.reasoning_text.lower()


def test_rejection_sends_no_payout(db: Session, low_stock_product) -> None:
    order = _proposed(db, low_stock_product)
    provider = FakePaymentProvider()

    approval_service.reject_order(db, order.id)

    assert provider.call_count == 0
    db.expire_all()
    stored = db.get(Order, order.id)
    assert stored.razorpay_payout_id is None
    assert stored.payout_attempted_at is None
    assert stored.approved_at is None


def test_rejected_order_cannot_be_approved(db: Session, low_stock_product) -> None:
    order = _proposed(db, low_stock_product)
    approval_service.reject_order(db, order.id)
    provider = FakePaymentProvider()

    with pytest.raises(Exception) as exc:
        approval_service.approve_order(db, order.id, payment_provider=provider)

    assert exc.value.status_code == 409
    assert provider.call_count == 0


def test_rejecting_twice_is_refused(db: Session, low_stock_product) -> None:
    order = _proposed(db, low_stock_product)
    approval_service.reject_order(db, order.id)

    with pytest.raises(OrderNotRejectableError):
        approval_service.reject_order(db, order.id)


def test_approved_order_cannot_be_rejected(db: Session, low_stock_product) -> None:
    """Money is already in motion; rejection is no longer meaningful."""
    order = _proposed(db, low_stock_product)
    approval_service.approve_order(
        db, order.id, payment_provider=FakePaymentProvider()
    )

    with pytest.raises(OrderNotRejectableError) as exc:
        approval_service.reject_order(db, order.id)

    assert exc.value.status_code == 409


def test_rejection_does_not_change_stock(db: Session, low_stock_product) -> None:
    order = _proposed(db, low_stock_product)

    approval_service.reject_order(db, order.id)

    db.expire_all()
    from app.models.product import Product

    assert db.get(Product, low_stock_product.id).current_stock == 42


def test_rejected_order_does_not_count_toward_daily_spend(
    db: Session, low_stock_product
) -> None:
    order = _proposed(db, low_stock_product)
    approval_service.reject_order(db, order.id)

    assert spending_service.get_spend_summary(db).committed_today_paise == 0


# --- rejection API ----------------------------------------------------------


def test_reject_endpoint_works_without_a_body(
    wired_client: TestClient, db: Session, low_stock_product
) -> None:
    order = _proposed(db, low_stock_product)

    response = wired_client.post(f"/api/orders/{order.id}/reject")

    assert response.status_code == 200
    assert response.json()["status"] == "rejected"


def test_reject_endpoint_accepts_a_reason(
    wired_client: TestClient, db: Session, low_stock_product
) -> None:
    order = _proposed(db, low_stock_product)

    response = wired_client.post(
        f"/api/orders/{order.id}/reject", json={"reason": "Supplier unreliable."}
    )

    assert response.status_code == 200
    trail = wired_client.get(f"/api/audit/{order.id}").json()["entries"]
    assert any("Supplier unreliable." in (e["reasoning_text"] or "") for e in trail)


def test_reject_endpoint_forbids_extra_fields(
    wired_client: TestClient, db: Session, low_stock_product
) -> None:
    order = _proposed(db, low_stock_product)

    response = wired_client.post(
        f"/api/orders/{order.id}/reject",
        json={"reason": "x", "status": "paid", "amount_paise": 1},
    )

    assert response.status_code == 422


def test_reject_endpoint_returns_409_for_a_settled_order(
    wired_client: TestClient, db: Session, low_stock_product, providers
) -> None:
    order = _proposed(db, low_stock_product)
    wired_client.post(f"/api/orders/{order.id}/approve")

    response = wired_client.post(f"/api/orders/{order.id}/reject")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "ORDER_NOT_REJECTABLE"


def test_reject_endpoint_returns_404_for_a_missing_order(
    wired_client: TestClient,
) -> None:
    response = wired_client.post("/api/orders/99999/reject")

    assert response.status_code == 404


def test_double_reject_via_api_is_safe(
    wired_client: TestClient, db: Session, low_stock_product
) -> None:
    order = _proposed(db, low_stock_product)

    first = wired_client.post(f"/api/orders/{order.id}/reject")
    second = wired_client.post(f"/api/orders/{order.id}/reject")

    assert first.status_code == 200
    assert second.status_code == 409


def test_rejected_orders_are_filterable(
    wired_client: TestClient, db: Session, low_stock_product
) -> None:
    order = _proposed(db, low_stock_product)
    wired_client.post(f"/api/orders/{order.id}/reject")

    body = wired_client.get("/api/orders", params={"status": "rejected"}).json()

    assert [item["id"] for item in body] == [order.id]
    assert wired_client.get("/api/proposals").json() == []
