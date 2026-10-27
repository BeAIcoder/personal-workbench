"""编排器：路由 → 专家 Agent 执行 → 轨迹与会话持久化。

路由策略：用户指定 > 高置信关键词 > 低置信纯文本 LLM 仲裁 > 弱关键词候选/默认（商业地产管家）。
Agent 实例按 (会话, 专家, 模式) 缓存在进程内；会话消息持久化到 SQLite，
进程重启后按历史重建会话上下文。模型实例默认按模型共享缓存，官方要求会话头的
供应商（OpenCode Go 等）按 (模型, 会话) 维度缓存，保证会话头值稳定。

工作模式（借鉴 QwenPaw Loop 模式）：
- standard 标准执行：全部工具，自动放行
- readonly 只读咨询：仅查询工具，不改任何数据
- deep    深度研究：全部工具 + 更高工具轮次 + 深度分析提示词
"""
import asyncio
import base64
import json
import logging
import re
from collections import OrderedDict
from datetime import datetime
from pathlib import Path

from sqlalchemy import func
from sqlalchemy.exc import OperationalError

from ..database import SessionLocal
from ..models import AgentSession, ChatMessage
from . import config_store
from .llm import build_model, llm_status, needs_session_headers
from .team import (
    COMMON_RULES,
    QUERY_TOOLS,
    TEAM,
    AgentSpec,
    best_keyword_candidate,
    default_agent_name,
    load_team,
    normalize_route_text,
    roster_text,
    route_scores,
)
from .tools import TOOL_SPECS, build_tools, tool_trace

logger = logging.getLogger(__name__)

_model = None
_models: dict[int, object | None] = {}  # 共享模型缓存：model_id → 模型实例（None 表示不可用）
# 要求会话头的供应商（OpenCode Go 等）按 (model_id|0, session_id) 维度缓存；
# model_id=0 表示全局激活模型。LRU 上限与会话 Agent 缓存同口径，淘汰即释放实例（含连接池）。
_SESSION_MODEL_CACHE_MAX = 64
_session_models: "OrderedDict[tuple[int, str], object | None]" = OrderedDict()
_session_model_ids: set[int] = set()
# 全局激活模型所属供应商是否要求会话头（首次构造时判定，invalidate 重置）
_model_needs_session = False
# 各会话 Agent 缓存（LRU：上限 64 个，超出淘汰最久未用，避免长驻服务内存无限增长）
_AGENT_CACHE_MAX = 64
_agents: "OrderedDict[tuple[str, str, str, int], object]" = OrderedDict()
_agent_locks: dict[tuple[str, str, str, int], asyncio.Lock] = {}


def _session_cache_put(key: tuple[int, str], model: object | None) -> None:
    """按会话维度的模型缓存（LRU）：淘汰最久未用会话，同步释放其模型实例。"""
    _session_models[key] = model
    _session_models.move_to_end(key)
    while len(_session_models) > _SESSION_MODEL_CACHE_MAX:
        _session_models.popitem(last=False)


def _session_cache_get(key: tuple[int, str]):
    """读取按会话缓存的模型实例；命中（含缓存的 None）即刷新新近度（真 LRU，非 FIFO）。"""
    if key in _session_models:
        _session_models.move_to_end(key)
    return _session_models[key]


def _agent_cache_put(key: tuple[str, str, str, int], agent: object) -> None:
    _agents[key] = agent
    _agents.move_to_end(key)
    while len(_agents) > _AGENT_CACHE_MAX:
        evicted, _ = _agents.popitem(last=False)
        _agent_locks.pop(evicted, None)  # 构建锁随淘汰一并清理，避免 _agent_locks 无限增长

# Agent 工作模式
WORK_MODES = {
    "standard": {"label": "标准执行", "hint": "全部工具，自动执行"},
    "readonly": {"label": "只读咨询", "hint": "仅查询分析，不修改任何数据"},
    "deep": {"label": "深度研究", "hint": "更高工具轮次，多角度深入分析"},
}

DEEP_SUFFIX = "\n\n【当前模式：深度研究】请进行更全面深入的多步分析：先调用查询工具核实工作台数据，再从多个角度交叉验证与拆解，最后给出结构化的结论、依据与建议。"
READONLY_SUFFIX = "\n\n【当前模式：只读咨询】本次仅提供分析与建议，不修改任何工作台数据（写入类工具已对你关闭）。"


def parse_route_reply(text: str, team: dict[str, AgentSpec]) -> str | None:
    """从纯文本回复中解析名册内唯一专家；接受精确 name/label 与 JSON 代码块。"""
    normalized = normalize_route_text(text or "").strip()
    if not normalized:
        return None

    aliases: dict[str, set[str]] = {}
    for name, spec in team.items():
        for value in (name, spec.label):
            alias = normalize_route_text(value).strip()
            if alias:
                aliases.setdefault(alias, set()).add(name)

    def _exact_names(value: str) -> set[str]:
        return aliases.get(normalize_route_text(value).strip(), set())

    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", normalized, re.DOTALL)
    candidate_text = (fenced.group(1) if fenced else normalized).strip()
    exact = _exact_names(candidate_text)
    if len(exact) == 1:
        return next(iter(exact))

    try:
        payload = json.loads(candidate_text)
    except (json.JSONDecodeError, TypeError):
        payload = None
    if isinstance(payload, dict):
        selected: set[str] = set()
        identity_values = [payload[key] for key in ("agent", "name", "label") if isinstance(payload.get(key), str)]
        if not identity_values:
            return None
        for value in identity_values:
            names = _exact_names(value)
            if len(names) != 1:
                return None
            selected.update(names)
        return next(iter(selected)) if len(selected) == 1 else None
    if isinstance(payload, str):
        names = _exact_names(payload)
        return next(iter(names)) if len(names) == 1 else None

    selected = set()
    for alias, names in aliases.items():
        pattern = re.escape(alias)
        if alias[0].isascii() and alias[0].isalnum():
            pattern = rf"(?<![a-z0-9_]){pattern}"
        if alias[-1].isascii() and alias[-1].isalnum():
            pattern = rf"{pattern}(?![a-z0-9_])"
        if re.search(pattern, candidate_text):
            selected.update(names)
    return next(iter(selected)) if len(selected) == 1 else None


