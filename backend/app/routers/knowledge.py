"""知识库与长期记忆接口：/api/knowledge（文档库、混合检索、记忆 CRUD、统计/导入导出）。"""
import asyncio
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import config
from ..agents import memory as memory_svc
from ..agents import rag
from ..database import get_db
from ..knowledge import embedding as embedding_svc
from ..knowledge import index as kb_index
from ..knowledge import retrieval as kb_retrieval
from ..knowledge import service as kb_service
from ..knowledge import store as kb_store
from ..knowledge.extract import DocumentParseError, SUPPORTED_SUFFIXES
from ..models import KbDoc
from ..upload import read_upload_limited
from ..utils import DbId

router = APIRouter(prefix="/knowledge", tags=["知识库与记忆"])


# ---------- 文档库 ----------

KB_MAX_UPLOAD = 20 * 1024 * 1024

DOC_META_FIELDS = (
    "category", "department", "doc_no", "version", "status",
    "effective_from", "effective_to", "owner", "summary",
)


class KbDocUpdateIn(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=200)
    category: str | None = Field(None, max_length=50)
    department: str | None = Field(None, max_length=100)
    doc_no: str | None = Field(None, max_length=100)
    version: str | None = Field(None, max_length=50)
    status: str | None = Field(None, max_length=20)
    effective_from: str | None = Field(None, max_length=10)
    effective_to: str | None = Field(None, max_length=10)
    tags: list[str] | str | None = None
    owner: str | None = Field(None, max_length=100)
    summary: str | None = Field(None, max_length=500)


class KbDocCreateIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=10, max_length=500000)
    category: str = Field("制度规范", max_length=50)
    department: str = Field("", max_length=100)
    doc_no: str = Field("", max_length=100)
    version: str = Field("v1.0", max_length=50)
    status: str = Field("effective", max_length=20)
    effective_from: str = Field("", max_length=10)
    effective_to: str = Field("", max_length=10)
    tags: list[str] | str | None = None
    owner: str = Field("", max_length=100)
    summary: str = Field("", max_length=500)


class KbSettingsIn(BaseModel):
    ocr_enabled: bool | None = None
    embedding_enabled: bool | None = None
    embedding_model: str | None = Field(None, max_length=120)
    embedding_base_url: str | None = Field(None, max_length=300)
    embedding_api_key: str | None = Field(None, max_length=300)
    embedding_dim: int | None = Field(None, ge=0, le=4096)


class KbTestIn(BaseModel):
    text: str = Field("公司差旅报销制度", min_length=1, max_length=500)
    # 允许测试弹窗里尚未保存的配置；空值表示沿用已保存配置
    embedding_model: str | None = Field(None, max_length=120)
    embedding_base_url: str | None = Field(None, max_length=300)
    embedding_api_key: str | None = Field(None, max_length=300)
    embedding_dim: int | None = Field(None, ge=0, le=4096)


def _doc_or_404(db: Session, doc_id: int) -> KbDoc:
    doc = db.get(KbDoc, doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail=f"知识库文档 {doc_id} 不存在")
    return doc


def _form_meta(
    category: str,
    department: str,
    doc_no: str,
    version: str,
    status: str,
    effective_from: str,
    effective_to: str,
    tags: str,
    owner: str,
    summary: str,
) -> dict:
    return {
        "category": category,
        "department": department,
        "doc_no": doc_no,
        "version": version,
        "status": status,
        "effective_from": effective_from,
        "effective_to": effective_to,
        "tags": kb_store.clean_tags(tags),
        "owner": owner,
        "summary": summary,
    }


