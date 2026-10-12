"""v1.15.0 会话归档与导出测试：归档/恢复/404/列表过滤/导出内容与 Content-Disposition。"""
from app.agents import orchestrator


def _mk_session(session_id: str, title: str = "测试会话") -> None:
    orchestrator.session_create(session_id, title)


def _mk_messages(session_id: str) -> None:
    orchestrator._persist(session_id, "user", "", "帮我看下这个月的进项发票", [])
    orchestrator._persist(
        session_id,
        "assistant",
        "it_finance",
        "已核对：进项发票共 12 张，其中 2 张待认证。",
        [{"tool": "workbench_stats", "args": {}, "result": {"task": {"todo": 3}}}],
        "先查任务统计，再核对发票状态",
    )


# ---------------- 归档 / 恢复 ----------------

def test_archive_and_unarchive(client):
    _mk_session("sess-arc-01")
    body = client.post("/api/assistant/sessions/sess-arc-01/archive").json()
    assert body["ok"] is True
    row = next(s for s in orchestrator.sessions_list() if s["session_id"] == "sess-arc-01")
    assert row["archived"] is True

    body = client.post("/api/assistant/sessions/sess-arc-01/unarchive").json()
    assert body["ok"] is True
    row = next(s for s in orchestrator.sessions_list() if s["session_id"] == "sess-arc-01")
    assert row["archived"] is False


def test_archive_missing_session_404(client):
    assert client.post("/api/assistant/sessions/sess-not-exist/archive").status_code == 404
    assert client.post("/api/assistant/sessions/sess-not-exist/unarchive").status_code == 404


def test_sessions_list_includes_archived_field(client):
    _mk_session("sess-arc-02")
    rows = client.get("/api/assistant/sessions").json()["sessions"]
    row = next(s for s in rows if s["session_id"] == "sess-arc-02")
    assert row["archived"] is False


def test_sessions_list_filter_by_archived(client):
    _mk_session("sess-arc-03")
    _mk_session("sess-arc-04")
    client.post("/api/assistant/sessions/sess-arc-03/archive")

    all_rows = client.get("/api/assistant/sessions").json()["sessions"]
    assert {s["session_id"] for s in all_rows} >= {"sess-arc-03", "sess-arc-04"}

    archived = client.get("/api/assistant/sessions", params={"archived": "true"}).json()["sessions"]
    assert [s["session_id"] for s in archived] == ["sess-arc-03"]

    active = client.get("/api/assistant/sessions", params={"archived": "false"}).json()["sessions"]
    assert all(s["session_id"] != "sess-arc-03" for s in active)
    assert any(s["session_id"] == "sess-arc-04" for s in active)


def test_archived_session_still_exportable(client):
    _mk_session("sess-arc-05")
    client.post("/api/assistant/sessions/sess-arc-05/archive")
    assert client.get("/api/assistant/sessions/sess-arc-05/export").status_code == 200


# ---------------- 导出 ----------------

def test_export_markdown_content_and_disposition(client):
    _mk_session("sess-exp-01", "进项发票核对")
    _mk_messages("sess-exp-01")

    resp = client.get("/api/assistant/sessions/sess-exp-01/export")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/markdown")
    assert 'attachment; filename="session-sess-exp-01.md"' in resp.headers["content-disposition"]

    text = resp.text
    assert "进项发票核对" in text          # 会话标题
    assert "sess-exp-01" in text            # 会话 ID
    assert "消息数：2" in text               # 消息数
    assert "用户" in text                    # 角色
    assert "信息化财务分析师" in text        # 专家名
    assert "进项发票共 12 张" in text        # 正文
    assert "先查任务统计" in text            # thinking（折叠小节）
    assert "workbench_stats" in text        # trace（折叠小节）
    assert "<details>" in text and "</details>" in text


def test_export_missing_session_404(client):
    assert client.get("/api/assistant/sessions/sess-not-exist/export").status_code == 404


def test_session_export_helper_orders_messages(client):
    from app.database import SessionLocal
    from app.models import ChatMessage

    _mk_session("sess-exp-02")
    with SessionLocal() as db:
        db.add(ChatMessage(session_id="sess-exp-02", role="user", content="第一条"))
        db.add(ChatMessage(session_id="sess-exp-02", role="assistant", agent_name="it_finance", content="第二条"))
        db.commit()
    data = orchestrator.session_export("sess-exp-02")
    assert data["msg_count"] == 2
    assert data["markdown"].index("第一条") < data["markdown"].index("第二条")
