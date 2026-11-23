from __future__ import annotations

import io
import zipfile
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen import canvas

from app.modules.finance_receipt.parsers import (
    ParsedReceipt,
    SourceFile,
    classify_receipt,
    deduplicate_receipts,
    discover_sources,
    parse_source,
)


FONT = "STSong-Light"
pdfmetrics.registerFont(UnicodeCIDFont(FONT))
CCB_ACCOUNT = "123456789012000354"
BOC_ACCOUNT = "12345678905971"
PA_ACCOUNT = "12345678905600"


def _write_pdf(path: Path, pages: list[list[str]], landscape: bool = False) -> bytes:
    buf = io.BytesIO()
    width, height = (842.0, 595.0) if landscape else (595.0, 842.0)
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
    data = buf.getvalue()
    path.write_bytes(data)
    return data


def _ccb(header_body: str) -> str:
    return f"中国建设银行单位客户专用回单\n{header_body}"


def _ccb_income(day: str = "05", amount: str = "1,234.56", payer: str = "合成客户") -> str:
    return _ccb(
        f"付 款 人 全 称 {payer}\n收 款 人 全 称 合成公司\n"
        "︵ 贷 方 回 单 ︶ ︵ 收 款 人 回 单 ︶\n"
        f"账 号 99887766\n账 号 {CCB_ACCOUNT}\n"
        f"（小写）￥{amount}\n用 途 合成租金\n年 月 日2026 10 {day}\nNo：5312"
    )


def test_parse_ccb_mixed_page(tmp_path: Path):
    path = tmp_path / "合成0354建行2026.10回单.pdf"
    clearing = _ccb(
        "付 款 人 全 称 待清算间联商户消费款户\n收 款 人 全 称 合成公司\n"
        "︵ 贷 方 回 单 ︶ ︵ 收 款 人 回 单 ︶\n"
        f"账 号 99887766\n账 号 {CCB_ACCOUNT}\n"
        "（小写）￥1,000.00\n用 途 收单清算款1057000000000001\n"
        "手续费：￥2.00\n交易净计金额：￥1,000.00\n年 月 日2026 10 06\nNo：5313"
    )
    fee = _ccb(
        f"户名：合成公司\n账号：{CCB_ACCOUNT}\n"
        "项目名称 工本费/转账汇款手续费/手续费\n合计金额 ￥2.00\n"
        "年 月 日2026 10 07\nNo：5314"
    )
    _write_pdf(path, [[_ccb_income(), clearing, fee]])

    items = parse_source(SourceFile(path=path, bank="建行", account_hint="0354"))

    assert len(items) == 3
    assert [item.category for item in items] == [
        "transfer_income",
        "pending_clearing_income",
        "bank_fee_expense",
    ]
    assert [item.amount for item in items] == [Decimal("1234.56"), Decimal("1000.00"), Decimal("2.00")]
    assert all(item.account_no == CCB_ACCOUNT for item in items)
    assert items[0].rdate == date(2026, 10, 5)
    assert items[1].rdate == date(2026, 10, 6)
    assert items[2].receipt_no == "5314"
    assert items[0].clip[3] > items[0].clip[1]
    assert items[1].clip[1] >= items[0].clip[3]


def test_parse_boc_and_pingan_zip_with_interest(tmp_path: Path):
    boc_path = tmp_path / "合成中行2026.10回单.pdf"
    boc_income = (
        "国内支付业务收款回单\n日期：2026年10月08日\n"
        f"收款人账号：{BOC_ACCOUNT}\n付款人账号：998877\n"
        "合成公司\n合成客户\n收款人名称：\n付款人名称：\n"
        "金额：CNY110.23\n用途：租赁费\n回单编号：B001"
    )
    boc_fee = (
        "客户付费回单\n日期：2026年10月08日\n付款人账号：" + BOC_ACCOUNT + "\n"
        "合成公司\n付款人名称：\n金额：CNY4.50\n费用名称：对公跨行转账汇款手续费\n回单编号：B002"
    )
    boc_interest = (
        "利息收入回单\n日期：2026年10月20日\n收款人账号：" + BOC_ACCOUNT + "\n"
        "合成公司\n收款人名称：\n金额：CNY9.87\n回单编号：B003"
    )
    _write_pdf(boc_path, [[boc_income, boc_fee], [boc_interest]])
    boc_items = parse_source(SourceFile(path=boc_path, bank="中行", account_hint=""))

    assert len(boc_items) == 3
    assert [item.category for item in boc_items] == [
        "transfer_income",
        "bank_fee_expense",
        "bank_interest_income",
    ]
    assert all(item.account_no == BOC_ACCOUNT for item in boc_items)
    assert boc_items[0].counterparty == "合成客户"
    assert boc_items[2].amount == Decimal("9.87")

    pa_pdf = tmp_path / "member.pdf"
    pa_income = (
        "收付款业务回单\n记账日期：2026-10-09\n回单号：P001\n"
        "付款人名称: 合成客户\n收款人名称: 合成公司\n"
        f"付款人账号: 998877\n收款人账号: {PA_ACCOUNT}\n小写：30.00\n备注：租金"
    )
    pa_expense = (
        "收付款业务回单\n记账日期：2026-10-10\n回单号：P002\n"
        "付款人名称: 合成公司\n收款人名称: 合成供应商\n"
        f"付款人账号: {PA_ACCOUNT}\n收款人账号: 998877\n小写：20.00\n备注：合同付款单"
    )
    pa_interest = "协定存款利息回单\n记账日期：2026-10-21\n回单号：P003\n收款人账号：" + PA_ACCOUNT + "\n小写：8.57"
    data = _write_pdf(pa_pdf, [[pa_income, pa_expense, pa_interest]])
    zip_path = tmp_path / f"{PA_ACCOUNT}_20261001-20261031_1.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("nested/member.pdf", data)

    pa_items = parse_source(SourceFile(path=zip_path, bank="平安", account_hint=PA_ACCOUNT))

    assert len(pa_items) == 3
    assert [item.category for item in pa_items] == [
        "transfer_income",
        "transfer_expense",
        "bank_interest_income",
    ]
    assert all(item.source_member == "nested/member.pdf" for item in pa_items)
    assert pa_items[1].counterparty == "合成供应商"