def invalidate() -> None:
    """配置变更后清空进程内缓存（模型实例与各会话 Agent）。"""
    global _model, _model_needs_session
    _model = None
    _model_needs_session = False
    _models.clear()
    _session_models.clear()
    _session_model_ids.clear()
    _agents.clear()
    _agent_locks.clear()


def _get_model(session_id: str | None = None):
    """全局激活模型（进程内共享缓存）。

    供应商要求会话头时按 session 维度缓存（LRU 有上限，命中刷新新近度，淘汰即释放实例）；
    其余供应商沿用共享实例。session_id 缺省时共享实例用稳定会话头值（LLM 路由等内部
    调用保持稳定即可）；该实例首次可能由某个会话调用构造，头值即该会话的稳定值。
    """
    global _model, _model_needs_session
    if _model is None:
        cfg = config_store.load()
        _model = build_model(cfg)
        _model_needs_session = needs_session_headers(cfg)
    if _model is None or not _model_needs_session or session_id is None:
        return _model
    key = (0, str(session_id))
    if key not in _session_models:
        _session_cache_put(key, build_model(config_store.load(), session_id=session_id))
    return _session_cache_get(key)


def _get_spec_model(spec: AgentSpec, session_id: str | None = None):
    """专家生效模型：绑定 model_id 时按其配置构建（进程内缓存），为空/不可用回退全局激活模型。

    需要会话头的供应商（OpenCode Go 等）按 (model_id, session_id) 维度缓存（LRU 有上限），
    保证同一会话内会话头稳定；其余供应商沿用共享缓存（model_id 维度）。
    """
    if spec.model_id:
        if spec.model_id in _session_model_ids and session_id is not None:
            key = (spec.model_id, str(session_id))
            if key not in _session_models:
                cfg = config_store.model_cfg_by_id(spec.model_id)
                model = build_model(cfg, session_id=session_id) if cfg else None
                _session_cache_put(key, model)
                if model is None:
                    logger.warning("专家 %s 绑定的模型不可用（model_id=%s），回退全局激活模型", spec.name, spec.model_id)
            if _session_cache_get(key) is not None:
                return _session_models[key]
        else:
            if spec.model_id not in _models:
                cfg = config_store.model_cfg_by_id(spec.model_id)
                model = build_model(cfg, session_id=session_id) if cfg else None
                _models[spec.model_id] = model
                if model is not None and cfg and needs_session_headers(cfg):
                    # 该供应商要求会话头：后续同会话调用走 (model_id, session) 缓存
                    _session_model_ids.add(spec.model_id)
                    if session_id is not None:
                        _session_cache_put((spec.model_id, str(session_id)), model)
                if model is None:
                    logger.warning("专家 %s 绑定的模型不可用（model_id=%s），回退全局激活模型", spec.name, spec.model_id)
            if _models[spec.model_id] is not None:
                return _models[spec.model_id]
    return _get_model(session_id)


def _resolve_mode(mode: str | None) -> str:
    return mode if mode in WORK_MODES else "standard"


async def _resolve_skill_loaders(cfg: dict, spec: AgentSpec):
    """构造注入 Agent 的技能 loader 列表（None = 不注入技能池）。

    全局未启用技能池或未配置技能池目录时不注入；专家级 skills 为空列表时
    显式关闭（None 跟随全局），非空列表为专家白名单——只注入名单内的技能包。
    已停用的技能包（_store.json 的 disabled 列表）与专家白名单由过滤 loader
    在注入侧统一剔除——LocalSkillLoader 本身不做过滤，停用与白名单由此对
    Agent 注入真实生效。
    """
    if not (cfg.get("enable_skills") and cfg.get("skills_dir")):
        return None
    if spec.skills is not None and not spec.skills:
        return None  # 专家配置显式关闭技能池
    skills_dir = Path(cfg["skills_dir"])
    if not skills_dir.is_dir():
        return None
    try:
        from agentscope.skill import LocalSkillLoader

        from . import skill_store

        disabled = set(skill_store.disabled_packs(skills_dir))
        whitelist = set(spec.skills) if spec.skills else None  # 专家级非空白名单

        class _EnabledSkillLoader(LocalSkillLoader):
            """只注入启用中（且在专家白名单内）技能包的 loader。"""

            async def list_skills(self):
                skills = await super().list_skills()
                if disabled:
                    skills = [s for s in skills if Path(s.dir).name not in disabled and s.name not in disabled]
                if whitelist is not None:
                    skills = [s for s in skills if s.name in whitelist or Path(s.dir).name in whitelist]
                return skills

        return [_EnabledSkillLoader(directory=str(skills_dir), scan_subdir=True)]
    except Exception:
        logger.warning("技能池加载失败，本次不注入技能：%s", skills_dir, exc_info=True)
        return None


def _readonly_tool_names() -> set[str]:
    """只读模式放行的工具名：内置查询工具 + 注册表中带 readonly=True 标记的工具。

    readonly 标记由模块 manifest（modules/spec.py 契约）与 MCP 工具注册（mcp.py）
    写入 TOOL_SPECS；构建时实时汇总，模块/MCP 注册变化即时生效。
    """
    # 快照迭代：MCP 连接/断开会在请求线程里增删 TOOL_SPECS
    return set(QUERY_TOOLS) | {name for name, spec in list(TOOL_SPECS.items()) if spec.get("readonly")}


