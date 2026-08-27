"""Inventory endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.schemas.inventory import InventoryCheckResponse, LowStockProduct
from app.services import inventory_service

router = APIRouter(prefix="/api/inventory", tags=["inventory"])


@router.post(
    "/check",
    response_model=InventoryCheckResponse,
    status_code=status.HTTP_200_OK,
    summary="Run a low-stock sweep",
    description=(
        "Evaluates every product against the deterministic rule "
        "`current_stock < reorder_threshold` and records the outcome in the "
        "audit trail. No LLM is involved.\n\n"
        "This is a POST rather than a GET because it writes audit events. Use "
        "`GET /api/inventory/low-stock` for a read-only view."
    ),
)
def check_inventory(db: Session = Depends(get_db)) -> InventoryCheckResponse:
    result = inventory_service.run_inventory_check(db)

    return InventoryCheckResponse(
        low_stock_products=[
            LowStockProduct.from_product(product)
            for product in result.low_stock_products
        ],
        products_checked=result.products_checked,
        low_stock_count=len(result.low_stock_products),
        newly_detected_product_ids=result.newly_detected_product_ids,
        checked_at=result.checked_at,
    )


@router.get(
    "/low-stock",
    response_model=list[LowStockProduct],
    summary="List low-stock products (read-only)",
    description=(
        "Same detection rule as `POST /api/inventory/check`, sharing the same "
        "service function, but writes nothing. Safe to poll."
    ),
)
def list_low_stock(db: Session = Depends(get_db)) -> list[LowStockProduct]:
    products = inventory_service.find_low_stock_products(db)
    return [LowStockProduct.from_product(product) for product in products]
