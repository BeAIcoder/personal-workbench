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
from types import SimpleNamespace
from typing import Callable

from .database import SessionLocal
from .models import Note, ScheduledJob
from .agents import orchestrator
from .agents.llm import llm_status

logger = logging.getLogger(__name__)

TICK_SECONDS = 20          # 调度检查间隔
RECENT_MAX = 20            # 每个任务保留的最近运行记录条数
SESSION_PREFIX = "job-"    # 定时任务专用会话前缀
MAX_CONCURRENT_JOBS = 3    # 自动调度派生任务的并发上限
SKIP_THROTTLE_SECONDS = 300

_running: set[int] = set()                 # 正在执行的 job id
_reserved: set[int] = set()                # 自动调度已预约（含等待信号量）的 job id
_tasks: set[asyncio.Task] = set()          # 后台执行任务引用（防 GC）
_skipped_at: dict[int, datetime] = {}      # skipped 不占到点槽位，仅做内存节流
_semaphore: asyncio.Semaphore | None = None
_semaphore_loop: asyncio.AbstractEventLoop | None = None

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

    周字段 0/7 均视为周日；日与周同时受限时本实现按 AND 同时命中，
    需要任一命中时应拆成两个任务，避免歧义。
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


def normalize_legacy_jobs(db) -> list[dict]:
    """禁用无法安全执行的历史任务；二次执行无变化时不提交、不重复告警。"""
    changes = []
    for job in db.query(ScheduledJob).all():
        before = (job.schedule_type, job.interval_minutes, job.enabled)
        reasons = []
        if job.schedule_type not in ("interval", "daily", "cron"):
            job.schedule_type = "interval"
            job.interval_minutes = 60
            reasons.append("未知调度类型已按 interval/60 分钟归一化")
        if job.schedule_type == "interval":
            interval = job.interval_minutes
            if isinstance(interval, bool) or not isinstance(interval, int) or not 5 <= interval <= 10080:
                job.interval_minutes = 60
                reasons.append("缺失或非法的 interval_minutes 已归一化为 60")
        elif job.schedule_type == "daily":
            value = job.daily_at or ""
            try:
                valid_daily = len(value) == 5 and value[2] == ":"
                hour, minute = int(value[:2]), int(value[3:])
                valid_daily = valid_daily and 0 <= hour <= 23 and 0 <= minute <= 59
            except ValueError:
                valid_daily = False
            if not valid_daily:
                reasons.append("daily_at 缺失或非法")
        elif not validate_cron_expr(job.cron_expr or ""):
            reasons.append("cron_expr 缺失或非法")
        if not str(job.action or "").strip() and not str(job.prompt or "").strip():
            reasons.append("提示词与模块动作均为空")
        if reasons:
            job.enabled = False
            after = (job.schedule_type, job.interval_minutes, job.enabled)
            if after != before:
                changes.append({"id": job.id, "name": job.name, "reasons": reasons})
    if changes:
        db.commit()
        logger.warning("历史定时任务已禁用并归一化：%s", changes)
    return changes


def _record(db, job: ScheduledJob, status: str, summary: str, note_id: int | None = None,
            update_last_run: bool = True) -> None:
    now = datetime.now()
    if update_last_run:
        job.last_run_at = now
    job.last_status = status
    if status == "error":
        job.last_error = summary[:500]
    elif status == "ok":
        job.last_error = ""
    runs = list(job.recent_runs or [])
    runs.insert(0, {"at": now.strftime("%Y-%m-%d %H:%M"), "status": status, "summary": summary[:200]})
    job.recent_runs = runs[:RECENT_MAX]
    if note_id is not None:
        job.last_note_id = note_id
    db.commit()


def _load_job(job_id: int) -> SimpleNamespace | None:
    fields = (
        "id", "name", "prompt", "agent_name", "mode", "schedule_type",
        "interval_minutes", "daily_at", "cron_expr", "action", "enabled", "last_run_at", "created_at",
    )
    with SessionLocal() as db:
        job = db.get(ScheduledJob, job_id)
        if job is None:
            return None
        return SimpleNamespace(**{field: getattr(job, field) for field in fields})


def _persist_result(job_id: int, status: str, summary: str, update_last_run: bool,
                    reply: str | None = None, agent_info: dict | None = None) -> int | None:
    with SessionLocal() as db:
        job = db.get(ScheduledJob, job_id)
        if job is None:
            return None
        note_id = None
        if status == "ok" and reply is not None:
            agent_info = agent_info or {}
            note = Note(
                title=f"【定时任务】{job.name} {datetime.now():%Y-%m-%d %H:%M}",
                content=(
                    f"任务：{job.name}\n提示词：{job.prompt}\n"
                    f"执行专家：{agent_info.get('label') or agent_info.get('name', '')}\n\n{reply}"
                ),
                tags=["定时任务", job.name],
            )
            db.add(note)
            db.flush()
            note_id = note.id
        _record(db, job, status, summary, note_id=note_id, update_last_run=update_last_run)
        return note_id


def _record_exception(job_id: int, message: str, manual: bool, touch: bool | None = None) -> None:
    with SessionLocal() as db:
        job = db.get(ScheduledJob, job_id)
        if job is not None:
            if touch is None:
                touch = not manual or job.schedule_type == "interval" or job_due(job, datetime.now())
            _record(db, job, "error", message[:500], update_last_run=touch)