async def _build_agent(spec: AgentSpec, mode: str = "standard", session_id: str | None = None):
    from pathlib import Path

    from agentscope.agent import Agent, ReActConfig
    from agentscope.permission import PermissionContext, PermissionMode
    from agentscope.state import AgentState
    from agentscope.tool import Toolkit

    cfg = config_store.load()
    tool_names = list(spec.tools)
    if mode == "readonly":
        # 只读模式：内置查询工具 + 注册表 readonly=True 标记的工具（模块/MCP 只读工具同样放行）
        readonly_names = _readonly_tool_names()
        tool_names = [t for t in tool_names if t in readonly_names]

    # 本地技能池（SKILL.md 格式，如 QwenPaw skill_pool）：经 LocalSkillLoader 注入，
    # 已停用的技能包在 loader 侧过滤（见 _resolve_skill_loaders）
    skills_or_loaders = await _resolve_skill_loaders(cfg, spec)

    toolkit = Toolkit(skills_or_loaders=skills_or_loaders)
    tools = build_tools(tool_names)
    if tools:
        await toolkit.add_tool(tools)

    # Bash 执行工具（跑技能脚本等）；自带危险文件/目录黑名单
    if cfg.get("enable_bash"):
        try:
            from agentscope.tool import Bash

            cwd = cfg.get("skills_dir") if skills_or_loaders else str(Path(__file__).resolve().parent.parent)
            await toolkit.add_tool(Bash(cwd=cwd))
        except Exception:
            logger.warning("Bash 工具加载失败，本次不启用", exc_info=True)

    # QwenPaw 工具型插件（经兼容层加载）；只读模式跳过（插件可能写外部系统）
    if cfg.get("enable_plugins") and cfg.get("plugins_dir") and mode != "readonly":
        plugins_dir = Path(cfg["plugins_dir"])
        if plugins_dir.is_dir():
            from agentscope.tool import FunctionTool

            from .qwenpaw_compat import load_qwenpaw_plugin

            for manifest in sorted(plugins_dir.glob("*/plugin.json")):
                try:
                    info = load_qwenpaw_plugin(str(manifest.parent))
                    for t in info["tools"]:
                        await toolkit.add_tool(
                            FunctionTool(
                                t["func"],
                                name=t["name"],
                                description=t["description"],
                                input_schema=t["schema"],
                            )
                        )
                except Exception:
                    logger.warning("插件加载失败，跳过：%s", manifest.parent.name, exc_info=True)
                    continue  # 单个插件失败不影响其余

    # 个人助手场景：工具白名单非破坏性，BYPASS 自动放行工具调用
    state = AgentState(permission_context=PermissionContext(mode=PermissionMode.BYPASS))

    max_iters = cfg.get("max_iters", 5)
    if mode == "deep":
        max_iters = max(max_iters, 10)

    system_prompt = spec.system_prompt + COMMON_RULES
    if mode == "deep":
        system_prompt += DEEP_SUFFIX
    elif mode == "readonly":
        system_prompt += READONLY_SUFFIX

    return Agent(
        name=spec.label,
        system_prompt=system_prompt,
        model=_get_spec_model(spec, session_id=session_id),
        toolkit=toolkit,
        state=state,
        react_config=ReActConfig(max_iters=max_iters),
    )


def _get_agent(session_id: str, spec: AgentSpec, mode: str):
    if _get_spec_model(spec, session_id=session_id) is None:
        raise RuntimeError("LLM 未配置")
    # 缓存 key 含专家绑定模型：模型变更（或绑定变化）时重建 Agent
    key = (session_id, spec.name, mode, spec.model_id or 0)
    agent = _agents.get(key)
    if agent is None:
        raise KeyError(key)  # 由调用方构建
    return agent


async def _ensure_agent(session_id: str, spec: AgentSpec, mode: str):
    try:
        return _get_agent(session_id, spec, mode)
    except KeyError:
        pass
    # 同一 (会话,专家,模式,模型) 并发首条消息时只构建一次，避免双份历史注入互相覆盖
    key = (session_id, spec.name, mode, spec.model_id or 0)
    lock = _agent_locks.setdefault(key, asyncio.Lock())
    async with lock:
        try:
            return _get_agent(session_id, spec, mode)
        except KeyError:
            agent = await _build_agent(spec, mode, session_id=session_id)
            history = _load_history_msgs(session_id)
            if history:
                agent.observe(history)
            _agent_cache_put(key, agent)
            return agent


def _load_history_msgs(session_id: str, limit: int | None = None) -> list:
    if session_id in _INTERNAL_SESSION_IDS:
        return []  # 记忆抽取等固定 id 内部会话：不注入历史，避免上下文反复膨胀
    cfg = config_store.load()
    if not cfg.get("enable_memory", True):
        return []
    n = limit or min(int(cfg.get("history_inject", 12)), 100)
    if n <= 0:
        return []
    from agentscope.message import Msg, TextBlock

    with SessionLocal() as db:
        rows = (
            db.query(ChatMessage)
            .filter(ChatMessage.session_id == session_id)
            .order_by(ChatMessage.id.desc())
            .limit(n)
            .all()
        )
    msgs = []
    for row in reversed(rows):
        role = "user" if row.role == "user" else "assistant"
        name = "用户" if role == "user" else (row.agent_name or "助手")
        msgs.append(
            Msg(name=name, role=role, content=[TextBlock(type="text", text=row.content)])
        )
    return msgs


async def _route_by_llm(question: str, team: dict[str, AgentSpec]) -> str | None:
    """LLM 纯文本仲裁；任何失败都返回 None（回退关键词/默认）。"""
    from agentscope.agent import Agent
    from agentscope.message import Msg, TextBlock

    if _get_model() is None:
        return None
    try:
        router = Agent(
            name="调度员",
            system_prompt=(
                "你是 Agent 团队的任务调度员。根据团队名册和用户消息，"
                "选出最合适的一位专家。只做路由选择，不做具体回答；只返回一个 name。"
            ),
            model=_get_model(),
        )
        reply = await router.reply(
            Msg(
                name="用户",
                role="user",
                content=[TextBlock(type="text", text=f"团队名册：\n{roster_text(team)}\n\n用户消息：{question}")],
            )
        )
        return parse_route_reply(reply.get_text_content() or "", team)
    except Exception as exc:
        logger.warning("LLM 路由阶段失败（%s），回退关键词/默认路由", type(exc).__name__)
    return None


