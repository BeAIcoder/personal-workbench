"""Swarm 任务 model-config-timeout：模型 base_url 安全校验 + SDK 显式超时（全离线）。

根因背景：URL 安全校验（仅 http(s)/需主机名/拒绝内嵌凭据与控制字符，允许 Ollama
等私网 http，见 config_store._safe_gateway_url）原先只覆盖 urllib 多模态测试路径；
SDK 构造路径（llm.build_model → OpenAICredential/AnthropicCredential →
openai.AsyncClient/anthropic.AsyncAnthropic）与 httpx 探测路径完全没有校验，
用户可控 base_url 可把存储的 API Key 带往别处；两种协议的 SDK 客户端也没有显式
超时（沿用 agentscope/SDK 默认值）。

本文件锁定：
- build_model 两种协议 base_url 走同一套校验（复用 config_store.validate_base_url）；
- 非法 URL 不构造 SDK 客户端、不打印/回显真实密钥；
- Ollama 等私网 http 合法地址与现有保存契约（save_provider 不做 URL 校验）不受影响；
- OpenAI/Anthropic 模型构造带统一 120 秒显式 timeout（client_kwargs 透传，mock 验证）。

mock 说明：agentscope 的 credential/model 类换成记录调用的假类，不联网、
不构造真实 SDK 客户端；base_url/超时断言打在假类捕获的构造参数上。
"""
import logging
from types import SimpleNamespace

import pytest

from app.agents import config_store, llm

_SECRET_KEY = "sk-swarm-secret-9876"

_BASE_CFG = {
    "protocol": "openai",
    "api_key": _SECRET_KEY,
    "base_url": "https://api.example.com/v1",
    "model": "test-model",
    "max_tokens": 2048,
}


def _make_fake_pair():
    """一对假 credential / 假 ChatModel：记录构造参数，不联网。"""

    class FakeParameters:
        def __init__(self, **kw):
            self.kwargs = kw

    class FakeCredential:
        instances: list = []

        def __init__(self, **kw):
            self.kwargs = kw
            FakeCredential.instances.append(self)

    class FakeChatModel:
        Parameters = FakeParameters
        instances: list = []

        def __init__(self, **kw):
            self.kwargs = kw
            FakeChatModel.instances.append(self)

    return FakeCredential, FakeChatModel


@pytest.fixture()
def fake_agentscope(monkeypatch):
    """把 agentscope 两种协议的 credential/model 换成假类（build_model 内局部 import 即时生效）。"""
    cred_o, model_o = _make_fake_pair()
    monkeypatch.setattr("agentscope.credential.OpenAICredential", cred_o)
    monkeypatch.setattr("agentscope.model.OpenAIChatModel", model_o)
    cred_a, model_a = _make_fake_pair()
    monkeypatch.setattr("agentscope.credential.AnthropicCredential", cred_a)
    monkeypatch.setattr("agentscope.model.AnthropicChatModel", model_a)
    return SimpleNamespace(
        openai_cred=cred_o,
        openai_model=model_o,
        anthropic_cred=cred_a,
        anthropic_model=model_a,
    )


# ---------- base_url 安全校验（SDK 构造路径） ----------

@pytest.mark.parametrize(
    "bad_url",
    [
        "https://user:pass@api.example.com/v1",  # 内嵌用户名/密码
        "ftp://api.example.com/v1",  # 非 http(s) 协议
        "file:///etc/passwd",  # 本地文件协议
        "https://api.example.com/v1\nx-evil: 1",  # 控制字符（请求头注入）
        "https:///v1",  # 缺主机名
    ],
)
def test_build_model_rejects_unsafe_base_url(fake_agentscope, bad_url):
    """非法 base_url：build_model 返回 None，且不构造任何 SDK 客户端。"""
    cfg = {**_BASE_CFG, "base_url": bad_url}
    assert llm.build_model(cfg) is None
    assert fake_agentscope.openai_cred.instances == []
    assert fake_agentscope.openai_model.instances == []


def test_build_model_rejects_unsafe_base_url_anthropic(fake_agentscope):
    """anthropic 协议同样校验；/v1 尾巴剥离只发生在校验通过之后。"""
    cfg = {**_BASE_CFG, "protocol": "anthropic", "base_url": "https://user:pass@api.anthropic.com/v1"}
    assert llm.build_model(cfg) is None
    assert fake_agentscope.anthropic_cred.instances == []
    assert fake_agentscope.anthropic_model.instances == []


def test_build_model_invalid_url_logs_reason_without_leaking_key(fake_agentscope, caplog):
    """拒绝时记录原因（不含地址与密钥）；真实 API Key 不得出现在任何日志里。"""
    cfg = {**_BASE_CFG, "base_url": "https://user:pass@api.example.com/v1"}
    with caplog.at_level(logging.WARNING, logger="app.agents.llm"):
        assert llm.build_model(cfg) is None
    assert _SECRET_KEY not in caplog.text


