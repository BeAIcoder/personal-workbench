# -*- coding: utf-8 -*-
"""finance_lease 导入导出测试（纯函数，无 DB）。

覆盖计划要求的 IO 边界：

- 模板与导出数据源同源：``importer.template_workbook_bytes`` 产出的模板必须能被
  ``parse_payment_schedule`` 解析，导出的「数据源」页可原样再导入（往返一致）；
- 标签锚点：表头不在第 1 行、列顺序变化、同义标签、未识别列都按标签定位，
  合计行/尾部说明行/连续空行停止解析且只告警；
- 严格性：免租期（0）与 0 月期保留；期次重复/不连续、非法日期、金额空/负/超
  两位小数、多合同号/多对象、无法识别的标识取值 → ``valid=False`` 或 ValueError；
- 导出五页签（数据源 / 评估表 / 期间表 / 校验 / 分录），金额两位、期间利率 4 位、
  折现因子 9 位；缺失值写空而不补 0；缺校验结果或校验为 unknown 时绝不显示通过；
- 所有字符串经 ``safe_cell``，重新打开后不是活公式；下载名中文 + 纯 ASCII 兜底。

全部数据为合成值，不读真实底稿、不碰 backend/data。
"""
from __future__ import annotations

import copy
import io
import json
import pathlib
from datetime import date
from decimal import Decimal

import openpyxl
import pytest

from app.modules.finance_lease import exporter, importer

HEADERS = ["支付期数", "计租起始日期", "计租结束日期", "金额（不含税）"]
TEMPLATE_PATH = (
    pathlib.Path(__file__).resolve().parents[2] / "数据模板" / importer.TEMPLATE_FILENAME
)


def _sheet_bytes(rows, headers=HEADERS, title="数据源", start_row=1, extra_sheets=()):
    workbook = openpyxl.Workbook()
    ws = workbook.active
    ws.title = title
    for offset, label in enumerate(headers, start=1):
        ws.cell(row=start_row, column=offset, value=label)
    for offset, row in enumerate(rows, start=start_row + 1):
        for column, value in enumerate(row, start=1):
            ws.cell(row=offset, column=column, value=value)
    for name, content in extra_sheets:
        extra = workbook.create_sheet(name)
        extra["A1"] = content
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _payload():
    return {
        "contract": {
            "contract_no": "SYN-001",
            "lessee_name": "合成主体",
            "project_name": "合成项目",
            "lessor_name": "合成出租方",
            "start_date": "2026-01-01",
            "end_date": "2027-06-30",
            "annual_rate": "0.035",
            "rate_basis": "合成利率依据",
            "note": "合成备注",
            "currency": "CNY",
        },
        "calculation": {
            "id": 9,
            "status": "calculated",
            "status_label": "已计算",
            "calculated_at": "2026-11-20 09:30:00",
            "rule_version": "rules-1",
            "input_hash": "hash-abc",
            "stale": False,
        },
        "totals": {
            "asset_total": "28500.00",
            "liability_total": "28500.00",
            "payment_total": "30000.00",
            "unrecognized_finance_cost": "1500.00",
        },
        "payments": [
            {
                "sequence": 1, "serial_no": "1", "project_code": "OBJ-1",
                "contract_no": "SYN-001", "lessor_code": "L1", "contract_type": "3",
                "handover_date": "2026-01-01", "start_date": "2026-01-01",
                "end_date": "2026-06-30", "amount": "10000.00", "currency": "CNY",
                "invoice_required": True, "opening_flag": True,
                "opening_payment_flag": True, "note": "合成首期",
            },
            {
                "sequence": 2, "serial_no": "2", "project_code": "OBJ-1",
                "start_date": "2026-07-01", "end_date": "2027-06-30",
                "amount": "20000.00", "currency": "CNY", "invoice_required": False,
                "opening_flag": False, "opening_payment_flag": False, "note": None,
            },
        ],
        "assessments": [
            {
                "sequence": 1, "months": 6, "amount": "10000.00", "annual_rate": "0.035",
                "period_rate": "0.0175", "discount_factor": "1.000000000",
                "present_value": "10000.00", "interest_total": "0.00",
                "interest": "159.25", "liability_balance": "10159.25", "source_row": 4,
            },
            {
                "sequence": 2, "months": 12, "amount": "20000.00", "annual_rate": "0.035",
                "period_rate": "0.0350", "discount_factor": "1.025806452",
                "present_value": "19496.85", "interest_total": "503.15",
                "interest": "503.15", "liability_balance": "0.00", "source_row": 5,
            },
        ],
        "periods": [
            {
                "period": "2026-01", "months": 1, "depreciation": "1583.33",
                "payment_due": None, "interest": "26.54", "asset_balance": "26916.67",
                "liability_balance": "10159.25", "sequence": 1,
            },
            {
                "period": "2026-07", "months": 1, "depreciation": "1583.33",
                "payment_due": "20000.00", "interest": "41.93",
                "asset_balance": "25333.34", "liability_balance": "0.00", "sequence": 2,
            },
        ],
        "checks": {
            "status": "pass",
            "items": [
                {"code": "C1", "title": "Σ折旧 = 使用权资产原值", "status": "pass",
                 "left": "28500.00", "right": "28500.00", "diff": "0.00",
                 "tolerance": "0.01", "message": ""},
            ],
            "counts": {"pass": 1, "fail": 0, "unknown": 0},
        },
        "entries": [
            {"number": "记-0001", "date": "2026-01-01", "summary": "初始确认",
             "account": "使用权资产", "debit": "28500.00", "credit": None, "source": "测算#9"},
            {"number": "记-0001", "date": "2026-01-01", "summary": "初始确认",
             "account": "租赁负债", "debit": None, "credit": "28500.00", "source": "测算#9"},
        ],
    }


