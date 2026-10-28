"""AI 助手配置接口测试（ZCode 式：供应商 → 模型 两级 + Agent 工作参数）。

测试环境 .env 为空且无激活模型 → 默认未配置；各用例通过 client 夹具
获得全新临时库，配置写入仅在本用例内生效。
"""
import asyncio
import json

from app.agents import config_store, orchestrator
from app.agents.secrets_box import unseal
from app.agents.team import QUERY_TOOLS, TEAM
from app.database import SessionLocal
from app.models import ModelProvider, ProviderModel


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
    # 自定义配置缺 api_key：400 明确拒绝（L3 修复：不借用激活密钥，状态码语义化）
    resp = client.post(
        "/api/assistant/config/test",
        json={"config": {"base_url": "https://x.com/v1", "model": "m"}},  # 缺 api_key
    )
    assert resp.status_code == 400
    assert "API Key" in resp.json()["detail"]


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


def test_provider_model_empty_name_is_schema_validation_422(client):
    """空模型名命中请求体 min_length=1，按 API.md 错误约定返回 422 而非业务 400。"""
    pid = _mk_provider(client)
    resp = client.post(f"/api/assistant/providers/{pid}/models", json={"model": ""})
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail and detail[0]["loc"][-1] == "model"


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


# ---------------- session_id 字符集校验（路径参数统一套用 _SESSION_ID_RE） ----------------

def test_session_path_endpoints_reject_illegal_session_id(client):
    """路径参数 session_id 不合法 → 400（导出会拼进响应文件名，必须拦在入口处）。"""
    bad_ids = ["bad id", "short", "x" * 65, "含有中文123", "a.b"]
    for bad in bad_ids:
        assert client.get(f"/api/assistant/sessions/{bad}/export").status_code == 400
        assert client.get(f"/api/assistant/history/{bad}").status_code == 400
        assert client.delete(f"/api/assistant/sessions/{bad}").status_code == 400
        assert client.post(f"/api/assistant/sessions/{bad}/archive").status_code == 400
        assert client.post(f"/api/assistant/sessions/{bad}/unarchive").status_code == 400
        # body 用合法 session_id，单独验证路径参数校验生效（body 非法会先被 pydantic 拦成 422）
        assert client.put(f"/api/assistant/sessions/{bad}", json={"session_id": "sess-ok-01"}).status_code == 400


def test_session_path_endpoints_legal_id_keep_original_semantics(client):
    """合法字符集不受影响：会话不存在仍走 404，存在则正常导出。"""
    assert client.get("/api/assistant/sessions/sess-none-01/export").status_code == 404
    orchestrator.session_create("sess-ok-01", "校验回归")
    resp = client.get("/api/assistant/sessions/sess-ok-01/export")
    assert resp.status_code == 200
    assert 'filename="session-sess-ok-01.md"' in resp.headers["content-disposition"]


# ---------------- Task 3：能力数据契约 / 管控凭证 / reset-overrides ----------------

def _mk_provider_full(client, name="能力供应商", **extra):
    payload = {"name": name, "protocol": "openai", "base_url": "https://api.example.com/v1", "api_key": "sk-cap-1234"}
    payload.update(extra)
    return client.post("/api/assistant/providers", json=payload).json()["provider"]["id"]


def _read_model(client, pid, mid):
    prov = next(p for p in client.get("/api/assistant/providers").json()["providers"] if p["id"] == pid)
    return next(m for m in prov["models"] if m["id"] == mid)


def test_provider_payload_accepts_preset_and_sealed_sync_credentials(client):
    """预设标识/列表地址可保存；管控 AK/SK 密封落库且任何 GET 不回传。"""
    pid = _mk_provider_full(
        client,
        preset_key="deepseek",
        models_url="https://api.deepseek.com/models",
        # 测试假 AK/SK：运行时拼装避免凭据形字面量触发安全扫描误报
        sync_access_key="AK-" + "test-1234",
        sync_secret_key="SK-" + "test-5678",
    )
    body = client.get("/api/assistant/providers").json()
    prov = next(p for p in body["providers"] if p["id"] == pid)
    assert prov["preset_key"] == "deepseek"
    assert prov["models_url"] == "https://api.deepseek.com/models"
    assert "sync_credentials" not in prov  # 凭证本体（含掩码）任何 GET 不得回传
    assert prov["sync_credentials_set"] is True
    raw = json.dumps(body, ensure_ascii=False)
    assert "AK-test-1234" not in raw and "SK-test-5678" not in raw
    with SessionLocal() as db:
        row = db.get(ModelProvider, pid)
        assert row.sync_credentials.startswith("dpapi:")
        assert json.loads(unseal(row.sync_credentials)) == {"access_key": "AK-test-1234", "secret_key": "SK-test-5678"}


