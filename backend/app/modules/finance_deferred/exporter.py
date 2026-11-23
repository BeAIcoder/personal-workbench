# -*- coding: utf-8 -*-
"""长期待摊 · 摊销底稿 xlsx 导出（项目参数 / 月度摊销 / 校验）。

数据源是服务层按**测算快照**组装的 :class:`DeferredExportBundle`；本文件不读业务库、
不重算金额。金额以两位 ``Decimal`` 直写，税率按百分比格式；缺失值写「未提供」或空
单元格而不是 0；校验结论四态原样披露（通过/警告/差异/未知），未知不得写成通过。

所有字符串单元格经 ``app.utils_xlsx.safe_cell`` 防公式注入；产物可用 openpyxl 回读。
"""
from __future__ import annotations

import io
import re
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from app.utils_xlsx import safe_cell

from .schemas import CHECK_STATUS_LABELS, DeferredExportBundle, DeferredExportFile

SHEET_TITLES: tuple[str, ...] = ("项目参数", "月度摊销", "校验")

_CENT = Decimal("0.01")
_MONEY_FMT = "#,##0.00"
_PERCENT_FMT = "0.0000%"
_ILLEGAL_XML = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_HEADER_FILL = PatternFill("solid", fgColor="DCE6F1")

_PERIOD_HEADERS = ("月份", "序号", "月摊金额", "累计摊销", "余额（净额−累计）", "剩余月数")
_CHECK_HEADERS = ("检查项", "结论", "期望值", "实际值", "差异", "说明")

_CALIBER_NOTES = (
    "口径：净额 = 含税金额 ÷ (1 + 税率)，两位四舍五入；月摊 = 净额 ÷ 摊销月数，两位四舍五入。",
    "末期尾差如实留在末期余额（余额 = 净额 − 累计摊销），不吸收进末期、不自动转销。",
    "摊销到期月晚于租赁到期日只作警告，不自动缩短摊销年限；本页不写回 BIP、不过账。",
)


def _safe_text(value: Any) -> str:
    return safe_cell(_ILLEGAL_XML.sub("", str(value)))


def _money(value: Any) -> Decimal | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    return Decimal(str(value)).quantize(_CENT, rounding=ROUND_HALF_UP)


class _Grid:
    """统一 safe_cell（字符串）与 Decimal/date 直写，并跟踪当前行号。"""

    def __init__(self, ws):
        self.ws = ws
        self.row = 0

    def title(self, text: str) -> None:
        self.row += 1
        self.ws.cell(row=self.row, column=1, value=_safe_text(text)).font = Font(bold=True, size=13)

    def note(self, text: str) -> None:
        self.row += 1
        self.ws.cell(row=self.row, column=1, value=_safe_text(text)).font = Font(
            italic=True, color="595959")

    def section(self, text: str) -> None:
        self.row += 1
        self.ws.cell(row=self.row, column=1, value=_safe_text(text)).font = Font(bold=True)

    def kv(self, label: str, value: Any, fmt: str | None = None) -> None:
        self.row += 1
        self.ws.cell(row=self.row, column=1, value=_safe_text(label)).font = Font(bold=True)
        cell = self.ws.cell(row=self.row, column=2, value=_cell_value(value))
        if fmt:
            cell.number_format = fmt

    def header(self, labels) -> None:
        self.row += 1
        for column, text in enumerate(labels, start=1):
            cell = self.ws.cell(row=self.row, column=column, value=_safe_text(text))
            cell.font, cell.fill = Font(bold=True), _HEADER_FILL
            cell.alignment = Alignment(vertical="center", wrap_text=True)

    def data(self, values, formats: dict[int, str] | None = None) -> None:
        self.row += 1
        for column, value in enumerate(values, start=1):
            cell = self.ws.cell(row=self.row, column=column, value=_cell_value(value))
            fmt = (formats or {}).get(column)
            if fmt:
                cell.number_format = fmt

    def blank(self) -> None:
        self.row += 1


