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
import hashlib
import logging
import threading
import time
from collections import OrderedDict
from datetime import datetime
from pathlib import Path

from sqlalchemy import func
from sqlalchemy.exc import OperationalError

from ..database import SessionLocal
from ..models import AgentSession, ChatMessage
from . import config_store
from .llm import build_model, llm_status, needs_session_headers
# 按变化原因拆出的子模块；此处统一 re-export，调用方（routers/scheduler/tests）
# 继续只从 orchestrator 取符号，import 路径与 patch 接缝保持不变。
from .orchestrator_prompt import (  # noqa: F401
    DEEP_SUFFIX,
    READONLY_SUFFIX,
    VISION_UNSUPPORTED_MESSAGE,
    _attachments_block,
    _image_blocks,
    _is_image_attachment,
    _read_text_attachment,
    _user_content_blocks,
    _vision_cfg,
    _with_memory_context,
    assert_vision_supported,
    compose_system_prompt,
)
from .orchestrator_routing import (  # noqa: F401
    _CONTINUATION_MARKERS,
    _is_continuation,
    _route_candidate_payload,
    parse_route_reply,
)
from .orchestrator_stream import (  # noqa: F401
    _TRACE_RESULT_MAX,
    _TRACE_TOTAL_MAX,
    _govern_trace,
    _interrupted_stream_message,
    _split_inline_think,
    _thinking_of,
)
from .orchestrator_tools import (  # noqa: F401
    WORK_MODES,
    _agent_bash_available,
    _readonly_tool_names,
    _resolve_mode,
    _resolve_skill_loaders,
    _tooling_plan,
)
from .team import (
    COMMON_RULES,
    TEAM,
    AgentSpec,
    best_keyword_candidate,
    default_agent_name,
    load_team,
    roster_text,
    route_scores,
)
from .tools import build_tools, tool_trace

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
# 构建锁空闲回收：配置保存会 invalidate，但不得清掉刚创建/在途的锁，否则同一个
# (会话,专家,模式,模型) 的锁内双重检查失效、并发双建 Agent（M8）。
_AGENT_LOCK_IDLE_SECONDS = 300.0
_agent_lock_last_used: dict[tuple[str, str, str, int], float] = {}
# 进程级缓存守卫：_agents 的 LRU 淘汰与 invalidate 的 clear 可能跨线程并发；
# 锁注册也用同一把守卫保证「建锁 + 记录时间」原子。
_agent_cache_guard = threading.Lock()


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


def _agent_lock_for(key: tuple[str, str, str, int]) -> asyncio.Lock:
    """取（不存在则建）该 key 的构建锁；在守卫内同时记录使用时间，供空闲回收。"""
    with _agent_cache_guard:
        lock = _agent_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            _agent_locks[key] = lock
        _agent_lock_last_used[key] = time.monotonic()
        return lock


def _agent_cache_put(key: tuple[str, str, str, int], agent: object) -> None:
    with _agent_cache_guard:
        _agents[key] = agent
        _agents.move_to_end(key)
        while len(_agents) > _AGENT_CACHE_MAX:
            evicted, _ = _agents.popitem(last=False)
            evicted_lock = _agent_locks.get(evicted)
            if evicted_lock is None or not evicted_lock.locked():
                # 构建锁随淘汰一并清理，避免 _agent_locks 无限增长；在途锁留给空闲回收
                _agent_locks.pop(evicted, None)
                _agent_lock_last_used.pop(evicted, None)


