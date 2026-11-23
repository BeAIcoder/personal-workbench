# -*- coding: utf-8 -*-
"""工会经费计提纯计算引擎（不读数据库、不写文件、不做任何 IO）。

唯一口径（与本模块文档、测试、导出页签逐条对应）::

    本期计提   current_accrued    = ROUND_HALF_UP(工资基数 × 批次费率, 2)
    上期差异   prior_variance     = 上期实际 − 上期已计提        # 负数 = 应冲回
    本期建议入账 current_book_amount = 本期计提 + 上期差异

三条硬规则：

1. **缺失即未知**：工资基数 / 上期已计提 / 上期实际 任一为 ``None``，该行
   ``line_status = unknown``，三个派生值全部为 ``None``；绝不把缺失当 0 参与计算。
   显式 0 与缺失是两件不同的事实（0 可计算，缺失不可确认）。
2. **费率只有一个来源**：批次费率；行级没有覆盖字段，本引擎也不接受行级费率。
3. **金额只走 Decimal + ROUND_HALF_UP 两位**，全模块禁止 ``round()``。

合计只汇总 ``line_status = ok`` 的行；unknown 行数与缺失字段名单独上报，
由调用方决定展示与阻断，不给「缺数据的行按 0 计入」留任何入口。
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

from .schemas import (
    BatchInput,
    BatchResult,
    LineInput,
    UnionCheck,
    UnionLineResult,
    UnionTotals,
)

# 规则版本（该字符串随结果返回，便于追溯口径；本模块暂无批次级快照表）
RULE_VERSION = "union-v1"

_CENT = Decimal("0.01")
_ZERO = Decimal("0.00")

# 缺失字段的中文名（顺序即展示顺序）
FIELD_LABELS: dict[str, str] = {
    "current_wage_base": "本期工资基数",
    "prior_accrual": "上期已计提",
    "prior_actual": "上期实际数",
}
_MISSING_ORDER = ("current_wage_base", "prior_accrual", "prior_actual")

# 说明文案：出现在接口与导出的「说明」列，前端不再自行拼接
NOTE_MANUAL_WAGE = "工资基数手工录入，不从 HR 取数"
NOTE_NO_SPLIT = "不含 60/40 分成、缴纳凭据与税前扣除判断"


def _money(value: Decimal) -> Decimal:
    """统一 2 位小数（ROUND_HALF_UP）。"""
    return value.quantize(_CENT, rounding=ROUND_HALF_UP)


def missing_fields(line: LineInput) -> list[str]:
    """返回该行缺失的金额字段名（按固定顺序），空列表 = 三数齐全。"""
    return [name for name in _MISSING_ORDER if getattr(line, name) is None]


def compute_line(line: LineInput, rate: Decimal) -> UnionLineResult:
    """单行计算：三数齐全才算，缺任一项即 unknown（派生值全 None）。"""
    missing = missing_fields(line)
    if missing:
        labels = "、".join(FIELD_LABELS[name] for name in missing)
        return UnionLineResult(
            department=line.department,
            current_wage_base=line.current_wage_base,
            prior_accrual=line.prior_accrual,
            prior_actual=line.prior_actual,
            line_status="unknown",
            missing_fields=missing,
            note=f"缺 {labels}：补齐后才能计算与确认，不按 0 处理",
        )
    accrued = _money(line.current_wage_base * rate)
    variance = _money(line.prior_actual - line.prior_accrual)
    book = _money(accrued + variance)
    note = "上期为负数差异，本期入账冲回" if variance < 0 else ""
    return UnionLineResult(
        department=line.department,
        current_wage_base=line.current_wage_base,
        prior_accrual=line.prior_accrual,
        prior_actual=line.prior_actual,
        line_status="ok",
        current_accrued=accrued,
        prior_variance=variance,
        current_book_amount=book,
        note=note,
    )


def _totals(ok_lines: list[UnionLineResult]) -> UnionTotals:
    """只汇总可计算行；无行时合计为 0.00（空批次的合计不代表任何业务事实）。"""
    base = accrued = prior_accrual = prior_actual = variance = book = _ZERO
    for row in ok_lines:
        base += row.current_wage_base
        accrued += row.current_accrued
        prior_accrual += row.prior_accrual
        prior_actual += row.prior_actual
        variance += row.prior_variance
        book += row.current_book_amount
    return UnionTotals(
        current_wage_base=_money(base),
        current_accrued=_money(accrued),
        prior_accrual=_money(prior_accrual),
        prior_actual=_money(prior_actual),
        prior_variance=_money(variance),
        current_book_amount=_money(book),
    )


def _checks(payload: BatchInput, lines: list[UnionLineResult], totals: UnionTotals) -> list[UnionCheck]:
    """四组检查：行存在 / 输入完整 / 工资基数非负 / 建议入账可复算。"""
    unknown = [row for row in lines if row.line_status == "unknown"]
    checks: list[UnionCheck] = []

    if not lines:
        checks.append(UnionCheck(
            key="batch_lines_present", title="批次至少一条部门行", status="failed",
            expected=Decimal("1"), actual=Decimal("0"), difference=Decimal("-1"),
            message="批次没有任何部门行，无法计算与确认",
        ))
    else:
        checks.append(UnionCheck(
            key="batch_lines_present", title="批次至少一条部门行", status="ok",
            expected=Decimal("1"), actual=Decimal(len(lines)), difference=Decimal("0"),
            message=f"共 {len(lines)} 条部门行",
        ))

    if not lines or unknown:
        detail = "批次无部门行" if not lines else (
            "缺数据行：" + "；".join(
                f"{row.department}（{'、'.join(FIELD_LABELS[name] for name in row.missing_fields)}）"
                for row in unknown[:10]
            )
        )
        checks.append(UnionCheck(
            key="line_inputs_complete", title="工资基数与上期数已齐全", status="unknown",
            expected=Decimal(len(lines)), actual=Decimal(len(lines) - len(unknown)),
            difference=Decimal(-len(unknown)),
            message=f"{detail}；缺数据不得按 0 计算",
        ))
    else:
        checks.append(UnionCheck(
            key="line_inputs_complete", title="工资基数与上期数已齐全", status="ok",
            expected=Decimal(len(lines)), actual=Decimal(len(lines)), difference=Decimal("0"),
            message="全部部门行三数齐全",
        ))

    negative = [row for row in lines if (row.current_wage_base or _ZERO) < 0]
    if negative:
        checks.append(UnionCheck(
            key="wage_base_non_negative", title="工资基数非负", status="failed",
            expected=_ZERO,
            actual=_money(sum(
                (row.current_wage_base for row in negative if row.current_wage_base is not None),
                _ZERO,
            )),
            message="工资基数不能为负数：" + "、".join(row.department for row in negative[:10]),
        ))
    else:
        checks.append(UnionCheck(
            key="wage_base_non_negative", title="工资基数非负", status="ok",
            expected=_ZERO, actual=_ZERO, difference=Decimal("0"),
            message="全部行工资基数 >= 0",
        ))

    if unknown:
        checks.append(UnionCheck(
            key="book_amount_recompute", title="本期建议入账 = 本期计提 + 上期差异",
            status="unknown", message="存在缺数据行，合计口径不完整，无法复核",
        ))
    else:
        accrued = sum((row.current_accrued for row in lines), _ZERO)
        variance = sum((row.prior_variance for row in lines), _ZERO)
        expected = _money(accrued + variance)
        actual = totals.current_book_amount
        difference = _money(actual - expected)
        checks.append(UnionCheck(
            key="book_amount_recompute", title="本期建议入账 = 本期计提 + 上期差异",
            status="ok" if difference == _ZERO else "failed",
            expected=expected, actual=actual, difference=difference,
            message=(
                "合计复算一致"
                if difference == _ZERO
                else "合计复算不一致：请检查是否存在非法输入或手工改动"
            ),
        ))
    return checks


def _confirm_reason(payload: BatchInput, checks: list[UnionCheck], unknown) -> str:
    """返回阻止确认的原因；空字符串 = 可确认（唯一判定入口，前端只展示）。"""
    if not payload.lines:
        return "批次没有任何部门行，无法确认"
    if unknown:
        names = "、".join(row.department for row in unknown[:10])
        more = f" 等 {len(unknown)} 行" if len(unknown) > 10 else ""
        return f"存在缺数据的部门行（{names}{more}），补齐工资基数与上期数后才能确认"
    failed = [check for check in checks if check.status == "failed"]
    if failed:
        return "存在差异校验结论：" + "、".join(check.title for check in failed)
    return ""


def compute(payload: BatchInput) -> BatchResult:
    """批次计算入口：行结果 + 合计 + 检查 + 可确认判定，纯函数。"""
    lines = [compute_line(line, payload.rate) for line in payload.lines]
    ok_lines = [row for row in lines if row.line_status == "ok"]
    unknown = [row for row in lines if row.line_status == "unknown"]
    totals = _totals(ok_lines)
    checks = _checks(payload, lines, totals)
    counts = {status: 0 for status in ("ok", "failed", "unknown")}
    for check in checks:
        counts[check.status] += 1
    reason = _confirm_reason(payload, checks, unknown)
    return BatchResult(
        rule_version=RULE_VERSION,
        line_count=len(lines),
        ok_line_count=len(ok_lines),
        unknown_line_count=len(unknown),
        lines=lines,
        totals=totals,
        checks=checks,
        check_counts=counts,
        all_passed=counts["failed"] == 0 and counts["unknown"] == 0,
        has_unknown=counts["unknown"] > 0,
        confirmable=not reason,
        confirm_reason=reason,
    )
