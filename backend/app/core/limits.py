"""Spend guardrails.

These are the hard ceilings on what the system can spend. Three properties are
deliberate:

1. **Deterministic.** Pure functions over configuration and database state. No
   LLM involvement, and nothing here can be influenced by model output or by a
   client payload.
2. **Not reachable from the agents.** Nothing in `app/agents/` imports this
   module, and the limits never appear in a prompt — a model that knew the cap
   could tailor a recommendation to sit just underneath it.
3. **Checked twice.** Once when a proposal is created, and again at approval
   time immediately before money moves, because a proposal can go stale.

All arithmetic is in integer paise (see `app/core/money.py`).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings, settings as default_settings
from app.core.errors import (
    DailySpendLimitError,
    OrderSpendLimitError,
    QuantityLimitError,
)
from app.core.money import PAISE_PER_RUPEE, format_inr
from app.models.order import Order, OrderStatus

#: Order states whose money counts against the daily cap.
#:
#: APPROVED and PAID both count: approval is the point at which the business
#: commits to the spend and a payout is sent, so an approved-but-unsettled order
#: has already put money in motion.
#:
#: FAILED and REVERSED deliberately do NOT count. In both cases the money either
#: never left or came back, so continuing to charge them against today's budget
#: would lock a merchant out of retrying after a supplier's bank rejected a
#: transfer — a failure they did not cause.
#:
#: PROPOSED does not count either: a proposal is a suggestion, not a commitment,
#: and counting it would let unapproved proposals starve the real budget.
DAILY_SPEND_COUNTED_STATUSES: frozenset[OrderStatus] = frozenset(
    {OrderStatus.APPROVED, OrderStatus.PAID}
)


def spend_day_bounds(
    moment: datetime | None = None,
    *,
    config: Settings | None = None,
) -> tuple[datetime, datetime]:
    """Return the UTC half-open interval `[start, end)` of the current spend day.

    The boundary follows the merchant's local calendar day, not UTC. For an
    Indian merchant a UTC day would roll over at 05:30 local time, which would
    split a single trading day's spending across two budgets and make the cap
    behave unpredictably in the morning.
    """
    config = config or default_settings
    tz = ZoneInfo(config.SPEND_DAY_TIMEZONE)
    moment = moment or datetime.now(timezone.utc)

    local_day: date = moment.astimezone(tz).date()
    start_local = datetime.combine(local_day, time.min, tzinfo=tz)
    end_local = start_local + timedelta(days=1)

    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


def committed_spend_paise(
    db: Session,
    *,
    moment: datetime | None = None,
    exclude_order_id: int | None = None,
    config: Settings | None = None,
) -> int:
    """Total already committed during the current spend day, in paise.

    Attributed by `approved_at` rather than `created_at`: the spend happens when
    a human authorises it, not when the agent drafted the proposal.

    `exclude_order_id` keeps an order from being counted against itself when it
    is re-validated after having already been marked approved.
    """
    start, end = spend_day_bounds(moment, config=config)

    stmt = select(func.coalesce(func.sum(Order.amount_paise), 0)).where(
        Order.status.in_(DAILY_SPEND_COUNTED_STATUSES),
        Order.approved_at.is_not(None),
        Order.approved_at >= start,
        Order.approved_at < end,
    )
    if exclude_order_id is not None:
        stmt = stmt.where(Order.id != exclude_order_id)

    return int(db.scalar(stmt) or 0)


def check_quantity(
    quantity: int,
    *,
    config: Settings | None = None,
    limits: "LimitValues | None" = None,
) -> None:
    """Reject a non-positive or oversized reorder quantity."""
    effective = limits or limits_from_config(config)

    if quantity <= 0:
        raise QuantityLimitError(
            "Reorder quantity must be greater than zero.",
            details={"quantity": quantity},
        )
    if quantity > effective.max_reorder_quantity:
        raise QuantityLimitError(
            f"Reorder quantity {quantity} exceeds the maximum of "
            f"{effective.max_reorder_quantity} units per order.",
            details={
                "quantity": quantity,
                "limit": effective.max_reorder_quantity,
            },
        )


def check_order_spend(
    amount_paise: int,
    *,
    config: Settings | None = None,
    limits: "LimitValues | None" = None,
) -> None:
    """Reject an order total above the per-order ceiling."""
    effective = limits or limits_from_config(config)
    limit_paise = effective.max_order_spend_paise

    if amount_paise <= 0:
        raise OrderSpendLimitError(
            "Order total must be greater than zero.",
            details={"amount_paise": amount_paise},
        )
    if amount_paise > limit_paise:
        raise OrderSpendLimitError(
            f"Order total {format_inr(amount_paise)} exceeds the per-order limit "
            f"of {format_inr(limit_paise)}.",
            details={
                "amount_paise": amount_paise,
                "limit_paise": limit_paise,
            },
        )


def check_daily_spend(
    db: Session,
    amount_paise: int,
    *,
    moment: datetime | None = None,
    exclude_order_id: int | None = None,
    config: Settings | None = None,
    limits: "LimitValues | None" = None,
) -> None:
    """Reject an order that would push the day's committed spend over the cap."""
    effective = limits or resolve_limits(db, config)
    limit_paise = effective.max_daily_spend_paise

    already = committed_spend_paise(
        db, moment=moment, exclude_order_id=exclude_order_id, config=config
    )
    projected = already + amount_paise

    if projected > limit_paise:
        raise DailySpendLimitError(
            f"This order ({format_inr(amount_paise)}) would take today's "
            f"committed spend to {format_inr(projected)}, over the daily limit "
            f"of {format_inr(limit_paise)}. "
            f"{format_inr(already)} is already committed today.",
            details={
                "amount_paise": amount_paise,
                "already_committed_paise": already,
                "projected_paise": projected,
                "limit_paise": limit_paise,
            },
        )


