# -*- coding: utf-8 -*-
"""T4A 聚焦测试：POS 凭证月份过滤 / 金额表头锚点与勾稽 / 脏 running 回收 /
引擎子进程编码与隔离 / 失败不得假成功 / 既有 --project 用例的合法名修正。

全部使用 tmp 目录与合成 xlsx/PDF，不触碰真实 backend/data、真实引擎或计划任务。
"""
import io
import subprocess
import threading
import time
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path
from decimal import Decimal

import openpyxl
import pytest

P = "/api/finance/pos"


# ── 合成对账单 ─────────────────────────────────────────────────────────

def build_statement_xlsx(
    *,
    report_date: str = "2026/09/09",
    shift: int = 0,
    gross: float = 10000.0,
    fee: float = 40.0,
    net: float = 9960.0,
    count: int = 1,
    terminal: str = "0005",
    gross_label: str = "交易金额",
    fee_label: str = "银行手续费",
    net_label: str = "划账金额",
    count_label: str = "笔数",
) -> bytes:
    """合成 SHOP 对账单：shift 把全部列右移 N 列（模拟真实列序变化/插列）。"""
    wb = openpyxl.Workbook()
    ws = wb.active

    def put(row: int, col: int, value) -> None:
        ws.cell(row, col + shift, value)

    put(14, 3, "报表日期:")
    put(14, 6, report_date)
    put(22, 3, "终端交易明细")
    put(23, 3, "终端号")
    put(23, 17, count_label)
    put(23, 21, gross_label)
    put(23, 25, fee_label)
    put(23, 27, net_label)
    put(24, 3, "10118182")
    put(25, 3, f"POS编号：105731065130066#{terminal}")
    put(25, 15, "终端小计")
    put(25, 19, count)
    put(25, 21, gross)
    put(25, 25, fee)
    put(25, 27, net)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _zip_of(xlsx: bytes) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("statement.xlsx", xlsx)
    return buf.getvalue()


def build_receipt_pdf(
    path: Path,
    day: str = "2026年09月09日",
    amounts=("￥11,030.00", "￥44.12", "￥10,985.88", "2笔"),
    terminal: str = "0006",
) -> None:
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfgen import canvas

    try:
        pdfmetrics.registerFont(TTFont("SimSunT4A", r"C:\Windows\Fonts\simsun.ttc"))
    except Exception:
        pdfmetrics.registerFont(TTFont("SimSunT4A", r"C:\Windows\Fonts\msyh.ttc"))
    c = canvas.Canvas(str(path), pagesize=(595, 842))
    c.setFont("SimSunT4A", 9)
    lines = [
        "中国建设银行单位客户专用回单",
        "No：5265",
        f"转账日期：{day} 凭证字号：X",
        f"交易金额：{amounts[0]}",
        f"手续费：{amounts[1]}",
        f"交易净计金额：{amounts[2]}",
        f"交易笔数：{amounts[3]}",
        "用途：收单清算款105731065130066" + terminal,
    ]
    for i, line in enumerate(lines):
        c.drawString(60, 780 - i * 14, line)
    c.save()


def _stub_convert(xlsx_files, out_dir):
    from pypdf import PdfWriter

    out_dir.mkdir(parents=True, exist_ok=True)
    results = {}
    for f in xlsx_files:
        w = PdfWriter()
        w.add_blank_page(width=842, height=595)
        pdf = out_dir / f"{f.stem}.pdf"
        w.write(str(pdf))
        results[f] = pdf
    return results


# ── 引擎环境 / 解码 ───────────────────────────────────────────────────

def test_engine_subprocess_env_utf8_and_isolated_tmp(tmp_path):
    """R11-030/R11-086：子进程统一 UTF-8，临时目录可注入（不写共享数据目录）。"""
    from app.utils import engine_subprocess_env

    env = engine_subprocess_env(tmp_path)
    assert env["PYTHONIOENCODING"].lower() == "utf-8"
    assert env["PYTHONUTF8"] == "1"
    tmp = Path(env["TEMP"])
    assert tmp == tmp_path / "_engine_tmp" and tmp.is_dir()


