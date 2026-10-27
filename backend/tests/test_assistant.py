"""AI 助手（Agent 团队）接口测试。

测试环境无模型密钥，聊天接口走 need_llm_config 分支；
Agent 构造、工具注册、路由规则、业务工具与历史持久化均可离线验证。
"""
import pytest

from app.agents import orchestrator
from app.agents import team as team_mod
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

def test_team_has_eight_specialists(client):
    body = client.get("/api/assistant/team").json()
    assert len(body["agents"]) == 8
    names = {a["name"] for a in body["agents"]}
    assert names == {"realestate", "leasing", "operations", "marketing", "property", "security", "it_finance", "finance_specialist"}
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


def _route_agent(name, label, keywords, sort=0):
    return team_mod.AgentSpec(
        name=name,
        label=label,
        emoji="🧪",
        color="#000000",
        description=f"{label}测试专家",
        system_prompt="",
        keywords=keywords,
        sort=sort,
    )


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("服务器中毒了怎么处理", "security"),
        ("这个月收缴情况怎么样", "operations"),
        ("房租和免租期怎么谈", "leasing"),
        ("发票台账这个月对一下", "finance_specialist"),
        ("这批单据今天要开票", "finance_specialist"),
        ("这张报销单怎么走流程", "finance_specialist"),
        ("活动档期怎么排", "marketing"),
        ("业财怎么看", "it_finance"),
        ("这个月申报怎么做", "finance_specialist"),
        ("报表和预算的信息化分析", "it_finance"),
    ],
)
def test_static_team_domain_routing(question, expected):
    assert team_mod.keyword_hit(question, TEAM) == expected


def test_normalize_route_text_applies_nfkc_and_casefold():
    assert team_mod.normalize_route_text("ＲＯＩ／ＩＲＲ") == "roi/irr"


@pytest.mark.parametrize("generic_term", ["安排", "任务", "待办", "提醒", "笔记", "日程", "分析"])
def test_generic_terms_do_not_override_specific_keywords(generic_term):
    team = {
        "general": _route_agent("general", "综合协调", [generic_term], sort=0),
        "specialist": _route_agent("specialist", "安全专家", ["病毒"], sort=1),
    }
    matches = team_mod.route_scores(f"{generic_term}病毒", team)
    scores = {match["name"]: match["score"] for match in matches}
    assert scores["specialist"] > scores["general"]


def test_long_keyword_outweighs_generic_term():
    team = {
        "general": _route_agent("general", "综合协调", ["安排"], sort=0),
        "specialist": _route_agent("specialist", "安全专家", ["网络安全"], sort=1),
    }
    matches = team_mod.route_scores("安排网络安全检查", team)
    scores = {match["name"]: match["score"] for match in matches}
    assert scores["specialist"] > scores["general"]


def test_custom_agent_label_is_a_high_weight_route_term():
    team = {
        "general": _route_agent("general", "综合协调", ["安排"], sort=0),
        "legal": _route_agent("legal", "法务顾问", ["合同"], sort=1),
    }
    matches = team_mod.route_scores("安排法务顾问", team)
    assert matches[0]["name"] == "legal"
    legal = next(match for match in matches if match["name"] == "legal")
    general = next(match for match in matches if match["name"] == "general")
    assert legal["matched_terms"] == ["法务顾问"]
    assert legal["score"] > general["score"]
    assert legal["confidence"] == "high"


def test_same_text_label_raises_keyword_weight_without_duplicate_term():
    team = {"legal": _route_agent("legal", "法务", ["法务"])}
    match = team_mod.route_scores("法务", team)[0]
    assert match["score"] == 4
    assert match["matched_terms"] == ["法务"]


def test_generic_label_is_specific_when_keyword_has_same_text():
    team = {"coordinator": _route_agent("coordinator", "安排", ["安排"])}
    match = team_mod.route_scores("安排", team)[0]
    assert match["score"] == 4
    assert match["matched_terms"] == ["安排"]
    assert match["confidence"] == "high"
    assert team_mod.keyword_hit("安排", team) == "coordinator"


