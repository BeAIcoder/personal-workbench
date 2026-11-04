"""BIP 多来源合并 / 余额更正 / 幂等回归（Task 4，台账 L-070～L-078）。

全部使用合成数据与临时 SQLite：
- 不读真实 backend/data，不触发外部 BIP；
- 金额 / 日期 / 列名均为构造值，不含真实公司名、账号或凭证。

覆盖：
- 先凭证列表后辅助明细账（及反向顺序）→ 不新增重复凭证、补写科目、汇总非空；
- 同来源重导不翻倍；余额表按期间 upsert、跨月同号独立；
- 全行无效 400、不可解析金额与非有限值不静默归零；
- 整数 / 紧凑 YYYYMMDD 日期、空表头列（nan.1）;
- 唯一索引迁移在重复数据前停止、归档与 commit 顺序不留孤儿。
"""
from __future__ import annotations

import math
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import openpyxl
import pandas as pd
import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from app import main as app_main
from app.database import Base, SessionLocal
from app.modules.finance_ledger_bip import service as svc
from app.modules.finance_ledger_bip.models import BipBalance, BipEntry, BipVoucher

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
LEDGER = "合并测试账簿"


# ---------------- 合成数据 ----------------

def _write_xlsx(path: Path, rows: list[dict], title_row: str | None = None) -> Path:
    """用 pandas 生成合成 xlsx；title_row 模拟 BIP 导出顶部标题行。"""
    df = pd.DataFrame(rows)
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        if title_row is not None:
            pd.DataFrame([[title_row]]).to_excel(writer, index=False, header=False)
            df.to_excel(writer, index=False, startrow=1)
        else:
            df.to_excel(writer, index=False)
    return path


def _plain_voucher_rows(dates: tuple[str, str] = ("2026-09-05", "2026-09-05")) -> list[dict]:
    """凭证列表：无科目列（旧契约形状），一张平衡凭证两条分录。"""
    return [
        {"日期": dates[0], "核算账簿": LEDGER, "凭证号": "记-9001", "摘要": "支付货款",
         "借方金额": 100.0, "贷方金额": 0.0},
        {"日期": dates[1], "核算账簿": LEDGER, "凭证号": "记-9001", "摘要": "支付货款",
         "借方金额": 0.0, "贷方金额": 100.0},
    ]


def _aux_rows() -> list[dict]:
    """辅助明细账：与凭证列表同一凭证、金额一一对应，带科目编码。"""
    return [
        {"科目编码": "100201", "科目名称": "银行存款-基本户", "日期": "2026-09-05",
         "核算账簿": LEDGER, "凭证号": "记-9001", "摘要": "支付货款",
         "借方本币": 100.0, "贷方本币": 0.0},
        {"科目编码": "220201", "科目名称": "应付账款-供应商", "日期": "2026-09-05",
         "核算账簿": LEDGER, "凭证号": "记-9001", "摘要": "支付货款",
         "借方本币": 0.0, "贷方本币": 100.0},
    ]


def _balance_rows(period_debit_1002: float = 800.0) -> list[dict]:
    return [
        {"科目编码": "1002", "科目名称": "银行存款", "期初借方余额": 1000.0, "期初贷方余额": 0.0,
         "本期借方发生额": period_debit_1002, "本期贷方发生额": 300.0,
         "期末借方余额": 1500.0, "期末贷方余额": 0.0},
        {"科目编码": "6602", "科目名称": "管理费用", "期初借方余额": 0.0, "期初贷方余额": 0.0,
         "本期借方发生额": 300.0, "本期贷方发生额": 0.0,
         "期末借方余额": 300.0, "期末贷方余额": 0.0},
    ]


# ---------------- 公共工具 ----------------

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


def _summary(client, period: str = "2026-09") -> dict:
    resp = client.get("/api/finance/ledger/bip/balances/summary", params={"period": period})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _vouchers(client, **params) -> list[dict]:
    return client.get("/api/finance/ledger/bip/vouchers", params=params).json()["items"]


def _entries(client, **params) -> dict:
    return client.get("/api/finance/ledger/bip/entries", params=params).json()


# ---------------- 跨来源合并（L-070 / L-071 / L-073） ----------------

