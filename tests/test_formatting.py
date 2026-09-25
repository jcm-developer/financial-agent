"""The backend's screen numbers have to read like the frontend's (`format.ts`)."""

from __future__ import annotations

from src.formatting import compact, money, number, percent, signed_money


def test_decimal_comma_and_grouping_from_five_digits():
    """`es-ES` does not group a four-digit number: `Intl` writes 1234,50."""
    assert number(1234.5) == "1234,50"
    assert number(12345.5) == "12.345,50"
    assert number(1234567.891) == "1.234.567,89"


def test_no_negative_zero():
    assert number(-0.001) == "0,00"
    assert signed_money(-0.001, "€") == "0,00 €"


def test_the_minus_is_a_real_minus_sign():
    assert number(-3) == "−3,00"
    assert signed_money(-3, "€") == "−3,00 €"


def test_money_puts_the_symbol_after():
    assert money(3949.2, "€") == "3949,20 €"
    assert money(10_000, "€") == "10.000,00 €"
    assert money(5, "") == "5,00"


def test_compact_drops_trailing_zeros():
    assert compact(7.94) == "7,94"
    assert compact(1.50) == "1,5"
    assert compact(50.0) == "50"


def test_percent_is_glued_and_optionally_signed():
    assert percent(5) == "5%"
    assert percent(12.5) == "12,5%"
    assert percent(1.25, signed=True) == "+1,25%"
    assert percent(-0.4, signed=True) == "−0,40%"
    assert percent(0, signed=True) == "0,00%"
