# -*- coding: utf-8 -*-
from __future__ import annotations

from decimal import Decimal

from .utils_money import money

MONTHLY_DEDUCTION = 5000.0

BRACKETS = [
    (36_000, 0.03, 0.0),
    (144_000, 0.10, 2_520.0),
    (300_000, 0.20, 16_920.0),
    (420_000, 0.25, 31_920.0),
    (660_000, 0.30, 52_920.0),
    (960_000, 0.35, 85_920.0),
    (float("inf"), 0.45, 181_920.0),
]


def _decimal(value) -> Decimal:
    return Decimal(str(value or 0))


def calc_cumulative_withholding(cumulative_base, prior_withheld, months_count) -> dict:
    cumulative_taxable = (
        _decimal(cumulative_base) - _decimal(MONTHLY_DEDUCTION) * int(months_count)
    )
    bracket_taxable = max(Decimal("0"), cumulative_taxable)
    for cap, rate, quick_deduction in BRACKETS:
        if bracket_taxable <= _decimal(cap):
            break

    cumulative_withholding = max(
        Decimal("0"),
        cumulative_taxable * _decimal(rate) - _decimal(quick_deduction),
    )
    monthly_withholding = money(cumulative_withholding - _decimal(prior_withheld))
    if monthly_withholding <= 0:
        monthly_withholding = 0.0
    return {
        "rate": rate,
        "quick_deduction": quick_deduction,
        "cumulative_taxable": money(cumulative_taxable),
        "cumulative_withholding": money(cumulative_withholding),
        "monthly_withholding": monthly_withholding,
    }
