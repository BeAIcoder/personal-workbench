"""Task 5：发票、银行流水、数据仓与执行管线的回归（全合成数据）。

覆盖台账 L-080～L-096 的可闭环项：
- 发票：人工修正作废旧 ZIP、跨月目录各自清单、失败批次不阻塞重传、ZIP 成员
  Windows 安全、文件占用 409、不可解析金额告警、汇总清单项目名公式防护；
- 银行流水：有界读取、双金额/无金额/坏日期明确拒绝、尾号最长匹配、多 sheet/封面、
  示例配置、账号脱敏；
- 现金流/POS/费用：批次上限、失败 rollback、成功清旧 error、run_dir 边界、
  产物同名防覆盖、陈旧 running 复位、执行互斥；
- 看板：GBK 忙态 409、schtasks 超时、仅非忙且通道不可用时回退；
- 数据仓：DuckDB 异常映射、NaN/Inf、合法 CTE/LIMIT、编码文本保留、mojibake、
  多 sheet、质量画像有界；
- 配置：纯空白引擎路径归一。

所有外部引擎/计划任务均以 monkeypatch 替身替代，不读真实数据、不触发外部进程。
"""
import asyncio
import io
import math
import os
import re
import subprocess
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

import openpyxl
import pandas as pd
import pytest
from openpyxl import load_workbook
from sqlalchemy import text

from app.config import Settings, settings
from app.database import Base, SessionLocal, engine
from app.modules.finance_bankflow import router as bf_router
from app.modules.finance_bankflow import service as bf
from app.modules.finance_board import service as board_svc
from app.modules.finance_cashflow import router as cf_router
from app.modules.finance_cashflow import service as cf_svc
from app.modules.finance_expense import router as expense_router
from app.modules.finance_expense import service as expense_svc
from app.modules.finance_invoice import engine as invoice_engine
from app.modules.finance_invoice import projects as invoice_projects
from app.modules.finance_invoice import invoice_export, invoice_parse
from app.modules.finance_invoice import service as inv_svc
from app.modules.finance_pos import router as pos_router
from app.modules.finance_pos import service as pos_svc
from app.modules.finance_vault import service as vault_svc

FAKE_PDF = b"%PDF-1.4 synthetic-task5-bytes"


@pytest.fixture(autouse=True)
def _ensure_tables():
    """非 client 用例同样需要表（conftest 只对 client 用例建表）。"""
    engine.dispose()
    Base.metadata.create_all(bind=engine)
    yield


# ---------------------------------------------------------------------------
# 发票
# ---------------------------------------------------------------------------

@pytest.fixture()
def invoice_env(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "finance_data_dir", str(tmp_path / "finance"))
    monkeypatch.setattr(invoice_projects, "load_project_rules", lambda: [])
    monkeypatch.setattr(invoice_parse, "_require_engine", lambda: None)
    canned: dict[str, dict] = {}

    def fake_parse(pdf_path):
        fields = canned.get(Path(pdf_path).name)
        return inv_svc.ParsedInvoice(**fields) if fields is not None else None

    monkeypatch.setattr(invoice_parse, "_parse_pdf_with_engine", fake_parse)
    return canned


def _inv_fields(**kw) -> dict:
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


def _inv_upload(client, files, name=""):
    multipart = [("files", (fname, content, "application/pdf")) for fname, content in files]
    data = {"name": name} if name else {}
    return client.post("/api/finance/invoice/batches", files=multipart, data=data)


def _inv_export(client, batch_id):
    return client.post(f"/api/finance/invoice/batches/{batch_id}/export")


def test_invoice_patch_after_export_invalidates_old_zip(client, invoice_env):
    invoice_env["a.pdf"] = _inv_fields()
    batch = _inv_upload(client, [("a.pdf", FAKE_PDF)]).json()
    assert _inv_export(client, batch["id"]).status_code == 200
    assert client.get(f"/api/finance/invoice/batches/{batch['id']}/download").status_code == 200

    item_id = batch["items"][0]["id"]
    patched = client.patch(f"/api/finance/invoice/items/{item_id}", json={"project_name": "人工项目"})
    assert patched.status_code == 200

    detail = client.get(f"/api/finance/invoice/batches/{batch['id']}").json()
    assert detail["status"] == "parsed" and detail["export_file"] == ""
    assert client.get(f"/api/finance/invoice/batches/{batch['id']}/download").status_code == 409
    rows = client.get(f"/api/finance/invoice/batches/{batch['id']}/projects").json()
    assert all(row["zip_ready"] is False for row in rows)
    assert client.get(
        f"/api/finance/invoice/batches/{batch['id']}/projects/download?project=人工项目"
    ).status_code == 409
    # 旧导出文件已被清理（不是只改数据库标记）
    outbox = Path(settings.finance_data_dir) / "finance_invoice" / "outbox"
    assert not list(outbox.glob(f"batch_{batch['id']}_export.zip"))


def test_invoice_cross_month_each_directory_has_own_list(client, invoice_env):
    invoice_env["a.pdf"] = _inv_fields(invoice_number="8001", amount="100.00", invoice_date="2026年10月01日")
    invoice_env["b.pdf"] = _inv_fields(invoice_number="8002", amount="50.00", invoice_date="2026年11月02日")
    batch = _inv_upload(client, [("a.pdf", FAKE_PDF), ("b.pdf", FAKE_PDF)]).json()
    assert _inv_export(client, batch["id"]).status_code == 200
    zip_path = Path(settings.finance_data_dir) / "finance_invoice" / "outbox" / f"batch_{batch['id']}_export.zip"
    with zipfile.ZipFile(zip_path) as zf:
        names = set(zf.namelist())
        assert "26年10月示例项目/发票清单.xlsx" in names
        assert "26年11月示例项目/发票清单.xlsx" in names
        october = load_workbook(io.BytesIO(zf.read("26年10月示例项目/发票清单.xlsx"))).active
        november = load_workbook(io.BytesIO(zf.read("26年11月示例项目/发票清单.xlsx"))).active
    assert october.cell(row=2, column=2).value == "8001"
    assert october.cell(row=4, column=3).value == 100.0
    assert november.cell(row=2, column=2).value == "8002"
    assert november.cell(row=4, column=3).value == 50.0
    # 11 月清单不得混入 10 月票（旧实现只写首月目录且是全量）
    assert november.cell(row=2, column=1).value == 1