def _persist(session_id: str, role: str, agent_name: str, content: str, trace: list, thinking: str = "") -> None:
    with SessionLocal() as db:
        db.add(
            ChatMessage(
                session_id=session_id,
                role=role,
                agent_name=agent_name,
                content=content,
                trace=trace,
                thinking=thinking,
            )
        )
        db.commit()


def _touch_session(session_id: str, first_message: str | None = None) -> None:
    """确保会话行存在；新会话以首条消息截断作为标题。"""
    try:
        with SessionLocal() as db:
            row = db.get(AgentSession, session_id)
            if row is None:
                title = (first_message or "新会话").strip().replace("\n", " ")[:30]
                db.add(AgentSession(session_id=session_id, title=title))
            else:
                # 界面「新建」先建了一个空会话（标题「新会话」），首条消息到达时补标题；
                # 用户手动改名后的标题不再覆盖
                if first_message and (row.title or "").strip() in ("", "新会话"):
                    row.title = first_message.strip().replace("\n", " ")[:30] or row.title
                row.updated_at = datetime.now()
            db.commit()
    except OperationalError:
        logger.warning("会话落库失败（数据库暂不可用）：session_id=%s", session_id, exc_info=True)


_THINK_TAG_RE = re.compile(r"<think>(.*?)</think>", re.DOTALL)

# 附件按文本注入的白名单后缀与大小上限
_TEXT_SUFFIXES = {".txt", ".md", ".csv", ".json", ".log", ".py", ".html", ".xml", ".yaml", ".yml", ".sql"}

# 聊天图片附件：扩展名 → MIME（base64 注入多模态消息用）；单张原始大小上限
_IMAGE_ATTACH_SUFFIXES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}
_MAX_IMAGE_BYTES = 10 * 1024 * 1024

# 当前模型未开启视觉能力时带图聊天的报错（用户可见，中文）
VISION_UNSUPPORTED_MESSAGE = "当前模型不支持图片理解，请在设置中为该模型开启视觉能力或切换视觉模型"

# 专家团队为空（全部停用）时的对话报错（用户可见，中文）
EMPTY_TEAM_MESSAGE = "专家团队为空：所有专家均已停用，请在 AI 助手「专家」页面启用至少一位专家后再对话"


def _is_image_attachment(att) -> bool:
    """判断聊天附件是否为图片（按路径后缀，回退文件名后缀）。"""
    path = Path(str(att.get("path") or ""))
    suffix = path.suffix.lower() or Path(str(att.get("name") or "")).suffix.lower()
    return suffix in _IMAGE_ATTACH_SUFFIXES


def _vision_cfg(agent_name: str | None = None, spec: AgentSpec | None = None) -> dict:
    """当前生效模型配置：优先专家绑定模型（直接给 spec 或按名查找），否则用全局激活模型。"""
    if spec is None and agent_name:
        try:
            spec = load_team().get(agent_name)
        except Exception:
            spec = None
    if spec is not None and spec.model_id:
        cfg = config_store.model_cfg_by_id(spec.model_id)
        if cfg:
            return cfg
    return config_store.load()


def assert_vision_supported(attachments, agent_name: str | None = None, spec: AgentSpec | None = None) -> None:
    """带图聊天前置校验：附件含图片而当前模型未开启视觉能力时抛 ValueError（路由层转 400）。

    模型整体未配置时不做拦截（走既有的 need_llm_config 引导）。
    自动路由（agent_name=None）时本校验只看全局模型，不代表实际生效模型——
    编排层在路由定到专家后会以该专家的 spec 再校验一次（见 chat/chat_stream）。
    """
    if not attachments or not any(_is_image_attachment(a) for a in attachments):
        return
    if not llm_status()["configured"]:
        return
    if not _vision_cfg(agent_name, spec).get("supports_vision"):
        raise ValueError(VISION_UNSUPPORTED_MESSAGE)


def _image_blocks(attachments) -> list:
    """把图片附件读成 base64，构造 AgentScope DataBlock（OpenAI 侧转 image_url，Anthropic 侧转原生图片块）。"""
    from agentscope.message import Base64Source, DataBlock

    from ..config import UPLOAD_DIR

    blocks, notes = [], []
    for a in attachments or []:
        if not _is_image_attachment(a):
            continue
        name = Path(str(a.get("name") or "image")).name
        path = Path(str(a.get("path") or ""))
        try:
            path.resolve().relative_to(UPLOAD_DIR.resolve())
        except Exception:
            continue  # 越界路径直接忽略
        if not path.exists():
            notes.append(f"【图片附件：{name}】文件不存在")
            continue
        if path.stat().st_size > _MAX_IMAGE_BYTES:
            notes.append(f"【图片附件：{name}】超过大小上限，已忽略")
            continue
        media_type = _IMAGE_ATTACH_SUFFIXES.get(path.suffix.lower(), "image/png")
        b64 = base64.b64encode(path.read_bytes()).decode("ascii")
        blocks.append(
            DataBlock(
                type="data",
                source=Base64Source(type="base64", data=b64, media_type=media_type),
                name=name,
            )
        )
        notes.append(f"【图片附件：{name}】")
    return blocks, notes


def _read_text_attachment(path: Path) -> str:
    """读取文本附件：严格 UTF-8 优先，失败回退 GBK（中文 Windows 常见编码），再不行替换非法字节。"""
    raw = path.read_bytes()
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        pass
    try:
        return raw.decode("gbk")
    except UnicodeDecodeError:
        return raw.decode("utf-8", errors="replace")


