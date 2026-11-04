"""Task 1 迁移、停机、专家墓碑与核心路由契约回归。"""
import asyncio
import sys
from datetime import datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from app import main as main_mod
from app import scheduler
from app.database import Base, SessionLocal, engine
from app.models import AgentSpecModel, ScheduledJob
from app.seed import seed_agent_specs


@pytest.fixture(autouse=True)
def _ensure_tables():
    Base.metadata.create_all(bind=engine)
    yield


def test_sqlite_column_migration_failure_is_isolated_per_column(monkeypatch, tmp_path):
    """回归目标：所有列共用事务时，alpha 的非法 DDL 会阻断 beta 补列。"""
    isolated = create_engine(f"sqlite:///{(tmp_path / 'migration.db').as_posix()}")
    with isolated.begin() as conn:
        conn.execute(text("CREATE TABLE alpha (id INTEGER PRIMARY KEY)"))
        conn.execute(text("CREATE TABLE beta (id INTEGER PRIMARY KEY)"))
    monkeypatch.setattr(main_mod, "engine", isolated)
    monkeypatch.setattr(main_mod, "collect_column_patches", lambda: {
        "alpha": {"broken": "NOT VALID DDL"},
        "beta": {"added": "TEXT DEFAULT ''"},
    })
    try:
        main_mod._ensure_sqlite_columns()
        with isolated.connect() as conn:
            columns = {row[1] for row in conn.execute(text("PRAGMA table_info(beta)"))}
        assert "added" in columns
    finally:
        isolated.dispose()


def test_sqlite_column_migration_is_dialect_gated(monkeypatch):
    """回归目标：非 SQLite 若仍进入 begin/PRAGMA，calls 会被置 1。"""
    calls = []

    class Dialect:
        name = "postgresql"

    class NonSQLite:
        dialect = Dialect()

        def begin(self):
            calls.append(1)
            raise AssertionError("非 SQLite 不应开始迁移事务")

    monkeypatch.setattr(main_mod, "engine", NonSQLite())
    main_mod._ensure_sqlite_columns()
    assert calls == []


