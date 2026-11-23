"""Agent 团队运行时可配置存储：模型供应商 → 模型 两级管理（参考 ZCode 配置方式）。

本文件是**对外 facade**：预设加载、连通性测试与其余配置符号的 re-export。
调用方（routers / scheduler / llm / model_sync / 测试）继续只从 config_store 取符号。

- `provider_presets.json`：供应商预设（界面选择后自动填充 base_url、候选模型与视觉模型
  清单），由 `load_presets()` 加载，缺失/损坏时回退内置默认集；
- 供应商 / 模型 CRUD、能力视图与实测结论在 `config_providers.py`；
- 全局工作参数与 `load()` / `model_cfg_by_id()` 组装在 `config_settings.py`；
- 密钥掩码、地址安全校验与管控凭证读取在 `config_secrets.py`。

为何预设与连通性测试留在这里：模块级 `PRESETS` 会被 `reload_presets()` 重绑、
被测试按模块属性备份/恢复；`_probe_urlopen` 是探测请求的测试注入点。两者都必须
与本模块同命名空间，搬到子模块会让重绑/注入静默失效。

连通性/多模态测试与协议无关（经 build_model 分流）。
"""
import asyncio
import base64
import json
import logging
import re
import struct
import time
import urllib.error
import urllib.request
import zlib
from pathlib import Path

from .config_providers import (  # noqa: F401 —— 供应商/模型 CRUD re-export
    _PROBE_VERDICT_FIELDS,
    _active_model,
    _json_list,
    _model_capability_view,
    _parse_temperature,
    _row,
    activate_model,
    delete_provider,
    delete_provider_model,
    list_providers,
    models_flat,
    provider_test_cfg,
    record_probe_verdict,
    reset_provider_model_overrides,
    resolve_test_model_id,
    save_provider,
    save_provider_model,
    sync_provider_models,
)
from .config_secrets import (  # noqa: F401 —— 密钥/掩码/地址校验 re-export
    MASK,
    MASK_PREFIX,
    _check_http_url,
    _read_sync_credentials,
    _safe_gateway_url,
    masked,
    validate_base_url,
)
from .config_settings import (  # noqa: F401 —— 全局设置 re-export
    DEFAULTS,
    MODEL_FIELDS,
    _REASONING_EFFORT_VALUES,
    current_api_key,
    load,
    model_cfg_by_id,
    save_globals,
)

logger = logging.getLogger(__name__)

# 供应商预设：数据文件 provider_presets.json（界面选择后自动填充 base_url 与候选模型）。
# 协议仅 openai / anthropic 两种；anthropic 协议经 agentscope 模型层分流。
# 文件缺失/解析失败时回退内置最小默认集（_FALLBACK_PRESETS），保证应用始终可用。
PRESETS_PATH = Path(__file__).resolve().parent / "provider_presets.json"

# 同步适配器白名单（Task 4 在线列表同步按此分派；none = 无官方列表，仅目录兜底）
SYNC_ADAPTERS = (
    "openai_models",
    "opencode_models",
    "anthropic_models",
    "openrouter_models",
    "deepseek_models",
    "alibaba_models",
    "tencent_models",
    "volcengine_ak_sk",
    "none",
)
# 套餐边界：general 按量 / plan_api_allowed 明确允许第三方 API / plan_interactive_only 限交互式工具（禁定时任务）
USAGE_SCOPES = ("general", "plan_api_allowed", "plan_interactive_only")
# 鉴权形态：bearer（OpenAI 兼容）/ x-api-key（Anthropic Messages）/ ak_sk（火山管控面 AK+SK）
AUTH_SCHEMES = ("bearer", "x-api-key", "ak_sk")

