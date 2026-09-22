"""定时任务接口：/api/jobs（定时 Agent：到点自动执行，结果落笔记）"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import ScheduledJob
from ..schemas import ScheduledJobCreate, ScheduledJobOut, ScheduledJobUpdate
from .. import scheduler

router = APIRouter(prefix="/jobs", tags=["定时任务"])


def _get_or_404(db: Session, job_id: int) -> ScheduledJob:
    job = db.get(ScheduledJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"任务 {job_id} 不存在")
    return job


@router.get("", response_model=list[ScheduledJobOut], summary="定时任务列表")
def list_jobs(db: Session = Depends(get_db)):
    return db.query(ScheduledJob).order_by(ScheduledJob.id.asc()).all()


@router.post("", response_model=ScheduledJobOut, status_code=status.HTTP_201_CREATED, summary="新建定时任务")
def create_job(payload: ScheduledJobCreate, db: Session = Depends(get_db)):
    job = ScheduledJob(**payload.model_dump())
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


@router.put("/{job_id}", response_model=ScheduledJobOut, summary="更新定时任务（仅传需要修改的字段）")
def update_job(job_id: int, payload: ScheduledJobUpdate, db: Session = Depends(get_db)):
    job = _get_or_404(db, job_id)
    data = payload.model_dump(exclude_unset=True)
    for field, value in data.items():
        setattr(job, field, value)
    # 更新后做一次调度参数一致性校验（与创建相同规则）
    if job.schedule_type == "daily":
        if not job.daily_at:
            raise HTTPException(status_code=422, detail="daily 类型必须提供 daily_at（HH:MM）")
        job.interval_minutes = None
    else:
        if job.interval_minutes is None:
            raise HTTPException(status_code=422, detail="interval 类型必须提供 interval_minutes（5-10080）")
        job.daily_at = None
    db.commit()
    db.refresh(job)
    return job


@router.delete("/{job_id}", status_code=status.HTTP_204_NO_CONTENT, summary="删除定时任务")
def delete_job(job_id: int, db: Session = Depends(get_db)):
    job = _get_or_404(db, job_id)
    db.delete(job)
    db.commit()


@router.post("/{job_id}/run", summary="立即执行一次（不影响下次到点触发）")
async def run_job_now(job_id: int, db: Session = Depends(get_db)):
    _get_or_404(db, job_id)
    result = await scheduler.run_job(job_id)
    return result
