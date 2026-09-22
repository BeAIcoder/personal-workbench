"""定时 Agent 调度：到点自动按提示词执行一次 Agent 对话，结果落笔记。

- 调度循环为 lifespan 内启动的 asyncio 任务（见 app.main），每 20 秒检查一次到期任务；
- 每个任务串行执行（同任务上一次未结束则跳过本轮），执行中状态记录在内存 `_running`；
- 执行链路直接复用编排器 chat()：路由（指定/关键词/LLM）→ Agent 执行 → 落一条定时任务会话 →
  同时把回复写成笔记（标题带任务名与时间，标签含「定时任务」）便于回看；
- LLM 未配置时本轮标记 skipped，不做无谓请求。
"""
import asyncio
import logging
from datetime import datetime, timedelta

from .database import SessionLocal
from .models import Note, ScheduledJob
from .agents import orchestrator
from .agents.llm import llm_status

logger = logging.getLogger(__name__)

TICK_SECONDS = 20          # 调度检查间隔
RECENT_MAX = 20            # 每个任务保留的最近运行记录条数
SESSION_PREFIX = "job-"    # 定时任务专用会话前缀

_running: set[int] = set()                 # 正在执行的 job id
_tasks: set[asyncio.Task] = set()          # 后台执行任务引用（防 GC）


def job_due(job: ScheduledJob, now: datetime | None = None) -> bool:
    """纯函数：给定当前时间判断任务是否到期（单测直接覆盖）。"""
    now = now or datetime.now()
    if job.schedule_type == "daily":
        if not job.daily_at or len(job.daily_at) != 5 or job.daily_at[2] != ":":
            return False
        try:
            hh, mm = int(job.daily_at[:2]), int(job.daily_at[3:])
        except ValueError:
            return False
        if not (0 <= hh <= 23 and 0 <= mm <= 59):
            return False
        due_at = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if job.last_run_at is None:
            return now >= due_at
        return now.date() != job.last_run_at.date() and now >= due_at
    # interval（默认 60 分钟兜底）
    interval = job.interval_minutes or 60
    if job.last_run_at is None:
        return True
    return now >= job.last_run_at + timedelta(minutes=interval)


def _record(db, job: ScheduledJob, status: str, summary: str, note_id: int | None = None) -> None:
    job.last_run_at = datetime.now()
    job.last_status = status
    runs = list(job.recent_runs or [])
    runs.insert(0, {"at": job.last_run_at.strftime("%Y-%m-%d %H:%M"), "status": status, "summary": summary[:200]})
    job.recent_runs = runs[:RECENT_MAX]
    if note_id is not None:
        job.last_note_id = note_id
    db.commit()


async def run_job(job_id: int) -> dict:
    """执行一次定时任务（立即运行按钮与调度循环共用）。返回 {status, summary, note_id?}。"""
    if job_id in _running:
        return {"status": "skipped", "summary": "上一次执行尚未结束，已跳过"}
    _running.add(job_id)
    try:
        with SessionLocal() as db:
            job = db.get(ScheduledJob, job_id)
            if job is None:
                return {"status": "error", "summary": "任务不存在"}
            if not llm_status()["configured"]:
                _record(db, job, "skipped", "模型未配置")
                return {"status": "skipped", "summary": "模型未配置"}

            result = await orchestrator.chat(
                session_id=f"{SESSION_PREFIX}{job.id}",
                message=job.prompt,
                agent_name=job.agent_name,
                mode=job.mode,
            )
            if not result.get("ok"):
                msg = result.get("message") or "执行失败"
                _record(db, job, "error", msg)
                return {"status": "error", "summary": msg[:200]}

            agent_info = result.get("agent") or {}
            note = Note(
                title=f"【定时任务】{job.name} {datetime.now():%Y-%m-%d %H:%M}",
                content=(
                    f"任务：{job.name}\n提示词：{job.prompt}\n"
                    f"执行专家：{agent_info.get('label') or agent_info.get('name', '')}\n\n"
                    f"{result.get('reply', '')}"
                ),
                tags=["定时任务", job.name],
            )
            db.add(note)
            db.flush()
            _record(db, job, "ok", result.get("reply", "")[:200], note_id=note.id)
            return {"status": "ok", "summary": result.get("reply", "")[:200], "note_id": note.id}
    except Exception as exc:
        logger.warning("定时任务 %s 执行失败", job_id, exc_info=True)
        with SessionLocal() as db:
            job = db.get(ScheduledJob, job_id)
            if job is not None:
                _record(db, job, "error", str(exc)[:500])
        return {"status": "error", "summary": str(exc)[:200]}
    finally:
        _running.discard(job_id)


def _spawn(job_id: int) -> None:
    task = asyncio.create_task(run_job(job_id))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def scheduler_loop() -> None:
    """调度主循环：每 TICK_SECONDS 检查一次，到期任务异步执行（同任务串行）。"""
    logger.info("定时任务调度器已启动（每 %ds 检查一次）", TICK_SECONDS)
    while True:
        try:
            await asyncio.sleep(TICK_SECONDS)
            now = datetime.now()
            with SessionLocal() as db:
                jobs = db.query(ScheduledJob).filter(ScheduledJob.enabled == True).all()
                due_ids = [j.id for j in jobs if job_due(j, now)]
            for job_id in due_ids:
                _spawn(job_id)
        except asyncio.CancelledError:
            logger.info("定时任务调度器已停止")
            raise
        except Exception:
            logger.warning("调度循环异常（已忽略，继续下一轮）", exc_info=True)
