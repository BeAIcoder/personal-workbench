"""定时 Agent 调度：到点自动按提示词执行一次 Agent 对话，结果落笔记。

- 调度循环为 lifespan 内启动的 asyncio 任务（见 app.main），每 20 秒检查一次到期任务；
- 每个任务串行执行（同任务上一次未结束则跳过本轮），执行中状态记录在内存 `_running`；
- 执行链路直接复用编排器 chat()：路由（指定/关键词/LLM）→ Agent 执行 → 落一条定时任务会话 →
  同时把回复写成笔记（标题带任务名与时间，标签含「定时任务」）便于回看；
- LLM 未配置时本轮标记 skipped，不做无谓请求。
"""
import asyncio
import logging
import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Callable

from sqlalchemy import or_, update

from .database import SessionLocal
from .models import Note, ScheduledJob, ScheduledJobRunFact
from .agents import orchestrator
from .agents.llm import llm_status

logger = logging.getLogger(__name__)

# 单定时器的安全上限：正常情况下按最近到期时间精确唤醒；系统休眠/时钟跳变时
# 每 300 秒兜底重算一次，不再做固定 20 秒轮询。
TICK_SECONDS = 300
RECENT_MAX = 20            # 每个任务保留的最近运行记录条数
RUN_FACTS_KEEP = 500       # 每个任务保留的运行事实条数（有界日志，不照搬「永不压缩」）
AGENT_JOB_TIMEOUT_SECONDS = 1800  # 定时 Agent 对话超时：超时记为 unknown，不自动重试
TERMINAL_STATUSES = {"ok", "error", "skipped", "unknown"}
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


def next_due_at(job: ScheduledJob, now: datetime | None = None) -> datetime | None:
    """纯函数：下一次到期时间（单定时器用）；无法在 366 天内到期返回 None。

    与 job_due 的分工：job_due 负责「现在是否该跑」（含错过补偿），
    next_due_at 负责「下一轮什么时候醒」。两者共享同一套调度语义。
    """
    now = now or datetime.now()
    if job.schedule_type == "cron":
        expr = job.cron_expr or ""
        cursor = now.replace(second=0, microsecond=0)
        if job.last_run_at is not None and job.last_run_at > cursor:
            cursor = job.last_run_at.replace(second=0, microsecond=0)
        cursor += timedelta(minutes=1)
        end = now + timedelta(days=366)
        while cursor <= end:
            if cron_matches(expr, cursor):
                return cursor
            cursor += timedelta(minutes=1)
        return None
    if job.schedule_type == "daily":
        if not job.daily_at or len(job.daily_at) != 5 or job.daily_at[2] != ":":
            return None
        try:
            hh, mm = int(job.daily_at[:2]), int(job.daily_at[3:])
        except ValueError:
            return None
        if not (0 <= hh <= 23 and 0 <= mm <= 59):
            return None
        due_at = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if job.last_run_at is not None and job.last_run_at.date() >= now.date():
            return due_at + timedelta(days=1)
        return due_at if now < due_at else now
    # interval（默认 60 分钟兜底）
    interval = job.interval_minutes or 60
    if job.last_run_at is None:
        return now
    return job.last_run_at + timedelta(minutes=interval)


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


def _fact_type_for_status(status: str) -> str:
    if status == "unknown":
        return "run.unknown"
    if status == "skipped":
        return "run.skipped"
    return "run.finished"


def _append_run_fact(db, job_id: int, run_id: str, fact_type: str, payload: dict,
                     created_at: datetime | None = None) -> None:
    db.add(ScheduledJobRunFact(
        job_id=job_id, run_id=run_id, type=fact_type, payload=payload,
        created_at=created_at or datetime.now(),
    ))


def _prune_run_facts(db, job_id: int) -> None:
    """有界保留：每个任务最多 RUN_FACTS_KEEP 条事实，超出删最旧。"""
    stale_ids = [
        row[0]
        for row in (
            db.query(ScheduledJobRunFact.id)
            .filter(ScheduledJobRunFact.job_id == job_id)
            .order_by(ScheduledJobRunFact.id.desc())
            .offset(RUN_FACTS_KEEP)
            .all()
        )
    ]
    if stale_ids:
        db.query(ScheduledJobRunFact).filter(
            ScheduledJobRunFact.id.in_(stale_ids)
        ).delete(synchronize_session=False)