@pytest.mark.parametrize(
    "private_url",
    [
        "http://localhost:11434/v1",  # Ollama
        "http://127.0.0.1:1234/v1",  # LM Studio
        "http://192.168.1.10:8080/v1",  # 内网网关
    ],
)
def test_build_model_allows_private_http_urls(fake_agentscope, private_url):
    """本机单用户场景：私网 http 合法地址照常构造模型（既有行为不回归）。"""
    cfg = {**_BASE_CFG, "base_url": private_url}
    model = llm.build_model(cfg)
    assert model is not None
    assert fake_agentscope.openai_cred.instances[0].kwargs["base_url"] == private_url


# ---------- 统一 120 秒显式 timeout ----------

def test_openai_model_gets_explicit_timeout(fake_agentscope):
    """OpenAI 协议：client_kwargs 透传统一 120 秒 timeout。"""
    model = llm.build_model(_BASE_CFG)
    assert model is not None
    assert llm.LLM_TIMEOUT_SECONDS == 120
    assert model.kwargs["client_kwargs"]["timeout"] == 120


def test_anthropic_model_gets_explicit_timeout(fake_agentscope):
    """Anthropic 协议：同一常量、同一超时值。"""
    cfg = {**_BASE_CFG, "protocol": "anthropic", "base_url": "https://api.anthropic.com"}
    model = llm.build_model(cfg)
    assert model is not None
    assert llm.LLM_TIMEOUT_SECONDS == 120
    assert model.kwargs["client_kwargs"]["timeout"] == 120


def test_anthropic_timeout_with_v1_suffix_stripped(fake_agentscope):
    """/v1 尾巴剥离（大小写不敏感、其余部分原样）与 timeout 透传同时成立。"""
    cfg = {**_BASE_CFG, "protocol": "anthropic", "base_url": "https://Gw.Example.COM/API/V1/"}
    model = llm.build_model(cfg)
    assert model is not None
    assert fake_agentscope.anthropic_cred.instances[0].kwargs["base_url"] == "https://Gw.Example.COM/API"
    assert model.kwargs["client_kwargs"]["timeout"] == 120


# ---------- 校验口径统一（validate_base_url 与网关探测同源） ----------

@pytest.mark.parametrize(
    "bad_url",
    [
        "https://user:pass@api.example.com/v1",
        "ftp://api.example.com/v1",
        "file:///etc/passwd",
        "https://api.example.com/v1\nx-evil: 1",
        "https:///v1",
    ],
)
def test_validator_matches_gateway_policy_on_bad_urls(bad_url):
    """同一批非法 URL：validate_base_url 与 _safe_gateway_url 都必须拒绝。"""
    with pytest.raises(ValueError):
        config_store.validate_base_url(bad_url)
    with pytest.raises(ValueError):
        config_store._safe_gateway_url(bad_url, "/chat/completions")


@pytest.mark.parametrize(
    "good_url",
    [
        "https://api.example.com/v1",
        "https://Gw.Example.COM:8443/API",
        "http://localhost:11434/v1",
        "http://192.168.1.10:8080/v1",
    ],
)
def test_validator_matches_gateway_policy_on_good_urls(good_url):
    """同一批合法 URL：两者都放行；validate_base_url 返回去尾斜杠的归一化值。"""
    normalized = config_store.validate_base_url(good_url)
    assert normalized == good_url.rstrip("/")
    assert config_store._safe_gateway_url(good_url, "/chat/completions") == f"{good_url.rstrip('/')}/chat/completions"


def test_validator_error_message_does_not_echo_url():
    """错误信息不回显 URL 主体（防止 userinfo 等敏感片段进日志/响应）。"""
    with pytest.raises(ValueError) as exc:
        config_store.validate_base_url("ftp://user:pass@api.example.com/v1")
    assert "user:pass" not in str(exc.value)


# ---------- 现有契约保持 ----------

def test_save_provider_keeps_base_url_contract(client):
    """保存契约不变：save_provider 不做 URL 校验、原样入库（校验在构造/测试侧）。"""
    exotic = "https://user:pass@10.0.0.9:8443/gw"
    pid = client.post(
        "/api/assistant/providers",
        json={"name": "url-contract", "protocol": "openai", "base_url": exotic, "api_key": "sk-keep-0001"},
    ).json()["provider"]["id"]
    prov = next(p for p in client.get("/api/assistant/providers").json()["providers"] if p["id"] == pid)
    assert prov["base_url"] == exotic


def test_mask_dual_forms_still_recognized():
    """掩码双形态保持：哨兵 ******** 与回显 ••••••+后四位 都是「未修改」标记。"""
    assert config_store.MASK == "********"
    echoed = config_store.masked({"api_key": "sk-abcd1234"})["api_key"]
    assert echoed == config_store.MASK_PREFIX + "1234"
