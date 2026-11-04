"""BIP Task 4 Fix round 1 回归（复核 C-1 / I-1～I-4 / m-1·m-3·m-4·m-9）。

全部使用合成数据与临时 SQLite；不读真实 backend/data、不触发外部 BIP。
本文件先于实现编写（RED），逐条锁定复核意见：

- C-1：余额唯一键/upsert 含核算账簿；旧空账簿行可就地升级；跨账簿同期间并存；
- I-1：合并失败/未匹配不写 sources，更正文件可重试补齐；
- I-2：余额部分列重导只更新本次映射字段，未映射旧值保留并告知；
- I-3：`-`/`—`/`－`/`／`/`--` 等零占位符按 0，`--5` 不得变正 5；
- I-4：同金额多候选按摘要二次匹配，仍歧义则告警不静默顺序配；
- m-1：merged_entries 按分录去重计数；
- m-3：唯一约束按凭证/余额分类，非唯一约束归 400；
- m-4：内容相同不报 updated；m-9：余额模板期初/期末映射与金额。
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from app import main as app_main
from app.database import Base, SessionLocal
from app.modules.finance_ledger_bip import service as svc
from app.modules.finance_ledger_bip.models import BipBalance, BipBatch

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
LEDGER_A = "测试账簿A"
LEDGER_B = "测试账簿B"


# ---------------- 合成数据与工具 ----------------

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


def _balance_rows(ledger: str | None = None, *, period_debit: float = 800.0) -> list[dict]:
    rows = [
        {"科目编码": "1002", "科目名称": "银行存款",
         "期初余额": 1000.0, "期初借方余额": 500.0, "期初贷方余额": 0.0,
         "本期借方发生额": period_debit, "本期贷方发生额": 300.0,
         "期末余额": 1500.0, "期末借方余额": 1200.0, "期末贷方余额": 0.0},
        {"科目编码": "6602", "科目名称": "管理费用",
         "期初余额": 0.0, "期初借方余额": 0.0, "期初贷方余额": 0.0,
         "本期借方发生额": 300.0, "本期贷方发生额": 0.0,
         "期末余额": 300.0, "期末借方余额": 300.0, "期末贷方余额": 0.0},
    ]
    if ledger is not None:
        for row in rows:
            row["核算账簿"] = ledger
    return rows


def _plain_voucher(ledger: str = LEDGER_A, voucher_no: str = "记-9001",
                   debit: float = 100.0, credit: float = 100.0,
                   summaries: tuple[str, str] = ("支付货款", "支付货款")) -> list[dict]:
    return [
        {"日期": "2026-09-05", "核算账簿": ledger, "凭证号": voucher_no, "摘要": summaries[0],
         "借方金额": debit, "贷方金额": 0.0},
        {"日期": "2026-09-05", "核算账簿": ledger, "凭证号": voucher_no, "摘要": summaries[1],
         "借方金额": 0.0, "贷方金额": credit},
    ]


def _aux(ledger: str = LEDGER_A, voucher_no: str = "记-9001",
         debit: float = 100.0, credit: float = 100.0,
         summaries: tuple[str, str] = ("支付货款", "支付货款")) -> list[dict]:
    return [
        {"科目编码": "100201", "科目名称": "银行存款-基本户", "日期": "2026-09-05",
         "核算账簿": ledger, "凭证号": voucher_no, "摘要": summaries[0],
         "借方本币": debit, "贷方本币": 0.0},
        {"科目编码": "220201", "科目名称": "应付账款-供应商", "日期": "2026-09-05",
         "核算账簿": ledger, "凭证号": voucher_no, "摘要": summaries[1],
         "借方本币": 0.0, "贷方本币": credit},
    ]


def _detail(client, voucher_no: str) -> dict:
    items = client.get("/api/finance/ledger/bip/vouchers",
                       params={"voucher_no": voucher_no}).json()["items"]
    assert len(items) == 1, items
    return client.get(f"/api/finance/ledger/bip/vouchers/{items[0]['id']}").json()


# ---------------- C-1：余额唯一键/upsert 含核算账簿 ----------------

def test_balance_cross_ledger_same_period_coexist(client, bip_dir, tmp_path):
    """同期间、同科目、不同核算账簿的余额行必须并存（A/B 各保留）。"""
    a = _import_file(client, _write_xlsx(tmp_path / "a.xlsx", _balance_rows(LEDGER_A)), period="2026-09")
    b = _import_file(client, _write_xlsx(tmp_path / "b.xlsx", _balance_rows(LEDGER_B)), period="2026-09")
    assert a["inserted_balances"] == 2 and a["updated_balances"] == 0
    assert b["inserted_balances"] == 2 and b["updated_balances"] == 0

    with SessionLocal() as db:
        rows = db.query(BipBalance).filter(BipBalance.period == "2026-09").all()
        assert len(rows) == 4
        assert {r.ledger for r in rows} == {LEDGER_A, LEDGER_B}
        assert {r.ledger for r in rows if r.subject_code == "1002"} == {LEDGER_A, LEDGER_B}


def test_balance_legacy_empty_ledger_row_is_adopted(client, bip_dir, tmp_path):
    """迁移前 ledger 为空的历史行：带账簿重导时就地升级，不产生重复行。"""
    with SessionLocal() as db:
        batch = BipBatch(filename="legacy.xlsx", file_type="balance", period="2026-09")
        db.add(batch)
        db.flush()
        db.add(BipBalance(batch_id=batch.id, period="2026-09", subject_code="1002",
                          subject_name="银行存款", period_debit=1.0))
        db.commit()

    body = _import_file(client, _write_xlsx(tmp_path / "a.xlsx", _balance_rows(LEDGER_A)), period="2026-09")
    assert body["inserted_balances"] == 1  # 1002 升级，6602 新增
    with SessionLocal() as db:
        rows = db.query(BipBalance).filter(BipBalance.period == "2026-09").all()
        codes = sorted((r.ledger, r.subject_code) for r in rows)
        assert codes == [(LEDGER_A, "1002"), (LEDGER_A, "6602")]
        row = next(r for r in rows if r.subject_code == "1002")
        assert row.period_debit == 800.0


def test_balance_mapping_includes_ledger_and_balances():
    cols = ["科目编码", "科目名称", "核算账簿", "期初余额",
            "本期借方发生额", "本期贷方发生额", "期末余额"]
    m = svc.suggest_mapping("balance", cols)
    assert m["ledger"] == "核算账簿"
    assert m["opening_balance"] == "期初余额"
    assert m["closing_balance"] == "期末余额"


def test_old_balance_index_replaced_by_new_name(tmp_path):
    """历史旧索引 (period, subject_code)：先建新名 uq_bip_balance_scope，成功后再删旧名。"""
    engine = create_engine(f"sqlite:///{(tmp_path / 'old-balance.db').as_posix()}")
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
            "CREATE UNIQUE INDEX uq_bip_balance_period_subject "
            "ON mod_bip_balance (period, subject_code)"
        ))
        conn.execute(text(
            "INSERT INTO mod_bip_balance (period, subject_code, ledger) "
            "VALUES ('2026-09', '1002', '')"
        ))

    created = app_main._ensure_indexes_on(engine, Base.metadata)
    assert any("uq_bip_balance_scope" in c for c in created)
    idx = {i["name"]: i for i in inspect(engine).get_indexes("mod_bip_balance")}
    assert idx["uq_bip_balance_scope"]["column_names"] == ["ledger", "period", "subject_code"]
    assert idx["uq_bip_balance_scope"]["unique"] == 1
    assert "uq_bip_balance_period_subject" not in idx  # 新索引成功后才清理旧名
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO mod_bip_balance (period, subject_code, ledger) VALUES ('2026-09','1002','A')"))
        conn.execute(text("INSERT INTO mod_bip_balance (period, subject_code, ledger) VALUES ('2026-09','1002','B')"))


# ---------------- I-1：合并失败不写 sources，可重试 ----------------

def test_merge_mismatch_keeps_source_retryable(client, bip_dir, tmp_path):
    """金额不匹配首导 → 不写 sources；更正文件重导可补齐科目。"""
    _import_file(client, _write_xlsx(tmp_path / "v.xlsx", _plain_voucher()))

    wrong = _import_file(client, _write_xlsx(tmp_path / "a-bad.xlsx",
                                             _aux(debit=90.0, credit=90.0)))
    assert wrong["merged_entries"] == 0
    assert any("未合并" in w and "重试" in w for w in wrong["warnings"])
    assert _detail(client, "记-9001")["sources"] == "voucher"
    assert all(e["subject_code"] == "" for e in _detail(client, "记-9001")["entries"])

    fixed = _import_file(client, _write_xlsx(tmp_path / "a-good.xlsx", _aux()))
    assert fixed["merged_entries"] == 2
    detail = _detail(client, "记-9001")
    assert set(detail["sources"].split(",")) == {"voucher", "aux"}
    assert {e["subject_code"] for e in detail["entries"]} == {"100201", "220201"}
    assert len(detail["entries"]) == 2


# ---------------- I-2：余额部分列重导不清零 ----------------

def test_balance_partial_mapping_preserves_unmapped_fields(client, bip_dir, tmp_path):
    full = _balance_rows(LEDGER_A)
    _import_file(client, _write_xlsx(tmp_path / "full.xlsx", full), period="2026-09")

    partial = [
        {"科目编码": "1002", "科目名称": "银行存款", "核算账簿": LEDGER_A,
         "本期借方发生额": 999.0},
        {"科目编码": "6602", "科目名称": "管理费用", "核算账簿": LEDGER_A,
         "本期借方发生额": 111.0},
    ]
    body = _import_file(client, _write_xlsx(tmp_path / "partial.xlsx", partial), period="2026-09")
    assert body["updated_balances"] == 2
    assert any("保留旧值" in w for w in body["warnings"])

    with SessionLocal() as db:
        row = db.query(BipBalance).filter(BipBalance.ledger == LEDGER_A,
                                          BipBalance.period == "2026-09",
                                          BipBalance.subject_code == "1002").one()
        assert row.period_debit == 999.0
        assert row.opening_balance == 1000.0
        assert row.opening_debit == 500.0
        assert row.closing_balance == 1500.0


# ---------------- I-3：零占位符与双符号 ----------------

@pytest.mark.parametrize("blank", ["-", "--", "---", "—", "——", "－", "／", "/", "–", "−", " - ", "— "])
def test_zero_placeholders_parse_as_zero(blank):
    assert svc._parse_amount(blank) == 0.0


@pytest.mark.parametrize("bad", ["--5", "---5", "+-5", "-+5", "／5", "5-"])
def test_double_sign_amounts_are_rejected_not_flipped(bad):
    with pytest.raises(ValueError):
        svc._parse_amount(bad)


def test_unicode_minus_still_negative():
    assert svc._parse_amount("−5") == -5.0
    assert svc._parse_amount("－5") == -5.0


# ---------------- I-4：同金额多候选按摘要消歧 ----------------

def test_same_amount_candidates_use_summary_then_ambiguous_warning(client, bip_dir, tmp_path):
    # 场景 1：同金额两条候选，但摘要不同 → 按摘要唯一匹配
    v1 = [
        {"日期": "2026-09-05", "核算账簿": LEDGER_A, "凭证号": "记-9501", "摘要": "办公费",
         "借方金额": 100.0, "贷方金额": 0.0},
        {"日期": "2026-09-05", "核算账簿": LEDGER_A, "凭证号": "记-9501", "摘要": "差旅费",
         "借方金额": 100.0, "贷方金额": 0.0},
        {"日期": "2026-09-05", "核算账簿": LEDGER_A, "凭证号": "记-9501", "摘要": "合计",
         "借方金额": 0.0, "贷方金额": 200.0},
    ]
    a1 = [
        {"科目编码": "660201", "科目名称": "管理费用-办公费", "日期": "2026-09-05",
         "核算账簿": LEDGER_A, "凭证号": "记-9501", "摘要": "办公费",
         "借方本币": 100.0, "贷方本币": 0.0},
        {"科目编码": "660202", "科目名称": "管理费用-差旅费", "日期": "2026-09-05",
         "核算账簿": LEDGER_A, "凭证号": "记-9501", "摘要": "差旅费",
         "借方本币": 100.0, "贷方本币": 0.0},
        {"科目编码": "100201", "科目名称": "银行存款", "日期": "2026-09-05",
         "核算账簿": LEDGER_A, "凭证号": "记-9501", "摘要": "合计",
         "借方本币": 0.0, "贷方本币": 200.0},
    ]
    _import_file(client, _write_xlsx(tmp_path / "v1.xlsx", v1))
    body = _import_file(client, _write_xlsx(tmp_path / "a1.xlsx", a1))
    assert body["merged_entries"] == 3 and body["ambiguous_entries"] == 0
    assert {e["subject_code"] for e in _detail(client, "记-9501")["entries"]} == {
        "660201", "660202", "100201"}

    # 场景 2：同金额两条候选且摘要也相同 → 歧义告警，不顺序硬配
    v2 = [
        {"日期": "2026-09-05", "核算账簿": LEDGER_A, "凭证号": "记-9502", "摘要": "办公费",
         "借方金额": 50.0, "贷方金额": 0.0},
        {"日期": "2026-09-05", "核算账簿": LEDGER_A, "凭证号": "记-9502", "摘要": "办公费",
         "借方金额": 50.0, "贷方金额": 0.0},
        {"日期": "2026-09-05", "核算账簿": LEDGER_A, "凭证号": "记-9502", "摘要": "合计",
         "借方金额": 0.0, "贷方金额": 100.0},
    ]
    a2 = [
        {"科目编码": "660201", "科目名称": "管理费用-办公费", "日期": "2026-09-05",
         "核算账簿": LEDGER_A, "凭证号": "记-9502", "摘要": "办公费",
         "借方本币": 50.0, "贷方本币": 0.0},
        {"科目编码": "660202", "科目名称": "管理费用-差旅费", "日期": "2026-09-05",
         "核算账簿": LEDGER_A, "凭证号": "记-9502", "摘要": "办公费",
         "借方本币": 50.0, "贷方本币": 0.0},
        {"科目编码": "100201", "科目名称": "银行存款", "日期": "2026-09-05",
         "核算账簿": LEDGER_A, "凭证号": "记-9502", "摘要": "合计",
         "借方本币": 0.0, "贷方本币": 100.0},
    ]
    _import_file(client, _write_xlsx(tmp_path / "v2.xlsx", v2))
    body2 = _import_file(client, _write_xlsx(tmp_path / "a2.xlsx", a2))
    assert body2["ambiguous_entries"] == 2
    assert any("ambiguous_entries" in w for w in body2["warnings"])
    detail2 = _detail(client, "记-9502")
    debits = [e for e in detail2["entries"] if e["debit"] == 50.0]
    assert len(debits) == 2 and all(e["subject_code"] == "" for e in debits)
    assert detail2["sources"] == "voucher"  # 歧义不标记来源，可重试


# ---------------- m-1：merged_entries 按分录去重 ----------------

def test_merged_entries_counts_distinct_entries_not_field_writes(client, bip_dir, tmp_path):
    """一条分录同时补科目与摘要只计 1 条。"""
    voucher_rows = [
        {"日期": "2026-09-05", "核算账簿": LEDGER_A, "凭证号": "记-9601",
         "借方金额": 100.0, "贷方金额": 0.0},
        {"日期": "2026-09-05", "核算账簿": LEDGER_A, "凭证号": "记-9601",
         "借方金额": 0.0, "贷方金额": 100.0},
    ]
    aux_rows = _aux(voucher_no="记-9601", summaries=("收到货款", "支付货款"))
    _import_file(client, _write_xlsx(tmp_path / "v.xlsx", voucher_rows))
    body = _import_file(client, _write_xlsx(tmp_path / "a.xlsx", aux_rows))
    assert body["merged_entries"] == 2  # 不是 4（科目+摘要各写一次）
    detail = _detail(client, "记-9601")
    assert {e["summary"] for e in detail["entries"]} == {"收到货款", "支付货款"}
    assert {e["subject_code"] for e in detail["entries"]} == {"100201", "220201"}


# ---------------- m-3：约束错误分类 ----------------

def test_constraint_error_classification():
    def err(message: str) -> IntegrityError:
        return IntegrityError("stmt", {}, Exception(message))

    msg, conflict = svc._classify_constraint_error(
        err("UNIQUE constraint failed: mod_bip_voucher.ledger, mod_bip_voucher.period, mod_bip_voucher.voucher_no"))
    assert conflict is True and "凭证" in msg

    msg, conflict = svc._classify_constraint_error(
        err("UNIQUE constraint failed: mod_bip_balance.ledger, mod_bip_balance.period, mod_bip_balance.subject_code"))
    assert conflict is True and "余额" in msg

    msg, conflict = svc._classify_constraint_error(
        err("NOT NULL constraint failed: mod_bip_entry.debit"))
    assert conflict is False and "约束" in msg


def test_constraint_error_http_mapping(client, bip_dir, tmp_path, monkeypatch):
    """唯一冲突 → 409；非唯一约束 → 400；数据库忙 → 409（均回滚，无半批数据）。"""
    from sqlalchemy.exc import OperationalError

    token = _preview(client, _write_xlsx(tmp_path / "b.xlsx", _balance_rows(LEDGER_A))).json()["token"]

    def unique_boom(*args, **kwargs):
        raise IntegrityError("stmt", {}, Exception(
            "UNIQUE constraint failed: mod_bip_balance.ledger, mod_bip_balance.period, mod_bip_balance.subject_code"))

    monkeypatch.setattr(svc, "_import_balance", unique_boom)
    resp = _confirm(client, token, period="2026-09")
    assert resp.status_code == 409 and "余额" in resp.json()["detail"]

    def other_boom(*args, **kwargs):
        raise IntegrityError("stmt", {}, Exception("NOT NULL constraint failed: mod_bip_balance.period"))

    monkeypatch.setattr(svc, "_import_balance", other_boom)
    resp2 = _confirm(client, token, period="2026-09")
    assert resp2.status_code == 400 and "约束" in resp2.json()["detail"]

    def busy_boom(*args, **kwargs):
        raise OperationalError("stmt", {}, Exception("database is locked"))

    monkeypatch.setattr(svc, "_import_balance", busy_boom)
    resp3 = _confirm(client, token, period="2026-09")
    assert resp3.status_code == 409 and "数据库忙" in resp3.json()["detail"]
    assert client.get("/api/finance/ledger/bip/imports").json()["total"] == 0


# ---------------- m-4：内容相同不报 updated ----------------

def test_balance_identical_reimport_reports_unchanged(client, bip_dir, tmp_path):
    path = _write_xlsx(tmp_path / "b.xlsx", _balance_rows(LEDGER_A))
    first = _import_file(client, path, period="2026-09")
    assert first["inserted_balances"] == 2
    second = _import_file(client, path, period="2026-09")
    assert second["inserted_balances"] == 0
    assert second["updated_balances"] == 0
    assert second["unchanged_balances"] == 2
