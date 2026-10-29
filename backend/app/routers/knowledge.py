"""知识库与长期记忆接口：/api/knowledge（RAG 检索与索引、记忆 CRUD、对话抽取、统计/导入导出）。"""
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..agents import memory as memory_svc
from ..agents import rag
from ..database import get_db

router = APIRouter(prefix="/knowledge", tags=["知识库与记忆"])


# ---------- RAG ----------

class RagSearchIn(BaseModel):
    query: str = Field(min_length=1, description="检索问题或关键词")
    top_k: int = Field(default=5, ge=1, le=20)


@router.get("/rag/stats", summary="知识库索引状态（笔记数/块数/词表）")
def rag_stats():
    return rag.stats()


@router.post("/rag/rebuild", summary="重建知识库索引")
def rag_rebuild():
    return rag.rebuild_index()


@router.post("/rag/search", summary="知识库检索（BM25）")
def rag_search(payload: RagSearchIn):
    return {"items": rag.search(payload.query, payload.top_k)}


# ---------- 长期记忆 ----------

class MemoryIn(BaseModel):
    content: str = Field(min_length=1, max_length=2000, description="记忆内容")
    tags: list[str] = Field(default_factory=list)
    pinned: bool = False
    type: str = Field(default="", description="类型：user/feedback/project/reference（空=未分类）")


class MemoryUpdateIn(BaseModel):
    content: str | None = Field(None, min_length=1, max_length=2000)
    tags: list[str] | None = None
    pinned: bool | None = None
    type: str | None = None


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
                                     type=payload.type)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.put("/memories/{memory_id}", summary="更新记忆（内容/标签/置顶/类型）")
def update_memory(memory_id: int, payload: MemoryUpdateIn, db: Session = Depends(get_db)):
    try:
        return memory_svc.update_memory(db, memory_id, payload.model_dump(exclude_unset=True))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/memories/{memory_id}", summary="删除记忆")
def delete_memory(memory_id: int, db: Session = Depends(get_db)):
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
