# -*- coding: utf-8 -*-
"""business_gm 总经理只读经营聚合测试（合成数据，临时库）。"""
from datetime import date
from types import SimpleNamespace

import pytest

from app.database import SessionLocal
from app.modules import get_loaded_modules
from app.modules.business_hr.service import hr_summary_tool
from app.modules.business_operations.service import ops_summary_tool

GM = "/api/business/gm"
OPS = "/api/business/operations"
NO_SOURCE_KEYS = {"operations.noi", "operations.sales_efficiency"}


def _contract(client, no, *, area=100, end_date="2027-12-31", status="active"):
    response = client.post(f"{OPS}/contracts", json={
        "contract_no": no,
        "tenant": "合成商户",
        "unit_no": "A-101",
        "area": area,
        "start_date": "2025-01-01",
        "end_date": end_date,
        "rent_monthly": 100,
        "property_fee_monthly": 0,
        "status": status,
    })
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _bill(client, contract_id, period, paid=0):
    response = client.post(
        f"{OPS}/contracts/{contract_id}/bills", json={"period": period},
    )
    assert response.status_code == 201, response.text
    bill_id = response.json()["id"]
    if paid:
        response = client.post(f"{OPS}/bills/{bill_id}/payments", json={"amount": paid})
        assert response.status_code == 200, response.text


def _campaign(client, name, year, budget):
    response = client.post("/api/business/planning/campaigns", json={
        "name": name,
        "year": year,
        "start_date": f"{year}-01-01",
        "end_date": f"{year}-01-02",
        "status": "done",
        "budget_items": [{"item": "合成物料", "budget": budget, "actual": budget}],
    })
    assert response.status_code == 201, response.text