def test_voucher_then_aux_merges_without_duplicate(client, bip_dir, tmp_path):
    """先导凭证列表、再导同期间辅助明细账：不新增凭证、补齐科目、汇总非空。"""
    vbody = _import_file(client, _write_xlsx(tmp_path / "v.xlsx", _plain_voucher_rows()))
    assert vbody["inserted_vouchers"] == 1 and vbody["inserted_entries"] == 2

    empty = _summary(client)
    assert empty["items"] == [] and empty["entries_without_subject"] == 2
    assert "未提供科目编码" in empty["note"]

    abody = _import_file(client, _write_xlsx(tmp_path / "a.xlsx", _aux_rows()))
    assert abody["inserted_vouchers"] == 0
    assert abody["merged_vouchers"] == 1
    assert abody["merged_entries"] == 2
    assert abody["inserted_entries"] == 0
    assert abody["skipped_vouchers"] == 0

    items = _vouchers(client, period="2026-09")
    assert len(items) == 1
    detail = client.get(f"/api/finance/ledger/bip/vouchers/{items[0]['id']}").json()
    assert len(detail["entries"]) == 2  # 不是 4：没有把同一凭证再落一遍
    assert {e["subject_code"] for e in detail["entries"]} == {"100201", "220201"}
    assert detail["debit_total"] == 100.0 and detail["credit_total"] == 100.0
    assert set((detail["sources"] or "").split(",")) == {"voucher", "aux"}

    summary = _summary(client)
    assert summary["subject_count"] == 2
    assert summary["debit_total"] == 100.0 and summary["credit_total"] == 100.0
    assert summary["entries_without_subject"] == 0 and summary["note"] == ""

    # 同来源（aux）重导：按重复跳过，分录不翻倍
    again = _import_file(client, _write_xlsx(tmp_path / "a2.xlsx", _aux_rows()))
    assert again["inserted_vouchers"] == 0 and again["merged_vouchers"] == 0
    assert again["skipped_vouchers"] == 1
    assert _entries(client, period="2026-09")["total"] == 2


def test_aux_then_voucher_does_not_overwrite_subjects(client, bip_dir, tmp_path):
    """反向顺序导入：凭证列表不覆盖已入库科目，也不新增任何分录。"""
    _import_file(client, _write_xlsx(tmp_path / "a.xlsx", _aux_rows()))
    body = _import_file(client, _write_xlsx(tmp_path / "v.xlsx", _plain_voucher_rows()))
    assert body["inserted_vouchers"] == 0
    assert body["merged_vouchers"] == 1
    assert body["merged_entries"] == 0  # 科目已存在，无可补写

    items = _vouchers(client)
    assert len(items) == 1
    detail = client.get(f"/api/finance/ledger/bip/vouchers/{items[0]['id']}").json()
    assert len(detail["entries"]) == 2
    assert {e["subject_code"] for e in detail["entries"]} == {"100201", "220201"}
    assert _entries(client)["total"] == 2


def test_voucher_list_subject_columns_populate_summary(client, bip_dir, tmp_path):
    """凭证列表自带科目列（模板真实形状）：分录带科目，仅导凭证列表汇总也非空。"""
    rows = [
        {"凭证日期": "2026-09-05", "核算账簿": LEDGER, "凭证号": "记-9011", "分录摘要": "支付货款",
         "科目编码": "100201", "科目名称": "银行存款-基本户", "借方金额": 60.0, "贷方金额": None},
        {"凭证日期": "", "核算账簿": "", "凭证号": "", "分录摘要": "支付货款",
         "科目编码": "220201", "科目名称": "应付账款-供应商", "借方金额": None, "贷方金额": 60.0},
    ]
    path = _write_xlsx(tmp_path / "v-subject.xlsx", rows, title_row="凭证列表")
    prev = _preview(client, path).json()
    assert prev["file_type"] == "voucher"
    assert prev["mapping"]["subject_code"] == "科目编码" and prev["mapping"]["subject_name"] == "科目名称"

    body = _import_file(client, path)
    assert body["inserted_vouchers"] == 1 and body["inserted_entries"] == 2
    summary = _summary(client)
    assert summary["subject_count"] == 2 and summary["entries_without_subject"] == 0
    assert "未提供科目编码" not in summary["note"]


def test_summary_totals_equal_entry_reaggregation(client, bip_dir, tmp_path):
    """汇总 = 明细重聚合（借贷合计逐分一致）。"""
    _import_file(client, _write_xlsx(tmp_path / "a.xlsx", _aux_rows()))
    summary = _summary(client)
    entries = _entries(client, period="2026-09", page_size=200)["items"]
    assert summary["debit_total"] == round(sum(e["debit"] for e in entries), 2)
    assert summary["credit_total"] == round(sum(e["credit"] for e in entries), 2)
    assert sum(i["entry_count"] for i in summary["items"]) == len(entries)