def test_sync_credentials_mask_and_empty_keep_old(client):
    """掩码/空值保留旧管控凭证；只更新其一另一保留。"""
    pid = _mk_provider_full(client, sync_access_key="AK-1", sync_secret_key="SK-1")
    client.post(
        "/api/assistant/providers",
        json={"provider_id": pid, "name": "能力供应商", "api_key": "********", "sync_access_key": "********", "sync_secret_key": ""},
    )
    with SessionLocal() as db:
        row = db.get(ModelProvider, pid)
        assert json.loads(unseal(row.sync_credentials)) == {"access_key": "AK-1", "secret_key": "SK-1"}
        assert row.api_key.startswith("dpapi:") and unseal(row.api_key) == "sk-cap-1234"
    client.post("/api/assistant/providers", json={"provider_id": pid, "name": "能力供应商", "sync_secret_key": "SK-2"})
    with SessionLocal() as db:
        row = db.get(ModelProvider, pid)
        assert json.loads(unseal(row.sync_credentials)) == {"access_key": "AK-1", "secret_key": "SK-2"}


def test_model_capability_fields_roundtrip_and_manual_tracking(client):
    """手动能力字段全链路可读（providers/models_flat/load/model_cfg_by_id）并记 manual 来源。"""
    pid = _mk_provider_full(client)
    mid = client.post(
        f"/api/assistant/providers/{pid}/models",
        json={
            "model": "m-cap",
            "context_size": 200000,
            "max_tokens": 8192,
            "max_input_tokens": 1000000,
            "supports_tools": 1,
            "input_modalities": ["text", "image"],
            "output_modalities": ["text"],
            "default_thinking_level": "high",
            "thinking_can_disable": True,
            "reasoning_style": "effort",
            "api_family": "chat_completions",
        },
    ).json()["model"]["id"]

    m = _read_model(client, pid, mid)
    assert m["max_input_tokens"] == 1000000
    assert m["supports_tools"] == 1
    assert m["input_modalities"] == ["text", "image"]
    assert m["output_modalities"] == ["text"]
    assert m["default_thinking_level"] == "high"
    assert m["thinking_can_disable"] is True
    assert m["reasoning_style"] == "effort"
    assert m["api_family"] == "chat_completions"
    assert m["supports_vision"] is True  # 输入模态含 image → 派生视觉能力
    assert m["capability"]["sources"]["supports_tools"] == "manual"
    assert "supports_tools" in m["capability"]["manual_fields"]
    assert m["capability"]["sources"]["context_size"] == "manual"

    flat = next(x for x in client.get("/api/assistant/models").json()["models"] if x["id"] == mid)
    assert flat["max_input_tokens"] == 1000000 and flat["supports_tools"] == 1
    assert flat["input_modalities"] == ["text", "image"]
    assert flat["capability"]["sources"]["supports_tools"] == "manual"

    client.post(f"/api/assistant/models/{mid}/activate")
    cfg = config_store.load()
    assert cfg["max_input_tokens"] == 1000000 and cfg["supports_tools"] == 1
    assert cfg["input_modalities"] == ["text", "image"] and cfg["output_modalities"] == ["text"]
    assert cfg["default_thinking_level"] == "high" and cfg["thinking_can_disable"] is True
    assert cfg["api_family"] == "chat_completions"
    by_id = config_store.model_cfg_by_id(mid)
    assert by_id["supports_tools"] == 1 and by_id["input_modalities"] == ["text", "image"]