def _finish_run(
    job_id: int,
    run_id: str,
    status: str,
    summary: str,
    *,
    update_last_run: bool = True,
    reply: str | None = None,
    agent_info: dict | None = None,
    error: str | None = None,
    last_run_at_override: datetime | None = None,
) -> int | None:
    """同一事务内追加终态事实并更新投影；返回写入的笔记 id（非 ok 为 None）。"""
    if status not in TERMINAL_STATUSES:
        summary = f"运行回执状态无效（{status}），已按失败记录"
        status = "error"
    with SessionLocal() as db:
        job = db.get(ScheduledJob, job_id)
        if job is None:
            return None
        # 幂等：同一 run 的终态事实已存在时不再重复写投影（迟到重复提交）
        terminal = (
            db.query(ScheduledJobRunFact.id)
            .filter(
                ScheduledJobRunFact.run_id == run_id,
                ScheduledJobRunFact.type.in_(("run.finished", "run.unknown", "run.skipped")),
            )
            .first()
        )
        if terminal is not None:
            db.rollback()
            return None
        now = datetime.now()
        started = job.running_since or now
        duration = max((now - started).total_seconds(), 0.0)
        note_id = None
        if status == "ok" and reply is not None:
            agent_info = agent_info or {}
            note = Note(
                title=f"【定时任务】{job.name} {now:%Y-%m-%d %H:%M}",
                content=(
                    f"任务：{job.name}\n提示词：{job.prompt}\n"
                    f"执行专家：{agent_info.get('label') or agent_info.get('name', '')}\n\n{reply}"
                ),
                tags=["定时任务", job.name],
            )
            db.add(note)
            db.flush()
            note_id = note.id
        if update_last_run:
            job.last_run_at = last_run_at_override or now
        job.last_status = status
        if status in ("error", "unknown"):
            job.last_error = (error or summary or "")[:500]
        elif status == "ok":
            job.last_error = ""
        runs = list(job.recent_runs or [])
        runs.insert(0, {
            "at": now.strftime("%Y-%m-%d %H:%M"),
            "status": status,
            "summary": (summary or "")[:200],
            "run_id": run_id,
            "duration_seconds": round(duration, 1),
        })
        job.recent_runs = runs[:RECENT_MAX]
        if note_id is not None:
            job.last_note_id = note_id
        job.running_token = ""
        job.running_since = None
        _append_run_fact(db, job_id, run_id, _fact_type_for_status(status), {
            "status": status,
            "summary": (summary or "")[:500],
            "note_id": note_id,
            "duration_seconds": round(duration, 1),
            "error": (error or "")[:500] if error else "",
        }, created_at=now)
        db.flush()
        _prune_run_facts(db, job_id)
        db.commit()
        return note_id


def _begin_run(job_id: int) -> str | None:
    """原子占入一轮运行；已有运行占入时返回 None（防并发/多进程重复执行）。"""
    run_id = uuid.uuid4().hex
    now = datetime.now()
    with SessionLocal() as db:
        job = db.get(ScheduledJob, job_id)
        if job is None:
            return None
        result = db.execute(
            update(ScheduledJob)
            .where(
                ScheduledJob.id == job_id,
                or_(ScheduledJob.running_token == "", ScheduledJob.running_token.is_(None)),
            )
            .values(running_token=run_id, running_since=now)
        )
        if result.rowcount != 1:
            db.rollback()
            return None
        _append_run_fact(db, job_id, run_id, "run.started", {
            "started_at": now.isoformat(timespec="seconds"),
            "mode": str(job.mode or ""),
            "action": str(job.action or ""),
        }, created_at=now)
        db.commit()
        return run_id


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


def _notify_unknown(job: SimpleNamespace, run_id: str, reason: str) -> None:
    """中断运行登记到调试中心问题记录；同标题未关闭去重，失败不阻断恢复。"""
    try:
        from .modules.debug_center import service as debug_service
        from .modules.debug_center.schemas import IssueCreate

        title = f"定时任务执行结果未知：{job.name}"
        for issue in debug_service.load_issues():
            if issue.get("title") == title and issue.get("status") not in ("done", "wontfix"):
                return
        debug_service.create_issue(IssueCreate(
            title=title,
            severity="P2",
            source="analysis",
            page="/jobs",
            description=f"任务 #{job.id} 上一轮运行未正常收尾：{reason}",
            evidence=run_id,
        ))
    except Exception:
        logger.warning("定时任务 unknown 告警登记失败（已忽略）", exc_info=True)


def recover_interrupted_runs() -> int:
    """启动恢复：把仍带 running_token 的任务标为 unknown，不自动重试。"""
    with SessionLocal() as db:
        rows = db.query(ScheduledJob).all()
        pending = [
            SimpleNamespace(id=row.id, name=row.name, run_id=row.running_token,
                            started_at=row.running_since)
            for row in rows if row.running_token
        ]
    recovered = 0
    for item in pending:
        reason = "服务重启时上一轮执行未完成，结果未知；未自动重试"
        try:
            _finish_run(
                item.id, item.run_id, "unknown", reason,
                update_last_run=True, error=reason,
                last_run_at_override=item.started_at,
            )
            _notify_unknown(SimpleNamespace(id=item.id, name=item.name), item.run_id, reason)
            recovered += 1
        except Exception:
            logger.warning("定时任务 %s 中断恢复失败", item.id, exc_info=True)
    return recovered


