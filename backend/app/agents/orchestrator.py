"""编排器：路由 → 专家 Agent 执行 → 轨迹与会话持久化。

路由策略：用户指定 > 关键词规则 > LLM 结构化调度 > 默认（商业地产管家）。
Agent 实例按 (会话, 专家, 模式) 缓存在进程内；会话消息持久化到 SQLite，
进程重启后按历史重建会话上下文。

工作模式（借鉴 QwenPaw Loop 模式）：
- standard 标准执行：全部工具，自动放行
- readonly 只读咨询：仅查询工具，不改任何数据
- deep    深度研究：全部工具 + 更高工具轮次 + 深度分析提示词
"""
import asyncio
import base64
import logging
import re
from collections import OrderedDict
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
    QUERY_TOOLS,
    TEAM,
    AgentSpec,
    default_agent_name,
    keyword_hit,
    load_team,
    roster_text,
)
from .tools import build_tools, tool_trace

logger = logging.getLogger(__name__)

_model = None
_models: dict[int, object | None] = {}  # 专家绑定模型缓存：model_id → 模型实例（None 表示不可用）
# 各会话 Agent 缓存（LRU：上限 64 个，超出淘汰最久未用，避免长驻服务内存无限增长）
_AGENT_CACHE_MAX = 64
_agents: "OrderedDict[tuple[str, str, str, int], object]" = OrderedDict()
_agent_locks: dict[tuple[str, str, str, int], asyncio.Lock] = {}


def _agent_cache_put(key: tuple[str, str, str, int], agent: object) -> None:
    _agents[key] = agent
    _agents.move_to_end(key)
    while len(_agents) > _AGENT_CACHE_MAX:
        _agents.popitem(last=False)

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
    _models.clear()
    _agents.clear()
    _agent_locks.clear()


def _get_model():
    global _model
    if _model is None:
        _model = build_model()
    return _model


def _get_spec_model(spec: AgentSpec):
    """专家生效模型：绑定 model_id 时按其配置构建（进程内缓存），为空/不可用回退全局激活模型。"""
    if spec.model_id:
        if spec.model_id not in _models:
            cfg = config_store.model_cfg_by_id(spec.model_id)
            _models[spec.model_id] = build_model(cfg) if cfg else None
            if _models[spec.model_id] is None:
                logger.warning("专家 %s 绑定的模型不可用（model_id=%s），回退全局激活模型", spec.name, spec.model_id)
        if _models[spec.model_id] is not None:
            return _models[spec.model_id]
    return _get_model()


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
    # 专家级 skills 为空列表时显式关闭；None 跟随全局配置
    skills_or_loaders = None
    if cfg.get("enable_skills") and cfg.get("skills_dir") and spec.skills is not None and not spec.skills:
        pass  # 专家配置显式关闭技能池
    elif cfg.get("enable_skills") and cfg.get("skills_dir"):
        skills_dir = Path(cfg["skills_dir"])
        if skills_dir.is_dir():
            try:
                from agentscope.skill import LocalSkillLoader

                skills_or_loaders = [LocalSkillLoader(directory=str(skills_dir), scan_subdir=True)]
            except Exception:
                logger.warning("技能池加载失败，本次不注入技能：%s", skills_dir, exc_info=True)
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
        model=_get_spec_model(spec),
        toolkit=toolkit,
        state=state,
        react_config=ReActConfig(max_iters=max_iters),
    )


def _get_agent(session_id: str, spec: AgentSpec, mode: str):
    if _get_spec_model(spec) is None:
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
            agent = await _build_agent(spec, mode)
            history = _load_history_msgs(session_id)
            if history:
                agent.observe(history)
            _agent_cache_put(key, agent)
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


async def _route_by_llm(question: str, team: dict[str, AgentSpec]) -> str | None:
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
                content=[TextBlock(type="text", text=f"团队名册：\n{roster_text(team)}\n\n用户消息：{question}")],
            ),
            structured_schema=RouteDecision,
        )
        decision = reply.structured_output or {}
        if decision.get("agent") in team:
            return decision["agent"]
    except Exception:
        logger.warning("LLM 结构化调度失败，回退关键词/默认路由", exc_info=True)
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