def test_invoice_failed_batch_does_not_block_reupload(client, invoice_env):
    invoice_env["a.pdf"] = _inv_fields(invoice_number="8001")
    batch = _inv_upload(client, [("a.pdf", FAKE_PDF)]).json()
    assert batch["items"][0]["status"] == "parsed"

    from app.modules.finance_invoice.models import InvoiceBatch

    with SessionLocal() as db:
        row = db.get(InvoiceBatch, batch["id"])
        row.status = InvoiceBatch.STATUS_FAILED
        row.error = "合成失败"
        db.commit()

    again = _inv_upload(client, [("a.pdf", FAKE_PDF)])
    assert again.status_code == 201
    body = again.json()
    assert body["duplicate_count"] == 0
    assert body["items"][0]["status"] == "parsed"


def test_invoice_zip_members_must_be_windows_safe(client, invoice_env):
    invoice_env["CON.pdf"] = _inv_fields()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("CON.pdf", FAKE_PDF)
    resp = _inv_upload(client, [("pack.zip", buf.getvalue())])
    assert resp.status_code == 400
    assert "非法文件名" in resp.json()["detail"]

    buf2 = io.BytesIO()
    with zipfile.ZipFile(buf2, "w") as zf:
        zf.writestr("a:b.pdf", FAKE_PDF)
    resp2 = _inv_upload(client, [("pack.zip", buf2.getvalue())])
    assert resp2.status_code == 400
    assert "非法" in resp2.json()["detail"]


def test_invoice_export_artifact_locked_returns_409(client, invoice_env, monkeypatch):
    invoice_env["a.pdf"] = _inv_fields()
    batch = _inv_upload(client, [("a.pdf", FAKE_PDF)]).json()
    assert _inv_export(client, batch["id"]).status_code == 200

    def locked(*args, **kwargs):
        raise PermissionError("synthetic lock")

    monkeypatch.setattr(invoice_export.shutil, "rmtree", locked)
    resp = _inv_export(client, batch["id"])
    assert resp.status_code == 409
    assert "占用" in resp.json()["detail"]


def test_invoice_unparseable_amount_is_warned(client, invoice_env):
    invoice_env["a.pdf"] = _inv_fields(invoice_number="8101", amount="无法解析")
    body = _inv_upload(client, [("a.pdf", FAKE_PDF)]).json()
    detail = client.get(f"/api/finance/invoice/batches/{body['id']}").json()
    assert detail["amount_warning_count"] == 1
    assert detail["amount_warnings"][0]["invoice_number"] == "8101"

    result = _inv_export(client, body["id"]).json()
    assert result["amount_warning_count"] == 1
    assert any("金额" in w for w in result["warnings"])


def test_invoice_reexport_is_idempotent(client, invoice_env):
    """重复导出（明细未变）：清理旧目录后重建，文件清单与汇总金额完全一致，不产生 _1 副本。"""
    invoice_env["a.pdf"] = _inv_fields(invoice_number="8201", amount="100.00")
    invoice_env["b.pdf"] = _inv_fields(invoice_number="8202", amount="50.00")
    batch = _inv_upload(client, [("a.pdf", FAKE_PDF), ("b.pdf", FAKE_PDF)]).json()
    assert _inv_export(client, batch["id"]).status_code == 200
    zip_path = Path(settings.finance_data_dir) / "finance_invoice" / "outbox" / f"batch_{batch['id']}_export.zip"
    with zipfile.ZipFile(zip_path) as zf:
        first_names = sorted(zf.namelist())
        first_summary = zf.read("汇总清单.xlsx")
    assert _inv_export(client, batch["id"]).status_code == 200
    with zipfile.ZipFile(zip_path) as zf:
        second_names = sorted(zf.namelist())
        second_summary = zf.read("汇总清单.xlsx")
    assert first_names == second_names
    assert not any(re.search(r"_\d+\.pdf$", n) for n in second_names)
    wb1 = load_workbook(io.BytesIO(first_summary)).active
    wb2 = load_workbook(io.BytesIO(second_summary)).active
    assert [[c.value for c in row] for row in wb1.iter_rows()] == [
        [c.value for c in row] for row in wb2.iter_rows()
    ]


def test_invoice_summary_project_name_is_formula_safe(client, invoice_env):
    invoice_env["a.pdf"] = _inv_fields(project_name="=1+1")
    body = _inv_upload(client, [("a.pdf", FAKE_PDF)]).json()
    assert _inv_export(client, body["id"]).status_code == 200
    zip_path = Path(settings.finance_data_dir) / "finance_invoice" / "outbox" / f"batch_{body['id']}_export.zip"
    with zipfile.ZipFile(zip_path) as zf:
        wb = load_workbook(io.BytesIO(zf.read("汇总清单.xlsx")))
    assert wb.active.cell(row=2, column=2).value == "'=1+1"


# ---------------------------------------------------------------------------
# 银行流水
# ---------------------------------------------------------------------------

_FLOW_HEADERS = [
    "组织", "银行", "开户银行", "账号", "币种", "交易日期", "收入", "支出",
    "当前余额", "用途", "对方户名", "对方账号", "来源", "资金交易生成状态",
    "附言(对账码)", "是否已对账", "银行交易流水号", "单证号码", "备注", "导出标识",
    "账户名称", "资金系统单据号", "被代理账号", "被代理账户开户行名", "被代理行号", "被代理账户名",
]

_ACC = "5500010000001111"


def _flow_row(d, income, expense, seq, account=_ACC, bank="CCB-建设银行", remark="", use="合成用途"):
    row = ["P1", bank, "测试支行", account, "CNY-人民币", d, income, expense, 1000.0,
           use, "合成对方", "6222000000000000", "银企直联", "未生成", remark, "×",
           seq, "", "", "已导出", "合成户名", "", "", "", "", ""]
    return row + [""] * (26 - len(row))


def _flow_xlsx(rows, headers=None):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "流水"
    ws.append(headers or _FLOW_HEADERS)
    for row in rows:
        ws.append(row)
    out = io.BytesIO()
    wb.save(out)
    wb.close()
    return out.getvalue()


@pytest.fixture()
def bank_env(tmp_path, monkeypatch):
    cfg = tmp_path / "config.local.yaml"
    cfg.write_text(
        'bank_accounts:\n  "5971": { ent: "TESTH001", name: "短尾" }\n'
        '  "0445971": { ent: "TESTH002", name: "长尾" }\n'
        '  "%s": { ent: "TESTH003", name: "全号" }\n' % _ACC,
        encoding="utf-8",
    )
    monkeypatch.setattr(bf, "_CONFIG_PATH", cfg)
    return cfg


def test_bankflow_preview_uses_bounded_reader(client, bank_env, monkeypatch):
    calls: list[int] = []

    async def spy(file, max_bytes, chunk_size=1024 * 1024):
        calls.append(max_bytes)
        raise bf_router.UploadTooLargeError(max_bytes)

    monkeypatch.setattr(bf_router, "read_upload_limited", spy)
    resp = client.post("/api/finance/bankflow/preview", files={"file": ("f.xlsx", b"x")})
    assert resp.status_code == 400
    assert calls and calls[0] == bf_router._MAX_UPLOAD


