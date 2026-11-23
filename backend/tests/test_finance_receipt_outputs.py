"""finance_receipt 产物：固定槽位 PDF、账户分册、待确认阻断、原子生成、下载/合并/ZIP、XLSX 防注入。"""
from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pymupdf
import pytest
from openpyxl import load_workbook
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen import canvas

P = "/api/finance/receipt"
FONT = "STSong-Light"
pdfmetrics.registerFont(UnicodeCIDFont(FONT))
CCB_0354 = "123456789012000354"
CCB_0956 = "123456789012000956"
BOC_ACCOUNT = "12345678905971"
A4_W, A4_H = 595.28, 841.89


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        yield c


def _pdf(pages: list[list[str]]) -> bytes:
    buf = io.BytesIO()
    width, height = 595.0, 842.0
    c = canvas.Canvas(buf, pagesize=(width, height))
    for blocks in pages:
        c.setFont(FONT, 9)
        slot_h = height / len(blocks)
        for index, text in enumerate(blocks):
            top = index * slot_h
            c.rect(8, height - top - slot_h + 6, width - 16, slot_h - 12)
            for line_no, line in enumerate(text.splitlines()):
                c.drawString(16, height - top - 20 - line_no * 11, line)
        c.showPage()
    c.save()
    return buf.getvalue()


def _ccb_income(payer: str, no: str, day: str, amount: str, account: str) -> str:
    return (
        "中国建设银行单位客户专用回单\n"
        f"付 款 人 全 称 {payer}\n收 款 人 全 称 合成公司\n"
        "︵ 贷 方 回 单 ︶ ︵ 收 款 人 回 单 ︶\n"
        f"账 号 99887766\n账 号 {account}\n"
        f"（小写）￥{amount}\n用 途 合成租金\n年 月 日2026 10 {day}\nNo：{no}"
    )


def _ccb_clearing(no: str, day: str, amount: str, account: str) -> str:
    return (
        "中国建设银行单位客户专用回单\n"
        "付 款 人 全 称 待清算间联商户消费款户\n收 款 人 全 称 合成公司\n"
        "︵ 贷 方 回 单 ︶ ︵ 收 款 人 回 单 ︶\n"
        f"账 号 99887766\n账 号 {account}\n"
        f"（小写）￥{amount}\n用 途 收单清算款1057000000000001\n"
        f"手续费：￥2.00\n交易净计金额：￥{amount}\n年 月 日2026 10 {day}\nNo：{no}"
    )


def _boc_income(payer: str, no: str, day: str, amount: str) -> str:
    return (
        "国内支付业务收款回单\n日期：2026年10月%s日\n"
        f"收款人账号：{BOC_ACCOUNT}\n付款人账号：998877\n"
        f"{payer}\n合成公司\n收款人名称：\n付款人名称：\n"
        f"金额：CNY{amount}\n用途：合成租金\n回单编号：{no}"
    ) % day


def _seed_four_ccb(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "合成0354建行2026.10回单.pdf").write_bytes(_pdf([
        [
            _ccb_income("客户一", "5301", "05", "100.00", CCB_0354),
            _ccb_income("客户二", "5302", "06", "200.00", CCB_0354),
            _ccb_income("客户三", "5303", "07", "300.00", CCB_0354),
        ],
        [_ccb_income("客户四", "5304", "08", "400.00", CCB_0354)],
    ]))


def _scan(client, source: Path, output: Path, start: str = "2026-10-01", end: str = "2026-10-31") -> dict:
    r = client.post(f"{P}/scan", json={
        "source_dir": str(source),
        "period_label": "2026-10",
        "start_date": start,
        "end_date": end,
        "output_dir": str(output),
    })
    assert r.status_code == 200, r.text
    return r.json()


def _generate(client, batch_id: int):
    return client.post(f"{P}/batches/{batch_id}/generate")


def _batch_dir(output: Path, batch_id: int) -> Path:
    return output / f"receipt_batch_{batch_id}"


def _file_path(output: Path, batch_id: int, item: dict) -> Path:
    return _batch_dir(output, batch_id) / item["folder"] / item["name"]


