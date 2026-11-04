# -*- coding: utf-8 -*-
"""business_hr 行政人事模块测试（合成数据，临时库）。"""
from datetime import date, timedelta

import pytest

from app.modules import get_loaded_modules

P = "/api/business/hr"


def _employee(client, no="E001", **over):
    body = {
        "emp_no": no, "name": "合成员工甲", "department": "营运部", "position": "主管",
        "hire_date": "2025-01-01", "status": "active",
        "base_salary": 10000.0, "social_base": 10000.0,
        "contract_end": (date.today() + timedelta(days=20)).isoformat(),
    }
    body.update(over)
    return client.post(f"{P}/employees", json=body)


def test_manifest_loaded():
    loaded = {m.name: m for m in get_loaded_modules()}
    assert "business_hr" in loaded
    m = loaded["business_hr"]
    assert m.prefix == "/api/business/hr"
    assert m.tables == ["mod_hr_employee", "mod_hr_attendance", "mod_hr_payroll"]


def test_employee_crud_and_expiring(client):
    r = _employee(client)
    assert r.status_code == 201, r.text
    eid = r.json()["id"]
    assert r.json()["expiring"] is True
    assert r.json()["expiring_types"] == ["合同"]

    assert _employee(client).status_code == 409
    exp = client.get(f"{P}/employees/expiring", params={"days": 30}).json()
    assert [e["id"] for e in exp] == [eid]

    upd = client.put(f"{P}/employees/{eid}", json={"position": "经理", "base_salary": 12000})
    assert upd.status_code == 200, upd.text
    assert upd.json()["base_salary"] == pytest.approx(12000.0)
    assert client.put(f"{P}/employees/{eid}", json={"status": "bogus"}).status_code == 422
    assert client.delete(f"{P}/employees/{eid}").status_code == 200


def test_attendance_upsert(client):
    eid = _employee(client).json()["id"]
    r = client.put(f"{P}/attendance/{eid}/2026-10", json={
        "work_days": 21.75, "actual_days": 20, "leave_days": 1.75,
        "overtime_hours": 8, "late_count": 1,
    })
    assert r.status_code == 200, r.text
    assert r.json()["leave_days"] == pytest.approx(1.75)

    # 同员工同月再 PUT 是更新而不是新增
    client.put(f"{P}/attendance/{eid}/2026-10", json={"leave_days": 0.5})
    rows = client.get(f"{P}/attendance", params={"month": "2026-10"}).json()
    assert len(rows) == 1 and rows[0]["leave_days"] == pytest.approx(0.5)
    # 非法月份
    assert client.get(f"{P}/attendance", params={"month": "2026-13"}).status_code == 400