# ---------------- 余额表按期间 upsert（L-072 / L-073） ----------------

def test_balance_upsert_same_period_and_cross_month(client, bip_dir, tmp_path):
    path = _write_xlsx(tmp_path / "b.xlsx", _balance_rows())
    first = _import_file(client, path, period="2026-09")
    assert first["inserted_balances"] == 2 and first["updated_balances"] == 0

    second = _import_file(client, path, period="2026-09")
    assert second["inserted_balances"] == 0
    assert second["updated_balances"] == 0  # 内容相同不谎报更正
    assert second["unchanged_balances"] == 2

    with SessionLocal() as db:
        rows = db.query(BipBalance).filter(BipBalance.period == "2026-09").all()
        assert len(rows) == 2
        assert {r.subject_code for r in rows} == {"1002", "6602"}

    # 更正文件：同期间同科目覆盖旧值，不新增行
    corrected = _write_xlsx(tmp_path / "b2.xlsx", _balance_rows(period_debit_1002=999.0))
    fixed = _import_file(client, corrected, period="2026-09")
    assert fixed["inserted_balances"] == 0
    assert fixed["updated_balances"] == 1  # 只有 1002 变化
    assert fixed["unchanged_balances"] == 1  # 6602 内容相同
    with SessionLocal() as db:
        row = db.query(BipBalance).filter(BipBalance.period == "2026-09",
                                          BipBalance.subject_code == "1002").one()
        assert row.period_debit == 999.0
        assert db.query(BipBalance).filter(BipBalance.period == "2026-09").count() == 2

    # 跨月同号独立：10 月新增两行，9 月值不变
    october = _import_file(client, path, period="2026-10")
    assert october["inserted_balances"] == 2 and october["updated_balances"] == 0
    with SessionLocal() as db:
        assert db.query(BipBalance).filter(BipBalance.period == "2026-10").count() == 2
        sep = db.query(BipBalance).filter(BipBalance.period == "2026-09",
                                          BipBalance.subject_code == "1002").one()
        assert sep.period_debit == 999.0


# ---------------- 无效数据与金额解析（L-074 / L-075） ----------------

def test_all_invalid_rows_return_400_without_batch(client, bip_dir, tmp_path):
    rows = [{"日期": "不是日期", "核算账簿": LEDGER, "凭证号": "记-9401", "摘要": "合计行",
             "借方金额": 100.0, "贷方金额": 0.0}]
    path = _write_xlsx(tmp_path / "invalid.xlsx", rows)
    token = _preview(client, path).json()["token"]
    resp = _confirm(client, token, file_type="voucher",
                    mapping={"date": "日期", "ledger": "核算账簿", "voucher_no": "凭证号",
                             "debit": "借方金额", "credit": "贷方金额"})
    assert resp.status_code == 400, resp.text
    assert "有效" in resp.json()["detail"] or "无效" in resp.json()["detail"]
    assert client.get("/api/finance/ledger/bip/imports").json()["total"] == 0
    inbox = bip_dir / "inbox"
    assert not inbox.exists() or not any(inbox.iterdir())


def test_balance_all_invalid_rows_return_400(client, bip_dir, tmp_path):
    rows = [{"科目编码": "", "科目名称": "空行", "本期借方发生额": 10.0}]
    path = _write_xlsx(tmp_path / "bad-balance.xlsx", rows)
    token = _preview(client, path).json()["token"]
    resp = _confirm(client, token, file_type="balance",
                    mapping={"subject_code": "科目编码", "subject_name": "科目名称",
                             "period_debit": "本期借方发生额"}, period="2026-09")
    assert resp.status_code == 400, resp.text
    assert client.get("/api/finance/ledger/bip/imports").json()["total"] == 0


@pytest.mark.parametrize("bad", ["1,2x.3", "inf", "-inf", "Infinity", "nan", "1.2.3"])
def test_unparseable_amounts_raise_instead_of_zeroing(bad):
    with pytest.raises(ValueError):
        svc._parse_amount(bad)


def test_parse_amount_keeps_valid_values_and_blank_cells():
    assert svc._parse_amount("1,234.50") == 1234.5
    assert svc._parse_amount("（1,234.50）") == -1234.5
    assert svc._parse_amount("(1,234.50)") == -1234.5
    assert svc._parse_amount(-12.345) == -12.35
    assert svc._parse_amount("") == 0.0
    assert svc._parse_amount(None) == 0.0
    assert svc._parse_amount(float("nan")) == 0.0  # Excel 空单元格


