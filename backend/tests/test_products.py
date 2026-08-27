"""Tests for /health and the product read endpoints."""

from __future__ import annotations

from datetime import date, timedelta

from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.product import Product
from app.models.sales_history import SalesHistory
from app.models.supplier import Supplier


def _commit_expecting_integrity_error(db: Session) -> bool:
    """Commit and report whether the database rejected the write."""
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return True
    return False


# --- health -----------------------------------------------------------------


def test_health_reports_ok_and_database_up(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["database"] == "up"
    assert body["environment"] == "test"


def test_health_never_leaks_configuration(client: TestClient) -> None:
    body = client.get("/health").json()

    # No DSN, no key material, no secret names.
    assert "database_url" not in {key.lower() for key in body}
    assert "sqlite" not in str(body).lower()


# --- GET /api/products ------------------------------------------------------


def test_list_products_returns_empty_list_when_no_data(client: TestClient) -> None:
    response = client.get("/api/products")

    assert response.status_code == 200
    assert response.json() == []


def test_list_products_computes_is_low_stock(client: TestClient, make_product) -> None:
    make_product("Milk", current_stock=42, reorder_threshold=60)
    make_product("Rice", current_stock=320, reorder_threshold=150)

    body = client.get("/api/products").json()

    by_name = {item["name"]: item for item in body}
    assert by_name["Milk"]["is_low_stock"] is True
    assert by_name["Rice"]["is_low_stock"] is False


def test_list_products_is_sorted_by_name(client: TestClient, make_product) -> None:
    for name in ("Rice", "Milk", "Coffee Beans"):
        make_product(name)

    names = [item["name"] for item in client.get("/api/products").json()]

    assert names == ["Coffee Beans", "Milk", "Rice"]


def test_list_products_low_stock_filter(client: TestClient, make_product) -> None:
    make_product("Milk", current_stock=42, reorder_threshold=60)
    make_product("Rice", current_stock=320, reorder_threshold=150)

    low = client.get("/api/products", params={"low_stock": "true"}).json()
    healthy = client.get("/api/products", params={"low_stock": "false"}).json()

    assert [item["name"] for item in low] == ["Milk"]
    assert [item["name"] for item in healthy] == ["Rice"]


def test_stock_exactly_at_threshold_is_not_low(client: TestClient, make_product) -> None:
    """Boundary case: the rule is a strict less-than, not less-than-or-equal."""
    make_product("Milk", current_stock=60, reorder_threshold=60)

    body = client.get("/api/products").json()

    assert body[0]["is_low_stock"] is False


# --- GET /api/products/{id} -------------------------------------------------


def test_get_product_returns_suppliers_and_recent_sales(
    client: TestClient, db: Session, make_product
) -> None:
    product = make_product(
        "Milk",
        current_stock=42,
        reorder_threshold=60,
        unit="litre",
        suppliers=[
            {"name": "Fast Co", "price_per_unit_paise": 4_800, "delivery_days": 2},
            {"name": "Cheap Co", "price_per_unit_paise": 4_400, "delivery_days": 6},
        ],
    )
    for day, quantity in ((date(2025, 6, 1), 20), (date(2025, 6, 2), 24)):
        db.add(SalesHistory(product_id=product.id, date=day, quantity_sold=quantity))
    db.commit()

    body = client.get(f"/api/products/{product.id}").json()

    assert body["unit"] == "litre"
    assert body["is_low_stock"] is True
    # Cheapest first.
    assert [s["name"] for s in body["suppliers"]] == ["Cheap Co", "Fast Co"]
    # Most recent sales day first.
    assert [s["date"] for s in body["recent_sales"]] == ["2025-06-02", "2025-06-01"]


def test_get_product_exposes_price_in_paise_and_rupees(
    client: TestClient, make_product
) -> None:
    product = make_product(
        "Milk", suppliers=[{"price_per_unit_paise": 4_850, "delivery_days": 2}]
    )

    supplier = client.get(f"/api/products/{product.id}").json()["suppliers"][0]

    assert supplier["price_per_unit_paise"] == 4_850
    # Serialised as a string so no client parses money into a float.
    assert supplier["price_per_unit"] == "48.50"


def test_get_product_flags_supplier_without_fund_account(
    client: TestClient, make_product
) -> None:
    product = make_product(
        "Cooking Oil",
        suppliers=[
            {"name": "Onboarded", "razorpay_fund_account_id": "fa_TESTOK"},
            {"name": "Not onboarded", "razorpay_fund_account_id": None},
        ],
    )

    suppliers = client.get(f"/api/products/{product.id}").json()["suppliers"]

    by_name = {s["name"]: s for s in suppliers}
    assert by_name["Onboarded"]["has_fund_account"] is True
    assert by_name["Not onboarded"]["has_fund_account"] is False


def test_get_product_sales_days_window_is_respected(
    client: TestClient, db: Session, make_product
) -> None:
    product = make_product("Milk")
    start = date(2025, 6, 1)
    for offset in range(10):
        db.add(
            SalesHistory(
                product_id=product.id,
                date=start + timedelta(days=offset),
                quantity_sold=offset,
            )
        )
    db.commit()

    body = client.get(f"/api/products/{product.id}", params={"sales_days": 3}).json()

    assert len(body["recent_sales"]) == 3
    assert body["recent_sales"][0]["date"] == "2025-06-10"


def test_get_missing_product_returns_404(client: TestClient) -> None:
    response = client.get("/api/products/9999")

    assert response.status_code == 404
    body = response.json()
    assert body["error"]["code"] == "PRODUCT_NOT_FOUND"
    assert "9999" in body["error"]["message"]
    assert body["error"]["details"]["product_id"] == 9999


def test_get_product_rejects_non_integer_id(client: TestClient) -> None:
    assert client.get("/api/products/not-an-id").status_code == 422


def test_sales_days_out_of_range_is_rejected(client: TestClient, make_product) -> None:
    product = make_product("Milk")

    assert (
        client.get(f"/api/products/{product.id}", params={"sales_days": 0}).status_code
        == 422
    )
    assert (
        client.get(f"/api/products/{product.id}", params={"sales_days": 400}).status_code
        == 422
    )


# --- Database integrity -----------------------------------------------------


def test_product_name_is_unique(db: Session, make_product) -> None:
    make_product("Milk")

    db.add(Product(name="Milk", unit="litre", current_stock=1, reorder_threshold=1))

    assert _commit_expecting_integrity_error(db), "products.name must be unique"


def test_negative_stock_is_rejected_by_check_constraint(db: Session) -> None:
    db.add(Product(name="Milk", unit="litre", current_stock=-1, reorder_threshold=10))

    assert _commit_expecting_integrity_error(db), "current_stock must be non-negative"


def test_supplier_foreign_key_is_enforced(db: Session) -> None:
    """Proves PRAGMA foreign_keys=ON is actually applied on SQLite."""
    db.add(
        Supplier(
            product_id=99_999,
            name="Ghost Supplier",
            price_per_unit_paise=1_000,
            delivery_days=1,
        )
    )

    assert _commit_expecting_integrity_error(db), "supplier FK must be enforced"


def test_sales_history_one_row_per_product_per_day(db: Session, make_product) -> None:
    product = make_product("Milk")
    db.add(SalesHistory(product_id=product.id, date=date(2025, 6, 1), quantity_sold=10))
    db.commit()

    db.add(SalesHistory(product_id=product.id, date=date(2025, 6, 1), quantity_sold=99))

    assert _commit_expecting_integrity_error(
        db
    ), "(product_id, date) must be unique in sales_history"


def test_supplier_price_must_be_positive(db: Session, make_product) -> None:
    product = make_product("Milk")
    db.add(
        Supplier(
            product_id=product.id,
            name="Free Milk Co",
            price_per_unit_paise=0,
            delivery_days=1,
        )
    )

    assert _commit_expecting_integrity_error(db), "price must be positive"
