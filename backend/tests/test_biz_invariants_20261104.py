# -*- coding: utf-8 -*-
"""经营/职能模块数据不变量回归（2026-11-04 Task 6）。

覆盖台账 BIZ L-100~L-112：
- HR 专项附加扣除不再被编辑清 0、已确认薪资阻止员工删除、缺月出薪告警、0 社保基数可表达；
- 合同 unit_id 始终以招商台账为准、删铺位/删合同的前置守卫、租决按租户匹配；
- 合同终止在同一事务释放铺位与旧签约占用，新租户可再签；
- GM period 缺省当月、DQ 严格日期校验、招商内部不变量巡检与真实问题计数；
- 企划年度与档期一致、工单父子循环检测、facility 完成日期校验与导出真值；
- preset_experts 必须可解析到内置专家（全新安装不缺工具）。

全部使用合成数据与临时库；日期类断言用固定 as_of，不读真实 backend/data。
"""
from datetime import date, datetime, timedelta

import pytest

from app.database import SessionLocal
from app.modules import get_loaded_modules
from app.modules.business_facility.models import FacilityCompliance
from app.modules.business_leasing.models import LeasingLead
from app.modules.business_operations.models import OpsContract

OPS = "/api/business/operations"
LEASING = "/api/business/leasing"
HR = "/api/business/hr"
PLAN = "/api/business/planning"
FAC = "/api/business/facility"
GM = "/api/business/gm"
DQ = "/api/business/dq"
AS_OF = "2026-11-01"


# ---------------- 辅助 ----------------

def _employee(client, no="E001", **over):
    body = {
        "emp_no": no, "name": f"合成员工{no}", "department": "营运部", "position": "主管",
        "hire_date": "2026-01-05", "status": "active",
        "base_salary": 10000.0, "social_base": 10000.0,
    }
    body.update(over)
    return client.post(f"{HR}/employees", json=body)


def _unit(client, unit_no="U-1001", area=100.0, status="vacant"):
    resp = client.post(f"{LEASING}/units", json={
        "building": "A栋", "floor": "1F", "unit_no": unit_no,
        "area": area, "status": status,
    })
    assert resp.status_code in (200, 201), resp.text
    return resp.json()["id"]


def _unit_status(client, unit_id):
    for unit in client.get(f"{LEASING}/units", params={"page_size": 200}).json()["items"]:
        if unit["id"] == unit_id:
            return unit["status"]
    raise AssertionError(f"铺位 {unit_id} 不在列表中")


def _contract(client, *, unit_id=None, unit_no="", tenant="合成商户甲", no="HT-001",
              start="2026-01-01", end="2026-12-31", rent=10000.0, property_fee=1200.0):
    body = {
        "contract_no": no, "tenant": tenant, "unit_no": unit_no,
        "area": 100.0, "start_date": start, "end_date": end,
        "rent_monthly": rent, "property_fee_monthly": property_fee, "deposit": 0.0,
    }
    if unit_id is not None:
        body["unit_id"] = unit_id
    return client.post(f"{OPS}/contracts", json=body)


def _bill(client, contract_id, period):
    return client.post(f"{OPS}/contracts/{contract_id}/bills", json={"period": period})


def _signed_lead(unit_id, company="合成签约商户", **over):
    with SessionLocal() as db:
        row = LeasingLead(company=company, stage="signed", unit_id=unit_id, **over)
        db.add(row)
        db.commit()
        return row.id


# ---------------- HR：L-100 / L-101 / L-108 / social_base ----------------

