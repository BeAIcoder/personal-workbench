"""Task 4：在线模型同步适配器、安全边界与幂等事务（全离线：mock HTTP + 临时库）。

锁定契约：
- 各供应商适配器把官方列表 payload 精确映射为 RemoteModel（上下文/输入输出/视觉/工具/
  effort 与「未知」的边界：远端未给的字段一律未知，不从模型名猜）；
- OpenCode 不把未知 api_family 默认当 chat_completions：未知/不兼容协议必须 skipped 并说明；
- 腾讯在线 status 逐字保留（preview 不臆改），在线结果优先于目录；
- 非法 URL 零请求；401/403/404/429/超时/非 JSON/超大响应不泄漏 Key、不变成「空模型列表」；
- 429 重试遵守有界 Retry-After；OpenRouter 只跟进同源分页；同步只发 GET 目录请求；
- 批内去重、单事务、第二次同步全 unchanged、并发 409、一条非法记录整体回滚；
- 远端消失不删本地；manual/probe/note/temperature/enabled/active 全保留；
- 无官方列表/无凭证/在线失败：目录兜底 + 明确 warning，partial=true、online=false，
  绝不伪装实时同步。

mock 说明：httpx.Client 换成挂 MockTransport 的真实客户端（真实状态码/头/JSON 解析），
密钥全部为合成值；DB 走 conftest 临时 SQLite，不碰真实 backend/data。
"""
import hashlib
import hmac
import json
import threading
import urllib.parse

import httpx
import pytest

from app.agents import config_store, model_catalog, model_sync
from app.agents.model_sync import ProviderSyncBusy, RemoteFetchError, SyncRecordError
from app.agents.secrets_box import seal
from app.database import SessionLocal
from app.models import AgentSettings, ModelProvider, ProviderModel

_SYNTH_KEY = "sk-synthetic-sync-key-1234"
_SYNTH_AK = "AKTESTSYNTH001"
_SYNTH_SK = "SK-SYNTHETIC-ONLY-9876"

# ---------- 适配器 fixture（按各官方接口文档的真实结构镜像） ----------

_OPENAI_PAYLOAD = {
    "object": "list",
    "data": [
        {"id": "gpt-4o-mini", "object": "model", "created": 1705398160, "owned_by": "system"},
        {"id": "gpt-4.1", "object": "model", "created": 1743463200, "owned_by": "system"},
        {"id": "text-embedding-3-large", "object": "model", "created": 1692901427, "owned_by": "system"},
        {"id": "whisper-1", "object": "model", "created": 1677532384, "owned_by": "openai-internal"},
    ],
}

# DeepSeek 官方 /models：ID + name（展示名）+ context_window/max_output_tokens/modalities +
# effort.supported_levels/default_level（思考档位）；富字段缺失即未知
_DEEPSEEK_PAYLOAD = {
    "object": "list",
    "data": [
        {
            "id": "deepseek-chat",
            "object": "model",
            "created": 1704067200,
            "owned_by": "deepseek",
            "name": "DeepSeek Chat",
            "context_window": 131072,
            "max_output_tokens": 8192,
            "input_modalities": ["text"],
            "output_modalities": ["text"],
            "supports_tools": True,
            "effort": {"supported_levels": ["off", "low", "high"], "default_level": "off"},
        },
        {"id": "deepseek-reasoner", "object": "model", "created": 1704067200, "owned_by": "deepseek"},
    ],
}

_OPENROUTER_PAGE1 = {
    "data": [
        {
            "id": "deepseek/deepseek-v4-flash",
            "name": "DeepSeek: DeepSeek V4 Flash",
            "created": 1755000000,
            "description": "…",
            "context_length": 1048576,
            "architecture": {"input_modalities": ["text", "image"], "output_modalities": ["text"], "tokenizer": "Other"},
            "pricing": {"prompt": "0.0000001", "completion": "0.0000002"},
            "top_provider": {"context_length": 1048576, "max_completion_tokens": 393216, "is_moderated": False},
            "supported_parameters": ["tools", "reasoning", "max_tokens"],
            "reasoning": {"supported_efforts": ["high", "none"]},
        },
        {
            "id": "openai/gpt-4o-mini",
            "name": "OpenAI: GPT-4o-mini",
            "created": 1705398160,
            "context_length": 128000,
            "architecture": {"input_modalities": ["text", "image"], "output_modalities": ["text"]},
            "top_provider": {"max_completion_tokens": 16384},
            "supported_parameters": ["tools"],
        },
    ],
    "links": {"next": "https://openrouter.ai/api/v1/models?output_modalities=text&page=2"},
}
_OPENROUTER_PAGE2 = {
    "data": [
        {
            "id": "moonshotai/kimi-k2.6",
            "name": "MoonshotAI: Kimi K2.6",
            "context_length": 262144,
            "architecture": {"input_modalities": ["text", "image", "video"], "output_modalities": ["text"]},
            "top_provider": {"max_completion_tokens": 65536},
            "supported_parameters": ["tools", "reasoning"],
        }
    ],
}

# 阿里百炼官方 GET /api/v1/models 返回形状（output.models；ID 字段 model、name 为展示名；
# inference_metadata 模态数组；model_info 三项长度；features/capabilities 字符串数组）
_ALIBABA_PAGE1 = {
    "code": None,
    "message": None,
    "success": True,
    "output": {
        "total": 3,
        "page_no": 1,
        "page_size": 2,
        "models": [
            {
                "model": "qwen3.8-max",
                "name": "通义千问3.8-Max",
                "description": "通义千问3系列Max模型",
                "features": ["function-calling", "structured-outputs", "web-search"],
                "capabilities": ["TG", "Reasoning"],
                "published_time": "2025-11-11 12:00:00",
                "inference_metadata": {"request_modality": ["Text", "Image"], "response_modality": ["Text"]},
                "model_info": {
                    "context_window": 1048576,
                    "max_input_tokens": 1048576,
                    "max_output_tokens": 65536,
                    "max_reasoning_tokens": None,
                },
            },
            {"model": "qwen-turbo", "name": "Qwen Turbo"},
        ],
    },
    "request_id": "d5f5201f-ee7a-9e3d-8569-bc0eedec21f9",
}
_ALIBABA_PAGE2 = {
    "output": {
        "total": 3,
        "page_no": 2,
        "page_size": 2,
        "models": [
            {
                "model": "qwen-image-3.0-pro",
                "name": "Qwen-Image-3.0-Pro",
                "features": ["model-experience"],
                "capabilities": ["IG"],
                "inference_metadata": {"request_modality": ["Text"], "response_modality": ["Image"]},
                "model_info": {"context_window": None, "max_input_tokens": None, "max_output_tokens": None},
                "inference_offline_info": {"offline_time": "2026-12-01"},
            }
        ],
    }
}
_ALIBABA_PAYLOAD = _ALIBABA_PAGE1

# 腾讯 TokenHub：在线 ID/status（preview 逐字保留，不臆改）
_TENCENT_PAYLOAD = {
    "object": "list",
    "data": [
        {"id": "hy3", "object": "model", "created": 1755000000, "owned_by": "tokenhub", "status": "preview"},
        {"id": "hy4-preview", "object": "model", "created": 1755000000, "owned_by": "tokenhub", "status": "online"},
        {"id": "hunyuan-t1", "object": "model", "created": 1700000000, "owned_by": "tokenhub", "status": "deprecated"},
    ],
}

# Anthropic 官方 GET /v1/models：lifecycle 状态 + capabilities 对象
# （effort 是 thinking 的兄弟节点；thinking.types 为对象；官方无 default 字段；
#  server_tools 不等于普通 function tools）。结构按官方文档示例，逐模型取值不同属正常。
_ANTHROPIC_PAYLOAD = {
    "data": [
        {
            "type": "model",
            "id": "claude-sonnet-4-6",
            "display_name": "Claude Sonnet 4.6",
            "created_at": "2025-05-01T00:00:00Z",
            "lifecycle": "active",
            "max_input_tokens": 200000,
            "max_tokens": 64000,
            "capabilities": {
                "image_input": {"supported": True},
                "thinking": {
                    "supported": True,
                    "types": {
                        "adaptive": {"supported": True},
                        "disabled": {"supported": True},
                        "enabled": {"supported": True},
                    },
                },
                "effort": {
                    "high": {"supported": True},
                    "low": {"supported": True},
                    "medium": {"supported": False},
                    "max": {"supported": True},
                },
                "server_tools": {"supported": True, "web_search": {"supported": True}},
            },
        },
        {
            "type": "model",
            "id": "claude-3-opus-20240229",
            "display_name": "Claude 3 Opus",
            "created_at": "2024-02-01T00:00:00Z",
            "lifecycle": "retired",
        },
    ],
    "has_more": False,
    "first_id": "claude-sonnet-4-6",
    "last_id": "claude-3-opus-20240229",
}

# 火山官方管控面（OpenAPI 风格只读 POST）：ListFoundationModels → Result.Items[]
# 请求体仅允许 {} 或 PageNumber/PageSize 两个只读分页字段；响应带 Result.TotalCount
_VOLC_FOUNDATION_PAYLOAD = {
    "Result": {
        "TotalCount": 2,
        "Items": [
            {"Name": "doubao-seed-1-6", "DisplayName": "Doubao Seed 1.6"},
            {"Name": "doubao-seed-2-0-pro", "DisplayName": "Doubao Seed 2.0 Pro"},
        ],
    }
}
_VOLC_FOUNDATION_PAGE1 = {
    "Result": {
        "TotalCount": 3,
        "Items": [
            {"Name": "doubao-seed-1-6", "DisplayName": "Doubao Seed 1.6"},
            {"Name": "doubao-seed-2-0-pro", "DisplayName": "Doubao Seed 2.0 Pro"},
        ],
    }
}
_VOLC_FOUNDATION_PAGE2 = {"Result": {"TotalCount": 3, "Items": [{"Name": "doubao-seed-2-1-pro"}]}}
# 两个 Plan Action → Result.Datas[].ModelID（无明确分页字段，按单页处理）
_VOLC_PLAN_PAYLOAD = {"Result": {"Datas": [{"ModelID": "deepseek-v4-flash"}]}}