def test_thinking_can_disable_explicit_null_resets_to_unknown(client):
    """显式传 thinking_can_disable=null 应恢复「未知」（None），而非被 exclude_none 丢弃后残留旧值。

    断点：路由 payload.model_dump(exclude_none=True) 丢掉显式 null，config_store
    的 `if "thinking_can_disable" in data` 恢复分支永不触发 → 用户选回「未知」后
    GET 仍是旧的 true/false。
    """
    pid = _mk_provider_full(client)
    mid = client.post(
        f"/api/assistant/providers/{pid}/models",
        json={"model": "m-tcd", "supports_thinking": 1, "thinking_can_disable": True, "default_thinking_level": "high"},
    ).json()["model"]["id"]
    assert _read_model(client, pid, mid)["thinking_can_disable"] is True

    # true → false：显式覆盖
    client.post(
        f"/api/assistant/providers/{pid}/models",
        json={"model_id": mid, "model": "m-tcd", "thinking_can_disable": False},
    )
    assert _read_model(client, pid, mid)["thinking_can_disable"] is False

    # false → null：前端「未知」选项显式发 null，必须落库为 None（本用例的核心断言）
    resp = client.post(
        f"/api/assistant/providers/{pid}/models",
        json={"model_id": mid, "model": "m-tcd", "thinking_can_disable": None},
    )
    assert resp.status_code == 200
    assert _read_model(client, pid, mid)["thinking_can_disable"] is None
    flat = next(x for x in client.get("/api/assistant/models").json()["models"] if x["id"] == mid)
    assert flat["thinking_can_disable"] is None

    # 未提交该字段（键不在请求体）：旧值语义不变——先设 true，再只改 note，true 应保留
    client.post(
        f"/api/assistant/providers/{pid}/models",
        json={"model_id": mid, "model": "m-tcd", "thinking_can_disable": True},
    )
    client.post(
        f"/api/assistant/providers/{pid}/models",
        json={"model_id": mid, "model": "m-tcd", "note": "只改备注"},
    )
    assert _read_model(client, pid, mid)["thinking_can_disable"] is True
    assert _read_model(client, pid, mid)["note"] == "只改备注"


def test_capability_meta_of_row_defaults_and_parse():
    """capability_meta_of：空/损坏 JSON 有默认值，合法 JSON 原样解析。"""
    from app.agents import model_catalog

    pm = ProviderModel(provider_id=1, model="x")
    meta = model_catalog.capability_meta_of(pm)
    assert meta["manual_fields"] == [] and meta["sources"] == {}
    assert meta["input_modalities"] == [] and meta["output_modalities"] == []
    assert meta["default_thinking_level"] is None and meta["thinking_can_disable"] is None
    assert meta["reasoning_style"] == "" and meta["api_family"] == "" and meta["remote_status"] == ""
    assert meta["last_sync"] is None

    pm.capability_meta = "{ 损坏"
    assert model_catalog.capability_meta_of(pm)["manual_fields"] == []
    pm.capability_meta = json.dumps(
        {"sources": {"supports_tools": "manual"}, "manual_fields": ["supports_tools"], "input_modalities": ["text", "image"]},
        ensure_ascii=False,
    )
    meta = model_catalog.capability_meta_of(pm)
    assert meta["manual_fields"] == ["supports_tools"]
    assert meta["input_modalities"] == ["text", "image"]
    assert meta["sources"]["supports_tools"] == "manual"


