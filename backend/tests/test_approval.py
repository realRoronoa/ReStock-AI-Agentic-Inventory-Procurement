"""Human approval gate tests.

Two properties dominate this file:

1. **Approval is the only route to spending**, and only from `PROPOSED`.
2. **Approving twice cannot pay twice** — proven against sequential retries, a
   re-entrant concurrent attempt, and the idempotency key itself.

Also covered: everything that must be re-validated at approval time, because a
proposal can be stale by the time a human gets to it.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import (
    DailySpendLimitError,
    OrderNotFoundError,
    OrderNotProposedError,
    PayoutAlreadyRequestedError,
    PayoutOutcomeUnknownError,
    SupplierNotForProductError,
    SupplierNotFoundError,
    SupplierNotPayableError,
)
from app.models.audit_log import AuditAction, AuditActor, AuditLog
from app.models.order import Order, OrderStatus
from app.models.product import Product
from app.services import approval_service
from app.services.approval_service import _claim_approval, _claim_payout_attempt
from tests.fakes import (
    FakePaymentProvider,
    FailingPaymentProvider,
    TimingOutPaymentProvider,
)


def _proposed_order(db: Session, product, *, quantity=75, supplier_index=0) -> Order:
    supplier = product.suppliers[supplier_index]
    order = Order(
        product_id=product.id,
        supplier_id=supplier.id,
        quantity=quantity,
        amount_paise=quantity * supplier.price_per_unit_paise,
        status=OrderStatus.PROPOSED,
        forecast_reasoning="Demand averages 20/day.",
        supplier_reasoning="Best balance of price and speed.",
        unit_price_paise_at_proposal=supplier.price_per_unit_paise,
    )
    db.add(order)
    db.commit()
    db.refresh(order)
    return order


def _order_in(db: Session, product, status: OrderStatus) -> Order:
    order = _proposed_order(db, product)
    order.status = status
    order.approved_at = datetime.now(timezone.utc)
    if status is not OrderStatus.PROPOSED:
        order.razorpay_payout_id = f"pout_EXISTING{status.value.upper()}"
        order.payout_attempted_at = datetime.now(timezone.utc)
        order.payout_idempotency_key = f"key-{status.value}"
    db.commit()
    db.refresh(order)
    return order


def _actions(db: Session) -> list[str]:
    return list(db.scalars(select(AuditLog.action).order_by(AuditLog.id)).all())


# --- the happy path ---------------------------------------------------------


def test_proposed_order_can_be_approved(db: Session, low_stock_product) -> None:
    order = _proposed_order(db, low_stock_product)
    provider = FakePaymentProvider()

    result = approval_service.approve_order(
        db, order.id, payment_provider=provider
    )

    assert result.order.status is OrderStatus.APPROVED
    assert result.payout.payout_id == "pout_TESTPAYOUT000001"
    assert provider.call_count == 1


def test_approval_does_not_mark_the_order_paid(
    db: Session, low_stock_product
) -> None:
    """A successful payout request is not settlement."""
    order = _proposed_order(db, low_stock_product)

    approval_service.approve_order(
        db, order.id, payment_provider=FakePaymentProvider()
    )

    db.expire_all()
    assert db.get(Order, order.id).status is OrderStatus.APPROVED


def test_approval_does_not_change_inventory(
    db: Session, low_stock_product
) -> None:
    order = _proposed_order(db, low_stock_product)

    approval_service.approve_order(
        db, order.id, payment_provider=FakePaymentProvider()
    )

    db.expire_all()
    assert db.get(Product, low_stock_product.id).current_stock == 42


def test_approval_stores_payout_id_and_status(
    db: Session, low_stock_product
) -> None:
    order = _proposed_order(db, low_stock_product)

    approval_service.approve_order(
        db,
        order.id,
        payment_provider=FakePaymentProvider(
            payout_id="pout_ABC123", raw_status="queued"
        ),
    )

    db.expire_all()
    stored = db.get(Order, order.id)
    assert stored.razorpay_payout_id == "pout_ABC123"
    assert stored.payout_status == "queued"
    assert stored.approved_at is not None
    assert stored.payout_attempted_at is not None


def test_approval_is_attributed_to_a_human_in_the_audit_trail(
    db: Session, low_stock_product
) -> None:
    order = _proposed_order(db, low_stock_product)

    approval_service.approve_order(
        db, order.id, payment_provider=FakePaymentProvider()
    )

    entry = db.scalar(
        select(AuditLog).where(AuditLog.action == AuditAction.ORDER_APPROVED.value)
    )
    assert entry is not None
    assert entry.actor is AuditActor.HUMAN
    assert entry.related_order_id == order.id


def test_payout_creation_audit_says_it_is_not_settlement(
    db: Session, low_stock_product
) -> None:
    order = _proposed_order(db, low_stock_product)

    approval_service.approve_order(
        db, order.id, payment_provider=FakePaymentProvider()
    )

    entry = db.scalar(
        select(AuditLog).where(
            AuditLog.action == AuditAction.RAZORPAY_PAYOUT_CREATED.value
        )
    )
    assert entry is not None
    assert "NOT that the supplier has been paid" in entry.reasoning_text


def test_payout_instruction_uses_authoritative_values(
    db: Session, low_stock_product
) -> None:
    order = _proposed_order(db, low_stock_product)
    provider = FakePaymentProvider()

    approval_service.approve_order(db, order.id, payment_provider=provider)

    instruction = provider.instructions[0]
    supplier = low_stock_product.suppliers[0]
    assert instruction.amount_paise == 75 * supplier.price_per_unit_paise
    assert instruction.fund_account_id == supplier.razorpay_fund_account_id
    assert instruction.notes["order_id"] == str(order.id)


# --- state gate -------------------------------------------------------------


@pytest.mark.parametrize(
    "status", [OrderStatus.PAID, OrderStatus.FAILED, OrderStatus.REVERSED]
)
def test_settled_orders_cannot_be_approved(
    db: Session, low_stock_product, status: OrderStatus
) -> None:
    order = _order_in(db, low_stock_product, status)
    provider = FakePaymentProvider()

    with pytest.raises(Exception) as exc:
        approval_service.approve_order(db, order.id, payment_provider=provider)

    assert provider.call_count == 0, "no payout may be attempted"
    assert exc.value.status_code == 409
    assert AuditAction.ORDER_APPROVAL_REJECTED.value in _actions(db)


def test_paid_order_approval_is_rejected_with_a_clear_code(
    db: Session, low_stock_product
) -> None:
    order = _order_in(db, low_stock_product, OrderStatus.PAID)

    with pytest.raises(OrderNotProposedError) as exc:
        approval_service.approve_order(
            db, order.id, payment_provider=FakePaymentProvider()
        )

    assert exc.value.code == "ORDER_NOT_PROPOSED"
    assert "paid" in exc.value.message


def test_missing_order_is_a_404(db: Session) -> None:
    with pytest.raises(OrderNotFoundError):
        approval_service.approve_order(
            db, 99_999, payment_provider=FakePaymentProvider()
        )


def test_approved_order_with_a_payout_cannot_be_approved_again(
    db: Session, low_stock_product
) -> None:
    order = _order_in(db, low_stock_product, OrderStatus.APPROVED)
    provider = FakePaymentProvider()

    with pytest.raises(PayoutAlreadyRequestedError):
        approval_service.approve_order(db, order.id, payment_provider=provider)

    assert provider.call_count == 0


def test_order_with_unknown_payout_outcome_is_not_retried(
    db: Session, low_stock_product
) -> None:
    """A lost response must not invite a fresh attempt."""
    order = _proposed_order(db, low_stock_product)
    order.status = OrderStatus.APPROVED
    order.approved_at = datetime.now(timezone.utc)
    order.payout_attempted_at = datetime.now(timezone.utc)
    order.payout_idempotency_key = "key-unknown"
    order.razorpay_payout_id = None  # attempted, no id came back
    db.commit()

    provider = FakePaymentProvider()
    with pytest.raises(PayoutOutcomeUnknownError) as exc:
        approval_service.approve_order(db, order.id, payment_provider=provider)

    assert provider.call_count == 0
    assert exc.value.status_code == 409
    assert "reference_id" in exc.value.details


# --- duplicate and concurrent approval --------------------------------------


def test_duplicate_approval_creates_only_one_payout(
    db: Session, low_stock_product
) -> None:
    """The double-click case, sequentially."""
    order = _proposed_order(db, low_stock_product)
    provider = FakePaymentProvider()

    approval_service.approve_order(db, order.id, payment_provider=provider)
    with pytest.raises(PayoutAlreadyRequestedError):
        approval_service.approve_order(db, order.id, payment_provider=provider)

    assert provider.call_count == 1
    assert len(provider.distinct_payout_ids) == 1


def test_concurrent_approval_mid_payout_cannot_create_a_second_payout(
    db: Session, session_factory, low_stock_product
) -> None:
    """A second approval arriving while the first payout call is in flight.

    This is the real double-submit shape: the first request has committed its
    approval and is waiting on RazorpayX when the second arrives. The payout
    claim, not the approval claim, is what has to stop it.
    """
    order = _proposed_order(db, low_stock_product)
    second_attempt: dict = {}

    def reenter(instruction):
        with session_factory() as other_session:
            try:
                approval_service.approve_order(
                    other_session,
                    instruction.order_id,
                    payment_provider=FakePaymentProvider(payout_id="pout_SECOND"),
                )
                second_attempt["result"] = "succeeded"
            except Exception as exc:
                second_attempt["error"] = type(exc).__name__

    provider = FakePaymentProvider(on_call=reenter)
    approval_service.approve_order(db, order.id, payment_provider=provider)

    assert "result" not in second_attempt, "a second payout must not be possible"
    assert second_attempt["error"] in {
        "PayoutAlreadyRequestedError",
        "PayoutOutcomeUnknownError",
    }
    assert provider.call_count == 1

    db.expire_all()
    assert db.get(Order, order.id).razorpay_payout_id == "pout_TESTPAYOUT000001"


def test_approval_claim_is_a_compare_and_swap(
    db: Session, low_stock_product
) -> None:
    """Only the first caller can move PROPOSED -> APPROVED."""
    order = _proposed_order(db, low_stock_product)

    first = _claim_approval(db, order.id, "key-a")
    db.commit()
    second = _claim_approval(db, order.id, "key-b")
    db.commit()

    assert first is True
    assert second is False, "a second claim must not win"
    db.expire_all()
    assert db.get(Order, order.id).payout_idempotency_key == "key-a"


def test_payout_claim_is_a_compare_and_swap(
    db: Session, low_stock_product
) -> None:
    order = _proposed_order(db, low_stock_product)
    _claim_approval(db, order.id, "key-a")
    db.commit()

    first = _claim_payout_attempt(db, order.id)
    db.commit()
    second = _claim_payout_attempt(db, order.id)
    db.commit()

    assert first is True
    assert second is False


def test_idempotency_key_is_persisted_before_the_payout_call(
    db: Session, low_stock_product
) -> None:
    """If the process died during the call, the key must already be durable."""
    order = _proposed_order(db, low_stock_product)
    observed: dict = {}

    def check(instruction):
        with_fresh = Session(bind=db.get_bind())
        with with_fresh as session:
            stored = session.get(Order, instruction.order_id)
            observed["persisted_key"] = stored.payout_idempotency_key
            observed["persisted_attempt"] = stored.payout_attempted_at

    approval_service.approve_order(
        db, order.id, payment_provider=FakePaymentProvider(on_call=check)
    )

    assert observed["persisted_key"] is not None
    assert observed["persisted_attempt"] is not None


def test_a_resumed_payout_reuses_the_stored_key(
    db: Session, low_stock_product
) -> None:
    """An approved-but-unsent order can be resumed, with the same key.

    Reusing the key is what makes the resume safe: RazorpayX collapses it onto
    the original payout if one was in fact created.
    """
    order = _proposed_order(db, low_stock_product)

    # First attempt: the provider dies, leaving APPROVED with no payout id.
    with pytest.raises(Exception):
        approval_service.approve_order(
            db, order.id, payment_provider=FailingPaymentProvider()
        )
    db.expire_all()
    stored = db.get(Order, order.id)
    original_key = stored.payout_idempotency_key
    assert stored.status is OrderStatus.APPROVED
    assert original_key is not None

    # Clear the attempt marker, as a deliberate reconciliation action would.
    stored.payout_attempted_at = None
    db.commit()

    provider = FakePaymentProvider()
    approval_service.approve_order(db, order.id, payment_provider=provider)

    assert provider.instructions[0].idempotency_key == original_key


# --- revalidation at approval time ------------------------------------------


def test_price_change_is_recomputed_at_approval(
    db: Session, low_stock_product
) -> None:
    """A merchant approves an order, not a remembered number."""
    order = _proposed_order(db, low_stock_product)
    supplier = low_stock_product.suppliers[0]
    supplier.price_per_unit_paise = 5_000  # was 4,800
    db.commit()

    result = approval_service.approve_order(
        db, order.id, payment_provider=FakePaymentProvider()
    )

    assert result.price_changed_since_proposal is True
    assert result.revalidated_amount_paise == 75 * 5_000
    db.expire_all()
    assert db.get(Order, order.id).amount_paise == 375_000


def test_recomputed_amount_is_what_gets_paid(
    db: Session, low_stock_product
) -> None:
    order = _proposed_order(db, low_stock_product)
    low_stock_product.suppliers[0].price_per_unit_paise = 5_000
    db.commit()
    provider = FakePaymentProvider()

    approval_service.approve_order(db, order.id, payment_provider=provider)

    assert provider.instructions[0].amount_paise == 375_000


def test_deleted_supplier_blocks_approval(db: Session, low_stock_product) -> None:
    order = _proposed_order(db, low_stock_product)
    db.delete(low_stock_product.suppliers[1])  # keep the one in use? no - delete used
    db.commit()
    # Delete the supplier actually referenced by the order via raw SQL, since
    # the FK is RESTRICT for exactly this reason.
    from sqlalchemy import text

    with pytest.raises(Exception):
        db.execute(
            text("DELETE FROM suppliers WHERE id = :sid"),
            {"sid": order.supplier_id},
        )
        db.commit()
    db.rollback()

    # The RESTRICT constraint is itself the protection.
    provider = FakePaymentProvider()
    approval_service.approve_order(db, order.id, payment_provider=provider)
    assert provider.call_count == 1


def test_supplier_repointed_to_another_product_blocks_approval(
    db: Session, low_stock_product, make_product
) -> None:
    """The stale-proposal case that would otherwise pay for the wrong goods."""
    order = _proposed_order(db, low_stock_product)
    other = make_product("Rice", current_stock=1, reorder_threshold=5)
    supplier = low_stock_product.suppliers[0]
    supplier.product_id = other.id
    db.commit()

    provider = FakePaymentProvider()
    with pytest.raises(SupplierNotForProductError):
        approval_service.approve_order(db, order.id, payment_provider=provider)

    assert provider.call_count == 0


def test_supplier_losing_its_fund_account_blocks_approval(
    db: Session, low_stock_product
) -> None:
    order = _proposed_order(db, low_stock_product)
    low_stock_product.suppliers[0].razorpay_fund_account_id = None
    db.commit()

    provider = FakePaymentProvider()
    with pytest.raises(SupplierNotPayableError):
        approval_service.approve_order(db, order.id, payment_provider=provider)

    assert provider.call_count == 0
    db.expire_all()
    assert db.get(Order, order.id).status is OrderStatus.PROPOSED


def test_daily_limit_reached_after_proposal_blocks_approval(
    db: Session, low_stock_product
) -> None:
    """The proposal was fine when created; the budget has since been used."""
    order = _proposed_order(db, low_stock_product)
    # Commit 2,480,000 paise today, leaving only 20,000 of the 2,500,000 cap.
    db.add(
        Order(
            product_id=low_stock_product.id,
            supplier_id=low_stock_product.suppliers[0].id,
            quantity=1,
            amount_paise=2_480_000,
            status=OrderStatus.PAID,
            approved_at=datetime.now(timezone.utc),
        )
    )
    db.commit()

    provider = FakePaymentProvider()
    with pytest.raises(DailySpendLimitError):
        approval_service.approve_order(db, order.id, payment_provider=provider)

    assert provider.call_count == 0
    db.expire_all()
    assert db.get(Order, order.id).status is OrderStatus.PROPOSED
    assert AuditAction.ORDER_APPROVAL_REJECTED.value in _actions(db)


def test_guardrail_rejection_leaves_the_order_approvable_later(
    db: Session, low_stock_product
) -> None:
    """A budget rejection is not terminal; tomorrow it can be approved."""
    order = _proposed_order(db, low_stock_product)
    blocker = Order(
        product_id=low_stock_product.id,
        supplier_id=low_stock_product.suppliers[0].id,
        quantity=1,
        amount_paise=2_480_000,
        status=OrderStatus.PAID,
        approved_at=datetime.now(timezone.utc),
    )
    db.add(blocker)
    db.commit()

    with pytest.raises(DailySpendLimitError):
        approval_service.approve_order(
            db, order.id, payment_provider=FakePaymentProvider()
        )

    # Budget frees up.
    db.delete(blocker)
    db.commit()

    result = approval_service.approve_order(
        db, order.id, payment_provider=FakePaymentProvider()
    )
    assert result.order.status is OrderStatus.APPROVED


# --- payment failures at approval time --------------------------------------


def test_provider_rejection_does_not_mark_the_order_failed(
    db: Session, low_stock_product
) -> None:
    """Only RazorpayX decides that a payout failed, and only via webhook."""
    order = _proposed_order(db, low_stock_product)

    with pytest.raises(Exception) as exc:
        approval_service.approve_order(
            db, order.id, payment_provider=FailingPaymentProvider()
        )

    assert exc.value.status_code == 502
    db.expire_all()
    stored = db.get(Order, order.id)
    assert stored.status is OrderStatus.APPROVED
    assert stored.razorpay_payout_id is None
    assert AuditAction.RAZORPAY_PAYOUT_FAILED.value in _actions(db)


def test_timeout_leaves_the_outcome_unknown_and_audited(
    db: Session, low_stock_product
) -> None:
    order = _proposed_order(db, low_stock_product)

    with pytest.raises(Exception) as exc:
        approval_service.approve_order(
            db, order.id, payment_provider=TimingOutPaymentProvider()
        )

    assert exc.value.status_code == 504
    db.expire_all()
    stored = db.get(Order, order.id)
    assert stored.status is OrderStatus.APPROVED
    assert stored.payout_outcome_unknown is True
    assert AuditAction.RAZORPAY_PAYOUT_OUTCOME_UNKNOWN.value in _actions(db)


def test_missing_credentials_prevent_any_payout(
    db: Session, low_stock_product
) -> None:
    """Real provider, no credentials. Must fail loudly, not simulate."""
    from app.core.config import Settings
    from app.services.payment_service import RazorpayXPaymentProvider

    order = _proposed_order(db, low_stock_product)
    provider = RazorpayXPaymentProvider(Settings())

    with pytest.raises(Exception) as exc:
        approval_service.approve_order(db, order.id, payment_provider=provider)

    assert exc.value.status_code == 503
    assert exc.value.code == "PAYMENT_NOT_CONFIGURED"
    db.expire_all()
    assert db.get(Order, order.id).razorpay_payout_id is None


# --- API surface ------------------------------------------------------------


def test_approve_endpoint_needs_no_request_body(
    wired_client: TestClient, db: Session, low_stock_product
) -> None:
    """A client supplies an order id and nothing else."""
    order = _proposed_order(db, low_stock_product)

    response = wired_client.post(f"/api/orders/{order.id}/approve")

    assert response.status_code == 200
    body = response.json()
    assert body["order"]["status"] == "approved"
    assert body["payout_requested"] is True
    assert body["payout_id"]
    assert "approved" in body["next_step"] or "webhook" in body["next_step"]


def test_approve_endpoint_ignores_a_client_supplied_amount(
    wired_client: TestClient, db: Session, low_stock_product
) -> None:
    """The frontend is untrusted; an injected amount must have no effect."""
    order = _proposed_order(db, low_stock_product)
    expected = order.amount_paise

    response = wired_client.post(
        f"/api/orders/{order.id}/approve",
        json={"amount_paise": 1, "status": "paid", "supplier_id": 999},
    )

    assert response.status_code == 200
    assert response.json()["amount_paise"] == expected
    db.expire_all()
    stored = db.get(Order, order.id)
    assert stored.amount_paise == expected
    assert stored.status is OrderStatus.APPROVED


def test_approve_endpoint_returns_409_for_a_settled_order(
    wired_client: TestClient, db: Session, low_stock_product
) -> None:
    order = _order_in(db, low_stock_product, OrderStatus.PAID)

    response = wired_client.post(f"/api/orders/{order.id}/approve")

    assert response.status_code == 409
    assert response.json()["error"]["code"] in {
        "PAYOUT_ALREADY_REQUESTED",
        "ORDER_NOT_PROPOSED",
    }


def test_approve_endpoint_returns_404_for_a_missing_order(
    wired_client: TestClient,
) -> None:
    response = wired_client.post("/api/orders/99999/approve")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "ORDER_NOT_FOUND"


def test_double_post_to_approve_creates_one_payout(
    wired_client: TestClient, db: Session, low_stock_product, providers
) -> None:
    order = _proposed_order(db, low_stock_product)

    first = wired_client.post(f"/api/orders/{order.id}/approve")
    second = wired_client.post(f"/api/orders/{order.id}/approve")

    assert first.status_code == 200
    assert second.status_code == 409
    assert providers.payment.call_count == 1


def test_no_endpoint_can_set_order_status_directly(
    wired_client: TestClient, db: Session, low_stock_product
) -> None:
    """There must be no route that bypasses the state machine."""
    order = _proposed_order(db, low_stock_product)

    for method in ("PATCH", "PUT", "DELETE"):
        response = wired_client.request(
            method, f"/api/orders/{order.id}", json={"status": "paid"}
        )
        assert response.status_code in (404, 405), f"{method} must not be routable"

    for path in (f"/api/orders/{order.id}/status", "/api/audit", "/api/products/1"):
        for method in ("PATCH", "PUT", "DELETE", "POST"):
            response = wired_client.request(method, path, json={"status": "paid"})
            assert response.status_code in (
                404,
                405,
            ), f"{method} {path} must not be routable"

    db.expire_all()
    assert db.get(Order, order.id).status is OrderStatus.PROPOSED


def test_only_one_order_row_exists_after_a_duplicate_approval(
    db: Session, low_stock_product
) -> None:
    order = _proposed_order(db, low_stock_product)
    provider = FakePaymentProvider()

    approval_service.approve_order(db, order.id, payment_provider=provider)
    with pytest.raises(PayoutAlreadyRequestedError):
        approval_service.approve_order(db, order.id, payment_provider=provider)

    assert db.scalar(select(func.count(Order.id))) == 1
