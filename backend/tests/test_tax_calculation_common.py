# -*- coding: utf-8 -*-
from __future__ import annotations

import pytest

from app.modules.finance_dsf import service as dsf
from app.modules.finance_iit import service as iit
from app.modules.finance_stamp_duty import service as stamp
from app.modules.finance_vat import service as vat


@pytest.mark.parametrize("value, expected", [
    (0.005, 0.01),
    (1.005, 1.01),
    (2.675, 2.68),
    (-1.005, -1.01),
])
def test_money_rounds_half_up_to_two_decimals(value, expected):
    from app.utils_money import money

    assert money(value) == expected


@pytest.mark.parametrize("taxable, rate, quick", [
    (36_000.00, 0.03, 0.0),
    (36_000.01, 0.10, 2_520.0),
    (144_000.00, 0.10, 2_520.0),
    (144_000.01, 0.20, 16_920.0),
    (300_000.00, 0.20, 16_920.0),
    (300_000.01, 0.25, 31_920.0),
    (420_000.00, 0.25, 31_920.0),
    (420_000.01, 0.30, 52_920.0),
    (660_000.00, 0.30, 52_920.0),
    (660_000.01, 0.35, 85_920.0),
    (960_000.00, 0.35, 85_920.0),
    (960_000.01, 0.45, 181_920.0),
])
def test_cumulative_withholding_bracket_boundaries(taxable, rate, quick):
    from app.finance_iit_common import calc_cumulative_withholding

    result = calc_cumulative_withholding(taxable + 5_000, 0, 1)
    assert result["cumulative_taxable"] == taxable
    assert result["rate"] == rate
    assert result["quick_deduction"] == quick


def test_cumulative_withholding_keeps_negative_taxable_and_never_refunds_current_month():
    from app.finance_iit_common import calc_cumulative_withholding

    negative = calc_cumulative_withholding(8_000, 450, 2)
    assert negative["cumulative_taxable"] == -2_000.0
    assert negative["cumulative_withholding"] == 0.0
    assert negative["monthly_withholding"] == 0.0

    decreased = calc_cumulative_withholding(23_000, 450, 2)
    assert decreased["cumulative_taxable"] == 13_000.0
    assert decreased["cumulative_withholding"] == 390.0
    assert decreased["monthly_withholding"] == 0.0


def test_cumulative_withholding_uses_employment_month_count():
    from app.finance_iit_common import calc_cumulative_withholding

    result = calc_cumulative_withholding(40_000, 0, 7)
    assert result["cumulative_taxable"] == 5_000.0
    assert result["cumulative_withholding"] == 150.0
    assert result["monthly_withholding"] == 150.0


def test_iit_mid_year_hire_and_policy_status():
    calc = iit.calc_filing("2026", [{
        "month": 7, "name": "合成员工", "employment_start_month": 7,
        "salary": 5_033.50,
    }])
    month = calc["employees"][0]["months"][0]
    assert month["cumulative_taxable"] == 33.50
    assert month["monthly_withholding"] == 1.01
    assert calc["policy"]["policy_key"] == "iit_special_deductions"
    assert calc["policy"]["status"] == "valid"


def test_iit_rejects_year_before_registered_special_deduction_policy():
    with pytest.raises(ValueError, match="2023-01-01"):
        iit.calc_filing("2022", [{"month": 1, "name": "合成员工", "salary": 10_000}])


def test_stamp_duty_item_tax_uses_half_up():
    calc = stamp.calc_filing("2026-09", [
        {"tax_item": "买卖合同", "amount": 3_350},
    ], half_discount=False)
    assert calc["details"][0]["tax"] == 1.01
    assert calc["total_tax"] == 1.01


def test_stamp_duty_half_discount_total_uses_half_up():
    calc = stamp.calc_filing("2026-09", [
        {"tax_item": "买卖合同", "amount": 6_700},
    ], half_discount=True)
    assert calc["raw_total"] == 2.01
    assert calc["total_tax"] == 1.01
    assert calc["half_saved"] == 1.00


def test_vat_general_tax_outputs_use_half_up():
    calc = vat.calc_general("2026-09", [
        {"business_type": "服务", "amount": 16.75, "rate": 0.06},
    ], input_vat=0)
    assert calc["output_vat"] == 1.01
    assert calc["payable"] == 1.01


def test_dsf_payable_uses_half_up():
    calc = dsf.calc_dsf("2026", 200, 402, 2, ratio=0.015)
    assert calc["base_amount"] == 2.01
    assert calc["payable"] == 1.01


def test_hr_and_finance_iit_same_input_use_same_half_up_tax(client):
    created = client.post("/api/business/hr/employees", json={
        "emp_no": "E-IIT-COMMON", "name": "合成员工",
        "hire_date": "2026-01-01", "base_salary": 5_033.50,
        "social_base": 5_033.50,
    })
    assert created.status_code == 201, created.text
    payroll = client.post("/api/business/hr/payroll/generate", json={
        "month": "2026-01", "social_rate": 0, "housing_rate": 0,
    })
    assert payroll.status_code == 201, payroll.text
    hr_tax = payroll.json()["items"][0]["tax"]

    filing = iit.calc_filing("2026", [{
        "month": 1, "name": "合成员工", "salary": 5_033.50,
    }])
    iit_tax = filing["employees"][0]["months"][0]["monthly_withholding"]
    assert (hr_tax, iit_tax) == (1.01, 1.01)


def test_vat_general_multiplies_decimal_inputs_before_rounding():
    calc = vat.calc_general("2026-09", [
        {"business_type": "服务", "amount": 2.50, "rate": 0.09},
    ], input_vat=0)
    assert calc["details"][0]["output_vat"] == 0.23
    assert calc["output_vat"] == 0.23


def test_vat_small_scale_multiplies_decimal_inputs_before_rounding(monkeypatch):
    monkeypatch.setattr(vat, "QUARTERLY_EXEMPT", 0)
    calc = vat.calc_small_scale(
        "2026-09", sales_amount=30_002.50, levy_rate=0.03, filing_period="quarter",
    )
    assert calc["payable"] == 900.08


def test_hr_and_finance_iit_normalize_cent_base_with_social_deduction(client):
    created = client.post("/api/business/hr/employees", json={
        "emp_no": "E-IIT-SOCIAL", "name": "合成员工",
        "hire_date": "2026-01-01", "base_salary": 8_192.05,
        "social_base": 3_158.55,
    })
    assert created.status_code == 201, created.text
    payroll = client.post("/api/business/hr/payroll/generate", json={
        "month": "2026-01", "social_rate": 1, "housing_rate": 0,
    })
    assert payroll.status_code == 201, payroll.text
    hr_tax = payroll.json()["items"][0]["tax"]

    filing = iit.calc_filing("2026", [{
        "month": 1, "name": "合成员工", "salary": 8_192.05,
        "social_insurance": 3_158.55,
    }])
    month = filing["employees"][0]["months"][0]
    assert month["cumulative_taxable"] == 33.50
    assert (hr_tax, month["monthly_withholding"]) == (1.01, 1.01)


def test_cumulative_withholding_sub_cent_no_refund_is_positive_zero():
    import math

    from app.finance_iit_common import calc_cumulative_withholding

    result = calc_cumulative_withholding(10_000.20, 0.01, 2)
    assert result["monthly_withholding"] == 0.0
    assert math.copysign(1.0, result["monthly_withholding"]) == 1.0