def test_english_keywords_match_nfkc_terms_next_to_chinese():
    team = {"investment": _route_agent("investment", "投资测算", ["ROI", "IRR"])}
    matches = team_mod.route_scores("测算ＲＯＩ和IRR", team)
    assert matches[0]["hits"] == ["ROI", "IRR"]
    assert matches[0]["matched_terms"] == ["roi", "irr"]


def test_english_keywords_do_not_match_inside_longer_words():
    team = {"investment": _route_agent("investment", "投资测算", ["ROI", "IRR"])}
    assert team_mod.route_scores("ROIC和IRRS", team) == []


def test_tied_top_scores_are_low_confidence_and_not_selected():
    team = {
        "first": _route_agent("first", "安全第一", ["病毒"], sort=0),
        "second": _route_agent("second", "安全第二", ["病毒"], sort=1),
    }
    matches = team_mod.route_scores("服务器病毒", team)
    assert [match["name"] for match in matches] == ["first", "second"]
    assert {match["confidence"] for match in matches} == {"low"}
    assert team_mod.best_keyword_candidate(matches) is None
    assert team_mod.keyword_hit("服务器病毒", team) is None


def test_generic_only_candidate_is_low_confidence():
    matches = team_mod.route_scores("安排", {"realestate": TEAM["realestate"]})
    assert matches[0]["confidence"] == "low"
    assert team_mod.best_keyword_candidate(matches) is None


def test_best_keyword_candidate_returns_unique_high_confidence_match():
    matches = team_mod.route_scores("服务器病毒", TEAM)
    candidate = team_mod.best_keyword_candidate(matches)
    assert candidate is not None
    assert candidate["name"] == "security"
    assert candidate["confidence"] == "high"


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


def test_session_id_charset_enforced(client):
    """创建/聊天与归档/导出等接口同口径校验 session_id，避免产生无法管理的幽灵会话。"""
    resp = client.post("/api/assistant/sessions", json={"session_id": "bad id!", "title": "非法ID"})
    assert resp.status_code == 400
    assert "非法的 session_id" in resp.json()["detail"]

    session_ids = {s["session_id"] for s in client.get("/api/assistant/sessions").json()["sessions"]}
    assert "bad id!" not in session_ids

    for path in ("/api/assistant/chat", "/api/assistant/chat/stream"):
        resp = client.post(path, json={"session_id": "bad id!", "message": "hi"})
        assert resp.status_code == 400, f"{path} 未校验 session_id: {resp.status_code}"
        assert "非法的 session_id" in resp.json()["detail"]


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


def test_first_message_sets_default_session_title(client):
    """界面「新建」先建空会话（标题「新会话」），首条消息需补成消息摘要。"""
    orchestrator.session_create("s_title_0001", "")
    orchestrator._touch_session("s_title_0001", "帮我核对一下这个月的发票")
    sessions = client.get("/api/assistant/sessions").json()["sessions"]
    row = next(s for s in sessions if s["session_id"] == "s_title_0001")
    assert row["title"] == "帮我核对一下这个月的发票"
    # 用户手动改名后，后续消息不得覆盖
    orchestrator.session_rename("s_title_0001", "自定义标题")
    orchestrator._touch_session("s_title_0001", "下一条消息")
    sessions = client.get("/api/assistant/sessions").json()["sessions"]
    row = next(s for s in sessions if s["session_id"] == "s_title_0001")
    assert row["title"] == "自定义标题"


def test_team_spec_tools_scoped():
    # 每位专家都至少有查询工具；写入工具按职责收敛
    assert set(TEAM["it_finance"].tools) >= {"query_tasks", "create_task", "create_note"}
    assert "create_note" not in TEAM["property"].tools


# ---------- Task 5：模型级实测（model_id）与定时任务套餐边界 ----------

def _mk_provider(client, name, preset_key, base_url="https://api.example.com/v1", api_key="sk-test-0001"):
    pid = client.post(
        "/api/assistant/providers",
        json={"name": name, "protocol": "openai", "base_url": base_url, "api_key": api_key, "preset_key": preset_key},
    ).json()["provider"]["id"]
    return pid


