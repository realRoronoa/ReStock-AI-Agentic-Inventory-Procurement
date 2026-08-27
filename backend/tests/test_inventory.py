"""Low-stock detection tests.

The rule under test is `current_stock < reorder_threshold` — strict less-than,
so equality is NOT low stock. That boundary is the one most likely to be got
wrong, so it is tested from three angles.
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.audit_log import AuditAction, AuditActor, AuditLog
from app.services import inventory_service


def _audit_actions(db: Session) -> list[str]:
    return list(db.scalars(select(AuditLog.action).order_by(AuditLog.id)).all())


# --- detection rule ---------------------------------------------------------


def test_product_below_threshold_is_detected(db: Session, make_product) -> None:
    make_product("Milk", current_stock=42, reorder_threshold=60)

    low = inventory_service.find_low_stock_products(db)

    assert [product.name for product in low] == ["Milk"]


def test_product_above_threshold_is_ignored(db: Session, make_product) -> None:
    make_product("Rice", current_stock=320, reorder_threshold=150)

    assert inventory_service.find_low_stock_products(db) == []


def test_product_exactly_at_threshold_is_not_low(db: Session, make_product) -> None:
    """Equality is not low stock."""
    make_product("Milk", current_stock=60, reorder_threshold=60)

    assert inventory_service.find_low_stock_products(db) == []


def test_one_unit_below_threshold_is_low(db: Session, make_product) -> None:
    """The other side of the same boundary."""
    make_product("Milk", current_stock=59, reorder_threshold=60)

    assert len(inventory_service.find_low_stock_products(db)) == 1


def test_zero_stock_is_low(db: Session, make_product) -> None:
    make_product("Milk", current_stock=0, reorder_threshold=1)

    assert len(inventory_service.find_low_stock_products(db)) == 1


def test_zero_threshold_means_never_low(db: Session, make_product) -> None:
    """A threshold of zero can never be undercut, since stock cannot go negative."""
    make_product("Milk", current_stock=0, reorder_threshold=0)

    assert inventory_service.find_low_stock_products(db) == []


def test_multiple_low_stock_products_are_all_detected(db: Session, make_product) -> None:
    make_product("Milk", current_stock=42, reorder_threshold=60)
    make_product("Coffee Beans", current_stock=12, reorder_threshold=25)
    make_product("Rice", current_stock=320, reorder_threshold=150)
    make_product("Cooking Oil", current_stock=95, reorder_threshold=80)

    low = inventory_service.find_low_stock_products(db)

    assert [product.name for product in low] == ["Coffee Beans", "Milk"]


def test_empty_database_returns_nothing(db: Session) -> None:
    assert inventory_service.find_low_stock_products(db) == []


def test_detection_is_sorted_by_name(db: Session, make_product) -> None:
    for name in ("Rice", "Apples", "Milk"):
        make_product(name, current_stock=1, reorder_threshold=10)

    low = inventory_service.find_low_stock_products(db)

    assert [product.name for product in low] == ["Apples", "Milk", "Rice"]


# --- audit behaviour --------------------------------------------------------


def test_check_writes_low_stock_and_completion_events(
    db: Session, make_product
) -> None:
    make_product("Milk", current_stock=42, reorder_threshold=60)

    inventory_service.run_inventory_check(db)

    actions = _audit_actions(db)
    assert AuditAction.LOW_STOCK_DETECTED.value in actions
    assert AuditAction.INVENTORY_CHECK_COMPLETED.value in actions


def test_low_stock_event_records_useful_context(db: Session, make_product) -> None:
    product = make_product("Milk", current_stock=42, reorder_threshold=60, unit="litre")

    inventory_service.run_inventory_check(db)

    entry = db.scalar(
        select(AuditLog).where(
            AuditLog.action == AuditAction.LOW_STOCK_DETECTED.value
        )
    )
    assert entry is not None
    assert entry.actor is AuditActor.SYSTEM
    assert entry.event_metadata["product_id"] == product.id
    assert entry.event_metadata["current_stock"] == 42
    assert entry.event_metadata["reorder_threshold"] == 60
    assert entry.event_metadata["shortfall"] == 18
    assert "Milk" in entry.reasoning_text


def test_healthy_product_produces_no_low_stock_event(db: Session, make_product) -> None:
    make_product("Rice", current_stock=320, reorder_threshold=150)

    result = inventory_service.run_inventory_check(db)

    assert result.low_stock_products == []
    assert AuditAction.LOW_STOCK_DETECTED.value not in _audit_actions(db)
    # The sweep is still recorded, so "no event" is never ambiguous.
    assert AuditAction.INVENTORY_CHECK_COMPLETED.value in _audit_actions(db)


def test_repeated_checks_do_not_duplicate_low_stock_events(
    db: Session, make_product
) -> None:
    """A merchant polling the endpoint must not flood the audit trail."""
    make_product("Milk", current_stock=42, reorder_threshold=60)

    first = inventory_service.run_inventory_check(db)
    second = inventory_service.run_inventory_check(db)
    third = inventory_service.run_inventory_check(db)

    detections = [
        action
        for action in _audit_actions(db)
        if action == AuditAction.LOW_STOCK_DETECTED.value
    ]
    assert len(detections) == 1
    # Still reported as low stock every time, just not re-audited.
    assert len(first.low_stock_products) == 1
    assert len(second.low_stock_products) == 1
    assert len(third.low_stock_products) == 1
    assert second.newly_detected_product_ids == []
    assert third.newly_detected_product_ids == []


def test_stock_change_re_arms_low_stock_detection(db: Session, make_product) -> None:
    """Dip, restock, dip again → two genuine detections."""
    product = make_product("Milk", current_stock=42, reorder_threshold=60)

    inventory_service.run_inventory_check(db)

    # Restocked above threshold...
    product.current_stock = 100
    db.commit()
    inventory_service.run_inventory_check(db)

    # ...then dips again. This is a new event, not a duplicate.
    product.current_stock = 30
    db.commit()
    result = inventory_service.run_inventory_check(db)

    detections = [
        action
        for action in _audit_actions(db)
        if action == AuditAction.LOW_STOCK_DETECTED.value
    ]
    assert len(detections) == 2
    assert result.newly_detected_product_ids == [product.id]


# --- API --------------------------------------------------------------------


def test_check_endpoint_returns_low_stock_products(
    client: TestClient, make_product
) -> None:
    make_product("Milk", current_stock=42, reorder_threshold=60, unit="litre")
    make_product("Rice", current_stock=320, reorder_threshold=150, unit="kg")

    response = client.post("/api/inventory/check")

    assert response.status_code == 200
    body = response.json()
    assert body["products_checked"] == 2
    assert body["low_stock_count"] == 1
    assert len(body["low_stock_products"]) == 1

    milk = body["low_stock_products"][0]
    assert milk["name"] == "Milk"
    assert milk["current_stock"] == 42
    assert milk["reorder_threshold"] == 60
    assert milk["unit"] == "litre"
    assert milk["status"] == "low_stock"
    assert milk["shortfall"] == 18
    assert "checked_at" in body


def test_check_endpoint_on_empty_database(client: TestClient) -> None:
    response = client.post("/api/inventory/check")

    assert response.status_code == 200
    body = response.json()
    assert body["low_stock_products"] == []
    assert body["products_checked"] == 0
    assert body["low_stock_count"] == 0


def test_low_stock_endpoint_is_read_only(
    client: TestClient, db: Session, make_product
) -> None:
    """GET must not write audit events, so it is safe to poll."""
    make_product("Milk", current_stock=42, reorder_threshold=60)

    response = client.get("/api/inventory/low-stock")

    assert response.status_code == 200
    assert [item["name"] for item in response.json()] == ["Milk"]
    assert _audit_actions(db) == []


def test_both_endpoints_agree(client: TestClient, make_product) -> None:
    """They share one service function; this proves they did not diverge."""
    make_product("Milk", current_stock=42, reorder_threshold=60)
    make_product("Coffee Beans", current_stock=12, reorder_threshold=25)
    make_product("Rice", current_stock=320, reorder_threshold=150)

    posted = client.post("/api/inventory/check").json()["low_stock_products"]
    fetched = client.get("/api/inventory/low-stock").json()

    assert [item["id"] for item in posted] == [item["id"] for item in fetched]


def test_check_endpoint_uses_seeded_dataset(client: TestClient, seeded_db) -> None:
    """Against the demo data, exactly Milk and Coffee Beans are low."""
    body = client.post("/api/inventory/check").json()

    assert body["products_checked"] == 4
    assert {item["name"] for item in body["low_stock_products"]} == {
        "Milk",
        "Coffee Beans",
    }