def invalidate() -> None:
    """配置变更后清空进程内缓存（模型实例与各会话 Agent）。"""
    global _model, _model_needs_session
    _model = None
    _model_needs_session = False
    _models.clear()
    _session_models.clear()
    _session_model_ids.clear()
    # Agent 缓存整体失效；构建锁只在「未持有且空闲够久」时回收。直接 clear 会让
    # 在途构建协程与新请求各持一把锁，锁内双重检查失效而双建 Agent（M8）。
    with _agent_cache_guard:
        _agents.clear()
        now = time.monotonic()
        for key, lock in list(_agent_locks.items()):
            if lock.locked():
                continue
            if now - _agent_lock_last_used.get(key, 0.0) >= _AGENT_LOCK_IDLE_SECONDS:
                _agent_locks.pop(key, None)
                _agent_lock_last_used.pop(key, None)
    _route_cache.clear()
    _summary_retry_until.clear()
    # 只清「未持有」的运行锁：正在生成中的会话应立即完成本轮，不能因配置保存
    # 而在下一轮出现同一 session 两把锁（BUGfix AGT-02）。
    # 先取快照：invalidate 在同步路由的线程池里执行，锁表可能被事件循环并发增删，
    # 直接迭代 items() 会抛 RuntimeError: dictionary changed size during iteration。
    for sid in [s for s, lock in list(_session_run_locks.items()) if not lock.locked()]:
        _session_run_locks.pop(sid, None)
        _session_run_lock_last_used.pop(sid, None)


# 同一会话的运行锁：同一 session 的 chat / chat_stream 必须串行，
# 否则两个协程同时对同一 Agent 调 reply_stream，内部状态与历史会交错。
_session_run_locks: dict[str, asyncio.Lock] = {}
_session_run_lock_last_used: dict[str, float] = {}
_SESSION_RUN_LOCK_IDLE_SECONDS = 300.0
# 单事件循环内的原子占用集合：同一个 session 的第二个请求立即 409，而不是在
# async with lock 上排队。仅覆盖当前部署的单 worker 模型。
_active_sessions: set[str] = set()
# 会话生命周期锁：begin 占入与 session_delete 的「检查 + 标记删除中」必须原子。
# 否则删除检查通过后、删库前有新请求占入，删除完成后本轮 _persist_user_turn →
# _touch_session 会按首条消息重建会话行，形成幽灵会话（M1）。删除在同步路由的
# 线程池执行，直接占入只加锁很短一段，不把 DB 删除放进临界区。
_session_lifecycle_lock = threading.Lock()
_deleting_sessions: set[str] = set()


def begin_session_run(session_id: str) -> bool:
    """原子占入会话；返回 False 表示同一会话已在生成或正在被删除。"""
    with _session_lifecycle_lock:
        if session_id in _deleting_sessions or session_id in _active_sessions:
            return False
        _active_sessions.add(session_id)
        return True


def end_session_run(session_id: str) -> None:
    _active_sessions.discard(session_id)


def session_run_lock(session_id: str) -> asyncio.Lock:
    """返回会话唯一运行锁；未持有且空闲超过 5 分钟的旧锁可回收。"""
    now = time.monotonic()
    for sid, existing in list(_session_run_locks.items()):
        idle_since = _session_run_lock_last_used.get(sid, now)
        if not existing.locked() and now - idle_since >= _SESSION_RUN_LOCK_IDLE_SECONDS:
            _session_run_locks.pop(sid, None)
            _session_run_lock_last_used.pop(sid, None)
    lock = _session_run_locks.get(session_id)
    if lock is None:
        lock = asyncio.Lock()
        _session_run_locks[session_id] = lock
    _session_run_lock_last_used[session_id] = now
    return lock


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


def _get_routing_model(session_id: str | None = None):
    """路由仲裁模型；job 使用交互套餐时返回 None，由调用方回退离线路由。"""
    rm_id = config_store.load().get("routing_model_id")
    if rm_id:
        cfg = config_store.model_cfg_by_id(int(rm_id))
        if _is_job_session(session_id) and cfg and cfg.get("usage_scope") == "plan_interactive_only":
            logger.warning("定时任务不使用仅限交互式套餐的路由模型（model_id=%s），回退关键词", rm_id)
            return None
        if rm_id not in _models:
            _models[rm_id] = build_model(cfg) if cfg else None
            if _models[rm_id] is None:
                logger.warning("路由模型不可用（model_id=%s），回退全局激活模型", rm_id)
        if _models[rm_id] is not None:
            return _models[rm_id]
    return _get_model()