@router.get("/docs", summary="知识库文档列表（关键词/分类/部门/状态筛选）")
def list_docs(
    q: str = Query("", max_length=100),
    category: str = Query("", max_length=50),
    department: str = Query("", max_length=100),
    status: str = Query("", max_length=20),
    include_inactive: bool = Query(False),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    try:
        items, total = kb_store.list_docs(
            db,
            q=q,
            category=category,
            department=department,
            status=status,
            include_inactive=include_inactive,
            page=page,
            page_size=page_size,
        )
    except kb_store.KbError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "items": [kb_store.doc_out(doc) for doc in items],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/docs/facets", summary="知识库筛选项计数（分类/部门/状态）")
def doc_facets(db: Session = Depends(get_db)):
    return kb_store.facets(db)


@router.post("/docs/upload", status_code=201, summary="上传制度/手册文件并建索引")
async def upload_doc(
    background: BackgroundTasks,
    file: UploadFile = File(...),
    title: str = Form(""),
    category: str = Form("制度规范"),
    department: str = Form(""),
    doc_no: str = Form(""),
    version: str = Form("v1.0"),
    status: str = Form("effective"),
    effective_from: str = Form(""),
    effective_to: str = Form(""),
    tags: str = Form(""),
    owner: str = Form(""),
    summary: str = Form(""),
    supersedes_id: int | None = Form(None),
    db: Session = Depends(get_db),
):
    filename = Path(file.filename or "document").name
    suffix = Path(filename).suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的知识库文件类型：{suffix or '无扩展名'}（支持 {', '.join(sorted(SUPPORTED_SUFFIXES))}）",
        )
    try:
        content = await read_upload_limited(file, KB_MAX_UPLOAD)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"文件读取失败或超过 {KB_MAX_UPLOAD // (1024 * 1024)}MB 上限") from exc

    import hashlib

    digest = hashlib.sha256(content).hexdigest()
    existing = kb_service.find_by_hash(db, digest)
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail=f"相同文件已入库：《{existing.title}》{existing.version}（ID {existing.id}）",
        )

    rel, size, digest = kb_store.save_source(filename, content)
    path = Path(config.UPLOAD_DIR) / rel
    meta = _form_meta(
        category, department, doc_no, version, status,
        effective_from, effective_to, tags, owner, summary,
    )
    meta["title"] = title.strip() or Path(filename).stem
    meta["supersedes_id"] = supersedes_id
    try:
        # 解析/切块是 CPU 活，放线程池；单用户场景下会话顺序使用，不存在并发写
        doc = await asyncio.to_thread(
            kb_service.ingest_file,
            db,
            path=path,
            filename=filename,
            source_rel_path=rel,
            source_size=size,
            source_hash=digest,
            meta=meta,
        )
    except kb_store.DuplicateDoc as exc:
        path.unlink(missing_ok=True)
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (DocumentParseError, kb_store.KbError) as exc:
        path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        path.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail="文档解析入库失败，请确认文件格式后重试") from exc

    if doc.index_status == "embedding":
        background.add_task(kb_service.embed_document, doc.id)
    return kb_store.doc_out(doc, detail=True, db=db)


