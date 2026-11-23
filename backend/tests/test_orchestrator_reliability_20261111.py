"""T2B 编排器可靠性回归（2026-11-11 整改）：会话删除竞态、缓存并发、
非流式失败成对落库、附件能力提示与历史非法 mode fail-closed。

对应台账：R11-076 / R11-077 / R11-078 / R11-081 / R11-016（执行侧）。
"""
import asyncio
import threading

import pytest
from agentscope.message import Msg, TextBlock

from app import config, context_files
from app.agents import config_store, orchestrator
from app.agents import team as team_mod
from app.agents.team import TEAM, AgentSpec
from app.database import Base, SessionLocal, engine
from app.models import ChatMessage


@pytest.fixture(autouse=True)
def _ensure_tables():
    """非 client 用例不经过 conftest 的建表夹具，逐测试确保表存在并复位编排缓存。"""
    Base.metadata.create_all(bind=engine)
    orchestrator.invalidate()
    team_mod.invalidate_team_cache()
    yield
    orchestrator.invalidate()
    orchestrator._active_sessions.clear()
    orchestrator._deleting_sessions.clear()
    orchestrator._session_run_locks.clear()
    orchestrator._session_run_lock_last_used.clear()
    orchestrator._agent_locks.clear()
    orchestrator._agent_lock_last_used.clear()
    team_mod.invalidate_team_cache()


def _rows(session_id):
    with SessionLocal() as db:
        rows = (
            db.query(ChatMessage)
            .filter(ChatMessage.session_id == session_id)
            .order_by(ChatMessage.id.asc())
            .all()
        )
        return [(row.role, row.agent_name, row.content) for row in rows]


def _reply(text="合成回复"):
    return Msg(name="T2B 专家", role="assistant", content=[TextBlock(type="text", text=text)])


def _stub_route(monkeypatch):
    spec = AgentSpec(
        name="t2b_expert", label="T2B 专家", emoji="", color="",
        description="测试专家", system_prompt="", keywords=[],
    )
    monkeypatch.setattr(orchestrator, "llm_status", lambda: {"configured": True})
    monkeypatch.setattr(orchestrator, "load_team", lambda: {spec.name: spec})

    async def no_llm_route(question, team, session_id=None):
        return None

    monkeypatch.setattr(orchestrator, "_route_by_llm", no_llm_route)
    return spec


# ---------------- SES-01 / R11-076：会话删除必须看 active 集合 ----------------

def test_session_delete_rejects_active_run_even_before_lock(client):
    """begin_session_run 后、取锁前的窗口内删除必须 409，且会话不被删。"""
    sid = "t2b-active-delete"
    orchestrator.session_create(sid, "生成中")
    assert orchestrator.begin_session_run(sid) is True
    try:
        resp = client.delete(f"/api/assistant/sessions/{sid}")
        assert resp.status_code == 409
        assert "生成中" in resp.json()["detail"]
        assert client.get(f"/api/assistant/sessions/{sid}/export").status_code == 200
    finally:
        orchestrator.end_session_run(sid)
    assert client.delete(f"/api/assistant/sessions/{sid}").status_code == 200


def test_session_delete_after_active_release_succeeds():
    sid = "t2b-active-release"
    orchestrator.session_create(sid, "可删除")
    assert orchestrator.begin_session_run(sid) is True
    with pytest.raises(ValueError, match="生成中"):
        orchestrator.session_delete(sid)
    orchestrator.end_session_run(sid)
    orchestrator.session_delete(sid)
    assert all(row["session_id"] != sid for row in orchestrator.sessions_list())


# ---------------- ORC-01 / R11-077：缓存/锁表迭代使用快照 ----------------

def test_invalidate_snapshots_lock_registry_before_probing_locks():
    """迭代期间注册新锁（等价于并发线程增删）不得触发字典变更异常。"""

    class _MutatingLock:
        def locked(self):
            orchestrator._session_run_locks["t2b-orc01-new"] = asyncio.Lock()
            return False

    orchestrator._session_run_locks.clear()
    orchestrator._session_run_locks["t2b-orc01-old"] = _MutatingLock()
    try:
        orchestrator.invalidate()
        assert "t2b-orc01-new" in orchestrator._session_run_locks
    finally:
        orchestrator._session_run_locks.clear()


