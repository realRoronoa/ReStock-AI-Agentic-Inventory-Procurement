"""Product read endpoints.

Read-only catalogue access. There is deliberately no product-mutation endpoint:
in this system stock is changed by the procurement workflow (a verified payout
webhook), never by a direct client write.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.database import get_db
from app.core.errors import ProductNotFoundError
from app.models.product import Product
from app.models.sales_history import SalesHistory
from app.schemas.error import error_responses
from app.schemas.product import ProductDetail, ProductRead, SalesPoint

router = APIRouter(prefix="/api/products", tags=["products"])

#: Default sales-history window. Matches the 28-day window the forecast agent
#: is given, so the API and the agent see the same evidence.
DEFAULT_SALES_WINDOW_DAYS = 28

LIST_DESCRIPTION = (
    "Every product with its stock level and computed `is_low_stock` flag, "
    "sorted by name.\n\n"
    "`is_low_stock` is `current_stock < reorder_threshold` — a strict "
    "less-than, so stock exactly at the threshold is **not** low. It is "
    "computed on read, never stored."
)

DETAIL_DESCRIPTION = (
    "A product plus everything needed to reason about reordering it: its "
    "suppliers (cheapest first) and its recent daily sales (newest first).\n\n"
    "A supplier with `has_fund_account: false` cannot be paid and will not be "
    "offered to the supplier agent — onboard its bank details first.\n\n"
    "There is deliberately no product-mutation endpoint. Stock is changed only "
    "by a signature-verified payout webhook."
)


@router.get(
    "",
    response_model=list[ProductRead],
    summary="List products",
    description=LIST_DESCRIPTION,
)
def list_products(
    low_stock: bool | None = Query(
        default=None,
        description=(
            "Filter to products below (true) or at/above (false) their threshold."
        ),
    ),
    db: Session = Depends(get_db),
) -> list[Product]:
    stmt = select(Product).order_by(Product.name)
    if low_stock is True:
        stmt = stmt.where(Product.current_stock < Product.reorder_threshold)
    elif low_stock is False:
        stmt = stmt.where(Product.current_stock >= Product.reorder_threshold)
    return list(db.scalars(stmt).all())


@router.get(
    "/{product_id}",
    response_model=ProductDetail,
    summary="Get a product with its suppliers and recent sales",
    description=DETAIL_DESCRIPTION,
    responses=error_responses((404, "Product not found")),
)
def get_product(
    product_id: int,
    sales_days: int = Query(
        default=DEFAULT_SALES_WINDOW_DAYS,
        ge=1,
        le=365,
        description="How many of the most recent sales days to return.",
    ),
    db: Session = Depends(get_db),
) -> ProductDetail:
    product = db.scalar(
        select(Product)
        .where(Product.id == product_id)
        .options(selectinload(Product.suppliers))
    )
    if product is None:
        raise ProductNotFoundError(
            f"Product {product_id} not found.", details={"product_id": product_id}
        )

    recent_sales = db.scalars(
        select(SalesHistory)
        .where(SalesHistory.product_id == product_id)
        .order_by(SalesHistory.date.desc())
        .limit(sales_days)
    ).all()

    detail = ProductDetail.model_validate(product)
    detail.recent_sales = [SalesPoint.model_validate(row) for row in recent_sales]
    detail.suppliers.sort(key=lambda s: (s.price_per_unit_paise, s.delivery_days))
    return detail