async def _build_agent(spec: AgentSpec, mode: str = "standard", session_id: str | None = None):
    from pathlib import Path

    from agentscope.agent import Agent, ReActConfig
    from agentscope.permission import PermissionContext, PermissionMode
    from agentscope.state import AgentState
    from agentscope.tool import Toolkit

    mode = _resolve_mode(mode)  # 直接调用也不得让历史脏值获得写能力
    cfg = config_store.load()
    tool_names, allow_bash, allow_plugins, max_iters = _tooling_plan(spec, mode, cfg)

    # 本地技能池（SKILL.md 格式，如 QwenPaw skill_pool）：经 LocalSkillLoader 注入，
    # 已停用的技能包在 loader 侧过滤（见 _resolve_skill_loaders）
    skills_or_loaders = await _resolve_skill_loaders(cfg, spec)

    toolkit = Toolkit(skills_or_loaders=skills_or_loaders)
    tools = build_tools(tool_names)
    if tools:
        await toolkit.add_tool(tools)

    # Bash 执行工具（跑技能脚本等）；自带危险文件/目录黑名单。
    # 只读模式必须跳过：否则「写入类工具已对你关闭」的承诺失效（BUGfix AGT-01）。
    if allow_bash:
        try:
            from agentscope.tool import Bash

            cwd = cfg.get("skills_dir") if skills_or_loaders else str(Path(__file__).resolve().parent.parent)
            await toolkit.add_tool(Bash(cwd=cwd))
        except Exception:
            logger.warning("Bash 工具加载失败，本次不启用", exc_info=True)

    # QwenPaw 工具型插件（经兼容层加载）；只读模式跳过（插件可能写外部系统）
    if allow_plugins:
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

    system_prompt = compose_system_prompt(spec, mode)

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
    if _is_job_session(session_id):
        return await _build_agent(spec, mode, session_id=session_id)
    try:
        return _get_agent(session_id, spec, mode)
    except KeyError:
        pass
    # 同一 (会话,专家,模式,模型) 并发首条消息时只构建一次，避免双份历史注入互相覆盖
    key = (session_id, spec.name, mode, spec.model_id or 0)
    lock = _agent_lock_for(key)
    async with lock:
        try:
            return _get_agent(session_id, spec, mode)
        except KeyError:
            agent = await _build_agent(spec, mode, session_id=session_id)
            history = _load_history_msgs(session_id)
            summary = await _refresh_session_summary(session_id)
            if summary:
                history = [_summary_message(summary)] + history
            if history:
                # observe 是 async：不 await 协程永不执行，历史注入（含滚动摘要）等于失效
                try:
                    await agent.observe(history)
                except Exception:
                    logger.warning("历史注入失败（已忽略，不影响本轮对话）", exc_info=True)
            _agent_cache_put(key, agent)
            return agent


def _is_job_session(session_id: str | None) -> bool:
    return any(str(session_id or "").startswith(prefix) for prefix in _INTERNAL_SESSION_PREFIXES)


def _load_history_msgs(session_id: str, limit: int | None = None) -> list:
    if session_id in _INTERNAL_SESSION_IDS or _is_job_session(session_id):
        return []  # 内部会话不注入历史；定时任务每次独立执行
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
            .limit(n + 1)
            .all()
        )
    if rows and rows[0].role == "user":
        rows = rows[1:]
    rows = rows[:n]
    msgs = []
    for row in reversed(rows):
        role = "user" if row.role == "user" else "assistant"
        name = "用户" if role == "user" else (row.agent_name or "助手")
        msgs.append(
            Msg(name=name, role=role, content=[TextBlock(type="text", text=row.content)])
        )
    return msgs


