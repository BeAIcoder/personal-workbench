# -*- coding: utf-8 -*-
"""准入制机制化回归（2026-10-31）。

覆盖：
- 税种准入清单：admitted / blocked 可查询，blocked 附原因，未登记 404
- require_tax_scope 门禁：未准入抛中文 ValueError
- 税率版本化：所属年度早于税率登记生效日 → 400，不用现行税率倒推历史
- GM 指标字典补齐 hr.payroll_count（caveat 指向的字段可见）

全部使用合成数据；client 由 conftest 提供（临时 SQLite，不写真实 backend/data）。
"""
import pytest

from app.modules.finance_tax.config.tax_scope import (
    list_tax_scopes,
    require_tax_scope,
    tax_scope_status,
)

P = "/api/finance/tax"


def test_scope_list_endpoint(client):
    resp = client.get(f"{P}/scope")
    assert resp.status_code == 200, resp.text
    rows = {row["key"]: row for row in resp.json()["tax_scopes"]}
    assert rows["corporate_income_tax"]["status"] == "admitted"
    assert rows["property_tax"]["status"] == "blocked"
    assert rows["property_tax"]["reason"]  # 未准入必须给原因


def test_scope_single_and_unknown(client):
    ok = client.get(f"{P}/scope/property_tax")
    assert ok.status_code == 200
    assert ok.json()["status"] == "blocked"
    assert client.get(f"{P}/scope/not_a_tax").status_code == 404


def test_require_tax_scope_blocks_unadmitted():
    with pytest.raises(ValueError) as exc:
        require_tax_scope("urban_land_use_tax")
    assert "尚未准入" in str(exc.value)

    require_tax_scope("corporate_income_tax")  # 已准入不抛


def test_unregistered_tax_is_blocked_not_usable():
    status = tax_scope_status("some_future_tax")
    assert status["status"] == "blocked"
    assert "未登记" in status["reason"]


def test_rate_versioning_rejects_pre_2008_standard_rate(client):
    """所属年度早于企业所得税法施行（2008-01-01）→ 400，不静默套用 25%。"""
    ent = client.post(f"{P}/enterprises/", json={
        "name": "税率版本测试企业", "tax_number": "91110108VER0001",
        "industry_type": "一般行业", "employee_count": 400,
        "total_assets": 8000.0, "is_restricted_industry": False,
    }).json()
    payload = {
        "enterprise_id": ent["id"],
        "revenue": 500.0, "total_costs": 0.0, "total_expenses": 0.0,
        "employee_count": 400, "total_assets": 8000.0,
    }
    old = client.post(f"{P}/tax-returns/calculate", json={**payload, "year": "2007"})
    assert old.status_code == 400, old.text
    assert "2008-01-01" in old.json()["detail"]

    now = client.post(f"{P}/tax-returns/calculate", json={**payload, "year": "2026"})
    assert now.status_code == 200, now.text
    assert now.json()["applicable_tax_rate"] == pytest.approx(0.25)


def test_gm_dictionary_includes_payroll_count(client):
    body = client.get("/api/business/gm/summary", params={"period": "2026-01"}).json()
    assert "hr.payroll_count" in body["metric_dictionary"]
    emitted = {item["key"] for item in body["metrics"]}
    assert "hr.payroll_count" in emitted