def test_employee_special_additional_survives_position_edit_and_export(client):
    created = _employee(client, special_additional=1500.0)
    assert created.status_code == 201, created.text
    employee_id = created.json()["id"]
    assert created.json()["special_additional"] == pytest.approx(1500.0)

    edited = client.put(f"{HR}/employees/{employee_id}", json={"position": "经理"})
    assert edited.status_code == 200, edited.text
    assert edited.json()["special_additional"] == pytest.approx(1500.0)
    rows = client.get(f"{HR}/employees").json()["items"]
    assert next(r for r in rows if r["id"] == employee_id)["special_additional"] == pytest.approx(1500.0)

    payroll = client.post(f"{HR}/payroll/generate", json={"month": "2026-01"})
    assert payroll.status_code == 201, payroll.text
    # 10000 - 1050 社保 - 700 公积金 - 1500 专项扣除 - 5000 = 1750 → 3% = 52.5
    assert payroll.json()["items"][0]["special_additional"] == pytest.approx(1500.0)
    assert payroll.json()["items"][0]["tax"] == pytest.approx(52.5)

    from io import BytesIO
    from openpyxl import load_workbook

    exported = client.get(f"{HR}/export/employees.xlsx")
    assert exported.status_code == 200 and exported.content[:2] == b"PK"
    sheet = load_workbook(BytesIO(exported.content)).active
    headers = [cell.value for cell in sheet[1]]
    assert "专项附加扣除" in headers
    column = headers.index("专项附加扣除")
    assert sheet.cell(row=2, column=column + 1).value == pytest.approx(1500.0)


def test_employee_delete_blocked_by_confirmed_payroll(client):
    employee_id = _employee(client, no="E101").json()["id"]
    payroll = client.post(f"{HR}/payroll/generate", json={"month": "2026-02"})
    assert payroll.status_code == 201, payroll.text
    payroll_id = payroll.json()["items"][0]["id"]
    assert client.post(f"{HR}/payroll/{payroll_id}/confirm").status_code == 200

    blocked = client.delete(f"{HR}/employees/{employee_id}")
    assert blocked.status_code == 409, blocked.text
    assert "已确认" in blocked.json()["detail"]
    # 员工与薪资记录都还在
    assert client.get(f"{HR}/employees").json()["total"] == 1
    assert len(client.get(f"{HR}/payroll", params={"month": "2026-02"}).json()) == 1

    # 只有草稿薪资的员工可删，且草稿薪资级联删除
    other_id = _employee(client, no="E102").json()["id"]
    deleted = client.delete(f"{HR}/employees/{other_id}")
    assert deleted.status_code == 200, deleted.text
    assert client.get(f"{HR}/employees").json()["total"] == 1


def test_payroll_zero_social_base_is_expressed_not_fallback(client):
    _employee(client, social_base=0, hire_date="2026-05-01")
    payroll = client.post(f"{HR}/payroll/generate", json={"month": "2026-05"})
    assert payroll.status_code == 201, payroll.text
    row = payroll.json()["items"][0]
    assert row["social_insurance"] == pytest.approx(0.0)
    assert row["housing_fund"] == pytest.approx(0.0)
    # 10000 - 5000 = 5000 → 3% = 150
    assert row["tax"] == pytest.approx(150.0)


def test_payroll_generate_warns_when_historical_months_missing(client):
    employee_id = _employee(client, hire_date="2026-01-05").json()["id"]
    assert client.put(f"{HR}/attendance/{employee_id}/2026-01",
                      json={"actual_days": 21.75}).status_code == 200
    first = client.post(f"{HR}/payroll/generate", json={"month": "2026-01"})
    assert first.status_code == 201, first.text
    assert [w for w in first.json()["warnings"] if "未生成" in w] == []

    march = client.post(f"{HR}/payroll/generate", json={"month": "2026-03"})
    assert march.status_code == 201, march.text
    missing = [w for w in march.json()["warnings"] if "未生成" in w]
    assert len(missing) == 1
    assert "2026-02" in missing[0]
    assert "E001" in missing[0]


def test_payroll_generate_warns_when_first_payroll_skips_hire_months(client):
    _employee(client, no="E201", hire_date="2026-01-05")
    june = client.post(f"{HR}/payroll/generate", json={"month": "2026-04"})
    assert june.status_code == 201, june.text
    missing = [w for w in june.json()["warnings"] if "未生成" in w]
    assert len(missing) == 1
    for month in ("2026-01", "2026-02", "2026-03"):
        assert month in missing[0]


# ---------------- 合同/铺位：L-102 / L-103 / L-104 / L-109 / L-111 ----------------

