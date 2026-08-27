"""Money representation tests.

These exist to justify — and lock in — the decision to store money as integer
paise rather than as a float or a SQLite NUMERIC.
"""

from __future__ import annotations

from decimal import Decimal

from app.core.money import format_inr, paise_to_rupees, rupees_to_paise


def test_round_trip_is_exact() -> None:
    assert rupees_to_paise("48.00") == 4_800
    assert rupees_to_paise(Decimal("0.01")) == 1
    assert rupees_to_paise(48) == 4_800
    assert paise_to_rupees(4_850) == Decimal("48.50")
    assert paise_to_rupees(1) == Decimal("0.01")
    assert paise_to_rupees(0) == Decimal("0.00")


def test_sub_paisa_input_rounds_half_up() -> None:
    assert rupees_to_paise("48.005") == 4_801
    assert rupees_to_paise("48.004") == 4_800


def test_integer_arithmetic_never_drifts() -> None:
    """Order totals are `quantity * price`, so this is the exact hot path."""
    price_paise = rupees_to_paise("48.10")

    assert 999 * price_paise == 4_805_190
    assert paise_to_rupees(999 * price_paise) == Decimal("48051.90")


def test_float_arithmetic_does_drift() -> None:
    """The failure mode being avoided.

    Accumulating a rupee price as a float loses money; the integer paise
    equivalent is exact.
    """
    assert sum([48.10] * 999) != 48_051.90

    # The decisive case: 1.15 rupees is not representable as a float, so a
    # naive rupee->paise cast silently truncates a whole paisa.
    assert int(1.15 * 100) == 114
    assert rupees_to_paise("1.15") == 115


def test_format_inr_is_readable_and_lossless() -> None:
    assert format_inr(4_805_190) == "INR 48,051.90"
    assert format_inr(1) == "INR 0.01"