# ---------------- 长会话滚动摘要（演进第二批） ----------------
# 历史未压缩部分超过注入窗口 2 倍时，Agent（重）构建阶段把窗口外的更早消息压缩进
# agent_sessions.summary（游标 summary_until 防重复压缩），注入时置顶 + 最近窗口。
# 失败只告警并沿用既有摘要，绝不阻塞聊天。
_SUMMARY_WINDOW_FACTOR = 2
_SUMMARY_MSG_MAX = 400
_SUMMARY_PROMPT_MAX = 6000
_SUMMARY_MAX_CHARS = 2000
# 摘要模型失败后的进程内退避：避免每次 Agent 重建都重试失败调用。
# 续聊/历史仍走旧摘要，游标不推进；重启后允许立即重试一次。
_SUMMARY_RETRY_SECONDS = 600.0
_summary_retry_until: dict[str, float] = {}


def _load_summary_text(session_id: str) -> str:
    with SessionLocal() as db:
        row = db.get(AgentSession, session_id)
    return (row.summary or "") if row is not None else ""


def _save_summary_text(session_id: str, text: str) -> None:
    with SessionLocal() as db:
        row = db.get(AgentSession, session_id)
        if row is None:
            row = AgentSession(session_id=session_id)
            db.add(row)
            db.flush()
        row.summary = (text or "").strip()[:_SUMMARY_MAX_CHARS]
        db.commit()


def _summary_message(text: str):
    from agentscope.message import Msg, TextBlock

    return Msg(name="系统摘要", role="user", content=[TextBlock(type="text", text=f"【此前对话摘要】{text}")])


def _summary_state(session_id: str) -> tuple[int, int | None]:
    """返回 (会话最新消息 id, 摘要游标 summary_until)。无消息时 latest=0。"""
    with SessionLocal() as db:
        row = db.get(AgentSession, session_id)
        cursor = row.summary_until if row is not None else None
        latest = (
            db.query(func.max(ChatMessage.id))
            .filter(ChatMessage.session_id == session_id)
            .scalar()
            or 0
        )
    return int(latest), (int(cursor) if cursor else None)


async def _refresh_session_summary(session_id: str) -> str:
    """超阈值时压缩普通会话；关闭记忆或内部执行会话不读取、不生成摘要。"""
    if session_id in _INTERNAL_SESSION_IDS or _is_job_session(session_id):
        return ""
    cfg = config_store.load()
    if not cfg.get("enable_memory", True):
        return ""
    existing = _load_summary_text(session_id)
    window = max(int(cfg.get("history_inject", 12)), 1)
    latest, cursor = _summary_state(session_id)
    pending = latest - max(cursor or 0, 0) - window
    if pending < window * _SUMMARY_WINDOW_FACTOR:
        return existing
    if _summary_retry_until.get(session_id, 0.0) > time.monotonic():
        return existing
    with SessionLocal() as db:
        older = (
            db.query(ChatMessage)
            .filter(
                ChatMessage.session_id == session_id,
                ChatMessage.id > (cursor or 0),
                ChatMessage.id <= latest - window,
            )
            .order_by(ChatMessage.id.asc())
            .all()
        )
    lines = []
    for row in older:
        role = "用户" if row.role == "user" else "助手"
        text = (row.content or "").strip()
        if text:
            lines.append(f"{role}：{text[:_SUMMARY_MSG_MAX]}")
    transcript = "\n".join(lines)[-_SUMMARY_PROMPT_MAX:]
    if not transcript:
        return existing
    model = _get_routing_model()
    if model is None:
        return existing
    try:
        from agentscope.agent import Agent
        from agentscope.message import Msg, TextBlock

        parts = []
        if existing:
            parts.append(f"此前摘要：\n{existing}")
        parts.append(
            "以下是更早的对话记录，请压缩成一段不超过 500 字的中文摘要，"
            "保留关键事实、数据、决定与未尽事项，不要逐句罗列：\n" + transcript
        )
        summarizer = Agent(
            name="摘要员",
            system_prompt="你负责把长对话压缩成简洁准确的中文摘要，只输出摘要正文。",
            model=model,
        )
        reply = await summarizer.reply(
            Msg(name="用户", role="user", content=[TextBlock(type="text", text="\n\n".join(parts))])
        )
        text = (reply.get_text_content() or "").strip()
        if text:
            _save_summary_text(session_id, text)
            with SessionLocal() as db:
                agg_row = db.get(AgentSession, session_id)
                if agg_row is not None:
                    agg_row.summary_until = max((older[-1].id if older else 0), cursor or 0)
                    db.commit()
        if text:
            _summary_retry_until.pop(session_id, None)
        return text if text else existing
    except Exception as exc:
        _summary_retry_until[session_id] = time.monotonic() + _SUMMARY_RETRY_SECONDS
        logger.warning("会话摘要压缩失败（%s），沿用既有摘要并退避 %.0fs", type(exc).__name__, _SUMMARY_RETRY_SECONDS)
    return existing


