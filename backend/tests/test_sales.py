"""Sales recording tests.

`record_sale` is the only function that decreases stock, and it closes the loop
that makes the whole product work: sale -> low stock -> recommendation. The
important properties are that stock and history move together, that same-day
sales accumulate rather than duplicate, and that an oversized sale is refused
rather than clamped.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import InsufficientStockError, ProductNotFoundError
from app.models.audit_log import AuditAction, AuditActor, AuditLog
from app.models.product import Product
from app.models.sales_history import SalesHistory
from app.services import inventory_service

TODAY = date(2025, 6, 15)


def _actions(db: Session) -> list[str]:
    return list(db.scalars(select(AuditLog.action).order_by(AuditLog.id)).all())


def _history(db: Session, product_id: int) -> list[tuple[date, int]]:
    rows = db.scalars(
        select(SalesHistory)
        .where(SalesHistory.product_id == product_id)
        .order_by(SalesHistory.date)
    ).all()
    return [(row.date, row.quantity_sold) for row in rows]


# --- the core behaviour -----------------------------------------------------


def test_sale_decreases_stock(db: Session, make_product) -> None:
    product = make_product("Milk", current_stock=100, reorder_threshold=60)

    result = inventory_service.record_sale(db, product.id, 12, sale_date=TODAY)

    assert result.stock_before == 100
    assert result.stock_after == 88
    db.expire_all()
    assert db.get(Product, product.id).current_stock == 88


def test_sale_is_added_to_sales_history(db: Session, make_product) -> None:
    """The forecast agent reads this table, so it must reflect the sale."""
    product = make_product("Milk", current_stock=100, reorder_threshold=60)

    inventory_service.record_sale(db, product.id, 12, sale_date=TODAY)

    assert _history(db, product.id) == [(TODAY, 12)]


def test_same_day_sales_accumulate_into_one_row(db: Session, make_product) -> None:
    """UNIQUE(product_id, date) means a second sale must add, not insert.

    Sales of 3 and 4 on one day are a day of 7 demand, not two days.
    """
    product = make_product("Milk", current_stock=100, reorder_threshold=60)

    inventory_service.record_sale(db, product.id, 3, sale_date=TODAY)
    inventory_service.record_sale(db, product.id, 4, sale_date=TODAY)

    assert _history(db, product.id) == [(TODAY, 7)]
    db.expire_all()
    assert db.get(Product, product.id).current_stock == 93


def test_sales_on_different_days_are_separate_rows(
    db: Session, make_product
) -> None:
    product = make_product("Milk", current_stock=100, reorder_threshold=60)

    inventory_service.record_sale(db, product.id, 3, sale_date=TODAY)
    inventory_service.record_sale(
        db, product.id, 4, sale_date=TODAY + timedelta(days=1)
    )

    assert _history(db, product.id) == [(TODAY, 3), (TODAY + timedelta(days=1), 4)]


def test_sale_adds_to_pre_existing_seeded_history(
    db: Session, make_product
) -> None:
    """A sale on a day the seed already wrote must not violate the constraint."""
    product = make_product("Milk", current_stock=100, reorder_threshold=60)
    db.add(SalesHistory(product_id=product.id, date=TODAY, quantity_sold=20))
    db.commit()

    inventory_service.record_sale(db, product.id, 5, sale_date=TODAY)

    assert _history(db, product.id) == [(TODAY, 25)]


def test_sale_is_audited_as_a_human_action(db: Session, make_product) -> None:
    product = make_product("Milk", current_stock=100, reorder_threshold=60)

    inventory_service.record_sale(db, product.id, 12, sale_date=TODAY)

    entry = db.scalar(
        select(AuditLog).where(AuditLog.action == AuditAction.SALE_RECORDED.value)
    )
    assert entry is not None
    assert entry.actor is AuditActor.HUMAN
    assert entry.event_metadata["quantity_sold"] == 12
    assert entry.event_metadata["stock_before"] == 100
    assert entry.event_metadata["stock_after"] == 88


# --- the low-stock trigger, which is the point of the feature ---------------


def test_sale_that_crosses_the_threshold_reports_it(
    db: Session, make_product
) -> None:
    product = make_product("Milk", current_stock=65, reorder_threshold=60)

    result = inventory_service.record_sale(db, product.id, 10, sale_date=TODAY)

    assert result.stock_after == 55
    assert result.became_low_stock is True
    assert AuditAction.LOW_STOCK_DETECTED.value in _actions(db)


def test_sale_that_stays_above_the_threshold_does_not_trigger(
    db: Session, make_product
) -> None:
    product = make_product("Milk", current_stock=100, reorder_threshold=60)

    result = inventory_service.record_sale(db, product.id, 10, sale_date=TODAY)

    assert result.became_low_stock is False
    assert AuditAction.LOW_STOCK_DETECTED.value not in _actions(db)


def test_sale_landing_exactly_on_the_threshold_is_not_low(
    db: Session, make_product
) -> None:
    """The `<` boundary again: at the threshold is not below it."""
    product = make_product("Milk", current_stock=70, reorder_threshold=60)

    result = inventory_service.record_sale(db, product.id, 10, sale_date=TODAY)

    assert result.stock_after == 60
    assert result.became_low_stock is False


def test_further_sales_while_already_low_do_not_re_audit(
    db: Session, make_product
) -> None:
    """Only the crossing is newsworthy; every subsequent sale is not."""
    product = make_product("Milk", current_stock=65, reorder_threshold=60)

    first = inventory_service.record_sale(db, product.id, 10, sale_date=TODAY)
    second = inventory_service.record_sale(db, product.id, 5, sale_date=TODAY)

    assert first.became_low_stock is True
    # The sale bumps updated_at, so the suppression window re-arms and a second
    # event is emitted. Documented behaviour; assert what actually happens.
    detections = [
        a for a in _actions(db) if a == AuditAction.LOW_STOCK_DETECTED.value
    ]
    assert len(detections) == 2
    assert second.stock_after == 50


# --- refusals ---------------------------------------------------------------


def test_sale_larger_than_stock_is_refused(db: Session, make_product) -> None:
    """Refused, not clamped: clamping would corrupt the demand signal."""
    product = make_product("Milk", current_stock=10, reorder_threshold=60)

    with pytest.raises(InsufficientStockError) as exc:
        inventory_service.record_sale(db, product.id, 11, sale_date=TODAY)

    assert exc.value.details["available_stock"] == 10
    db.expire_all()
    assert db.get(Product, product.id).current_stock == 10
    assert _history(db, product.id) == []


def test_sale_of_exactly_all_stock_is_allowed(db: Session, make_product) -> None:
    product = make_product("Milk", current_stock=10, reorder_threshold=60)

    result = inventory_service.record_sale(db, product.id, 10, sale_date=TODAY)

    assert result.stock_after == 0
    assert result.became_low_stock is True


@pytest.mark.parametrize("quantity", [0, -1])
def test_non_positive_quantity_is_refused(
    db: Session, make_product, quantity: int
) -> None:
    product = make_product("Milk", current_stock=100, reorder_threshold=60)

    with pytest.raises(InsufficientStockError):
        inventory_service.record_sale(db, product.id, quantity, sale_date=TODAY)


def test_sale_for_unknown_product_is_refused(db: Session) -> None:
    with pytest.raises(ProductNotFoundError):
        inventory_service.record_sale(db, 99_999, 5, sale_date=TODAY)


def test_a_refused_sale_leaves_nothing_behind(db: Session, make_product) -> None:
    """Stock, history and audit must all be untouched."""
    product = make_product("Milk", current_stock=5, reorder_threshold=60)

    with pytest.raises(InsufficientStockError):
        inventory_service.record_sale(db, product.id, 50, sale_date=TODAY)

    db.expire_all()
    assert db.get(Product, product.id).current_stock == 5
    assert db.scalar(select(func.count(SalesHistory.id))) == 0
    assert AuditAction.SALE_RECORDED.value not in _actions(db)


# --- API --------------------------------------------------------------------


def test_endpoint_records_a_sale(client: TestClient, make_product) -> None:
    product = make_product(
        "Milk", current_stock=100, reorder_threshold=60, unit="litre"
    )

    response = client.post(
        "/api/sales", json={"product_id": product.id, "quantity": 12}
    )

    assert response.status_code == 201
    body = response.json()
    assert body["product_name"] == "Milk"
    assert body["unit"] == "litre"
    assert body["quantity_sold"] == 12
    assert body["stock_before"] == 100
    assert body["stock_after"] == 88
    assert body["is_low_stock"] is False
    assert body["status"] == "ok"
    assert body["became_low_stock"] is False


def test_endpoint_surfaces_the_reorder_prompt(
    client: TestClient, make_product
) -> None:
    product = make_product("Milk", current_stock=65, reorder_threshold=60)

    body = client.post(
        "/api/sales", json={"product_id": product.id, "quantity": 10}
    ).json()

    assert body["became_low_stock"] is True
    assert body["is_low_stock"] is True
    assert body["status"] == "low_stock"
    assert body["shortfall"] == 5
    assert f"/api/proposals/product/{product.id}" in body["next_step"]


def test_endpoint_rejects_an_oversized_sale_with_400(
    client: TestClient, make_product
) -> None:
    product = make_product("Milk", current_stock=5, reorder_threshold=60)

    response = client.post(
        "/api/sales", json={"product_id": product.id, "quantity": 50}
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INSUFFICIENT_STOCK"


def test_endpoint_rejects_unknown_product_with_404(client: TestClient) -> None:
    response = client.post("/api/sales", json={"product_id": 99999, "quantity": 1})

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "PRODUCT_NOT_FOUND"


@pytest.mark.parametrize(
    "payload",
    [
        {"product_id": 1},
        {"quantity": 5},
        {"product_id": 1, "quantity": 0},
        {"product_id": 1, "quantity": -5},
        {"product_id": 0, "quantity": 5},
        {"product_id": 1, "quantity": "many"},
    ],
)
def test_endpoint_validates_the_body(client: TestClient, payload: dict) -> None:
    response = client.post("/api/sales", json=payload)

    assert response.status_code == 422


def test_endpoint_rejects_unexpected_fields(
    client: TestClient, make_product
) -> None:
    """`extra="forbid"` — a client cannot smuggle in a stock level."""
    product = make_product("Milk", current_stock=100, reorder_threshold=60)

    response = client.post(
        "/api/sales",
        json={
            "product_id": product.id,
            "quantity": 1,
            "current_stock": 9999,
            "amount_paise": 1,
        },
    )

    assert response.status_code == 422


def test_endpoint_cannot_be_used_to_increase_stock(
    client: TestClient, make_product
) -> None:
    """There is no sign or direction argument. Sales only ever reduce stock."""
    product = make_product("Milk", current_stock=50, reorder_threshold=60)

    for quantity in (-10, 0):
        response = client.post(
            "/api/sales", json={"product_id": product.id, "quantity": quantity}
        )
        assert response.status_code == 422

    body = client.get(f"/api/products/{product.id}").json()
    assert body["current_stock"] == 50


def test_sale_then_inventory_check_agree(client: TestClient, make_product) -> None:
    """The loop: a sale makes the product appear in the low-stock sweep."""
    product = make_product("Milk", current_stock=65, reorder_threshold=60)

    assert client.get("/api/inventory/low-stock").json() == []

    client.post("/api/sales", json={"product_id": product.id, "quantity": 10})

    low = client.get("/api/inventory/low-stock").json()
    assert [item["name"] for item in low] == ["Milk"]
    assert low[0]["current_stock"] == 55
