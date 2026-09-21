"""AI 助手（Agent 团队）接口测试。

测试环境无模型密钥，聊天接口走 need_llm_config 分支；
Agent 构造、工具注册、路由规则、业务工具与历史持久化均可离线验证。
"""
from app.agents import orchestrator
from app.agents.llm import build_model, llm_status
from app.agents.team import TEAM, route_by_keyword
from app.agents.tools import (
    complete_task,
    create_note,
    create_schedule,
    create_task,
    query_schedules,
    query_tasks,
    search_notes,
    workbench_stats,
)
from app.config import settings
from app.database import SessionLocal
from app.models import ChatMessage


# ---------- 团队名册 ----------

def test_team_has_seven_specialists(client):
    body = client.get("/api/assistant/team").json()
    assert len(body["agents"]) == 7
    names = {a["name"] for a in body["agents"]}
    assert names == {"realestate", "leasing", "operations", "marketing", "property", "security", "it_finance"}
    for agent in body["agents"]:
        assert agent["label"] and agent["description"] and agent["tools"]


def test_team_reports_llm_not_configured_in_test_env(client):
    body = client.get("/api/assistant/team").json()
    assert body["llm"]["configured"] is False


# ---------- 路由规则 ----------

def test_keyword_routing():
    assert route_by_keyword("帮我看看写字楼租约的免租期条款") == "leasing"
    assert route_by_keyword("这几张凭证的分录帮我核对一下") == "it_finance"
    assert route_by_keyword("服务器发现一个漏洞要整改") == "security"
    assert route_by_keyword("上个月客流下滑了怎么分析") == "operations"
    assert route_by_keyword("策划一场周年庆活动方案") == "marketing"
    assert route_by_keyword("三楼空调机组维保安排") == "property"
    assert route_by_keyword("您好") == "realestate"  # 无关键词 → 默认管家


# ---------- 模型工厂 ----------

def test_build_model_returns_none_when_unconfigured():
    assert build_model() is None


def test_build_model_with_fake_config():
    settings.llm_base_url = "https://example.com/v1"
    settings.llm_api_key = "sk-fake"
    settings.llm_model = "test-model"
    try:
        assert llm_status()["configured"] is True
        model = build_model()
        assert model is not None
    finally:
        settings.llm_base_url = ""
        settings.llm_api_key = ""
        settings.llm_model = ""


# ---------- 聊天接口（无密钥分支）----------

def test_chat_returns_config_guidance_without_key(client):
    resp = client.post(
        "/api/assistant/chat",
        json={"session_id": "test-session-01", "message": "你好"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert body["need_llm_config"] is True


def test_chat_request_validation(client):
    assert client.post("/api/assistant/chat", json={"session_id": "ab", "message": "hi"}).status_code == 422
    assert client.post("/api/assistant/chat", json={"session_id": "test-session-01", "message": ""}).status_code == 422


# ---------- 业务工具（直接读写测试库）----------

def test_task_tools_roundtrip(client):
    created = create_task(title="招商跟进：某品牌", priority="high", due_date="2026-09-10 17:00", category="招商跟进")
    assert created["ok"] is True
    rows = query_tasks(keyword="招商跟进")
    assert any(r["id"] == created["task_id"] for r in rows)
    assert rows[0]["status"] in ("待办", "进行中", "已完成")
    done = complete_task(created["task_id"])
    assert done["ok"] is True
    assert all(r["id"] != created["task_id"] for r in query_tasks(overdue_only=True))


def test_create_task_invalid_date():
    result = create_task(title="测试", due_date="9月10号")
    assert result["ok"] is False and "无法解析" in result["error"]


def test_schedule_tools(client):
    ok = create_schedule(title="商户洽谈", date="2026-09-09", start_time="14:00", end_time="15:00", location="会议室B")
    assert ok["ok"] is True
    bad = create_schedule(title="倒置", date="2026-09-09", start_time="15:00", end_time="14:00")
    assert bad["ok"] is False and "晚于" in bad["error"]
    rows = query_schedules(start_date="2026-09-09", end_date="2026-09-09")
    assert any(s["title"] == "商户洽谈" for s in rows)


def test_note_tools(client):
    created = create_note(title="谈判纪要", content="品牌方要求免租期 6 个月", tags="招商,纪要")
    assert created["ok"] is True
    rows = search_notes(keyword="免租期")
    assert any(n["id"] == created["note_id"] for n in rows)
    assert any("招商" in n["tags"] for n in rows)


def test_workbench_stats_shape(client):
    stats = workbench_stats()
    assert {"task", "schedule", "note"} <= set(stats)
    assert {"todo", "in_progress", "done", "overdue"} <= set(stats["task"])


# ---------- 历史持久化 ----------

def test_history_endpoint(client):
    body = client.get("/api/assistant/history/test-session-99").json()
    assert body["session_id"] == "test-session-99"
    assert body["messages"] == []
    with SessionLocal() as db:
        db.add(ChatMessage(session_id="test-session-99", role="user", content="问题"))
        db.add(ChatMessage(session_id="test-session-99", role="assistant", agent_name="it_finance", content="回答", trace=[{"tool": "workbench_stats"}]))
        db.commit()
    body = client.get("/api/assistant/history/test-session-99").json()
    assert len(body["messages"]) == 2
    assert body["messages"][0]["role"] == "user"
    assert body["messages"][1]["agent_label"] == "信息化财务分析师"
    assert body["messages"][1]["trace"][0]["tool"] == "workbench_stats"


def test_team_spec_tools_scoped():
    # 每位专家都至少有查询工具；写入工具按职责收敛
    assert set(TEAM["it_finance"].tools) >= {"query_tasks", "create_task", "create_note"}
    assert "create_note" not in TEAM["property"].tools
    assert "complete_task" not in TEAM["security"].tools