def test_config_test_model_id_writes_probe_verdicts(client, monkeypatch):
    """TestRequest 可选 model_id：工具/图片实测按该模型执行，结论写 supports_tools/supports_vision（来源 probe）。"""
    from app.agents import config_store

    pid = _mk_provider(client, "实测供应商", "deepseek")
    mid = client.post(f"/api/assistant/providers/{pid}/models", json={"model": "m-probe"}).json()["model"]["id"]

    async def fake_text(cfg):
        return {"ok": True, "latency_ms": 1, "reply": "连接成功"}

    async def fake_tools(cfg):
        assert cfg["model"] == "m-probe"
        return {"ok": True, "latency_ms": 1, "tool_calls": ["get_current_time"], "verdict": "supported"}

    async def fake_image(cfg):
        return {"ok": True, "latency_ms": 1, "reply": "红色", "verdict": "supported"}

    monkeypatch.setattr(config_store, "test_connection", fake_text)
    monkeypatch.setattr(config_store, "test_tool_calling", fake_tools)
    monkeypatch.setattr(config_store, "test_multimodal", fake_image)

    resp = client.post(
        "/api/assistant/config/test",
        json={"model_id": mid, "test_tools": True, "include_image": True},
    )
    assert resp.status_code == 200, resp.text
    prov = next(p for p in client.get("/api/assistant/providers").json()["providers"] if p["id"] == pid)
    m = next(x for x in prov["models"] if x["id"] == mid)
    assert m["supports_tools"] == 1 and m["supports_vision"] is True
    assert m["capability"]["sources"]["supports_tools"] == "probe"
    assert m["capability"]["sources"]["supports_vision"] == "probe"


def test_config_test_model_id_verdict_rejected_and_unknown(client, monkeypatch):
    """明确参数拒绝写 0；网络错误（unknown）保持原值，不把「没测到」当成「不支持」。"""
    from app.agents import config_store

    pid = _mk_provider(client, "实测供应商二", "deepseek")
    mid = client.post(f"/api/assistant/providers/{pid}/models", json={"model": "m-probe2"}).json()["model"]["id"]

    async def fake_text(cfg):
        return {"ok": True, "latency_ms": 1, "reply": "连接成功"}

    async def fake_tools(cfg):
        return {"ok": False, "latency_ms": 1, "error": "请求被服务器拒绝（400）", "verdict": "rejected"}

    async def fake_image(cfg):
        return {"ok": False, "latency_ms": 1, "error": "无法连接到服务器", "verdict": "unknown"}

    monkeypatch.setattr(config_store, "test_connection", fake_text)
    monkeypatch.setattr(config_store, "test_tool_calling", fake_tools)
    monkeypatch.setattr(config_store, "test_multimodal", fake_image)

    resp = client.post(
        "/api/assistant/config/test",
        json={"model_id": mid, "test_tools": True, "include_image": True},
    )
    assert resp.status_code == 200, resp.text
    prov = next(p for p in client.get("/api/assistant/providers").json()["providers"] if p["id"] == pid)
    m = next(x for x in prov["models"] if x["id"] == mid)
    assert m["supports_tools"] == 0
    assert m["capability"]["sources"]["supports_tools"] == "probe"
    assert m["supports_vision"] is False  # 初值保持，来源不写
    assert "supports_vision" not in m["capability"]["sources"]


def test_config_test_unknown_model_id_rejected(client, monkeypatch):
    """model_id 不存在/未启用：400 拒绝，不发起任何测试。"""
    from app.agents import config_store

    seen = []

    async def fake_text(cfg):
        seen.append(cfg)
        return {"ok": True}

    monkeypatch.setattr(config_store, "test_connection", fake_text)
    resp = client.post("/api/assistant/config/test", json={"model_id": 999999})
    assert resp.status_code == 400
    assert seen == []


def test_job_session_refuses_plan_interactive_only_model(client):
    """定时任务会话（job-*）选中交互式套餐模型：中文拒绝（聊天/流式同口径），不发起推理。"""
    import asyncio

    pid = _mk_provider(client, "智谱交互套餐", "zhipu_coding", base_url="https://open.bigmodel.cn/api/paas/v4")
    mid = client.post(f"/api/assistant/providers/{pid}/models", json={"model": "glm-5.2"}).json()["model"]["id"]
    assert client.post(f"/api/assistant/models/{mid}/activate").status_code == 200

    result = asyncio.run(orchestrator.chat(session_id="job-1", message="汇总今天的待办"))
    assert result["ok"] is False
    assert "定时任务" in result["message"] and "套餐" in result["message"]

    async def _collect():
        return [ev async for ev in orchestrator.chat_stream(session_id="job-1", message="汇总今天的待办")]

    events = asyncio.run(_collect())
    assert any(ev.get("type") == "error" and "套餐" in ev.get("message", "") for ev in events)