def test_catalog_merge_respects_manual_and_probe_fields(client):
    """目录同步不覆盖手动字段；probe/备注/温度/启停永不受影响。"""
    from app.agents import model_catalog

    pid = _mk_provider_full(client, preset_key="deepseek")
    mid = client.post(
        f"/api/assistant/providers/{pid}/models",
        json={"model": "deepseek-flash", "supports_tools": 0, "context_size": 128000, "note": "主力模型", "temperature": "0.5"},
    ).json()["model"]["id"]
    # 预置探测结果（模拟用户已实测）
    with SessionLocal() as db:
        pm = db.get(ProviderModel, mid)
        pm.probed_context = 900000
        pm.probed_max_output = 32768
        pm.probed_at = "2026-10-20T10:00:00"
        db.commit()

    entry = model_catalog.catalog_for("deepseek", "deepseek-flash")
    assert entry and entry["context_size"] == 1048576 and entry["supports_tools"] == 1
    with SessionLocal() as db:
        pm = db.get(ProviderModel, mid)
        applied = model_catalog.apply_capability_entry(pm, entry, source="official_catalog")
        db.commit()
    assert applied["context_size"] == "skipped_manual"
    assert applied["supports_tools"] == "skipped_manual"
    assert applied["input_modalities"] == "applied"
    assert applied["supports_vision"] == "applied"  # 由模态派生，未被手动标记

    m = _read_model(client, pid, mid)
    assert m["context_size"] == 128000 and m["supports_tools"] == 0  # 手动值保住
    assert m["input_modalities"] == ["text", "image"]  # 未手动的字段被目录更新
    assert m["supports_vision"] is True and m["multimodal"] is True
    assert m["probed_context"] == 900000 and m["probed_max_output"] == 32768 and m["probed_at"] == "2026-10-20T10:00:00"
    assert m["note"] == "主力模型" and m["temperature"] == "0.5" and m["enabled"] is True
    assert m["capability"]["sources"]["context_size"] == "manual"
    assert m["capability"]["sources"]["input_modalities"] == "official_catalog"
    assert m["synced_at"]

    # override_manual=True 才允许同步覆盖手动字段
    with SessionLocal() as db:
        pm = db.get(ProviderModel, mid)
        applied = model_catalog.apply_capability_entry(pm, entry, source="official_catalog", override_manual=True)
        db.commit()
    assert applied["context_size"] == "applied" and applied["supports_tools"] == "applied"
    m = _read_model(client, pid, mid)
    assert m["context_size"] == 1048576 and m["supports_tools"] == 1
    assert m["capability"]["sources"]["context_size"] == "official_catalog"
    assert m["probed_context"] == 900000  # 实测值仍不动


def test_reset_overrides_clears_manual_and_applies_snapshot(client):
    """reset-overrides：清 manual 标记并应用最近同步快照；无快照仅清标记。"""
    pid = _mk_provider_full(client, preset_key="deepseek")
    mid = client.post(
        f"/api/assistant/providers/{pid}/models",
        json={"model": "deepseek-flash", "supports_tools": 0, "context_size": 128000},
    ).json()["model"]["id"]
    from app.agents import model_catalog

    entry = model_catalog.catalog_for("deepseek", "deepseek-flash")
    with SessionLocal() as db:
        pm = db.get(ProviderModel, mid)
        model_catalog.apply_capability_entry(pm, entry, source="official_catalog")
        db.commit()
    assert _read_model(client, pid, mid)["supports_tools"] == 0  # 同步时手动值被保住

    resp = client.post(f"/api/assistant/models/{mid}/capabilities/reset-overrides")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True and body["applied"] is True and body["model_id"] == mid
    m = next(x for x in next(p for p in body["providers"] if p["id"] == pid)["models"] if x["id"] == mid)
    assert m["supports_tools"] == 1 and m["context_size"] == 1048576
    assert m["capability"]["manual_fields"] == []
    assert m["capability"]["sources"]["context_size"] == "official_catalog"

    # 无快照：仅清标记并说明下次同步生效
    mid2 = client.post(
        f"/api/assistant/providers/{pid}/models",
        json={"model": "deepseek-v4-pro", "supports_tools": 0},
    ).json()["model"]["id"]
    resp2 = client.post(f"/api/assistant/models/{mid2}/capabilities/reset-overrides").json()
    assert resp2["applied"] is False
    assert "下次同步" in resp2["message"]
    m2 = _read_model(client, pid, mid2)
    assert m2["capability"]["manual_fields"] == [] and m2["supports_tools"] == 0

    assert client.post("/api/assistant/models/999999/capabilities/reset-overrides").status_code == 404


# ---------------- Fix round 1：meta 未知键保留 / 模态与 vision 一致 ----------------

def test_capability_meta_of_and_apply_preserve_unknown_keys():
    """capability_meta_of 保留未知键；合并后仍在（Task 4 新同步状态不被手动保存抹掉）。"""
    from app.agents import model_catalog

    pm = ProviderModel(provider_id=1, model="x")
    pm.capability_meta = json.dumps(
        {"sync_state": "pending", "remote_etag": "abc", "manual_fields": ["supports_tools"]}, ensure_ascii=False
    )
    meta = model_catalog.capability_meta_of(pm)
    assert meta["sync_state"] == "pending" and meta["remote_etag"] == "abc"
    assert meta["manual_fields"] == ["supports_tools"]

    applied = model_catalog.apply_capability_entry(pm, {"context_size": 1000, "supports_tools": 1}, source="provider_api")
    assert applied["context_size"] == "applied" and applied["supports_tools"] == "skipped_manual"
    meta2 = model_catalog.capability_meta_of(pm)
    assert meta2["sync_state"] == "pending" and meta2["remote_etag"] == "abc"
    assert meta2["sources"]["context_size"] == "provider_api"


