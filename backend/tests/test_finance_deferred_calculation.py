# -*- coding: utf-8 -*-
"""finance_deferred 纯计算引擎与校验的黄金用例（临时库无关，只用合成数据）。

覆盖：税额 0、净额/月摊两位 HALF_UP、末期尾差如实保留、整月与跨年月份网格、
摊销到期超租赁到期日只警告且不缩短年限、边界日相等、非法日期/税率/月数，
以及校验层的 failed / unknown 判定（缺数据不得按 0 通过）。
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.modules.finance_deferred import calculation, checks
from app.modules.finance_deferred.schemas import CalculationInput


def make_input(**overrides) -> CalculationInput:
    body = {
        "amount_incl_tax": "113.00",
        "tax_rate": "0.13",
        "start_month": "2026-01",
        "months": 12,
        "lease_end_date": "2026-12-31",
    }
    return CalculationInput(**{**body, **overrides})


def check_map(result):
    return {item.key: item for item in result.checks}


# ---------- 金额口径 ----------


def test_zero_tax_rate_keeps_net_equal_to_gross():
    result = calculation.compute(make_input(amount_incl_tax="1200.00", tax_rate="0"))
    summary = result.summary
    assert summary.tax_amount == Decimal("0.00")
    assert summary.amount_net == Decimal("1200.00")
    assert summary.monthly_amount == Decimal("100.00")
    assert summary.amortized_total == Decimal("1200.00")
    assert summary.rounding_difference == Decimal("0.00")
    assert summary.risk_flags == []
    statuses = {item.key: item.status for item in result.checks}
    assert statuses["net_amount_formula"] == "ok"
    assert statuses["tax_amount"] == "ok"
    assert set(statuses.values()) == {"ok"}


def test_net_amount_rounds_half_up():
    result = calculation.compute(make_input(amount_incl_tax="100.00", tax_rate="0.13",
                                            months=10, lease_end_date="2026-10-31"))
    assert result.summary.amount_net == Decimal("88.50")  # 88.4956… → 88.50
    assert result.summary.tax_amount == Decimal("11.50")
    assert result.summary.monthly_amount == Decimal("8.85")
    assert result.summary.amortized_total == Decimal("88.50")


def test_monthly_residual_is_disclosed_not_absorbed():
    result = calculation.compute(make_input(amount_incl_tax="1000.00", tax_rate="0",
                                            start_month="2026-01", months=3,
                                            lease_end_date="2026-12-31"))
    summary = result.summary
    assert summary.monthly_amount == Decimal("333.33")
    assert summary.amortized_total == Decimal("999.99")
    assert summary.rounding_difference == Decimal("0.01")
    assert [row.monthly_amount for row in result.periods] == [Decimal("333.33")] * 3
    # 末期不吸差：末期仍为 333.33，余额如实等于尾差
    assert result.periods[-1].balance == Decimal("0.01")
    assert result.periods[-1].remaining_months == 0
    target = check_map(result)["amortization_total"]
    assert target.status == "warning"
    assert target.difference == Decimal("-0.01")
    assert "尾差" in target.message and "未自动转销" in target.message
    assert any("末期尾差" in flag for flag in summary.risk_flags)
    assert check_map(result)["balance_rollforward"].status == "ok"


def test_exact_division_has_no_warning():
    result = calculation.compute(make_input(amount_incl_tax="1356.00", tax_rate="0.13",
                                            months=12, lease_end_date="2026-12-31"))
    assert result.summary.amount_net == Decimal("1200.00")
    assert result.summary.monthly_amount == Decimal("100.00")
    assert result.summary.rounding_difference == Decimal("0.00")
    assert result.summary.risk_flags == []
    assert all(item.status == "ok" for item in result.checks)


# ---------- 月份网格 ----------


def test_month_grid_crosses_year_and_keeps_sequence():
    result = calculation.compute(make_input(amount_incl_tax="1356.00", tax_rate="0.13",
                                            start_month="2026-11", months=15,
                                            lease_end_date="2028-01-31"))
    labels = [row.period_month for row in result.periods]
    assert labels[0] == "2026-11" and labels[1] == "2026-12" and labels[2] == "2027-01"
    assert labels[-1] == "2028-01"
    assert result.summary.end_month == "2028-01"
    assert [row.sequence for row in result.periods] == list(range(1, 16))
    assert result.periods[0].remaining_months == 14
    assert result.periods[-1].remaining_months == 0
    assert check_map(result)["month_sequence"].status == "ok"


def test_single_month_amortization():
    result = calculation.compute(make_input(amount_incl_tax="113.00", tax_rate="0.13",
                                            months=1, lease_end_date="2026-01-31"))
    assert len(result.periods) == 1
    assert result.periods[0].monthly_amount == Decimal("100.00")
    assert result.periods[0].balance == Decimal("0.00")


# ---------- 租赁到期日 ----------


def test_expiry_after_lease_is_warning_and_years_not_shortened():
    result = calculation.compute(make_input(amount_incl_tax="1200.00", tax_rate="0",
                                            start_month="2026-01", months=12,
                                            lease_end_date="2026-06-30"))
    # 不自动缩短年限：仍生成 12 个摊销月
    assert len(result.periods) == 12
    assert result.summary.end_month == "2026-12"
    assert result.summary.expiry_after_lease is True
    target = check_map(result)["lease_expiry_coverage"]
    assert target.status == "warning"
    assert "2026-12-31" in target.message and "2026-06-30" in target.message
    assert "不自动缩短" in target.message
    assert any("晚于租赁到期日" in flag for flag in result.summary.risk_flags)


def test_expiry_on_lease_last_day_is_ok():
    result = calculation.compute(make_input(amount_incl_tax="1200.00", tax_rate="0",
                                            start_month="2026-01", months=12,
                                            lease_end_date="2026-12-31"))
    assert result.summary.expiry_after_lease is False
    assert check_map(result)["lease_expiry_coverage"].status == "ok"


def test_expiry_compare_uses_month_end_not_first_day():
    # 租赁到期日 2026-12-15 < 摊销到期月月末 2026-12-31 → 警告
    result = calculation.compute(make_input(amount_incl_tax="1200.00", tax_rate="0",
                                            start_month="2026-01", months=12,
                                            lease_end_date="2026-12-15"))
    assert result.summary.expiry_after_lease is True


# ---------- 非法输入 ----------


@pytest.mark.parametrize("overrides", [
    {"months": 0},
    {"months": 601},
    {"tax_rate": "1.5"},
    {"tax_rate": "-0.01"},
    {"amount_incl_tax": "-1.00"},
    {"start_month": "2026-13"},
    {"start_month": "2026/01"},
    {"lease_end_date": "2026-02-30"},
    {"lease_end_date": "2026-01-01T00:00:00"},
])
def test_invalid_inputs_are_rejected_by_schema(overrides):
    with pytest.raises(ValueError):
        make_input(**overrides)


def test_engine_guards_defend_in_depth():
    payload = make_input()
    bad = payload.model_copy(update={"months": 0})
    with pytest.raises(calculation.DeferredRuleError) as error:
        calculation.compute(bad)
    assert error.value.code == "invalid_months"


def test_month_helpers_validate_period_format():
    with pytest.raises(calculation.DeferredRuleError):
        calculation.month_grid("2026-1", 2)
    assert calculation.month_grid("2026-12", 2) == ["2026-12", "2027-01"]
    assert calculation.month_end("2028-02").isoformat() == "2028-02-29"


# ---------- 校验层：failed / unknown 不得伪装通过 ----------


def facts(**overrides) -> checks.AmortizationFacts:
    body = dict(
        amount_incl_tax=Decimal("1200.00"),
        tax_rate=Decimal("0"),
        amount_net=Decimal("1200.00"),
        tax_amount=Decimal("0.00"),
        months=12,
        monthly_amount=Decimal("100.00"),
        period_count=12,
        amortized_total=Decimal("1200.00"),
        rounding_difference=Decimal("0.00"),
        closing_balance=Decimal("0.00"),
        first_month="2026-01",
        last_month="2026-12",
        month_sequence_ok=True,
        remaining_closes=True,
        lease_end_date=date(2026, 12, 31),
        end_month="2026-12",
        expiry_after_lease=False,
    )
    body.update(overrides)
    return checks.AmortizationFacts(**body)


def test_formula_breaks_report_failed():
    result = checks.evaluate(facts(amount_net=Decimal("1100.00")))
    by_key = {item.key: item for item in result}
    assert by_key["net_amount_formula"].status == "failed"
    assert by_key["monthly_amount"].status == "failed"
    assert by_key["balance_rollforward"].status == "failed"


def test_residual_beyond_tolerance_is_failed():
    result = checks.evaluate(facts(amortized_total=Decimal("1100.00"),
                                   rounding_difference=Decimal("100.00"),
                                   closing_balance=Decimal("100.00")))
    target = {item.key: item for item in result}["amortization_total"]
    assert target.status == "failed"
    assert "超出尾差容差" in target.message


def test_missing_periods_is_unknown_not_zero_pass():
    result = checks.evaluate(facts(period_count=0, closing_balance=None,
                                   first_month=None, last_month=None,
                                   month_sequence_ok=False, remaining_closes=False,
                                   amortized_total=Decimal("0.00")))
    by_key = {item.key: item for item in result}
    for key in ("period_count", "amortization_total", "month_sequence",
                "remaining_months", "balance_rollforward"):
        assert by_key[key].status == "unknown", key


def test_missing_lease_date_is_unknown():
    result = checks.evaluate(facts(lease_end_date=None, end_month=None))
    target = {item.key: item for item in result}["lease_expiry_coverage"]
    assert target.status == "unknown"
    assert "不得视为通过" in target.message


def test_check_keys_unique_and_statuses_bounded():
    result = calculation.compute(make_input())
    keys = [item.key for item in result.checks]
    assert len(keys) == len(set(keys))
    assert {item.status for item in result.checks} <= {"ok", "warning", "failed", "unknown"}
    assert result.rule_version == calculation.RULE_VERSION
