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


def test_seed_agent_specs_only_fills_empty_table(client):
    """seed_agent_specs 只灌空表：已有数据的库（如 v1.7 老库）不受新内置专家影响。"""
    with SessionLocal() as db:
        # 模拟老库：清掉内置专家，只留一个自定义专家（表非空）
        db.query(AgentSpecModel).delete()
        db.add(AgentSpecModel(name="my_custom", display_name="自定义专家", sort=0))
        db.commit()
        assert seed_agent_specs(db) == 0
        names = {r.name for r in db.query(AgentSpecModel).all()}
        assert names == {"my_custom"}  # finance_specialist 未被灌入
    team_mod.invalidate_team_cache()