def _rows_by_sheet(data: bytes) -> dict[str, list[tuple]]:
    workbook = openpyxl.load_workbook(io.BytesIO(data))
    return {ws.title: list(ws.iter_rows(values_only=True)) for ws in workbook.worksheets}


def _flat(values) -> str:
    return "\n".join("|".join("" if cell is None else str(cell) for cell in row) for row in values)


def _cell_under(rows, header_first: str, label: str, offset: int = 0):
    """按表头文字取真实单元格值：表头 ⇄ 取值列 ⇄ 服务字段一致性用。"""
    header_index = next(i for i, row in enumerate(rows) if row and row[0] == header_first)
    column = list(rows[header_index]).index(label)
    return rows[header_index + 1 + offset][column]


def _num(value) -> Decimal | None:
    return None if value is None else Decimal(str(value))


# ---------------- 导入：模板与标签锚点 ----------------

def test_template_bytes_parse_and_reopen():
    data = importer.template_workbook_bytes()
    workbook = openpyxl.load_workbook(io.BytesIO(data))
    assert workbook.sheetnames == [importer.GUIDE_SHEET_NAME, importer.DATA_SHEET_NAME]

    result = importer.parse_payment_schedule(data, importer.TEMPLATE_FILENAME)
    assert result.valid, [issue.message for issue in result.errors]
    assert result.row_count == 3
    assert result.total_amount == Decimal("30000.00")
    assert result.sheet_name == importer.DATA_SHEET_NAME
    assert result.contract_no == "DEMO-LT-001"
    assert [row.amount for row in result.rows] == [Decimal("10000.00")] * 3
    assert [row.months for row in result.rows] == [6, 6, 6]
    assert result.rows[0].opening_payment_flag is True
    assert result.rows[1].opening_payment_flag is False