# JSON 缺失/损坏时的内置最小默认集（仅保留最常用的几家，避免界面无预设可选）
_FALLBACK_PRESETS: list[dict] = [
    {
        "name": "deepseek",
        "preset_key": "deepseek",
        "label": "DeepSeek 官方",
        "protocol": "openai",
        "base_url": "https://api.deepseek.com/v1",
        "sync_adapter": "openai_models",
        "models_url": "https://api.deepseek.com/models",
        "catalog_key": "deepseek",
        "usage_scope": "general",
        "auth_scheme": "bearer",
        "models": ["deepseek-flash", "deepseek-v4-pro"],
        "note": "官方直连",
        "docs_url": "https://platform.deepseek.com/api-docs/",
        "reasoning_models": [],
        "vision_models": [],
    },
    {
        "name": "ollama",
        "preset_key": "ollama",
        "label": "本地 Ollama",
        "protocol": "openai",
        "base_url": "http://localhost:11434/v1",
        "sync_adapter": "openai_models",
        "models_url": "http://localhost:11434/v1/models",
        "catalog_key": "",
        "usage_scope": "general",
        "auth_scheme": "bearer",
        "models": ["qwen2.5:7b", "llama3.1:8b"],
        "note": "完全本地、免费；需先安装 Ollama 并拉取模型",
        "docs_url": "https://ollama.com/",
        "reasoning_models": [],
        "vision_models": [],
    },
    {
        "name": "custom",
        "preset_key": "custom",
        "label": "自定义",
        "protocol": "openai",
        "base_url": "",
        "sync_adapter": "none",
        "models_url": "",
        "catalog_key": "",
        "usage_scope": "general",
        "auth_scheme": "bearer",
        "models": [],
        "note": "任意 OpenAI 兼容服务；anthropic 协议请用协议下拉选择",
        "docs_url": "",
        "reasoning_models": [],
        "vision_models": [],
    },
]


def _normalize_preset(item) -> dict | None:
    """校验并归一化单条预设；结构不合法（缺 name/label、协议不在白名单）返回 None。"""
    if not isinstance(item, dict):
        return None
    name = str(item.get("name") or "").strip()[:50]
    label = str(item.get("label") or "").strip()[:100]
    protocol = str(item.get("protocol") or "openai").strip().lower()
    if not name or not label or protocol not in ("openai", "anthropic"):
        return None
    models = item.get("models") if isinstance(item.get("models"), list) else []
    reasoning = item.get("reasoning_models") if isinstance(item.get("reasoning_models"), list) else []
    vision = item.get("vision_models") if isinstance(item.get("vision_models"), list) else []
    preset_key = str(item.get("preset_key") or name).strip()[:50]
    sync_adapter = str(item.get("sync_adapter") or "none").strip().lower()
    if sync_adapter not in SYNC_ADAPTERS:
        sync_adapter = "none"
    # 列表地址必须是 http(s)：协议非法按「无在线列表」处理（界面只显示目录兜底）
    models_url = str(item.get("models_url") or "").strip().rstrip("/")[:300]
    if models_url and not models_url.startswith(("http://", "https://")):
        models_url = ""
    # catalog_key 显式留空 = 无目录（仅在线/手动）；缺字段才回退 preset_key
    raw_catalog_key = item.get("catalog_key")
    catalog_key = str(preset_key if raw_catalog_key is None else raw_catalog_key).strip()[:50]
    usage_scope = str(item.get("usage_scope") or "general").strip().lower()
    if usage_scope not in USAGE_SCOPES:
        usage_scope = "general"
    auth_scheme = str(item.get("auth_scheme") or "bearer").strip().lower()
    if auth_scheme not in AUTH_SCHEMES:
        auth_scheme = "bearer"
    # 官方要求会话头的套餐（OpenCode Go）：会话头名 + 自定义 UA；非法头名/含控制字符的 UA 一律忽略
    session_header = str(item.get("session_header") or "").strip()[:64]
    if not session_header or not all(ch.isascii() and (ch.isalnum() or ch == "-") for ch in session_header):
        session_header = ""
    user_agent = str(item.get("user_agent") or "").strip()[:200]
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in user_agent):
        user_agent = ""
    return {
        "name": name,
        "preset_key": preset_key,
        "label": label,
        "protocol": protocol,
        "base_url": str(item.get("base_url") or "").strip().rstrip("/")[:300],
        "sync_adapter": sync_adapter,
        "models_url": models_url,
        "catalog_key": catalog_key,
        "usage_scope": usage_scope,
        "auth_scheme": auth_scheme,
        "session_header": session_header,
        "user_agent": user_agent,
        "models": [str(m).strip()[:120] for m in models if str(m).strip()][:30],
        "note": str(item.get("note") or "").strip()[:200],
        "docs_url": str(item.get("docs_url") or "").strip()[:300],
        "reasoning_models": [str(m).strip()[:120] for m in reasoning if str(m).strip()][:30],
        # 已知支持图片理解的视觉模型（界面据此预勾选模型的 supports_vision）
        "vision_models": [str(m).strip()[:120] for m in vision if str(m).strip()][:30],
    }