def test_job_scope_refusal_iterates_all_internal_prefixes(client, monkeypatch):
    """套餐边界遍历 _INTERNAL_SESSION_PREFIXES 全集：后续新增内部会话前缀同样受约束。"""
    import asyncio

    pid = _mk_provider(client, "智谱交互套餐三", "zhipu_coding", base_url="https://open.bigmodel.cn/api/paas/v4")
    mid = client.post(f"/api/assistant/providers/{pid}/models", json={"model": "glm-5.2"}).json()["model"]["id"]
    assert client.post(f"/api/assistant/models/{mid}/activate").status_code == 200

    monkeypatch.setattr(orchestrator, "_INTERNAL_SESSION_PREFIXES", ("job-", "memory-extract"))
    result = asyncio.run(orchestrator.chat(session_id="memory-extract-42", message="抽取记忆"))
    assert result["ok"] is False and "套餐" in result["message"]


def test_normal_chat_not_blocked_by_plan_scope_guard(client, monkeypatch):
    """普通聊天会话（界面 session_id 不以 job- 开头）不受套餐边界影响。"""
    import asyncio

    pid = _mk_provider(client, "智谱交互套餐二", "zhipu_coding", base_url="https://open.bigmodel.cn/api/paas/v4")
    mid = client.post(f"/api/assistant/providers/{pid}/models", json={"model": "glm-5.2"}).json()["model"]["id"]
    assert client.post(f"/api/assistant/models/{mid}/activate").status_code == 200

    async def fake_ensure_agent(session_id, spec, mode):
        class _Agent:
            async def reply(self, msg):
                class _M:
                    def get_text_content(self):
                        return "正常回复"

                    def get_content_blocks(self):
                        return []

                return _M()

        return _Agent()

    monkeypatch.setattr(orchestrator, "_ensure_agent", fake_ensure_agent)
    result = asyncio.run(orchestrator.chat(session_id="chat-normal-01", message="你好"))
    assert result["ok"] is True
    assert result["reply"] == "正常回复"


def test_get_spec_model_session_cache_dimension(client, monkeypatch):
    """只有要求会话头的供应商按 session 维度缓存模型实例；其余供应商沿用共享缓存。"""
    from app.agents.team import AgentSpec

    calls = []

    def fake_build(cfg=None, session_id=None):
        calls.append(session_id)
        return f"model:{cfg.get('model')}"

    monkeypatch.setattr(orchestrator, "build_model", fake_build)
    orchestrator.invalidate()

    pid_general = _mk_provider(client, "普通供应商", "deepseek")
    mid_general = client.post(f"/api/assistant/providers/{pid_general}/models", json={"model": "m-general"}).json()["model"]["id"]
    spec_general = AgentSpec(name="t_general", label="通用", emoji="", color="", description="", system_prompt="", model_id=mid_general)
    assert orchestrator._get_spec_model(spec_general, session_id="job-1") is orchestrator._get_spec_model(
        spec_general, session_id="job-2"
    )
    assert calls == ["job-1"]  # 共享缓存：两个会话只构建一次

    pid_oc = _mk_provider(client, "OpenCode Go", "opencode_go", base_url="https://opencode.ai/inference/go/openai/v1")
    mid_oc = client.post(f"/api/assistant/providers/{pid_oc}/models", json={"model": "m-oc"}).json()["model"]["id"]
    spec_oc = AgentSpec(name="t_oc", label="OC", emoji="", color="", description="", system_prompt="", model_id=mid_oc)
    m1 = orchestrator._get_spec_model(spec_oc, session_id="job-1")
    m1b = orchestrator._get_spec_model(spec_oc, session_id="job-1")
    m2 = orchestrator._get_spec_model(spec_oc, session_id="job-2")
    assert m1 is m1b and m1 is not m2
    assert calls[-2:] == ["job-1", "job-2"]