def test_init_db_reset_registers_module_tables_before_drop(tmp_path):
    """回归目标：独立 CLI 不加载模块时，reset 会遗漏模块表并保留旧行。"""
    import os
    import sqlite3
    import subprocess
    from pathlib import Path

    database = tmp_path / "init.db"
    with sqlite3.connect(database) as conn:
        conn.execute("CREATE TABLE mod_fac_equipment (id INTEGER PRIMARY KEY)")
        conn.execute("INSERT INTO mod_fac_equipment (id) VALUES (1)")
    env = os.environ.copy()
    env.update({
        "DATABASE_URL": f"sqlite:///{database.as_posix()}",
        "WORKBENCH_ENV_FILE": "",
        "SEED_ON_STARTUP": "0",
        "PYTHONIOENCODING": "utf-8",
    })
    completed = subprocess.run(
        [sys.executable, "-m", "app.init_db", "--reset"],
        cwd=Path(__file__).resolve().parents[1], env=env,
        capture_output=True, text=True, encoding="utf-8", timeout=60, check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    with sqlite3.connect(database) as conn:
        count = conn.execute("SELECT COUNT(*) FROM mod_fac_equipment").fetchone()[0]
        agents = conn.execute("SELECT COUNT(*) FROM agent_specs").fetchone()[0]
    assert count == 0
    assert agents == 8


def test_init_db_reset_replays_loaded_module_presets(tmp_path):
    """回归目标：load_modules 写入旧专家行的预置在 drop 后必须经 loaded presets 重放。"""
    import json
    import os
    import sqlite3
    import subprocess
    from pathlib import Path
    from sqlalchemy.orm import sessionmaker

    database = tmp_path / "preset-reset.db"
    isolated = create_engine(f"sqlite:///{database.as_posix()}")
    Base.metadata.create_all(bind=isolated)
    isolated_sessions = sessionmaker(bind=isolated, expire_on_commit=False)
    with isolated_sessions() as db:
        seed_agent_specs(db)
        row = db.query(AgentSpecModel).filter_by(name="finance_specialist").one()
        row.tools = []
        db.commit()
    isolated.dispose()

    env = os.environ.copy()
    env.update({
        "DATABASE_URL": f"sqlite:///{database.as_posix()}",
        "WORKBENCH_ENV_FILE": "",
        "SEED_ON_STARTUP": "0",
        "PYTHONIOENCODING": "utf-8",
    })
    completed = subprocess.run(
        [sys.executable, "-m", "app.init_db", "--reset"],
        cwd=Path(__file__).resolve().parents[1], env=env,
        capture_output=True, text=True, encoding="utf-8", timeout=60, check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    with sqlite3.connect(database) as conn:
        raw_tools = conn.execute(
            "SELECT tools FROM agent_specs WHERE name='finance_specialist'"
        ).fetchone()[0]
    assert "hello_ping" in json.loads(raw_tools)


def test_builtin_agent_delete_leaves_tombstone_until_reset(client):
    """回归目标：无墓碑时，API 删除后 seed 会立即复活内置专家。"""
    agents = client.get("/api/assistant/agents").json()["agents"]
    victim = next(row for row in agents if row["name"] == "leasing")
    assert client.delete(f"/api/assistant/agents/{victim['id']}").status_code == 200
    with SessionLocal() as db:
        assert seed_agent_specs(db) == 0
        assert db.query(AgentSpecModel).filter_by(name="leasing").first() is None
    assert client.post("/api/assistant/agents/reset").status_code == 200
    restored = client.get("/api/assistant/agents").json()["agents"]
    assert any(row["name"] == "leasing" for row in restored)


def _isolate_lifespan(monkeypatch, events):
    from app.agents import mcp

    async def fake_scheduler_loop():
        await asyncio.Event().wait()

    async def fake_shutdown():
        events.append("scheduler")

    monkeypatch.setattr(main_mod, "scheduler_loop", fake_scheduler_loop)
    monkeypatch.setattr(main_mod, "scheduler_shutdown", fake_shutdown, raising=False)
    monkeypatch.setattr(main_mod, "ensure_dpapi_available", lambda: None)
    monkeypatch.setattr(mcp, "start_all_from_db", lambda: {})
    monkeypatch.setattr(mcp, "migrate_env_secrets", lambda db: 0)
    monkeypatch.setattr(mcp, "stop_all", lambda: events.append("mcp"))
    monkeypatch.setattr(main_mod.secrets_box, "migrate_seal_keys", lambda: 0)


def test_lifespan_shutdown_awaits_scheduler_before_mcp(monkeypatch):
    """回归目标：lifespan 若不调用 shutdown，events 只有 mcp。"""
    events = []
    _isolate_lifespan(monkeypatch, events)
    with TestClient(main_mod.app):
        pass
    assert events == ["scheduler", "mcp"]


def test_lifespan_normalizes_legacy_jobs(monkeypatch):
    """回归目标：仅提供归一化函数但启动不调用时，脏行仍保持 enabled。"""
    events = []
    _isolate_lifespan(monkeypatch, events)
    with SessionLocal() as db:
        db.add(ScheduledJob(
            id=920100, name="Task1-启动归一化", prompt="x", schedule_type="cron",
            cron_expr="", enabled=True, created_at=datetime(2026, 1, 1),
        ))
        db.commit()
    try:
        with TestClient(main_mod.app):
            pass
        with SessionLocal() as db:
            assert db.get(ScheduledJob, 920100).enabled is False
    finally:
        with SessionLocal() as db:
            db.query(ScheduledJob).filter_by(id=920100).delete()
            db.commit()


def test_origin_guard_error_names_cors_configuration():
    """回归目标：403 只有通用文案时，用户不知道需同时配置 CORS_ORIGINS。"""
    mini = FastAPI()
    mini.add_middleware(main_mod.OriginGuardMiddleware)

    @mini.post("/api/probe")
    def probe():
        return {"ok": True}

    response = TestClient(mini).post("/api/probe", headers={"Origin": "http://evil.example"})
    assert response.status_code == 403
    assert "CORS_ORIGINS" in response.json()["detail"]


def test_business_overflow_is_not_misreported_as_an_id():
    """回归目标：无数字路径参数的业务 OverflowError 必须继续抛出而非变成 ID 422。"""
    mini = FastAPI()
    mini.add_exception_handler(OverflowError, main_mod._overflow_error_as_422)

    @mini.get("/api/business-overflow")
    def probe():
        raise OverflowError("business overflow")

    with pytest.raises(OverflowError, match="business overflow"):
        TestClient(mini).get("/api/business-overflow")


def test_knowledge_ids_use_consistent_validation_errors(client):
    """回归目标：裸 int 的越界 ID 走全局字符串 detail，而 DbId 返回标准 422 数组。"""
    big = 2**63
    for method in ("PUT", "DELETE"):
        response = client.request(
            method, f"/api/knowledge/memories/{big}",
            json={"content": "合成"} if method == "PUT" else None,
        )
        assert response.status_code == 422
        assert isinstance(response.json()["detail"], list)


def test_search_total_counts_matches_beyond_five_returned_rows(client):
    """回归目标：total 若只累加 limit(5) 的结果，6 条命中会错误返回 5。"""
    for index in range(6):
        assert client.post("/api/tasks", json={"title": f"统一检索词-{index}"}).status_code == 201
    body = client.get("/api/search", params={"q": "统一检索词"}).json()
    assert len(body["tasks"]) == 5
    assert body["total"] == 6


def test_snippet_indices_survive_unicode_lowercase_expansion():
    """回归目标：lower() 把 İ 展开为两个码点，旧索引会偏移并漏掉关键词。"""
    from app.routers.search import _snippet

    text = "İ" * 20 + "abc" + "Z" * 100
    assert "abc" in _snippet(text, "abc")


def test_upload_mojibake_repair_roundtrip_characterization():
    """表征现有可逆行为：合法 GBK 乱码可修复，普通 Latin-1 文件名不变。"""
    from app.utils import repair_upload_mojibake

    original = "辅助明细账.xlsx"
    mangled = original.encode("gbk").decode("latin-1")
    assert repair_upload_mojibake(mangled) == original
    assert repair_upload_mojibake("café.xlsx") == "café.xlsx"
