"""Idempotent demo-data seeding.

Run from the `backend/` directory, after the schema exists:

    alembic upgrade head
    python -m app.seed.seed

Idempotency strategy — natural keys, not truncation:

* products      matched by `name` (unique)
* suppliers     matched by `(product_id, name)` (unique)
* sales_history matched by `(product_id, date)` (unique)

Re-running therefore inserts only what is missing and reports what it skipped.
Nothing is deleted, and existing rows are never silently overwritten: a product
whose stock has moved through the real workflow keeps its current stock unless
`--reset-stock` is passed.
"""

from __future__ import annotations

import argparse
import logging
from datetime import date, timezone, datetime

from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

from app.core.database import Base, SessionLocal, engine
from app.core.money import format_inr
from app.models.product import Product
from app.models.sales_history import SalesHistory
from app.models.supplier import Supplier
from app.seed.data import PRODUCTS, SALES_HISTORY_DAYS, ProductSpec, generate_sales_history

logger = logging.getLogger("restock.seed")

REQUIRED_TABLES = ("products", "suppliers", "sales_history", "orders", "audit_log")


class SchemaMissingError(RuntimeError):
    """Raised when the schema has not been migrated yet."""


def _assert_schema_exists() -> None:
    existing = set(inspect(engine).get_table_names())
    missing = [name for name in REQUIRED_TABLES if name not in existing]
    if missing:
        raise SchemaMissingError(
            "Database schema is not initialised (missing tables: "
            f"{', '.join(missing)}). Run `alembic upgrade head` first."
        )


def _seed_product(
    db: Session,
    spec: ProductSpec,
    *,
    today: date,
    reset_stock: bool,
) -> dict[str, int | str | bool]:
    """Create or top up a single product. Returns a per-product summary."""
    product = db.scalar(select(Product).where(Product.name == spec.name))
    created = product is None

    if product is None:
        product = Product(
            name=spec.name,
            unit=spec.unit,
            current_stock=spec.current_stock,
            reorder_threshold=spec.reorder_threshold,
        )
        db.add(product)
        db.flush()  # assign product.id for the child rows below
    elif reset_stock:
        product.current_stock = spec.current_stock
        product.reorder_threshold = spec.reorder_threshold

    # --- suppliers ---------------------------------------------------------
    existing_supplier_names = set(
        db.scalars(
            select(Supplier.name).where(Supplier.product_id == product.id)
        ).all()
    )
    suppliers_added = 0
    for supplier_spec in spec.suppliers:
        if supplier_spec.name in existing_supplier_names:
            continue
        db.add(
            Supplier(
                product_id=product.id,
                name=supplier_spec.name,
                price_per_unit_paise=supplier_spec.price_per_unit_paise,
                delivery_days=supplier_spec.delivery_days,
                razorpay_fund_account_id=supplier_spec.razorpay_fund_account_id,
            )
        )
        suppliers_added += 1

    # --- sales history -----------------------------------------------------
    existing_dates = set(
        db.scalars(
            select(SalesHistory.date).where(SalesHistory.product_id == product.id)
        ).all()
    )
    sales_added = 0
    for day, quantity in generate_sales_history(spec, today=today):
        if day in existing_dates:
            continue
        db.add(
            SalesHistory(product_id=product.id, date=day, quantity_sold=quantity)
        )
        sales_added += 1

    return {
        "product": spec.name,
        "created": created,
        "suppliers_added": suppliers_added,
        "sales_days_added": sales_added,
    }


def seed(
    db: Session,
    *,
    today: date | None = None,
    reset_stock: bool = False,
) -> list[dict[str, int | str | bool]]:
    """Seed the demo dataset. Safe to call repeatedly.

    Runs as one transaction: either the whole dataset lands or none of it does.
    """
    today = today or datetime.now(timezone.utc).date()
    summaries = [
        _seed_product(db, spec, today=today, reset_stock=reset_stock)
        for spec in PRODUCTS
    ]
    db.commit()
    return summaries


def _print_report(db: Session, summaries: list[dict[str, int | str | bool]]) -> None:
    print(f"\nSeeded {len(summaries)} products ({SALES_HISTORY_DAYS} days of history each)\n")
    header = f"{'PRODUCT':<15}{'STOCK':>8}{'THRESH':>8}{'LOW?':>7}{'SUPPLIERS':>11}{'+DAYS':>7}"
    print(header)
    print("-" * len(header))

    for summary in summaries:
        product = db.scalar(select(Product).where(Product.name == summary["product"]))
        assert product is not None
        print(
            f"{product.name:<15}{product.current_stock:>8}"
            f"{product.reorder_threshold:>8}"
            f"{('LOW' if product.is_low_stock else '-'):>7}"
            f"{len(product.suppliers):>11}"
            f"{summary['sales_days_added']:>7}"
        )

    print("\nSupplier trade-offs:")
    for spec in PRODUCTS:
        product = db.scalar(select(Product).where(Product.name == spec.name))
        assert product is not None
        print(f"  {product.name}:")
        for supplier in sorted(product.suppliers, key=lambda s: s.price_per_unit_paise):
            fund = supplier.razorpay_fund_account_id or "NO FUND ACCOUNT"
            print(
                f"    - {supplier.name:<24} "
                f"{format_inr(supplier.price_per_unit_paise):>12}/{product.unit:<6} "
                f"{supplier.delivery_days:>2}d  {fund}"
            )


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed ReStock AI demo data.")
    parser.add_argument(
        "--reset-stock",
        action="store_true",
        help=(
            "Reset current_stock and reorder_threshold of existing products back "
            "to their seed values. Off by default so real workflow state is not "
            "clobbered."
        ),
    )
    parser.add_argument(
        "--create-tables",
        action="store_true",
        help=(
            "Create tables directly from the models instead of requiring Alembic. "
            "For throwaway local databases only — Alembic remains the source of "
            "truth for schema."
        ),
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)-8s %(message)s")

    if args.create_tables:
        logger.warning("Creating tables from models (bypassing Alembic).")
        Base.metadata.create_all(bind=engine)

    _assert_schema_exists()

    with SessionLocal() as db:
        summaries = seed(db, reset_stock=args.reset_stock)
        _print_report(db, summaries)


if __name__ == "__main__":
    main()
