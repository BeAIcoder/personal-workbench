# -*- coding: utf-8 -*-
"""Task 6 Fix round 1 回归（C-1 / I-1 / I-2 + 复核 Minor）。

C-1：`social_base` 留空 = 按基本工资（0 仍是显式不缴），不改变默认工资/个税/实发；
I-1：编辑已释放的 signed 线索不得复活铺位或清 released_at；
I-2：删除 active 合同必须同时释放铺位占用，不产生"幽灵已租"；
Minor：手工标记已租的审计、DQ 枚举漂移、租决未匹配提示、HR 文案、缺月告警聚合、并发释放。

全部合成数据 + 临时库；日期断言用固定 as_of / 固定入职月。
"""
from __future__ import annotations

import logging
import threading
from datetime import date, timedelta

import pytest
from sqlalchemy.exc import OperationalError

from app.database import SessionLocal
from app.modules.business_leasing.models import LeasingLead
from app.modules.business_operations.models import OpsContract
from app.modules.business_operations.service import contract_save

HR = "/api/business/hr"
OPS = "/api/business/operations"
LEASING = "/api/business/leasing"
DQ = "/api/business/dq"
AS_OF = "2026-11-01"


def _employee(client, no="E001", **over):
    body = {
        "emp_no": no, "name": f"合成员工{no}", "department": "营运部", "position": "主管",
        "hire_date": "2026-01-05", "status": "active", "base_salary": 10000.0,
    }
    body.update(over)
    return client.post(f"{HR}/employees", json=body)


def _unit(client, unit_no="U-1001", area=100.0, status="vacant"):
    resp = client.post(f"{LEASING}/units", json={
        "building": "A栋", "floor": "1F", "unit_no": unit_no, "area": area, "status": status,
    })
    assert resp.status_code in (200, 201), resp.text
    return resp.json()["id"]


def _unit_status(client, unit_id):
    for unit in client.get(f"{LEASING}/units", params={"page_size": 200}).json()["items"]:
        if unit["id"] == unit_id:
            return unit["status"]
    raise AssertionError(f"铺位 {unit_id} 不在列表中")


def _contract(client, *, unit_id=None, tenant="合成商户甲", no="HT-001",
              start="2026-01-01", end="2026-12-31", rent=10000.0):
    body = {
        "contract_no": no, "tenant": tenant, "unit_no": "",
        "area": 100.0, "start_date": start, "end_date": end,
        "rent_monthly": rent, "property_fee_monthly": 1200.0, "deposit": 0.0,
    }
    if unit_id is not None:
        body["unit_id"] = unit_id
    return client.post(f"{OPS}/contracts", json=body)


def _signed_lead(unit_id, company="合成商户甲", **over):
    with SessionLocal() as db:
        row = LeasingLead(company=company, stage="signed", unit_id=unit_id, **over)
        db.add(row)
        db.commit()
        return row.id


def _lead_row(lead_id):
    with SessionLocal() as db:
        row = db.get(LeasingLead, lead_id)
        return {"stage": row.stage, "released_at": row.released_at, "unit_id": row.unit_id}


# ---------------- C-1 social_base 默认口径 ----------------

def test_employee_without_social_base_uses_base_salary_default(client):
    """不传 social_base = 留空（按基本工资），不是"不缴社保"。"""
    created = _employee(client)
    assert created.status_code == 201, created.text
    assert created.json()["social_base"] is None

    payroll = client.post(f"{HR}/payroll/generate", json={"month": "2026-01"})
    assert payroll.status_code == 201, payroll.text
    row = payroll.json()["items"][0]
    assert row["social_insurance"] == pytest.approx(1050.0)
    assert row["housing_fund"] == pytest.approx(700.0)
    assert row["tax"] == pytest.approx(97.5)
    assert row["net_pay"] == pytest.approx(8152.5)


