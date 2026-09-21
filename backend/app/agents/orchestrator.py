"""编排器：路由 → 专家 Agent 执行 → 轨迹与会话持久化。

路由策略：用户指定 > 关键词规则 > LLM 结构化调度 > 默认（商业地产管家）。
Agent 实例按 (会话, 专家, 模式) 缓存在进程内；会话消息持久化到 SQLite，
进程重启后按历史重建会话上下文。

工作模式（借鉴 QwenPaw Loop 模式）：
- standard 标准执行：全部工具，自动放行
- readonly 只读咨询：仅查询工具，不改任何数据
- deep    深度研究：全部工具 + 更高工具轮次 + 深度分析提示词
"""
import re
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.exc import OperationalError

from ..database import SessionLocal
from ..models import AgentSession, ChatMessage
from . import config_store
from .llm import build_model, llm_status
from .team import (
    COMMON_RULES,
    DEFAULT_AGENT,
    QUERY_TOOLS,
    TEAM,
    AgentSpec,
    keyword_hit,
    roster_text,
)
from .tools import build_tools, tool_trace

_model = None
_agents: dict[tuple[str, str, str], object] = {}

# Agent 工作模式
WORK_MODES = {
    "standard": {"label": "标准执行", "hint": "全部工具，自动执行"},
    "readonly": {"label": "只读咨询", "hint": "仅查询分析，不修改任何数据"},
    "deep": {"label": "深度研究", "hint": "更高工具轮次，多角度深入分析"},
}

DEEP_SUFFIX = "\n\n【当前模式：深度研究】请进行更全面深入的多步分析：先调用查询工具核实工作台数据，再从多个角度交叉验证与拆解，最后给出结构化的结论、依据与建议。"
READONLY_SUFFIX = "\n\n【当前模式：只读咨询】本次仅提供分析与建议，不修改任何工作台数据（写入类工具已对你关闭）。"


class RouteDecision(BaseModel):
    """LLM 路由的结构化输出。"""

    agent: str = Field(description="专家标识，必须是名册中列出的 name")
    reason: str = Field(description="选择理由，20 字以内")


def invalidate() -> None:
    """配置变更后清空进程内缓存（模型实例与各会话 Agent）。"""
    global _model
    _model = None
    _agents.clear()


def _get_model():
    global _model
    if _model is None:
        _model = build_model()
    return _model


def _resolve_mode(mode: str | None) -> str:
    return mode if mode in WORK_MODES else "standard"


async def _build_agent(spec: AgentSpec, mode: str = "standard"):
    from pathlib import Path

    from agentscope.agent import Agent, ReActConfig
    from agentscope.permission import PermissionContext, PermissionMode
    from agentscope.state import AgentState
    from agentscope.tool import Toolkit

    cfg = config_store.load()
    tool_names = list(spec.tools)
    if mode == "readonly":
        tool_names = [t for t in tool_names if t in QUERY_TOOLS]

    # 本地技能池（SKILL.md 格式，如 QwenPaw skill_pool）：经 LocalSkillLoader 注入
    skills_or_loaders = None
    if cfg.get("enable_skills") and cfg.get("skills_dir"):
        skills_dir = Path(cfg["skills_dir"])
        if skills_dir.is_dir():
            try:
                from agentscope.skill import LocalSkillLoader

                skills_or_loaders = [LocalSkillLoader(directory=str(skills_dir), scan_subdir=True)]
            except Exception:
                skills_or_loaders = None

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
            pass

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
        model=_get_model(),
        toolkit=toolkit,
        state=state,
        react_config=ReActConfig(max_iters=max_iters),
    )


def _get_agent(session_id: str, spec: AgentSpec, mode: str):
    if _get_model() is None:
        raise RuntimeError("LLM 未配置")
    key = (session_id, spec.name, mode)
    agent = _agents.get(key)
    if agent is None:
        raise KeyError(key)  # 由调用方构建
    return agent


async def _ensure_agent(session_id: str, spec: AgentSpec, mode: str):
    try:
        return _get_agent(session_id, spec, mode)
    except KeyError:
        agent = await _build_agent(spec, mode)
        history = _load_history_msgs(session_id)
        if history:
            agent.observe(history)
        _agents[(session_id, spec.name, mode)] = agent
        return agent


def _load_history_msgs(session_id: str, limit: int | None = None) -> list:
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


async def _route_by_llm(question: str) -> str | None:
    """LLM 结构化调度；任何失败都返回 None（回退关键词/默认）。"""
    from agentscope.agent import Agent
    from agentscope.message import Msg, TextBlock

    if _get_model() is None:
        return None
    try:
        router = Agent(
            name="调度员",
            system_prompt=(
                "你是 Agent 团队的任务调度员。根据团队名册和用户消息，"
                "选出最合适的一位专家。只做路由选择，不做具体回答。"
            ),
            model=_get_model(),
        )
        reply = await router.reply(
            Msg(
                name="用户",
                role="user",
                content=[TextBlock(type="text", text=f"团队名册：\n{roster_text()}\n\n用户消息：{question}")],
            ),
            structured_schema=RouteDecision,
        )
        decision = reply.structured_output or {}
        if decision.get("agent") in TEAM:
            return decision["agent"]
    except Exception:
        pass
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
                row.updated_at = datetime.now()
            db.commit()
    except OperationalError:
        pass


