"""Task 1 调度器与任务数据生命周期回归。"""
import asyncio
import itertools
import threading
from datetime import datetime

import pytest

from app import scheduler
from app.database import Base, SessionLocal, engine
from app.models import Note, ScheduledJob


@pytest.fixture(autouse=True)
def _ensure_tables():
    Base.metadata.create_all(bind=engine)
    yield
    with SessionLocal() as db:
        db.query(ScheduledJob).filter(ScheduledJob.id >= 910000).delete()
        db.query(Note).filter(Note.title.like("【定时任务】Task1-%")).delete()
        db.commit()
    scheduler._running.clear()
    getattr(scheduler, "_skipped_at", {}).clear()


_ids = itertools.count(910000)


def _job(**overrides):
    values = {
        "id": next(_ids),
        "name": "Task1-调度回归",
        "prompt": "合成提示词",
        "schedule_type": "interval",
        "interval_minutes": 60,
        "created_at": datetime(2026, 1, 1),
        "enabled": True,
    }
    values.update(overrides)
    with SessionLocal() as db:
        row = ScheduledJob(**values)
        db.add(row)
        db.commit()
        return row.id


def _configured(monkeypatch):
    monkeypatch.setattr(scheduler, "llm_status", lambda: {"configured": True})


class _SessionThreadProbe:
    def __init__(self, real, threads, started=None):
        self._real = real
        self._threads = threads
        self._started = started

    def __enter__(self):
        self._real.__enter__()
        return self

    def __exit__(self, *args):
        return self._real.__exit__(*args)

    def __getattr__(self, name):
        value = getattr(self._real, name)
        if not callable(value):
            return value

        def call(*args, **kwargs):
            self._threads.append(threading.get_ident())
            if self._started is not None:
                self._started.set()
            return value(*args, **kwargs)

        return call


def test_six_blocked_llm_jobs_do_not_hold_db_connections_or_block_loop(monkeypatch):
    """回归目标：run_job 若在 await chat 期间保留 Session，本例 checkedout 会是 6。"""
    ids = [_job(name=f"Task1-连接-{i}") for i in range(6)]
    _configured(monkeypatch)
    started = asyncio.Event()
    release = asyncio.Event()
    active = 0

    async def blocked_chat(**kwargs):
        nonlocal active
        active += 1
        if active == 6:
            started.set()
        await release.wait()
        return {"ok": True, "reply": "完成", "agent": {"name": "a", "label": "甲"}}

    monkeypatch.setattr(scheduler.orchestrator, "chat", blocked_chat)

    async def scenario():
        tasks = [asyncio.create_task(scheduler.run_job(job_id)) for job_id in ids]
        try:
            await asyncio.wait_for(started.wait(), 3)
            assert engine.pool.checkedout() == 0
            probe = asyncio.get_running_loop().create_future()
            asyncio.get_running_loop().call_soon(probe.set_result, True)
            assert await asyncio.wait_for(probe, 1) is True
        finally:
            release.set()
            results = await asyncio.gather(*tasks)
        assert all(item["status"] == "ok" for item in results)

    asyncio.run(scenario())


def test_spawn_bounds_six_jobs_to_three_concurrent_executions(monkeypatch):
    """回归目标：_spawn 若不经过默认大小 3 的信号量，六个 chat 会同时进入。"""
    ids = [_job(name=f"Task1-并发-{i}") for i in range(6)]
    _configured(monkeypatch)
    release = asyncio.Event()
    three_entered = asyncio.Event()
    active = 0
    peak = 0
    entered = 0

    async def blocked_chat(**kwargs):
        nonlocal active, peak, entered
        active += 1
        entered += 1
        peak = max(peak, active)
        if entered == 3:
            three_entered.set()
        try:
            await release.wait()
            return {"ok": True, "reply": "完成", "agent": {}}
        finally:
            active -= 1

    monkeypatch.setattr(scheduler.orchestrator, "chat", blocked_chat)

    async def scenario():
        for job_id in ids:
            scheduler._spawn(job_id)
        await asyncio.wait_for(three_entered.wait(), 3)
        try:
            assert entered == 3
            assert peak == 3
        finally:
            release.set()
        tasks = list(scheduler._tasks)
        results = await asyncio.gather(*tasks)
        assert len(results) == 6
        assert entered == 6
        assert peak == 3

    asyncio.run(scenario())


