"""Spend reporting endpoint.

Read-only view of what the guardrails are already enforcing. Backed by the same
functions as the approval gate, so this can never disagree with what an approval
will actually allow.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.schemas.spending import SpendSummaryRead
from app.services import spending_service

router = APIRouter(prefix="/api/spending", tags=["spending"])


@router.get(
    "",
    response_model=SpendSummaryRead,
    summary="Committed spend against the configured limits",
    description=(
        "Today's committed spend, the active ceilings, the orders that make up "
        "today's figure, and a daily series for charting.\n\n"
        "**Which orders count:** `approved` and `paid`. Approval is the point "
        "the business commits, so an approved-but-unsettled order has money in "
        "motion. `proposed` does not count (a suggestion; counting it would let "
        "drafts starve the budget), and neither do `failed`, `reversed` or "
        "`rejected` — in those cases the money never left, came back, or was "
        "never requested.\n\n"
        "Spend is attributed by `approved_at`, and the day boundary follows the "
        "merchant timezone rather than UTC.\n\n"
        "Every day in the history window is present, including zero-spend days, "
        "so a chart does not close gaps and imply spending that never happened."
    ),
)
def get_spending(
    history_days: int = Query(
        default=14,
        ge=1,
        le=spending_service.MAX_HISTORY_DAYS,
        description="Length of the daily series to return.",
    ),
    db: Session = Depends(get_db),
) -> SpendSummaryRead:
    summary = spending_service.get_spend_summary(db, history_days=history_days)
    return SpendSummaryRead.from_summary(summary)