def replay_recent_runs(facts: list, limit: int = RECENT_MAX) -> list[dict]:
    """独立 oracle：只按事实顺序重建终态运行投影，不使用 _finish_run 的逻辑。"""
    runs: dict[str, dict] = {}
    for fact in facts:
        if fact.type not in ("run.finished", "run.unknown", "run.skipped"):
            continue
        payload = fact.payload or {}
        runs[fact.run_id] = {
            "at": fact.created_at.strftime("%Y-%m-%d %H:%M"),
            "status": payload.get("status") or fact.type.removeprefix("run."),
            "summary": str(payload.get("summary") or "")[:200],
            "run_id": fact.run_id,
            "duration_seconds": payload.get("duration_seconds", 0),
            "_order": fact.id,
        }
    ordered = sorted(runs.values(), key=lambda item: item["_order"], reverse=True)
    for item in ordered:
        item.pop("_order", None)
    return ordered[:limit]


def verify_projection(job_id: int, limit: int = RECENT_MAX) -> tuple[bool, list[dict]]:
    """核对投影是否可由事实重建；只比较带 run_id 的新条目，忽略升级前历史条目。"""
    with SessionLocal() as db:
        job = db.get(ScheduledJob, job_id)
        if job is None:
            return True, []
        facts = (
            db.query(ScheduledJobRunFact)
            .filter(ScheduledJobRunFact.job_id == job_id)
            .order_by(ScheduledJobRunFact.id.asc())
            .all()
        )
        projection = [
            {
                "at": item.get("at"),
                "status": item.get("status"),
                "summary": item.get("summary"),
                "run_id": item.get("run_id"),
                "duration_seconds": item.get("duration_seconds", 0),
            }
            for item in (job.recent_runs or [])
            if item.get("run_id")
        ][:limit]
    replayed = replay_recent_runs(facts, limit=limit)
    return replayed == projection, replayed


async def _run_action_job(job: SimpleNamespace) -> dict:
    handler = JOB_ACTIONS.get(job.action)
    if handler is None:
        return {"status": "error", "summary": f"动作未注册：{job.action}"}
    try:
        result = await asyncio.to_thread(handler)
    except Exception as exc:
        logger.warning("模块动作任务 %s 执行失败", job.id, exc_info=True)
        return {"status": "error", "summary": str(exc)[:500]}
    # 回执坏了只记失败，不重跑 handler：业务可能已经发生，重做副作用代价更高。
    if not isinstance(result, dict):
        return {"status": "error", "summary": "模块动作回执无效：handler 必须返回 dict"}
    status = str(result.get("status") or "ok")
    if status not in TERMINAL_STATUSES:
        return {"status": "error", "summary": f"模块动作回执无效：未知状态 {status}"}
    summary = result.get("message") or result.get("summary") or "完成"
    return {"status": status, "summary": str(summary)[:200]}


