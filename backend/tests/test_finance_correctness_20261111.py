"""2026-11-11 财务正确性修复（T4B）回归：银行令牌 / 金额 HALF_UP / BIP 余额表 / 发票跨批配对 / 现金流年份。

全部合成数据 + 临时库/临时目录：
- 不写真实 backend/data、不触真实银行账号/H 编码、真实引擎、真实计划任务；
- 银行账号、购方、发票号均为构造值。

覆盖：
1. 银行流水预览只返回掩码账号 + 稳定令牌，导出按令牌服务端反查（旧掩码/全号入参兼容）；
2. 银行/BIP/发票/费用金额统一 Decimal ROUND_HALF_UP 两位；
3. BIP 只有科目余额表、没有凭证分录时，余额摘要回退余额表并可按余额行查询；
4. 发票跨批次蓝红配对（双向）、排除已配对/重复项、删除批次解除配对、崩溃批次恢复可重传；
5. 现金流运行记录持久化实际年份，宿主轻量迁移补列且幂等。
"""
from __future__ import annotations

import io
import json
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path

import openpyxl
import pytest
from sqlalchemy import create_engine

from app import main as app_main
from app.database import Base, SessionLocal, engine
from app.modules import collect_column_patches
from app.modules.finance_bankflow import service as bf
from app.modules.finance_cashflow import service as cash_svc
from app.modules.finance_cashflow.models import CashflowRun
from app.modules.finance_expense import service as expense_svc
from app.modules.finance_invoice import invoice_batch, invoice_parse
from app.modules.finance_invoice import service as inv_svc
from app.modules.finance_invoice.models import InvoiceBatch, InvoiceItem
from app.modules.finance_ledger_bip import service as bip_svc

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
FAKE_PDF = b"%PDF-1.4 synthetic-test-bytes"


@pytest.fixture(autouse=True)
def _ensure_tables():
    """非 client 触库用例前确保全部模块表存在（conftest 对 client 用例 teardown 只 drop 不重建）。"""
    Base.metadata.create_all(bind=engine)
    yield


# ==================== 银行流水：掩码 + 稳定令牌 ====================

_ACC_A = "5500010000001111"
_ACC_B = "5500010000002222"
_FLOW_HEADERS = [
    "组织", "银行", "开户银行", "账号", "币种", "交易日期", "收入", "支出",
    "当前余额", "用途", "对方户名", "对方账号", "来源", "资金交易生成状态",
    "附言(对账码)", "是否已对账", "银行交易流水号", "单证号码", "备注", "导出标识",
    "账户名称", "资金系统单据号", "被代理账号", "被代理账户开户行名", "被代理行号", "被代理账户名",
]


def _flow_row(account, d, income, expense, bal, use, counter, seq, currency="CNY-人民币"):
    row = ["P1-合成项目", "CCB-建设银行", "测试银行测试支行", account, currency, d,
           income, expense, bal, use, counter, "6222000000000000",
           "银企直联", "未生成", "", "×", seq, "", "", "已导出", "合成户名", "", "", "", "", ""]
    return row + [""] * (26 - len(row))


