"""定时任务运行事实、unknown 恢复与单定时器 due 计算回归。"""
import asyncio
from datetime import datetime, timedelta

import pytest

from app import scheduler
from app.database import Base, SessionLocal, engine
from app.models import ScheduledJob, ScheduledJobRunFact


@pytest.fixture(autouse=True)
def _ensure_tables():
    # conftest 对 client 用例 teardown 只 drop 不重建（TL-11）：非 client 测试
    # 跟在其后必须自建表，否则全量回归中按字母序跑到这里会撞「no such table」。
    Base.metadata.create_all(bind=engine)
    yield


def _job(**kw):
    with SessionLocal() as db:
        job = ScheduledJob(
            name=kw.pop("name", "事实测试"),
            prompt=kw.pop("prompt", "汇总今日事项"),
            schedule_type=kw.pop("schedule_type", "interval"),
            interval_minutes=kw.pop("interval_minutes", 60),
            mode=kw.pop("mode", "readonly"),
            **kw,
        )
        db.add(job)
        db.commit()
        db.refresh(job)
        return job.id


def _row(job_id):
    with SessionLocal() as db:
        job = db.get(ScheduledJob, job_id)
        db.expunge(job)
        return job


def _facts(job_id):
    with SessionLocal() as db:
        return (
            db.query(ScheduledJobRunFact)
            .filter(ScheduledJobRunFact.job_id == job_id)
            .order_by(ScheduledJobRunFact.id.asc())
            .all()
        )


def test_run_facts_and_projection_replay(monkeypatch):
    job_id = _job()
    monkeypatch.setattr(scheduler, "llm_status", lambda: {"configured": True})

    async def fake_chat(**kwargs):
        return {"ok": True, "reply": "今日 3 项待办", "agent": {"name": "realestate", "label": "商业地产管家"}}

    monkeypatch.setattr(scheduler.orchestrator, "chat", fake_chat)
    result = asyncio.run(scheduler.run_job(job_id, manual=True))
    assert result["status"] == "ok"
    assert result["note_id"]

    row = _row(job_id)
    assert row.last_status == "ok"
    assert row.running_token == ""
    assert row.running_since is None
    assert row.recent_runs[0]["status"] == "ok"
    assert row.recent_runs[0]["run_id"]
    assert row.recent_runs[0]["summary"].startswith("今日 3 项")

    facts = _facts(job_id)
    assert [f.type for f in facts] == ["run.started", "run.finished"]
    ok, replayed = scheduler.verify_projection(job_id)
    assert ok is True
    assert replayed[0]["run_id"] == row.recent_runs[0]["run_id"]
    assert replayed[0]["summary"].startswith("今日 3 项")


def test_recover_interrupted_run_marks_unknown_without_rerun(monkeypatch):
    started = datetime.now() - timedelta(minutes=5)
    job_id = _job(running_token="run-interrupted", running_since=started)
    notified = []
    monkeypatch.setattr(scheduler, "_notify_unknown", lambda job, run_id, reason: notified.append((job.id, run_id)))

    assert scheduler.recover_interrupted_runs() == 1
    row = _row(job_id)
    assert row.last_status == "unknown"
    assert "结果未知" in row.last_error
    assert row.running_token == ""
    assert row.running_since is None
    assert row.last_run_at == started
    assert row.recent_runs[0]["status"] == "unknown"
    assert row.recent_runs[0]["run_id"] == "run-interrupted"
    assert notified == [(job_id, "run-interrupted")]

    facts = _facts(job_id)
    assert [f.type for f in facts] == ["run.unknown"]
    assert facts[0].payload["status"] == "unknown"

    # 幂等：再次恢复不会重复写事实/投影
    assert scheduler.recover_interrupted_runs() == 0
    assert len(_facts(job_id)) == 1
    assert len(_row(job_id).recent_runs) == 1