def test_decode_engine_output_gb18030_fallback():
    """R11-030：引擎绕过 env 直接写 GBK 字节时仍能解码（保留中文关键字匹配）。"""
    from app.utils import decode_engine_output

    gbk = "项目汇总: 项目A:2天 / 项目B:0天\n无法识别项目，请使用 --project 指定".encode("gbk")
    assert "项目汇总" in decode_engine_output(gbk)
    assert "项目汇总" in decode_engine_output("项目汇总".encode("utf-8"))
    assert decode_engine_output(b"plain ascii") == "plain ascii"
    assert decode_engine_output(None) == ""


# ── 对账单解析：表头锚点 + 三列勾稽 ───────────────────────────────────

def test_parse_statement_shifted_amount_columns_anchored_by_header():
    """R11-011：列整体右移 2 列后仍按表头锚点取金额，不再静默读成 0 元。"""
    from app.modules.finance_pos import vouchers_service as vs

    data = _zip_of(build_statement_xlsx(shift=2, gross=12345.67, fee=61.73, net=12283.94))
    r = vs.parse_statement_zip(data, "SHOP.105731065130066.20260908.zip")
    assert len(r.subtotals) == 1
    s = r.subtotals[0]
    assert s.terminal == "0005"
    assert s.gross == Decimal("12345.67")
    assert s.fee == Decimal("61.73")
    assert s.net == Decimal("12283.94")
    assert s.report_date == date(2026, 9, 9)


def test_parse_statement_all_zero_amounts_rejected():
    """R11-011：金额全零（列位偏移的典型症状）必须报错，不得生成 0 元凭证。"""
    from app.modules.finance_pos import vouchers_service as vs

    data = _zip_of(build_statement_xlsx(gross=0, fee=0, net=0, count=2))
    with pytest.raises(ValueError) as exc:
        vs.parse_statement_zip(data, "SHOP.105731065130066.20260908.zip")
    assert "金额全为 0" in str(exc.value)


def test_parse_statement_amount_relation_mismatch_rejected():
    """R11-011：交易金额-手续费≠到账 必须报错。"""
    from app.modules.finance_pos import vouchers_service as vs

    data = _zip_of(build_statement_xlsx(gross=10000.0, fee=40.0, net=9000.0))
    with pytest.raises(ValueError) as exc:
        vs.parse_statement_zip(data, "SHOP.105731065130066.20260908.zip")
    assert "勾稽" in str(exc.value) or "交易金额" in str(exc.value)


def test_parse_statement_missing_amount_headers_rejected():
    """R11-011：表头没有金额列时 fail-closed，不得回退旧列位猜读。"""
    from app.modules.finance_pos import vouchers_service as vs

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.cell(22, 3, "终端交易明细")
    ws.cell(23, 3, "终端号")
    ws.cell(25, 3, "POS编号：105731065130066#0005")
    ws.cell(25, 15, "终端小计")
    ws.cell(25, 19, 1)
    buf = io.BytesIO()
    wb.save(buf)
    with pytest.raises(ValueError) as exc:
        vs.parse_statement_zip(_zip_of(buf.getvalue()), "SHOP.105731065130066.20260908.zip")
    assert "金额列" in str(exc.value)


def test_parse_statement_count_anchored_on_shifted_columns():
    """M-5：笔数按「笔数」表头锚点取值；整体右移 6 列（超出旧 17–23 扫描）仍正确。"""
    from app.modules.finance_pos import vouchers_service as vs

    data = _zip_of(build_statement_xlsx(shift=6, count=2))
    r = vs.parse_statement_zip(data, "SHOP.105731065130066.20260908.zip")
    assert r.subtotals[0].count == 2


def test_parse_statement_similar_header_labels_not_misbound():
    """M-5：别名只允许精确匹配/单位后缀，不得把「手续费率」「交易金额合计」当金额列。"""
    from app.modules.finance_pos import vouchers_service as vs

    for kwargs in ({"fee_label": "手续费率"}, {"gross_label": "交易金额合计"}):
        data = _zip_of(build_statement_xlsx(**kwargs))
        with pytest.raises(ValueError) as exc:
            vs.parse_statement_zip(data, "SHOP.105731065130066.20260908.zip")
        assert "金额列" in str(exc.value), kwargs


