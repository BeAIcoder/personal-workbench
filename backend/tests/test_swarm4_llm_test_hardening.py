"""外部验证 L11/L8/L9 修复回归：连通性测试失败中文化 + 30s 超时 + 探测限流重试。

覆盖：
- _friendly_error：401/403/429/连接错误/超时/5xx/400 → 中文提示，不回传堆栈与上游原文；
- test_connection/test_tool_calling：build_model 收到 client_timeout=30（测试路径显式收紧）；
- test_connection：模型空回复 → ok=False + 中文提示；
- _chat_once：429 限流自动退避重试（默认 2 次），重试后成功则 ok；耗尽重试仍 429 → error。

异步用例按项目惯例用 asyncio.run 驱动（无 pytest-asyncio 插件）。
"""
import asyncio

import httpx
import pytest

from app.agents import config_store, model_probe


# ---------- _friendly_error 单元 ----------

class AuthenticationError(Exception):
    pass


class APIConnectionError(Exception):
    pass


class APITimeoutError(Exception):
    pass


class InternalServerError(Exception):
    pass


def _named(name):
    return type(name, (Exception,), {})


@pytest.mark.parametrize(
    "exc,expect",
    [
        (AuthenticationError("Error code: 401 - Token is invalid."), "API Key"),
        (_named("PermissionDeniedError")("403 forbidden"), "权限"),
        (APITimeoutError("Request timed out"), "超时"),
        (APIConnectionError("Connection error."), "Base URL"),
        (InternalServerError("Error code: 500"), "5xx"),
        (Exception("upstream said 429 slow down"), "429"),
        (RuntimeError("something odd"), "测试失败"),
    ],
)
def test_friendly_error_maps_to_chinese(exc, expect):
    msg = config_store._friendly_error(exc)
    assert expect in msg
    assert "Traceback" not in msg and "{" not in msg  # 不回传堆栈与上游 JSON 原文


def test_friendly_error_no_stack_or_upstream_text():
    msg = config_store._friendly_error(AuthenticationError("Error code: 401 - {'code': 30014, 'message': 'Token is invalid.'}"))
    assert "30014" not in msg and "Token is invalid" not in msg


# ---------- 测试路径超时与空回复 ----------

class _TextBlock:
    type = "text"

    def __init__(self, text):
        self.text = text


class _FakeModel:
    """最小 agentscope 模型替身：可编程返回内容或抛异常。"""

    def __init__(self, text="连接成功", exc=None):
        self._text = text
        self._exc = exc

    async def __call__(self, *args, **kwargs):
        if self._exc:
            raise self._exc
        return {"content": [_TextBlock(self._text)], "usage": None}


def _patch_build_model(monkeypatch, captured, model):
    def fake(cfg):
        captured.update(cfg)
        return model

    monkeypatch.setattr("app.agents.llm.build_model", fake)


_CFG = {"api_key": "sk-test", "base_url": "https://x.example/v1", "model": "m", "protocol": "openai"}


def test_test_connection_uses_short_timeout(monkeypatch):
    captured = {}
    _patch_build_model(monkeypatch, captured, _FakeModel())
    result = asyncio.run(config_store.test_connection(dict(_CFG)))
    assert result["ok"] is True
    assert captured.get("client_timeout") == config_store.TEST_TIMEOUT_SECONDS == 30.0
    assert captured.get("client_max_retries") == 0  # 测试路径快速失败，不靠重试拖长等待


def test_tool_calling_uses_short_timeout(monkeypatch):
    captured = {}

    class _ToolCallBlock:
        type = "tool_call"
        name = "get_current_time"

    class _ToolModel:
        async def __call__(self, *args, **kwargs):
            assert kwargs.get("tools")
            return {"content": [_ToolCallBlock()], "usage": None}

    _patch_build_model(monkeypatch, captured, _ToolModel())
    result = asyncio.run(config_store.test_tool_calling(dict(_CFG)))
    assert result["ok"] is True
    assert captured.get("client_timeout") == 30.0
    assert captured.get("client_max_retries") == 0


def test_test_connection_empty_reply_is_failure(monkeypatch):
    captured = {}
    _patch_build_model(monkeypatch, captured, _FakeModel(text="   "))
    result = asyncio.run(config_store.test_connection(dict(_CFG)))
    assert result["ok"] is False
    assert "空回复" in result["error"]


def test_test_connection_friendly_error(monkeypatch):
    captured = {}
    _patch_build_model(monkeypatch, captured, _FakeModel(exc=AuthenticationError("Error code: 401")))
    result = asyncio.run(config_store.test_connection(dict(_CFG)))
    assert result["ok"] is False
    assert "API Key" in result["error"]


# ---------- _chat_once 429 退避重试 ----------

class _FakeResp:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self.text = f"HTTP {status_code}"
        self._payload = payload or {"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]}

    def json(self):
        return self._payload


def _patch_post(monkeypatch, script):
    """script: 按调用次序返回的状态码列表（末位兜底重复）。"""
    calls = []

    def fake_post(self, url, **kwargs):
        calls.append(url)
        return _FakeResp(script[min(len(calls) - 1, len(script) - 1)])

    monkeypatch.setattr(httpx.Client, "post", fake_post)
    monkeypatch.setattr(model_probe.time, "sleep", lambda s: None)
    return calls


def test_chat_once_retries_on_429_then_succeeds(monkeypatch):
    calls = _patch_post(monkeypatch, [429, 429, 200])
    out = model_probe._chat_once("https://x.example/v1", "k", "m", [{"role": "user", "content": "hi"}], max_tokens=16)
    assert len(calls) == 3
    assert out["ok"] is True and out["status_code"] == 200


def test_chat_once_rate_limit_exhausted_returns_error(monkeypatch):
    calls = _patch_post(monkeypatch, [429])
    out = model_probe._chat_once("https://x.example/v1", "k", "m", [{"role": "user", "content": "hi"}], max_tokens=16)
    # 首次 + 2 次重试 = 3 次，仍 429 → error 字段，绝不抛异常
    assert len(calls) == 3
    assert out["ok"] is False and out["status_code"] == 429 and out["error"]


# ---------- 引擎/数据路径：目录或单一文件（外部验证第 4 章需求） ----------

def test_resolve_engine_root_accepts_dir_or_file(tmp_path):
    from app.utils import resolve_engine_root

    d = tmp_path / "engine"
    d.mkdir()
    f = d / "entry.py"
    f.write_text("# entry", encoding="utf-8")

    assert resolve_engine_root("") is None
    assert resolve_engine_root(None) is None
    assert resolve_engine_root(str(d)) == d            # 目录原样返回
    assert resolve_engine_root(str(f)) == d            # 单一文件 → 父目录
    assert resolve_engine_root("  " + str(d) + "  ") == d  # 首尾空白归一
    missing = tmp_path / "nope"
    assert resolve_engine_root(str(missing)) == missing  # 不存在 → 原样返回，交由调用方校验
