"""finance_receipt 模块 API：扫描建批、逐笔复核、原单预览与 manifest 注册（合成数据，临时目录）。"""
from __future__ import annotations

import io
from pathlib import Path

import pytest
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen import canvas

from app.modules import get_loaded_modules

P = "/api/finance/receipt"
FONT = "STSong-Light"
pdfmetrics.registerFont(UnicodeCIDFont(FONT))
CCB_ACCOUNT = "123456789012000354"


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        yield c


def _ccb_block(body: str) -> str:
    return f"中国建设银行单位客户专用回单\n{body}"


def _income(no: str, day: str, amount: str) -> str:
    return _ccb_block(
        "付 款 人 全 称 合成客户\n收 款 人 全 称 合成公司\n"
        "︵ 贷 方 回 单 ︶ ︵ 收 款 人 回 单 ︶\n"
        f"账 号 99887766\n账 号 {CCB_ACCOUNT}\n"
        f"（小写）￥{amount}\n用 途 合成租金\n年 月 日2026 10 {day}\nNo：{no}"
    )


def _clearing(no: str, day: str, amount: str) -> str:
    return _ccb_block(
        "付 款 人 全 称 待清算间联商户消费款户\n收 款 人 全 称 合成公司\n"
        "︵ 贷 方 回 单 ︶ ︵ 收 款 人 回 单 ︶\n"
        f"账 号 99887766\n账 号 {CCB_ACCOUNT}\n"
        f"（小写）￥{amount}\n用 途 收单清算款1057000000000001\n"
        f"手续费：￥2.00\n交易净计金额：￥{amount}\n年 月 日2026 10 {day}\nNo：{no}"
    )


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


@pytest.fixture()
def source_dir(tmp_path: Path) -> Path:
    root = tmp_path / "回单源"
    root.mkdir()
    (root / "合成0354建行2026.10回单.pdf").write_bytes(
        _pdf([[_income("5301", "05", "1,234.56"), _clearing("5302", "06", "1,000.00"), _income("5303", "07", "9.00")]])
    )
    return root


def _scan(client, source: Path, start: str = "2026-10-01", end: str = "2026-10-31", **extra):
    body = {
        "source_dir": str(source),
        "period_label": "2026-10",
        "start_date": start,
        "end_date": end,
    }
    body.update(extra)
    return client.post(f"{P}/scan", json=body)


def test_manifest_registered_with_readonly_summary_tool():
    loaded = {m.name: m for m in get_loaded_modules()}
    assert "finance_receipt" in loaded
    manifest = loaded["finance_receipt"]
    assert manifest.prefix == "/api/finance/receipt"
    assert manifest.preset_experts == ["finance_specialist"]
    tool = next(t for t in manifest.agent_tools if t["name"] == "receipt_summary")
    assert tool["readonly"] is True


def test_config_exposes_category_labels(client):
    r = client.get(f"{P}/config")
    assert r.status_code == 200
    body = r.json()
    assert set(body["categories"]) == {
        "pending_clearing_income",
        "transfer_income",
        "transfer_expense",
        "bank_interest_income",
        "bank_fee_expense",
    }
    assert "default_output_dir" in body


def test_scan_creates_scanned_batch_with_items(client, source_dir):
    r = _scan(client, source_dir)
    assert r.status_code == 200, r.text
    batch = r.json()
    assert batch["status"] == "scanned"
    assert batch["period_label"] == "2026-10"
    assert batch["summary"]["counts"] == {
        "pending_clearing_income": 1,
        "transfer_income": 2,
        "transfer_expense": 0,
        "bank_interest_income": 0,
        "bank_fee_expense": 0,
        "unclassified": 0,
    }
    assert batch["summary"]["pending_confirm"] == 0
    items = batch["items"]
    assert len(items) == 3
    assert {it["account_no"] for it in items} == {CCB_ACCOUNT}
    assert all(it["included"] and not it["reviewed"] for it in items)


def test_scan_rejects_invalid_period_and_missing_directory(client, tmp_path, source_dir):
    assert _scan(client, source_dir, start="2026-10-31", end="2026-10-01").status_code == 400
    assert _scan(client, tmp_path / "不存在").status_code == 400


def test_scan_reports_failed_file_without_hiding_others(client, source_dir):
    (source_dir / "合成0956建行2026.10回单.pdf").write_bytes(b"not a pdf")
    r = _scan(client, source_dir)
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["items"]) == 3
    assert any("合成0956建行2026.10回单.pdf" in f["name"] for f in body["failed_sources"])


def test_scan_all_files_failed_returns_400(client, tmp_path):
    root = tmp_path / "坏回单"
    root.mkdir()
    (root / "合成0354建行2026.10回单.pdf").write_bytes(b"broken")
    r = _scan(client, root)
    assert r.status_code == 400
    assert "无法读取" in r.json()["detail"]


def test_patch_item_changes_category_and_marks_reviewed(client, source_dir):
    batch = _scan(client, source_dir).json()
    item_id = batch["items"][0]["id"]
    r = client.patch(
        f"{P}/batches/{batch['id']}/items/{item_id}",
        json={"category": "transfer_expense", "included": False},
    )
    assert r.status_code == 200, r.text
    item = r.json()
    assert item["category"] == "transfer_expense"
    assert item["included"] is False
    assert item["reviewed"] is True
    detail = client.get(f"{P}/batches/{batch['id']}").json()
    assert detail["summary"]["excluded"] == 1


def test_patch_rejects_unknown_category_and_foreign_item(client, source_dir):
    batch = _scan(client, source_dir).json()
    item_id = batch["items"][0]["id"]
    assert client.patch(
        f"{P}/batches/{batch['id']}/items/{item_id}", json={"category": "随便写"}
    ).status_code == 422
    assert client.patch(
        f"{P}/batches/999999/items/{item_id}", json={"included": True}
    ).status_code == 404


def test_preview_returns_single_receipt_pdf(client, source_dir):
    batch = _scan(client, source_dir).json()
    item_id = batch["items"][0]["id"]
    r = client.get(f"{P}/batches/{batch['id']}/items/{item_id}/preview")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("application/pdf")
    assert r.content.startswith(b"%PDF")


def test_preview_missing_source_is_reported(client, source_dir):
    batch = _scan(client, source_dir).json()
    item_id = batch["items"][0]["id"]
    (source_dir / "合成0354建行2026.10回单.pdf").unlink()
    r = client.get(f"{P}/batches/{batch['id']}/items/{item_id}/preview")
    assert r.status_code == 404
    assert "源文件" in r.json()["detail"]


def test_list_and_get_batches(client, source_dir):
    created = _scan(client, source_dir).json()
    listing = client.get(f"{P}/batches").json()
    assert listing["total"] >= 1
    assert any(item["id"] == created["id"] for item in listing["items"])
    assert client.get(f"{P}/batches/{created['id']}").json()["id"] == created["id"]
    assert client.get(f"{P}/batches/999999").status_code == 404


def test_configured_receipt_dirs_join_fs_browse_roots(monkeypatch, tmp_path):
    from app import config as app_config

    source = tmp_path / "回单源"
    output = tmp_path / "回单输出"
    monkeypatch.setattr(app_config.settings, "fs_browse_roots", "")
    monkeypatch.setattr(app_config.settings, "finance_receipt_source_dir", str(source))
    monkeypatch.setattr(app_config.settings, "finance_receipt_output_dir", str(output))
    roots = [p.resolve() for p in app_config.get_fs_browse_roots()]
    assert source.resolve() in roots
    assert output.resolve() in roots