def test_employee_explicit_zero_social_base_means_no_contribution(client):
    created = _employee(client, social_base=0)
    assert created.status_code == 201, created.text
    assert created.json()["social_base"] == pytest.approx(0.0)

    payroll = client.post(f"{HR}/payroll/generate", json={"month": "2026-01"})
    assert payroll.status_code == 201, payroll.text
    row = payroll.json()["items"][0]
    assert row["social_insurance"] == pytest.approx(0.0)
    assert row["housing_fund"] == pytest.approx(0.0)
    # 10000 - 5000 = 5000 → 3% = 150
    assert row["tax"] == pytest.approx(150.0)


def test_employee_update_can_clear_social_base_back_to_default(client):
    employee_id = _employee(client, social_base=8000).json()["id"]
    assert client.get(f"{HR}/employees").json()["items"][0]["social_base"] == pytest.approx(8000.0)

    cleared = client.put(f"{HR}/employees/{employee_id}", json={"social_base": None})
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["social_base"] is None

    payroll = client.post(f"{HR}/payroll/generate", json={"month": "2026-01"})
    assert payroll.status_code == 201, payroll.text
    assert payroll.json()["items"][0]["social_insurance"] == pytest.approx(1050.0)


def test_legacy_zero_social_base_row_keeps_base_salary_fallback(client):
    """改造前的 0 是列默认值（NOT NULL），按"未填"处理，不能变成不缴社保。"""
    from app.modules.business_hr.models import HrEmployee

    with SessionLocal() as db:
        row = HrEmployee(emp_no="E-LEGACY", name="历史员工", hire_date=date(2026, 1, 5),
                         status="active", base_salary=10000.0, social_base=0.0,
                         special_additional=0.0)
        db.add(row)
        db.commit()

    listed = client.get(f"{HR}/employees").json()["items"]
    assert listed[0]["social_base"] is None
    payroll = client.post(f"{HR}/payroll/generate", json={"month": "2026-01"})
    assert payroll.status_code == 201, payroll.text
    assert payroll.json()["items"][0]["social_insurance"] == pytest.approx(1050.0)


def test_employees_export_shows_blank_for_unspecified_social_base(client):
    from io import BytesIO
    from openpyxl import load_workbook

    _employee(client, no="E-NONE")
    _employee(client, no="E-ZERO", name="显式零", social_base=0)
    exported = client.get(f"{HR}/export/employees.xlsx")
    sheet = load_workbook(BytesIO(exported.content)).active
    headers = [cell.value for cell in sheet[1]]
    column = headers.index("社保基数") + 1
    values = {sheet.cell(row=r, column=1).value: sheet.cell(row=r, column=column).value
              for r in range(2, sheet.max_row + 1)}
    assert values["E-NONE"] in (None, "")
    assert values["E-ZERO"] == pytest.approx(0.0)


# ---------------- I-1 已释放线索的纯编辑 ----------------

def test_editing_released_signed_lead_keeps_release_state(client):
    unit_id = _unit(client, "U-REL-EDIT")
    lead_id = _signed_lead(unit_id)
    contract = _contract(client, unit_id=unit_id)
    assert contract.status_code == 201, contract.text
    assert client.put(f"{OPS}/contracts/{contract.json()['id']}",
                      json={"status": "terminated"}).status_code == 200
    assert _unit_status(client, unit_id) == "vacant"
    released_at = _lead_row(lead_id)["released_at"]
    assert released_at is not None

    # 只改备注：不得复活铺位、不得清 released_at
    edited = client.put(f"{LEASING}/leads/{lead_id}", json={"note": "退租后回访记录"})
    assert edited.status_code == 200, edited.text
    assert edited.json()["released_at"] is not None
    assert edited.json()["stage"] == "signed"
    assert _unit_status(client, unit_id) == "vacant"
    assert _lead_row(lead_id)["released_at"] == released_at


def test_resigning_released_lead_to_another_unit_claims_it_again(client):
    first = _unit(client, "U-OLD")
    second = _unit(client, "U-NEW")
    lead_id = _signed_lead(first)
    contract = _contract(client, unit_id=first)
    client.put(f"{OPS}/contracts/{contract.json()['id']}", json={"status": "terminated"})
    assert _unit_status(client, first) == "vacant"

    moved = client.put(f"{LEASING}/leads/{lead_id}",
                       json={"unit_id": second, "stage": "signed"})
    assert moved.status_code == 200, moved.text
    assert moved.json()["released_at"] is None
    assert _unit_status(client, second) == "leased"


