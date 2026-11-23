"""长期记忆：跨会话事实/偏好/经验的存取与检索注入。

设计（ADR-004 口径）：
- 存储：`memories` 表（content/source/tags/pinned/hit_count）；
- 写入：手动保存为主；「从最近对话抽取」为一次性动作（用户触发，不常驻自动），
  抽取走既有 LLM 编排器，产物经去重（近重复 containment 检查）后入库；
- 检索：关键词（LIKE 多词 OR）+ 置顶/新近度加权；`recall_memories` 注册为
  只读 Agent 工具；编排器在每次对话前把 Top-N 相关记忆注入用户消息上下文；
- 向量记忆（embedding）留待配置 embedding 端点后按同接口扩展。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..database import SessionLocal
from ..models import KbDoc, Memory
from ..utils import escape_like

MAX_CONTENT = 2000
RECALL_TOP_K = 5

# 通用功能字：含任一字符的 bigram 视为「通用 token」，不参与多 token 查询的
# 有效性判断（仅通用 bigram 重叠不应召回无关记忆）。
_GENERIC_CHARS = "的是了在和与或及对为把被得地这那就也都又还吗呢吧啊么呀嘛哦"


def _is_generic_token(token: str) -> bool:
    return any(ch in _GENERIC_CHARS for ch in token)


# ---------- 存取 ----------

def add_memory(db: Session, content: str, source: str = "manual",
               session_id: str = "", tags: list[str] | None = None,
               pinned: bool = False, type: str = "",
               source_doc_id: int | None = None) -> dict:
    content = (content or "").strip()
    if not content:
        raise ValueError("记忆内容不能为空")
    if len(content) > MAX_CONTENT:
        content = content[:MAX_CONTENT]
    if _is_duplicate(db, content):
        raise ValueError("已存在基本相同的记忆，未重复保存")
    doc_title = ""
    if source_doc_id is not None:
        doc_title = _resolve_source_doc(db, source_doc_id).title
    row = Memory(content=content, source=source, session_id=session_id,
                 tags=tags or [], pinned=bool(pinned),
                 type=_norm_type(type), source_doc_id=source_doc_id)
    db.add(row)
    db.commit()
    db.refresh(row)
    return _out(row, doc_title=doc_title)


def _resolve_source_doc(db: Session, source_doc_id) -> KbDoc:
    """校验显式引用的知识库文档：非法/不存在一律 ValueError（路由层转 400）。"""
    if not isinstance(source_doc_id, int) or isinstance(source_doc_id, bool) \
            or source_doc_id <= 0:
        raise ValueError("引用的知识库文档 id 必须为正整数")
    doc = db.get(KbDoc, source_doc_id)
    if doc is None:
        raise ValueError(f"引用的知识库文档 {source_doc_id} 不存在")
    return doc


MEMORY_TYPES = ("user", "feedback", "project", "reference")


def _norm_type(t: str | None) -> str:
    """类型归一：仅接受四类白名单，非法值归空（未分类）。"""
    t = str(t or "").strip().lower()
    return t if t in MEMORY_TYPES else ""


def _content_samples(db: Session) -> list[str]:
    """一次取回全部既有记忆内容，供批量导入复用（避免每条全表扫）。"""
    return [row[0] for row in db.query(Memory.content).all() if row[0]]


def _duplicate_against(content: str, samples: list[str]) -> bool:
    sample = content[:80]
    return any(sample in existing[:80] or existing[:80] in sample for existing in samples)


def _is_duplicate(db: Session, content: str) -> bool:
    """近重复检查：与既有记忆互相高覆盖率包含即视为重复。"""
    return _duplicate_against(content, _content_samples(db))


def add_memories(db: Session, entries: list, source: str = "import",
                 default_type: str = "", session_id: str = "") -> dict:
    """批量保存：一次性预取既有内容做近重复判断，避免逐条全表扫描。

    entries 支持字符串或 {content, type, tags, pinned, session_id} 字典。
    返回 {added, skipped, skipped_empty, items}；批内重复同样跳过。
    """
    samples = _content_samples(db)
    rows: list[Memory] = []
    skipped = skipped_empty = 0
    try:
        for entry in entries:
            if isinstance(entry, str):
                content, type_, tags, pinned, sid = entry, default_type, None, False, session_id
            else:
                content = entry.get("content", "")
                type_ = entry.get("type", default_type)
                tags = entry.get("tags")
                pinned = bool(entry.get("pinned", False))
                sid = entry.get("session_id", session_id)
            content = (content or "").strip()
            if not content:
                skipped_empty += 1
                continue
            if len(content) > MAX_CONTENT:
                content = content[:MAX_CONTENT]
            if _duplicate_against(content, samples):
                skipped += 1
                continue
            row = Memory(content=content, source=source, session_id=sid or "",
                         tags=tags or [], pinned=pinned, type=_norm_type(type_))
            db.add(row)
            db.flush()
            samples.append(content)
            rows.append(row)
        db.commit()
    except Exception:
        # 整批一个事务：任一行失败（含 flush/commit）全部回滚，不留下半批记忆。
        db.rollback()
        raise
    return {"added": len(rows), "skipped": skipped, "skipped_empty": skipped_empty,
            "items": [_out(row) for row in rows]}


def update_memory(db: Session, memory_id: int, data: dict) -> dict:
    row = db.get(Memory, memory_id)
    if row is None:
        raise LookupError("记忆不存在")
    if "content" in data and data["content"] is not None:
        content = data["content"].strip()
        if not content:
            raise ValueError("记忆内容不能为空")
        row.content = content[:MAX_CONTENT]
    if "tags" in data and data["tags"] is not None:
        row.tags = data["tags"]
    if "pinned" in data and data["pinned"] is not None:
        row.pinned = bool(data["pinned"])
    if "type" in data and data["type"] is not None:
        row.type = _norm_type(data["type"])
    if "source_doc_id" in data:
        if data["source_doc_id"] is None:
            row.source_doc_id = None  # 显式传 null = 清空引用
        else:
            row.source_doc_id = _resolve_source_doc(db, data["source_doc_id"]).id
    db.commit()
    db.refresh(row)
    # 标题按最终生效的 source_doc_id 取：未触碰来源字段时既有引用保持原样输出
    titles = _doc_titles(db, [row])
    return _out(row, doc_title=titles.get(row.source_doc_id))


def delete_memory(db: Session, memory_id: int) -> None:
    row = db.get(Memory, memory_id)
    if row is None:
        raise LookupError("记忆不存在")
    db.delete(row)
    db.commit()


def list_memories(db: Session, keyword: str = "", limit: int = 100,
                  type: str = "") -> list[dict]:
    q = db.query(Memory)
    if keyword:
        # 关键词按字面匹配：% / _ / 反斜杠经 escape_like 转义 + 显式 escape，
        # 不通配放大结果集（审计台账 A08）
        q = q.filter(Memory.content.like(f"%{escape_like(keyword)}%", escape="\\"))
    t = _norm_type(type)
    if t:
        q = q.filter(Memory.type == t)
    rows = q.order_by(Memory.pinned.desc(), Memory.id.desc()).limit(min(limit, 500)).all()
    titles = _doc_titles(db, rows)
    return [_out(r, doc_title=titles.get(r.source_doc_id)) for r in rows]


def _doc_titles(db: Session, rows: list[Memory]) -> dict[int, str]:
    """按 source_doc_id 批量取文档标题（一次查询，避免逐条 N+1）。"""
    ids = sorted({r.source_doc_id for r in rows if r.source_doc_id})
    if not ids:
        return {}
    found = dict(db.query(KbDoc.id, KbDoc.title).filter(KbDoc.id.in_(ids)).all())
    return {i: found[i] for i in ids if i in found}


def _out(r: Memory, doc_title: str | None = None) -> dict:
    return {
        "id": r.id,
        "content": r.content,
        "source": r.source,
        "type": r.type or "",
        "session_id": r.session_id,
        "tags": r.tags or [],
        "pinned": bool(r.pinned),
        "hit_count": r.hit_count,
        "source_doc_id": r.source_doc_id,
        "source_doc_title": doc_title or "",
        "created_at": r.created_at.strftime("%Y-%m-%d %H:%M") if r.created_at else "",
    }


# ---------- 检索（供 Agent 工具与上下文注入共用） ----------

def recall(query: str, top_k: int = RECALL_TOP_K) -> list[dict]:
    """检索记忆：中文 bigram 重叠打分 + 置顶/新近度加权；命中即累计 hit_count。

    打分口径（噪声抑制是「降低」，不是完全消除）：
    - token 与记忆内容统一 casefold，英文大小写不敏感；
    - 查询 token 先按集合去重，再剔除含通用功能字的 bigram 得到「有效 token」；
      有效 token ≥2 时要求 ≥2 个重叠——仅 1 个有效 token 重叠的多 token
      查询不再召回无关记忆；
    - 有效 token 为 0 或 1 时（单 token、短查询、全通用词查询）回退宽松
      阈值 min_overlap=1：全通用词查询可能仍召回通用重叠的记忆，
      换取短查询不静默失效。

    单用户规模（数百条）内一次性取「置顶 + 最近 300 条」在内存打分，
    避免中文无分词条件下的 LIKE 全句匹配失效。
    """
    from .rag import _query_tokens

    q_tokens = {t.casefold() for t in _query_tokens(query or "")}
    if not q_tokens:
        return []  # 纯空白/标点查询无有效 token，不返回任意结果也不累计 hit_count
    kept = {t for t in q_tokens if not _is_generic_token(t)}
    if kept:
        q_tokens = kept
        min_overlap = 1 if len(kept) <= 1 else 2
    else:
        # 查询全是通用 token（如「什么怎么」）：回退原始 token + 宽松阈值，避免静默失效
        min_overlap = 1
    with SessionLocal() as db:
        pinned = (
            db.query(Memory)
            .filter(Memory.pinned.is_(True))
            .order_by(Memory.id.desc())
            .all()
        )
        recent = (
            db.query(Memory)
            .filter(Memory.pinned.is_(False))
            .order_by(Memory.id.desc())
            .limit(300)
            .all()
        )
        scored = []
        for r in [*pinned, *recent]:
            content_cf = (r.content or "").casefold()
            overlap = sum(1 for t in q_tokens if t in content_cf)
            if overlap < min_overlap:
                continue
            score = overlap * 1.0
            if r.pinned:
                score += 2.0
            if getattr(r, "type", "") in ("user", "feedback"):
                score += 0.5  # 用户画像与纠正类记忆权重略高（zcode 四类口径）
            score += min(r.hit_count or 0, 10) * 0.05
            scored.append((score, r))
        if not scored:
            return []
        scored.sort(key=lambda x: (x[0], x[1].id), reverse=True)
        picked = scored[: max(1, min(top_k, 10))]
        titles = _doc_titles(db, [r for _, r in picked])
        for _, r in picked:
            r.hit_count = (r.hit_count or 0) + 1
            r.last_used_at = datetime.now()
        db.commit()
        return [_out(r, doc_title=titles.get(r.source_doc_id)) for _, r in picked]


def recall_for_context(message: str, top_k: int = 3) -> list[dict]:
    """编排器注入用：从用户消息提取关键词检索，条数受 agent_settings.memory_inject_max 预算约束。"""
    if not message or not message.strip():
        return []
    budget = 3
    try:
        from ..database import SessionLocal as _SL
        from ..models import AgentSettings
        with _SL() as s:
            row = s.query(AgentSettings).first()
            if row is not None and row.memory_inject_max is not None:
                budget = max(0, min(int(row.memory_inject_max), 10))
    except Exception:  # noqa: BLE001  设置不可读时用默认预算
        budget = 3
    if budget <= 0:
        return []
    return recall(message, top_k=min(top_k, budget))


# ---------- 统计 / 导出 / 导入（Part E） ----------

def memory_stats(db: Session) -> dict:
    """记忆统计：总数/来源分布/类型分布/置顶数/命中 Top5。"""
    rows = db.query(Memory).all()
    by_source: dict[str, int] = {}
    by_type: dict[str, int] = {}
    for r in rows:
        by_source[r.source or "manual"] = by_source.get(r.source or "manual", 0) + 1
        t = r.type or "未分类"
        by_type[t] = by_type.get(t, 0) + 1
    top_hits = sorted(rows, key=lambda r: -(r.hit_count or 0))[:5]
    return {
        "total": len(rows),
        "by_source": by_source,
        "by_type": by_type,
        "pinned": sum(1 for r in rows if r.pinned),
        "top_hits": [
            {"id": r.id, "content": (r.content or "")[:60], "hit_count": r.hit_count}
            for r in top_hits if (r.hit_count or 0) > 0
        ],
    }


def export_markdown() -> str:
    """导出全部记忆为 MEMORY.md 风格 Markdown（zcode/WorkBuddy 兼容形态）。"""
    with SessionLocal() as db:
        rows = db.query(Memory).order_by(Memory.type.asc(), Memory.id.desc()).all()
    lines = ["# MEMORY.md（工作台记忆导出）", ""]
    current_type = None
    for r in rows:
        t = r.type or "未分类"
        if t != current_type:
            lines.append(f"## {t}")
            current_type = t
        pinned_mark = " [置顶]" if r.pinned else ""
        lines.append(f"- {r.content}{pinned_mark}")
    return "\n".join(lines) + "\n"


def import_markdown(db: Session, text: str) -> dict:
    """导入 MEMORY.md 风格 Markdown：## 节=类型，- 列表行=记忆条目（近重复跳过）。"""
    current_type = ""
    entries: list[dict] = []
    for raw in (text or "").splitlines():
        line = raw.rstrip()
        if line.startswith("## "):
            head = line[3:].strip()
            current_type = head if head in MEMORY_TYPES else ""
        elif line.startswith("- "):
            content = line[2:].strip()
            content = content.replace(" [置顶]", "")
            if not content:
                continue
            entries.append({"content": content, "type": current_type})
    result = add_memories(db, entries, source="import")
    return {"added": result["added"], "skipped": result["skipped"]}


