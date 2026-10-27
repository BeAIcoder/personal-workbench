"""LLM 厂商预设 + 模型能力自动探测测试。

所有探测用例均 monkeypatch ``model_probe._chat_once``（或 ``httpx.Client.post``），
不发起真实网络请求；HTTP 端点用例经 conftest 的 client 夹具（独立临时库）验证。
"""
import json

import httpx
import pytest

from app.agents import config_store, model_catalog, model_probe, model_sync
from app.agents.secrets_box import seal
from app.database import SessionLocal
from app.models import ModelProvider, ProviderModel

# httpx.Client 真实类（mock 工厂内必须用它，否则指向被替换后的工厂自身造成递归）
_REAL_HTTPX_CLIENT = httpx.Client


@pytest.fixture()
def restore_presets():
    """reload 会重绑模块级 PRESETS，用例结束后恢复原值，避免污染其他测试。"""
    original = config_store.PRESETS
    yield
    config_store.PRESETS = original


# ---------- 假响应构造 ----------

def _ok(content="9.9", reasoning_content="", finish_reason="stop", usage=None):
    return {
        "ok": True,
        "status_code": 200,
        "error": None,
        "content": content,
        "reasoning_content": reasoning_content,
        "finish_reason": finish_reason,
        "usage": usage if usage is not None else {"total_tokens": 5},
    }


def _err(status_code=400, message="bad request"):
    return {
        "ok": False,
        "status_code": status_code,
        "error": message,
        "content": "",
        "reasoning_content": "",
        "finish_reason": None,
        "usage": None,
    }


def _mk_model_row(protocol="openai", base_url="https://api.example.com/v1", model="test-model", context_size=128000):
    """直接在建好的测试库里插一条供应商 + 模型行，返回 model_id。"""
    with SessionLocal() as db:
        prov = ModelProvider(name="探测供应商", protocol=protocol, base_url=base_url, api_key=seal("sk-test"))
        db.add(prov)
        db.flush()
        pm = ProviderModel(provider_id=prov.id, model=model, context_size=context_size, max_tokens=4096)
        db.add(pm)
        db.commit()
        return pm.id


# ---------- 预设 JSON 加载 ----------

def test_presets_json_has_all_vendors():
    presets = config_store.load_presets()
    assert len(presets) >= 14
    names = {p["name"] for p in presets}
    # 原有 7 家保留
    assert {"modelscope", "siliconflow", "deepseek", "volcengine", "dashscope", "ollama", "custom"} <= names
    # 新增厂商
    assert {
        "zhipu", "moonshot", "minimax", "hunyuan", "spark", "openai",
        "anthropic", "openrouter", "groq", "together", "lmstudio",
    } <= names
    for p in presets:
        assert p["protocol"] in ("openai", "anthropic")
        for field in ("name", "label", "protocol", "base_url", "models", "note", "docs_url", "reasoning_models"):
            assert field in p
    by_name = {p["name"]: p for p in presets}
    assert by_name["anthropic"]["protocol"] == "anthropic"
    assert by_name["ollama"]["base_url"] == "http://localhost:11434/v1"
    assert by_name["lmstudio"]["base_url"] == "http://localhost:1234/v1"
    # 原有预设的 base_url 不变
    assert by_name["deepseek"]["base_url"] == "https://api.deepseek.com/v1"
    assert by_name["dashscope"]["base_url"] == "https://dashscope.aliyuncs.com/compatible-mode/v1"


def test_presets_fallback_when_file_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(config_store, "PRESETS_PATH", tmp_path / "not-exists.json")
    presets = config_store.load_presets()
    assert presets and all(p["protocol"] == "openai" for p in presets)
    assert {p["name"] for p in presets} >= {"custom", "deepseek", "ollama"}


