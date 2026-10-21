"""定时任务（定时 Agent）接口与调度逻辑测试。"""
from datetime import datetime, timedelta

from app.models import Note, ScheduledJob
from app.scheduler import job_due


def _payload(**kw):
    base = {"name": "晨间简报", "prompt": "汇总今日任务与日程", "schedule_type": "interval", "interval_minutes": 60}
    base.update(kw)
    return base


def test_job_due_interval():
    now = datetime(2026, 9, 22, 9, 0, 0)
    job = ScheduledJob(schedule_type="interval", interval_minutes=60, last_run_at=None)
    assert job_due(job, now) is True
    job.last_run_at = now - timedelta(minutes=59)
    assert job_due(job, now) is False
    job.last_run_at = now - timedelta(minutes=61)
    assert job_due(job, now) is True


def test_job_due_daily():
    now = datetime(2026, 9, 22, 9, 0, 0)
    job = ScheduledJob(schedule_type="daily", daily_at="08:30", last_run_at=None)
    assert job_due(job, now) is True
    job.last_run_at = datetime(2026, 9, 22, 8, 31, 0)
    assert job_due(job, now) is False  # 今天已跑过
    job.last_run_at = datetime(2026, 9, 21, 8, 31, 0)
    assert job_due(job, now) is True   # 新的一天且已过点
    early = datetime(2026, 9, 22, 7, 0, 0)
    assert job_due(job, early) is False
    bad = ScheduledJob(schedule_type="daily", daily_at="99:99", last_run_at=None)
    assert job_due(bad, now) is False


def test_jobs_crud(client):
    resp = client.post("/api/jobs", json=_payload())
    assert resp.status_code == 201
    job = resp.json()
    assert job["name"] == "晨间简报"
    assert job["last_status"] == ""

    resp = client.get("/api/jobs")
    assert len(resp.json()) == 1

    resp = client.put(f"/api/jobs/{job['id']}", json={"enabled": False, "interval_minutes": 120})
    assert resp.status_code == 200
    assert resp.json()["enabled"] is False
    assert resp.json()["interval_minutes"] == 120

    resp = client.delete(f"/api/jobs/{job['id']}")
    assert resp.status_code == 204
    assert client.get("/api/jobs").json() == []


def test_jobs_validation(client):
    # interval 缺间隔
    resp = client.post("/api/jobs", json={"name": "x", "prompt": "y", "schedule_type": "interval"})
    assert resp.status_code == 422
    # daily 缺时间
    resp = client.post("/api/jobs", json={"name": "x", "prompt": "y", "schedule_type": "daily"})
    assert resp.status_code == 422
    # daily 时间格式错
    resp = client.post("/api/jobs", json={"name": "x", "prompt": "y", "schedule_type": "daily", "daily_at": "9:00"})
    assert resp.status_code == 422
    # 间隔越界
    resp = client.post("/api/jobs", json=_payload(interval_minutes=2))
    assert resp.status_code == 422
    # 改 daily 后没给时间
    job = client.post("/api/jobs", json=_payload()).json()
    resp = client.put(f"/api/jobs/{job['id']}", json={"schedule_type": "daily"})
    assert resp.status_code == 422


def test_job_run_now_persists_note(client, monkeypatch):
    from app import scheduler

    async def fake_chat(**kwargs):
        return {"ok": True, "reply": "今日 3 项待办，重点是发票认证。", "agent": {"name": "realestate", "label": "商业地产管家"}}

    monkeypatch.setattr(scheduler, "llm_status", lambda: {"configured": True})
    monkeypatch.setattr(scheduler.orchestrator, "chat", fake_chat)

    job = client.post("/api/jobs", json=_payload(name="E2E定时任务")).json()
    resp = client.post(f"/api/jobs/{job['id']}/run")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"

    body = client.get("/api/jobs").json()[0]
    assert body["last_status"] == "ok"
    assert body["last_note_id"]
    assert body["recent_runs"][0]["summary"].startswith("今日")

    note = client.get(f"/api/notes/{body['last_note_id']}").json()
    assert note["title"].startswith("【定时任务】E2E定时任务")
    assert "定时任务" in note["tags"]


def test_job_run_skipped_without_llm(client, monkeypatch):
    from app import scheduler

    monkeypatch.setattr(scheduler, "llm_status", lambda: {"configured": False})
    job = client.post("/api/jobs", json=_payload()).json()
    resp = client.post(f"/api/jobs/{job['id']}/run")
    assert resp.status_code == 200
    assert resp.json()["status"] == "skipped"
    body = client.get("/api/jobs").json()[0]
    assert body["last_status"] == "skipped"


def test_job_not_found(client):
    assert client.post("/api/jobs/999/run").status_code == 404
    assert client.delete("/api/jobs/999").status_code == 404


# ---------------- v1.10.0：cron 与模块动作 ----------------

from app.scheduler import JOB_ACTIONS, cron_matches, list_actions, register_action  # noqa: E402