async def _run_action_job(job: SimpleNamespace) -> dict:
    handler = JOB_ACTIONS.get(job.action)
    if handler is None:
        return {"status": "error", "summary": f"动作未注册：{job.action}"}
    try:
        result = await asyncio.to_thread(handler)
        summary = str(result.get("message") or result.get("summary") or "完成")[:200]
        return {"status": result.get("status", "ok"), "summary": summary}
    except Exception as exc:
        logger.warning("模块动作任务 %s 执行失败", job.id, exc_info=True)
        return {"status": "error", "summary": str(exc)[:500]}


async def run_job(job_id: int, manual: bool = False) -> dict:
    """执行一次定时任务；数据库只在执行前后的短会话与 worker 线程中使用。

    manual=True 时，未到点的 daily/cron 不占用调度槽位；当前已到点则正常消费。
    skipped 不消费 daily/cron 槽位，自动调度侧按五分钟节流。
    """
    if job_id in _running:
        return {"status": "skipped", "summary": "上一次执行尚未结束，已跳过"}
    _running.add(job_id)
    touch: bool | None = None
    try:
        job = await asyncio.to_thread(_load_job, job_id)
        if job is None:
            return {"status": "error", "summary": "任务不存在"}
        touch = not manual or job.schedule_type == "interval" or job_due(job, datetime.now())
        if job.action:
            outcome = await _run_action_job(job)
        elif not (await asyncio.to_thread(lambda: llm_status()["configured"])):
            outcome = {"status": "skipped", "summary": "模型未配置"}
        else:
            result = await orchestrator.chat(
                session_id=f"{SESSION_PREFIX}{job.id}",
                message=job.prompt,
                agent_name=job.agent_name,
                mode=job.mode,
            )
            if result.get("ok"):
                reply = str(result.get("reply") or "")
                outcome = {
                    "status": "ok", "summary": reply[:200], "reply": reply,
                    "agent_info": result.get("agent") or {},
                }
            else:
                message = str(result.get("message") or "执行失败")
                outcome = {"status": "error", "summary": message[:500]}
        status = outcome["status"]
        update_last_run = touch and not (status == "skipped" and job.schedule_type in ("daily", "cron"))
        note_id = await asyncio.to_thread(
            _persist_result, job_id, status, outcome["summary"], update_last_run,
            outcome.get("reply"), outcome.get("agent_info"),
        )
        if status == "skipped" and job.schedule_type in ("daily", "cron"):
            _skipped_at[job_id] = datetime.now()
        else:
            _skipped_at.pop(job_id, None)
        response = {"status": status, "summary": outcome["summary"][:200]}
        if note_id is not None:
            response["note_id"] = note_id
        return response
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning("定时任务 %s 执行失败", job_id, exc_info=True)
        try:
            await asyncio.to_thread(_record_exception, job_id, str(exc), manual, touch)
        except Exception:
            logger.warning("定时任务 %s 错误状态落库失败", job_id, exc_info=True)
        return {"status": "error", "summary": str(exc)[:200]}
    finally:
        _running.discard(job_id)


def _job_semaphore() -> asyncio.Semaphore:
    global _semaphore, _semaphore_loop
    loop = asyncio.get_running_loop()
    if _semaphore is None or _semaphore_loop is not loop:
        _semaphore = asyncio.Semaphore(MAX_CONCURRENT_JOBS)
        _semaphore_loop = loop
    return _semaphore


def _spawn(job_id: int) -> None:
    if job_id in _reserved:
        return
    skipped_at = _skipped_at.get(job_id)
    if skipped_at is not None and datetime.now() - skipped_at < timedelta(seconds=SKIP_THROTTLE_SECONDS):
        return
    _reserved.add(job_id)

    async def bounded_run():
        try:
            async with _job_semaphore():
                job = await asyncio.to_thread(_load_job, job_id)
                if job is None or not job.enabled or not job_due(job, datetime.now()):
                    return {"status": "skipped", "summary": "任务已不在到期状态，已跳过"}
                return await run_job(job_id)
        finally:
            _reserved.discard(job_id)

    try:
        task = asyncio.create_task(bounded_run())
    except Exception:
        _reserved.discard(job_id)
        raise
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


def _scan_due_ids(now: datetime) -> list[int]:
    with SessionLocal() as db:
        jobs = db.query(ScheduledJob).filter(ScheduledJob.enabled == True).all()
        return [job.id for job in jobs if job_due(job, now)]


async def scheduler_loop() -> None:
    """调度主循环：每 TICK_SECONDS 在线程中扫描，到期任务有界异步执行。"""
    logger.info("定时任务调度器已启动（每 %ds 检查一次）", TICK_SECONDS)
    while True:
        try:
            await asyncio.sleep(TICK_SECONDS)
            due_ids = await asyncio.to_thread(_scan_due_ids, datetime.now())
            for job_id in due_ids:
                _spawn(job_id)
        except asyncio.CancelledError:
            logger.info("定时任务调度器已停止")
            raise
        except Exception:
            logger.warning("调度循环异常（已忽略，继续下一轮）", exc_info=True)


async def shutdown() -> None:
    """取消并等待全部自动调度派生任务，再释放调度器进程内状态。"""
    global _semaphore, _semaphore_loop
    tasks = list(_tasks)
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    _tasks.clear()
    _running.clear()
    _reserved.clear()
    _skipped_at.clear()
    _semaphore = None
    _semaphore_loop = None
