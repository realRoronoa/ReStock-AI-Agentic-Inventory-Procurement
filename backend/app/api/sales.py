"""Sales recording endpoint.

The point-of-sale side of the loop, and the only client-reachable write path to
`products.current_stock`. It can only ever *decrease* stock; increases remain the
exclusive result of a signature-verified payout webhook.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.schemas.error import error_responses
from app.schemas.sales import SaleCreate, SaleResponse
from app.services import inventory_service

router = APIRouter(prefix="/api/sales", tags=["sales"])


@router.post(
    "",
    response_model=SaleResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Record units sold",
    description=(
        "Decreases stock and adds to the sales history in one transaction, so "
        "the low-stock detector and the forecast agent always see a consistent "
        "picture.\n\n"
        "A second sale on the same date **accumulates** into that day rather "
        "than creating a duplicate row: sales of 3 and 4 leave one row of 7, "
        "which is what a forecast should see.\n\n"
        "If the sale takes the product below its reorder threshold, "
        "`became_low_stock` is true and a `LOW_STOCK_DETECTED` audit event is "
        "written — so a client can prompt for a reorder without a second "
        "request.\n\n"
        "A sale larger than stock on hand is **refused**, not clamped to zero: "
        "silently absorbing the difference would corrupt the demand signal the "
        "forecast depends on.\n\n"
        "This endpoint cannot increase stock. Stock only rises as the result of "
        "a verified payout webhook."
    ),
    responses=error_responses(
        (400, "Sale exceeds stock on hand"),
        (404, "Product not found"),
        (422, "Request validation failed"),
    ),
)
def record_sale(
    payload: SaleCreate,
    db: Session = Depends(get_db),
) -> SaleResponse:
    result = inventory_service.record_sale(
        db,
        payload.product_id,
        payload.quantity,
        sale_date=payload.sale_date,
    )
    return SaleResponse.from_result(result)
