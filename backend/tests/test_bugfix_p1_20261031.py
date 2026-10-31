# -*- coding: utf-8 -*-
"""P1 回归（2026-10-31）：入参收口与模型归属校验。

覆盖：
- 空白日程标题：PUT 直接 422，不再「先 commit 再被响应校验拒绝」污染该行
- 空白专家显示名：同上
- 模型端点跨供应商编辑/删除：400/404，不得改到别的供应商

全部使用合成数据；client 由 conftest 提供（临时 SQLite，不写真实 backend/data）。
"""
import pytest


def _schedule_payload(**kw):
    base = {
        "title": "月度经营分析会",
        "start_time": "2026-10-08T09:30:00",
        "end_time": "2026-10-08T11:00:00",
        "location": "会议室A",
        "all_day": False,
    }
    base.update(kw)
    return base


def test_schedule_blank_title_rejected_without_polluting_row(client):
    sid = client.post("/api/schedules", json=_schedule_payload()).json()["id"]

    resp = client.put(f"/api/schedules/{sid}", json={"title": "   "})
    assert resp.status_code == 422, resp.text

    # 该行未被污染：详情与列表仍可用，标题保持原值
    detail = client.get(f"/api/schedules/{sid}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["title"] == "月度经营分析会"
    assert client.get("/api/schedules").status_code == 200


def test_schedule_blank_title_on_create_rejected(client):
    resp = client.post("/api/schedules", json=_schedule_payload(title="  "))
    assert resp.status_code == 422, resp.text


def _create_agent(client, name: str):
    resp = client.post("/api/assistant/agents", json={
        "name": name,
        "display_name": "法务顾问",
        "emoji": "⚖️",
        "color": "#409EFF",
        "description": "合同条款与合规咨询",
        "role_prompt": "你是「法务顾问」，专注合同审查与合规建议。",
        "keywords": ["合同", "法务", "诉讼"],
        "sort": 7,
    })
    assert resp.status_code == 201, resp.text
    return resp.json()["agent"]


def test_agent_blank_display_name_rejected_without_polluting_row(client):
    agent = _create_agent(client, "p1_blank_display")

    resp = client.put(f"/api/assistant/agents/{agent['id']}", json={"display_name": "  "})
    assert resp.status_code == 422, resp.text

    # 列表仍可序列化、显示名保持原值
    listed = client.get("/api/assistant/agents")
    assert listed.status_code == 200, listed.text
    row = next(a for a in listed.json()["agents"] if a["id"] == agent["id"])
    assert row["display_name"] == "法务顾问"


def _mk_provider(client, name: str):
    resp = client.post("/api/assistant/providers", json={
        "name": name, "protocol": "openai",
        "base_url": "https://api.example.com/v1", "api_key": "sk-test-0001",
    })
    assert resp.status_code == 200, resp.text
    return resp.json()["provider"]["id"]


def _models_of(client, pid: int):
    prov = next(p for p in client.get("/api/assistant/providers").json()["providers"] if p["id"] == pid)
    return prov["models"]


def test_model_cannot_be_edited_across_providers(client):
    pa = _mk_provider(client, "供应商A")
    pb = _mk_provider(client, "供应商B")
    mid = client.post(f"/api/assistant/providers/{pa}/models", json={"model": "model-a"}).json()["model"]["id"]

    # 用 B 的路径修改 A 的模型：拒绝且 A 的模型名不变
    hijack = client.post(f"/api/assistant/providers/{pb}/models", json={"model": "hijacked", "model_id": mid})
    assert hijack.status_code == 400, hijack.text
    assert next(m for m in _models_of(client, pa) if m["id"] == mid)["model"] == "model-a"


def test_model_cannot_be_deleted_across_providers(client):
    pa = _mk_provider(client, "供应商A2")
    pb = _mk_provider(client, "供应商B2")
    mid = client.post(f"/api/assistant/providers/{pa}/models", json={"model": "model-a2"}).json()["model"]["id"]

    resp = client.delete(f"/api/assistant/providers/{pb}/models/{mid}")
    assert resp.status_code == 404, resp.text
    assert any(m["id"] == mid for m in _models_of(client, pa))

    # 正确路径可以删除
    ok = client.delete(f"/api/assistant/providers/{pa}/models/{mid}")
    assert ok.status_code == 200, ok.text
    assert all(m["id"] != mid for m in _models_of(client, pa))