def test_parse_statement_filename_content_date_mismatch_rejected():
    """M-4：xlsx 内报表日期与文件名交易日期相差过大（改名/放错文件）必须拒绝。"""
    from app.modules.finance_pos import vouchers_service as vs

    data = _zip_of(build_statement_xlsx(report_date="2026/10/09"))
    with pytest.raises(ValueError) as exc:
        vs.parse_statement_zip(data, "SHOP.105731065130066.20260908.zip")
    assert "报表日期" in str(exc.value) and "不一致" in str(exc.value)


def test_parse_statement_post_holiday_report_date_accepted():
    """N-2：国庆长假后报表（09-30→10-09，滞后 9 天）属合法，必须接受。"""
    from app.modules.finance_pos import vouchers_service as vs

    data = _zip_of(build_statement_xlsx(report_date="2026/10/09"))
    r = vs.parse_statement_zip(data, "SHOP.105731065130066.20260930.zip")
    assert r.subtotals[0].trading_date == date(2026, 9, 30)
    assert r.subtotals[0].report_date == date(2026, 10, 9)


def test_report_date_tolerance_derived_from_match_tolerance(monkeypatch):
    """N-2：阈值由既有 match_date_tolerance 派生（下限 10 天），不新增配置项。"""
    from app.config import settings
    from app.modules.finance_pos import vouchers_service as vs

    monkeypatch.setattr(settings, "finance_pos_match_date_tolerance", 2)
    assert vs.report_date_tolerance_days() == 10
    monkeypatch.setattr(settings, "finance_pos_match_date_tolerance", 5)
    assert vs.report_date_tolerance_days() == 12


def test_parse_statement_report_date_before_trading_rejected():
    """N-2：报表日期早于交易日仍属明显错名，继续拒绝。"""
    from app.modules.finance_pos import vouchers_service as vs

    data = _zip_of(build_statement_xlsx(report_date="2026/09/07"))
    with pytest.raises(ValueError) as exc:
        vs.parse_statement_zip(data, "SHOP.105731065130066.20260908.zip")
    assert "报表日期" in str(exc.value) and "不一致" in str(exc.value)


# ── 月份过滤 ──────────────────────────────────────────────────────────

@pytest.fixture()
def two_month_dir(tmp_path, monkeypatch):
    mdir = tmp_path / "months" / "2026年" / "09"
    mdir.mkdir(parents=True)
    (mdir / "SHOP.105731065130066.20260908.zip").write_bytes(
        _zip_of(build_statement_xlsx(report_date="2026/09/09"))
    )
    (mdir / "SHOP.105731065130066.20261008.zip").write_bytes(
        _zip_of(build_statement_xlsx(report_date="2026/10/09", gross=20000.0, fee=80.0, net=19920.0))
    )
    build_receipt_pdf(
        mdir / "云南0354建行2026.9回单.pdf",
        amounts=("￥10,000.00", "￥40.00", "￥9,960.00", "1笔"),
        terminal="0005",
    )
    build_receipt_pdf(
        mdir / "云南0354建行2026.10回单.pdf",
        day="2026年10月09日",
        amounts=("￥500.00", "￥2.00", "￥498.00", "1笔"),
        terminal="0006",
    )
    monkeypatch.setattr(
        "app.modules.finance_pos.vouchers_service._convert_xlsx_to_pdf", _stub_convert
    )
    monkeypatch.setattr("app.config.settings.finance_pos_statement_dir", str(mdir))
    return mdir