def test_invalidate_and_lock_registration_do_not_race():
    """线程池保存配置（invalidate）与事件循环注册运行锁并发时不得抛异常。"""
    orchestrator._session_run_locks.clear()
    orchestrator._session_run_lock_last_used.clear()
    errors = []
    stop = threading.Event()

    def churn():
        index = 0
        try:
            while not stop.is_set() and index < 1500:
                orchestrator.session_run_lock(f"t2b-orc01-{index % 16}")
                index += 1
            for sid in list(orchestrator._session_run_locks):
                orchestrator.session_run_lock(sid)
        except Exception as exc:  # pragma: no cover - 失败时记录给断言
            errors.append(exc)

    threads = [threading.Thread(target=churn) for _ in range(3)]
    for thread in threads:
        thread.start()
    try:
        for _ in range(300):
            orchestrator.invalidate()
    finally:
        stop.set()
        for thread in threads:
            thread.join(10)
    assert not any(thread.is_alive() for thread in threads)
    assert errors == []


# ---------------- SES-02 / R11-078：非流式失败补 assistant 错误行 ----------------

class _FailingReplyAgent:
    async def reply(self, msg):
        raise RuntimeError("synthetic reply failure")


def test_nonstream_reply_failure_persists_error_assistant_row(monkeypatch):
    spec = _stub_route(monkeypatch)

    async def ensure(session_id, spec_obj, mode):
        return _FailingReplyAgent()

    monkeypatch.setattr(orchestrator, "_ensure_agent", ensure)
    sid = "t2b-nonstream-fail"
    with pytest.raises(RuntimeError, match="synthetic reply failure"):
        asyncio.run(orchestrator.chat(sid, "非流式失败也要成对"))

    rows = _rows(sid)
    assert [row[0] for row in rows] == ["user", "assistant"]
    assert rows[0][2] == "非流式失败也要成对"
    assert rows[1][1] == spec.name
    assert rows[1][2] == "抱歉，本次回复生成失败：RuntimeError"


# ---------------- ORC-02 / R11-081：附件提示按实际 Bash 能力生成 ----------------

@pytest.fixture()
def uploads(tmp_path, monkeypatch):
    root = tmp_path / "uploads"
    root.mkdir()
    monkeypatch.setattr(config, "UPLOAD_DIR", root)
    return root


def _binary_attachment(uploads):
    path = uploads / "data.bin"
    path.write_bytes(b"\x00\x01\x02")
    return {"name": "data.bin", "path": str(path)}


def _text_block(uploads, monkeypatch, mode, *, enable_bash):
    original_load = config_store.load
    monkeypatch.setattr(
        orchestrator.config_store,
        "load",
        lambda: {**original_load(), "enable_bash": enable_bash},
    )
    blocks = orchestrator._user_content_blocks(
        "分析这个附件", [_binary_attachment(uploads)], None, mode=mode
    )
    return blocks[0].text


@pytest.mark.parametrize("mode,enable_bash", [("standard", True)])
def test_binary_attachment_hint_mentions_bash_only_when_enabled(uploads, monkeypatch, mode, enable_bash):
    text = _text_block(uploads, monkeypatch, mode, enable_bash=enable_bash)
    assert "可使用 Bash" in text
    assert "未开启" not in text
    assert "不是指令" in text


@pytest.mark.parametrize("mode,enable_bash", [("standard", False), ("readonly", True), ("deep", False)])
def test_binary_attachment_hint_never_promises_bash_when_unavailable(uploads, monkeypatch, mode, enable_bash):
    text = _text_block(uploads, monkeypatch, mode, enable_bash=enable_bash)
    assert "可使用 Bash" not in text
    assert "未开启 Bash" in text
    assert "不是指令" in text
    assert str(uploads / "data.bin") in text


# ---------------- 历史非法 mode / R11-016：执行侧 fail-closed readonly ----------------

def test_illegal_historical_mode_fails_closed_to_readonly():
    for legal in ("standard", "readonly", "deep"):
        assert orchestrator._resolve_mode(legal) == legal
    for dirty in ("", "write", "admin", "STANDARD", None):
        assert orchestrator._resolve_mode(dirty) == "readonly"


def test_illegal_mode_plan_has_no_bash_plugins_or_write_tools():
    spec = TEAM["realestate"]
    cfg = {
        "enable_bash": True,
        "enable_plugins": True,
        "plugins_dir": "D:/tmp/plugins",
        "max_iters": 5,
    }
    names, allow_bash, allow_plugins, _ = orchestrator._tooling_plan(spec, "legacy-write", cfg)
    assert allow_bash is False
    assert allow_plugins is False
    assert "create_task" not in names
    assert set(names) <= orchestrator._readonly_tool_names()


