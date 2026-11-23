# -*- coding: utf-8 -*-
"""finance_lease 统一检查输出与容差（纯函数，无数据库、无文件依赖）。

检查项覆盖计划要求：三组恒等式、负债滚动、剩余计划付款、月折旧/月利息规则、
合同范围、源表月数一致性，以及首期与月度分录汇总的借贷平衡（只做汇总校验，
不在引擎生成逐笔记账行）。

口径边界
--------
- 所有金额都是**本模块计划口径**（合同计划付款、计划折旧、计划利息），
  不等同 BIP 账面数；不得把本文件结论当作账实核对结果。
- 本文件只做**汇总级**检查；逐笔底稿分录由 ``entries.py`` 生成并逐凭证校验，
  两者边界不重叠，不要用这里的 ``ok`` 代替凭证级借贷校验。
- 缺数据一律 ``unknown``，不得按 0 判 ``ok``；容差只认本模块常量，不按调用方放宽。
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

from .schemas import CalculationCheck

TOL_TOTAL = Decimal("1.00")
TOL_UNIT = Decimal("0.01")

_CENT = Decimal("0.01")


@dataclass(frozen=True)
class ScheduleFacts:
    """引擎算完后交给检查层的只读事实，避免检查层重算几何。"""

    asset_cost: Decimal
    payment_total: Decimal
    unconfirmed_interest: Decimal
    total_months: int
    depreciation_sum: Decimal
    payable_sum: Decimal
    interest_sum: Decimal
    monthly_depreciation: Decimal | None
    monthly_interest_rule_sum: Decimal
    closing_balance: Decimal
    remaining_planned: Decimal
    contract_range_ok: bool
    contract_range_reason: str
    months_provided: bool
    month_mismatch: tuple[int, int, int] | None


def _money(value) -> Decimal:
    return Decimal(value).quantize(_CENT, rounding=ROUND_HALF_UP)


def _ok(difference, tolerance) -> bool:
    return abs(Decimal(difference)) <= tolerance


def _check(key: str, title: str, status: str, *, expected=None, actual=None,
           message: str = "") -> CalculationCheck:
    difference = None
    if expected is not None and actual is not None:
        difference = _money(Decimal(actual) - Decimal(expected))
    return CalculationCheck(
        key=key,
        title=title,
        status=status,
        expected=None if expected is None else _money(expected),
        actual=None if actual is None else _money(actual),
        difference=difference,
        message=message,
    )


def _identity(key: str, title: str, expected: Decimal, actual: Decimal,
              hint: str) -> CalculationCheck:
    difference = _money(actual - expected)
    passed = _ok(difference, TOL_TOTAL)
    return _check(
        key, title, "ok" if passed else "failed",
        expected=expected, actual=actual,
        message="" if passed else
        f"{hint}；差异 {difference} 超过容差 {TOL_TOTAL}，不得倒挤尾差或放宽容差",
    )


def _monthly_dep_rule(facts: ScheduleFacts) -> Decimal:
    if not facts.total_months:
        return Decimal("0.00")
    return _money(facts.asset_cost / Decimal(facts.total_months))


def evaluate(facts: ScheduleFacts) -> list[CalculationCheck]:
    """返回全部检查；key 唯一，状态只有 ok / failed / unknown。"""
    dep_rule = _monthly_dep_rule(facts)
    reported_dep = Decimal(facts.monthly_depreciation or 0)

    # 首期确认：借 使用权资产 H2 + 借 未确认融资费用 M2 = 贷 租赁付款额 K2
    recognition_debit = facts.asset_cost + facts.unconfirmed_interest
    # 月度分录：借（折旧 J + 利息 N + 租赁付款额 L）对 贷（折旧 + 未确认融资费用 + 应付）
    monthly_debit = facts.depreciation_sum + facts.interest_sum + facts.payable_sum
    monthly_credit = (facts.depreciation_sum + facts.unconfirmed_interest
                      + facts.payment_total)

    checks = [
        _identity(
            "identity_depreciation", "Σ月折旧 = 使用权资产原值",
            facts.asset_cost, facts.depreciation_sum,
            "月折旧按 H2 ÷ 月期总数取 2 位",
        ),
        _identity(
            "identity_payable", "Σ应付 = 租赁付款额",
            facts.payment_total, facts.payable_sum,
            "应付按付款前一月确认，每期只能确认一次",
        ),
        _identity(
            "identity_interest", "Σ月利息 = 未确认融资费用",
            facts.unconfirmed_interest, facts.interest_sum,
            "月利息 = 当期利息 ÷ 期内月数，逐月取 2 位",
        ),
        _check(
            "liability_rollforward", "租赁负债期末归零",
            "ok" if _ok(facts.closing_balance, TOL_TOTAL) else "failed",
            expected=Decimal("0.00"), actual=facts.closing_balance,
            message="" if _ok(facts.closing_balance, TOL_TOTAL) else
            f"末期负债余额 {facts.closing_balance} 超过容差 {TOL_TOTAL}",
        ),
        _check(
            "remaining_planned_payment", "剩余计划付款（总额 − 累计计划应付）",
            "ok" if _ok(facts.remaining_planned, TOL_TOTAL) else "failed",
            expected=Decimal("0.00"), actual=facts.remaining_planned,
            message="仅使用本模块计划数据，不对账外部实付金额",
        ),
        _check(
            "monthly_depreciation", "月折旧 = 使用权资产原值 ÷ 月期总数",
            "ok" if _ok(reported_dep - dep_rule, TOL_UNIT) else "failed",
            expected=dep_rule, actual=reported_dep,
        ),
        _check(
            "monthly_interest", "月利息 = 当期利息 ÷ 期内月数",
            "ok" if _ok(facts.interest_sum - facts.monthly_interest_rule_sum, TOL_UNIT)
            else "failed",
            expected=facts.monthly_interest_rule_sum, actual=facts.interest_sum,
        ),
        _check(
            "entry_recognition", "首期确认分录借贷平衡",
            "ok" if _ok(recognition_debit - facts.payment_total, TOL_TOTAL) else "failed",
            expected=facts.payment_total, actual=recognition_debit,
            message=("借：使用权资产 + 未确认融资费用；贷：租赁付款额。"
                     "本项仅做汇总级检查；逐笔底稿分录由 entries.py 生成并逐凭证校验"),
        ),
        _check(
            "entry_monthly", "月度分录汇总借贷平衡",
            "ok" if _ok(monthly_debit - monthly_credit, TOL_TOTAL) else "failed",
            expected=monthly_debit, actual=monthly_credit,
            message=("借：折旧 + 利息 + 租赁付款额；贷：折旧 + 未确认融资费用 + 应付。"
                     "本项仅做汇总级检查；逐笔底稿分录由 entries.py 生成并逐凭证校验"),
        ),
    ]

    if facts.contract_range_ok:
        checks.append(_check("contract_range", "合同区间与付款计划一致", "ok"))
    else:
        checks.append(_check(
            "contract_range", "合同区间与付款计划一致", "unknown",
            message=facts.contract_range_reason or "合同起止与付款期不对应，无法确认租赁期完整性",
        ))

    if facts.months_provided:
        if facts.month_mismatch is None:
            checks.append(_check("month_count_consistency", "源表月期数与 DATEDIF 一致", "ok"))
        else:
            sequence, source_months, computed = facts.month_mismatch
            checks.append(_check(
                "month_count_consistency", "源表月期数与 DATEDIF 一致", "failed",
                expected=Decimal(computed), actual=Decimal(source_months),
                message=(f"第 {sequence} 期源表月数 {source_months} 与 DATEDIF {computed} "
                         "不一致；以 DATEDIF 为准，请核对计租起止日期"),
            ))
    return checks
