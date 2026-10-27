"""模型能力目录与统一能力契约（Task 3：AI 助手智能路由与多供应商模型能力同步）。

- `RemoteModel`：远端/目录模型能力的归一化 dataclass（同步适配器与目录的共同契约）；
- `load_capability_catalog()`：读 `model_capabilities.json`（{catalog_key: {model_id: entry}}）；
- `catalog_for(preset_key, model_id)`：按预设取目录能力（预设无 catalog_key → None）；
- `capability_meta_of(row)`：解析 `ProviderModel.capability_meta`（含默认值与容错）；
- `apply_capability_entry(pm, entry, ...)`：把目录/远端能力合并进模型行，
  `manual_fields` 内的字段默认不被覆盖（`override_manual=True` 才覆盖），
  probe 字段与 note/temperature/enabled 永不受影响（Task 4 同步同款入口）；
- `reset_manual_overrides(pm)`：清除 manual_fields 并应用最近一次同步快照。

未知即未知：没有模态字段时不从模型名（如 "VL"）猜视觉/多模态；目录未收录的字段一律缺失。
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

# 能力档位/能力的三态约定：-1 未知 / 0 否 / 1 是（与 supports_thinking 列一致）
UNKNOWN = -1

CATALOG_PATH = Path(__file__).resolve().parent / "model_capabilities.json"

# 目录条目里可被合并进模型行的字段 → 落点（列 or capability_meta 键）
_COLUMN_FIELDS = {
    "context_size": "context_size",
    "max_input_tokens": "max_input_tokens",
    "max_tokens": "max_tokens",
    "supports_thinking": "supports_thinking",
    "supports_tools": "supports_tools",
    "multimodal": "multimodal",
    "supports_vision": "supports_vision",
}
_META_FIELDS = (
    "input_modalities",
    "output_modalities",
    "default_thinking_level",
    "thinking_can_disable",
    "reasoning_style",
    "api_family",
    "remote_status",
)


@dataclass(slots=True)
class RemoteModel:
    """远端/目录模型能力归一化契约（字段与计划文件第 2 节一致）。"""

    model: str
    display_name: str = ""
    context_size: int | None = None
    max_input_tokens: int | None = None
    max_tokens: int | None = None
    input_modalities: list[str] | None = None
    output_modalities: list[str] | None = None
    supports_thinking: int = -1
    thinking_levels: list[str] | None = None
    default_thinking_level: str | None = None
    thinking_can_disable: bool | None = None
    supports_tools: int = -1
    supports_vision: bool | None = None
    api_family: str = "chat_completions"
    status: str = ""
    source: str = "provider_api"
    source_url: str = ""

    def capability_entry(self) -> dict:
        """转为目录条目 dict（仅含有值字段；未知不写）。"""
        entry: dict = {}
        for f in (
            "display_name", "context_size", "max_input_tokens", "max_tokens",
            "input_modalities", "output_modalities", "thinking_levels",
            "default_thinking_level", "thinking_can_disable", "api_family", "status",
        ):
            v = getattr(self, f)
            if v not in (None, "", []):
                entry[f] = v
        if self.supports_thinking != UNKNOWN:
            entry["supports_thinking"] = self.supports_thinking
        if self.supports_tools != UNKNOWN:
            entry["supports_tools"] = self.supports_tools
        if self.supports_vision is not None:
            entry["supports_vision"] = self.supports_vision
        entry["source"] = self.source
        if self.source_url:
            entry["source_url"] = self.source_url
        return entry


def derive_supports_vision(input_modalities) -> bool | None:
    """视觉能力只由明确输入模态推导：含 image → True；有模态列表但不含 → False；无列表 → None。"""
    if not input_modalities:
        return None
    return "image" in [str(m).lower() for m in input_modalities]


def derive_multimodal(input_modalities) -> bool | None:
    """多模态 = 输入模态存在非 text；无模态字段 → None（未知）。"""
    if not input_modalities:
        return None
    return any(str(m).lower() != "text" for m in input_modalities)


_CATALOG_CACHE: dict | None = None
_CATALOG_MTIME: float | None = None


def load_capability_catalog(*, reload: bool = False) -> dict:
    """加载能力目录；文件缺失/损坏返回空目录（记警告，不影响启动）。

    带 mtime 缓存：改 JSON 后下次调用自动重载（预设 reload 同款语义）。
    """
    global _CATALOG_CACHE, _CATALOG_MTIME
    try:
        mtime = CATALOG_PATH.stat().st_mtime
    except OSError:
        if _CATALOG_CACHE is not None and not reload:
            return _CATALOG_CACHE
        logger.warning("能力目录文件缺失：%s", CATALOG_PATH)
        _CATALOG_CACHE, _CATALOG_MTIME = {}, None
        return {}
    if reload or _CATALOG_CACHE is None or mtime != _CATALOG_MTIME:
        try:
            raw = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
            catalogs = raw.get("catalogs") if isinstance(raw, dict) else {}
            if not isinstance(catalogs, dict):
                raise ValueError("catalogs 结构非法")
            _CATALOG_CACHE = catalogs
            _CATALOG_MTIME = mtime
        except Exception:
            logger.warning("能力目录解析失败：%s", CATALOG_PATH, exc_info=True)
            if _CATALOG_CACHE is None:
                _CATALOG_CACHE, _CATALOG_MTIME = {}, None
    return _CATALOG_CACHE or {}


def reload_capability_catalog() -> dict:
    """强制重载能力目录（配置界面「重载」用）。"""
    return load_capability_catalog(reload=True)


def _catalog_key_for(preset_key: str) -> str:
    """预设 → 目录键。

    区分两种「没有目录」：预设缺 catalog_key 字段 → 回退 preset_key 自身；
    显式空串（catalog_key=""）= 有意不配目录 → 返回空串，调用方按无目录处理
    （不得回退成 preset_key 把别的组误当成它的目录）。
    """
    try:
        from . import config_store  # 延迟导入避免循环依赖

        for p in config_store.load_presets():
            if p.get("preset_key") == preset_key:
                raw = p.get("catalog_key")
                if raw is None:
                    return preset_key
                return str(raw)[:50]
    except Exception:
        logger.warning("预设加载失败，catalog_for 按 preset_key 直查目录", exc_info=True)
    return preset_key


def catalog_group(preset_key: str) -> dict:
    """某预设的目录模型表 {model_id: entry}；无目录/预设不存在返回 {}。"""
    if not preset_key:
        return {}
    key = _catalog_key_for(preset_key)
    if not key:
        return {}
    group = (load_capability_catalog().get(key) or {})
    models = group.get("models") if isinstance(group, dict) else None
    return models if isinstance(models, dict) else {}


def catalog_group_info(preset_key: str) -> dict:
    """某预设的目录组元信息（source/source_url/verified_on）；无目录返回 {}。"""
    if not preset_key:
        return {}
    key = _catalog_key_for(preset_key)
    if not key:
        return {}
    group = load_capability_catalog().get(key)
    return group if isinstance(group, dict) else {}


def catalog_for(preset_key: str, model_id: str) -> dict | None:
    """取某预设下某模型 ID 的目录能力；未收录返回 None（调用方按未知处理）。"""
    if not preset_key or not model_id:
        return None
    entry = catalog_group(preset_key).get(model_id)
    return dict(entry) if isinstance(entry, dict) else None


def capability_meta_of(row) -> dict:
    """解析 ProviderModel.capability_meta JSON；空/损坏按默认值返回（绝不抛错）。

    未知键原样保留：Task 4 可能新增同步状态字段（如 sync_state/remote_etag），
    手动保存不得把它们抹掉。
    """
    raw = getattr(row, "capability_meta", "") or ""
    try:
        data = json.loads(raw) if raw.strip() else {}
    except Exception:
        logger.warning("capability_meta JSON 损坏，按默认值处理", exc_info=True)
        data = {}
    if not isinstance(data, dict):
        data = {}
    # 默认值每次现构造（避免模块级可变对象被调用方改坏）
    meta = {
        "sources": {},
        "manual_fields": [],
        "input_modalities": [],
        "output_modalities": [],
        "default_thinking_level": None,
        "thinking_can_disable": None,
        "reasoning_style": "",
        "api_family": "",
        "remote_status": "",
        "last_sync": None,
    }
    # 已知键归一化（含类型守卫）
    meta["sources"] = data["sources"] if isinstance(data.get("sources"), dict) else {}
    meta["manual_fields"] = list(data.get("manual_fields") or [])
    meta["input_modalities"] = list(data.get("input_modalities") or [])
    meta["output_modalities"] = list(data.get("output_modalities") or [])
    meta["default_thinking_level"] = data.get("default_thinking_level")
    meta["thinking_can_disable"] = data.get("thinking_can_disable")
    meta["reasoning_style"] = str(data.get("reasoning_style") or "")
    meta["api_family"] = str(data.get("api_family") or "")
    meta["remote_status"] = str(data.get("remote_status") or "")
    meta["last_sync"] = data.get("last_sync") if isinstance(data.get("last_sync"), dict) else None
    # 未知键原样保留
    for key, value in data.items():
        if key not in meta:
            meta[key] = value
    return meta


def _set_field(pm, name: str, value):
    """按字段名写列（multimodal/supports_vision 为布尔列，其余直写）。"""
    if name == "multimodal":
        pm.multimodal = bool(value)
    elif name == "supports_vision":
        pm.supports_vision = bool(value)
    else:
        setattr(pm, name, value)


def apply_capability_entry(pm, entry: dict, *, source: str = "official_catalog", override_manual: bool = False) -> dict:
    """把目录/远端能力合并进模型行（Task 4 同步与 reset-overrides 共用）。

    - `manual_fields` 内的字段默认跳过（`override_manual=True` 才覆盖）；
    - 条目未提供的字段一律不写（未知保持未知）；
    - probe_* 与 note/temperature/enabled 永不受影响；
    - 返回 {字段: "applied"|"skipped_manual"} 供调用方汇报。
    """
    if not isinstance(entry, dict):
        return {}
    meta = capability_meta_of(pm)
    manual_fields = set(meta["manual_fields"])
    sources = dict(meta["sources"])
    result: dict[str, str] = {}

    # 模态派生（只基于明确模态字段；无模态字段则两个派生值都不写）
    in_mod = entry.get("input_modalities")
    derived = {}
    if in_mod:
        derived = {"multimodal": derive_multimodal(in_mod), "supports_vision": derive_supports_vision(in_mod)}

    for name, value in list(entry.items()) + list(derived.items()):
        if name not in _COLUMN_FIELDS and name not in _META_FIELDS:
            continue
        if value is None or value == "":
            continue
        if name in manual_fields and not override_manual:
            result[name] = "skipped_manual"
            continue
        if name in _COLUMN_FIELDS:
            _set_field(pm, name, value)
        else:
            meta[name] = value
        sources[name] = source
        result[name] = "applied"

    # 思考档位：列存 JSON 文本，meta 不重复存
    levels = entry.get("thinking_levels")
    if levels:
        if "thinking_levels" in manual_fields and not override_manual:
            result["thinking_levels"] = "skipped_manual"
        else:
            pm.thinking_levels = json.dumps([str(x)[:20] for x in levels][:12], ensure_ascii=False)
            sources["thinking_levels"] = source
            result["thinking_levels"] = "applied"

    # 远端状态（在线状态优先、目录状态补齐；任一来源给出状态即写入）
    if entry.get("remote_status"):
        meta["remote_status"] = str(entry["remote_status"])
        sources["remote_status"] = source
        result["remote_status"] = "applied"

    meta["sources"] = sources
    if override_manual:
        # 显式覆盖后这些字段不再是「手动」值
        meta["manual_fields"] = [f for f in meta["manual_fields"] if f not in result or result[f] != "applied"]
    meta["last_sync"] = {
        "at": datetime.now().isoformat(timespec="seconds"),
        "source": source,
        "snapshot": _snapshot_of(entry),
    }
    pm.capability_meta = json.dumps(meta, ensure_ascii=False)
    pm.synced_at = datetime.now().isoformat(timespec="seconds")
    return result


def _snapshot_of(entry: dict) -> dict:
    """最近一次同步快照：只存可回放的字段（reset-overrides 用）。"""
    snap = {}
    for name in list(_COLUMN_FIELDS) + list(_META_FIELDS) + ["thinking_levels"]:
        if name == "thinking_levels":
            if entry.get("thinking_levels"):
                snap["thinking_levels"] = list(entry["thinking_levels"])
            continue
        v = entry.get(name)
        if v is not None and v != "":
            snap[name] = v
    in_mod = entry.get("input_modalities")
    if in_mod:
        snap["multimodal"] = derive_multimodal(in_mod)
        snap["supports_vision"] = derive_supports_vision(in_mod)
    return snap


def reset_manual_overrides(pm) -> dict:
    """清除 manual_fields 并应用最近一次同步快照；无快照仅清标记。

    返回 {"cleared": [...], "applied": bool, "synced_at": str|None, "message": str}。
    """
    meta = capability_meta_of(pm)
    cleared = list(meta["manual_fields"])
    meta["manual_fields"] = []
    last_sync = meta.get("last_sync") or {}
    snapshot = last_sync.get("snapshot") or {}
    prev_synced_at = pm.synced_at or None
    applied = False
    if snapshot:
        # 先落「manual_fields 已清空」，快照回放时这些字段才不被跳过
        pm.capability_meta = json.dumps(meta, ensure_ascii=False)
        apply_capability_entry(pm, snapshot, source=str(last_sync.get("source") or "official_catalog"))
        pm.synced_at = prev_synced_at or ""  # reset 不改变「最近一次同步」时间
        applied = True
        message = "已清除手动覆盖并按最近一次同步快照恢复能力"
    else:
        pm.capability_meta = json.dumps(meta, ensure_ascii=False)
        message = "已清除手动覆盖标记；尚无同步快照，下次同步后生效"
    return {"cleared": cleared, "applied": applied, "synced_at": prev_synced_at, "message": message}