def test_scheduler_scan_and_run_persistence_are_offloaded(monkeypatch):
    """回归目标：扫描 query 与执行 get/commit 任一落在事件循环线程都会失败。"""
    _job(name="Task1-线程卸载")
    loop_ident = threading.get_ident()
    scan_threads = []
    scan_started = threading.Event()
    real_session = scheduler.SessionLocal

    def scan_session():
        return _SessionThreadProbe(real_session(), scan_threads, scan_started)

    monkeypatch.setattr(scheduler, "SessionLocal", scan_session)
    monkeypatch.setattr(scheduler, "TICK_SECONDS", 0)

    async def scan():
        task = asyncio.create_task(scheduler.scheduler_loop())
        try:
            assert await asyncio.to_thread(scan_started.wait, 2)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scan())
    assert scan_threads
    assert all(ident != loop_ident for ident in scan_threads)

    run_job_id = _job(name="Task1-落库线程", action="test.task1.offload", prompt="")
    run_threads = []

    def run_session():
        return _SessionThreadProbe(real_session(), run_threads)

    monkeypatch.setattr(scheduler, "SessionLocal", run_session)
    scheduler.register_action("test.task1.offload", lambda: {"status": "ok", "message": "完成"})
    try:
        result = asyncio.run(scheduler.run_job(run_job_id))
    finally:
        scheduler.JOB_ACTIONS.pop("test.task1.offload", None)
    assert result["status"] == "ok"
    assert run_threads
    assert all(ident != loop_ident for ident in run_threads)


def test_shutdown_cancels_and_awaits_spawned_jobs(monkeypatch):
    """回归目标：没有 shutdown 或 shutdown 不 gather 时，阻塞派生任务会泄漏。"""
    job_id = _job(name="Task1-停机")
    _configured(monkeypatch)
    started = asyncio.Event()

    async def blocked_chat(**kwargs):
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(scheduler.orchestrator, "chat", blocked_chat)

    async def scenario():
        scheduler._spawn(job_id)
        await asyncio.wait_for(started.wait(), 2)
        spawned = list(scheduler._tasks)
        assert spawned
        await asyncio.wait_for(scheduler.shutdown(), 2)
        assert all(task.done() and task.cancelled() for task in spawned)
        assert not scheduler._tasks
        assert job_id not in scheduler._running
        assert job_id not in scheduler._reserved

    asyncio.run(scenario())


def test_legacy_job_rows_do_not_break_list_and_are_normalized(client):
    """回归目标：Out 继承输入校验会让空 cron 行导致整表响应 500。"""
    with SessionLocal() as db:
        bad_cron = ScheduledJob(
            id=910100, name="Task1-脏cron", prompt="x", schedule_type="cron",
            cron_expr="", enabled=True, created_at=datetime(2026, 1, 1),
        )
        bad_interval = ScheduledJob(
            id=910101, name="Task1-脏interval", prompt="x", schedule_type="interval",
            interval_minutes=None, enabled=True, created_at=datetime(2026, 1, 1),
        )
        empty = ScheduledJob(
            id=910102, name="Task1-空执行体", prompt="", action="",
            schedule_type="interval", interval_minutes=60, enabled=True,
            created_at=datetime(2026, 1, 1),
        )
        db.add_all([bad_cron, bad_interval, empty])
        db.commit()

    client.raise_server_exceptions = False
    try:
        response = client.get("/api/jobs")
    finally:
        client.raise_server_exceptions = True
    assert response.status_code == 200, response.text
    assert len(response.json()) == 3

    with SessionLocal() as db:
        changes = scheduler.normalize_legacy_jobs(db)
        rows = {row.id: row for row in db.query(ScheduledJob).all()}
        assert rows[910100].enabled is False
        assert scheduler.job_due(rows[910100], datetime(2026, 11, 3, 9, 0)) is False
        assert rows[910101].interval_minutes == 60
        assert rows[910101].enabled is False
        assert rows[910102].enabled is False
        assert {change["id"] for change in changes} == {910100, 910101, 910102}