@router.post("/docs", status_code=201, summary="粘贴 Markdown/纯文本创建知识库文档")
async def create_doc_from_text(
    payload: KbDocCreateIn,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
):
    meta = {
        "category": payload.category,
        "department": payload.department,
        "doc_no": payload.doc_no,
        "version": payload.version,
        "status": payload.status,
        "effective_from": payload.effective_from,
        "effective_to": payload.effective_to,
        "tags": kb_store.clean_tags(payload.tags),
        "owner": payload.owner,
        "summary": payload.summary,
    }
    try:
        doc = await asyncio.to_thread(
            kb_service.ingest_text,
            db,
            title=payload.title,
            content=payload.content,
            meta=meta,
        )
    except kb_store.DuplicateDoc as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (DocumentParseError, kb_store.KbError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if doc.index_status == "embedding":
        background.add_task(kb_service.embed_document, doc.id)
    return kb_store.doc_out(doc, detail=True, db=db)


@router.get("/docs/{doc_id}", summary="知识库文档详情（大纲/版本链）")
def get_doc(doc_id: DbId, db: Session = Depends(get_db)):
    return kb_store.doc_out(_doc_or_404(db, doc_id), detail=True, db=db)


@router.put("/docs/{doc_id}", summary="编辑知识库文档元数据")
def update_doc(doc_id: DbId, payload: KbDocUpdateIn, db: Session = Depends(get_db)):
    doc = _doc_or_404(db, doc_id)
    try:
        doc = kb_store.update_meta(db, doc, payload.model_dump(exclude_unset=True))
    except kb_store.DuplicateDoc as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except kb_store.KbError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return kb_store.doc_out(doc, detail=True, db=db)


@router.delete("/docs/{doc_id}", status_code=204, summary="删除知识库文档（级联块/索引/原件）")
def delete_doc(doc_id: DbId, db: Session = Depends(get_db)):
    kb_store.delete_doc(db, _doc_or_404(db, doc_id))


@router.post("/docs/{doc_id}/reindex", summary="重解析原件并重建索引")
async def reindex_doc(
    doc_id: DbId,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
):
    doc = _doc_or_404(db, doc_id)
    try:
        doc = await asyncio.to_thread(kb_service.reindex, db, doc)
    except (DocumentParseError, kb_store.KbError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if doc.index_status == "embedding":
        background.add_task(kb_service.embed_document, doc.id)
    return kb_store.doc_out(doc, detail=True, db=db)


@router.post("/docs/{doc_id}/embed", summary="手动重算该文档的向量索引")
async def embed_doc(doc_id: DbId, background: BackgroundTasks, db: Session = Depends(get_db)):
    doc = _doc_or_404(db, doc_id)
    cfg, reason = embedding_svc.resolve_config_with_reason(db)
    if cfg is None:
        raise HTTPException(status_code=400, detail=reason)
    doc.index_status = "embedding"
    db.commit()
    background.add_task(kb_service.embed_document, doc.id)
    return {"ok": True, "index_status": "embedding"}


@router.get("/docs/{doc_id}/file", summary="下载知识库原件")
def download_doc(doc_id: DbId, db: Session = Depends(get_db)):
    doc = _doc_or_404(db, doc_id)
    try:
        path = kb_store.resolve_source(doc.source_rel_path)
    except kb_store.KbError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    suffix = path.suffix or ".bin"
    ascii_name = f"knowledge-{doc.id}{suffix}"
    headers = {
        "Content-Disposition": f"attachment; filename=\"{ascii_name}\"; filename*=utf-8''{quote(doc.source_name or path.name)}"
    }
    return FileResponse(path, media_type="application/octet-stream", headers=headers)


@router.get("/docs/{doc_id}/chunks/{chunk_id}", summary="读取命中块的前后文")
def doc_chunk_context(
    doc_id: DbId,
    chunk_id: DbId,
    before: int = Query(1, ge=0, le=5),
    after: int = Query(1, ge=0, le=5),
    db: Session = Depends(get_db),
):
    doc = _doc_or_404(db, doc_id)
    try:
        return kb_store.read_context(db, doc, chunk_id, before=before, after=after)
    except kb_store.KbError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# ---------- 向量设置 ----------

@router.get("/settings", summary="知识库设置（向量检索开关/模型/维度）")
def get_settings(db: Session = Depends(get_db)):
    row = embedding_svc.get_settings(db)
    cfg, reason = embedding_svc.resolve_config_with_reason(db)
    data = embedding_svc.public_settings(row)
    data["resolved"] = cfg is not None
    data["resolved_detail"] = reason
    data["vector_ready"] = kb_index.vector_available(db)
    return data


@router.put("/settings", summary="保存知识库设置")
def save_settings(payload: KbSettingsIn, db: Session = Depends(get_db)):
    try:
        row = embedding_svc.save_settings(db, payload.model_dump(exclude_unset=True))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    cfg, reason = embedding_svc.resolve_config_with_reason(db)
    data = embedding_svc.public_settings(row)
    data["resolved"] = cfg is not None
    data["resolved_detail"] = reason
    data["vector_ready"] = kb_index.vector_available(db)
    return data


@router.post("/settings/test", summary="测试 embedding 端点连通性（可用未保存的当前表单值）")
async def test_embedding(payload: KbTestIn, db: Session = Depends(get_db)):
    overrides = {
        "model": payload.embedding_model,
        "base_url": payload.embedding_base_url,
        "api_key": payload.embedding_api_key,
        "dim": payload.embedding_dim,
    }
    overrides = {key: value for key, value in overrides.items() if value not in (None, "")}
    cfg, reason = embedding_svc.resolve_config_with_reason(db, overrides=overrides)
    if cfg is None:
        # 自定义地址缺专用 Key / 地址非法：直接拒绝，绝不发起外呼
        raise HTTPException(status_code=400, detail=reason)
    # 连接测试探测模型真实维度：表单/已保存的旧维度只作保存提示，不能挡住换模型
    cfg = {**cfg, "dim": 0}
    try:
        vectors, dim = await asyncio.to_thread(embedding_svc.embed_texts_sync, [payload.text], cfg)
    except embedding_svc.EmbeddingError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "dim": dim, "vectors": len(vectors)}


# ---------- 检索与索引 ----------

class RagSearchIn(BaseModel):
    query: str = Field(min_length=1, description="检索问题或关键词")
    top_k: int = Field(default=5, ge=1, le=20)
    scope: str = Field("all", description="all / docs / notes")
    category: str = Field("", max_length=50)
    department: str = Field("", max_length=100)
    status: str = Field("", max_length=20)
    include_inactive: bool = False


@router.get("/rag/stats", summary="知识库索引状态（笔记/文档/块/向量）")
def rag_stats(db: Session = Depends(get_db)):
    return {**rag.stats(), "documents": kb_retrieval.stats(db)}


@router.post("/rag/rebuild", summary="重建知识库索引（笔记 + 文档；可选重算向量）")
def rag_rebuild(with_vectors: bool = Query(False), db: Session = Depends(get_db)):
    result = {"notes": rag.rebuild_index(), "documents": kb_index.rebuild_all(db)}
    if with_vectors:
        # 换 embedding 模型/维度后必须全量重算；默认处理全部文档，维度变化会删旧 vec 表重建
        vectors = kb_service.rebuild_vectors()
        result["vectors"] = vectors
        if vectors.get("error") and not vectors.get("done"):
            raise HTTPException(status_code=400, detail=f"向量重建失败：{vectors['error']}")
    return result


@router.post("/rag/search", summary="知识库检索（关键词 + 向量混合）")
async def rag_search(payload: RagSearchIn):
    items = await asyncio.to_thread(
        kb_retrieval.search_items,
        payload.query,
        payload.top_k,
        scope=payload.scope,
        category=payload.category,
        department=payload.department,
        status=payload.status,
        include_inactive=payload.include_inactive,
    )
    return {"items": items}


# ---------- 长期记忆 ----------

class MemoryIn(BaseModel):
    content: str = Field(min_length=1, max_length=2000, description="记忆内容")
    tags: list[str] = Field(default_factory=list)
    pinned: bool = False
    type: str = Field(default="", description="类型：user/feedback/project/reference（空=未分类）")
    source_doc_id: int | None = Field(
        None, description="可选：显式引用的知识库文档 id（不存在/非法返回 400）")


class MemoryUpdateIn(BaseModel):
    content: str | None = Field(None, min_length=1, max_length=2000)
    tags: list[str] | None = None
    pinned: bool | None = None
    type: str | None = None
    source_doc_id: int | None = None  # 显式传 null = 清空引用


class MemoryImportIn(BaseModel):
    text: str = Field(min_length=1, max_length=200000, description="MEMORY.md 风格 Markdown 全文")


class MemoryRecallIn(BaseModel):
    query: str = Field(min_length=1)
    top_k: int = Field(default=5, ge=1, le=10)


class MemoryExtractIn(BaseModel):
    conversation: str = Field(min_length=20, description="对话全文（或最近若干轮）")


@router.get("/memories", summary="记忆列表（关键词/类型过滤，置顶优先）")
def list_memories(keyword: str = "", type: str = "", db: Session = Depends(get_db)):
    return {"items": memory_svc.list_memories(db, keyword=keyword, type=type)}


@router.post("/memories", status_code=201, summary="新建记忆")
def create_memory(payload: MemoryIn, db: Session = Depends(get_db)):
    try:
        return memory_svc.add_memory(db, payload.content, source="manual",
                                     tags=payload.tags, pinned=payload.pinned,
                                     type=payload.type,
                                     source_doc_id=payload.source_doc_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.put("/memories/{memory_id}", summary="更新记忆（内容/标签/置顶/类型）")
def update_memory(memory_id: DbId, payload: MemoryUpdateIn, db: Session = Depends(get_db)):
    try:
        return memory_svc.update_memory(db, memory_id, payload.model_dump(exclude_unset=True))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/memories/{memory_id}", summary="删除记忆")
def delete_memory(memory_id: DbId, db: Session = Depends(get_db)):
    try:
        memory_svc.delete_memory(db, memory_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"ok": True}


@router.post("/memories/recall", summary="记忆检索（与 Agent 工具同一实现）")
def recall_memories(payload: MemoryRecallIn):
    return {"items": memory_svc.recall(payload.query, payload.top_k)}


@router.post("/memories/extract", summary="从对话文本抽取记忆（一次性，LLM）")
async def extract_memories(payload: MemoryExtractIn):
    return await memory_svc.extract_from_conversation(payload.conversation)


@router.get("/memories/stats", summary="记忆统计（总数/来源/类型分布/命中Top）")
def memories_stats(db: Session = Depends(get_db)):
    return memory_svc.memory_stats(db)


@router.get("/memories/export.md", summary="导出记忆为 MEMORY.md 风格 Markdown")
def export_memories_md():
    return PlainTextResponse(memory_svc.export_markdown(), media_type="text/markdown; charset=utf-8")


@router.post("/memories/import.md", summary="导入 MEMORY.md 风格 Markdown（## 节=类型，- 行=条目）")
def import_memories_md(payload: MemoryImportIn, db: Session = Depends(get_db)):
    return memory_svc.import_markdown(db, payload.text)
