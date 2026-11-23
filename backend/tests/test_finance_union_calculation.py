# -*- coding: utf-8 -*-
"""finance_union 纯计算引擎：口径矩阵与合成用例（无数据库、无 IO）。

本文件是「工会经费计提」口径的唯一测试源，``calculation.py`` 按它实现：

    本期计提   = ROUND_HALF_UP(工资基数 × 批次费率, 2)
    上期差异   = 上期实际 − 上期已计提            # 负数 = 本期冲回
    本期建议入账 = 本期计提 + 上期差异

全部用例只用合成主体/部门与合成金额，不含真实名称、路径或金额。
任何断言都不得删除、跳过或放宽：缺数据必须是 unknown，绝不按 0 通过。
"""
from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.modules.finance_union import calculation
from app.modules.finance_union.schemas import BatchInput, LineInput, UnionLineIn

TWO_PERCENT = "0.02"


def build(lines, rate=TWO_PERCENT, period="2026-11", entity="示例主体") -> BatchInput:
    return BatchInput(
        entity=entity,
        period=period,
        rate=Decimal(rate),
        lines=[LineInput(**line) for line in lines],
    )


def one_line(**overrides) -> list[dict]:
    line = {
        "department": "行政部",
        "current_wage_base": "1000.00",
        "prior_accrual": "500.00",
        "prior_actual": "500.00",
    }
    line.update(overrides)
    return [line]


# ---------- 1. 基础口径 ----------


def test_rule_version_is_declared():
    assert isinstance(calculation.RULE_VERSION, str) and calculation.RULE_VERSION.strip()


def test_two_percent_accrual_on_wage_base():
    result = calculation.compute(build(one_line(current_wage_base="12345.67")))
    row = result.lines[0]
    assert row.line_status == "ok"
    # 12345.67 × 0.02 = 246.9134 → 246.91
    assert row.current_accrued == Decimal("246.91")
    assert row.prior_variance == Decimal("0.00")
    assert row.current_book_amount == Decimal("246.91")


def test_half_up_rounding_boundary():
    # 100.25 × 0.02 = 2.005 → HALF_UP 2.01（银行家舍入会得 2.00，必须不同）
    result = calculation.compute(build(one_line(current_wage_base="100.25")))
    assert result.lines[0].current_accrued == Decimal("2.01")


def test_rate_comes_from_batch_only():
    # 同一费率作用于所有行：行级没有任何覆盖入口
    lines = [
        {"department": "行政部", "current_wage_base": "1000.00",
         "prior_accrual": "0", "prior_actual": "0"},
        {"department": "技术部", "current_wage_base": "2000.00",
         "prior_accrual": "0", "prior_actual": "0"},
    ]
    result = calculation.compute(build(lines))
    assert [row.current_accrued for row in result.lines] == [Decimal("20.00"), Decimal("40.00")]
    # 行输入模型不含任何费率字段（extra=forbid 会直接拒绝）
    with pytest.raises(ValidationError):
        UnionLineIn(department="行政部", current_wage_base="1000.00",
                    prior_accrual="0", prior_actual="0", rate="0.05")


# ---------- 2. 上期差异三态 ----------


def test_prior_accrual_above_actual_is_negative_adjustment():
    result = calculation.compute(build(one_line(prior_accrual="500.00", prior_actual="480.00")))
    row = result.lines[0]
    assert row.prior_variance == Decimal("-20.00")
    # 本期计提 20.00 + (-20.00) = 0.00（全额冲回）
    assert row.current_book_amount == Decimal("0.00")
    assert "冲回" in row.note


def test_prior_actual_above_accrual_is_positive_adjustment():
    result = calculation.compute(build(one_line(prior_accrual="300.00", prior_actual="420.00")))
    row = result.lines[0]
    assert row.prior_variance == Decimal("120.00")
    assert row.current_book_amount == Decimal("140.00")
    assert row.note == ""


def test_prior_variance_equal_to_zero_is_ok_not_unknown():
    result = calculation.compute(build(one_line(prior_accrual="500.00", prior_actual="500.00")))
    assert result.lines[0].line_status == "ok"
    assert result.lines[0].prior_variance == Decimal("0.00")


# ---------- 3. 显式 0 与缺失的区别 ----------


def test_explicit_zero_wage_base_is_computable():
    result = calculation.compute(build(one_line(
        current_wage_base="0", prior_accrual="0", prior_actual="0")))
    row = result.lines[0]
    assert row.line_status == "ok"
    assert row.current_accrued == Decimal("0.00")
    assert row.prior_variance == Decimal("0.00")
    assert row.current_book_amount == Decimal("0.00")
    assert result.confirmable is True