def test_contract_unit_no_always_follows_ledger_unit(client):
    unit_a = _unit(client, "U-A")
    unit_b = _unit(client, "U-B")

    created = _contract(client, unit_id=unit_a, unit_no="手填旧铺位")
    assert created.status_code == 201, created.text
    assert created.json()["unit_no"] == "U-A"

    switched = client.put(
        f"{OPS}/contracts/{created.json()['id']}",
        json={"unit_id": unit_b, "unit_no": "手填仍要覆盖"},
    )
    assert switched.status_code == 200, switched.text
    assert switched.json()["unit_id"] == unit_b
    assert switched.json()["unit_no"] == "U-B"

    unbound = client.put(f"{OPS}/contracts/{created.json()['id']}", json={"unit_id": None})
    assert unbound.status_code == 200, unbound.text
    assert unbound.json()["unit_id"] is None
    assert unbound.json()["unit_no"] == "U-B"


def test_unit_delete_blocked_by_operations_contract(client):
    unit_id = _unit(client, "U-DEL")
    contract = _contract(client, unit_id=unit_id, no="HT-DEL")
    assert contract.status_code == 201, contract.text

    blocked = client.delete(f"{LEASING}/units/{unit_id}")
    assert blocked.status_code == 409, blocked.text
    assert "营运" in blocked.json()["detail"] or "合同" in blocked.json()["detail"]

    assert client.put(f"{OPS}/contracts/{contract.json()['id']}",
                      json={"unit_id": None}).status_code == 200
    assert client.delete(f"{LEASING}/units/{unit_id}").status_code == 200
    # 解除关联后 dq 不再报合同→铺位孤儿
    profile = client.get(f"{DQ}/summary", params={"as_of": AS_OF}).json()
    assert profile["domains"]["operations"]["score"] == 100


def test_contract_delete_blocked_by_work_order(client):
    contract_id = _contract(client, no="HT-WO").json()["id"]
    order = client.post(f"{OPS}/work-orders", json={
        "type": "repair", "description": "空调不制冷", "contract_id": contract_id,
    })
    assert order.status_code == 201, order.text

    blocked = client.delete(f"{OPS}/contracts/{contract_id}")
    assert blocked.status_code == 409, blocked.text
    assert "工单" in blocked.json()["detail"]

    assert client.put(f"{OPS}/work-orders/{order.json()['id']}",
                      json={"contract_id": None}).status_code == 200
    assert client.delete(f"{OPS}/contracts/{contract_id}").status_code == 200


def test_lease_terms_only_apply_to_matching_tenant(client):
    unit_id = _unit(client, "U-TERM")
    _signed_lead(unit_id, company="老租户", free_rent_months=3, escalation_rate=10.0,
                 rent_form="fixed")

    inheritor = _contract(client, unit_id=unit_id, tenant="新租户", no="HT-NEW")
    assert inheritor.status_code == 201, inheritor.text
    bill = _bill(client, inheritor.json()["id"], "2026-01")
    assert bill.status_code == 201, bill.text
    assert bill.json()["rent_amount"] == pytest.approx(10000.0)

    # 同铺位不允许两份生效合同：先终止新租户合同再签老租户合同
    assert client.put(f"{OPS}/contracts/{inheritor.json()['id']}",
                      json={"status": "terminated"}).status_code == 200
    matched = _contract(client, unit_id=unit_id, tenant="老租户", no="HT-OLD",
                        start="2026-02-01", end="2026-12-31")
    assert matched.status_code == 201, matched.text
    free = _bill(client, matched.json()["id"], "2026-02")
    assert free.status_code == 201, free.text
    assert free.json()["rent_amount"] == pytest.approx(0.0)
    assert free.json()["property_amount"] == pytest.approx(1200.0)