# OpenCode 列表：协议/模态/下线状态过滤（未知 api_family 不得默认当 chat_completions）
_OPENCODE_PAYLOAD = {
    "data": [
        {"id": "kimi-k2.6", "api_family": "chat_completions", "output_modalities": ["text"]},
        {"id": "claude-sonnet-4-6", "api_family": "messages", "output_modalities": ["text"]},
        {"id": "gpt-5.4", "output_modalities": ["text"]},
        {"id": "gpt-5.4-codex", "api_family": "responses", "output_modalities": ["text"]},
        {"id": "sora-2", "api_family": "chat_completions", "output_modalities": ["image"]},
        {"id": "space-bunny", "api_family": "chat_completions", "output_modalities": ["text"], "status": "deprecated"},
    ],
}


# ---------- 测试辅助 ----------

_REAL_HTTPX_CLIENT = httpx.Client


def _mock_httpx(monkeypatch, handler):
    """把 httpx.Client 换成挂 MockTransport 的真实客户端（真实状态码/响应头/JSON 解析）。

    真实类在导入时捕获：同一测试里多次替换时不会拿到上一个工厂（否则 transport 重复传参）。
    """

    def _factory(**kwargs):
        kwargs.pop("trust_env", None)
        return _REAL_HTTPX_CLIENT(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(httpx, "Client", _factory)


def _forbid_requests(monkeypatch):
    """任何 HTTP 调用都让测试失败（目录兜底路径必须零请求）。"""

    def _factory(**kwargs):
        raise AssertionError("目录兜底路径不得发起任何 HTTP 请求")

    monkeypatch.setattr(httpx, "Client", _factory)


def _mk_provider(preset_key="deepseek", models_url="", api_key=_SYNTH_KEY, name="同步供应商", sync_credentials=""):
    with SessionLocal() as db:
        prov = ModelProvider(
            name=name,
            protocol="openai",
            base_url="https://api.deepseek.com/v1",
            api_key=seal(api_key) if api_key else "",
            preset_key=preset_key,
            models_url=models_url,
            sync_credentials=sync_credentials,
        )
        db.add(prov)
        db.commit()
        return prov.id


def _provider_row(pid):
    with SessionLocal() as db:
        row = db.get(ModelProvider, pid)
        db.expunge_all()  # 分离实例保留已加载属性，供 fetch 适配器读取
        return row


def _rows(pid):
    with SessionLocal() as db:
        rows = db.query(ProviderModel).filter(ProviderModel.provider_id == pid).order_by(ProviderModel.id).all()
        db.expunge_all()
        return rows


def _meta_of(pm):
    with SessionLocal() as db:
        row = db.get(ProviderModel, pm.id)
        return model_catalog.capability_meta_of(row)


# ---------- 适配器：精确映射与未知边界 ----------

def test_parse_openai_models_maps_ids_with_unknown_capabilities():
    """OpenAI 简表：官方只保证 ID，能力一律未知（含 whisper/embedding 也先按 ID 解析，过滤在同步层）。"""
    models = model_sync.parse_openai_models(_OPENAI_PAYLOAD)
    assert [m.model for m in models] == ["gpt-4o-mini", "gpt-4.1", "text-embedding-3-large", "whisper-1"]
    m = models[0]
    assert m.api_family == "chat_completions"
    assert m.source == "provider_api"
    assert m.context_size is None and m.max_input_tokens is None and m.max_tokens is None
    assert m.supports_thinking == -1 and m.supports_tools == -1
    assert m.supports_vision is None and m.input_modalities is None and m.output_modalities is None


def test_parse_deepseek_models_maps_rich_fields_unknown_when_absent():
    """DeepSeek 富字段精确映射；简表条目（无富字段）全部保持未知。"""
    models = model_sync.parse_deepseek_models(_DEEPSEEK_PAYLOAD)
    assert [m.model for m in models] == ["deepseek-chat", "deepseek-reasoner"]
    rich, bare = models
    assert rich.display_name == "DeepSeek Chat"  # 官方展示名字段是 name
    assert rich.context_size == 131072 and rich.max_tokens == 8192
    assert rich.input_modalities == ["text"] and rich.output_modalities == ["text"]
    assert rich.supports_tools == 1
    assert rich.supports_thinking == 1 and rich.thinking_levels == ["off", "low", "high"]
    assert rich.default_thinking_level == "off"  # effort.default_level
    assert rich.supports_vision is False  # 输入模态只有 text
    assert bare.context_size is None and bare.max_tokens is None
    assert bare.supports_thinking == -1 and bare.supports_tools == -1
    assert bare.supports_vision is None and bare.input_modalities is None


def test_parse_openrouter_models_maps_rich_fields_and_effort_from_reasoning_only():
    """OpenRouter：context/top_provider/模态/supported_parameters/reasoning 精确映射。"""
    models = model_sync.parse_openrouter_models(_OPENROUTER_PAGE1)
    assert [m.model for m in models] == ["deepseek/deepseek-v4-flash", "openai/gpt-4o-mini"]
    ds, gpt = models
    assert ds.context_size == 1048576 and ds.max_tokens == 393216
    assert ds.input_modalities == ["text", "image"] and ds.output_modalities == ["text"]
    assert ds.supports_vision is True and ds.supports_tools == 1
    assert ds.supports_thinking == 1 and ds.thinking_levels == ["high", "none"]
    # 无 reasoning 声明 → 思考未知（不因 supported_parameters 含 reasoning 猜「支持」）
    assert gpt.supports_thinking == -1 and gpt.thinking_levels is None
    assert gpt.supports_tools == 1 and gpt.supports_vision is True
    assert gpt.max_tokens == 16384 and gpt.context_size == 128000


def test_parse_alibaba_models_maps_official_output_shape():
    """阿里官方 output.models：model/name、inference_metadata 模态、model_info 三项、
    features/capabilities 字符串数组；缺字段保持未知。"""
    models = model_sync.parse_alibaba_models(_ALIBABA_PAGE1)
    assert [m.model for m in models] == ["qwen3.8-max", "qwen-turbo"]
    rich, bare = models
    assert rich.display_name == "通义千问3.8-Max"  # name 是展示名，model 才是 ID
    assert rich.context_size == 1048576 and rich.max_input_tokens == 1048576 and rich.max_tokens == 65536
    assert rich.input_modalities == ["text", "image"] and rich.output_modalities == ["text"]
    assert rich.supports_tools == 1  # features 含 function-calling
    assert rich.supports_thinking == 1  # capabilities 含 Reasoning
    assert rich.supports_vision is True  # 输入模态含 image
    assert rich.status == ""  # 无下线信息 → 不臆测状态
    assert bare.context_size is None and bare.supports_tools == -1 and bare.supports_thinking == -1
    assert bare.supports_vision is None


def test_parse_alibaba_models_marks_offline_info():
    """阿里 inference_offline_info（含 offline_time）→ status=offline，由同步层跳过。"""
    models = model_sync.parse_alibaba_models(_ALIBABA_PAGE2)
    assert [m.model for m in models] == ["qwen-image-3.0-pro"]
    m = models[0]
    assert m.status == "offline"
    assert m.output_modalities == ["image"] and m.input_modalities == ["text"]
    assert m.context_size is None and m.supports_tools == -1 and m.supports_thinking == -1


def test_parse_tencent_models_keeps_status_verbatim():
    """腾讯：status 逐字保留（preview 不臆改），能力字段未知。"""
    models = model_sync.parse_tencent_models(_TENCENT_PAYLOAD)
    assert [m.model for m in models] == ["hy3", "hy4-preview", "hunyuan-t1"]
    assert [m.status for m in models] == ["preview", "online", "deprecated"]
    assert all(m.context_size is None and m.supports_tools == -1 for m in models)


def test_parse_anthropic_models_maps_official_capabilities_object():
    """Anthropic 官方 capabilities 是对象，且 effort 是 thinking 的**兄弟节点**：
    `capabilities.image_input.supported`→视觉；`capabilities.thinking.supported`→思考；
    `capabilities.effort.<level>.supported=true`→档位（按官方推荐顺序）；
    官方无 default 字段 → default_thinking_level 必须为 None（不猜）；
    官方无通用工具能力键 → supports_tools 保持 -1（server_tools 不推断 function tools）。"""
    models = model_sync.parse_anthropic_models(_ANTHROPIC_PAYLOAD)
    assert [m.model for m in models] == ["claude-sonnet-4-6", "claude-3-opus-20240229"]
    active, retired = models
    assert active.display_name == "Claude Sonnet 4.6"
    assert active.api_family == "messages"
    assert active.status == "active" and retired.status == "retired"
    assert active.supports_vision is True
    assert active.supports_thinking == 1
    assert active.thinking_levels == ["high", "low", "max"]  # medium.supported=false 不入列
    assert active.default_thinking_level is None  # 官方无 default 字段，不编不猜
    assert active.supports_tools == -1  # 官方无通用工具能力键，不猜
    assert active.max_input_tokens == 200000 and active.max_tokens == 64000
    assert retired.supports_vision is None and retired.supports_thinking == -1
    assert retired.thinking_levels is None and retired.default_thinking_level is None
    assert retired.supports_tools == -1  # 只有 server_tools，不推断普通 function tools
    assert retired.max_input_tokens is None and retired.max_tokens is None


def test_parse_volcengine_official_action_shapes():
    """火山官方：ListFoundationModels → Result.Items[].Name/DisplayName；
    两个 Plan Action → Result.Datas[].ModelID。"""
    foundation = model_sync.parse_volcengine_foundation_models(_VOLC_FOUNDATION_PAYLOAD)
    assert [m.model for m in foundation] == ["doubao-seed-1-6", "doubao-seed-2-0-pro"]
    assert foundation[0].display_name == "Doubao Seed 1.6"
    assert foundation[0].api_family == "chat_completions"
    assert foundation[0].context_size is None and foundation[0].supports_tools == -1
    plan = model_sync.parse_volcengine_plan_models(_VOLC_PLAN_PAYLOAD)
    assert [m.model for m in plan] == ["deepseek-v4-flash"]
    assert plan[0].context_size is None


def test_parse_opencode_models_marks_unknown_api_family():
    """OpenCode 解析只做映射：api_family 逐条保留（缺失=unknown），status/模态原样带出。"""
    models = model_sync.parse_opencode_models(_OPENCODE_PAYLOAD)
    assert [m.model for m in models] == [
        "kimi-k2.6", "claude-sonnet-4-6", "gpt-5.4", "gpt-5.4-codex", "sora-2", "space-bunny",
    ]
    assert [m.api_family for m in models] == [
        "chat_completions", "messages", model_sync.UNKNOWN_API_FAMILY, "responses", "chat_completions", "chat_completions",
    ]
    assert models[0].output_modalities == ["text"] and models[4].output_modalities == ["image"]
    assert models[5].status == "deprecated"
    assert all(m.context_size is None and m.supports_thinking == -1 for m in models)


# ---------- 安全与失败：非法 URL 零请求；HTTP 失败不变成空列表 ----------

@pytest.mark.parametrize(
    "bad_url",
    [
        "https://user:pass@api.example.com/models",
        "ftp://api.example.com/models",
        "file:///etc/passwd",
        "https://api.example.com/models\nx-evil: 1",
        "https:///models",
    ],
)
def test_fetch_illegal_models_url_sends_no_request(client, monkeypatch, bad_url):
    """非法列表地址：零请求、抛 RemoteFetchError（不写成空列表）、不回显地址与密钥。"""
    pid = _mk_provider(models_url=bad_url)
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"object": "list", "data": []})

    _mock_httpx(monkeypatch, handler)
    with pytest.raises(RemoteFetchError) as exc:
        model_sync.fetch_remote_models(_provider_row(pid), {"api_key": _SYNTH_KEY})
    assert calls == []
    msg = str(exc.value)
    assert _SYNTH_KEY not in msg and "user:pass" not in msg and "api.example.com" not in msg