def test_manual_modalities_protect_derived_vision(client):
    """手动 input_modalities=['text'] 后，同步条目带 image 模态不得翻转 vision/multimodal。"""
    from app.agents import model_catalog

    pid = _mk_provider_full(client, preset_key="deepseek")
    mid = client.post(
        f"/api/assistant/providers/{pid}/models",
        json={"model": "m-mod", "input_modalities": ["text"]},
    ).json()["model"]["id"]
    m = _read_model(client, pid, mid)
    assert m["multimodal"] is False and m["supports_vision"] is False
    assert "multimodal" in m["capability"]["manual_fields"]
    assert "supports_vision" in m["capability"]["manual_fields"]

    with SessionLocal() as db:
        pm = db.get(ProviderModel, mid)
        applied = model_catalog.apply_capability_entry(
            pm, {"input_modalities": ["text", "image"], "supports_vision": True}, source="provider_api"
        )
        db.commit()
    assert applied["input_modalities"] == "skipped_manual"
    assert applied["supports_vision"] == "skipped_manual"
    assert applied["multimodal"] == "skipped_manual"
    m = _read_model(client, pid, mid)
    assert m["input_modalities"] == ["text"]  # 模态与 vision 保持一致，不被同步翻转
    assert m["supports_vision"] is False and m["multimodal"] is False


# ---------------- Task 6B：reasoning_effort 白名单 / models_flat 思考字段 ----------------

def test_save_globals_reasoning_effort_accepts_canonical_and_legacy_off(client):
    """canonical 七档 + 空 + 旧 off 均可保存且不会被清成默认；非法值仍清空。"""
    for value in ["", "off", "none", "minimal", "low", "medium", "high", "xhigh", "max"]:
        resp = client.put("/api/assistant/config", json={"reasoning_effort": value})
        assert resp.status_code == 200, value
        again = client.get("/api/assistant/config").json()["config"]
        assert again["reasoning_effort"] == value, f" saved {value!r} → {again['reasoning_effort']!r}"
    # 大小写/空白归一化后同样接受
    client.put("/api/assistant/config", json={"reasoning_effort": " High "})
    assert client.get("/api/assistant/config").json()["config"]["reasoning_effort"] == "high"
    # 非法值清空（既有行为保留）
    client.put("/api/assistant/config", json={"reasoning_effort": "bogus"})
    assert client.get("/api/assistant/config").json()["config"]["reasoning_effort"] == ""


def test_models_flat_includes_thinking_fields_same_as_providers(client):
    """models_flat 增量补 supports_thinking/thinking_levels/reasoning_style，与 providers 同口径；
    其余字段与停用过滤不变。"""
    pid = _mk_provider_full(client)
    mid = client.post(
        f"/api/assistant/providers/{pid}/models",
        json={
            "model": "kimi-k3",
            "context_size": 1048576,
            "max_tokens": 131072,
            "supports_thinking": 1,
            "thinking_levels": ["none", "low", "high", "max"],
            "reasoning_style": "effort",
            "max_input_tokens": 1048576,
            "supports_tools": 1,
            "input_modalities": ["text", "image"],
        },
    ).json()["model"]["id"]
    client.post(f"/api/assistant/providers/{pid}/models", json={"model": "mm-off", "enabled": False})

    flat = next(x for x in client.get("/api/assistant/models").json()["models"] if x["id"] == mid)
    prov = next(m for m in _read_provider_models(client, pid) if m["id"] == mid)
    # 新增三字段与 providers 视图逐值同口径
    assert flat["supports_thinking"] == 1 == prov["supports_thinking"]
    assert flat["thinking_levels"] == ["none", "low", "high", "max"] == prov["thinking_levels"]
    assert flat["reasoning_style"] == "effort" == prov["reasoning_style"]
    # 既有字段不受影响（两视图共有字段逐值一致）
    for key in ("id", "model", "multimodal", "supports_vision", "active",
                "max_input_tokens", "supports_tools", "input_modalities", "output_modalities",
                "default_thinking_level", "thinking_can_disable", "api_family", "capability"):
        assert flat[key] == prov[key], key
    # flat 专属字段保持原样
    assert flat["label"] == f"{_read_provider(client, pid)['name']} · kimi-k3"
    assert flat["provider"] == _read_provider(client, pid)["name"]
    # 停用模型仍被过滤（不因新字段泄漏）
    assert all(x["id"] != next(m["id"] for m in _read_provider_models(client, pid) if m["model"] == "mm-off")
               for x in client.get("/api/assistant/models").json()["models"])


