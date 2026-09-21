"""日程管理接口：/api/schedules"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Schedule
from ..schemas import Page, ScheduleCreate, ScheduleOut, ScheduleUpdate
from ..utils import parse_dt

router = APIRouter(prefix="/schedules", tags=["日程管理"])


def _get_or_404(db: Session, schedule_id: int) -> Schedule:
    schedule = db.get(Schedule, schedule_id)
    if schedule is None:
        raise HTTPException(status_code=404, detail=f"日程 {schedule_id} 不存在")
    return schedule


@router.get("", response_model=Page[ScheduleOut], summary="日程列表（按时间范围 / 关键词查询）")
def list_schedules(
    start: Optional[str] = Query(None, description="起始时间（含），如 2026-09-01"),
    end: Optional[str] = Query(None, description="结束时间（含）"),
    q: Optional[str] = Query(None, max_length=100, description="关键词，匹配标题 / 地点 / 备注"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(100, ge=1, le=200, description="每页条数"),
    db: Session = Depends(get_db),
):
    """时间范围按「日程与区间有交集」匹配，适合日历视图整月拉取。"""
    query = db.query(Schedule)
    if start:
        query = query.filter(Schedule.end_time >= parse_dt(start))
    if end:
        query = query.filter(Schedule.start_time <= parse_dt(end, end_of_day=True))
    if q:
        query = query.filter(
            or_(
                Schedule.title.contains(q, autoescape=True),
                Schedule.location.contains(q, autoescape=True),
                Schedule.description.contains(q, autoescape=True),
            )
        )

    total = query.count()
    items = (
        query.order_by(Schedule.start_time.asc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return Page(items=items, total=total, page=page, page_size=page_size)


@router.post("", response_model=ScheduleOut, status_code=status.HTTP_201_CREATED, summary="新建日程")
def create_schedule(payload: ScheduleCreate, db: Session = Depends(get_db)):
    schedule = Schedule(**payload.model_dump())
    db.add(schedule)
    db.commit()
    db.refresh(schedule)
    return schedule


@router.get("/{schedule_id}", response_model=ScheduleOut, summary="日程详情")
def get_schedule(schedule_id: int, db: Session = Depends(get_db)):
    return _get_or_404(db, schedule_id)


@router.put("/{schedule_id}", response_model=ScheduleOut, summary="更新日程（仅传需要修改的字段）")
def update_schedule(schedule_id: int, payload: ScheduleUpdate, db: Session = Depends(get_db)):
    schedule = _get_or_404(db, schedule_id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(schedule, field, value)
    if schedule.end_time <= schedule.start_time:
        raise HTTPException(status_code=422, detail="结束时间必须晚于开始时间")
    db.commit()
    db.refresh(schedule)
    return schedule


@router.delete("/{schedule_id}", status_code=status.HTTP_204_NO_CONTENT, summary="删除日程")
def delete_schedule(schedule_id: int, db: Session = Depends(get_db)):
    schedule = _get_or_404(db, schedule_id)
    db.delete(schedule)
    db.commit()