@pytest.mark.parametrize("status", [401, 403, 404, 500, 503])
def test_fetch_http_error_raises_without_key_leak(client, monkeypatch, status):
    """401/403/404/5xx：抛错而非空列表；错误信息不含密钥。"""
    pid = _mk_provider(models_url="https://api.deepseek.com/models")
    seen = []

    def handler(request):
        seen.append(request.headers.get("Authorization"))
        return httpx.Response(status, json={"error": {"message": "boom"}})

    _mock_httpx(monkeypatch, handler)
    with pytest.raises(RemoteFetchError) as exc:
        model_sync.fetch_remote_models(_provider_row(pid), {"api_key": _SYNTH_KEY})
    assert seen == [f"Bearer {_SYNTH_KEY}"]  # 密钥只发往校验过的列表地址
    assert str(status) in str(exc.value)
    assert _SYNTH_KEY not in str(exc.value)


def test_fetch_timeout_is_honest_error(client, monkeypatch):
    """超时：转为明确错误，不变成空列表。"""
    pid = _mk_provider(models_url="https://api.deepseek.com/models")

    def handler(request):
        raise httpx.ReadTimeout("read timed out")

    _mock_httpx(monkeypatch, handler)
    with pytest.raises(RemoteFetchError) as exc:
        model_sync.fetch_remote_models(_provider_row(pid), {"api_key": _SYNTH_KEY})
    assert "超时" in str(exc.value)


def test_fetch_non_json_is_honest_error(client, monkeypatch):
    """非 JSON 响应：明确错误，不变成空列表。"""
    pid = _mk_provider(models_url="https://api.deepseek.com/models")

    def handler(request):
        return httpx.Response(200, text="<html>gateway</html>", headers={"content-type": "text/html"})

    _mock_httpx(monkeypatch, handler)
    with pytest.raises(RemoteFetchError) as exc:
        model_sync.fetch_remote_models(_provider_row(pid), {"api_key": _SYNTH_KEY})
    assert "JSON" in str(exc.value)


def test_fetch_oversized_response_is_rejected(client, monkeypatch):
    """超大响应：拒绝解析并明确报错。"""
    pid = _mk_provider(models_url="https://api.deepseek.com/models")
    oversized = b"x" * (model_sync.MAX_RESPONSE_BYTES + 1)

    def handler(request):
        return httpx.Response(200, content=oversized, headers={"content-type": "application/json"})

    _mock_httpx(monkeypatch, handler)
    with pytest.raises(RemoteFetchError) as exc:
        model_sync.fetch_remote_models(_provider_row(pid), {"api_key": _SYNTH_KEY})
    assert "过大" in str(exc.value)


def test_fetch_429_retries_with_bounded_retry_after(client, monkeypatch):
    """429：按 Retry-After 退避重试，但等待时间有界（不得真的等一小时）。"""
    pid = _mk_provider(models_url="https://api.deepseek.com/models")
    waits = []
    monkeypatch.setattr(model_sync.time, "sleep", lambda s: waits.append(s))
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(429, headers={"Retry-After": "3600"}, json={"error": "rate limited"})
        return httpx.Response(200, json=_DEEPSEEK_PAYLOAD)

    _mock_httpx(monkeypatch, handler)
    models, _ = model_sync.fetch_remote_models(_provider_row(pid), {"api_key": _SYNTH_KEY})
    assert len(calls) == 2
    assert waits == [model_sync.MAX_RETRY_WAIT_SECONDS]  # 3600s 被夹到有界上限
    assert [m.model for m in models] == ["deepseek-chat", "deepseek-reasoner"]


def test_fetch_429_without_retry_after_fails_immediately(client, monkeypatch):
    """429 无 Retry-After：不盲猜重试，立即明确失败。"""
    pid = _mk_provider(models_url="https://api.deepseek.com/models")
    waits = []
    monkeypatch.setattr(model_sync.time, "sleep", lambda s: waits.append(s))
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(429, json={"error": "rate limited"})

    _mock_httpx(monkeypatch, handler)
    with pytest.raises(RemoteFetchError) as exc:
        model_sync.fetch_remote_models(_provider_row(pid), {"api_key": _SYNTH_KEY})
    assert len(calls) == 1 and waits == []
    assert "429" in str(exc.value)


def test_fetch_redirect_is_not_followed(client, monkeypatch):
    """默认不跟随重定向：3xx 明确失败（避免把密钥带往重定向目标）。"""
    pid = _mk_provider(models_url="https://api.deepseek.com/models")

    def handler(request):
        return httpx.Response(302, headers={"location": "https://elsewhere.example.com/models"})

    _mock_httpx(monkeypatch, handler)
    with pytest.raises(RemoteFetchError) as exc:
        model_sync.fetch_remote_models(_provider_row(pid), {"api_key": _SYNTH_KEY})
    assert "重定向" in str(exc.value)


def test_openrouter_pagination_follows_same_origin_only(client, monkeypatch):
    """OpenRouter 分页：同源 links.next 跟进合并；跨域链接不请求并明确警告。"""
    pid = _mk_provider(preset_key="openrouter", models_url="https://openrouter.ai/api/v1/models?output_modalities=text")
    calls = []

    def same_origin(request):
        calls.append(str(request.url))
        if "page=2" in str(request.url):
            return httpx.Response(200, json=_OPENROUTER_PAGE2)
        return httpx.Response(200, json=_OPENROUTER_PAGE1)

    _mock_httpx(monkeypatch, same_origin)
    models, warnings = model_sync.fetch_remote_models(_provider_row(pid), {"api_key": _SYNTH_KEY})
    assert len(calls) == 2 and calls[1].endswith("page=2")
    assert [m.model for m in models] == [
        "deepseek/deepseek-v4-flash", "openai/gpt-4o-mini", "moonshotai/kimi-k2.6",
    ]
    assert warnings == []

    pid2 = _mk_provider(preset_key="openrouter", models_url="https://openrouter.ai/api/v1/models?output_modalities=text", name="跨域分页")
    cross_calls = []
    payload = {"data": _OPENROUTER_PAGE1["data"], "links": {"next": "https://evil.example.com/api/v1/models?page=2"}}

    def cross_origin(request):
        cross_calls.append(str(request.url))
        return httpx.Response(200, json=payload)

    _mock_httpx(monkeypatch, cross_origin)
    models2, warnings2 = model_sync.fetch_remote_models(_provider_row(pid2), {"api_key": _SYNTH_KEY})
    assert len(cross_calls) == 1  # 跨域下一页从未被请求
    assert len(models2) == 2
    assert any("同源" in w or "跨域" in w for w in warnings2)


def test_openrouter_pagination_is_capped(client, monkeypatch):
    """分页页数有上限：异常厂商无限 next 不得造成无限请求。"""
    pid = _mk_provider(preset_key="openrouter", models_url="https://openrouter.ai/api/v1/models")
    calls = []

    def handler(request):
        calls.append(str(request.url))
        page = int(request.url.params.get("page", "1"))
        return httpx.Response(200, json={
            "data": [{"id": f"m{page}", "context_length": 1000}],
            "links": {"next": f"https://openrouter.ai/api/v1/models?page={page + 1}"},
        })

    _mock_httpx(monkeypatch, handler)
    models, warnings = model_sync.fetch_remote_models(_provider_row(pid), {"api_key": _SYNTH_KEY})
    assert len(calls) == model_sync.MAX_PAGES
    assert len(models) == model_sync.MAX_PAGES
    assert any("可能未取全" in w for w in warnings)  # 截断必须明说，不当作完整成功


def test_sync_reports_truncated_pagination(client, monkeypatch):
    """截断不能当完整成功：同步响应带 truncated 标记与 warning。"""
    pid = _mk_provider(preset_key="openrouter", models_url="https://openrouter.ai/api/v1/models")

    def handler(request):
        page = int(request.url.params.get("page", "1"))
        return httpx.Response(200, json={
            "data": [{"id": f"m{page}", "context_length": 1000}],
            "links": {"next": f"https://openrouter.ai/api/v1/models?page={page + 1}"},
        })

    _mock_httpx(monkeypatch, handler)
    with SessionLocal() as db:
        result = model_sync.sync_provider_models(db, pid)
    assert result["online"] is True and result["added"] == model_sync.MAX_PAGES
    assert result["truncated"] is True
    assert any("可能未取全" in w for w in result["warnings"])


