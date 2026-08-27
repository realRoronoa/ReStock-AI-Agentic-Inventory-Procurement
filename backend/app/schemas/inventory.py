"""Inventory check schemas."""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class StockStatus(str, Enum):
    LOW_STOCK = "low_stock"
    OK = "ok"


class LowStockProduct(BaseModel):
    """A product identified as needing a reorder."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    current_stock: int
    reorder_threshold: int
    unit: str
    status: StockStatus = StockStatus.LOW_STOCK
    shortfall: int = Field(
        description="How many units below the threshold the product currently is."
    )

    @classmethod
    def from_product(cls, product) -> "LowStockProduct":
        return cls(
            id=product.id,
            name=product.name,
            current_stock=product.current_stock,
            reorder_threshold=product.reorder_threshold,
            unit=product.unit,
            status=(
                StockStatus.LOW_STOCK if product.is_low_stock else StockStatus.OK
            ),
            shortfall=max(0, product.reorder_threshold - product.current_stock),
        )


class InventoryCheckResponse(BaseModel):
    """Result of `POST /api/inventory/check`."""

    low_stock_products: list[LowStockProduct]
    products_checked: int = Field(description="Total products evaluated in this sweep.")
    low_stock_count: int
    newly_detected_product_ids: list[int] = Field(
        description=(
            "Products that produced a new LOW_STOCK_DETECTED audit event in this "
            "check. Products already recorded as low since their last change are "
            "reported as low stock but not re-audited."
        )
    )
    checked_at: datetime
