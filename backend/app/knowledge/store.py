"""知识库存储：文档元数据、块、版本链与原件路径。

约定：
- `doc_key` 是同一制度的稳定标识（制度编号 > 规范化标题），`(doc_key, version)`
  唯一；同 key 新版本入库时旧版自动置 superseded。
- 原件统一放 `UPLOAD_DIR/knowledge/`，数据库只存相对路径；读取时经
  `resolve_source()` 做目录边界校验，杜绝路径穿越。
- 索引由 `index.index_chunks()` 维护，本模块负责保证「块已 flush 再建索引」。
"""
from __future__ import annotations

import hashlib
import re
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path

from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import config
from ..models import KbChunk, KbDoc, Memory
from ..utils import escape_like
from .chunk import Chunk
from .index import index_chunks, remove_doc as remove_index

STATUSES = ("draft", "effective", "superseded", "archived")
DEFAULT_CATEGORY = "制度规范"
MAX_TITLE = 200
MAX_SUMMARY = 500
MAX_TAGS = 10
MAX_TAG_LEN = 20


class KbError(ValueError):
    """知识库业务错误：消息可直接返回给用户。"""


class DuplicateDoc(KbError):
    """同 doc_key + version 已存在。"""

    def __init__(self, message: str, doc_id: int | None = None):
        super().__init__(message)
        self.doc_id = doc_id


def knowledge_dir() -> Path:
    return Path(config.UPLOAD_DIR) / "knowledge"


def _safe_name(name: str) -> str:
    base = Path(str(name or "document")).name
    base = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff._-]+", "_", base).strip("._")
    return (base or "document")[:80]


def save_source(filename: str, content: bytes) -> tuple[str, int, str]:
    """落盘原件，返回 (相对路径, 字节数, sha256)。"""
    directory = knowledge_dir()
    directory.mkdir(parents=True, exist_ok=True)
    safe = _safe_name(filename)
    rel = f"knowledge/{uuid.uuid4().hex[:16]}-{safe}"
    path = Path(config.UPLOAD_DIR) / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return rel, len(content), hashlib.sha256(content).hexdigest()


def resolve_source(rel_path: str) -> Path:
    """把相对路径解析为原件绝对路径；越界/不存在抛 KbError。"""
    root = Path(config.UPLOAD_DIR).resolve()
    raw = str(rel_path or "").strip()
    if not raw:
        raise KbError("该文档没有原件（可能是粘贴创建的）")
    candidate = Path(raw)
    candidate = candidate if candidate.is_absolute() else root / candidate
    try:
        resolved = candidate.resolve()
        resolved.relative_to(root)
    except (OSError, ValueError) as exc:
        raise KbError("原件路径不在允许的数据目录内") from exc
    if not resolved.is_file():
        raise KbError("原件不存在或已被移动")
    return resolved


def normalize_doc_key(title: str, doc_no: str = "") -> str:
    raw = (doc_no or "").strip() or (title or "").strip()
    key = re.sub(r"[\s《》()（）\[\]【】\-—_/\\]+", "", raw).lower()
    return key[:200] or "untitled"


def validate_date(value: str, field: str) -> str:
    value = str(value or "").strip()
    if not value:
        return ""
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise KbError(f"{field}格式错误，应为 YYYY-MM-DD") from exc


def validate_status(value: str) -> str:
    status = str(value or "").strip().lower()
    if status not in STATUSES:
        raise KbError(f"状态必须是 {'/'.join(STATUSES)} 之一")
    return status


