# -*- coding: utf-8 -*-
"""BUGfix.md P0/P1 回归（2026-10-30）。

覆盖：
- TAX-01 企业所得税保存走引擎税率，不再使用 5%/10%/25% 自造阶梯
- TAX-02 小微判定含 300 万所得与限制行业，字段缺失不臆断
- TAX-06 增值税进项转出符号（销项 - 进项 + 转出）
- FIL-01 残保金 <1% 按 90% 缴纳
- FIL-02 个税累计预扣法不再逐月截断
- FE-01 企业档案 employee_count/total_assets/is_restricted_industry 可落库

全部使用合成数据；client 由 conftest 提供（临时 SQLite，不写真实 backend/data）。
"""
import pytest

from app.modules.finance_dsf import service as dsf
from app.modules.finance_iit import service as iit
from app.modules.finance_tax.models import Enterprise
from app.modules.finance_tax.routers import tax_returns as tr
from app.modules.finance_tax.services.value_added_tax_calculator import (
    BusinessType,
    InputInvoiceItem,
    SalesItem,
    VATRate,
    ValueAddedTaxCalculator,
)

P = "/api/finance/tax"


def _create_enterprise(client, tax_number: str, *, employee_count, total_assets,
                       is_restricted_industry=False, industry_type="一般行业"):
    r = client.post(f"{P}/enterprises/", json={
        "name": "回归测试企业",
        "tax_number": tax_number,
        "industry_type": industry_type,
        "employee_count": employee_count,
        "total_assets": total_assets,
        "is_restricted_industry": is_restricted_industry,
    })
    assert r.status_code == 200, r.text
    return r.json()


# ---------- TAX-01 ----------

def test_tax_return_small_micro_uses_5pct(client):
    """50 人 / 100 万资产 / 100 万所得 → 小微 5%，落库 50,000。"""
    ent = _create_enterprise(client, "91110108P0TEST0001", employee_count=50, total_assets=100.0)
    r = client.post(f"{P}/tax-returns/", json={
        "enterprise_id": ent["id"], "tax_period": "2026", "report_type": "A类",
        "total_revenue": 1_000_000, "total_costs": 0, "total_expenses": 0,
    })
    assert r.status_code in (200, 201), r.text
    assert r.json()["tax_amount"] == pytest.approx(50_000.0)


def test_tax_return_non_small_micro_has_no_10pct_bracket(client):
    """400 人 → 非小微；500 万所得必须按 25% = 1,250,000，而不是旧 10% = 500,000。"""
    ent = _create_enterprise(client, "91110108P0TEST0002", employee_count=400, total_assets=100.0)
    r = client.post(f"{P}/tax-returns/", json={
        "enterprise_id": ent["id"], "tax_period": "2026", "report_type": "A类",
        "total_revenue": 5_000_000, "total_costs": 0, "total_expenses": 0,
    })
    assert r.status_code in (200, 201), r.text
    assert r.json()["tax_amount"] == pytest.approx(1_250_000.0)


def test_tax_return_missing_headcount_defaults_to_standard_rate(client):
    """未填人数/资产时不臆断小微：按 25% 计算。"""
    r = client.post(f"{P}/enterprises/", json={
        "name": "回归测试企业", "tax_number": "91110108P0TEST0003",
    })
    assert r.status_code == 200, r.text
    ent = r.json()
    assert ent["employee_count"] is None and ent["total_assets"] is None

    r = client.post(f"{P}/tax-returns/", json={
        "enterprise_id": ent["id"], "tax_period": "2026", "report_type": "A类",
        "total_revenue": 1_000_000, "total_costs": 0, "total_expenses": 0,
    })
    assert r.status_code in (200, 201), r.text
    assert r.json()["tax_amount"] == pytest.approx(250_000.0)


# ---------- TAX-02 ----------

