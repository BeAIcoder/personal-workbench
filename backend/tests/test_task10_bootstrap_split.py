"""2026-11-17 启动/迁移拆分（#10）：lifespan 只做编排，迁移实现可复用、可显式执行。

覆盖四类契约：
- 启动幂等：连续两次启动不得改表/索引 DDL，也不得重复灌种子；
- 机械拆分兼容：main.py 仍按原名 re-export bootstrap 的实现（老脚本/测试不改调用）；
- 失败策略：可降级项只告警，必需项（模块工具预置）fail-fast；
- 一次性迁移可显式执行：scripts/migrate.py 子进程建表/补列幂等，status 只读。
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, text

from app import bootstrap
from app import main as main_mod
from app.database import Base, SessionLocal, engine

BACKEND = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _ensure_tables():
    Base.metadata.create_all(bind=engine)
    yield


def _schema_snapshot() -> list:
    with engine.connect() as conn:
        return conn.execute(
            text("SELECT type, name, sql FROM sqlite_master ORDER BY type, name")
        ).fetchall()


def _agent_count() -> int:
    with engine.connect() as conn:
        return conn.execute(text("SELECT COUNT(*) FROM agent_specs")).scalar()


# ---------- 启动幂等 ----------

def test_lifespan_start_twice_is_idempotent():
    """回归目标：启动块若每次都重建/重复灌种子，第二次启动的 schema 或行数会变。"""
    with TestClient(main_mod.app):
        pass
    first_schema = _schema_snapshot()
    first_agents = _agent_count()
    with TestClient(main_mod.app):
        pass
    assert _schema_snapshot() == first_schema, "二次启动不得改表/索引 DDL"
    assert _agent_count() == first_agents, "二次启动不得重复灌内置专家"


# ---------- 机械拆分兼容（re-export 契约） ----------

def test_main_reexports_bootstrap_schema_functions():
    """回归目标：拆走后旧私有名消失，scripts/sync_zcode_models.py 与既有测试会 ImportError。"""
    assert main_mod._ensure_indexes_on is bootstrap.ensure_indexes
    assert main_mod._duplicate_sample is bootstrap.duplicate_sample
    assert main_mod._drop_obsolete_indexes is bootstrap.drop_obsolete_indexes
    assert main_mod._ensure_active_contract_index is bootstrap.ensure_active_contract_index
    assert callable(main_mod._ensure_sqlite_columns)
    assert callable(main_mod._ensure_sqlite_indexes)


# ---------- 失败策略 ----------

def test_degradable_seed_step_only_warns(monkeypatch, caplog):
    """可降级项（知识库索引）失败必须只告警，不能拦住启动。"""
    def boom():
        raise RuntimeError("合成知识库索引失败")

    monkeypatch.setattr("app.knowledge.index.ensure_schema", boom)
    with caplog.at_level(logging.WARNING):
        bootstrap.seeds.ensure_knowledge_index()
    assert "知识库索引初始化跳过" in caplog.text


def test_required_preset_step_fails_fast(monkeypatch):
    """必需项（模块工具预置）失败必须抛出，不能静默缺工具。"""
    def boom():
        raise RuntimeError("合成预置失败")

    monkeypatch.setattr("app.modules.apply_pending_presets", boom)
    with pytest.raises(RuntimeError, match="合成预置失败"):
        bootstrap.seeds.apply_module_presets()


# ---------- 一次性秘密迁移：脚本入口幂等、启动只读 ----------

def test_legacy_migration_seals_plaintext_key_and_is_idempotent():
    from app.models import ModelProvider

    name = "合成-迁移供应商"
    with SessionLocal() as db:
        db.add(ModelProvider(name=name, api_key="合成明文-key-1"))
        db.commit()
    try:
        first = bootstrap.run_legacy_migrations()
        assert first["provider_keys"] >= 1
        with SessionLocal() as db:
            row = db.query(ModelProvider).filter_by(name=name).one()
            assert row.api_key.startswith(bootstrap.legacy.SEALED_PREFIX)
        second = bootstrap.run_legacy_migrations()
        assert second["provider_keys"] == 0, "迁移必须幂等：已密封的 Key 不再处理"
    finally:
        with SessionLocal() as db:
            db.query(ModelProvider).filter_by(name=name).delete()
            db.commit()


def test_pending_secrets_probe_does_not_leak_values(caplog):
    """启动告警只做 COUNT：捕获的 SQL 参数与日志里都不得出现密钥原文。"""
    from app.models import AgentSettings

    secret = "合成-探测明文-5521"
    with SessionLocal() as db:
        row = db.get(AgentSettings, 1) or AgentSettings(id=1)
        row.api_key = secret
        db.add(row)
        db.commit()

    captured = []

    def _capture(conn, cursor, statement, parameters, context, executemany):
        captured.append((statement, parameters))

    event.listen(engine, "before_cursor_execute", _capture)
    try:
        with caplog.at_level(logging.DEBUG):
            counts = bootstrap.report_pending_secrets()
    finally:
        event.remove(engine, "before_cursor_execute", _capture)
        with SessionLocal() as db:
            db.get(AgentSettings, 1).api_key = ""
            db.commit()

    assert counts["agent_settings_keys"] >= 1
    assert "启动不再自动迁移" in caplog.text
    assert secret not in json.dumps([p for _, p in captured], ensure_ascii=False, default=str)
    assert secret not in caplog.text


# ---------- scripts/migrate.py 子进程 ----------

def _run_migrate(action: str, database: Path) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env.update({
        "DATABASE_URL": f"sqlite:///{database.as_posix()}",
        "WORKBENCH_DATA_DIR": str(database.parent / "data"),
        "FINANCE_DATA_DIR": str(database.parent / "finance"),
        "DEBUG_DATA_DIR": str(database.parent / "debug"),
        "SEED_ON_STARTUP": "0",
        "WORKBENCH_ENV_FILE": "",
        "PYTHONIOENCODING": "utf-8",
    })
    return subprocess.run(
        [sys.executable, str(BACKEND / "scripts" / "migrate.py"), action],
        cwd=str(BACKEND), env=env,
        capture_output=True, text=True, encoding="utf-8", timeout=180, check=False,
    )


def test_migrate_script_schema_creates_module_tables_and_is_idempotent(tmp_path):
    database = tmp_path / "migrate.db"
    first = _run_migrate("schema", database)
    assert first.returncode == 0, first.stdout + first.stderr
    second = _run_migrate("schema", database)
    assert second.returncode == 0, second.stdout + second.stderr
    with sqlite3.connect(database) as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        columns = {row[1] for row in conn.execute("PRAGMA table_info(provider_models)")}
    assert {"tasks", "agent_specs", "mod_fac_equipment"} <= tables, "脚本必须加载模块表"
    assert {"max_input_tokens", "capability_meta"} <= columns, "脚本必须补齐后加列"


def test_migrate_script_schema_backfills_legacy_table(tmp_path):
    database = tmp_path / "legacy.db"
    with sqlite3.connect(database) as conn:
        conn.execute(
            "CREATE TABLE model_providers (id INTEGER PRIMARY KEY, name VARCHAR(50) NOT NULL,"
            " protocol VARCHAR(20) DEFAULT 'openai', base_url VARCHAR(300) DEFAULT '',"
            " api_key VARCHAR(300) DEFAULT '', enabled BOOLEAN DEFAULT 1,"
            " created_at DATETIME, updated_at DATETIME)"
        )
        conn.execute("INSERT INTO model_providers (name) VALUES ('合成老供应商')")
        conn.commit()

    result = _run_migrate("schema", database)
    assert result.returncode == 0, result.stdout + result.stderr
    with sqlite3.connect(database) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(model_providers)")}
        preset_key = conn.execute("SELECT preset_key FROM model_providers").fetchone()[0]
    assert {"preset_key", "models_url", "sync_credentials"} <= columns
    assert preset_key == "", "补列必须给历史行落默认值"


def test_migrate_script_status_is_read_only(tmp_path):
    database = tmp_path / "status.db"
    assert _run_migrate("schema", database).returncode == 0
    with sqlite3.connect(database) as conn:
        before = conn.execute("SELECT COUNT(*) FROM model_providers").fetchone()[0]
    result = _run_migrate("status", database)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "未迁移明文秘密统计" in result.stdout
    assert "provider_keys 0 条" in result.stdout
    with sqlite3.connect(database) as conn:
        after = conn.execute("SELECT COUNT(*) FROM model_providers").fetchone()[0]
    assert after == before, "status 必须只读"