def clean_tags(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        items = re.split(r"[,，;；\n]+", value)
    else:
        items = list(value)
    result: list[str] = []
    for item in items:
        tag = str(item or "").strip()[:MAX_TAG_LEN]
        if tag and tag not in result:
            result.append(tag)
        if len(result) >= MAX_TAGS:
            break
    return result


def _supersede_previous(db: Session, doc: KbDoc) -> None:
    """同 doc_key 的其它生效版本置为 superseded（版本链只保留一个现行有效）。"""
    if doc.status != "effective":
        return
    others = (
        db.query(KbDoc)
        .filter(KbDoc.doc_key == doc.doc_key, KbDoc.id != doc.id, KbDoc.status == "effective")
        .all()
    )
    for other in others:
        other.status = "superseded"
        if doc.effective_from:
            try:
                other.effective_to = (date.fromisoformat(doc.effective_from) - timedelta(days=1)).isoformat()
            except ValueError:
                pass


def create_doc(
    db: Session,
    *,
    title: str,
    chunks: list[Chunk],
    category: str = DEFAULT_CATEGORY,
    department: str = "",
    doc_no: str = "",
    version: str = "v1.0",
    status: str = "effective",
    effective_from: str = "",
    effective_to: str = "",
    tags=None,
    owner: str = "",
    summary: str = "",
    source_name: str = "",
    source_rel_path: str = "",
    source_hash: str = "",
    source_size: int = 0,
    page_count: int = 0,
    char_count: int = 0,
    ocr_pages: int = 0,
    index_status: str = "ok",
    index_error: str = "",
    supersedes_id: int | None = None,
) -> KbDoc:
    title = str(title or "").strip()[:MAX_TITLE]
    if not title:
        raise KbError("文档标题不能为空")
    version = (str(version or "").strip() or "v1.0")[:50]
    status = validate_status(status)
    effective_from = validate_date(effective_from, "生效日期")
    effective_to = validate_date(effective_to, "废止日期")
    if effective_from and effective_to and effective_from > effective_to:
        raise KbError("生效日期不能晚于废止日期")
    doc_key = normalize_doc_key(title, doc_no)
    if doc_key == "untitled":
        raise KbError("文档标题不能只包含符号")

    existing = (
        db.query(KbDoc)
        .filter(KbDoc.doc_key == doc_key, KbDoc.version == version)
        .first()
    )
    if existing is not None:
        raise DuplicateDoc(f"同一制度已存在版本 {version}（文档：{existing.title}）", existing.id)

    doc = KbDoc(
        title=title,
        category=(category or DEFAULT_CATEGORY).strip()[:50],
        department=str(department or "").strip()[:100],
        doc_no=str(doc_no or "").strip()[:100],
        doc_key=doc_key,
        version=version,
        status=status,
        effective_from=effective_from,
        effective_to=effective_to,
        tags=clean_tags(tags),
        owner=str(owner or "").strip()[:100],
        summary=(summary or "").strip()[:MAX_SUMMARY],
        source_name=str(source_name or "")[:255],
        source_rel_path=str(source_rel_path or "")[:500],
        source_hash=str(source_hash or "")[:64],
        source_size=int(source_size or 0),
        page_count=int(page_count or 0),
        char_count=int(char_count or 0),
        chunk_count=len(chunks),
        ocr_pages=int(ocr_pages or 0),
        index_status=index_status,
        index_error=str(index_error or "")[:500],
        supersedes_id=supersedes_id,
        indexed_at=datetime.now(),
    )
    db.add(doc)
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
    index_chunks(db, doc.id, chunks)
    _supersede_previous(db, doc)
    db.commit()
    db.refresh(doc)
    return doc


def update_meta(db: Session, doc: KbDoc, payload: dict) -> KbDoc:
    if "title" in payload and payload["title"] is not None:
        title = str(payload["title"]).strip()[:MAX_TITLE]
        if not title:
            raise KbError("文档标题不能为空")
        doc.title = title
    if "category" in payload and payload["category"] is not None:
        doc.category = str(payload["category"]).strip()[:50] or DEFAULT_CATEGORY
    if "department" in payload and payload["department"] is not None:
        doc.department = str(payload["department"]).strip()[:100]
    if "doc_no" in payload and payload["doc_no"] is not None:
        doc.doc_no = str(payload["doc_no"]).strip()[:100]
    if "version" in payload and payload["version"] is not None:
        version = str(payload["version"]).strip()[:50]
        if not version:
            raise KbError("版本号不能为空")
        conflict = (
            db.query(KbDoc)
            .filter(KbDoc.doc_key == doc.doc_key, KbDoc.version == version, KbDoc.id != doc.id)
            .first()
        )
        if conflict is not None:
            raise DuplicateDoc(f"同一制度已存在版本 {version}（文档：{conflict.title}）", conflict.id)
        doc.version = version
    if "status" in payload and payload["status"] is not None:
        doc.status = validate_status(payload["status"])
    if "effective_from" in payload and payload["effective_from"] is not None:
        doc.effective_from = validate_date(payload["effective_from"], "生效日期")
    if "effective_to" in payload and payload["effective_to"] is not None:
        doc.effective_to = validate_date(payload["effective_to"], "废止日期")
    if doc.effective_from and doc.effective_to and doc.effective_from > doc.effective_to:
        raise KbError("生效日期不能晚于废止日期")
    if "tags" in payload and payload["tags"] is not None:
        doc.tags = clean_tags(payload["tags"])
    if "owner" in payload and payload["owner"] is not None:
        doc.owner = str(payload["owner"]).strip()[:100]
    if "summary" in payload and payload["summary"] is not None:
        doc.summary = str(payload["summary"]).strip()[:MAX_SUMMARY]
    if "doc_no" in payload or "title" in payload:
        new_key = normalize_doc_key(doc.title, doc.doc_no)
        conflict = (
            db.query(KbDoc)
            .filter(KbDoc.doc_key == new_key, KbDoc.version == doc.version, KbDoc.id != doc.id)
            .first()
        )
        if conflict is not None:
            raise DuplicateDoc(
                f"修改后与已有文档冲突：{conflict.title} {conflict.version}（ID {conflict.id}）",
                conflict.id,
            )
        doc.doc_key = new_key
    _supersede_previous(db, doc)
    db.commit()
    db.refresh(doc)
    return doc


def delete_doc(db: Session, doc: KbDoc) -> None:
    path: Path | None = None
    if doc.source_rel_path:
        try:
            path = resolve_source(doc.source_rel_path)
        except KbError:
            path = None
    remove_index(db, doc.id)
    db.query(KbChunk).filter(KbChunk.doc_id == doc.id).delete(synchronize_session=False)
    # 引用该文档的记忆只清空引用（置空），记忆本体保留，不出孤儿 doc id
    db.query(Memory).filter(Memory.source_doc_id == doc.id).update(
        {Memory.source_doc_id: None}, synchronize_session=False)
    db.delete(doc)
    db.commit()
    if path is not None:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


def list_docs(
    db: Session,
    *,
    q: str = "",
    category: str = "",
    department: str = "",
    status: str = "",
    include_inactive: bool = False,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[KbDoc], int]:
    query = db.query(KbDoc)
    if q:
        like = f"%{escape_like(q.strip())}%"
        query = query.filter(
            KbDoc.title.like(like, escape="\\")
            | KbDoc.doc_no.like(like, escape="\\")
            | KbDoc.summary.like(like, escape="\\")
        )
    if category:
        query = query.filter(KbDoc.category == category)
    if department:
        query = query.filter(KbDoc.department == department)
    if status:
        query = query.filter(KbDoc.status == validate_status(status))
    elif not include_inactive:
        query = query.filter(KbDoc.status == "effective")
    total = query.count()
    items = (
        query.order_by(KbDoc.updated_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return items, total


def facets(db: Session) -> dict:
    def _group(column):
        rows = (
            db.query(column, func.count(KbDoc.id))
            .filter(column != "")
            .group_by(column)
            .order_by(func.count(KbDoc.id).desc())
            .all()
        )
        return [{"value": value, "count": count} for value, count in rows]

    return {
        "categories": _group(KbDoc.category),
        "departments": _group(KbDoc.department),
        "statuses": _group(KbDoc.status),
        "total": db.query(func.count(KbDoc.id)).scalar() or 0,
    }


def outline(db: Session, doc: KbDoc, preview_chars: int = 60) -> list[dict]:
    rows = (
        db.query(KbChunk)
        .filter(KbChunk.doc_id == doc.id)
        .order_by(KbChunk.seq)
        .all()
    )
    return [
        {
            "chunk_id": row.id,
            "seq": row.seq,
            "heading": row.heading,
            "page_no": row.page_no,
            "preview": (row.text or "")[:preview_chars],
        }
        for row in rows
    ]


def version_chain(db: Session, doc: KbDoc) -> list[dict]:
    rows = (
        db.query(KbDoc)
        .filter(KbDoc.doc_key == doc.doc_key)
        .order_by(KbDoc.effective_from.desc(), KbDoc.id.desc())
        .all()
    )
    return [
        {
            "id": row.id,
            "version": row.version,
            "status": row.status,
            "effective_from": row.effective_from,
            "effective_to": row.effective_to,
            "is_current": row.id == doc.id,
        }
        for row in rows
    ]


def doc_out(doc: KbDoc, *, detail: bool = False, db: Session | None = None) -> dict:
    data = {
        "id": doc.id,
        "title": doc.title,
        "category": doc.category,
        "department": doc.department,
        "doc_no": doc.doc_no,
        "version": doc.version,
        "status": doc.status,
        "effective_from": doc.effective_from,
        "effective_to": doc.effective_to,
        "tags": list(doc.tags or []),
        "owner": doc.owner,
        "summary": doc.summary,
        "source_name": doc.source_name,
        "source_size": doc.source_size,
        "page_count": doc.page_count,
        "char_count": doc.char_count,
        "chunk_count": doc.chunk_count,
        "ocr_pages": int(doc.ocr_pages or 0),
        "index_status": doc.index_status,
        "index_error": doc.index_error,
        "created_at": doc.created_at.isoformat(sep=" ", timespec="seconds") if doc.created_at else "",
        "updated_at": doc.updated_at.isoformat(sep=" ", timespec="seconds") if doc.updated_at else "",
    }
    if detail and db is not None:
        data["outline"] = outline(db, doc)
        data["versions"] = version_chain(db, doc)
    return data


def read_context(db: Session, doc: KbDoc, chunk_id: int, before: int = 1, after: int = 1) -> dict:
    target = db.get(KbChunk, chunk_id)
    if target is None or target.doc_id != doc.id:
        raise KbError("指定的文档块不存在")
    low = max(target.seq - max(before, 0), 0)
    high = target.seq + max(after, 0)
    rows = (
        db.query(KbChunk)
        .filter(KbChunk.doc_id == doc.id, KbChunk.seq >= low, KbChunk.seq <= high)
        .order_by(KbChunk.seq)
        .all()
    )
    return {
        "doc_id": doc.id,
        "doc_title": doc.title,
        "version": doc.version,
        "heading": target.heading,
        "page_no": target.page_no,
        "items": [
            {
                "chunk_id": row.id,
                "seq": row.seq,
                "heading": row.heading,
                "page_no": row.page_no,
                "text": row.text,
                "is_hit": row.id == target.id,
            }
            for row in rows
        ],
    }
