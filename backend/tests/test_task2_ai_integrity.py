"""Task 2：AI 回合完整性、隐私边界与会话锁回归。"""
import asyncio

import pytest
from agentscope.message import Msg, TextBlock
from sqlalchemy.exc import OperationalError

from app.agents import config_store, orchestrator
from app.agents import team as team_mod
from app.agents.team import AgentSpec
from app.database import SessionLocal
from app.models import AgentSession, ChatMessage


@pytest.fixture(autouse=True)
def _clean_agent_state():
    orchestrator.invalidate()
    team_mod.invalidate_team_cache()
    yield
    orchestrator.invalidate()
    team_mod.invalidate_team_cache()


def _messages(session_id):
    with SessionLocal() as db:
        rows = (
            db.query(ChatMessage)
            .filter(ChatMessage.session_id == session_id)
            .order_by(ChatMessage.id.asc())
            .all()
        )
        return [(row.role, row.content) for row in rows]


def _reply(text="合成回复"):
    return Msg(name="测试专家", role="assistant", content=[TextBlock(type="text", text=text)])


class _ReplyAgent:
    def __init__(self, error=None):
        self.error = error

    async def reply(self, msg):
        if self.error:
            raise self.error
        return _reply()

    async def reply_stream(self, msg, yield_final_msg=True):
        if self.error:
            raise self.error
        yield _reply()


def _stub_route(monkeypatch):
    spec = AgentSpec(
        name="realestate", label="综合管家", emoji="", color="",
        description="测试", system_prompt="", keywords=[],
    )
    monkeypatch.setattr(orchestrator, "llm_status", lambda: {"configured": True})
    monkeypatch.setattr(orchestrator, "load_team", lambda: {"realestate": spec})

    async def no_llm_route(question, team, session_id=None):
        return None

    monkeypatch.setattr(orchestrator, "_route_by_llm", no_llm_route)
    return spec


def _stub_agent(monkeypatch, agent):
    async def ensure(session_id, spec, mode):
        return agent

    monkeypatch.setattr(orchestrator, "_ensure_agent", ensure)


@pytest.mark.parametrize("streaming", [False, True])
def test_user_turn_survives_agent_build_failure(client, monkeypatch, streaming):
    _stub_route(monkeypatch)

    async def fail_build(session_id, spec, mode):
        raise RuntimeError("synthetic build failure")

    monkeypatch.setattr(orchestrator, "_ensure_agent", fail_build)
    sid = f"task2-build-{streaming}"

    async def run():
        if streaming:
            return [event async for event in orchestrator.chat_stream(sid, "构建失败也要保留提问")]
        return await orchestrator.chat(sid, "构建失败也要保留提问")

    with pytest.raises(RuntimeError, match="synthetic build"):
        asyncio.run(run())
    assert _messages(sid) == [("user", "构建失败也要保留提问")]


def test_user_turn_survives_nonstream_reply_failure(client, monkeypatch):
    _stub_route(monkeypatch)
    _stub_agent(monkeypatch, _ReplyAgent(RuntimeError("synthetic reply failure")))
    with pytest.raises(RuntimeError, match="synthetic reply"):
        asyncio.run(orchestrator.chat("task2-reply-fail", "回复失败也要保留提问"))
    assert _messages("task2-reply-fail") == [("user", "回复失败也要保留提问")]


def test_user_turn_is_saved_before_routing_model(client, monkeypatch):
    spec = _stub_route(monkeypatch)
    _stub_agent(monkeypatch, _ReplyAgent())
    seen = []

    async def route_with_db_check(question, team, session_id=None):
        seen.append(_messages("task2-before-route"))
        return spec.name

    monkeypatch.setattr(orchestrator, "_route_by_llm", route_with_db_check)
    result = asyncio.run(orchestrator.chat("task2-before-route", "先保存再仲裁"))
    assert result["ok"] is True
    assert seen == [[("user", "先保存再仲裁")]]