def test_bankflow_preview_reports_invalid_rows(client, bank_env):
    rows = [
        _flow_row("2026-10-01", "10", "5", "S1"),      # 双金额
        _flow_row("2026-10-02", None, None, "S2"),     # 无金额
        _flow_row("2026-13-45", "3", None, "S3"),      # 坏日期
        _flow_row("2026-10-03", "7", None, "S4"),      # 正常
    ]
    resp = client.post("/api/finance/bankflow/preview", files={"file": ("f.xlsx", _flow_xlsx(rows))})
    assert resp.status_code == 200, resp.text
    acc = resp.json()["accounts"][0]
    assert acc["rows"] == 1
    assert acc["invalid_rows"] == 3
    assert acc["invalid"]["amount_conflict"] == 1
    assert acc["invalid"]["missing_amount"] == 1
    assert acc["invalid"]["bad_date"] == 1
    assert resp.json()["warnings"]


@pytest.mark.parametrize(
    ("row", "needle"),
    [
        (_flow_row("2026-10-01", "10", "5", "S1"), "收入与支出"),
        (_flow_row("2026-10-02", None, None, "S2"), "金额"),
        (_flow_row("2026-13-45", "3", None, "S3"), "交易日期"),
    ],
)
def test_bankflow_template_rejects_bad_rows(client, bank_env, row, needle):
    resp = client.post(
        "/api/finance/bankflow/template",
        files={"file": ("f.xlsx", _flow_xlsx([row]))},
        data={"account": bf.mask_account(_ACC), "date_start": "", "date_end": ""},
    )
    assert resp.status_code == 400, resp.text
    assert needle in resp.json()["detail"]


def test_bankflow_template_never_exports_blank_required(client, bank_env):
    rows = [_flow_row("2026-10-01", "10", None, "S1"), _flow_row("2026-10-02", None, "5", "S2")]
    resp = client.post(
        "/api/finance/bankflow/template",
        files={"file": ("f.xlsx", _flow_xlsx(rows))},
        data={"account": bf.mask_account(_ACC), "date_start": "", "date_end": ""},
    )
    assert resp.status_code == 200, resp.text
    wb = openpyxl.load_workbook(io.BytesIO(resp.content))
    ws = wb["银行对账单"]
    for row_no in range(10, 12):
        assert ws.cell(row=row_no, column=5).value  # 交易日期
        assert ws.cell(row=row_no, column=8).value in ("收入", "支出")
        assert ws.cell(row=row_no, column=9).value not in (None, "")
        assert ws.cell(row=row_no, column=10).value  # 用途（必填）
        assert ws.cell(row=row_no, column=11).value  # 摘要（必填，可回退用途）
        assert ws.cell(row=row_no, column=12).value  # 流水号
    wb.close()


def test_bankflow_remark_column_uses_attachment_text(client, bank_env):
    """附言(对账码)解析后必须写入模板「摘要」列（旧实现只重复用途、丢弃附言）。"""
    row = _flow_row("2026-10-01", "10", None, "S1", remark="附言-合成摘要")
    resp = client.post(
        "/api/finance/bankflow/template",
        files={"file": ("f.xlsx", _flow_xlsx([row]))},
        data={"account": bf.mask_account(_ACC), "date_start": "", "date_end": ""},
    )
    assert resp.status_code == 200, resp.text
    ws = openpyxl.load_workbook(io.BytesIO(resp.content))["银行对账单"]
    assert ws.cell(row=10, column=10).value == "合成用途"
    assert ws.cell(row=10, column=11).value == "附言-合成摘要"
    ws.parent.close()


def test_bankflow_suffix_mapping_uses_longest_match(bank_env):
    acc_map = bf.load_account_map(bank_env)
    assert bf.resolve_account(acc_map, "99990445971")["ent"] == "TESTH002"
    assert bf.resolve_account(acc_map, "99995971")["ent"] == "TESTH001"


def test_bankflow_multisheet_with_cover(bank_env):
    wb = openpyxl.Workbook()
    cover = wb.active
    cover.title = "封面"
    cover.append(["银行流水导出"])
    ws = wb.create_sheet("流水明细")
    ws.append(_FLOW_HEADERS)
    ws.append(_flow_row("2026-10-01", "10", None, "S1"))
    buf = io.BytesIO()
    wb.save(buf)
    wb.close()
    rows = bf.parse_flow(buf.getvalue())
    assert len(rows) == 1 and rows[0]["income"] == 10.0


def test_bankflow_example_config_has_no_real_values(bank_env):
    example = Path(bf.__file__).resolve().parent / "config.local.example.yaml"
    assert example.is_file()
    text = example.read_text(encoding="utf-8")
    assert "bank_accounts" in text and "示例" in text
    assert "H003" not in text  # 不得带真实账套编码


def test_bankflow_accounts_endpoint_masks_full_number(client, bank_env):
    items = client.get("/api/finance/bankflow/accounts").json()["items"]
    target = next(i for i in items if i["tail"] == "1111")
    assert target["account"].endswith("1111")
    assert "550001000000" not in target["account"]


# ---------------------------------------------------------------------------
# 现金流 / POS / 费用
# ---------------------------------------------------------------------------

CF_STUB = '''
import argparse
from pathlib import Path
p = argparse.ArgumentParser()
p.add_argument("--source-dir", type=Path)
p.add_argument("--output-dir", type=Path)
p.add_argument("--actual-month", type=int)
a = p.parse_args()
out = a.output_dir
out.mkdir(parents=True, exist_ok=True)
(out / "填报.xlsx").write_text("filled", encoding="utf-8")
sub1 = out / "子目录A"; sub1.mkdir()
sub2 = out / "子目录B"; sub2.mkdir()
(sub1 / "同名.txt").write_text("A", encoding="utf-8")
(sub2 / "同名.txt").write_text("B", encoding="utf-8")
print("stub done")
'''

POS_LEDGER = '''
import argparse
from pathlib import Path
p = argparse.ArgumentParser()
p.add_argument("--root", type=Path)
p.add_argument("--output", type=Path)
a = p.parse_args()
a.output.parent.mkdir(parents=True, exist_ok=True)
a.output.write_text("ledger", encoding="utf-8")
'''

POS_ANALYZE = '''
import argparse
from pathlib import Path
p = argparse.ArgumentParser()
p.add_argument("--root", type=Path)
p.add_argument("--project", default=None)
p.add_argument("--pos", type=Path, default=None)
a = p.parse_args()
(a.root / "POS项目台账.xlsx").write_text("input-copy", encoding="utf-8")
'''