def test_shipped_template_file_matches_code_contract():
    assert TEMPLATE_PATH.is_file(), f"模板缺失：{TEMPLATE_PATH}"
    shipped = importer.parse_payment_schedule(TEMPLATE_PATH.read_bytes(), TEMPLATE_PATH.name)
    generated = importer.parse_payment_schedule(
        importer.template_workbook_bytes(), importer.TEMPLATE_FILENAME,
    )
    assert shipped.valid and generated.valid
    assert shipped.to_dict()["rows"] == generated.to_dict()["rows"]


def test_parse_uses_label_anchor_not_row_number():
    data = _sheet_bytes(
        [
            [1, date(2026, 1, 1), date(2026, 6, 30), 1000, "OBJ-9", "含税列忽略"],
            [2, date(2026, 7, 1), date(2026, 7, 1), 0, "OBJ-9", ""],
        ],
        headers=["期次", "起始日", "截止日", "金额", "对象", "票据号"],
        start_row=7,
        extra_sheets=[("填写说明", "辅助页不参与解析")],
    )
    result = importer.parse_payment_schedule(data, "锚点.xlsx")
    assert result.valid, [issue.message for issue in result.errors]
    assert result.header_row == 7
    assert result.row_count == 2
    assert result.project_code == "OBJ-9"
    assert result.ignored_columns == ["票据号"]
    assert any("已忽略未识别列" in issue.message for issue in result.warnings)


def test_parse_keeps_zero_amount_and_zero_month_periods():
    data = _sheet_bytes([
        [1, "2026-01-01", "2026-06-30", "1000.00"],
        [2, "2026-07-01", "2026-07-01", "0"],
        [3, "2026-08-01", "2026-12-31", "1000.00"],
    ])
    result = importer.parse_payment_schedule(data)
    assert result.valid
    assert result.row_count == 3
    assert result.zero_amount_periods == 1
    assert result.zero_month_periods == 1
    assert result.rows[1].amount == Decimal("0.00")
    assert result.rows[1].months == 0
    assert result.total_amount == Decimal("2000.00")


def test_parse_stops_at_total_row_and_footer_note():
    total_data = _sheet_bytes([
        [1, "2026-01-01", "2026-06-30", 100],
        ["合计", None, None, 100],
        [2, "2026-07-01", "2026-12-31", 100],
    ])
    result = importer.parse_payment_schedule(total_data)
    assert result.row_count == 1
    assert result.stopped_at_row == 3
    assert any("合计行" in issue.message for issue in result.warnings)

    footer_data = _sheet_bytes([
        [1, "2026-01-01", "2026-06-30", 100],
        [None, None, None, None],
        ["口径：金额为不含税应付额", None, None, None],
    ])
    footer = importer.parse_payment_schedule(footer_data)
    assert footer.row_count == 1
    assert footer.valid
    assert any("尾部说明行" in issue.message for issue in footer.warnings)


def test_parse_rejects_duplicate_and_gap_sequences():
    duplicate = importer.parse_payment_schedule(_sheet_bytes([
        [1, "2026-01-01", "2026-06-30", 100],
        [2, "2026-07-01", "2026-12-31", 100],
        [2, "2027-01-01", "2027-06-30", 100],
    ]))
    assert not duplicate.valid
    assert any("支付期数重复" in issue.message for issue in duplicate.errors)

    gap = importer.parse_payment_schedule(_sheet_bytes([
        [1, "2026-01-01", "2026-06-30", 100],
        [2, "2026-07-01", "2026-12-31", 100],
        [4, "2027-01-01", "2027-06-30", 100],
    ]))
    assert not gap.valid
    messages = " ".join(issue.message for issue in gap.errors)
    assert "连续编号" in messages and "应为 3" in messages


