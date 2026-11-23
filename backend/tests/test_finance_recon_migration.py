# -*- coding: utf-8 -*-
"""finance_recon 老库迁移测试（Task 3 修复轮 2，对应独立复核 C1 / I3 / I2）。

一律临时 legacy SQLite（程序创建，不含任何真实业务数据）：
- 老库结构：mod_recon_batch 无 ever_ready 列、mod_recon_issue 无唯一索引；
- 回填规则（保守）：ready → 1；legacy failed/checking（无法证明未就绪）→ 1；
  draft → 保持 0；幂等：第二次执行零回填、索引不重复；
- 失败（重复指纹）单事务整体回滚（含 ALTER/CREATE DDL），显式中文 MigrationError；
- dry-run / status 只读不写；表缺失的库直接跳过不算失败；
- 与宿主 bootstrap 兼容：manifest column_patches 补列与 ensure_indexes 均幂等。
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.migrate_recon import (  # noqa: E402
    INDEX_NAME,
    MigrationError,
    ensure_column,
    migrate,
    status,
)


@pytest.fixture(autouse=True)
def _ensure_tables():
    """触库用例保证 recon 六表存在（契约：test_test_isolation_contract）。"""
    from sqlalchemy import inspect

    from app.database import Base, engine

    if not inspect(engine).has_table("mod_recon_batch"):
        Base.metadata.create_all(bind=engine)
    yield


_LEGACY_BATCH_DDL = """
CREATE TABLE mod_recon_batch (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT DEFAULT '',
    status TEXT DEFAULT 'draft',
    needs_recheck INTEGER DEFAULT 0,
    updated_at TEXT
)
"""
_LEGACY_ISSUE_DDL = """
CREATE TABLE mod_recon_issue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id INTEGER,
    origin TEXT DEFAULT 'machine',
    fingerprint TEXT DEFAULT '',
    category TEXT DEFAULT '',
    status TEXT DEFAULT 'pending',
    active INTEGER DEFAULT 1
)
"""


def _legacy_db(tmp_path, *, with_duplicates: bool = False, with_tables: bool = True) -> str:
    db_path = tmp_path / "legacy_recon.db"
    conn = sqlite3.connect(str(db_path))
    if with_tables:
        conn.execute(_LEGACY_BATCH_DDL)
        conn.execute(_LEGACY_ISSUE_DDL)
        for name, status_value in (
            ("批次A", "ready"),
            ("批次B", "ready"),
            ("批次C", "failed"),
            ("批次D", "checking"),
            ("批次E", "draft"),
        ):
            conn.execute(
                "INSERT INTO mod_recon_batch (name, status) VALUES (?, ?)", (name, status_value)
            )
        if with_duplicates:
            for _ in range(2):
                conn.execute(
                    "INSERT INTO mod_recon_issue (batch_id, origin, fingerprint) "
                    "VALUES (1, 'machine', 'dup')"
                )
        else:
            conn.execute(
                "INSERT INTO mod_recon_issue (batch_id, origin, fingerprint) "
                "VALUES (1, 'machine', 'fp-1')"
            )
    conn.commit()
    conn.close()
    return str(db_path)


def _ever_ready_by_status(db_path: str) -> dict[str, int]:
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute("SELECT status, ever_ready FROM mod_recon_batch").fetchall()
        return {row[0]: row[1] for row in rows}
    finally:
        conn.close()


def _has_column(db_path: str, column: str) -> bool:
    conn = sqlite3.connect(db_path)
    try:
        cols = [row[1] for row in conn.execute("PRAGMA table_info(mod_recon_batch)")]
        return column in cols
    finally:
        conn.close()


def _index_unique(db_path: str) -> bool | None:
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute("PRAGMA index_list(mod_recon_issue)").fetchall()
        for row in rows:
            if row[1] == INDEX_NAME:
                return bool(row[2])
        return None
    finally:
        conn.close()


def test_migrate_backfills_and_creates_index_idempotent(tmp_path):
    db = _legacy_db(tmp_path)
    summary = migrate(db)
    assert summary["column_added"] is True
    assert summary["backfill"] == {"ready": 2, "failed": 1, "checking": 1}
    assert summary["index_created"] is True
    assert _ever_ready_by_status(db) == {
        "ready": 1, "failed": 1, "checking": 1, "draft": 0,
    }
    assert _has_column(db, "ever_ready") is True
    assert _index_unique(db) is True  # 唯一索引真实生效

    # 第二次执行：零改动（幂等）
    summary2 = migrate(db)
    assert summary2["column_added"] is False
    assert summary2["backfill"] == {"ready": 0, "failed": 0, "checking": 0}
    assert summary2["index_created"] is False
    assert _index_unique(db) is True


def test_migrate_rolls_back_entirely_on_duplicate_fingerprints(tmp_path):
    """重复指纹 → 显式 MigrationError；单事务回滚（列与回填一并撤销）。"""
    db = _legacy_db(tmp_path, with_duplicates=True)
    with pytest.raises(MigrationError) as exc:
        migrate(db)
    assert "重复" in str(exc.value) and "不自动删除" in str(exc.value)
    assert _has_column(db, "ever_ready") is False  # ALTER 已回滚
    assert _index_unique(db) is None  # 唯一索引未创建

    # 人工清理重复后可再次执行成功
    conn = sqlite3.connect(db)
    conn.execute("DELETE FROM mod_recon_issue WHERE fingerprint = 'dup'")
    conn.commit()
    conn.close()
    summary = migrate(db)
    assert summary["column_added"] is True and summary["index_created"] is True
    assert summary["backfill"]["ready"] == 2


def test_dry_run_and_status_are_read_only(tmp_path):
    db = _legacy_db(tmp_path)
    plan = migrate(db, dry_run=True)
    assert plan["dry_run"] is True
    assert plan["backfill"] == {"ready": 2, "failed": 1, "checking": 1}
    assert _has_column(db, "ever_ready") is False  # 未写库
    assert _index_unique(db) is None

    info = status(db)
    assert info["column_ready"] is False and info["index_exists"] is False
    assert info["backfill_plan"]["ready"] == 2

    # status/dry-run 之后正式迁移仍可执行
    migrate(db)
    assert _has_column(db, "ever_ready") is True


def test_tables_missing_is_skipped_not_failure(tmp_path):
    db = _legacy_db(tmp_path, with_tables=False)
    summary = migrate(db)
    assert summary["column_added"] is False
    assert summary["backfill"] == {}
    assert summary["index_created"] is False


def test_ensure_column_direct_idempotent(tmp_path):
    db = _legacy_db(tmp_path)
    conn = sqlite3.connect(db)
    try:
        assert ensure_column(conn) is True
        assert ensure_column(conn) is False  # 已存在：幂等
        conn.commit()
    finally:
        conn.close()
    assert _has_column(db, "ever_ready") is True


def test_bootstrap_layer_compatible_with_migrated_legacy_db(tmp_path):
    """迁移后的老库与宿主启动路径兼容：manifest 补列幂等、唯一索引不重复创建。"""
    from app.bootstrap.schema import ensure_columns, ensure_indexes
    from app.database import Base
    from app.modules import collect_column_patches, load_modules
    import fastapi

    db = _legacy_db(tmp_path)
    migrate(db)

    engine = create_engine(f"sqlite:///{db}")
    try:
        load_modules(fastapi.FastAPI())
        # manifest column_patches：ever_ready 已存在 → 不再 ALTER（幂等）
        ensure_columns(bind=engine, module_patches=collect_column_patches())
        assert _has_column(db, "ever_ready") is True

        # ensure_indexes：唯一索引已在（迁移脚本建）→ 跳过；Base.metadata 里
        # mod_recon_issue 恰好只有一个唯一索引结构（无 UniqueConstraint 重复）
        issue_table = Base.metadata.tables["mod_recon_issue"]
        unique_indexes = [ix for ix in issue_table.indexes if ix.unique]
        assert len(unique_indexes) == 1
        index = unique_indexes[0]
        assert index.name == INDEX_NAME
        created = ensure_indexes(bind=engine, metadata=Base.metadata)
        # 唯一指纹索引已由迁移脚本创建：ensure_indexes 不得重复创建它
        assert f"mod_recon_issue.{INDEX_NAME}" not in created
        assert _index_unique(db) is True
        # 普通二级索引（ix_status 等）在老库缺失属正常补建（幂等设计）
        assert any(name.startswith("mod_recon_issue.") for name in created)
    finally:
        engine.dispose()


def test_same_name_non_unique_index_rejected_with_chinese_reason(tmp_path):
    """同名索引已存在但非唯一：显式中文报错，不删除/不替换既有索引，不做其他改动。"""
    db = _legacy_db(tmp_path)
    conn = sqlite3.connect(db)
    conn.execute("CREATE INDEX uq_recon_issue_fingerprint ON mod_recon_issue (batch_id)")
    conn.commit()
    conn.close()

    with pytest.raises(MigrationError) as exc:
        migrate(db)
    assert "非唯一" in str(exc.value)

    assert _has_column(db, "ever_ready") is False  # 整体回滚：未补列
    conn = sqlite3.connect(db)
    rows = conn.execute("PRAGMA index_list(mod_recon_issue)").fetchall()
    conn.close()
    target = [row for row in rows if row[1] == INDEX_NAME]
    assert len(target) == 1 and target[0][2] == 0  # 原非唯一索引原样保留


def test_same_name_index_wrong_columns_rejected_with_chinese_reason(tmp_path):
    """同名唯一索引但列不符：显式中文报错，不删除/不替换既有索引。"""
    db = _legacy_db(tmp_path)
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE UNIQUE INDEX uq_recon_issue_fingerprint ON mod_recon_issue (fingerprint)"
    )
    conn.commit()
    conn.close()

    with pytest.raises(MigrationError) as exc:
        migrate(db)
    assert "不一致" in str(exc.value)

    assert _has_column(db, "ever_ready") is False
    conn = sqlite3.connect(db)
    cols = [row[2] for row in conn.execute(f"PRAGMA index_info({INDEX_NAME})")]
    conn.close()
    assert cols == ["fingerprint"]  # 原索引原样保留


def test_connect_invalid_path_has_chinese_reason(tmp_path):
    """连接前置校验：不存在 / 目录路径都给中文原因。"""
    from scripts import migrate_recon

    with pytest.raises(MigrationError) as exc:
        migrate_recon._connect(str(tmp_path / "missing.db"))
    assert "不存在" in str(exc.value)

    with pytest.raises(MigrationError) as exc2:
        migrate_recon._connect(str(tmp_path))  # 目录而非文件
    assert "目录而非文件" in str(exc2.value)


def test_connect_sqlite_error_mapped_to_chinese_reason(tmp_path, monkeypatch):
    """sqlite3 打开错误（占用/不可访问）映射为中文 MigrationError。"""
    from scripts import migrate_recon

    db = _legacy_db(tmp_path)  # 先建真实文件，越过 exists 前置校验

    def _boom(*args, **kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(sqlite3, "connect", _boom)
    with pytest.raises(MigrationError) as exc:
        migrate_recon._connect(db)
    assert "无法打开数据库" in str(exc.value)
    assert "locked" in str(exc.value)  # 原始错误信息保留，便于排障