def test_last_error_is_truncated_and_cleared_after_success():
    """回归目标：_record 不写 last_error 或成功时不清空都会失败。"""
    action = "test.task1.failure"
    message = "失败" * 300

    def failing():
        raise RuntimeError(message)

    scheduler.register_action(action, failing)
    job_id = _job(name="Task1-错误记录", action=action, prompt="")
    try:
        assert asyncio.run(scheduler.run_job(job_id))["status"] == "error"
        with SessionLocal() as db:
            row = db.get(ScheduledJob, job_id)
            assert row.last_status == "error"
            assert row.last_error == message[:500]
        scheduler.register_action(action, lambda: {"status": "ok", "message": "恢复"})
        assert asyncio.run(scheduler.run_job(job_id))["status"] == "ok"
        with SessionLocal() as db:
            assert db.get(ScheduledJob, job_id).last_error == ""
    finally:
        scheduler.JOB_ACTIONS.pop(action, None)


def test_skipped_daily_stays_due_and_is_throttled_for_five_minutes(monkeypatch):
    """回归目标：skipped 若更新 last_run_at 会吞槽位；若无节流会反复写记录。"""
    class FakeDateTime(datetime):
        current = datetime(2026, 11, 3, 9, 0, 20)

        @classmethod
        def now(cls):
            return cls.current

    monkeypatch.setattr(scheduler, "datetime", FakeDateTime)
    monkeypatch.setattr(scheduler, "llm_status", lambda: {"configured": False})
    job_id = _job(
        name="Task1-跳过节流", schedule_type="daily", daily_at="09:00",
        interval_minutes=None,
    )

    async def scenario():
        scheduler._spawn(job_id)
        await asyncio.gather(*list(scheduler._tasks))
        scheduler._spawn(job_id)
        await asyncio.sleep(0)
        assert not scheduler._tasks

    asyncio.run(scenario())
    with SessionLocal() as db:
        row = db.get(ScheduledJob, job_id)
        assert row.last_status == "skipped"
        assert row.last_run_at is None
        assert len(row.recent_runs) == 1
        assert scheduler.job_due(row, datetime(2026, 11, 3, 9, 5)) is True


def test_manual_daily_or_cron_consumes_slot_only_when_currently_due(monkeypatch):
    """回归目标：手动任务若一律不 touch，到点槽位会双跑；若一律 touch，未到点会吞槽。"""
    class FakeDateTime(datetime):
        current = datetime(2026, 11, 3, 9, 0, 20)

        @classmethod
        def now(cls):
            return cls.current

    _configured(monkeypatch)

    async def ok_chat(**kwargs):
        return {"ok": True, "reply": "完成", "agent": {}}

    monkeypatch.setattr(scheduler.orchestrator, "chat", ok_chat)
    monkeypatch.setattr(scheduler, "datetime", FakeDateTime)
    due_id = _job(
        name="Task1-到点手动", schedule_type="cron", cron_expr="0 9 * * *",
        interval_minutes=None,
    )
    early_id = _job(
        name="Task1-提前手动", schedule_type="daily", daily_at="10:00",
        interval_minutes=None,
    )
    assert asyncio.run(scheduler.run_job(due_id, manual=True))["status"] == "ok"
    assert asyncio.run(scheduler.run_job(early_id, manual=True))["status"] == "ok"
    with SessionLocal() as db:
        due = db.get(ScheduledJob, due_id)
        early = db.get(ScheduledJob, early_id)
        assert due.last_run_at == datetime(2026, 11, 3, 9, 0, 20)
        assert scheduler.job_due(due, datetime(2026, 11, 3, 9, 0, 30)) is False
        assert early.last_run_at is None
        assert scheduler.job_due(early, datetime(2026, 11, 3, 10, 0)) is True


def test_legacy_job_normalization_is_idempotent():
    """回归目标：已禁用的非法行若仍进入 changes，每次启动都会重复落库与告警。"""
    job_id = _job(
        name="Task1-归一化幂等", schedule_type="cron", cron_expr="",
        interval_minutes=None,
    )
    with SessionLocal() as db:
        first = scheduler.normalize_legacy_jobs(db)
        assert [item["id"] for item in first] == [job_id]
    with SessionLocal() as db:
        assert scheduler.normalize_legacy_jobs(db) == []
        assert db.get(ScheduledJob, job_id).enabled is False


