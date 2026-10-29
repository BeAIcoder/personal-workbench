# -*- coding: utf-8 -*-
"""税法政策有效期与公告号登记（Part B）。"""
from __future__ import annotations

import pytest

from app.modules.finance_tax.config.policy_validity import (
    POLICY_REGISTRY,
    policy_status,
)
from app.modules.finance_tax.config.tax_rates import (
    ADDITIONAL_TAX_RATES,
    CORPORATE_INCOME_TAX_RATES,
    VALUE_ADDED_TAX_RATES,
)
from app.modules.finance_tax.config.tax_rules import RD_EXPENSE_SUPER_DEDUCTION_RULES
from app.modules.finance_tax.services.corporate_income_tax_calculator import (
    CorporateIncomeTaxCalculator,
    EnterpriseTaxType,
)


def test_policy_registry_covers_all_keys():
    expected = {
        "small_micro_5pct",
        "six_tax_two_fee_half",
        "vat_small_scale_1pct",
        "vat_small_scale_exempt10w",
        "rd_deduction_100",
        "rd_deduction_120",
        "disability_fund_exemption",
        "disability_fund_tiered",
    }
    assert expected.issubset(set(POLICY_REGISTRY))


def test_policy_registry_dates_valid():
    from datetime import date

    for key, pv in POLICY_REGISTRY.items():
        assert pv.policy_basis, f"{key} 缺 policy_basis"
        assert pv.effective_date
        date.fromisoformat(pv.effective_date)
        if pv.expires_date:
            date.fromisoformat(pv.expires_date)


def test_policy_status_three_states():
    assert policy_status("small_micro_5pct", "2026")["status"] == "valid"
    assert policy_status("small_micro_5pct", "2028")["status"] == "expired"
    assert policy_status("small_micro_5pct", "2022")["status"] == "not_started"


def test_policy_status_valid_2026_message_has_basis():
    st = policy_status("small_micro_5pct", "2026")
    assert st["valid"] is True
    assert "12号" in st["policy_basis"]


def test_policy_status_expired_message_has_date():
    st = policy_status("small_micro_5pct", "2028")
    assert st["valid"] is False
    assert "2027-12-31" in st["message"]


def test_policy_status_long_term_no_expiry():
    st = policy_status("rd_deduction_100", "2026")
    assert st["valid"] is True
    assert st["expires_date"] is None


def test_policy_status_unknown_key():
    st = policy_status("不存在的政策", "2026")
    assert st["valid"] is False
    assert "未登记" in st["message"]


# ---------- 税率项政策元数据 ----------

def test_tax_rate_items_have_policy_basis():
    items = (
        CORPORATE_INCOME_TAX_RATES.rates
        + VALUE_ADDED_TAX_RATES.rates
        + ADDITIONAL_TAX_RATES.rates
    )
    for item in items:
        assert getattr(item, "policy_basis", "") != "", f"{item.name} 缺 policy_basis"


def test_rd_rules_have_policy_metadata():
    for rule in RD_EXPENSE_SUPER_DEDUCTION_RULES:
        assert rule.policy_basis
        if rule.deduction_rate == 1.2:
            assert rule.expires_date == "2027-12-31"
        else:
            assert rule.expires_date is None


# ---------- 企业所得税计算接有效期 ----------

def test_small_micro_valid_year_applies_5pct_with_basis():
    small = CorporateIncomeTaxCalculator.check_small_micro_enterprise(
        employee_count=12, total_assets_wan=800.0, taxable_income_wan=20.0,
        is_restricted_industry=False)
    assert small["qualified"] is True
    etype = CorporateIncomeTaxCalculator.determine_enterprise_tax_type(False, small)
    res = CorporateIncomeTaxCalculator.calculate_tax_amount(20.0, etype, tax_year="2026")
    assert res["applicable_rate"] == 0.05
    assert "2023年第12号" in res["reason"]
    assert res["policy_status"] == "valid"


def test_small_micro_expired_year_rejects_with_chinese_message():
    small = CorporateIncomeTaxCalculator.check_small_micro_enterprise(12, 800.0, 20.0, False)
    etype = CorporateIncomeTaxCalculator.determine_enterprise_tax_type(False, small)
    with pytest.raises(ValueError) as exc_info:
        CorporateIncomeTaxCalculator.calculate_tax_amount(20.0, etype, tax_year="2028")
    msg = str(exc_info.value)
    assert "2027-12-31" in msg
    assert "12号" in msg


def test_no_tax_year_keeps_backward_compat():
    small = CorporateIncomeTaxCalculator.check_small_micro_enterprise(12, 800.0, 20.0, False)
    etype = CorporateIncomeTaxCalculator.determine_enterprise_tax_type(False, small)
    res = CorporateIncomeTaxCalculator.calculate_tax_amount(20.0, etype)
    assert res["applicable_rate"] == 0.05
    assert res.get("policy_status", "") == ""


def test_rd_rule_selection_by_enterprise_type():
    from app.modules.finance_tax.config.tax_rules import get_rd_deduction_rate
    assert get_rd_deduction_rate("一般企业") == 1.0
    assert get_rd_deduction_rate("集成电路企业") == 1.2


# ---------- 路由层接线（tax_year → 400/200） ----------

def _create_client_enterprise(client):
    r = client.post("/api/finance/tax/enterprises/", json={
        "name": "政策校验测试企业", "tax_number": "91110108POLICY001",
        "industry_type": "软件", "employee_count": 5, "total_assets": 200.0})
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


def test_api_calculate_with_tax_year_valid(client):
    eid = _create_client_enterprise(client)
    r = client.post("/api/finance/tax/tax-returns/calculate", json={
        "enterprise_id": eid, "tax_year": "2026",
        "revenue": 300.0, "total_costs": 150.0, "total_expenses": 50.0,
        "employee_count": 12, "total_assets": 800.0})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["success"] is True
    assert body["policy_status"] == "valid"


def test_api_calculate_with_tax_year_expired(client):
    eid = _create_client_enterprise(client)
    r = client.post("/api/finance/tax/tax-returns/calculate", json={
        "enterprise_id": eid, "tax_year": "2028",
        "revenue": 300.0, "total_costs": 150.0, "total_expenses": 50.0,
        "employee_count": 12, "total_assets": 800.0})
    assert r.status_code == 400
    assert "2027-12-31" in r.json()["detail"]
    assert "12号" in r.json()["detail"]
