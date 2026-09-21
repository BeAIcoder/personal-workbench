"""全局搜索接口：/api/search"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Note, Schedule, Task
from ..schemas import SearchItem, SearchResult

router = APIRouter(prefix="/search", tags=["全局搜索"])


def _snippet(text: str | None, keyword: str, limit: int = 60) -> str:
    """返回包含关键词的上下文片段。"""
    text = (text or "").strip().replace("\n", " ")
    if not text:
        return ""
    idx = text.lower().find(keyword.lower())
    if idx < 0:
        return text[:limit] + ("…" if len(text) > limit else "")
    start = max(0, idx - 15)
    frag = text[start:start + limit]
    prefix = "…" if start > 0 else ""
    suffix = "…" if start + limit < len(text) else ""
    return prefix + frag + suffix


@router.get("", response_model=SearchResult, summary="全局搜索（任务 / 日程 / 笔记）")
def search(q: str = Query(..., min_length=1, max_length=100, description="搜索关键词"), db: Session = Depends(get_db)):
    tasks = (
        db.query(Task)
        .filter(or_(Task.title.contains(q, autoescape=True), Task.description.contains(q, autoescape=True)))
        .order_by(Task.updated_at.desc())
        .limit(5)
        .all()
    )
    schedules = (
        db.query(Schedule)
        .filter(
            or_(
                Schedule.title.contains(q, autoescape=True),
                Schedule.location.contains(q, autoescape=True),
                Schedule.description.contains(q, autoescape=True),
            )
        )
        .order_by(Schedule.start_time.asc())
        .limit(5)
        .all()
    )
    notes = (
        db.query(Note)
        .filter(or_(Note.title.contains(q, autoescape=True), Note.content.contains(q, autoescape=True)))
        .order_by(Note.updated_at.desc())
        .limit(5)
        .all()
    )

    task_items = [
        SearchItem(id=t.id, type="task", title=t.title, snippet=_snippet(t.description, q), time=t.due_date or t.updated_at)
        for t in tasks
    ]
    schedule_items = [
        SearchItem(id=s.id, type="schedule", title=s.title, snippet=_snippet(s.location or s.description, q), time=s.start_time)
        for s in schedules
    ]
    note_items = [
        SearchItem(id=n.id, type="note", title=n.title, snippet=_snippet(n.content, q), time=n.updated_at)
        for n in notes
    ]
    return SearchResult(
        q=q,
        total=len(task_items) + len(schedule_items) + len(note_items),
        tasks=task_items,
        schedules=schedule_items,
        notes=note_items,
    )