def test_income_pdf_has_fixed_three_slots_and_header(client, tmp_path):
    source = tmp_path / "源"
    output = tmp_path / "输出"
    _seed_four_ccb(source)
    batch = _scan(client, source, output)
    r = _generate(client, batch["id"])
    assert r.status_code == 200, r.text
    files = [f for f in r.json()["files"] if f["kind"] == "category"]
    income = next(f for f in files if f["category"] == "transfer_income")
    assert income["count"] == 4 and income["pages"] == 2
    assert income["account_no"] == CCB_0354

    doc = pymupdf.open(_file_path(output, batch["id"], income))
    try:
        assert doc.page_count == 2
        first, second = doc[0], doc[1]
        assert abs(first.rect.width - A4_W) < 1 and abs(first.rect.height - A4_H) < 1
        first_text = first.get_text()
        assert "本页 3 笔" in first_text and "类别共 4 笔" in first_text
        assert CCB_0354 in first_text and "正常转账收入" in first_text and "2026-10" in first_text
        assert "本页 1 笔" in second.get_text()
        slot_bottom = 40 + (A4_H - 24 - 40) / 3
        rects = second.search_for("客户四")
        assert rects and rects[0].y0 < slot_bottom
    finally:
        doc.close()


def test_accounts_and_categories_are_separate_files(client, tmp_path):
    source = tmp_path / "源"
    output = tmp_path / "输出"
    source.mkdir()
    (source / "合成0354建行2026.10回单.pdf").write_bytes(_pdf([[
        _ccb_income("客户一", "5301", "05", "100.00", CCB_0354),
        _ccb_clearing("5302", "06", "900.00", CCB_0354),
    ]]))
    (source / "合成0956建行2026.10回单.pdf").write_bytes(_pdf([[
        _ccb_income("客户五", "5401", "07", "50.00", CCB_0956),
    ]]))
    batch = _scan(client, source, output)
    r = _generate(client, batch["id"])
    assert r.status_code == 200, r.text
    categories = {(f["account_no"], f["category"]) for f in r.json()["files"] if f["kind"] == "category"}
    assert categories == {
        (CCB_0354, "transfer_income"),
        (CCB_0354, "pending_clearing_income"),
        (CCB_0956, "transfer_income"),
    }
    folders = {f["folder"] for f in r.json()["files"] if f["kind"] == "category"}
    assert folders == {"建行0354", "建行0956"}


def test_boc_uses_two_slots_per_page(client, tmp_path):
    source = tmp_path / "源"
    output = tmp_path / "输出"
    source.mkdir()
    (source / "合成中行2026.10回单.pdf").write_bytes(_pdf([
        [_boc_income("客户甲", "B001", "08", "10.00"), _boc_income("客户乙", "B002", "09", "20.00")],
        [_boc_income("客户丙", "B003", "10", "30.00")],
    ]))
    batch = _scan(client, source, output)
    r = _generate(client, batch["id"])
    assert r.status_code == 200, r.text
    boc = next(f for f in r.json()["files"] if f["kind"] == "category" and f["bank"] == "中行")
    assert boc["count"] == 3 and boc["pages"] == 2
    doc = pymupdf.open(_file_path(output, batch["id"], boc))
    try:
        assert "本页 2 笔" in doc[0].get_text()
        assert "本页 1 笔" in doc[1].get_text()
    finally:
        doc.close()


def test_excluded_items_are_not_generated(client, tmp_path):
    source = tmp_path / "源"
    output = tmp_path / "输出"
    _seed_four_ccb(source)
    batch = _scan(client, source, output)
    target = batch["items"][0]["id"]
    assert client.patch(f"{P}/batches/{batch['id']}/items/{target}", json={"included": False}).status_code == 200
    r = _generate(client, batch["id"])
    assert r.status_code == 200, r.text
    income = next(f for f in r.json()["files"] if f["category"] == "transfer_income")
    assert income["count"] == 3


def test_pending_confirmation_blocks_generation(client, tmp_path):
    from app.database import SessionLocal
    from app.modules.finance_receipt.models import ReceiptItem

    source = tmp_path / "源"
    output = tmp_path / "输出"
    _seed_four_ccb(source)
    batch = _scan(client, source, output)
    with SessionLocal() as db:
        item = db.get(ReceiptItem, batch["items"][0]["id"])
        item.category = "unclassified"
        db.commit()
    r = _generate(client, batch["id"])
    assert r.status_code == 409
    assert "待确认" in r.json()["detail"]
    assert not _batch_dir(output, batch["id"]).exists()


