"""模块注册机制（Module Registry）测试。

覆盖：模块发现、manifest 校验（坏 manifest 被跳过且不崩宿主）、路由挂载、
模块建表与读写、column_patches 合并、register_module_tools 生效、
build_tools 容错未知工具名、财税专员 seed 行为（只灌空表）。
"""
from fastapi import FastAPI
from sqlalchemy import text

from app.agents import team as team_mod
from app.agents.tools import TOOL_SPECS, build_tools
from app.database import SessionLocal, engine
from app.models import AgentSpecModel
from app.modules import collect_column_patches, discover_module_names, get_loaded_modules
from app.modules import registry as registry_mod
from app.modules.spec import ModuleManifest
from app.seed import seed_agent_specs


# ---------- 模块发现 ----------

def test_discover_module_names():
    names = discover_module_names()
    assert "finance_hello" in names
    # _ 开头目录（模板）不视为模块
    assert "_template" not in names
    assert all(not n.startswith("_") for n in names)


def test_hello_module_loaded():
    loaded = {m.name: m for m in get_loaded_modules()}
    assert "finance_hello" in loaded
    manifest = loaded["finance_hello"]
    assert manifest.prefix == "/api/finance/hello"
    assert manifest.tables == ["mod_hello_entry"]
    assert manifest.menu["group"] == "财务" and manifest.menu["order"] == 99


# ---------- manifest 校验 ----------

def test_manifest_validate_rules():
    good = ModuleManifest(name="finance_x", prefix="/api/finance/x", tables=["mod_x_entry"], menu={"group": "财务"})
    assert good.validate() == []
    bad = ModuleManifest(
        name="finance_bad",
        prefix="/api/other/",                    # 前缀不合法
        tables=["plain_table"],                  # 表名缺 mod_ 前缀
        menu="not-a-dict",                       # menu 类型错误
        agent_tools=[{"name": "t"}],             # 缺 description/readonly
        requires="finance_x",                    # requires 类型错误
    )
    errors = bad.validate()
    assert len(errors) >= 4


def test_bad_module_skipped_without_crash(monkeypatch):
    """模块导入/校验失败只记日志跳过，绝不抛异常拖垮宿主。"""
    monkeypatch.setattr(registry_mod, "discover_module_names", lambda: ["ghost_no_such_module"])
    loaded = registry_mod.load_modules(FastAPI())
    assert loaded == []
    # 恢复后重新发现，finance_hello 仍可正常加载
    monkeypatch.undo()
    loaded = registry_mod.load_modules(FastAPI())
    assert "finance_hello" in {m.name for m in loaded}


def test_module_dependency_missing_skipped(monkeypatch):
    """requires 依赖未加载时该模块跳过，且不崩。"""
    dep = ModuleManifest(name="finance_dep", prefix="/api/finance/dep", requires=["finance_missing"])
    monkeypatch.setattr(registry_mod, "discover_module_names", lambda: ["finance_dep"])
    monkeypatch.setattr(registry_mod, "_load_manifest", lambda name: dep)
    loaded = registry_mod.load_modules(FastAPI())
    assert loaded == []
    monkeypatch.undo()
    registry_mod.load_modules(FastAPI())


# ---------- 路由挂载与建表 ----------

def test_hello_ping_mounted(client):
    resp = client.get("/api/finance/hello/ping")
    assert resp.status_code == 200
    assert resp.json() == {"module": "finance_hello", "ok": True}


def test_module_table_created_and_writable(client):
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' AND name='mod_hello_entry'")
        ).first()
    assert row is not None
    # 表可读写：经模块接口走一轮
    resp = client.post("/api/finance/hello/entries", json={"name": "机制实证"})
    assert resp.status_code == 201
    entry = resp.json()
    assert entry["name"] == "机制实证" and entry["id"] >= 1
    items = client.get("/api/finance/hello/entries").json()
    assert any(i["name"] == "机制实证" for i in items)


# ---------- 轻量迁移补丁合并 ----------

def test_collect_column_patches_merge(monkeypatch):
    fake = ModuleManifest(
        name="finance_fake",
        prefix="/api/finance/fake",
        column_patches={"mod_fake_t": {"extra_col": "TEXT DEFAULT ''"}},
    )
    monkeypatch.setattr(registry_mod, "_loaded", registry_mod.get_loaded_modules() + [fake])
    merged = collect_column_patches()
    assert merged["mod_fake_t"]["extra_col"] == "TEXT DEFAULT ''"


# ---------- Agent 工具注册与容错 ----------

def test_register_module_tools_effective(client):
    # conftest 导入 app.main 时已执行 load_modules，模块工具应已并入注册表
    assert "hello_ping" in TOOL_SPECS
    spec = TOOL_SPECS["hello_ping"]
    assert spec["readonly"] is True
    assert callable(spec["func"])
    # client fixture 已建表，工具真实可查
    assert spec["func"]()["module"] == "finance_hello"


def test_build_tools_skips_unknown_names(caplog):
    """白名单引用未注册工具（如模块被禁用）时跳过并记日志，不崩。"""
    import logging

    with caplog.at_level(logging.WARNING):
        tools = build_tools(["query_tasks", "ghost_tool_from_disabled_module", "hello_ping"])
    assert len(tools) == 2
    assert any("ghost_tool_from_disabled_module" in r.message for r in caplog.records)


# ---------- 财税专员 seed 行为 ----------