def load_presets() -> list[dict]:
    """从 provider_presets.json 加载供应商预设。

    文件缺失或解析失败 → 回退内置最小默认集并记警告日志；
    顶层可以是 list，也可以是 {"presets": [...]}；按 name 去重，跳过非法条目；
    全部条目非法时同样回退默认集。
    """
    try:
        raw = json.loads(PRESETS_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        logger.warning("供应商预设文件缺失：%s，使用内置默认集", PRESETS_PATH)
        return [dict(p) for p in _FALLBACK_PRESETS]
    except Exception:
        logger.warning("供应商预设文件解析失败：%s，使用内置默认集", PRESETS_PATH, exc_info=True)
        return [dict(p) for p in _FALLBACK_PRESETS]

    items = raw.get("presets") if isinstance(raw, dict) else raw
    if not isinstance(items, list):
        logger.warning("供应商预设文件结构非法（应为 list 或含 presets 键的对象），使用内置默认集")
        return [dict(p) for p in _FALLBACK_PRESETS]

    presets: list[dict] = []
    seen: set[str] = set()
    for item in items:
        norm = _normalize_preset(item)
        if norm is None or norm["name"] in seen:
            continue
        seen.add(norm["name"])
        presets.append(norm)
    if not presets:
        logger.warning("供应商预设文件无有效条目，使用内置默认集")
        return [dict(p) for p in _FALLBACK_PRESETS]
    return presets


def reload_presets() -> list[dict]:
    """重新加载预设 JSON 并刷新模块级 PRESETS（预设管理界面「重载」用）。"""
    global PRESETS
    PRESETS = load_presets()
    return PRESETS


def preset_by_key(preset_key: str) -> dict | None:
    """按 preset_key 取预设（适配器只认 preset_key，不认中文显示名）。"""
    for p in PRESETS:
        if p.get("preset_key") == preset_key:
            return p
    return None


PRESETS: list[dict] = load_presets()

# 连通性/多模态测试的显式客户端超时（秒）：SDK 默认 600s、聊天路径 120s，
# 测试路径收紧到 30s——「挂起服务器」应快速失败并给出中文提示，而不是长时间占住请求。
TEST_TIMEOUT_SECONDS = 30.0


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """禁止跟随重定向：urllib 默认会把 Authorization/x-api-key 复制到新主机。

    BUGfix AGT-05：测试请求由用户填写的 base_url 发起，一旦对方 301/302 到
    任意主机，用户的真实 API Key 就会被送到该主机。3xx 交给 HTTPError 分支
    按失败处理（不跟随、不重发）。
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401
        return None


_NO_REDIRECT_OPENER = urllib.request.build_opener(_NoRedirectHandler)


def _probe_urlopen(req, timeout=None):
    """探测请求统一出口：生产不跟随重定向；测试可 patch 本函数（而非 urlopen）。"""
    return _NO_REDIRECT_OPENER.open(req, timeout=timeout)

# LLM 调用常见失败的中文提示映射（连通性/多模态/工具测试共用）：
# 只做类型与状态码识别，不回传上游原文与堆栈，避免把内部细节暴露给界面。
_FRIENDLY_ERROR_RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("AuthenticationError", "PermissionDeniedError"), "API Key 无效、已过期或没有权限（鉴权失败），请检查 Key 与账号状态"),
    (("RateLimitError",), "请求过于频繁或被限流（429），请稍后重试或降低调用频率"),
    (("APITimeoutError", "TimeoutError", "ReadTimeout"), "请求超时，请检查网络连接或稍后重试"),
    (("APIConnectionError", "ConnectionError", "ConnectError"), "无法连接到服务器，请检查 Base URL 与网络连接"),
    (("InternalServerError",), "服务器内部错误（5xx），请稍后重试"),
    (("BadRequestError",), "请求被服务器拒绝（400），请检查模型名与参数是否正确"),
)


def _friendly_error(exc: BaseException) -> str:
    """把 LLM 调用异常映射为中文提示（供测试路径使用，不回传堆栈与上游原文）。"""
    name = type(exc).__name__
    text = str(exc)
    # 优先按异常类型名匹配；其次按消息里的 HTTP 状态码兜底
    for names, message in _FRIENDLY_ERROR_RULES:
        if name in names:
            return message
    for code, hint in (
        ("401", "API Key 无效、已过期或没有权限（鉴权失败），请检查 Key 与账号状态"),
        ("403", "没有访问权限（403），请检查 Key 与账号状态"),
        ("429", "请求过于频繁或被限流（429），请稍后重试或降低调用频率"),
        ("400", "请求被服务器拒绝（400），请检查模型名与参数是否正确"),
        ("500", "服务器内部错误（5xx），请稍后重试"),
        ("502", "网关错误（502），请稍后重试"),
        ("503", "服务暂时不可用（503），请稍后重试"),
        ("504", "网关超时（504），请稍后重试"),
    ):
        if code in text:
            return hint
    if "timed out" in text.lower() or "timeout" in text.lower():
        return "请求超时，请检查网络连接或稍后重试"
    if "connection" in text.lower():
        return "无法连接到服务器，请检查 Base URL 与网络连接"
    return f"测试失败（{name}），请检查配置或稍后重试"


def _red_png_data_uri(size: int = 48) -> str:
    """生成一张纯红色 PNG 的 data URI（用于多模态能力测试）。"""
    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)  # 8bit RGB
    raw = b"".join(b"\x00" + b"\xef\x3d\x2f" * size for _ in range(size))  # 红色 #EF3D2F
    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")
    return "data:image/png;base64," + base64.b64encode(png).decode()


async def _consume_model_call(model, messages, tools=None):
    """统一消费模型调用结果：兼容 ChatResponse 与流式 async_generator。

    返回 (全部文本, tool_call 名称列表, usage)。
    """
    if tools:
        resp = await model(messages, tools=tools)
    else:
        resp = await model(messages)

    blocks, usage = [], None
    if hasattr(resp, "__aiter__"):  # 流式：逐 chunk 累积
        async for chunk in resp:
            blocks.extend(chunk.get("content") or [])
            if chunk.get("usage") is not None:
                usage = chunk.get("usage")
    else:
        blocks = resp.get("content") or []
        usage = resp.get("usage")

    texts, tool_calls = [], []
    for b in blocks:
        t = getattr(b, "type", None)
        if t == "text":
            texts.append(getattr(b, "text", ""))
        elif t == "tool_call":
            tool_calls.append(getattr(b, "name", "") or getattr(b, "tool_call_name", ""))
    return "".join(texts), tool_calls, usage


def _extract_reply(raw: str) -> str:
    """从模型原始输出里提取正式回答（剥离思考过程）。"""
    raw = raw.strip()
    for marker in ("Final Output:", "</think>"):
        idx = raw.rfind(marker)
        if idx >= 0:
            raw = raw[idx + len(marker):].strip()
            break
    return raw[:120] or "(空回复)"


async def test_connection(cfg: dict) -> dict:
    """文本连通性测试：构造模型发一条消息，返回延迟与回复（协议无关、兼容流式）。"""
    from ..agents.llm import build_model

    if not (cfg.get("api_key") and cfg.get("base_url") and cfg.get("model")):
        return {"ok": False, "error": "请先填写 Base URL / API Key / 模型名"}
    # 测试路径显式收紧超时（默认 120s → 30s）：挂起服务器应快速失败并给出中文提示
    model = build_model({**cfg, "client_timeout": TEST_TIMEOUT_SECONDS, "client_max_retries": 0})
    if model is None:
        return {"ok": False, "error": "配置不完整，无法构造模型"}
    from agentscope.message import Msg, TextBlock

    started = time.time()
    try:
        text, _, usage = await _consume_model_call(
            model, [Msg(name="user", role="user", content=[TextBlock(type="text", text="请只回复：连接成功")])]
        )
        latency = round((time.time() - started) * 1000)
        if not text.strip():
            return {"ok": False, "latency_ms": latency, "error": "模型返回了空回复，请检查模型名是否正确"}
        return {
            "ok": True,
            "latency_ms": latency,
            "reply": _extract_reply(text),
            "usage": {
                "input_tokens": getattr(usage, "input_tokens", None),
                "output_tokens": getattr(usage, "output_tokens", None),
            },
        }
    except Exception as exc:
        logger.info("连通性测试失败：%s", type(exc).__name__)
        return {"ok": False, "latency_ms": round((time.time() - started) * 1000), "error": _friendly_error(exc)}


# 工具调用测试用的极简工具（OpenAI function 格式；AgentScope 两种协议的模型层都接受该格式并自行转换）
_TEST_TOOL_SPEC = {
    "name": "get_current_time",
    "description": "获取当前的日期和时间",
    "parameters": {
        "type": "object",
        "properties": {},
        "required": [],
    },
}


async def test_tool_calling(cfg: dict) -> dict:
    """工具调用能力测试：给模型一个测试工具，验证它是否会发起工具调用。"""
    from ..agents.llm import build_model

    if not (cfg.get("api_key") and cfg.get("base_url") and cfg.get("model")):
        return {"ok": False, "error": "请先填写 Base URL / API Key / 模型名"}
    # 与文本连通性测试同一套 30s 显式超时（见 TEST_TIMEOUT_SECONDS）
    model = build_model({**cfg, "client_timeout": TEST_TIMEOUT_SECONDS, "client_max_retries": 0})
    if model is None:
        return {"ok": False, "error": "配置不完整，无法构造模型"}

    tools = [{"type": "function", "function": _TEST_TOOL_SPEC}]

    from agentscope.message import Msg, TextBlock

    started = time.time()
    try:
        text, tool_calls, _ = await _consume_model_call(
            model,
            [Msg(name="user", role="user", content=[TextBlock(type="text", text="请调用 get_current_time 工具查询当前时间")])],
            tools=tools,
        )
        latency = round((time.time() - started) * 1000)
        ok = any("get_current_time" in (tc or "") for tc in tool_calls)
        # 三态判定：明确调用成功=supported；模型正常回复但未调用=unknown（不是明确的参数拒绝）
        return {
            "ok": ok,
            "latency_ms": latency,
            "tool_calls": [tc for tc in tool_calls if tc],
            "reply": _extract_reply(text),
            "error": None if ok else "模型未发起工具调用",
            "verdict": "supported" if ok else "unknown",
        }
    except Exception as exc:
        logger.info("工具调用测试失败：%s", type(exc).__name__)
        verdict = "rejected" if _is_param_rejection(exc) else "unknown"
        return {"ok": False, "latency_ms": round((time.time() - started) * 1000), "error": _friendly_error(exc), "verdict": verdict}


def _is_param_rejection(exc: BaseException) -> bool:
    """是否「明确的参数拒绝」（仅此判 rejected，避免把鉴权/限流/服务端错误误写成不支持）。

    判定优先级：SDK 异常类型（BadRequestError）> status_code 属性（400/422）>
    消息文本中明确的 ``Error code: 400`` / ``Error code: 422``。
    消息里偶然出现 400（端口号、请求 ID 等）不视为参数拒绝。
    """
    if type(exc).__name__ == "BadRequestError":
        return True
    code = getattr(exc, "status_code", None)
    if code in (400, 422):
        return True
    return bool(re.search(r"Error code:\s*(400|422)\b", str(exc)))


# 图片实测中只有这两个状态码是「明确的参数/能力拒绝」；401/403/404/408/429/5xx 一律 unknown
_IMAGE_REJECTED_CODES = (400, 422)


def _http_anthropic_multimodal_test(cfg: dict) -> dict:
    """anthropic 协议图片识别测试：/v1/messages + base64 图片块。"""
    started = time.time()
    b64 = _red_png_data_uri().split(",", 1)[1]
    base = cfg["base_url"].rstrip("/")
    if base.lower().endswith("/v1"):  # SDK 同款问题：避免 /v1/v1/messages
        base = base[: -len("/v1")]
    payload = {
        "model": cfg["model"],
        "max_tokens": 32,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "这张图片是什么颜色？请只回答颜色名称（如：红色）。"},
                    {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64}},
                ],
            }
        ],
    }
    req = urllib.request.Request(
        _safe_gateway_url(base, "/v1/messages"),
        data=json.dumps(payload).encode(),
        headers={
            "x-api-key": cfg["api_key"],
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        },
    )
    try:
        resp = json.load(_probe_urlopen(req, timeout=TEST_TIMEOUT_SECONDS))
        latency = round((time.time() - started) * 1000)
        content = "".join(b.get("text", "") for b in resp.get("content", []) if isinstance(b, dict))
        ok = ("红" in content) or ("red" in content.lower())
        return {"ok": ok, "latency_ms": latency, "reply": content.strip()[:120] or "(空回复)", "supported": True, "verdict": "supported" if ok else "unknown"}
    except urllib.error.HTTPError as exc:
        latency = round((time.time() - started) * 1000)
        # 仅 400/422 是明确的参数/能力拒绝；401/403/404/408/429/5xx 结论保持未知
        verdict = "rejected" if exc.code in _IMAGE_REJECTED_CODES else "unknown"
        return {"ok": False, "latency_ms": latency, "supported": False, "error": _friendly_error(exc), "verdict": verdict}
    except Exception as exc:
        latency = round((time.time() - started) * 1000)
        return {"ok": False, "latency_ms": latency, "supported": False, "error": _friendly_error(exc), "verdict": "unknown"}


async def test_multimodal_anthropic(cfg: dict) -> dict:
    """anthropic 协议图片识别测试（异步包装同步 HTTP）。"""
    return await asyncio.to_thread(_http_anthropic_multimodal_test, cfg)


def _http_multimodal_test(cfg: dict) -> dict:
    """直接以 OpenAI 兼容协议发送「文字 + 红色图片」，让模型回答颜色（仅 openai 协议）。"""
    started = time.time()
    payload = {
        "model": cfg["model"],
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "这张图片是什么颜色？请只回答颜色名称（如：红色）。"},
                    {"type": "image_url", "image_url": {"url": _red_png_data_uri()}},
                ],
            }
        ],
        "max_tokens": 32,
    }
    req = urllib.request.Request(
        _safe_gateway_url(cfg["base_url"], "/chat/completions"),
        data=json.dumps(payload).encode(),
        headers={"Authorization": "Bearer " + cfg["api_key"], "Content-Type": "application/json"},
    )
    try:
        resp = json.load(_probe_urlopen(req, timeout=TEST_TIMEOUT_SECONDS))
        latency = round((time.time() - started) * 1000)
        content = (resp["choices"][0]["message"].get("content") or "")
        ok = ("红" in content) or ("red" in content.lower())
        return {"ok": ok, "latency_ms": latency, "reply": content.strip()[:120] or "(空回复)", "supported": True, "verdict": "supported" if ok else "unknown"}
    except urllib.error.HTTPError as exc:
        latency = round((time.time() - started) * 1000)
        # 仅 400/422 是明确的参数/能力拒绝；401/403/404/408/429/5xx 结论保持未知
        verdict = "rejected" if exc.code in _IMAGE_REJECTED_CODES else "unknown"
        return {"ok": False, "latency_ms": latency, "supported": False, "error": _friendly_error(exc), "verdict": verdict}
    except Exception as exc:
        latency = round((time.time() - started) * 1000)
        return {"ok": False, "latency_ms": latency, "supported": False, "error": _friendly_error(exc), "verdict": "unknown"}


async def test_multimodal(cfg: dict) -> dict:
    """多模态能力测试（异步包装同步 HTTP）。"""
    return await asyncio.to_thread(_http_multimodal_test, cfg)