def test_generate_filters_other_months(client, two_month_dir, tmp_path):
    """R11-011：同目录混入 10 月对账单时，生成 2026-09 只含 09 数据并提示跳过。"""
    out = tmp_path / "out"
    resp = client.post(f"{P}/vouchers/generate", json={
        "month": "2026-09", "statement_dir": str(two_month_dir), "output_dir": str(out),
    })
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "ok"
    # 09 只有 1 条小计；修复前会把 10 月的也收进来（2 条）
    assert data["summary"]["statements_total"] == 1
    assert data["summary"]["skipped_other_month"] == ["SHOP.105731065130066.20261008.zip"]
    txn_dates = {f["receipt_date"] for f in data["files"] if f["kind"] == "txn"}
    assert txn_dates == {"2026-09-09"}
    client.delete(f"{P}/vouchers/{data['id']}")

    # 所选月份没有任何数据：中文 400（不是空批次成功）
    empty = client.post(f"{P}/vouchers/generate", json={
        "month": "2026-11", "statement_dir": str(two_month_dir),
        "output_dir": str(tmp_path / "out_empty"),
    })
    assert empty.status_code == 400, empty.text
    assert "没有对账单小计" in empty.json()["detail"]


def test_generate_marks_other_month_receipts(client, two_month_dir, tmp_path):
    """M-4：非本月回单不参与本月未匹配统计，单独列入 other_month_receipts。"""
    resp = client.post(f"{P}/vouchers/generate", json={
        "month": "2026-09", "statement_dir": str(two_month_dir),
        "output_dir": str(tmp_path / "out_receipts"),
    })
    assert resp.status_code == 200, resp.text
    data = resp.json()
    other = data["summary"]["other_month_receipts"]
    assert [r["receipt_date"] for r in other] == ["2026-10-09"]
    assert data["summary"]["unmatched"] == 0
    assert all(i["receipt_date"] != "2026-10-09" for i in data["summary"]["unmatched_list"])
    txn_dates = {f["receipt_date"] for f in data["files"] if f["kind"] == "txn"}
    assert txn_dates == {"2026-09-09"}
    client.delete(f"{P}/vouchers/{data['id']}")


def test_generate_duplicate_receipt_numbers_keep_statement_association(client, tmp_path, monkeypatch):
    """跨文件同号回单不得互相覆盖对账单；0002 仍关联本终端本日小计。"""
    mdir = tmp_path / "months" / "2026年" / "09"
    mdir.mkdir(parents=True)
    (mdir / "SHOP.105731065130066.20260908.zip").write_bytes(_zip_of(build_statement_xlsx(
        report_date="2026/09/09", gross=11030.0, fee=44.12, net=10985.88,
        count=2, terminal="0006",
    )))
    (mdir / "SHOP.105731065130066.20260914.zip").write_bytes(_zip_of(build_statement_xlsx(
        report_date="2026/09/15", gross=15419.29, fee=61.68, net=15357.61,
        count=1, terminal="0002",
    )))
    build_receipt_pdf(
        mdir / "云南0354建行2026.9上旬回单.pdf",
        day="2026年09月09日", amounts=("￥11,030.00", "￥44.12", "￥10,985.88", "2笔"),
        terminal="0006",
    )
    build_receipt_pdf(
        mdir / "云南0354建行2026.9中旬回单.pdf",
        day="2026年09月15日", amounts=("￥15,419.29", "￥61.68", "￥15,357.61", "1笔"),
        terminal="0002",
    )

    def convert_with_terminal_width(xlsx_files, out_dir):
        from pypdf import PdfWriter

        out_dir.mkdir(parents=True, exist_ok=True)
        results = {}
        for f in xlsx_files:
            writer = PdfWriter()
            width = {"0002": 800, "0006": 842}[f.stem.rsplit("_", 1)[-1]]
            writer.add_blank_page(width=width, height=595)
            pdf = out_dir / f"{f.stem}.pdf"
            writer.write(str(pdf))
            results[f] = pdf
        return results

    monkeypatch.setattr(
        "app.modules.finance_pos.vouchers_service._convert_xlsx_to_pdf",
        convert_with_terminal_width,
    )
    out = tmp_path / "out"
    resp = client.post(f"{P}/vouchers/generate", json={
        "month": "2026-09", "statement_dir": str(mdir), "output_dir": str(out),
    })
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["summary"]["matched"] == 2
    txn_files = {f["terminal"]: f for f in data["files"] if f["kind"] == "txn"}
    assert {t: f["receipt_date"] for t, f in txn_files.items()} == {
        "0002": "2026-09-15", "0006": "2026-09-09",
    }

    from pypdf import PdfReader

    for terminal, expected_width in (("0006", 842), ("0002", 800)):
        path = next(out.rglob(txn_files[terminal]["name"]))
        actual_width = float(PdfReader(str(path)).pages[0].mediabox.width)
        assert actual_width == expected_width, f"终端{terminal}拿到了其他同号回单的对账单"
    client.delete(f"{P}/vouchers/{data['id']}")