def _attachments_block(attachments) -> str:
    """把聊天附件拼成可注入消息的文本块（安全：仅允许上传目录内的文件）。"""
    from ..config import UPLOAD_DIR
    from ..document_text import DOCUMENT_SUFFIXES, extracted_text_path

    if not attachments:
        return ""
    parts: list[str] = []
    for a in attachments:
        name = Path(str(a.get("name") or "file")).name
        path = Path(str(a.get("path") or ""))
        try:
            path.resolve().relative_to(UPLOAD_DIR.resolve())
        except Exception:
            continue  # 越界路径直接忽略
        if not path.exists():
            parts.append(f"【附件 {name}】文件不存在")
            continue
        size = path.stat().st_size
        is_document = path.suffix.lower() in DOCUMENT_SUFFIXES
        read_path = extracted_text_path(path) if is_document else path
        if is_document:
            try:
                read_path.resolve().relative_to(UPLOAD_DIR.resolve())
            except Exception:
                parts.append(f"【文档附件：{name}】提取文本路径越界，已忽略")
                continue
            if not read_path.is_file():
                parts.append(f"【文档附件：{name}】提取文本不存在，请重新上传")
                continue
        read_size = read_path.stat().st_size
        if is_document or (read_path.suffix.lower() in _TEXT_SUFFIXES and read_size <= 300_000):
            content = _read_text_attachment(read_path)[:80000]
            content_label = "提取文本如下" if is_document else "内容如下"
            parts.append(
                f"【附件文件：{name}（{size} 字节）{content_label}】\n\n```\n{content}\n```\n\n"
                f"（完整路径：{path}。如需更深入的统计、透视或处理，可使用 Bash 工具读取该文件。）"
            )
        else:
            parts.append(
                f"【附件文件：{name}（{size} 字节，二进制或超大文件）】已保存到：{path}\n"
                f"如需分析，可使用 Bash 工具与 python 处理该文件。"
            )
    return "\n\n".join(parts)


def _split_inline_think(text: str) -> tuple[str, str]:
    """剥离正文中的 <think>…</think>（部分思考型模型会把它混进正文）。"""
    m = _THINK_TAG_RE.search(text)
    if m:
        think = m.group(1).strip()
        rest = (text[:m.start()] + text[m.end():]).strip()
    else:
        think, rest = "", text
    # 孤立的 think 结束标签也清掉（如 Qwen 正文开头的 "</think>"）
    rest = rest.replace("<think>", "").replace("</think>", "").strip()
    return rest, think


def _thinking_of(msg) -> str:
    """从最终消息中提取思考块文本。"""
    blocks = msg.get_content_blocks() if hasattr(msg, "get_content_blocks") else []
    parts = []
    for b in blocks or []:
        if getattr(b, "type", None) == "thinking":
            parts.append(getattr(b, "text", ""))
    return "\n".join(p for p in parts if p).strip()


def _route_candidate_payload(matches: list[dict]) -> list[dict]:
    return [
        {"name": match["name"], "score": match["score"], "confidence": match["confidence"]}
        for match in matches
    ]


# 续聊判定：这些表达强依赖上一轮上下文，不应作为新问题重新路由
_CONTINUATION_MARKERS = (
    "继续",
    "接着",
    "接着说",
    "按照上面",
    "如上",
    "同上",
    "刚才",
    "刚刚",
    "上面",
)


def _is_continuation(question: str) -> bool:
    """判断用户是否在要求延续当前会话上下文。"""
    normalized = normalize_route_text(question or "")
    return any(marker in normalized for marker in _CONTINUATION_MARKERS)


def _session_current_agent(session_id: str | None) -> str | None:
    """取当前会话最近一次实际回复的专家；无历史/内部会话返回 None。

    定时任务（job-*）等内部会话不参与续聊保持：任务提示词即使出现“继续”，
    也应按当次任务重新路由，而不是沿用上一次任务留下的专家。
    """
    if not session_id or session_id in _INTERNAL_SESSION_IDS:
        return None
    if any(session_id.startswith(prefix) for prefix in _INTERNAL_SESSION_PREFIXES):
        return None
    try:
        with SessionLocal() as db:
            row = (
                db.query(ChatMessage.agent_name)
                .filter(
                    ChatMessage.session_id == session_id,
                    ChatMessage.role == "assistant",
                    ChatMessage.agent_name != "",
                )
                .order_by(ChatMessage.id.desc())
                .first()
            )
    except OperationalError:
        logger.warning("读取会话当前专家失败（数据库暂不可用）", exc_info=True)
        return None
    return row[0] if row and row[0] else None


def _interrupted_stream_message(delta_parts: list[str], thinking_parts: list[str]) -> tuple[str, str]:
    """把中断前已流式产出的正文/思考整理为可落库内容，避免历史缺片。"""
    text, inline_think = _split_inline_think("".join(delta_parts))
    thinking = "\n".join(part for part in (inline_think, "".join(thinking_parts)) if part).strip()
    return (text.strip() or "（已停止生成）"), thinking


async def _resolve_route(question: str, agent_name: str | None, session_id: str | None = None):
    team = load_team()
    if not team:
        return None
    matches = route_scores(question, team)
    if agent_name in team:
        return team[agent_name], "指定", matches
    direct = best_keyword_candidate(matches)
    if direct:
        return team[direct["name"]], "关键词", matches
    # 续聊保持：没有高置信关键词且用户明确依赖上文时，继续当前会话专家，
    # 避免“继续/接着说”掉回默认专家造成角色漂移与上下文断片。
    if session_id and _is_continuation(question):
        current_agent = _session_current_agent(session_id)
        if current_agent in team:
            return team[current_agent], "续聊", matches
    picked = await _route_by_llm(question, team)
    if picked in team:
        return team[picked], "AI 调度", matches
    if matches:
        return team[matches[0]["name"]], "关键词回退", matches
    return team[default_agent_name(team)], "默认", matches


async def _resolve_spec(question: str, agent_name: str | None, session_id: str | None = None):
    """路由：返回 (spec, routed_by)。专家团队读库（启用中），兜底内置定义。

    专家团队为空（全部停用）时返回 None，由调用方给出明确错误（见 EMPTY_TEAM_MESSAGE）。
    """
    resolved = await _resolve_route(question, agent_name, session_id)
    return resolved[:2] if resolved is not None else None


def _with_memory_context(message: str) -> str:
    """把检索到的相关长期记忆作为上下文前缀注入用户消息（失败静默，不影响对话）。"""
    try:
        from .memory import recall_for_context

        hits = recall_for_context(message, top_k=3)
        if not hits:
            return message
        lines = ["[长期记忆参考]"]
        lines += [f"- {h['content']}" for h in hits]
        lines.append("[对话开始]")
        return "\n".join(lines) + "\n" + message
    except Exception:
        logger.warning("长期记忆注入失败（已忽略）", exc_info=True)
        return message


