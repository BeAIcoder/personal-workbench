"""工作台概览接口：/api/dashboard"""
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Note, Schedule, Task
from ..schemas import NoteOut, ScheduleOut, TaskOut

router = APIRouter(prefix="/dashboard", tags=["工作台概览"])


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
    today_schedules = (
        db.query(Schedule)
        .filter(Schedule.start_time < tomorrow, Schedule.end_time >= day_start)
        .order_by(Schedule.start_time.asc())
        .all()
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
    trend = []
    for i in range(6, -1, -1):
        d = today - timedelta(days=i)
        start = datetime.combine(d, datetime.min.time())
        end = start + timedelta(days=1)
        count = (
            db.query(func.count())
            .select_from(Task)
            .filter(Task.completed_at >= start, Task.completed_at < end)
            .scalar()
            or 0
        )
        trend.append({"date": d.isoformat(), "count": count})

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
        "schedule": {"today_count": len(today_schedules), "upcoming_7d": upcoming_schedule_count},
        "note": {"total": note_total, "week_added": week_note_count},
        "upcoming_tasks": [TaskOut.model_validate(t) for t in upcoming_tasks],
        "today_schedules": [ScheduleOut.model_validate(s) for s in today_schedules],
        "recent_notes": [NoteOut.model_validate(n) for n in recent_notes],
        "trend_7d": trend,
    }
