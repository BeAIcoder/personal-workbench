"""工作台概览接口：/api/dashboard"""
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Note, Schedule, Task
from ..schemas import NoteOut, ScheduleOut, TaskOut

router = APIRouter(prefix="/dashboard", tags=["工作台概览"])

# 概览页「今日日程」有界读取：个人库日程很多时不一次取全量；截断状态可见，
# 用户可点「查看全部」去日程页，不静默丢数据
MAX_TODAY_SCHEDULES = 100


@router.get("/summary", summary="工作台统计数据（概览页一次取全）")
def summary(db: Session = Depends(get_db)):
    now = datetime.now()
    today = now.date()
    day_start = datetime.combine(today, datetime.min.time())
    tomorrow = day_start + timedelta(days=1)

    # ---- 任务统计 ----
    status_counts = dict(db.query(Task.status, func.count()).group_by(Task.status).all())
    todo = status_counts.get("todo", 0)
    in_progress = status_counts.get("in_progress", 0)
    done = status_counts.get("done", 0)
    total_tasks = todo + in_progress + done
    overdue = (
        db.query(func.count())
        .select_from(Task)
        .filter(Task.status != "done", Task.due_date.isnot(None), Task.due_date < now)
        .scalar()
        or 0
    )
    today_due = (
        db.query(func.count())
        .select_from(Task)
        .filter(Task.status != "done", Task.due_date >= day_start, Task.due_date < tomorrow)
        .scalar()
        or 0
    )
    upcoming_tasks = (
        db.query(Task)
        .filter(Task.status != "done", Task.due_date.isnot(None))
        .order_by(Task.due_date.asc())
        .limit(6)
        .all()
    )

    # ---- 日程统计 ----
    today_schedule_query = (
        db.query(Schedule)
        # end_time == day_start 表示日程在今天 00:00 整结束，与今天无交集，不应计入「今日日程」
        .filter(Schedule.start_time < tomorrow, Schedule.end_time > day_start)
    )
    today_schedule_total = today_schedule_query.count()
    today_schedules = (
        today_schedule_query.order_by(Schedule.start_time.asc()).limit(MAX_TODAY_SCHEDULES).all()
    )
    week_end = day_start + timedelta(days=7)
    upcoming_schedule_count = (
        db.query(func.count())
        .select_from(Schedule)
        .filter(Schedule.start_time >= now, Schedule.start_time < week_end)
        .scalar()
        or 0
    )

    # ---- 笔记统计 ----
    note_total = db.query(func.count()).select_from(Note).scalar() or 0
    week_ago = day_start - timedelta(days=6)
    week_note_count = (
        db.query(func.count()).select_from(Note).filter(Note.created_at >= week_ago).scalar() or 0
    )
    recent_notes = db.query(Note).order_by(Note.updated_at.desc()).limit(5).all()

    # ---- 近 7 天完成任务趋势 ----
    # 一次范围查询取回 7 天窗口内的完成时间，再在 Python 侧按天计数。
    # 原先是 for 循环里 7 次 count(*)（7 次全表扫描 + 7 次 SQL 往返），概览页每次刷新都要付
    # 这个代价；合并后只剩 1 次走 completed_at 索引的范围扫描。
    week_start = day_start - timedelta(days=6)
    per_day: dict = {}
    for (completed_at,) in db.query(Task.completed_at).filter(
        Task.completed_at >= week_start, Task.completed_at < tomorrow
    ).all():
        if completed_at is None:
            continue
        key = completed_at.date()
        per_day[key] = per_day.get(key, 0) + 1
    trend = [
        {"date": day.isoformat(), "count": per_day.get(day, 0)}
        for day in ((week_start + timedelta(days=i)).date() for i in range(7))
    ]

    return {
        "task": {
            "total": total_tasks,
            "todo": todo,
            "in_progress": in_progress,
            "done": done,
            "overdue": overdue,
            "today_due": today_due,
            "completion_rate": round(done / total_tasks * 100, 1) if total_tasks else 0.0,
        },
        "schedule": {
            "today_count": len(today_schedules),
            "today_schedule_total": today_schedule_total,
            "today_schedules_truncated": today_schedule_total > len(today_schedules),
            "upcoming_7d": upcoming_schedule_count,
        },
        "note": {"total": note_total, "week_added": week_note_count},
        "upcoming_tasks": [TaskOut.model_validate(t) for t in upcoming_tasks],
        "today_schedules": [ScheduleOut.model_validate(s) for s in today_schedules],
        "recent_notes": [NoteOut.model_validate(n) for n in recent_notes],
        "trend_7d": trend,
    }
