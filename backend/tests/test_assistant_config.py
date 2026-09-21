"""AI 助手配置接口测试（ZCode 式：供应商 → 模型 两级 + Agent 工作参数）。

测试环境 .env 为空且无激活模型 → 默认未配置；各用例通过 client 夹具
获得全新临时库，配置写入仅在本用例内生效。
"""
import asyncio

from app.agents import config_store, orchestrator
from app.agents.team import QUERY_TOOLS, TEAM


def test_config_endpoint_defaults(client):
    body = client.get("/api/assistant/config").json()
    assert body["llm"]["configured"] is False
    assert len(body["presets"]) >= 6
    assert all(p["protocol"] == "openai" for p in body["presets"])
    assert body["providers"] == [] and body["models"] == []


def test_save_globals(client):
    body = client.put("/api/assistant/config", json={"max_iters": 9, "history_inject": 6, "enable_memory": True}).json()
    assert body is not None
    again = client.get("/api/assistant/config").json()["config"]
    # 全局参数保存在 agent_settings；模型字段走供应商接口
    assert again["max_iters"] == 9
    assert again["history_inject"] == 6


def test_save_globals_validates(client):
    assert client.put("/api/assistant/config", json={"max_iters": 99}).status_code == 422
    assert client.put("/api/assistant/config", json={"history_inject": 999}).status_code == 422


def test_test_connection_requires_complete_config(client):
    body = client.post(
        "/api/assistant/config/test",
        json={"config": {"base_url": "https://x.com/v1", "model": "m"}},  # 缺 api_key
    ).json()
    assert body["text"]["ok"] is False
    assert "API Key" in body["text"]["error"]


def test_multimodal_png_util_returns_valid_data_uri():
    uri = config_store._red_png_data_uri()
    assert uri.startswith("data:image/png;base64,")
    import base64

    raw = base64.b64decode(uri.split(",", 1)[1])
    assert raw[:8] == b"\x89PNG\r\n\x1a\n"  # 合法 PNG 头


# ---------------- 供应商 / 模型 ----------------

def _mk_provider(client, name="测试供应商", protocol="openai", base_url="https://x.com/v1", api_key="sk-x-1234"):
    body = client.post(
        "/api/assistant/providers",
        json={"name": name, "protocol": protocol, "base_url": base_url, "api_key": api_key},
    ).json()
    return body["provider"]["id"]


def test_provider_crud(client):
    pid = _mk_provider(client)
    provs = client.get("/api/assistant/providers").json()["providers"]
    prov = next(p for p in provs if p["id"] == pid)
    assert prov["name"] == "测试供应商" and prov["api_key_set"] is True and prov["enabled"] is True

    # 更新（掩码哨兵保留 key）
    client.post("/api/assistant/providers", json={"provider_id": pid, "name": "改名", "api_key": "********"})
    prov = next(p for p in client.get("/api/assistant/providers").json()["providers"] if p["id"] == pid)
    assert prov["name"] == "改名" and prov["api_key_set"] is True

    assert client.delete(f"/api/assistant/providers/{pid}").status_code == 200
    assert all(p["id"] != pid for p in client.get("/api/assistant/providers").json()["providers"])


def test_provider_models_and_activate(client):
    pid = _mk_provider(client)
    m1 = client.post(
        f"/api/assistant/providers/{pid}/models",
        json={"model": "glm-5.2", "context_size": 1000000, "max_tokens": 128000},
    ).json()["model"]["id"]
    m2 = client.post(
        f"/api/assistant/providers/{pid}/models",
        json={"model": "deepseek-v4-flash", "context_size": 1000000, "max_tokens": 384000, "multimodal": False},
    ).json()["model"]["id"]

    # 激活 m1 → 配置切到该模型
    body = client.post(f"/api/assistant/models/{m1}/activate").json()
    assert body["llm"]["configured"] is True
    assert body["config"]["model"] == "glm-5.2"
    assert body["config"]["protocol"] == "openai"
    assert next(m for m in body["models"] if m["id"] == m1)["active"] is True

    # 激活 m2 → 切换
    body = client.post(f"/api/assistant/models/{m2}/activate").json()
    assert body["config"]["model"] == "deepseek-v4-flash"

    # 删除激活中的模型 → 激活清空，回退 .env 兜底（测试环境为空）
    client.delete(f"/api/assistant/providers/{pid}/models/{m2}")
    assert client.get("/api/assistant/config").json()["llm"]["configured"] is False


def test_activate_missing_model_404(client):
    assert client.post("/api/assistant/models/9999/activate").status_code == 404


def test_provider_model_requires_valid_provider(client):
    resp = client.post("/api/assistant/providers/999/models", json={"model": "x"})
    assert resp.status_code == 400


def test_models_flat_skips_disabled(client):
    pid = _mk_provider(client)
    m1 = client.post(f"/api/assistant/providers/{pid}/models", json={"model": "mma"}).json()["model"]["id"]
    client.post(f"/api/assistant/providers/{pid}/models", json={"model": "mmb", "enabled": False})
    flat = client.get("/api/assistant/models").json()["models"]
    labels = [m["label"] for m in flat]
    assert any("mma" in x for x in labels)
    assert not any("mmb" in x for x in labels)

    # 停用整个供应商 → 模型从扁平列表消失
    client.post("/api/assistant/providers", json={"provider_id": pid, "name": "测试供应商", "enabled": False})
    flat = client.get("/api/assistant/models").json()["models"]
    assert not any("mma" in x for x in flat)
    orchestrator.invalidate()


# ---------------- 模型级配置驱动 Agent（离线构造） ----------------

def test_provider_config_drives_build_model(client):
    from app.agents.llm import build_model

    pid = _mk_provider(client)
    m1 = client.post(f"/api/assistant/providers/{pid}/models", json={"model": "test-model"}).json()["model"]["id"]
    client.post(f"/api/assistant/models/{m1}/activate")
    orchestrator.invalidate()
    try:
        cfg = config_store.load()
        assert cfg["model"] == "test-model" and cfg["base_url"] == "https://x.com/v1"
        model = build_model(cfg)
        assert model is not None
    finally:
        client.delete(f"/api/assistant/providers/{pid}")
        orchestrator.invalidate()


# ---------------- 工作模式 ----------------

def test_resolve_mode():
    assert orchestrator._resolve_mode("readonly") == "readonly"
    assert orchestrator._resolve_mode("deep") == "deep"
    assert orchestrator._resolve_mode("unknown") == "standard"


def test_work_modes_registry(client):
    body = client.get("/api/assistant/team").json()
    assert set(body["work_modes"]) == {"standard", "readonly", "deep"}


def test_readonly_agent_has_query_tools_only(client):
    pid = _mk_provider(client)
    m1 = client.post(f"/api/assistant/providers/{pid}/models", json={"model": "test-model"}).json()["model"]["id"]
    client.post(f"/api/assistant/models/{m1}/activate")
    orchestrator.invalidate()
    try:
        agent = asyncio.run(orchestrator._build_agent(TEAM["it_finance"], "readonly"))
        schemas = asyncio.run(agent.toolkit.get_tool_schemas())
        names = {s["function"]["name"] for s in schemas}
        assert names and names <= set(QUERY_TOOLS)
        assert "create_task" not in names

        agent_full = asyncio.run(orchestrator._build_agent(TEAM["it_finance"], "standard"))
        schemas_full = asyncio.run(agent_full.toolkit.get_tool_schemas())
        names_full = {s["function"]["name"] for s in schemas_full}
        assert "create_task" in names_full
    finally:
        client.delete(f"/api/assistant/providers/{pid}")
        orchestrator.invalidate()
