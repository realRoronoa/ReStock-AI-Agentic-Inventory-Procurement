"""Sales recording schemas.

`SaleCreate` is the only request body in the whole API that a client uses to
change stock, and it is deliberately narrow: a product, a quantity, and
optionally a date. It cannot set a stock level, a price, or anything monetary.
"""

from __future__ import annotations

from datetime import date as date_type

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.inventory import StockStatus


class SaleCreate(BaseModel):
    """Record units sold."""

    model_config = ConfigDict(extra="forbid")

    product_id: int = Field(gt=0)
    quantity: int = Field(
        gt=0,
        le=1_000_000,
        description="Units sold. Must be positive and no more than current stock.",
    )
    sale_date: date_type | None = Field(
        default=None,
        description=(
            "Defaults to today in the server timezone. A second sale on the "
            "same date accumulates into that day rather than creating a "
            "duplicate row."
        ),
    )


class SaleResponse(BaseModel):
    """Result of recording a sale, with the resulting stock position."""

    product_id: int
    product_name: str
    unit: str
    quantity_sold: int
    sale_date: date_type

    stock_before: int
    stock_after: int
    reorder_threshold: int
    status: StockStatus
    is_low_stock: bool
    shortfall: int = Field(description="Units below the threshold; 0 if healthy.")

    became_low_stock: bool = Field(
        description=(
            "True when this sale is what pushed the product below its "
            "threshold. Surfaced so a client can prompt for a reorder without "
            "a second request."
        )
    )
    next_step: str

    @classmethod
    def from_result(cls, result) -> "SaleResponse":
        product = result.product
        shortfall = max(0, product.reorder_threshold - product.current_stock)
        return cls(
            product_id=product.id,
            product_name=product.name,
            unit=product.unit,
            quantity_sold=result.quantity_sold,
            sale_date=result.sale_date,
            stock_before=result.stock_before,
            stock_after=result.stock_after,
            reorder_threshold=product.reorder_threshold,
            status=(
                StockStatus.LOW_STOCK if product.is_low_stock else StockStatus.OK
            ),
            is_low_stock=product.is_low_stock,
            shortfall=shortfall,
            became_low_stock=result.became_low_stock,
            next_step=(
                f"{product.name} is now below its reorder threshold. Call "
                f"POST /api/proposals/product/{product.id} to generate a "
                "reorder proposal."
                if product.is_low_stock
                else f"{product.name} is still above its reorder threshold."
            ),
        )
