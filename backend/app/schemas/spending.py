"""Spend reporting schemas."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from app.core.money import paise_to_rupees
from app.schemas.order import OrderRead


class DaySpendRead(BaseModel):
    """One day of committed spend, for a time series."""

    day: str = Field(description="Merchant-local calendar day, ISO-8601 date.")
    committed_paise: int
    committed: Decimal
    order_count: int
    is_today: bool = Field(
        description="True for the current day, whose figure is usually partial."
    )


class SpendSummaryRead(BaseModel):
    """Committed spend against the configured ceilings."""

    spend_day_start: datetime = Field(
        description="UTC instant the current spend day began."
    )
    spend_day_end: datetime
    timezone: str = Field(
        description=(
            "IANA timezone whose calendar day defines 'today'. A UTC day would "
            "roll over at 05:30 IST and split one trading day in two."
        )
    )

    committed_today_paise: int
    committed_today: Decimal
    daily_limit_paise: int
    daily_limit: Decimal
    remaining_today_paise: int
    remaining_today: Decimal
    utilisation_percent: float = Field(
        description="committed / daily_limit, as a percentage."
    )

    max_order_spend_paise: int
    max_order_spend: Decimal
    max_reorder_quantity: int

    counted_statuses: list[str] = Field(
        description=(
            "Order statuses whose money counts toward the daily cap. FAILED and "
            "REVERSED deliberately do not: the money never left or came back."
        )
    )
    orders_today: list[OrderRead]
    history: list[DaySpendRead]

    @classmethod
    def from_summary(cls, summary) -> "SpendSummaryRead":
        return cls(
            spend_day_start=summary.spend_day_start,
            spend_day_end=summary.spend_day_end,
            timezone=summary.timezone_name,
            committed_today_paise=summary.committed_today_paise,
            committed_today=paise_to_rupees(summary.committed_today_paise),
            daily_limit_paise=summary.daily_limit_paise,
            daily_limit=paise_to_rupees(summary.daily_limit_paise),
            remaining_today_paise=summary.remaining_today_paise,
            remaining_today=paise_to_rupees(summary.remaining_today_paise),
            utilisation_percent=summary.utilisation_percent,
            max_order_spend_paise=summary.max_order_spend_paise,
            max_order_spend=paise_to_rupees(summary.max_order_spend_paise),
            max_reorder_quantity=summary.max_reorder_quantity,
            counted_statuses=summary.counted_statuses,
            orders_today=[OrderRead.from_order(o) for o in summary.orders_today],
            history=[
                DaySpendRead(
                    day=day.day,
                    committed_paise=day.committed_paise,
                    committed=paise_to_rupees(day.committed_paise),
                    order_count=day.order_count,
                    is_today=day.is_today,
                )
                for day in summary.history
            ],
        )