def test_nonstream_assistant_persistence_failure_is_explicit(client, monkeypatch):
    _stub_route(monkeypatch)
    _stub_agent(monkeypatch, _ReplyAgent())
    real_persist = orchestrator._persist

    def persist_with_failure(session_id, role, *args):
        if role == "assistant":
            raise OperationalError("INSERT", {}, RuntimeError("synthetic db failure"))
        return real_persist(session_id, role, *args)

    monkeypatch.setattr(orchestrator, "_persist", persist_with_failure)
    result = asyncio.run(orchestrator.chat("task2-save-reply", "保存失败不能假成功"))
    assert result["ok"] is True and result["reply"] == "合成回复"
    assert result["persisted"] is False
    assert "保存" in result["persistence_warning"]
    assert _messages("task2-save-reply") == [("user", "保存失败不能假成功")]


def test_stream_assistant_persistence_failure_is_done_warning_only(client, monkeypatch):
    """落库失败是成功回复的持久化告警，不是对话错误：不得发 error（error 仍是终止语义）。"""
    _stub_route(monkeypatch)
    _stub_agent(monkeypatch, _ReplyAgent())
    real_persist = orchestrator._persist

    def persist_with_failure(session_id, role, *args):
        if role == "assistant":
            raise OperationalError("INSERT", {}, RuntimeError("synthetic db failure"))
        return real_persist(session_id, role, *args)

    monkeypatch.setattr(orchestrator, "_persist", persist_with_failure)

    async def collect():
        return [event async for event in orchestrator.chat_stream("task2-stream-save", "流式保存失败")]

    events = asyncio.run(collect())
    assert not any(event["type"] == "error" for event in events)
    done = events[-1]
    assert done["type"] == "done"
    assert done["reply"] == "合成回复"
    assert done["persisted"] is False
    assert "保存" in done["persistence_warning"]
    assert _messages("task2-stream-save") == [("user", "流式保存失败")]


def _collect_stream(session_id, message, **kwargs):
    async def collect():
        return [event async for event in orchestrator.chat_stream(session_id, message, **kwargs)]

    return asyncio.run(collect())


def test_llm_unconfigured_does_not_persist_orphan_user(client, monkeypatch):
    _stub_route(monkeypatch)
    monkeypatch.setattr(orchestrator, "llm_status", lambda: {"configured": False})

    result = asyncio.run(orchestrator.chat("task2-nollm", "未配置时不要落库"))
    assert result["ok"] is False and result["need_llm_config"] is True
    events = _collect_stream("task2-nollm-stream", "未配置时不要落库")
    assert events[0]["type"] == "error" and events[0]["need_llm_config"] is True
    assert _messages("task2-nollm") == []
    assert _messages("task2-nollm-stream") == []


def test_empty_team_does_not_persist_orphan_user(client, monkeypatch):
    _stub_route(monkeypatch)
    monkeypatch.setattr(orchestrator, "load_team", lambda: {})

    result = asyncio.run(orchestrator.chat("task2-empty-team", "空团队不要落库"))
    assert result["ok"] is False and "专家团队为空" in result["message"]
    events = _collect_stream("task2-empty-team-stream", "空团队不要落库")
    assert any(event["type"] == "error" and "专家团队为空" in event["message"] for event in events)
    assert _messages("task2-empty-team") == []
    assert _messages("task2-empty-team-stream") == []


def test_preroute_scope_refusal_does_not_persist_orphan_user(client, monkeypatch):
    _stub_route(monkeypatch)
    monkeypatch.setattr(
        config_store,
        "load",
        lambda: {"usage_scope": "plan_interactive_only", "provider_name": "合成套餐"},
    )

    result = asyncio.run(orchestrator.chat("job-task2-scope", "定时任务不要落库"))
    assert result["ok"] is False and "套餐" in result["message"]
    events = _collect_stream("job-task2-scope-stream", "定时任务不要落库")
    assert any(event["type"] == "error" and "套餐" in event["message"] for event in events)
    assert _messages("job-task2-scope") == []
    assert _messages("job-task2-scope-stream") == []


