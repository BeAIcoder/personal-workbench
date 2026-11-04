"""长会话滚动摘要（演进第二批）回归。"""
import asyncio

import pytest
from agentscope.agent import Agent

from app.agents import config_store, orchestrator
from app.agents.team import AgentSpec
from app.database import Base, SessionLocal, engine
from app.models import AgentSession, ChatMessage


@pytest.fixture(autouse=True)
def _ensure_tables():
    # conftest 对 client 用例的 teardown 只 drop 不重建（TL-11）：非 client 测试
    # 跟在其后必须自建表，否则全量回归中按字母序跑到这里会撞「no such table」
    Base.metadata.create_all(bind=engine)
    yield


def _assistant_msg(text):
    from agentscope.message import Msg, TextBlock

    return Msg(name="调度员", role="assistant", content=[TextBlock(type="text", text=text)])


def _row(sid):
    with SessionLocal() as db:
        r = db.get(AgentSession, sid)
        if r is None:
            return None
        db.expunge(r)
    return r


def _persist_msgs(sid, texts):
    with SessionLocal() as db:
        if db.get(AgentSession, sid) is None:
            db.add(AgentSession(session_id=sid))
            db.commit()
    for i, t in enumerate(texts, 1):
        role = "user" if i % 2 else "assistant"
        orchestrator._persist(sid, role, "" if role == "user" else "realestate", t, [])


def _cleanup(sid):
    with SessionLocal() as db:
        db.query(ChatMessage).filter_by(session_id=sid).delete()
        db.query(AgentSession).filter_by(session_id=sid).delete()
        db.commit()


def _team():
    def _spec(name, label, keywords, sort):
        return AgentSpec(name=name, label=label, emoji="", color="", description=label, system_prompt="", keywords=keywords, sort=sort)

    return {
        "realestate": _spec("realestate", "综合管家", ["综合"], 0),
        "leasing": _spec("leasing", "招商专员", ["招商"], 1),
    }


@pytest.fixture(autouse=True)
def _restore_globals():
    yield
    config_store.save_globals({"history_inject": 12})


def test_summary_threshold_trigger_and_cursor():
    sid = "sess-sum-ok"
    _cleanup(sid)
    config_store.save_globals({"history_inject": 2})
    _persist_msgs(sid, [f"消息{i}" for i in range(1, 17)])
    prompts = []

    async def _reply(self, msg, **kwargs):
        prompts.append(msg.get_text_content() or "")
        return _assistant_msg("压缩后的摘要")

    mp = pytest.MonkeyPatch()
    try:
        mp.setattr(orchestrator, "_get_routing_model", lambda: object())
        mp.setattr(Agent, "reply", _reply)
        out = asyncio.run(orchestrator._refresh_session_summary(sid))
    finally:
        mp.undo()
    assert out == "压缩后的摘要"
    row = _row(sid)
    assert row.summary == "压缩后的摘要"
    assert row.summary_until == 14
    assert "消息14" in prompts[0] and "消息15" not in prompts[0]
    _cleanup(sid)


def test_summary_cursor_excludes_already_summarized():
    sid = "summary-cursor-exclusion-test"
    _cleanup(sid)
    config_store.save_globals({"history_inject": 2})
    _persist_msgs(sid, [f"新消息{i}" for i in range(1, 15)])
    with SessionLocal() as db:
        r = db.get(AgentSession, sid)
        r.summary = "旧"
        r.summary_until = 6
        db.commit()
    prompts = []

    async def _reply(self, msg, **kwargs):
        prompts.append(msg.get_text_content() or "")
        return _assistant_msg("压缩后的摘要")

    mp = pytest.MonkeyPatch()
    try:
        mp.setattr(orchestrator, "_get_routing_model", lambda: object())
        mp.setattr(Agent, "reply", _reply)
        asyncio.run(orchestrator._refresh_session_summary(sid))
    finally:
        mp.undo()
    assert "新消息7" in prompts[0] and "新消息12" in prompts[0]
    assert "新消息13" not in prompts[0]
    assert _row(sid).summary_until == 12
    _cleanup(sid)


