"""Tests for the demo seed dataset.

The seed data is not decoration — later milestones demo and test against it, so
its shape is asserted here (spec section 8) and its idempotency is proven.
"""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.product import Product
from app.models.sales_history import SalesHistory
from app.models.supplier import Supplier
from app.seed.data import PRODUCTS, SALES_HISTORY_DAYS, generate_sales_history
from app.seed.seed import seed

REFERENCE_DAY = date(2025, 6, 1)


def test_seed_creates_the_expected_products(seeded_db: Session) -> None:
    names = set(seeded_db.scalars(select(Product.name)).all())

    assert names == {"Milk", "Coffee Beans", "Rice", "Cooking Oil"}


def test_every_product_has_28_days_of_history(seeded_db: Session) -> None:
    counts = dict(
        seeded_db.execute(
            select(Product.name, func.count(SalesHistory.id))
            .join(SalesHistory, SalesHistory.product_id == Product.id)
            .group_by(Product.name)
        ).all()
    )

    assert len(counts) == 4
    assert all(count == SALES_HISTORY_DAYS for count in counts.values()), counts


def test_every_product_has_at_least_two_suppliers(seeded_db: Session) -> None:
    counts = dict(
        seeded_db.execute(
            select(Product.name, func.count(Supplier.id))
            .join(Supplier, Supplier.product_id == Product.id)
            .group_by(Product.name)
        ).all()
    )

    assert len(counts) == 4
    assert all(count >= 2 for count in counts.values()), counts


def test_at_least_two_products_start_below_threshold(seeded_db: Session) -> None:
    low = seeded_db.scalars(
        select(Product.name).where(Product.current_stock < Product.reorder_threshold)
    ).all()

    assert set(low) == {"Milk", "Coffee Beans"}


@pytest.mark.parametrize("spec", PRODUCTS, ids=lambda spec: spec.name)
def test_suppliers_present_a_real_trade_off(seeded_db: Session, spec) -> None:
    """The cheapest supplier must also be the slowest.

    Without this the supplier agent has no decision to make: one option would
    dominate on both price and speed.
    """
    suppliers = seeded_db.scalars(
        select(Supplier)
        .join(Product)
        .where(Product.name == spec.name)
        .order_by(Supplier.price_per_unit_paise)
    ).all()

    cheapest, priciest = suppliers[0], suppliers[-1]
    assert cheapest.price_per_unit_paise < priciest.price_per_unit_paise
    assert cheapest.delivery_days > priciest.delivery_days


def test_sales_history_generation_is_deterministic() -> None:
    first = generate_sales_history(PRODUCTS[0], today=REFERENCE_DAY)
    second = generate_sales_history(PRODUCTS[0], today=REFERENCE_DAY)

    assert first == second
    assert len(first) == SALES_HISTORY_DAYS
    # Oldest first, ending the day before `today`.
    assert first[0][0] < first[-1][0]
    assert first[-1][0] == date(2025, 5, 31)
    assert all(quantity >= 0 for _, quantity in first)


def test_seed_is_idempotent(db: Session) -> None:
    seed(db, today=REFERENCE_DAY)
    first_counts = _row_counts(db)

    summaries = seed(db, today=REFERENCE_DAY)

    assert _row_counts(db) == first_counts
    assert all(summary["created"] is False for summary in summaries)
    assert all(summary["suppliers_added"] == 0 for summary in summaries)
    assert all(summary["sales_days_added"] == 0 for summary in summaries)


def test_reseeding_does_not_clobber_workflow_stock_changes(db: Session) -> None:
    """A completed procurement must survive a re-seed."""
    seed(db, today=REFERENCE_DAY)
    milk = db.scalar(select(Product).where(Product.name == "Milk"))
    assert milk is not None
    milk.current_stock = 137
    db.commit()

    seed(db, today=REFERENCE_DAY)

    db.refresh(milk)
    assert milk.current_stock == 137


def test_reset_stock_flag_restores_seed_values(db: Session) -> None:
    seed(db, today=REFERENCE_DAY)
    milk = db.scalar(select(Product).where(Product.name == "Milk"))
    assert milk is not None
    milk.current_stock = 137
    db.commit()

    seed(db, today=REFERENCE_DAY, reset_stock=True)

    db.refresh(milk)
    assert milk.current_stock == 42


def test_cooking_oil_has_a_supplier_that_cannot_be_paid(seeded_db: Session) -> None:
    """Deliberate fixture for the missing-fund-account failure path."""
    suppliers = seeded_db.scalars(
        select(Supplier).join(Product).where(Product.name == "Cooking Oil")
    ).all()

    assert any(s.razorpay_fund_account_id is None for s in suppliers)
    assert any(s.razorpay_fund_account_id is not None for s in suppliers)


def _row_counts(db: Session) -> dict[str, int]:
    return {
        "products": db.scalar(select(func.count(Product.id))) or 0,
        "suppliers": db.scalar(select(func.count(Supplier.id))) or 0,
        "sales_history": db.scalar(select(func.count(SalesHistory.id))) or 0,
    }