@pytest.fixture()
def cf_env(tmp_path, monkeypatch):
    base = tmp_path / "cf_data"
    engine_dir = tmp_path / "cf_engine"
    engine_dir.mkdir()
    (engine_dir / "cashflow_autofill.py").write_text(CF_STUB, encoding="utf-8")
    monkeypatch.setattr(cf_svc, "get_finance_data_dir", lambda: base)
    monkeypatch.setattr(cf_svc, "engine_dir", lambda: engine_dir)
    return base


@pytest.fixture()
def pos_env(tmp_path, monkeypatch):
    base = tmp_path / "pos_data"
    engine_dir = tmp_path / "pos_engine"
    engine_dir.mkdir()
    (engine_dir / "generate_pos_ledger.py").write_text(POS_LEDGER, encoding="utf-8")
    (engine_dir / "analyze_pos_reconciliation.py").write_text(POS_ANALYZE, encoding="utf-8")
    monkeypatch.setattr(pos_svc, "get_finance_data_dir", lambda: base)
    monkeypatch.setattr(pos_svc, "engine_dir", lambda: engine_dir)
    return base


def _post_cf(client, *files):
    return client.post(
        "/api/finance/cashflow/runs",
        data={"actual_month": 9},
        files=[("files", (name, content)) for name, content in files],
    )


def test_cashflow_batch_total_limit(client, cf_env, monkeypatch):
    monkeypatch.setattr(cf_svc, "MAX_BATCH_BYTES", 10)
    resp = _post_cf(client, ("a.xlsx", b"123456"), ("b.xlsx", b"123456"))
    assert resp.status_code == 400
    assert "批次总大小" in resp.json()["detail"]


def test_cashflow_file_count_limit(client, cf_env, monkeypatch):
    monkeypatch.setattr(cf_svc, "MAX_FILES_PER_BATCH", 1)
    resp = _post_cf(client, ("a.xlsx", b"1"), ("b.xlsx", b"2"))
    assert resp.status_code == 400
    assert "文件数" in resp.json()["detail"]


def test_cashflow_success_clears_previous_error(client, cf_env):
    run = _post_cf(client, ("a.xlsx", b"1")).json()
    from app.modules.finance_cashflow.models import CashflowRun

    with SessionLocal() as db:
        row = db.get(CashflowRun, run["id"])
        row.error = "旧错误"
        db.commit()
    resp = client.post(f"/api/finance/cashflow/runs/{run['id']}/execute")
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "ok"
    assert resp.json()["error"] == ""


def test_cashflow_run_dir_must_stay_inside_root(client, cf_env, tmp_path):
    run = _post_cf(client, ("a.xlsx", b"1")).json()
    outside = tmp_path / "outside"
    outside.mkdir()
    from app.modules.finance_cashflow.models import CashflowRun

    with SessionLocal() as db:
        row = db.get(CashflowRun, run["id"])
        row.run_dir = str(outside)
        db.commit()
    resp = client.post(f"/api/finance/cashflow/runs/{run['id']}/execute")
    assert resp.status_code == 503
    assert "工作目录" in resp.json()["detail"]
    assert not list(outside.iterdir())


def test_cashflow_artifacts_keep_subdirectory_names(client, cf_env):
    run = _post_cf(client, ("a.xlsx", b"1")).json()
    body = client.post(f"/api/finance/cashflow/runs/{run['id']}/execute").json()
    names = {a["name"] for a in body["artifacts"]}
    assert {"子目录A/同名.txt", "子目录B/同名.txt"} <= names
    a = client.get(f"/api/finance/cashflow/runs/{run['id']}/artifacts/子目录A/同名.txt")
    b = client.get(f"/api/finance/cashflow/runs/{run['id']}/artifacts/子目录B/同名.txt")
    assert a.status_code == 200 and a.text == "A"
    assert b.status_code == 200 and b.text == "B"


def test_cashflow_mark_failed_recovers_from_broken_session(cf_env):
    from app.modules.finance_cashflow.models import CashflowRun

    with SessionLocal() as db:
        run = CashflowRun(actual_month=9, status="running", run_dir="x")
        db.add(run)
        db.commit()
        run_id = run.id
        with pytest.raises(Exception):
            db.execute(text("SELECT * FROM table_that_does_not_exist"))
        cf_svc._mark_failed(db, run_id, "合成失败", 1.5)
    with SessionLocal() as db:
        row = db.get(CashflowRun, run_id)
        assert row.status == "failed" and "合成失败" in row.error


def test_pos_success_clears_previous_error(client, pos_env):
    run = client.post("/api/finance/pos/runs", files=[("files", ("a.xlsx", b"1"))]).json()
    from app.modules.finance_pos.models import PosRun

    with SessionLocal() as db:
        row = db.get(PosRun, run["id"])
        row.error = "旧错误"
        db.commit()
    resp = client.post(f"/api/finance/pos/runs/{run['id']}/execute")
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "ok"
    assert resp.json()["error"] == ""


def test_pos_run_dir_must_stay_inside_root(client, pos_env, tmp_path):
    run = client.post("/api/finance/pos/runs", files=[("files", ("a.xlsx", b"1"))]).json()
    outside = tmp_path / "outside_pos"
    outside.mkdir()
    from app.modules.finance_pos.models import PosRun

    with SessionLocal() as db:
        row = db.get(PosRun, run["id"])
        row.run_dir = str(outside)
        db.commit()
    resp = client.post(f"/api/finance/pos/runs/{run['id']}/execute")
    assert resp.status_code == 503
    assert "工作目录" in resp.json()["detail"]
    assert not list(outside.iterdir())


def test_pos_artifacts_do_not_overwrite_same_name(client, pos_env):
    run = client.post("/api/finance/pos/runs", files=[("files", ("a.xlsx", b"1"))]).json()
    body = client.post(f"/api/finance/pos/runs/{run['id']}/execute").json()
    names = [a["name"] for a in body["artifacts"] if a["name"].startswith("POS项目台账")]
    assert len(names) == 2 and len(set(names)) == 2


def test_pos_mark_failed_recovers_from_broken_session(pos_env):
    from app.modules.finance_pos.models import PosRun

    with SessionLocal() as db:
        run = PosRun(status="running", run_dir="x")
        db.add(run)
        db.commit()
        run_id = run.id
        with pytest.raises(Exception):
            db.execute(text("SELECT * FROM table_that_does_not_exist"))
        pos_svc._mark_failed(db, run_id, "合成失败", 1.0)
    with SessionLocal() as db:
        row = db.get(PosRun, run_id)
        assert row.status == "failed" and "合成失败" in row.error


