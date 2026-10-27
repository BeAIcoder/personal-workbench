"""Agent 团队运行时可配置存储：模型供应商 → 模型 两级管理（参考 ZCode 配置方式）。

- `model_providers`：一家供应商一张卡片（名称/协议/base_url/api_key/启停）；
- `provider_models`：供应商下挂多个模型（上下文/输出上限/多模态标记/能力探测结果/启停）；
- `agent_settings.active_model_id`：当前激活的模型，跨供应商选择；
- `provider_presets.json`：供应商预设（界面选择后自动填充 base_url、候选模型与视觉模型清单），
  由 `load_presets()` 加载，缺失/损坏时回退内置默认集；
- `.env` 的 LLM_* 仅在没有任何激活模型时兜底。

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
import urllib.parse
import urllib.request
import zlib
from datetime import datetime
from pathlib import Path

from sqlalchemy.exc import OperationalError

from ..config import settings
from ..database import SessionLocal
from ..models import AgentSettings, ModelProvider, ProviderModel
# seal/unseal 在 DPAPI 不可用时 fail-fast 抛 RuntimeError（配合启动校验，N-1），绝不降级明文
from .secrets_box import seal, unseal
from . import model_catalog

# 前端回显密钥时使用的掩码哨兵：界面"未改动"传回该值 → 保留现值
MASK = "********"
# GET 回显的掩码形态：MASK_PREFIX + 原密钥后四位（见 masked()）；
# 保存侧对该形态同样视为「未修改保留原值」，防止回显掩码被当成新密钥覆盖真实密钥
MASK_PREFIX = "••••••"

logger = logging.getLogger(__name__)

# 连通性/多模态测试的显式客户端超时（秒）：SDK 默认 600s、聊天路径 120s，
# 测试路径收紧到 30s——「挂起服务器」应快速失败并给出中文提示，而不是长时间占住请求。
TEST_TIMEOUT_SECONDS = 30.0

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


def _check_http_url(url: str) -> str:
    """http(s) 地址安全校验（SDK 构造 / 网关探测 / httpx 测试共用口径）。

    本应用为本机单用户场景，允许 127.0.0.1/内网地址（如 Ollama 本地网关），
    因此只做协议白名单与字符合法性校验，不封禁私网段。
    错误信息不回显 URL 主体：地址可能内嵌 userinfo 等敏感片段，不进日志/响应。
    """
    if any(ord(ch) < 0x21 or ch == "\x7f" for ch in url):
        raise ValueError("网关地址含非法字符")
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError("网关地址仅允许 http(s)")
    if parts.username or parts.password:
        raise ValueError("网关地址不允许内嵌用户名/密码")
    return url


def _safe_gateway_url(base_url: str, path: str) -> str:
    """连通性/多模态测试的网关地址校验：仅 http(s)、拒绝内嵌凭据与控制字符。

    口径与 SDK 构造侧（validate_base_url）同源：本机单用户场景允许
    127.0.0.1/内网地址（如 Ollama 本地网关），不封禁私网段。
    """
    return _check_http_url(f"{str(base_url).rstrip('/')}{path}")


def validate_base_url(base_url) -> str:
    """供应商/自定义模型 base_url 安全校验（SDK 构造与探测共用入口）。

    与 _safe_gateway_url 同一套口径：仅 http(s)、需有主机名、拒绝内嵌凭据与
    控制字符；允许 Ollama 等本机/内网 http 地址。返回去首尾空白与尾部斜杠后
    的归一化地址，供 OpenAICredential/AnthropicCredential 与 httpx 测试使用。
    """
    return _check_http_url(str(base_url or "").strip().rstrip("/"))

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


def _model_capability_view(m) -> dict:
    """模型能力视图：能力列 + capability_meta（字段来源/手动字段/最近同步快照）。"""
    cap = model_catalog.capability_meta_of(m)
    return {
        "max_input_tokens": m.max_input_tokens,
        "supports_tools": m.supports_tools,
        "input_modalities": cap["input_modalities"],
        "output_modalities": cap["output_modalities"],
        "default_thinking_level": cap["default_thinking_level"],
        "thinking_can_disable": cap["thinking_can_disable"],
        "reasoning_style": cap["reasoning_style"],
        "api_family": cap["api_family"],
        "remote_status": cap["remote_status"],
        "synced_at": m.synced_at,
        "capability": cap,
    }


PRESETS: list[dict] = load_presets()

DEFAULTS: dict = {
    "protocol": "openai",
    "base_url": "",
    "api_key": "",
    "model": "",
    "context_size": 128000,
    "max_tokens": 2048,
    "multimodal": False,
    "supports_vision": False,
    # 已声明/实测的思考档位（空 = 未声明，不做档位门控）
    "thinking_levels": [],
    "max_iters": 5,
    "enable_memory": True,
    "history_inject": 12,
    "enable_skills": False,
    "skills_dir": "",
    "enable_bash": False,
    "enable_plugins": False,
    "plugins_dir": "",
    "reasoning_effort": "",
}

# 模型级字段（供应商/模型两表承载）
MODEL_FIELDS = ("protocol", "base_url", "api_key", "model", "context_size", "max_tokens", "multimodal")

# 思考深度可选值（save_globals 白名单；与 llm._THINKING_LEVELS 同口径，此处不得 import llm）
# '' = 跟随模型；off = 旧版「关闭思考」（保留兼容，语义同 none）；其余 = canonical 档位
_REASONING_EFFORT_VALUES = frozenset({"", "off", "none", "minimal", "low", "medium", "high", "xhigh", "max"})


def _row() -> AgentSettings | None:
    with SessionLocal() as db:
        return db.get(AgentSettings, 1)


def _active_model():
    """当前激活的 (provider, provider_model)；无激活或已停用返回 None。"""
    try:
        with SessionLocal() as db:
            row = db.get(AgentSettings, 1)
            if row is None or not row.active_model_id:
                return None
            pm = db.get(ProviderModel, row.active_model_id)
            if pm is None or not pm.enabled:
                return None
            prov = db.get(ModelProvider, pm.provider_id)
            if prov is None or not prov.enabled:
                return None
            return prov, pm
    except OperationalError:
        logger.warning("读取激活模型失败（数据库暂不可用）", exc_info=True)
        return None


def _parse_temperature(raw) -> float | None:
    """模型级温度解析：空/非法 → None（不设置）；合法 → float（0-2 夹取）。"""
    if raw is None or str(raw).strip() == "":
        return None
    try:
        t = float(str(raw).strip())
    except ValueError:
        return None
    return t if 0.0 <= t <= 2.0 else None


def load() -> dict:
    """读取当前生效配置。

    优先级：激活模型（供应商+模型）> .env 兜底；
    Agent 工作参数（max_iters/记忆）始终取 agent_settings。
    """
    cfg = {**DEFAULTS}

    try:
        row = _row()
    except OperationalError:
        logger.warning("读取 Agent 设置失败（数据库暂不可用），使用默认配置", exc_info=True)
        row = None
    if row is not None:
        cfg.update(
            {
                "max_iters": row.max_iters,
                "enable_memory": row.enable_memory,
                "history_inject": row.history_inject,
                "enable_skills": row.enable_skills,
                "skills_dir": row.skills_dir or "",
                "enable_bash": row.enable_bash,
                "enable_plugins": row.enable_plugins,
                "plugins_dir": row.plugins_dir or "",
                "reasoning_effort": row.reasoning_effort or "",
            }
        )

    active = _active_model()
    if active is not None:
        prov, pm = active
        cfg.update(
            {
                "protocol": prov.protocol,
                "base_url": prov.base_url,
                "api_key": unseal(prov.api_key),
                "model": pm.model,
                "context_size": pm.context_size,
                "max_tokens": pm.max_tokens,
                "multimodal": pm.multimodal,
                "supports_vision": bool(pm.supports_vision),
                # 思考能力探测结论（Task 6 fix round 1：-1 未探测 / 0 不支持 / 1 支持，
                # 与 providers 视图同口径；前端据此禁用思考下拉，llm 据此兜底不发思考参数）
                "supports_thinking": pm.supports_thinking,
                "provider_name": prov.name,
                # 模型级采样温度（空 = 不设置，跟随厂商默认）；非法值忽略
                "temperature": _parse_temperature(pm.temperature),
                # 已声明/实测的思考档位（llm.reasoning_request 据此门控档位）
                "thinking_levels": _json_list(pm.thinking_levels),
                # 能力契约字段（Task 3）：工具/输入输出模态/思考默认档/api_family/字段来源
                **_model_capability_view(pm),
                # 套餐边界与会话头（Task 5）：preset_key 定位预设，usage_scope 约束定时任务
                "preset_key": prov.preset_key or "",
                "usage_scope": (preset_by_key(prov.preset_key) or {}).get("usage_scope", "general"),
            }
        )
        return cfg

    # .env 兜底
    cfg.update(
        {
            "protocol": "openai",
            "base_url": settings.llm_base_url or "",
            "api_key": settings.llm_api_key or "",
            "model": settings.llm_model or "",
            "provider_name": "env兜底",
            "preset_key": "",
            "usage_scope": "general",
        }
    )
    return cfg


def model_cfg_by_id(mid: int) -> dict | None:
    """按 provider_models.id 取完整模型配置（供应商+模型+密钥）；不存在或已停用返回 None。

    供专家级模型绑定使用：在 load() 的 Agent 工作参数基础上覆盖模型级字段。
    """
    try:
        with SessionLocal() as db:
            pm = db.get(ProviderModel, int(mid))
            if pm is None or not pm.enabled:
                return None
            prov = db.get(ModelProvider, pm.provider_id)
            if prov is None or not prov.enabled:
                return None
            cfg = load()
            cfg.update(
                {
                    "protocol": prov.protocol,
                    "base_url": prov.base_url,
                    "api_key": unseal(prov.api_key),
                    "model": pm.model,
                    "context_size": pm.context_size,
                    "max_tokens": pm.max_tokens,
                    "multimodal": pm.multimodal,
                    "supports_vision": bool(pm.supports_vision),
                    # 思考能力探测结论（Task 6 fix round 1，同 load() 口径）
                    "supports_thinking": pm.supports_thinking,
                    "provider_name": prov.name,
                    "temperature": _parse_temperature(pm.temperature),
                    # 已声明/实测的思考档位（llm.reasoning_request 据此门控档位）
                    "thinking_levels": _json_list(pm.thinking_levels),
                    # 能力契约字段（Task 3，同 load() 口径）
                    **_model_capability_view(pm),
                    # 套餐边界与会话头（Task 5，同 load() 口径）
                    "preset_key": prov.preset_key or "",
                    "usage_scope": (preset_by_key(prov.preset_key) or {}).get("usage_scope", "general"),
                }
            )
            return cfg
    except OperationalError:
        logger.warning("读取模型配置失败（数据库暂不可用）：model_id=%s", mid, exc_info=True)
        return None


def masked(cfg: dict) -> dict:
    """回传给前端的配置：api_key 脱敏。"""
    out = {**cfg}
    key = (cfg.get("api_key") or "").strip()
    if not key:
        out["api_key"] = ""
    elif len(key) <= 4:
        out["api_key"] = MASK_PREFIX  # 短密钥整体掩码：后缀回显会把全文明文泄露给前端
    else:
        out["api_key"] = MASK_PREFIX + key[-4:]
    out["api_key_set"] = bool(key)
    return out


def save_globals(data: dict) -> None:
    """保存 Agent 工作参数（max_iters / 记忆管理）；模型字段请走供应商/模型接口。"""
    with SessionLocal() as db:
        row = db.get(AgentSettings, 1)
        if row is None:
            row = AgentSettings(id=1)
            db.add(row)
            db.flush()
        if "max_iters" in data:
            row.max_iters = max(1, min(int(data["max_iters"] or 5), 20))
        if "enable_memory" in data:
            row.enable_memory = bool(data["enable_memory"])
        if "history_inject" in data:
            row.history_inject = max(0, min(int(data["history_inject"] or 12), 100))
        if "enable_skills" in data:
            row.enable_skills = bool(data["enable_skills"])
        if "skills_dir" in data:
            row.skills_dir = str(data.get("skills_dir") or "").strip()[:300]
        if "enable_bash" in data:
            row.enable_bash = bool(data["enable_bash"])
        if "enable_plugins" in data:
            row.enable_plugins = bool(data["enable_plugins"])
        if "plugins_dir" in data:
            row.plugins_dir = str(data.get("plugins_dir") or "").strip()[:300]
        if "reasoning_effort" in data:
            v = str(data.get("reasoning_effort") or "").strip().lower()
            # 白名单与 llm._THINKING_LEVELS 同口径（config_store 不能 import llm：llm 依赖本模块）。
            # '' = 跟随模型；off = 旧版「关闭思考」（Task 6B 起保留兼容，语义同 none）；
            # none/minimal/low/medium/high/xhigh/max = canonical 七档（Task 6B 前端可提交）
            row.reasoning_effort = v if v in _REASONING_EFFORT_VALUES else ""
        row.updated_at = datetime.now()
        db.commit()


def current_api_key() -> str:
    """当前生效配置中的 API Key（供测试时回填掩码哨兵）。"""
    return load().get("api_key", "")


def resolve_test_model_id(model_id: int | None = None, provider_id: int | None = None) -> int | None:
    """定位工具/图片实测要回写的模型行：显式 model_id > 供应商首个启用模型 > 激活模型。"""
    if model_id:
        return int(model_id)
    if provider_id:
        for p in list_providers():
            if p["id"] == provider_id:
                for m in p["models"]:
                    if m["enabled"]:
                        return m["id"]
        return None
    with SessionLocal() as db:
        row = db.get(AgentSettings, 1)
        if row is None or not row.active_model_id:
            return None
        return row.active_model_id


def provider_test_cfg(pid: int) -> dict:
    """取某供应商第一个启用模型的测试配置。"""
    for p in list_providers():
        if p["id"] == pid:
            models = [m for m in p["models"] if m["enabled"]]
            if not models:
                raise ValueError(f"供应商「{p['name']}」下没有启用中的模型")
            key = "sk-unknown"
            pm = None
            try:
                with SessionLocal() as db:
                    prov = db.get(ModelProvider, pid)
                    key = unseal(prov.api_key) if prov else key
                    pm = db.get(ProviderModel, models[0]["id"])
            except OperationalError:
                logger.warning("读取供应商密钥失败（数据库暂不可用），使用占位 key", exc_info=True)
            m = models[0]
            cfg = {
                "protocol": p["protocol"],
                "base_url": p["base_url"],
                "api_key": key,
                "model": m["model"],
                "context_size": m["context_size"],
                "max_tokens": m["max_tokens"],
                # 套餐边界与会话头（Task 5）：供应商测试同样按 preset 生成请求头/受套餐约束
                "preset_key": p["preset_key"],
                "usage_scope": p["usage_scope"],
            }
            if pm is not None:
                # 能力契约（Task 3/5）：测试路径同样按模型的思考风格/档位生成请求参数
                cfg.update(_model_capability_view(pm))
            return cfg
    raise ValueError(f"供应商 {pid} 不存在")


# ---------------- 供应商 / 模型 CRUD（ZCode 式） ----------------

def _json_list(raw: str) -> list:
    """解析存为 JSON 文本的列表字段（如 thinking_levels）；失败返回空列表。"""
    try:
        val = json.loads(raw or "[]")
    except Exception:
        return []
    return val if isinstance(val, list) else []


def list_providers() -> list[dict]:
    try:
        row = _row()
        active_model_id = row.active_model_id if row else None
        with SessionLocal() as db:
            provs = db.query(ModelProvider).order_by(ModelProvider.id.asc()).all()
            models = db.query(ProviderModel).order_by(ProviderModel.id.asc()).all()
    except OperationalError:
        logger.warning("供应商列表查询失败（数据库暂不可用）", exc_info=True)
        return []

    by_prov: dict[int, list] = {}
    for m in models:
        by_prov.setdefault(m.provider_id, []).append(m)

    return [
        {
            "id": p.id,
            "name": p.name,
            "protocol": p.protocol,
            "base_url": p.base_url,
            "api_key_set": bool(p.api_key),
            "enabled": p.enabled,
            # 预设标识与在线列表地址（适配器只认 preset_key；usage_scope 等套餐边界取自预设文件）
            "preset_key": p.preset_key or "",
            "models_url": p.models_url or "",
            "usage_scope": (preset_by_key(p.preset_key) or {}).get("usage_scope", "general"),
            "auth_scheme": (preset_by_key(p.preset_key) or {}).get("auth_scheme", "bearer"),
            "sync_adapter": (preset_by_key(p.preset_key) or {}).get("sync_adapter", "none"),
            # 管控凭证只暴露「是否已配置」布尔量，绝不回传凭证内容（含掩码）
            "sync_credentials_set": bool(p.sync_credentials),
            "models": [
                {
                    "id": m.id,
                    "model": m.model,
                    "context_size": m.context_size,
                    "max_tokens": m.max_tokens,
                    "multimodal": m.multimodal,
                    # 视觉（图片理解）能力：聊天传图需开启
                    "supports_vision": bool(m.supports_vision),
                    "enabled": m.enabled,
                    "active": m.id == active_model_id,
                    # 采样温度（空 = 跟随系统默认）与备注
                    "temperature": m.temperature,
                    "note": m.note,
                    # 思考能力探测：-1 未探测 / 0 否 / 1 是 + 可用档位
                    "supports_thinking": m.supports_thinking,
                    "thinking_levels": _json_list(m.thinking_levels),
                    # 探测出的有效上下文 / 最大输出（0 = 未探测）与探测时间
                    "probed_context": m.probed_context,
                    "probed_max_output": m.probed_max_output,
                    "probed_at": m.probed_at,
                    # 能力契约字段（Task 3）：工具/模态/思考默认档/api_family/来源/同步时间
                    **_model_capability_view(m),
                }
                for m in by_prov.get(p.id, [])
            ],
        }
        for p in provs
    ]


def models_flat() -> list[dict]:
    """所有启用中的（供应商, 模型）扁平列表，供快速切换下拉。"""
    out = []
    for p in list_providers():
        if not p["enabled"]:
            continue
        for m in p["models"]:
            if m["enabled"]:
                out.append(
                    {
                        "id": m["id"],
                        "label": f"{p['name']} · {m['model']}",
                        "provider": p["name"],
                        "model": m["model"],
                        "multimodal": m["multimodal"],
                        "supports_vision": m["supports_vision"],
                        "active": m["active"],
                        # 思考能力（Task 6B：与 providers 视图同口径，供前端按 Agent 生效模型生成档位）
                        "supports_thinking": m["supports_thinking"],
                        "thinking_levels": m["thinking_levels"],
                        "reasoning_style": m["reasoning_style"],
                        # 能力契约字段（Task 3，与 providers 视图同口径）
                        "max_input_tokens": m["max_input_tokens"],
                        "supports_tools": m["supports_tools"],
                        "input_modalities": m["input_modalities"],
                        "output_modalities": m["output_modalities"],
                        "default_thinking_level": m["default_thinking_level"],
                        "thinking_can_disable": m["thinking_can_disable"],
                        "api_family": m["api_family"],
                        "capability": m["capability"],
                    }
                )
    return out


def _read_sync_credentials(stored: str) -> dict:
    """解析密封的管控凭证 JSON；空/损坏返回空 dict。"""
    if not stored:
        return {}
    try:
        data = json.loads(unseal(stored) or "{}")
    except Exception:
        logger.warning("管控凭证解析失败，按未配置处理", exc_info=True)
        return {}
    return data if isinstance(data, dict) else {}


def save_provider(data: dict) -> dict:
    """新增/更新供应商；api_key 传空或掩码（MASK 哨兵 / MASK_PREFIX 回显形态）保留现值。

    preset_key/models_url 供同步适配器定位官方列表；sync_access_key/sync_secret_key
    为可选管控面凭证（火山 AK/SK），密封 JSON 存 sync_credentials：空/掩码保留旧值，
    任何 GET 不回传。
    """
    pid = data.get("provider_id")
    name = str(data.get("name") or "").strip()[:50] or "未命名供应商"
    with SessionLocal() as db:
        prov = db.get(ModelProvider, int(pid)) if pid else None
        if prov is None:
            prov = ModelProvider(name=name)
            db.add(prov)
            db.flush()
        else:
            prov.name = name
        prov.protocol = str(data.get("protocol") or prov.protocol or "openai")[:20]
        if data.get("base_url") is not None:
            prov.base_url = str(data["base_url"]).strip().rstrip("/")[:300]
        if data.get("preset_key") is not None:
            prov.preset_key = str(data["preset_key"]).strip()[:50]
        if data.get("models_url") is not None:
            url = str(data["models_url"]).strip().rstrip("/")[:300]
            if url and not url.startswith(("http://", "https://")):
                raise ValueError("模型列表地址仅允许 http(s)")
            prov.models_url = url
        key_in = str(data.get("api_key") or "").strip()
        # 两种掩码形态均视为「未修改保留原值」：哨兵 ******** 与 GET 回显的 ••••••+后四位
        if key_in and key_in != MASK and not key_in.startswith(MASK_PREFIX):
            prov.api_key = seal(key_in[:300])
        # 管控凭证：与 api_key 同口径（空/掩码保留旧值），逐字段合并后整体重新密封
        ak_in = str(data.get("sync_access_key") or "").strip()
        sk_in = str(data.get("sync_secret_key") or "").strip()
        if ak_in or sk_in:
            creds = _read_sync_credentials(prov.sync_credentials)
            changed = False
            if ak_in and ak_in != MASK and not ak_in.startswith(MASK_PREFIX):
                creds["access_key"] = ak_in[:300]
                changed = True
            if sk_in and sk_in != MASK and not sk_in.startswith(MASK_PREFIX):
                creds["secret_key"] = sk_in[:300]
                changed = True
            if changed:
                prov.sync_credentials = seal(json.dumps(creds, ensure_ascii=False))
        if data.get("enabled") is not None:
            prov.enabled = bool(data["enabled"])
        prov.updated_at = datetime.now()
        db.commit()
        return {"id": prov.id, "name": prov.name, "protocol": prov.protocol}


def delete_provider(pid: int) -> None:
    """删除供应商，同事务级联删除其全部模型；激活模型属于该供应商时清理激活态。

    两表无数据库级联：不删 ProviderModel 会留孤儿数据，SQLite 复用 rowid 后
    旧模型还会挂到新供应商名下。
    """
    with SessionLocal() as db:
        pid = int(pid)
        models = db.query(ProviderModel).filter(ProviderModel.provider_id == pid).all()
        model_ids = {m.id for m in models}
        row = db.get(AgentSettings, 1)
        if row is not None and row.active_model_id in model_ids:
            row.active_model_id = None
        for m in models:
            db.delete(m)
        prov = db.get(ModelProvider, pid)
        if prov is not None:
            db.delete(prov)
        db.commit()


def save_provider_model(data: dict) -> dict:
    """新增/更新供应商下的模型。

    手动填写的能力字段（上下文/输出/输入上限/工具/模态/思考档位/思考默认档/api_family 等）
    记入 capability_meta.manual_fields，目录/在线同步默认不覆盖它们；
    probe_* 与 note/temperature/enabled 由用户单独管理，同步永不触碰。
    """
    provider_id = int(data["provider_id"])
    model = str(data.get("model") or "").strip()[:120]
    if not model:
        raise ValueError("模型名不能为空")
    mid = data.get("model_id")
    with SessionLocal() as db:
        prov = db.get(ModelProvider, provider_id)
        if prov is None:
            raise ValueError(f"供应商 {provider_id} 不存在")
        pm = db.get(ProviderModel, int(mid)) if mid else None
        if pm is None:
            pm = ProviderModel(provider_id=provider_id, model=model)
            db.add(pm)
            db.flush()
        else:
            pm.model = model
        pm.context_size = max(4096, int(data.get("context_size") or pm.context_size or 128000))
        pm.max_tokens = max(64, int(data.get("max_tokens") or pm.max_tokens or 2048))
        if data.get("multimodal") is not None:
            pm.multimodal = bool(data["multimodal"])
        if data.get("supports_vision") is not None:
            pm.supports_vision = bool(data["supports_vision"])
        if data.get("enabled") is not None:
            pm.enabled = bool(data["enabled"])
        # 采样温度：空串 = 跟随系统默认（TEXT 存储，兼容手动填写）
        if data.get("temperature") is not None:
            t = data["temperature"]
            pm.temperature = str(t).strip()[:16] if t != "" else ""
        if data.get("note") is not None:
            pm.note = str(data.get("note") or "")[:500]
        # 思考能力：探测接口会自动写回，此处也允许手动标记（-1/0/1）
        if data.get("supports_thinking") is not None:
            pm.supports_thinking = max(-1, min(1, int(data["supports_thinking"])))
        if data.get("thinking_levels") is not None:
            levels = [str(x)[:20] for x in (data["thinking_levels"] or [])][:12]
            pm.thinking_levels = json.dumps(levels, ensure_ascii=False)
        # 能力契约字段（Task 3）：手动填写 → 记 manual_fields，同步默认不覆盖
        if data.get("max_input_tokens") is not None:
            pm.max_input_tokens = max(0, int(data["max_input_tokens"]))
        if data.get("supports_tools") is not None:
            pm.supports_tools = max(-1, min(1, int(data["supports_tools"])))
        meta = model_catalog.capability_meta_of(pm)
        manual_fields = set(meta["manual_fields"])
        sources = dict(meta["sources"])
        for key in ("max_input_tokens", "supports_tools"):
            if data.get(key) is not None:
                manual_fields.add(key)
                sources[key] = "manual"
        if data.get("input_modalities") is not None:
            meta["input_modalities"] = [str(x)[:20] for x in (data["input_modalities"] or [])][:8]
            manual_fields.add("input_modalities")
            sources["input_modalities"] = "manual"
            # 手动模态驱动派生能力（未显式传布尔值时），派生值同步标记 manual：
            # 模态是用户的权威声明，目录/同步不得把 vision/multimodal 翻转成与模态矛盾的值
            if data.get("multimodal") is None:
                pm.multimodal = bool(model_catalog.derive_multimodal(meta["input_modalities"]))
                manual_fields.add("multimodal")
                sources["multimodal"] = "manual"
            if data.get("supports_vision") is None:
                pm.supports_vision = bool(model_catalog.derive_supports_vision(meta["input_modalities"]))
                manual_fields.add("supports_vision")
                sources["supports_vision"] = "manual"
        if data.get("output_modalities") is not None:
            meta["output_modalities"] = [str(x)[:20] for x in (data["output_modalities"] or [])][:8]
            manual_fields.add("output_modalities")
            sources["output_modalities"] = "manual"
        if data.get("default_thinking_level") is not None:
            meta["default_thinking_level"] = str(data["default_thinking_level"]).strip()[:20] or None
            manual_fields.add("default_thinking_level")
            sources["default_thinking_level"] = "manual"
        if "thinking_can_disable" in data:
            v = data["thinking_can_disable"]
            meta["thinking_can_disable"] = None if v is None else bool(v)
            manual_fields.add("thinking_can_disable")
            sources["thinking_can_disable"] = "manual"
        if data.get("reasoning_style") is not None:
            meta["reasoning_style"] = str(data["reasoning_style"]).strip()[:20]
            manual_fields.add("reasoning_style")
            sources["reasoning_style"] = "manual"
        if data.get("api_family") is not None:
            fam = str(data["api_family"]).strip().lower()[:30]
            meta["api_family"] = fam if fam in ("chat_completions", "messages", "responses", "other") else ""
            manual_fields.add("api_family")
            sources["api_family"] = "manual"
        # 既有字段的手动标记（同步默认同样不覆盖这些字段）
        for key, col in (
            ("context_size", "context_size"),
            ("max_tokens", "max_tokens"),
            ("multimodal", "multimodal"),
            ("supports_vision", "supports_vision"),
            ("supports_thinking", "supports_thinking"),
            ("thinking_levels", "thinking_levels"),
        ):
            if data.get(key) is not None:
                manual_fields.add(col)
                sources[col] = "manual"
        meta["manual_fields"] = sorted(manual_fields)
        meta["sources"] = sources
        pm.capability_meta = json.dumps(meta, ensure_ascii=False)
        pm.updated_at = datetime.now()
        db.commit()
        return {"id": pm.id, "model": pm.model}


def delete_provider_model(mid: int) -> None:
    with SessionLocal() as db:
        pm = db.get(ProviderModel, int(mid))
        if pm is not None:
            db.delete(pm)
        row = db.get(AgentSettings, 1)
        if row is not None and row.active_model_id == int(mid):
            row.active_model_id = None
        db.commit()


def activate_model(mid: int) -> dict:
    """激活某个模型（跨供应商），立即生效。"""
    with SessionLocal() as db:
        pm = db.get(ProviderModel, int(mid))
        if pm is None or not pm.enabled:
            raise ValueError(f"模型 {mid} 不存在或未启用")
        prov = db.get(ModelProvider, pm.provider_id)
        if prov is None or not prov.enabled:
            raise ValueError(f"供应商未启用：{pm.provider_id}")
        row = db.get(AgentSettings, 1)
        if row is None:
            row = AgentSettings(id=1)
            db.add(row)
            db.flush()
        row.active_model_id = int(mid)
        db.commit()
        return {"model": pm.model, "provider": prov.name}


def reset_provider_model_overrides(mid: int) -> dict:
    """清除模型的手动能力覆盖并按最近一次同步快照恢复；模型不存在抛 ValueError。"""
    with SessionLocal() as db:
        pm = db.get(ProviderModel, int(mid))
        if pm is None:
            raise ValueError(f"模型 {mid} 不存在")
        result = model_catalog.reset_manual_overrides(pm)
        db.commit()
        return {"model_id": pm.id, **result}


def sync_provider_models(provider_id: int, include_disabled: bool = False, override_manual: bool = False) -> dict:
    """同步供应商在线模型列表（Task 4）：在线适配器优先，失败/无来源时能力目录兜底。

    在线获取只发目录/管控请求（火山官方管控面为只读列表 Action 的只读 POST，body 仅 {} 或
    PageNumber/PageSize 分页字段）、绝不发推理请求；
    单事务幂等 upsert，manual/probe/note/temperature/enabled/active 不被覆盖。实现在 model_sync
    （延迟导入避免循环依赖）。
    """
    from . import model_sync  # 延迟导入：model_sync 依赖本模块的校验/预设/凭证读取

    with SessionLocal() as db:
        return model_sync.sync_provider_models(
            db, provider_id, include_disabled=include_disabled, override_manual=override_manual
        )


# ---------------- 连通性测试 ----------------

# 工具/图片实测结论的三态判定（落库口径与界面展示一致）
_PROBE_VERDICT_FIELDS = ("supports_tools", "supports_vision")


def record_probe_verdict(model_id: int, field: str, verdict: str) -> dict | None:
    """把工具/图片实测结论写回模型行（来源 probe）。

    field: supports_tools / supports_vision；verdict: supported / rejected / unknown。
    只有明确结论才写列与来源（supported→1、rejected→0）；unknown（网络错误等
    未获得明确证据）不改动任何值——不把「没测到」当成「不支持」。

    probe 结论同时记入 manual_fields 作为**本地覆盖**：默认目录同步不覆盖它
    （实测优先于目录声明），`override_manual=True` 的同步或 reset-overrides
    可清除覆盖并恢复同步值。
    """
    if field not in _PROBE_VERDICT_FIELDS or verdict not in ("supported", "rejected"):
        return None
    value = 1 if verdict == "supported" else 0
    with SessionLocal() as db:
        pm = db.get(ProviderModel, int(model_id))
        if pm is None:
            return None
        setattr(pm, field, value)
        meta = model_catalog.capability_meta_of(pm)
        sources = dict(meta["sources"])
        sources[field] = "probe"
        meta["sources"] = sources
        # 实测结论作为本地覆盖：默认同步不再用目录值覆盖它（override_manual/reset 可清除）
        if field not in meta["manual_fields"]:
            meta["manual_fields"] = sorted([*meta["manual_fields"], field])
        pm.capability_meta = json.dumps(meta, ensure_ascii=False)
        try:
            result = json.loads(pm.probe_result or "{}")
        except Exception:
            result = {}
        if not isinstance(result, dict):
            result = {}
        result[field] = {
            "verdict": verdict,
            "value": value,
            "at": datetime.now().isoformat(timespec="seconds"),
        }
        pm.probe_result = json.dumps(result, ensure_ascii=False)
        pm.probed_at = datetime.now().isoformat(timespec="seconds")
        db.commit()
        return {"model_id": pm.id, "field": field, "value": value}


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
        resp = json.load(urllib.request.urlopen(req, timeout=TEST_TIMEOUT_SECONDS))
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
        resp = json.load(urllib.request.urlopen(req, timeout=TEST_TIMEOUT_SECONDS))
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
