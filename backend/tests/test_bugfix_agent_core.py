"""agent-core 批次 bugfix 回归测试（严格 TDD：每个用例先于修复编写并亲见失败）。

覆盖：
- B1  只读模式放行带 readonly=True 标记的工具（模块 manifest / MCP 注册），不只认硬编码 QUERY_TOOLS
- B2  技能包启停/新建/删除后失效进程内缓存的 Agent（orchestrator.invalidate）
- B3  删除技能包对非法名称（如 %2E%2E → ".."）返回 400 而非 500
- B4  专家级 skills 非空白名单：只注入名单内技能
- B5  视觉能力校验按路由后专家的绑定模型判定（自动路由时校验在路由之后）
- B6  _agent_locks 随会话删除与 LRU 淘汰一并清理（不再泄漏）
- B7  内部会话（job-* / memory-extract）不进会话列表；memory-extract 不注入历史
- B8  专家 tools=[] 表示真零工具；tools=NULL 表示默认查询工具池
- B9  全部专家停用 → load_team 返回空团队（不回退内置 TEAM）；chat 给明确错误
- B10 GBK 编码文本附件注入不乱码（严格 utf-8 → gbk → replace 三级回退）
"""
import asyncio

import pytest
from fastapi.testclient import TestClient

from app.agents import orchestrator, skill_store
from app.agents import team as team_mod
from app.agents.team import QUERY_TOOLS, AgentSpec
from app.database import Base, SessionLocal, engine
from app.main import app
from app.models import AgentSpecModel

VISION_ERROR = "当前模型不支持图片理解，请在设置中为该模型开启视觉能力或切换视觉模型"


@pytest.fixture(autouse=True)
def _clean_agent_caches():
    """每个用例前后清空编排器进程内缓存与团队缓存，用例间互不影响。"""
    orchestrator.invalidate()
    team_mod.invalidate_team_cache()
    yield
    orchestrator.invalidate()
    team_mod.invalidate_team_cache()


@pytest.fixture()
def skill_store_tmp(tmp_path, monkeypatch):
    """技能包 store 指向临时目录（不写真实 data/skills）。"""
    root = tmp_path / "skills"

    def _store_dir():
        root.mkdir(parents=True, exist_ok=True)
        return root

    monkeypatch.setattr(skill_store, "store_dir", _store_dir)
    return root


@pytest.fixture()
def sclient(skill_store_tmp):
    """带临时技能 store 的 TestClient（建表/清表语义同 conftest 的 client）。"""
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    with TestClient(app) as c:
        yield c
    Base.metadata.drop_all(bind=engine)


@pytest.fixture()
def upload_dir(tmp_path, monkeypatch):
    """上传目录改道临时目录（编排层 _attachments_block 函数内 from ..config 引用 UPLOAD_DIR）。"""
    from app import config as app_config

    target = tmp_path / "uploads"
    target.mkdir()
    monkeypatch.setattr(app_config, "UPLOAD_DIR", target)
    return target


def _mk_provider(client, name="测试供应商", base_url="http://127.0.0.1:9/v1", api_key="sk-x-1234"):
    """创建供应商；base_url 默认指向本机废弃端口，任何真实模型调用都会立即连接失败。"""
    body = client.post(
        "/api/assistant/providers",
        json={"name": name, "protocol": "openai", "base_url": base_url, "api_key": api_key},
    ).json()
    return body["provider"]["id"]


def _mk_model(client, pid, model="test-model", supports_vision=None):
    payload = {"model": model}
    if supports_vision is not None:
        payload["supports_vision"] = supports_vision
    return client.post(f"/api/assistant/providers/{pid}/models", json=payload).json()["model"]["id"]


def _activate(client, mid):
    assert client.post(f"/api/assistant/models/{mid}/activate").status_code == 200
    orchestrator.invalidate()


def _disable_all_experts():
    with SessionLocal() as db:
        for row in db.query(AgentSpecModel).all():
            row.enabled = False
        db.commit()
    team_mod.invalidate_team_cache()