def test_invalid_amount_row_skipped_not_zeroed(client, bip_dir, tmp_path):
    rows = [
        {"日期": "2026-09-05", "核算账簿": LEDGER, "凭证号": "记-9101", "摘要": "正常行",
         "借方金额": 100.0, "贷方金额": 0.0},
        {"日期": "2026-09-05", "核算账簿": LEDGER, "凭证号": "记-9101", "摘要": "坏金额",
         "借方金额": 0.0, "贷方金额": "1,2x.3"},
    ]
    body = _import_file(client, _write_xlsx(tmp_path / "bad-amt.xlsx", rows))
    assert body["inserted_vouchers"] == 1
    assert body["inserted_entries"] == 1  # 坏行被跳过，未以 0 金额入库
    assert body["skipped_rows"] == 1 and body["invalid_amount_rows"] == 1
    assert any("金额" in w for w in body["warnings"])

    detail = client.get(f"/api/finance/ledger/bip/vouchers/{_vouchers(client)[0]['id']}").json()
    assert len(detail["entries"]) == 1
    assert detail["entries"][0]["debit"] == 100.0
    assert detail["entries"][0]["credit"] == 0.0


def test_non_finite_amount_row_skipped(client, bip_dir, tmp_path):
    rows = [
        {"日期": "2026-09-05", "核算账簿": LEDGER, "凭证号": "记-9102", "摘要": "正常行",
         "借方金额": 50.0, "贷方金额": 0.0},
        {"日期": "2026-09-05", "核算账簿": LEDGER, "凭证号": "记-9102", "摘要": "inf",
         "借方金额": 0.0, "贷方金额": "inf"},
    ]
    body = _import_file(client, _write_xlsx(tmp_path / "inf.xlsx", rows))
    assert body["inserted_entries"] == 1 and body["invalid_amount_rows"] == 1
    stored = _entries(client, period="2026-09")["items"]
    assert len(stored) == 1
    assert all(math.isfinite(e["debit"]) and math.isfinite(e["credit"]) for e in stored)


# ---------------- 日期与空列（L-076 / L-077） ----------------

def test_integer_and_compact_dates(client, bip_dir, tmp_path):
    rows = [
        {"日期": 20260901, "核算账簿": LEDGER, "凭证号": "记-9201", "摘要": "整数日期",
         "借方金额": 10.0, "贷方金额": 0.0},
        {"日期": 20260901, "核算账簿": LEDGER, "凭证号": "记-9201", "摘要": "整数日期",
         "借方金额": 0.0, "贷方金额": 10.0},
        {"日期": "20260902", "核算账簿": LEDGER, "凭证号": "记-9202", "摘要": "紧凑日期",
         "借方金额": 20.0, "贷方金额": 0.0},
        {"日期": "20260902", "核算账簿": LEDGER, "凭证号": "记-9202", "摘要": "紧凑日期",
         "借方金额": 0.0, "贷方金额": 20.0},
    ]
    body = _import_file(client, _write_xlsx(tmp_path / "dates.xlsx", rows))
    assert body["inserted_vouchers"] == 2 and body["skipped_rows"] == 0
    got = {v["voucher_no"]: v["voucher_date"] for v in _vouchers(client)}
    assert got == {"记-9201": "2026-09-01", "记-9202": "2026-09-02"}
    assert svc._to_date_text(20260901) == "2026-09-01"
    assert svc._to_date_text("20260901") == "2026-09-01"
    assert svc._to_date_text(20261332) == ""
    assert svc._to_date_text(float("inf")) == ""


def test_blank_header_columns_do_not_become_nan_dot_one(tmp_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["日期", None, "核算账簿", "凭证号", None, "摘要", "借方金额", "贷方金额"])
    ws.append(["2026-09-01", "噪声", LEDGER, "记-9301", "噪声", "摘要", 100.0, None])
    ws.append(["2026-09-01", "噪声", LEDGER, "记-9301", "噪声", "摘要", None, 100.0])
    path = tmp_path / "blank-header.xlsx"
    wb.save(path)

    df = svc.read_bip_excel(path)
    cols = list(df.columns)
    assert "" not in cols
    assert not any(str(c).lower().startswith(("nan", "unnamed")) for c in cols)
    assert cols == ["日期", "核算账簿", "凭证号", "摘要", "借方金额", "贷方金额"]
    assert svc.detect_file_type(cols) == "voucher"