def _cell_value(value: Any) -> Any:
    if isinstance(value, str):
        return _safe_text(value)
    if isinstance(value, Decimal):
        return value.quantize(_CENT, rounding=ROUND_HALF_UP)
    return value


def _fit_widths(ws, min_width: int = 8, max_width: int = 46) -> None:
    widths: dict[int, int] = {}
    for row in ws.iter_rows():
        for cell in row:
            if cell.value is None:
                continue
            length = sum(2 if ord(ch) > 127 else 1 for ch in str(cell.value))
            widths[cell.column] = max(widths.get(cell.column, 0), length)
    for column, width in widths.items():
        ws.column_dimensions[get_column_letter(column)].width = max(
            min_width, min(max_width, width + 2))


def _status_text(status: str) -> str:
    return CHECK_STATUS_LABELS.get(status, "未知")


def _project_sheet(grid: _Grid, bundle: DeferredExportBundle) -> None:
    item, run = bundle.item, bundle.run
    prefix = "【已过期】" if bundle.stale else ""
    grid.title(
        f"{prefix}长期待摊 · 租入资产改良支出 · 项目 {item.project_code} "
        f"· 测算 #{run.id} · {run.status_label}"
    )
    grid.kv("项目编码", item.project_code)
    grid.kv("项目名称", item.name)
    grid.kv("所属项目", item.project)
    grid.kv("含税金额", _money(item.amount_incl_tax), _MONEY_FMT)
    grid.kv("税率", item.tax_rate, _PERCENT_FMT)
    grid.kv("净额（含税÷(1+税率)）", _money(item.amount_net), _MONEY_FMT)
    grid.kv("税额（含税−净额）", _money(item.tax_amount), _MONEY_FMT)
    grid.kv("起摊月", item.start_month)
    grid.kv("摊销月数", item.months)
    grid.kv("摊销到期月", run.summary.end_month)
    grid.kv("租赁到期日", item.lease_end_date.isoformat())
    grid.kv("摊销是否超租赁到期日", "是（仅警告，未缩短年限）" if run.summary.expiry_after_lease else "否")
    grid.kv("项目状态", item.status_label)
    grid.kv("规则版本 / 输入哈希", f"{run.rule_version} / {run.input_hash}")
    grid.kv("测算时间", run.created_at.isoformat(sep=" ", timespec="seconds"))
    grid.kv("确认时间",
            run.confirmed_at.isoformat(sep=" ", timespec="seconds") if run.confirmed_at else "未确认")
    grid.kv("风险已确认", "是" if run.risk_acknowledged else "否")
    grid.kv("风险说明", run.risk_note or "未填写")
    if item.remark:
        grid.kv("备注", item.remark)
    grid.blank()
    grid.section("摊销汇总")
    summary = run.summary
    grid.kv("月摊金额", _money(summary.monthly_amount), _MONEY_FMT)
    grid.kv("累计摊销（月摊×月数）", _money(summary.amortized_total), _MONEY_FMT)
    grid.kv("末期尾差（净额−累计摊销）", _money(summary.rounding_difference), _MONEY_FMT)
    grid.kv("期间行数", summary.period_count)
    grid.blank()
    for text in _CALIBER_NOTES:
        grid.note(text)
    if bundle.stale:
        grid.note("本批次结果已过期：项目参数在测算后被修改，仅供追溯查看，不得作为当前结果使用。")


def _period_sheet(grid: _Grid, bundle: DeferredExportBundle) -> None:
    item, run = bundle.item, bundle.run
    grid.title(f"月度摊销 · 项目 {item.project_code} · 测算 #{run.id}")
    grid.note("一行 = 一个摊销月份；月摊每月相同；余额 = 净额 − 累计摊销，末期余额即尾差。")
    grid.header(_PERIOD_HEADERS)
    formats = {3: _MONEY_FMT, 4: _MONEY_FMT, 5: _MONEY_FMT}
    if not bundle.periods:
        grid.data(("无月度摊销行（本次测算未提供）",))
        return
    for row in bundle.periods:
        grid.data(
            (
                row.period_month,
                row.sequence,
                _money(row.monthly_amount),
                _money(row.cumulative),
                _money(row.balance),
                row.remaining_months,
            ),
            formats=formats,
        )
    grid.blank()
    summary = run.summary
    grid.note("合计：累计摊销 = 月摊 × 月数；余额列即末期尾差（净额 − 累计摊销），未吸差。")
    grid.data(
        ("合计", None, _money(summary.amortized_total), None,
         _money(summary.rounding_difference), 0),
        formats={3: _MONEY_FMT, 5: _MONEY_FMT},
    )