# ── 脏 running 批次 ───────────────────────────────────────────────────

def test_stale_voucher_batch_recovered(client, monkeypatch):
    """R11-028：崩溃后超时的 running 批次在查询时回收为 failed（带可操作原因）。"""
    from app.database import SessionLocal
    from app.modules.finance_pos import vouchers_service as vs
    from app.modules.finance_pos.models import PosVoucherBatch

    stale_at = datetime.now() - timedelta(seconds=vs.STALE_BATCH_SECONDS + 60)
    with SessionLocal() as db:
        stale = PosVoucherBatch(status="running", month="2026-09",
                                created_at=stale_at, updated_at=stale_at)
        fresh = PosVoucherBatch(status="running", month="2026-09")
        db.add_all([stale, fresh])
        db.commit()
        stale_id, fresh_id = stale.id, fresh.id

    items = {i["id"]: i for i in client.get(f"{P}/vouchers").json()["items"]}
    assert items[stale_id]["status"] == "failed"
    assert "中断" in items[stale_id]["error"]
    assert items[fresh_id]["status"] == "running", "未超时的批次不得误杀"


def test_list_does_not_reap_while_generation_active(client):
    """M-3：本进程持锁生成期间，列表查询不得把超 2h 的活跃批次置 failed。"""
    from app.database import SessionLocal
    from app.modules.finance_pos import vouchers_service as vs
    from app.modules.finance_pos.models import PosVoucherBatch

    stale_at = datetime.now() - timedelta(seconds=vs.STALE_BATCH_SECONDS + 60)
    with SessionLocal() as db:
        row = PosVoucherBatch(status="running", month="2026-09",
                              created_at=stale_at, updated_at=stale_at)
        db.add(row)
        db.commit()
        bid = row.id

    assert vs._VOUCHER_LOCK.acquire(blocking=False)
    try:
        items = {i["id"]: i for i in client.get(f"{P}/vouchers").json()["items"]}
        assert items[bid]["status"] == "running", "持锁生成中不得误杀"
    finally:
        vs._VOUCHER_LOCK.release()

    items = {i["id"]: i for i in client.get(f"{P}/vouchers").json()["items"]}
    assert items[bid]["status"] == "failed", "锁释放后仍应回收超时批次"


def test_batch_heartbeat_refreshes_updated_at(client):
    """M-3：生成期心跳线程持续刷新 updated_at（独立会话），避免被当作陈旧批次。"""
    from app.database import SessionLocal
    from app.modules.finance_pos import vouchers_service as vs
    from app.modules.finance_pos.models import PosVoucherBatch

    old = datetime.now() - timedelta(hours=3)
    with SessionLocal() as db:
        row = PosVoucherBatch(status="running", month="2026-09",
                              created_at=old, updated_at=old)
        db.add(row)
        db.commit()
        bid, before = row.id, row.updated_at

    hb = vs._BatchHeartbeat(bid, interval=0.05).start()
    try:
        deadline = time.time() + 3
        refreshed = False
        while time.time() < deadline:
            with SessionLocal() as db:
                if db.get(PosVoucherBatch, bid).updated_at > before:
                    refreshed = True
                    break
            time.sleep(0.02)
        assert refreshed, "心跳未刷新 updated_at"
    finally:
        hb.stop()