def test_finance_specialist_seeded_on_fresh_db(client):
    """空库启动时灌入的内置专家包含 finance_specialist。"""
    agents = client.get("/api/assistant/agents").json()["agents"]
    by_name = {a["name"]: a for a in agents}
    assert "finance_specialist" in by_name
    spec = by_name["finance_specialist"]
    assert spec["is_builtin"] is True
    assert set(spec["keywords"]) >= {"发票", "凭证", "台账"}
    assert "hello_ping" in spec["tools"]


def test_seed_agent_specs_backfills_missing_builtins(client):
    """seed_agent_specs 增量 upsert：老库缺内置专家时补齐，自定义专家不被覆盖/删除。

    （v1.13 起语义从「只灌空表」改为「缺谁补谁」——升级后新增的内置专家自动出现，
    与 zcode/WorkBuddy 插件「安装即添加 agent」一致。）
    """
    with SessionLocal() as db:
        # 模拟老库：清掉内置专家，只留一个自定义专家（表非空）
        db.query(AgentSpecModel).delete()
        db.add(AgentSpecModel(name="my_custom", display_name="自定义专家", sort=0))
        db.commit()
        assert seed_agent_specs(db) == 8  # 8 位内置专家全部补齐
        names = {r.name for r in db.query(AgentSpecModel).all()}
        assert "my_custom" in names and "finance_specialist" in names
        # 幂等：再次 seed 不重复
        assert seed_agent_specs(db) == 0
    team_mod.invalidate_team_cache()


def test_module_tools_preset_into_finance_specialist(client):
    """模块工具预置：启动后财税专员白名单包含各财务模块的 agent_tools（幂等合并）。"""
    from app.agents import team as team_mod
    from app.agents.module_tool_preset import FINANCE_TOOL_NAMES

    team = team_mod.load_team(force=True)
    fs = team["finance_specialist"]
    missing = [t for t in FINANCE_TOOL_NAMES if t not in fs.tools]
    assert not missing, f"财税专员缺少预置工具：{missing}"

    # 手工移除一个预置工具后，重新应用预置应补回（预置=模块声明的最小集）
    with SessionLocal() as db:
        row = db.query(AgentSpecModel).filter(AgentSpecModel.name == "finance_specialist").one()
        row.tools = [t for t in (row.tools or []) if t != "vault_query"]
        db.commit()
    team_mod.invalidate_team_cache()
    from app.modules import apply_pending_presets

    assert apply_pending_presets() >= 1
    team = team_mod.load_team(force=True)
    assert "vault_query" in team["finance_specialist"].tools


# ---------- agent_tools 结构校验 ----------

def test_manifest_validate_agent_tools_structure():
    """agent_tools 条目须为 dict 且含 name/description/func/params/required/readonly，
    func 可调用、params 为 dict、required 为 list——残缺 spec 在加载期即被检出。"""
    base = dict(name="finance_x", prefix="/api/finance/x", tables=["mod_x_entry"], menu={})
    ok_tool = {"name": "t", "description": "d", "readonly": True,
               "func": lambda: None, "params": {}, "required": []}
    assert ModuleManifest(**base, agent_tools=[ok_tool]).validate() == []

    broken = [
        {"name": "t", "description": "d", "readonly": True},      # 缺 func/params/required
        {**ok_tool, "func": "not-callable"},                      # func 不可调用
        {**ok_tool, "params": "not-a-dict"},                      # params 类型错误
        {**ok_tool, "required": "not-a-list"},                    # required 类型错误
        "not-a-dict",                                             # 条目本身不是 dict
    ]
    for tool in broken:
        errors = ModuleManifest(**base, agent_tools=[tool]).validate()
        assert errors, f"残缺 agent_tools 未被检出：{tool!r}"


# ---------- 工具注册/预置阶段异常隔离 ----------

def test_module_tool_stage_failure_does_not_crash_host(monkeypatch, caplog):
    """register_module_tools / _preset_expert_tools 抛异常：仅记日志跳过对应动作，
    模块本体（路由）仍正常挂载，宿主不崩。"""
    import logging

    from fastapi.testclient import TestClient

    fake = ModuleManifest(
        name="finance_hello",
        prefix="/api/finance/hello",
        tables=["mod_hello_entry"],
        agent_tools=[{"name": "hello_ping_probe", "description": "d", "readonly": True,
                      "func": lambda: {"ok": True}, "params": {}, "required": []}],
        preset_experts=["finance_specialist"],
    )
    monkeypatch.setattr(registry_mod, "discover_module_names", lambda: ["finance_hello"])
    monkeypatch.setattr(registry_mod, "_load_manifest", lambda name: fake)

    def _boom_register(specs):
        raise RuntimeError("模拟注册表损坏")

    def _boom_preset(manifest):
        raise RuntimeError("模拟预置写库异常")

    monkeypatch.setattr(registry_mod, "register_module_tools", _boom_register)
    monkeypatch.setattr(registry_mod, "_preset_expert_tools", _boom_preset)

    app = FastAPI()
    with caplog.at_level(logging.ERROR):
        loaded = registry_mod.load_modules(app)
    # 工具注册/预置失败不拖垮模块：路由仍挂载、模块仍计入已加载
    assert [m.name for m in loaded] == ["finance_hello"]
    assert TestClient(app).get("/api/finance/hello/ping").status_code == 200
    assert any("工具注册失败" in r.message for r in caplog.records)
    assert any("工具预置失败" in r.message for r in caplog.records)

    # 恢复后重新加载，真实模块清单不受影响
    monkeypatch.undo()
    loaded = registry_mod.load_modules(FastAPI())
    assert "finance_hello" in {m.name for m in loaded}