def test_expense_amount_conversion_is_tolerant():
    assert expense_svc._safe_amount("1,234.56") == 1234.56
    assert expense_svc._safe_amount(float("nan")) == 0.0
    assert expense_svc._safe_amount(float("inf")) == 0.0
    assert expense_svc._safe_amount("坏值") == 0.0
    assert expense_svc._safe_amount(None) == 0.0


def test_expense_stale_running_is_reset():
    from app.modules.finance_expense.models import ExpenseRun

    with SessionLocal() as db:
        old = ExpenseRun(original_filename="旧.xlsx", status="running")
        old.created_at = datetime.now() - timedelta(hours=3)
        fresh = ExpenseRun(original_filename="新.xlsx", status="running")
        db.add_all([old, fresh])
        db.commit()
        expense_svc.recover_stale_runs(db)
        db.refresh(old)
        db.refresh(fresh)
        assert old.status == "failed"
        assert fresh.status == "running"


def test_expense_mark_failed_recovers_from_broken_session():
    from app.modules.finance_expense.models import ExpenseRun

    with SessionLocal() as db:
        run = ExpenseRun(original_filename="坏会话.xlsx", status="running")
        db.add(run)
        db.commit()
        run_id = run.id
        with pytest.raises(Exception):
            db.execute(text("SELECT * FROM table_that_does_not_exist"))
        expense_svc._mark_run_failed(db, run_id, 1.0)
    with SessionLocal() as db:
        row = db.get(ExpenseRun, run_id)
        assert row.status == "failed"


def test_expense_execution_lock_rejects_concurrent_run(tmp_path):
    from app.modules.finance_expense.models import ExpenseRun

    path = tmp_path / "x.xlsx"
    path.write_bytes(b"not read in this test")
    with SessionLocal() as db:
        before = db.query(ExpenseRun).count()
        assert expense_svc._EXEC_LOCK.acquire(blocking=False)
        try:
            with pytest.raises(expense_svc.RunBusyError):
                expense_svc.classify_file(db, "x.xlsx", path)
        finally:
            expense_svc._EXEC_LOCK.release()
        assert db.query(ExpenseRun).count() == before