def _user_content_blocks(message: str, attachments, spec: AgentSpec | None = None) -> list:
    """构造发给 LLM 的 user 消息内容块。

    文本部分 = 记忆上下文 + 文本类附件说明；当前生效模型（路由后专家的绑定模型优先，
    见 _vision_cfg）开启视觉能力时，图片附件以 base64 DataBlock 追加
    （OpenAI 协议转 image_url，Anthropic 转原生图片块）。
    """
    from agentscope.message import TextBlock

    text = _with_memory_context(message)
    text_attachments = [a for a in (attachments or []) if not _is_image_attachment(a)]
    block = _attachments_block(text_attachments)
    if block:
        text = f"{text}\n\n{block}"

    image_blocks: list = []
    notes: list[str] = []
    if any(_is_image_attachment(a) for a in (attachments or [])):
        if _vision_cfg(spec=spec).get("supports_vision"):
            image_blocks, notes = _image_blocks(attachments)
        else:
            notes.append(f"【图片附件】{VISION_UNSUPPORTED_MESSAGE}")
    if notes:
        text = f"{text}\n\n" + "\n".join(notes)
    return [TextBlock(type="text", text=text)] + image_blocks


def _job_scope_refusal(session_id: str, spec: AgentSpec | None = None) -> str | None:
    """定时任务会话（job-*）的套餐边界：交互式套餐模型拒绝执行；普通聊天不受影响。

    生效模型 = 专家绑定模型（路由后复核）或全局激活模型；usage_scope 取自供应商预设
    （plan_interactive_only = 官方约定仅限交互式工具，禁定时任务/API 调用）。
    """
    if not any(str(session_id or "").startswith(prefix) for prefix in _INTERNAL_SESSION_PREFIXES):
        return None
    cfg = None
    if spec is not None and spec.model_id:
        cfg = config_store.model_cfg_by_id(spec.model_id)
    if cfg is None:
        cfg = config_store.load()
    if str(cfg.get("usage_scope") or "general") != "plan_interactive_only":
        return None
    who = str(cfg.get("provider_name") or "当前模型")
    return (
        f"定时任务已阻止：{who} 属于仅限交互式使用的套餐（官方约定不用于定时任务/API 调用），"
        f"请在 AI 助手设置中切换到按量付费或明确允许 API 调用的模型后再运行。"
    )


def _preroute_scope_refusal(session_id: str, agent_name: str | None) -> str | None:
    """路由前的套餐预检：仅自动路由需要（路由本身可能调用全局激活模型仲裁）。

    明确指定专家时路由不会触碰全局模型，由路由后的 spec 复核兜底——
    避免「全局是交互式套餐、专家绑定按量模型」的合法定时任务被误伤。
    """
    if agent_name and agent_name in load_team():
        return None
    return _job_scope_refusal(session_id)


async def chat(
    session_id: str,
    message: str,
    agent_name: str | None = None,
    mode: str = "standard",
    attachments: list | None = None,
) -> dict:
    """处理一轮对话（非流式）：路由 → 专家执行 → 持久化。"""
    if not llm_status()["configured"]:
        return {
            "ok": False,
            "need_llm_config": True,
            "message": "模型未配置：请在 AI 助手「⚙ 设置」中选择供应商并填写 API Key，保存后即可激活 Agent 团队。",
        }

    refusal = _preroute_scope_refusal(session_id, agent_name)
    if refusal:
        return {"ok": False, "message": refusal}

    mode = _resolve_mode(mode)
    resolved = await _resolve_spec(message, agent_name, session_id)
    if resolved is None:
        return {"ok": False, "message": EMPTY_TEAM_MESSAGE}
    spec, routed_by = resolved
    # 路由定到专家后按其绑定模型复核套餐边界（专家绑定模型可能与全局不同）
    refusal = _job_scope_refusal(session_id, spec)
    if refusal:
        return {"ok": False, "message": refusal}
    # 路由定到专家后按其绑定模型复核视觉能力（自动路由时路由层前置校验只看了全局模型）
    assert_vision_supported(attachments, spec=spec)
    agent = await _ensure_agent(session_id, spec, mode)

    from agentscope.message import Msg

    token = tool_trace.set([])
    try:
        reply = await agent.reply(
            Msg(name="用户", role="user", content=_user_content_blocks(message, attachments, spec))
        )
        text = (reply.get_text_content() or "").strip() or "（模型未返回内容）"
        text, inline_think = _split_inline_think(text)
        thinking = "\n".join(x for x in (inline_think, _thinking_of(reply)) if x).strip()
        trace = list(tool_trace.get() or [])
    finally:
        tool_trace.reset(token)

    _touch_session(session_id, message)
    _persist(session_id, "user", "", message, [])
    _persist(session_id, "assistant", spec.name, text, trace, thinking)

    return {
        "ok": True,
        "session_id": session_id,
        "reply": text,
        "thinking": thinking,
        "agent": {"name": spec.name, "label": spec.label, "emoji": spec.emoji, "color": spec.color},
        "routed_by": routed_by,
        "mode": mode,
        "trace": trace,
    }


