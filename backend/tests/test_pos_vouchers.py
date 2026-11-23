# -*- coding: utf-8 -*-
"""finance_pos 刷卡凭证：合成数据单测（对账单/回单解析、匹配、API）。

合成对账单 xlsx 镜像真实 SHOP 结构（报表日期行/终端交易明细/表头/数据行/小计行），
合成回单 PDF 用 reportlab 按真实回单文本行构造；LibreOffice 转换在测试中打桩。
"""
import io
import json
import zipfile
from pathlib import Path
from datetime import date
from decimal import Decimal

import openpyxl
import pytest


def build_statement_xlsx() -> bytes:
    """合成一份对账单 xlsx（镜像真实结构：0005/0006 两终端各一笔）。"""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.cell(14, 3, "报表日期:")
    ws.cell(14, 6, "2026/09/09")
    ws.cell(22, 3, "终端交易明细")
    ws.cell(23, 3, "终端号")
    ws.cell(23, 21, "交易金额")
    ws.cell(23, 25, "银行手续费")
    ws.cell(23, 27, "划账金额")
    # 0005: 1 笔 10000/40/9960
    ws.cell(24, 3, "10118182")
    ws.cell(25, 3, "POS编号：105731065130066#0005")
    ws.cell(25, 15, "终端小计")
    ws.cell(25, 19, 1)
    ws.cell(25, 21, 10000.00)
    ws.cell(25, 25, 40.00)
    ws.cell(25, 27, 9960.00)
    # 0006: 2 笔 11030/44.12/10985.88
    ws.cell(26, 3, "10163755")
    ws.cell(27, 3, "10163755")
    ws.cell(28, 3, "POS编号：105731065130066#0006")
    ws.cell(28, 15, "终端小计")
    ws.cell(28, 19, 2)
    ws.cell(28, 21, 11030.00)
    ws.cell(28, 25, 44.12)
    ws.cell(28, 27, 10985.88)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def build_statement_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("statement.xlsx", build_statement_xlsx())
    return buf.getvalue()


def build_receipt_pdf(path, with_dup: bool = False) -> None:
    """合成建行回单 PDF：一页两块回单文本；with_dup 时第二块与第一块同号（去重测试）。"""
    from reportlab.pdfgen import canvas
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    try:
        pdfmetrics.registerFont(TTFont("SimSun", r"C:\Windows\Fonts\simsun.ttc"))
    except Exception:
        pdfmetrics.registerFont(TTFont("SimSun", r"C:\Windows\Fonts\msyh.ttc"))
    c = canvas.Canvas(str(path), pagesize=(595, 842))
    def block(y, no, day, amounts=None):
        c.setFont("SimSun", 9)
        a = amounts or ("￥11,030.00", "￥44.12", "￥10,985.88", "2笔")
        for i, line in enumerate([
            "中国建设银行单位客户专用回单",
            f"No：{no}",
            f"转账日期：{day} 凭证字号：X",
            f"交易金额：{a[0]}",
            f"手续费：{a[1]}",
            f"交易净计金额：{a[2]}",
            f"交易笔数：{a[3]}",
            "用途：收单清算款1057310651300660006",
        ]):
            c.drawString(60, y - i * 14, line)
    block(780, "5265", "2026年09月09日")
    block(560, "5265" if with_dup else "5266", "2026年09月12日",
          amounts=("￥500.00", "￥2.00", "￥498.00", "1笔"))
    block(780, "5265", "2026年09月09日")
    block(560, "5265" if with_dup else "5266", "2026年09月11日")
    c.save()


def _stub_convert(xlsx_files, out_dir):
    """转换桩：为每个 xlsx 生成一页空白 PDF（匹配不读 PDF 文本，空白页足够）。"""
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


@pytest.fixture()
def month_env(tmp_path, monkeypatch):
    """月度目录 + 源目录配置 + 转换桩。"""
    base = tmp_path / "months"
    mdir = base / "2026年" / "09"
    mdir.mkdir(parents=True)
    (mdir / "SHOP.105731065130066.20260908.zip").write_bytes(build_statement_zip())
    build_receipt_pdf(mdir / "云南0354建行2026.9回单.pdf")
    monkeypatch.setattr("app.modules.finance_pos.vouchers_service._convert_xlsx_to_pdf", _stub_convert)
    monkeypatch.setattr("app.config.settings.finance_pos_statement_dir", str(base))
    return mdir


