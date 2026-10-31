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

    upd = client.put(f"{P}/contracts/{rid}", json={"status": "approved", "risk_level": "low"})
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

    upd = client.put(f"{P}/cases/{cid}", json={"stage": "closed", "result": "调解结案"})
    assert upd.status_code == 200
    s = client.get(f"{P}/summary").json()
    assert s["open_cases"] == 0 and s["hearings_7"] == 0

    assert client.put(f"{P}/cases/{cid}", json={"stage": "bogus"}).status_code == 422
    assert client.delete(f"{P}/cases/{cid}").status_code == 200


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
