"""Proposal pipeline tests.

Covers the orchestration itself: the order of the steps, what gets refused
before an LLM call is spent, and that the authoritative amount comes from the
database rather than from either agent.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agents.llm_client import AgentTransportError
from app.core.errors import ProductNotFoundError, ProductNotLowStockError
from app.models.audit_log import AuditAction, AuditActor, AuditLog
from app.models.order import Order, OrderStatus
from app.services import proposal_service
from tests.fakes import FakeForecastProvider, FakeSupplierProvider


def _create(db, product_id, *, forecast=None, supplier=None):
    return proposal_service.create_proposal(
        db,
        product_id,
        forecast_provider=forecast or FakeForecastProvider(),
        supplier_provider=supplier or FakeSupplierProvider(),
    )


def _actions(db: Session) -> list[str]:
    return list(db.scalars(select(AuditLog.action).order_by(AuditLog.id)).all())


# --- happy path -------------------------------------------------------------


def test_proposal_is_created_for_a_low_stock_product(
    db: Session, low_stock_product
) -> None:
    proposal = _create(db, low_stock_product.id)

    assert proposal.order.status is OrderStatus.PROPOSED
    assert proposal.order.quantity == 75
    assert proposal.forecast_reasoning
    assert proposal.supplier_reasoning


def test_amount_is_computed_from_the_database_price(
    db: Session, low_stock_product
) -> None:
    cheapest = min(
        low_stock_product.suppliers, key=lambda s: s.price_per_unit_paise
    )
    proposal = _create(
        db,
        low_stock_product.id,
        forecast=FakeForecastProvider(quantity=75),
        supplier=FakeSupplierProvider(supplier_id=cheapest.id),
    )

    assert proposal.order.amount_paise == 75 * cheapest.price_per_unit_paise
    assert proposal.order.amount_paise == 330_000  # 75 x INR 44.00


def test_reasoning_is_persisted_on_the_order(
    db: Session, low_stock_product
) -> None:
    """So a historical order keeps the words it was actually decided with."""
    proposal = _create(
        db,
        low_stock_product.id,
        forecast=FakeForecastProvider(reasoning="Demand is climbing."),
        supplier=FakeSupplierProvider(reasoning="Speed matters here."),
    )

    db.expire_all()
    stored = db.get(Order, proposal.order.id)
    assert stored.forecast_reasoning == "Demand is climbing."
    assert stored.supplier_reasoning == "Speed matters here."


def test_unit_price_at_proposal_is_snapshotted(
    db: Session, low_stock_product
) -> None:
    proposal = _create(db, low_stock_product.id)

    assert proposal.order.unit_price_paise_at_proposal is not None
    assert (
        proposal.order.unit_price_paise_at_proposal
        == proposal.order.supplier.price_per_unit_paise
    )


def test_proposal_writes_the_full_audit_chain(
    db: Session, low_stock_product
) -> None:
    _create(db, low_stock_product.id)

    actions = _actions(db)
    for expected in (
        AuditAction.FORECAST_STARTED,
        AuditAction.FORECAST_GENERATED,
        AuditAction.SUPPLIER_SELECTION_STARTED,
        AuditAction.SUPPLIER_SELECTED,
        AuditAction.PROPOSAL_CREATED,
    ):
        assert expected.value in actions


def test_agent_events_are_attributed_to_the_agent(
    db: Session, low_stock_product
) -> None:
    """`actor` distinguishes a model recommendation from a backend decision."""
    _create(db, low_stock_product.id)

    agent_actions = set(
        db.scalars(
            select(AuditLog.action).where(AuditLog.actor == AuditActor.AGENT)
        ).all()
    )

    assert agent_actions == {
        AuditAction.FORECAST_GENERATED.value,
        AuditAction.SUPPLIER_SELECTED.value,
    }


def test_proposal_audit_records_the_limits_in_force(
    db: Session, low_stock_product
) -> None:
    """So a later reader knows which ceilings applied at the time."""
    proposal = _create(db, low_stock_product.id)

    entry = db.scalar(
        select(AuditLog).where(
            AuditLog.action == AuditAction.PROPOSAL_CREATED.value
        )
    )
    assert entry is not None
    assert entry.related_order_id == proposal.order.id
    assert entry.event_metadata["limits_at_decision"]["max_order_spend_paise"] == (
        1_000_000
    )


def test_no_money_language_in_the_proposal_audit(
    db: Session, low_stock_product
) -> None:
    _create(db, low_stock_product.id)

    entry = db.scalar(
        select(AuditLog).where(
            AuditLog.action == AuditAction.PROPOSAL_CREATED.value
        )
    )
    assert "no money has moved" in entry.reasoning_text.lower()


# --- refusals before spending an LLM call -----------------------------------


def test_healthy_product_is_refused_without_calling_any_agent(
    db: Session, make_product
) -> None:
    """The check happens first, so no LLM budget is spent on a non-problem."""
    from datetime import date, timedelta

    from app.models.sales_history import SalesHistory

    product = make_product(
        "Rice",
        current_stock=320,
        reorder_threshold=150,
        suppliers=[{"razorpay_fund_account_id": "fa_TESTRICE"}],
    )
    for offset in range(28):
        db.add(
            SalesHistory(
                product_id=product.id,
                date=date(2025, 6, 1) + timedelta(days=offset),
                quantity_sold=18,
            )
        )
    db.commit()

    forecast = FakeForecastProvider()
    supplier = FakeSupplierProvider()

    with pytest.raises(ProductNotLowStockError):
        _create(db, product.id, forecast=forecast, supplier=supplier)

    assert forecast.calls == []
    assert supplier.calls == []
    assert db.scalars(select(Order)).all() == []
    assert AuditAction.PROPOSAL_FAILED.value in _actions(db)


def test_product_at_exactly_the_threshold_is_refused(
    db: Session, make_product
) -> None:
    product = make_product("Milk", current_stock=60, reorder_threshold=60)

    with pytest.raises(ProductNotLowStockError):
        _create(db, product.id)


def test_missing_product_is_a_404(db: Session) -> None:
    with pytest.raises(ProductNotFoundError):
        _create(db, 99_999)


# --- failures create no order -----------------------------------------------


def test_forecast_failure_creates_no_order_and_no_supplier_call(
    db: Session, low_stock_product
) -> None:
    """The pipeline stops at the first failure; the second agent is never asked."""
    supplier = FakeSupplierProvider()

    with pytest.raises(Exception):
        _create(
            db,
            low_stock_product.id,
            forecast=FakeForecastProvider(raise_error=AgentTransportError("down")),
            supplier=supplier,
        )

    assert supplier.calls == []
    assert db.scalars(select(Order)).all() == []


def test_supplier_failure_creates_no_order(db: Session, low_stock_product) -> None:
    with pytest.raises(Exception):
        _create(
            db,
            low_stock_product.id,
            supplier=FakeSupplierProvider(
                raise_error=AgentTransportError("down")
            ),
        )

    assert db.scalars(select(Order)).all() == []


def test_guardrail_breach_creates_no_order(db: Session, make_product) -> None:
    """Coffee-beans shape: a plausible quantity at a high unit price."""
    from datetime import date, timedelta

    from app.models.sales_history import SalesHistory

    product = make_product(
        "Coffee Beans",
        current_stock=12,
        reorder_threshold=25,
        unit="kg",
        suppliers=[
            {
                "name": "Blue Tokai",
                "price_per_unit_paise": 72_000,  # INR 720/kg
                "delivery_days": 3,
                "razorpay_fund_account_id": "fa_TESTCOFF",
            }
        ],
    )
    for offset in range(28):
        db.add(
            SalesHistory(
                product_id=product.id,
                date=date(2025, 6, 1) + timedelta(days=offset),
                quantity_sold=4,
            )
        )
    db.commit()

    # 30 kg x INR 720 = INR 21,600, over the INR 10,000 per-order cap.
    with pytest.raises(Exception) as exc:
        _create(db, product.id, forecast=FakeForecastProvider(quantity=30))

    assert exc.value.code == "ORDER_SPEND_LIMIT_EXCEEDED"
    assert db.scalars(select(Order)).all() == []
    assert AuditAction.GUARDRAIL_VIOLATION.value in _actions(db)


def test_guardrail_audit_preserves_the_rejected_reasoning(
    db: Session, make_product
) -> None:
    """Even a rejected recommendation is worth being able to review."""
    from datetime import date, timedelta

    from app.models.sales_history import SalesHistory

    product = make_product(
        "Coffee Beans",
        current_stock=12,
        reorder_threshold=25,
        unit="kg",
        suppliers=[
            {
                "price_per_unit_paise": 72_000,
                "delivery_days": 3,
                "razorpay_fund_account_id": "fa_TESTCOFF",
            }
        ],
    )
    for offset in range(28):
        db.add(
            SalesHistory(
                product_id=product.id,
                date=date(2025, 6, 1) + timedelta(days=offset),
                quantity_sold=4,
            )
        )
    db.commit()

    with pytest.raises(Exception):
        _create(
            db,
            product.id,
            forecast=FakeForecastProvider(
                quantity=30, reasoning="Four kg a day, three-day lead time."
            ),
        )

    entry = db.scalar(
        select(AuditLog).where(
            AuditLog.action == AuditAction.GUARDRAIL_VIOLATION.value
        )
    )
    assert entry is not None
    assert entry.event_metadata["forecast_reasoning"] == (
        "Four kg a day, three-day lead time."
    )
    assert entry.event_metadata["amount_paise"] == 2_160_000


# --- API surface ------------------------------------------------------------


def test_endpoint_returns_the_complete_decision(
    wired_client: TestClient, low_stock_product
) -> None:
    response = wired_client.post(
        f"/api/proposals/product/{low_stock_product.id}"
    )

    assert response.status_code == 201
    body = response.json()

    # Everything a dashboard needs to render the decision, in one response.
    for field in (
        "order_id",
        "status",
        "product_id",
        "product_name",
        "current_stock",
        "reorder_threshold",
        "unit",
        "shortfall",
        "recommended_quantity",
        "forecast_reasoning",
        "supplier_id",
        "supplier_name",
        "delivery_days",
        "supplier_reasoning",
        "unit_price_paise",
        "unit_price",
        "total_amount_paise",
        "total_amount",
        "created_at",
        "next_step",
    ):
        assert field in body, f"missing field: {field}"

    assert body["status"] == "proposed"
    assert body["shortfall"] == 18
    assert body["observed_daily_average"] == 20.0
    assert body["history_days"] == 28


def test_endpoint_money_is_consistent_between_paise_and_rupees(
    wired_client: TestClient, low_stock_product
) -> None:
    body = wired_client.post(
        f"/api/proposals/product/{low_stock_product.id}"
    ).json()

    assert body["total_amount_paise"] == (
        body["recommended_quantity"] * body["unit_price_paise"]
    )
    # Rupee values are strings, so no client parses money as a float.
    assert isinstance(body["total_amount"], str)
    assert isinstance(body["unit_price"], str)
    assert float(body["total_amount"]) * 100 == body["total_amount_paise"]


def test_endpoint_refuses_a_healthy_product_with_400(
    wired_client: TestClient, make_product
) -> None:
    product = make_product("Rice", current_stock=320, reorder_threshold=150)

    response = wired_client.post(f"/api/proposals/product/{product.id}")

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "PRODUCT_NOT_LOW_STOCK"


def test_endpoint_returns_404_for_a_missing_product(
    wired_client: TestClient,
) -> None:
    response = wired_client.post("/api/proposals/product/99999")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "PRODUCT_NOT_FOUND"


def test_endpoint_reports_a_guardrail_breach_as_422(
    wired_client: TestClient, low_stock_product, providers
) -> None:
    providers.forecast.quantity = 400  # 400 x INR 48 = INR 19,200

    response = wired_client.post(
        f"/api/proposals/product/{low_stock_product.id}"
    )

    assert response.status_code == 422
    body = response.json()["error"]
    assert body["code"] == "ORDER_SPEND_LIMIT_EXCEEDED"
    assert "details" in body


def test_endpoint_reports_a_model_outage_as_502(
    wired_client: TestClient, low_stock_product, providers
) -> None:
    providers.forecast.raise_error = AgentTransportError("timeout")

    response = wired_client.post(
        f"/api/proposals/product/{low_stock_product.id}"
    )

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "FORECAST_UNAVAILABLE"


def test_list_proposals_returns_only_proposed_orders(
    wired_client: TestClient, db: Session, low_stock_product
) -> None:
    proposal = _create(db, low_stock_product.id)
    settled = Order(
        product_id=low_stock_product.id,
        supplier_id=low_stock_product.suppliers[0].id,
        quantity=10,
        amount_paise=48_000,
        status=OrderStatus.PAID,
    )
    db.add(settled)
    db.commit()

    body = wired_client.get("/api/proposals").json()

    assert [item["id"] for item in body] == [proposal.order.id]


def test_repeated_proposals_create_separate_orders(
    wired_client: TestClient, db: Session, low_stock_product
) -> None:
    """The alternative-supplier flow depends on this.

    After a failure a merchant asks again; the new proposal must be its own
    order with its own id and its own audit trail, not a mutation of the old.
    """
    first = wired_client.post(
        f"/api/proposals/product/{low_stock_product.id}"
    ).json()
    second = wired_client.post(
        f"/api/proposals/product/{low_stock_product.id}"
    ).json()

    assert first["order_id"] != second["order_id"]
    assert db.scalar(select(func.count(Order.id))) == 2
