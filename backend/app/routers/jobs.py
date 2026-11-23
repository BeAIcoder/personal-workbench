"""定时任务接口：/api/jobs（定时 Agent：到点自动执行，结果落笔记）"""
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import ScheduledJob, ScheduledJobRunFact
from ..schemas import ScheduledJobCreate, ScheduledJobOut, ScheduledJobUpdate
from .. import scheduler
from ..utils import DbId

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
    scheduler.wake()
    return job


@router.put("/{job_id}", response_model=ScheduledJobOut, summary="更新定时任务（仅传需要修改的字段）")
def update_job(job_id: DbId, payload: ScheduledJobUpdate, db: Session = Depends(get_db)):
    job = _get_or_404(db, job_id)
    data = payload.model_dump(exclude_unset=True)
    # 显式 null 不能写入非空列（否则 IntegrityError → 500）
    for field in ("name", "prompt", "mode", "schedule_type", "cron_expr", "action", "enabled"):
        if field in data and data[field] is None:
            raise HTTPException(status_code=422, detail=f"{field} 不能为空")
    for field, value in data.items():
        setattr(job, field, value)
    # 更新后仍须满足「提示词或模块动作至少一个」（否则该行会让列表响应校验 500）
    if not (str(job.action or "").strip() or str(job.prompt or "").strip()):
        raise HTTPException(status_code=422, detail="请提供提示词，或选择模块动作")
    # 更新后做一次调度参数一致性校验（与创建相同规则）
    if job.schedule_type == "daily":
        if not job.daily_at:
            raise HTTPException(status_code=422, detail="daily 类型必须提供 daily_at（HH:MM）")
        job.interval_minutes = None
    elif job.schedule_type == "cron":
        if not scheduler.validate_cron_expr(job.cron_expr or ""):
            raise HTTPException(status_code=422, detail="cron 类型必须提供合法的五段 cron_expr（分 时 日 月 周）")
        job.interval_minutes = None
        job.daily_at = None
    else:
        if job.interval_minutes is None:
            raise HTTPException(status_code=422, detail="interval 类型必须提供 interval_minutes（5-10080）")
        job.daily_at = None
    db.commit()
    db.refresh(job)
    scheduler.wake()
    return job


@router.delete("/{job_id}", status_code=status.HTTP_204_NO_CONTENT, summary="删除定时任务")
def delete_job(job_id: DbId, db: Session = Depends(get_db)):
    job = _get_or_404(db, job_id)
    db.query(ScheduledJobRunFact).filter(
        ScheduledJobRunFact.job_id == job_id
    ).delete(synchronize_session=False)
    db.delete(job)
    db.commit()
    scheduler.forget_job(job_id)
    scheduler.wake()


@router.post("/{job_id}/run", summary="立即执行一次（不影响下次到点触发）")
async def run_job_now(job_id: DbId, db: Session = Depends(get_db)):
    _get_or_404(db, job_id)
    result = await scheduler.run_job(job_id, manual=True)
    return result


@router.get("/{job_id}/runs", summary="定时任务运行时间线（事实重放 + 投影核对）")
def job_runs(
    job_id: DbId,
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    _get_or_404(db, job_id)
    facts = (
        db.query(ScheduledJobRunFact)
        .filter(ScheduledJobRunFact.job_id == job_id)
        .order_by(ScheduledJobRunFact.id.desc())
        .limit(limit)
        .all()
    )
    facts.reverse()
    timeline = scheduler.replay_recent_runs(facts, limit=limit)
    replay_ok, replayed = scheduler.verify_projection(job_id, limit=min(limit, scheduler.RECENT_MAX))
    return {
        "job_id": job_id,
        "timeline": timeline,
        "replay_ok": replay_ok,
        "mismatch": None if replay_ok else replayed,
    }


@router.get("/actions", summary="已注册的模块动作（供定时任务选择，如 finance_board.run）")
def list_job_actions():
    return {"actions": scheduler.list_actions()}
