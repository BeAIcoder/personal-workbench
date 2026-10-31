# -*- coding: utf-8 -*-
"""企业所得税申报单位契约回归（P0，2026-10-31）。

契约：`TaxReturnCreate` 与前端表单入参为**万元**；`mod_tax_return` 落库与
A200000 导出为**元**（申报表口径）。旧实现把万元入参当元再除 10000，
导致小微误判与税额量级错误，本文件锁定修复后的边界。

全部使用合成数据；client 由 conftest 提供（临时 SQLite，不写真实 backend/data）。
"""
import pytest

from app.modules.finance_tax.models import Enterprise, TaxReturn
from app.modules.finance_tax.routers import tax_returns as tr

P = "/api/finance/tax"


def _create_enterprise(client, tax_number, *, employee_count=50, total_assets=100.0):
    r = client.post(f"{P}/enterprises/", json={
        "name": "单位契约测试企业",
        "tax_number": tax_number,
        "industry_type": "一般行业",
        "employee_count": employee_count,
        "total_assets": total_assets,
        "is_restricted_industry": False,
    })
    assert r.status_code == 200, r.text
    return r.json()


def test_wan_input_saved_as_yuan(client):
    """输入 100 万元 → 落库 1,000,000 元；小微 5% → 50,000 元。"""
    ent = _create_enterprise(client, "91110108UNIT0001")
    r = client.post(f"{P}/tax-returns/", json={
        "enterprise_id": ent["id"], "tax_period": "2026", "report_type": "A类",
        "total_revenue": 100, "total_costs": 0, "total_expenses": 0,
    })
    assert r.status_code in (200, 201), r.text
    body = r.json()
    assert body["taxable_income"] == pytest.approx(1_000_000.0)
    assert body["tax_amount"] == pytest.approx(50_000.0)


def test_small_micro_boundary_300_wan(client):
    """300 万含等号仍小微（5%）；300.01 万按 25%。"""
    ent = _create_enterprise(client, "91110108UNIT0002")
    at_limit = client.post(f"{P}/tax-returns/", json={
        "enterprise_id": ent["id"], "tax_period": "2026", "report_type": "A类",
        "total_revenue": 300, "total_costs": 0, "total_expenses": 0,
    })
    assert at_limit.status_code in (200, 201), at_limit.text
    assert at_limit.json()["tax_amount"] == pytest.approx(150_000.0)

    ent2 = _create_enterprise(client, "91110108UNIT0003")
    over = client.post(f"{P}/tax-returns/", json={
        "enterprise_id": ent2["id"], "tax_period": "2026", "report_type": "A类",
        "total_revenue": 300.01, "total_costs": 0, "total_expenses": 0,
    })
    assert over.status_code in (200, 201), over.text
    assert over.json()["tax_amount"] == pytest.approx(750_025.0)


def test_a200000_export_uses_yuan():
    """A200000「金额单位：元」：100 万元所得 → 利润总额 1,000,000、应补 50,000。"""
    ent = Enterprise(
        id=1, name="单位契约测试企业", tax_number="X", industry_type="一般行业",
        total_assets=100.0, employee_count=50, is_restricted_industry=False,
    )
    ret = TaxReturn(
        id=1, enterprise_id=1, year=2026, quarter=None, return_type="年度",
        status="draft", taxable_income=1_000_000.0, tax_amount=50_000.0,
    )
    form = tr._build_a200000_form_data(ret, ent)
    assert form.total_profit == pytest.approx(1_000_000.0)
    assert form.tax_to_pay_or_refund == pytest.approx(50_000.0)


def test_calculate_accepts_year_alias_and_rejects_expired_policy(client):
    """前端传 year（而非 tax_year）也必须触发政策有效期校验（2028 → 400）。"""
    ent = _create_enterprise(client, "91110108UNIT0004", employee_count=12, total_assets=800.0)
    payload = {
        "enterprise_id": ent["id"],
        "revenue": 300.0, "total_costs": 150.0, "total_expenses": 50.0,
        "employee_count": 12, "total_assets": 800.0,
    }
    ok = client.post(f"{P}/tax-returns/calculate", json={**payload, "year": "2026"})
    assert ok.status_code == 200, ok.text

    expired = client.post(f"{P}/tax-returns/calculate", json={**payload, "year": "2028"})
    assert expired.status_code == 400
    assert "2027-12-31" in expired.json()["detail"]


def test_calculate_excludes_expired_prior_losses(client):
    """2019 年亏损在 2026 年申报已过期不得弥补；2021 年亏损可弥补 100 万。"""
    ent = _create_enterprise(client, "91110108UNIT0005", employee_count=12, total_assets=800.0)
    payload = {
        "enterprise_id": ent["id"], "year": "2026",
        "revenue": 100.0, "total_costs": 0.0, "total_expenses": 0.0,
        "employee_count": 12, "total_assets": 800.0,
    }
    expired = client.post(
        f"{P}/tax-returns/calculate", json={**payload, "prior_year_losses": {"2019": 400}}
    )
    assert expired.status_code == 200, expired.text
    assert expired.json()["loss_carry_forward_used_wan"] == pytest.approx(0.0)

    valid = client.post(
        f"{P}/tax-returns/calculate", json={**payload, "prior_year_losses": {"2021": 400}}
    )
    assert valid.status_code == 200, valid.text
    assert valid.json()["loss_carry_forward_used_wan"] == pytest.approx(100.0)