def test_anthropic_pagination_truncation_warns(client, monkeypatch):
    """Anthropic 到页数上限且上游仍有后续页：同样 warning + truncated。"""
    pid = _mk_provider(preset_key="anthropic", models_url="https://api.anthropic.com/v1/models")
    calls = []

    def handler(request):
        calls.append(str(request.url))
        page = len(calls)
        return httpx.Response(200, json={
            "data": [{"type": "model", "id": f"claude-m{page}", "display_name": f"M{page}",
                      "created_at": "2025-01-01T00:00:00Z", "lifecycle": "active"}],
            "has_more": True,
            "last_id": f"claude-m{page}",
        })

    _mock_httpx(monkeypatch, handler)
    models, warnings = model_sync.fetch_remote_models(_provider_row(pid), {"api_key": _SYNTH_KEY})
    assert len(models) == model_sync.MAX_PAGES
    assert any("可能未取全" in w for w in warnings)


def test_alibaba_pagination_truncation_warns(client, monkeypatch):
    """阿里到页数上限且 total 未取满：warning + truncated（不静默截断）。"""
    pid = _mk_provider(preset_key="dashscope", models_url="https://dashscope.aliyuncs.com/api/v1/models")

    def handler(request):
        page = int(urllib.parse.parse_qs(urllib.parse.urlsplit(str(request.url)).query)["page_no"][0])
        return httpx.Response(200, json={
            "output": {"total": 999, "page_no": page, "page_size": 2,
                       "models": [{"model": f"m{page}-a", "name": f"M{page}A"},
                                  {"model": f"m{page}-b", "name": f"M{page}B"}]}
        })

    _mock_httpx(monkeypatch, handler)
    models, warnings = model_sync.fetch_remote_models(_provider_row(pid), {"api_key": _SYNTH_KEY})
    assert len(models) == model_sync.MAX_PAGES * 2
    assert any("可能未取全" in w for w in warnings)


def test_volcengine_pagination_truncation_warns(client, monkeypatch):
    """火山到页数上限且 TotalCount 未取满：warning + truncated。"""
    creds = seal(json.dumps({"access_key": _SYNTH_AK, "secret_key": _SYNTH_SK}, ensure_ascii=False))
    pid = _mk_provider(preset_key="volcengine", sync_credentials=creds)
    bodies = []

    def handler(request):
        bodies.append(request.content)
        page = json.loads(request.content).get("PageNumber", 1)
        return httpx.Response(200, json={
            "Result": {"TotalCount": 999, "Items": [{"Name": f"doubao-p{page}"}]}
        })

    _mock_httpx(monkeypatch, handler)
    with SessionLocal() as db:
        result = model_sync.sync_provider_models(db, pid)
    assert result["online"] is True and result["truncated"] is True
    assert any("可能未取全" in w for w in result["warnings"])
    assert len(bodies) == model_sync.MAX_PAGES
    assert bodies == [b'{"PageNumber":%d,"PageSize":100}' % p for p in range(1, model_sync.MAX_PAGES + 1)]


def test_sync_only_issues_catalog_requests(client, monkeypatch):
    """同步只发目录/管控请求（GET；火山管控面为只读列表 POST），绝不发推理请求。"""
    pid = _mk_provider(models_url="https://api.deepseek.com/models")
    methods = []
    urls = []

    def handler(request):
        methods.append(request.method)
        urls.append(str(request.url))
        return httpx.Response(200, json=_DEEPSEEK_PAYLOAD)

    _mock_httpx(monkeypatch, handler)
    with SessionLocal() as db:
        result = model_sync.sync_provider_models(db, pid)
    assert result["online"] is True
    assert methods == ["GET"] * len(methods) and methods
    assert all("chat/completions" not in u and "/messages" not in u for u in urls)


def test_sync_skips_non_chat_models_from_openai_style_list(client, monkeypatch):
    """OpenAI 兼容列表里的嵌入/语音类模型不同步（skipped 说明），只留文本对话模型。"""
    pid = _mk_provider(models_url="https://api.deepseek.com/models")
    _mock_httpx(monkeypatch, lambda request: httpx.Response(200, json=_OPENAI_PAYLOAD))
    with SessionLocal() as db:
        result = model_sync.sync_provider_models(db, pid)
    assert {r.model for r in _rows(pid)} == {"gpt-4o-mini", "gpt-4.1"}
    reasons = {s["model"]: s["reason"] for s in result["skipped"]}
    assert set(reasons) == {"text-embedding-3-large", "whisper-1"}
    assert all("文本对话" in r for r in reasons.values())


def test_is_non_chat_id_matches_vendor_segment(client):
    """非对话前缀按完整 ID 或最后一个 `/` 后末段命中（厂商段 ID 不漏过）；正常对话 ID 不误伤。

    断点：OpenRouter 的 `vendor/model` 形状 ID（openai/text-embedding-3-small）在只按
    完整 ID startswith 时不命中，会被当对话模型同步。fixture 不依赖 output_modalities 兜底。
    """
    from app.agents.model_sync import _is_non_chat_id

    # 应跳过（非对话）：完整 ID 或最后一个 `/` 后末段命中前缀
    for mid in (
        "text-embedding-3-large",  # 无厂商段（既有口径）
        "openai/text-embedding-3-small",  # 厂商段嵌入
        "acme/whisper-1",  # 厂商段语音
        "vendor/dall-e-3",  # 厂商段图像生成
        "x/gpt-image-1",  # 厂商段图像生成
        "y/omni-moderation-latest",  # 厂商段审核
        "a/b/tts-1-hd",  # 多级路径取末段
        "acme/embeddings-plus-chat",  # 末段以 embedding 开头（前缀语义）
        "acme/text-embeddings",  # 复数仍以 text-embedding 前缀开头（前缀语义）
    ):
        assert _is_non_chat_id(mid) is True, mid
    # 不误伤（正常对话 ID）
    for mid in (
        "gpt-4o-mini",
        "openai/gpt-5.1",
        "deepseek-chat",
        "moonshotai/kimi-k3.1-pro",
        "qwen/qwen3-max",
        "acme/not-text-embedding",  # 末段非前缀开头（前缀语义，不子串误伤）
        "acme/chat-embedding-fast",  # 含 embedding 子串但末段非前缀开头（不误伤）
    ):
        assert _is_non_chat_id(mid) is False, mid


def test_sync_skips_vendor_segment_non_chat_models(client, monkeypatch):
    """OpenRouter 形状列表：厂商段嵌入/语音/图像生成 ID 进 skipped 并说明，对话模型不受影响。"""
    payload = {
        "object": "list",
        "data": [
            {
                "id": "acme/chat-pro",
                "name": "Acme Chat Pro",
                "context_length": 200000,
                "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
                "supported_parameters": ["tools"],
            },
            {
                "id": "openai/text-embedding-3-small",
                "name": "OpenAI Embedding",
                "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
                "supported_parameters": ["dimensions"],
            },
            {
                "id": "acme/whisper-1",
                "name": "Acme Whisper",
                "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
            },
            {
                "id": "acme/gpt-image-1",
                "name": "Acme Image",
                "architecture": {"input_modalities": ["text", "image"], "output_modalities": ["text"]},
            },
        ],
    }
    pid = _mk_provider(preset_key="openrouter", models_url="https://openrouter.ai/api/v1/models")
    _mock_httpx(monkeypatch, lambda request: httpx.Response(200, json=payload))
    with SessionLocal() as db:
        result = model_sync.sync_provider_models(db, pid)
    assert {r.model for r in _rows(pid)} == {"acme/chat-pro"}
    reasons = {s["model"]: s["reason"] for s in result["skipped"]}
    assert set(reasons) == {"openai/text-embedding-3-small", "acme/whisper-1", "acme/gpt-image-1"}
    assert all("文本对话" in r for r in reasons.values())


# ---------- 幂等事务 ----------

def test_sync_dedupes_duplicate_ids_in_batch(client, monkeypatch):
    """批内重复 ID：只建一行，重复项进 skipped 并说明。"""
    pid = _mk_provider(models_url="https://api.deepseek.com/models")
    payload = {
        "object": "list",
        "data": [
            {"id": "deepseek-chat", "object": "model", "created": 1, "owned_by": "deepseek"},
            {"id": "deepseek-chat", "object": "model", "created": 1, "owned_by": "deepseek"},
        ],
    }
    _mock_httpx(monkeypatch, lambda request: httpx.Response(200, json=payload))
    with SessionLocal() as db:
        result = model_sync.sync_provider_models(db, pid)
    assert result["added"] == 1
    assert [s["model"] for s in result["skipped"]] == ["deepseek-chat"]
    assert "重复" in result["skipped"][0]["reason"]
    assert len(_rows(pid)) == 1


def test_second_sync_reports_all_unchanged(client, monkeypatch):
    """连续两次同步：第二次全部 unchanged，不重复写 synced_at。"""
    pid = _mk_provider(models_url="https://api.deepseek.com/models")
    payload = {"object": "list", "data": [{"id": "deepseek-chat", "object": "model", "created": 1, "owned_by": "deepseek"}]}
    _mock_httpx(monkeypatch, lambda request: httpx.Response(200, json=payload))
    with SessionLocal() as db:
        first = model_sync.sync_provider_models(db, pid)
    assert (first["added"], first["updated"], first["unchanged"]) == (1, 0, 0)
    assert first["online"] is True and first["partial"] is False
    synced_before = _rows(pid)[0].synced_at
    with SessionLocal() as db:
        second = model_sync.sync_provider_models(db, pid)
    assert (second["added"], second["updated"], second["unchanged"]) == (0, 0, 1)
    assert second["skipped"] == [] and second["errors"] == []
    assert _rows(pid)[0].synced_at == synced_before  # 幂等：不触碰时间戳


def test_one_malformed_record_rolls_back_whole_batch(client, monkeypatch):
    """一条非法记录（缺模型 ID）：整批回滚，不留下半批。"""
    pid = _mk_provider(models_url="https://api.deepseek.com/models")
    payload = {
        "object": "list",
        "data": [
            {"id": "good-one", "object": "model", "created": 1, "owned_by": "deepseek"},
            {"object": "model", "created": 1, "owned_by": "deepseek"},
        ],
    }
    _mock_httpx(monkeypatch, lambda request: httpx.Response(200, json=payload))
    with SessionLocal() as db:
        result = model_sync.sync_provider_models(db, pid)
    assert result["ok"] is False
    assert result["errors"] and result["added"] == 0
    assert _rows(pid) == []  # 半批不得落库


