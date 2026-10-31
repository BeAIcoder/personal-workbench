# -*- coding: utf-8 -*-
"""business_legal 法务管理模块测试（合成数据，临时库）。"""
from datetime import date, timedelta

import pytest

from app.modules import get_loaded_modules

P = "/api/business/legal"


def test_manifest_loaded():
    loaded = {m.name: m for m in get_loaded_modules()}
    assert "business_legal" in loaded
    m = loaded["business_legal"]
    assert m.prefix == "/api/business/legal"
    assert m.tables == ["mod_legal_contract_review", "mod_legal_case", "mod_legal_seal"]


def test_review_crud_and_summary(client):
    expire = (date.today() + timedelta(days=10)).isoformat()
    r = client.post(f"{P}/contracts", json={
        "title": "合成租赁合同", "contract_type": "租赁", "counterparty": "合成商户甲",
        "amount": 120000, "reviewer": "法务甲", "status": "pending",
        "risk_level": "high", "expire_date": expire,
    })
    assert r.status_code == 201, r.text
    rid = r.json()["id"]
    assert r.json()["expiring"] is True
    assert r.json()["risk_label"] == "高"

    s = client.get(f"{P}/summary").json()
    assert s["reviews_total"] == 1
    assert s["pending_reviews"] == 1
    assert s["high_risk"] == 1
    assert s["expiring_30"] == 1

    upd = client.put(f"{P}/contracts/{rid}", json={"status": "reviewing", "risk_level": "low"})
    assert upd.status_code == 200, upd.text
    assert upd.json()["status"] == "reviewing"
    upd = client.put(f"{P}/contracts/{rid}", json={"status": "approved"})
    assert upd.status_code == 200, upd.text
    assert upd.json()["status"] == "approved"
    s = client.get(f"{P}/summary").json()
    assert s["pending_reviews"] == 0 and s["high_risk"] == 0

    assert client.put(f"{P}/contracts/{rid}", json={"status": "bogus"}).status_code == 422
    assert client.delete(f"{P}/contracts/{rid}").status_code == 200


def test_case_lifecycle_and_hearing(client):
    hearing = (date.today() + timedelta(days=3)).isoformat()
    r = client.post(f"{P}/cases", json={
        "case_no": "(2026)合民初001号", "title": "合成租金纠纷",
        "case_type": "litigation", "counterparty": "合成商户乙",
        "amount": 50000, "stage": "hearing", "hearing_date": hearing,
    })
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    assert r.json()["hearing_soon"] is True

    s = client.get(f"{P}/summary").json()
    assert s["open_cases"] == 1
    assert s["hearings_7"] == 1
    assert s["case_amount"] == pytest.approx(50000.0)

    upd = client.put(f"{P}/cases/{cid}", json={"stage": "judgment", "result": "一审判决"})
    assert upd.status_code == 200, upd.text
    upd = client.put(f"{P}/cases/{cid}", json={"stage": "closed", "result": "判决生效结案"})
    assert upd.status_code == 200, upd.text
    s = client.get(f"{P}/summary").json()
    assert s["open_cases"] == 0 and s["hearings_7"] == 0

    assert client.put(f"{P}/cases/{cid}", json={"stage": "bogus"}).status_code == 422
    assert client.delete(f"{P}/cases/{cid}").status_code == 200


@pytest.mark.parametrize("resource,field", [
    ("contracts", "expire_date"),
    ("cases", "filed_date"),
    ("cases", "hearing_date"),
])
def test_legal_update_explicit_null_clears_nullable_dates(client, resource, field):
    created = client.post(f"{P}/{resource}", json={
        "title": f"合成{resource}{field}", field: "2026-01-15",
    })
    assert created.status_code == 201, created.text
    row_id = created.json()["id"]

    ordinary = client.put(f"{P}/{resource}/{row_id}", json={"note": "未提交日期应保持原值"})
    assert ordinary.status_code == 200, ordinary.text
    assert ordinary.json()[field] == "2026-01-15"

    cleared = client.put(f"{P}/{resource}/{row_id}", json={field: None})
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()[field] is None
    items = client.get(f"{P}/{resource}").json()["items"]
    assert next(row for row in items if row["id"] == row_id)[field] is None


