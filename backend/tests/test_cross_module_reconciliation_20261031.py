# -*- coding: utf-8 -*-
"""OpsContract.unit_id 勾稽与租决出账、HR 专项附加扣除（2026-10-31 第 3 批）。

覆盖：
- 合同关联真实铺位（unit_id 校验、unit_no 同步、同铺位唯一生效合同）
- 出账读租决参数：免租期租金为 0（物业费照收）、按年递增、抽成合同拒绝固定出账
- HR 专项附加扣除进入累计预扣，与 finance_iit 同函数同口径

全部使用合成数据；client 由 conftest 独立库提供。
"""
import pytest

from app.database import SessionLocal
from app.modules.business_leasing.models import LeasingLead

OPS = "/api/business/operations"
LEASING = "/api/business/leasing"
HR = "/api/business/hr"


def _make_unit(client, unit_no="U-1001"):
    resp = client.post(f"{LEASING}/units", json={
        "building": "A栋", "floor": "1F", "unit_no": unit_no, "area": 100.0,
    })
    assert resp.status_code in (200, 201), resp.text
    return resp.json()["id"]


def _make_signed_lead(unit_id: int, company: str = "勾稽商户", **terms) -> None:
    with SessionLocal() as db:
        db.add(LeasingLead(
            company=company, stage="signed", unit_id=unit_id, **terms))
        db.commit()


def _make_contract(client, unit_id=None, no="HT-XM-001", start="2026-01-01",
                   end="2026-12-31", rent=10000.0):
    body = {
        "contract_no": no, "tenant": "勾稽商户", "unit_no": "",
        "area": 100.0, "start_date": start, "end_date": end,
        "rent_monthly": rent, "property_fee_monthly": 1200.0, "deposit": 0.0,
    }
    if unit_id is not None:
        body["unit_id"] = unit_id
    resp = client.post(f"{OPS}/contracts", json=body)
    return resp


def _bill(client, contract_id, period):
    resp = client.post(f"{OPS}/contracts/{contract_id}/bills", json={"period": period})
    return resp


def test_contract_links_real_unit_and_syncs_unit_no(client):
    unit_id = _make_unit(client, "U-LINK-1")
    resp = _make_contract(client, unit_id=unit_id)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["unit_id"] == unit_id
    assert body["unit_no"] == "U-LINK-1"  # 铺位号以招商台账为准回填


def test_contract_unknown_unit_rejected(client):
    resp = _make_contract(client, unit_id=999999)
    assert resp.status_code == 409, resp.text
    assert "铺位不存在" in resp.json()["detail"]


def test_one_active_contract_per_unit(client):
    unit_id = _make_unit(client, "U-DUP-1")
    first = _make_contract(client, unit_id=unit_id, no="HT-DUP-A")
    assert first.status_code == 201, first.text
    second = _make_contract(client, unit_id=unit_id, no="HT-DUP-B")
    assert second.status_code == 409, second.text
    assert "重复出租" in second.json()["detail"]


def test_free_rent_month_bills_zero_rent_but_property_fee(client):
    unit_id = _make_unit(client, "U-FREE-1")
    _make_signed_lead(unit_id, free_rent_months=1)
    resp = _make_contract(client, unit_id=unit_id, no="HT-FREE-1")
    assert resp.status_code == 201, resp.text
    cid = resp.json()["id"]

    first = _bill(client, cid, "2026-01")
    assert first.status_code == 201, first.text
    body = first.json()
    assert body["rent_amount"] == pytest.approx(0.0)
    assert body["property_amount"] == pytest.approx(1200.0)
    assert body["total_amount"] == pytest.approx(1200.0)

    second = _bill(client, cid, "2026-02")
    assert second.status_code == 201, second.text
    assert second.json()["rent_amount"] == pytest.approx(10000.0)


def test_escalation_applies_from_second_year(client):
    unit_id = _make_unit(client, "U-ESC-1")
    _make_signed_lead(unit_id, escalation_rate=10.0)  # 每年 +10%
    resp = _make_contract(client, unit_id=unit_id, no="HT-ESC-1", start="2025-01-01", end="2026-12-31")
    assert resp.status_code == 201, resp.text
    cid = resp.json()["id"]

    in_first_year = _bill(client, cid, "2025-06")
    assert in_first_year.status_code == 201, in_first_year.text
    assert in_first_year.json()["rent_amount"] == pytest.approx(10000.0)

    in_second_year = _bill(client, cid, "2026-01")
    assert in_second_year.status_code == 201, in_second_year.text
    assert in_second_year.json()["rent_amount"] == pytest.approx(11000.0)


def test_percentage_rent_form_rejects_fixed_billing(client):
    unit_id = _make_unit(client, "U-PCT-1")
    _make_signed_lead(unit_id, rent_form="percentage")
    resp = _make_contract(client, unit_id=unit_id, no="HT-PCT-1")
    assert resp.status_code == 201, resp.text
    bill = _bill(client, resp.json()["id"], "2026-01")
    assert bill.status_code == 409, bill.text
    assert "抽成" in bill.json()["detail"]


def test_hr_special_additional_enters_cumulative_withholding(client):
    resp = client.post(f"{HR}/employees", json={
        "emp_no": "E-SA-1", "name": "专项附加员工", "hire_date": "2026-01-01",
        "status": "active", "base_salary": 10000.0, "special_additional": 1500.0,
    })
    assert resp.status_code == 201, resp.text

    jan = client.post(f"{HR}/payroll/generate", json={"month": "2026-01", "social_rate": 0, "housing_rate": 0})
    assert jan.status_code == 201, jan.text
    # 应税 = 10000 − 5000（减除费用） − 1500（专项附加） = 3500 → 3% = 105
    assert jan.json()["items"][0]["tax"] == pytest.approx(105.0)

    feb = client.post(f"{HR}/payroll/generate", json={"month": "2026-02", "social_rate": 0, "housing_rate": 0})
    assert feb.status_code == 201, feb.text
    # 累计应税 7000 → 累计税 210，减已预扣 105 → 本月 105
    assert feb.json()["items"][0]["tax"] == pytest.approx(105.0)
    assert feb.json()["items"][0]["special_additional"] == pytest.approx(1500.0)


def test_hr_without_special_additional_baseline(client):
    resp = client.post(f"{HR}/employees", json={
        "emp_no": "E-SA-2", "name": "无专项附加员工", "hire_date": "2026-01-01",
        "status": "active", "base_salary": 10000.0,
    })
    assert resp.status_code == 201, resp.text
    jan = client.post(f"{HR}/payroll/generate", json={"month": "2026-01", "social_rate": 0, "housing_rate": 0})
    assert jan.status_code == 201, jan.text
    # 基线：无专项附加时 5000 应税 → 150
    assert jan.json()["items"][0]["tax"] == pytest.approx(150.0)
