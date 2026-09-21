"""笔记管理接口：/api/notes"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Note
from ..schemas import NoteCreate, NoteOut, NoteUpdate, Page

router = APIRouter(prefix="/notes", tags=["笔记管理"])


def _get_or_404(db: Session, note_id: int) -> Note:
    note = db.get(Note, note_id)
    if note is None:
        raise HTTPException(status_code=404, detail=f"笔记 {note_id} 不存在")
    return note


@router.get("/tags", summary="全部标签（去重）")
def list_tags(db: Session = Depends(get_db)):
    tags: list[str] = []
    for (value,) in db.query(Note.tags).all():
        for t in value or []:
            if t and t not in tags:
                tags.append(t)
    return {"tags": tags}


@router.get("", response_model=Page[NoteOut], summary="笔记列表（搜索 / 标签过滤 / 分页）")
def list_notes(
    q: Optional[str] = Query(None, max_length=100, description="关键词，匹配标题或内容"),
    tag: Optional[str] = Query(None, max_length=20, description="按标签过滤"),
    pinned: Optional[bool] = Query(None, description="只看置顶 / 只看未置顶"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页条数"),
    db: Session = Depends(get_db),
):
    query = db.query(Note)
    if q:
        query = query.filter(
            or_(
                Note.title.contains(q, autoescape=True),
                Note.content.contains(q, autoescape=True),
            )
        )
    if tag:
        # JSON 数组按 ensure_ascii=False 序列化，形如 ["报销"]，用 LIKE 匹配标签（参数绑定）
        query = query.filter(Note.tags.like(f'%"{tag}"%'))
    if pinned is not None:
        query = query.filter(Note.pinned == pinned)

    total = query.count()
    items = (
        query.order_by(Note.pinned.desc(), Note.updated_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return Page(items=items, total=total, page=page, page_size=page_size)


@router.post("", response_model=NoteOut, status_code=status.HTTP_201_CREATED, summary="新建笔记")
def create_note(payload: NoteCreate, db: Session = Depends(get_db)):
    note = Note(**payload.model_dump())
    db.add(note)
    db.commit()
    db.refresh(note)
    return note


@router.get("/{note_id}", response_model=NoteOut, summary="笔记详情")
def get_note(note_id: int, db: Session = Depends(get_db)):
    return _get_or_404(db, note_id)


@router.put("/{note_id}", response_model=NoteOut, summary="更新笔记（仅传需要修改的字段）")
def update_note(note_id: int, payload: NoteUpdate, db: Session = Depends(get_db)):
    note = _get_or_404(db, note_id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(note, field, value)
    db.commit()
    db.refresh(note)
    return note


@router.delete("/{note_id}", status_code=status.HTTP_204_NO_CONTENT, summary="删除笔记")
def delete_note(note_id: int, db: Session = Depends(get_db)):
    note = _get_or_404(db, note_id)
    db.delete(note)
    db.commit()