# LLM 仲裁结果短窗缓存：键=(名册文本, 完整问题 SHA-256)，TTL 10 分钟，上限 128 条。
# 只缓存「成功解析出唯一专家」的仲裁；失败/None 不缓存（模型恢复后立即可用）。
_ROUTE_CACHE_TTL = 600.0
_ROUTE_CACHE_MAX = 128
_route_cache: dict[tuple[str, str], tuple[float, str]] = {}


async def _route_by_llm(question: str, team: dict[str, AgentSpec], session_id: str | None = None) -> str | None:
    """LLM 纯文本仲裁；任何失败都返回 None（回退关键词/默认）。"""
    from agentscope.agent import Agent
    from agentscope.message import Msg, TextBlock

    normalized_question = (question or "").strip()
    question_hash = hashlib.sha256(normalized_question.encode("utf-8")).hexdigest()
    cache_key = (roster_text(team), question_hash)
    hit = _route_cache.get(cache_key)
    if hit is not None and time.monotonic() - hit[0] < _ROUTE_CACHE_TTL:
        return hit[1]
    model = _get_routing_model(session_id=session_id) if session_id is not None else _get_routing_model()
    if model is None:
        return None
    try:
        router = Agent(
            name="调度员",
            system_prompt=(
                "你是 Agent 团队的任务调度员。根据团队名册和用户消息，"
                "选出最合适的一位专家。只做路由选择，不做具体回答；只返回一个 name。"
            ),
            model=model,
        )
        reply = await router.reply(
            Msg(
                name="用户",
                role="user",
                content=[TextBlock(type="text", text=f"团队名册：\n{roster_text(team)}\n\n用户消息：{question}")],
            )
        )
        name = parse_route_reply(reply.get_text_content() or "", team)
        if name is not None:
            _route_cache[cache_key] = (time.monotonic(), name)
            while len(_route_cache) > _ROUTE_CACHE_MAX:
                _route_cache.pop(next(iter(_route_cache)))
        return name
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
                trace=_govern_trace(trace),
                thinking=thinking,
            )
        )
        db.commit()


def _persist_user_turn(session_id: str, message: str) -> None:
    _touch_session(session_id, message)
    _persist(session_id, "user", "", message, [])


def _try_persist_assistant(session_id: str, spec: AgentSpec, text: str, trace: list, thinking: str) -> str | None:
    try:
        _persist(session_id, "assistant", spec.name, text, trace, thinking)
    except Exception:
        logger.warning("助手回复落库失败：session_id=%s", session_id, exc_info=True)
        return "回复已生成，但保存到会话失败；请复制保留本次回复，并检查数据库后再继续。"
    return None


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