def enforce_all(
    db: Session,
    *,
    quantity: int,
    amount_paise: int,
    moment: datetime | None = None,
    exclude_order_id: int | None = None,
    config: Settings | None = None,
    limits: "LimitValues | None" = None,
) -> None:
    """Run every guardrail. Raises the first violation found.

    Ordered cheapest-and-most-specific first so the error a merchant sees names
    the most actionable problem: an absurd quantity is reported as a quantity
    problem rather than as a spend problem.
    """
    effective = limits or resolve_limits(db, config)

    check_quantity(quantity, limits=effective)
    check_order_spend(amount_paise, limits=effective)
    check_daily_spend(
        db,
        amount_paise,
        moment=moment,
        exclude_order_id=exclude_order_id,
        limits=effective,
    )


def limits_snapshot(
    config: Settings | None = None,
    *,
    db: Session | None = None,
) -> dict[str, int]:
    """The active limits, for audit metadata and API responses.

    Recorded alongside decisions so a later reader knows which ceilings were in
    force at the time, even if they have since been changed.
    """
    effective = resolve_limits(db, config)
    return {
        "max_reorder_quantity": effective.max_reorder_quantity,
        "max_order_spend_paise": effective.max_order_spend_paise,
        "max_daily_spend_paise": effective.max_daily_spend_paise,
    }


# --- effective limits -------------------------------------------------------


@dataclass(frozen=True)
class LimitValues:
    """The limits actually in force, after DB overrides are applied."""

    max_reorder_quantity: int
    max_order_spend_paise: int
    max_daily_spend_paise: int
    #: True when at least one value came from the database rather than the
    #: environment. Surfaced so the UI can say so.
    from_database: bool = False


def limits_from_config(config: Settings | None = None) -> LimitValues:
    """The environment-configured defaults."""
    config = config or default_settings
    return LimitValues(
        max_reorder_quantity=config.MAX_REORDER_QUANTITY,
        max_order_spend_paise=config.MAX_ORDER_SPEND_INR * PAISE_PER_RUPEE,
        max_daily_spend_paise=config.MAX_DAILY_SPEND_INR * PAISE_PER_RUPEE,
    )


def resolve_limits(
    db: Session | None,
    config: Settings | None = None,
) -> LimitValues:
    """Limits in force: the `merchant_settings` row where set, else the env.

    A NULL column means "not overridden", so a deployment behaves exactly as it
    did before anyone touched Settings. Passing `db=None` skips the lookup
    entirely, which is what the pure-function guardrail checks do.
    """
    base = limits_from_config(config)
    if db is None:
        return base

    # Imported here rather than at module scope: `app.models` imports
    # `app.core.database`, and importing models at the top of a core module
    # would create a cycle.
    from app.models.merchant_settings import SINGLETON_ID, MerchantSettings

    row = db.get(MerchantSettings, SINGLETON_ID)
    if row is None:
        return base

    overridden = any(
        value is not None
        for value in (
            row.max_reorder_quantity,
            row.max_order_spend_paise,
            row.max_daily_spend_paise,
        )
    )
    return LimitValues(
        max_reorder_quantity=row.max_reorder_quantity or base.max_reorder_quantity,
        max_order_spend_paise=(
            row.max_order_spend_paise or base.max_order_spend_paise
        ),
        max_daily_spend_paise=(
            row.max_daily_spend_paise or base.max_daily_spend_paise
        ),
        from_database=overridden,
    )
