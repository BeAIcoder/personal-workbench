# -*- coding: utf-8 -*-
"""business_planning 企划管理模块测试（合成数据，临时库）。"""
import pytest

from app.modules import get_loaded_modules

P = "/api/business/planning"


def _campaign(client, name="合成活动", **over):
    body = {
        "name": name, "year": 2026, "category": "promo",
        "start_date": "2026-05-01", "end_date": "2026-05-07",
        "owner": "企划甲", "channel": "公众号", "target": "客流提升 10%",
        "status": "running",
        "budget_items": [
            {"item": "物料", "budget": 10000, "actual": 12000},
            {"item": "演艺", "budget": 20000, "actual": 15000},
        ],
        "metrics": {"footfall_lift": 8.5, "sales_lift": 6.2, "roi": 1.8, "review": "复购一般"},
    }
    body.update(over)
    return client.post(f"{P}/campaigns", json=body)


def test_manifest_loaded():
    loaded = {m.name: m for m in get_loaded_modules()}
    assert "business_planning" in loaded
    m = loaded["business_planning"]
    assert m.prefix == "/api/business/planning"
    assert m.tables == ["mod_plan_campaign", "mod_plan_vendor"]


def test_campaign_budget_summary_calendar(client):
    r = _campaign(client)
    assert r.status_code == 201, r.text
    c = r.json()
    cid = c["id"]
    assert c["budget_total"] == pytest.approx(30000.0)
    assert c["actual_total"] == pytest.approx(27000.0)
    assert c["over_budget"] is False
    assert c["variance"] == pytest.approx(3000.0)
    assert c["roi"] == pytest.approx(1.8)

    _campaign(client, name="超支活动", budget_items=[
        {"item": "投放", "budget": 10000, "actual": 13000},
    ])

    lst = client.get(f"{P}/campaigns", params={"year": 2026}).json()
    assert lst["total"] == 2

    cal = client.get(f"{P}/campaigns/calendar", params={"year": 2026}).json()
    may = next(m for m in cal["months"] if m["month"] == 5)
    assert {x["name"] for x in may["items"]} == {"合成活动", "超支活动"}

    s = client.get(f"{P}/summary", params={"year": 2026}).json()
    assert s["campaigns"] == 2
    assert s["budget_total"] == pytest.approx(40000.0)
    assert s["actual_total"] == pytest.approx(40000.0)
    assert s["over_budget_count"] == 1
    assert s["running"] == 2
    assert s["avg_roi"] == pytest.approx(1.8)

    upd = client.put(f"{P}/campaigns/{cid}", json={"status": "done",
                                                  "metrics": {"roi": 2.0, "review": "收官"}})
    assert upd.status_code == 200, upd.text
    assert upd.json()["status"] == "done"
    assert upd.json()["roi"] == pytest.approx(2.0)

    assert client.delete(f"{P}/campaigns/{cid}").status_code == 200


def test_campaign_validation(client):
    # 结束早于开始
    r = _campaign(client, start_date="2026-06-10", end_date="2026-06-01")
    assert r.status_code == 400
    # 空预算科目（schema min_length 拦截为 422；服务层也有一道 400）
    r = _campaign(client, budget_items=[{"item": "", "budget": 1}])
    assert r.status_code in (400, 422)
    # 未知状态由 Literal 拦截
    r = _campaign(client, status="bogus")
    assert r.status_code == 422
    # 年度越界
    r = _campaign(client, year=1999)
    assert r.status_code == 422


def test_vendor_crud(client):
    r = client.post(f"{P}/vendors", json={
        "name": "合成美陈公司", "service": "美陈", "contact": "赵六",
        "phone": "13900000000", "rating": 5,
    })
    assert r.status_code == 201, r.text
    vid = r.json()["id"]

    lst = client.get(f"{P}/vendors", params={"service": "美陈"}).json()
    assert [v["id"] for v in lst] == [vid]

    upd = client.put(f"{P}/vendors/{vid}", json={"rating": 3})
    assert upd.status_code == 200
    assert upd.json()["rating"] == 3
    assert client.put(f"{P}/vendors/{vid}", json={"rating": 9}).status_code == 422
    assert client.delete(f"{P}/vendors/{vid}").status_code == 200


def test_export(client):
    _campaign(client)
    r = client.get(f"{P}/export/campaigns.xlsx", params={"year": 2026})
    assert r.status_code == 200 and r.content[:2] == b"PK"