def test_parse_rejects_invalid_values():
    data = _sheet_bytes(
        [
            [1, "2026-02-30", "2026-06-30", "1.005", "待定"],
            [2, "2026-07-01", "2026-12-31", "-5", "X"],
            [3, "2027-01-01", "2027-06-30", None, None],
            [4, "2027-07-01", "2027-06-30", "100.00", None],
        ],
        headers=HEADERS + ["期初支付标识"],
    )
    result = importer.parse_payment_schedule(data)
    assert not result.valid
    messages = " ".join(issue.message for issue in result.errors)
    assert "日期不存在" in messages
    assert "超过 2 位小数" in messages
    assert "金额不能为负" in messages
    assert "金额为空" in messages
    assert "期初支付标识取值无法识别" in messages
    assert "计租结束日期早于起始日期" in messages
    assert {issue.row for issue in result.errors} >= {2, 3, 4}


def test_parse_rejects_multi_contract_and_multi_project():
    multi_contract = importer.parse_payment_schedule(_sheet_bytes([
        [1, "2026-01-01", "2026-06-30", 100, "SYN-A"],
        [2, "2026-07-01", "2026-12-31", 100, "SYN-B"],
    ], headers=HEADERS + ["合同编号"]))
    assert not multi_contract.valid
    assert any("多个合同编号" in issue.message for issue in multi_contract.errors)

    multi_project = importer.parse_payment_schedule(_sheet_bytes([
        [1, "2026-01-01", "2026-06-30", 100, "OBJ-A"],
        [2, "2026-07-01", "2026-12-31", 100, "OBJ-B"],
    ], headers=HEADERS + ["对象"]))
    assert not multi_project.valid
    assert any("多个对象" in issue.message for issue in multi_project.errors)


def test_parse_flag_values_and_warnings():
    data = _sheet_bytes(
        [
            [1, "2026-01-01", "2026-06-30", 100, "是", "X", "X"],
            [2, "2026-07-01", "2026-12-31", 100, "否", "", "X"],
        ],
        headers=HEADERS + ["是否提供发票", "期初标识", "期初支付标识"],
    )
    result = importer.parse_payment_schedule(data)
    assert result.valid
    assert result.rows[0].invoice_required is True
    assert result.rows[1].invoice_required is False
    assert any("期初支付标识" in issue.message for issue in result.warnings)


def test_parse_rejects_unreadable_input(monkeypatch):
    with pytest.raises(ValueError, match="文件为空"):
        importer.parse_payment_schedule(b"", "空.xlsx")
    with pytest.raises(ValueError, match="不是可读取的 xlsx"):
        importer.parse_payment_schedule(b"not-an-xlsx", "坏文件.xlsx")
    with pytest.raises(ValueError, match="未找到付款计划表头"):
        importer.parse_payment_schedule(_sheet_bytes([[1, "2026-01-01", "2026-06-30", 1]],
                                                    headers=["甲", "乙", "丙", "丁"]))
    monkeypatch.setattr(importer, "MAX_UPLOAD_BYTES", 16)
    with pytest.raises(ValueError, match="上限"):
        importer.parse_payment_schedule(b"x" * 32, "大文件.xlsx")


def test_preview_adapter_accepts_schedule_dto():
    """后端接缝 PaymentImportPreview.from_schedule 必须能消费本 DTO（含 months）。"""
    from app.modules.finance_lease.schemas import PaymentImportPreview

    result = importer.parse_payment_schedule(importer.template_workbook_bytes())
    preview = PaymentImportPreview.from_schedule(result)
    assert preview.errors == []
    assert [item.months for item in preview.items] == [6, 6, 6]
    assert preview.items[0].amount == Decimal("10000.00")
    assert preview.items[0].prepay_flag is True


def test_parse_to_dict_is_json_serializable():
    result = importer.parse_payment_schedule(importer.template_workbook_bytes())
    payload = json.loads(json.dumps(result.to_dict(), ensure_ascii=False))
    assert payload["valid"] is True
    assert payload["rows"][0]["amount"] == "10000.00"
    assert payload["rows"][0]["start_date"] == "2026-01-01"
    assert payload["zero_month_periods"] == 0


# ---------------- 导出：五页签与数值 ----------------