EMPTY_TEAM_MESSAGE = "专家团队为空：所有专家均已停用，请在 AI 助手「专家」页面启用至少一位专家后再对话"


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
    if _is_job_session(session_id):
        picked = await _route_by_llm(question, team, session_id=session_id)
    else:
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
    """路由前的套餐预检：用「本轮最可能生效的模型」提前拒绝，避免先写 user 行再早退。

    明确指定专家时按该专家绑定模型（路由后不会触碰全局模型，不能用全局套餐误伤）；
    自动路由时路由本身可能调用全局激活模型仲裁，因此按全局模型预检，
    路由定到专家后再按实际 spec 复核一次（见 chat/chat_stream）。
    """
    team = load_team()
    if agent_name and agent_name in team:
        return _job_scope_refusal(session_id, team[agent_name])
    return _job_scope_refusal(session_id)


async def chat(
    session_id: str,
    message: str,
    agent_name: str | None = None,
    mode: str = "standard",
    attachments: list | None = None,
) -> dict:
    """处理一轮对话（非流式）：前置门禁通过后保存提问，再路由、执行并保存回复。"""
    if not llm_status()["configured"]:
        return {
            "ok": False,
            "need_llm_config": True,
            "message": "模型未配置：请在 AI 助手「⚙ 设置」中选择供应商并填写 API Key，保存后即可激活 Agent 团队。",
        }
    if not load_team():
        return {"ok": False, "message": EMPTY_TEAM_MESSAGE}
    refusal = _preroute_scope_refusal(session_id, agent_name)
    if refusal:
        return {"ok": False, "message": refusal}
    # 门禁通过才写入提问：早退路径不留孤立 user 行（下次对话不会注入被拒绝的问题）；
    # 模型/构建失败仍保留提问（写入发生在路由与调用之前）
    _persist_user_turn(session_id, message)

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
    bash_available = await _agent_bash_available(agent)

    from agentscope.message import Msg

    token = tool_trace.set([])
    try:
        try:
            reply = await agent.reply(
                Msg(
                    name="用户",
                    role="user",
                    content=_user_content_blocks(
                        message, attachments, spec, session_id=session_id, mode=mode,
                        allow_bash=bash_available,
                    ),
                )
            )
        except Exception as exc:
            # 用户消息已落库，补一条 assistant 侧错误消息，保持历史成对（与流式一致）
            err_text = f"抱歉，本次回复生成失败：{type(exc).__name__}"
            logger.warning("非流式对话执行失败：session_id=%s，%s", session_id, exc, exc_info=True)
            try:
                _persist(session_id, "assistant", spec.name, err_text, list(tool_trace.get() or []))
            except Exception:
                logger.warning("错误消息落库失败：session_id=%s", session_id, exc_info=True)
            raise
        except (asyncio.CancelledError, GeneratorExit):
            # 取消/断开也是 BaseException，不会被上面的 Exception 捕获；与流式一致
            # 补一条中断 assistant 行，避免历史只剩 user 行（M2）。
            try:
                _persist(session_id, "assistant", spec.name, "（已停止生成）", list(tool_trace.get() or []))
            except Exception:
                logger.warning("中断内容落库失败：session_id=%s", session_id, exc_info=True)
            raise
        text = (reply.get_text_content() or "").strip() or "（模型未返回内容）"
        text, inline_think = _split_inline_think(text)
        thinking = "\n".join(x for x in (inline_think, _thinking_of(reply)) if x).strip()
        trace = list(tool_trace.get() or [])
    finally:
        tool_trace.reset(token)

    persistence_warning = _try_persist_assistant(session_id, spec, text, trace, thinking)
    return {
        "ok": True,
        "session_id": session_id,
        "reply": text,
        "thinking": thinking,
        "agent": {"name": spec.name, "label": spec.label, "emoji": spec.emoji, "color": spec.color},
        "routed_by": routed_by,
        "mode": mode,
        "trace": trace,
        "persisted": persistence_warning is None,
        "persistence_warning": persistence_warning or "",
    }