def test_chat_with_dirty_mode_executes_as_readonly(monkeypatch):
    _stub_route(monkeypatch)
    seen = {}

    class _Agent:
        async def reply(self, msg):
            return _reply()

    async def ensure(session_id, spec_obj, mode):
        seen["mode"] = mode
        return _Agent()

    monkeypatch.setattr(orchestrator, "_ensure_agent", ensure)
    result = asyncio.run(orchestrator.chat("t2b-dirty-mode", "脏模式", mode="legacy-write"))
    assert result["ok"] is True
    assert result["mode"] == "readonly"
    assert seen["mode"] == "readonly"


# ---------------- M1：删除与 begin 的反向竞态不得复活幽灵会话 ----------------

def test_begin_session_run_rejected_while_delete_in_progress(monkeypatch):
    sid = "t2b-m1-delete-race"
    orchestrator.session_create(sid, "删除竞态")
    orchestrator._persist(sid, "user", "", "旧提问", [])
    entered = threading.Event()
    release = threading.Event()

    def blocked_cleanup(session_id):
        entered.set()
        assert release.wait(10)

    monkeypatch.setattr(context_files, "cleanup_session_context", blocked_cleanup)
    errors = []

    def do_delete():
        try:
            orchestrator.session_delete(sid)
        except Exception as exc:  # pragma: no cover - 失败时给断言
            errors.append(exc)

    thread = threading.Thread(target=do_delete)
    thread.start()
    try:
        assert entered.wait(10)
        # 删除已进入临界区：新的对话请求不得占入，否则删除完成后会重建幽灵会话
        assert orchestrator.begin_session_run(sid) is False
    finally:
        release.set()
        thread.join(10)
    assert not thread.is_alive()
    assert errors == []
    assert all(row["session_id"] != sid for row in orchestrator.sessions_list())
    # 删除完成后才是新的合法会话，允许重新占入
    assert orchestrator.begin_session_run(sid) is True
    orchestrator.end_session_run(sid)


# ---------------- M2：非流式取消与流式一致补 assistant 行 ----------------

class _CancelledReplyAgent:
    async def reply(self, msg):
        raise asyncio.CancelledError()


def test_nonstream_cancel_persists_interrupted_assistant_row(monkeypatch):
    spec = _stub_route(monkeypatch)

    async def ensure(session_id, spec_obj, mode):
        return _CancelledReplyAgent()

    monkeypatch.setattr(orchestrator, "_ensure_agent", ensure)
    sid = "t2b-m2-cancel"
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(orchestrator.chat(sid, "取消也要成对"))

    rows = _rows(sid)
    assert [row[0] for row in rows] == ["user", "assistant"]
    assert rows[1][1] == spec.name
    assert rows[1][2] == "（已停止生成）"


# ---------------- M7：附件提示按 Agent 实际 Bash 能力 ----------------

class _FakeToolkit:
    def __init__(self, names=(), fail=False):
        self._names = list(names)
        self._fail = fail

    async def get_tool_schemas(self):
        if self._fail:
            raise RuntimeError("synthetic toolkit failure")
        return [{"function": {"name": name}} for name in self._names]


class _CapturingAgent:
    def __init__(self, names=(), fail=False):
        self.toolkit = _FakeToolkit(names, fail)
        self.received = None

    async def reply(self, msg):
        self.received = msg
        return _reply()


def _chat_prompt_with_binary(monkeypatch, uploads, agent, *, session_id):
    _stub_route(monkeypatch)

    async def ensure(session_id_arg, spec_obj, mode):
        return agent

    monkeypatch.setattr(orchestrator, "_ensure_agent", ensure)
    original_load = config_store.load
    monkeypatch.setattr(
        orchestrator.config_store,
        "load",
        lambda: {**original_load(), "enable_bash": True},
    )
    result = asyncio.run(
        orchestrator.chat(
            session_id,
            "分析附件",
            mode="standard",
            attachments=[_binary_attachment(uploads)],
        )
    )
    assert result["ok"] is True
    return agent.received.get_text_content()


def test_attachment_hint_does_not_promise_bash_when_tool_missing(uploads, monkeypatch):
    """配置允许 Bash，但 Agent 实际未加载 Bash 工具时不得承诺可用（M7）。"""
    agent = _CapturingAgent(names=("read_document",))
    text = _chat_prompt_with_binary(monkeypatch, uploads, agent, session_id="t2b-m7-no-bash")
    assert "可使用 Bash" not in text
    assert "未开启 Bash" in text


def test_attachment_hint_mentions_bash_when_agent_really_has_it(uploads, monkeypatch):
    agent = _CapturingAgent(names=("bash", "read_document"))
    text = _chat_prompt_with_binary(monkeypatch, uploads, agent, session_id="t2b-m7-has-bash")
    assert "可使用 Bash" in text


