"""Agent 团队配置化测试：agent_specs CRUD、路由预览、seed 幂等、关键词路由行为。

测试环境启动时 lifespan 会向空表灌入 7 位内置专家（is_builtin=True）；
配置变更端点会调用 invalidate，团队缓存随即失效，无需手动清理。
"""
from app.agents import team as team_mod
from app.database import SessionLocal
from app.models import AgentSpecModel
from app.seed import seed_agent_specs


def _names(body) -> set:
    return {a["name"] for a in body["agents"]}


# ---------- 启动 seed ----------

def test_builtin_agents_seeded_on_startup(client):
    body = client.get("/api/assistant/agents").json()
    assert len(body["agents"]) == 7
    assert _names(body) == {"realestate", "leasing", "operations", "marketing", "property", "security", "it_finance"}
    for agent in body["agents"]:
        assert agent["is_builtin"] is True
        assert agent["enabled"] is True
        assert agent["keywords"]
        assert agent["model_id"] is None


def test_seed_agent_specs_idempotent(client):
    # 表已有数据时再次 seed 不重复灌入、不覆盖用户改动
    with SessionLocal() as db:
        assert seed_agent_specs(db) == 0
        assert db.query(AgentSpecModel).count() == 7
    team_mod.invalidate_team_cache()


# ---------- CRUD ----------

def test_agent_crud_roundtrip(client):
    payload = {
        "name": "legal_advisor",
        "display_name": "法务顾问",
        "emoji": "⚖️",
        "color": "#795548",
        "description": "合同条款与合规咨询",
        "role_prompt": "你是「法务顾问」，专注合同审查与合规建议。",
        "keywords": ["合同", "法务", "诉讼"],
        "sort": 7,
    }
    resp = client.post("/api/assistant/agents", json=payload)
    assert resp.status_code == 201
    agent = resp.json()["agent"]
    assert agent["name"] == "legal_advisor" and agent["is_builtin"] is False
    assert agent["keywords"] == ["合同", "法务", "诉讼"]

    # 重名拒绝
    assert client.post("/api/assistant/agents", json=payload).status_code == 400

    # 更新关键词与显示名
    resp = client.put(f"/api/assistant/agents/{agent['id']}", json={"keywords": ["合同"], "display_name": "法务专员"})
    assert resp.status_code == 200
    assert resp.json()["agent"]["keywords"] == ["合同"]
    assert resp.json()["agent"]["display_name"] == "法务专员"

    # 删除
    assert client.delete(f"/api/assistant/agents/{agent['id']}").json()["deleted"] == "legal_advisor"
    assert client.delete(f"/api/assistant/agents/{agent['id']}").status_code == 404
    assert "legal_advisor" not in _names(client.get("/api/assistant/agents").json())


def test_agent_create_validation(client):
    base = {"name": "bad", "display_name": "x"}
    # name 必须小写字母开头
    assert client.post("/api/assistant/agents", json={**base, "name": "Bad Name"}).status_code == 422
    # keywords 必须是字符串列表
    assert client.post("/api/assistant/agents", json={**base, "name": "ok_name", "keywords": "合同"}).status_code == 422


def test_update_builtin_agent_allowed_and_delete_allowed(client):
    agents = client.get("/api/assistant/agents").json()["agents"]
    leasing = next(a for a in agents if a["name"] == "leasing")
    resp = client.put(f"/api/assistant/agents/{leasing['id']}", json={"description": "改动后的描述"})
    assert resp.status_code == 200
    assert resp.json()["agent"]["description"] == "改动后的描述"
    # 内置专家允许删除（前端负责警告）
    assert client.delete(f"/api/assistant/agents/{leasing['id']}").status_code == 200


# ---------- 路由预览 ----------

def test_route_test_hits(client):
    resp = client.post("/api/assistant/agents/route-test", json={"text": "这几张凭证的分录帮我核对一下"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["routed"] == "it_finance"
    assert body["matched_by"] == "关键词"
    top = body["matches"][0]
    assert top["name"] == "it_finance"
    assert set(top["hits"]) >= {"凭证", "分录"}


def test_route_test_default_when_no_hit(client):
    body = client.post("/api/assistant/agents/route-test", json={"text": "今天天气怎么样"}).json()
    assert body["matches"] == []
    assert body["matched_by"] == "默认"
    assert body["routed"] == "realestate"


def test_keyword_change_affects_routing(client):
    agents = client.get("/api/assistant/agents").json()["agents"]
    leasing = next(a for a in agents if a["name"] == "leasing")
    # 改掉招商专员的关键词后，原命中语句不再命中 leasing
    resp = client.put(f"/api/assistant/agents/{leasing['id']}", json={"keywords": ["不存在的词xyz"]})
    assert resp.status_code == 200
    body = client.post("/api/assistant/agents/route-test", json={"text": "写字楼租约的免租期条款"}).json()
    assert all(m["name"] != "leasing" for m in body["matches"])


def test_disabled_agent_not_routed(client):
    agents = client.get("/api/assistant/agents").json()["agents"]
    security = next(a for a in agents if a["name"] == "security")
    client.put(f"/api/assistant/agents/{security['id']}", json={"enabled": False})
    body = client.post("/api/assistant/agents/route-test", json={"text": "服务器发现漏洞要整改"}).json()
    assert all(m["name"] != "security" for m in body["matches"])


# ---------- 恢复出厂 ----------

def test_reset_restores_builtin(client):
    agents = client.get("/api/assistant/agents").json()["agents"]
    leasing = next(a for a in agents if a["name"] == "leasing")
    client.put(f"/api/assistant/agents/{leasing['id']}", json={"keywords": ["改动词"], "enabled": False})
    resp = client.post("/api/assistant/agents/reset")
    assert resp.status_code == 200
    assert set(resp.json()["restored"]) == {"realestate", "leasing", "operations", "marketing", "property", "security", "it_finance"}
    agents = client.get("/api/assistant/agents").json()["agents"]
    leasing = next(a for a in agents if a["name"] == "leasing")
    assert "免租" in leasing["keywords"] and leasing["enabled"] is True
    # reset 幂等：再次执行不新增行
    client.post("/api/assistant/agents/reset")
    assert len(client.get("/api/assistant/agents").json()["agents"]) == 7


# ---------- /team 端点兼容 ----------

def test_team_endpoint_reads_db_and_keeps_shape(client):
    body = client.get("/api/assistant/team").json()
    assert len(body["agents"]) == 7
    for agent in body["agents"]:
        # 原有字段保持不变
        assert {"name", "label", "emoji", "color", "description", "tools"} <= set(agent)
    # 停用一位后 /team 只返回启用中的专家
    agents = client.get("/api/assistant/agents").json()["agents"]
    marketing = next(a for a in agents if a["name"] == "marketing")
    client.put(f"/api/assistant/agents/{marketing['id']}", json={"enabled": False})
    body = client.get("/api/assistant/team").json()
    assert len(body["agents"]) == 6
    assert "marketing" not in _names(body)