def test_is_small_micro_checks_taxable_income_and_restricted_industry():
    base = dict(name="x", tax_number="y", employee_count=50, total_assets=100.0,
                is_restricted_industry=False, industry_type="一般行业")
    assert tr._is_small_micro(Enterprise(**base), 1_000_000) is True
    # 应纳税所得额超过 300 万：不符合
    assert tr._is_small_micro(Enterprise(**base), 4_000_000) is False
    # 限制行业：不符合
    assert tr._is_small_micro(
        Enterprise(**{**base, "is_restricted_industry": True}), 1_000_000
    ) is False
    # 字段缺失：无法判定，不臆断
    assert tr._is_small_micro(
        Enterprise(**{**base, "employee_count": None}), 1_000_000
    ) is False


# ---------- TAX-06 ----------

def test_vat_input_tax_transferred_out_is_added_back():
    result = ValueAddedTaxCalculator.calculate_general_taxpayer(
        sales_items=[SalesItem(description="货物销售", amount=113.0,
                               business_type=BusinessType.SALES_OF_GOODS)],
        input_invoices=[InputInvoiceItem(
            description="办公用品", invoice_amount=0.0, tax_amount=0.0,
            vat_rate=VATRate.RATE_13, is_deductible=True, non_deductible_reason="",
        )],
        input_tax_transferred_out=10.0,
    )
    assert result.output_tax == pytest.approx(13.0)
    # 销项 13 - 进项 0 + 转出 10 = 23（旧实现为 3）
    assert result.vat_payable == pytest.approx(23.0)


# ---------- FIL-01 ----------

def test_dsf_low_ratio_pays_90_percent():
    calc = dsf.calc_dsf("2026", 100, 10_000_000, 0)
    assert calc["payable"] == pytest.approx(135_000.0)
    assert any("90%" in w for w in calc["warnings"])


def test_dsf_wage_cap_uses_local_average():
    """财税〔2015〕98 号：年平均工资超过当地社平 2 倍的按 2 倍封顶。"""
    # 100 人、工资 3000 万 → 年均 30 万；当地社平 8 万 → 封顶 16 万
    calc = dsf.calc_dsf("2026", 100, 30_000_000, 0, local_avg_wage=80_000)
    assert calc["wage_cap"] == pytest.approx(160_000.0)
    assert calc["charge_wage"] == pytest.approx(160_000.0)
    # 差额 1.5 人 × 16 万 = 24 万；<1% → 按 90% = 21.6 万
    assert calc["base_amount"] == pytest.approx(240_000.0)
    assert calc["payable"] == pytest.approx(216_000.0)
    assert any("封顶" in w for w in calc["warnings"])

    # 未传社平：不封锁计算，但必须提示未校验上限
    no_cap = dsf.calc_dsf("2026", 100, 30_000_000, 0)
    assert any("未校验" in w for w in no_cap["warnings"])


# ---------- FIL-02 ----------

def test_iit_cumulative_allows_negative_month_to_offset():
    calc = iit.calc_filing("2026", [
        {"month": 1, "name": "员工甲", "salary": 20_000},
        {"month": 2, "name": "员工甲", "salary": 3_000},
    ])
    months = calc["employees"][0]["months"]
    # 1 月：15000 × 3% = 450
    assert months[0]["monthly_withholding"] == pytest.approx(450.0)
    # 2 月：累计 13000 × 3% = 390，已预扣 450 → 本期 0（暂不退税）
    assert months[1]["cumulative_taxable"] == pytest.approx(13_000.0)
    assert months[1]["monthly_withholding"] == 0.0
    assert calc["totals"]["tax_withheld"] == pytest.approx(450.0)


# ---------- FE-01 ----------

def test_enterprise_profile_fields_are_persisted(client):
    ent = _create_enterprise(client, "91110108P0TEST0004", employee_count=120,
                             total_assets=800.5, is_restricted_industry=True,
                             industry_type="餐饮")
    got = client.get(f"{P}/enterprises/{ent['id']}").json()
    assert got["employee_count"] == 120
    assert got["total_assets"] == pytest.approx(800.5)
    assert got["is_restricted_industry"] is True
    assert got["industry_type"] == "餐饮"

    r = client.put(f"{P}/enterprises/{ent['id']}", json={
        "employee_count": 90, "industry_type": "零售",
    })
    assert r.status_code == 200, r.text
    got = client.get(f"{P}/enterprises/{ent['id']}").json()
    assert got["employee_count"] == 90
    assert got["industry_type"] == "零售"
