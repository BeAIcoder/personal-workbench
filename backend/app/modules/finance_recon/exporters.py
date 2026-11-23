# -*- coding: utf-8 -*-
"""商户核销对账三格式导出公共入口（Task 4）。

唯一数据源是 ``reporting.build_report`` 产出的 ``ReportViewModel``：xlsx / Markdown /
docx 三种导出与 summary 端点共用同一份视图，本文件绝不回查业务库、绝不重算合计。

- ``export_xlsx``：五类工作表（汇总勾稽 / 核销逐笔 / 欠费账龄与违约金 / 问题清单 /
  待补资料）；所有字符串单元格经 ``app.utils_xlsx.safe_cell`` 防公式注入；金额以
  Decimal 直写单元格（全程不经 float），未填金额为空单元格（null 与 0 严格区分）；
- ``export_markdown`` / ``export_docx``：由 ``reporters.py`` 渲染（共享常量与视图行
  构造也在该文件，本文件接近 800 行软上限时按规则拆出），此处再导出保持公共 API；
- ``download_names``：中文文件名 + 纯 ASCII 兜底名（Content-Disposition 双头由
  router 拼装）；导出字段遵循 INV-006 最小字段原则，不含户名/完整账号。
"""
from __future__ import annotations

import io

from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

from app.utils_xlsx import safe_cell

from .reporters import (  # noqa: F401  公共 API 再导出：调用方/测试统一从 exporters 引用
    EMPTY_AGING_NOTE,
    EMPTY_CLAIM_NOTE,
    EMPTY_ISSUE_NOTE,
    EMPTY_SETTLEMENT_NOTE,
    EMPTY_SUPPLEMENT_NOTE,
    EMPTY_WRITEOFF_NOTE,
    KEY_TOTAL_LABELS,
    _batch_meta,
    _check_rows,
    _claim_rows,
    _issue_rows,
    _num,
    _settlement_rows,
    _supplement_items,
    download_names,
    export_docx,
    export_markdown,
    is_draft,
    strip_illegal_xml_chars,
)
from .reporting import ReportViewModel
from .reporters import (
    _CHECK_HEADERS,
    _CLAIM_HEADERS,
    _ISSUE_HEADERS,
    _SETTLEMENT_HEADERS,
    _SUPPLEMENT_HEADERS,
    DRAFT_NOTE,
    DRAFT_WARNING,
)

# xlsx 五类工作表（顺序即产出顺序）
SHEET_TITLES: tuple[str, ...] = ("汇总勾稽", "核销逐笔", "欠费账龄与违约金", "问题清单", "待补资料")

_WRITEOFF_HEADERS = (
    "序号", "来源文件", "源行号", "合同号", "单据号", "费用类型", "费用期起", "费用期止",
    "核销日期", "账单月", "费用期应收（元）", "费用期核销（元）", "核销后欠费（元）", "预收款金额（元）",
)


class _Grid:
    """xlsx 写表助手：统一 safe_cell（字符串）与 Decimal 直写（金额），并跟踪行号。

    所有字符串入口统一走 ``_text``：先清洗 XML 1.0 禁止控制字符（openpyxl 遇之
    抛 IllegalCharacterError → 500），再做公式注入防护（顺序不可颠倒：清洗后
    才以 ``=`` 开头的值仍要加前导引号）。"""

    def __init__(self, ws):
        self.ws = ws
        self.row = 0

    def _advance(self) -> int:
        self.row += 1
        return self.row

    @staticmethod
    def _text(value: str) -> str:
        return safe_cell(strip_illegal_xml_chars(value))

    def title(self, text: str) -> None:
        cell = self.ws.cell(row=self._advance(), column=1, value=self._text(text))
        cell.font = Font(bold=True, size=13)

    def kv(self, label: str, value) -> None:
        row = self._advance()
        self.ws.cell(row=row, column=1, value=self._text(label))
        self.ws.cell(row=row, column=2, value=self._text(value) if isinstance(value, str) else value)

    def section(self, text: str) -> None:
        cell = self.ws.cell(row=self._advance(), column=1, value=self._text(text))
        cell.font = Font(bold=True)

    def header(self, headers) -> None:
        row = self._advance()
        for col, text in enumerate(headers, start=1):
            cell = self.ws.cell(row=row, column=col, value=self._text(text))
            cell.font = Font(bold=True)

    def data(self, values) -> None:
        row = self._advance()
        for col, value in enumerate(values, start=1):
            self.ws.cell(row=row, column=col, value=self._text(value) if isinstance(value, str) else value)

    def blank(self) -> None:
        self._advance()


def _fit_widths(ws, min_width: int = 6, max_width: int = 46) -> None:
    """按内容估列宽（中文字符按 2 计），只做展示优化，不改动任何单元格内容。"""
    widths: dict[int, int] = {}
    for row in ws.iter_rows():
        for cell in row:
            if cell.value is None:
                continue
            length = sum(2 if ord(ch) > 127 else 1 for ch in str(cell.value))
            col = cell.column
            widths[col] = max(widths.get(col, min_width), length + 2)
    for col, width in widths.items():
        ws.column_dimensions[get_column_letter(col)].width = min(max_width, max(min_width, width))