def test_malformed_payload_shape_raises_sync_record_error(monkeypatch):
    """解析层对结构非法的记录抛 SyncRecordError（供同步层整体回滚）。"""
    with pytest.raises(SyncRecordError):
        model_sync.parse_openai_models({"object": "list", "data": [{"object": "model"}]})


# ---------- 终审 Important 2：畸形记录结构守卫 + fetch 阶段结构化兜底 ----------

# 常见畸形记录：字符串 / null / 数组 / 数字（都曾让 record.get 裸抛 AttributeError 穿透端点）
_MALFORMED_RECORDS = ("not-a-dict", None, ["id", "x"], 42)


@pytest.mark.parametrize("bad", _MALFORMED_RECORDS)
def test_parsers_reject_non_object_records(bad):
    """终审 Important 2：所有解析器对非对象记录一律 SyncRecordError（绝不裸 500）。"""
    for parser, payload in (
        (model_sync.parse_openai_models, {"data": [{"id": "ok-one"}, bad]}),
        (model_sync.parse_opencode_models, {"data": [{"id": "ok-one"}, bad]}),
        (model_sync.parse_deepseek_models, {"data": [{"id": "ok-one"}, bad]}),
        (model_sync.parse_openrouter_models, {"data": [{"id": "ok-one"}, bad]}),
        (model_sync.parse_tencent_models, {"data": [{"id": "ok-one"}, bad]}),
        (model_sync.parse_anthropic_models, {"data": [{"id": "ok-one"}, bad]}),
        (model_sync.parse_alibaba_models, {"output": {"models": [{"model": "ok-one"}, bad]}}),
        # 阿里兜底路径（data.models）同样守卫
        (model_sync.parse_alibaba_models, {"data": {"models": [{"model": "ok-one"}, bad]}}),
        (model_sync.parse_volcengine_foundation_models, {"Result": {"Items": [{"Name": "ok-one"}, bad]}}),
        (model_sync.parse_volcengine_plan_models, {"Result": {"Datas": [{"ModelID": "ok-one"}, bad]}}),
    ):
        with pytest.raises(SyncRecordError):
            parser(payload)


def test_sync_non_object_record_is_structured_failure_not_500(client, monkeypatch):
    """终审 Important 2：畸形记录经守卫 → SyncRecordError → 结构化 ok=false，端点不裸 500。"""
    pid = _mk_provider(models_url="https://api.deepseek.com/models")
    payload = {"object": "list", "data": [{"id": "good-one", "object": "model"}, "not-an-object"]}
    _mock_httpx(monkeypatch, lambda request: httpx.Response(200, json=payload))
    with SessionLocal() as db:
        result = model_sync.sync_provider_models(db, pid)
    assert result["ok"] is False and result["added"] == 0
    assert result["errors"] and "非法记录" in result["errors"][0]
    assert _rows(pid) == []  # 半批不得落库

    resp = client.post(f"/api/assistant/providers/{pid}/sync-models", json={})
    assert resp.status_code == 200  # 结构化失败，不裸 500
    body = resp.json()
    assert body["ok"] is False and body["errors"]
    assert _rows(pid) == []


def test_sync_fetch_stage_unexpected_error_is_structured_failure(client, monkeypatch):
    """终审 Important 2：适配器未预期缺陷（非 Remote/SyncRecord 异常）也结构化失败。

    错误只记异常类型（不外泄内部消息），整批不落库，端点 200 + ok=false。
    """
    pid = _mk_provider(models_url="https://api.deepseek.com/models")

    def boom(provider, credentials):
        raise RuntimeError("adapter bug with sensitive internals")

    monkeypatch.setattr(model_sync, "fetch_remote_models", boom)
    with SessionLocal() as db:
        result = model_sync.sync_provider_models(db, pid)
    assert result["ok"] is False and result["added"] == 0 and result["online"] is False
    assert result["errors"] == ["在线列表解析失败，已整批拒绝（RuntimeError）"]
    assert "sensitive internals" not in json.dumps(result, ensure_ascii=False)
    assert _rows(pid) == []

    resp = client.post(f"/api/assistant/providers/{pid}/sync-models", json={})
    assert resp.status_code == 200
    assert resp.json()["ok"] is False
    assert _rows(pid) == []


def test_sync_keeps_local_rows_when_remote_model_disappears(client, monkeypatch):
    """远端消失不删本地：旧行原样保留。"""
    pid = _mk_provider(models_url="https://api.deepseek.com/models")
    first_payload = {
        "object": "list",
        "data": [
            {"id": "remote-a", "object": "model", "created": 1, "owned_by": "deepseek"},
            {"id": "remote-b", "object": "model", "created": 1, "owned_by": "deepseek"},
        ],
    }
    second_payload = {"object": "list", "data": [{"id": "remote-a", "object": "model", "created": 1, "owned_by": "deepseek"}]}
    _mock_httpx(monkeypatch, lambda request: httpx.Response(200, json=first_payload))
    with SessionLocal() as db:
        model_sync.sync_provider_models(db, pid)
    _mock_httpx(monkeypatch, lambda request: httpx.Response(200, json=second_payload))
    with SessionLocal() as db:
        result = model_sync.sync_provider_models(db, pid)
    assert {r.model for r in _rows(pid)} == {"remote-a", "remote-b"}
    assert result["added"] == 0 and result["unchanged"] == 1


def test_sync_preserves_manual_probe_note_temperature_enabled_active(client, monkeypatch):
    """同步不覆盖手动能力字段、探测结果、备注、温度、启停与激活态。"""
    pid = _mk_provider(models_url="https://api.deepseek.com/models")
    saved = config_store.save_provider_model({
        "provider_id": pid,
        "model": "deepseek-flash",
        "context_size": 999999,
        "max_tokens": 4096,
        "supports_tools": 0,
        "api_family": "messages",
        "note": "主力模型",
        "temperature": "0.3",
    })
    mid = saved["id"]
    with SessionLocal() as db:
        pm = db.get(ProviderModel, mid)
        pm.supports_thinking = 1
        pm.probed_context = 123456
        pm.probed_max_output = 8192
        pm.probed_at = "2026-10-20T10:00:00"
        pm.probe_result = json.dumps({"reasoning": {"levels": {"none": "ok"}}}, ensure_ascii=False)
        db.commit()
    config_store.activate_model(mid)
    payload = {"object": "list", "data": [{"id": "deepseek-flash", "object": "model", "created": 1, "owned_by": "deepseek"}]}
    _mock_httpx(monkeypatch, lambda request: httpx.Response(200, json=payload))
    with SessionLocal() as db:
        model_sync.sync_provider_models(db, pid)
    with SessionLocal() as db:
        row = db.get(ProviderModel, mid)
        assert row.context_size == 999999 and row.max_tokens == 4096  # 手动字段不被目录/远端覆盖
        assert row.supports_tools == 0
        meta = model_catalog.capability_meta_of(row)
        assert meta["api_family"] == "messages"
        assert meta["sources"]["context_size"] == "manual"
        assert row.note == "主力模型" and row.temperature == "0.3" and row.enabled is True
        assert row.supports_thinking == 1 and row.probed_context == 123456
        assert row.probed_max_output == 8192 and row.probed_at == "2026-10-20T10:00:00"
        assert db.get(AgentSettings, 1).active_model_id == mid  # 激活态保留


def test_override_manual_sync_overwrites_manual_fields(client, monkeypatch):
    """override_manual=True：显式覆盖手动字段并清除对应 manual 标记。"""
    pid = _mk_provider(models_url="https://api.deepseek.com/models")
    mid = config_store.save_provider_model({
        "provider_id": pid, "model": "deepseek-flash", "context_size": 4096,
    })["id"]
    payload = {"object": "list", "data": [{"id": "deepseek-flash", "object": "model", "created": 1, "owned_by": "deepseek"}]}
    _mock_httpx(monkeypatch, lambda request: httpx.Response(200, json=payload))
    with SessionLocal() as db:
        result = model_sync.sync_provider_models(db, pid, override_manual=True)
    assert result["updated"] == 1
    with SessionLocal() as db:
        row = db.get(ProviderModel, mid)
        assert row.context_size == 1048576  # 被目录/远端值覆盖
        assert "context_size" not in model_catalog.capability_meta_of(row)["manual_fields"]


def test_concurrent_sync_conflicts(client, monkeypatch):
    """同一供应商并发同步：第二次直接冲突（ProviderSyncBusy → 端点 409）。"""
    pid = _mk_provider(models_url="https://api.deepseek.com/models")
    lock = model_sync._lock_for(pid)
    assert lock.acquire(blocking=False)
    try:
        with SessionLocal() as db:
            with pytest.raises(ProviderSyncBusy):
                model_sync.sync_provider_models(db, pid)
    finally:
        lock.release()


def test_concurrent_sync_threads_conflict(client, monkeypatch):
    """线程级并发：第二个同步在第一个持有锁期间失败，不产生重复行。"""
    pid = _mk_provider(models_url="https://api.deepseek.com/models")
    payload = {"object": "list", "data": [{"id": "deepseek-chat", "object": "model", "created": 1, "owned_by": "deepseek"}]}
    _mock_httpx(monkeypatch, lambda request: httpx.Response(200, json=payload))
    entered = threading.Event()
    release = threading.Event()
    real_fetch = model_sync.fetch_remote_models

    def slow_fetch(provider, credentials):
        entered.set()
        release.wait(5)
        return real_fetch(provider, credentials)

    monkeypatch.setattr(model_sync, "fetch_remote_models", slow_fetch)
    outcomes = []

    def run_sync():
        with SessionLocal() as db:
            try:
                outcomes.append(model_sync.sync_provider_models(db, pid))
            except ProviderSyncBusy as exc:
                outcomes.append(exc)

    t = threading.Thread(target=run_sync)
    t.start()
    assert entered.wait(5)
    with SessionLocal() as db:
        with pytest.raises(ProviderSyncBusy):
            model_sync.sync_provider_models(db, pid)
    release.set()
    t.join(10)
    assert isinstance(outcomes[0], dict) and outcomes[0]["added"] == 1
    assert len(_rows(pid)) == 1


# ---------- 诚实兜底：目录 / 套餐 / 火山凭证 ----------