def test_parse_landscape_tax_receipt(tmp_path: Path):
    path = tmp_path / "合成0354建行2026.10回单.pdf"
    text = (
        "电子缴税付款凭证\n付款人全称：合成公司\n"
        f"付款人账号：{CCB_ACCOUNT}\n小写（合计）金额：￥88.00\n"
        "缴款书交易流水号：TAX001\n年 月 日2026 10 11\n凭证字号：T001"
    )
    _write_pdf(path, [[text]], landscape=True)

    items = parse_source(SourceFile(path=path, bank="建行", account_hint="0354"))

    assert len(items) == 1
    assert items[0].category == "transfer_expense"
    assert items[0].kind == "电子缴税付款凭证"
    assert items[0].amount == Decimal("88.00")
    assert items[0].clip == (0, 0, 842.0, 595.0)


@pytest.mark.parametrize(
    ("text", "direction", "expected"),
    [
        ("收单清算款 手续费：￥2.00", "income", "pending_clearing_income"),
        ("活期利息 合计金额", "income", "bank_interest_income"),
        ("收费回单(付款通知)", "expense", "bank_fee_expense"),
        ("普通收款", "income", "transfer_income"),
        ("合同付款单", "expense", "transfer_expense"),
        ("无法识别", "", "unclassified"),
    ],
)
def test_classification_priority(text: str, direction: str, expected: str):
    item = ParsedReceipt(raw=text, direction=direction)
    assert classify_receipt(item) == expected


def test_discover_sources_across_year_and_ignore_statements(tmp_path: Path):
    names = [
        "合成0354建行2025.12回单.pdf",
        "合成0354建行2026.1-3回单.pdf",
        "合成0354建行2026.2回单.pdf",
        "合成0354建行2025.12银行对账单.ofd",
        "合成中行2026.1银行对账单.pdf",
        f"{PA_ACCOUNT}_20251201-20251231_1.zip",
        f"{PA_ACCOUNT}_20251101-20251130_1.zip",
        f"平安银行20260110_2101_001_{PA_ACCOUNT}.pdf",
    ]
    for name in names:
        (tmp_path / name).write_bytes(b"x")

    sources = discover_sources(tmp_path, date(2025, 12, 25), date(2026, 1, 31))
    names_out = [source.path.name for source in sources]

    assert "合成0354建行2025.12回单.pdf" in names_out
    assert "合成0354建行2026.1-3回单.pdf" in names_out
    assert "合成0354建行2026.2回单.pdf" not in names_out
    assert f"{PA_ACCOUNT}_20251201-20251231_1.zip" in names_out
    assert f"{PA_ACCOUNT}_20251101-20251130_1.zip" not in names_out
    assert f"平安银行20260110_2101_001_{PA_ACCOUNT}.pdf" in names_out
    assert all("对账单" not in name for name in names_out)


def test_deduplicate_receipts_across_sources():
    first = ParsedReceipt(
        bank="平安",
        account_no=PA_ACCOUNT,
        receipt_no="P001",
        rdate=date(2026, 10, 9),
        amount=Decimal("30.00"),
        source_pdf="bundle.zip::member.pdf",
    )
    duplicate = ParsedReceipt(
        bank="平安",
        account_no=PA_ACCOUNT,
        receipt_no="P001",
        rdate=date(2026, 10, 9),
        amount=Decimal("30.00"),
        source_pdf="平安银行-direct.pdf",
    )
    other = ParsedReceipt(
        bank="平安",
        account_no=PA_ACCOUNT,
        receipt_no="P002",
        rdate=date(2026, 10, 10),
        amount=Decimal("20.00"),
    )

    unique, duplicates = deduplicate_receipts([first, duplicate, other])

    assert len(unique) == 2
    assert duplicates == [duplicate]
    assert unique[0].source_pdf == "bundle.zip::member.pdf"