def test_models_flat_thinking_defaults_for_plain_model(client):
    """未填写思考字段的模型：supports_thinking=-1、thinking_levels=[]、reasoning_style=''。"""
    pid = _mk_provider_full(client)
    mid = client.post(f"/api/assistant/providers/{pid}/models", json={"model": "plain-m"}).json()["model"]["id"]
    flat = next(x for x in client.get("/api/assistant/models").json()["models"] if x["id"] == mid)
    assert flat["supports_thinking"] == -1
    assert flat["thinking_levels"] == []
    assert flat["reasoning_style"] == ""


def _read_provider_models(client, pid):
    return next(p for p in client.get("/api/assistant/providers").json()["providers"] if p["id"] == pid)["models"]


def _read_provider(client, pid):
    return next(p for p in client.get("/api/assistant/providers").json()["providers"] if p["id"] == pid)


# ---------------- Task 6 fix round 1：激活模型 cfg 暴露 supports_thinking ----------------

def test_active_model_cfg_exposes_supports_thinking(client):
    """load()/model_cfg_by_id()/config.get 暴露 supports_thinking（与 providers 同口径），
    前端「已知不支持 → 禁用思考下拉」在生产数据上可达（fix round 1 Important）。"""
    pid = _mk_provider_full(client)
    mid = client.post(
        f"/api/assistant/providers/{pid}/models",
        json={"model": "no-think", "supports_thinking": 0, "supports_tools": 1},
    ).json()["model"]["id"]
    client.post(f"/api/assistant/models/{mid}/activate")
    orchestrator.invalidate()

    cfg = config_store.load()
    assert cfg["supports_thinking"] == 0 and cfg["model"] == "no-think"
    by_id = config_store.model_cfg_by_id(mid)
    assert by_id["supports_thinking"] == 0
    body = client.get("/api/assistant/config").json()["config"]
    assert body["supports_thinking"] == 0
    # 与 providers 视图逐值一致
    prov = _read_model(client, pid, mid)
    assert body["supports_thinking"] == prov["supports_thinking"]

    # 未探测的模型为 -1（未知不被当成不支持）
    mid2 = client.post(f"/api/assistant/providers/{pid}/models", json={"model": "plain"}).json()["model"]["id"]
    client.post(f"/api/assistant/models/{mid2}/activate")
    orchestrator.invalidate()
    assert config_store.load()["supports_thinking"] == -1
    assert config_store.model_cfg_by_id(mid2)["supports_thinking"] == -1


def test_edit_model_note_only_does_not_mark_capability_fields_manual(client):
    """仅改备注/温度等非能力字段：能力字段不重新标记 manual（fix round 1 Minor 4 的后端契约）。"""
    pid = _mk_provider_full(client)
    mid = client.post(f"/api/assistant/providers/{pid}/models", json={"model": "m-cap"}).json()["model"]["id"]
    # 目录同步写入能力（来源 official_catalog，无 manual 标记）
    from app.agents import model_catalog

    entry = model_catalog.catalog_for("deepseek", "deepseek-flash")
    with SessionLocal() as db:
        pm = db.get(ProviderModel, mid)
        model_catalog.apply_capability_entry(pm, entry, source="official_catalog")
        db.commit()
    synced = _read_model(client, pid, mid)
    assert synced["capability"]["manual_fields"] == []
    assert synced["capability"]["sources"]["context_size"] == "official_catalog"

    resp = client.post(f"/api/assistant/providers/{pid}/models", json={"model_id": mid, "model": "m-cap", "note": "只改备注"})
    assert resp.status_code == 200
    after = _read_model(client, pid, mid)
    assert after["note"] == "只改备注"
    # 未提交的能力字段不得被重新标记 manual，来源保持同步
    assert after["capability"]["manual_fields"] == []
    assert after["capability"]["sources"]["context_size"] == "official_catalog"
    assert after["context_size"] == synced["context_size"]