def test_presets_fallback_when_file_corrupted(monkeypatch, tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{ 这不是合法 JSON", encoding="utf-8")
    monkeypatch.setattr(config_store, "PRESETS_PATH", bad)
    assert config_store.load_presets()  # 回退内置默认集（非空）


def test_load_presets_skips_invalid_entries(monkeypatch, tmp_path):
    f = tmp_path / "p.json"
    f.write_text(
        json.dumps(
            {
                "presets": [
                    {"name": "good", "label": "好的", "protocol": "openai", "base_url": "https://x/v1", "models": ["m"]},
                    {"name": "bad-proto", "label": "协议非法", "protocol": "cohere", "base_url": ""},
                    {"name": "", "label": "缺名字", "protocol": "openai"},
                    "not-a-dict",
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(config_store, "PRESETS_PATH", f)
    presets = config_store.load_presets()
    assert [p["name"] for p in presets] == ["good"]
    assert presets[0]["models"] == ["m"] and presets[0]["reasoning_models"] == []


# ---------- 预设端点 ----------

def test_config_endpoint_presets_openai_only(client):
    """旧版 /assistant/config 仍只暴露 OpenAI 兼容预设（保持既有行为）。"""
    body = client.get("/api/assistant/config").json()
    assert body["presets"] and all(p["protocol"] == "openai" for p in body["presets"])
    # 完整预设（含 anthropic）走新端点
    full = client.get("/api/providers/presets").json()["presets"]
    assert any(p["protocol"] == "anthropic" for p in full)


def test_presets_endpoints(client, restore_presets):
    body = client.get("/api/providers/presets").json()
    assert body["count"] == len(body["presets"]) >= 14
    reloaded = client.post("/api/providers/presets/reload").json()
    assert reloaded["count"] >= 14
    assert {p["name"] for p in reloaded["presets"]} >= {"anthropic", "zhipu", "lmstudio"}
    # 别名路径（按「router prefix=/models」字面解读时的路径）
    alias = client.get("/api/models/providers/presets").json()
    assert alias["count"] >= 14
    assert client.post("/api/models/providers/presets/reload").status_code == 200


def test_reload_presets_picks_up_file_change(client, monkeypatch, tmp_path, restore_presets):
    """reload 后 PRESETS 换成文件最新内容（改文件即生效，无需重启）。"""
    f = tmp_path / "p.json"
    f.write_text(
        json.dumps(
            {
                "presets": [
                    {
                        "name": "only-one",
                        "label": "唯一预设",
                        "protocol": "openai",
                        "base_url": "https://x/v1",
                        "models": ["m1"],
                        "note": "",
                        "docs_url": "",
                        "reasoning_models": [],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(config_store, "PRESETS_PATH", f)
    body = client.post("/api/providers/presets/reload").json()
    assert body["count"] == 1 and body["presets"][0]["name"] == "only-one"
    assert config_store.PRESETS[0]["name"] == "only-one"


def test_reload_presets_invalidates_orchestrator_cache(client, restore_presets):
    """终审收尾：presets reload 后清空 orchestrator 进程缓存。

    预设可能改变 session_header/usage_scope 等运行配置——不清缓存的话已构建的模型实例
    会继续沿用旧头/旧套餐边界。断言真实缓存状态（不 mock invalidate 本身）。
    """
    from app.agents import orchestrator

    with SessionLocal() as db:
        prov = ModelProvider(
            name="缓存供应商", protocol="openai", base_url="https://api.deepseek.com/v1",
            api_key=seal("sk-test"), preset_key="deepseek",
        )
        db.add(prov)
        db.flush()
        pm = ProviderModel(provider_id=prov.id, model="deepseek-chat", context_size=128000, max_tokens=4096)
        db.add(pm)
        db.commit()
        mid = pm.id
    config_store.activate_model(mid)
    orchestrator._get_model()  # 先建立进程内模型缓存
    assert orchestrator._model is not None

    resp = client.post("/api/providers/presets/reload")
    assert resp.status_code == 200
    assert orchestrator._model is None  # 缓存已清：下次取用按新预设重建
    assert orchestrator._models == {} and orchestrator._session_models == {}


# ---------- 思考能力探测 ----------

def test_probe_reasoning_all_supported(client, monkeypatch):
    mid = _mk_model_row()

    def fake(base_url, api_key, model, messages, max_tokens, reasoning_effort=None, timeout=30):
        assert api_key == "sk-test" and messages[0]["content"] == model_probe._REASONING_PROMPT
        return _ok(reasoning_content="先比较小数位……", usage={"total_tokens": 9, "completion_tokens_details": {"reasoning_tokens": 6}})

    monkeypatch.setattr(model_probe, "_chat_once", fake)
    with SessionLocal() as db:
        row = db.get(ProviderModel, mid)
        result = model_probe.probe_reasoning(db, row)
        assert result["supported"] == ["none", "low", "medium", "high"]
        assert result["accepted"] == []
        assert result["requests"] == 4
        assert row.supports_thinking == 1
        assert json.loads(row.thinking_levels) == ["none", "low", "medium", "high"]
        assert result["levels"]["high"] == "supported"


def test_probe_reasoning_partial_supported(client, monkeypatch):
    mid = _mk_model_row()

    def fake(base_url, api_key, model, messages, max_tokens, reasoning_effort=None, timeout=30):
        if reasoning_effort == "none":
            return _ok(content="9.9")  # 关闭思考档：接受参数但无思考输出
        return _ok(content="9.9", reasoning_content="思考中……")

    monkeypatch.setattr(model_probe, "_chat_once", fake)
    with SessionLocal() as db:
        row = db.get(ProviderModel, mid)
        result = model_probe.probe_reasoning(db, row)
        assert result["levels"]["none"] == "accepted"
        assert result["levels"]["low"] == "supported"
        assert result["supported"] == ["low", "medium", "high"]
        assert result["accepted"] == ["none"]
        assert row.supports_thinking == 1
        # thinking_levels = supported + accepted（none 被接受但无思考输出，仍算可用档位）
        assert json.loads(row.thinking_levels) == ["none", "low", "medium", "high"]


def test_probe_reasoning_param_error_unsupported(client, monkeypatch):
    mid = _mk_model_row()

    def fake(base_url, api_key, model, messages, max_tokens, reasoning_effort=None, timeout=30):
        return _err(400, "Unsupported parameter: 'reasoning_effort' is not supported with this model")

    monkeypatch.setattr(model_probe, "_chat_once", fake)
    with SessionLocal() as db:
        row = db.get(ProviderModel, mid)
        result = model_probe.probe_reasoning(db, row)
        assert result["levels"]["medium"] == "unsupported"
        assert result["supported"] == [] and result["accepted"] == []
        assert row.supports_thinking == 0
        assert json.loads(row.thinking_levels) == []


def test_probe_reasoning_network_error_keeps_unknown(client, monkeypatch):
    mid = _mk_model_row()

    def fake(base_url, api_key, model, messages, max_tokens, reasoning_effort=None, timeout=30):
        return _err(None, "ConnectError: 厂商不可达")

    monkeypatch.setattr(model_probe, "_chat_once", fake)
    with SessionLocal() as db:
        row = db.get(ProviderModel, mid)
        result = model_probe.probe_reasoning(db, row)
        assert result["levels"]["none"] == "error"
        assert "厂商不可达" in result["error"]
        assert row.supports_thinking == -1  # 全部失败不写结论
        assert row.probed_at  # 但探测记录已落库


# ---------- 有效上下文探测 ----------

def test_probe_context_bisection(client, monkeypatch):
    mid = _mk_model_row(context_size=128000)
    limit = 50000  # 模拟厂商真实上下文上限（tokens）

    def fake(base_url, api_key, model, messages, max_tokens, reasoning_effort=None, timeout=30):
        assert max_tokens == 1  # 上下文探测只发 1 个输出 token
        tokens = len(messages[0]["content"]) / 1.5  # 1 token≈1.5 汉字
        if tokens > limit:
            return _err(400, "This model's maximum context length is 50000 tokens. However you requested about more tokens.")
        return _ok(content="ok")

    monkeypatch.setattr(model_probe, "_chat_once", fake)
    monkeypatch.setattr(model_probe.time, "sleep", lambda _: None)
    with SessionLocal() as db:
        row = db.get(ProviderModel, mid)
        result = model_probe.probe_context(db, row)
        assert result["requests"] <= 6
        assert result["steps"][0]["tokens"] == (2000 + 128000) // 2
        assert 40000 <= result["effective_context"] <= 60000  # 二分收敛到真实上限附近
        assert row.probed_context == result["effective_context"]


def test_probe_context_stops_on_network_error(client, monkeypatch):
    mid = _mk_model_row()

    def fake(base_url, api_key, model, messages, max_tokens, reasoning_effort=None, timeout=30):
        return _err(None, "ConnectError: connection refused")

    monkeypatch.setattr(model_probe, "_chat_once", fake)
    monkeypatch.setattr(model_probe.time, "sleep", lambda _: None)
    with SessionLocal() as db:
        row = db.get(ProviderModel, mid)
        result = model_probe.probe_context(db, row)
        assert result["requests"] == 1
        assert result["effective_context"] == 0
        assert "ConnectError" in result["error"]
        assert row.probed_context == 0


def test_probe_context_anthropic_uses_messages(client, monkeypatch):
    """anthropic 协议的上下文探测同样走 /v1/messages 实测（不再 skipped）。"""
    mid = _mk_model_row(protocol="anthropic", base_url="https://api.anthropic.com")
    calls = []

    def fake(base_url, api_key, model, messages, max_tokens, extra=None, timeout=30):
        calls.append((base_url, max_tokens, len(messages[0]["content"])))
        return _ok(content="ok")

    monkeypatch.setattr(model_probe, "_messages_once", fake)
    with SessionLocal() as db:
        row = db.get(ProviderModel, mid)
        result = model_probe.probe_context(db, row)
    assert calls and all(base == "https://api.anthropic.com" for base, _, _ in calls)
    assert result["effective_context"] > 0
    assert row.probed_context > 0


# ---------- 最大输出探测 ----------

def test_probe_output_ladder_stops_on_max_tokens_error(client, monkeypatch):
    mid = _mk_model_row()

    def fake(base_url, api_key, model, messages, max_tokens, reasoning_effort=None, timeout=30):
        if max_tokens > 8192:
            return _err(400, "max_tokens is too large: 16384. This model supports at most 8192 output tokens.")
        return _ok(content="春天来了……", finish_reason="length")

    monkeypatch.setattr(model_probe, "_chat_once", fake)
    with SessionLocal() as db:
        row = db.get(ProviderModel, mid)
        result = model_probe.probe_output(db, row)
        assert [s["verdict"] for s in result["steps"]] == ["ok", "ok", "rejected"]
        assert result["effective_max_output"] == 4096
        assert result["requests"] == 3
        assert row.probed_max_output == 4096


def test_probe_output_all_supported(client, monkeypatch):
    mid = _mk_model_row()

    def fake(base_url, api_key, model, messages, max_tokens, reasoning_effort=None, timeout=30):
        assert messages[0]["content"] == model_probe._OUTPUT_PROMPT
        return _ok(content="……", finish_reason="length")

    monkeypatch.setattr(model_probe, "_chat_once", fake)
    with SessionLocal() as db:
        row = db.get(ProviderModel, mid)
        result = model_probe.probe_output(db, row)
        assert result["effective_max_output"] == 131072
        assert result["requests"] == 6
        assert row.probed_max_output == 131072


def test_probe_output_first_rung_rejected(client, monkeypatch):
    mid = _mk_model_row()

    def fake(base_url, api_key, model, messages, max_tokens, reasoning_effort=None, timeout=30):
        return _err(400, "max_tokens exceeds the limit for this model")

    monkeypatch.setattr(model_probe, "_chat_once", fake)
    with SessionLocal() as db:
        row = db.get(ProviderModel, mid)
        result = model_probe.probe_output(db, row)
        assert result["requests"] == 1
        assert result["effective_max_output"] == 0
        assert row.probed_max_output == 0


# ---------- 全量探测容错 ----------

def test_probe_all_isolates_step_failures(client, monkeypatch):
    mid = _mk_model_row()

    def boom(db, row):
        raise RuntimeError("模拟探测步骤崩溃")

    monkeypatch.setattr(model_probe, "probe_reasoning", boom)
    monkeypatch.setattr(model_probe, "_chat_once", lambda *a, **k: _ok(finish_reason="stop"))
    monkeypatch.setattr(model_probe.time, "sleep", lambda _: None)
    with SessionLocal() as db:
        row = db.get(ProviderModel, mid)
        result = model_probe.probe_all(db, row)
        assert "RuntimeError" in result["reasoning"]["error"]  # 崩溃只记录
        assert result["context"]["effective_context"] > 0  # 其余步骤照常执行
        assert result["output"]["effective_max_output"] == 131072
        assert result["probed_at"]
        assert row.probed_at
        detail = json.loads(row.probe_result)
        assert set(detail) >= {"reasoning", "context", "output"}


# ---------- HTTP 端点 ----------

def _mk_provider_model_via_api(client, model="m-probe"):
    pid = client.post(
        "/api/assistant/providers",
        json={"name": "探测供应商", "base_url": "https://api.example.com/v1", "api_key": "sk-test"},
    ).json()["provider"]["id"]
    mid = client.post(
        f"/api/assistant/providers/{pid}/models",
        json={"model": model, "context_size": 128000, "max_tokens": 4096},
    ).json()["model"]["id"]
    return pid, mid


def test_probe_endpoints_roundtrip(client, monkeypatch):
    pid, mid = _mk_provider_model_via_api(client)
    monkeypatch.setattr(model_probe, "_chat_once", lambda *a, **k: _ok(reasoning_content="思考中……"))

    resp = client.post(f"/api/models/{mid}/probe/reasoning")
    assert resp.status_code == 200
    body = resp.json()
    assert body["model_id"] == mid
    assert body["result"]["supported"] == ["none", "low", "medium", "high"]
    assert body["probe"]["supports_thinking"] == 1
    assert body["probe"]["thinking_levels"] == ["none", "low", "medium", "high"]

    assert client.post(f"/api/models/{mid}/probe/context").status_code == 200
    assert client.post(f"/api/models/{mid}/probe/output").status_code == 200

    all_resp = client.post(f"/api/models/{mid}/probe/all").json()
    assert all_resp["result"]["probed_at"]
    assert all_resp["probe"]["probed_context"] > 0
    assert all_resp["probe"]["probed_max_output"] == 131072

    status = client.get(f"/api/models/{mid}/probe").json()
    assert status["model_id"] == mid
    assert status["probe"]["supports_thinking"] == 1
    assert status["probe"]["probe_result"]["output"]["effective_max_output"] == 131072

    # 模型不存在 → 404
    assert client.post("/api/models/999999/probe/all").status_code == 404
    assert client.get("/api/models/999999/probe").status_code == 404

    # 字段已落库（经 /assistant/providers 回读）
    prov = next(p for p in client.get("/api/assistant/providers").json()["providers"] if p["id"] == pid)
    m = next(x for x in prov["models"] if x["id"] == mid)
    assert m["supports_thinking"] == 1 and m["probed_max_output"] == 131072 and m["probed_at"]


def test_probe_endpoint_unreachable_returns_200_with_error(client, monkeypatch):
    pid, mid = _mk_provider_model_via_api(client)
    monkeypatch.setattr(model_probe, "_chat_once", lambda *a, **k: _err(None, "ConnectError: 厂商不可达"))
    resp = client.post(f"/api/models/{mid}/probe/reasoning")
    assert resp.status_code == 200  # 探测失败是正常结果，不是 HTTP 错误
    body = resp.json()
    assert "厂商不可达" in body["result"]["error"]
    assert body["probe"]["supports_thinking"] == -1


# ---------- assistant ModelPayload 新字段 ----------

def test_assistant_model_payload_new_fields(client):
    pid = client.post(
        "/api/assistant/providers",
        json={"name": "新字段供应商", "api_key": "sk-x"},
    ).json()["provider"]["id"]
    mid = client.post(
        f"/api/assistant/providers/{pid}/models",
        json={
            "model": "m-new",
            "temperature": 0.7,
            "note": "测试备注",
            "supports_thinking": 1,
            "thinking_levels": ["low", "high"],
        },
    ).json()["model"]["id"]

    def _read_model():
        prov = next(p for p in client.get("/api/assistant/providers").json()["providers"] if p["id"] == pid)
        return next(x for x in prov["models"] if x["id"] == mid)

    m = _read_model()
    assert m["temperature"] == "0.7"
    assert m["note"] == "测试备注"
    assert m["supports_thinking"] == 1
    assert m["thinking_levels"] == ["low", "high"]
    assert m["probed_context"] == 0 and m["probed_at"] == ""

    # 更新：清空温度（跟随系统）、改备注
    client.post(
        f"/api/assistant/providers/{pid}/models",
        json={"model_id": mid, "model": "m-new", "temperature": "", "note": "改后"},
    )
    m2 = _read_model()
    assert m2["temperature"] == ""
    assert m2["note"] == "改后"
    assert m2["supports_thinking"] == 1  # 未传的字段保持原值


# ---------- _chat_once 直连层（monkeypatch httpx，无真实网络） ----------

def test_chat_once_network_error(monkeypatch):
    def raise_connect(self, url, **kwargs):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx.Client, "post", raise_connect)
    r = model_probe._chat_once(
        "https://api.example.com/v1", "sk-x", "m", [{"role": "user", "content": "hi"}], max_tokens=8
    )
    assert r["ok"] is False and r["status_code"] is None
    assert "ConnectError" in r["error"]  # 网络错误转为 error 字段，不抛异常


def test_chat_once_http_error(monkeypatch):
    req = httpx.Request("POST", "https://api.example.com/v1/chat/completions")

    def fake_post(self, url, **kwargs):
        return httpx.Response(400, request=req, json={"error": {"message": "Unsupported parameter: reasoning_effort"}})

    monkeypatch.setattr(httpx.Client, "post", fake_post)
    r = model_probe._chat_once(
        "https://api.example.com/v1", "sk-x", "m", [{"role": "user", "content": "hi"}], max_tokens=8
    )
    assert r["ok"] is False and r["status_code"] == 400
    assert "reasoning_effort" in r["error"]


def test_chat_once_success_with_reasoning(monkeypatch):
    req = httpx.Request("POST", "https://api.example.com/v1/chat/completions")

    def fake_post(self, url, **kwargs):
        return httpx.Response(
            200,
            request=req,
            json={
                "choices": [
                    {"message": {"content": "9.9", "reasoning_content": "先比较小数位"}, "finish_reason": "stop"}
                ],
                "usage": {"total_tokens": 12, "completion_tokens_details": {"reasoning_tokens": 8}},
            },
        )

    monkeypatch.setattr(httpx.Client, "post", fake_post)
    r = model_probe._chat_once(
        "https://api.example.com/v1", "sk-x", "m", [{"role": "user", "content": "hi"}], max_tokens=8
    )
    assert r["ok"] is True and r["content"] == "9.9"
    assert r["reasoning_content"] == "先比较小数位"
    assert r["finish_reason"] == "stop" and r["usage"]["total_tokens"] == 12


def test_chat_once_sends_reasoning_effort(monkeypatch):
    captured = {}

    def fake_post(self, url, **kwargs):
        captured.update(kwargs)
        return httpx.Response(
            200,
            request=httpx.Request("POST", url),
            json={"choices": [{"message": {"content": "9.9"}, "finish_reason": "stop"}]},
        )

    monkeypatch.setattr(httpx.Client, "post", fake_post)
    model_probe._chat_once(
        "https://api.example.com/v1", "sk-x", "m", [{"role": "user", "content": "hi"}],
        max_tokens=8, reasoning_effort="high",
    )
    assert captured["json"]["reasoning_effort"] == "high"
    assert captured["json"]["max_tokens"] == 8
    assert captured["json"]["temperature"] == 0
    assert captured["headers"]["Authorization"] == "Bearer sk-x"
    assert captured["json"]["model"] == "m"


# ---------------- 模型级 temperature 运行时接线 ----------------

def test_temperature_surfaced_in_active_cfg(client):
    """激活模型的 temperature 进入 load() 配置（空=None，非法=None，合法=float）。"""
    from app.agents import config_store

    prov = client.post("/api/assistant/providers", json={
        "name": "temp-test", "protocol": "openai",
        "base_url": "https://api.example.com/v1", "api_key": "sk-test",
    }).json()
    pid = prov["id"] if isinstance(prov, dict) and "id" in prov else prov.get("provider", {}).get("id")
    m1 = client.post(f"/api/assistant/providers/{pid}/models",
                     json={"model": "m-temp", "temperature": "0.7"}).json()
    mid = m1["id"] if isinstance(m1, dict) and "id" in m1 else m1.get("model", {}).get("id")
    client.post(f"/api/assistant/models/{mid}/activate")

    cfg = config_store.load()
    assert cfg.get("temperature") == 0.7
    by_id = config_store.model_cfg_by_id(mid)
    assert by_id and by_id.get("temperature") == 0.7

    # 清空 → None（跟随厂商默认）；更新需带 model_id（缺省会新建行）
    client.post(f"/api/assistant/providers/{pid}/models",
                json={"model_id": mid, "model": "m-temp", "temperature": ""})
    assert config_store.load().get("temperature") is None

    # 非法值 → None
    client.post(f"/api/assistant/providers/{pid}/models",
                json={"model_id": mid, "model": "m-temp", "temperature": "abc"})
    assert config_store.load().get("temperature") is None

    # build_model 把温度注入 OpenAI Parameters（monkeypatch agentscope 构造）
    client.post(f"/api/assistant/providers/{pid}/models",
                json={"model_id": mid, "model": "m-temp", "temperature": "1.2"})
    from app.agents import llm

    captured = {}

    class _FakeParams:
        def __init__(self, **kw):
            captured.update(kw)

    class _FakeModel:
        def __init__(self, **kw):
            pass

    import app.agents.llm as llm_mod

    orig_import = llm_mod.build_model
    # 验证思考参数之后的 param_kwargs 组装逻辑：经 build_model 走 fake
    import agentscope.model as am

    real_openai = getattr(am, "OpenAIChatModel", None)
    try:
        class _Params:
            def __init__(self, **kw):
                captured.update(kw)

        class _OA:
            Parameters = _Params

            def __init__(self, **kw):
                pass

        am.OpenAIChatModel = _OA
        model = llm_mod.build_model()
        assert model is not None
        assert captured.get("temperature") == 1.2
    finally:
        if real_openai is not None:
            am.OpenAIChatModel = real_openai


# ---------- Task 3：SQLite 轻量迁移补列 ----------

def test_ensure_sqlite_columns_patches_legacy_db(monkeypatch, tmp_path):
    """老库缺列：启动补丁补齐且幂等（二次执行不报错、不加重复列）。"""
    from sqlalchemy import create_engine, text

    import app.main as main_mod

    eng = create_engine(f"sqlite:///{(tmp_path / 'legacy.db').as_posix()}")
    # 造老库：只建 Task 3 之前的列（带服务端默认，模拟历史库）
    with eng.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE model_providers (id INTEGER PRIMARY KEY AUTOINCREMENT, name VARCHAR(50) NOT NULL,"
                " protocol VARCHAR(20) DEFAULT 'openai', base_url VARCHAR(300) DEFAULT '', api_key VARCHAR(300) DEFAULT '',"
                " enabled BOOLEAN DEFAULT 1, created_at DATETIME, updated_at DATETIME)"
            )
        )
        conn.execute(
            text(
                "CREATE TABLE provider_models (id INTEGER PRIMARY KEY AUTOINCREMENT, provider_id INTEGER NOT NULL,"
                " model VARCHAR(120) NOT NULL, context_size INTEGER DEFAULT 128000, max_tokens INTEGER DEFAULT 2048,"
                " multimodal BOOLEAN DEFAULT 0, enabled BOOLEAN DEFAULT 1, temperature TEXT DEFAULT '', note TEXT DEFAULT '',"
                " supports_thinking INTEGER DEFAULT -1, thinking_levels TEXT DEFAULT '', probed_context INTEGER DEFAULT 0,"
                " probed_max_output INTEGER DEFAULT 0, supports_vision BOOLEAN DEFAULT 0, probe_result TEXT DEFAULT '',"
                " probed_at TEXT DEFAULT '', created_at DATETIME, updated_at DATETIME)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO model_providers (name, protocol, base_url, api_key, enabled, created_at, updated_at) "
                "VALUES ('老供应商','openai','https://x/v1','',1,'2026-01-01 00:00:00','2026-01-01 00:00:00')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO provider_models (provider_id, model, context_size, max_tokens, created_at, updated_at) "
                "VALUES (1,'old-model',128000,2048,'2026-01-01 00:00:00','2026-01-01 00:00:00')"
            )
        )
    monkeypatch.setattr(main_mod, "engine", eng)
    main_mod._ensure_sqlite_columns()
    main_mod._ensure_sqlite_columns()  # 幂等：二次执行不报错
    with eng.connect() as conn:
        cols = {r[1] for r in conn.execute(text("PRAGMA table_info(model_providers)"))}
        assert {"preset_key", "models_url", "sync_credentials"} <= cols
        cols_m = {r[1] for r in conn.execute(text("PRAGMA table_info(provider_models)"))}
        assert {"max_input_tokens", "supports_tools", "capability_meta", "synced_at"} <= cols_m
        row = conn.execute(text("SELECT preset_key, models_url, sync_credentials FROM model_providers WHERE name='老供应商'")).one()
        assert tuple(row) == ("", "", "")
        m = conn.execute(
            text("SELECT max_input_tokens, supports_tools, capability_meta, synced_at FROM provider_models WHERE model='old-model'")
        ).one()
        assert tuple(m) == (0, -1, "{}", "")


# ---------- Task 3：目标供应商/套餐预设矩阵 ----------

_TARGET_PRESET_KEYS = {
    "dashscope", "dashscope_coding", "dashscope_token_plan",
    "tokenhub", "tokenhub_token_plan", "tokenhub_coding_plan",
    "zhipu", "zhipu_coding", "zhipu_anthropic",
    "moonshot", "kimi_code",
    "deepseek", "siliconflow", "siliconflow_intl", "modelscope",
    "volcengine", "volcengine_coding", "volcengine_agent_plan",
    "stepfun", "step_plan", "opencode", "opencode_go", "openrouter",
}


def test_target_preset_matrix():
    """目标供应商/套餐都有独立预设：key 唯一、URL 协议合法、普通与套餐 base 不混用。"""
    presets = config_store.load_presets()
    keys = [p["preset_key"] for p in presets]
    assert len(keys) == len(set(keys)), "preset_key 必须唯一"
    by_key = {p["preset_key"]: p for p in presets}
    assert _TARGET_PRESET_KEYS <= set(by_key)
    for p in presets:
        assert p["usage_scope"] in ("general", "plan_api_allowed", "plan_interactive_only")
        assert p["auth_scheme"] in ("bearer", "x-api-key", "ak_sk")
        for url in (p["base_url"], p["models_url"]):
            assert url == "" or url.startswith(("http://", "https://")), url
    # 每家套餐独立预设且 base 与按量不同（Key/base 不混用）
    for normal, plans in {
        "dashscope": ["dashscope_coding", "dashscope_token_plan"],
        "tokenhub": ["tokenhub_token_plan", "tokenhub_coding_plan"],
        "zhipu": ["zhipu_coding", "zhipu_anthropic"],
        "moonshot": ["kimi_code"],
        "volcengine": ["volcengine_coding", "volcengine_agent_plan"],
        "stepfun": ["step_plan"],
        "opencode": ["opencode_go"],
        "siliconflow": ["siliconflow_intl"],
    }.items():
        for plan in plans:
            assert by_key[plan]["base_url"], plan
            assert by_key[plan]["base_url"] != by_key[normal]["base_url"], plan
    # 交互限制套餐明确标记（UI 提醒 + 禁止定时任务）；Step Plan/OpenCode Go 明确放行第三方 API
    for key in (
        "dashscope_coding", "dashscope_token_plan", "tokenhub_token_plan", "tokenhub_coding_plan",
        "zhipu_coding", "kimi_code", "volcengine_coding", "volcengine_agent_plan",
    ):
        assert by_key[key]["usage_scope"] == "plan_interactive_only", key
    assert by_key["step_plan"]["usage_scope"] == "plan_api_allowed"
    assert by_key["opencode_go"]["usage_scope"] == "plan_api_allowed"
    # 关键 base / 列表 URL 与官方资料一致
    assert by_key["dashscope_coding"]["base_url"] == "https://coding.dashscope.aliyuncs.com/v1"
    assert by_key["dashscope_token_plan"]["base_url"] == "https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
    assert by_key["tokenhub"]["base_url"] == "https://tokenhub.tencentmaas.com/v1"
    assert by_key["tokenhub_token_plan"]["base_url"] == "https://api.lkeap.cloud.tencent.com/plan/v3"
    assert by_key["tokenhub_coding_plan"]["base_url"] == "https://api.lkeap.cloud.tencent.com/coding/v3"
    assert by_key["zhipu_coding"]["base_url"] == "https://open.bigmodel.cn/api/coding/paas/v4"
    assert by_key["zhipu_anthropic"]["protocol"] == "anthropic"
    assert by_key["kimi_code"]["base_url"] == "https://api.kimi.com/coding/v1"
    assert by_key["step_plan"]["base_url"] == "https://api.stepfun.com/step_plan/v1"
    assert by_key["opencode"]["base_url"] == "https://opencode.ai/inference/openai/v1"
    assert by_key["opencode_go"]["base_url"] == "https://opencode.ai/inference/go/openai/v1"
    assert by_key["siliconflow_intl"]["base_url"] == "https://api.siliconflow.com/v1"
    assert by_key["volcengine"]["base_url"] == "https://ark.cn-beijing.volces.com/api/v3"
    assert by_key["volcengine_coding"]["base_url"] == "https://ark.cn-beijing.volces.com/api/coding/v3"
    assert by_key["volcengine_agent_plan"]["base_url"] == "https://ark.cn-beijing.volces.com/api/plan/v3"
    assert by_key["deepseek"]["models_url"] == "https://api.deepseek.com/models"
    assert by_key["tokenhub"]["models_url"] == "https://tokenhub.tencentmaas.com/v1/models"
    assert by_key["opencode"]["models_url"] == "https://opencode.ai/inference/v1/models"
    assert by_key["opencode_go"]["models_url"] == "https://opencode.ai/inference/go/v1/models"
    assert by_key["openrouter"]["models_url"] == "https://openrouter.ai/api/v1/models?output_modalities=text"
    assert by_key["siliconflow"]["models_url"] == "https://api.siliconflow.cn/v1/models?sub_type=chat"
    assert by_key["anthropic"]["models_url"] == "https://api.anthropic.com/v1/models"
    # 旧字符串 models 继续可读
    assert isinstance(by_key["deepseek"]["models"], list) and by_key["deepseek"]["models"]
    assert by_key["custom"]["models"] == []
    # 官方已下线/淘汰模型不在预设候选（用户库旧行由同步与界面处理，不删除）
    candidates = by_key["moonshot"]["models"] + by_key["kimi_code"]["models"] + by_key["deepseek"]["models"]
    for gone in ("moonshot-v1-128k", "moonshot-v1-32k-vision-preview", "kimi-k2-thinking", "kimi-latest", "kimi-k2-0905-preview", "deepseek-reasoner", "deepseek-chat"):
        assert gone not in candidates, gone
    assert "deepseek-flash" in by_key["deepseek"]["models"]
    assert "kimi-k3.1-pro" in by_key["moonshot"]["models"]


# ---------- Task 3：能力目录（RemoteModel / catalog_for / capability_meta_of） ----------

def test_remote_model_defaults_keep_unknown():
    """无资料时保持未知：不带模态字段不猜视觉，名字带 VL 也不猜。"""
    from app.agents import model_catalog

    rm = model_catalog.RemoteModel(model="qwen-vl-max")
    assert rm.supports_thinking == -1 and rm.supports_tools == -1
    assert rm.supports_vision is None and rm.input_modalities is None
    assert rm.thinking_levels is None and rm.thinking_can_disable is None
    assert model_catalog.derive_supports_vision(rm.input_modalities) is None
    assert model_catalog.derive_multimodal(rm.input_modalities) is None
    assert model_catalog.derive_supports_vision(["text"]) is False
    assert model_catalog.derive_supports_vision(["text", "image"]) is True
    assert model_catalog.derive_multimodal(["text", "image"]) is True
    assert model_catalog.derive_multimodal(["text"]) is False


def test_catalog_sample_mapping_openrouter_and_deepseek():
    """OpenRouter/DeepSeek 样本按官方 API 字段映射；无 reasoning 声明保持未知。"""
    from app.agents import model_catalog

    or_chat = model_catalog.catalog_for("openrouter", "deepseek/deepseek-chat")
    assert or_chat["context_size"] == 163840
    assert or_chat["max_input_tokens"] == 128000
    assert or_chat["max_tokens"] == 16000
    assert or_chat["input_modalities"] == ["text"]
    assert or_chat["output_modalities"] == ["text"]
    assert or_chat["supports_tools"] == 1
    assert or_chat["supports_thinking"] == -1  # 无 reasoning 声明 → 未知，不当成不支持
    assert "thinking_levels" not in or_chat
    assert or_chat["supports_vision"] is False

    or_flash = model_catalog.catalog_for("openrouter", "deepseek/deepseek-v4-flash")
    assert or_flash["supports_thinking"] == 1
    assert or_flash["thinking_levels"] == ["xhigh", "high", "none"]
    assert or_flash["default_thinking_level"] == "high"
    assert or_flash["thinking_can_disable"] is True
    assert or_flash["context_size"] == 1048576

    ds = model_catalog.catalog_for("deepseek", "deepseek-flash")
    assert ds["context_size"] == 1048576
    assert ds["max_tokens"] == 393216
    assert ds["input_modalities"] == ["text", "image"]
    assert ds["supports_vision"] is True
    assert ds["supports_thinking"] == 1
    assert ds["thinking_can_disable"] is True
    assert ds["supports_tools"] == 1
    ds_pro = model_catalog.catalog_for("deepseek", "deepseek-v4-pro")
    assert ds_pro["supports_vision"] is False  # 官方：Vision Not supported

    assert model_catalog.catalog_for("deepseek", "no-such-model") is None
    assert model_catalog.catalog_for("no-such-preset", "x") is None


def test_catalog_glm_thinking_from_official_docs():
    """GLM-5.3 按官方思考文档：强制思考（不可关）、档位 max/high/low、默认 max。"""
    from app.agents import model_catalog

    g = model_catalog.catalog_for("zhipu", "glm-5.3")
    assert g["context_size"] == 1000000
    assert g["max_tokens"] == 131072
    assert g["supports_thinking"] == 1
    assert g["thinking_can_disable"] is False
    assert g["thinking_levels"] == ["max", "high", "low"]
    assert g["default_thinking_level"] == "max"
    g2 = model_catalog.catalog_for("zhipu", "glm-5.2")
    assert g2["context_size"] == 1000000
    assert g2["thinking_can_disable"] is True


def test_capability_catalog_sources_and_coverage():
    """目录每条带来源/URL/核对日期；目标供应商全部有目录（zhipu_anthropic 与 zhipu 共用目录）。"""
    from app.agents import model_catalog

    catalog = model_catalog.load_capability_catalog()
    assert catalog
    for key, group in catalog.items():
        assert group["source"] in ("official_catalog", "models_dev", "provider_api", "preset"), key
        assert group["source_url"].startswith("https://"), key
        assert group["verified_on"] >= "2026-10-01", key
        assert group["models"], key
    by_key = {p["preset_key"]: p for p in config_store.load_presets()}
    for key in _TARGET_PRESET_KEYS:
        catalog_key = by_key[key]["catalog_key"] or key
        assert catalog_key in catalog, key
        assert model_catalog.catalog_for(key, next(iter(catalog[catalog_key]["models"]))), key


# ---------- Fix round 1：幽灵 slug / deprecated 候选 / 百炼 ID / none 自洽 / modelscope 交集 ----------

def test_openrouter_preset_uses_real_slug():
    """OpenRouter 候选必须是实时 API 存在的 slug（幽灵 slug 会造成未知能力与重复行）。"""
    presets = config_store.load_presets()
    or_preset = next(p for p in presets if p["preset_key"] == "openrouter")
    assert "anthropic/claude-opus-4.5" in or_preset["models"]
    assert "anthropic/claude-opus-4-5" not in or_preset["models"]
    entry = model_catalog.catalog_for("openrouter", "anthropic/claude-opus-4.5")
    assert entry and entry["context_size"] == 200000 and entry["supports_vision"] is True


def test_preset_candidates_exclude_deprecated_and_intersect_catalog():
    """预设候选不得包含目录中明确 deprecated 的模型；有目录的预设候选必须与目录有交集。"""
    catalog = model_catalog.load_capability_catalog()
    checked = 0
    for preset in config_store.load_presets():
        key = preset["catalog_key"]
        if not key or not preset["models"]:
            continue
        checked += 1
        group = (catalog.get(key) or {}).get("models") or {}
        for mid in preset["models"]:
            entry = group.get(mid)
            if entry is not None:
                assert entry.get("status") != "deprecated", f"{preset['preset_key']}/{mid} 已标注 deprecated"
        assert any(mid in group for mid in preset["models"]), f"{preset['preset_key']} 候选与目录零交集"
    assert checked >= 20


def test_catalog_entries_carry_status_from_source():
    """目录条目按来源带 status：models.dev 标注 deprecated 的必须落库，现役条目为空串。"""
    catalog = model_catalog.load_capability_catalog()
    assert catalog["minimax"]["models"]["MiniMax-M2"]["status"] == "deprecated"
    assert catalog["groq"]["models"]["llama-3.3-70b-versatile"]["status"] == "deprecated"
    assert catalog["volcengine"]["models"]["doubao-seed-1-6-251015"]["status"] == "deprecated"
    assert catalog["tokenhub_coding_plan"]["models"]["hunyuan-t1"]["status"] == "deprecated"
    assert catalog["zhipu"]["models"]["glm-5.3"]["status"] == ""
    assert catalog["deepseek"]["models"]["deepseek-flash"]["status"] == ""
    for group in catalog.values():
        for entry in group["models"].values():
            assert "status" in entry


def test_thinking_none_level_and_can_disable_are_consistent():
    """none 是有效关闭档 ⟺ thinking_can_disable=True；强制思考模型 levels 不含 none。"""
    catalog = model_catalog.load_capability_catalog()
    for group_key, group in catalog.items():
        for mid, entry in group["models"].items():
            levels = entry.get("thinking_levels") or []
            if "none" in levels:
                assert entry.get("thinking_can_disable") is True, f"{group_key}/{mid}"
            if entry.get("thinking_can_disable") is False:
                assert "none" not in levels, f"{group_key}/{mid}"
    kf = model_catalog.catalog_for("kimi_code", "kimi-for-coding")
    assert kf["thinking_levels"] == ["none", "low", "high", "max"]
    assert kf["thinking_can_disable"] is True
    db_entry = model_catalog.catalog_for("volcengine", "doubao-seed-2-1-pro-260628")
    assert db_entry["thinking_can_disable"] is True
    g = model_catalog.catalog_for("zhipu", "glm-5.3")
    assert g["thinking_levels"] == ["max", "high", "low"] and g["thinking_can_disable"] is False


def test_catalog_thinking_levels_are_canonical():
    """终审收尾：全目录 thinking_levels 只含 canonical 档位（llm._THINKING_LEVELS 同源）。

    非 canonical 值（如历史遗留的 "default"）会穿透到思考下拉与探测档位，
    前端 CANONICAL_LEVELS 会静默剔除——目录侧必须同口径校验，不依赖下游过滤。
    """
    from app.agents import llm

    catalog = model_catalog.load_capability_catalog()
    offenders = []
    for group_key, group in catalog.items():
        for mid, entry in group["models"].items():
            for level in entry.get("thinking_levels") or []:
                if level not in llm._THINKING_LEVELS:
                    offenders.append(f"{group_key}/{mid}:{level}")
    assert offenders == []
    groq = model_catalog.catalog_for("groq", "qwen/qwen3.8-27b")
    assert groq["thinking_levels"] == ["none", "low", "medium", "high"]


def test_dashscope_preset_ids_match_catalog():
    """百炼候选 ID 与目录同形（kimi-k3/glm-5.3/deepseek-v4-pro），不出现带斜杠的异构形态。"""
    by_key = {p["preset_key"]: p for p in config_store.load_presets()}
    ds = by_key["dashscope"]
    assert "kimi-k3" in ds["models"] and "kimi/kimi-k3" not in ds["models"]
    assert "glm-5.3" in ds["models"] and "ZHIPU/GLM-5.3" not in ds["models"]
    assert "deepseek-v4-pro" in ds["models"] and "deepseek-v4-pro-0813" not in ds["models"]
    for mid in ("kimi-k3", "glm-5.3", "deepseek-v4-pro", "qwen3.8-max"):
        assert model_catalog.catalog_for("dashscope", mid), mid


def test_modelscope_candidates_all_in_catalog():
    """魔搭候选全部取自当前目录组有效 ID（刷新前与目录零交集）。"""
    by_key = {p["preset_key"]: p for p in config_store.load_presets()}
    ms = by_key["modelscope"]
    assert ms["models"]
    for mid in ms["models"]:
        assert model_catalog.catalog_for("modelscope", mid), mid


# ---------------- Task 5：思考探测用同步档位与风格 / Anthropic Messages 实测 ----------------

def _set_row_capability(mid, *, thinking_levels=None, reasoning_style=None):
    """给模型行写入 Task 3/4 同步产出的能力字段（thinking_levels / reasoning_style）。"""
    with SessionLocal() as db:
        row = db.get(ProviderModel, mid)
        if thinking_levels is not None:
            row.thinking_levels = json.dumps(thinking_levels, ensure_ascii=False)
        meta = model_catalog.capability_meta_of(row)
        if reasoning_style is not None:
            meta["reasoning_style"] = reasoning_style
        row.capability_meta = json.dumps(meta, ensure_ascii=False)
        db.commit()


def test_probe_reasoning_uses_synced_levels_and_effort_style(client, monkeypatch):
    """探测档位取自已同步/手动的 thinking_levels；effort 风格档位原样发送（含 AgentScope 不认识的 max）。"""
    mid = _mk_model_row()
    _set_row_capability(mid, thinking_levels=["low", "high", "max"], reasoning_style="effort")
    seen = []

    def fake(base_url, api_key, model, messages, max_tokens, reasoning_effort=None, timeout=30, extra=None):
        seen.append((reasoning_effort, extra))
        return _ok(content="9.9", reasoning_content="先比较小数位……")

    monkeypatch.setattr(model_probe, "_chat_once", fake)
    with SessionLocal() as db:
        row = db.get(ProviderModel, mid)
        result = model_probe.probe_reasoning(db, row)
    assert [lv for lv, _ in seen] == ["low", "high", "max"]
    assert all(extra is None for _, extra in seen)
    assert result["supported"] == ["low", "high", "max"]
    assert result["style"] == "effort"
    assert row.supports_thinking == 1
    assert json.loads(row.thinking_levels) == ["low", "high", "max"]


def test_probe_reasoning_accepted_is_not_supported(client, monkeypatch):
    """accepted（接受参数但无思考输出）≠ supported：不得据此判定模型支持思考。"""
    mid = _mk_model_row()

    def fake(base_url, api_key, model, messages, max_tokens, reasoning_effort=None, timeout=30, extra=None):
        return _ok(content="9.9")  # 每个档位都接受参数、但不产出思考内容

    monkeypatch.setattr(model_probe, "_chat_once", fake)
    with SessionLocal() as db:
        row = db.get(ProviderModel, mid)
        result = model_probe.probe_reasoning(db, row)
    assert result["supported"] == []
    assert set(result["accepted"]) == {"none", "low", "medium", "high"}
    assert result["levels"]["low"] == "accepted"
    assert row.supports_thinking == 0


def test_probe_reasoning_toggle_style_sends_thinking_type(client, monkeypatch):
    """toggle 风格：请求体带 thinking{type} 开关；off 档显式关闭、高档显式开启。"""
    mid = _mk_model_row()
    _set_row_capability(mid, thinking_levels=["off", "high"], reasoning_style="toggle")
    seen = []

    def fake(base_url, api_key, model, messages, max_tokens, reasoning_effort=None, timeout=30, extra=None):
        seen.append(extra)
        if extra and extra.get("thinking", {}).get("type") == "enabled":
            return _ok(content="9.9", reasoning_content="思考中……")
        return _ok(content="9.9")

    monkeypatch.setattr(model_probe, "_chat_once", fake)
    with SessionLocal() as db:
        row = db.get(ProviderModel, mid)
        result = model_probe.probe_reasoning(db, row)
    assert seen == [{"thinking": {"type": "disabled"}}, {"thinking": {"type": "enabled"}}]
    assert result["levels"]["off"] == "accepted"
    assert result["levels"]["high"] == "supported"


def test_probe_reasoning_budget_style_sends_enable_thinking(client, monkeypatch):
    """budget 风格：enable_thinking+thinking_budget 数值档，不伪装成 effort 字符串档。"""
    mid = _mk_model_row()
    _set_row_capability(mid, thinking_levels=["low", "high"], reasoning_style="budget")
    seen = []

    def fake(base_url, api_key, model, messages, max_tokens, reasoning_effort=None, timeout=30, extra=None):
        seen.append(extra)
        return _ok(content="9.9", reasoning_content="思考中……")

    monkeypatch.setattr(model_probe, "_chat_once", fake)
    with SessionLocal() as db:
        row = db.get(ProviderModel, mid)
        result = model_probe.probe_reasoning(db, row)
    assert seen == [
        {"enable_thinking": True, "thinking_budget": 4096},
        {"enable_thinking": True, "thinking_budget": 32768},
    ]
    assert result["supported"] == ["low", "high"]


def test_probe_reasoning_anthropic_uses_messages_protocol(client, monkeypatch):
    """Anthropic 不再一律 skipped：走 /v1/messages，effort 档位进 output_config，max 原样发送。"""
    mid = _mk_model_row(protocol="anthropic", base_url="https://api.anthropic.com")
    _set_row_capability(mid, thinking_levels=["low", "max"], reasoning_style="effort")
    seen = []

    def fake(base_url, api_key, model, messages, max_tokens, extra=None, timeout=30):
        seen.append((base_url, api_key, max_tokens, extra))
        return _ok(content="9.9", reasoning_content="先比较小数位……")

    monkeypatch.setattr(model_probe, "_messages_once", fake)
    with SessionLocal() as db:
        row = db.get(ProviderModel, mid)
        result = model_probe.probe_reasoning(db, row)
    assert [extra for _, _, _, extra in seen] == [
        {"output_config": {"effort": "low"}},
        {"output_config": {"effort": "max"}},
    ]
    assert all(api_key == "sk-test" for _, api_key, _, _ in seen)
    assert result["supported"] == ["low", "max"]
    assert row.supports_thinking == 1


def test_probe_reasoning_anthropic_param_error_unsupported(client, monkeypatch):
    """Anthropic 明确参数拒绝（4xx 指向 effort/output_config）→ unsupported，不是 skipped。"""
    mid = _mk_model_row(protocol="anthropic", base_url="https://api.anthropic.com")
    _set_row_capability(mid, thinking_levels=["low"], reasoning_style="effort")

    def fake(base_url, api_key, model, messages, max_tokens, extra=None, timeout=30):
        return _err(400, "output_config.effort is not supported with this model")

    monkeypatch.setattr(model_probe, "_messages_once", fake)
    with SessionLocal() as db:
        row = db.get(ProviderModel, mid)
        result = model_probe.probe_reasoning(db, row)
    assert result["levels"]["low"] == "unsupported"
    assert row.supports_thinking == 0


def test_probe_reasoning_anthropic_network_error_keeps_unknown(client, monkeypatch):
    """Anthropic 网络错误：结论保持未知（-1），只记录 error，不当成不支持。"""
    mid = _mk_model_row(protocol="anthropic", base_url="https://api.anthropic.com")

    def fake(base_url, api_key, model, messages, max_tokens, extra=None, timeout=30):
        return _err(None, "ConnectError: 厂商不可达")

    monkeypatch.setattr(model_probe, "_messages_once", fake)
    with SessionLocal() as db:
        row = db.get(ProviderModel, mid)
        result = model_probe.probe_reasoning(db, row)
    assert "厂商不可达" in result["error"]
    assert row.supports_thinking == -1


# ---------------- Task 5：工具/图片实测结论落库（来源 probe） ----------------

def test_record_probe_verdict_writes_definitive_results(client):
    """明确成功→1、明确参数拒绝→0，并记录来源 probe。"""
    mid = _mk_model_row()
    assert config_store.record_probe_verdict(mid, "supports_tools", "supported")["value"] == 1
    assert config_store.record_probe_verdict(mid, "supports_vision", "rejected")["value"] == 0
    with SessionLocal() as db:
        row = db.get(ProviderModel, mid)
        assert row.supports_tools == 1
        assert row.supports_vision == 0
        sources = model_catalog.capability_meta_of(row)["sources"]
        assert sources["supports_tools"] == "probe"
        assert sources["supports_vision"] == "probe"


def test_record_probe_verdict_unknown_keeps_existing_value(client):
    """网络错误等未获明确证据：不改动任何值（不把「没测到」当成「不支持」）。"""
    mid = _mk_model_row()
    with SessionLocal() as db:
        row = db.get(ProviderModel, mid)
        row.supports_tools = 1
        db.commit()
    assert config_store.record_probe_verdict(mid, "supports_tools", "unknown") is None
    with SessionLocal() as db:
        assert db.get(ProviderModel, mid).supports_tools == 1
    mid2 = _mk_model_row(model="m-unknown")
    config_store.record_probe_verdict(mid2, "supports_vision", "unknown")
    with SessionLocal() as db:
        row2 = db.get(ProviderModel, mid2)
        assert row2.supports_vision == 0  # 初值保持
        assert "supports_vision" not in model_catalog.capability_meta_of(row2)["sources"]


def test_tool_calling_verdicts(client, monkeypatch):
    """工具实测三分：明确成功=supported、参数拒绝=rejected、网络错误/未调用=unknown。"""
    from app.agents import llm

    cfg = {"api_key": "sk-test", "base_url": "https://api.example.com/v1", "model": "m"}

    class _Block:
        def __init__(self, type_, text="", name=""):
            self.type = type_
            self.text = text
            self.name = name
            self.tool_call_name = name

    class _Model:
        def __init__(self, blocks=None, exc=None):
            self._blocks = blocks or []
            self._exc = exc

        async def __call__(self, messages, tools=None):
            if self._exc is not None:
                raise self._exc
            return {"content": self._blocks, "usage": None}

    import asyncio

    monkeypatch.setattr(llm, "build_model", lambda c: _Model(blocks=[_Block("tool_call", name="get_current_time")]))
    ok = asyncio.run(config_store.test_tool_calling(cfg))
    assert ok["ok"] is True and ok["verdict"] == "supported"

    class BadRequestError(Exception):
        """模拟 openai SDK 400 参数拒绝（类型名即判定依据）。"""

    monkeypatch.setattr(llm, "build_model", lambda c: _Model(exc=BadRequestError("Error code: 400")))
    rejected = asyncio.run(config_store.test_tool_calling(cfg))
    assert rejected["ok"] is False and rejected["verdict"] == "rejected"

    monkeypatch.setattr(llm, "build_model", lambda c: _Model(exc=ConnectionError("refused")))
    unknown = asyncio.run(config_store.test_tool_calling(cfg))
    assert unknown["ok"] is False and unknown["verdict"] == "unknown"

    # 模型正常回复但不调用工具：不是明确的参数拒绝，保持未知
    monkeypatch.setattr(llm, "build_model", lambda c: _Model(blocks=[_Block("text", text="我无法调用工具")]))
    silent = asyncio.run(config_store.test_tool_calling(cfg))
    assert silent["ok"] is False and silent["verdict"] == "unknown"


def test_multimodal_verdicts(monkeypatch):
    """图片实测三分：明确成功=supported、4xx 参数拒绝=rejected、网络错误=unknown。"""
    import asyncio
    import io
    import urllib.error

    cfg = {"api_key": "sk-test", "base_url": "https://api.example.com/v1", "model": "m"}
    ok_body = json.dumps({"choices": [{"message": {"content": "红色"}}]}).encode()

    def urlopen_ok(req, timeout=None):
        return io.BytesIO(ok_body)

    monkeypatch.setattr(urllib.request, "urlopen", urlopen_ok)
    ok = asyncio.run(config_store.test_multimodal(cfg))
    assert ok["ok"] is True and ok["verdict"] == "supported"

    def urlopen_400(req, timeout=None):
        raise urllib.error.HTTPError("https://x/v1/chat/completions", 400, "Bad Request", {}, io.BytesIO(b"{}"))

    monkeypatch.setattr(urllib.request, "urlopen", urlopen_400)
    rejected = asyncio.run(config_store.test_multimodal(cfg))
    assert rejected["ok"] is False and rejected["verdict"] == "rejected"

    def urlopen_down(req, timeout=None):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(urllib.request, "urlopen", urlopen_down)
    unknown = asyncio.run(config_store.test_multimodal(cfg))
    assert unknown["ok"] is False and unknown["verdict"] == "unknown"


# ---------- fix round 1：只有 400/422 是明确的参数拒绝 ----------

def test_multimodal_verdicts_only_400_422_are_rejected(monkeypatch):
    """401/403/404/408/429/5xx 不是「模型不支持图片」：鉴权/限流/服务端问题一律 unknown。"""
    import asyncio
    import io
    import urllib.error

    cfg = {"api_key": "sk-test", "base_url": "https://api.example.com/v1", "model": "m"}

    def _http_error(code):
        def urlopen_err(req, timeout=None):
            raise urllib.error.HTTPError("https://x/v1/chat/completions", code, "Err", {}, io.BytesIO(b"{}"))

        return urlopen_err

    for code in (400, 422):
        monkeypatch.setattr(urllib.request, "urlopen", _http_error(code))
        assert asyncio.run(config_store.test_multimodal(cfg))["verdict"] == "rejected", code
    for code in (401, 403, 404, 408, 429, 500, 503):
        monkeypatch.setattr(urllib.request, "urlopen", _http_error(code))
        assert asyncio.run(config_store.test_multimodal(cfg))["verdict"] == "unknown", code


def test_multimodal_anthropic_verdicts_only_400_422_are_rejected(monkeypatch):
    """anthropic 图片测试同口径：401（Key 无效）/429（限流）不得写成「不支持视觉」。"""
    import asyncio
    import io
    import urllib.error

    cfg = {
        "api_key": "sk-test",
        "base_url": "https://api.anthropic.com/v1",
        "model": "claude-test",
        "protocol": "anthropic",
    }

    def _http_error(code):
        def urlopen_err(req, timeout=None):
            raise urllib.error.HTTPError("https://x/v1/messages", code, "Err", {}, io.BytesIO(b"{}"))

        return urlopen_err

    for code in (400, 422):
        monkeypatch.setattr(urllib.request, "urlopen", _http_error(code))
        assert asyncio.run(config_store.test_multimodal_anthropic(cfg))["verdict"] == "rejected", code
    for code in (401, 403, 404, 408, 429, 500):
        monkeypatch.setattr(urllib.request, "urlopen", _http_error(code))
        assert asyncio.run(config_store.test_multimodal_anthropic(cfg))["verdict"] == "unknown", code


def test_tool_calling_param_rejection_is_precise(client, monkeypatch):
    """工具实测：仅 SDK BadRequestError 类型 / status_code=400,422 / 明确 Error code: 400|422 判 rejected；
    401/429/500 与偶含 400 的无关文本（端口号等）一律 unknown。"""
    from app.agents import llm

    cfg = {"api_key": "sk-test", "base_url": "https://api.example.com/v1", "model": "m"}

    class _Model:
        def __init__(self, exc):
            self._exc = exc

        async def __call__(self, messages, tools=None):
            raise self._exc

    import asyncio

    class BadRequestError(Exception):
        """openai SDK 的 400 参数拒绝（类型名即判定依据，消息不含状态码也要识别）。"""

    monkeypatch.setattr(llm, "build_model", lambda c: _Model(BadRequestError("invalid parameter")))
    assert asyncio.run(config_store.test_tool_calling(cfg))["verdict"] == "rejected"

    class _StatusErr(Exception):
        def __init__(self, msg, code):
            super().__init__(msg)
            self.status_code = code

    monkeypatch.setattr(llm, "build_model", lambda c: _Model(_StatusErr("nope", 400)))
    assert asyncio.run(config_store.test_tool_calling(cfg))["verdict"] == "rejected"
    monkeypatch.setattr(llm, "build_model", lambda c: _Model(_StatusErr("nope", 422)))
    assert asyncio.run(config_store.test_tool_calling(cfg))["verdict"] == "rejected"

    class _TextErr(Exception):
        pass

    for msg, want in (
        ("Error code: 400 - {'error': {'message': 'bad'}}", "rejected"),
        ("Error code: 422", "rejected"),
        ("Error code: 401 - invalid api key", "unknown"),
        ("Error code: 429 - rate limited", "unknown"),
        ("connect to host:4001 refused", "unknown"),
        ("upstream returned HTTP 500", "unknown"),
    ):
        monkeypatch.setattr(llm, "build_model", lambda c, m=msg: _Model(_TextErr(m)))
        assert asyncio.run(config_store.test_tool_calling(cfg))["verdict"] == want, msg


# ---------- fix round 1：probe 结论是本地覆盖，默认同步不覆盖 ----------

def test_record_probe_verdict_is_local_override_respected_by_sync(client):
    """probe 结论记 manual_fields 作为本地覆盖：默认同步跳过，reset-overrides 恢复同步值。"""
    mid = _mk_model_row()
    config_store.record_probe_verdict(mid, "supports_tools", "supported")
    with SessionLocal() as db:
        row = db.get(ProviderModel, mid)
        meta = model_catalog.capability_meta_of(row)
        assert row.supports_tools == 1
        assert meta["sources"]["supports_tools"] == "probe"
        assert "supports_tools" in meta["manual_fields"]
        # 默认目录同步：manual 字段跳过，probe 实测值保留；非 manual 字段正常合并
        applied = model_catalog.apply_capability_entry(
            row, {"supports_tools": 0, "context_size": 200000}, source="official_catalog"
        )
        assert applied["supports_tools"] == "skipped_manual"
        assert applied["context_size"] == "applied"
        assert row.supports_tools == 1
        assert row.context_size == 200000
        db.commit()

    # reset-overrides：清 manual 并按最近同步快照恢复（同步值覆盖 probe）
    out = config_store.reset_provider_model_overrides(mid)
    assert "supports_tools" in out["cleared"] and out["applied"] is True
    with SessionLocal() as db:
        row = db.get(ProviderModel, mid)
        assert row.supports_tools == 0
        meta = model_catalog.capability_meta_of(row)
        assert "supports_tools" not in meta["manual_fields"]
        assert meta["sources"]["supports_tools"] == "official_catalog"


def test_record_probe_verdict_override_manual_sync_can_clear(client):
    """override_manual=True 的同步可清除 probe 本地覆盖（不引入新字段的最小兼容方案）。"""
    mid = _mk_model_row()
    config_store.record_probe_verdict(mid, "supports_tools", "rejected")
    with SessionLocal() as db:
        row = db.get(ProviderModel, mid)
        applied = model_catalog.apply_capability_entry(
            row, {"supports_tools": 1}, source="official_catalog", override_manual=True
        )
        assert applied["supports_tools"] == "applied"
        assert row.supports_tools == 1
        assert "supports_tools" not in model_catalog.capability_meta_of(row)["manual_fields"]
        db.commit()


# ---------- 终审 Important 1：思考探测结论是 probe 本地覆盖 ----------

def _mk_sync_provider(models_url="https://api.deepseek.com/models"):
    """带 deepseek 预设与列表地址的供应商（供 probe→同步链路用例；密钥为合成值）。"""
    with SessionLocal() as db:
        prov = ModelProvider(
            name="探测同步供应商",
            protocol="openai",
            base_url="https://api.deepseek.com/v1",
            api_key=seal("sk-test"),
            preset_key="deepseek",
            models_url=models_url,
        )
        db.add(prov)
        db.commit()
        return prov.id


def _mock_sync_httpx(monkeypatch, payload):
    """把 httpx.Client 换成返回固定 payload 的 MockTransport 客户端（真实解析路径）。

    真实类在导入时捕获：否则工厂内的 httpx.Client 会指向被替换后的工厂自身（递归）。
    """

    def _factory(**kwargs):
        kwargs.pop("trust_env", None)
        return _REAL_HTTPX_CLIENT(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload)), **kwargs
        )

    monkeypatch.setattr(httpx, "Client", _factory)


def test_probe_reasoning_definitive_is_probe_local_override_respected_by_sync(client, monkeypatch):
    """终审 Important 1：明确的思考探测结论记 manual_fields + sources=probe。

    默认目录/在线同步不得把实测「不支持」翻回目录「支持」；reset-overrides 清覆盖后
    按最近同步快照恢复同步值（probe→sync→reset 全链路）。
    """
    pid = _mk_sync_provider()
    _mock_sync_httpx(
        monkeypatch,
        {"object": "list", "data": [{"id": "deepseek-flash", "object": "model", "created": 1, "owned_by": "deepseek"}]},
    )
    with SessionLocal() as db:
        first = model_sync.sync_provider_models(db, pid)
    assert first["added"] == 1
    with SessionLocal() as db:
        row = db.query(ProviderModel).filter(ProviderModel.provider_id == pid).one()
        mid = row.id
        assert row.supports_thinking == 1  # 目录值先落库

    # 实测：全部档位 4xx 参数拒绝 → 明确「不支持思考」（definitive）
    def fake(base_url, api_key, model, messages, max_tokens, reasoning_effort=None, timeout=30):
        return _err(400, "Unsupported parameter: 'reasoning_effort' is not supported with this model")

    monkeypatch.setattr(model_probe, "_chat_once", fake)
    with SessionLocal() as db:
        row = db.get(ProviderModel, mid)
        result = model_probe.probe_reasoning(db, row)
        assert result["supported"] == [] and result["accepted"] == []
        assert row.supports_thinking == 0 and json.loads(row.thinking_levels) == []
        meta = model_catalog.capability_meta_of(row)
        assert meta["sources"]["supports_thinking"] == "probe"
        assert meta["sources"]["thinking_levels"] == "probe"
        assert "supports_thinking" in meta["manual_fields"]
        assert "thinking_levels" in meta["manual_fields"]

    # 默认同步：probe 结论不被目录翻回「支持」（实测优先于目录声明）
    with SessionLocal() as db:
        model_sync.sync_provider_models(db, pid)
    with SessionLocal() as db:
        row = db.get(ProviderModel, mid)
        assert row.supports_thinking == 0 and json.loads(row.thinking_levels) == []

    # reset-overrides：清 probe 覆盖，按最近一次同步快照恢复同步值
    out = config_store.reset_provider_model_overrides(mid)
    assert {"supports_thinking", "thinking_levels"} <= set(out["cleared"]) and out["applied"] is True
    with SessionLocal() as db:
        row = db.get(ProviderModel, mid)
        assert row.supports_thinking == 1
        meta = model_catalog.capability_meta_of(row)
        assert "supports_thinking" not in meta["manual_fields"]
        assert meta["sources"]["supports_thinking"] == "provider_api"  # 恢复为同步值（本次为在线同步）


def test_probe_reasoning_override_manual_sync_can_clear(client, monkeypatch):
    """终审 Important 1：override_manual=True 的同步可清除 probe 覆盖（用户显式选择）。"""
    pid = _mk_sync_provider()
    _mock_sync_httpx(
        monkeypatch,
        {"object": "list", "data": [{"id": "deepseek-flash", "object": "model", "created": 1, "owned_by": "deepseek"}]},
    )
    with SessionLocal() as db:
        model_sync.sync_provider_models(db, pid)
    with SessionLocal() as db:
        mid = db.query(ProviderModel).filter(ProviderModel.provider_id == pid).one().id

    def fake(base_url, api_key, model, messages, max_tokens, reasoning_effort=None, timeout=30):
        return _err(400, "Unsupported parameter: 'reasoning_effort' is not supported with this model")

    monkeypatch.setattr(model_probe, "_chat_once", fake)
    with SessionLocal() as db:
        row = db.get(ProviderModel, mid)
        model_probe.probe_reasoning(db, row)
        assert row.supports_thinking == 0

    with SessionLocal() as db:
        third = model_sync.sync_provider_models(db, pid, override_manual=True)
    assert third["ok"] is True and third["updated"] == 1
    with SessionLocal() as db:
        row = db.get(ProviderModel, mid)
        assert row.supports_thinking == 1  # 被目录值覆盖
        meta = model_catalog.capability_meta_of(row)
        assert "supports_thinking" not in meta["manual_fields"]
        assert meta["sources"]["supports_thinking"] == "provider_api"  # 覆盖后来源即本次同步


def test_probe_reasoning_all_failed_does_not_mark_override(client, monkeypatch):
    """终审 Important 1：全部请求失败（无明确结论）不写 manual_fields——「没测到」不是实测值。"""
    mid = _mk_model_row()

    def fake(base_url, api_key, model, messages, max_tokens, reasoning_effort=None, timeout=30):
        return _err(None, "ConnectError: 厂商不可达")

    monkeypatch.setattr(model_probe, "_chat_once", fake)
    with SessionLocal() as db:
        row = db.get(ProviderModel, mid)
        result = model_probe.probe_reasoning(db, row)
        assert result["levels"]["none"] == "error"
        assert row.supports_thinking == -1
        meta = model_catalog.capability_meta_of(row)
        assert "supports_thinking" not in meta["manual_fields"]
        assert "supports_thinking" not in meta["sources"]


# ---------- fix round 1：Anthropic effort 探测的 max_tokens 与错误归类 ----------

def test_probe_reasoning_anthropic_effort_max_tokens_large_enough(client, monkeypatch):
    """effort 探测 max_tokens 必须 ≥2048：否则 Anthropic 会因 budget>max_tokens 直接报错。"""
    mid = _mk_model_row(protocol="anthropic", base_url="https://api.anthropic.com")
    _set_row_capability(mid, thinking_levels=["low", "max"], reasoning_style="effort")
    seen = []

    def fake(base_url, api_key, model, messages, max_tokens, extra=None, timeout=30):
        seen.append(max_tokens)
        return _ok(content="9.9", reasoning_content="先比较小数位……")

    monkeypatch.setattr(model_probe, "_messages_once", fake)
    with SessionLocal() as db:
        row = db.get(ProviderModel, mid)
        result = model_probe.probe_reasoning(db, row)
    assert seen and all(mt >= 2048 for mt in seen)
    assert result["supported"] == ["low", "max"]


def test_probe_reasoning_anthropic_budget_error_is_error_not_unsupported(client, monkeypatch):
    """budget/max_tokens 超限类 4xx 归 error（探测条件问题），不得误判成「不支持思考」。"""
    mid = _mk_model_row(protocol="anthropic", base_url="https://api.anthropic.com")
    _set_row_capability(mid, thinking_levels=["low"], reasoning_style="effort")

    def fake(base_url, api_key, model, messages, max_tokens, extra=None, timeout=30):
        return _err(400, "max_tokens: 64 > thinking.budget_tokens: 1024")

    monkeypatch.setattr(model_probe, "_messages_once", fake)
    with SessionLocal() as db:
        row = db.get(ProviderModel, mid)
        result = model_probe.probe_reasoning(db, row)
    assert result["levels"]["low"] == "error"
    assert "未更新探测结论" in result["error"]
    assert row.supports_thinking == -1
