"""Supplier selection and validation tests.

The critical property: an LLM-supplied supplier id is a pointer to be verified,
never a fact to be trusted. The most dangerous case is a *real* supplier that
belongs to a *different* product — it would price and pay perfectly well, for
the wrong goods.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.llm_client import AgentMalformedOutputError, AgentTransportError
from app.agents.supplier_agent import SupplierSelection
from app.core.errors import (
    NoSuppliersError,
    SupplierNotForProductError,
    SupplierNotFoundError,
    SupplierNotPayableError,
    SupplierSelectionInvalidError,
    SupplierSelectionUnavailableError,
)
from app.models.audit_log import AuditAction, AuditActor, AuditLog
from app.models.order import Order
from app.services import supplier_service
from tests.fakes import FakeSupplierProvider


def _actions(db: Session) -> list[str]:
    return list(db.scalars(select(AuditLog.action).order_by(AuditLog.id)).all())


# --- schema validation ------------------------------------------------------


def test_valid_selection_is_accepted() -> None:
    selection = SupplierSelection.model_validate(
        {"supplier_id": 12, "reasoning": "Cheaper, and the shortfall is mild."}
    )

    assert selection.supplier_id == 12


def test_string_supplier_id_is_rejected() -> None:
    with pytest.raises(ValidationError):
        SupplierSelection.model_validate({"supplier_id": "12", "reasoning": "x"})


def test_missing_supplier_id_is_rejected() -> None:
    with pytest.raises(ValidationError):
        SupplierSelection.model_validate({"reasoning": "I liked the first one."})


def test_empty_reasoning_is_rejected() -> None:
    with pytest.raises(ValidationError):
        SupplierSelection.model_validate({"supplier_id": 12, "reasoning": ""})


# --- authoritative pricing --------------------------------------------------


def test_amount_is_quantity_times_database_price(
    db: Session, low_stock_product
) -> None:
    supplier = low_stock_product.suppliers[0]

    amount = supplier_service.calculate_amount_paise(supplier, 75)

    assert amount == 75 * supplier.price_per_unit_paise


def test_amount_is_exact_for_awkward_prices(db: Session, make_product) -> None:
    """Integer paise, so no rounding drift regardless of the price."""
    product = make_product(
        "Spice", suppliers=[{"price_per_unit_paise": 4_810, "delivery_days": 1}]
    )

    amount = supplier_service.calculate_amount_paise(product.suppliers[0], 999)

    assert amount == 4_805_190


# --- selection validation ---------------------------------------------------


def test_valid_supplier_is_accepted_and_audited(
    db: Session, low_stock_product
) -> None:
    cheapest = min(
        low_stock_product.suppliers, key=lambda s: s.price_per_unit_paise
    )
    provider = FakeSupplierProvider(supplier_id=cheapest.id, reasoning="Cheapest.")

    choice = supplier_service.select_supplier(
        db, low_stock_product, 75, provider=provider
    )

    assert choice.supplier.id == cheapest.id
    assert choice.reasoning == "Cheapest."
    assert AuditAction.SUPPLIER_SELECTED.value in _actions(db)


def test_selection_audit_stores_db_price_not_model_claims(
    db: Session, low_stock_product
) -> None:
    supplier = low_stock_product.suppliers[0]
    provider = FakeSupplierProvider(
        supplier_id=supplier.id,
        reasoning="This supplier charges only INR 1.00 per litre.",
    )

    supplier_service.select_supplier(db, low_stock_product, 75, provider=provider)

    entry = db.scalar(
        select(AuditLog).where(
            AuditLog.action == AuditAction.SUPPLIER_SELECTED.value
        )
    )
    assert entry is not None
    assert entry.actor is AuditActor.AGENT
    # The model's false claim is preserved as its reasoning...
    assert "INR 1.00" in entry.reasoning_text
    # ...but the recorded price is the database price.
    assert entry.event_metadata["unit_price_paise"] == supplier.price_per_unit_paise


def test_nonexistent_supplier_id_is_rejected(db: Session, low_stock_product) -> None:
    provider = FakeSupplierProvider(supplier_id=99_999)

    with pytest.raises(SupplierSelectionInvalidError) as exc:
        supplier_service.select_supplier(
            db, low_stock_product, 75, provider=provider
        )

    assert exc.value.details["recommended_supplier_id"] == 99_999
    assert AuditAction.SUPPLIER_SELECTION_FAILED.value in _actions(db)


def test_supplier_belonging_to_another_product_is_rejected(
    db: Session, low_stock_product, make_product
) -> None:
    """The dangerous case: a real supplier, wrong product."""
    other = make_product(
        "Rice",
        suppliers=[
            {
                "name": "Rice Mills",
                "price_per_unit_paise": 6_200,
                "delivery_days": 4,
                "razorpay_fund_account_id": "fa_TESTRICE01",
            }
        ],
    )
    foreign_supplier = other.suppliers[0]
    provider = FakeSupplierProvider(supplier_id=foreign_supplier.id)

    with pytest.raises(SupplierSelectionInvalidError):
        supplier_service.select_supplier(
            db, low_stock_product, 75, provider=provider
        )

    assert db.scalars(select(Order)).all() == []


def test_direct_validation_rejects_a_foreign_supplier(
    db: Session, low_stock_product, make_product
) -> None:
    """The same check, exercised directly — it is also used at approval time."""
    other = make_product(
        "Rice",
        suppliers=[
            {
                "name": "Rice Mills",
                "price_per_unit_paise": 6_200,
                "delivery_days": 4,
                "razorpay_fund_account_id": "fa_TESTRICE01",
            }
        ],
    )

    with pytest.raises(SupplierNotForProductError) as exc:
        supplier_service.validate_supplier_for_product(
            db, other.suppliers[0].id, low_stock_product
        )

    assert exc.value.details["supplier_product_id"] == other.id


def test_direct_validation_rejects_a_missing_supplier(
    db: Session, low_stock_product
) -> None:
    with pytest.raises(SupplierNotFoundError):
        supplier_service.validate_supplier_for_product(
            db, 99_999, low_stock_product
        )


def test_direct_validation_rejects_an_unpayable_supplier(
    db: Session, make_product
) -> None:
    product = make_product(
        "Cooking Oil",
        current_stock=1,
        reorder_threshold=10,
        suppliers=[
            {
                "name": "Sunrich Oils",
                "price_per_unit_paise": 13_100,
                "delivery_days": 7,
                "razorpay_fund_account_id": None,
            }
        ],
    )

    with pytest.raises(SupplierNotPayableError):
        supplier_service.validate_supplier_for_product(
            db, product.suppliers[0].id, product
        )


def test_product_with_no_suppliers_is_refused(db: Session, make_product) -> None:
    product = make_product("Orphan", current_stock=1, reorder_threshold=10)

    with pytest.raises(NoSuppliersError):
        supplier_service.select_supplier(
            db, product, 10, provider=FakeSupplierProvider()
        )


def test_product_whose_suppliers_cannot_be_paid_is_refused(
    db: Session, make_product
) -> None:
    product = make_product(
        "Cooking Oil",
        current_stock=1,
        reorder_threshold=10,
        suppliers=[
            {"name": "A", "razorpay_fund_account_id": None},
            {"name": "B", "razorpay_fund_account_id": None},
        ],
    )

    with pytest.raises(SupplierNotPayableError) as exc:
        supplier_service.select_supplier(
            db, product, 10, provider=FakeSupplierProvider()
        )

    assert exc.value.details["supplier_count"] == 2


def test_unpayable_suppliers_are_never_offered_to_the_model(
    db: Session, make_product
) -> None:
    """Filtered before the call, not rejected after.

    Showing an option the backend would refuse anyway wastes an LLM call and
    turns an onboarding gap into a confusing decision error.
    """
    product = make_product(
        "Cooking Oil",
        current_stock=1,
        reorder_threshold=10,
        suppliers=[
            {
                "name": "Payable",
                "price_per_unit_paise": 14_200,
                "delivery_days": 3,
                "razorpay_fund_account_id": "fa_TESTOK",
            },
            {
                "name": "Not payable",
                "price_per_unit_paise": 13_100,
                "delivery_days": 7,
                "razorpay_fund_account_id": None,
            },
        ],
    )
    provider = FakeSupplierProvider()

    supplier_service.select_supplier(db, product, 10, provider=provider)

    offered = {option.name for option in provider.calls[0].suppliers}
    assert offered == {"Payable"}


def test_provider_timeout_is_reported_as_unavailable(
    db: Session, low_stock_product
) -> None:
    provider = FakeSupplierProvider(
        raise_error=AgentTransportError("Model did not respond.")
    )

    with pytest.raises(SupplierSelectionUnavailableError) as exc:
        supplier_service.select_supplier(
            db, low_stock_product, 75, provider=provider
        )

    assert exc.value.status_code == 502


def test_malformed_output_is_rejected(db: Session, low_stock_product) -> None:
    provider = FakeSupplierProvider(
        raise_error=AgentMalformedOutputError("Not JSON.")
    )

    with pytest.raises(SupplierSelectionInvalidError):
        supplier_service.select_supplier(
            db, low_stock_product, 75, provider=provider
        )

    assert AuditAction.SUPPLIER_SELECTION_FAILED.value in _actions(db)


def test_supplier_prompt_shows_both_price_and_delivery(
    db: Session, low_stock_product
) -> None:
    """The trade-off must be visible or there is nothing to reason about."""
    provider = FakeSupplierProvider()

    supplier_service.select_supplier(
        db, low_stock_product, 75, provider=provider
    )

    options = provider.calls[0].suppliers
    assert len(options) == 2
    assert {option.price_per_unit_paise for option in options} == {4_400, 4_800}
    assert {option.delivery_days for option in options} == {2, 6}


def test_rejected_options_are_recorded(db: Session, low_stock_product) -> None:
    """So a later reader can see what the agent chose *against*."""
    chosen = low_stock_product.suppliers[0]
    provider = FakeSupplierProvider(supplier_id=chosen.id)

    supplier_service.select_supplier(db, low_stock_product, 75, provider=provider)

    entry = db.scalar(
        select(AuditLog).where(
            AuditLog.action == AuditAction.SUPPLIER_SELECTED.value
        )
    )
    assert entry is not None
    assert chosen.id not in entry.event_metadata["rejected_supplier_ids"]
    assert len(entry.event_metadata["rejected_supplier_ids"]) == 1


def test_suppliers_are_listed_cheapest_first(db: Session, low_stock_product) -> None:
    suppliers = supplier_service.list_suppliers_for_product(
        db, low_stock_product.id
    )

    prices = [supplier.price_per_unit_paise for supplier in suppliers]
    assert prices == sorted(prices)