async def run_job(job_id: int, manual: bool = False) -> dict:
    """执行一次定时任务；数据库只在执行前后的短会话与 worker 线程中使用。

    manual=True 时，未到点的 daily/cron 不占用调度槽位；当前已到点则正常消费。
    skipped 不消费 daily/cron 槽位，自动调度侧按五分钟节流。
    """
    if job_id in _running:
        return {"status": "skipped", "summary": "上一次执行尚未结束，已跳过"}
    _running.add(job_id)
    touch: bool | None = None
    run_id: str | None = None
    try:
        job = await asyncio.to_thread(_load_job, job_id)
        if job is None:
            return {"status": "error", "summary": "任务不存在"}
        touch = not manual or job.schedule_type == "interval" or job_due(job, datetime.now())
        run_id = await asyncio.to_thread(_begin_run, job_id)
        if run_id is None:
            return {"status": "skipped", "summary": "上一轮运行仍在进行中（或占入标记未释放）"}
        if job.action:
            outcome = await _run_action_job(job)
        elif not (await asyncio.to_thread(lambda: llm_status()["configured"])):
            outcome = {"status": "skipped", "summary": "模型未配置"}
        else:
            try:
                result = await asyncio.wait_for(
                    orchestrator.chat(
                        session_id=f"{SESSION_PREFIX}{job.id}",
                        message=job.prompt,
                        agent_name=job.agent_name,
                        mode=job.mode,
                    ),
                    timeout=AGENT_JOB_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError:
                # 超时不是「确定失败」：工具可能已经写入了数据，不自动重试。
                outcome = {
                    "status": "unknown",
                    "summary": (
                        f"执行超过 {AGENT_JOB_TIMEOUT_SECONDS} 秒，结果未知；"
                        "可能已产生部分副作用，未自动重试"
                    ),
                }
            else:
                if result.get("ok"):
                    reply = str(result.get("reply") or "")
                    outcome = {
                        "status": "ok", "summary": reply[:200], "reply": reply,
                        "agent_info": result.get("agent") or {},
                    }
                else:
                    message = str(result.get("message") or "执行失败")
                    outcome = {"status": "error", "summary": message[:500]}
        status = str(outcome.get("status") or "error")
        update_last_run = touch and not (status == "skipped" and job.schedule_type in ("daily", "cron"))
        if status == "unknown":
            # 未知结果不自动补跑，避免重复产生副作用；由人工核实后手动执行。
            update_last_run = True
        note_id = await asyncio.to_thread(
            _finish_run, job_id, run_id, status, str(outcome.get("summary") or ""),
            update_last_run=bool(update_last_run),
            reply=outcome.get("reply"),
            agent_info=outcome.get("agent_info"),
            error=str(outcome.get("summary") or "") if status in ("error", "unknown") else None,
        )
        if status == "skipped" and job.schedule_type in ("daily", "cron"):
            _skipped_at[job_id] = datetime.now()
        else:
            _skipped_at.pop(job_id, None)
        response = {"status": status, "summary": str(outcome.get("summary") or "")[:200]}
        if note_id is not None:
            response["note_id"] = note_id
        return response
    except asyncio.CancelledError:
        # 保留 running_token：进程退出/任务取消后由启动恢复标 unknown，不在这里假装失败。
        raise
    except Exception as exc:
        logger.warning("定时任务 %s 执行失败", job_id, exc_info=True)
        if run_id:
            try:
                await asyncio.to_thread(
                    _finish_run, job_id, run_id, "error", str(exc)[:500],
                    update_last_run=bool(touch), error=str(exc)[:500],
                )
            except Exception:
                logger.warning("定时任务 %s 错误状态落库失败", job_id, exc_info=True)
        return {"status": "error", "summary": str(exc)[:200]}
    finally:
        _running.discard(job_id)
        wake()


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
            wake()

    try:
        task = asyncio.create_task(bounded_run())
    except Exception:
        _reserved.discard(job_id)
        raise
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


_wake_event: asyncio.Event | None = None
_scheduler_loop: asyncio.AbstractEventLoop | None = None


def wake() -> None:
    """唤醒单定时器重新计算到期时间；可从同步路由线程调用。"""
    loop = _scheduler_loop
    event = _wake_event
    if loop is None or event is None or loop.is_closed():
        return
    try:
        loop.call_soon_threadsafe(event.set)
    except RuntimeError:
        pass


def _next_wakeup(now: datetime) -> tuple[list[int], datetime | None]:
    """返回 (现在到期的任务, 下一次到期时间)；由线程执行，避免阻塞事件循环。"""
    with SessionLocal() as db:
        jobs = db.query(ScheduledJob).filter(ScheduledJob.enabled == True).all()
    due: list[int] = []
    next_at: datetime | None = None
    for job in jobs:
        if job.id in _running or job.id in _reserved:
            continue
        if job_due(job, now):
            due.append(job.id)
            continue
        candidate = next_due_at(job, now)
        if candidate is not None and (next_at is None or candidate < next_at):
            next_at = candidate
    return due, next_at


async def scheduler_loop() -> None:
    """单定时器调度：只在最近到期时间或任务变更时醒来，不做固定间隔轮询。"""
    global _wake_event, _scheduler_loop
    _scheduler_loop = asyncio.get_running_loop()
    _wake_event = asyncio.Event()
    logger.info("定时任务调度器已启动（单定时器，安全上限 %ds）", TICK_SECONDS)
    try:
        while True:
            try:
                due_ids, next_at = await asyncio.to_thread(_next_wakeup, datetime.now())
                for job_id in due_ids:
                    _spawn(job_id)
                wait = float(TICK_SECONDS)
                if next_at is not None:
                    remaining = (next_at - datetime.now()).total_seconds()
                    wait = min(max(remaining, 0.05), wait)
                if TICK_SECONDS <= 0:
                    wait = 0  # 测试接缝：0 = 立即重算，不等待
                try:
                    await asyncio.wait_for(_wake_event.wait(), timeout=wait)
                except asyncio.TimeoutError:
                    pass
                _wake_event.clear()
            except asyncio.CancelledError:
                logger.info("定时任务调度器已停止")
                raise
            except Exception:
                logger.warning("调度循环异常（已忽略，继续下一轮）", exc_info=True)
                await asyncio.sleep(min(max(TICK_SECONDS, 1), 60))
    finally:
        _wake_event = None
        _scheduler_loop = None


async def shutdown() -> None:
    """取消并等待全部自动调度派生任务，再释放调度器进程内状态。"""
    global _semaphore, _semaphore_loop, _wake_event, _scheduler_loop
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
    _wake_event = None
    _scheduler_loop = None