def test_empty_database_returns_all_domains_and_honest_gaps(client):
    response = client.get(f"{GM}/summary", params={"period": "2026-01"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body["domains"]) == {"leasing", "operations", "planning", "legal", "hr", "facility"}
    for key in ("leasing", "operations", "planning", "legal", "hr"):
        assert body["domains"][key]["available"] is True
        assert isinstance(body["domains"][key]["data"], dict)
    facility = body["domains"]["facility"]
    assert facility["available"] is True
    assert facility["data"]["equipment_count"] == 0
    assert facility["data"]["compliance_total"] == 0
    assert facility["gap_reasons"] == []
    assert body["domains"]["operations"]["data"]["expiry_12m"]["area_ratio"] is None
    assert any("总面积为 0" in reason for reason in body["domains"]["operations"]["gap_reasons"])

    metrics = {item["key"]: item for item in body["metrics"]}
    for key in NO_SOURCE_KEYS:
        assert metrics[key]["value"] is None
        assert metrics[key]["reason"]
    assert body["reminders"] == []


def test_one_domain_failure_does_not_unload_gm_or_turn_into_zero(client, monkeypatch):
    from app.modules.business_gm import service as gm_service

    real_import = gm_service.importlib.import_module

    def fail_planning(name, *args, **kwargs):
        if name == "app.modules.business_planning.service":
            raise RuntimeError("synthetic planning outage")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(gm_service.importlib, "import_module", fail_planning)
    response = client.get(f"{GM}/summary", params={"period": "2026-01"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["domains"]["leasing"]["available"] is True
    assert body["domains"]["operations"]["available"] is True
    planning = body["domains"]["planning"]
    assert planning["available"] is False
    assert planning["data"] is None
    assert planning["gap_reasons"]
    planning_metrics = [
        item for item in body["metrics"]
        if item["domain"] == "planning" and item["key"] in body["metric_dictionary"]
    ]
    assert planning_metrics
    assert all(item["value"] is None and item["reason"] for item in planning_metrics)


def test_metric_dictionary_has_no_drift_with_emitted_metrics(client):
    response = client.get(f"{GM}/summary", params={"period": "2026-01"})
    assert response.status_code == 200, response.text
    body = response.json()
    required = {"label", "formula", "grain", "window", "source", "population", "caveats"}
    dictionary = body["metric_dictionary"]
    assert dictionary
    assert all(required <= set(definition) for definition in dictionary.values())
    emitted = {item["key"] for item in body["metrics"]} - NO_SOURCE_KEYS
    assert emitted == set(dictionary)
    assert NO_SOURCE_KEYS.isdisjoint(dictionary)
    assert all(item["value"] is None and item["reason"] for item in body["metrics"]
               if item["key"] in NO_SOURCE_KEYS)


def test_operations_and_planning_reconcile_with_filtered_sources(client):
    contract_id = _contract(client, "HT-GM-RECON")
    _bill(client, contract_id, "2026-01", paid=25)
    _bill(client, contract_id, "2026-02", paid=100)
    _campaign(client, "合成 2025 活动", 2025, 50)
    _campaign(client, "合成 2026 活动", 2026, 80)

    response = client.get(f"{GM}/summary", params={"period": "2026-01"})
    assert response.status_code == 200, response.text
    domains = response.json()["domains"]
    bills = client.get(f"{OPS}/bills", params={
        "period": "2026-01", "page_size": 200,
    }).json()["items"]
    operations = domains["operations"]["data"]
    assert operations["receivable"] == pytest.approx(sum(row["total_amount"] for row in bills))
    assert operations["received"] == pytest.approx(sum(row["paid_amount"] for row in bills))
    assert operations["arrears"] == pytest.approx(sum(row["arrears"] for row in bills))
    source_summary = client.get(f"{OPS}/summary", params={"period": "2026-01"}).json()
    for key in ("receivable", "received", "collection_rate", "arrears", "arrears_contracts"):
        assert operations[key] == source_summary[key]

    campaigns = client.get("/api/business/planning/campaigns", params={
        "year": 2026, "page_size": 200,
    }).json()["items"]
    planning = domains["planning"]["data"]
    assert planning["year"] == 2026
    assert planning["campaigns"] == len(campaigns) == 1
    assert planning["budget_total"] == pytest.approx(sum(row["budget_total"] for row in campaigns))


def test_due_followups_count_is_not_capped_by_due_list_limit(client):
    from app.modules.business_leasing.models import LeasingLead
    from app.modules.business_leasing.service import leads_due, leasing_summary

    today = date.today()
    rows = [
        LeasingLead(company=f"合成到期线索 {index:03d}", stage="lead", next_action_at=today)
        for index in range(105)
    ]
    rows.extend([
        LeasingLead(company="合成已签约", stage="signed", next_action_at=today),
        LeasingLead(company="合成已流失", stage="lost", next_action_at=today),
        LeasingLead(company="合成无跟进日期", stage="lead", next_action_at=None),
    ])
    with SessionLocal() as db:
        db.add_all(rows)
        db.commit()

    response = client.get(f"{GM}/summary", params={"period": "2026-01"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["domains"]["leasing"]["data"]["due_followups"] == 105
    metric = next(item for item in body["metrics"] if item["key"] == "leasing.due_followups")
    assert metric["value"] == 105
    assert body["metric_dictionary"]["leasing.due_followups"]["source"] == (
        "business_leasing.service.leads_due_count"
    )
    assert leasing_summary()["due_followups"] == 105
    with SessionLocal() as db:
        assert len(leads_due(db, today=today)) == 100


def test_expiry_concentration_zero_denominator_is_null_not_zero(client):
    _contract(client, "HT-GM-ZERO", area=0, end_date="2027-01-01")
    _contract(client, "HT-GM-TERMINATED", area=500, end_date="2027-01-01", status="terminated")
    from app.modules.business_gm.service import build_summary

    with SessionLocal() as db:
        body = build_summary(db, period="2026-01", as_of=date(2026, 1, 1))
    expiry = body["domains"]["operations"]["data"]["expiry_12m"]
    assert expiry["as_of"] == "2026-01-01"
    assert expiry["window_end"] == "2027-01-01"
    assert expiry["active_area"] == 0
    assert expiry["expiring_area"] == 0
    assert expiry["area_ratio"] is None
    assert any("总面积为 0" in reason for reason in body["domains"]["operations"]["gap_reasons"])
    assert not any(item["code"] == "operations.expiry_concentration" for item in body["reminders"])


@pytest.mark.parametrize(("expiring_area", "expected_ratio", "warns"), [
    (100, 0.25, False),
    (101, 0.2525, True),
])
def test_expiry_concentration_warning_threshold_and_fixed_as_of(
    client, expiring_area, expected_ratio, warns,
):
    _contract(client, "HT-GM-EXPIRING", area=expiring_area, end_date="2027-01-01")
    _contract(client, "HT-GM-LATER", area=400 - expiring_area, end_date="2027-01-02")
    _contract(client, "HT-GM-EXCLUDED", area=900, end_date="2027-01-01", status="terminated")
    from app.modules.business_gm.service import build_summary

    with SessionLocal() as db:
        body = build_summary(db, period="2026-01", as_of=date(2026, 1, 1))
    expiry = body["domains"]["operations"]["data"]["expiry_12m"]
    assert expiry["active_area"] == 400
    assert expiry["expiring_area"] == expiring_area
    assert expiry["area_ratio"] == expected_ratio
    reminders = [item for item in body["reminders"] if item["code"] == "operations.expiry_concentration"]
    assert bool(reminders) is warns
    if warns:
        assert reminders[0]["level"] == "warning"
        assert "25" in reminders[0]["message"]


def test_facility_summary_uses_gm_as_of(client):
    from datetime import date as date_cls
    from app.database import SessionLocal
    from app.modules.business_gm.service import build_summary

    eq = client.post("/api/business/facility/equipment", json={
        "code": "EQ-GM-ASOF", "name": "合成设备", "category": "A",
    }).json()
    created = client.post("/api/business/facility/compliance", json={
        "equipment_id": eq["id"], "kind": "fire", "title": "合成消防检查",
        "due_date": "2026-01-10",
    })
    assert created.status_code == 201, created.text

    with SessionLocal() as db:
        body = build_summary(db, "2026-01", as_of=date_cls(2026, 1, 5))
    facility = body["domains"]["facility"]["data"]
    assert facility["as_of"] == "2026-01-05"
    assert facility["overdue"] == 0
    assert facility["compliance_30"] == 1


def test_facility_adapter_can_pick_up_later_summary(client, monkeypatch):
    from app.modules.business_gm import service as gm_service

    real_import = gm_service.importlib.import_module

    def with_facility(name, *args, **kwargs):
        if name == "app.modules.business_facility.service":
            return SimpleNamespace(facility_summary=lambda db, as_of=None: {
                "equipment_count": 2, "category_a": 1, "compliance_30": 0, "overdue": 0,
            })
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(gm_service.importlib, "import_module", with_facility)
    response = client.get(f"{GM}/summary", params={"period": "2026-01"})
    assert response.status_code == 200, response.text
    facility = response.json()["domains"]["facility"]
    assert facility == {"available": True, "data": {
        "equipment_count": 2, "category_a": 1, "compliance_30": 0, "overdue": 0,
    }, "gap_reasons": []}


def test_ops_and_hr_tools_optional_period_preserve_no_argument_behavior(client):
    contract_id = _contract(client, "HT-GM-TOOLS")
    _bill(client, contract_id, "2026-01")
    _bill(client, contract_id, "2026-02")
    assert ops_summary_tool()["receivable"] == 200
    assert ops_summary_tool(period="2026-01")["receivable"] == 100
    with pytest.raises(ValueError, match="所属期格式非法"):
        ops_summary_tool(period="2026-13")

    employee = client.post("/api/business/hr/employees", json={
        "emp_no": "E-GM-1", "name": "合成员工", "hire_date": "2025-01-01",
        "base_salary": 10000, "social_base": 10000,
    })
    assert employee.status_code == 201, employee.text
    generated = client.post("/api/business/hr/payroll/generate", json={"month": "2026-01"})
    assert generated.status_code == 201, generated.text
    assert hr_summary_tool()["month"] == ""
    assert hr_summary_tool()["payroll_count"] == 0
    assert hr_summary_tool(month="2026-01")["payroll_count"] == 1
    with pytest.raises(ValueError, match="月份格式非法"):
        hr_summary_tool(month="2026-13")


def test_gm_manifest_endpoint_and_tool_are_readonly_and_preset(client):
    from app.modules.business_gm.service import gm_summary

    loaded = {item.name: item for item in get_loaded_modules()}
    manifest = loaded["business_gm"]
    assert manifest.tables == []
    assert manifest.preset_experts == ["realestate"]
    tool = next(item for item in manifest.agent_tools if item["name"] == "gm_summary")
    assert tool["readonly"] is True
    assert tool["required"] == []
    assert tool["params"]["period"][0] == "string"
    assert tool["params"]["period"][2] is False
    assert client.get(f"{GM}/summary", params={"period": "2026-01"}).status_code == 200
    assert client.post(f"{GM}/summary", json={}).status_code == 405
    result = gm_summary(period="2026-01")
    assert result["ok"] is True
    assert result["period"] == "2026-01"
    assert client.get(f"{GM}/summary", params={"period": "2026-13"}).status_code == 422