def test_export_workbook_structure_and_values():
    data = exporter.export_calculation_workbook(_payload())
    sheets = _rows_by_sheet(data)
    assert list(sheets) == list(exporter.SHEET_TITLES)

    data_sheet = sheets["数据源"]
    header_row = next(i for i, row in enumerate(data_sheet) if row[0] == "支付期数")
    assert tuple(data_sheet[header_row]) == tuple(label for _, label in exporter.PAYMENT_HEADERS)
    first = data_sheet[header_row + 1]
    assert first[0] == 1
    start_cell = first[7]
    assert (start_cell.date() if hasattr(start_cell, "date") else start_cell) == date(2026, 1, 1)
    assert first[9] == 10000
    assert first[11] == "是"
    assert data_sheet[header_row + 2][12] is None

    assessment_sheet = sheets["评估表"]
    text = _flat(assessment_sheet)
    assert "28500.00" in text or "28500" in text
    assert "使用权资产原值" in text and "未确认融资费用" in text
    periods_text = _flat(sheets["期间表"])
    assert "2026-01" in periods_text and "1583.33" in periods_text
    entries_text = _flat(sheets["分录"])
    assert "记-0001" in entries_text and "28500" in entries_text

    workbook = openpyxl.load_workbook(io.BytesIO(data))
    rate_cell = None
    for row in workbook["评估表"].iter_rows():
        if row[0].value == 1:
            rate_cell = row[4]
            break
    assert rate_cell is not None
    assert rate_cell.number_format == "0.0000"
    assert float(rate_cell.value) == 0.0175


def test_export_columns_match_service_fields():
    """表头 ⇄ 取值列 ⇄ 服务 payload 键名三方一致：按真实单元格值断言，不只看表头。"""
    sheets = _rows_by_sheet(exporter.export_calculation_workbook(_payload()))
    assessments = sheets["评估表"]
    assert _cell_under(assessments, "期次", "月期数") == 6
    assert _num(_cell_under(assessments, "期次", "现值")) == Decimal("10000.00")
    assert _num(_cell_under(assessments, "期次", "利息（非实际当期）")) == Decimal("0.00")
    assert _num(_cell_under(assessments, "期次", "当期利息")) == Decimal("159.25")
    assert _num(_cell_under(assessments, "期次", "租赁负债余额")) == Decimal("10159.25")
    assert _cell_under(assessments, "期次", "源行号") == 4

    periods = sheets["期间表"]
    header = next(row for row in periods if row and row[0] == "月份")
    assert "月期数" not in header
    assert "本行月数（每月=1）" in header
    assert _cell_under(periods, "月份", "本行月数（每月=1）") == 1
    assert _num(_cell_under(periods, "月份", "月折旧")) == Decimal("1583.33")
    assert _cell_under(periods, "月份", "应付") is None
    assert _num(_cell_under(periods, "月份", "月利息")) == Decimal("26.54")
    assert _num(_cell_under(periods, "月份", "使用权资产净值")) == Decimal("26916.67")
    assert _num(_cell_under(periods, "月份", "租赁负债余额", offset=1)) == Decimal("0.00")
    assert _cell_under(periods, "月份", "对应期次", offset=1) == 2


def test_period_sheet_rejects_non_monthly_rows():
    payload = _payload()
    payload["periods"][0]["months"] = 3
    with pytest.raises(ValueError, match="本行月数"):
        exporter.export_calculation_workbook(payload)


def test_export_money_and_factor_precision():
    payload = _payload()
    payload["payments"][0]["amount"] = "1000.005"
    payload["assessments"][0]["discount_factor"] = "1.0258064516"
    data = exporter.export_calculation_workbook(payload)
    workbook = openpyxl.load_workbook(io.BytesIO(data))
    data_sheet = workbook["数据源"]
    header_row = next(
        i for i in range(1, data_sheet.max_row + 1) if data_sheet.cell(i, 1).value == "支付期数"
    )
    assert data_sheet.cell(header_row + 1, 10).value == 1000.01
    factor_cell = None
    for row in workbook["评估表"].iter_rows():
        if row[0].value == 1:
            factor_cell = row[5]
            break
    assert factor_cell.number_format == "0.000000000"