# ---------- LLM 抽取（一次性动作） ----------

EXTRACT_PROMPT = """你是记忆抽取器。从下面的对话中抽取「值得跨会话长期记住」的事实/偏好/经验。
规则：只抽稳定、可复用、与用户工作生活相关的条目（如偏好、习惯、项目背景、常用口径）；
每行一条，每条不超过 80 字；不要抽一次性信息；没有值得记的就输出空。
对话内容：
---
{conversation}
---

请只输出条目（每行一条），不要编号、不要解释。"""


async def extract_from_conversation(conversation: str) -> dict:
    """调 LLM 从对话文本抽取记忆条目并去重入库。返回 {extracted, saved}。"""
    from .llm import llm_status
    from .orchestrator import chat as orchestrator_chat

    if not llm_status().get("configured"):
        return {"extracted": 0, "saved": 0, "error": "模型未配置"}
    conversation = (conversation or "").strip()
    if len(conversation) < 20:
        return {"extracted": 0, "saved": 0, "error": "对话内容太短"}

    result = await orchestrator_chat(
        session_id="memory-extract",
        message=EXTRACT_PROMPT.format(conversation=conversation[-6000:]),
        mode="readonly",
    )
    if not result.get("ok"):
        return {"extracted": 0, "saved": 0, "error": result.get("message", "抽取失败")}

    lines = [ln.strip(" -•·\t") for ln in (result.get("reply") or "").splitlines()]
    entries = [ln for ln in lines if 8 <= len(ln) <= 200][:10]
    saved = 0
    with SessionLocal() as db:
        result = add_memories(db, entries, source="auto", session_id="memory-extract")
        saved = result["added"]
    return {"extracted": len(entries), "saved": saved}
