"""Proposal endpoints.

A proposal is an order in `PROPOSED` state. Creating one costs two LLM calls and
commits no money.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.agents.forecast_agent import ForecastProvider
from app.agents.supplier_agent import SupplierProvider
from app.core.database import get_db
from app.schemas.error import error_responses
from app.schemas.order import OrderRead
from app.schemas.proposal import ProposalRead
from app.services import (
    forecast_service,
    order_service,
    proposal_service,
    supplier_service,
)

router = APIRouter(prefix="/api/proposals", tags=["proposals"])


@router.post(
    "/product/{product_id}",
    response_model=ProposalRead,
    status_code=status.HTTP_201_CREATED,
    summary="Generate a reorder proposal for a low-stock product",
    description=(
        "Runs the full recommendation pipeline and creates an order in "
        "`proposed` state.\n\n"
        "Pipeline: verify the product is genuinely low stock, load 28 days of "
        "sales, forecast agent (LLM), validate the quantity, load payable "
        "suppliers, supplier agent (LLM), re-read and validate the supplier from "
        "the database, compute `quantity x price` from the database price, apply "
        "the quantity / per-order / daily spend guardrails, persist.\n\n"
        "**No money moves.** The response includes the reasoning from both "
        "agents so a merchant can judge the recommendation before approving it. "
        "If any step fails, no order is created and the failure is audited.\n\n"
        "Requires a configured LLM provider. Without one this returns 503 rather "
        "than inventing a recommendation."
    ),
    responses=error_responses(
        (400, "Product is not low stock, or has no suppliers / no sales history"),
        (404, "Product not found"),
        (422, "Model output rejected, or a spend guardrail was breached"),
        (502, "The model provider could not be reached"),
        (503, "No LLM provider is configured on this server"),
    ),
)
def create_proposal(
    product_id: int,
    db: Session = Depends(get_db),
    forecast_provider: ForecastProvider = Depends(
        forecast_service.get_forecast_provider
    ),
    supplier_provider: SupplierProvider = Depends(
        supplier_service.get_supplier_provider
    ),
) -> ProposalRead:
    proposal = proposal_service.create_proposal(
        db,
        product_id,
        forecast_provider=forecast_provider,
        supplier_provider=supplier_provider,
    )
    return ProposalRead.from_proposal(proposal)


@router.get(
    "",
    response_model=list[OrderRead],
    summary="List proposals awaiting a human decision",
    description="Orders in `proposed` state, newest first.",
)
def list_proposals(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> list[OrderRead]:
    orders = proposal_service.list_proposals(db, limit=limit, offset=offset)
    return [OrderRead.from_order(order) for order in orders]


@router.get(
    "/{order_id}",
    response_model=OrderRead,
    summary="Get one proposal",
    description=(
        "Summary view of a single order. Use `GET /api/orders/{order_id}` for the "
        "full detail view including the stored AI reasoning."
    ),
    responses=error_responses((404, "Order not found")),
)
def get_proposal(order_id: int, db: Session = Depends(get_db)) -> OrderRead:
    order = order_service.get_order(db, order_id)
    return OrderRead.from_order(order)