def test_export_formula_injection_is_neutralized():
    payload = _payload()
    payload["contract"]["note"] = "=HYPERLINK(\"http://x\")"
    payload["payments"][0]["note"] = "@SUM(A1:A9)"
    payload["checks"]["items"][0]["message"] = "+cmd"
    payload["entries"][0]["summary"] = "-1+1"
    data = exporter.export_calculation_workbook(payload)
    workbook = openpyxl.load_workbook(io.BytesIO(data))
    values = [
        cell.value
        for ws in workbook.worksheets
        for row in ws.iter_rows()
        for cell in row
        if isinstance(cell.value, str) and cell.value.startswith("'")
    ]
    assert any("=HYPERLINK" in value for value in values)
    assert any("@SUM" in value for value in values)
    assert any("+cmd" in value for value in values)
    assert any("-1+1" in value for value in values)
    for ws in workbook.worksheets:
        for row in ws.iter_rows():
            for cell in row:
                assert cell.data_type != "f", (ws.title, cell.coordinate)


def test_export_missing_checks_is_unknown_not_pass():
    payload = _payload()
    payload["checks"] = None
    text = _flat(_rows_by_sheet(exporter.export_calculation_workbook(payload))["校验"])
    assert "未知" in text
    assert "未提供校验结果" in text
    assert "总结论" not in text
    assert "校验结果 · 通过" not in text


def test_export_unknown_check_item_never_shows_pass():
    payload = _payload()
    payload["checks"] = {
        "status": "unknown",
        "items": [{"code": "C9", "title": "负债滚动", "status": "unknown",
                   "left": None, "right": "1", "diff": None, "tolerance": "0.01",
                   "message": "缺付款期数据"}],
    }
    text = _flat(_rows_by_sheet(exporter.export_calculation_workbook(payload))["校验"])
    assert "校验结果 · 未知" in text
    assert "通过 0 / 未通过 0 / 未知 1" in text
    assert "负债滚动" in text and "未知" in text


def test_export_empty_entries_states_scope():
    payload = _payload()
    payload["entries"] = []
    text = _flat(_rows_by_sheet(exporter.export_calculation_workbook(payload))["分录"])
    assert "本批次无逐笔底稿分录" in text
    assert "未过账/不回写 BIP" in text
    assert "租金变更" in text and "不支持" in text


def test_export_missing_assessment_rows_do_not_fabricate_numbers():
    payload = _payload()
    payload["assessments"] = []
    payload["periods"] = []
    sheets = _rows_by_sheet(exporter.export_calculation_workbook(payload))
    assert "无评估行" in _flat(sheets["评估表"])
    assert "无期间行" in _flat(sheets["期间表"])


def test_export_rejects_broken_payload():
    payload = _payload()
    with pytest.raises(ValueError, match="缺少「contract」"):
        exporter.export_calculation_workbook({})
    payload["payments"] = []
    with pytest.raises(ValueError, match="付款计划为空"):
        exporter.export_calculation_workbook(payload)
    payload = _payload()
    payload["checks"] = {"status": "passed", "items": []}
    with pytest.raises(ValueError, match="pass / fail / unknown"):
        exporter.export_calculation_workbook(payload)