def test_generate_reaps_stale_while_holding_lock(client, two_month_dir, tmp_path):
    """M-3：生成入口在已持锁时仍回收陈旧批次（skip_if_busy=False 分支）。"""
    from app.database import SessionLocal
    from app.modules.finance_pos import vouchers_service as vs
    from app.modules.finance_pos.models import PosVoucherBatch

    stale_at = datetime.now() - timedelta(seconds=vs.STALE_BATCH_SECONDS + 60)
    with SessionLocal() as db:
        row = PosVoucherBatch(status="running", month="2026-09",
                              created_at=stale_at, updated_at=stale_at)
        db.add(row)
        db.commit()
        bid = row.id

    resp = client.post(f"{P}/vouchers/generate", json={
        "month": "2026-09", "statement_dir": str(two_month_dir),
        "output_dir": str(tmp_path / "out_reap"),
    })
    assert resp.status_code == 200, resp.text
    with SessionLocal() as db:
        assert db.get(PosVoucherBatch, bid).status == "failed"
    client.delete(f"{P}/vouchers/{resp.json()['id']}")


def test_generate_holds_lock_during_parse(client, two_month_dir, tmp_path, monkeypatch):
    """R11-028：解析/转换等重活必须在批次锁内执行（不再只锁创建半段）。"""
    from app.modules.finance_pos import vouchers_service as vs

    seen: list[bool] = []
    orig = vs.parse_statement_zip

    def spy(data, name):
        seen.append(vs._VOUCHER_LOCK.locked())
        return orig(data, name)

    monkeypatch.setattr(vs, "parse_statement_zip", spy)
    resp = client.post(f"{P}/vouchers/generate", json={
        "month": "2026-09", "statement_dir": str(two_month_dir),
        "output_dir": str(tmp_path / "out_lock"),
    })
    assert resp.status_code == 200, resp.text
    assert seen and all(seen)
    client.delete(f"{P}/vouchers/{resp.json()['id']}")


# ── 引擎执行：失败不得仅 HTTP 200、编码容错、TEMP 隔离 ─────────────────

STUB_LEDGER = '''
import argparse
from pathlib import Path
p = argparse.ArgumentParser()
p.add_argument("--root", type=Path)
p.add_argument("--output", type=Path)
a = p.parse_args()
a.output.parent.mkdir(parents=True, exist_ok=True)
a.output.write_text("stub-pos-ledger", encoding="utf-8")
print("stub ledger done")
'''

STUB_SILENT = '''
import argparse
p = argparse.ArgumentParser()
p.add_argument("--root")
p.add_argument("--project", default=None)
p.add_argument("--pos", default=None)
p.parse_args()
print("did nothing")
'''

STUB_SILENT_LEDGER = '''
import argparse
p = argparse.ArgumentParser()
p.add_argument("--root")
p.add_argument("--output")
p.parse_args()
print("did nothing")
'''

STUB_GBK_STDERR = '''
import argparse, sys
p = argparse.ArgumentParser()
p.add_argument("--root")
p.add_argument("--project", default=None)
p.add_argument("--pos", default=None)
p.parse_args()
sys.stderr.buffer.write(
    "项目汇总: 项目A:2天 / 项目B:0天\\n无法从辅助明细账识别项目，请使用 --project 指定\\n".encode("gbk")
)
sys.exit(1)
'''

STUB_EMPTY_OUTPUT = '''
import argparse
from pathlib import Path
p = argparse.ArgumentParser()
p.add_argument("--root", type=Path)
p.add_argument("--output", type=Path)
a = p.parse_args()
a.output.parent.mkdir(parents=True, exist_ok=True)
a.output.write_bytes(b"")
print("wrote empty ledger")
'''

STUB_MIXED_OUTPUT = '''
import argparse
from pathlib import Path
p = argparse.ArgumentParser()
p.add_argument("--root", type=Path)
p.add_argument("--output", type=Path)
a = p.parse_args()
a.output.parent.mkdir(parents=True, exist_ok=True)
a.output.write_text("stub-pos-ledger", encoding="utf-8")
(a.output.parent / "空产物.txt").write_bytes(b"")
print("wrote mixed")
'''