def test_contract_termination_releases_unit_and_signed_lead_keeps_history(client):
    unit_id = _unit(client, "U-RELEASE", status="leased")
    lead_id = _signed_lead(unit_id, company="退租商户")
    assert _unit_status(client, unit_id) == "leased"
    contract = _contract(client, unit_id=unit_id, tenant="退租商户", no="HT-REL")
    assert contract.status_code == 201, contract.text

    terminated = client.put(f"/api/business/operations/contracts/{contract.json()['id']}",
                            json={"status": "terminated"})
    assert terminated.status_code == 200, terminated.text
    assert terminated.json()["status"] == "terminated"
    assert _unit_status(client, unit_id) == "vacant"

    # 历史线索保留：stage 仍是 signed，只标记 released_at 解除占用
    history = client.get(f"{LEASING}/leads", params={"keyword": "退租商户"}).json()["items"]
    assert len(history) == 1
    assert history[0]["id"] == lead_id
    assert history[0]["company"] == "退租商户"
    assert history[0]["stage"] == "signed"
    assert history[0]["released_at"] is not None

    # 新租户可再签同一铺位
    newcomer = client.post(f"{LEASING}/leads", json={
        "company": "新签商户", "stage": "signed", "unit_id": unit_id,
    })
    assert newcomer.status_code == 201, newcomer.text
    assert _unit_status(client, unit_id) == "leased"
    new_contract = _contract(client, unit_id=unit_id, tenant="新签商户", no="HT-NEXT")
    assert new_contract.status_code == 201, new_contract.text


def test_termination_keeps_unit_when_another_active_contract_remains(client):
    unit_id = _unit(client, "U-KEEP", status="leased")
    first = _contract(client, unit_id=unit_id, tenant="商户甲", no="HT-K1")
    assert first.status_code == 201, first.text
    # 存量脏数据：同铺位第二份生效合同（写守卫会拦，这里经 ORM 直接落库）
    with SessionLocal() as db:
        db.add(OpsContract(
            contract_no="HT-K2", tenant="商户乙", unit_no="U-KEEP", unit_id=unit_id,
            area=10.0, start_date=date(2026, 1, 1), end_date=date(2026, 12, 31),
            rent_monthly=1.0, property_fee_monthly=0.0, deposit=0.0, status="active",
        ))
        db.commit()

    assert client.put(f"{OPS}/contracts/{first.json()['id']}",
                      json={"status": "terminated"}).status_code == 200
    assert _unit_status(client, unit_id) == "leased"


# ---------------- GM / DQ：L-105 / L-106 / L-107 / L-112 ----------------

def test_gm_summary_without_period_defaults_to_current_month(client):
    response = client.get(f"{GM}/summary")
    assert response.status_code == 200, response.text
    assert response.json()["period"] == date.today().strftime("%Y-%m")
    assert client.get(f"{GM}/summary", params={"period": "2026-13"}).status_code == 422


def test_dq_rejects_impossible_calendar_dates(client):
    for value in ("2026-02-30", "2026-11-31", "2026-13-01"):
        assert client.get(f"{DQ}/summary", params={"as_of": value}).status_code == 422, value
    assert client.get(f"{DQ}/summary", params={"as_of": "2026-02-28"}).status_code == 200


def test_dq_router_maps_profile_value_error_to_422(client, monkeypatch):
    from app.modules.business_dq import router as dq_router

    def boom(db, as_of=""):
        raise ValueError("巡检参数非法")

    monkeypatch.setattr(dq_router, "build_profile", boom)
    response = client.get(f"{DQ}/summary", params={"as_of": AS_OF})
    assert response.status_code == 422
    assert "巡检参数非法" in response.json()["detail"]


