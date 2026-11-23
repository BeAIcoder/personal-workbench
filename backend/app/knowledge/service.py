"""知识库入库编排：解析 → 切块 → 落库 → 建索引 → 异步向量化。"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from sqlalchemy.orm import Session

from .. import config
from ..database import SessionLocal
from ..models import KbChunk, KbDoc
from . import embedding as embedding_svc
from . import index as index_svc
from . import store
from .chunk import build_chunks
from .extract import Block, DocumentParseError, Extraction, extract_blocks

logger = logging.getLogger(__name__)

SUMMARY_CHARS = 200


def _summary_of(blocks: list[Block]) -> str:
    parts: list[str] = []
    total = 0
    for block in blocks:
        if block.kind == "heading":
            continue
        text = block.text.strip()
        if not text:
            continue
        parts.append(text)
        total += len(text)
        if total >= SUMMARY_CHARS:
            break
    return " ".join(parts)[:SUMMARY_CHARS]


def _base_status(extraction: Extraction) -> tuple[str, str]:
    notes: list[str] = []
    if extraction.truncated:
        notes.append("内容超过 30 万字上限，仅索引前一部分；完整内容请查看原件")
    if extraction.ocr_truncated:
        notes.append("扫描页超过 OCR 页数上限（50 页），其余页未识别，请拆分后重新上传")
    return "ok", "；".join(notes)


def find_by_hash(db: Session, source_hash: str) -> KbDoc | None:
    if not source_hash:
        return None
    return db.query(KbDoc).filter(KbDoc.source_hash == source_hash).first()


def ingest_file(
    db: Session,
    *,
    path: Path,
    filename: str,
    source_rel_path: str,
    source_size: int,
    source_hash: str,
    meta: dict,
) -> KbDoc:
    """解析并入库一个已落盘的文件；解析失败抛 DocumentParseError。"""
    settings = embedding_svc.get_settings(db)
    extraction = extract_blocks(path, ocr=bool(settings.ocr_enabled))
    chunks = build_chunks(extraction.blocks)
    if not chunks:
        raise DocumentParseError("文档未提取到可索引的正文")
    status, note = _base_status(extraction)

    cfg = embedding_svc.resolve_config(db)
    index_status = "embedding" if cfg else status
    index_error = note

    return store.create_doc(
        db,
        title=meta.get("title") or Path(filename).stem,
        chunks=chunks,
        category=meta.get("category") or store.DEFAULT_CATEGORY,
        department=meta.get("department") or "",
        doc_no=meta.get("doc_no") or "",
        version=meta.get("version") or "v1.0",
        status=meta.get("status") or "effective",
        effective_from=meta.get("effective_from") or "",
        effective_to=meta.get("effective_to") or "",
        tags=meta.get("tags"),
        owner=meta.get("owner") or "",
        summary=meta.get("summary") or _summary_of(extraction.blocks),
        source_name=filename[:255],
        source_rel_path=source_rel_path,
        source_hash=source_hash,
        source_size=source_size,
        page_count=extraction.page_count,
        char_count=extraction.chars,
        ocr_pages=extraction.ocr_pages,
        index_status=index_status,
        index_error=index_error,
    )


def ingest_text(db: Session, *, title: str, content: str, meta: dict) -> KbDoc:
    """把粘贴的 Markdown/纯文本存为 .md 原件后入库（软件手册常用）。"""
    text = str(content or "").strip()
    if len(text) < 10:
        raise store.KbError("正文太短，至少 10 个字符")
    if len(text) > 500_000:
        raise store.KbError("正文超过 50 万字上限，请拆分后分篇创建")

    import hashlib

    payload = text.encode("utf-8")
    filename = f"{title or 'manual'}.md"
    rel, size, digest = store.save_source(filename, payload)
    path = Path(config.UPLOAD_DIR) / rel
    return ingest_file(
        db,
        path=path,
        filename=filename,
        source_rel_path=rel,
        source_size=size,
        source_hash=hashlib.sha256(payload).hexdigest(),
        meta={**meta, "title": title},
    )


def reindex(db: Session, doc: KbDoc) -> KbDoc:
    """重解析原件并重建该文档的块与索引。"""
    path = store.resolve_source(doc.source_rel_path)
    settings = embedding_svc.get_settings(db)
    extraction = extract_blocks(path, ocr=bool(settings.ocr_enabled))
    chunks = build_chunks(extraction.blocks)
    if not chunks:
        raise DocumentParseError("文档未提取到可索引的正文")

    index_svc.remove_doc(db, doc.id)
    db.query(KbChunk).filter(KbChunk.doc_id == doc.id).delete(synchronize_session=False)
    db.flush()
    for chunk in chunks:
        db.add(
            KbChunk(
                doc_id=doc.id,
                seq=chunk.seq,
                heading=chunk.heading[:300],
                page_no=chunk.page_no,
                text=chunk.text,
                char_len=len(chunk.text),
            )
        )
    db.flush()
    index_svc.index_chunks(db, doc.id, chunks)

    doc.page_count = extraction.page_count
    doc.char_count = extraction.chars
    doc.chunk_count = len(chunks)
    doc.ocr_pages = extraction.ocr_pages
    status, note = _base_status(extraction)
    cfg = embedding_svc.resolve_config(db)
    doc.index_status = "embedding" if cfg else status
    doc.index_error = note
    doc.indexed_at = datetime.now()
    db.commit()
    db.refresh(doc)
    return doc


def _chunk_texts(db: Session, doc_id: int) -> list[str]:
    rows = (
        db.query(KbChunk)
        .filter(KbChunk.doc_id == doc_id)
        .order_by(KbChunk.seq)
        .all()
    )
    return [f"{row.heading}\n{row.text}".strip() for row in rows]


def _save_embedding_dim(db: Session, dim: int) -> None:
    if int(dim or 0) <= 0:
        return
    row = embedding_svc.get_settings(db)
    if int(row.embedding_dim or 0) != int(dim):
        row.embedding_dim = int(dim)
        db.commit()


def _store_vectors(db: Session, doc: KbDoc, vectors: list[list[float]], dim: int) -> None:
    if not index_svc.index_vectors(db, doc.id, vectors):
        raise embedding_svc.EmbeddingError(
            f"向量索引写入失败：sqlite-vec 不可用或向量维度与已有索引不一致（本次 {dim} 维）"
        )
    row = embedding_svc.get_settings(db)
    row.embedding_dim = int(dim or 0)
    doc.index_status = "ok"
    doc.index_error = ""
    doc.indexed_at = datetime.now()
    db.commit()


def embed_document(doc_id: int) -> dict:
    """同步向量化一篇文档（BackgroundTasks 的线程池里执行）。"""
    with SessionLocal() as db:
        doc = db.get(KbDoc, doc_id)
        if doc is None:
            return {"ok": False, "error": "文档不存在"}
        cfg, reason = embedding_svc.resolve_config_with_reason(db)
        if cfg is None:
            if doc.index_status in ("embedding", "embedding_failed"):
                doc.index_status = "ok"
                doc.index_error = reason
                db.commit()
            return {"ok": True, "skipped": True, "reason": reason}
        texts = _chunk_texts(db, doc_id)
        if not texts:
            doc.index_status = "ok"
            db.commit()
            return {"ok": True, "skipped": True}
        try:
            vectors, dim = embedding_svc.embed_texts_sync(texts, cfg)
            _store_vectors(db, doc, vectors, dim)
            return {"ok": True, "vectors": len(vectors), "dim": dim}
        except embedding_svc.EmbeddingError as exc:
            db.rollback()
            row = db.get(KbDoc, doc_id)
            if row is not None:
                row.index_status = "embedding_failed"
                row.index_error = str(exc)[:500]
                db.commit()
            return {"ok": False, "error": str(exc)}
        except Exception as exc:  # 后台任务不允许把异常抛给请求线程
            logger.warning("知识库向量化失败：doc_id=%s", doc_id, exc_info=True)
            db.rollback()
            row = db.get(KbDoc, doc_id)
            if row is not None:
                row.index_status = "embedding_failed"
                row.index_error = f"向量化失败：{type(exc).__name__}"
                db.commit()
            return {"ok": False, "error": "向量化失败"}


def _mark_docs_failed(db: Session, doc_ids: list[int], message: str) -> None:
    """把未完成/失败的文档标为 embedding_failed，保证「补嵌」与再次重建可续跑。"""
    if not doc_ids:
        return
    payload = {
        KbDoc.index_status: "embedding_failed",
        KbDoc.index_error: str(message or "")[:500],
    }
    # SQLite 的 IN 参数有上限（常见 999/32766），大知识库分批更新
    for start in range(0, len(doc_ids), 400):
        chunk = doc_ids[start : start + 400]
        db.query(KbDoc).filter(KbDoc.id.in_(chunk)).update(payload, synchronize_session=False)
    db.commit()


def rebuild_vectors(limit: int | None = None) -> dict:
    """换 embedding 模型/维度后重建向量索引（默认全量；limit 仅用于显式分批）。

    vec0 表无法原地改维度，必须删表重建。关键约束：
    - 探测/首篇向量化**忽略已保存的 embedding_dim**（`{"dim": 0}`），否则换到
      不同维度的模型后会被旧维度门禁挡住，用户永远无法重建（C1）；
    - 先真实向量化首篇文档，失败时保留旧表与旧向量（探测失败不破坏数据）；
    - 维度变化才 `reset_vectors`；同维写入失败时不得删除仍有效的旧向量（I1）；
    - 默认处理全部文档；显式 `limit` 时把未处理文档标 `embedding_failed` 并返回
      `remaining`，避免第 201 篇起静默失去向量（I2）；
    - 全程不持模块级锁，重建期间的新文档写入不受影响。
    """
    with SessionLocal() as db:
        cfg, reason = embedding_svc.resolve_config_with_reason(db)
        total = int(db.query(KbDoc.id).count())
        if cfg is None:
            return {
                "processed": 0,
                "done": 0,
                "failed": total,
                "remaining": 0,
                "dim": 0,
                "error": reason,
            }
        if not index_svc.vector_available(db):
            return {
                "processed": 0,
                "done": 0,
                "failed": total,
                "remaining": 0,
                "dim": 0,
                "error": "sqlite-vec 不可用，无法重建向量索引",
            }
        probe_cfg = {**cfg, "dim": 0}
        all_ids = [
            int(row[0])
            for row in db.query(KbChunk.doc_id)
            .group_by(KbChunk.doc_id)
            .order_by(KbChunk.doc_id)
            .all()
        ]
        doc_ids = all_ids[:limit] if limit is not None else list(all_ids)
        remaining_ids = all_ids[len(doc_ids) :]
        existing = index_svc.vector_dim(db)

        if not doc_ids:
            try:
                _vectors, dim = embedding_svc.embed_texts_sync(["知识库向量维度探测"], probe_cfg)
            except embedding_svc.EmbeddingError as exc:
                return {
                    "processed": 0,
                    "done": 0,
                    "failed": 0,
                    "remaining": len(remaining_ids),
                    "dim": 0,
                    "error": str(exc),
                }
            changed = existing is not None and existing != dim
            if changed:
                index_svc.reset_vectors(db)
            index_svc.ensure_vector_table(db, dim)
            _save_embedding_dim(db, dim)
            if remaining_ids:
                # 仅显式 limit<=0 时会出现：文档未处理，标失败保证可续跑
                _mark_docs_failed(
                    db,
                    remaining_ids,
                    "向量重建待继续：本篇尚未重建，请再次执行「重建向量索引」",
                )
            return {
                "processed": 0,
                "done": 0,
                "failed": 0,
                "remaining": len(remaining_ids),
                "dim": dim,
                "dim_changed": changed,
            }

        first = db.get(KbDoc, doc_ids[0])
        if first is None:
            return {
                "processed": 0,
                "done": 0,
                "failed": len(doc_ids),
                "remaining": len(remaining_ids),
                "dim": 0,
                "error": "文档不存在",
            }
        try:
            vectors, dim = embedding_svc.embed_texts_sync(_chunk_texts(db, first.id), probe_cfg)
        except embedding_svc.EmbeddingError as exc:
            # 探测失败：旧表/旧向量原样保留，不做破坏性操作
            return {
                "processed": 0,
                "done": 0,
                "failed": len(doc_ids),
                "remaining": len(remaining_ids),
                "dim": 0,
                "error": str(exc),
            }
        if not vectors:
            return {
                "processed": 0,
                "done": 0,
                "failed": len(doc_ids),
                "remaining": len(remaining_ids),
                "dim": 0,
                "error": "首篇文档没有可向量化的正文",
            }

        changed = existing is not None and existing != dim
        if changed:
            index_svc.reset_vectors(db)
        try:
            _store_vectors(db, first, vectors, dim)
        except embedding_svc.EmbeddingError as exc:
            db.rollback()
            if changed or existing is None:
                # 旧维度向量已随 reset 失效：重建空表，并标记所有待重建文档可续跑
                index_svc.reset_vectors(db)
                index_svc.ensure_vector_table(db, dim)
                _save_embedding_dim(db, dim)
                _mark_docs_failed(db, all_ids, f"向量重建失败：{exc}")
            else:
                # 同维写入失败：旧表旧向量仍然有效，绝不 reset（I1）
                _mark_docs_failed(db, [first.id], f"向量重建失败：{exc}")
            return {
                "processed": 0,
                "done": 0,
                "failed": len(doc_ids),
                "remaining": len(remaining_ids),
                "dim": dim,
                "error": str(exc),
            }

    done = 1
    failed = 0
    for doc_id in doc_ids[1:]:
        result = embed_document(doc_id)
        if result.get("ok"):
            done += 1
        else:
            failed += 1
    if remaining_ids:
        with SessionLocal() as db:
            _mark_docs_failed(
                db,
                remaining_ids,
                "向量重建待继续：本篇尚未重建，请再次执行「重建向量索引」",
            )
    return {
        "processed": len(doc_ids),
        "done": done,
        "failed": failed,
        "remaining": len(remaining_ids),
        "dim": dim,
        "dim_changed": changed,
    }


def embed_missing(limit: int = 50) -> dict:
    """补嵌所有处于 embedding/embedding_failed 的文档（重建向量索引用）。"""
    with SessionLocal() as db:
        rows = (
            db.query(KbDoc.id)
            .filter(KbDoc.index_status.in_(("embedding", "embedding_failed")))
            .order_by(KbDoc.id)
            .limit(limit)
            .all()
        )
    return _embed_ids([doc_id for (doc_id,) in rows])


def embed_all(limit: int = 200) -> dict:
    """强制重算全部文档向量（换 embedding 模型/维度后使用）。"""
    with SessionLocal() as db:
        rows = db.query(KbDoc.id).order_by(KbDoc.id).limit(limit).all()
    return _embed_ids([doc_id for (doc_id,) in rows])


def _embed_ids(doc_ids: list[int]) -> dict:
    done = failed = 0
    for doc_id in doc_ids:
        result = embed_document(doc_id)
        if result.get("ok"):
            done += 1
        else:
            failed += 1
    return {"processed": len(doc_ids), "done": done, "failed": failed}
