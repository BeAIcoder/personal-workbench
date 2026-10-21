"""sched-misc 批次 bug 回归测试（B1-B10）。

每个用例对应一条已确认的 bug；修复前全部应失败（RED），修复后转绿（GREEN）。
"""
import json
import sys
import textwrap
from datetime import datetime, timedelta

import pytest

from app.models import Note, ScheduledJob
from app.scheduler import cron_matches, job_due


def _cron_payload(**kw):
    base = {"name": "cron任务", "prompt": "汇总", "schedule_type": "cron", "cron_expr": "0 9 * * *"}
    base.update(kw)
    return base


# ---------------- B1：cron 任务 PUT 永远 422 ----------------

def test_b1_cron_job_update_enabled_not_422(client):
    job = client.post("/api/jobs", json=_cron_payload())
    assert job.status_code == 201, job.text
    jid = job.json()["id"]

    resp = client.put(f"/api/jobs/{jid}", json={"enabled": False})
    assert resp.status_code == 200, resp.text
    assert resp.json()["enabled"] is False

    # 改名也不应 422
    resp = client.put(f"/api/jobs/{jid}", json={"name": "改名后"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["name"] == "改名后"


def test_b1_cron_job_update_still_validates_cron(client):
    job = client.post("/api/jobs", json=_cron_payload()).json()
    # 改成非法 cron → 422
    resp = client.put(f"/api/jobs/{job['id']}", json={"cron_expr": "not a cron"})
    assert resp.status_code == 422
    # 从 interval 改成 cron 但不给表达式 → 422
    job2 = client.post("/api/jobs", json={
        "name": "i", "prompt": "x", "schedule_type": "interval", "interval_minutes": 60,
    }).json()
    resp = client.put(f"/api/jobs/{job2['id']}", json={"schedule_type": "cron"})
    assert resp.status_code == 422
    resp = client.put(f"/api/jobs/{job2['id']}", json={"schedule_type": "cron", "cron_expr": "*/30 * * * *"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["interval_minutes"] is None
    assert resp.json()["daily_at"] is None


# ---------------- B2：cron 表达式逐项校验 + 非法项不毒化字段 ----------------

def test_b2_create_rejects_invalid_cron_items(client):
    # 分钟段含越界项 60 → 整个表达式非法
    resp = client.post("/api/jobs", json=_cron_payload(cron_expr="0,60 * * * *"))
    assert resp.status_code == 422
    for bad in ("0 25 * * *", "0 9 * 13 *", "0 9 * * 8", "a b c d e", "*/0 * * * *", "0 9", ""):
        resp = client.post("/api/jobs", json=_cron_payload(cron_expr=bad))
        assert resp.status_code == 422, f"应拒绝：{bad!r}"
    # 合法表达式仍可用
    for ok in ("0,30 * * * *", "*/15 9-17 * * 1-5", "0 9 1,15 * *", "0 9 * * 0"):
        resp = client.post("/api/jobs", json=_cron_payload(name=f"ok-{ok}", cron_expr=ok))
        assert resp.status_code == 201, f"应接受：{ok!r} → {resp.text}"


def test_b2_cron_field_match_skips_invalid_items():
    from app.scheduler import _cron_field_match

    # 非法项不再毒化整个字段：合法项照常参与匹配
    assert _cron_field_match("0,60", 0, 0, 59) is True
    assert _cron_field_match("0,60", 30, 0, 59) is False
    assert _cron_field_match("bad,15", 15, 0, 59) is True
    # 全部项非法 → 不匹配
    assert _cron_field_match("60", 0, 0, 59) is False
    assert _cron_field_match("*/0", 0, 0, 59) is False
    assert _cron_field_match("9-5", 9, 0, 23) is False
    # 既有合法行为不退化
    assert _cron_field_match("*/15", 30, 0, 59) is True
    assert _cron_field_match("9-17", 17, 0, 23) is True
    assert _cron_field_match("*", 5, 0, 59) is True


# ---------------- B3：手动「立即运行」不吞掉 daily/cron 的到点触发 ----------------

def _mock_llm_ok(scheduler):
    async def fake_chat(**kwargs):
        return {"ok": True, "reply": "执行完毕", "agent": {"name": "a", "label": "甲"}}

    return fake_chat


def test_b3_manual_run_now_does_not_touch_last_run_for_daily(client, monkeypatch):
    from app import scheduler

    monkeypatch.setattr(scheduler, "llm_status", lambda: {"configured": True})
    monkeypatch.setattr(scheduler.orchestrator, "chat", _mock_llm_ok(scheduler))

    job = client.post("/api/jobs", json={
        "name": "每日简报", "prompt": "汇总", "schedule_type": "daily", "daily_at": "09:00",
    }).json()
    resp = client.post(f"/api/jobs/{job['id']}/run")
    assert resp.status_code == 200 and resp.json()["status"] == "ok"

    body = client.get("/api/jobs").json()[0]
    assert body["last_status"] == "ok"          # 状态仍记录
    assert body["last_note_id"]                 # 笔记仍落库
    assert body["recent_runs"]                  # 运行记录仍落库
    assert body["last_run_at"] is None          # 关键：不占用到点触发

    # 08:30 手动运行后，当天 09:00 仍 due
    from app.database import SessionLocal

    with SessionLocal() as db:
        row = db.get(ScheduledJob, job["id"])
        due_at_9 = datetime.now().replace(hour=9, minute=0, second=0, microsecond=0)
        assert job_due(row, due_at_9) is True


def test_b3_manual_run_now_keeps_interval_postpone(client, monkeypatch):
    """interval 任务维持现状：手动运行照常顺延（更新 last_run_at）。"""
    from app import scheduler

    monkeypatch.setattr(scheduler, "llm_status", lambda: {"configured": True})
    monkeypatch.setattr(scheduler.orchestrator, "chat", _mock_llm_ok(scheduler))

    job = client.post("/api/jobs", json={
        "name": "间隔任务", "prompt": "汇总", "schedule_type": "interval", "interval_minutes": 60,
    }).json()
    resp = client.post(f"/api/jobs/{job['id']}/run")
    assert resp.json()["status"] == "ok"
    body = client.get("/api/jobs").json()[0]
    assert body["last_run_at"] is not None


def test_b3_scheduled_run_still_updates_last_run_for_daily(client, monkeypatch):
    """调度循环触发（非手动）的 daily 任务照常更新 last_run_at。"""
    import asyncio

    from app import scheduler

    monkeypatch.setattr(scheduler, "llm_status", lambda: {"configured": True})
    monkeypatch.setattr(scheduler.orchestrator, "chat", _mock_llm_ok(scheduler))

    job = client.post("/api/jobs", json={
        "name": "每日简报2", "prompt": "汇总", "schedule_type": "daily", "daily_at": "09:00",
    }).json()
    result = asyncio.run(scheduler.run_job(job["id"]))
    assert result["status"] == "ok"
    body = client.get("/api/jobs").json()[0]
    assert body["last_run_at"] is not None


# ---------------- B4：cron 错过补偿（有界回溯 24h） ----------------

def test_b4_cron_missed_minute_caught_up():
    # 2026-10-19 是周一；now=11:00，今天 09:00 的触发被休眠错过
    now = datetime(2026, 10, 19, 11, 0, 0)
    job = ScheduledJob(schedule_type="cron", cron_expr="0 9 * * *",
                       last_run_at=datetime(2026, 10, 18, 9, 0, 0))
    assert job_due(job, now) is True
    # 今天 09:00 已执行过 → 不重复补
    job.last_run_at = datetime(2026, 10, 19, 9, 0, 0)
    assert job_due(job, now) is False


def test_b4_cron_catchup_bounded_by_24h():
    now = datetime(2026, 10, 19, 11, 0, 0)  # 周一
    # 上周五 09:00（约 74h 前）超出回溯窗口 → 不补
    job = ScheduledJob(schedule_type="cron", cron_expr="0 9 * * 5", last_run_at=None)
    assert job_due(job, now) is False
    # 周一 09:00 的 cron 在窗口内 → 补
    job2 = ScheduledJob(schedule_type="cron", cron_expr="0 9 * * 1",
                        last_run_at=datetime(2026, 10, 18, 0, 0, 0))
    assert job_due(job2, now) is True


def test_b4_cron_normal_path_unaffected():
    now = datetime(2026, 10, 19, 9, 0, 0)
    job = ScheduledJob(schedule_type="cron", cron_expr="0 9 * * *", last_run_at=None)
    assert job_due(job, now) is True
    job.last_run_at = datetime(2026, 10, 19, 9, 0, 30)  # 同一分钟已跑
    assert job_due(job, now) is False
    # 最近的匹配分钟已执行过 → 不重复补（09:30 已跑，(09:30, 11:00] 无匹配）
    job2 = ScheduledJob(schedule_type="cron", cron_expr="30 9 * * *",
                        last_run_at=datetime(2026, 10, 19, 9, 30, 0))
    assert job_due(job2, datetime(2026, 10, 19, 11, 0, 0)) is False


# ---------------- B5：删笔记时清理 ScheduledJob.last_note_id 悬挂引用 ----------------

def test_b5_delete_note_clears_job_last_note_id(client):
    from app.database import SessionLocal

    note = client.post("/api/notes", json={"title": "运行结果", "content": "内容"}).json()
    job = client.post("/api/jobs", json={
        "name": "引用笔记的任务", "prompt": "x", "schedule_type": "interval", "interval_minutes": 60,
    }).json()
    with SessionLocal() as db:
        row = db.get(ScheduledJob, job["id"])
        row.last_note_id = note["id"]
        db.commit()

    resp = client.delete(f"/api/notes/{note['id']}")
    assert resp.status_code == 204

    body = client.get("/api/jobs").json()[0]
    assert body["last_note_id"] is None

    # 不影响其他任务的引用
    note2 = client.post("/api/notes", json={"title": "另一条", "content": "x"}).json()
    job2 = client.post("/api/jobs", json={
        "name": "任务2", "prompt": "x", "schedule_type": "interval", "interval_minutes": 60,
    }).json()
    with SessionLocal() as db:
        db.get(ScheduledJob, job2["id"]).last_note_id = note2["id"]
        db.commit()
    client.delete(f"/api/notes/{note['id'] + 999}")  # 不存在，404 也不误清
    assert client.get("/api/jobs").json()[1]["last_note_id"] == note2["id"]


# ---------------- B6：更新 MCP 服务器先停掉旧进程 ----------------

_FAKE_MCP_SERVER = textwrap.dedent('''
    import sys, json
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    def send(obj):
        sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\\n")
        sys.stdout.flush()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        msg = json.loads(line)
        mid, method = msg.get("id"), msg.get("method")
        if method == "initialize":
            send({"jsonrpc": "2.0", "id": mid, "result": {
                "protocolVersion": "2024-11-05", "capabilities": {},
                "serverInfo": {"name": "fake", "version": "0.1"}}})
        elif method == "tools/list":
            send({"jsonrpc": "2.0", "id": mid, "result": {"tools": [{
                "name": "echo", "description": "回声工具",
                "inputSchema": {"type": "object",
                                "properties": {"text": {"type": "string", "description": "文本"}},
                                "required": ["text"]}}]}})
        elif isinstance(method, str) and method.startswith("notifications/"):
            pass
''')

_HANG_MCP_SERVER = textwrap.dedent('''
    import sys, json
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    def send(obj):
        sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\\n")
        sys.stdout.flush()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        msg = json.loads(line)
        mid, method = msg.get("id"), msg.get("method")
        if method == "initialize":
            send({"jsonrpc": "2.0", "id": mid, "result": {
                "protocolVersion": "2024-11-05", "capabilities": {},
                "serverInfo": {"name": "silent", "version": "0.1"}}})
        elif method == "tools/list":
            send({"jsonrpc": "2.0", "id": mid, "result": {"tools": [{
                "name": "hang", "description": "收到调用但不回行的工具",
                "inputSchema": {"type": "object", "properties": {}}}]}})
        elif method == "tools/call":
            pass  # 模拟服务器挂起：进程活着但永不回行
''')


def test_b6_update_server_stops_old_process(client, tmp_path):
    from app.agents import mcp
    from app.agents.tools import TOOL_SPECS

    script = tmp_path / "fake_mcp_server.py"
    script.write_text(_FAKE_MCP_SERVER, encoding="utf-8")
    created = client.post("/api/mcp/servers", json={
        "name": "updtest", "command": sys.executable, "args": [str(script)], "enabled": True,
    })
    assert created.status_code == 201, created.text
    sid = created.json()["id"]
    conn = client.post(f"/api/mcp/servers/{sid}/connect")
    assert conn.status_code == 200, conn.text

    old = mcp._servers.get("updtest")
    assert old is not None and old.is_alive()
    old_proc = old.proc

    try:
        resp = client.put(f"/api/mcp/servers/{sid}", json={"env": {"K": "V"}})
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "stopped"
        # 旧子进程被终止，_servers 无残留
        assert not old.is_alive()
        assert old_proc.poll() is not None
        assert "updtest" not in mcp._servers
    finally:
        client.delete(f"/api/mcp/servers/{sid}")
        TOOL_SPECS.pop("mcp_updtest__echo", None)
        with mcp._manager_lock:
            leftover = mcp._servers.pop("updtest", None)
        if leftover:
            leftover.stop()


# ---------------- B7：MCP 读超时终止进程，释放阻塞读线程 ----------------

def test_b7_read_timeout_terminates_process(tmp_path, monkeypatch):
    import time

    from app.agents import mcp

    script = tmp_path / "hang_mcp_server.py"
    script.write_text(_HANG_MCP_SERVER, encoding="utf-8")
    monkeypatch.setattr(mcp, "CALL_TIMEOUT", 1)  # 缩短注入；默认值在调用时解析

    server = mcp.McpStdioServer(name="hangtest", command=sys.executable, args=[str(script)])
    server.start()
    proc = server.proc
    try:
        t0 = time.monotonic()
        with pytest.raises(mcp.McpError, match="超时"):
            server.call_tool("hang", {})
        assert time.monotonic() - t0 < 10
        # 超时后进程被终止：管道关闭，阻塞在 readline 的读线程随之退出
        assert not server.is_alive()
        assert proc.poll() is not None
    finally:
        server.stop()
    assert not server.is_alive()


# ---------------- B8：memory.recall 空 token 查询直接返回空 ----------------

def test_b8_recall_blank_query_returns_empty(client):
    from app.agents import memory
    from app.database import SessionLocal
    from app.models import Memory

    client.post("/api/knowledge/memories", json={"content": "报销票据按月份归档，发票抬头保持一致。"})

    # 纯标点查询：空 token → 返回空，且不白加 hit_count
    assert memory.recall("！！！") == []
    assert memory.recall("  ") == []
    assert memory.recall("") == []
    with SessionLocal() as db:
        row = db.query(Memory).first()
        assert row is not None
        assert (row.hit_count or 0) == 0
        assert row.last_used_at is None

    # 正常查询不受影响
    hits = memory.recall("发票抬头")
    assert hits and "发票" in hits[0]["content"]
    with SessionLocal() as db:
        assert db.query(Memory).first().hit_count == 1


# ---------------- B9：RAG 中文数字混合词 / 单字查询 ----------------

def test_b9_rag_mixed_alnum_han_and_single_char(client):
    from app.agents import rag
    from app.database import SessionLocal

    with SessionLocal() as db:
        note = Note(title="报税提醒", content="请于10月完成增值税报税，逾期将产生滞纳金。", tags=[])
        db.add(note)
        db.commit()
        db.refresh(note)
        nid = note.id
    rag.rebuild_index()

    hits = rag.search("10月", top_k=5)
    assert hits and hits[0]["note_id"] == nid, f"search('10月') 应命中：{hits}"

    hits = rag.search("税", top_k=5)
    assert hits and hits[0]["note_id"] == nid, f"search('税') 应命中：{hits}"

    # 原有短语查询不退化
    for q in ("报税", "增值税", "滞纳金"):
        hits = rag.search(q, top_k=5)
        assert hits and hits[0]["note_id"] == nid, f"search({q!r}) 不应退化：{hits}"

    # 无关查询仍返回空
    assert rag.search("量子引力波") == []


# ---------------- B10：qwenpaw_compat 重复加载不产生副作用累积 ----------------

def test_b10_load_qwenpaw_plugin_idempotent(tmp_path):
    from app.agents import qwenpaw_compat as qc

    marker = tmp_path / "exec_count.txt"
    plugin_dir = tmp_path / "demo_plugin"
    plugin_dir.mkdir()
    (plugin_dir / "plugin.json").write_text(json.dumps(
        {"id": "demo", "name": "Demo", "entry": {"backend": "main.py"}},
        ensure_ascii=False), encoding="utf-8")
    (plugin_dir / "main.py").write_text(textwrap.dedent(f'''
        import pathlib
        _marker = pathlib.Path(r"{marker.as_posix()}")
        _n = int(_marker.read_text()) if _marker.exists() else 0
        _marker.write_text(str(_n + 1))  # 模块级副作用：exec_module 每跑一次 +1

        def _ping():
            return "pong"

        class _Plugin:
            def register(self, api):
                api.register_tool("ping", _ping, "测试工具")

        plugin = _Plugin()
    '''), encoding="utf-8")

    path_str = str(plugin_dir)
    before = len(sys.path)
    try:
        r1 = qc.load_qwenpaw_plugin(path_str)
        mid = len(sys.path)
        r2 = qc.load_qwenpaw_plugin(path_str)
        # 第二次构建：sys.path 不增长，模块不重复 exec
        assert len(sys.path) == mid
        assert mid <= before + 1
        assert marker.read_text() == "1"
        assert r1["id"] == "demo" and r1["tools"][0]["name"] == "ping"
        assert r2["id"] == "demo" and r2["tools"][0]["name"] == "ping"
    finally:
        while path_str in sys.path:
            sys.path.remove(path_str)
        getattr(qc, "_loaded_plugins", {}).pop(str(plugin_dir.resolve()), None)