def test_dq_flags_leasing_internal_invariants_and_real_issue_count(client):
    unit_id = _unit(client, "U-DQ")
    _signed_lead(unit_id, company="重复签约甲")
    _signed_lead(unit_id, company="重复签约乙")
    with SessionLocal() as db:
        from app.modules.business_leasing.models import LeasingUnit
        db.add(LeasingUnit(building="A栋", floor="2F", unit_no="U-DQ-EMPTY",
                           area=50.0, status="leased"))
        db.add(LeasingLead(company="非法阶段", stage="bogus", unit_id=None))
        db.commit()
        for index in range(12):
            from app.modules.business_legal.models import LegalCase
            db.add(LegalCase(case_no="DUP-DQ", title=f"重复案件{index}"))
        db.commit()

    payload = client.get(f"{DQ}/summary", params={"as_of": AS_OF}).json()
    checks = {(issue["check"]): issue for issue in payload["domains"]["leasing"]["issues"]}
    assert checks["重复签约"]["count"] == 1
    assert checks["已租无签约"]["count"] == 1
    assert checks["非法枚举"]["count"] == 1

    all_counts = sum(
        issue["count"]
        for domain in payload["domains"].values()
        for issue in domain.get("issues", [])
    )
    assert payload["issue_count"] == all_counts
    assert payload["issue_count"] > 0
    assert {issue["check"] for issue in payload["domains"]["leasing"]["issues"]} == {
        "重复签约", "已租无签约", "非法枚举",
    }
    # 计数来自全量问题，不依赖被截断的 top_issues 列表
    assert payload["issue_count"] >= len(payload["top_issues"])


# ---------------- 企划 / 工单 / facility / preset ----------------

def _campaign(client, name="合成活动", **over):
    body = {
        "name": name, "year": 2026, "category": "promo",
        "start_date": "2026-05-01", "end_date": "2026-05-07", "status": "running",
        "budget_items": [{"item": "物料", "budget": 1000, "actual": 1000}],
    }
    body.update(over)
    return client.post(f"{PLAN}/campaigns", json=body)


def test_planning_year_must_cover_schedule_and_calendar_matches_summary(client):
    outside = _campaign(client, name="年份错位", year=2025,
                        start_date="2026-05-01", end_date="2026-05-07")
    assert outside.status_code == 400, outside.text
    assert "年度" in outside.json()["detail"]

    inside = _campaign(client, name="跨年活动", year=2027,
                       start_date="2026-12-20", end_date="2027-01-10")
    assert inside.status_code == 201, inside.text

    calendar_2027 = client.get(f"{PLAN}/campaigns/calendar", params={"year": 2027}).json()
    ids = {item["id"] for month in calendar_2027["months"] for item in month["items"]}
    summary_2027 = client.get(f"{PLAN}/summary", params={"year": 2027}).json()
    assert summary_2027["campaigns"] == len(ids) == 1
    # 归属年度之外的日历不再展示（避免汇总与日历双口径）
    calendar_2026 = client.get(f"{PLAN}/campaigns/calendar", params={"year": 2026}).json()
    assert all(item["id"] != inside.json()["id"]
               for month in calendar_2026["months"] for item in month["items"])


def test_planning_legacy_mismatched_year_still_editable_for_unrelated_fields(client):
    from app.modules.business_planning.models import PlanCampaign

    with SessionLocal() as db:
        row = PlanCampaign(name="历史错位活动", year=2025, category="promo",
                           start_date=date(2026, 5, 1), end_date=date(2026, 5, 7),
                           status="done")
        db.add(row)
        db.commit()
        campaign_id = row.id

    # 未触及年度/档期的编辑不能被历史错位卡死
    ordinary = client.put(f"{PLAN}/campaigns/{campaign_id}", json={"note": "补一条备注"})
    assert ordinary.status_code == 200, ordinary.text
    # 一旦显式提交错位年度就按新规则拒绝
    rejected = client.put(f"{PLAN}/campaigns/{campaign_id}", json={"year": 2025})
    assert rejected.status_code == 400, rejected.text
    assert "年度" in rejected.json()["detail"]


def test_work_order_parent_cycle_is_detected(client):
    root = client.post(f"{OPS}/work-orders", json={
        "type": "repair", "description": "一级工单",
    }).json()
    child = client.post(f"{OPS}/work-orders", json={
        "type": "repair", "description": "二级工单", "parent_id": root["id"],
    }).json()
    grandchild = client.post(f"{OPS}/work-orders", json={
        "type": "repair", "description": "三级工单", "parent_id": child["id"],
    }).json()

    cycle = client.put(f"{OPS}/work-orders/{root['id']}",
                       json={"parent_id": grandchild["id"]})
    assert cycle.status_code == 400, cycle.text
    assert "循环" in cycle.json()["detail"]
    # 自身仍是非法
    assert client.put(f"{OPS}/work-orders/{root['id']}",
                      json={"parent_id": root["id"]}).status_code == 400