# ---------------- 唯一索引与归档顺序（L-078） ----------------

def test_unique_index_declared_on_models():
    def unique_sets(table):
        return {
            tuple(c.name for c in idx.columns)
            for idx in table.indexes
            if idx.unique
        }

    assert ("ledger", "period", "voucher_no") in unique_sets(BipVoucher.__table__)
    assert ("ledger", "period", "subject_code") in unique_sets(BipBalance.__table__)


def test_unique_index_migration_skips_existing_duplicates(tmp_path, caplog):
    """历史库已有重复时停止自动迁移并报告，不删除任何数据。"""
    engine = create_engine(f"sqlite:///{(tmp_path / 'mig.db').as_posix()}")
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE mod_bip_voucher ("
            "id INTEGER PRIMARY KEY, batch_id INTEGER, period TEXT, voucher_date TEXT, "
            "ledger TEXT, voucher_no TEXT, voucher_full_no TEXT, summary TEXT, "
            "sources TEXT, created_at DATETIME)"
        ))
        conn.execute(text(
            "INSERT INTO mod_bip_voucher (ledger, period, voucher_no) VALUES "
            "('L', '2026-09', '记-1'), ('L', '2026-09', '记-1')"
        ))

    with caplog.at_level("WARNING"):
        created = app_main._ensure_indexes_on(engine, Base.metadata)
    assert not any("uq_bip_voucher_scope" in c for c in created)
    assert "重复" in caplog.text
    assert "uq_bip_voucher_scope" not in {i["name"] for i in inspect(engine).get_indexes("mod_bip_voucher")}
    with engine.connect() as conn:  # 数据一行都不许被删
        assert conn.execute(text("SELECT COUNT(*) FROM mod_bip_voucher")).scalar() == 2

    # 人工清理重复后再次迁移 → 唯一索引建立且真正生效
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM mod_bip_voucher WHERE id = 2"))
    created2 = app_main._ensure_indexes_on(engine, Base.metadata)
    assert any("uq_bip_voucher_scope" in c for c in created2)
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO mod_bip_voucher (ledger, period, voucher_no) VALUES "
                "('L', '2026-09', '记-1')"
            ))


def test_import_failure_rolls_back_batch_and_leaves_no_archive(client, bip_dir, tmp_path, monkeypatch):
    path = _write_xlsx(tmp_path / "v.xlsx", _plain_voucher_rows())
    token = _preview(client, path).json()["token"]

    def boom(*args, **kwargs):
        raise ValueError("模拟入库失败")

    monkeypatch.setattr(svc, "_import_voucher_like", boom)
    resp = _confirm(client, token)
    assert resp.status_code == 400
    assert client.get("/api/finance/ledger/bip/imports").json()["total"] == 0
    inbox = bip_dir / "inbox"
    assert not inbox.exists() or not any(inbox.iterdir())


def test_archive_failure_keeps_committed_data_without_orphan(client, bip_dir, tmp_path, monkeypatch):
    real_sub_dir = svc._sub_dir

    def fake_sub_dir(name):
        if name == "inbox":
            raise OSError("模拟归档失败")
        return real_sub_dir(name)

    monkeypatch.setattr(svc, "_sub_dir", fake_sub_dir)
    body = _import_file(client, _write_xlsx(tmp_path / "v.xlsx", _plain_voucher_rows()))
    assert body["inserted_vouchers"] == 1 and body["inserted_entries"] == 2
    assert _vouchers(client)  # 数据已提交
    batch = client.get("/api/finance/ledger/bip/imports").json()["items"][0]
    assert batch["stored_path"] == ""


def test_concurrent_double_click_is_idempotent(client, bip_dir, tmp_path):
    """并发双击同一文件：进程内导入锁 + 唯一索引保证只入库一次。"""
    content = _write_xlsx(tmp_path / "v.xlsx", _plain_voucher_rows()).read_bytes()
    token1, _ = svc.save_preview_file("双击1.xlsx", content)
    token2, _ = svc.save_preview_file("双击2.xlsx", content)

    def work(tok: str):
        with SessionLocal() as db:
            return svc.confirm_import(db, token=tok)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(work, [token1, token2]))

    assert sum(r["inserted_vouchers"] for r in results) == 1
    assert sum(r["skipped_vouchers"] for r in results) == 1
    with SessionLocal() as db:
        assert db.query(BipVoucher).count() == 1
        assert db.query(BipEntry).count() == 2
