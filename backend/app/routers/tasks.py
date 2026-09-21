"""任务管理接口：/api/tasks"""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import case, or_
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Task
from ..schemas import Page, TaskCreate, TaskOut, TaskUpdate
from ..utils import parse_dt

router = APIRouter(prefix="/tasks", tags=["任务管理"])

# 优先级排序权重：紧急 > 高 > 中 > 低
PRIORITY_ORDER = case(
    (Task.priority == "urgent", 0),
    (Task.priority == "high", 1),
    (Task.priority == "medium", 2),
    (Task.priority == "low", 3),
    else_=4,
)

SORTS = {
    "created_desc": Task.created_at.desc(),
    "created_asc": Task.created_at.asc(),
    "due_asc": Task.due_date.asc().nullslast(),
    "due_desc": Task.due_date.desc().nullsfirst(),
    "priority_desc": PRIORITY_ORDER.asc(),
}


def _get_or_404(db: Session, task_id: int) -> Task:
    task = db.get(Task, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"任务 {task_id} 不存在")
    return task


@router.get("", response_model=Page[TaskOut], summary="任务列表（筛选 / 搜索 / 排序 / 分页）")
def list_tasks(
    status: Optional[str] = Query(None, description="按状态：todo / in_progress / done"),
    priority: Optional[str] = Query(None, description="按优先级：low / medium / high / urgent"),
    category: Optional[str] = Query(None, description="按分类精确匹配"),
    overdue: Optional[bool] = Query(None, description="只看已逾期（未完成且截止时间已过）"),
    q: Optional[str] = Query(None, max_length=100, description="关键词，匹配标题或描述"),
    due_before: Optional[str] = Query(None, description="截止时间不晚于该时间，如 2026-09-07"),
    due_after: Optional[str] = Query(None, description="截止时间不早于该时间"),
    sort: str = Query(
        "created_desc",
        description="排序：created_desc / created_asc / due_asc / due_desc / priority_desc",
    ),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(10, ge=1, le=100, description="每页条数"),
    db: Session = Depends(get_db),
):
    query = db.query(Task)
    if status:
        query = query.filter(Task.status == status)
    if priority:
        query = query.filter(Task.priority == priority)
    if category:
        query = query.filter(Task.category == category)
    if overdue:
        query = query.filter(Task.status != "done", Task.due_date.isnot(None), Task.due_date < datetime.now())
    if q:
        query = query.filter(
            or_(
                Task.title.contains(q, autoescape=True),
                Task.description.contains(q, autoescape=True),
            )
        )
    if due_before:
        query = query.filter(Task.due_date <= parse_dt(due_before, end_of_day=True))
    if due_after:
        query = query.filter(Task.due_date >= parse_dt(due_after))

    total = query.count()
    items = (
        query.order_by(SORTS.get(sort, SORTS["created_desc"]))
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return Page(items=items, total=total, page=page, page_size=page_size)


@router.post("", response_model=TaskOut, status_code=status.HTTP_201_CREATED, summary="新建任务")
def create_task(payload: TaskCreate, db: Session = Depends(get_db)):
    task = Task(**payload.model_dump())
    if task.status == "done":
        task.completed_at = datetime.now()
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


@router.get("/{task_id}", response_model=TaskOut, summary="任务详情")
def get_task(task_id: int, db: Session = Depends(get_db)):
    return _get_or_404(db, task_id)


@router.put("/{task_id}", response_model=TaskOut, summary="更新任务（仅传需要修改的字段）")
def update_task(task_id: int, payload: TaskUpdate, db: Session = Depends(get_db)):
    task = _get_or_404(db, task_id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(task, field, value)
    if task.status == "done":
        if task.completed_at is None:
            task.completed_at = datetime.now()
    else:
        task.completed_at = None
    db.commit()
    db.refresh(task)
    return task


@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT, summary="删除任务")
def delete_task(task_id: int, db: Session = Depends(get_db)):
    task = _get_or_404(db, task_id)
    db.delete(task)
    db.commit()