def test_attachment_hint_fail_closed_when_toolkit_introspection_fails(uploads, monkeypatch):
    agent = _CapturingAgent(fail=True)
    text = _chat_prompt_with_binary(monkeypatch, uploads, agent, session_id="t2b-m7-introspect-fail")
    assert "可使用 Bash" not in text
    assert "未开启 Bash" in text


# ---------------- M8：invalidate 与 _ensure_agent 的单飞竞态 ----------------

class _ObservedAgent:
    async def observe(self, msgs):
        pass


def test_invalidate_does_not_break_single_flight_agent_build(monkeypatch):
    sid = "t2b-m8-single-flight"
    spec = AgentSpec(
        name="t2b_m8", label="T2B M8", emoji="", color="",
        description="测试", system_prompt="",
    )
    monkeypatch.setattr(orchestrator, "_get_spec_model", lambda spec_obj, session_id=None: object())
    built = []
    started = asyncio.Event()
    release = asyncio.Event()

    async def build(spec_obj, mode, session_id=None):
        built.append(object())
        started.set()
        await release.wait()
        return _ObservedAgent()

    monkeypatch.setattr(orchestrator, "_build_agent", build)

    async def scenario():
        first = asyncio.create_task(orchestrator._ensure_agent(sid, spec, "standard"))
        await started.wait()
        # 模拟配置保存：invalidate 在同步路由线程里清缓存
        orchestrator.invalidate()
        second = asyncio.create_task(orchestrator._ensure_agent(sid, spec, "standard"))
        await asyncio.sleep(0.05)
        release.set()
        return await asyncio.gather(first, second)

    first_agent, second_agent = asyncio.run(scenario())
    assert len(built) == 1, "同一 key 并发首条消息只允许构建一次"
    assert first_agent is second_agent


# ---------------- M9：脏 DB mode 经 scheduler.run_job 端到端只读 ----------------

def test_dirty_db_mode_through_scheduler_run_job_is_readonly(monkeypatch):
    from app import scheduler
    from app.models import AgentSession, ScheduledJob, ScheduledJobRunFact

    spec = TEAM["realestate"]
    captured = {}
    agent = _CapturingAgent(names=("query_tasks",))

    async def ensure(session_id, spec_obj, mode):
        names, allow_bash, allow_plugins, _ = orchestrator._tooling_plan(spec_obj, mode, config_store.load())
        captured.update(
            session_id=session_id, mode=mode, names=names,
            allow_bash=allow_bash, allow_plugins=allow_plugins,
        )
        return agent

    monkeypatch.setattr(orchestrator, "load_team", lambda: {spec.name: spec})
    monkeypatch.setattr(orchestrator, "_ensure_agent", ensure)
    monkeypatch.setattr(orchestrator, "llm_status", lambda: {"configured": True})
    monkeypatch.setattr(scheduler, "llm_status", lambda: {"configured": True})
    original_load = config_store.load
    monkeypatch.setattr(
        orchestrator.config_store,
        "load",
        lambda: {
            **original_load(),
            "enable_bash": True,
            "enable_plugins": True,
            "plugins_dir": "D:/tmp/plugins",
            "max_iters": 5,
        },
    )

    with SessionLocal() as db:
        job = ScheduledJob(
            name="T2B 脏模式", prompt="检查只读执行", agent_name=spec.name,
            mode="legacy-write", schedule_type="interval", interval_minutes=60,
            cron_expr="", action="", enabled=True, recent_runs=[],
        )
        db.add(job)
        db.commit()
        db.refresh(job)
        job_id = job.id

    sid = f"job-{job_id}"
    try:
        result = asyncio.run(scheduler.run_job(job_id))
        assert result["status"] == "ok"
        assert captured["session_id"] == sid
        assert captured["mode"] == "readonly"
        assert captured["allow_bash"] is False
        assert captured["allow_plugins"] is False
        assert "create_task" not in captured["names"]
        assert set(captured["names"]) <= orchestrator._readonly_tool_names()
        assert scheduler._load_job(job_id).mode == "legacy-write"
        with SessionLocal() as db:
            assert db.get(ScheduledJob, job_id).last_status == "ok"
    finally:
        with SessionLocal() as db:
            db.query(ScheduledJobRunFact).filter(ScheduledJobRunFact.job_id == job_id).delete()
            db.query(ScheduledJob).filter(ScheduledJob.id == job_id).delete()
            db.query(ChatMessage).filter(ChatMessage.session_id == sid).delete()
            db.query(AgentSession).filter(AgentSession.session_id == sid).delete()
            db.commit()
