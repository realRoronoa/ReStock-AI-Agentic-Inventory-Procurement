"""Spend guardrail tests.

Boundaries are tested from both sides throughout: limits are **inclusive**, so
exactly-at-the-limit passes and one-over fails. Off-by-one here either blocks a
legitimate purchase or lets an over-budget one through.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import (
    DailySpendLimitError,
    OrderSpendLimitError,
    QuantityLimitError,
)
from app.core.limits import (
    DAILY_SPEND_COUNTED_STATUSES,
    check_daily_spend,
    check_order_spend,
    check_quantity,
    committed_spend_paise,
    enforce_all,
    limits_snapshot,
    spend_day_bounds,
)
from app.models.order import Order, OrderStatus

# Defaults under test: 500 units, INR 10,000 per order, INR 25,000 per day.
CONFIG = Settings(
    MAX_REORDER_QUANTITY=500,
    MAX_ORDER_SPEND_INR=10_000,
    MAX_DAILY_SPEND_INR=25_000,
)


def _order(
    db: Session,
    product,
    *,
    amount_paise: int,
    status: OrderStatus,
    approved_at: datetime | None,
    quantity: int = 10,
) -> Order:
    order = Order(
        product_id=product.id,
        supplier_id=product.suppliers[0].id,
        quantity=quantity,
        amount_paise=amount_paise,
        status=status,
        approved_at=approved_at,
    )
    db.add(order)
    db.commit()
    return order


# --- quantity ---------------------------------------------------------------


def test_quantity_below_limit_passes() -> None:
    check_quantity(499, config=CONFIG)


def test_quantity_exactly_at_limit_passes() -> None:
    check_quantity(500, config=CONFIG)


def test_quantity_one_over_limit_fails() -> None:
    with pytest.raises(QuantityLimitError) as exc:
        check_quantity(501, config=CONFIG)

    assert exc.value.details["limit"] == 500


@pytest.mark.parametrize("quantity", [0, -1, -500])
def test_non_positive_quantity_fails(quantity: int) -> None:
    with pytest.raises(QuantityLimitError):
        check_quantity(quantity, config=CONFIG)


# --- per-order spend --------------------------------------------------------


def test_order_spend_below_limit_passes() -> None:
    check_order_spend(999_999, config=CONFIG)  # INR 9,999.99


def test_order_spend_exactly_at_limit_passes() -> None:
    check_order_spend(1_000_000, config=CONFIG)  # INR 10,000.00


def test_order_spend_one_paisa_over_limit_fails() -> None:
    """One paisa over. The cap is enforced at full precision, not rounded."""
    with pytest.raises(OrderSpendLimitError) as exc:
        check_order_spend(1_000_001, config=CONFIG)

    assert exc.value.details["limit_paise"] == 1_000_000


def test_order_spend_message_is_readable() -> None:
    with pytest.raises(OrderSpendLimitError) as exc:
        check_order_spend(1_920_000, config=CONFIG)

    assert "INR 19,200.00" in exc.value.message
    assert "INR 10,000.00" in exc.value.message


def test_zero_amount_fails() -> None:
    with pytest.raises(OrderSpendLimitError):
        check_order_spend(0, config=CONFIG)


# --- daily spend ------------------------------------------------------------


def test_daily_spend_is_zero_with_no_orders(db: Session) -> None:
    assert committed_spend_paise(db, config=CONFIG) == 0


def test_approved_and_paid_orders_count(db: Session, low_stock_product) -> None:
    now = datetime.now(timezone.utc)
    _order(
        db,
        low_stock_product,
        amount_paise=500_000,
        status=OrderStatus.APPROVED,
        approved_at=now,
    )
    _order(
        db,
        low_stock_product,
        amount_paise=300_000,
        status=OrderStatus.PAID,
        approved_at=now,
    )

    assert committed_spend_paise(db, config=CONFIG) == 800_000


def test_proposed_orders_do_not_count(db: Session, low_stock_product) -> None:
    """A proposal is a suggestion. Counting it would let drafts starve the budget."""
    _order(
        db,
        low_stock_product,
        amount_paise=900_000,
        status=OrderStatus.PROPOSED,
        approved_at=None,
    )

    assert committed_spend_paise(db, config=CONFIG) == 0


def test_failed_and_reversed_orders_do_not_count(
    db: Session, low_stock_product
) -> None:
    """The money never left, or came back.

    Charging a failed payout against today's budget would lock a merchant out of
    retrying after a supplier bank rejection they did not cause.
    """
    now = datetime.now(timezone.utc)
    _order(
        db,
        low_stock_product,
        amount_paise=900_000,
        status=OrderStatus.FAILED,
        approved_at=now,
    )
    _order(
        db,
        low_stock_product,
        amount_paise=900_000,
        status=OrderStatus.REVERSED,
        approved_at=now,
    )

    assert committed_spend_paise(db, config=CONFIG) == 0


def test_counted_statuses_are_exactly_approved_and_paid() -> None:
    """Pins the documented policy so a change is deliberate."""
    assert DAILY_SPEND_COUNTED_STATUSES == frozenset(
        {OrderStatus.APPROVED, OrderStatus.PAID}
    )


def test_yesterdays_orders_do_not_count(db: Session, low_stock_product) -> None:
    start, _ = spend_day_bounds(config=CONFIG)
    _order(
        db,
        low_stock_product,
        amount_paise=900_000,
        status=OrderStatus.PAID,
        approved_at=start - timedelta(seconds=1),
    )

    assert committed_spend_paise(db, config=CONFIG) == 0


def test_order_at_the_first_instant_of_today_counts(
    db: Session, low_stock_product
) -> None:
    """The interval is half-open `[start, end)`, so start is included."""
    start, _ = spend_day_bounds(config=CONFIG)
    _order(
        db,
        low_stock_product,
        amount_paise=100_000,
        status=OrderStatus.PAID,
        approved_at=start,
    )

    assert committed_spend_paise(db, config=CONFIG) == 100_000


def test_orders_with_no_approved_at_do_not_count(
    db: Session, low_stock_product
) -> None:
    _order(
        db,
        low_stock_product,
        amount_paise=900_000,
        status=OrderStatus.APPROVED,
        approved_at=None,
    )

    assert committed_spend_paise(db, config=CONFIG) == 0


def test_spend_day_follows_the_merchant_timezone() -> None:
    """A UTC day would roll over at 05:30 IST and split a trading day in two."""
    config = Settings(SPEND_DAY_TIMEZONE="Asia/Kolkata")
    # 01:00 IST on 2 June is 19:30 UTC on 1 June.
    moment = datetime(2025, 6, 1, 19, 30, tzinfo=timezone.utc)

    start, end = spend_day_bounds(moment, config=config)

    # The IST day containing that instant starts 2 June 00:00 IST = 1 June 18:30 UTC.
    assert start == datetime(2025, 6, 1, 18, 30, tzinfo=timezone.utc)
    assert end - start == timedelta(days=1)
    assert start <= moment < end


def test_daily_limit_not_reached_passes(db: Session, low_stock_product) -> None:
    _order(
        db,
        low_stock_product,
        amount_paise=1_000_000,
        status=OrderStatus.PAID,
        approved_at=datetime.now(timezone.utc),
    )

    check_daily_spend(db, 1_000_000, config=CONFIG)  # 20,000 total, cap 25,000


def test_daily_limit_reached_exactly_passes(db: Session, low_stock_product) -> None:
    """2,000,000 already + 500,000 = 2,500,000 = exactly the cap."""
    _order(
        db,
        low_stock_product,
        amount_paise=2_000_000,
        status=OrderStatus.PAID,
        approved_at=datetime.now(timezone.utc),
    )

    check_daily_spend(db, 500_000, config=CONFIG)


def test_daily_limit_exceeded_by_one_paisa_fails(
    db: Session, low_stock_product
) -> None:
    _order(
        db,
        low_stock_product,
        amount_paise=2_000_000,
        status=OrderStatus.PAID,
        approved_at=datetime.now(timezone.utc),
    )

    with pytest.raises(DailySpendLimitError) as exc:
        check_daily_spend(db, 500_001, config=CONFIG)

    assert exc.value.details["already_committed_paise"] == 2_000_000
    assert exc.value.details["projected_paise"] == 2_500_001
    assert exc.value.details["limit_paise"] == 2_500_000


def test_daily_limit_message_shows_the_arithmetic(
    db: Session, low_stock_product
) -> None:
    _order(
        db,
        low_stock_product,
        amount_paise=2_400_000,
        status=OrderStatus.PAID,
        approved_at=datetime.now(timezone.utc),
    )

    with pytest.raises(DailySpendLimitError) as exc:
        check_daily_spend(db, 500_000, config=CONFIG)

    assert "INR 24,000.00 is already committed today" in exc.value.message


def test_excluded_order_is_not_counted_against_itself(
    db: Session, low_stock_product
) -> None:
    """Needed when re-validating an order that is already APPROVED."""
    order = _order(
        db,
        low_stock_product,
        amount_paise=2_400_000,
        status=OrderStatus.APPROVED,
        approved_at=datetime.now(timezone.utc),
    )

    assert committed_spend_paise(db, config=CONFIG) == 2_400_000
    assert (
        committed_spend_paise(db, exclude_order_id=order.id, config=CONFIG) == 0
    )


def test_no_double_counting_of_one_order(db: Session, low_stock_product) -> None:
    order = _order(
        db,
        low_stock_product,
        amount_paise=1_000_000,
        status=OrderStatus.APPROVED,
        approved_at=datetime.now(timezone.utc),
    )
    # Same order transitions APPROVED -> PAID; both statuses count, but it is
    # one row, so the total must not change.
    order.status = OrderStatus.PAID
    db.commit()

    assert committed_spend_paise(db, config=CONFIG) == 1_000_000


# --- combined ---------------------------------------------------------------


def test_enforce_all_reports_quantity_before_spend(db: Session) -> None:
    """An absurd quantity should be named as a quantity problem."""
    with pytest.raises(QuantityLimitError):
        enforce_all(db, quantity=10_000, amount_paise=99_999_999, config=CONFIG)


def test_enforce_all_passes_a_reasonable_order(db: Session) -> None:
    enforce_all(db, quantity=75, amount_paise=360_000, config=CONFIG)


def test_limits_snapshot_is_recorded_in_paise() -> None:
    snapshot = limits_snapshot(CONFIG)

    assert snapshot == {
        "max_reorder_quantity": 500,
        "max_order_spend_paise": 1_000_000,
        "max_daily_spend_paise": 2_500_000,
    }