def test_parse_statement_subtotals(month_env):
    from app.modules.finance_pos import vouchers_service as vs

    r = vs.parse_statement_zip(build_statement_zip(), "SHOP.105731065130066.20260908.zip")
    assert len(r.subtotals) == 2
    s5, s6 = r.subtotals
    assert s5.terminal == "0005" and s5.gross == Decimal("10000.00")
    assert s5.fee == Decimal("40.00") and s5.net == Decimal("9960.00") and s5.count == 1
    assert s6.terminal == "0006" and s6.net == Decimal("10985.88") and s6.count == 2
    assert s6.report_date == date(2026, 9, 9)


def test_parse_receipts_dedup(tmp_path):
    from app.modules.finance_pos import vouchers_service as vs

    p = tmp_path / "r.pdf"
    build_receipt_pdf(p, with_dup=True)
    recs = vs.parse_receipts(p, "建行0354")
    assert len(recs) == 1
    r = recs[0]
    assert r.terminal == "0006" and r.net == Decimal("10985.88") and r.count == 2
    assert r.receipt_date == date(2026, 9, 9)


def test_match_receipts_exact(month_env):
    from app.modules.finance_pos import vouchers_service as vs

    stmt = vs.parse_statement_zip(build_statement_zip(), "SHOP.105731065130066.20260908.zip")
    recs = vs.parse_receipts(month_env / "云南0354建行2026.9回单.pdf", "建行0354")
    recs = [r for r in recs if r.terminal == "0006"]
    matched, unmatched = vs.match_receipts_to_subtotals(recs, stmt.subtotals)
    assert len(matched) == 1
    r, s, info = matched[0]
    assert info["amount_hit"] and r.terminal == s.terminal == "0006"
    # 5266（0912，超容差）未匹配并给出原因
    assert len(unmatched) == 1 and unmatched[0][0].receipt_no == "5266"


def test_dynamic_settings(monkeypatch):
    """项目映射 / 回单通配 / 容差均可经配置覆盖。"""
    from app.modules.finance_pos import vouchers_service as vs
    from app.config import settings

    monkeypatch.setattr(settings, "finance_pos_project_map", "0009=测试项目A, bad, 0005=")
    assert vs.project_map() == {"0009": "测试项目A"}
    monkeypatch.setattr(settings, "finance_pos_receipt_glob", "建行*回单*.pdf , *其他*.PDF")
    assert vs.receipt_globs() == ("建行*回单*.pdf", "*其他*.PDF")
    monkeypatch.setattr(settings, "finance_pos_match_date_tolerance", 5)
    monkeypatch.setattr(settings, "finance_pos_match_amount_tolerance", "0.5")
    assert vs.date_tolerance_days() == 5 and vs.amount_tolerance() == Decimal("0.5")
    monkeypatch.setattr(settings, "finance_pos_match_amount_tolerance", "bad")
    assert vs.amount_tolerance() == vs.DEFAULT_AMOUNT_TOLERANCE


def test_generate_vouchers_api(client, month_env):
    resp = client.post("/api/finance/pos/vouchers/generate", json={"month": "2026-09"})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "ok"
    assert data["summary"]["receipts_total"] == 2
    assert data["summary"]["matched"] == 1
    kinds = {f["kind"] for f in data["files"]}
    assert kinds == {"txn", "fee"}
    # 列表 + 合并 + 删除
    lst = client.get("/api/finance/pos/vouchers").json()
    assert lst["total"] == 1
    names = [f["name"] for f in data["files"]]
    merged = client.post(f"/api/finance/pos/vouchers/{data['id']}/merge", json={"names": names})
    assert merged.status_code == 200
    assert merged.headers["content-type"].startswith("application/pdf")
    assert client.delete(f"/api/finance/pos/vouchers/{data['id']}").status_code == 204
    assert client.get("/api/finance/pos/vouchers").json()["total"] == 0


def test_generate_with_custom_dirs(client, tmp_path, month_env):
    """statement_dir/output_dir 人工指定：输出批次目录落在自选目录，config 接口回显默认值。"""
    out_dir = tmp_path / "my_output"
    resp = client.post("/api/finance/pos/vouchers/generate", json={
        "month": "2026-09",
        "statement_dir": str(month_env.parent.parent),  # 传归档基目录（按布局拼 2026年/09）
        "output_dir": str(out_dir),
    })
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "ok"
    assert Path(data["output_dir"]) == out_dir
    assert any(out_dir.rglob("manifest.json"))
    assert data["files"], "自定义输出目录下应有凭证文件"
    cfg = client.get("/api/finance/pos/vouchers/config").json()
    assert "default_statement_dir" in cfg and "default_output_dir" in cfg
    client.delete(f"/api/finance/pos/vouchers/{data['id']}")