def test_ensure_agent_forwards_session_id(client, monkeypatch):
    """_build_agent 接收 session_id 并透传给模型构造（会话头供应商据此保持会话稳定）。"""
    import asyncio

    from app.agents.team import AgentSpec

    monkeypatch.setattr(orchestrator, "build_model", lambda cfg=None, session_id=None: "stub-model")
    seen = {}

    async def fake_build(spec, mode="standard", session_id=None):
        seen["mode"] = mode
        seen["session_id"] = session_id
        return "agent-stub"

    monkeypatch.setattr(orchestrator, "_build_agent", fake_build)
    orchestrator.invalidate()
    spec = AgentSpec(name="t_fwd", label="转发", emoji="", color="", description="", system_prompt="")
    agent = asyncio.run(orchestrator._ensure_agent("job-42", spec, "standard"))
    assert agent == "agent-stub"
    assert seen == {"mode": "standard", "session_id": "job-42"}


# ---------- fix round 1：会话模型缓存 LRU 与预路由守卫 ----------

def test_session_model_cache_lru_evicts(client, monkeypatch):
    """会话模型缓存有上限（LRU）：淘汰最久未用会话，且不持住被淘汰会话的模型引用。"""
    from app.agents.team import AgentSpec

    monkeypatch.setattr(orchestrator, "_SESSION_MODEL_CACHE_MAX", 2)
    monkeypatch.setattr(orchestrator, "build_model", lambda cfg=None, session_id=None: f"model:{session_id}")
    orchestrator.invalidate()

    pid = _mk_provider(client, "OpenCode Go", "opencode_go", base_url="https://opencode.ai/inference/go/openai/v1")
    mid = client.post(f"/api/assistant/providers/{pid}/models", json={"model": "m-oc"}).json()["model"]["id"]
    spec = AgentSpec(name="t_oc", label="OC", emoji="", color="", description="", system_prompt="", model_id=mid)

    orchestrator._get_spec_model(spec, session_id="s1")
    orchestrator._get_spec_model(spec, session_id="s2")
    assert set(orchestrator._session_models) == {(mid, "s1"), (mid, "s2")}
    orchestrator._get_spec_model(spec, session_id="s3")  # 淘汰最久未用的 s1
    assert set(orchestrator._session_models) == {(mid, "s2"), (mid, "s3")}
    assert "model:s1" not in [str(m) for m in orchestrator._session_models.values()]

    # 全局模型的按会话缓存同样受 LRU 约束
    assert client.post(f"/api/assistant/models/{mid}/activate").status_code == 200
    orchestrator.invalidate()
    orchestrator._get_model(session_id="g1")
    orchestrator._get_model(session_id="g2")
    assert set(orchestrator._session_models) == {(0, "g1"), (0, "g2")}
    orchestrator._get_model(session_id="g3")
    assert set(orchestrator._session_models) == {(0, "g2"), (0, "g3")}


def test_session_model_cache_evicts_65th_session(client, monkeypatch):
    """第 65 个会话挤掉第 1 个（上限 64），缓存不随会话数无界增长。"""
    monkeypatch.setattr(orchestrator, "build_model", lambda cfg=None, session_id=None: f"model:{session_id}")
    orchestrator.invalidate()
    pid = _mk_provider(client, "OpenCode Go 三", "opencode_go", base_url="https://opencode.ai/inference/go/openai/v1")
    mid = client.post(f"/api/assistant/providers/{pid}/models", json={"model": "m-oc3"}).json()["model"]["id"]
    assert client.post(f"/api/assistant/models/{mid}/activate").status_code == 200
    for i in range(65):
        orchestrator._get_model(session_id=f"job-{i}")
    assert len(orchestrator._session_models) == 64
    assert (0, "job-0") not in orchestrator._session_models
    assert (0, "job-64") in orchestrator._session_models


