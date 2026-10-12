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

from sqlalchemy.orm import Session

from ..database import SessionLocal
from ..models import Note

CHUNK_SIZE = 240          # 笔记分块字符数（重叠一半）
TOP_K_DEFAULT = 5
_index_lock = threading.Lock()
_index: dict | None = None   # {"stamp": str, "chunks": [...], "df": Counter, "avgdl": float}


def _bigrams(text: str) -> list[str]:
    """中文友好的轻量分词：连续二元组 + 英文/数字单词。"""
    text = (text or "").lower()
    tokens: list[str] = []
    buf: list[str] = []
    for ch in text:
        if ch.isalnum():
            buf.append(ch)
        else:
            if buf:
                tokens.append("".join(buf))
                buf = []
    if buf:
        tokens.append("".join(buf))
    han = re.sub(r"[^一-鿿]", "", text)
    tokens.extend(han[i : i + 2] for i in range(len(han) - 1))
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
        for token in set(_bigrams(chunk["text"])):
            df[token] += 1
    total_len = sum(len(c["text"]) for c in chunks) or 1
    return {"stamp": stamp, "chunks": chunks, "df": df, "avgdl": total_len / max(len(chunks), 1)}


def _ensure_index() -> dict:
    """懒加载 + 失效自动重建。"""
    global _index
    with SessionLocal() as db:
        from sqlalchemy import func

        stamp = str(db.query(func.max(Note.updated_at)).scalar() or "")
        note_count = db.query(func.count(Note.id)).scalar() or 0
        if _index is not None and _index.get("stamp") == stamp and _index.get("count") == note_count:
            return _index
        with _index_lock:
            if _index is not None and _index.get("stamp") == stamp and _index.get("count") == note_count:
                return _index
            _index = {**_build_index(db), "count": note_count}
            return _index


def rebuild_index() -> dict:
    """手动重建索引（新建/编辑笔记后也可调用，通常无需）。"""
    global _index
    with SessionLocal() as db:
        from sqlalchemy import func

        note_count = db.query(func.count(Note.id)).scalar() or 0
        with _index_lock:
            _index = {**_build_index(db), "count": note_count}
            return stats()


def stats() -> dict:
    idx = _ensure_index()
    return {
        "notes_indexed": idx.get("count", 0),
        "chunks": len(idx.get("chunks", [])),
        "vocabulary": len(idx.get("df", {})),
    }


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

    q_tokens = _bigrams(query)
    if not q_tokens:
        return []
    scores: list[tuple[float, dict]] = []
    for chunk in chunks:
        tf = Counter(_bigrams(chunk["text"]))
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
