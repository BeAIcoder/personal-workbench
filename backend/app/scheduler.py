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
from typing import Callable

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

# 模块动作注册表：name -> sync handler() -> dict（由各模块在 import 时注册，
# 定时任务到点执行注册动作而非 LLM 对话，如 finance_board.run）
JOB_ACTIONS: dict[str, Callable[[], dict]] = {}


def register_action(name: str, handler: Callable[[], dict]) -> None:
    """注册一个可被定时任务调用的模块动作（重名覆盖，模块启停即生效）。"""
    JOB_ACTIONS[name] = handler


def list_actions() -> list[str]:
    """已注册的模块动作名（供前端下拉与测试）。"""
    return sorted(JOB_ACTIONS)


def _cron_field_match(field: str, value: int, lo: int, hi: int) -> bool:
    """单个 cron 字段匹配：支持 *、逗号列表、连字符区间、步长（*/n 与 a-b/n）。

    逗号列表中的非法项只跳过该项，不毒化整个字段（如 "0,60" 的 0 仍生效）。
    """
    field = field.strip()
    if field == "*":
        return True
    for part in field.split(","):
        part = part.strip()
        if not part:
            continue
        step = 1
        if "/" in part:
            part, step_s = part.split("/", 1)
            try:
                step = int(step_s)
            except ValueError:
                continue
            if step < 1:
                continue
        if part == "*":
            a, b = lo, hi
        elif "-" in part:
            a_s, b_s = part.split("-", 1)
            try:
                a, b = int(a_s), int(b_s)
            except ValueError:
                continue
        else:
            try:
                a = int(part)
            except ValueError:
                continue
            b = hi if step > 1 else a  # 单值加步长视为从该值到上限
        if a > b or a < lo or b > hi:
            continue
        if a <= value <= b and (value - a) % step == 0:
            return True
    return False


_CRON_BOUNDS = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 7))  # 分 时 日 月 周


def _cron_field_valid(field: str, lo: int, hi: int) -> bool:
    """单个 cron 字段合法性：每一项必须是 数字 / * / a-b / 带 /n 步长，且在界内。"""
    field = field.strip()
    if not field:
        return False
    for part in field.split(","):
        part = part.strip()
        if not part:
            return False
        step = 1
        if "/" in part:
            part, _, step_s = part.partition("/")
            if not step_s.isdigit() or int(step_s) < 1:
                return False
            step = int(step_s)
        if part == "*":
            continue
        if "-" in part:
            a_s, _, b_s = part.partition("-")
            if not (a_s.isdigit() and b_s.isdigit()):
                return False
            a, b = int(a_s), int(b_s)
            if a > b or a < lo or b > hi:
                return False
        else:
            if not part.isdigit():
                return False
            a = int(part)
            b = hi if step > 1 else a  # 与 _cron_field_match 同口径
            if a < lo or b > hi:
                return False
    return True


def validate_cron_expr(expr: str) -> bool:
    """五段 cron（分 时 日 月 周）整体合法性校验（创建/更新入口共用）。"""
    parts = (expr or "").split()
    if len(parts) != 5:
        return False
    return all(_cron_field_valid(field, lo, hi) for field, (lo, hi) in zip(parts, _CRON_BOUNDS))


def cron_matches(expr: str, now: datetime | None = None) -> bool:
    """纯函数：五段 cron（分 时 日 月 周）匹配判断；表达式非法返回 False。

    周字段 0/7 均视为周日；日与周同时受限时按「任一命中」语义（与 cron 惯例一致
    的简化：本实现为 AND，单用户场景以明确写法规避，如仅用其一）。
    """
    now = now or datetime.now()
    parts = (expr or "").split()
    if len(parts) != 5:
        return False
    minute, hour, dom, month, dow = parts
    if not _cron_field_match(minute, now.minute, 0, 59):
        return False
    if not _cron_field_match(hour, now.hour, 0, 23):
        return False
    if not _cron_field_match(dom, now.day, 1, 31):
        return False
    if not _cron_field_match(month, now.month, 1, 12):
        return False
    weekday = now.weekday() + 1  # cron: 0=周日 … 6=周六；python: 0=周一
    sunday = 7 if weekday == 7 else weekday
    return _cron_field_match(dow, sunday, 0, 7) or _cron_field_match(dow, sunday % 7, 0, 7)