@pytest.mark.parametrize("missing_field", ["current_wage_base", "prior_accrual", "prior_actual"])
def test_missing_amount_is_unknown_not_zero(missing_field):
    result = calculation.compute(build(one_line(**{missing_field: None})))
    row = result.lines[0]
    assert row.line_status == "unknown"
    assert row.missing_fields == [missing_field]
    # 派生值全部 None：绝不能按 0 给出
    assert row.current_accrued is None
    assert row.prior_variance is None
    assert row.current_book_amount is None
    assert result.has_unknown is True
    assert result.confirmable is False


def test_unknown_line_blocks_confirmation_with_chinese_reason():
    result = calculation.compute(build(one_line(prior_actual=None)))
    assert "缺数据" in result.confirm_reason
    assert "行政部" in result.confirm_reason


def test_unknown_line_excluded_from_totals():
    lines = [
        {"department": "行政部", "current_wage_base": "1000.00",
         "prior_accrual": "0", "prior_actual": "0"},
        {"department": "技术部", "current_wage_base": "2000.00",
         "prior_accrual": "0", "prior_actual": None},
    ]
    result = calculation.compute(build(lines))
    assert result.line_count == 2 and result.ok_line_count == 1
    assert result.unknown_line_count == 1
    # 合计只含行政部 1000×2%=20.00；技术部不按 0 混入
    assert result.totals.current_accrued == Decimal("20.00")
    assert result.totals.current_wage_base == Decimal("1000.00")


# ---------- 4. 合计与复算检查 ----------


def test_totals_sum_all_components():
    lines = [
        {"department": "行政部", "current_wage_base": "1000.00",
         "prior_accrual": "500.00", "prior_actual": "480.00"},
        {"department": "技术部", "current_wage_base": "2000.00",
         "prior_accrual": "300.00", "prior_actual": "420.00"},
    ]
    totals = calculation.compute(build(lines)).totals
    assert totals.current_wage_base == Decimal("3000.00")
    assert totals.current_accrued == Decimal("60.00")
    assert totals.prior_accrual == Decimal("800.00")
    assert totals.prior_actual == Decimal("900.00")
    assert totals.prior_variance == Decimal("100.00")
    assert totals.current_book_amount == Decimal("160.00")


def test_checks_all_pass_for_complete_batch():
    result = calculation.compute(build(one_line()))
    assert result.all_passed is True
    assert result.check_counts == {"ok": 4, "failed": 0, "unknown": 0}
    assert {check.key for check in result.checks} == {
        "batch_lines_present", "line_inputs_complete",
        "wage_base_non_negative", "book_amount_recompute",
    }


def test_empty_batch_is_not_confirmable():
    result = calculation.compute(build([]))
    assert result.line_count == 0
    assert result.confirmable is False
    assert "没有任何部门行" in result.confirm_reason
    statuses = {check.key: check.status for check in result.checks}
    assert statuses["batch_lines_present"] == "failed"
    assert statuses["line_inputs_complete"] == "unknown"


def test_book_amount_recompute_is_unknown_when_any_line_unknown():
    result = calculation.compute(build(one_line(current_wage_base=None)))
    statuses = {check.key: check.status for check in result.checks}
    assert statuses["book_amount_recompute"] == "unknown"
    assert result.all_passed is False


# ---------- 5. 非法输入（schema 层拒绝，引擎不接收） ----------


@pytest.mark.parametrize("rate", ["0", "-0.02", "1.000001", "2"])
def test_invalid_rate_rejected_by_schema(rate):
    with pytest.raises(ValidationError):
        build(one_line(), rate=rate)


def test_negative_wage_base_rejected_by_schema():
    with pytest.raises(ValidationError):
        UnionLineIn(department="行政部", current_wage_base="-1.00",
                    prior_accrual="0", prior_actual="0")


@pytest.mark.parametrize("period", ["2026-13", "2026-00", "2026/11", "26-11", "2026-1"])
def test_invalid_period_rejected_by_schema(period):
    with pytest.raises(ValidationError):
        build(one_line(), period=period)


def test_rate_is_mandatory_in_batch_input():
    with pytest.raises(ValidationError):
        BatchInput(entity="示例主体", period="2026-11", lines=[])


def test_amounts_beyond_two_decimals_rejected():
    with pytest.raises(ValidationError):
        UnionLineIn(department="行政部", current_wage_base="1.005",
                    prior_accrual="0", prior_actual="0")


# ---------- 6. 输入不可变性与文本处理 ----------


def test_compute_does_not_mutate_input():
    payload = build(one_line())
    before = payload.model_dump()
    calculation.compute(payload)
    assert payload.model_dump() == before


def test_department_is_trimmed_by_schema():
    line = UnionLineIn(department="  行政部  ", current_wage_base="1.00",
                       prior_accrual="0", prior_actual="0")
    assert line.department == "行政部"


def test_blank_department_rejected():
    with pytest.raises(ValidationError):
        UnionLineIn(department="   ", current_wage_base="1.00",
                    prior_accrual="0", prior_actual="0")