def test_timeout_marks_unknown_and_does_not_rerun(monkeypatch):
    job_id = _job()
    monkeypatch.setattr(scheduler, "llm_status", lambda: {"configured": True})
    monkeypatch.setattr(scheduler, "AGENT_JOB_TIMEOUT_SECONDS", 0.01)
    calls = []

    async def slow_chat(**kwargs):
        calls.append(1)
        await asyncio.sleep(1)
        return {"ok": True, "reply": "不该到这里"}

    monkeypatch.setattr(scheduler.orchestrator, "chat", slow_chat)
    result = asyncio.run(scheduler.run_job(job_id, manual=True))
    assert result["status"] == "unknown"
    assert "结果未知" in result["summary"]
    assert calls == [1]

    row = _row(job_id)
    assert row.last_status == "unknown"
    assert row.running_token == ""
    assert row.last_run_at is not None
    assert row.recent_runs[0]["status"] == "unknown"
    assert [f.type for f in _facts(job_id)] == ["run.started", "run.unknown"]


def test_action_invalid_receipt_records_error_without_rerun(monkeypatch):
    job_id = _job(action="test.bad.receipt", prompt="")
    calls = []

    def handler():
        calls.append(1)
        return "not-a-dict"

    scheduler.register_action("test.bad.receipt", handler)
    try:
        result = asyncio.run(scheduler.run_job(job_id, manual=True))
    finally:
        scheduler.JOB_ACTIONS.pop("test.bad.receipt", None)
    assert result["status"] == "error"
    assert "回执无效" in result["summary"]
    assert calls == [1]  # 回执坏了只记失败，不重跑 handler
    row = _row(job_id)
    assert row.last_status == "error"
    assert "回执无效" in row.last_error


def test_run_facts_retention_is_bounded(monkeypatch):
    job_id = _job()
    monkeypatch.setattr(scheduler, "llm_status", lambda: {"configured": True})
    monkeypatch.setattr(scheduler, "RUN_FACTS_KEEP", 5)

    async def fake_chat(**kwargs):
        return {"ok": True, "reply": "ok", "agent": {}}

    monkeypatch.setattr(scheduler.orchestrator, "chat", fake_chat)
    for _ in range(5):
        asyncio.run(scheduler.run_job(job_id, manual=True))
    # 每轮 started+finished 两条；有界保留 5 条，最旧的被裁掉
    assert len(_facts(job_id)) <= 5
    assert _row(job_id).recent_runs[0]["status"] == "ok"


def test_next_due_at_interval_daily_cron():
    now = datetime(2026, 11, 4, 10, 30, 0)
    interval = ScheduledJob(schedule_type="interval", interval_minutes=60, last_run_at=now - timedelta(minutes=10))
    assert scheduler.next_due_at(interval, now) == now + timedelta(minutes=50)

    daily = ScheduledJob(schedule_type="daily", daily_at="08:30", last_run_at=now - timedelta(days=1))
    assert scheduler.next_due_at(daily, now) == now  # 已过点且昨天跑过 → 现在到期
    daily.last_run_at = datetime(2026, 11, 4, 8, 30)
    assert scheduler.next_due_at(daily, now) == datetime(2026, 11, 5, 8, 30)
    early = ScheduledJob(schedule_type="daily", daily_at="11:00", last_run_at=now - timedelta(days=1))
    assert scheduler.next_due_at(early, now) == datetime(2026, 11, 4, 11, 0)

    cron = ScheduledJob(schedule_type="cron", cron_expr="0 9 * * *", last_run_at=None)
    assert scheduler.next_due_at(cron, now) == datetime(2026, 11, 5, 9, 0)
    cron.last_run_at = datetime(2026, 11, 4, 9, 0)
    assert scheduler.next_due_at(cron, now) == datetime(2026, 11, 5, 9, 0)
    bad = ScheduledJob(schedule_type="daily", daily_at="99:99")
    assert scheduler.next_due_at(bad, now) is None


# ---------------- 调度边界：稀疏 cron、手动 interval、删除节流清理 ----------------

