"""路由轻量化（演进第二批）回归：routing_model_id 配置、LLM 仲裁 TTL 缓存、模型兜底。"""
import asyncio

import pytest

from app.agents import config_store, orchestrator
from app.database import Base, engine


@pytest.fixture(autouse=True)
def _ensure_tables():
    # conftest 对 client 用例的 teardown 只 drop 不重建（TL-11）：非 client 测试
    # 跟在其后必须自建表，否则全量回归中会撞「no such table」
    Base.metadata.create_all(bind=engine)
    yield


def _team():
    from app.agents.team import AgentSpec

    def _spec(name, label, keywords, sort):
        return AgentSpec(name=name, label=label, emoji="", color="", description=label, system_prompt="", keywords=keywords, sort=sort)

    return {
        "realestate": _spec("realestate", "综合管家", ["综合"], 0),
        "leasing": _spec("leasing", "招商专员", ["招商"], 1),
    }


@pytest.fixture(autouse=True)
def _clean_route_state():
    orchestrator._route_cache.clear()
    yield
    orchestrator._route_cache.clear()


def _assistant_msg(text):
    from agentscope.message import Msg, TextBlock

    return Msg(name="调度员", role="assistant", content=[TextBlock(type="text", text=text)])


def test_routing_model_id_save_and_load_roundtrip():
    config_store.save_globals({"routing_model_id": 7})
    assert config_store.load()["routing_model_id"] == 7
    config_store.save_globals({"routing_model_id": None})
    assert config_store.load()["routing_model_id"] is None
    config_store.save_globals({"routing_model_id": 0})
    config_store.save_globals({"routing_model_id": -3})
    assert config_store.load()["routing_model_id"] is None


def test_route_by_llm_cache_hit_avoids_second_call():
    team = _team()
    calls = []

    async def _reply(self, msg, **kwargs):
        calls.append(1)
        return _assistant_msg("招商专员")

    from agentscope.agent import Agent

    monkeypatch = pytest.MonkeyPatch()
    try:
        monkeypatch.setattr(orchestrator, "_get_routing_model", lambda: object())
        monkeypatch.setattr(Agent, "reply", _reply)
        q = "缓存命中测试问题"
        assert asyncio.run(orchestrator._route_by_llm(q, team)) == "leasing"
        assert asyncio.run(orchestrator._route_by_llm(q, team)) == "leasing"
    finally:
        monkeypatch.undo()
    assert len(calls) == 1


def test_route_by_llm_failure_not_cached():
    team = _team()

    async def _reply(self, msg, **kwargs):
        raise RuntimeError("boom")

    from agentscope.agent import Agent

    monkeypatch = pytest.MonkeyPatch()
    try:
        monkeypatch.setattr(orchestrator, "_get_routing_model", lambda: object())
        monkeypatch.setattr(Agent, "reply", _reply)
        assert asyncio.run(orchestrator._route_by_llm("失败路径问题", team)) is None
        assert orchestrator._route_cache == {}
    finally:
        monkeypatch.undo()


def test_route_by_llm_ttl_expiry_triggers_new_call():
    team = _team()
    calls = []

    async def _reply(self, msg, **kwargs):
        calls.append(1)
        return _assistant_msg("招商专员")

    from agentscope.agent import Agent

    fake_time = {"t": 0.0}
    monkeypatch = pytest.MonkeyPatch()
    try:
        monkeypatch.setattr(orchestrator, "_get_routing_model", lambda: object())
        monkeypatch.setattr(Agent, "reply", _reply)
        monkeypatch.setattr(orchestrator.time, "monotonic", lambda: fake_time["t"])
        q = "TTL 过期测试问题"
        assert asyncio.run(orchestrator._route_by_llm(q, team)) == "leasing"
        fake_time["t"] = orchestrator._ROUTE_CACHE_TTL + 1
        assert asyncio.run(orchestrator._route_by_llm(q, team)) == "leasing"
    finally:
        monkeypatch.undo()
    assert len(calls) == 2


def test_get_routing_model_prefers_configured_with_shared_cache():
    orchestrator.invalidate()
    monkeypatch = pytest.MonkeyPatch()
    try:
        monkeypatch.setattr(orchestrator.config_store, "load", lambda: {"routing_model_id": 99})
        monkeypatch.setattr(orchestrator.config_store, "model_cfg_by_id", lambda i: {"model": f"m{i}"})
        built = []

        def fake_build(cfg, session_id=None):
            built.append(cfg)
            return object()

        monkeypatch.setattr(orchestrator, "build_model", fake_build)
        m1 = orchestrator._get_routing_model()
        m2 = orchestrator._get_routing_model()
        assert built == [{"model": "m99"}]
        assert m1 is m2
    finally:
        monkeypatch.undo()
    orchestrator.invalidate()


def test_get_routing_model_falls_back_when_unavailable():
    orchestrator.invalidate()
    monkeypatch = pytest.MonkeyPatch()
    try:
        monkeypatch.setattr(orchestrator.config_store, "load", lambda: {"routing_model_id": 88})
        monkeypatch.setattr(orchestrator.config_store, "model_cfg_by_id", lambda i: None)
        monkeypatch.setattr(orchestrator, "_get_model", lambda session_id=None: "GLOBAL")
        assert orchestrator._get_routing_model() == "GLOBAL"
    finally:
        monkeypatch.undo()
    orchestrator.invalidate()


def test_invalidate_clears_route_cache():
    orchestrator._route_cache[("k", "q")] = (0.0, "leasing")
    orchestrator.invalidate()
    assert orchestrator._route_cache == {}