def test_facility_compliance_rejects_future_done_date_and_export_uses_real_labels(client):
    equipment = client.post(f"{FAC}/equipment", json={
        "code": "EQ-001", "name": "冷却塔", "category": "A", "status": "running",
    })
    assert equipment.status_code == 201, equipment.text
    future = (date.today() + timedelta(days=1)).isoformat()
    rejected = client.post(f"{FAC}/compliance", json={
        "equipment_id": equipment.json()["id"], "kind": "maintenance",
        "title": "年度维保", "due_date": (date.today() + timedelta(days=30)).isoformat(),
        "done_date": future,
    })
    assert rejected.status_code == 400, rejected.text
    assert "完成日期" in rejected.json()["detail"]

    today = date.today().isoformat()
    ok = client.post(f"{FAC}/compliance", json={
        "kind": "fire", "title": "消防检查", "due_date": today, "done_date": today,
    })
    assert ok.status_code == 201, ok.text

    from io import BytesIO
    from openpyxl import load_workbook

    exported = client.get(f"{FAC}/export/equipment.xlsx")
    assert exported.status_code == 200 and exported.content[:2] == b"PK"
    sheet = load_workbook(BytesIO(exported.content)).active
    headers = [cell.value for cell in sheet[1]]
    assert headers[2] == "等级"
    assert sheet.cell(row=2, column=3).value == "A类关键设备"


def test_all_manifest_preset_experts_resolve_to_builtin_team():
    from app.agents.team import TEAM

    loaded = {manifest.name: manifest for manifest in get_loaded_modules()}
    assert loaded["business_gm"].preset_experts == ["realestate"]
    assert loaded["business_dq"].preset_experts == ["realestate"]

    missing = {}
    for manifest in loaded.values():
        unknown = [name for name in manifest.preset_experts if name not in TEAM]
        if unknown:
            missing[manifest.name] = unknown
    assert missing == {}, f"模块预置专家不在内置团队：{missing}"


def test_zero_denominator_rates_stay_zero_not_error(client):
    units = client.get(f"{LEASING}/units/summary")
    assert units.status_code == 200, units.text
    assert units.json()["vacancy_rate"] == 0.0
    assert units.json()["leasing_rate"] == 0.0
    assert units.json()["opening_rate"] == 0.0

    ops = client.get(f"{OPS}/summary")
    assert ops.status_code == 200, ops.text
    assert ops.json()["collection_rate"] == 0.0
    assert ops.json()["receivable"] == 0.0

    hr = client.get(f"{HR}/summary", params={"month": "2026-10"})
    assert hr.status_code == 200, hr.text
    assert hr.json()["payroll_count"] == 0
    assert hr.json()["payroll_net"] == 0.0


def test_dq_degrades_when_a_module_table_is_missing(client):
    from sqlalchemy import text as sa_text

    from app.database import engine

    with engine.begin() as conn:
        conn.execute(sa_text("DROP TABLE mod_hr_payroll"))

    payload = client.get(f"{DQ}/summary", params={"as_of": AS_OF}).json()
    hr = payload["domains"]["hr"]
    assert hr["available"] is False
    assert hr["score"] is None
    assert any("未就绪" in reason for reason in hr["gap_reasons"])
    # 其它域照常评分，overall 只在可用域上平均
    assert payload["domains"]["leasing"]["available"] is True
    assert payload["overall_score"] is not None


def test_empty_database_gm_and_dq_stay_honest(client):
    gm = client.get(f"{GM}/summary", params={"period": "2026-01"})
    assert gm.status_code == 200, gm.text
    assert gm.json()["metrics"]
    dq = client.get(f"{DQ}/summary", params={"as_of": AS_OF})
    assert dq.status_code == 200 and dq.json()["overall_score"] == 100
    assert dq.json()["issue_count"] == 0
