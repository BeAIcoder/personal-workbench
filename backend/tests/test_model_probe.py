"""LLM 厂商预设 + 模型能力自动探测测试。

所有探测用例均 monkeypatch ``model_probe._chat_once``（或 ``httpx.Client.post``），
不发起真实网络请求；HTTP 端点用例经 conftest 的 client 夹具（独立临时库）验证。
"""
import json

import httpx
import pytest

from app.agents import config_store, model_probe
from app.agents.secrets_box import seal
from app.database import SessionLocal
from app.models import ModelProvider, ProviderModel


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


def test_probe_reasoning_anthropic_skipped(client, monkeypatch):
    mid = _mk_model_row(protocol="anthropic", base_url="https://api.anthropic.com")
    calls = []

    def fake(*args, **kwargs):
        calls.append(1)
        return _ok()

    monkeypatch.setattr(model_probe, "_chat_once", fake)
    with SessionLocal() as db:
        row = db.get(ProviderModel, mid)
        result = model_probe.probe_reasoning(db, row)
    assert result == {"skipped": "anthropic 协议暂不支持自动探测"}
    assert calls == []  # 不发起任何请求
    with SessionLocal() as db:
        assert db.get(ProviderModel, mid).supports_thinking == -1  # 保持未探测


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


def test_probe_context_anthropic_skipped(client, monkeypatch):
    mid = _mk_model_row(protocol="anthropic", base_url="https://api.anthropic.com")
    with SessionLocal() as db:
        row = db.get(ProviderModel, mid)
        assert model_probe.probe_context(db, row)["skipped"].startswith("anthropic")
        assert row.probed_context == 0


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
    # 直接验证 _apply_reasoning 之后的 param_kwargs 组装逻辑：经 build_model 走 fake
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