def test_spawn_deduplicates_waiting_job_across_due_ticks(monkeypatch):
    """回归目标：三个槽位被占满时，同 job 多 tick 的等待副本在释放后不得重复执行。"""
    blockers = [_job(name=f"Task1-占位-{i}") for i in range(3)]
    target = _job(name="Task1-预约去重")
    _configured(monkeypatch)
    releases = [asyncio.Event() for _ in range(3)]
    blockers_started = asyncio.Event()
    blocker_calls = 0
    target_calls = 0

    async def fake_chat(**kwargs):
        nonlocal blocker_calls, target_calls
        session_id = kwargs["session_id"]
        if session_id == f"job-{target}":
            target_calls += 1
            return {"ok": True, "reply": "目标完成", "agent": {}}
        index = blockers.index(int(session_id.removeprefix("job-")))
        blocker_calls += 1
        if blocker_calls == 3:
            blockers_started.set()
        await releases[index].wait()
        return {"ok": True, "reply": "占位完成", "agent": {}}

    monkeypatch.setattr(scheduler.orchestrator, "chat", fake_chat)

    async def scenario():
        def spawn(job_id):
            before = set(scheduler._tasks)
            scheduler._spawn(job_id)
            created = set(scheduler._tasks) - before
            return next(iter(created), None)

        blocker_tasks = [spawn(job_id) for job_id in blockers]
        await asyncio.wait_for(blockers_started.wait(), 3)
        target_tasks = [spawn(target) for _ in range(3)]
        try:
            for index, blocker_task in enumerate(blocker_tasks):
                releases[index].set()
                await asyncio.wait_for(blocker_task, 3)
                if index < len(target_tasks) and target_tasks[index] is not None:
                    await asyncio.wait_for(target_tasks[index], 3)
            await asyncio.gather(*list(scheduler._tasks), return_exceptions=True)
        finally:
            for event in releases:
                event.set()
            await asyncio.gather(*list(scheduler._tasks), return_exceptions=True)

    asyncio.run(scenario())
    assert target_calls == 1
    with SessionLocal() as db:
        notes = db.query(Note).filter(Note.title.like("【定时任务】Task1-预约去重%")).count()
        assert notes == 1


def test_spawn_rechecks_due_after_semaphore_wait(monkeypatch):
    """回归目标：等待期间手动执行已消费槽位，获得信号量后必须重新判 due。"""
    blockers = [_job(name=f"Task1-到期占位-{i}") for i in range(3)]
    target = _job(name="Task1-到期复核")
    _configured(monkeypatch)
    release = asyncio.Event()
    blockers_started = asyncio.Event()
    blocker_calls = 0
    target_calls = 0

    async def fake_chat(**kwargs):
        nonlocal blocker_calls, target_calls
        if kwargs["session_id"] == f"job-{target}":
            target_calls += 1
            return {"ok": True, "reply": "手动完成", "agent": {}}
        blocker_calls += 1
        if blocker_calls == 3:
            blockers_started.set()
        await release.wait()
        return {"ok": True, "reply": "占位完成", "agent": {}}

    monkeypatch.setattr(scheduler.orchestrator, "chat", fake_chat)

    async def scenario():
        for job_id in blockers:
            scheduler._spawn(job_id)
        await asyncio.wait_for(blockers_started.wait(), 3)
        scheduler._spawn(target)
        manual_result = await scheduler.run_job(target, manual=True)
        assert manual_result["status"] == "ok"
        assert manual_result["summary"] == "手动完成"
        release.set()
        await asyncio.gather(*list(scheduler._tasks))

    asyncio.run(scenario())
    assert target_calls == 1


def test_manual_exception_crossing_due_time_does_not_consume_slot(monkeypatch):
    """回归目标：提前手动开始时 touch=False，跨到点异常仍不得更新 last_run_at。"""
    class FakeDateTime(datetime):
        current = datetime(2026, 11, 3, 8, 59, 59)

        @classmethod
        def now(cls):
            return cls.current

    _configured(monkeypatch)
    monkeypatch.setattr(scheduler, "datetime", FakeDateTime)

    async def failing_chat(**kwargs):
        FakeDateTime.current = datetime(2026, 11, 3, 9, 0, 1)
        raise RuntimeError("跨到点失败")

    monkeypatch.setattr(scheduler.orchestrator, "chat", failing_chat)
    job_id = _job(
        name="Task1-跨到点异常", schedule_type="daily", daily_at="09:00",
        interval_minutes=None,
    )
    result = asyncio.run(scheduler.run_job(job_id, manual=True))
    assert result["status"] == "error"
    with SessionLocal() as db:
        row = db.get(ScheduledJob, job_id)
        assert row.last_error == "跨到点失败"
        assert row.last_run_at is None
        assert scheduler.job_due(row, datetime(2026, 11, 3, 9, 0, 1)) is True