def test_payroll_generate_confirm_and_tax(client):
    eid = _employee(client).json()["id"]

    r = client.post(f"{P}/payroll/generate", json={"month": "2026-01"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["count"] == 1
    row = body["items"][0]
    # 应发 10000，社保 1050，公积金 700，累计应税 3250 → 3% = 97.5
    assert row["social_insurance"] == pytest.approx(1050.0)
    assert row["housing_fund"] == pytest.approx(700.0)
    assert row["tax"] == pytest.approx(97.5)
    assert row["net_pay"] == pytest.approx(8152.5)

    # 同月再生成 → 409
    assert client.post(f"{P}/payroll/generate", json={"month": "2026-01"}).status_code == 409

    # 第二个月：累计 6500 → 195，减已预扣 97.5 → 97.5
    r2 = client.post(f"{P}/payroll/generate", json={"month": "2026-02"})
    assert r2.status_code == 201, r2.text
    feb = r2.json()["items"][0]
    assert feb["tax"] == pytest.approx(97.5)
    assert feb["net_pay"] == pytest.approx(8152.5)

    lst = client.get(f"{P}/payroll", params={"month": "2026-01"}).json()
    assert len(lst) == 1 and lst[0]["status"] == "draft"

    confirmed = client.post(f"{P}/payroll/{row['id']}/confirm")
    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "confirmed"
    # 已确认：不能重复确认、不能删除
    assert client.post(f"{P}/payroll/{row['id']}/confirm").status_code == 409
    assert client.delete(f"{P}/payroll/{row['id']}").status_code == 409

    s = client.get(f"{P}/summary", params={"month": "2026-01"}).json()
    assert s["payroll_count"] == 1
    assert s["payroll_net"] == pytest.approx(8152.5)
    assert s["payroll_draft"] == 0

    # 草稿可删
    assert client.delete(f"{P}/payroll/{feb['id']}").status_code == 200


def test_employee_update_explicit_null_clears_contract_end(client):
    eid = _employee(client).json()["id"]
    ordinary = client.put(f"{P}/employees/{eid}", json={"position": "经理"})
    assert ordinary.status_code == 200, ordinary.text
    assert ordinary.json()["contract_end"] is not None

    cleared = client.put(f"{P}/employees/{eid}", json={"contract_end": None})
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["contract_end"] is None
    rows = client.get(f"{P}/employees").json()["items"]
    assert next(row for row in rows if row["id"] == eid)["contract_end"] is None


def test_payroll_generate_warns_for_each_employee_without_attendance(client):
    attended_id = _employee(client, no="E001", name="合成有考勤").json()["id"]
    _employee(client, no="E002", name="合成缺考勤")
    month = "2026-04"
    response = client.put(f"{P}/attendance/{attended_id}/{month}", json={"actual_days": 21.75})
    assert response.status_code == 200, response.text

    generated = client.post(f"{P}/payroll/generate", json={
        "month": month, "social_rate": 0, "housing_rate": 0,
    })
    assert generated.status_code == 201, generated.text
    body = generated.json()
    assert body["count"] == 2
    assert len(body["items"]) == 2
    # 缺月告警与考勤告警是两类独立提示：这里只断言考勤这一类
    attendance_warnings = [w for w in body["warnings"] if "缺少考勤记录" in w]
    assert len(attendance_warnings) == 1
    assert "E002" in attendance_warnings[0]
    assert "合成缺考勤" in attendance_warnings[0]
    assert month in attendance_warnings[0]
    assert "E001" not in attendance_warnings[0]
    # 2026-01~03 未生成薪资（入职 2025 年）→ 两名员工聚合成一条缺月告警
    missing = [w for w in body["warnings"] if "未生成" in w]
    assert len(missing) == 1
    assert "2 人" in missing[0] and "E001" in missing[0] and "E002" in missing[0]


def test_payroll_mid_year_hire_uses_employment_months_not_payroll_rows(client):
    _employee(client, hire_date="2026-07-15")
    generated = client.post(f"{P}/payroll/generate", json={
        "month": "2026-08", "social_rate": 0, "housing_rate": 0,
    })
    assert generated.status_code == 201, generated.text
    # 7 月入职，截至 8 月减除 2×5000；即使这是首张薪资单也不能只减 1 个月。
    assert generated.json()["items"][0]["tax"] == pytest.approx(0.0)


def test_payroll_missing_month_does_not_reduce_employment_month_count(client):
    _employee(client, hire_date="2026-01-10")
    january = client.post(f"{P}/payroll/generate", json={
        "month": "2026-01", "social_rate": 0, "housing_rate": 0,
    })
    assert january.status_code == 201, january.text
    assert january.json()["items"][0]["tax"] == pytest.approx(150.0)

    march = client.post(f"{P}/payroll/generate", json={
        "month": "2026-03", "social_rate": 0, "housing_rate": 0,
    })
    assert march.status_code == 201, march.text
    # 2 月未生成薪资，3 月仍按任职第 3 个月减除；累计税额 150，减已预扣 150。
    assert march.json()["items"][0]["tax"] == pytest.approx(0.0)


def test_export(client):
    _employee(client)
    client.post(f"{P}/payroll/generate", json={"month": "2026-03"})
    r = client.get(f"{P}/export/employees.xlsx")
    assert r.status_code == 200 and r.content[:2] == b"PK"
    r = client.get(f"{P}/export/payroll.xlsx", params={"month": "2026-03"})
    assert r.status_code == 200 and r.content[:2] == b"PK"