def test_summary_failure_keeps_old_summary():
    sid = "sess-sum-fail"
    _cleanup(sid)
    config_store.save_globals({"history_inject": 2})
    _persist_msgs(sid, [f"批量{i}" for i in range(1, 17)])
    with SessionLocal() as db:
        r = db.get(AgentSession, sid)
        r.summary = "既有旧摘要"
        r.summary_until = 0
        db.commit()

    async def _reply(self, msg, **kwargs):
        raise RuntimeError("summarize boom")

    mp = pytest.MonkeyPatch()
    try:
        mp.setattr(orchestrator, "_get_routing_model", lambda: object())
        mp.setattr(Agent, "reply", _reply)
        out = asyncio.run(orchestrator._refresh_session_summary(sid))
    finally:
        mp.undo()
    assert out == "既有旧摘要"
    row = _row(sid)
    assert row.summary == "既有旧摘要"
    assert row.summary_until == 0
    _cleanup(sid)


def test_summary_failure_backoff_avoids_repeated_model_calls():
    sid = "sess-sum-retry"
    _cleanup(sid)
    config_store.save_globals({"history_inject": 2})
    _persist_msgs(sid, [f"退避{i}" for i in range(1, 17)])
    with SessionLocal() as db:
        r = db.get(AgentSession, sid)
        r.summary = "旧摘要"
        r.summary_until = 0
        db.commit()

    calls = []

    async def _reply(self, msg, **kwargs):
        calls.append(1)
        raise RuntimeError("summarize boom")

    mp = pytest.MonkeyPatch()
    try:
        mp.setattr(orchestrator, "_get_routing_model", lambda: object())
        mp.setattr(Agent, "reply", _reply)
        assert asyncio.run(orchestrator._refresh_session_summary(sid)) == "旧摘要"
        assert asyncio.run(orchestrator._refresh_session_summary(sid)) == "旧摘要"
        assert len(calls) == 1
        orchestrator._summary_retry_until[sid] = 0
        assert asyncio.run(orchestrator._refresh_session_summary(sid)) == "旧摘要"
        assert len(calls) == 2
    finally:
        mp.undo()
        orchestrator._summary_retry_until.pop(sid, None)
    assert _row(sid).summary_until == 0
    _cleanup(sid)


def test_ensure_agent_injects_summary_and_history(monkeypatch):
    sid = "sess-sum-inject"
    _cleanup(sid)
    config_store.save_globals({"history_inject": 12})
    _persist_msgs(sid, ["问1", "答1", "问2"])
    with SessionLocal() as db:
        r = db.get(AgentSession, sid)
        if r is None:
            r = AgentSession(session_id=sid)
            db.add(r)
        r.summary = "历史摘要文本"
        r.summary_until = 3
        db.commit()
    observed = []

    class _FakeAgent:
        # 真实 agentscope Agent.observe 是 async：调用方若漏了 await，协程不会执行，
        # observed 保持为空——本用例因此能抓住「历史注入静默失效」的回归。
        async def observe(self, msgs):
            observed.extend(msgs)

    async def fake_build(spec, mode, session_id=None):
        return _FakeAgent()

    monkeypatch.setattr(orchestrator, "_build_agent", fake_build)
    monkeypatch.setattr(orchestrator, "_get_spec_model", lambda spec, session_id=None: object())
    monkeypatch.setattr(orchestrator, "_get_routing_model", lambda: object())
    spec = _team()["realestate"]
    asyncio.run(orchestrator._ensure_agent(sid, spec, "standard"))
    texts = [m.get_text_content() or "" for m in observed]
    assert texts[0].startswith("【此前对话摘要】历史摘要文本")
    assert texts[1:] == ["问1", "答1"]
    assert "问2" not in texts  # 末尾用户消息是本轮输入，由 reply 接收，不能重复 observe
    _cleanup(sid)
    orchestrator.invalidate()