"""RAG 知识库：基于 BM25 的笔记检索（中文二元分词，零外部依赖、离线可用）。

设计（ADR-003 口径）：
- 中文不引入 jieba/向量库：按**字符二元组（bigram）**分词 + BM25 打分，
  对中文短语检索效果足够，且完全离线、零依赖；
- 索引常驻内存，按 `max(notes.updated_at)` 失效自动重建；也提供手动重建；
- 向量检索（embedding）留待配置了 embedding 端点后按同一接口扩展，
  本模块的 `search()` 返回结构不变。
"""
from __future__ import annotations

import math
import re
import threading
from collections import Counter

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..database import SessionLocal
from ..models import Note

CHUNK_SIZE = 240          # 笔记分块字符数（重叠一半）
TOP_K_DEFAULT = 5
_index_lock = threading.Lock()
_index: dict | None = None   # {"stamp": str, "chunks": [...], "df": Counter, "avgdl": float}


def _bigrams(text: str) -> list[str]:
    """兼容包装：分词实现已迁到 knowledge.tokenize（笔记/制度共用一份）。"""
    from ..knowledge.tokenize import bigrams

    return bigrams(text)


def _query_tokens(text: str) -> list[str]:
    """查询侧分词：存在多字 token 时丢弃单字 token。

    走 `_bigrams` 而非直接 import，保留测试对分词函数的 patch 接缝
    （test_task8_minors 用它验证「查询只分词一次」）。
    """
    tokens = _bigrams(text)
    if any(len(t) >= 2 for t in tokens):
        tokens = [t for t in tokens if len(t) >= 2]
    return tokens


def _chunk_note(note_id: int, title: str, content: str) -> list[dict]:
    """把一篇笔记切成带上下文的块（块内带标题，检索结果可定位）。"""
    text = (content or "").strip()
    if not text and not title:
        return []
    full = (title or "") + "\n" + text
    if len(full) <= CHUNK_SIZE:
        return [{"note_id": note_id, "title": title or "（无标题）", "text": full}]
    chunks: list[dict] = []
    step = CHUNK_SIZE // 2
    for start in range(0, len(full), step):
        piece = full[start : start + CHUNK_SIZE]
        if piece.strip():
            chunks.append({"note_id": note_id, "title": title or "（无标题）", "text": piece})
        if start + CHUNK_SIZE >= len(full):
            break
    return chunks


def _build_index(db: Session) -> dict:
    chunks: list[dict] = []
    stamp = ""
    for note in db.query(Note.id, Note.title, Note.content, Note.updated_at).all():
        stamp = max(stamp, str(note.updated_at or ""))
        chunks.extend(_chunk_note(note.id, note.title or "", note.content or ""))
    df: Counter = Counter()
    for chunk in chunks:
        tokens = _bigrams(chunk["text"])
        # 词频随索引缓存，检索时不再逐 chunk 重新分词（只对查询分词一次）。
        chunk["tf"] = Counter(tokens)
        for token in set(tokens):
            df[token] += 1
    total_len = sum(len(c["text"]) for c in chunks) or 1
    return {"stamp": stamp, "chunks": chunks, "df": df, "avgdl": total_len / max(len(chunks), 1)}


def _ensure_index() -> dict:
    """懒加载 + 失效自动重建。"""
    global _index
    with SessionLocal() as db:
        stamp = str(db.query(func.max(Note.updated_at)).scalar() or "")
        note_count = db.query(func.count(Note.id)).scalar() or 0
        if _index is not None and _index.get("stamp") == stamp and _index.get("count") == note_count:
            return _index
        with _index_lock:
            if _index is not None and _index.get("stamp") == stamp and _index.get("count") == note_count:
                return _index
            _index = {**_build_index(db), "count": note_count}
            return _index


def _stats_of(idx: dict) -> dict:
    return {
        "notes_indexed": idx.get("count", 0),
        "chunks": len(idx.get("chunks", [])),
        "vocabulary": len(idx.get("df", {})),
    }


def rebuild_index() -> dict:
    """手动重建索引（新建/编辑笔记后也可调用，通常无需）。

    构建在锁外完成、锁内只做发布：`_index_lock` 是普通 Lock，重建时若在持锁
    状态下回调 `stats()`/`_ensure_index()`，一旦期间有笔记写入（stamp/count 变化）
    就会在同一把锁上自锁。统计直接由构建结果计算，不再重入。
    """
    global _index
    with SessionLocal() as db:
        note_count = db.query(func.count(Note.id)).scalar() or 0
        built = _build_index(db)
    with _index_lock:
        _index = {**built, "count": note_count}
        return _stats_of(_index)


def stats() -> dict:
    return _stats_of(_ensure_index())


def search(query: str, top_k: int = TOP_K_DEFAULT) -> list[dict]:
    """BM25 检索笔记块，返回 [{note_id, title, snippet, score}]。"""
    query = (query or "").strip()
    if not query:
        return []
    idx = _ensure_index()
    chunks: list[dict] = idx["chunks"]
    if not chunks:
        return []
    df: Counter = idx["df"]
    n_docs = len(chunks)
    avgdl = idx["avgdl"] or 1.0
    k1, b = 1.5, 0.75

    q_tokens = _query_tokens(query)
    if not q_tokens:
        return []
    scores: list[tuple[float, dict]] = []
    for chunk in chunks:
        tf = chunk.get("tf") or Counter(_bigrams(chunk["text"]))
        dl = len(chunk["text"]) or 1
        score = 0.0
        for token in q_tokens:
            f = tf.get(token, 0)
            if f == 0 or df.get(token, 0) == 0:
                continue
            idf = math.log((n_docs - df[token] + 0.5) / (df[token] + 0.5) + 1.0)
            score += idf * (f * (k1 + 1)) / (f + k1 * (1 - b + b * dl / avgdl))
        if score > 0:
            scores.append((score, chunk))
    scores.sort(key=lambda x: x[0], reverse=True)

    # 同笔记去重（保留最高分块）
    seen: set[int] = set()
    results: list[dict] = []
    for score, chunk in scores:
        if chunk["note_id"] in seen:
            continue
        seen.add(chunk["note_id"])
        snippet = chunk["text"].strip()
        if len(snippet) > 220:
            snippet = snippet[:220] + "…"
        results.append({
            "note_id": chunk["note_id"],
            "title": chunk["title"],
            "snippet": snippet,
            "score": round(score, 3),
        })
        if len(results) >= max(1, min(top_k, 20)):
            break
    return results
