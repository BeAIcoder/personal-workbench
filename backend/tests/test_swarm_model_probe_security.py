"""Swarm 任务 model-probe-security：模型能力探测 base_url 安全校验（全离线）。

根因背景：`config_store.validate_base_url`（第一轮 model-config-timeout 落地）只接线了
`llm.build_model` 的 SDK 构造路径；`model_probe._chat_once` 仍直接字符串拼接 URL，
DB 里被污染的供应商 base_url（内嵌凭据/非 http(s)/控制字符/缺主机名）会让探测请求
把已保存的 API Key 发往任意地址，绕过保存侧之外的全部校验口径。

本文件锁定：
- `_chat_once` 对非法 base_url 不发任何请求（httpx.Client.post 零调用），
  以 error 字段返回（探测失败 200+error 契约不破）；
- 错误信息与警告日志不回显 URL 主体与密钥；
- 合法私网 http（Ollama/LM Studio/内网）照常探测，去尾斜杠归一化行为不变；
- 探测函数与 HTTP 端点层对非法供应商地址同样落在 200 + error，且不写探测结论。

mock 说明：monkeypatch httpx.Client.post 记录调用并返回假 200，不联网；
探测用例经 conftest 的 client 夹具（独立临时库）。
"""
import json
import logging

import httpx
import pytest

from app.agents import model_probe
from app.agents.secrets_box import seal
from app.database import SessionLocal
from app.models import ModelProvider, ProviderModel

_SECRET_KEY = "sk-probe-secret-6543"

# 与第一轮 test_swarm_model_security.py 同一批非法 URL（口径同源）
_BAD_URLS = [
    "https://user:pass@api.example.com/v1",  # 内嵌用户名/密码
    "ftp://api.example.com/v1",  # 非 http(s) 协议
    "file:///etc/passwd",  # 本地文件协议
    "https://api.example.com/v1\nx-evil: 1",  # 控制字符（请求头注入）
    "https:///v1",  # 缺主机名
]

# 本机单用户场景合法的私网 http 地址（Ollama/LM Studio/内网网关）
_PRIVATE_URLS = [
    "http://localhost:11434/v1",
    "http://127.0.0.1:1234/v1",
    "http://192.168.1.10:8080/v1",
]

_OK_PAYLOAD = {"choices": [{"message": {"content": "9.9"}, "finish_reason": "stop"}]}


def _recording_post(calls: list):
    """假 httpx.Client.post：记录 url 与关键字参数，返回 200，不联网。"""

    def fake_post(self, url, **kwargs):
        calls.append({"url": url, "kwargs": kwargs})
        # 用固定合法 request 构造响应，避免非法 URL 本身在测试夹具里解析失败
        return httpx.Response(200, request=httpx.Request("POST", "https://probe.invalid/x"), json=_OK_PAYLOAD)

    return fake_post


def _mk_model_row(base_url="https://api.example.com/v1", model="test-model"):
    """在测试库插一条供应商 + 模型行，返回 model_id（依赖 client 夹具建表）。"""
    with SessionLocal() as db:
        prov = ModelProvider(name="探测供应商", protocol="openai", base_url=base_url, api_key=seal(_SECRET_KEY))
        db.add(prov)
        db.flush()
        pm = ProviderModel(provider_id=prov.id, model=model, context_size=128000, max_tokens=4096)
        db.add(pm)
        db.commit()
        return pm.id


# ---------- _chat_once 直连层：非法 base_url 拒绝发起请求 ----------

@pytest.mark.parametrize("bad_url", _BAD_URLS)
def test_chat_once_illegal_base_url_sends_no_request(monkeypatch, bad_url):
    """非法 base_url：不发任何请求（密钥不会被带往该地址），error 字段返回。"""
    calls: list = []
    monkeypatch.setattr(httpx.Client, "post", _recording_post(calls))
    r = model_probe._chat_once(bad_url, _SECRET_KEY, "m", [{"role": "user", "content": "hi"}], max_tokens=8)
    assert calls == []
    assert r["ok"] is False and r["status_code"] is None
    assert "ValueError" in r["error"]
    assert _SECRET_KEY not in r["error"]
    assert "user:pass" not in r["error"] and "example.com" not in r["error"]


def test_chat_once_illegal_base_url_logs_without_leaking_key(monkeypatch, caplog):
    """拒绝时记警告（不含地址与密钥）；真实 API Key 不得出现在任何日志里。"""
    calls: list = []
    monkeypatch.setattr(httpx.Client, "post", _recording_post(calls))
    with caplog.at_level(logging.WARNING, logger="app.agents.model_probe"):
        r = model_probe._chat_once("https://user:pass@api.example.com/v1", _SECRET_KEY, "m", [{"role": "user", "content": "hi"}], max_tokens=8)
    assert r["ok"] is False
    assert _SECRET_KEY not in caplog.text
    assert "user:pass" not in caplog.text