def test_sync_remote_failure_is_honest(client, monkeypatch):
    """在线失败必须诚实：partial=true、online=false、目录兜底 + warning，绝不伪装实时。"""
    pid = _mk_provider(models_url="https://api.deepseek.com/models")

    def handler(request):
        assert request.headers["Authorization"] == f"Bearer {_SYNTH_KEY}"
        return httpx.Response(401, json={"error": {"message": "invalid key"}})

    _mock_httpx(monkeypatch, handler)
    with SessionLocal() as db:
        result = model_sync.sync_provider_models(db, pid)
    assert result["online"] is False
    assert result["partial"] is True
    assert result["source"] == "official_catalog"
    assert result["errors"] and "401" in result["errors"][0]
    assert _SYNTH_KEY not in json.dumps(result, ensure_ascii=False)
    assert any("目录" in w for w in result["warnings"])
    # 目录兜底真实生效：deepseek 目录的两个现役模型落库，能力来自目录
    rows = {r.model: r for r in _rows(pid)}
    assert set(rows) == {"deepseek-flash", "deepseek-v4-pro"}
    assert rows["deepseek-flash"].context_size == 1048576
    assert rows["deepseek-flash"].supports_vision is True
    meta = model_catalog.capability_meta_of(rows["deepseek-flash"])
    assert meta["sources"]["context_size"] == "official_catalog"


def test_sync_without_official_list_uses_catalog_only(client, monkeypatch):
    """无官方列表的套餐（dashscope_coding / zhipu_coding GLM）：零请求、目录兜底、partial + warning。"""
    pid = _mk_provider(preset_key="dashscope_coding", api_key=_SYNTH_KEY)
    _forbid_requests(monkeypatch)
    with SessionLocal() as db:
        result = model_sync.sync_provider_models(db, pid)
    assert result["online"] is False and result["partial"] is True
    assert result["source"] == "official_catalog"
    assert any("目录" in w for w in result["warnings"])
    rows = {r.model for r in _rows(pid)}
    assert "qwen3.5-plus" in rows and "glm-4.7" not in rows  # deprecated 默认不同步
    assert "glm-4.7" in {s["model"] for s in result["skipped"]}

    glm_pid = _mk_provider(preset_key="zhipu_coding", name="GLM Coding")
    with SessionLocal() as db:
        glm = model_sync.sync_provider_models(db, glm_pid)
    assert glm["online"] is False and glm["partial"] is True
    assert any("目录" in w for w in glm["warnings"])
    glm_rows = {r.model for r in _rows(glm_pid)}
    assert "glm-5.3" in glm_rows  # GLM Coding 套餐：目录候选 + 官方思考档位
    with SessionLocal() as db:
        row = next(r for r in _rows(glm_pid) if r.model == "glm-5.3")
    assert model_catalog.capability_meta_of(row)["default_thinking_level"] == "max"


def test_sync_include_disabled_syncs_deprecated_models(client, monkeypatch):
    """include_disabled=true：deprecated 目录条目也同步（状态如实记录）。"""
    pid = _mk_provider(preset_key="dashscope_coding")
    _forbid_requests(monkeypatch)
    with SessionLocal() as db:
        result = model_sync.sync_provider_models(db, pid, include_disabled=True)
    assert "glm-4.7" in {r.model for r in _rows(pid)}
    assert result["skipped"] == []


def test_sync_missing_api_key_is_catalog_only_with_warning(client, monkeypatch):
    """有列表地址但没 Key：不发起请求，目录兜底 + 明确 warning。"""
    pid = _mk_provider(models_url="https://api.deepseek.com/models", api_key="")
    _forbid_requests(monkeypatch)
    with SessionLocal() as db:
        result = model_sync.sync_provider_models(db, pid)
    assert result["online"] is False and result["partial"] is True
    assert any("Key" in w for w in result["warnings"])
    assert {r.model for r in _rows(pid)} == {"deepseek-flash", "deepseek-v4-pro"}


def test_volcengine_without_credentials_is_catalog_only_with_warning(client, monkeypatch):
    """火山未配置 AK/SK：零请求、目录兜底，warning 明确说明需要只读 AK/SK。"""
    pid = _mk_provider(preset_key="volcengine")
    _forbid_requests(monkeypatch)
    with SessionLocal() as db:
        result = model_sync.sync_provider_models(db, pid)
    assert result["online"] is False and result["partial"] is True
    assert any("AK/SK" in w for w in result["warnings"])
    rows = {r.model for r in _rows(pid)}
    assert "doubao-seed-2-1-pro-260628" in rows
    assert "doubao-seed-1-6-251015" not in rows  # deprecated 默认跳过


def test_volcengine_with_credentials_posts_official_signed_request(client, monkeypatch):
    """火山管控同步：官方 host + Action/Version query + 只读 POST（body 仅分页字段）+ 四段签名头。"""
    creds = seal(json.dumps({"access_key": _SYNTH_AK, "secret_key": _SYNTH_SK}, ensure_ascii=False))
    pid = _mk_provider(preset_key="volcengine", sync_credentials=creds)
    seen = {}

    def handler(request):
        seen["method"] = request.method
        seen["url"] = str(request.url)
        seen["body"] = request.content
        seen["headers"] = {k.lower(): v for k, v in request.headers.items()}
        return httpx.Response(200, json=_VOLC_FOUNDATION_PAYLOAD)

    _mock_httpx(monkeypatch, handler)
    with SessionLocal() as db:
        result = model_sync.sync_provider_models(db, pid)
    assert result["online"] is True and result["partial"] is False
    assert result["truncated"] is False
    assert seen["method"] == "POST"
    assert seen["url"].startswith("https://ark.cn-beijing.volcengineapi.com/?")
    assert "Action=ListFoundationModels" in seen["url"] and "Version=2024-01-01" in seen["url"]
    # body 只允许 {} 或 PageNumber/PageSize 两个只读分页字段
    assert seen["body"] == b'{"PageNumber":1,"PageSize":100}'
    headers = seen["headers"]
    assert headers["content-type"] == "application/json"
    assert headers["x-date"]
    assert headers["x-content-sha256"] == hashlib.sha256(seen["body"]).hexdigest()
    assert headers["authorization"].startswith(f"HMAC-SHA256 Credential={_SYNTH_AK}/")
    assert "SignedHeaders=content-type;host;x-content-sha256;x-date" in headers["authorization"]
    assert _SYNTH_SK not in seen["url"] and _SYNTH_SK not in headers["authorization"]
    assert _SYNTH_SK not in json.dumps(result, ensure_ascii=False)
    rows = {r.model for r in _rows(pid)}
    assert {"doubao-seed-1-6", "doubao-seed-2-0-pro"} <= rows  # Result.Items[].Name 落库


def test_volcengine_foundation_paginates_by_total_count(client, monkeypatch):
    """火山 ListFoundationModels 官方分页：body 带 PageNumber，按 Result.TotalCount 收口。"""
    creds = seal(json.dumps({"access_key": _SYNTH_AK, "secret_key": _SYNTH_SK}, ensure_ascii=False))
    pid = _mk_provider(preset_key="volcengine", sync_credentials=creds)
    bodies = []

    def handler(request):
        bodies.append(request.content)
        if b'"PageNumber":2' in request.content:
            return httpx.Response(200, json=_VOLC_FOUNDATION_PAGE2)
        return httpx.Response(200, json=_VOLC_FOUNDATION_PAGE1)

    _mock_httpx(monkeypatch, handler)
    with SessionLocal() as db:
        result = model_sync.sync_provider_models(db, pid)
    assert result["online"] is True and result["truncated"] is False
    assert len(bodies) == 2
    assert bodies[1] == b'{"PageNumber":2,"PageSize":100}'
    assert {r.model for r in _rows(pid)} == {"doubao-seed-1-6", "doubao-seed-2-0-pro", "doubao-seed-2-1-pro"}


def test_volcengine_plan_action_posts_empty_body(client, monkeypatch):
    """火山 Coding/Agent Plan：无明确分页字段 → 单页只读 POST，body 为 {}。"""
    creds = seal(json.dumps({"access_key": _SYNTH_AK, "secret_key": _SYNTH_SK}, ensure_ascii=False))
    pid = _mk_provider(preset_key="volcengine_coding", sync_credentials=creds)
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["method"] = request.method
        seen["body"] = request.content
        seen["sha"] = request.headers.get("x-content-sha256")
        return httpx.Response(200, json=_VOLC_PLAN_PAYLOAD)

    _mock_httpx(monkeypatch, handler)
    with SessionLocal() as db:
        result = model_sync.sync_provider_models(db, pid)
    assert result["online"] is True
    assert "Action=ListArkCodingPlanModel" in seen["url"] and seen["method"] == "POST"
    assert seen["body"] == b"{}"  # 无分页字段：单页空体
    assert seen["sha"] == hashlib.sha256(b"{}").hexdigest()
    assert "deepseek-v4-flash" in {r.model for r in _rows(pid)}


def test_volcengine_forces_official_version_even_if_url_overrides(client, monkeypatch):
    """models_url 带其他 Version 时强制覆盖为官方 2024-01-01（setdefault 会沿用的 bug）。"""
    creds = seal(json.dumps({"access_key": _SYNTH_AK, "secret_key": _SYNTH_SK}, ensure_ascii=False))
    pid = _mk_provider(
        preset_key="volcengine",
        models_url="https://ark.cn-beijing.volcengineapi.com/?Action=ListFoundationModels&Version=2020-01-01",
        sync_credentials=creds,
    )
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        return httpx.Response(200, json=_VOLC_FOUNDATION_PAYLOAD)

    _mock_httpx(monkeypatch, handler)
    with SessionLocal() as db:
        result = model_sync.sync_provider_models(db, pid)
    assert result["online"] is True
    assert "Version=2024-01-01" in seen["url"] and "Version=2020-01-01" not in seen["url"]


