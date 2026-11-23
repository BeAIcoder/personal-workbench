# -*- coding: utf-8 -*-
"""finance_lease 纯计算引擎：按冻结规则矩阵产出评估行、期间行、汇总与检查。

规则矩阵与合成黄金用例见 ``backend/tests/test_finance_lease_calculation.py`` 顶部；
本文件只做纯函数计算，不读数据库、不写文件。

关键约定
--------
- ``months = DATEDIF(起-1天, 止+1天, "M")``，逐期自算，不采用入参值。
- ``period_rate = ROUND_HALF_UP(年利率 × months / 12, 4)``，逐行按自有月数。
- ``discount_factor_1 = 1``；``d_i = ROUND_HALF_UP(d_{i-1} × (1 + Q_{i-1}), 9)``。
- ``present_value_1 = amount_1``（首行不折现）；其余 ``ROUND(amount_i / d_i, 2)``。
- ``liability_balance_1 = H2 - amount_1``；``B_i = B_{i-1} + U_{i-1} - amount_i``；
  ``U_i = ROUND(B_i × Q_i, 2)``（付款后余额计息）。
- 月折旧/月利息取 2 位，**不吸收尾差**；恒等式超出容差即判 ``failed``，
  由 ``checks.py`` 报出，不得倒挤末期。

两个同名字段的区别（禁止混用）
------------------------------
- ``AssessmentRow.unamortized_finance`` = ``amount - present_value``，即折现差额
  （源表 S 列“利息（非实际当期利息）”），只作逐期留痕。
- ``PeriodRow.unamortized_finance`` = 月末未确认融资费用余额 = ``M2 - 累计已确认利息``。
  两者含义不同，导出与对外文案必须分开表述。
- 期间表的 ``lease_payment`` / ``liability_balance`` 均为**计划口径**，
  不对账 BIP 账面或外部实付。
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

from .checks import TOL_TOTAL, TOL_UNIT, ScheduleFacts, evaluate
from .schemas import (
    AssessmentRow,
    CalculationInput,
    CalculationResult,
    CalculationSummary,
    PeriodRow,
)

RULE_VERSION = "lease-mvp-1"

_CENT = Decimal("0.01")
_RATE4 = Decimal("0.0001")
_FACTOR9 = Decimal("0.000000001")
_ONE_DAY = timedelta(days=1)


class LeaseRuleError(ValueError):
    """计算前置校验失败：``code`` 供调用方判定，``message`` 为中文原因。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def round_money(value) -> Decimal:
    return Decimal(value).quantize(_CENT, rounding=ROUND_HALF_UP)


def round_factor9(value) -> Decimal:
    return Decimal(value).quantize(_FACTOR9, rounding=ROUND_HALF_UP)


def period_rate(annual_rate, months) -> Decimal:
    return (Decimal(annual_rate) * Decimal(months) / Decimal(12)).quantize(
        _RATE4, rounding=ROUND_HALF_UP)


def month_count(begin: date, end: date) -> int:
    """Excel ``DATEDIF(begin-1, end+1, "M")`` 的等价实现。"""
    start = begin - _ONE_DAY
    stop = end + _ONE_DAY
    months = (stop.year - start.year) * 12 + (stop.month - start.month)
    if stop.day < start.day:
        months -= 1
    return months


def _month_label(year: int, month: int) -> str:
    return f"{year:04d}-{month:02d}"


def _validate(payments) -> list[int]:
    if not payments:
        raise LeaseRuleError("empty_payments", "付款计划为空，无法测算")
    counts: list[int] = []
    for row in payments:
        if row.period_end < row.period_start:
            raise LeaseRuleError(
                "invalid_period", f"第 {row.sequence} 期计租结束日早于开始日")
        counts.append(month_count(row.period_start, row.period_end))
    if not any(counts):
        raise LeaseRuleError("no_month_rows", "全部付款期月数为 0，无法生成期间表")
    for row, count in zip(payments, counts):
        if count == 0 and Decimal(row.amount) == 0:
            raise LeaseRuleError(
                "zero_month_zero_amount", f"第 {row.sequence} 期既无月数又无金额")
    for previous, current in zip(payments, payments[1:]):
        if current.period_start != previous.period_end + _ONE_DAY:
            raise LeaseRuleError(
                "discontinuous_period",
                f"第 {current.sequence} 期计租起始日与上一期结束日不连续")
    return counts


def _build_assessment(payments, counts, rate: Decimal) -> list[AssessmentRow]:
    rows: list[AssessmentRow] = []
    factor = Decimal(1)
    amounts: list[Decimal] = []
    for index, (row, count) in enumerate(zip(payments, counts)):
        q = period_rate(rate, count)
        amount = Decimal(row.amount)
        pv = amount if index == 0 else round_money(amount / factor)
        amounts.append(amount)
        rows.append(AssessmentRow(
            sequence=row.sequence,
            period_start=row.period_start,
            period_end=row.period_end,
            months=count,
            amount=amount,
            period_rate=q,
            discount_factor=factor,
            present_value=pv,
            unamortized_finance=amount - pv,
            interest_u=Decimal("0.00"),
            liability_balance=Decimal("0.00"),
        ))
        factor = round_factor9((Decimal(1) + q) * factor)

    asset_cost = sum((row.present_value for row in rows), Decimal("0"))
    balance = asset_cost - amounts[0]
    rows[0] = rows[0].model_copy(update={
        "liability_balance": balance,
        "interest_u": round_money(balance * rows[0].period_rate),
    })
    for index in range(1, len(rows)):
        balance = rows[index - 1].liability_balance + rows[index - 1].interest_u - amounts[index]
        rows[index] = rows[index].model_copy(update={
            "liability_balance": balance,
            "interest_u": round_money(balance * rows[index].period_rate),
        })
    return rows


