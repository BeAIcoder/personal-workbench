# -*- coding: utf-8 -*-
"""finance_deferred 纯计算引擎：租入资产改良支出的直线摊销。

规则矩阵（唯一实现，冻结在本文件 + ``RULE_VERSION``）
----------------------------------------------------
- ``净额 = ROUND_HALF_UP(含税金额 ÷ (1 + 税率), 2)``；``税额 = 含税金额 − 净额``。
- ``月摊 = ROUND_HALF_UP(净额 ÷ 摊销月数, 2)``；**每月金额相同**。
- ``累计摊销_i = 月摊 × i``；``余额_i = 净额 − 累计摊销_i``；``剩余月数_i = 月数 − i``。
- 末期尾差 ``净额 − 月摊 × 月数`` **如实留在末期余额**：不吸收进末期、不自动转销、
  不做最后一次倒挤。
- 摊销到期月 = 起摊月 + 月数 − 1；与租赁到期日比较时取该月月末：
  月末晚于租赁到期日只产出 ``warning``，**不自动缩短摊销年限**。
- 月数、税率、期间非法一律抛 :class:`DeferredRuleError`，不静默回退。

本文件只做纯函数计算，不读数据库、不写文件。
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

from .checks import AmortizationFacts, evaluate
from .schemas import AmortizationSummary, CalculationInput, PeriodRow, RunResult

RULE_VERSION = "deferred-mvp-1"

_CENT = Decimal("0.01")
_ONE_DAY = timedelta(days=1)
MAX_MONTHS = 600


class DeferredRuleError(ValueError):
    """计算前置校验失败：``code`` 供调用方判定，``message`` 为中文原因。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def round_money(value) -> Decimal:
    return Decimal(value).quantize(_CENT, rounding=ROUND_HALF_UP)


def _split_month(month: str) -> tuple[int, int]:
    if not isinstance(month, str) or len(month) != 7 or month[4] != "-":
        raise DeferredRuleError("invalid_month", f"期间必须为 YYYY-MM：{month!r}")
    try:
        year, mon = int(month[:4]), int(month[5:])
    except ValueError as exc:
        raise DeferredRuleError("invalid_month", f"期间必须为 YYYY-MM：{month!r}") from exc
    if year < 1900 or not 1 <= mon <= 12:
        raise DeferredRuleError("invalid_month", f"期间必须为 YYYY-MM：{month!r}")
    return year, mon


def month_end(month: str) -> date:
    """期间（YYYY-MM）的月末日期。"""
    year, mon = _split_month(month)
    if mon == 12:
        return date(year, 12, 31)
    return date(year, mon + 1, 1) - _ONE_DAY


def month_grid(start_month: str, months: int) -> list[str]:
    """从起摊月开始的连续月份标签（跨年递增）。"""
    year, mon = _split_month(start_month)
    labels: list[str] = []
    for _ in range(months):
        labels.append(f"{year:04d}-{mon:02d}")
        mon += 1
        if mon == 13:
            year, mon = year + 1, 1
    return labels


def _validate(payload: CalculationInput) -> tuple[Decimal, Decimal]:
    if not isinstance(payload.months, int) or payload.months < 1:
        raise DeferredRuleError("invalid_months", "摊销月数必须为正整数")
    if payload.months > MAX_MONTHS:
        raise DeferredRuleError("invalid_months", f"摊销月数不能超过 {MAX_MONTHS} 个月")
    rate = Decimal(payload.tax_rate)
    if rate < 0 or rate > 1:
        raise DeferredRuleError("invalid_rate", "税率必须为 0~1 之间的小数（例如 6% 填 0.06）")
    amount = Decimal(payload.amount_incl_tax)
    if amount < 0:
        raise DeferredRuleError("invalid_amount", "含税金额不能为负")
    return amount, rate


def compute(payload: CalculationInput) -> RunResult:
    """按冻结规则矩阵计算；失败抛 :class:`DeferredRuleError`，不留半套结果。"""
    amount_incl_tax, rate = _validate(payload)
    amount_net = round_money(amount_incl_tax / (Decimal(1) + rate))
    tax_amount = round_money(amount_incl_tax - amount_net)
    monthly_amount = round_money(amount_net / Decimal(payload.months))
    labels = month_grid(payload.start_month, payload.months)

    periods: list[PeriodRow] = []
    cumulative = Decimal("0.00")
    for index, label in enumerate(labels, start=1):
        cumulative = cumulative + monthly_amount
        periods.append(PeriodRow(
            period_month=label,
            sequence=index,
            monthly_amount=monthly_amount,
            cumulative=cumulative,
            balance=amount_net - cumulative,
            remaining_months=payload.months - index,
        ))

    amortized_total = monthly_amount * Decimal(payload.months)
    rounding_difference = amount_net - amortized_total
    end_month = labels[-1]
    expiry_after_lease = month_end(end_month) > payload.lease_end_date

    risk_flags: list[str] = []
    if expiry_after_lease:
        risk_flags.append(
            f"摊销到期月 {end_month}（月末 {month_end(end_month).isoformat()}）"
            f"晚于租赁到期日 {payload.lease_end_date.isoformat()}：按规则不自动缩短年限"
        )
    if rounding_difference != Decimal("0.00"):
        risk_flags.append(
            f"末期尾差 {rounding_difference} 元如实留在末期余额，未吸差、未自动转销"
        )

    facts = AmortizationFacts(
        amount_incl_tax=amount_incl_tax,
        tax_rate=rate,
        amount_net=amount_net,
        tax_amount=tax_amount,
        months=payload.months,
        monthly_amount=monthly_amount,
        period_count=len(periods),
        amortized_total=amortized_total,
        rounding_difference=rounding_difference,
        closing_balance=periods[-1].balance if periods else None,
        first_month=labels[0] if labels else None,
        last_month=labels[-1] if labels else None,
        month_sequence_ok=labels == month_grid(payload.start_month, len(labels)),
        remaining_closes=bool(periods) and periods[-1].remaining_months == 0,
        lease_end_date=payload.lease_end_date,
        end_month=end_month,
        expiry_after_lease=expiry_after_lease,
    )

    summary = AmortizationSummary(
        amount_incl_tax=amount_incl_tax,
        tax_rate=rate,
        tax_amount=tax_amount,
        amount_net=amount_net,
        monthly_amount=monthly_amount,
        months=payload.months,
        start_month=labels[0],
        end_month=end_month,
        amortized_total=amortized_total,
        rounding_difference=rounding_difference,
        lease_end_date=payload.lease_end_date,
        expiry_after_lease=expiry_after_lease,
        risk_flags=risk_flags,
        period_count=len(periods),
    )
    return RunResult(
        rule_version=RULE_VERSION,
        periods=periods,
        summary=summary,
        checks=evaluate(facts),
    )
