# -*- coding: utf-8 -*-
"""business_leasing 招商管理模块测试（合成数据，临时库）。"""
from datetime import date, timedelta

import pytest

from app.modules import get_loaded_modules

P = "/api/business/leasing"


def test_manifest_loaded():
    loaded = {m.name: m for m in get_loaded_modules()}
    assert "business_leasing" in loaded
    m = loaded["business_leasing"]
    assert m.prefix == "/api/business/leasing"
    assert m.tables == ["mod_leasing_unit", "mod_leasing_lead", "mod_leasing_followup"]
    assert m.menu["group"] == "经营"


def test_unit_crud_and_summary(client):
    r = client.post(f"{P}/units", json={
        "building": "A栋", "floor": "1F", "unit_no": "A-101",
        "area": 120.5, "rent_price": 80, "property_fee": 12, "status": "vacant",
    })
    assert r.status_code == 201, r.text
    uid = r.json()["id"]
    assert r.json()["monthly_rent"] == pytest.approx(9640.0)

    r = client.post(f"{P}/units", json={
        "building": "A栋", "floor": "1F", "unit_no": "A-102",
        "area": 100, "rent_price": 80, "status": "leased",
    })
    assert r.status_code == 201, r.text

    summary = client.get(f"{P}/units/summary").json()
    assert summary["total_units"] == 2
    assert summary["total_area"] == pytest.approx(220.5)
    assert summary["vacant_area"] == pytest.approx(120.5)
    assert summary["vacancy_rate"] == pytest.approx(120.5 / 220.5, abs=1e-4)
    assert summary["buildings"] == ["A栋"]

    # 同楼栋/楼层/铺位号重复 → 409
    dup = client.post(f"{P}/units", json={
        "building": "A栋", "floor": "1F", "unit_no": "A-101", "area": 1,
    })
    assert dup.status_code == 409

    upd = client.put(f"{P}/units/{uid}", json={"status": "negotiating", "rent_price": 85})
    assert upd.status_code == 200, upd.text
    assert upd.json()["status"] == "negotiating"
    assert upd.json()["rent_price"] == pytest.approx(85)

    assert client.delete(f"{P}/units/{uid}").status_code == 200
    assert client.delete(f"{P}/units/{uid}").status_code == 404
    assert client.get(f"{P}/units").json()["total"] == 1


def test_lead_funnel_followup_and_due(client):
    r = client.post(f"{P}/leads", json={
        "company": "合成餐饮公司", "contact": "张三", "phone": "13800000000",
        "demand_area": 200, "budget": 30000, "stage": "contact", "owner": "李四",
        "next_action_at": (date.today() - timedelta(days=1)).isoformat(),
    })
    assert r.status_code == 201, r.text
    lid = r.json()["id"]

    client.post(f"{P}/leads", json={
        "company": "合成零售公司", "stage": "signed", "demand_area": 100,
    })

    funnel = client.get(f"{P}/funnel").json()
    assert funnel["total"] == 2 and funnel["signed"] == 1
    assert funnel["conversion_rate"] == pytest.approx(0.5)
    stage = {s["stage"]: s for s in funnel["stages"]}
    assert stage["contact"]["count"] == 1
    assert stage["contact"]["area"] == pytest.approx(200.0)

    due = client.get(f"{P}/leads/due").json()
    assert [x["id"] for x in due] == [lid]

    f = client.post(f"{P}/leads/{lid}/followups", json={
        "at": date.today().isoformat(), "channel": "微信", "content": "发送报价单", "result": "待回复",
    })
    assert f.status_code == 201, f.text
    assert len(client.get(f"{P}/leads/{lid}/followups").json()) == 1

    # 非法渠道 → 422（Literal 校验）
    bad = client.post(f"{P}/leads/{lid}/followups", json={"channel": "飞鸽", "content": "x"})
    assert bad.status_code == 422

    assert client.delete(f"{P}/leads/{lid}").status_code == 200
    assert client.get(f"{P}/leads/{lid}/followups").json() == []


def test_export_xlsx(client):
    client.post(f"{P}/units", json={"building": "B栋", "unit_no": "B-201", "area": 50})
    r = client.get(f"{P}/export/units.xlsx")
    assert r.status_code == 200
    assert r.content[:2] == b"PK"
    assert "spreadsheetml" in r.headers["content-type"]