def test_explicit_agent_scope_refusal_does_not_persist_orphan_user(client, monkeypatch):
    _stub_route(monkeypatch)
    monkeypatch.setattr(
        config_store,
        "load",
        lambda: {"usage_scope": "plan_interactive_only", "provider_name": "合成套餐"},
    )

    result = asyncio.run(
        orchestrator.chat("job-task2-scope-agent", "指定专家也不要落库", agent_name="realestate")
    )
    assert result["ok"] is False and "套餐" in result["message"]
    events = _collect_stream(
        "job-task2-scope-agent-stream", "指定专家也不要落库", agent_name="realestate"
    )
    assert any(event["type"] == "error" and "套餐" in event["message"] for event in events)
    assert _messages("job-task2-scope-agent") == []
    assert _messages("job-task2-scope-agent-stream") == []


def test_memory_disabled_observes_no_history_or_summary(client, monkeypatch):
    sid = "task2-memory-off"
    orchestrator._touch_session(sid, "旧会话")
    orchestrator._persist(sid, "user", "", "旧提问", [])
    orchestrator._persist(sid, "assistant", "realestate", "旧回复", [])
    with SessionLocal() as db:
        row = db.get(AgentSession, sid)
        row.summary = "不应发送的旧摘要"
        db.commit()
    config_store.save_globals({"enable_memory": False, "history_inject": 12})
    observed = []

    class FakeAgent:
        async def observe(self, msgs):
            observed.extend(msgs)

    async def build(spec, mode, session_id=None):
        return FakeAgent()

    monkeypatch.setattr(orchestrator, "_build_agent", build)
    monkeypatch.setattr(orchestrator, "_get_spec_model", lambda spec, session_id=None: object())
    spec = AgentSpec(name="realestate", label="综合", emoji="", color="", description="", system_prompt="")
    asyncio.run(orchestrator._ensure_agent(sid, spec, "standard"))
    assert observed == []


def test_job_sessions_build_fresh_agent_without_history_or_summary(client, monkeypatch):
    sid = "job-task2-independent"
    config_store.save_globals({"enable_memory": True, "history_inject": 2})
    orchestrator._touch_session(sid, "定时任务")
    orchestrator._persist(sid, "user", "", "上一次任务", [])
    orchestrator._persist(sid, "assistant", "realestate", "上一次结果", [])
    with SessionLocal() as db:
        row = db.get(AgentSession, sid)
        row.summary = "上一次任务摘要"
        db.commit()
    built = []

    class FakeAgent:
        def __init__(self):
            self.observed = []

        async def observe(self, msgs):
            self.observed.extend(msgs)

    async def build(spec, mode, session_id=None):
        agent = FakeAgent()
        built.append(agent)
        return agent

    monkeypatch.setattr(orchestrator, "_build_agent", build)
    monkeypatch.setattr(orchestrator, "_get_spec_model", lambda spec, session_id=None: object())
    spec = AgentSpec(name="realestate", label="综合", emoji="", color="", description="", system_prompt="")
    first = asyncio.run(orchestrator._ensure_agent(sid, spec, "standard"))
    second = asyncio.run(orchestrator._ensure_agent(sid, spec, "standard"))
    assert first is not second and len(built) == 2
    assert first.observed == [] and second.observed == []
    assert asyncio.run(orchestrator._refresh_session_summary(sid)) == ""


def test_current_user_message_is_not_duplicated_in_observed_history(client, monkeypatch):
    _stub_route(monkeypatch)
    observed = []

    class FakeAgent:
        async def observe(self, msgs):
            observed.append([msg.get_text_content() or "" for msg in msgs])

        async def reply(self, msg):
            return _reply("第一答")

    async def build(spec, mode, session_id=None):
        return FakeAgent()

    monkeypatch.setattr(orchestrator, "_build_agent", build)
    monkeypatch.setattr(orchestrator, "_get_spec_model", lambda spec, session_id=None: object())
    config_store.save_globals({"enable_memory": True, "history_inject": 12})
    sid = "task2-no-duplicate"
    assert asyncio.run(orchestrator.chat(sid, "第一问"))["ok"] is True
    orchestrator._agents.clear()
    assert asyncio.run(orchestrator.chat(sid, "第二问"))["ok"] is True
    assert observed == [["第一问", "第一答"]]