def _write_pack(root, dir_name):
    pack = root / dir_name
    pack.mkdir(parents=True, exist_ok=True)
    (pack / "SKILL.md").write_text(f"---\nname: {dir_name}\ndescription: {dir_name}\n---\n正文", encoding="utf-8")
    return pack


# ---------------- B1：只读模式消费 readonly 标记 ----------------

def test_readonly_mode_keeps_readonly_flagged_tools(client):
    """带 readonly=True 标记的工具（模块/MCP 注册进 TOOL_SPECS）在只读模式下应放行。"""
    from app.agents.tools import TOOL_SPECS

    pid = _mk_provider(client)
    _activate(client, _mk_model(client, pid))

    async def _fake_tool():
        return {"ok": True}

    TOOL_SPECS["zz_b1_readonly"] = {
        "func": _fake_tool, "description": "B1 测试只读工具", "params": {}, "required": [], "readonly": True,
    }
    TOOL_SPECS["zz_b1_write"] = {
        "func": _fake_tool, "description": "B1 测试写入工具", "params": {}, "required": [],
    }
    try:
        spec = AgentSpec(
            name="b1spec", label="B1", emoji="", color="", description="", system_prompt="",
            tools=["query_tasks", "zz_b1_readonly", "zz_b1_write", "create_task"],
        )
        agent = asyncio.run(orchestrator._build_agent(spec, "readonly"))
        names = {s["function"]["name"] for s in asyncio.run(agent.toolkit.get_tool_schemas())}
        assert "query_tasks" in names          # 内置查询工具保留
        assert "zz_b1_readonly" in names       # readonly=True 标记工具放行
        assert "zz_b1_write" not in names      # 无标记写入工具仍被裁掉
        assert "create_task" not in names
    finally:
        TOOL_SPECS.pop("zz_b1_readonly", None)
        TOOL_SPECS.pop("zz_b1_write", None)


# ---------------- B2：技能包变更失效已缓存 Agent ----------------

def test_skills_pack_endpoints_invalidate_agent_cache(sclient):
    """启停/新建/删除技能包成功后，进程内缓存的 Agent 必须失效（技能池可能已变）。"""
    resp = sclient.post("/api/skills/packs", json={"name": "b2-demo", "description": "缓存失效测试"})
    assert resp.status_code == 201

    orchestrator._agents[("sess-b2-1", "realestate", "standard", 0)] = object()
    resp = sclient.put("/api/skills/packs/b2-demo/enabled", json={"enabled": False})
    assert resp.status_code == 200
    assert not orchestrator._agents, "启停技能包后 Agent 缓存未失效"

    orchestrator._agents[("sess-b2-2", "realestate", "standard", 0)] = object()
    resp = sclient.post("/api/skills/packs", json={"name": "b2-demo2", "description": "x"})
    assert resp.status_code == 201
    assert not orchestrator._agents, "新建技能包后 Agent 缓存未失效"

    orchestrator._agents[("sess-b2-3", "realestate", "standard", 0)] = object()
    resp = sclient.delete("/api/skills/packs/b2-demo2")
    assert resp.status_code == 200
    assert not orchestrator._agents, "删除技能包后 Agent 缓存未失效"


# ---------------- B3：删除技能包路由捕 ValueError → 400 ----------------

def test_delete_pack_traversal_name_returns_400(sclient):
    """DELETE /api/skills/packs/%2E%2E（解码为 ".."）：非法名称返回 400 而非 500。"""
    resp = sclient.delete("/api/skills/packs/%2E%2E")
    assert resp.status_code == 400


# ---------------- B4：专家级 skills 非空白名单 ----------------

def test_expert_skills_whitelist_limits_injection(skill_store_tmp):
    """spec.skills=["alpha"] 时只注入名单内技能（与停用过滤同一通道）。"""
    _write_pack(skill_store_tmp, "alpha")
    _write_pack(skill_store_tmp, "beta")

    cfg = {"enable_skills": True, "skills_dir": str(skill_store_tmp)}
    spec = AgentSpec(name="w", label="W", emoji="", color="", description="", system_prompt="", skills=["alpha"])
    loaders = asyncio.run(orchestrator._resolve_skill_loaders(cfg, spec))
    assert loaders, "白名单非空且目录有效时应返回 loader"
    assert {s.name for s in asyncio.run(loaders[0].list_skills())} == {"alpha"}


