"""知识库索引：SQLite FTS5 关键词索引 + sqlite-vec 向量索引。

两张虚拟表都不进 `Base.metadata`（ORM 不感知虚拟表），由 `ensure_schema()`
在启动时幂等创建：

- `kb_fts`：`rowid = kb_chunks.id`，只存 bigram 分词串；原文留在 kb_chunks，
  snippet 在 Python 侧从原文生成，避免 FTS 返回带空格的碎片。
- `kb_vec`：`chunk_id` 主键 + float[N] 向量；维度由 embedding 模型决定，
  变更维度时旧表会被重建（旧向量作废，需重新嵌入）。

向量能力是**可选增强**：sqlite-vec 未安装/加载失败时 `vector_available()`
返回 False，检索自动只用关键词，不影响主流程。
"""
from __future__ import annotations

import logging
import re
import struct

from sqlalchemy import text
from sqlalchemy.orm import Session

from ..database import SessionLocal
from .chunk import Chunk
from .tokenize import fts_text

logger = logging.getLogger(__name__)

FTS_TABLE = "kb_fts"
VEC_TABLE = "kb_vec"

_fts_ok: bool | None = None
_vec_ok: bool | None = None


def fts_available(db: Session) -> bool:
    global _fts_ok
    if _fts_ok is not None:
        return _fts_ok
    try:
        db.execute(text("SELECT count(*) FROM sqlite_master WHERE name = :name"), {"name": FTS_TABLE})
        _fts_ok = True
    except Exception:
        logger.warning("FTS5 不可用，知识库降级为 LIKE 检索", exc_info=True)
        _fts_ok = False
    return _fts_ok


def vector_available(db: Session) -> bool:
    """sqlite-vec 是否可用（扩展加载成功且能执行版本查询）。"""
    global _vec_ok
    if _vec_ok is not None:
        return _vec_ok
    try:
        db.execute(text("SELECT vec_version()"))
        _vec_ok = True
    except Exception:
        _vec_ok = False
    return _vec_ok


def _vec_dim(db: Session) -> int | None:
    row = db.execute(
        text("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = :name"),
        {"name": VEC_TABLE},
    ).first()
    if not row or not row[0]:
        return None
    match = re.search(r"float\[(\d+)\]", str(row[0]))
    return int(match.group(1)) if match else None


def ensure_schema(db: Session | None = None) -> dict:
    """幂等创建 FTS/向量表；返回 {fts, vector, dim}。"""
    global _fts_ok
    own = db is None
    if own:
        db = SessionLocal()
    try:
        fts = True
        try:
            db.execute(
                text(
                    f"CREATE VIRTUAL TABLE IF NOT EXISTS {FTS_TABLE} USING fts5("
                    "tokens, doc_id UNINDEXED, chunk_id UNINDEXED, tokenize='unicode61')"
                )
            )
            db.commit()
        except Exception:
            db.rollback()
            fts = False
            logger.warning("创建 FTS5 知识库索引失败，关键词检索降级为 LIKE", exc_info=True)
        _fts_ok = fts

        dim = 0
        vector = False
        if vector_available(db):
            try:
                from ..models import KbSettings

                row = db.get(KbSettings, 1)
                dim = int(row.embedding_dim or 0) if row else 0
            except Exception:
                dim = 0
            if dim > 0:
                vector = _ensure_vec_table(db, dim)
        return {"fts": fts, "vector": vector, "dim": dim}
    finally:
        if own:
            db.close()


def _ensure_vec_table(db: Session, dim: int) -> bool:
    """确保向量表存在且维度一致；维度不一致时拒绝（不静默丢已有向量）。"""
    existing = _vec_dim(db)
    if existing is not None and existing != dim:
        logger.warning(
            "向量维度不一致：已有 %s，本次 %s；请调用「重建向量索引」（reset_vectors + ensure_vector_table）",
            existing,
            dim,
        )
        return False
    try:
        if existing is None:
            db.execute(
                text(f"CREATE VIRTUAL TABLE {VEC_TABLE} USING vec0(chunk_id INTEGER PRIMARY KEY, embedding float[{int(dim)}])")
            )
        db.commit()
        return True
    except Exception:
        db.rollback()
        logger.warning("创建 sqlite-vec 向量表失败，知识库向量检索关闭", exc_info=True)
        return False


def vector_dim(db: Session) -> int | None:
    """当前向量表维度（无表返回 None）。"""
    return _vec_dim(db)


def reset_vectors(db: Session) -> None:
    """删除向量表；维度变更全量重建前调用（FTS/块不受影响）。"""
    db.execute(text(f"DROP TABLE IF EXISTS {VEC_TABLE}"))
    db.commit()


def ensure_vector_table(db: Session, dim: int) -> bool:
    """按指定维度创建空向量表；调用方需先 reset_vectors 处理旧维度。"""
    if int(dim or 0) <= 0:
        return False
    return _ensure_vec_table(db, int(dim))