# ---------------- I-2 删除 active 合同不得留下幽灵占用 ----------------

def test_deleting_active_contract_releases_unit_and_lead(client):
    unit_id = _unit(client, "U-DEL-REL", status="leased")
    lead_id = _signed_lead(unit_id)
    contract = _contract(client, unit_id=unit_id)
    assert contract.status_code == 201, contract.text
    assert _lead_row(lead_id)["released_at"] is None

    deleted = client.delete(f"{OPS}/contracts/{contract.json()['id']}")
    assert deleted.status_code == 200, deleted.text
    assert _unit_status(client, unit_id) == "vacant"
    assert _lead_row(lead_id)["released_at"] is not None

    newcomer = client.post(f"{LEASING}/leads", json={
        "company": "删除后新租户", "stage": "signed", "unit_id": unit_id,
    })
    assert newcomer.status_code == 201, newcomer.text
    assert _unit_status(client, unit_id) == "leased"


def test_deleting_contract_with_work_order_does_not_release_unit(client):
    unit_id = _unit(client, "U-DEL-WO", status="leased")
    lead_id = _signed_lead(unit_id)
    contract = _contract(client, unit_id=unit_id)
    order = client.post(f"{OPS}/work-orders", json={
        "type": "repair", "description": "漏水", "contract_id": contract.json()["id"],
    })
    assert order.status_code == 201, order.text

    blocked = client.delete(f"{OPS}/contracts/{contract.json()['id']}")
    assert blocked.status_code == 409, blocked.text
    # 失败路径不得有副作用
    assert _unit_status(client, unit_id) == "leased"
    assert _lead_row(lead_id)["released_at"] is None
    assert client.get(f"{OPS}/contracts").json()["total"] == 1


def test_concurrent_termination_releases_unit_exactly_once(client):
    unit_id = _unit(client, "U-RACE", status="leased")
    lead_id = _signed_lead(unit_id)
    contract = _contract(client, unit_id=unit_id)
    contract_id = contract.json()["id"]
    outcomes = []

    def worker():
        with SessionLocal() as session:
            try:
                contract_save(session, {"status": "terminated"}, contract_id)
                outcomes.append("ok")
            except (ValueError, OperationalError):
                outcomes.append("rejected")
            except Exception as exc:  # 非法异常类型单独记录，便于断言
                outcomes.append(f"bad:{type(exc).__name__}")

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not [item for item in outcomes if item.startswith("bad:")], outcomes
    assert outcomes.count("ok") >= 1
    contracts = client.get(f"{OPS}/contracts").json()["items"]
    assert contracts[0]["status"] == "terminated"
    assert _unit_status(client, unit_id) == "vacant"
    released = _lead_row(lead_id)["released_at"]
    assert released is not None
    # 幂等：再次终止不会改写释放时间
    with SessionLocal() as session:
        contract_save(session, {"status": "terminated"}, contract_id)
    assert _lead_row(lead_id)["released_at"] == released


# ---------------- Minor：审计 / DQ 契约 / 提示 / 文案 / 聚合 ----------------

def test_manual_leased_without_signed_lead_is_audited(client, caplog):
    unit_id = _unit(client, "U-MANUAL")
    with caplog.at_level(logging.WARNING, logger="app.modules.business_leasing.service"):
        resp = client.put(f"{LEASING}/units/{unit_id}", json={"status": "leased"})
    assert resp.status_code == 200, resp.text
    assert any("没有占用中的签约线索" in record.message or "手工" in record.message
               for record in caplog.records)


def test_dq_lead_and_unit_enums_match_leasing_constants():
    from app.modules.business_dq.service import LEAD_STAGE_VALUES, UNIT_STATUS_VALUES
    from app.modules.business_leasing.service import LEAD_STAGES, UNIT_STATUSES

    assert tuple(LEAD_STAGE_VALUES) == tuple(LEAD_STAGES)
    assert tuple(UNIT_STATUS_VALUES) == tuple(UNIT_STATUSES)