def test_expense_unexpected_error_maps_503_and_cleans_upload(client, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "finance_data_dir", str(tmp_path / "finance"))
    monkeypatch.setattr(expense_svc, "ensure_engine_ready", lambda: None)

    def boom(db, original, stored):
        raise RuntimeError("合成未预期错误")

    monkeypatch.setattr(expense_svc, "classify_file", boom)
    resp = client.post(
        "/api/finance/expense/classify",
        files={"file": ("明细账.xlsx", b"x" * 16, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert resp.status_code == 503
    assert "费用分类执行失败" in resp.json()["detail"]
    inbox = Path(settings.finance_data_dir) / "finance_expense" / "inbox"
    assert not inbox.exists() or not any(inbox.iterdir())


# ---------------------------------------------------------------------------
# 看板计划任务通道
# ---------------------------------------------------------------------------

def test_board_scheduled_task_busy_detected_from_gbk(monkeypatch, tmp_path):
    monkeypatch.setattr(board_svc, "pipeline_dir", lambda: tmp_path)
    monkeypatch.setattr(board_svc, "_task_name", lambda: "合成任务")
    monkeypatch.setattr(board_svc, "_pipeline_lock_fresh", lambda: False)
    gbk = '错误: 计划任务 "合成任务" 已在运行中。'.encode("gbk")

    def fake_run(args, **kwargs):
        return subprocess.CompletedProcess(args, 1, stdout=b"", stderr=gbk)

    monkeypatch.setattr(board_svc.subprocess, "run", fake_run)
    with pytest.raises(board_svc.PipelineBusyError):
        board_svc._run_steps_via_scheduled_task()


def test_board_schtasks_query_has_timeout_and_handles_it(monkeypatch):
    def slow(*args, **kwargs):
        assert kwargs.get("timeout")
        raise subprocess.TimeoutExpired(cmd="schtasks", timeout=1)

    monkeypatch.setattr(board_svc.subprocess, "run", slow)
    assert board_svc._scheduled_task_available() is False


def test_board_falls_back_to_direct_only_when_channel_unavailable(monkeypatch, tmp_path):
    monkeypatch.setattr(board_svc, "_scheduled_task_available", lambda: True)
    calls = {"direct": 0}

    def channel_down():
        raise board_svc.PipelineChannelUnavailableError("schtasks 无法启动")

    def direct(pdir, stamp, t0, save_failed=True):
        calls["direct"] += 1
        return "direct logs"

    monkeypatch.setattr(board_svc, "_run_steps_via_scheduled_task", channel_down)
    monkeypatch.setattr(board_svc, "_run_steps_directly", direct)
    assert board_svc._execute_attempt(tmp_path, "s", 0.0) == "direct logs"
    assert calls["direct"] == 1

    # 忙态不得回退
    def busy():
        raise board_svc.PipelineBusyError("正在运行")

    monkeypatch.setattr(board_svc, "_run_steps_via_scheduled_task", busy)
    with pytest.raises(board_svc.PipelineBusyError):
        board_svc._execute_attempt(tmp_path, "s", 0.0)
    assert calls["direct"] == 1

    # 任务已启动但步骤失败：不得回退重跑
    def step_failed():
        raise board_svc.PipelineUnavailableError("管线步骤失败：m02")

    monkeypatch.setattr(board_svc, "_run_steps_via_scheduled_task", step_failed)
    with pytest.raises(board_svc.PipelineUnavailableError):
        board_svc._execute_attempt(tmp_path, "s", 0.0)
    assert calls["direct"] == 1


def test_board_busy_maps_to_409(client, monkeypatch, tmp_path):
    pdir = tmp_path / "pipe_busy"
    (pdir / "scripts").mkdir(parents=True)
    monkeypatch.setattr(board_svc, "pipeline_dir", lambda: pdir)

    def busy(*args, **kwargs):
        raise board_svc.PipelineBusyError("报表监控管线正在运行中")

    monkeypatch.setattr(board_svc, "_execute_attempt", busy)
    resp = client.post("/api/finance/board/run")
    assert resp.status_code == 409
    assert "运行中" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# 数据仓
# ---------------------------------------------------------------------------

@pytest.fixture()
def vault_dir(tmp_path, monkeypatch, client):
    base = tmp_path / "vault_data"
    monkeypatch.setattr(vault_svc, "get_finance_data_dir", lambda: base)
    return base


def _df():
    return pd.DataFrame({"月份": ["2026-09", "2026-09", "2026-10"], "金额": [1.0, 2.0, 3.0]})


def test_vault_preview_missing_view_maps_409(client, vault_dir):
    vault_svc.ingest_dataframe("t_gone", _df())
    con = vault_svc._connect()
    try:
        con.execute('DROP VIEW "t_gone"')
    finally:
        con.close()
    resp = client.get("/api/finance/vault/tables/t_gone/preview")
    assert resp.status_code == 409
    assert "数据集" in resp.json()["detail"]


def test_vault_nan_and_infinity_serialize_as_null(vault_dir):
    result = vault_svc.run_query(
        "SELECT CAST('NaN' AS DOUBLE) AS a, CAST('Infinity' AS DOUBLE) AS b, 1.5 AS c"
    )
    assert result["rows"] == [[None, None, 1.5]]
    assert all(
        v is None or (isinstance(v, float) and math.isfinite(v))
        for row in result["rows"] for v in row
    )


def test_vault_accepts_cte_column_list_and_materialized(vault_dir):
    vault_svc.ingest_dataframe("t_cte", _df())
    r1 = vault_svc.run_query("WITH x(a) AS (SELECT 1 AS a) SELECT a FROM x")
    assert r1["rows"][0][0] == 1
    r2 = vault_svc.run_query("WITH x AS MATERIALIZED (SELECT 2 AS a) SELECT a FROM x")
    assert r2["rows"][0][0] == 2
    r3 = vault_svc.run_query("WITH x(a) AS (SELECT 1 AS a), y AS (SELECT * FROM x) SELECT count(*) AS n FROM y")
    assert r3["rows"][0][0] == 1


def test_vault_accepts_from_first_syntax(vault_dir):
    vault_svc.ingest_dataframe("t_fromfirst", _df())
    r = vault_svc.run_query("FROM t_fromfirst SELECT count(*) AS n")
    assert r["rows"][0][0] == 3


def test_vault_exponent_limit_rejected_not_double_appended(vault_dir):
    vault_svc.ingest_dataframe("t_limit", _df())
    with pytest.raises(vault_svc.SqlRejected) as exc:
        vault_svc.run_query("SELECT * FROM t_limit LIMIT 1e3")
    assert "LIMIT" in str(exc.value)
    # 正常整数 LIMIT / OFFSET 仍可用
    ok = vault_svc.run_query("SELECT * FROM t_limit LIMIT 2 OFFSET 1")
    assert ok["row_count"] == 2


def test_vault_csv_keeps_codes_as_text(vault_dir):
    csv_bytes = "编码,账号,期间,金额\n007,6222020200112233445,2026-10,123.45\n".encode("utf-8")
    result = vault_svc.ingest_uploaded_file("t_codes", "codes.csv", csv_bytes)
    assert result["keep_text"] is True
    assert "编码" in result["text_columns"] and "账号" in result["text_columns"]
    q = vault_svc.run_query("SELECT 编码, 账号, 期间 FROM t_codes")
    assert q["rows"][0] == ["007", "6222020200112233445", "2026-10"]
    total = vault_svc.run_query("SELECT sum(金额) AS n FROM t_codes")
    assert total["rows"][0][0] == 123.45


def test_vault_upload_mojibake_filename_repaired(vault_dir):
    mojibake = "检查.csv".encode("gbk").decode("latin-1")
    result = vault_svc.ingest_uploaded_file("t_name", mojibake, "a,b\n1,2\n".encode("utf-8"))
    assert result["source"] == "检查.csv"


def test_vault_multisheet_reports_actual_sheet_and_honors_numeric_name(vault_dir):
    wb = openpyxl.Workbook()
    cover = wb.active
    cover.title = "封面"
    cover.append(["说明"])
    data = wb.create_sheet("2026")
    data.append(["编号", "金额"])
    data.append(["A1", 10])
    buf = io.BytesIO()
    wb.save(buf)
    wb.close()
    content = buf.getvalue()

    first = vault_svc.ingest_uploaded_file("t_ms", "multi.xlsx", content)
    assert first["sheet"] == "封面" and first["sheet_count"] == 2
    assert first["sheets"][:2] == ["封面", "2026"]
    second = vault_svc.ingest_uploaded_file("t_ms", "multi.xlsx", content, sheet="2026")
    assert second["sheet"] == "2026"
    q = vault_svc.run_query("SELECT count(*) AS n FROM t_ms")
    assert q["rows"][0][0] == 1


def test_vault_quality_profile_is_bounded(vault_dir, monkeypatch):
    vault_svc.ingest_dataframe("t_big", _df())
    monkeypatch.setattr(vault_svc, "QUALITY_SCAN_ROW_LIMIT", 1)
    q = vault_svc.vault_quality_overview()
    item = next(t for t in q["tables"] if t["table"] == "t_big")
    assert item["sampled"] is True
    assert item["dup_rows"] is None
    assert q["truncated"] is True and q["gap_reasons"]
    # 时间预算为 0 时也不得抛异常 / 卡住
    zero = vault_svc.vault_quality_overview(time_budget=0)
    assert zero["available"] is True


# ---------------------------------------------------------------------------
# 纯空白引擎路径归一
# ---------------------------------------------------------------------------

def test_dq_summary_tolerates_sampled_vault_profile(client, vault_dir, monkeypatch):
    """dq 摘要消费质量画像：采样表 dup_rows=None 时不得 TypeError（回归 L-090 联动）。"""
    vault_svc.ingest_dataframe("t_dq_sample", _df())
    monkeypatch.setattr(vault_svc, "QUALITY_SCAN_ROW_LIMIT", 1)
    resp = client.get("/api/business/dq/summary")
    assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------------------
# Fix round 1（复核 I1/I2/I3 + M1～M12）
# ---------------------------------------------------------------------------

def test_vault_subquery_limit_is_not_rejected(vault_dir):
    """I1：子查询/派生表内的 LIMIT 不是顶层尾部 LIMIT，不得误拒。"""
    vault_svc.ingest_dataframe("t_sub", _df())
    r1 = vault_svc.run_query("SELECT * FROM (SELECT * FROM t_sub LIMIT 5)")
    assert r1["row_count"] == 3
    r2 = vault_svc.run_query(
        "SELECT * FROM t_sub WHERE 金额 IN (SELECT 金额 FROM t_sub LIMIT 2)"
    )
    assert r2["row_count"] >= 1
    with pytest.raises(vault_svc.SqlRejected) as exc:
        vault_svc.run_query("SELECT * FROM t_sub LIMIT 1e3")
    assert "LIMIT" in str(exc.value)


def test_vault_decimal_rates_numeric_but_leading_zero_codes_text(vault_dir):
    """I2：前导零规则只作用于纯整数；0.13/0.5 必须保持数值可 SUM。"""
    csv_bytes = "编码,费率,费率二,长编码\n007,0.13,0.5,0012345\n".encode("utf-8")
    result = vault_svc.ingest_uploaded_file("t_rates", "rates.csv", csv_bytes)
    assert "编码" in result["text_columns"] and "长编码" in result["text_columns"]
    assert "费率" not in result["text_columns"] and "费率二" not in result["text_columns"]
    q = vault_svc.run_query("SELECT sum(费率) AS a, sum(费率二) AS b FROM t_rates")
    assert q["rows"][0][0] == pytest.approx(0.13)
    assert q["rows"][0][1] == pytest.approx(0.5)
    codes = vault_svc.run_query("SELECT 编码, 长编码 FROM t_rates")
    assert codes["rows"][0] == ["007", "0012345"]


def test_bankflow_empty_use_name_rejected_with_row_number(client, bank_env):
    """I3：用途为空不得导出必填空白；400 指名行号，预览计入 missing_use。"""
    row = _flow_row("2026-10-01", "10", None, "S1", use="")
    preview = client.post("/api/finance/bankflow/preview", files={"file": ("f.xlsx", _flow_xlsx([row]))})
    assert preview.status_code == 200
    assert preview.json()["accounts"][0]["invalid"]["missing_use"] == 1

    resp = client.post(
        "/api/finance/bankflow/template",
        files={"file": ("f.xlsx", _flow_xlsx([row]))},
        data={"account": bf.mask_account(_ACC), "date_start": "", "date_end": ""},
    )
    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert "用途" in detail and "第 2 行" in detail


def test_invoice_patch_same_value_keeps_export(client, invoice_env):
    """M1：相同值 patch 不标 corrected、不作废已导出 ZIP。"""
    invoice_env["a.pdf"] = _inv_fields()
    batch = _inv_upload(client, [("a.pdf", FAKE_PDF)]).json()
    assert _inv_export(client, batch["id"]).status_code == 200
    item = batch["items"][0]
    resp = client.patch(
        f"/api/finance/invoice/items/{item['id']}",
        json={"project_name": item["project_name"], "remark": item["remark"]},
    )
    assert resp.status_code == 200
    assert resp.json()["corrected"] is False
    detail = client.get(f"/api/finance/invoice/batches/{batch['id']}").json()
    assert detail["status"] == "exported" and detail["export_file"]
    assert client.get(f"/api/finance/invoice/batches/{batch['id']}/download").status_code == 200


def test_invoice_remove_missing_export_dirs_does_not_warn(invoice_env, caplog):
    """M2：目录本就不存在时清理不应产生告警。"""
    import logging

    caplog.set_level(logging.WARNING)
    inv_svc._remove_export_artifacts(987654, "missing.zip")
    warnings = [
        r for r in caplog.records
        if r.name.startswith("app.modules.finance_invoice") and r.levelno >= logging.WARNING
    ]
    assert warnings == []


def test_invoice_zip_device_name_with_extra_suffix_rejected(client, invoice_env):
    """M3：CON.foo.bar 仍属 Windows 设备名（设备名按第一个点前的基名判定）。"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("CON.foo.bar.pdf", FAKE_PDF)
    resp = _inv_upload(client, [("pack.zip", buf.getvalue())])
    assert resp.status_code == 400
    assert "非法文件名" in resp.json()["detail"]


def test_invoice_direct_upload_uses_upload_wording(client, invoice_env):
    """M4：直传文件的错误文案不得沿用「ZIP 内」。"""
    resp = _inv_upload(client, [("CON.pdf", FAKE_PDF)])
    assert resp.status_code == 400
    assert "上传文件名" in resp.json()["detail"]
    assert "ZIP 内" not in resp.json()["detail"]


def test_bankflow_date_filter_precedes_required_check(client, bank_env):
    """M5：先按日期区间筛选，区间外的空用途行不应阻塞本月导出。"""
    rows = [
        _flow_row("2026-10-05", "10", None, "S1", use="合成用途"),
        _flow_row("2026-11-05", "20", None, "S2", use=""),
    ]
    resp = client.post(
        "/api/finance/bankflow/template",
        files={"file": ("f.xlsx", _flow_xlsx(rows))},
        data={"account": bf.mask_account(_ACC), "date_start": "2026-10-01", "date_end": "2026-10-31"},
    )
    assert resp.status_code == 200, resp.text
    ws = openpyxl.load_workbook(io.BytesIO(resp.content))["银行对账单"]
    assert ws.max_row == 10 and ws.cell(row=10, column=12).value == "S1"
    ws.parent.close()


def test_bankflow_bad_date_reported_separately(client, bank_env):
    """M5：坏日期单独列出，不与必填列缺失混为一条。"""
    rows = [
        _flow_row("2026-10-05", "10", None, "S1", use="合成用途"),
        _flow_row("2026-13-45", "20", None, "S2", use="合成用途"),
    ]
    resp = client.post(
        "/api/finance/bankflow/template",
        files={"file": ("f.xlsx", _flow_xlsx(rows))},
        data={"account": bf.mask_account(_ACC), "date_start": "", "date_end": ""},
    )
    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert "交易日期" in detail and "第 3 行" in detail


def test_bankflow_build_template_returns_two_values(bank_env):
    """M6：skipped 恒为 0 的死返回值移除。"""
    rows = bf.parse_flow(_flow_xlsx([_flow_row("2026-10-01", "10", None, "S1")]))
    result = bf.build_template(rows, _ACC, "TESTH003", "", "")
    assert len(result) == 2
    content, exported = result
    assert exported == 1 and content[:2] == b"PK"


def test_bankflow_parses_all_data_sheets(bank_env):
    """M7：多数据 sheet 全部遍历，而不是只解析第一张。"""
    wb = openpyxl.Workbook()
    cover = wb.active
    cover.title = "封面"
    cover.append(["银行流水导出"])
    ws1 = wb.create_sheet("账户一")
    ws1.append(_FLOW_HEADERS)
    ws1.append(_flow_row("2026-10-01", "10", None, "S1"))
    ws2 = wb.create_sheet("账户二")
    ws2.append(_FLOW_HEADERS)
    ws2.append(_flow_row("2026-10-02", "20", None, "S2"))
    buf = io.BytesIO()
    wb.save(buf)
    wb.close()
    rows = bf.parse_flow(buf.getvalue())
    assert len(rows) == 2
    assert {r["sheet"] for r in rows} == {"账户一", "账户二"}


def test_bankflow_warnings_and_errors_mask_account(client, bank_env):
    """M8：告警/错误只露尾 4 位，不输出完整账号。"""
    bad = _flow_row("2026-10-01", "10", "5", "S1")
    preview = client.post("/api/finance/bankflow/preview", files={"file": ("f.xlsx", _flow_xlsx([bad]))})
    warning = preview.json()["warnings"][0]
    assert _ACC not in warning and _ACC[-4:] in warning

    empty_use = _flow_row("2026-10-01", "10", None, "S1", use="")
    resp = client.post(
        "/api/finance/bankflow/template",
        files={"file": ("f.xlsx", _flow_xlsx([empty_use]))},
        data={"account": bf.mask_account(_ACC), "date_start": "", "date_end": ""},
    )
    assert resp.status_code == 400
    assert _ACC not in resp.json()["detail"] and _ACC[-4:] in resp.json()["detail"]


def test_vault_quality_table_budget_marks_sampled(vault_dir, monkeypatch):
    """M9：单表预算为 0 时不做昂贵统计，标记 sampled 而不是无限执行。"""
    vault_svc.ingest_dataframe("t_slow", _df())
    monkeypatch.setattr(vault_svc, "QUALITY_TABLE_BUDGET_SEC", 0)
    q = vault_svc.vault_quality_overview(time_budget=10)
    item = next(t for t in q["tables"] if t["table"] == "t_slow")
    assert item["sampled"] is True and item["dup_rows"] is None


def test_vault_quality_cache_returns_deep_copy(vault_dir):
    """M9：缓存返回深拷贝，调用方改写结果不得污染后续调用。"""
    vault_svc.ingest_dataframe("t_cache", _df())
    first = vault_svc.vault_quality_overview()
    first["tables"][0]["dup_rows"] = 999
    second = vault_svc.vault_quality_overview()
    assert second["tables"][0]["dup_rows"] != 999


def test_cashflow_timeout_records_log_tail(client, cf_env, monkeypatch):
    """M11：引擎超时也要把超时说明写入 log_tail，便于排障。"""
    engine_dir = Path(cf_env).parent / "cf_engine"
    (engine_dir / "cashflow_autofill.py").write_text("import time\ntime.sleep(5)\n", encoding="utf-8")
    monkeypatch.setattr(cf_svc, "STEP_TIMEOUT", 0.2)
    run = _post_cf(client, ("a.xlsx", b"1")).json()
    resp = client.post(f"/api/finance/cashflow/runs/{run['id']}/execute")
    assert resp.status_code == 503
    detail = client.get(f"/api/finance/cashflow/runs/{run['id']}").json()
    assert "超时" in detail["log_tail"]


def test_cashflow_timeout_keeps_partial_engine_output(client, cf_env, monkeypatch):
    """Task 8：超时异常携带的 stdout/stderr 也要进入 log_tail（不能只留一句超时）。"""
    def timeout_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(
            cmd=args[0] if args else "engine", timeout=1,
            output="partial-out", stderr="partial-err",
        )

    monkeypatch.setattr(cf_svc.subprocess, "run", timeout_run)
    run = _post_cf(client, ("a.xlsx", b"1")).json()
    resp = client.post(f"/api/finance/cashflow/runs/{run['id']}/execute")
    assert resp.status_code == 503
    detail = client.get(f"/api/finance/cashflow/runs/{run['id']}").json()
    assert "partial-out" in detail["log_tail"]
    assert "partial-err" in detail["log_tail"]


def test_expense_unparseable_amount_is_counted(tmp_path, monkeypatch):
    """M12：不可解析金额按 0 入账但必须计数并告警。"""
    class FakeClassifier:
        def read_bank_data(self, path):
            return True

        def integrate_data(self):
            return True

        def clean_data(self):
            return True

        def classify_expenses(self):
            return pd.DataFrame(
                [
                    {"摘要": "正常", "贷方本币": "1,234.56", "费用类别": "办公费"},
                    {"摘要": "坏值", "贷方本币": "无法解析", "费用类别": "未分类"},
                ]
            )

        def summarize_expenses(self):
            return True

        def export_results(self, path):
            Path(path).write_text("stub", encoding="utf-8")
            return True

    def fake_subdir(kind):
        path = tmp_path / kind
        path.mkdir(parents=True, exist_ok=True)
        return path

    monkeypatch.setattr(expense_svc, "_guard_import_rows", lambda path: None)
    monkeypatch.setattr(expense_svc, "_locate_bank_header_row", lambda path: 0)
    monkeypatch.setattr(expense_svc, "module_subdir", fake_subdir)
    monkeypatch.setattr(
        expense_svc,
        "_ensure_engine",
        lambda: {"classifier_cls": FakeClassifier, "load_rules": None, "app_config": None, "engine_dir": str(tmp_path)},
    )
    with SessionLocal() as db:
        run = expense_svc.classify_file(db, "fake.xlsx", tmp_path / "fake.xlsx")
        assert run.status == "success"
        assert run.bad_amount_rows == 1
        view = expense_svc.run_to_dict(run)
        assert view["bad_amount_rows"] == 1
        assert any("金额" in w for w in view["warnings"])


def test_whitespace_engine_paths_normalized_at_config_layer():
    cfg = Settings(
        finance_pos_engine_path="   ",
        finance_cashflow_engine_path="\t",
        finance_board_pipeline_path=" ",
        finance_expense_engine_path="\n",
        finance_invoice_engine_path="  ",
    )
    assert cfg.finance_pos_engine_path == ""
    assert cfg.finance_cashflow_engine_path == ""
    assert cfg.finance_board_pipeline_path == ""
    assert cfg.finance_expense_engine_path == ""
    assert cfg.finance_invoice_engine_path == ""


def test_whitespace_engine_paths_do_not_crash_modules(monkeypatch):
    monkeypatch.setattr(settings, "finance_pos_engine_path", "  ")
    monkeypatch.setattr(settings, "finance_cashflow_engine_path", "  ")
    monkeypatch.setattr(settings, "finance_board_pipeline_path", "  ")
    monkeypatch.setattr(settings, "finance_expense_engine_path", "  ")
    monkeypatch.setattr(pos_svc, "find_sibling_project", lambda *a, **k: None)
    monkeypatch.setattr(cf_svc, "find_sibling_project", lambda *a, **k: None)
    monkeypatch.setattr(board_svc, "find_sibling_project", lambda *a, **k: None)
    monkeypatch.setattr(expense_svc, "find_sibling_project", lambda *a, **k: None)

    with pytest.raises(pos_svc.EngineUnavailableError):
        pos_svc.engine_dir()
    with pytest.raises(cf_svc.EngineUnavailableError):
        cf_svc.engine_dir()
    with pytest.raises(board_svc.PipelineUnavailableError):
        board_svc.pipeline_dir()
    assert expense_svc.find_engine_dir() is None
    assert invoice_engine.engine_root() is not None
