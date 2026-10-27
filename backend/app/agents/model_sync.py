"""在线模型列表同步适配器与幂等 upsert（Task 4 + fix round 1：官方响应形状修正）。

职责边界：
- 适配器只做「官方列表 payload → RemoteModel」的精确映射：远端未提供的字段一律未知
  （supports_thinking/supports_tools=-1、模态/视觉=None），不从模型名（如 "VL"）猜能力；
- 同步只发**目录/管控请求**，绝不发推理请求（chat/completions、messages 等）；
  唯一例外是火山官方管控面三个只读列表 Action 的 POST（body 仅 `{}` 或
  PageNumber/PageSize 两个分页字段），发送前强制校验官方 host + Action 白名单 + 根路径，
  其他 Action/推理路径一律拒绝；
- 安全边界：地址发出前经 config_store._check_http_url 校验（非法 URL 零请求）；
  20 秒超时、响应大小上限、默认不跟随重定向；401/403/404/429/超时/非 JSON/超大/空列表/
  结构不可识别一律抛 RemoteFetchError，绝不静默变成「空模型列表」或假成功；
  429 仅在可解析且有界 Retry-After 时退避重试；OpenRouter 只跟进同源分页（相对路径先
  urljoin 再校验）且页数有上限；Anthropic 用 has_more+after_id 有界分页；阿里用
  page_no/page_size/total 有界分页；
- 火山管控面（volcengine_ak_sk）：官方 HMAC-SHA256 签名（只读 POST + 按实际 body 计算的
  X-Content-Sha256），AK/SK 只从密封 sync_credentials 读取；未配置凭证时返回 None（调用方按目录兜底）并给出
  明确 warning，不伪造在线结果；
- 协议过滤：OpenCode 等混合协议列表里 api_family 未知或不兼容当前对话入口的模型必须
  skipped 并说明，绝不默认当 chat_completions；只同步文本对话模型；
- upsert：同一供应商并发同步抛 ProviderSyncBusy（端点 409）；批内按模型 ID 去重；
  单事务提交，一条非法记录（SyncRecordError：缺模型 ID 或记录根本不是 JSON 对象，
  _require_entries/_alibaba_entries 统一守卫）或写入异常整批回滚（结构化 ok=false，
  不裸 500）；fetch 阶段对适配器未预期缺陷另有结构化兜底（只记异常类型）；
  远端消失不删本地行；manual/probe/note/temperature/enabled/active 由
  apply_capability_entry 的既有契约保留；连续两次同步第二次全部 unchanged（不重复写 synced_at）。

fetch_remote_models 返回的 models 为 None 表示「该供应商没有在线列表来源」
（未适配/未配置地址或凭证），调用方按能力目录兜底并标注 partial/online=false。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import threading
import time
import urllib.parse
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import httpx

from ..models import ModelProvider, ProviderModel
from . import model_catalog
from .config_store import _check_http_url, _read_sync_credentials, preset_by_key
from .model_catalog import RemoteModel
from .secrets_box import unseal

logger = logging.getLogger(__name__)

# 目录/管控请求的显式超时（秒）：挂起服务器应快速失败并给出中文提示
TIMEOUT_SECONDS = 20.0
# 响应大小上限（字节）：异常厂商返回巨响应时不耗尽内存
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
# 分页页数上限（OpenRouter links.next / Anthropic after_id / 阿里 page_no 共用）
MAX_PAGES = 5
# 429 退避重试：最多 1 次，且等待时间夹到有界上限（Retry-After 可能是一小时）
RATE_LIMIT_MAX_RETRIES = 1
MAX_RETRY_WAIT_SECONDS = 2.0
# 同步请求的 UA（OpenCode Go 等要求带 UA 的入口统一使用）
SYNC_USER_AGENT = "personal-workbench-model-sync/1.0"

# ---------------- 火山官方管控面（OpenAPI 风格，AK/SK 签名） ----------------
# 官方域名与版本；models_url 可显式覆盖，但发送前仍强制校验 host/Action 白名单
VOLC_HOST = "ark.cn-beijing.volcengineapi.com"
VOLC_ENDPOINT = f"https://{VOLC_HOST}/"
VOLC_VERSION = "2024-01-01"
VOLC_REGION = "cn-beijing"
VOLC_SERVICE = "ark"
# 官方固定签名字段：只读 POST + 四段 SignedHeaders
VOLC_SIGNED_HEADERS = "content-type;host;x-content-sha256;x-date"
# 只读 POST 请求体：空 {} 或仅 PageNumber/PageSize 两个分页字段（Ruling），其他字段禁止
VOLC_BODY = b"{}"
VOLC_PAGE_SIZE = 100
# 套餐 preset_key → 官方只读列表 Action（其他 Action/推理路径一律拒绝）
VOLC_ACTIONS = {
    "volcengine": "ListFoundationModels",
    "volcengine_coding": "ListArkCodingPlanModel",
    "volcengine_agent_plan": "ListArkAgentPlanModel",
}
VOLC_ALLOWED_ACTIONS = frozenset(VOLC_ACTIONS.values())
# ListFoundationModels 官方分页：请求体 PageNumber/PageSize，响应 Result.TotalCount
VOLC_FOUNDATION_ACTION = "ListFoundationModels"

# 阿里官方分页页大小（GET /api/v1/models，page_no/page_size，total 指示总数）
ALIBABA_PAGE_SIZE = 100

# api_family 未知标记：混合协议列表未声明协议时不得默认当 chat_completions
UNKNOWN_API_FAMILY = "unknown"
# 分页截断警告前缀（同步层据此置 truncated，响应不把截断当完整成功）
TRUNCATION_MARK = "分页截断"
# 已下线/停用状态（include_disabled=false 时跳过）；含腾讯 pre-offline 变体
_DISABLED_STATUSES = {"deprecated", "offline", "retired", "disabled", "stopped", "pre-offline", "preoffline"}
# OpenAI 兼容列表里常见的非文本对话模型 ID 前缀（嵌入/语音/图像类）
_NON_CHAT_PREFIXES = ("whisper", "tts", "text-embedding", "embedding", "dall-e", "gpt-image", "omni-moderation")
# Anthropic 官方思考档位展示顺序（effort 子项 supported=true 才入列）
_ANTHROPIC_EFFORT_ORDER = ("high", "low", "medium", "max", "xhigh")


def _truncation_warning(pages: int) -> str:
    return f"{TRUNCATION_MARK}：已拉取前 {pages} 页，可能未取全，请增大页大小或联系厂商"


class RemoteFetchError(Exception):
    """在线列表获取失败（网络/HTTP 状态/非 JSON/超大/空列表/结构不可识别/非法 URL/跨域分页）。

    调用方按「在线失败」诚实处理：目录兜底 + partial=true + online=false，
    绝不把失败写成空模型列表或假成功。
    """


class SyncRecordError(Exception):
    """列表里存在结构非法的记录（如缺模型 ID）：整批拒绝，不留下半批。"""


class ProviderSyncBusy(Exception):
    """同一供应商已有同步在进行（并发重复点击）。"""


# ---------------- 小工具：类型归一 ----------------

def _entries(payload, *keys) -> list:
    """按优先级取 payload 里的记录数组（data/models/items…）。"""
    if not isinstance(payload, dict):
        return []
    for key in keys:
        val = payload.get(key)
        if isinstance(val, list):
            return val
    return []


def _require_entries(payload, *keys) -> list:
    """取记录数组；预期字段完全缺失 → 结构不可识别（RemoteFetchError，不假成功）。

    数组元素必须是 JSON 对象：字符串/null/数组/数字等畸形记录直接抛 SyncRecordError
    （整批拒绝），绝不让适配器在 record.get 上崩成裸 500。
    """
    if not isinstance(payload, dict) or not any(isinstance(payload.get(k), list) for k in keys):
        raise RemoteFetchError(f"模型列表响应结构不可识别（缺少 {'/'.join(keys)} 列表字段）")
    entries = _entries(payload, *keys)
    _require_records(entries)
    return entries


def _require_records(entries) -> None:
    """记录数组元素守卫：任一非 JSON 对象 → SyncRecordError（整批拒绝，不裸 500）。"""
    for record in entries:
        if not isinstance(record, dict):
            raise SyncRecordError(f"模型列表记录不是 JSON 对象（{type(record).__name__}）")


def _record_id(record) -> str:
    """取记录的模型 ID（id/model/model_id/ModelID/Name）；缺失或非对象抛 SyncRecordError。"""
    if not isinstance(record, dict):
        raise SyncRecordError("模型列表记录不是 JSON 对象")
    for key in ("id", "model", "model_id", "modelId", "ModelID", "Name", "name"):
        val = record.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()[:120]
    raise SyncRecordError("模型列表记录缺少模型 ID")


def _to_int(val):
    """宽松整数解析：bool/浮点/数字字符串均可；非法返回 None（未知）。"""
    if isinstance(val, bool):
        return None
    if isinstance(val, int):
        return val
    if isinstance(val, float) and val.is_integer():
        return int(val)
    if isinstance(val, str) and val.strip().lstrip("-").isdigit():
        return int(val.strip())
    return None


def _modalities(val):
    """模态列表归一（小写去空白）；支持官方字符串/逗号串/数组三种形态；空 → None（未知）。"""
    if isinstance(val, str):
        val = re.split(r"[,;]", val) if ("," in val or ";" in val) else [val]
    if not isinstance(val, list):
        return None
    out = [str(x).strip().lower()[:20] for x in val if str(x).strip()]
    return out or None


def _str_list(val) -> list[str]:
    """字符串数组归一（小写）；非数组返回 []。"""
    if not isinstance(val, list):
        return []
    return [str(x).strip().lower() for x in val if str(x).strip()]


def _bool_state(val):
    """API 布尔字段 → 1/0；缺失或不可识别返回 None（未知）。"""
    if isinstance(val, bool):
        return 1 if val else 0
    if isinstance(val, int) and val in (0, 1):
        return val
    if isinstance(val, str):
        v = val.strip().lower()
        if v in ("true", "yes", "supported", "support"):
            return 1
        if v in ("false", "no", "unsupported"):
            return 0
    return None


def _bool_or(val, default: int = -1) -> int:
    """API 布尔字段 → 三态列；缺失按 default（-1 未知）。"""
    state = _bool_state(val)
    return default if state is None else state


def _tri_state(val, default: int = -1) -> int:
    """三态列（-1 未知/0 否/1 是）解析；不可识别按 default。"""
    if isinstance(val, bool):
        return 1 if val else 0
    if isinstance(val, int) and val in (-1, 0, 1):
        return val
    if isinstance(val, str) and val.strip() in ("-1", "0", "1"):
        return int(val.strip())
    return default


def _vision(in_mod):
    """视觉只由明确输入模态推导（含 image → True；有模态不含 image → False；无模态 → None）。"""
    return model_catalog.derive_supports_vision(in_mod)


def _efforts(raw):
    """思考档位声明 → (supports_thinking, levels|None)；无声明 (None, None)。

    兼容官方两种形态：OpenRouter `reasoning.supported_efforts` 与
    DeepSeek `effort.supported_levels`。档位只从这些字段读，不从 supported_parameters 猜。
    """
    if raw is None:
        return None, None
    if isinstance(raw, bool):
        return (1 if raw else 0), None
    if isinstance(raw, dict):
        levels = raw.get("supported_efforts") or raw.get("supported_levels") or raw.get("efforts")
        if isinstance(levels, list) and levels:
            return 1, [str(x)[:20] for x in levels][:12]
        return 1, None
    if isinstance(raw, list) and raw:
        return 1, [str(x)[:20] for x in raw][:12]
    return None, None


def _default_level(raw):
    """思考默认档位（DeepSeek effort.default_level 等）；无声明 None。"""
    if isinstance(raw, dict):
        v = str(raw.get("default_level") or raw.get("default_effort") or "").strip().lower()[:20]
        return v or None
    return None


# ---------------- 适配器：官方 payload → RemoteModel（纯映射，不过滤） ----------------

def parse_openai_models(payload) -> list[RemoteModel]:
    """OpenAI 兼容简表（GET /models）：官方只保证 ID，能力一律未知。"""
    out = []
    for record in _require_entries(payload, "data", "models"):
        out.append(RemoteModel(
            model=_record_id(record),
            display_name=str(record.get("display_name") or "")[:120],
            api_family="chat_completions",
            status=str(record.get("status") or "")[:30],
            input_modalities=_modalities(record.get("input_modalities")),
            output_modalities=_modalities(record.get("output_modalities")),
        ))
    return out


def parse_opencode_models(payload) -> list[RemoteModel]:
    """OpenCode 列表：逐条保留 api_family（缺失=unknown），协议过滤交给同步层。

    列表 URL 与对话 base 由预设分开配置；这里只解析列表，不使用对话 base。
    """
    out = []
    for record in _require_entries(payload, "data", "models"):
        family = str(record.get("api_family") or record.get("protocol") or "").strip().lower()[:30]
        out.append(RemoteModel(
            model=_record_id(record),
            display_name=str(record.get("name") or record.get("display_name") or "")[:120],
            context_size=_to_int(record.get("context_length") or record.get("context_window")),
            input_modalities=_modalities(record.get("input_modalities")),
            output_modalities=_modalities(record.get("output_modalities")),
            supports_vision=_vision(record.get("input_modalities")),
            api_family=family or UNKNOWN_API_FAMILY,
            status=str(record.get("status") or "")[:30],
        ))
    return out


def parse_deepseek_models(payload) -> list[RemoteModel]:
    """DeepSeek 官方 /models：ID/name/context_window/max_output_tokens/modalities +
    effort.supported_levels/default_level；富字段缺失即未知。"""
    out = []
    for record in _require_entries(payload, "data", "models"):
        effort = record.get("effort")
        thinking, levels = _efforts(effort)
        out.append(RemoteModel(
            model=_record_id(record),
            display_name=str(record.get("name") or record.get("display_name") or "")[:120],
            context_size=_to_int(record.get("context_window") or record.get("context_length")),
            max_input_tokens=_to_int(record.get("max_input_tokens")),
            max_tokens=_to_int(record.get("max_output_tokens") or record.get("max_tokens")),
            input_modalities=_modalities(record.get("input_modalities")),
            output_modalities=_modalities(record.get("output_modalities")),
            supports_vision=_vision(record.get("input_modalities")),
            supports_thinking=-1 if thinking is None else thinking,
            thinking_levels=levels,
            default_thinking_level=_default_level(effort),
            supports_tools=_bool_or(record.get("supports_tools")),
            api_family="chat_completions",
            status=str(record.get("status") or "")[:30],
        ))
    return out


def parse_openrouter_models(payload) -> list[RemoteModel]:
    """OpenRouter：context/top_provider/架构模态/supported_parameters/reasoning 精确映射。"""
    out = []
    for record in _require_entries(payload, "data"):
        arch = record.get("architecture") if isinstance(record.get("architecture"), dict) else {}
        top = record.get("top_provider") if isinstance(record.get("top_provider"), dict) else {}
        params = record.get("supported_parameters") if isinstance(record.get("supported_parameters"), list) else []
        params = [str(p) for p in params]
        thinking, levels = _efforts(record.get("reasoning"))
        out.append(RemoteModel(
            model=_record_id(record),
            display_name=str(record.get("name") or "")[:120],
            context_size=_to_int(record.get("context_length") or top.get("context_length")),
            max_input_tokens=_to_int(record.get("max_input_tokens")),
            max_tokens=_to_int(top.get("max_completion_tokens") or record.get("max_tokens")),
            input_modalities=_modalities(arch.get("input_modalities")),
            output_modalities=_modalities(arch.get("output_modalities")),
            supports_vision=_vision(arch.get("input_modalities")),
            supports_thinking=-1 if thinking is None else thinking,
            thinking_levels=levels,
            supports_tools=1 if "tools" in params else 0,
            api_family="chat_completions",
            status=str(record.get("status") or "")[:30],
        ))
    return out


def _alibaba_entries(payload) -> list:
    """阿里官方返回：output.models[]（兼容兜底 data.models[]）。"""
    output = payload.get("output") if isinstance(payload, dict) else None
    models = output.get("models") if isinstance(output, dict) else None
    if not isinstance(models, list):
        data = payload.get("data") if isinstance(payload, dict) else None
        models = data.get("models") if isinstance(data, dict) else None
    if not isinstance(models, list):
        raise RemoteFetchError("阿里模型列表响应结构不可识别（缺少 output.models）")
    _require_records(models)
    return models


def parse_alibaba_models(payload) -> list[RemoteModel]:
    """阿里百炼官方 GET /api/v1/models：model/name、inference_metadata 模态、
    model_info 三项长度、features/capabilities 字符串数组、inference_offline_info 下线信息。"""
    out = []
    for record in _alibaba_entries(payload):
        info = record.get("model_info") if isinstance(record.get("model_info"), dict) else {}
        # inference_metadata 在记录根级（官方示例），兼容极少数包在 model_info 内的形态
        meta = record.get("inference_metadata") if isinstance(record.get("inference_metadata"), dict) else {}
        if not meta and isinstance(info.get("inference_metadata"), dict):
            meta = info["inference_metadata"]
        features = _str_list(record.get("features") or info.get("features"))
        caps = _str_list(record.get("capabilities") or info.get("capabilities"))
        in_mod = _modalities(meta.get("request_modality"))
        out_mod = _modalities(meta.get("response_modality"))
        # 下线信息：inference_offline_info（含 offline_time）或显式 status/offline 字段
        offline_info = record.get("inference_offline_info")
        if not isinstance(offline_info, dict):
            offline_info = info.get("inference_offline_info") if isinstance(info.get("inference_offline_info"), dict) else {}
        status = str(record.get("status") or info.get("status") or "")[:30]
        if not status and (record.get("offline") is True or info.get("offline") is True):
            status = "offline"
        if not status and offline_info.get("offline_time"):
            status = "offline"
        # 能力：只认官方显式声明（features/capabilities/模态），缺失一律未知
        supports_tools = 1 if any(f in features for f in ("function-calling", "function_calling", "tool_use", "tools")) else -1
        supports_thinking = 1 if "reasoning" in caps else -1
        supports_vision = True if ("vu" in caps or "vision" in features or (in_mod and "image" in in_mod)) else _vision(in_mod)
        out.append(RemoteModel(
            model=_record_id(record),
            display_name=str(record.get("name") or record.get("display_name") or "")[:120],
            context_size=_to_int(info.get("context_window")),
            max_input_tokens=_to_int(info.get("max_input_tokens")),
            max_tokens=_to_int(info.get("max_output_tokens")),
            input_modalities=in_mod,
            output_modalities=out_mod,
            supports_vision=supports_vision,
            supports_thinking=supports_thinking,
            supports_tools=supports_tools,
            api_family="chat_completions",
            status=status,
        ))
    return out


def parse_tencent_models(payload) -> list[RemoteModel]:
    """腾讯 TokenHub GET /models：在线 ID 与 status 逐字保留（preview 不臆改）。"""
    out = []
    for record in _require_entries(payload, "data", "models"):
        out.append(RemoteModel(
            model=_record_id(record),
            display_name=str(record.get("name") or record.get("display_name") or "")[:120],
            api_family="chat_completions",
            status=str(record.get("status") or "")[:30],
        ))
    return out


def parse_anthropic_models(payload) -> list[RemoteModel]:
    """Anthropic 官方 GET /v1/models：lifecycle 状态 + capabilities 对象。

    官方结构（`capabilities` 为对象，键始终存在）：
    - `capabilities.image_input.supported` → supports_vision；
    - `capabilities.thinking.supported` → supports_thinking（`thinking.types` 为各思考形态
      的 supported 对象，本适配器不使用）；
    - `capabilities.effort.<level>.supported=true` → thinking_levels，按官方推荐顺序
      （high/low/medium/max/xhigh）；**effort 是 thinking 的兄弟节点**，个别响应若挂在
      thinking 下仅作非权威兼容兜底；
    - 官方无 default 字段：仅在有明确 default 类字段时才填 default_thinking_level，否则 None；
    - 官方无通用工具能力键 → supports_tools 保持 -1（不从 server_tools 推断 function tools）；
    - `max_input_tokens/max_tokens` 能确认才映射，其余一律未知。
    """
    out = []
    for record in _require_entries(payload, "data", "models"):
        caps = record.get("capabilities") if isinstance(record.get("capabilities"), dict) else {}
        image = caps.get("image_input") if isinstance(caps.get("image_input"), dict) else {}
        thinking = caps.get("thinking") if isinstance(caps.get("thinking"), dict) else {}
        # 官方路径：effort 与 thinking 同级；兼容兜底：个别形态挂在 thinking 下
        effort = caps.get("effort") if isinstance(caps.get("effort"), dict) else {}
        if not effort and isinstance(thinking.get("effort"), dict):
            effort = thinking["effort"]
        vision = _bool_state(image.get("supported")) if "supported" in image else None
        thinking_state = _bool_state(thinking.get("supported")) if "supported" in thinking else None
        levels = [
            lvl for lvl in _ANTHROPIC_EFFORT_ORDER
            if isinstance(effort.get(lvl), dict) and effort[lvl].get("supported") is True
        ]
        default_level = None
        for key in ("default", "default_effort", "default_level"):
            raw = thinking.get(key) if thinking.get(key) is not None else effort.get(key)
            if isinstance(raw, str) and raw.strip():
                default_level = raw.strip().lower()[:20]
                break
        if thinking_state is None:
            thinking_state = 1 if levels else -1
        out.append(RemoteModel(
            model=_record_id(record),
            display_name=str(record.get("display_name") or record.get("name") or "")[:120],
            context_size=_to_int(record.get("context_window") or record.get("max_input_tokens")),
            max_input_tokens=_to_int(record.get("max_input_tokens")),
            max_tokens=_to_int(record.get("max_tokens")),
            supports_vision=None if vision is None else bool(vision),
            supports_thinking=thinking_state,
            thinking_levels=levels or None,
            default_thinking_level=default_level,
            api_family="messages",
            status=str(record.get("lifecycle") or record.get("status") or "")[:30],
        ))
    return out


def _volc_result(payload, key: str) -> list:
    """火山管控面响应：Result.Items / Result.Datas；缺失即结构不可识别。"""
    result = payload.get("Result") if isinstance(payload, dict) else None
    items = result.get(key) if isinstance(result, dict) else None
    if not isinstance(items, list):
        raise RemoteFetchError(f"火山管控面响应结构不可识别（缺少 Result.{key}）")
    return items


def parse_volcengine_foundation_models(payload) -> list[RemoteModel]:
    """ListFoundationModels：Result.Items[].Name/DisplayName（官方未给状态字段 → 不臆造）。"""
    out = []
    for record in _volc_result(payload, "Items"):
        out.append(RemoteModel(
            model=_record_id(record),
            display_name=str(record.get("DisplayName") or record.get("Name") or "")[:120],
            api_family="chat_completions",
        ))
    return out


def parse_volcengine_plan_models(payload) -> list[RemoteModel]:
    """ListArkCodingPlanModel / ListArkAgentPlanModel：Result.Datas[].ModelID（状态未知不臆造）。"""
    out = []
    for record in _volc_result(payload, "Datas"):
        out.append(RemoteModel(
            model=_record_id(record),
            display_name=str(record.get("ModelName") or record.get("DisplayName") or "")[:120],
            api_family="chat_completions",
        ))
    return out


# 适配器 → (解析函数, 是否按 links.next 分页)；阿里/Anthropic/火山有专门拉取逻辑
_FETCHERS = {
    "openai_models": (parse_openai_models, False),
    "opencode_models": (parse_opencode_models, False),
    "deepseek_models": (parse_deepseek_models, False),
    "tencent_models": (parse_tencent_models, False),
    "openrouter_models": (parse_openrouter_models, True),
}


# ---------------- 安全 HTTP ----------------

def _parse_retry_after(raw):
    """解析 Retry-After（秒或 HTTP-date）；不可解析返回 None（不盲猜重试）。"""
    if raw is None:
        return None
    text = str(raw).strip()
    if text.isdigit():
        return float(int(text))
    try:
        dt = parsedate_to_datetime(text)
    except Exception:
        return None
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return max(0.0, (dt - datetime.now(timezone.utc)).total_seconds())


def _raise_for_status(resp) -> None:
    """HTTP 状态 → RemoteFetchError（中文提示，不回传 URL 主体与密钥）。"""
    code = resp.status_code
    if code == 401:
        raise RemoteFetchError("API Key 无效或没有权限（401），无法获取在线模型列表")
    if code == 403:
        raise RemoteFetchError("没有访问权限（403），无法获取在线模型列表")
    if code == 404:
        raise RemoteFetchError("模型列表接口不存在（404），请检查列表地址")
    if 300 <= code < 400:
        raise RemoteFetchError(f"模型列表接口返回重定向（{code}），已停止跟随")
    if code >= 400:
        raise RemoteFetchError(f"模型列表接口返回 HTTP {code}")


def _read_json(resp) -> dict:
    """读响应体：大小上限 + 合法 JSON 对象；否则抛 RemoteFetchError（不变成空列表）。"""
    raw_len = resp.headers.get("content-length")
    if raw_len and raw_len.isdigit() and int(raw_len) > MAX_RESPONSE_BYTES:
        raise RemoteFetchError(f"模型列表响应过大（超过 {MAX_RESPONSE_BYTES // 1024 // 1024}MB），已拒绝解析")
    if len(resp.content) > MAX_RESPONSE_BYTES:
        raise RemoteFetchError(f"模型列表响应过大（超过 {MAX_RESPONSE_BYTES // 1024 // 1024}MB），已拒绝解析")
    try:
        data = resp.json()
    except Exception:
        raise RemoteFetchError("模型列表响应不是合法 JSON")
    if not isinstance(data, dict):
        raise RemoteFetchError("模型列表响应结构异常（非 JSON 对象）")
    return data


def _send(request_method: str, url: str, headers: dict, content: bytes | None) -> httpx.Response:
    """发一次目录/管控请求（429 按有界 Retry-After 有限重试）；失败一律抛 RemoteFetchError。"""
    for attempt in range(RATE_LIMIT_MAX_RETRIES + 1):
        try:
            with httpx.Client(timeout=TIMEOUT_SECONDS, follow_redirects=False) as client:
                if request_method == "POST":
                    resp = client.post(url, headers=headers, content=content or b"")
                else:
                    resp = client.get(url, headers=headers)
        except httpx.TimeoutException as exc:
            raise RemoteFetchError(f"模型列表请求超时（{TIMEOUT_SECONDS:.0f} 秒）") from exc
        except httpx.HTTPError as exc:
            raise RemoteFetchError(f"模型列表请求失败（{type(exc).__name__}）") from exc
        if resp.status_code == 429:
            wait = _parse_retry_after(resp.headers.get("Retry-After"))
            if wait is not None and attempt < RATE_LIMIT_MAX_RETRIES:
                time.sleep(min(wait, MAX_RETRY_WAIT_SECONDS))
                continue
            raise RemoteFetchError("模型列表被限流（429），请稍后重试")
        _raise_for_status(resp)
        return resp
    raise RemoteFetchError("模型列表请求失败：重试后仍被限流（429）")


def _get_json_once(url: str, headers: dict) -> dict:
    return _read_json(_send("GET", url, headers, None))


def _post_json(url: str, headers: dict, body: bytes) -> dict:
    return _read_json(_send("POST", url, headers, body))


def _with_query(url: str, params: dict) -> str:
    """在既有 query 上合并参数（分页用；保持 scheme/host/path 不变）。"""
    parts = urllib.parse.urlsplit(url)
    query = dict(urllib.parse.parse_qsl(parts.query, keep_blank_values=True))
    query.update({k: str(v) for k, v in params.items()})
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path, urllib.parse.urlencode(query), parts.fragment))


def _same_origin(a: str, b: str) -> bool:
    pa, pb = urllib.parse.urlsplit(a), urllib.parse.urlsplit(b)
    return (pa.scheme.lower(), (pa.hostname or "").lower(), pa.port) == (pb.scheme.lower(), (pb.hostname or "").lower(), pb.port)


def _next_page(payload: dict, current_url: str, origin_url: str) -> tuple[str | None, str | None]:
    """取分页链接：相对路径先 urljoin，再做同源校验；跨域/不安全返回 (None, warning)。"""
    links = payload.get("links") if isinstance(payload, dict) else None
    nxt = links.get("next") if isinstance(links, dict) else None
    if not isinstance(nxt, str) or not nxt.strip():
        return None, None
    nxt = urllib.parse.urljoin(current_url, nxt.strip())
    if not _same_origin(origin_url, nxt):
        return None, "分页链接指向其他域名，已停止跟进（仅允许同源分页）"
    try:
        _check_http_url(nxt)
    except ValueError:
        return None, "分页链接未通过安全校验，已停止跟进"
    return nxt, None


def _fetch_list(url: str, headers: dict, parser, *, paginate: bool) -> tuple[list[RemoteModel], list[str]]:
    """拉取（可同源分页）列表并解析；分页页数有上限，截断时明示（不当作完整成功）。"""
    models: list[RemoteModel] = []
    warnings: list[str] = []
    pages = 0
    current: str | None = url
    while current and pages < MAX_PAGES:
        payload = _get_json_once(current, headers)
        models.extend(parser(payload))
        pages += 1
        if not paginate:
            break
        current, warn = _next_page(payload, current, url)
        if warn:
            warnings.append(warn)
    if current and pages >= MAX_PAGES:
        warnings.append(_truncation_warning(MAX_PAGES))
    return models, warnings


def _fetch_anthropic(url: str, headers: dict) -> tuple[list[RemoteModel], list[str]]:
    """Anthropic 官方分页：has_more + after_id=last_id（有界，回到首页 URL 累加参数）。"""
    models: list[RemoteModel] = []
    warnings: list[str] = []
    current = url
    for _ in range(MAX_PAGES):
        payload = _get_json_once(current, headers)
        models.extend(parse_anthropic_models(payload))
        if not payload.get("has_more"):
            break
        last_id = str(payload.get("last_id") or "").strip()
        if not last_id:
            warnings.append("官方分页标识（last_id）缺失，已停止跟进，仅拉取当前页")
            break
        current = _with_query(url, {"after_id": last_id})
    else:
        warnings.append(_truncation_warning(MAX_PAGES))
    return models, warnings


def _fetch_alibaba(url: str, headers: dict) -> tuple[list[RemoteModel], list[str]]:
    """阿里官方分页：page_no 递增，直到取满 output.total（有界；截断明示）。"""
    models: list[RemoteModel] = []
    warnings: list[str] = []
    total = None
    for page in range(1, MAX_PAGES + 1):
        payload = _get_json_once(_with_query(url, {"page_no": page, "page_size": ALIBABA_PAGE_SIZE}), headers)
        page_models = parse_alibaba_models(payload)
        models.extend(page_models)
        output = payload.get("output") if isinstance(payload, dict) else None
        page_size = ALIBABA_PAGE_SIZE
        if isinstance(output, dict):
            total = _to_int(output.get("total")) or total
            page_size = _to_int(output.get("page_size")) or ALIBABA_PAGE_SIZE
        if total is not None:
            if len(models) >= total:
                break
            continue
        if len(page_models) < page_size:  # 无 total 信息时按「本页未满」兜底停止
            break
    else:
        warnings.append(_truncation_warning(MAX_PAGES))
    return models, warnings


# ---------------- 火山管控面 HMAC-SHA256 ----------------

def _q(value) -> str:
    """canonical query 的 RFC3986 编码（官方签名要求）。"""
    return urllib.parse.quote(str(value), safe="-_.~")


def volc_signature(
    method: str,
    path: str,
    query: dict,
    host: str,
    ak: str,
    sk: str,
    x_date: str,
    body_sha256: str,
    region: str = VOLC_REGION,
    service: str = VOLC_SERVICE,
) -> str:
    """火山引擎 OpenAPI 风格 HMAC-SHA256 签名（x_date 形如 20261026T120000Z）。

    canonical 请求固定包含：方法、路径、排序+编码后的 query、四段 CanonicalHeaders
    （content-type/host/x-content-sha256/x-date）、SignedHeaders 与请求体 SHA256。
    """
    canonical_query = "&".join(f"{_q(k)}={_q(v)}" for k, v in sorted(query.items()))
    canonical_headers = (
        f"content-type:application/json\n"
        f"host:{host}\n"
        f"x-content-sha256:{body_sha256}\n"
        f"x-date:{x_date}\n"
    )
    canonical_request = "\n".join([
        method.upper(),
        path or "/",
        canonical_query,
        canonical_headers,
        VOLC_SIGNED_HEADERS,
        body_sha256,
    ])
    credential_scope = f"{x_date[:8]}/{region}/{service}/request"
    string_to_sign = "\n".join([
        "HMAC-SHA256",
        x_date,
        credential_scope,
        hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
    ])
    k_date = hmac.new(sk.encode("utf-8"), x_date[:8].encode("utf-8"), hashlib.sha256).digest()
    k_region = hmac.new(k_date, region.encode("utf-8"), hashlib.sha256).digest()
    k_service = hmac.new(k_region, service.encode("utf-8"), hashlib.sha256).digest()
    k_signing = hmac.new(k_service, b"request", hashlib.sha256).digest()
    return hmac.new(k_signing, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()


def _volc_headers(ak: str, sk: str, host: str, query: dict, body: bytes) -> dict:
    """火山管控面请求头：AK 进 Authorization，SK 只参与签名计算（绝不外泄）。

    X-Content-Sha256 按**实际请求体**计算（空 {} 或 PageNumber/PageSize 分页体）。
    """
    x_date = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    body_sha256 = hashlib.sha256(body).hexdigest()
    signature = volc_signature("POST", "/", query, host, ak, sk, x_date, body_sha256)
    credential_scope = f"{x_date[:8]}/{VOLC_REGION}/{VOLC_SERVICE}/request"
    return {
        "Content-Type": "application/json",
        "Host": host,
        "X-Date": x_date,
        "X-Content-Sha256": body_sha256,
        "Authorization": (
            f"HMAC-SHA256 Credential={ak}/{credential_scope}, "
            f"SignedHeaders={VOLC_SIGNED_HEADERS}, Signature={signature}"
        ),
        "Accept": "application/json",
        "User-Agent": SYNC_USER_AGENT,
    }


def _volc_url(query: dict) -> str:
    return urllib.parse.urlunsplit(("https", VOLC_HOST, "/", urllib.parse.urlencode(query), ""))


def _volc_body(page_number: int | None = None) -> bytes:
    """只读 POST 请求体：无分页 → {}；分页 → 仅 PageNumber/PageSize 两个字段。"""
    if page_number is None:
        return VOLC_BODY
    return json.dumps({"PageNumber": page_number, "PageSize": VOLC_PAGE_SIZE}, separators=(",", ":")).encode("utf-8")


def _volc_post(ak: str, sk: str, query: dict, body: bytes) -> dict:
    headers = _volc_headers(ak, sk, VOLC_HOST, query, body)
    return _post_json(_volc_url(query), headers, body)


def _fetch_volc_foundation(ak: str, sk: str, query: dict) -> tuple[list[RemoteModel], list[str]]:
    """ListFoundationModels 官方分页：body 带 PageNumber，按 Result.TotalCount 收口。"""
    models: list[RemoteModel] = []
    warnings: list[str] = []
    total = None
    for page in range(1, MAX_PAGES + 1):
        payload = _volc_post(ak, sk, query, _volc_body(page))
        page_models = parse_volcengine_foundation_models(payload)
        models.extend(page_models)
        result = payload.get("Result") if isinstance(payload, dict) else None
        if isinstance(result, dict):
            total = _to_int(result.get("TotalCount")) or total
        if total is not None:
            if len(models) >= total:
                break
            continue
        if len(page_models) < VOLC_PAGE_SIZE:  # 无 TotalCount 时按「本页未满」兜底停止
            break
    else:
        warnings.append(_truncation_warning(MAX_PAGES))
    return models, warnings


def _fetch_volcengine(provider, credentials: dict, url: str) -> tuple[list[RemoteModel] | None, list[str]]:
    """火山：有 AK/SK 走官方管控面只读列表请求（ListFoundationModels 分页、Plan 单页）；
    无凭证返回 None + 明确 warning（不伪造在线结果）。

    发送前强制校验：官方 host、根路径、Action 白名单（三个只读列表 Action）。
    models_url 显式覆盖优先，但仍须通过同一道校验（禁止其他 Action/推理路径）。
    Version 强制为官方 2024-01-01（不吃 URL 里的其他版本）。
    """
    action = VOLC_ACTIONS.get(str(getattr(provider, "preset_key", "") or ""))
    if not action:
        return None, ["该火山套餐没有对应的官方管控列表 Action，本次仅按能力目录同步（非实时全量）"]
    ak = str((credentials or {}).get("access_key") or "")
    sk = str((credentials or {}).get("secret_key") or "")
    if not ak or not sk:
        return None, ["火山引擎未配置只读 AK/SK，无法调用官方管控面获取在线列表，本次仅按能力目录同步（非实时全量）"]

    target = (url or VOLC_ENDPOINT).strip()
    parts = urllib.parse.urlsplit(target)
    if (parts.hostname or "").lower() != VOLC_HOST:
        raise RemoteFetchError("火山管控面地址不在官方 Host 白名单内，已拒绝发起请求")
    if parts.path not in ("", "/"):
        raise RemoteFetchError("火山管控面仅允许根路径的列表 Action 请求，已拒绝发起请求")
    query = dict(urllib.parse.parse_qsl(parts.query, keep_blank_values=True))
    req_action = str(query.get("Action") or action).strip()
    if req_action not in VOLC_ALLOWED_ACTIONS:
        raise RemoteFetchError(f"非法的管控 Action（{req_action}），仅允许三个只读模型列表 Action")
    query["Action"] = req_action
    query["Version"] = VOLC_VERSION  # 强制官方版本（不用 setdefault：URL 可能带旧版本）

    if req_action == VOLC_FOUNDATION_ACTION:
        models, warnings = _fetch_volc_foundation(ak, sk, query)
    else:
        # Coding/Agent Plan：官方无明确分页字段 → 单页只读 POST（body {}），如实处理
        models = parse_volcengine_plan_models(_volc_post(ak, sk, query, _volc_body()))
        warnings = []
    if not models:
        raise RemoteFetchError("火山管控面返回空列表或结构不可识别")
    for m in models:
        m.source = "provider_api"
        m.source_url = _volc_url(query)
    return models, warnings


# ---------------- 在线列表入口 ----------------

def _auth_headers(adapter: str, api_key: str) -> dict:
    if adapter == "anthropic_models":
        return {"x-api-key": api_key, "anthropic-version": "2023-06-01"}
    return {"Authorization": f"Bearer {api_key}"}


def _prepare(adapter: str, url: str, label: str, credentials: dict) -> tuple[dict | None, list[str] | None]:
    """地址/密钥前置校验：返回 (headers, None) 或 (None, 目录兜底 warning)；非法 URL 抛错。"""
    if not url:
        return None, [f"「{label}」未配置模型列表地址，本次仅按能力目录同步（非实时全量）"]
    try:
        _check_http_url(url)
    except ValueError as exc:
        raise RemoteFetchError(f"模型列表地址未通过安全校验（{exc}），已拒绝发起请求")
    api_key = str((credentials or {}).get("api_key") or "")
    if not api_key:
        return None, [f"「{label}」未配置 API Key，无法获取在线列表，本次仅按能力目录同步（非实时全量）"]
    return {"Accept": "application/json", "User-Agent": SYNC_USER_AGENT, **_auth_headers(adapter, api_key)}, None


def fetch_remote_models(provider, credentials) -> tuple[list[RemoteModel] | None, list[str]]:
    """按预设适配器获取在线模型列表（只发目录/管控请求，绝不发推理请求）。

    返回 (models, warnings)：models 为 None 表示没有在线列表来源（未适配/未配置地址或凭证），
    调用方按能力目录兜底；在线失败抛 RemoteFetchError（绝不返回空列表冒充成功）。
    """
    preset = preset_by_key(getattr(provider, "preset_key", "") or "") or {}
    adapter = str(preset.get("sync_adapter") or "none")
    label = str(preset.get("label") or getattr(provider, "preset_key", "") or "自定义供应商")
    url = str(getattr(provider, "models_url", "") or preset.get("models_url") or "").strip().rstrip("/")
    credentials = credentials or {}

    if adapter == "volcengine_ak_sk":
        return _fetch_volcengine(provider, credentials, url)
    if adapter not in _FETCHERS and adapter not in ("alibaba_models", "anthropic_models"):
        return None, [f"「{label}」没有官方模型列表接口，本次仅按能力目录同步（非实时全量）"]

    headers, early = _prepare(adapter, url, label, credentials)
    if early is not None:
        return None, early
    if adapter == "alibaba_models":
        models, warnings = _fetch_alibaba(url, headers)
    elif adapter == "anthropic_models":
        models, warnings = _fetch_anthropic(url, headers)
    else:
        parser, paginate = _FETCHERS[adapter]
        models, warnings = _fetch_list(url, headers, parser, paginate=paginate)
    if not models:
        raise RemoteFetchError("在线模型列表为空或响应结构不可识别")
    for m in models:
        m.source = "provider_api"
        m.source_url = url
    return models, warnings


# ---------------- 合并与过滤 ----------------

def _catalog_models(preset_key: str, protocol: str) -> list[RemoteModel]:
    """能力目录候选 → RemoteModel 列表（source=official_catalog，非在线）。"""
    if not preset_key:
        return []
    models = model_catalog.catalog_group(preset_key)
    source_url = str(model_catalog.catalog_group_info(preset_key).get("source_url") or "")
    family = "messages" if str(protocol or "").lower() == "anthropic" else "chat_completions"
    out: list[RemoteModel] = []
    for mid, entry in models.items():
        if not isinstance(entry, dict):
            continue
        levels = entry.get("thinking_levels") if isinstance(entry.get("thinking_levels"), list) else None
        out.append(RemoteModel(
            model=str(mid)[:120],
            display_name=str(entry.get("display_name") or "")[:120],
            context_size=_to_int(entry.get("context_size")),
            max_input_tokens=_to_int(entry.get("max_input_tokens")),
            max_tokens=_to_int(entry.get("max_tokens")),
            input_modalities=_modalities(entry.get("input_modalities")),
            output_modalities=_modalities(entry.get("output_modalities")),
            supports_thinking=_tri_state(entry.get("supports_thinking")),
            thinking_levels=[str(x)[:20] for x in levels][:12] if levels else None,
            default_thinking_level=str(entry.get("default_thinking_level") or "") or None,
            thinking_can_disable=entry.get("thinking_can_disable") if isinstance(entry.get("thinking_can_disable"), bool) else None,
            supports_tools=_tri_state(entry.get("supports_tools")),
            supports_vision=entry.get("supports_vision") if isinstance(entry.get("supports_vision"), bool) else None,
            api_family=family,
            status=str(entry.get("status") or "")[:30],
            source="official_catalog",
            source_url=source_url,
        ))
    return out


def _merge_entry(rm: RemoteModel, preset_key: str, protocol: str) -> dict:
    """目录条目打底，远端字段逐项覆盖（在线结果优先）；status 归入 remote_status。"""
    entry: dict = {}
    catalog = model_catalog.catalog_for(preset_key, rm.model) if preset_key else None
    if catalog:
        entry.update({k: v for k, v in catalog.items() if v not in (None, "")})
    remote_entry = rm.capability_entry()
    status = str(remote_entry.pop("status", "") or entry.pop("status", "") or "")
    for key, value in remote_entry.items():
        if value not in (None, "", []):
            entry[key] = value
    if status:
        entry["remote_status"] = status[:30]
    return entry


def _is_non_chat_id(model_id: str) -> bool:
    """嵌入/语音/图像生成类模型 ID 判定：完整 ID 或最后一个 `/` 后末段命中前缀均算。

    厂商段形状（OpenRouter `vendor/model`，如 `openai/text-embedding-3-small`）只按
    完整 ID startswith 会漏过，故末段同样参与前缀判定；前缀语义，不做子串误伤。
    """
    mid = str(model_id).lower()
    tail = mid.rsplit("/", 1)[-1]
    return any(mid.startswith(p) or tail.startswith(p) for p in _NON_CHAT_PREFIXES)


def _skip_reason(rm: RemoteModel, provider, include_disabled: bool, status: str = "") -> str:
    """同步前的过滤决策（返回空串 = 同步）；未知/不兼容协议绝不默认放行。

    status 取合并后的状态（在线优先、目录补齐）：任一侧标注下线/停用即按 include_disabled 处理。
    """
    status = str(status or rm.status or "").strip().lower()
    if status in _DISABLED_STATUSES and not include_disabled:
        return f"官方状态为 {status}（已下线/停用），include_disabled=false 未同步"
    family = str(rm.api_family or "").strip().lower()
    protocol = str(getattr(provider, "protocol", "") or "openai").strip().lower()
    expected = "messages" if protocol == "anthropic" else "chat_completions"
    if not family or family == UNKNOWN_API_FAMILY:
        return "无法确认协议兼容（api_family 未知），不默认按 chat_completions 同步"
    if family != expected:
        return f"协议不兼容（api_family={family}，该供应商对话入口为 {expected}）"
    in_mod = [str(x).lower() for x in (rm.input_modalities or [])]
    if in_mod and "text" not in in_mod:
        return f"非文本对话模型（输入模态={rm.input_modalities}）"
    out_mod = [str(x).lower() for x in (rm.output_modalities or [])]
    if out_mod and "text" not in out_mod:
        return f"非文本对话模型（输出模态={rm.output_modalities}）"
    if _is_non_chat_id(rm.model):
        return "非文本对话模型（官方列表中的嵌入/语音/图像类模型）"
    return ""


def _signature(pm) -> tuple:
    """能力相关状态的签名（不含 sources/last_sync/synced_at）：用于 unchanged 判定。"""
    meta = model_catalog.capability_meta_of(pm)
    return (
        pm.context_size, pm.max_input_tokens, pm.max_tokens,
        pm.supports_thinking, pm.supports_tools,
        bool(pm.multimodal), bool(pm.supports_vision), pm.thinking_levels,
        tuple(meta["input_modalities"]), tuple(meta["output_modalities"]),
        meta["default_thinking_level"], meta["thinking_can_disable"],
        meta["reasoning_style"], meta["api_family"], meta["remote_status"],
    )


# ---------------- 同步（provider lock + 单事务 upsert） ----------------

_LOCKS: dict[int, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _lock_for(provider_id: int) -> threading.Lock:
    with _LOCKS_GUARD:
        lock = _LOCKS.get(provider_id)
        if lock is None:
            lock = threading.Lock()
            _LOCKS[provider_id] = lock
        return lock


def sync_provider_models(db, provider_id, include_disabled: bool = False, override_manual: bool = False) -> dict:
    """同步某供应商的模型列表：在线适配器优先，失败/无来源时能力目录兜底（如实标注）。

    - 同一供应商并发同步抛 ProviderSyncBusy（端点映射 409）；
    - 批内按模型 ID 去重；单事务提交；一条非法记录或写入异常整批回滚
      （结构化 ok=false，不裸 500、不留下半批）；
    - 远端消失不删本地行；manual/probe/note/temperature/enabled/active 不被覆盖；
    - 连续两次同步第二次全部 unchanged（不重复写 synced_at）。
    """
    pid = int(provider_id)
    prov = db.get(ModelProvider, pid)
    if prov is None:
        raise ValueError(f"供应商 {pid} 不存在")
    lock = _lock_for(pid)
    if not lock.acquire(blocking=False):
        raise ProviderSyncBusy(f"供应商「{prov.name}」正在同步中，请稍候")
    started = datetime.now().isoformat(timespec="seconds")
    try:
        preset_key = str(prov.preset_key or "")
        try:
            api_key = unseal(prov.api_key) if prov.api_key else ""
        except Exception:
            logger.warning("供应商密钥解封失败，按未配置处理（不记录任何密钥内容）", exc_info=True)
            api_key = ""
        credentials = {**_read_sync_credentials(prov.sync_credentials), "api_key": api_key}

        online = False
        warnings: list[str] = []
        errors: list[str] = []
        truncated = False
        try:
            remotes, warnings = fetch_remote_models(prov, credentials)
            online = remotes is not None
            truncated = any(w.startswith(TRUNCATION_MARK) for w in warnings)
        except RemoteFetchError as exc:
            errors.append(str(exc))
            warnings.append("在线列表获取失败，本次仅按能力目录同步（非实时全量）")
        except SyncRecordError as exc:
            db.rollback()
            return {
                "ok": False, "partial": False, "provider_id": pid, "source": "", "online": False,
                "added": 0, "updated": 0, "unchanged": 0, "skipped": [], "warnings": [], "truncated": False,
                "errors": [f"在线列表包含非法记录，已整批拒绝：{exc}"], "synced_at": started,
            }
        except Exception as exc:  # 适配器未预期缺陷：结构化失败兜底（只记异常类型，防裸 500）
            db.rollback()
            logger.warning("在线列表获取出现未预期异常，已整批拒绝：%s", type(exc).__name__, exc_info=True)
            return {
                "ok": False, "partial": False, "provider_id": pid, "source": "", "online": False,
                "added": 0, "updated": 0, "unchanged": 0, "skipped": [], "warnings": [], "truncated": False,
                "errors": [f"在线列表解析失败，已整批拒绝（{type(exc).__name__}）"], "synced_at": started,
            }

        source = "provider_api" if online else "official_catalog"
        if not online:
            remotes = _catalog_models(preset_key, prov.protocol)
            if not remotes:
                warnings.append("该供应商没有可用的能力目录，本次没有同步任何模型")

        existing = {
            m.model: m
            for m in db.query(ProviderModel).filter(ProviderModel.provider_id == pid).all()
        }
        added = updated = unchanged = 0
        skipped: list[dict] = []
        seen: set[str] = set()
        try:
            for rm in remotes or []:
                mid = rm.model
                entry = _merge_entry(rm, preset_key, prov.protocol)
                reason = _skip_reason(rm, prov, include_disabled, status=str(entry.get("remote_status") or ""))
                if reason:
                    skipped.append({"model": mid, "reason": reason})
                    continue
                if mid in seen:
                    skipped.append({"model": mid, "reason": "同一批列表中出现重复模型 ID，已保留首条"})
                    continue
                seen.add(mid)
                pm = existing.get(mid)
                if pm is None:
                    pm = ProviderModel(provider_id=pid, model=mid, context_size=128000, max_tokens=2048)
                    db.add(pm)
                    db.flush()
                    existing[mid] = pm
                    model_catalog.apply_capability_entry(pm, entry, source=source, override_manual=override_manual)
                    added += 1
                    continue
                before = _signature(pm)
                prev_meta, prev_synced = pm.capability_meta, pm.synced_at
                model_catalog.apply_capability_entry(pm, entry, source=source, override_manual=override_manual)
                if _signature(pm) == before:
                    # 幂等：能力无变化时整体还原（不重复写 last_sync/synced_at）
                    pm.capability_meta, pm.synced_at = prev_meta, prev_synced
                    unchanged += 1
                else:
                    updated += 1
            db.commit()
        except Exception as exc:  # 写入异常（DB 错误/意外 bug）：整批回滚，结构化失败
            db.rollback()
            logger.warning("模型同步写入失败，已整批回滚：%s", type(exc).__name__, exc_info=True)
            return {
                "ok": False, "partial": not online, "provider_id": pid, "source": source,
                "online": online, "added": 0, "updated": 0, "unchanged": 0, "skipped": [],
                "warnings": warnings, "truncated": truncated,
                "errors": [f"同步写入失败，已整批回滚（{type(exc).__name__}）"],
                "synced_at": started,
            }
        return {
            "ok": True, "partial": not online, "provider_id": pid, "source": source,
            "online": online, "added": added, "updated": updated, "unchanged": unchanged,
            "skipped": skipped, "warnings": warnings, "truncated": truncated,
            "errors": errors, "synced_at": started,
        }
    finally:
        lock.release()