def _is_image_attachment(att) -> bool:
    """判断聊天附件是否为图片（按路径后缀，回退文件名后缀）。"""
    path = Path(str(att.get("path") or ""))
    suffix = path.suffix.lower() or Path(str(att.get("name") or "")).suffix.lower()
    return suffix in _IMAGE_ATTACH_SUFFIXES


def _vision_cfg(agent_name: str | None = None) -> dict:
    """当前生效模型配置：指定专家且绑定了模型时用其配置，否则用全局激活模型。"""
    if agent_name:
        try:
            spec = load_team().get(agent_name)
        except Exception:
            spec = None
        if spec is not None and spec.model_id:
            cfg = config_store.model_cfg_by_id(spec.model_id)
            if cfg:
                return cfg
    return config_store.load()


def assert_vision_supported(attachments, agent_name: str | None = None) -> None:
    """带图聊天前置校验：附件含图片而当前模型未开启视觉能力时抛 ValueError（路由层转 400）。

    模型整体未配置时不做拦截（走既有的 need_llm_config 引导）。
    """
    if not attachments or not any(_is_image_attachment(a) for a in attachments):
        return
    if not llm_status()["configured"]:
        return
    if not _vision_cfg(agent_name).get("supports_vision"):
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
    """路由：返回 (spec, routed_by)。专家团队读库（启用中），兜底内置定义。"""
    team = load_team()
    if agent_name in team:
        return team[agent_name], "指定"
    hit = keyword_hit(question, team)
    if hit:
        return team[hit], "关键词"
    picked = await _route_by_llm(question, team)
    if picked:
        return team[picked], "AI 调度"
    return team[default_agent_name(team)], "默认"


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


def _user_content_blocks(message: str, attachments, agent_name: str | None) -> list:
    """构造发给 LLM 的 user 消息内容块。

    文本部分 = 记忆上下文 + 文本类附件说明；当前模型开启视觉能力时，
    图片附件以 base64 DataBlock 追加（OpenAI 协议转 image_url，Anthropic 转原生图片块）。
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
        if _vision_cfg(agent_name).get("supports_vision"):
            image_blocks, notes = _image_blocks(attachments)
        else:
            notes.append(f"【图片附件】{VISION_UNSUPPORTED_MESSAGE}")
    if notes:
        text = f"{text}\n\n" + "\n".join(notes)
    return [TextBlock(type="text", text=text)] + image_blocks


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

    from agentscope.message import Msg

    token = tool_trace.set([])
    try:
        reply = await agent.reply(
            Msg(name="用户", role="user", content=_user_content_blocks(message, attachments, agent_name))
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

    from agentscope.event import (
        ThinkingBlockDeltaEvent,
        TextBlockDeltaEvent,
        ToolCallStartEvent,
    )
    from agentscope.message import Msg

    _touch_session(session_id, message)
    _persist(session_id, "user", "", message, [])

    token = tool_trace.set([])
    try:
        final_msg = None
        async for ev in agent.reply_stream(
            Msg(name="用户", role="user", content=_user_content_blocks(message, attachments, agent_name)),
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
        # 用户消息已落库，补一条 assistant 侧错误消息，保持历史成对
        err_text = f"抱歉，本次回复生成失败：{type(exc).__name__}"
        logger.warning("流式对话执行失败：session_id=%s，%s", session_id, exc, exc_info=True)
        try:
            _persist(session_id, "assistant", spec.name, err_text, [])
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
        "mode": mode,
        "trace": trace,
    }


# ---------------- 会话管理 ----------------

def sessions_list(limit: int = 50, archived: bool | None = None) -> list[dict]:
    """会话列表（按更新时间倒序）。

    archived=None 返回全部（含归档）；True/False 时按归档状态过滤。
    """
    try:
        with SessionLocal() as db:
            query = db.query(AgentSession)
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
    # 清理该会话的进程内 Agent 缓存
    for key in [k for k in list(_agents) if k[0] == session_id]:
        _agents.pop(key, None)


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