# ---------------- B5：视觉校验按路由后专家绑定模型 ----------------

def test_auto_route_image_chat_validates_routed_agent_model(client):
    """自动路由带图：按路由后专家的绑定模型判定（全局视觉模型 + 专家绑非视觉模型 → 400）。"""
    pid = _mk_provider(client)
    mid_vision = _mk_model(client, pid, "vision-global", supports_vision=True)
    mid_plain = _mk_model(client, pid, "plain-bound", supports_vision=False)
    _activate(client, mid_vision)

    resp = client.post(
        "/api/assistant/agents",
        json={"name": "b5visbound", "display_name": "视觉绑定专家", "keywords": ["b5zzzvis"], "model_id": mid_plain},
    )
    assert resp.status_code == 201, resp.text

    resp = client.post(
        "/api/assistant/chat",
        json={
            "session_id": "sess-b5-01",
            "message": "b5zzzvis 看这张图",
            "attachments": [{"name": "red.png", "path": "red.png"}],
        },
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == VISION_ERROR


def test_user_content_blocks_follow_spec_bound_model(client):
    """消息内容块的图片注入按路由后专家的绑定模型判定（绑定非视觉模型 → 不注入图片块）。"""
    pid = _mk_provider(client)
    mid_vision = _mk_model(client, pid, "vision-global", supports_vision=True)
    mid_plain = _mk_model(client, pid, "plain-bound", supports_vision=False)
    _activate(client, mid_vision)

    spec = AgentSpec(name="b5spec", label="B5", emoji="", color="", description="", system_prompt="", model_id=mid_plain)
    blocks = orchestrator._user_content_blocks("看图", [{"name": "red.png", "path": "red.png"}], spec)
    assert all(getattr(b, "type", None) != "data" for b in blocks), "绑定非视觉模型时不应注入图片块"
    assert VISION_ERROR in blocks[0].text


# ---------------- B6：_agent_locks 泄漏 ----------------

def test_session_delete_clears_agent_locks(client):
    """删除会话：_agents 与 _agent_locks 同步清理，锁表不得残留。"""
    key = ("sess-b6-01", "realestate", "standard", 0)
    orchestrator.session_create("sess-b6-01", "锁清理")
    orchestrator._agents[key] = object()
    orchestrator._agent_locks[key] = asyncio.Lock()
    orchestrator.session_delete("sess-b6-01")
    assert key not in orchestrator._agents
    assert key not in orchestrator._agent_locks, "会话删除后锁表残留"


def test_agent_cache_lru_eviction_clears_locks():
    """LRU 淘汰 Agent 时对应锁一并清除，避免 _agent_locks 无限增长。"""
    victim = ("sess-b6-victim", "realestate", "standard", 0)
    orchestrator._agent_cache_put(victim, object())
    orchestrator._agent_locks[victim] = asyncio.Lock()
    for i in range(orchestrator._AGENT_CACHE_MAX):
        orchestrator._agent_cache_put((f"sess-b6-{i:03d}", "x", "standard", 0), object())
    assert victim not in orchestrator._agents  # 已被淘汰
    assert victim not in orchestrator._agent_locks, "LRU 淘汰后锁表残留"


# ---------------- B7：内部会话污染 ----------------

def test_sessions_list_excludes_internal_sessions(client):
    """内部会话（定时任务 job-*、记忆抽取 memory-extract）不出现在会话列表。"""
    orchestrator.session_create("job-7", "定时任务执行")
    orchestrator.session_create("memory-extract", "记忆抽取")
    orchestrator.session_create("sess-b7-01", "正常会话")
    ids = [s["session_id"] for s in orchestrator.sessions_list()]
    assert "sess-b7-01" in ids
    assert "job-7" not in ids
    assert "memory-extract" not in ids


def test_memory_extract_session_skips_history_injection(client):
    """memory-extract 固定 id 会话不注入历史（避免上下文反复膨胀）；普通会话不受影响。"""
    orchestrator._persist("memory-extract", "user", "", "第一轮抽取请求", [])
    orchestrator._persist("memory-extract", "assistant", "", "第一轮抽取结果", [])
    assert orchestrator._load_history_msgs("memory-extract") == []

    orchestrator._persist("sess-b7-02", "user", "", "你好", [])
    orchestrator._persist("sess-b7-02", "assistant", "realestate", "你好呀", [])
    assert len(orchestrator._load_history_msgs("sess-b7-02")) == 2


# ---------------- B8：tools 空列表语义 ----------------

def test_tools_empty_list_means_no_tools(client):
    """专家 tools=[]：真零工具（不回退默认查询池）。"""
    with SessionLocal() as db:
        row = AgentSpecModel(name="b8_zero", display_name="零工具专家", tools=[])
        db.add(row)
        db.commit()
        db.refresh(row)
        spec = team_mod._spec_from_row(row)
    assert spec.tools == []


def test_tools_null_means_default_query_tools(client):
    """专家 tools=NULL：默认查询工具池（现状保持，回归守卫）。"""
    with SessionLocal() as db:
        row = AgentSpecModel(name="b8_null", display_name="默认工具专家", tools=None)
        db.add(row)
        db.commit()
        db.refresh(row)
        spec = team_mod._spec_from_row(row)
    assert spec.tools == list(QUERY_TOOLS)


# ---------------- B9：全部专家停用 ----------------

def test_load_team_all_disabled_returns_empty_team(client):
    """有行但全部停用 → 空团队（用户显式配置），不回退内置 TEAM。"""
    _disable_all_experts()
    assert team_mod.load_team(force=True) == {}


def test_load_team_empty_table_falls_back_to_builtin(client):
    """表为空（无数据）→ 回退内置 TEAM（离线/异常兜底，现状保持，回归守卫）。"""
    with SessionLocal() as db:
        db.query(AgentSpecModel).delete()
        db.commit()
    team_mod.invalidate_team_cache()
    team = team_mod.load_team(force=True)
    assert "realestate" in team


def test_chat_with_empty_team_returns_clear_error(client):
    """全部专家停用时对话（非流式）：明确错误提示而不是静默回退内置团队/崩溃。"""
    pid = _mk_provider(client)
    _activate(client, _mk_model(client, pid))
    _disable_all_experts()
    result = asyncio.run(orchestrator.chat("sess-b9-01", "你好"))
    assert result["ok"] is False
    assert "专家" in result["message"]


def test_chat_stream_with_empty_team_yields_error_event(client):
    """全部专家停用时对话（流式）：首事件即明确错误，不出 meta/不崩溃。"""
    pid = _mk_provider(client)
    _activate(client, _mk_model(client, pid))
    _disable_all_experts()

    async def _collect():
        return [ev async for ev in orchestrator.chat_stream("sess-b9-02", "你好")]

    events = asyncio.run(_collect())
    assert events[0]["type"] == "error"
    assert "专家" in events[0]["message"]


# ---------------- B10：GBK 文本附件解码 ----------------

def test_attachments_block_decodes_gbk_chinese(upload_dir):
    """GBK 编码的中文文本附件注入后无乱码；UTF-8 附件照常（回归）。"""
    d = upload_dir / "sess-b10-01"
    d.mkdir()
    gbk_file = d / "报表.txt"
    gbk_file.write_bytes("租金收缴率：98.5%，同比上升".encode("gbk"))
    block = orchestrator._attachments_block([{"name": "报表.txt", "path": str(gbk_file)}])
    assert "租金收缴率：98.5%" in block
    assert "\ufffd" not in block

    utf8_file = d / "说明.txt"
    utf8_file.write_text("这是一段 UTF-8 中文", encoding="utf-8")
    block2 = orchestrator._attachments_block([{"name": "说明.txt", "path": str(utf8_file)}])
    assert "这是一段 UTF-8 中文" in block2