def _flow_xlsx(rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "可操作组织明细查看"
    ws.append(_FLOW_HEADERS)
    for r in rows:
        ws.append(r)
    out = io.BytesIO()
    wb.save(out)
    wb.close()
    return out.getvalue()


_ROWS_A = [
    _flow_row(_ACC_A, "2026-10-05", "100.5", "-", 1000.0, "合成收入甲一", "合成对手户名X", "SEQ-A1"),
    _flow_row(_ACC_A, "2026-10-12", "-", "50", 950.5, "合成支出甲二", "合成对手户名Y", "SEQ-A2"),
    _flow_row(_ACC_A, "2026-10-20", "33.33", "-", 983.83, "合成收入甲三", "合成对手户名Z", "SEQ-A3"),
]


@pytest.fixture()
def bank_cfg(tmp_path, monkeypatch):
    cfg = tmp_path / "config.local.yaml"
    cfg.write_text(
        'bank_accounts:\n  "%s": { ent: "TESTH001", name: "合成甲" }\n  "%s": { ent: "TESTH002", name: "合成乙" }\n'
        % (_ACC_A, _ACC_B),
        encoding="utf-8",
    )
    monkeypatch.setattr(bf, "_CONFIG_PATH", cfg)
    # 令牌密钥固定放 tmp，避免跨用例相互影响
    monkeypatch.setattr(bf, "_TOKEN_KEY_PATH", tmp_path / "bank_token.key", raising=False)
    return cfg


def test_bank_preview_masks_account_and_token_is_stable(client, bank_cfg):
    data = _flow_xlsx(_ROWS_A + [_flow_row(_ACC_B, "2026-10-08", "7", "-", 700.0, "乙一", "户B", "SEQ-B1")])
    body = client.post("/api/finance/bankflow/preview", files={"file": ("flow.xlsx", data)}).json()
    dumped = json.dumps(body, ensure_ascii=False)
    assert _ACC_A not in dumped and _ACC_B not in dumped  # 响应 JSON 不含完整账号
    acc = next(a for a in body["accounts"] if a["tail"] == "1111")
    assert acc["account"] == bf.mask_account(_ACC_A)
    assert acc["account_token"] and _ACC_A not in acc["account_token"]
    # 稳定：同一账号多次预览（含跨请求）令牌一致
    again = client.post("/api/finance/bankflow/preview", files={"file": ("flow.xlsx", data)}).json()
    assert next(a for a in again["accounts"] if a["tail"] == "1111")["account_token"] == acc["account_token"]


def test_bank_template_export_by_token(client, bank_cfg):
    data = _flow_xlsx(_ROWS_A)
    body = client.post("/api/finance/bankflow/preview", files={"file": ("flow.xlsx", data)}).json()
    token = next(a for a in body["accounts"] if a["tail"] == "1111")["account_token"]
    r = client.post(
        "/api/finance/bankflow/template",
        files={"file": ("flow.xlsx", data)},
        data={"account_token": token, "date_start": "2026-10-01", "date_end": "2026-10-23"},
    )
    assert r.status_code == 200, r.text
    wb = openpyxl.load_workbook(io.BytesIO(r.content))
    ws = wb["银行对账单"]
    assert ws.cell(row=10, column=4).value == "TESTH001"
    assert ws.cell(row=10, column=9).value == 100.5
    assert ws.max_row == 12  # 9 头 + 3 数据
    wb.close()


def test_bank_template_export_by_masked_account_for_ui_compat(client, bank_cfg):
    """前端当前回传预览里的 account 字段：掩码账号也必须能服务端反查到完整账号。"""
    data = _flow_xlsx(_ROWS_A)
    body = client.post("/api/finance/bankflow/preview", files={"file": ("flow.xlsx", data)}).json()
    masked = next(a for a in body["accounts"] if a["tail"] == "1111")["account"]
    r = client.post(
        "/api/finance/bankflow/template",
        files={"file": ("flow.xlsx", data)},
        data={"account": masked, "date_start": "2026-10-01", "date_end": "2026-10-23"},
    )
    assert r.status_code == 200, r.text


def test_bank_template_unknown_token_400(client, bank_cfg):
    data = _flow_xlsx(_ROWS_A)
    r = client.post(
        "/api/finance/bankflow/template",
        files={"file": ("flow.xlsx", data)},
        data={"account_token": "BK000000000000000000000000", "date_start": "", "date_end": ""},
    )
    assert r.status_code == 400
    assert "令牌" in r.json()["detail"]


def test_bank_template_requires_exactly_one_credential(client, bank_cfg):
    """account_token 与 account 必须显式二选一；完整账号兼容分支已移除。"""
    data = _flow_xlsx(_ROWS_A)
    body = client.post("/api/finance/bankflow/preview", files={"file": ("flow.xlsx", data)}).json()
    acc = next(a for a in body["accounts"] if a["tail"] == "1111")

    both = client.post(
        "/api/finance/bankflow/template",
        files={"file": ("flow.xlsx", data)},
        data={"account_token": acc["account_token"], "account": acc["account"]},
    )
    assert both.status_code == 400
    assert "二选一" in both.json()["detail"]

    neither = client.post(
        "/api/finance/bankflow/template",
        files={"file": ("flow.xlsx", data)},
        data={"date_start": "", "date_end": ""},
    )
    assert neither.status_code == 400
    assert "二选一" in neither.json()["detail"]

    full_account = client.post(
        "/api/finance/bankflow/template",
        files={"file": ("flow.xlsx", data)},
        data={"account": _ACC_A, "date_start": "", "date_end": ""},
    )
    assert full_account.status_code == 400
    assert "令牌" in full_account.json()["detail"]


def test_bank_token_key_creation_is_thread_safe(tmp_path, monkeypatch):
    """首次并发生成令牌密钥时只能有一个赢家，否则先发出的令牌会失效。"""
    key_path = tmp_path / "race.key"
    monkeypatch.setattr(bf, "_TOKEN_KEY_PATH", key_path)
    original = bf.secrets.token_bytes
    entered = threading.Lock()
    state = {"count": 0}
    both_generating = threading.Event()

    def slow_token_bytes(n):
        with entered:
            state["count"] += 1
            if state["count"] >= 2:
                both_generating.set()
        # 无锁实现会让两个线程都走到这里；有锁时只有一个线程，等超时后继续
        both_generating.wait(timeout=0.3)
        return original(n)

    monkeypatch.setattr(bf.secrets, "token_bytes", slow_token_bytes)
    barrier = threading.Barrier(2)

    def worker():
        barrier.wait()
        return bf.account_token(_ACC_A)

    with ThreadPoolExecutor(max_workers=2) as pool:
        tokens = [f.result() for f in [pool.submit(worker), pool.submit(worker)]]

    assert tokens[0] == tokens[1]
    assert key_path.is_file() and key_path.stat().st_size == 32


def test_bank_masked_account_ambiguity_requires_token(client, bank_cfg):
    """同长度同尾号的两个账号掩码完全相同：掩码入参必须拒绝，令牌仍可精确定位。"""
    acc_c = "6600010000001111"  # 与 _ACC_A 同长度、同尾号 → 掩码一致
    bank_cfg.write_text(
        'bank_accounts:\n  "%s": { ent: "TESTH001", name: "合成甲" }\n  "%s": { ent: "TESTH003", name: "合成丙" }\n'
        % (_ACC_A, acc_c),
        encoding="utf-8",
    )
    data = _flow_xlsx(_ROWS_A + [_flow_row(acc_c, "2026-10-09", "8", "-", 800.0, "丙一", "户C", "SEQ-C1")])
    body = client.post("/api/finance/bankflow/preview", files={"file": ("flow.xlsx", data)}).json()
    masked = bf.mask_account(_ACC_A)
    # 两个不同账号掩码相同 → 预览是两条账户项，掩码本身不足以唯一定位
    assert [a["account"] for a in body["accounts"]].count(masked) == 2
    assert len({a["account_token"] for a in body["accounts"]}) == 2
    r = client.post(
        "/api/finance/bankflow/template",
        files={"file": ("flow.xlsx", data)},
        data={"account": masked, "date_start": "", "date_end": ""},
    )
    assert r.status_code == 400
    assert "令牌" in r.json()["detail"]

    token_c = next(a for a in body["accounts"] if a["tail"] == "1111" and a["ent"] == "TESTH003")["account_token"]
    ok = client.post(
        "/api/finance/bankflow/template",
        files={"file": ("flow.xlsx", data)},
        data={"account_token": token_c, "date_start": "", "date_end": ""},
    )
    assert ok.status_code == 200
    wb = openpyxl.load_workbook(io.BytesIO(ok.content))
    assert wb["银行对账单"].cell(row=10, column=4).value == "TESTH003"
    wb.close()


def test_bank_amounts_round_half_up():
    # 二进制 float 的 round() 会把 1.005 舍成 1.0；财务口径必须 HALF_UP 到 1.01
    assert bf._norm_amount("1.005") == 1.01
    assert bf._norm_amount(2.675) == 2.68
    assert bf._norm_amount("-50.555") == -50.56
    assert bf._norm_amount("1,000.125") == 1000.13
    rows = bf.parse_flow(_flow_xlsx([
        _flow_row(_ACC_A, "2026-10-05", "1.005", "-", 1000.0, "合成收入", "户X", "SEQ-H1"),
        _flow_row(_ACC_A, "2026-10-06", "2.675", "-", 1002.68, "合成收入二", "户Y", "SEQ-H2"),
    ]))
    content, exported = bf.build_template(rows, _ACC_A, "TESTH001", "20261001", "20261031")
    assert exported == 2
    wb = openpyxl.load_workbook(io.BytesIO(content))
    values = [wb["银行对账单"].cell(row=r, column=9).value for r in (10, 11)]
    wb.close()
    assert values == [1.01, 2.68]


# ==================== BIP：金额 HALF_UP + 只有余额表 ====================

def _write_xlsx(path: Path, headers: list[str], rows: list[list], title: str | None = None) -> Path:
    wb = openpyxl.Workbook()
    ws = wb.active
    if title:
        ws.append([title])
    ws.append(headers)
    for row in rows:
        ws.append(row)
    wb.save(path)
    wb.close()
    return path


_BALANCE_HEADERS = [
    "科目编码", "科目名称", "核算账簿", "期初借方余额", "期初贷方余额",
    "本期借方发生额", "本期贷方发生额", "期末借方余额", "期末贷方余额",
]


def _balance_rows() -> list[list]:
    return [
        ["1002", "银行存款", "测试账簿甲", 1000.0, 0.0, 800.0, 300.0, 1500.0, 0.0],
        ["6602", "管理费用", "测试账簿甲", 0.0, 0.0, 300.0, 0.0, 300.0, 0.0],
    ]


@pytest.fixture()
def bip_dir(monkeypatch, tmp_path):
    d = tmp_path / "bip_data"
    monkeypatch.setattr(bip_svc, "module_data_dir", lambda: d)
    return d


def _import_balance(
    client, tmp_path: Path, period: str = "2026-09", rows: list[list] | None = None,
) -> dict:
    path = _write_xlsx(tmp_path / "balance.xlsx", _BALANCE_HEADERS, rows or _balance_rows(), title="科目余额表")
    with open(path, "rb") as f:
        prev = client.post(
            "/api/finance/ledger/bip/imports/preview",
            files={"file": ("balance.xlsx", f, XLSX_MIME)},
        )
    assert prev.status_code == 200, prev.text
    assert prev.json()["file_type"] == "balance"
    resp = client.post(
        "/api/finance/ledger/bip/imports/confirm",
        json={"token": prev.json()["token"], "period": period},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_bip_amounts_round_half_up():
    assert bip_svc._r2(2.675) == 2.68
    assert bip_svc._r2("1.005") == 1.01
    assert bip_svc._parse_amount("1.005") == 1.01
    assert bip_svc._parse_amount("(1.005)") == -1.01
    assert bip_svc._parse_amount("-2.675") == -2.68


def test_bip_balance_only_summary_and_row_query(client, bip_dir, tmp_path):
    imported = _import_balance(client, tmp_path)
    assert imported["inserted_balances"] == 2

    summary = client.get("/api/finance/ledger/bip/balances/summary", params={"period": "2026-09"})
    assert summary.status_code == 200, summary.text
    body = summary.json()
    assert body["source"] == "balances"  # 无凭证分录时回退余额表
    items = {i["subject_code"]: i for i in body["items"]}
    assert set(items) == {"1002", "6602"}
    assert items["1002"]["debit_total"] == 800.0
    assert items["1002"]["credit_total"] == 300.0
    assert body["debit_total"] == 1100.0
    assert "余额表" in body["note"]

    rows = client.get(
        "/api/finance/ledger/bip/balances",
        params={"period": "2026-09", "subject_prefix": "1002"},
    )
    assert rows.status_code == 200, rows.text
    page = rows.json()
    assert page["total"] == 1
    row = page["items"][0]
    assert row["subject_code"] == "1002" and row["ledger"] == "测试账簿甲"
    assert row["period_debit"] == 800.0 and row["period_credit"] == 300.0
    assert row["closing_debit"] == 1500.0
    assert page["totals"]["period_debit"] == 800.0
    assert client.get("/api/finance/ledger/bip/balances", params={"period": "2026-9"}).status_code == 400

    # Agent 工具同样能读只有余额表的数据
    tool = bip_svc.bip_balance_query(period="2026-09", subject_prefix="1002")
    assert tool["source"] == "balances" and tool["debit_total"] == 800.0


def test_bip_balance_fallback_aggregates_by_subject_across_ledgers(client, bip_dir, tmp_path):
    """多账簿同科目：回退摘要按科目编码聚合，不在 items 中重复出现。"""
    rows = [
        ["1002", "银行存款", "测试账簿甲", 1000.0, 0.0, 800.0, 300.0, 1500.0, 0.0],
        ["1002", "银行存款", "测试账簿乙", 500.0, 0.0, 200.0, 100.0, 600.0, 0.0],
        ["6602", "管理费用", "测试账簿甲", 0.0, 0.0, 300.0, 0.0, 300.0, 0.0],
    ]
    imported = _import_balance(client, tmp_path, rows=rows)
    assert imported["inserted_balances"] == 3

    body = client.get(
        "/api/finance/ledger/bip/balances/summary", params={"period": "2026-09"}
    ).json()
    assert body["source"] == "balances"
    codes = [i["subject_code"] for i in body["items"]]
    assert codes == ["1002", "6602"]  # 每科目一行，不按账簿重复
    assert body["subject_count"] == 2
    assert body["balance_row_count"] == 3
    items = {i["subject_code"]: i for i in body["items"]}
    assert items["1002"]["subject_name"] == "银行存款"
    assert items["1002"]["debit_total"] == 1000.0
    assert items["1002"]["credit_total"] == 400.0
    assert body["debit_total"] == 1300.0 and body["credit_total"] == 400.0

    # 余额行查询仍按账簿保留明细
    page = client.get(
        "/api/finance/ledger/bip/balances", params={"period": "2026-09", "subject_prefix": "1002"}
    ).json()
    assert page["total"] == 2


def test_bip_balance_summary_prefers_entries_when_both_exist(client, bip_dir, tmp_path):
    """余额表与凭证分录并存时摘要仍以分录为准，避免重复计数。"""
    _import_balance(client, tmp_path)
    voucher = _write_xlsx(
        tmp_path / "aux.xlsx",
        ["科目编码", "科目名称", "日期", "核算账簿", "凭证号", "摘要", "借方本币", "贷方本币"],
        [["100201", "银行存款-基本户", "2026-09-05", "测试账簿甲", "记-0001", "收款", 500.0, 0.0],
         ["112201", "应收账款-客户A", "2026-09-05", "测试账簿甲", "记-0001", "收款", 0.0, 500.0]],
    )
    with open(voucher, "rb") as f:
        prev = client.post(
            "/api/finance/ledger/bip/imports/preview",
            files={"file": ("aux.xlsx", f, XLSX_MIME)},
        )
    assert prev.status_code == 200 and prev.json()["file_type"] == "aux"
    assert client.post(
        "/api/finance/ledger/bip/imports/confirm", json={"token": prev.json()["token"]}
    ).status_code == 200
    body = client.get("/api/finance/ledger/bip/balances/summary", params={"period": "2026-09"}).json()
    assert body["source"] == "entries"
    items = {i["subject_code"]: i for i in body["items"]}
    assert items["100201"]["debit_total"] == 500.0
    assert body["balance_row_count"] == 2  # 余额行仍可通过余额行查询查看


# ==================== 发票：跨批配对 / 崩溃恢复 / HALF_UP ====================

def _invoice(**kw) -> InvoiceItem:
    base = dict(status=InvoiceItem.STATUS_PARSED)
    base.update(kw)
    return InvoiceItem(**base)


@pytest.fixture()
def finance_dir(tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.settings.finance_data_dir", str(tmp_path / "finance"))
    return tmp_path / "finance"


@pytest.fixture()
def mock_engine(monkeypatch):
    canned: dict[str, dict] = {}
    monkeypatch.setattr(invoice_parse, "_require_engine", lambda: None)

    def fake_parse(pdf_path):
        fields = canned.get(Path(pdf_path).name)
        return inv_svc.ParsedInvoice(**fields) if fields is not None else None

    monkeypatch.setattr(invoice_parse, "_parse_pdf_with_engine", fake_parse)
    return canned


def _make_fields(**kw) -> dict:
    base = dict(
        invoice_number="10000001",
        check_code="c001",
        buyer_name="示例购方公司",
        seller_name="示例销方公司",
        amount="100.00",
        tax_amount="13.00",
        tax_rate="0.13",
        invoice_date="2026年10月01日",
        invoice_type="普票",
        invoice_direction="正数发票",
        project_name="示例项目",
        brand_name="",
        remark="合成备注",
        extraction_success=True,
    )
    base.update(kw)
    return base


def _upload(client, files: list[tuple[str, bytes]], name: str = ""):
    multipart = [("files", (fname, content, "application/pdf")) for fname, content in files]
    data = {"name": name} if name else {}
    return client.post("/api/finance/invoice/batches", files=multipart, data=data)


def test_invoice_pair_red_invoices_cross_batch_candidates():
    """跨批候选：双向配对；已配对/重复候选一律排除。"""
    # 当前批红票 ↔ 历史批正票
    red_cur = _invoice(id=1, buyer_name="甲", amount="-100.00", invoice_direction="冲红发票")
    pos_old = _invoice(id=11, buyer_name="甲", amount="100.00", invoice_direction="正数发票")
    pos_paired = _invoice(id=12, buyer_name="甲", amount="100.00", invoice_direction="正数发票", paired_item_id=99)
    pos_dup = _invoice(id=13, buyer_name="甲", amount="100.00", invoice_direction="正数发票",
                       status=InvoiceItem.STATUS_DUPLICATE)
    matched, unmatched = inv_svc.pair_red_invoices(
        [red_cur], candidates=[pos_paired, pos_dup, pos_old]
    )
    assert (matched, unmatched) == (1, 0)
    assert red_cur.paired_item_id == 11 and pos_old.paired_item_id == 1
    assert pos_paired.paired_item_id == 99 and pos_dup.paired_item_id is None

    # 当前批正票 ↔ 历史批红票（反向）
    pos_cur = _invoice(id=2, buyer_name="乙", amount="200.00", invoice_direction="正数发票")
    red_old = _invoice(id=21, buyer_name="乙", amount="-200.00", invoice_direction="冲红发票")
    red_old_paired = _invoice(id=22, buyer_name="乙", amount="-200.00", invoice_direction="冲红发票", paired_item_id=88)
    matched, unmatched = inv_svc.pair_red_invoices(
        [pos_cur], candidates=[red_old_paired, red_old]
    )
    assert (matched, unmatched) == (1, 0)
    assert pos_cur.paired_item_id == 21 and red_old.paired_item_id == 2


def test_invoice_cross_batch_positive_then_red(client, finance_dir, mock_engine):
    mock_engine["pos.pdf"] = _make_fields(invoice_number="7001", amount="113.00")
    b1 = _upload(client, [("pos.pdf", FAKE_PDF)]).json()
    pos_id = b1["items"][0]["id"]

    mock_engine["red.pdf"] = _make_fields(
        invoice_number="7002", amount="-113.00", invoice_direction="冲红发票"
    )
    b2 = _upload(client, [("red.pdf", FAKE_PDF)]).json()
    assert b2["red_matched_count"] == 1 and b2["red_unmatched_count"] == 0
    red = b2["items"][0]
    assert red["paired_item_id"] == pos_id and red["paired_invoice_number"] == "7001"

    # 旧批次查询时也能看到双向配对
    b1_again = client.get(f"/api/finance/invoice/batches/{b1['id']}").json()
    assert b1_again["items"][0]["paired_item_id"] == red["id"]

    # 已配对的正票不再被第二张红票占用
    mock_engine["red3.pdf"] = _make_fields(
        invoice_number="7003", amount="-113.00", invoice_direction="冲红发票"
    )
    b3 = _upload(client, [("red3.pdf", FAKE_PDF)]).json()
    assert b3["red_matched_count"] == 0 and b3["red_unmatched_count"] == 1
    assert b3["items"][0]["paired_item_id"] is None


def test_invoice_cross_batch_red_then_positive_updates_old_batch(client, finance_dir, mock_engine):
    mock_engine["red.pdf"] = _make_fields(
        invoice_number="7101", amount="-200.00", invoice_direction="冲红发票"
    )
    b1 = _upload(client, [("red.pdf", FAKE_PDF)]).json()
    assert (b1["red_matched_count"], b1["red_unmatched_count"]) == (0, 1)

    mock_engine["pos.pdf"] = _make_fields(invoice_number="7102", amount="200.00")
    b2 = _upload(client, [("pos.pdf", FAKE_PDF)]).json()
    assert b2["items"][0]["paired_item_id"] == b1["items"][0]["id"]

    # 旧批次的冲红统计随跨批配对更新
    b1_again = client.get(f"/api/finance/invoice/batches/{b1['id']}").json()
    assert (b1_again["red_matched_count"], b1_again["red_unmatched_count"]) == (1, 0)


def test_invoice_delete_batch_clears_cross_batch_pointer(client, finance_dir, mock_engine):
    mock_engine["pos.pdf"] = _make_fields(invoice_number="7201", amount="300.00")
    b1 = _upload(client, [("pos.pdf", FAKE_PDF)]).json()
    mock_engine["red.pdf"] = _make_fields(
        invoice_number="7202", amount="-300.00", invoice_direction="冲红发票"
    )
    b2 = _upload(client, [("red.pdf", FAKE_PDF)]).json()
    assert b2["items"][0]["paired_item_id"] == b1["items"][0]["id"]

    assert client.delete(f"/api/finance/invoice/batches/{b2['id']}").status_code == 200
    b1_again = client.get(f"/api/finance/invoice/batches/{b1['id']}").json()
    assert b1_again["items"][0]["paired_item_id"] is None  # 不留下悬空指针

    # 解除配对后，新红票可以重新配对
    mock_engine["red2.pdf"] = _make_fields(
        invoice_number="7203", amount="-300.00", invoice_direction="冲红发票"
    )
    b3 = _upload(client, [("red2.pdf", FAKE_PDF)]).json()
    assert b3["red_matched_count"] == 1
    assert b3["items"][0]["paired_item_id"] == b1["items"][0]["id"]


def test_invoice_stale_uploaded_batch_recovered_and_retryable(client, finance_dir, mock_engine):
    """解析中途崩溃遗留的 uploaded 批次：列表时自动置 failed，重传不再被判重。"""
    old = datetime.now() - timedelta(hours=2)
    with SessionLocal() as db:
        stale = InvoiceBatch(
            name="崩溃批次",
            status=InvoiceBatch.STATUS_UPLOADED,
            created_at=old,
            updated_at=old,  # 心跳也停了才算崩溃
        )
        fresh = InvoiceBatch(name="新批次", status=InvoiceBatch.STATUS_UPLOADED)
        # 解析很久但仍在心跳：created_at 老、updated_at 新 → 不得误杀
        active = InvoiceBatch(
            name="解析中批次",
            status=InvoiceBatch.STATUS_UPLOADED,
            created_at=old,
            updated_at=datetime.now(),
        )
        db.add_all([stale, fresh, active])
        db.flush()
        db.add(
            InvoiceItem(
                batch_id=stale.id,
                source_file="crash.pdf",
                invoice_number="8001",
                check_code="c1",
                amount="100.00",
                invoice_direction="正数发票",
                status=InvoiceItem.STATUS_PARSED,
            )
        )
        db.commit()
        stale_id, fresh_id, active_id = stale.id, fresh.id, active.id

    listing = client.get("/api/finance/invoice/batches").json()
    by_id = {b["id"]: b for b in listing["items"]}
    assert by_id[stale_id]["status"] == "failed"
    assert "重试" in by_id[stale_id]["error"]
    assert by_id[fresh_id]["status"] == "uploaded"  # 未超时批次不受影响
    assert by_id[active_id]["status"] == "uploaded"  # 有心跳的活跃解析不得误杀

    # 幂等：再次恢复不应重复处理（已 failed 的不再改动，未超时的不动）
    with SessionLocal() as db:
        assert inv_svc.recover_stale_batches(db) == 0

    # 失败批次不参与去重：同号同校验码重传应为 parsed（而不是 duplicate）
    mock_engine["retry.pdf"] = _make_fields(invoice_number="8001", check_code="c1", amount="100.00")
    retry = _upload(client, [("retry.pdf", FAKE_PDF)]).json()
    assert retry["items"][0]["status"] == "parsed"
    assert retry["duplicate_count"] == 0


def test_invoice_heartbeat_keeps_active_parse_alive(client):
    """解析期心跳刷新 updated_at，恢复逻辑不再误杀仍在解析的批次。"""
    old = datetime.now() - timedelta(hours=2)
    with SessionLocal() as db:
        batch = InvoiceBatch(
            name="长解析", status=InvoiceBatch.STATUS_UPLOADED, created_at=old, updated_at=old
        )
        db.add(batch)
        db.commit()
        batch_id = batch.id

    inv_svc._heartbeat_batch(batch_id)
    with SessionLocal() as db:
        batch = db.get(InvoiceBatch, batch_id)
        assert batch.updated_at > old
        assert inv_svc.recover_stale_batches(db) == 0
        assert db.get(InvoiceBatch, batch_id).status == "uploaded"


def _slow_parse_started(release: threading.Event):
    """返回一个「解析开始即阻塞到 release」的替身，用于模拟单文件超过恢复阈值的长解析。"""

    def slow_parse(pdf_path):
        started.set()
        release.wait(timeout=10)
        return inv_svc.ParsedInvoice(**_make_fields(invoice_number="9401", check_code="slow"))

    started = threading.Event()
    return started, slow_parse


def _start_batch_worker(uploads: list[tuple[str, bytes]], result: dict) -> threading.Thread:
    """在独立线程/独立 Session 里跑 create_batch（模拟真实请求线程）。"""

    def worker():
        with SessionLocal() as db:
            try:
                batch = inv_svc.create_batch(db, uploads)
                result.update(id=batch.id, status=batch.status)
            except Exception as exc:  # noqa: BLE001 - 测试需把异常带回主线程断言
                result["error"] = exc

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    return thread


def test_invoice_slow_single_file_not_reaped_by_recovery(
    client, finance_dir, mock_engine, monkeypatch
):
    """单文件解析超过恢复阈值时，后台心跳必须续命，不得被列表恢复逻辑误判崩溃。"""
    release = threading.Event()
    started, slow_parse = _slow_parse_started(release)
    monkeypatch.setattr(invoice_parse, "_parse_pdf_with_engine", slow_parse)
    monkeypatch.setattr(invoice_batch, "HEARTBEAT_INTERVAL_SECONDS", 0.05)

    result: dict = {}
    thread = _start_batch_worker([("slow.pdf", FAKE_PDF)], result)
    try:
        assert started.wait(5), "解析未启动"
        time.sleep(0.5)  # 远超 0.2s 恢复阈值；有心跳则 updated_at 持续刷新
        with SessionLocal() as db:
            assert inv_svc.recover_stale_batches(db, max_age_seconds=0.2) == 0
            assert db.query(InvoiceBatch).filter(InvoiceBatch.status == "failed").count() == 0
    finally:
        release.set()
        thread.join(timeout=10)

    assert not thread.is_alive(), "解析线程未结束"
    assert "error" not in result, f"解析异常：{result.get('error')!r}"
    assert result["status"] == "parsed"


def test_invoice_parse_heartbeat_runs_during_single_file(
    client, finance_dir, mock_engine, monkeypatch
):
    """心跳由后台线程按固定间隔触发，不依赖「解析完一个文件」的循环边界。"""
    calls: list[int] = []
    release = threading.Event()
    started, slow_parse = _slow_parse_started(release)
    monkeypatch.setattr(invoice_parse, "_parse_pdf_with_engine", slow_parse)
    monkeypatch.setattr(invoice_batch, "HEARTBEAT_INTERVAL_SECONDS", 0.05)
    monkeypatch.setattr(invoice_batch, "_heartbeat_batch", lambda bid: calls.append(bid))

    result: dict = {}
    thread = _start_batch_worker([("slow.pdf", FAKE_PDF)], result)
    try:
        assert started.wait(5), "解析未启动"
        time.sleep(0.3)
        assert len(calls) >= 2  # 单文件解析期间至少两次心跳
    finally:
        release.set()
        thread.join(timeout=10)

    assert not thread.is_alive() and "error" not in result, result
    assert result["status"] == "parsed"


def test_invoice_batch_stats_excludes_failed_batches(client):
    """统计口径与去重/配对一致：failed 批次的明细与净额不参与。"""
    with SessionLocal() as db:
        failed = InvoiceBatch(name="失败批", status=InvoiceBatch.STATUS_FAILED, total_amount=999.0)
        ok = InvoiceBatch(name="正常批", status=InvoiceBatch.STATUS_PARSED, total_amount=100.0)
        db.add_all([failed, ok])
        db.flush()
        db.add_all([
            InvoiceItem(batch_id=failed.id, invoice_number="f1", amount="-50.00",
                       invoice_direction="冲红发票", status=InvoiceItem.STATUS_PARSED),
            InvoiceItem(batch_id=failed.id, invoice_number="f2", amount="10.00",
                       check_code="dup", status=InvoiceItem.STATUS_DUPLICATE),
            InvoiceItem(batch_id=failed.id, invoice_number="f3", status=InvoiceItem.STATUS_FAILED),
            InvoiceItem(batch_id=ok.id, invoice_number="o1", amount="-100.00",
                       invoice_direction="冲红发票", status=InvoiceItem.STATUS_PARSED),
        ])
        db.commit()

    stats = inv_svc.invoice_batch_stats()
    assert stats["items"] == 1  # 只剩正常批的 1 条
    assert stats["duplicates"] == 0
    assert stats["failed"] == 0
    assert (stats["red_matched"], stats["red_unmatched"]) == (0, 1)
    assert stats["net_amount"] == 100.0


def test_invoice_delete_positive_batch_refreshes_external_red(client, finance_dir, mock_engine):
    """删除正票批后，外部红票批的配对/未配对统计必须同步刷新。"""
    mock_engine["pos.pdf"] = _make_fields(invoice_number="7301", amount="300.00")
    b1 = _upload(client, [("pos.pdf", FAKE_PDF)]).json()
    mock_engine["red.pdf"] = _make_fields(
        invoice_number="7302", amount="-300.00", invoice_direction="冲红发票"
    )
    b2 = _upload(client, [("red.pdf", FAKE_PDF)]).json()
    assert (b2["red_matched_count"], b2["red_unmatched_count"]) == (1, 0)

    assert client.delete(f"/api/finance/invoice/batches/{b1['id']}").status_code == 200
    b2_again = client.get(f"/api/finance/invoice/batches/{b2['id']}").json()
    assert b2_again["items"][0]["paired_item_id"] is None
    assert (b2_again["red_matched_count"], b2_again["red_unmatched_count"]) == (0, 1)


def test_invoice_amounts_round_half_up():
    assert inv_svc._parse_amount_strict("1.005") == 1.01
    assert inv_svc._parse_amount_strict("-1.005") == -1.01
    assert inv_svc._parse_amount_strict("2.675") == 2.68
    assert inv_svc.parse_amount("1,000.125") == 1000.13


# ==================== 现金流：年份落库 + 迁移 ====================

STUB_ENGINE = '''
import argparse
from pathlib import Path

p = argparse.ArgumentParser()
p.add_argument("--source-dir", type=Path)
p.add_argument("--output-dir", type=Path)
p.add_argument("--actual-month", type=int)
a = p.parse_args()
out = a.output_dir
out.mkdir(parents=True, exist_ok=True)
(out / f"现金流量滚动预测_已填报_{a.actual_month}月.xlsx").write_text("stub", encoding="utf-8")
print("stub engine done")
'''


@pytest.fixture()
def cash_dir(monkeypatch, tmp_path):
    base = tmp_path / "cash_data"
    engine_dir = tmp_path / "engine"
    engine_dir.mkdir()
    (engine_dir / "cashflow_autofill.py").write_text(STUB_ENGINE, encoding="utf-8")
    monkeypatch.setattr(cash_svc, "get_finance_data_dir", lambda: base)
    monkeypatch.setattr(cash_svc, "engine_dir", lambda: engine_dir)
    return base


def test_cashflow_run_records_explicit_year(client, cash_dir):
    r = client.post(
        "/api/finance/cashflow/runs",
        data={"actual_month": 12, "actual_year": 2025},
        files=[("files", ("a.xlsx", b"stub-xlsx-content"))],
    )
    assert r.status_code == 201, r.text
    run = r.json()
    assert run["actual_month"] == 12 and run["actual_year"] == 2025
    # 详情与列表同样带年份
    assert client.get(f"/api/finance/cashflow/runs/{run['id']}").json()["actual_year"] == 2025
    listed = client.get("/api/finance/cashflow/runs").json()["items"]
    assert next(i for i in listed if i["id"] == run["id"])["actual_year"] == 2025


def test_cashflow_run_year_defaults_to_current(client, cash_dir):
    r = client.post(
        "/api/finance/cashflow/runs",
        data={"actual_month": 9},
        files=[("files", ("a.xlsx", b"stub-xlsx-content"))],
    )
    assert r.status_code == 201, r.text
    assert r.json()["actual_year"] == datetime.now().year


def test_cashflow_year_column_migration_idempotent(client, tmp_path, monkeypatch):
    """宿主轻量迁移：在独立临时库上补列，重复执行不报错、历史行取默认 0（不 DD 共享测试库）。"""
    patches = collect_column_patches()
    assert patches["mod_cashflow_run"]["actual_year"] == "INTEGER DEFAULT 0"

    legacy_engine = create_engine(f"sqlite:///{(tmp_path / 'legacy_cashflow.db').as_posix()}")
    try:
        Base.metadata.create_all(bind=legacy_engine)
        with legacy_engine.begin() as conn:
            conn.exec_driver_sql("ALTER TABLE mod_cashflow_run DROP COLUMN actual_year")
            conn.exec_driver_sql(
                "INSERT INTO mod_cashflow_run "
                "(actual_month, status, input_files, artifacts, run_dir, log_tail, error, "
                " elapsed_sec, created_at, updated_at) "
                "VALUES (9, 'uploaded', '[]', '[]', '', '', '', 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        monkeypatch.setattr(app_main, "engine", legacy_engine)
        app_main._ensure_sqlite_columns()
        app_main._ensure_sqlite_columns()  # 幂等：第二次同列已存在直接跳过
        with legacy_engine.connect() as conn:
            cols = [r[1] for r in conn.exec_driver_sql("PRAGMA table_info(mod_cashflow_run)")]
            year = conn.exec_driver_sql("SELECT actual_year FROM mod_cashflow_run").fetchone()[0]
        assert "actual_year" in cols
        assert year == 0
    finally:
        legacy_engine.dispose()

    # 历史行（year=0）输出时按创建年份兜底，不显示 0
    legacy = CashflowRun(actual_month=9, actual_year=0, created_at=datetime(2025, 3, 1), status="ok")
    assert cash_svc._out(legacy)["actual_year"] == 2025


def _create_cash_run(client, **data) -> dict:
    payload = {"actual_month": 9, **data}
    resp = client.post(
        "/api/finance/cashflow/runs",
        data=payload,
        files=[("files", ("a.xlsx", b"stub-xlsx-content"))],
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


ZERO_ARTIFACT_ENGINE = '''
import argparse
from pathlib import Path

p = argparse.ArgumentParser()
p.add_argument("--source-dir", type=Path)
p.add_argument("--output-dir", type=Path)
p.add_argument("--actual-month", type=int)
a = p.parse_args()
out = a.output_dir
out.mkdir(parents=True, exist_ok=True)
(out / "空产物.xlsx").write_bytes(b"")
print("zero artifact done")
'''

MIXED_ARTIFACT_ENGINE = '''
import argparse
from pathlib import Path

p = argparse.ArgumentParser()
p.add_argument("--source-dir", type=Path)
p.add_argument("--output-dir", type=Path)
p.add_argument("--actual-month", type=int)
a = p.parse_args()
out = a.output_dir
out.mkdir(parents=True, exist_ok=True)
(out / "空文件.txt").write_bytes(b"")
(out / "有效产物.txt").write_text("data", encoding="utf-8")
print("mixed artifact done")
'''


def test_cashflow_zero_byte_artifacts_fail_503(client, cash_dir, tmp_path):
    """引擎 exit 0 但产物全为 0 字节：必须 503 + failed，与 POS 同口径。"""
    (tmp_path / "engine" / "cashflow_autofill.py").write_text(ZERO_ARTIFACT_ENGINE, encoding="utf-8")
    run = _create_cash_run(client)
    ex = client.post(f"/api/finance/cashflow/runs/{run['id']}/execute")
    assert ex.status_code == 503, ex.text
    assert "0 字节" in ex.json()["detail"] or "有效产物" in ex.json()["detail"]
    detail = client.get(f"/api/finance/cashflow/runs/{run['id']}").json()
    assert detail["status"] == "failed"
    assert "0 字节" in detail["error"] or "有效产物" in detail["error"]


def test_cashflow_mixed_artifacts_skip_zero_byte(client, cash_dir, tmp_path):
    """存在有效产物时仍算成功，但 0 字节文件不得登记为产物。"""
    (tmp_path / "engine" / "cashflow_autofill.py").write_text(MIXED_ARTIFACT_ENGINE, encoding="utf-8")
    run = _create_cash_run(client)
    ex = client.post(f"/api/finance/cashflow/runs/{run['id']}/execute")
    assert ex.status_code == 200, ex.text
    body = ex.json()
    assert body["status"] == "ok"
    assert [a["name"] for a in body["artifacts"]] == ["有效产物.txt"]


def test_cashflow_failed_rerun_clears_previous_artifacts(client, cash_dir, tmp_path):
    """重跑产物为空时不得沿用上一次成功运行的产物清单。"""
    run = _create_cash_run(client)
    ok = client.post(f"/api/finance/cashflow/runs/{run['id']}/execute")
    assert ok.status_code == 200 and ok.json()["artifacts"]

    (tmp_path / "engine" / "cashflow_autofill.py").write_text(ZERO_ARTIFACT_ENGINE, encoding="utf-8")
    failed = client.post(f"/api/finance/cashflow/runs/{run['id']}/execute")
    assert failed.status_code == 503
    detail = client.get(f"/api/finance/cashflow/runs/{run['id']}").json()
    assert detail["status"] == "failed" and detail["artifacts"] == []


NONZERO_ENGINE = '''
import sys
sys.exit(2)
'''


def test_cashflow_nonzero_exit_rerun_clears_artifacts(client, cash_dir, tmp_path):
    """成功→非零退出重跑：产物清单必须清空，下载不得给上一次的旧产物。"""
    run = _create_cash_run(client)
    ok = client.post(f"/api/finance/cashflow/runs/{run['id']}/execute")
    assert ok.status_code == 200 and ok.json()["artifacts"]
    assert client.get(f"/api/finance/cashflow/runs/{run['id']}/download").status_code == 200

    (tmp_path / "engine" / "cashflow_autofill.py").write_text(NONZERO_ENGINE, encoding="utf-8")
    failed = client.post(f"/api/finance/cashflow/runs/{run['id']}/execute")
    assert failed.status_code == 503, failed.text
    detail = client.get(f"/api/finance/cashflow/runs/{run['id']}").json()
    assert detail["status"] == "failed" and detail["artifacts"] == []
    assert "退出码" in detail["error"]
    assert client.get(f"/api/finance/cashflow/runs/{run['id']}/download").status_code == 409

    # 再次成功重跑：新产物恢复可下载，状态机不残留 failed
    (tmp_path / "engine" / "cashflow_autofill.py").write_text(STUB_ENGINE, encoding="utf-8")
    again = client.post(f"/api/finance/cashflow/runs/{run['id']}/execute")
    assert again.status_code == 200 and again.json()["artifacts"]
    assert client.get(f"/api/finance/cashflow/runs/{run['id']}/download").status_code == 200


def test_cashflow_timeout_rerun_clears_artifacts(client, cash_dir, monkeypatch):
    """成功→超时重跑：清空产物清单，下载 409。"""
    run = _create_cash_run(client)
    assert client.post(f"/api/finance/cashflow/runs/{run['id']}/execute").status_code == 200

    def timeout_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="cashflow_autofill.py", timeout=1)

    monkeypatch.setattr(cash_svc.subprocess, "run", timeout_run)
    failed = client.post(f"/api/finance/cashflow/runs/{run['id']}/execute")
    assert failed.status_code == 503, failed.text
    detail = client.get(f"/api/finance/cashflow/runs/{run['id']}").json()
    assert detail["status"] == "failed" and detail["artifacts"] == []
    assert "超时" in detail["error"]
    assert client.get(f"/api/finance/cashflow/runs/{run['id']}/download").status_code == 409


def test_cashflow_unexpected_error_rerun_clears_artifacts(client, cash_dir, monkeypatch):
    """成功→兜底异常重跑：清空产物清单，下载 409。"""
    run = _create_cash_run(client)
    assert client.post(f"/api/finance/cashflow/runs/{run['id']}/execute").status_code == 200

    def boom(_data):
        raise RuntimeError("合成解码失败")

    monkeypatch.setattr(cash_svc, "decode_engine_output", boom)
    failed = client.post(f"/api/finance/cashflow/runs/{run['id']}/execute")
    assert failed.status_code == 503, failed.text
    detail = client.get(f"/api/finance/cashflow/runs/{run['id']}").json()
    assert detail["status"] == "failed" and detail["artifacts"] == []
    assert "执行异常" in detail["error"]
    assert client.get(f"/api/finance/cashflow/runs/{run['id']}/download").status_code == 409


def test_cashflow_engine_missing_rerun_clears_artifacts(client, cash_dir, monkeypatch):
    """成功→引擎缺失重跑：同样落 failed 且清空旧产物，不保留可下载的上次结果。"""
    run = _create_cash_run(client)
    assert client.post(f"/api/finance/cashflow/runs/{run['id']}/execute").status_code == 200

    monkeypatch.setattr(cash_svc, "engine_dir", lambda: Path("Z:/no/such/engine"))
    failed = client.post(f"/api/finance/cashflow/runs/{run['id']}/execute")
    assert failed.status_code == 503, failed.text
    detail = client.get(f"/api/finance/cashflow/runs/{run['id']}").json()
    assert detail["status"] == "failed" and detail["artifacts"] == []
    assert "引擎" in detail["error"]
    assert client.get(f"/api/finance/cashflow/runs/{run['id']}/download").status_code == 409


def test_cashflow_stale_running_reset_clears_artifacts(client, monkeypatch, tmp_path):
    """崩溃遗留 running 自动置 failed 时同样清空旧产物清单。"""
    old = datetime.now() - timedelta(hours=3)
    with SessionLocal() as db:
        run = CashflowRun(
            actual_year=2026, actual_month=9, status="running",
            artifacts=[{"name": "上次.xlsx", "size": 10, "path": "stale"}],
            created_at=old, updated_at=old,
        )
        db.add(run)
        db.commit()
        run_id = run.id

    monkeypatch.setattr(cash_svc, "engine_dir", lambda: tmp_path / "missing_engine")
    with SessionLocal() as db:
        with pytest.raises(cash_svc.EngineUnavailableError):
            cash_svc.execute_run(db, run_id)
    with SessionLocal() as db:
        row = db.get(CashflowRun, run_id)
        assert row.status == "failed" and row.artifacts == []


def test_cashflow_engine_output_decoded_gb18030(client, cash_dir, monkeypatch):
    """子进程输出按字节捕获：UTF-8 失败回退 GB18030，中文错误不丢。"""
    run = _create_cash_run(client)

    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(
            args=args, returncode=1,
            stdout="引擎中文报错：模板缺失".encode("gb18030"),
            stderr=b"",
        )

    monkeypatch.setattr(cash_svc.subprocess, "run", fake_run)
    ex = client.post(f"/api/finance/cashflow/runs/{run['id']}/execute")
    assert ex.status_code == 503, ex.text
    detail = client.get(f"/api/finance/cashflow/runs/{run['id']}").json()
    assert detail["status"] == "failed"
    assert "引擎中文报错：模板缺失" in detail["log_tail"]


def test_cashflow_summary_tool_includes_actual_year(client, cash_dir):
    """Agent 摘要与列表/详情口径一致，包含实际年份。"""
    _create_cash_run(client, actual_year=2024)
    latest = cash_svc.cashflow_summary_tool()["latest"]
    assert latest["actual_year"] == 2024


# ==================== 费用：金额 HALF_UP ====================

def test_expense_amounts_round_half_up():
    assert expense_svc._safe_amount("1.005") == 1.01
    assert expense_svc._safe_amount(2.675) == 2.68
    assert expense_svc._safe_amount("-0.005") == -0.01
    assert expense_svc._safe_amount("1,000.125") == 1000.13
    assert expense_svc._safe_amount("not-a-number") == 0.0