@pytest.fixture()
def pos_env(monkeypatch, tmp_path):
    from app.modules.finance_pos import service as svc

    base = tmp_path / "pos_data"
    engine = tmp_path / "pos_engine"
    engine.mkdir()
    (engine / "generate_pos_ledger.py").write_text(STUB_LEDGER, encoding="utf-8")
    (engine / "analyze_pos_reconciliation.py").write_text(STUB_SILENT, encoding="utf-8")
    monkeypatch.setattr(svc, "get_finance_data_dir", lambda: base)
    monkeypatch.setattr(svc, "engine_dir", lambda: engine)
    return base, engine


def _new_run(client):
    r = client.post(f"{P}/runs", files=[("files", ("SHOP对账单.xlsx", b"stub"))])
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_execute_without_artifacts_returns_503_and_failed(client, pos_env):
    """引擎 exit 0 但零产物：必须 503 + failed，不能仅 HTTP 200 就算成功。"""
    _base, engine = pos_env
    (engine / "generate_pos_ledger.py").write_text(STUB_SILENT_LEDGER, encoding="utf-8")
    run_id = _new_run(client)
    ex = client.post(f"{P}/runs/{run_id}/execute")
    assert ex.status_code == 503, ex.text
    assert "未产生产物" in ex.json()["detail"]
    detail = client.get(f"{P}/runs/{run_id}").json()
    assert detail["status"] == "failed" and "未产生产物" in detail["error"]


def test_execute_zero_byte_artifacts_returns_503_and_failed(client, pos_env):
    """零字节产物不得算成功：全部产物为空 → 503 + failed + 可操作原因。"""
    _base, engine = pos_env
    (engine / "generate_pos_ledger.py").write_text(STUB_EMPTY_OUTPUT, encoding="utf-8")
    run_id = _new_run(client)
    ex = client.post(f"{P}/runs/{run_id}/execute")
    assert ex.status_code == 503, ex.text
    detail_text = ex.json()["detail"]
    assert "0 字节" in detail_text or "空文件" in detail_text, detail_text
    detail = client.get(f"{P}/runs/{run_id}").json()
    assert detail["status"] == "failed"
    assert "0 字节" in detail["error"] or "空文件" in detail["error"], detail["error"]


def test_execute_mixed_zero_byte_keeps_valid_artifacts(client, pos_env):
    """混合产物：零字节文件不列入产物，但非空产物仍按成功归档。"""
    _base, engine = pos_env
    (engine / "generate_pos_ledger.py").write_text(STUB_MIXED_OUTPUT, encoding="utf-8")
    run_id = _new_run(client)
    ex = client.post(f"{P}/runs/{run_id}/execute")
    assert ex.status_code == 200, ex.text
    body = ex.json()
    names = {a["name"] for a in body["artifacts"]}
    assert names == {"POS项目台账.xlsx"}, names
    assert all(a["size"] > 0 for a in body["artifacts"])
    assert "空产物.txt" in body["log_tail"]


def test_execute_gbk_stderr_project_names_parsed(client, pos_env):
    """R11-030：引擎以 GBK 写 stderr 时仍解析出可用项目名（GB18030 回退）。"""
    _base, engine = pos_env
    (engine / "analyze_pos_reconciliation.py").write_text(STUB_GBK_STDERR, encoding="utf-8")
    run_id = _new_run(client)
    ex = client.post(f"{P}/runs/{run_id}/execute")
    assert ex.status_code == 503, ex.text
    detail = ex.json()["detail"]
    assert "识别项目" in detail and "可用项目名：项目A/项目B" in detail, detail


def test_engine_env_uses_service_data_dir(client, pos_env):
    """R11-086：引擎临时目录跟随服务层数据目录（测试注入生效，不写共享目录）。"""
    base, _engine = pos_env
    run_id = _new_run(client)
    ex = client.post(f"{P}/runs/{run_id}/execute")
    assert ex.status_code == 200, ex.text
    assert (base / "_engine_tmp").is_dir()