def test_regenerate_replaces_output_without_leftovers(client, tmp_path):
    source = tmp_path / "源"
    output = tmp_path / "输出"
    _seed_four_ccb(source)
    batch = _scan(client, source, output)
    first = _generate(client, batch["id"]).json()
    second = _generate(client, batch["id"]).json()
    assert sorted(f["name"] for f in first["files"]) == sorted(f["name"] for f in second["files"])
    leftovers = [p.name for p in output.iterdir() if p.name.startswith(".receipt_tmp")]
    assert leftovers == []


def test_missing_source_fails_without_partial_output(client, tmp_path):
    source = tmp_path / "源"
    output = tmp_path / "输出"
    _seed_four_ccb(source)
    batch = _scan(client, source, output)
    (source / "合成0354建行2026.10回单.pdf").unlink()
    r = _generate(client, batch["id"])
    assert r.status_code == 400
    assert "合成0354建行2026.10回单.pdf" in r.json()["detail"]
    assert not _batch_dir(output, batch["id"]).exists()
    detail = client.get(f"{P}/batches/{batch['id']}").json()
    assert detail["status"] == "failed"


def test_single_file_download_and_merge(client, tmp_path):
    source = tmp_path / "源"
    output = tmp_path / "输出"
    _seed_four_ccb(source)
    batch = _scan(client, source, output)
    gen = _generate(client, batch["id"]).json()
    income = next(f for f in gen["files"] if f["category"] == "transfer_income")

    r = client.get(f"{P}/batches/{batch['id']}/files/{income['id']}")
    assert r.status_code == 200
    assert r.content.startswith(b"%PDF")
    assert "filename*=utf-8''" in r.headers["content-disposition"]
    inline = client.get(f"{P}/batches/{batch['id']}/files/{income['id']}", params={"disposition": "inline"})
    assert inline.headers["content-disposition"].startswith("inline")

    merged = client.post(f"{P}/batches/{batch['id']}/merge", json={"file_ids": [income["id"]]})
    assert merged.status_code == 200
    assert pymupdf.open(stream=merged.content, filetype="pdf").page_count == income["pages"]

    assert client.get(f"{P}/batches/{batch['id']}/files/不存在").status_code == 404
    assert client.post(f"{P}/batches/{batch['id']}/merge", json={"file_ids": ["不存在"]}).status_code == 400


def test_zip_download_keeps_account_folders(client, tmp_path):
    source = tmp_path / "源"
    output = tmp_path / "输出"
    _seed_four_ccb(source)
    batch = _scan(client, source, output)
    gen = _generate(client, batch["id"]).json()
    r = client.get(f"{P}/batches/{batch['id']}/download")
    assert r.status_code == 200
    with zipfile.ZipFile(io.BytesIO(r.content)) as archive:
        names = set(archive.namelist())
    income = next(f for f in gen["files"] if f["category"] == "transfer_income")
    assert f"{income['folder']}/{income['name']}" in names
    assert any(name.endswith(".xlsx") for name in names)


def test_xlsx_index_escapes_formula_text(client, tmp_path):
    from app.database import SessionLocal
    from app.modules.finance_receipt.models import ReceiptItem

    source = tmp_path / "源"
    output = tmp_path / "输出"
    _seed_four_ccb(source)
    batch = _scan(client, source, output)
    with SessionLocal() as db:
        item = db.get(ReceiptItem, batch["items"][0]["id"])
        item.payer = '=HYPERLINK("http://example.invalid","客户")'
        db.commit()
    gen = _generate(client, batch["id"]).json()
    index = next(f for f in gen["files"] if f["kind"] == "index")
    r = client.get(f"{P}/batches/{batch['id']}/files/{index['id']}")
    assert r.status_code == 200
    wb = load_workbook(io.BytesIO(r.content))
    cells = [cell for ws in wb.worksheets for row in ws.iter_rows() for cell in row]
    assert all(cell.data_type != "f" for cell in cells)
    assert any(isinstance(cell.value, str) and cell.value.startswith("'=HYPERLINK") for cell in cells)


def test_unknown_batch_generation_returns_404(client):
    assert _generate(client, 999999).status_code == 404
