# -*- coding: utf-8 -*-
"""技能市场 + MCP 服务器目录测试（v1.18.0）：扫描/安装/目录添加，全部临时目录。"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.agents import skill_market as sm
from app.agents import skill_store
from app.main import app


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """临时 QwenPaw 池 + 临时平台技能池 + 临时 WorkBuddy 家。"""
    pool = tmp_path / "skill_pool"
    store = tmp_path / "skills"
    wb_home = tmp_path / ".workbuddy"

    qpack = pool / "demo-market-skill"
    qpack.mkdir(parents=True)
    (qpack / "SKILL.md").write_text(
        "---\nname: demo-market-skill\ndescription: 市场演示技能\nversion: 1.2.0\n---\n\n# 演示\n",
        encoding="utf-8")
    # 中文目录包（QwenPaw 池真实存在此类目录）
    cpack = pool / "演示中文包"
    cpack.mkdir()
    (cpack / "SKILL.md").write_text(
        "---\nname: cn-pack\ndescription: 中文目录技能\n---\n\n# 中文\n", encoding="utf-8")

    wskill = wb_home / "buddy-skill-store" / "s" / "b" / "c"
    wskill.mkdir(parents=True)
    (wskill / "SKILL.md").write_text(
        "---\nname: wb-market-skill\ndescription: WorkBuddy 市场技能\n---\n\n# WB\n", encoding="utf-8")

    def _pool():
        return pool

    def _store():
        store.mkdir(parents=True, exist_ok=True)
        return store

    monkeypatch.setenv("QWENPAW_SKILL_POOL", str(pool))
    monkeypatch.setattr(sm, "_qwenpaw_pool", _pool)
    monkeypatch.setattr(skill_store, "store_dir", _store)
    monkeypatch.setattr(sm.workbuddy_sync, "DEFAULT_HOME", str(wb_home))
    return {"pool": pool, "store": store, "wb": wb_home}


@pytest.fixture()
def client(env):
    with TestClient(app) as c:
        yield c


def test_scan_market_aggregates_sources(env):
    plan = sm.scan_market("")
    names = {i["name"] for i in plan["items"]}
    assert {"demo-market-skill", "cn-pack", "wb-market-skill"}.issubset(names)
    by_name = {i["name"]: i for i in plan["items"]}
    assert by_name["demo-market-skill"]["source"] == "qwenpaw"
    assert by_name["demo-market-skill"]["version"] == "1.2.0"
    assert by_name["cn-pack"]["source"] == "qwenpaw"
    assert by_name["wb-market-skill"]["source"] == "workbuddy"


def test_scan_market_keyword_filter(env):
    plan = sm.scan_market("演示")
    assert any(i["name"] == "demo-market-skill" for i in plan["items"])
    assert not any(i["name"] == "wb-market-skill" for i in plan["items"])


def test_install_from_market_and_idempotent(env):
    r = sm.install_from_market("demo-market-skill")
    assert r["installed"] is True and r["updated"] is False
    assert (env["store"] / "demo-market-skill" / "SKILL.md").is_file()
    assert "demo-market-skill" in skill_store.disabled_packs(env["store"])  # 默认停用

    # 幂等：再次安装 = 更新，启停状态保留
    skill_store.set_enabled("demo-market-skill", True)
    r2 = sm.install_from_market("demo-market-skill")
    assert r2["updated"] is True
    assert "demo-market-skill" not in skill_store.disabled_packs(env["store"])


def test_install_normalizes_cn_dir_name(env):
    r = sm.install_from_market("cn-pack")
    assert r["name"] == "cn-pack"  # 归一自目录名「演示中文包」的 frontmatter name
    assert (env["store"] / "cn-pack" / "SKILL.md").is_file()
    text = (env["store"] / "cn-pack" / "SKILL.md").read_text(encoding="utf-8")
    assert "name: cn-pack" in text


def test_install_unknown_rejected(env):
    with pytest.raises(ValueError):
        sm.install_from_market("不存在的技能")


def test_api_market_endpoints(client, env):
    r = client.get("/api/skills/market/scan?keyword=市场演示")
    assert r.status_code == 200
    assert any(i["name"] == "demo-market-skill" for i in r.json()["items"])

    r2 = client.post("/api/skills/market/install", json={"name": "demo-market-skill"})
    assert r2.status_code == 200 and r2.json()["installed"] is True


# ---------- MCP 服务器目录 ----------

def test_mcp_catalog_list_and_add(client):
    r = client.get("/api/mcp/catalog")
    assert r.status_code == 200
    body = r.json()
    ids = {s["id"] for s in body["servers"]}
    assert {"filesystem", "fetch", "everything"}.issubset(ids)
    assert all("installed" in s for s in body["servers"])

    r2 = client.post("/api/mcp/catalog/fetch/add", json={"name": "fetch"})
    assert r2.status_code == 201, r2.text
    assert r2.json()["server"]["enabled"] is False  # 默认停用

    r3 = client.get("/api/mcp/catalog")
    assert next(s for s in r3.json()["servers"] if s["id"] == "fetch")["installed"] is True

    # 重复添加：400（服务器名已存在）
    r4 = client.post("/api/mcp/catalog/fetch/add", json={"name": "fetch"})
    assert r4.status_code == 400


def test_mcp_catalog_unknown_id(client):
    assert client.post("/api/mcp/catalog/不存在/add", json={"name": "x"}).status_code == 404