def test_execute_returns_409_when_another_run_active(client, pos_env):
    """Integration M3：执行锁非阻塞占入——已有运行在执行时立即 409，不排队到前端超时。"""
    from app.modules.finance_pos import service as svc

    run_id = _new_run(client)
    assert svc._EXEC_LOCK.acquire(blocking=False)
    released = {"done": False}

    def _release():
        if not released["done"]:
            released["done"] = True
            svc._EXEC_LOCK.release()

    timer = threading.Timer(1.0, _release)
    timer.start()
    try:
        t0 = time.perf_counter()
        ex = client.post(f"{P}/runs/{run_id}/execute")
        elapsed = time.perf_counter() - t0
    finally:
        timer.cancel()
        _release()

    assert ex.status_code == 409, ex.text
    assert "已有" in ex.json()["detail"]
    assert elapsed < 1.0, f"不应等待锁释放（实际 {elapsed:.2f}s）"
    assert client.get(f"{P}/runs/{run_id}").json()["status"] == "uploaded"

    # 锁释放后可正常重试
    ok = client.post(f"{P}/runs/{run_id}/execute")
    assert ok.status_code == 200, ok.text
    assert ok.json()["status"] == "ok"


def _raise_timeout(*_args, **_kwargs):
    raise subprocess.TimeoutExpired("stub", 1)


def _raise_runtime(*_args, **_kwargs):
    raise RuntimeError("stub boom")


@pytest.mark.parametrize("where", ["engine", "run_dir"])
def test_preflight_failure_clears_previous_artifacts(client, pos_env, monkeypatch, where):
    """预检 503（引擎目录/运行目录不可用）同样不得保留旧产物。"""
    from app.modules.finance_pos import service as svc

    run_id = _new_run(client)
    ok = client.post(f"{P}/runs/{run_id}/execute")
    assert ok.status_code == 200, ok.text
    assert ok.json()["artifacts"]

    def _engine_missing():
        raise svc.EngineUnavailableError("无法定位 POS 引擎目录")

    def _run_dir_invalid(_run):
        raise svc.EngineUnavailableError("运行工作目录无效")

    if where == "engine":
        monkeypatch.setattr(svc, "engine_dir", _engine_missing)
    else:
        monkeypatch.setattr(svc, "_resolve_run_dir", _run_dir_invalid)

    ex = client.post(f"{P}/runs/{run_id}/execute")
    assert ex.status_code == 503, ex.text
    detail = client.get(f"{P}/runs/{run_id}").json()
    assert detail["status"] == "failed"
    assert detail["artifacts"] == []
    assert client.get(f"{P}/runs/{run_id}/download").status_code == 409


@pytest.mark.parametrize("mode", ["exit", "timeout", "exception"])
def test_failed_rerun_clears_previous_artifacts(client, pos_env, monkeypatch, mode):
    """失败重跑不得保留旧产物：非零退出/超时/兜底异常均清空产物并可重试。"""
    from app.modules.finance_pos import service as svc

    _base, engine = pos_env
    run_id = _new_run(client)
    ok = client.post(f"{P}/runs/{run_id}/execute")
    assert ok.status_code == 200, ok.text
    art = ok.json()["artifacts"][0]["name"]
    assert client.get(f"{P}/runs/{run_id}/artifacts/{art}").status_code == 200

    if mode == "exit":
        (engine / "analyze_pos_reconciliation.py").write_text(
            "import sys\nsys.exit(1)\n", encoding="utf-8"
        )
    elif mode == "timeout":
        monkeypatch.setattr(svc, "_run_script", _raise_timeout)
    else:
        monkeypatch.setattr(svc, "_run_script", _raise_runtime)

    ex = client.post(f"{P}/runs/{run_id}/execute")
    assert ex.status_code == 503, ex.text
    detail = client.get(f"{P}/runs/{run_id}").json()
    assert detail["status"] == "failed"
    assert detail["artifacts"] == [], detail["artifacts"]
    assert client.get(f"{P}/runs/{run_id}/download").status_code == 409
    assert client.get(f"{P}/runs/{run_id}/artifacts/{art}").status_code == 404