async def chat_stream(
    session_id: str,
    message: str,
    agent_name: str | None = None,
    mode: str = "standard",
    attachments: list | None = None,
):
    """流式对话：前置门禁通过后保存提问，再逐段 yield 事件 dict，由路由层封装为 SSE。"""
    if not llm_status()["configured"]:
        yield {
            "type": "error",
            "need_llm_config": True,
            "message": "模型未配置：请在 AI 助手「⚙ 设置」中选择供应商并填写 API Key，保存后即可激活 Agent 团队。",
        }
        return
    if not load_team():
        yield {"type": "error", "message": EMPTY_TEAM_MESSAGE}
        return
    refusal = _preroute_scope_refusal(session_id, agent_name)
    if refusal:
        yield {"type": "error", "message": refusal}
        return
    # 门禁通过才写入提问：早退路径不留孤立 user 行；模型/流式失败仍保留提问
    _persist_user_turn(session_id, message)

    mode = _resolve_mode(mode)
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
    bash_available = await _agent_bash_available(agent)

    from agentscope.event import (
        ThinkingBlockDeltaEvent,
        TextBlockDeltaEvent,
        ToolCallStartEvent,
    )
    from agentscope.message import Msg

    token = tool_trace.set([])
    delta_parts: list[str] = []
    thinking_parts: list[str] = []
    assistant_persisted = False
    try:
        final_msg = None
        async for ev in agent.reply_stream(
            Msg(
                name="用户",
                role="user",
                content=_user_content_blocks(
                    message, attachments, spec, session_id=session_id, mode=mode,
                    allow_bash=bash_available,
                ),
            ),
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

    # 落库失败只是「回复已生成但未入历史」的非终止告警：只随 done 下发，
    # 不再发 type=error（error 在 SSE 契约里表示本轮终止，前端会把它渲染成失败气泡）
    persistence_warning = _try_persist_assistant(session_id, spec, text, trace, thinking)
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
        "persisted": persistence_warning is None,
        "persistence_warning": persistence_warning or "",
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
    # 同时看 active 占入集合与运行锁：请求先 begin_session_run、后取锁，两者之间
    # 存在「已占入但锁尚未持有」的窗口，只查 locked() 会把正在生成的会话删掉，
    # 随后本轮又把消息写回形成幽灵会话（R11-076）。
    # 与 begin_session_run 共享进程锁：检查后立即立「删除中」标记再删库，堵住反
    # 方向窗口——检查通过后新请求占入、删除完成后由 _touch_session 重建会话行（M1）。
    with _session_lifecycle_lock:
        lock = _session_run_locks.get(session_id)
        if session_id in _active_sessions or (lock is not None and lock.locked()):
            raise ValueError("当前会话正在生成中，请稍后再删除")
        _deleting_sessions.add(session_id)
    try:
        _delete_session_data(session_id)
    finally:
        with _session_lifecycle_lock:
            _deleting_sessions.discard(session_id)


def _delete_session_data(session_id: str) -> None:
    with SessionLocal() as db:
        row = db.get(AgentSession, session_id)
        if row is not None:
            db.delete(row)
        for msg in db.query(ChatMessage).filter(ChatMessage.session_id == session_id).all():
            db.delete(msg)
        db.commit()
    # 清理该会话生成的只读上下文文件（大段附件）
    try:
        from ..context_files import cleanup_session_context

        cleanup_session_context(session_id)
    except Exception:
        logger.warning("会话上下文文件清理失败（已忽略）：%s", session_id, exc_info=True)
    # 清理该会话的进程内 Agent 缓存与构建锁（与 invalidate/缓存淘汰共用守卫）
    with _agent_cache_guard:
        for key in [k for k in list(_agents) if k[0] == session_id]:
            _agents.pop(key, None)
        for key in [k for k in list(_agent_locks) if k[0] == session_id]:
            _agent_locks.pop(key, None)
            _agent_lock_last_used.pop(key, None)
    # 该会话按 (模型, 会话) 维度缓存的模型实例一并清理（含 SDK 连接池引用）
    for key in [k for k in list(_session_models) if k[1] == session_id]:
        _session_models.pop(key, None)
    _session_run_locks.pop(session_id, None)
    _session_run_lock_last_used.pop(session_id, None)


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