def test_export_accepts_backend_snapshot_payload_shape():
    """后端 _export_payload 的实际键形（无 totals / 无 tolerance / 空 entries）也能导出。"""
    payload = {
        "contract": {"contract_no": "SYN-002", "lessee_name": "", "project_name": "",
                     "lessor_name": "", "start_date": "2026-01-01", "end_date": "2026-12-31",
                     "annual_rate": "0.035", "currency": "CNY"},
        "calculation": {"id": 3, "status": "calculated", "status_label": "已计算",
                        "calculated_at": "2026-11-20T09:30:00", "rule_version": "r1",
                        "input_hash": "h1", "stale": False, "stale_reason": ""},
        "payments": [{"sequence": 1, "contract_no": "SYN-002", "start_date": "2026-01-01",
                      "end_date": "2026-12-31", "amount": "1200.00", "currency": "CNY",
                      "opening_flag": True, "opening_payment_flag": False, "note": ""}],
        "assessments": [{"sequence": 1, "months": 12, "amount": "1200.00",
                         "annual_rate": "0.035", "period_rate": "0.0350",
                         "discount_factor": "1.000000000", "present_value": "1200.00",
                         "interest_total": "0.00", "interest": "3.50",
                         "liability_balance": "1242.00", "source_row": 4}],
        "periods": [{"period": "2026-01", "months": 1, "depreciation": "100.00",
                     "payment_due": "1200.00", "interest": "3.50",
                     "asset_balance": "42.00", "liability_balance": "42.00", "sequence": 1}],
        "checks": {"status": "pass", "counts": {"ok": 3, "failed": 0, "unknown": 0},
                   "items": [{"code": "C1", "title": "Σ折旧", "status": "pass",
                              "left": "1200.00", "right": "1200.00", "diff": "0.00",
                              "message": ""}]},
        "entries": [],
    }
    sheets = _rows_by_sheet(exporter.export_calculation_workbook(payload))
    assert list(sheets) == list(exporter.SHEET_TITLES)
    assert "未提供" in _flat(sheets["评估表"])
    checks_text = _flat(sheets["校验"])
    assert "Σ折旧" in checks_text
    assert "通过 3 / 未通过 0 / 未知 0" in checks_text
    assert "本批次无逐笔底稿分录" in _flat(sheets["分录"])
    assert _num(_cell_under(sheets["评估表"], "期次", "当期利息")) == Decimal("3.50")
    assert _num(_cell_under(sheets["期间表"], "月份", "使用权资产净值")) == Decimal("42.00")
    assert _cell_under(sheets["期间表"], "月份", "本行月数（每月=1）") == 1


def test_export_stale_calculation_is_marked():
    payload = _payload()
    payload["calculation"]["stale"] = True
    payload["calculation"]["stale_reason"] = "付款计划在测算后被修改"
    text = _flat(_rows_by_sheet(exporter.export_calculation_workbook(payload))["数据源"])
    assert "【已过期】" in text
    assert "不得作为当前结果使用" in text


def test_export_download_names_have_ascii_fallback():
    payload = _payload()
    payload["contract"]["contract_no"] = "合成/合同:01"
    cn_name, ascii_name = exporter.download_names(payload, stamp="20261120_101010")
    assert cn_name.startswith("租赁核算_") and cn_name.endswith(".xlsx")
    assert "/" not in cn_name and ":" not in cn_name
    assert ascii_name.isascii()
    assert ascii_name.startswith("lease_") and "01" in ascii_name
    assert ascii_name.endswith("20261120_101010.xlsx")


def test_exported_data_sheet_reimports():
    data = exporter.export_calculation_workbook(_payload())
    result = importer.parse_payment_schedule(data, "导出再导入.xlsx")
    assert result.valid, [issue.message for issue in result.errors]
    assert result.sheet_name == "数据源"
    assert result.row_count == 2
    assert result.total_amount == Decimal("30000.00")
    assert result.contract_no == "SYN-001"
    assert result.rows[0].opening_payment_flag is True
    assert result.rows[1].opening_payment_flag is False
    assert result.zero_month_periods == 0


def test_export_does_not_mutate_payload():
    payload = _payload()
    snapshot = copy.deepcopy(payload)
    exporter.export_calculation_workbook(payload)
    assert payload == snapshot