def test_cron_matches_basic():
    # 2026-10-12 是周一；用固定时间点验证各字段
    mon_9am = datetime(2026, 10, 12, 9, 0, 0)
    assert cron_matches("0 9 * * *", mon_9am) is True
    assert cron_matches("30 9 * * *", mon_9am) is False
    assert cron_matches("0 9 * * 1", mon_9am) is True      # 周一
    assert cron_matches("0 9 * * 2", mon_9am) is False     # 非周二
    assert cron_matches("0 9 * * 0", mon_9am) is False     # 非周日
    assert cron_matches("*/15 * * * *", datetime(2026, 10, 12, 9, 30, 0)) is True
    assert cron_matches("*/15 * * * *", datetime(2026, 10, 12, 9, 31, 0)) is False
    assert cron_matches("0 9-17 * * *", datetime(2026, 10, 12, 17, 0, 0)) is True
    assert cron_matches("0 9-17 * * *", datetime(2026, 10, 12, 18, 0, 0)) is False
    assert cron_matches("0 0 1 * *", datetime(2026, 11, 1, 0, 0, 0)) is True
    assert cron_matches("0 0 1 * *", datetime(2026, 11, 2, 0, 0, 0)) is False
    # 周日：python weekday=6 → cron 0 与 7 都命中
    sun = datetime(2026, 10, 18, 8, 0, 0)
    assert cron_matches("0 8 * * 0", sun) is True
    assert cron_matches("0 8 * * 7", sun) is True
    # 非法表达式
    assert cron_matches("bad", mon_9am) is False
    assert cron_matches("60 24 * * *", mon_9am) is False


def test_job_due_cron():
    now = datetime(2026, 10, 12, 9, 0, 0)
    job = ScheduledJob(schedule_type="cron", cron_expr="0 9 * * *", last_run_at=None)
    assert job_due(job, now) is True
    job.last_run_at = datetime(2026, 10, 12, 9, 0, 30)  # 同一分钟已跑
    assert job_due(job, now) is False
    job.last_run_at = datetime(2026, 10, 11, 9, 0, 0)
    assert job_due(job, now) is True


def test_job_due_cron_does_not_backfill_before_creation():
    """新建 cron 任务不补跑创建之前的匹配点（否则 10:30 新建会立刻把 10:15 跑一遍）。"""
    job = ScheduledJob(
        schedule_type="cron", cron_expr="15 * * * *", last_run_at=None,
        created_at=datetime(2026, 10, 12, 10, 30, 40),
    )
    # 10:35 检查：窗口内唯一匹配 10:15 早于创建时间 → 不 due
    assert job_due(job, datetime(2026, 10, 12, 10, 35, 0)) is False
    # 创建后错过 12:15：14:00 检查仍补跑
    assert job_due(job, datetime(2026, 10, 12, 14, 0, 0)) is True


def test_create_job_rejects_unknown_schedule_type(client):
    """未知 schedule_type 必须 422；否则运行时会落进 interval 兜底分支被误执行。"""
    resp = client.post("/api/jobs", json={"name": "未知类型", "prompt": "x", "schedule_type": "weekly"})
    assert resp.status_code == 422, resp.text


def test_create_job_normalizes_cross_type_fields(client):
    """创建时与更新一致地清理跨类型残留字段（daily/cron 不保留 interval_minutes）。"""
    daily = client.post("/api/jobs", json={
        "name": "每日归一化", "prompt": "x", "schedule_type": "daily",
        "daily_at": "08:30", "interval_minutes": 60,
    })
    assert daily.status_code == 201, daily.text
    assert daily.json()["interval_minutes"] is None
    cron = client.post("/api/jobs", json={
        "name": "Cron归一化", "prompt": "x", "schedule_type": "cron",
        "cron_expr": "0 9 1 1 *", "interval_minutes": 60, "daily_at": "08:30",
    })
    assert cron.status_code == 201, cron.text
    assert cron.json()["interval_minutes"] is None
    assert cron.json()["daily_at"] is None


def test_action_registry_and_actions_endpoint(client):
    # 模块加载时 finance_board 已注册其动作
    assert "finance_board.run" in JOB_ACTIONS
    resp = client.get("/api/jobs/actions")
    assert resp.status_code == 200
    assert "finance_board.run" in resp.json()["actions"]

    # 注册/覆盖一个假动作用于任务执行测试
    calls = []

    def fake():
        calls.append(1)
        return {"status": "ok", "message": "假动作完成"}

    register_action("test.fake", fake)
    try:
        assert "test.fake" in list_actions()
        job = client.post("/api/jobs", json={
            "name": "假动作任务", "prompt": "", "action": "test.fake",
            "schedule_type": "interval", "interval_minutes": 60,
        })
        assert job.status_code == 201, job.text
        jid = job.json()["id"]

        import asyncio
        result = asyncio.run(_run(jid))
        assert result["status"] == "ok" and "假动作完成" in result["summary"]
        assert calls == [1]

        # 执行记录落库（recent_runs），不写笔记
        detail = client.get("/api/jobs").json()
        row = next(j for j in detail if j["id"] == jid)
        assert row["recent_runs"][0]["status"] == "ok"
        assert row["last_status"] == "ok"
    finally:
        JOB_ACTIONS.pop("test.fake", None)


def test_action_job_unregistered_action(client):
    job = client.post("/api/jobs", json={
        "name": "未注册动作", "prompt": "", "action": "no.such.action",
        "schedule_type": "interval", "interval_minutes": 60,
    })
    assert job.status_code == 201
    import asyncio
    result = asyncio.run(_run(job.json()["id"]))
    assert result["status"] == "error" and "未注册" in result["summary"]


def test_cron_job_validation(client):
    bad = client.post("/api/jobs", json={
        "name": "坏 cron", "prompt": "x", "schedule_type": "cron", "cron_expr": "0 9 * *",
    })
    assert bad.status_code == 422
    ok = client.post("/api/jobs", json={
        "name": "好 cron", "prompt": "", "action": "test.placeholder",
        "schedule_type": "cron", "cron_expr": "0 9 * * 1",
    })
    # action 未注册不影响创建（到点才报错）
    assert ok.status_code == 201, ok.text


async def _run(job_id):
    from app.scheduler import run_job
    return await run_job(job_id)