@pytest.mark.parametrize(
    "bad_url",
    [
        "https://evil.example.com/?Action=ListFoundationModels&Version=2024-01-01",  # 非官方 host
        "https://ark.cn-beijing.volcengineapi.com/?Action=GetAPIKey&Version=2024-01-01",  # 非白名单 Action
        "https://ark.cn-beijing.volcengineapi.com/api/v3/chat/completions",  # 推理路径
        "https://ark.cn-beijing.volcengineapi.com/?Action=ChatCompletion&Version=2024-01-01",
    ],
)
def test_volcengine_rejects_non_official_host_action_or_path(client, monkeypatch, bad_url):
    """火山发送前守卫：仅允许官方 host + 三个只读列表 Action + 根路径；其余零请求拒绝。"""
    creds = seal(json.dumps({"access_key": _SYNTH_AK, "secret_key": _SYNTH_SK}, ensure_ascii=False))
    pid = _mk_provider(preset_key="volcengine", models_url=bad_url, sync_credentials=creds)
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=_VOLC_FOUNDATION_PAYLOAD)

    _mock_httpx(monkeypatch, handler)
    with pytest.raises(RemoteFetchError):
        model_sync.fetch_remote_models(_provider_row(pid), {"api_key": _SYNTH_KEY, "access_key": _SYNTH_AK, "secret_key": _SYNTH_SK})
    assert calls == []


def test_volcengine_models_url_override_keeps_official_gate(client, monkeypatch):
    """models_url 显式覆盖优先：覆盖为官方地址+白名单 Action 时照常签名请求。"""
    creds = seal(json.dumps({"access_key": _SYNTH_AK, "secret_key": _SYNTH_SK}, ensure_ascii=False))
    pid = _mk_provider(
        preset_key="volcengine",
        models_url="https://ark.cn-beijing.volcengineapi.com/?Action=ListFoundationModels&Version=2024-01-01",
        sync_credentials=creds,
    )
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        return httpx.Response(200, json=_VOLC_FOUNDATION_PAYLOAD)

    _mock_httpx(monkeypatch, handler)
    with SessionLocal() as db:
        result = model_sync.sync_provider_models(db, pid)
    assert result["online"] is True
    assert "Action=ListFoundationModels" in seen["url"]