_THINK_TAG_RE = re.compile(r"<think>(.*?)</think>", re.DOTALL)

# 附件按文本注入的白名单后缀与大小上限
_TEXT_SUFFIXES = {".txt", ".md", ".csv", ".json", ".log", ".py", ".html", ".xml", ".yaml", ".yml", ".sql"}


def _attachments_block(attachments) -> str:
    """把聊天附件拼成可注入消息的文本块（安全：仅允许上传目录内的文件）。"""
    from ..config import UPLOAD_DIR

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
        if path.suffix.lower() in _TEXT_SUFFIXES and size <= 300_000:
            content = path.read_text(encoding="utf-8", errors="replace")[:80000]
            parts.append(
                f"【附件文件：{name}（{size} 字节）内容如下】\n\n```\n{content}\n```\n\n"
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


async def _resolve_spec(question: str, agent_name: str | None):
    """路由：返回 (spec, routed_by)。"""
    if agent_name in TEAM:
        return TEAM[agent_name], "指定"
    hit = keyword_hit(question)
    if hit:
        return TEAM[hit], "关键词"
    picked = await _route_by_llm(question)
    return TEAM[picked or DEFAULT_AGENT], ("AI 调度" if picked else "默认")


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

    mode = _resolve_mode(mode)
    spec, routed_by = await _resolve_spec(message, agent_name)
    agent = await _ensure_agent(session_id, spec, mode)

    full_message = message + ("\n\n" + _attachments_block(attachments) if attachments else "")

    from agentscope.message import Msg, TextBlock

    token = tool_trace.set([])
    try:
        reply = await agent.reply(
            Msg(name="用户", role="user", content=[TextBlock(type="text", text=full_message)])
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
    spec, routed_by = await _resolve_spec(message, agent_name)
    yield {"type": "meta", "agent": {"name": spec.name, "label": spec.label, "emoji": spec.emoji, "color": spec.color}, "routed_by": routed_by, "mode": mode}

    agent = await _ensure_agent(session_id, spec, mode)

    full_message = message + ("\n\n" + _attachments_block(attachments) if attachments else "")

    from agentscope.event import (
        ThinkingBlockDeltaEvent,
        TextBlockDeltaEvent,
        ToolCallStartEvent,
    )
    from agentscope.message import Msg, TextBlock

    _touch_session(session_id, message)
    _persist(session_id, "user", "", message, [])

    token = tool_trace.set([])
    try:
        final_msg = None
        async for ev in agent.reply_stream(
            Msg(name="用户", role="user", content=[TextBlock(type="text", text=full_message)]),
            yield_final_msg=True,
        ):
            if isinstance(ev, TextBlockDeltaEvent):
                if ev.delta:
                    yield {"type": "delta", "text": ev.delta}
            elif isinstance(ev, ThinkingBlockDeltaEvent):
                if ev.delta:
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
    except Exception as exc:
        yield {"type": "error", "message": f"Agent 执行失败：{type(exc).__name__}: {exc}"[:300]}
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
        "mode": mode,
        "trace": trace,
    }


# ---------------- 会话管理 ----------------

def sessions_list(limit: int = 50) -> list[dict]:
    try:
        with SessionLocal() as db:
            rows = (
                db.query(AgentSession)
                .order_by(AgentSession.updated_at.desc())
                .limit(max(1, min(limit, 200)))
                .all()
            )
            counts = dict(db.query(ChatMessage.session_id, func.count()).group_by(ChatMessage.session_id).all())
    except OperationalError:
        return []
    return [
        {
            "session_id": r.session_id,
            "title": r.title or "新会话",
            "msg_count": counts.get(r.session_id, 0),
            "updated_at": r.updated_at.strftime("%m-%d %H:%M"),
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
    # 清理该会话的进程内 Agent 缓存
    for key in [k for k in list(_agents) if k[0] == session_id]:
        _agents.pop(key, None)


def history(session_id: str, limit: int = 50) -> list[dict]:
    with SessionLocal() as db:
        rows = (
            db.query(ChatMessage)
            .filter(ChatMessage.session_id == session_id)
            .order_by(ChatMessage.id.desc())
            .limit(max(1, min(limit, 200)))
            .all()
        )
    return [
        {
            "id": r.id,
            "role": r.role,
            "agent_name": r.agent_name,
            "agent_label": TEAM[r.agent_name].label if r.agent_name in TEAM else r.agent_name,
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
            }
            for s in TEAM.values()
        ],
    }