async def chat_stream(
    session_id: str,
    message: str,
    agent_name: str | None = None,
    mode: str = "standard",
    attachments: list | None = None,
):
    """流式对话：逐段 yield 事件 dict，由路由层封装为 SSE。"""
    if not llm_status()["configured"]:
        yield {
            "type": "error",
            "need_llm_config": True,
            "message": "模型未配置：请在 AI 助手「⚙ 设置」中选择供应商并填写 API Key，保存后即可激活 Agent 团队。",
        }
        return

    mode = _resolve_mode(mode)
    refusal = _preroute_scope_refusal(session_id, agent_name)
    if refusal:
        yield {"type": "error", "message": refusal}
        return
    resolved = await _resolve_route(message, agent_name, session_id)
    if resolved is None:
        yield {"type": "error", "message": EMPTY_TEAM_MESSAGE}
        return
    spec, routed_by, matches = resolved
    # 路由定到专家后按其绑定模型复核套餐边界（专家绑定模型可能与全局不同）
    refusal = _job_scope_refusal(session_id, spec)
    if refusal:
        yield {"type": "error", "message": refusal}
        return
    route_candidates = _route_candidate_payload(matches)
    try:
        # 路由定到专家后按其绑定模型复核视觉能力（自动路由时路由层前置校验只看了全局模型）
        assert_vision_supported(attachments, spec=spec)
    except ValueError as exc:
        yield {"type": "error", "message": str(exc)}
        return
    yield {
        "type": "meta",
        "agent": {"name": spec.name, "label": spec.label, "emoji": spec.emoji, "color": spec.color},
        "routed_by": routed_by,
        "route_candidates": route_candidates,
        "mode": mode,
    }

    agent = await _ensure_agent(session_id, spec, mode)

    from agentscope.event import (
        ThinkingBlockDeltaEvent,
        TextBlockDeltaEvent,
        ToolCallStartEvent,
    )
    from agentscope.message import Msg

    _touch_session(session_id, message)
    _persist(session_id, "user", "", message, [])

    token = tool_trace.set([])
    delta_parts: list[str] = []
    thinking_parts: list[str] = []
    assistant_persisted = False
    try:
        final_msg = None
        async for ev in agent.reply_stream(
            Msg(name="用户", role="user", content=_user_content_blocks(message, attachments, spec)),
            yield_final_msg=True,
        ):
            if isinstance(ev, TextBlockDeltaEvent):
                if ev.delta:
                    delta_parts.append(ev.delta)
                    yield {"type": "delta", "text": ev.delta}
            elif isinstance(ev, ThinkingBlockDeltaEvent):
                if ev.delta:
                    thinking_parts.append(ev.delta)
                    yield {"type": "thinking_delta", "text": ev.delta}
            elif isinstance(ev, ToolCallStartEvent):
                yield {"type": "tool_start", "name": ev.tool_call_name}
            elif isinstance(ev, Msg):
                final_msg = ev

        text = (final_msg.get_text_content() or "").strip() if final_msg is not None else ""
        text = text or "（模型未返回内容）"
        text, inline_think = _split_inline_think(text)
        thinking = "\n".join(x for x in (inline_think, _thinking_of(final_msg) if final_msg is not None else "") if x).strip()
        trace = list(tool_trace.get() or [])
    except (asyncio.CancelledError, GeneratorExit):
        # 客户端停止/断开：用户消息已落库，把已流式产出的内容补存为 assistant，
        # 否则继续输入时新 Agent 看不到这半轮上下文，表现为“遗忘上文”。
        if not assistant_persisted:
            text, thinking = _interrupted_stream_message(delta_parts, thinking_parts)
            trace = list(tool_trace.get() or [])
            try:
                _persist(session_id, "assistant", spec.name, text, trace, thinking)
                assistant_persisted = True
            except Exception:
                logger.warning("中断内容落库失败：session_id=%s", session_id, exc_info=True)
        raise
    except Exception as exc:
        # 用户消息已落库，补一条 assistant 侧错误消息，保持历史成对
        err_text = f"抱歉，本次回复生成失败：{type(exc).__name__}"
        logger.warning("流式对话执行失败：session_id=%s，%s", session_id, exc, exc_info=True)
        try:
            _persist(session_id, "assistant", spec.name, err_text, [])
            assistant_persisted = True
        except Exception:
            logger.warning("错误消息落库失败：session_id=%s", session_id, exc_info=True)
        yield {"type": "error", "message": err_text}
        return
    finally:
        tool_trace.reset(token)

    _persist(session_id, "assistant", spec.name, text, trace, thinking)
    yield {
        "type": "done",
        "session_id": session_id,
        "reply": text,
        "thinking": thinking,
        "agent": {"name": spec.name, "label": spec.label, "emoji": spec.emoji, "color": spec.color},
        "routed_by": routed_by,
        "route_candidates": route_candidates,
        "mode": mode,
        "trace": trace,
    }


# ---------------- 会话管理 ----------------

# 内部会话标识：定时任务执行会话（scheduler.SESSION_PREFIX，job-N 每次运行复用同一 id）
# 与记忆抽取固定会话（memory.py 的 "memory-extract"）——不进会话列表；
# memory-extract 不注入历史（固定 id 反复注入会导致上下文膨胀）
_INTERNAL_SESSION_PREFIXES = ("job-",)
_INTERNAL_SESSION_IDS = ("memory-extract",)


def sessions_list(limit: int = 50, archived: bool | None = None) -> list[dict]:
    """会话列表（按更新时间倒序）。

    archived=None 返回全部（含归档）；True/False 时按归档状态过滤。
    内部会话（定时任务 job-*、记忆抽取 memory-extract）始终不出现在列表中。
    """
    try:
        with SessionLocal() as db:
            query = db.query(AgentSession)
            for prefix in _INTERNAL_SESSION_PREFIXES:
                query = query.filter(~AgentSession.session_id.like(f"{prefix}%"))
            for sid in _INTERNAL_SESSION_IDS:
                query = query.filter(AgentSession.session_id != sid)
            if archived is not None:
                query = query.filter(AgentSession.archived == bool(archived))
            rows = query.order_by(AgentSession.updated_at.desc()).limit(max(1, min(limit, 200))).all()
            counts = dict(db.query(ChatMessage.session_id, func.count()).group_by(ChatMessage.session_id).all())
    except OperationalError:
        logger.warning("会话列表查询失败（数据库暂不可用）", exc_info=True)
        return []
    return [
        {
            "session_id": r.session_id,
            "title": r.title or "新会话",
            "msg_count": counts.get(r.session_id, 0),
            "archived": bool(r.archived),
            "updated_at": r.updated_at.isoformat(),
        }
        for r in rows
    ]


def session_create(session_id: str, title: str = "") -> dict:
    _touch_session(session_id, title or None)
    return {"session_id": session_id, "title": title or "新会话"}


