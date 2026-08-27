"""Supplier selection, validation, and authoritative pricing.

Two invariants live here, and they are the reason this file exists separately
from the agent:

1. **A supplier id from the model is a pointer, not a fact.** It is looked up in
   the database and checked to belong to the product before anything is done
   with it. A model naming supplier 999, or naming a real supplier that belongs
   to a different product, gets rejected.
2. **Price comes from the database, always.** `calculate_amount_paise` reads the
   supplier row at the moment of use. Nothing accepts a price from the model or
   from a client.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.llm_client import (
    AgentMalformedOutputError,
    AgentNotConfigured,
    AgentTransportError,
)
from app.agents.supplier_agent import (
    LLMSupplierProvider,
    SupplierOption,
    SupplierProvider,
    SupplierSelection,
    SupplierSelectionRequest,
)
from app.core.errors import (
    AgentNotConfiguredError,
    NoSuppliersError,
    SupplierNotForProductError,
    SupplierNotFoundError,
    SupplierNotPayableError,
    SupplierSelectionInvalidError,
    SupplierSelectionUnavailableError,
)
from app.core.money import format_inr
from app.models.audit_log import AuditAction, AuditActor
from app.models.product import Product
from app.models.supplier import Supplier
from app.services import audit_service

logger = logging.getLogger("restock.supplier")


@dataclass(frozen=True)
class ValidatedSupplierChoice:
    """A supplier the backend has verified and is willing to pay."""

    supplier: Supplier
    reasoning: str
    provider: str
    options_considered: int


def list_suppliers_for_product(db: Session, product_id: int) -> Sequence[Supplier]:
    """All suppliers for a product, cheapest first."""
    return db.scalars(
        select(Supplier)
        .where(Supplier.product_id == product_id)
        .order_by(Supplier.price_per_unit_paise, Supplier.delivery_days)
    ).all()


def payable_suppliers(db: Session, product_id: int) -> list[Supplier]:
    """Suppliers that could actually receive a payout.

    A supplier with no RazorpayX fund account is excluded *before* the model
    sees it. Showing it and rejecting the choice afterwards would waste an LLM
    call and produce a confusing error for something the merchant can see is an
    onboarding gap, not a decision problem.
    """
    return [
        supplier
        for supplier in list_suppliers_for_product(db, product_id)
        if supplier.has_fund_account
    ]


def calculate_amount_paise(supplier: Supplier, quantity: int) -> int:
    """The authoritative order total.

    `quantity × price_per_unit_paise`, both integers, so the result is exact.
    This is the only place an order amount is ever computed.
    """
    return quantity * supplier.price_per_unit_paise


def validate_supplier_for_product(
    db: Session,
    supplier_id: int,
    product: Product,
    *,
    require_fund_account: bool = True,
) -> Supplier:
    """Re-read a supplier and confirm it may be used for this product.

    Called both when the model proposes a supplier and again at approval time,
    because a supplier can be deleted or repointed between the two.
    """
    supplier = db.get(Supplier, supplier_id)

    if supplier is None:
        raise SupplierNotFoundError(
            f"Supplier {supplier_id} does not exist.",
            details={"supplier_id": supplier_id},
        )

    if supplier.product_id != product.id:
        # The dangerous case: a real supplier, but for a different product. It
        # would price and pay perfectly well, for the wrong goods.
        raise SupplierNotForProductError(
            f"Supplier {supplier_id} ({supplier.name}) supplies product "
            f"{supplier.product_id}, not {product.id} ({product.name}).",
            details={
                "supplier_id": supplier_id,
                "supplier_product_id": supplier.product_id,
                "requested_product_id": product.id,
            },
        )

    if require_fund_account and not supplier.has_fund_account:
        raise SupplierNotPayableError(
            f"Supplier {supplier.name} has no RazorpayX fund account, so it "
            "cannot be paid. Add the supplier's bank details first.",
            details={"supplier_id": supplier_id},
        )

    return supplier


def build_request(
    product: Product,
    suppliers: Sequence[Supplier],
    quantity: int,
) -> SupplierSelectionRequest:
    return SupplierSelectionRequest(
        product_name=product.name,
        unit=product.unit,
        quantity=quantity,
        current_stock=product.current_stock,
        reorder_threshold=product.reorder_threshold,
        suppliers=tuple(
            SupplierOption(
                supplier_id=supplier.id,
                name=supplier.name,
                price_per_unit_paise=supplier.price_per_unit_paise,
                delivery_days=supplier.delivery_days,
            )
            for supplier in suppliers
        ),
    )


def get_supplier_provider() -> SupplierProvider:
    """FastAPI dependency. One production implementation; tests override it."""
    return LLMSupplierProvider()


def select_supplier(
    db: Session,
    product: Product,
    quantity: int,
    *,
    provider: SupplierProvider,
) -> ValidatedSupplierChoice:
    """Ask the model to choose a supplier, then verify the answer.

    Raises rather than falling back to "cheapest". A silent fallback would be
    recorded in the audit trail as an agent decision it never made.
    """
    candidates = payable_suppliers(db, product.id)

    if not candidates:
        all_suppliers = list_suppliers_for_product(db, product.id)
        if not all_suppliers:
            raise NoSuppliersError(
                f"{product.name} has no suppliers, so nothing can be ordered.",
                details={"product_id": product.id},
            )
        raise SupplierNotPayableError(
            f"{product.name} has {len(all_suppliers)} supplier(s) but none has a "
            "RazorpayX fund account, so none can be paid. Onboard a supplier's "
            "bank details first.",
            details={
                "product_id": product.id,
                "supplier_count": len(all_suppliers),
            },
        )

    request = build_request(product, candidates, quantity)
    provider_name = getattr(provider, "name", type(provider).__name__)

    audit_service.log(
        db,
        actor=AuditActor.SYSTEM,
        action=AuditAction.SUPPLIER_SELECTION_STARTED,
        reasoning_text=(
            f"Asking {provider_name} to choose between {len(candidates)} payable "
            f"supplier(s) for {quantity} {product.unit} of {product.name}."
        ),
        metadata={
            "product_id": product.id,
            "provider": provider_name,
            "quantity": quantity,
            "candidate_supplier_ids": [s.id for s in candidates],
        },
    )
    db.commit()

    # --- call the model ---
    try:
        selection: SupplierSelection = provider.select(request)
    except AgentNotConfigured as exc:
        _audit_failure(db, product, "provider_not_configured", str(exc))
        raise AgentNotConfiguredError(
            "No LLM provider is configured on this server, so a supplier "
            "recommendation cannot be generated. No order was created."
        ) from exc
    except AgentTransportError as exc:
        _audit_failure(db, product, "provider_unavailable", str(exc))
        raise SupplierSelectionUnavailableError(
            f"The supplier-selection model could not be reached: {exc} No order "
            "was created.",
            details={"product_id": product.id},
        ) from exc
    except AgentMalformedOutputError as exc:
        _audit_failure(db, product, "malformed_output", str(exc))
        raise SupplierSelectionInvalidError(
            f"The supplier-selection model returned output the backend refuses "
            f"to act on: {exc} No order was created.",
            details={"product_id": product.id},
        ) from exc
    except Exception as exc:
        logger.exception("supplier_provider_error product_id=%s", product.id)
        _audit_failure(db, product, "provider_error", type(exc).__name__)
        raise SupplierSelectionInvalidError(
            "The supplier-selection model returned output the backend refuses "
            "to act on. No order was created.",
            details={"product_id": product.id},
        ) from exc

    # --- verify the answer against the database ---
    offered_ids = {option.supplier_id for option in request.suppliers}
    if selection.supplier_id not in offered_ids:
        # Covers both a hallucinated id and a real supplier that was not on the
        # menu (e.g. one filtered out for having no fund account).
        _audit_failure(
            db,
            product,
            "supplier_not_offered",
            f"Model chose supplier {selection.supplier_id}; offered "
            f"{sorted(offered_ids)}.",
            model_reasoning=selection.reasoning,
        )
        raise SupplierSelectionInvalidError(
            f"The model recommended supplier {selection.supplier_id}, which was "
            "not one of the options it was given. No order was created.",
            details={
                "recommended_supplier_id": selection.supplier_id,
                "offered_supplier_ids": sorted(offered_ids),
            },
        )

    try:
        # Independent re-read, not a lookup in the request we built. This is the
        # check that a supplier belongs to the product.
        supplier = validate_supplier_for_product(db, selection.supplier_id, product)
    except (
        SupplierNotFoundError,
        SupplierNotForProductError,
        SupplierNotPayableError,
    ) as exc:
        _audit_failure(
            db,
            product,
            "supplier_validation_failed",
            exc.message,
            model_reasoning=selection.reasoning,
        )
        raise

    choice = ValidatedSupplierChoice(
        supplier=supplier,
        reasoning=selection.reasoning,
        provider=provider_name,
        options_considered=len(candidates),
    )

    audit_service.log(
        db,
        actor=AuditActor.AGENT,
        action=AuditAction.SUPPLIER_SELECTED,
        reasoning_text=selection.reasoning,
        metadata={
            "product_id": product.id,
            "supplier_id": supplier.id,
            "supplier_name": supplier.name,
            "provider": provider_name,
            # Price recorded from the database row, not from the model.
            "unit_price_paise": supplier.price_per_unit_paise,
            "unit_price_display": format_inr(supplier.price_per_unit_paise),
            "delivery_days": supplier.delivery_days,
            "options_considered": len(candidates),
            "rejected_supplier_ids": [
                s.id for s in candidates if s.id != supplier.id
            ],
        },
    )
    db.commit()

    logger.info(
        "supplier_selected product_id=%s supplier_id=%s provider=%s options=%d",
        product.id,
        supplier.id,
        provider_name,
        len(candidates),
    )
    return choice


def _audit_failure(
    db: Session,
    product: Product,
    reason_code: str,
    detail: str,
    *,
    model_reasoning: str | None = None,
) -> None:
    logger.warning(
        "supplier_selection_failed product_id=%s reason=%s", product.id, reason_code
    )
    audit_service.log_independently(
        db,
        actor=AuditActor.SYSTEM,
        action=AuditAction.SUPPLIER_SELECTION_FAILED,
        reasoning_text=(
            f"Supplier selection for {product.name} rejected ({reason_code}): "
            f"{detail} No order was created."
            + (
                f" The model's stated reasoning was: {model_reasoning}"
                if model_reasoning
                else ""
            )
        ),
        metadata={
            "product_id": product.id,
            "product_name": product.name,
            "reason_code": reason_code,
            "detail": detail,
        },
    )