@pytest.mark.parametrize("base_url", _PRIVATE_URLS)
def test_chat_once_private_http_urls_still_probed(monkeypatch, base_url):
    """合法私网 http：照常发起探测，URL 与既有拼接一致，密钥仍正常携带。"""
    calls: list = []
    monkeypatch.setattr(httpx.Client, "post", _recording_post(calls))
    r = model_probe._chat_once(base_url, _SECRET_KEY, "m", [{"role": "user", "content": "hi"}], max_tokens=8)
    assert r["ok"] is True
    assert len(calls) == 1
    assert calls[0]["url"] == f"{base_url}/chat/completions"
    assert calls[0]["kwargs"]["headers"]["Authorization"] == f"Bearer {_SECRET_KEY}"


def test_chat_once_normalizes_trailing_slash(monkeypatch):
    """合法地址仍按既有口径去尾斜杠（validate_base_url 归一化不改变探测 URL 形态）。"""
    calls: list = []
    monkeypatch.setattr(httpx.Client, "post", _recording_post(calls))
    model_probe._chat_once("https://api.example.com/v1/", _SECRET_KEY, "m", [{"role": "user", "content": "hi"}], max_tokens=8)
    assert calls[0]["url"] == "https://api.example.com/v1/chat/completions"


# ---------- 探测函数层：非法供应商地址落 error、不发请求、不写结论 ----------

def test_probe_reasoning_illegal_base_url_error_without_request(client, monkeypatch):
    """probe_reasoning：非法 base_url 全部档位 error，不发请求，supports_thinking 保持 -1。"""
    mid = _mk_model_row(base_url="https://user:pass@api.example.com/v1")
    calls: list = []
    monkeypatch.setattr(httpx.Client, "post", _recording_post(calls))
    with SessionLocal() as db:
        row = db.get(ProviderModel, mid)
        result = model_probe.probe_reasoning(db, row)
    assert calls == []
    assert result["levels"]["none"] == "error"
    assert "ValueError" in result["steps"][0]["error"]
    assert result["error"]  # 汇总带 error（探测失败是正常结果）
    with SessionLocal() as db:
        assert db.get(ProviderModel, mid).supports_thinking == -1  # 不写探测结论


def test_probe_all_illegal_base_url_isolated_error_without_request(client, monkeypatch):
    """probe_all：三维度全部落 error 且零请求，分步详情落库、probed_at 刷新。"""
    mid = _mk_model_row(base_url="ftp://api.example.com/v1")
    calls: list = []
    monkeypatch.setattr(httpx.Client, "post", _recording_post(calls))
    with SessionLocal() as db:
        row = db.get(ProviderModel, mid)
        result = model_probe.probe_all(db, row)
    assert calls == []
    assert result["reasoning"]["error"]
    assert result["context"]["effective_context"] == 0 and result["context"]["error"]
    assert result["output"]["effective_max_output"] == 0 and result["output"]["error"]
    with SessionLocal() as db:
        row2 = db.get(ProviderModel, mid)
        assert row2.supports_thinking == -1
        assert row2.probed_context == 0 and row2.probed_max_output == 0
        assert row2.probed_at and set(json.loads(row2.probe_result)) >= {"reasoning", "context", "output"}


# ---------- HTTP 端点层：200 + error 契约不破 ----------

def test_probe_endpoint_illegal_base_url_returns_200_with_error(client, monkeypatch):
    """端点层：非法供应商地址 → 200 + result.error，不发请求，supports_thinking 保持 -1。"""
    pid = client.post(
        "/api/assistant/providers",
        json={"name": "探测供应商", "base_url": "https://user:pass@api.example.com/v1", "api_key": _SECRET_KEY},
    ).json()["provider"]["id"]
    mid = client.post(
        f"/api/assistant/providers/{pid}/models",
        json={"model": "m-probe", "context_size": 128000, "max_tokens": 4096},
    ).json()["model"]["id"]

    calls: list = []
    monkeypatch.setattr(httpx.Client, "post", _recording_post(calls))
    resp = client.post(f"/api/models/{mid}/probe/reasoning")
    assert resp.status_code == 200  # 探测失败是正常结果，不是 HTTP 错误
    body = resp.json()
    assert body["result"]["error"]
    assert "ValueError" in body["result"]["steps"][0]["error"]
    assert body["probe"]["supports_thinking"] == -1
    assert calls == []

    resp_all = client.post(f"/api/models/{mid}/probe/all")
    assert resp_all.status_code == 200
    assert resp_all.json()["result"]["reasoning"]["error"]
    assert calls == []  # 全量探测同样零请求