def test_volc_signature_matches_official_canonical_vector():
    """火山签名：按官方 canonical 字段（POST/根路径/Action+Version 排序 query/四段
    CanonicalHeaders/X-Content-Sha256）在测试内独立重算 HMAC 链后逐字节比对。"""
    ak, sk = "AKTESTSYNTH001", "SK-SYNTHETIC-ONLY-9876"
    host = "ark.cn-beijing.volcengineapi.com"
    x_date = "20261026T120000Z"
    body_sha = hashlib.sha256(b"{}").hexdigest()

    # 测试侧手写 canonical 请求（不调用生产代码拼装），字段与官方文档一致
    canonical_request = "\n".join([
        "POST",
        "/",
        "Action=ListFoundationModels&Version=2024-01-01",
        "content-type:application/json\n"
        f"host:{host}\n"
        f"x-content-sha256:{body_sha}\n"
        f"x-date:{x_date}\n",
        "content-type;host;x-content-sha256;x-date",
        body_sha,
    ])
    string_to_sign = "\n".join([
        "HMAC-SHA256",
        x_date,
        "20261026/cn-beijing/ark/request",
        hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
    ])
    k_date = hmac.new(sk.encode("utf-8"), b"20261026", hashlib.sha256).digest()
    k_region = hmac.new(k_date, b"cn-beijing", hashlib.sha256).digest()
    k_service = hmac.new(k_region, b"ark", hashlib.sha256).digest()
    k_signing = hmac.new(k_service, b"request", hashlib.sha256).digest()
    expected = hmac.new(k_signing, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()

    got = model_sync.volc_signature(
        "POST", "/", {"Action": "ListFoundationModels", "Version": "2024-01-01"},
        host, ak, sk, x_date, body_sha,
    )
    assert got == expected
    assert got != "0" * 64

    # 分页体（PageNumber/PageSize）按实际 body 计算 X-Content-Sha256，签名随之变化
    paged_body = b'{"PageNumber":2,"PageSize":100}'
    paged_sha = hashlib.sha256(paged_body).hexdigest()
    paged_request = canonical_request.replace(body_sha, paged_sha)
    paged_sign = "\n".join([
        "HMAC-SHA256", x_date, "20261026/cn-beijing/ark/request",
        hashlib.sha256(paged_request.encode("utf-8")).hexdigest(),
    ])
    paged_expected = hmac.new(k_signing, paged_sign.encode("utf-8"), hashlib.sha256).hexdigest()
    paged_got = model_sync.volc_signature(
        "POST", "/", {"Action": "ListFoundationModels", "Version": "2024-01-01"},
        host, ak, sk, x_date, paged_sha,
    )
    assert paged_got == paged_expected
    assert paged_got != got  # 换 body 必须换签名


def test_volc_signature_query_is_sorted_and_encoded():
    """canonical query 按键排序并做 RFC3986 编码（官方签名要求；乱序会导致签名不符）。"""
    ak, sk = "AKTESTSYNTH001", "SK-SYNTHETIC-ONLY-9876"
    host = "ark.cn-beijing.volcengineapi.com"
    x_date = "20261026T120000Z"
    body_sha = hashlib.sha256(b"{}").hexdigest()
    ordered = model_sync.volc_signature("POST", "/", {"Action": "ListArkCodingPlanModel", "Version": "2024-01-01"}, host, ak, sk, x_date, body_sha)
    shuffled = model_sync.volc_signature("POST", "/", {"Version": "2024-01-01", "Action": "ListArkCodingPlanModel"}, host, ak, sk, x_date, body_sha)
    assert ordered == shuffled  # 排序归一：传参顺序不影响签名
    spacey = model_sync.volc_signature("POST", "/", {"Action": "List Foundation Models"}, host, ak, sk, x_date, body_sha)
    assert spacey != ordered


def test_opencode_unknown_api_family_not_synced_as_chat(client, monkeypatch):
    """OpenCode：未知/不兼容协议不同步（skipped 说明），只有 chat_completions 文本模型落库。"""
    pid = _mk_provider(preset_key="opencode", models_url="https://opencode.ai/inference/v1/models")
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(200, json=_OPENCODE_PAYLOAD)

    _mock_httpx(monkeypatch, handler)
    with SessionLocal() as db:
        result = model_sync.sync_provider_models(db, pid)
    assert seen == ["https://opencode.ai/inference/v1/models"]  # 只请求列表 URL，不用对话 base
    rows = {r.model for r in _rows(pid)}
    assert rows == {"kimi-k2.6"}
    with SessionLocal() as db:
        meta = model_catalog.capability_meta_of(_rows(pid)[0])
    assert meta["api_family"] == "chat_completions"
    reasons = {s["model"]: s["reason"] for s in result["skipped"]}
    assert "协议" in reasons["claude-sonnet-4-6"] and "未知" in reasons["gpt-5.4"]
    assert "claude-sonnet-4-6" not in rows


def test_tokenhub_preview_status_is_not_guessed(client, monkeypatch):
    """腾讯在线 status 逐字保留：preview 不臆改，能力仍由目录补齐。"""
    pid = _mk_provider(preset_key="tokenhub", models_url="https://tokenhub.tencentmaas.com/v1/models")
    payload = {"object": "list", "data": [{"id": "hy3", "object": "model", "created": 1, "owned_by": "tokenhub", "status": "preview"}]}
    _mock_httpx(monkeypatch, lambda request: httpx.Response(200, json=payload))
    with SessionLocal() as db:
        result = model_sync.sync_provider_models(db, pid)
    assert result["online"] is True
    row = _rows(pid)[0]
    assert _meta_of(row)["remote_status"] == "preview"
    assert row.context_size == 256000  # 能力来自目录（tokenhub/hy3）


def test_online_status_overrides_catalog_status(client, monkeypatch):
    """在线结果优先：远端状态覆盖目录状态（opencode 目录 deprecated + 在线 online）。"""
    pid = _mk_provider(preset_key="opencode", models_url="https://opencode.ai/inference/v1/models")
    payload = {"data": [{"id": "kimi-k2.5-free", "api_family": "chat_completions", "status": "online"}]}
    _mock_httpx(monkeypatch, lambda request: httpx.Response(200, json=payload))
    with SessionLocal() as db:
        model_sync.sync_provider_models(db, pid)
    assert _meta_of(_rows(pid)[0])["remote_status"] == "online"


def test_catalog_status_fills_gap_when_remote_silent(client, monkeypatch):
    """远端未给状态时目录状态补齐（opencode 目录标注 deprecated，include_disabled 才同步）。"""
    pid = _mk_provider(preset_key="opencode", models_url="https://opencode.ai/inference/v1/models")
    payload = {"data": [{"id": "kimi-k2.5-free", "api_family": "chat_completions"}]}
    _mock_httpx(monkeypatch, lambda request: httpx.Response(200, json=payload))
    with SessionLocal() as db:
        result = model_sync.sync_provider_models(db, pid)
    assert _rows(pid) == []  # deprecated 默认不同步
    assert "deprecated" in result["skipped"][0]["reason"]
    with SessionLocal() as db:
        model_sync.sync_provider_models(db, pid, include_disabled=True)
    assert _meta_of(_rows(pid)[0])["remote_status"] == "deprecated"


# ---------- fix round 1：空响应诚实失败 / 官方分页 / pre-offline / DB 异常 ----------

def test_fetch_empty_online_list_raises(client, monkeypatch):
    """200 + 空列表：抛 RemoteFetchError（绝不变成 added=0 的假成功）。"""
    pid = _mk_provider(models_url="https://api.deepseek.com/models")
    _mock_httpx(monkeypatch, lambda request: httpx.Response(200, json={"object": "list", "data": []}))
    with pytest.raises(RemoteFetchError) as exc:
        model_sync.fetch_remote_models(_provider_row(pid), {"api_key": _SYNTH_KEY})
    assert "空" in str(exc.value)


def test_fetch_unrecognizable_structure_raises(client, monkeypatch):
    """200 + 不可识别结构：抛 RemoteFetchError（结构不可识别，不假成功）。"""
    pid = _mk_provider(models_url="https://api.deepseek.com/models")
    _mock_httpx(monkeypatch, lambda request: httpx.Response(200, json={"unexpected": {"shape": True}}))
    with pytest.raises(RemoteFetchError) as exc:
        model_sync.fetch_remote_models(_provider_row(pid), {"api_key": _SYNTH_KEY})
    assert "结构" in str(exc.value)


def test_sync_empty_online_list_falls_back_to_catalog_honestly(client, monkeypatch):
    """空列表同步：online=false、partial=true、errors 明确、目录兜底真实生效。"""
    pid = _mk_provider(models_url="https://api.deepseek.com/models")
    _mock_httpx(monkeypatch, lambda request: httpx.Response(200, json={"object": "list", "data": []}))
    with SessionLocal() as db:
        result = model_sync.sync_provider_models(db, pid)
    assert result["online"] is False and result["partial"] is True
    assert result["source"] == "official_catalog"
    assert result["errors"] and result["added"] >= 1
    assert {r.model for r in _rows(pid)} == {"deepseek-flash", "deepseek-v4-pro"}


def test_fetch_alibaba_paginates_by_page_no_and_total(client, monkeypatch):
    """阿里官方分页：page_no 递增直到取满 output.total；请求带 page_no/page_size。"""
    pid = _mk_provider(preset_key="dashscope", models_url="https://dashscope.aliyuncs.com/api/v1/models")
    urls = []

    def handler(request):
        urls.append(str(request.url))
        if "page_no=2" in str(request.url):
            return httpx.Response(200, json=_ALIBABA_PAGE2)
        return httpx.Response(200, json=_ALIBABA_PAGE1)

    _mock_httpx(monkeypatch, handler)
    models, warnings = model_sync.fetch_remote_models(_provider_row(pid), {"api_key": _SYNTH_KEY})
    assert len(urls) == 2
    assert "page_no=1" in urls[0] and "page_size=100" in urls[0]
    assert "page_no=2" in urls[1]
    assert [m.model for m in models] == ["qwen3.8-max", "qwen-turbo", "qwen-image-3.0-pro"]
    assert models[-1].status == "offline"
    assert warnings == []


def test_fetch_anthropic_paginates_with_after_id(client, monkeypatch):
    """Anthropic 官方分页：has_more + after_id=last_id，有界（页数上限）。"""
    pid = _mk_provider(preset_key="anthropic", models_url="https://api.anthropic.com/v1/models")
    urls = []

    def handler(request):
        urls.append(str(request.url))
        if "after_id=" in str(request.url):
            return httpx.Response(200, json={
                "data": [{"type": "model", "id": "claude-haiku-4-5", "display_name": "Claude Haiku 4.5",
                          "created_at": "2024-11-01T00:00:00Z", "lifecycle": "active"}],
                "has_more": False,
            })
        return httpx.Response(200, json={**_ANTHROPIC_PAYLOAD, "has_more": True, "last_id": "claude-3-opus-20240229"})

    _mock_httpx(monkeypatch, handler)
    models, _ = model_sync.fetch_remote_models(_provider_row(pid), {"api_key": _SYNTH_KEY})
    assert [m.model for m in models] == ["claude-sonnet-4-6", "claude-3-opus-20240229", "claude-haiku-4-5"]
    assert len(urls) == 2 and "after_id=claude-3-opus-20240229" in urls[1]


def test_openrouter_relative_next_link_is_joined_before_origin_check(client, monkeypatch):
    """OpenRouter 分页：相对路径先 urljoin 再校验同源（协议相对 //host 同样按跨域拒绝）。"""
    pid = _mk_provider(preset_key="openrouter", models_url="https://openrouter.ai/api/v1/models")
    calls = []

    def relative(request):
        calls.append(str(request.url))
        if "page=2" in str(request.url):
            return httpx.Response(200, json=_OPENROUTER_PAGE2)
        return httpx.Response(200, json={**_OPENROUTER_PAGE1, "links": {"next": "/api/v1/models?page=2"}})

    _mock_httpx(monkeypatch, relative)
    models, warnings = model_sync.fetch_remote_models(_provider_row(pid), {"api_key": _SYNTH_KEY})
    assert len(calls) == 2 and calls[1].startswith("https://openrouter.ai/api/v1/models?page=2")
    assert len(models) == 3 and warnings == []

    pid2 = _mk_provider(preset_key="openrouter", models_url="https://openrouter.ai/api/v1/models", name="协议相对")
    cross_calls = []

    def protocol_relative(request):
        cross_calls.append(str(request.url))
        return httpx.Response(200, json={**_OPENROUTER_PAGE1, "links": {"next": "//evil.example.com/api/v1/models?page=2"}})

    _mock_httpx(monkeypatch, protocol_relative)
    models2, warnings2 = model_sync.fetch_remote_models(_provider_row(pid2), {"api_key": _SYNTH_KEY})
    assert len(cross_calls) == 1  # urljoin 后跨域，未被请求
    assert len(models2) == 2 and any("同源" in w for w in warnings2)


def test_sync_skips_tencent_pre_offline_models(client, monkeypatch):
    """腾讯 pre-offline/preoffline 变体同样纳入下线过滤（不同步、skipped 说明）。"""
    pid = _mk_provider(preset_key="tokenhub", models_url="https://tokenhub.tencentmaas.com/v1/models")
    payload = {
        "object": "list",
        "data": [
            {"id": "hy3", "object": "model", "created": 1, "owned_by": "tokenhub", "status": "pre-offline"},
            {"id": "hy4-preview", "object": "model", "created": 1, "owned_by": "tokenhub", "status": "online"},
        ],
    }
    _mock_httpx(monkeypatch, lambda request: httpx.Response(200, json=payload))
    with SessionLocal() as db:
        result = model_sync.sync_provider_models(db, pid)
    assert {r.model for r in _rows(pid)} == {"hy4-preview"}
    reasons = {s["model"]: s["reason"] for s in result["skipped"]}
    assert "pre-offline" in reasons["hy3"]


def test_sync_write_failure_rolls_back_without_500(client, monkeypatch):
    """写入异常（DB 错误/意外 bug）：整批回滚 + 结构化 ok=false，端点不裸 500。"""
    pid = _mk_provider(models_url="https://api.deepseek.com/models")
    payload = {"object": "list", "data": [{"id": "deepseek-chat", "object": "model", "created": 1, "owned_by": "deepseek"}]}
    _mock_httpx(monkeypatch, lambda request: httpx.Response(200, json=payload))

    def boom(pm, entry, **kwargs):
        raise RuntimeError("disk I/O error")

    monkeypatch.setattr(model_catalog, "apply_capability_entry", boom)
    with SessionLocal() as db:
        result = model_sync.sync_provider_models(db, pid)
    assert result["ok"] is False
    assert result["errors"] and "回滚" in result["errors"][0]
    assert result["online"] is True and result["added"] == 0 and "partial" in result
    assert _rows(pid) == []  # 半批不得落库

    resp = client.post(f"/api/assistant/providers/{pid}/sync-models", json={})
    assert resp.status_code == 200  # 结构化失败，不裸 500
    body = resp.json()
    assert body["ok"] is False and body["errors"]
    assert _rows(pid) == []


# ---------- 端点层 ----------

def test_sync_endpoint_happy_path_returns_providers(client, monkeypatch):
    """端点：200 + 统计 + 最新 providers；响应不含任何密钥/管控凭证。"""
    pid = client.post(
        "/api/assistant/providers",
        json={"name": "同步供应商", "base_url": "https://api.deepseek.com/v1", "api_key": _SYNTH_KEY,
              "preset_key": "deepseek", "models_url": "https://api.deepseek.com/models"},
    ).json()["provider"]["id"]
    payload = {"object": "list", "data": [{"id": "deepseek-chat", "object": "model", "created": 1, "owned_by": "deepseek"}]}
    _mock_httpx(monkeypatch, lambda request: httpx.Response(200, json=payload))
    resp = client.post(f"/api/assistant/providers/{pid}/sync-models", json={})
    assert resp.status_code == 200
    body = resp.json()
    for key in ("ok", "partial", "online", "source", "added", "updated", "unchanged", "skipped", "warnings", "errors", "synced_at", "providers"):
        assert key in body
    assert body["online"] is True and body["added"] == 1
    assert any(p["id"] == pid and any(m["model"] == "deepseek-chat" for m in p["models"]) for p in body["providers"])
    text = json.dumps(body, ensure_ascii=False)
    assert _SYNTH_KEY not in text and _SYNTH_AK not in text and _SYNTH_SK not in text and "access_key" not in text


def test_sync_endpoint_unknown_provider_returns_404(client):
    resp = client.post("/api/assistant/providers/999999/sync-models", json={})
    assert resp.status_code == 404


def test_sync_endpoint_busy_provider_returns_409(client, monkeypatch):
    """端点：持有同步锁时再次同步 → 409（重复点击不产生重复行）。"""
    pid = client.post(
        "/api/assistant/providers",
        json={"name": "同步供应商", "base_url": "https://api.deepseek.com/v1", "api_key": _SYNTH_KEY,
              "preset_key": "deepseek", "models_url": "https://api.deepseek.com/models"},
    ).json()["provider"]["id"]
    lock = model_sync._lock_for(pid)
    assert lock.acquire(blocking=False)
    try:
        resp = client.post(f"/api/assistant/providers/{pid}/sync-models", json={})
    finally:
        lock.release()
    assert resp.status_code == 409
    assert resp.json()["detail"]


# ---------- Task 3 评审遗留：catalog_key 的 None 与显式空串 ----------

def test_catalog_key_distinguishes_none_and_explicit_empty(monkeypatch):
    """catalog_key 缺字段才回退 preset_key；显式空串 = 无目录（catalog_for 返回 None）。"""
    monkeypatch.setattr(config_store, "load_presets", lambda: [{"preset_key": "deepseek"}])
    assert model_catalog._catalog_key_for("deepseek") == "deepseek"
    monkeypatch.setattr(config_store, "load_presets", lambda: [{"preset_key": "deepseek", "catalog_key": ""}])
    assert model_catalog._catalog_key_for("deepseek") == ""
    assert model_catalog.catalog_for("deepseek", "deepseek-flash") is None
    monkeypatch.setattr(config_store, "load_presets", lambda: [{"preset_key": "deepseek", "catalog_key": "custom"}])
    assert model_catalog._catalog_key_for("deepseek") == "custom"
    monkeypatch.setattr(config_store, "load_presets", lambda: [{"preset_key": "deepseek", "catalog_key": None}])
    assert model_catalog._catalog_key_for("deepseek") == "deepseek"
