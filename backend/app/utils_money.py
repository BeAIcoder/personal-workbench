# -*- coding: utf-8 -*-
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

_CENT = Decimal("0.01")


def money(value) -> float:
    return float(Decimal(str(value or 0)).quantize(_CENT, rounding=ROUND_HALF_UP))
