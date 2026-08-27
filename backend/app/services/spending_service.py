"""Spend reporting.

Read-only. Reports what the guardrails in `app/core/limits.py` are already
enforcing, rather than reimplementing the arithmetic — the same
`committed_spend_paise` and `spend_day_bounds` functions back both the enforcement
and this view, so a dashboard can never disagree with what the approval gate will
actually allow.

Nothing here mutates anything, and nothing here can raise a limit.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.core.config import Settings, settings as default_settings
from app.core.limits import (
    DAILY_SPEND_COUNTED_STATUSES,
    committed_spend_paise,
    spend_day_bounds,
)
from app.core.money import PAISE_PER_RUPEE
from app.models.order import Order

logger = logging.getLogger("restock.spending")

#: Cap on the history window, so a client cannot ask for an unbounded scan.
MAX_HISTORY_DAYS = 90


@dataclass(frozen=True)
class DaySpend:
    """One day of committed spend."""

    day: str
    committed_paise: int
    order_count: int
    #: True for the day the caller is currently in, which is usually partial.
    is_today: bool


@dataclass
class SpendSummary:
    """Today's spend against the configured ceilings, plus recent history."""

    spend_day_start: datetime
    spend_day_end: datetime
    timezone_name: str

    committed_today_paise: int
    daily_limit_paise: int
    remaining_today_paise: int
    utilisation_percent: float

    max_order_spend_paise: int
    max_reorder_quantity: int

    counted_statuses: list[str]
    orders_today: list[Order]
    history: list[DaySpend]


def _day_key(moment: datetime, config: Settings) -> str:
    """The merchant-local calendar day an instant falls in."""
    from zoneinfo import ZoneInfo

    return moment.astimezone(ZoneInfo(config.SPEND_DAY_TIMEZONE)).date().isoformat()


def get_spend_summary(
    db: Session,
    *,
    history_days: int = 14,
    moment: datetime | None = None,
    config: Settings | None = None,
) -> SpendSummary:
    """Today's committed spend, the active limits, and a daily series.

    The history is grouped in Python rather than SQL because the day boundary is
    timezone-dependent and `date_trunc`-style grouping differs between SQLite and
    PostgreSQL. The query is bounded by `history_days`, so the row count stays
    small and the grouping cost is irrelevant.
    """
    config = config or default_settings
    history_days = max(1, min(history_days, MAX_HISTORY_DAYS))
    moment = moment or datetime.now(timezone.utc)

    start, end = spend_day_bounds(moment, config=config)
    daily_limit_paise = config.MAX_DAILY_SPEND_INR * PAISE_PER_RUPEE
    committed = committed_spend_paise(db, moment=moment, config=config)

    orders_today = list(
        db.scalars(
            select(Order)
            .options(joinedload(Order.product), joinedload(Order.supplier))
            .where(
                Order.status.in_(DAILY_SPEND_COUNTED_STATUSES),
                Order.approved_at.is_not(None),
                Order.approved_at >= start,
                Order.approved_at < end,
            )
            .order_by(Order.approved_at.desc())
        )
        .unique()
        .all()
    )

    # --- daily series -----------------------------------------------------
    window_start = start - timedelta(days=history_days - 1)
    historical = db.scalars(
        select(Order)
        .where(
            Order.status.in_(DAILY_SPEND_COUNTED_STATUSES),
            Order.approved_at.is_not(None),
            Order.approved_at >= window_start,
            Order.approved_at < end,
        )
        .order_by(Order.approved_at)
    ).all()

    totals: dict[str, list[int]] = {}
    for order in historical:
        key = _day_key(order.approved_at, config)
        bucket = totals.setdefault(key, [0, 0])
        bucket[0] += order.amount_paise
        bucket[1] += 1

    today_key = _day_key(moment, config)
    # Every day in the window appears, including zero days, so a chart does not
    # silently close gaps and imply spending that never happened.
    history: list[DaySpend] = []
    for offset in range(history_days - 1, -1, -1):
        day_start = start - timedelta(days=offset)
        key = _day_key(day_start, config)
        amount, count = totals.get(key, [0, 0])
        history.append(
            DaySpend(
                day=key,
                committed_paise=amount,
                order_count=count,
                is_today=key == today_key,
            )
        )

    utilisation = (
        round(committed / daily_limit_paise * 100, 2) if daily_limit_paise else 0.0
    )

    logger.info(
        "spend_summary committed_paise=%d limit_paise=%d orders_today=%d",
        committed,
        daily_limit_paise,
        len(orders_today),
    )

    return SpendSummary(
        spend_day_start=start,
        spend_day_end=end,
        timezone_name=config.SPEND_DAY_TIMEZONE,
        committed_today_paise=committed,
        daily_limit_paise=daily_limit_paise,
        # Clamped at zero: a limit reduced after orders were approved could
        # otherwise report a negative remaining budget.
        remaining_today_paise=max(0, daily_limit_paise - committed),
        utilisation_percent=utilisation,
        max_order_spend_paise=config.MAX_ORDER_SPEND_INR * PAISE_PER_RUPEE,
        max_reorder_quantity=config.MAX_REORDER_QUANTITY,
        counted_statuses=sorted(
            status.value for status in DAILY_SPEND_COUNTED_STATUSES
        ),
        orders_today=orders_today,
        history=history,
    )
