"""AI 助手 v1.3 新能力测试：会话管理、模型档案、工作模式、SSE 流式端点。"""
import asyncio

from app.agents import config_store, orchestrator
from app.agents.team import QUERY_TOOLS, TEAM


# ---------------- 会话管理 ----------------

def test_sessions_lifecycle(client):
    orchestrator.session_create("sess-test-001", "我的会话")
    lst = orchestrator.sessions_list()
    assert any(s["session_id"] == "sess-test-001" and s["title"] == "我的会话" for s in lst)

    orchestrator.session_rename("sess-test-001", "改名会话")
    assert any(s["title"] == "改名会话" for s in orchestrator.sessions_list())

    orchestrator.session_delete("sess-test-001")
    assert all(s["session_id"] != "sess-test-001" for s in orchestrator.sessions_list())


def test_touch_session_creates_title_from_message(client):
    orchestrator._touch_session("sess-title-01", "帮我整理本月进项发票并认证")
    lst = orchestrator.sessions_list()
    row = next(s for s in lst if s["session_id"] == "sess-title-01")
    assert row["title"].startswith("帮我整理本月进项发票")


def test_sessions_api(client):
    orchestrator.session_create("sess-api-01", "接口会话")
    body = client.get("/api/assistant/sessions").json()
    assert any(s["session_id"] == "sess-api-01" for s in body["sessions"])
    assert client.delete("/api/assistant/sessions/sess-api-01").json()["ok"] is True


def test_history_includes_thinking(client):
    from app.database import SessionLocal
    from app.models import ChatMessage

    with SessionLocal() as db:
        db.add(ChatMessage(session_id="sess-think-01", role="assistant", agent_name="it_finance",
                           content="结论", thinking="先核对借贷方向"))
        db.commit()
    body = client.get("/api/assistant/history/sess-think-01").json()
    assert body["messages"][0]["thinking"] == "先核对借贷方向"


# ---------------- 工作模式 ----------------

def test_resolve_mode():
    assert orchestrator._resolve_mode("readonly") == "readonly"
    assert orchestrator._resolve_mode("deep") == "deep"
    assert orchestrator._resolve_mode("unknown") == "standard"
    assert orchestrator._resolve_mode(None) == "standard"


def test_work_modes_registry(client):
    body = client.get("/api/assistant/team").json()
    assert set(body["work_modes"]) == {"standard", "readonly", "deep"}


# （只读模式工具裁剪的验证见 test_assistant_config.py::test_readonly_agent_has_query_tools_only）


# ---------------- SSE 流式端点 ----------------

def test_stream_endpoint_without_llm(client):
    resp = client.post(
        "/api/assistant/chat/stream",
        json={"session_id": "sess-stream-01", "message": "你好"},
    )
    assert resp.status_code == 200
    assert "need_llm_config" in resp.text
    assert "[DONE]" in resp.text


def test_stream_endpoint_validation(client):
    assert client.post("/api/assistant/chat/stream", json={"session_id": "ab", "message": "hi"}).status_code == 422
