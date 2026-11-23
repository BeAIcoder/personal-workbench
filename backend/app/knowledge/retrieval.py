"""知识库检索：关键词（FTS5 BM25）与向量（sqlite-vec）混合，返回可引用结果。

融合策略用 RRF（Reciprocal Rank Fusion）：关键词与向量各自出一个文档级排名，
`score = Σ 1/(60 + rank)`。它不依赖两路分数量纲，工程上比调权重稳，
也是 RAGFlow / Dify 一类系统的常见做法。

制度检索默认只返回 `effective`（现行有效）；历史版本需显式 include_inactive。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.orm import Session

from ..agents import rag
from ..models import KbChunk, KbDoc
from . import embedding as embedding_svc
from . import index as index_svc
from .tokenize import fts_query, query_tokens

RRF_K = 60
SNIPPET_CHARS = 180
MAX_READ_CHARS = 20_000


@dataclass
class DocHit:
    doc: KbDoc
    chunk: KbChunk
    score: float
    keyword_rank: int | None = None
    vector_rank: int | None = None
    distance: float | None = None


def _snippet(text_value: str, query: str, limit: int = SNIPPET_CHARS) -> str:
    body = re.sub(r"\s+", " ", str(text_value or "")).strip()
    if not body:
        return ""
    for token in sorted(query_tokens(query), key=len, reverse=True):
        if len(token) < 2:
            continue
        found = body.lower().find(token.lower())
        if found >= 0:
            start = max(0, found - 40)
            fragment = body[start : start + limit]
            return ("…" if start > 0 else "") + fragment + ("…" if start + limit < len(body) else "")
    return body[:limit] + ("…" if len(body) > limit else "")


def citation_of(doc: KbDoc, chunk: KbChunk | None = None) -> str:
    parts = [f"《{doc.title}》"]
    if doc.version:
        parts.append(str(doc.version))
    if chunk is not None:
        heading = chunk.heading or ""
        # 标题路径首层通常就是文档标题，引用里去掉避免「《X制度》· X制度 > 第三章」重复
        title_prefix = f"{doc.title} > "
        if heading.startswith(title_prefix):
            heading = heading[len(title_prefix):]
        if heading:
            parts.append(heading)
        if chunk.page_no:
            parts.append(f"第 {chunk.page_no} 页")
    return " · ".join(parts)


def _doc_filter_sql(
    *,
    status: str,
    category: str,
    department: str,
    include_inactive: bool,
    doc_id: int | None = None,
) -> tuple[str, dict]:
    clauses: list[str] = []
    params: dict = {}
    if not include_inactive:
        clauses.append("d.status = :status")
        params["status"] = status or "effective"
    elif status:
        clauses.append("d.status = :status")
        params["status"] = status
    if category:
        clauses.append("d.category = :category")
        params["category"] = category
    if department:
        clauses.append("d.department = :department")
        params["department"] = department
    if doc_id:
        clauses.append("d.id = :doc_id")
        params["doc_id"] = doc_id
    return (" AND " + " AND ".join(clauses)) if clauses else "", params


def _keyword_hits(
    db: Session,
    query: str,
    limit: int,
    *,
    status: str,
    category: str,
    department: str,
    include_inactive: bool,
    doc_id: int | None = None,
) -> list[DocHit]:
    where, params = _doc_filter_sql(
        status=status,
        category=category,
        department=department,
        include_inactive=include_inactive,
        doc_id=doc_id,
    )
    match = fts_query(query)
    rows: list[tuple[int, float]] = []
    if match and index_svc.fts_available(db):
        try:
            rows = [
                (int(row[0]), float(row[1]))
                for row in db.execute(
                    text(
                        "SELECT kb_fts.chunk_id, bm25(kb_fts) AS rank "
                        "FROM kb_fts "
                        "JOIN kb_chunks c ON c.id = kb_fts.chunk_id "
                        "JOIN kb_docs d ON d.id = c.doc_id "
                        f"WHERE kb_fts MATCH :q{where} "
                        "ORDER BY rank LIMIT :limit"
                    ),
                    {"q": match, "limit": limit, **params},
                ).all()
            ]
        except Exception:
            rows = []
    if not rows:
        # 单字/无 bigram 查询或 FTS 不可用：退化为 LIKE（有界）
        keyword = str(query or "").strip()
        if not keyword:
            return []
        from ..utils import escape_like

        like = f"%{escape_like(keyword)}%"
        rows = [
            (int(row[0]), 0.0)
            for row in db.execute(
                text(
                    "SELECT c.id, 0.0 AS rank "
                    "FROM kb_chunks c JOIN kb_docs d ON d.id = c.doc_id "
                    f"WHERE c.text LIKE :like ESCAPE '\\'{where} "
                    "ORDER BY c.doc_id, c.seq LIMIT :limit"
                ),
                {"like": like, "limit": limit, **params},
            ).all()
        ]

    chunk_ids = [chunk_id for chunk_id, _ in rows]
    if not chunk_ids:
        return []
    chunks = {row.id: row for row in db.query(KbChunk).filter(KbChunk.id.in_(chunk_ids)).all()}
    doc_ids = {row.doc_id for row in chunks.values()}
    docs = {row.id: row for row in db.query(KbDoc).filter(KbDoc.id.in_(doc_ids)).all()}
    hits: list[DocHit] = []
    for rank, (chunk_id, score) in enumerate(rows, start=1):
        chunk = chunks.get(chunk_id)
        doc = docs.get(chunk.doc_id) if chunk else None
        if chunk is None or doc is None:
            continue
        hits.append(DocHit(doc=doc, chunk=chunk, score=-score, keyword_rank=rank))
    return hits


def _vector_hits(
    db: Session,
    query: str,
    limit: int,
    *,
    status: str,
    category: str,
    department: str,
    include_inactive: bool,
    doc_id: int | None = None,
) -> tuple[list[DocHit], str]:
    cfg = embedding_svc.resolve_config(db)
    if cfg is None:
        return [], ""
    if not index_svc.vector_available(db) or index_svc._vec_dim(db) is None:
        return [], "向量索引不可用"
    try:
        vectors, _ = embedding_svc.embed_texts_sync([query], cfg)
    except embedding_svc.EmbeddingError as exc:
        return [], str(exc)
    if not vectors:
        return [], ""
    import struct

    packed = struct.pack(f"{len(vectors[0])}f", *vectors[0])
    where, params = _doc_filter_sql(
        status=status,
        category=category,
        department=department,
        include_inactive=include_inactive,
        doc_id=doc_id,
    )
    try:
        rows = db.execute(
            text(
                "SELECT v.chunk_id, v.distance "
                "FROM kb_vec v "
                "JOIN kb_chunks c ON c.id = v.chunk_id "
                "JOIN kb_docs d ON d.id = c.doc_id "
                f"WHERE v.embedding MATCH :vec AND k = :k{where} "
                "ORDER BY v.distance"
            ),
            {"vec": packed, "k": max(limit, 1), **params},
        ).all()
    except Exception as exc:
        return [], f"向量检索失败：{type(exc).__name__}"
    chunk_ids = [int(row[0]) for row in rows]
    if not chunk_ids:
        return [], ""
    chunks = {row.id: row for row in db.query(KbChunk).filter(KbChunk.id.in_(chunk_ids)).all()}
    doc_ids = {row.doc_id for row in chunks.values()}
    docs = {row.id: row for row in db.query(KbDoc).filter(KbDoc.id.in_(doc_ids)).all()}
    hits: list[DocHit] = []
    for rank, (chunk_id, distance) in enumerate(rows, start=1):
        chunk = chunks.get(int(chunk_id))
        doc = docs.get(chunk.doc_id) if chunk else None
        if chunk is None or doc is None:
            continue
        hits.append(
            DocHit(
                doc=doc,
                chunk=chunk,
                score=1.0 / (1.0 + float(distance)),
                vector_rank=rank,
                distance=float(distance),
            )
        )
    return hits, ""


def _channel_best(hits: list[DocHit]) -> dict[int, tuple[int, DocHit]]:
    """单通道内每文档的最佳 (rank, hit)；列表已按 rank 升序，文档首次出现即最佳。

    RRF 融合只取每通道每文档最佳 rank：同一文档多个块命中时不得重复累加
    `1/(RRF_K + rank)`，否则多块文档会堆分压制单块最佳文档。
    """
    best: dict[int, tuple[int, DocHit]] = {}
    for rank, hit in enumerate(hits, start=1):
        best.setdefault(hit.doc.id, (rank, hit))
    return best


def _pick_display(
    kw: tuple[int, DocHit] | None, vec: tuple[int, DocHit] | None
) -> DocHit | None:
    """展示块：在关键词/向量各自最佳候选中按通道 rank 择优，并列时关键词优先。

    不再无条件 keyword_rank 优先——向量 rank 1 的最佳证据不应被关键词
    rank 更差的块覆盖。
    """
    if kw is None:
        return vec[1] if vec is not None else None
    if vec is None:
        return kw[1]
    return kw[1] if kw[0] <= vec[0] else vec[1]


def search_items(
    query: str,
    top_k: int = 5,
    *,
    scope: str = "all",
    category: str = "",
    department: str = "",
    status: str = "",
    include_inactive: bool = False,
    doc_id: int | None = None,
) -> list[dict]:
    """统一检索：制度文档 + 笔记，RRF 融合后返回带引用的条目。"""
    from ..database import SessionLocal

    query = str(query or "").strip()
    if not query:
        return []
    top_k = max(1, min(int(top_k or 5), 20))
    scope = (scope or "all").strip().lower()
    with SessionLocal() as db:
        keyword = []
        vector = []
        vector_note = ""
        if scope in ("all", "docs", "doc"):
            fetch = max(top_k * 4, 20)
            keyword = _keyword_hits(
                db,
                query,
                fetch,
                status=status,
                category=category,
                department=department,
                include_inactive=include_inactive,
                doc_id=doc_id,
            )
            vector, vector_note = _vector_hits(
                db,
                query,
                fetch,
                status=status,
                category=category,
                department=department,
                include_inactive=include_inactive,
                doc_id=doc_id,
            )

        notes = rag.search(query, top_k=max(top_k, 5)) if scope in ("all", "notes", "note") else []

        doc_scores: dict[int, float] = {}
        kw_best = _channel_best(keyword)
        vec_best = _channel_best(vector)
        for channel in (kw_best, vec_best):
            for doc_key, (rank, _) in channel.items():
                doc_scores[doc_key] = doc_scores.get(doc_key, 0.0) + 1.0 / (RRF_K + rank)

        items: list[dict] = []
        for doc_key, score in doc_scores.items():
            hit = _pick_display(kw_best.get(doc_key), vec_best.get(doc_key))
            if hit is None:
                continue
            items.append(
                {
                    "source": "doc",
                    "id": hit.doc.id,
                    "title": hit.doc.title,
                    "snippet": _snippet(hit.chunk.text, query),
                    "score": score,
                    "citation": citation_of(hit.doc, hit.chunk),
                    "doc": {
                        "id": hit.doc.id,
                        "version": hit.doc.version,
                        "status": hit.doc.status,
                        "category": hit.doc.category,
                        "department": hit.doc.department,
                        "doc_no": hit.doc.doc_no,
                        "effective_from": hit.doc.effective_from,
                        "heading": hit.chunk.heading,
                        "page_no": hit.chunk.page_no,
                        "chunk_id": hit.chunk.id,
                    },
                }
            )
        for rank, note in enumerate(notes, start=1):
            items.append(
                {
                    "source": "note",
                    "id": int(note.get("note_id") or 0),
                    "title": note.get("title") or "（无标题）",
                    "snippet": note.get("snippet") or "",
                    "score": 1.0 / (RRF_K + rank),
                    "citation": "笔记",
                    "doc": None,
                }
            )

        items.sort(key=lambda item: item["score"], reverse=True)
        items = items[:top_k]
        max_score = items[0]["score"] if items else 0.0
        for item in items:
            item["score"] = round(item["score"] / max_score, 3) if max_score else 0.0
        if vector_note and not vector:
            for item in items:
                item["vector_note"] = vector_note
        return items


def stats(db: Session) -> dict:
    from ..models import KbDoc

    index_stats = index_svc.stats(db)
    return {
        "docs": int(db.query(KbDoc).count()),
        "effective_docs": int(db.query(KbDoc).filter(KbDoc.status == "effective").count()),
        **index_stats,
    }


def read_doc_text(db: Session, doc_id: int, offset: int = 0, limit: int = 120) -> dict:
    """按行读取整篇文档（Agent 工具用）；单次最多 2 万字。"""
    doc = db.get(KbDoc, doc_id)
    if doc is None:
        return {"ok": False, "error": "文档不存在"}
    chunks = db.query(KbChunk).filter(KbChunk.doc_id == doc_id).order_by(KbChunk.seq).all()
    lines: list[str] = []
    for chunk in chunks:
        if chunk.heading:
            lines.append(f"[{chunk.heading}]")
        if chunk.page_no:
            lines.append(f"[第 {chunk.page_no} 页]")
        lines.extend((chunk.text or "").splitlines())
    try:
        offset = max(int(offset), 0)
    except (TypeError, ValueError):
        offset = 0
    try:
        limit = max(1, min(int(limit), 200))
    except (TypeError, ValueError):
        limit = 120
    selected = lines[offset : offset + limit]
    text_out = "\n".join(selected)
    truncated = len(text_out) > MAX_READ_CHARS
    if truncated:
        text_out = text_out[:MAX_READ_CHARS]
    return {
        "ok": True,
        "doc_id": doc.id,
        "title": doc.title,
        "version": doc.version,
        "status": doc.status,
        "citation": citation_of(doc),
        "offset": offset,
        "limit": limit,
        "total_lines": len(lines),
        "lines": text_out,
        "truncated": truncated or offset + len(selected) < len(lines),
    }
