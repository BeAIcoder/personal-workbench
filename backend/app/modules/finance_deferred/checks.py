# -*- coding: utf-8 -*-
"""finance_deferred 统一检查输出（纯函数，无数据库、无文件依赖）。

检查覆盖：净额公式、税额、月摊公式、Σ月摊 vs 净额（末期尾差只警告不吸差）、
期间行数、月份连续性、末期剩余月数归零、余额滚动、摊销到期 vs 租赁到期日。

口径边界
--------
- ``warning`` = 已披露但不阻断：超租赁到期日、末期尾差；确认前必须由人显式
  确认风险并写明说明，绝不自动缩短年限、绝不把尾差并入末期。
- ``failed`` = 规则被破坏（公式/行数/连续性不符），阻断确认与导出。
- 缺数据一律 ``unknown``，不得按 0 判 ``ok``；容差只认本模块常量。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

from .schemas import DeferredCheck

# 单期金额容差：公式类检查要求严格相等，这里只用于金额间的比较展示
TOL_UNIT = Decimal("0.01")

_CENT = Decimal("0.01")


@dataclass(frozen=True)
class AmortizationFacts:
    """引擎算完后交给检查层的只读事实，避免检查层重算几何。"""

    amount_incl_tax: Decimal
    tax_rate: Decimal
    amount_net: Decimal
    tax_amount: Decimal
    months: int
    monthly_amount: Decimal
    period_count: int
    amortized_total: Decimal
    rounding_difference: Decimal
    closing_balance: Decimal | None
    first_month: str | None
    last_month: str | None
    month_sequence_ok: bool
    remaining_closes: bool
    lease_end_date: date | None
    end_month: str | None
    expiry_after_lease: bool


def _money(value) -> Decimal:
    return Decimal(value).quantize(_CENT, rounding=ROUND_HALF_UP)


def _check(key: str, title: str, status: str, *, expected=None, actual=None,
           message: str = "") -> DeferredCheck:
    difference = None
    if expected is not None and actual is not None:
        difference = _money(Decimal(actual) - Decimal(expected))
    return DeferredCheck(
        key=key,
        title=title,
        status=status,
        expected=None if expected is None else _money(expected),
        actual=None if actual is None else _money(actual),
        difference=difference,
        message=message,
    )


def expected_net(amount_incl_tax, tax_rate) -> Decimal:
    """净额 = 含税 ÷ (1 + 税率)，两位 HALF_UP。"""
    return _money(Decimal(amount_incl_tax) / (Decimal(1) + Decimal(tax_rate)))


def expected_monthly(amount_net, months: int) -> Decimal | None:
    """月摊 = 净额 ÷ 月数，两位 HALF_UP；月数非法时返回 None（交由调用方判 unknown）。"""
    if not isinstance(months, int) or months <= 0:
        return None
    return _money(Decimal(amount_net) / Decimal(months))


def _formula_check(key: str, title: str, expected: Decimal, actual: Decimal,
                   hint: str) -> DeferredCheck:
    difference = _money(actual - expected)
    passed = difference == Decimal("0.00")
    return _check(
        key, title, "ok" if passed else "failed",
        expected=expected, actual=actual,
        message="" if passed else f"{hint}；差异 {difference}，请核对输入参数",
    )


def evaluate(facts: AmortizationFacts) -> list[DeferredCheck]:
    """返回全部检查；key 唯一，状态只有 ok / warning / failed / unknown。"""
    checks: list[DeferredCheck] = []

    checks.append(_formula_check(
        "net_amount_formula", "净额 = 含税 ÷ (1 + 税率)",
        expected_net(facts.amount_incl_tax, facts.tax_rate), facts.amount_net,
        "净额按两位 HALF_UP 计算",
    ))

    expected_tax = _money(Decimal(facts.amount_incl_tax) - Decimal(facts.amount_net))
    checks.append(_formula_check(
        "tax_amount", "税额 = 含税金额 − 净额",
        expected_tax, facts.tax_amount,
        "税率为 0 时税额应为 0 且净额等于含税金额",
    ))

    monthly = expected_monthly(facts.amount_net, facts.months)
    if monthly is None:
        checks.append(_check(
            "monthly_amount", "月摊 = 净额 ÷ 摊销月数", "unknown",
            message=f"摊销月数 {facts.months} 不合法，无法核对月摊金额",
        ))
    else:
        checks.append(_formula_check(
            "monthly_amount", "月摊 = 净额 ÷ 摊销月数", monthly, facts.monthly_amount,
            "月摊按两位 HALF_UP 计算，不吸收尾差",
        ))

    if facts.period_count <= 0:
        checks.append(_check(
            "period_count", "期间行数 = 摊销月数", "unknown",
            message="未生成月度摊销行（数据不足），不得视为通过",
        ))
        checks.append(_check(
            "amortization_total", "Σ月摊 = 净额（末期尾差如实保留）", "unknown",
            message="缺少月度摊销行，无法核对累计摊销",
        ))
    else:
        checks.append(_check(
            "period_count", "期间行数 = 摊销月数",
            "ok" if facts.period_count == facts.months else "failed",
            expected=Decimal(facts.months), actual=Decimal(facts.period_count),
            message="" if facts.period_count == facts.months else
            f"期间行数 {facts.period_count} 与摊销月数 {facts.months} 不一致",
        ))
        difference = _money(Decimal(facts.amortized_total) - Decimal(facts.amount_net))
        tolerance = TOL_UNIT * Decimal(max(facts.months, 1))
        if difference == Decimal("0.00"):
            checks.append(_check(
                "amortization_total", "Σ月摊 = 净额（末期尾差如实保留）", "ok",
                expected=facts.amount_net, actual=facts.amortized_total,
            ))
        elif abs(difference) <= tolerance:
            checks.append(_check(
                "amortization_total", "Σ月摊 = 净额（末期尾差如实保留）", "warning",
                expected=facts.amount_net, actual=facts.amortized_total,
                message=(f"末期尾差 {difference} 元：已如实留在末期余额，"
                         "未吸收进末期、未自动转销；确认前需显式确认该差异"),
            ))
        else:
            checks.append(_check(
                "amortization_total", "Σ月摊 = 净额（末期尾差如实保留）", "failed",
                expected=facts.amount_net, actual=facts.amortized_total,
                message=(f"累计摊销与净额差异 {difference} 超出尾差容差 {tolerance}，"
                         "规则被破坏，请重新测算"),
            ))

    if facts.period_count <= 0:
        checks.append(_check("month_sequence", "月份连续且起于起摊月", "unknown",
                             message="缺少月度摊销行，无法核对月份连续性"))
        checks.append(_check("remaining_months", "末期剩余月数 = 0", "unknown",
                             message="缺少月度摊销行，无法核对剩余月数"))
        checks.append(_check("balance_rollforward", "余额 = 净额 − 累计摊销", "unknown",
                             message="缺少月度摊销行，无法核对余额滚动"))
    else:
        checks.append(_check(
            "month_sequence", "月份连续且起于起摊月",
            "ok" if facts.month_sequence_ok else "failed",
            message="" if facts.month_sequence_ok else
            f"月份序列不连续（首月 {facts.first_month or '—'} / 末月 {facts.last_month or '—'}）",
        ))
        checks.append(_check(
            "remaining_months", "末期剩余月数 = 0",
            "ok" if facts.remaining_closes else "failed",
            message="" if facts.remaining_closes else "末期剩余月数不为 0，摊销未走完",
        ))
        closing = Decimal(facts.closing_balance or 0)
        target = _money(Decimal(facts.amount_net) - Decimal(facts.amortized_total))
        checks.append(_formula_check(
            "balance_rollforward", "余额 = 净额 − 累计摊销", target, closing,
            "末期余额应等于净额减累计摊销（即尾差本身）",
        ))

    if facts.lease_end_date is None or facts.end_month is None:
        checks.append(_check(
            "lease_expiry_coverage", "摊销到期不晚于租赁到期日", "unknown",
            message="缺少租赁到期日或摊销到期月（数据不足），不得视为通过",
        ))
    elif facts.expiry_after_lease:
        checks.append(_check(
            "lease_expiry_coverage", "摊销到期不晚于租赁到期日", "warning",
            message=(f"摊销到期月 {facts.end_month}（月末 {_month_end_iso(facts.end_month)}）"
                     f"晚于租赁到期日 {facts.lease_end_date.isoformat()}；"
                     "按规则不自动缩短年限，确认前需显式确认风险并写明说明"),
        ))
    else:
        checks.append(_check(
            "lease_expiry_coverage", "摊销到期不晚于租赁到期日", "ok",
            message=f"摊销到期月 {facts.end_month} 不晚于租赁到期日 "
                    f"{facts.lease_end_date.isoformat()}",
        ))

    return checks


def _month_end_iso(month: str) -> str:
    year, mon = int(month[:4]), int(month[5:7])
    if mon == 12:
        return date(year, 12, 31).isoformat()
    return (date(year, mon + 1, 1) - timedelta(days=1)).isoformat()