def test_running_session_delete_returns_409_and_keeps_lock(client):
    sid = "task2-delete-running"
    orchestrator.session_create(sid, "生成中")
    lock = orchestrator.session_run_lock(sid)
    asyncio.run(lock.acquire())
    try:
        resp = client.delete(f"/api/assistant/sessions/{sid}")
        assert resp.status_code == 409
        assert "生成中" in resp.json()["detail"]
        assert orchestrator.session_run_lock(sid) is lock
        assert client.get(f"/api/assistant/sessions/{sid}/export").status_code == 200
    finally:
        lock.release()
    assert client.delete(f"/api/assistant/sessions/{sid}").status_code == 200


def test_idle_session_lock_is_reclaimed(client, monkeypatch):
    clock = {"now": 100.0}
    monkeypatch.setattr(orchestrator.time, "monotonic", lambda: clock["now"])
    sid = "task2-idle-lock"
    lock = orchestrator.session_run_lock(sid)
    asyncio.run(lock.acquire())
    lock.release()
    clock["now"] = 10_000.0
    assert orchestrator.session_run_lock(sid) is not lock


def test_idle_cleanup_sweeps_other_sessions_but_keeps_running(client, monkeypatch):
    clock = {"now": 100.0}
    monkeypatch.setattr(orchestrator.time, "monotonic", lambda: clock["now"])
    for index in range(3):
        orchestrator.session_run_lock(f"task2-stale-{index}")
    running = orchestrator.session_run_lock("task2-still-running")
    asyncio.run(running.acquire())
    clock["now"] = 10_000.0
    orchestrator.session_run_lock("task2-new-session")
    assert not any(key.startswith("task2-stale-") for key in orchestrator._session_run_locks)
    assert orchestrator._session_run_locks["task2-still-running"] is running
    running.release()


def test_begin_session_run_is_atomic_and_releasable():
    sid = "task2-active-session"
    try:
        assert orchestrator.begin_session_run(sid) is True
        assert orchestrator.begin_session_run(sid) is False
    finally:
        orchestrator.end_session_run(sid)
    assert orchestrator.begin_session_run(sid) is True
    orchestrator.end_session_run(sid)


def test_double_click_second_chat_returns_409(client, monkeypatch):
    import threading

    entered = threading.Event()
    release = threading.Event()
    first_result = []

    async def slow_chat(*args):
        entered.set()
        await asyncio.to_thread(release.wait)
        return {"ok": True, "reply": "完成"}

    monkeypatch.setattr(orchestrator, "chat", slow_chat)

    def first_request():
        first_result.append(client.post(
            "/api/assistant/chat",
            json={"session_id": "task2-double-click", "message": "第一次"},
        ))

    thread = threading.Thread(target=first_request)
    thread.start()
    assert entered.wait(5)
    try:
        second = client.post(
            "/api/assistant/chat",
            json={"session_id": "task2-double-click", "message": "第二次"},
        )
        assert second.status_code == 409
    finally:
        release.set()
        thread.join(5)
    assert not thread.is_alive()
    assert first_result[0].status_code == 200


@pytest.mark.parametrize("call", [
    lambda: __import__("app.agents.tools", fromlist=["query_tasks"]).query_tasks(limit="bad"),
    lambda: __import__("app.agents.tools", fromlist=["query_schedules"]).query_schedules(limit="bad"),
    lambda: __import__("app.agents.tools", fromlist=["search_notes"]).search_notes(limit="bad"),
    lambda: __import__("app.agents.tools", fromlist=["complete_task"]).complete_task("bad"),
])
def test_tool_integer_parameter_errors_are_structured(client, call):
    result = call()
    assert result["ok"] is False
    assert "参数" in result["error"] and "整数" in result["error"]