def test_dq_flags_contract_without_matching_tenant_lead(client):
    unit_id = _unit(client, "U-DQ-TENANT", status="leased")
    _signed_lead(unit_id, company="另一个租户")
    contract = _contract(client, unit_id=unit_id, tenant="本租户")
    assert contract.status_code == 201, contract.text

    payload = client.get(f"{DQ}/summary", params={"as_of": AS_OF}).json()
    checks = {issue["check"]: issue for issue in payload["domains"]["operations"]["issues"]}
    assert checks["租决未匹配"]["count"] == 1
    assert checks["租决未匹配"]["level"] == "info"
    assert "固定租金" in checks["租决未匹配"]["suggestion"]


def test_missing_month_warnings_are_aggregated(client):
    _employee(client, no="E101", hire_date="2026-01-05")
    _employee(client, no="E102", hire_date="2026-02-10")
    generated = client.post(f"{HR}/payroll/generate", json={"month": "2026-04"})
    assert generated.status_code == 201, generated.text
    missing = [w for w in generated.json()["warnings"] if "未生成" in w]
    assert len(missing) == 1
    assert "2 人" in missing[0]
    assert "E101" in missing[0] and "E102" in missing[0]


def test_hr_delete_conflict_message_points_to_executable_action(client):
    employee_id = _employee(client, no="E409").json()["id"]
    payroll = client.post(f"{HR}/payroll/generate", json={"month": "2026-02"})
    client.post(f"{HR}/payroll/{payroll.json()['items'][0]['id']}/confirm")

    blocked = client.delete(f"{HR}/employees/{employee_id}")
    assert blocked.status_code == 409, blocked.text
    detail = blocked.json()["detail"]
    assert "离职" in detail
    assert "导出留底" not in detail
    assert "薪资页处理" not in detail
    # 「离职」是页面可执行操作：PUT status=left 成功
    assert client.put(f"{HR}/employees/{employee_id}", json={"status": "left"}).status_code == 200


def test_hr_column_patches_migration_is_idempotent(tmp_path, monkeypatch):
    """旧库补 social_base_set：两列都要补上，且二次启动 schema/行数据不变。"""
    import sqlite3

    from sqlalchemy import create_engine, text

    from app import main as app_main

    path = tmp_path / "old_hr.db"
    raw = sqlite3.connect(path)
    raw.execute(
        "CREATE TABLE mod_hr_employee (id INTEGER PRIMARY KEY, emp_no VARCHAR(50), "
        "name VARCHAR(100), social_base FLOAT NOT NULL DEFAULT 0)"
    )
    raw.execute("INSERT INTO mod_hr_employee VALUES (1, 'OLD-1', '历史员工', 0)")
    raw.commit()
    raw.close()

    old_engine = create_engine(f"sqlite:///{path.as_posix()}")
    monkeypatch.setattr(app_main, "engine", old_engine)
    try:
        app_main._ensure_sqlite_columns()
        with old_engine.connect() as conn:
            columns = {row[1] for row in conn.execute(text("PRAGMA table_info(mod_hr_employee)"))}
            assert {"special_additional", "social_base_set"} <= columns
            first_schema = conn.execute(text(
                "SELECT name, sql FROM sqlite_master WHERE name = 'mod_hr_employee'"
            )).fetchall()
            first_rows = conn.execute(text("SELECT * FROM mod_hr_employee")).fetchall()

        app_main._ensure_sqlite_columns()
        with old_engine.connect() as conn:
            second_schema = conn.execute(text(
                "SELECT name, sql FROM sqlite_master WHERE name = 'mod_hr_employee'"
            )).fetchall()
            second_rows = conn.execute(text("SELECT * FROM mod_hr_employee")).fetchall()
        assert second_schema == first_schema
        assert second_rows == first_rows
    finally:
        old_engine.dispose()
