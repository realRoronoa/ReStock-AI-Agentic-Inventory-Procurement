"""Demo dataset definition.

This module is pure data plus deterministic generators — it performs no
database access. `seed.py` owns persistence.

The dataset is instrumented so that every important branch of the procurement
workflow can be demonstrated against it:

    Product        Stock vs threshold   What it exercises
    -------------  -------------------  -----------------------------------------
    Milk           42 / 60  → LOW       the clean happy path end-to-end
    Coffee Beans   12 / 25  → LOW       MAX_ORDER_SPEND_INR rejection (high unit
                                        price × a realistic forecast exceeds the
                                        per-order cap)
    Rice           320 / 150 → OK       "product is not low stock" rejection
    Cooking Oil    95 / 80  → OK        its cheaper supplier has no RazorpayX
                                        fund account → "supplier cannot be paid"

Every product has at least two suppliers with a real trade-off (cheaper but
slower vs. pricier but faster), so the supplier agent has something to reason
about rather than an obvious single answer.

Fund account IDs below are PLACEHOLDERS. RazorpayX will reject a payout to a
fund account that does not exist in your account. Before running a real Test
Mode payout, create contacts + fund accounts in the RazorpayX Test dashboard
(or via its API) and replace these values.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import date, timedelta

#: Days of history generated per product. Matches the forecast window.
SALES_HISTORY_DAYS = 28

#: Marks placeholder fund accounts so nothing mistakes them for onboarded ones.
PLACEHOLDER_FUND_ACCOUNT_PREFIX = "fa_TEST"


@dataclass(frozen=True)
class SupplierSpec:
    name: str
    price_per_unit_paise: int
    delivery_days: int
    razorpay_fund_account_id: str | None = None


@dataclass(frozen=True)
class ProductSpec:
    name: str
    unit: str
    current_stock: int
    reorder_threshold: int
    suppliers: tuple[SupplierSpec, ...]

    #: Shape of the generated sales history.
    baseline_daily_demand: int
    weekend_multiplier: float = 1.0
    demand_jitter: int = 0
    #: Fixed RNG seed → the same 28 days every run, so tests and demos are
    #: reproducible and re-seeding never changes the numbers.
    sales_seed: int = 0

    trade_off_note: str = field(default="", compare=False)


PRODUCTS: tuple[ProductSpec, ...] = (
    ProductSpec(
        name="Milk",
        unit="litre",
        current_stock=42,
        reorder_threshold=60,
        baseline_daily_demand=20,
        weekend_multiplier=1.35,
        demand_jitter=4,
        sales_seed=1001,
        trade_off_note="Fast supplier costs 9% more per litre.",
        suppliers=(
            SupplierSpec(
                name="Amul Dairy Direct",
                price_per_unit_paise=4_800,  # INR 48.00
                delivery_days=2,
                razorpay_fund_account_id=f"{PLACEHOLDER_FUND_ACCOUNT_PREFIX}MILKAMUL01",
            ),
            SupplierSpec(
                name="Krishna Dairy Co-op",
                price_per_unit_paise=4_400,  # INR 44.00
                delivery_days=6,
                razorpay_fund_account_id=f"{PLACEHOLDER_FUND_ACCOUNT_PREFIX}MILKKRSH01",
            ),
        ),
    ),
    ProductSpec(
        name="Coffee Beans",
        unit="kg",
        current_stock=12,
        reorder_threshold=25,
        baseline_daily_demand=4,
        weekend_multiplier=1.5,
        demand_jitter=2,
        sales_seed=1002,
        trade_off_note=(
            "High unit price: a realistic reorder quantity breaches the "
            "per-order spend cap, which the guardrails must catch."
        ),
        suppliers=(
            SupplierSpec(
                name="Blue Tokai Roasters",
                price_per_unit_paise=72_000,  # INR 720.00
                delivery_days=3,
                razorpay_fund_account_id=f"{PLACEHOLDER_FUND_ACCOUNT_PREFIX}COFFBLUE01",
            ),
            SupplierSpec(
                name="Coorg Estate Traders",
                price_per_unit_paise=64_000,  # INR 640.00
                delivery_days=8,
                razorpay_fund_account_id=f"{PLACEHOLDER_FUND_ACCOUNT_PREFIX}COFFCORG01",
            ),
            SupplierSpec(
                name="Kaapi Direct",
                price_per_unit_paise=69_000,  # INR 690.00
                delivery_days=5,
                razorpay_fund_account_id=f"{PLACEHOLDER_FUND_ACCOUNT_PREFIX}COFFKAAP01",
            ),
        ),
    ),
    ProductSpec(
        name="Rice",
        unit="kg",
        current_stock=320,
        reorder_threshold=150,
        baseline_daily_demand=18,
        weekend_multiplier=1.2,
        demand_jitter=5,
        sales_seed=1003,
        trade_off_note="Well stocked — proposals for it must be refused.",
        suppliers=(
            SupplierSpec(
                name="Sona Masoori Mills",
                price_per_unit_paise=6_200,  # INR 62.00
                delivery_days=4,
                razorpay_fund_account_id=f"{PLACEHOLDER_FUND_ACCOUNT_PREFIX}RICESONA01",
            ),
            SupplierSpec(
                name="Godavari Grains",
                price_per_unit_paise=5_600,  # INR 56.00
                delivery_days=9,
                razorpay_fund_account_id=f"{PLACEHOLDER_FUND_ACCOUNT_PREFIX}RICEGODA01",
            ),
        ),
    ),
    ProductSpec(
        name="Cooking Oil",
        unit="litre",
        current_stock=95,
        reorder_threshold=80,
        baseline_daily_demand=9,
        weekend_multiplier=1.15,
        demand_jitter=3,
        sales_seed=1004,
        trade_off_note=(
            "Cheapest supplier has no fund account, so it cannot be paid even "
            "if an agent prefers it."
        ),
        suppliers=(
            SupplierSpec(
                name="Fortune Bulk Supply",
                price_per_unit_paise=14_200,  # INR 142.00
                delivery_days=3,
                razorpay_fund_account_id=f"{PLACEHOLDER_FUND_ACCOUNT_PREFIX}OILFORT001",
            ),
            SupplierSpec(
                name="Sunrich Oils",
                price_per_unit_paise=13_100,  # INR 131.00
                delivery_days=7,
                razorpay_fund_account_id=None,  # intentionally not onboarded
            ),
        ),
    ),
)


def generate_sales_history(
    spec: ProductSpec,
    *,
    today: date,
    days: int = SALES_HISTORY_DAYS,
) -> list[tuple[date, int]]:
    """Build `days` of daily sales ending yesterday, oldest first.

    Deterministic for a given `spec` and `days`: the RNG is seeded from
    `spec.sales_seed`, so re-running the seed script produces identical history.
    Today is excluded because the current day is still in progress.
    """
    rng = random.Random(spec.sales_seed)
    rows: list[tuple[date, int]] = []

    for offset in range(days, 0, -1):
        day = today - timedelta(days=offset)
        demand = float(spec.baseline_daily_demand)
        if day.weekday() >= 5:  # Saturday / Sunday
            demand *= spec.weekend_multiplier
        if spec.demand_jitter:
            demand += rng.randint(-spec.demand_jitter, spec.demand_jitter)
        rows.append((day, max(0, round(demand))))

    return rows