def _pack(vector: list[float]) -> bytes:
    return struct.pack(f"{len(vector)}f", *[float(v) for v in vector])


def index_chunks(db: Session, doc_id: int, chunks: list[Chunk]) -> None:
    """重建单篇文档的 FTS 索引；调用方负责先 flush 块再调用、最后提交事务。"""
    remove_doc(db, doc_id)
    if not chunks or not fts_available(db):
        return
    for chunk, row in zip(chunks, _chunk_rows(db, doc_id)):
        db.execute(
            text(
                f"INSERT INTO {FTS_TABLE}(rowid, tokens, doc_id, chunk_id) "
                "VALUES (:rowid, :tokens, :doc_id, :chunk_id)"
            ),
            {
                "rowid": int(row),
                "tokens": fts_text(f"{chunk.heading}\n{chunk.text}"),
                "doc_id": doc_id,
                "chunk_id": int(row),
            },
        )


def index_vectors(db: Session, doc_id: int, vectors: list[list[float]]) -> bool:
    """写入单篇文档的向量索引；维度冲突/扩展不可用时返回 False。"""
    if not vectors or not vector_available(db):
        return False
    dim = len(vectors[0])
    if not _ensure_vec_table(db, dim):
        return False
    ids = _chunk_rows(db, doc_id)
    if len(ids) != len(vectors):
        logger.warning("向量条数与块数不一致：%d vs %d", len(ids), len(vectors))
        return False
    for chunk_id in ids:
        db.execute(text(f"DELETE FROM {VEC_TABLE} WHERE chunk_id = :chunk_id"), {"chunk_id": int(chunk_id)})
    for chunk_id, vector in zip(ids, vectors):
        if len(vector) != dim:
            continue
        db.execute(
            text(f"INSERT INTO {VEC_TABLE}(chunk_id, embedding) VALUES (:chunk_id, :embedding)"),
            {"chunk_id": int(chunk_id), "embedding": _pack(vector)},
        )
    db.commit()
    return True


def _chunk_rows(db: Session, doc_id: int) -> list[int]:
    return [
        int(row[0])
        for row in db.execute(
            text("SELECT id FROM kb_chunks WHERE doc_id = :doc_id ORDER BY seq"),
            {"doc_id": doc_id},
        ).all()
    ]


def remove_doc(db: Session, doc_id: int) -> None:
    """删除单篇文档的 FTS/向量索引（不删 kb_chunks 本身）。"""
    ids = _chunk_rows(db, doc_id)
    if fts_available(db):
        db.execute(text(f"DELETE FROM {FTS_TABLE} WHERE doc_id = :doc_id"), {"doc_id": doc_id})
    if ids and vector_available(db) and _vec_dim(db) is not None:
        for chunk_id in ids:
            db.execute(text(f"DELETE FROM {VEC_TABLE} WHERE chunk_id = :chunk_id"), {"chunk_id": chunk_id})


def clear_all(db: Session) -> None:
    """清空两张索引表（测试隔离用；生产重建不要用它，避免误删向量）。"""
    if fts_available(db):
        db.execute(text(f"DELETE FROM {FTS_TABLE}"))
    if vector_available(db) and _vec_dim(db) is not None:
        db.execute(text(f"DROP TABLE IF EXISTS {VEC_TABLE}"))
    db.commit()


def rebuild_all(db: Session) -> dict:
    """从 kb_chunks 全量重建 FTS 索引；向量保留（块 id 未变，仍然有效）。"""
    from ..models import KbChunk

    if fts_available(db):
        db.execute(text(f"DELETE FROM {FTS_TABLE}"))
        db.commit()
    rows = db.query(KbChunk).order_by(KbChunk.doc_id, KbChunk.seq).all()
    by_doc: dict[int, list[KbChunk]] = {}
    for row in rows:
        by_doc.setdefault(row.doc_id, []).append(row)
    indexed = 0
    for doc_id, chunks in by_doc.items():
        if not fts_available(db):
            break
        for chunk in chunks:
            db.execute(
                text(
                    f"INSERT INTO {FTS_TABLE}(rowid, tokens, doc_id, chunk_id) "
                    "VALUES (:rowid, :tokens, :doc_id, :chunk_id)"
                ),
                {
                    "rowid": chunk.id,
                    "tokens": fts_text(f"{chunk.heading}\n{chunk.text}"),
                    "doc_id": doc_id,
                    "chunk_id": chunk.id,
                },
            )
            indexed += 1
    db.commit()
    return {"docs": len(by_doc), "chunks": indexed, "fts": fts_available(db)}


def stats(db: Session) -> dict:
    rows = db.execute(text("SELECT count(*) FROM kb_chunks")).scalar() or 0
    return {
        "chunks": int(rows),
        "fts": fts_available(db),
        "vector": vector_available(db) and _vec_dim(db) is not None,
        "vector_dim": _vec_dim(db) or 0,
    }