def test_session_model_cache_lru_hit_refreshes_recency(client, monkeypatch):
    """真 LRU：命中旧会话要刷新其新近度——命中 s1 后，下一个被淘汰的应是 s2 而不是 s1。"""
    monkeypatch.setattr(orchestrator, "_SESSION_MODEL_CACHE_MAX", 2)
    monkeypatch.setattr(orchestrator, "build_model", lambda cfg=None, session_id=None: f"model:{session_id}")
    orchestrator.invalidate()
    pid = _mk_provider(client, "OpenCode Go LRU", "opencode_go", base_url="https://opencode.ai/inference/go/openai/v1")
    mid = client.post(f"/api/assistant/providers/{pid}/models", json={"model": "m-lru"}).json()["model"]["id"]
    assert client.post(f"/api/assistant/models/{mid}/activate").status_code == 200

    orchestrator._get_model(session_id="s1")
    orchestrator._get_model(session_id="s2")
    orchestrator._get_model(session_id="s1")  # 命中 s1：刷新新近度
    orchestrator._get_model(session_id="s3")  # 应淘汰最久未用的 s2
    assert (0, "s2") not in orchestrator._session_models
    assert (0, "s1") in orchestrator._session_models and (0, "s3") in orchestrator._session_models


def test_session_delete_drops_session_model_cache(client, monkeypatch):
    """删除会话时同步清理其按会话缓存的模型实例。"""
    monkeypatch.setattr(orchestrator, "build_model", lambda cfg=None, session_id=None: f"model:{session_id}")
    orchestrator.invalidate()
    pid = _mk_provider(client, "OpenCode Go 四", "opencode_go", base_url="https://opencode.ai/inference/go/openai/v1")
    mid = client.post(f"/api/assistant/providers/{pid}/models", json={"model": "m-oc4"}).json()["model"]["id"]
    assert client.post(f"/api/assistant/models/{mid}/activate").status_code == 200
    orchestrator._get_model(session_id="job-del")
    assert (0, "job-del") in orchestrator._session_models
    orchestrator.session_delete("job-del")
    assert (0, "job-del") not in orchestrator._session_models


def test_job_guard_allows_explicit_expert_with_general_model(client, monkeypatch):
    """明确指定专家且其绑定 general 模型：预路由全局守卫不误伤；自动路由仍预检全局。"""
    import asyncio

    from app.agents.team import AgentSpec

    pid_plan = _mk_provider(client, "交互套餐", "zhipu_coding", base_url="https://open.bigmodel.cn/api/paas/v4")
    mid_plan = client.post(f"/api/assistant/providers/{pid_plan}/models", json={"model": "glm-5.2"}).json()["model"]["id"]
    assert client.post(f"/api/assistant/models/{mid_plan}/activate").status_code == 200
    pid_general = _mk_provider(client, "按量供应商", "deepseek")
    mid_general = client.post(f"/api/assistant/providers/{pid_general}/models", json={"model": "deepseek-v4"}).json()["model"]["id"]

    spec = AgentSpec(
        name="it_finance", label="财务", emoji="", color="", description="", system_prompt="", model_id=mid_general
    )
    monkeypatch.setattr(orchestrator, "load_team", lambda: {"it_finance": spec})

    async def fake_ensure_agent(session_id, spec, mode):
        class _M:
            def get_text_content(self):
                return "正常回复"

            def get_content_blocks(self):
                return []

        class _Agent:
            async def reply(self, msg):
                return _M()

            async def reply_stream(self, msg, yield_final_msg=True):
                yield _M()

        return _Agent()

    monkeypatch.setattr(orchestrator, "_ensure_agent", fake_ensure_agent)

    # 明确指定专家：路由不会调用全局模型 → 不误伤
    result = asyncio.run(orchestrator.chat(session_id="job-1", message="汇总今天的待办", agent_name="it_finance"))
    assert result["ok"] is True and result["reply"] == "正常回复"

    # 自动路由：路由本身可能调用全局（交互式套餐）模型 → 仍然预检拒绝
    auto = asyncio.run(orchestrator.chat(session_id="job-1", message="汇总今天的待办"))
    assert auto["ok"] is False and "套餐" in auto["message"]

    # 流式同口径
    async def _collect():
        return [ev async for ev in orchestrator.chat_stream(session_id="job-1", message="x", agent_name="it_finance")]

    events = asyncio.run(_collect())
    assert any(ev.get("type") == "done" for ev in events)
    assert "complete_task" not in TEAM["security"].tools