def job_due(job: ScheduledJob, now: datetime | None = None) -> bool:
    """纯函数：给定当前时间判断任务是否到期（单测直接覆盖）。"""
    now = now or datetime.now()
    if job.schedule_type == "cron":
        expr = job.cron_expr or ""
        if cron_matches(expr, now):
            # 同一自然分钟内不重复触发
            return job.last_run_at is None or job.last_run_at.replace(second=0, microsecond=0) != now.replace(second=0, microsecond=0)
        # 错过补偿：在 (lower, now) 内找最近一次匹配分钟（上限回溯 24h），找到即 due。
        # 下界同时受创建时间约束：新建任务不得补跑创建之前的匹配点（例如 10:30:40
        # 创建 * * * * *，10:31 检查时不能把 10:30 当作「错过」再跑一次）。
        lower = now - timedelta(hours=24)
        if job.last_run_at is not None:
            lower = max(lower, job.last_run_at.replace(second=0, microsecond=0))
        created_at = getattr(job, "created_at", None)
        if created_at is not None:
            lower = max(lower, created_at)
        cursor = now.replace(second=0, microsecond=0)
        while True:
            cursor -= timedelta(minutes=1)
            if cursor <= lower:  # lower 分钟本身已执行过/超出窗口，不计
                return False
            if cron_matches(expr, cursor):
                return True
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


def _record(db, job: ScheduledJob, status: str, summary: str, note_id: int | None = None,
            update_last_run: bool = True) -> None:
    now = datetime.now()
    # 手动「立即运行」对 daily/cron 不更新 last_run_at，避免吞掉到点触发（interval 维持顺延）
    if update_last_run:
        job.last_run_at = now
    job.last_status = status
    runs = list(job.recent_runs or [])
    runs.insert(0, {"at": now.strftime("%Y-%m-%d %H:%M"), "status": status, "summary": summary[:200]})
    job.recent_runs = runs[:RECENT_MAX]
    if note_id is not None:
        job.last_note_id = note_id
    db.commit()


async def _run_action_job(db, job: ScheduledJob, update_last_run: bool = True) -> dict:
    """模块动作任务：线程池执行注册的同步 handler，结果记 recent_runs。"""
    handler = JOB_ACTIONS.get(job.action)
    if handler is None:
        _record(db, job, "error", f"动作未注册：{job.action}", update_last_run=update_last_run)
        return {"status": "error", "summary": f"动作未注册：{job.action}"}
    try:
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(None, handler)
        summary = str(result.get("message") or result.get("summary") or "完成")[:200]
        status = result.get("status", "ok")
        _record(db, job, status, summary, update_last_run=update_last_run)
        return {"status": status, "summary": summary}
    except Exception as exc:
        logger.warning("模块动作任务 %s 执行失败", job.id, exc_info=True)
        _record(db, job, "error", str(exc)[:500], update_last_run=update_last_run)
        return {"status": "error", "summary": str(exc)[:200]}


async def run_job(job_id: int, manual: bool = False) -> dict:
    """执行一次定时任务（立即运行按钮与调度循环共用）。返回 {status, summary, note_id?}。

    job.action 非空 = 模块动作任务：到点调用注册的同步 handler（线程池执行），
    不走 LLM、不写笔记，结果只记 recent_runs；handler 抛错记 error。

    manual=True（「立即运行」入口）时，daily/cron 任务不更新 last_run_at，
    不影响下次到点触发；interval 任务维持现状顺延。
    """
    if job_id in _running:
        return {"status": "skipped", "summary": "上一次执行尚未结束，已跳过"}
    _running.add(job_id)
    try:
        with SessionLocal() as db:
            job = db.get(ScheduledJob, job_id)
            if job is None:
                return {"status": "error", "summary": "任务不存在"}
            touch = not manual or job.schedule_type == "interval"
            if job.action:
                return await _run_action_job(db, job, update_last_run=touch)
            if not llm_status()["configured"]:
                _record(db, job, "skipped", "模型未配置", update_last_run=touch)
                return {"status": "skipped", "summary": "模型未配置"}

            result = await orchestrator.chat(
                session_id=f"{SESSION_PREFIX}{job.id}",
                message=job.prompt,
                agent_name=job.agent_name,
                mode=job.mode,
            )
            if not result.get("ok"):
                msg = result.get("message") or "执行失败"
                _record(db, job, "error", msg, update_last_run=touch)
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
            _record(db, job, "ok", result.get("reply", "")[:200], note_id=note.id, update_last_run=touch)
            return {"status": "ok", "summary": result.get("reply", "")[:200], "note_id": note.id}
    except Exception as exc:
        logger.warning("定时任务 %s 执行失败", job_id, exc_info=True)
        with SessionLocal() as db:
            job = db.get(ScheduledJob, job_id)
            if job is not None:
                _record(db, job, "error", str(exc)[:500],
                        update_last_run=not manual or job.schedule_type == "interval")
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