def _checks_sheet(grid: _Grid, bundle: DeferredExportBundle) -> None:
    run = bundle.run
    counts = run.check_counts
    statuses = [check.status for check in run.checks]
    if not run.checks:
        overall = "unknown"
    elif "failed" in statuses:
        overall = "failed"
    elif "unknown" in statuses:
        overall = "unknown"
    elif "warning" in statuses:
        overall = "warning"
    else:
        overall = "ok"
    grid.title(f"校验结果 · {_status_text(overall)}")
    grid.kv("总结论", _status_text(overall))
    grid.kv("检查项统计", f"通过 {counts.get('ok', 0)} / 警告 {counts.get('warning', 0)} / "
                          f"差异 {counts.get('failed', 0)} / 未知 {counts.get('unknown', 0)}")
    grid.kv("风险确认", "是" if run.risk_acknowledged else "否")
    grid.kv("风险说明", run.risk_note or "未填写")
    for flag in run.summary.risk_flags:
        grid.kv("风险提示", flag)
    grid.blank()
    grid.header(_CHECK_HEADERS)
    formats = {3: _MONEY_FMT, 4: _MONEY_FMT, 5: _MONEY_FMT}
    if not run.checks:
        grid.data(("未提供校验项（未知，不得视为通过）",))
        return
    for check in run.checks:
        grid.data(
            (
                check.title or check.key,
                _status_text(check.status),
                check.expected,
                check.actual,
                check.difference,
                check.message,
            ),
            formats=formats,
        )


def build_workbook(bundle: DeferredExportBundle) -> Workbook:
    """生成三页签工作簿（项目参数 / 月度摊销 / 校验）。"""
    builders = (
        lambda grid: _project_sheet(grid, bundle),
        lambda grid: _period_sheet(grid, bundle),
        lambda grid: _checks_sheet(grid, bundle),
    )
    workbook = Workbook()
    for index, (title, builder) in enumerate(zip(SHEET_TITLES, builders)):
        ws = workbook.active if index == 0 else workbook.create_sheet(title)
        ws.title = title
        grid = _Grid(ws)
        builder(grid)
        _fit_widths(ws)
    return workbook


def download_names(bundle: DeferredExportBundle, stamp: str | None = None) -> tuple[str, str]:
    """返回（中文文件名, 纯 ASCII 兜底名）；中文名供 filename*，ASCII 名供 filename。"""
    stamp = stamp or datetime.now().strftime("%Y%m%d_%H%M%S")
    code = str(bundle.item.project_code or "未编号")
    run_id = str(bundle.run.id)
    cn_code = re.sub(r'[\\/:*?"<>|\r\n\t]', "", code).strip()[:40] or "未编号"
    ascii_code = re.sub(r"[^A-Za-z0-9._-]+", "", code).strip("._-")[:40] or "item"
    return (
        f"长期待摊_{cn_code}_{run_id}_{stamp}.xlsx",
        f"deferred_{ascii_code}_{run_id}_{stamp}.xlsx",
    )


def export(bundle: DeferredExportBundle) -> DeferredExportFile:
    """返回文件名与工作簿字节；调用方负责下载响应头。"""
    buffer = io.BytesIO()
    build_workbook(bundle).save(buffer)
    cn_name, ascii_name = download_names(bundle)
    return DeferredExportFile(
        filename=cn_name, filename_ascii=ascii_name, content=buffer.getvalue()
    )
