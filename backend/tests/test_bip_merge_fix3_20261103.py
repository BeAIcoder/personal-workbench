"""BIP Task 4 Fix round 3：索引迁移必须「新名先建成功 → 再 DROP 旧名」。

复核 Important：SQLite 的 DDL 在 `engine.begin()` 下不保证真正事务化，
旧实现先 DROP 旧索引、再 CREATE 新索引，CREATE 失败会丢旧索引。
本文件强制 CREATE 失败，断言旧索引仍在、数据 0 删除；并验证失败后重试可完成替换。
"""
from __future__ import annotations

import pytest
from sqlalchemy import Index, create_engine, inspect, text

from app import main as app_main
from app.database import Base

OLD_INDEX = "uq_bip_balance_period_subject"
NEW_INDEX = "uq_bip_balance_scope"


def _old_balance_db(path):
    """构造历史库：旧唯一索引 (period, subject_code) + 一行数据。"""
    engine = create_engine(f"sqlite:///{path.as_posix()}")
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE mod_bip_balance ("
            "id INTEGER PRIMARY KEY, batch_id INTEGER, period TEXT, subject_code TEXT, "
            "subject_name TEXT, ledger TEXT DEFAULT '', opening_debit FLOAT DEFAULT 0, "
            "opening_credit FLOAT DEFAULT 0, period_debit FLOAT DEFAULT 0, "
            "period_credit FLOAT DEFAULT 0, closing_debit FLOAT DEFAULT 0, "
            "closing_credit FLOAT DEFAULT 0, opening_balance FLOAT DEFAULT 0, "
            "closing_balance FLOAT DEFAULT 0)"
        ))
        conn.execute(text(
            f"CREATE UNIQUE INDEX {OLD_INDEX} ON mod_bip_balance (period, subject_code)"
        ))
        conn.execute(text(
            "INSERT INTO mod_bip_balance (period, subject_code, ledger) "
            "VALUES ('2026-09', '1002', '')"
        ))
    return engine


def test_create_failure_keeps_old_index_and_data(tmp_path, monkeypatch, caplog):
    """强制新索引 CREATE 失败：旧索引仍在、新索引未建、行数不变。"""
    engine = _old_balance_db(tmp_path / "create-fail.db")
    real_create = Index.create

    def failing_create(self, bind=None, checkfirst=False):
        if self.name == NEW_INDEX:
            raise RuntimeError("forced create failure")
        return real_create(self, bind=bind, checkfirst=checkfirst)

    monkeypatch.setattr(Index, "create", failing_create)
    with caplog.at_level("WARNING"):
        created = app_main._ensure_indexes_on(engine, Base.metadata)

    assert not any(NEW_INDEX in c for c in created)
    idx = {i["name"]: i for i in inspect(engine).get_indexes("mod_bip_balance")}
    assert OLD_INDEX in idx  # 旧索引必须保留
    assert idx[OLD_INDEX]["column_names"] == ["period", "subject_code"]
    assert NEW_INDEX not in idx
    with engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM mod_bip_balance")).scalar() == 1
    assert "补齐失败" in caplog.text


def test_retry_after_failure_succeeds_then_drops_old_index(tmp_path, monkeypatch):
    """失败后重试：新索引建成，旧索引才被清理，数据保持。"""
    engine = _old_balance_db(tmp_path / "retry.db")
    real_create = Index.create

    def failing_create(self, bind=None, checkfirst=False):
        if self.name == NEW_INDEX:
            raise RuntimeError("forced create failure")
        return real_create(self, bind=bind, checkfirst=checkfirst)

    monkeypatch.setattr(Index, "create", failing_create)
    app_main._ensure_indexes_on(engine, Base.metadata)
    idx = {i["name"] for i in inspect(engine).get_indexes("mod_bip_balance")}
    assert OLD_INDEX in idx and NEW_INDEX not in idx

    monkeypatch.undo()  # 恢复 Index.create，模拟下次启动重试
    created = app_main._ensure_indexes_on(engine, Base.metadata)
    assert any(NEW_INDEX in c for c in created)
    idx = {i["name"]: i for i in inspect(engine).get_indexes("mod_bip_balance")}
    assert idx[NEW_INDEX]["column_names"] == ["ledger", "period", "subject_code"]
    assert idx[NEW_INDEX]["unique"] == 1
    assert OLD_INDEX not in idx  # 新索引成功后才清理旧名
    with engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM mod_bip_balance")).scalar() == 1
