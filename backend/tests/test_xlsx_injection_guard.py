# -*- coding: utf-8 -*-
"""xlsx 公式注入防护回归（2026-10-31）。

finance_iit/invoice/vat 三处导出此前把导入来的字符串直接写单元格，`=`
开头会被 Excel 当活公式执行；统一收敛到 utils_xlsx.safe_cell。
"""
from io import BytesIO

from openpyxl import Workbook, load_workbook

from app.utils_xlsx import safe_cell


def test_safe_cell_prefixes_quoted():
    for text in ("=cmd|calc", "+1", "-5月", "@SUM(1)"):
        assert safe_cell(text) == "'" + text


def test_safe_cell_keeps_normal_text_and_numbers():
    assert safe_cell("正常文本") == "正常文本"
    assert safe_cell(5) == 5
    assert safe_cell(None) is None
    assert safe_cell("") == ""


def test_written_cell_is_string_not_formula():
    wb = Workbook()
    ws = wb.active
    ws.append([safe_cell("=cmd|calc")])
    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    loaded = load_workbook(buf)
    cell = loaded.active["A1"]
    assert cell.data_type == "s"
    assert cell.value == "'=cmd|calc"