def _month_grid(payments, counts) -> list[str]:
    total = sum(counts)
    first = next(index for index, count in enumerate(counts) if count > 0)
    year, month = payments[first].period_start.year, payments[first].period_start.month
    labels: list[str] = []
    for _ in range(total):
        labels.append(_month_label(year, month))
        month += 1
        if month == 13:
            year, month = year + 1, 1
    return labels


def _payable_by_month(payments, counts) -> list[Decimal]:
    """付款前一月确认应付；0 月期并入前一实际月份（首期 0 月期并入首月）。"""
    total = sum(counts)
    first_index: dict[int, int] = {}
    cursor = 0
    for index, count in enumerate(counts):
        if count > 0:
            first_index[index] = cursor
            cursor += count

    payable = [Decimal("0.00")] * total
    for index, (row, count) in enumerate(zip(payments, counts)):
        if count > 0:
            target = first_index[index] - 1
        else:
            later = next((j for j in range(index + 1, len(counts)) if counts[j] > 0), None)
            target = first_index[later] - 1 if later is not None else total - 1
        payable[max(0, target)] += Decimal(row.amount)
    return payable


def _build_periods(payments, counts, assessment, labels, payable,
                   monthly_depreciation: Decimal) -> list[PeriodRow]:
    total_payment = sum((Decimal(row.amount) for row in payments), Decimal("0"))
    total_interest = sum((row.interest_u for row in assessment), Decimal("0"))

    periods: list[PeriodRow] = []
    position = 0
    cumulative_interest = Decimal("0.00")
    cumulative_payable = Decimal("0.00")
    for index, (row, count) in enumerate(zip(payments, counts)):
        if count == 0:
            continue
        monthly_interest = round_money(assessment[index].interest_u / Decimal(count))
        for _ in range(count):
            cumulative_interest += monthly_interest
            cumulative_payable += payable[position]
            unamortized = total_interest - cumulative_interest
            periods.append(PeriodRow(
                period_month=labels[position],
                sequence=row.sequence,
                depreciation=monthly_depreciation,
                payable=payable[position],
                interest=monthly_interest,
                lease_payment=Decimal(row.amount),
                unamortized_finance=unamortized,
                liability_balance=(total_payment - cumulative_payable) - unamortized,
            ))
            position += 1
    return periods


def compute(payload: CalculationInput) -> CalculationResult:
    """按冻结规则矩阵计算；失败抛 :class:`LeaseRuleError`，不留半套结果。"""
    payments = list(payload.payments)
    counts = _validate(payments)
    rate = Decimal(payload.annual_rate)
    if rate < 0:
        raise LeaseRuleError("invalid_rate", "年利率不能为负")

    assessment = _build_assessment(payments, counts, rate)
    asset_cost = sum((row.present_value for row in assessment), Decimal("0"))
    total_payment = sum((row.amount for row in assessment), Decimal("0"))
    total_interest = sum((row.interest_u for row in assessment), Decimal("0"))
    total_months = sum(counts)

    labels = _month_grid(payments, counts)
    payable = _payable_by_month(payments, counts)
    monthly_depreciation = round_money(asset_cost / Decimal(total_months))
    periods = _build_periods(payments, counts, assessment, labels, payable,
                             monthly_depreciation)

    depreciation_sum = sum((row.depreciation for row in periods), Decimal("0.00"))
    payable_sum = sum((row.payable for row in periods), Decimal("0.00"))
    interest_sum = sum((row.interest for row in periods), Decimal("0.00"))
    monthly_interest_rule = sum(
        (round_money(row.interest_u / Decimal(count)) * Decimal(count)
         for row, count in zip(assessment, counts) if count > 0),
        Decimal("0.00"),
    )

    start_ok = (payload.contract_start == payments[0].period_start
                and payload.contract_end == payments[-1].period_end)
    reason = ""
    if not start_ok:
        reason = ("合同起止（{0} 至 {1}）与付款计划（{2} 至 {3}）不对应，"
                  "可能漏录免租期或区间外期间").format(
            payload.contract_start.isoformat(), payload.contract_end.isoformat(),
            payments[0].period_start.isoformat(), payments[-1].period_end.isoformat())

    months_provided = any(row.months is not None for row in payments)
    mismatch = None
    if months_provided:
        for row, count in zip(payments, counts):
            if row.months is not None and row.months != count:
                mismatch = (row.sequence, row.months, count)
                break

    facts = ScheduleFacts(
        asset_cost=asset_cost,
        payment_total=total_payment,
        unconfirmed_interest=total_interest,
        total_months=total_months,
        depreciation_sum=depreciation_sum,
        payable_sum=payable_sum,
        interest_sum=interest_sum,
        monthly_depreciation=monthly_depreciation,
        monthly_interest_rule_sum=monthly_interest_rule,
        closing_balance=periods[-1].liability_balance if periods else Decimal("0.00"),
        remaining_planned=total_payment - payable_sum,
        contract_range_ok=start_ok,
        contract_range_reason=reason,
        months_provided=months_provided,
        month_mismatch=mismatch,
    )

    summary = CalculationSummary(
        total_payment=total_payment,
        present_value=asset_cost,
        total_interest=total_interest,
        total_depreciation=depreciation_sum,
        payment_count=len(assessment),
        month_count=total_months,
        first_period=labels[0] if labels else None,
        last_period=labels[-1] if labels else None,
    )
    return CalculationResult(
        rule_version=RULE_VERSION,
        assessment=assessment,
        periods=periods,
        summary=summary,
        checks=evaluate(facts),
    )