def session_rename(session_id: str, title: str) -> dict:
    with SessionLocal() as db:
        row = db.get(AgentSession, session_id)
        if row is None:
            row = AgentSession(session_id=session_id, title=title[:100])
            db.add(row)
        else:
            row.title = title.strip()[:100] or row.title
        db.commit()
        return {"session_id": session_id, "title": row.title}


def session_delete(session_id: str) -> None:
    with SessionLocal() as db:
        row = db.get(AgentSession, session_id)
        if row is not None:
            db.delete(row)
        for msg in db.query(ChatMessage).filter(ChatMessage.session_id == session_id).all():
            db.delete(msg)
        db.commit()
    # 清理该会话的进程内 Agent 缓存与构建锁
    for key in [k for k in list(_agents) if k[0] == session_id]:
        _agents.pop(key, None)
    for key in [k for k in list(_agent_locks) if k[0] == session_id]:
        _agent_locks.pop(key, None)
    # 该会话按 (模型, 会话) 维度缓存的模型实例一并清理（含 SDK 连接池引用）
    for key in [k for k in list(_session_models) if k[1] == session_id]:
        _session_models.pop(key, None)


def session_set_archived(session_id: str, archived: bool) -> dict:
    """归档/恢复会话；会话不存在抛 ValueError（路由层转 404）。"""
    with SessionLocal() as db:
        row = db.get(AgentSession, session_id)
        if row is None:
            raise ValueError(f"会话不存在：{session_id}")
        row.archived = bool(archived)
        row.updated_at = datetime.now()
        db.commit()
        db.refresh(row)
        return {"session_id": row.session_id, "title": row.title or "新会话", "archived": bool(row.archived)}


def session_export(session_id: str) -> dict:
    """导出会话为 Markdown（标题/创建时间/消息数 + 全量消息，思考与轨迹折叠附上）。

    会话不存在抛 ValueError（路由层转 404）。
    """
    with SessionLocal() as db:
        row = db.get(AgentSession, session_id)
        if row is None:
            raise ValueError(f"会话不存在：{session_id}")
        title = row.title or "新会话"
        created_at = row.created_at or datetime.now()
        msgs = (
            db.query(ChatMessage)
            .filter(ChatMessage.session_id == session_id)
            .order_by(ChatMessage.id.asc())
            .all()
        )
        payload = [
            {
                "role": m.role,
                "agent_name": m.agent_name or "",
                "content": m.content or "",
                "thinking": m.thinking or "",
                "trace": m.trace or [],
                "created_at": m.created_at or datetime.now(),
            }
            for m in msgs
        ]

    team = load_team()

    def _label(agent_name: str) -> str:
        spec = team.get(agent_name) or TEAM.get(agent_name)
        return spec.label if spec else (agent_name or "助手")

    lines: list[str] = [
        f"# 会话导出：{title}",
        "",
        f"- 会话 ID：{session_id}",
        f"- 创建时间：{created_at.strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 导出时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 消息数：{len(payload)}",
        "",
        "---",
        "",
    ]
    for i, m in enumerate(payload, 1):
        if m["role"] == "user":
            who = "用户"
        else:
            who = _label(m["agent_name"])
        lines.append(f"## {i}. {who} · {m['created_at'].strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append("")
        lines.append(m["content"] or "（空）")
        lines.append("")
        if m["thinking"]:
            lines.append("<details><summary>思考过程</summary>")
            lines.append("")
            lines.append(m["thinking"])
            lines.append("")
            lines.append("</details>")
            lines.append("")
        if m["trace"]:
            lines.append("<details><summary>工具执行轨迹</summary>")
            lines.append("")
            for t in m["trace"]:
                if not isinstance(t, dict):
                    continue
                name = t.get("tool") or t.get("name") or "工具"
                args = t.get("args")
                result = t.get("result")
                lines.append(f"- `{name}`" + (f" 参数：`{args}`" if args not in (None, "", {}, []) else ""))
                if result not in (None, ""):
                    lines.append(f"  - 结果：{result}")
            lines.append("")
            lines.append("</details>")
            lines.append("")
    return {
        "session_id": session_id,
        "title": title,
        "msg_count": len(payload),
        "markdown": "\n".join(lines).rstrip() + "\n",
    }


def history(session_id: str, limit: int = 50) -> list[dict]:
    with SessionLocal() as db:
        rows = (
            db.query(ChatMessage)
            .filter(ChatMessage.session_id == session_id)
            .order_by(ChatMessage.id.desc())
            .limit(max(1, min(limit, 200)))
            .all()
        )
    team = load_team()

    def _label(agent_name: str) -> str:
        spec = team.get(agent_name) or TEAM.get(agent_name)
        return spec.label if spec else agent_name

    return [
        {
            "id": r.id,
            "role": r.role,
            "agent_name": r.agent_name,
            "agent_label": _label(r.agent_name),
            "content": r.content,
            "thinking": r.thinking or "",
            "trace": r.trace or [],
            "created_at": r.created_at.strftime("%Y-%m-%d %H:%M"),
        }
        for r in reversed(rows)
    ]


def team_payload() -> dict:
    """团队名册 + 模型状态 + 运行时配置（脱敏）+ 供应商/模型 + 工作模式。"""
    cfg = config_store.load()
    return {
        "llm": llm_status(cfg),
        "config": config_store.masked(cfg),
        "presets": config_store.PRESETS,
        "providers": config_store.list_providers(),
        "models": config_store.models_flat(),
        "work_modes": WORK_MODES,
        "agents": [
            {
                "name": s.name,
                "label": s.label,
                "emoji": s.emoji,
                "color": s.color,
                "description": s.description,
                "tools": s.tools,
                # 配置化新增字段（原结构保持不变，前端可按需取用）
                "id": s.row_id,
                "model_id": s.model_id,
                "keywords": s.keywords,
                "enabled": s.enabled,
                "sort": s.sort,
                "is_builtin": s.is_builtin,
            }
            for s in load_team(force=True).values()
        ],
    }
