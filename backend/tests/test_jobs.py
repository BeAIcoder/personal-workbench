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