@pytest.mark.parametrize("current,target", [
    ("pending", "approved"),
    ("pending", "archived"),
    ("reviewing", "pending"),
    ("approved", "pending"),
    ("approved", "reviewing"),
    ("rejected", "pending"),
    ("archived", "reviewing"),
])
def test_review_rejects_invalid_status_transitions(client, current, target):
    created = client.post(f"{P}/contracts", json={"title": "合成状态合同", "status": current})
    row_id = created.json()["id"]
    response = client.put(f"{P}/contracts/{row_id}", json={"status": target})
    assert response.status_code == 400, response.text


@pytest.mark.parametrize("decision", ["approved", "rejected"])
def test_review_valid_transitions_and_same_status_edit(client, decision):
    created = client.post(f"{P}/contracts", json={"title": "合成合法流程", "status": "pending"})
    row_id = created.json()["id"]
    for status in ("reviewing", decision, "archived"):
        response = client.put(f"{P}/contracts/{row_id}", json={"status": status})
        assert response.status_code == 200, response.text
    ordinary = client.put(f"{P}/contracts/{row_id}", json={
        "status": "archived", "note": "归档后补充普通备注",
    })
    assert ordinary.status_code == 200, ordinary.text
    assert ordinary.json()["note"] == "归档后补充普通备注"


@pytest.mark.parametrize("current,target", [
    ("filed", "judgment"),
    ("hearing", "closed"),
    ("judgment", "filed"),
    ("appeal", "judgment"),
    ("closed", "appeal"),
    ("closed", "filed"),
])
def test_case_rejects_invalid_stage_transitions(client, current, target):
    created = client.post(f"{P}/cases", json={"title": "合成状态案件", "stage": current})
    row_id = created.json()["id"]
    response = client.put(f"{P}/cases/{row_id}", json={"stage": target})
    assert response.status_code == 400, response.text


@pytest.mark.parametrize("stages", [
    ("filed", "hearing", "judgment", "appeal", "closed"),
    ("filed", "hearing", "judgment", "closed"),
])
def test_case_valid_transitions_and_same_status_edit(client, stages):
    created = client.post(f"{P}/cases", json={"title": "合成合法案件", "stage": "filed"})
    row_id = created.json()["id"]
    for stage in stages[1:]:
        response = client.put(f"{P}/cases/{row_id}", json={"stage": stage})
        assert response.status_code == 200, response.text
    ordinary = client.put(f"{P}/cases/{row_id}", json={
        "stage": "closed", "result": "结案后补充结果说明",
    })
    assert ordinary.status_code == 200, ordinary.text
    assert ordinary.json()["result"] == "结案后补充结果说明"


def test_overdue_contracts_and_hearings_remain_visible_separately(client):
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    expired = client.post(f"{P}/contracts", json={
        "title": "合成已过期合同", "expire_date": yesterday,
    })
    assert expired.status_code == 201, expired.text
    assert expired.json()["overdue"] is True
    assert expired.json()["expiring"] is False
    client.post(f"{P}/contracts", json={"title": "合成临期合同", "expire_date": tomorrow})

    overdue = client.post(f"{P}/cases", json={
        "title": "合成逾期未更新开庭", "stage": "hearing", "hearing_date": yesterday,
    })
    assert overdue.status_code == 201, overdue.text
    assert overdue.json()["overdue"] is True
    assert overdue.json()["hearing_soon"] is False
    client.post(f"{P}/cases", json={
        "title": "合成临期开庭", "stage": "hearing", "hearing_date": tomorrow,
    })

    summary = client.get(f"{P}/summary").json()
    assert summary["expiring_30"] == 1
    assert summary["overdue_reviews"] == 1
    assert summary["hearings_7"] == 1
    assert summary["overdue_hearings"] == 1


def test_seal_crud(client):
    r = client.post(f"{P}/seals", json={
        "purpose": "租赁合同用印", "doc_type": "合同", "applicant": "业务甲",
        "approver": "总经理", "copies": 2,
    })
    assert r.status_code == 201, r.text
    sid = r.json()["id"]
    assert r.json()["used_at"] is not None

    assert client.post(f"{P}/seals", json={"purpose": "x", "copies": 0}).status_code == 422
    assert client.get(f"{P}/seals").json()["total"] == 1
    assert client.delete(f"{P}/seals/{sid}").status_code == 200


def test_export(client):
    client.post(f"{P}/contracts", json={"title": "导出测试合同", "amount": 1})
    r = client.get(f"{P}/export/contracts.xlsx")
    assert r.status_code == 200 and r.content[:2] == b"PK"
    r = client.get(f"{P}/export/cases.xlsx")
    assert r.status_code == 200 and r.content[:2] == b"PK"