def _xlsx_summary(grid: _Grid, view: ReportViewModel) -> None:
    batch = view.batch
    grid.title(f"商户核销对账汇总（批次 {batch['id']}）")
    for label, value in _batch_meta(view):
        grid.kv(label, value)
    grid.kv("待确认问题（项）", int(view.pending_count))
    grid.kv("待确认建议数（项）", int((view.matches.get("counts") or {}).get("suggested", 0)))
    grid.kv("已确认匹配数（项）", int((view.matches.get("counts") or {}).get("confirmed", 0)))
    if is_draft(view):
        grid.blank()
        grid.kv(DRAFT_WARNING, DRAFT_NOTE)
    grid.blank()
    grid.section("关键合计（元）")
    grid.header(("指标", "金额（元）"))
    for key, label in KEY_TOTAL_LABELS.items():
        grid.kv(label, _num(view.totals.get(key)))
    grid.blank()
    grid.section("勾稽检查结论")
    grid.header(_CHECK_HEADERS)
    for row in _check_rows(view):
        grid.data(row)
    grid.blank()
    grid.section("认领覆盖（机检建议与人工确认分摊）")
    claims = _claim_rows(view)
    grid.header(_CLAIM_HEADERS)
    if not claims:
        grid.data((EMPTY_CLAIM_NOTE,))
    for row in claims:
        grid.data(row)


def _xlsx_writeoffs(grid: _Grid, view: ReportViewModel) -> None:
    grid.title("核销逐笔（来源文件 + 源行号可追溯；未填金额留空，不按 0 补齐）")
    grid.header(_WRITEOFF_HEADERS)
    rows = view.writeoff_rows
    if not rows:
        grid.data((EMPTY_WRITEOFF_NOTE,))
        return
    for idx, row in enumerate(rows, start=1):
        grid.data(
            (
                idx,
                row["source_name"],
                int(row["row_no"]),
                row["contract_no"],
                row["doc_no"],
                row["fee_type"],
                row["fee_period_start"] or "",
                row["fee_period_end"] or "",
                row["writeoff_date"] or "",
                row["bill_month"],
                _num(row["amount_receivable"]),
                _num(row["amount_cleared"]),
                _num(row["amount_left"]),
                _num(row["amount_prepaid"]),
            )
        )


def _xlsx_aging_settlement(grid: _Grid, view: ReportViewModel) -> None:
    grid.title(f"欠费账龄与违约金（对账日 {view.batch['as_of']}；违约金为参考测算，不计入结算净额）")
    aging = view.aging
    buckets = aging.get("buckets") or []
    grid.section("账龄分桶（总计，按日历月）")
    if not buckets:
        grid.data((EMPTY_AGING_NOTE,))
    else:
        grid.header(("账龄区间", "金额（元）"))
        for bucket in buckets:
            grid.data((bucket["label"], _num(bucket["total"])))
        grid.kv("无法归账龄金额（元）", _num(aging.get("unaged_total")))
        grid.kv("账龄合计（元）", _num(aging.get("grand_total")))
        grid.kv(
            "账龄完整性",
            "完整" if aging.get("complete") else "不完整（存在无法归账龄行，账龄合计不完整）",
        )
    grid.blank()
    by_contract = aging.get("by_contract") or []
    if by_contract:
        grid.section("逐合同账龄")
        grid.header(("合同", "账龄区间", "金额（元）"))
        for item in by_contract:
            for bucket in item["buckets"]:
                grid.data((item["contract_no"], bucket["label"], _num(bucket["total"])))
            unaged = _num(item.get("unaged_total"))
            if unaged:
                grid.data((item["contract_no"], "无法归账龄", unaged))
        grid.blank()
    grid.section("结算草稿（当前欠费 − 已确认抵扣；违约金列为参考测算，不计入结算净额）")
    settlement = _settlement_rows(view)
    grid.header(_SETTLEMENT_HEADERS)
    if not settlement:
        grid.data((EMPTY_SETTLEMENT_NOTE,))
    for row in settlement:
        grid.data(row)


def _xlsx_issues(grid: _Grid, view: ReportViewModel) -> None:
    grid.title("问题清单（当前活动问题：机检待确认 + 人工登记；已解除/失效问题不在此列）")
    rows = _issue_rows(view)
    grid.header(_ISSUE_HEADERS)
    if not rows:
        grid.data((EMPTY_ISSUE_NOTE,))
    for row in rows:
        grid.data(row)


def _xlsx_supplements(grid: _Grid, view: ReportViewModel) -> None:
    grid.title("待补资料（人工登记的未决事项，来自当前 active 人工问题；不含静态文案）")
    items = _supplement_items(view)
    grid.header(_SUPPLEMENT_HEADERS)
    if not items:
        grid.data((EMPTY_SUPPLEMENT_NOTE,))
    for item in items:
        grid.data(
            (
                int(item["id"]),
                item["category_label"],
                item["status_label"],
                _num(item["amount_impact"]),
                "；".join(item["evidence"]),
                item["note"],
                item["created_at"],
            )
        )


def export_xlsx(view: ReportViewModel) -> io.BytesIO:
    """五类工作表底稿：金额 Decimal 直写、字符串全部 safe_cell、空态显式说明。"""
    workbook = Workbook()
    builders = (_xlsx_summary, _xlsx_writeoffs, _xlsx_aging_settlement, _xlsx_issues, _xlsx_supplements)
    for title, builder in zip(SHEET_TITLES, builders):
        ws = workbook.active if title == SHEET_TITLES[0] else workbook.create_sheet(title)
        ws.title = title
        grid = _Grid(ws)
        builder(grid, view)
        _fit_widths(ws)
    buf = io.BytesIO()
    workbook.save(buf)
    buf.seek(0)
    return buf
