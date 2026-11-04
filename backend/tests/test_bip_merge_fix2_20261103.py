"""BIP Task 4 Fix round 2 回归（C-1 残余：余额文件空账簿单元格）。

全部合成数据 + 临时 SQLite；不读真实 backend/data、不触发外部 BIP。

- 空账簿单元格必须沿用上一行（同一文件内），不能落成空账簿行或跨账簿升级；
- 旧空账簿行只允许被**一个**账簿就地升级，后续另一账簿同科目不得改标签覆盖；
- 唯一索引定义变化但新键已有重复时，保留旧索引并告警（不删数据）；
- 多 sheet 余额表无账簿列时以 sheet 名作为核算账簿（回退映射）。
"""
from __future__ import annotations

from pathlib import Path

import openpyxl
import pandas as pd
import pytest
from sqlalchemy import create_engine, inspect, text

from app import main as app_main
from app.database import Base, SessionLocal
from app.modules.finance_ledger_bip import service as svc
from app.modules.finance_ledger_bip.models import BipBalance, BipBatch

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
LEDGER_A = "测试账簿A"
LEDGER_B = "测试账簿B"


def _write_xlsx(path: Path, rows: list[dict]) -> Path:
    pd.DataFrame(rows).to_excel(path, index=False, engine="openpyxl")
    return path


@pytest.fixture()
def bip_dir(monkeypatch, tmp_path):
    d = tmp_path / "bip_data"
    monkeypatch.setattr(svc, "module_data_dir", lambda: d)
    return d


def _preview(client, path: Path, name: str = "sample.xlsx"):
    with open(path, "rb") as f:
        return client.post("/api/finance/ledger/bip/imports/preview",
                           files={"file": (name, f, XLSX_MIME)})


def _confirm(client, token: str, **kw):
    return client.post("/api/finance/ledger/bip/imports/confirm", json={"token": token, **kw})


def _import_file(client, path: Path, **kw) -> dict:
    prev = _preview(client, path).json()
    resp = _confirm(client, prev["token"], **kw)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _row(ledger, code: str = "1002", debit: float = 100.0) -> dict:
    return {"核算账簿": ledger, "科目编码": code, "科目名称": "银行存款",
            "本期借方发生额": debit}


def _legacy_row(period: str = "2026-09", code: str = "1002", debit: float = 1.0) -> None:
    """迁移前 ledger 为空的历史余额行（旧唯一索引保证同期间同科目至多一条）。"""
    with SessionLocal() as db:
        batch = BipBatch(filename="legacy.xlsx", file_type="balance", period=period)
        db.add(batch)
        db.flush()
        db.add(BipBalance(batch_id=batch.id, period=period, subject_code=code,
                          subject_name="银行存款", period_debit=debit))
        db.commit()


# ---------------- C-1 残余：空账簿单元格 ----------------

def test_balance_blank_ledger_cell_inherits_previous_ledger(client, bip_dir, tmp_path):
    """同文件续行空账簿单元格沿用上一行 → 同科目只有一行，不落成空账簿行。"""
    rows = [_row(LEDGER_A, debit=100.0), _row(None, debit=200.0), _row("", debit=300.0)]
    body = _import_file(client, _write_xlsx(tmp_path / "b.xlsx", rows), period="2026-09")
    assert body["inserted_balances"] == 1
    assert any("文件内重复" in w for w in body["warnings"])

    with SessionLocal() as db:
        rows_db = db.query(BipBalance).filter(BipBalance.period == "2026-09").all()
        assert len(rows_db) == 1
        assert rows_db[0].ledger == LEDGER_A
        assert rows_db[0].period_debit == 300.0  # 文件内同键按最后一行


def test_balance_blank_ledger_cell_does_not_cross_ledger_upgrade(client, bip_dir, tmp_path):
    """旧空账簿行被 A 升级后，B 不得再次升级/改标签覆盖它。"""
    _legacy_row(debit=1.0)
    rows = [
        _row(LEDGER_A, debit=2.0),
        _row(None, debit=3.0),        # 空单元格属于 A
        _row(LEDGER_B, debit=4.0),
        _row("", debit=5.0),          # 空单元格属于 B
    ]
    body = _import_file(client, _write_xlsx(tmp_path / "b.xlsx", rows), period="2026-09")
    assert body["inserted_balances"] == 1  # 旧行升级为 A，B 新增
    assert body["updated_balances"] == 1

    with SessionLocal() as db:
        rows_db = db.query(BipBalance).filter(BipBalance.period == "2026-09").all()
        by_ledger = {r.ledger: r.period_debit for r in rows_db}
        assert by_ledger == {LEDGER_A: 3.0, LEDGER_B: 5.0}
        assert "" not in by_ledger


def test_balance_multisheet_without_ledger_column_uses_sheet_name(client, bip_dir, tmp_path):
    """多 sheet 余额表无账簿列：以 sheet 名作为核算账簿，A/B 同科目并存。"""
    wb = openpyxl.Workbook()
    for name, amount in ((LEDGER_A, 800.0), (LEDGER_B, 900.0)):
        ws = wb.create_sheet(name)
        ws.append(["科目编码", "科目名称", "本期借方发生额"])
        ws.append(["1002", "银行存款", amount])
    wb.remove(wb.active)
    path = tmp_path / "multi-balance.xlsx"
    wb.save(path)

    prev = _preview(client, path).json()
    assert prev["file_type"] == "balance"
    assert prev["mapping"]["ledger"] == svc.SHEET_LEDGER_COL

    body = _import_file(client, path, period="2026-09")
    assert body["inserted_balances"] == 2
    with SessionLocal() as db:
        rows_db = db.query(BipBalance).filter(BipBalance.period == "2026-09").all()
        assert {r.ledger: r.period_debit for r in rows_db} == {LEDGER_A: 800.0, LEDGER_B: 900.0}


# ---------------- 唯一索引重建：新键重复时保留旧索引 ----------------

def test_index_rebuild_keeps_old_when_new_key_has_duplicates(tmp_path, caplog):
    """同名索引定义变化但新键已有重复 → 不重建、保留旧索引、只告警、不删数据。

    覆盖锁定（复核 round 2 要求）：旧唯一索引保证旧键无重复，但新键是超集；
    若历史状态异常（同名旧索引非唯一且存在重复），迁移必须失败关闭。
    """
    engine = create_engine(f"sqlite:///{(tmp_path / 'dup-index.db').as_posix()}")
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
            "CREATE INDEX uq_bip_balance_period_subject "
            "ON mod_bip_balance (period, subject_code)"
        ))
        conn.execute(text(
            "INSERT INTO mod_bip_balance (period, subject_code, ledger) VALUES "
            "('2026-09', '1002', ''), ('2026-09', '1002', '')"
        ))

    with caplog.at_level("WARNING"):
        created = app_main._ensure_indexes_on(engine, Base.metadata)
    assert not any("uq_bip_balance_period_subject" in c for c in created)
    assert "已跳过唯一索引" in caplog.text
    assert "不会自动删除任何数据" in caplog.text

    idx = {i["name"]: i for i in inspect(engine).get_indexes("mod_bip_balance")}
    assert idx["uq_bip_balance_period_subject"]["column_names"] == ["period", "subject_code"]
    assert idx["uq_bip_balance_period_subject"]["unique"] == 0
    assert "uq_bip_balance_scope" not in idx  # 新键重复：新索引未建，旧索引保留
    with engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM mod_bip_balance")).scalar() == 2
