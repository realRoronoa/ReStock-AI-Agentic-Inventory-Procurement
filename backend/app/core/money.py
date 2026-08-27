"""Money representation.

Every monetary value is stored in the database as an **integer number of paise**
(1 INR = 100 paise). Rationale:

1. Integer arithmetic cannot drift. `quantity * price_per_unit` is exact.
2. SQLite has no real DECIMAL type, so a `NUMERIC` column round-trips through
   a C double there — silent precision loss on the one value that must be exact.
3. The RazorpayX Payouts API expects `amount` as an integer in the smallest
   currency unit. Storing paise means the payment call needs zero conversion,
   at exactly the point where a rounding bug would move real money.

Conversion to rupees happens only at the presentation boundary (Pydantic
response schemas) and in guardrail comparisons, both of which live here.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

PAISE_PER_RUPEE = 100


def rupees_to_paise(rupees: Decimal | int | str) -> int:
    """Convert a rupee amount to paise, rounding half-up to the nearest paisa."""
    value = Decimal(str(rupees)) * PAISE_PER_RUPEE
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def paise_to_rupees(paise: int) -> Decimal:
    """Convert paise to an exact two-decimal rupee `Decimal`."""
    return (Decimal(paise) / PAISE_PER_RUPEE).quantize(Decimal("0.01"))


def format_inr(paise: int) -> str:
    """Human-readable amount for logs and audit reasoning text."""
    return f"INR {paise_to_rupees(paise):,.2f}"
