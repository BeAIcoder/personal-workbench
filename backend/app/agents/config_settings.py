"""Agent 全局工作参数与生效配置组装（自 config_store.py 机械拆出）。

职责边界：DEFAULTS、agent_settings 的工作参数（工具轮次/记忆/技能池/Bash/插件/
思考档位/路由模型）、激活模型解析，以及 load() / model_cfg_by_id() 的配置组装。
供应商与模型的 CRUD 在 config_providers.py；密钥掩码在 config_secrets.py。
"""
import logging
from datetime import datetime

from sqlalchemy.exc import OperationalError

from ..config import settings
from ..database import SessionLocal
from ..models import AgentSettings, ModelProvider, ProviderModel
from .config_providers import (
    _active_model,
    _json_list,
    _model_capability_view,
    _parse_temperature,
    _row,
)
from .secrets_box import unseal

logger = logging.getLogger(__name__)

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
    # 路由仲裁模型（演进第二批）：None = 跟随全局激活模型；int = provider_models.id
    "routing_model_id": None,
}

# 模型级字段（供应商/模型两表承载）
MODEL_FIELDS = ("protocol", "base_url", "api_key", "model", "context_size", "max_tokens", "multimodal")

# 思考深度可选值（save_globals 白名单；与 llm._THINKING_LEVELS 同口径，此处不得 import llm）
# '' = 跟随模型；off = 旧版「关闭思考」（保留兼容，语义同 none）；其余 = canonical 档位
_REASONING_EFFORT_VALUES = frozenset({"", "off", "none", "minimal", "low", "medium", "high", "xhigh", "max"})


def load() -> dict:
    """读取当前生效配置。

    优先级：激活模型（供应商+模型）> .env 兜底；
    Agent 工作参数（max_iters/记忆）始终取 agent_settings。
    """
    from .config_store import preset_by_key  # 预设唯一源在 facade（PRESETS 可被 reload 与测试重绑）
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
                "memory_inject_max": row.memory_inject_max,
                "enable_skills": row.enable_skills,
                "skills_dir": row.skills_dir or "",
                "enable_bash": row.enable_bash,
                "enable_plugins": row.enable_plugins,
                "plugins_dir": row.plugins_dir or "",
                "reasoning_effort": row.reasoning_effort or "",
                "routing_model_id": row.routing_model_id,
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
    from .config_store import preset_by_key  # 预设唯一源在 facade（PRESETS 可被 reload 与测试重绑）
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
        if "memory_inject_max" in data:
            raw = data.get("memory_inject_max")
            raw = 3 if raw is None else raw
            row.memory_inject_max = max(0, min(int(raw), 10))
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
        if "routing_model_id" in data:
            raw = data.get("routing_model_id")
            # None/非正数/非法值 = 跟随全局激活模型；正整数 = 指定 provider_models.id
            # （存在性由取用方兜底，不可用时 _get_routing_model 自动回退全局激活模型并告警）
            try:
                rid = int(raw)
            except (TypeError, ValueError):
                rid = 0
            row.routing_model_id = rid if rid > 0 else None
        row.updated_at = datetime.now()
        db.commit()


def current_api_key() -> str:
    """当前生效配置中的 API Key（供测试时回填掩码哨兵）。"""
    return load().get("api_key", "")