def test_sparse_cron_next_due_is_not_minute_scanned(monkeypatch):
    """RED 回归：永不匹配的 cron 曾逐分钟扫描 366 天（约 52.7 万次 cron_matches）。

    只做结构性硬断言（调用次数），不使用墙钟阈值，避免 xdist/负载下抖动。
    """
    calls = []
    real_match = scheduler.cron_matches

    def counting_match(expr, now=None):
        calls.append(1)
        return real_match(expr, now)

    monkeypatch.setattr(scheduler, "cron_matches", counting_match)
    now = datetime(2026, 11, 4, 10, 30, 0)
    job = ScheduledJob(schedule_type="cron", cron_expr="0 0 31 2 *", last_run_at=None)
    assert scheduler.next_due_at(job, now) is None
    assert len(calls) < 5000, f"下一次触发不应逐分钟扫描，实际调用 cron_matches {len(calls)} 次"


def test_sparse_cron_search_stays_within_small_step_budget(monkeypatch):
    """结构性步数硬断言：合法稀疏表达式在 200 步预算内必须找到下一次到点。"""
    monkeypatch.setattr(scheduler, "_CRON_SEARCH_MAX_STEPS", 200, raising=False)
    job = ScheduledJob(schedule_type="cron", cron_expr="0 0 1 1 *", last_run_at=None)
    assert scheduler.next_due_at(job, datetime(2026, 11, 4, 10, 30, 0)) == datetime(2027, 1, 1, 0, 0)


def test_sparse_cron_leap_day_next_due_is_correct():
    """跳步搜索必须保持语义：2027-03-01 之后的下一个 2 月 29 日是 2028-02-29。"""
    job = ScheduledJob(schedule_type="cron", cron_expr="0 0 29 2 *", last_run_at=None)
    assert scheduler.next_due_at(job, datetime(2027, 3, 1, 0, 0, 0)) == datetime(2028, 2, 29, 0, 0)


def test_cron_search_honours_step_budget(monkeypatch):
    """红/绿锁：搜索步数预算耗尽时按「暂无下一到点」返回，不无限扫描。"""
    monkeypatch.setattr(scheduler, "_CRON_SEARCH_MAX_STEPS", 2, raising=False)
    job = ScheduledJob(schedule_type="cron", cron_expr="0 0 1 1 *", last_run_at=None)
    assert scheduler.next_due_at(job, datetime(2026, 11, 4, 10, 30, 0)) is None


def test_manual_interval_run_before_due_does_not_postpone():
    """RED 回归：未到点的 interval 手动执行曾把 last_run_at 改成 now，整周期顺延。"""
    anchor = datetime.now().replace(microsecond=0) - timedelta(minutes=10)
    job_id = _job(
        name="手动间隔不顺延", action="test.manual.interval", prompt="",
        interval_minutes=60, last_run_at=anchor,
    )

    def handler():
        return {"status": "ok", "message": "手动执行完成"}

    scheduler.register_action("test.manual.interval", handler)
    try:
        result = asyncio.run(scheduler.run_job(job_id, manual=True))
    finally:
        scheduler.JOB_ACTIONS.pop("test.manual.interval", None)

    assert result["status"] == "ok"
    row = _row(job_id)
    assert row.last_status == "ok"
    assert row.last_run_at == anchor
    assert scheduler.next_due_at(row, datetime.now()) >= anchor + timedelta(minutes=60)


def test_manual_interval_run_when_due_consumes_slot():
    """已到点的 interval 手动执行应消费槽位，避免自动调度立刻重复执行。"""
    now = datetime.now().replace(microsecond=0)
    anchor = now - timedelta(minutes=61)
    job_id = _job(
        name="手动间隔消费槽位", action="test.manual.interval.due", prompt="",
        interval_minutes=60, last_run_at=anchor,
    )

    def handler():
        return {"status": "ok", "message": "到点手动完成"}

    scheduler.register_action("test.manual.interval.due", handler)
    try:
        result = asyncio.run(scheduler.run_job(job_id, manual=True))
    finally:
        scheduler.JOB_ACTIONS.pop("test.manual.interval.due", None)

    assert result["status"] == "ok"
    row = _row(job_id)
    assert row.last_run_at is not None and row.last_run_at > anchor
    assert scheduler.job_due(row, datetime.now()) is False
