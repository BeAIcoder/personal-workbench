# -*- coding: utf-8 -*-
"""工会经费计提 · xlsx 导出（批次信息 / 部门明细 / 校验 三页签）。

唯一数据源是调用方传入的 payload（服务层从批次 + 实时计算组装），本文件
**不读业务库、不重算金额**：金额按两位小数直写 ``Decimal``，费率按 6 位小数
百分比展示；缺失值写空单元格而不是 0；未提供校验结果时写「未知」，绝不显示通过。

payload 键名即契约::

    batch:       id, entity, period, rate, status, status_label, remark,
                 confirmed_at, created_at
    lines:       department, current_wage_base, prior_accrual, prior_actual,
                 line_status, line_status_label, current_accrued,
                 prior_variance, current_book_amount, missing_fields, note
    totals:      current_wage_base, current_accrued, prior_accrual,
                 prior_actual, prior_variance, current_book_amount
    checks:      None 或 {status: pass|fail|unknown, items: [...], counts: {...}}
    exported_at / rule_version / all_passed / has_unknown

所有字符串单元格经 ``app.utils_xlsx.safe_cell`` 防公式注入；金额与日期以原生
单元格类型写入，产出可用 openpyxl 重新读取。
"""
from __future__ import annotations

import io
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Mapping, Sequence

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from app.utils_xlsx import safe_cell

SHEET_TITLES: tuple[str, ...] = ("批次信息", "部门明细", "校验")

_CENT = Decimal("0.01")
_MONEY_FMT = "#,##0.00"
_RATE_FMT = "0.0000%"
_ILLEGAL_XML = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_HEADER_FILL = PatternFill("solid", fgColor="DCE6F1")
_CHECK_LABELS = {"pass": "通过", "fail": "未通过", "unknown": "未知"}
_LINE_LABELS = {"ok": "可计算", "unknown": "缺数据"}
_STATUS_LABELS = {"draft": "草稿", "confirmed": "已确认"}

BATCH_SCOPE_NOTE = (
    "口径说明：工资基数手工录入，不从 HR 取数；不含 60/40 分成、缴纳凭据和税前扣除判断；"
    "本表不构成支付指令，也不回写 BIP。"
)
_DETAIL_HEADERS = (
    "部门", "本期工资基数", "上期已计提", "上期实际数",
    "本期计提", "上期差异（实际−已计提）", "本期建议入账", "行状态", "说明",
)
_CHECK_HEADERS = ("检查项", "结论", "期望", "实际", "差异", "说明")


# ---------------- 取值与校验 ----------------

def _safe_text(value: Any) -> str:
    return safe_cell(_ILLEGAL_XML.sub("", str(value)))


def _cell_value(value: Any) -> Any:
    return _safe_text(value) if isinstance(value, str) else value


def _decimal(value: Any, label: str) -> Decimal | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, bool):
        raise ValueError(f"导出字段「{label}」不是数字：{value!r}")
    try:
        if isinstance(value, Decimal):
            number = value
        elif isinstance(value, int):
            number = Decimal(value)
        elif isinstance(value, float):
            number = Decimal(str(value))
        else:
            number = Decimal(str(value).strip().replace(",", "").replace("，", ""))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"导出字段「{label}」不是数字：{value!r}") from exc
    if not number.is_finite():
        raise ValueError(f"导出字段「{label}」不是有限数字：{value!r}")
    return number


def _money(value: Any, label: str) -> Decimal | None:
    number = _decimal(value, label)
    return None if number is None else number.quantize(_CENT, rounding=ROUND_HALF_UP)


def _check_status(value: Any) -> str:
    status = str(value or "").strip().lower()
    if not status:
        return "unknown"
    if status not in _CHECK_LABELS:
        raise ValueError(f"校验结论必须是 pass / fail / unknown：{value!r}")
    return status


def _num_or_text(value: Any) -> Any:
    if value is None or isinstance(value, bool):
        return None if value is None else ("是" if value else "否")
    if isinstance(value, (int, float, Decimal)):
        return _decimal(value, "check")
    text = str(value).strip()
    try:
        return Decimal(text.replace(",", ""))
    except InvalidOperation:
        return _safe_text(text)


def _section(payload: Mapping[str, Any], key: str, required: bool = False) -> Mapping[str, Any]:
    value = payload.get(key)
    if value is None:
        if required:
            raise ValueError(f"导出数据缺少「{key}」，请按契约组装 payload")
        return {}
    if not isinstance(value, Mapping):
        raise ValueError(f"导出数据「{key}」必须是字典")
    return value


def _items(payload: Mapping[str, Any], key: str, required: bool = False) -> list[Mapping[str, Any]]:
    value = payload.get(key)
    if value is None:
        if required:
            raise ValueError(f"导出数据缺少「{key}」，请按契约组装 payload")
        return []
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"导出数据「{key}」必须是列表")
    rows = list(value)
    for row in rows:
        if not isinstance(row, Mapping):
            raise ValueError(f"导出数据「{key}」的每一项必须是字典")
    return rows


# ---------------- 写表助手 ----------------

class _Grid:
    """统一 safe_cell（字符串）、Decimal/date 直写，并跟踪当前行号。"""

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

    def header(self, labels: Sequence[str]) -> int:
        self.row += 1
        for column, text in enumerate(labels, start=1):
            cell = self.ws.cell(row=self.row, column=column, value=_safe_text(text))
            cell.font, cell.fill = Font(bold=True), _HEADER_FILL
            cell.alignment = Alignment(vertical="center", wrap_text=True)
        return self.row

    def data(self, values: Sequence[Any], formats: Mapping[int, str] | None = None) -> None:
        self.row += 1
        for column, value in enumerate(values, start=1):
            cell = self.ws.cell(row=self.row, column=column, value=_cell_value(value))
            fmt = (formats or {}).get(column)
            if fmt:
                cell.number_format = fmt

    def blank(self) -> None:
        self.row += 1


def _fit_widths(ws, min_width: int = 8, max_width: int = 46) -> None:
    widths: dict[int, int] = {}
    for row in ws.iter_rows():
        for cell in row:
            if cell.value is None:
                continue
            length = sum(2 if ord(ch) > 127 else 1 for ch in str(cell.value))
            widths[cell.column] = max(widths.get(cell.column, 0), length)
    for column, width in widths.items():
        ws.column_dimensions[get_column_letter(column)].width = max(min_width, min(max_width, width + 2))


# ---------------- 三页签 ----------------

def _batch_sheet(grid: _Grid, batch: Mapping[str, Any], payload: Mapping[str, Any]) -> None:
    status = str(batch.get("status") or "").strip().lower()
    label = batch.get("status_label") or _STATUS_LABELS.get(status) or "状态未知"
    entity = batch.get("entity") or "（未填主体）"
    period = batch.get("period") or "（未填期间）"
    grid.title(f"工会经费计提 · {entity} · {period} · {label}")
    grid.note(BATCH_SCOPE_NOTE)
    grid.blank()
    grid.section("批次参数")
    grid.kv("计提主体", entity)
    grid.kv("所属期间", period)
    rate = _decimal(batch.get("rate"), "rate")
    grid.kv("计提率", rate if rate is not None else "未提供", _RATE_FMT)
    grid.kv("批次状态", label)
    grid.kv("规则版本", payload.get("rule_version") or "未提供")
    grid.kv("导出时间", payload.get("exported_at") or "未提供")
    grid.kv("确认时间", batch.get("confirmed_at") or "未确认")
    if batch.get("remark"):
        grid.kv("备注", batch.get("remark"))

    totals = _section(payload, "totals")
    grid.blank()
    grid.section("汇总（只含可计算行；缺数据行不按 0 计入）")
    for label_text, key in (
        ("本期工资基数合计", "current_wage_base"),
        ("本期应计提", "current_accrued"),
        ("上期已计提合计", "prior_accrual"),
        ("上期实际数合计", "prior_actual"),
        ("上期差异合计（实际−已计提）", "prior_variance"),
        ("本期建议入账合计", "current_book_amount"),
    ):
        value = _money(totals.get(key), key)
        grid.kv(label_text, value if value is not None else "未提供", _MONEY_FMT)


def _detail_sheet(grid: _Grid, batch: Mapping[str, Any], lines: list[Mapping[str, Any]]) -> None:
    grid.title(f"部门明细 · {batch.get('entity') or '（未填主体）'} · {batch.get('period') or '（未填期间）'}")
    grid.note("一行 = 一个部门；本期计提 = ROUND(工资基数 × 批次费率, 2)，"
              "上期差异 = 上期实际 − 上期已计提（负数即冲回），本期建议入账 = 本期计提 + 上期差异。")
    grid.header(_DETAIL_HEADERS)
    formats = {2: _MONEY_FMT, 3: _MONEY_FMT, 4: _MONEY_FMT, 5: _MONEY_FMT, 6: _MONEY_FMT, 7: _MONEY_FMT}
    if not lines:
        grid.data(("本批次没有部门行",))
        return
    for item in lines:
        status = str(item.get("line_status") or "").strip().lower() or "unknown"
        if status not in _LINE_LABELS:
            raise ValueError(f"行状态必须是 ok / unknown：{item.get('line_status')!r}")
        grid.data(
            (
                item.get("department"),
                _money(item.get("current_wage_base"), "current_wage_base"),
                _money(item.get("prior_accrual"), "prior_accrual"),
                _money(item.get("prior_actual"), "prior_actual"),
                _money(item.get("current_accrued"), "current_accrued"),
                _money(item.get("prior_variance"), "prior_variance"),
                _money(item.get("current_book_amount"), "current_book_amount"),
                item.get("line_status_label") or _LINE_LABELS[status],
                item.get("note") or "",
            ),
            formats=formats,
        )


def _checks_sheet(grid: _Grid, batch: Mapping[str, Any], payload: Mapping[str, Any]) -> None:
    grid.title(f"校验 · {batch.get('entity') or '（未填主体）'} · {batch.get('period') or '（未填期间）'}")
    checks = payload.get("checks")
    if checks is None:
        grid.title("校验结果 · 未知")
        grid.note("未提供校验结果（unknown）：不得视为通过，请先补数或重新计算。")
        return
    if not isinstance(checks, Mapping):
        raise ValueError("导出数据「checks」必须是字典或 None")
    status = _check_status(checks.get("status"))
    grid.kv("总结论", _CHECK_LABELS[status])
    items = checks.get("items") or []
    if not isinstance(items, (list, tuple)):
        raise ValueError("导出数据「checks.items」必须是列表")
    if items:
        unknown = sum(1 for item in items if _check_status(item.get("status")) == "unknown")
        failed = sum(1 for item in items if _check_status(item.get("status")) == "fail")
        grid.kv("检查项统计", f"未通过 {failed} / 未知 {unknown} / 共 {len(items)}")
    grid.blank()
    grid.header(_CHECK_HEADERS)
    formats = {3: _MONEY_FMT, 4: _MONEY_FMT, 5: _MONEY_FMT}
    if not items:
        grid.data(("未提供校验项（unknown，不得视为通过）",))
        return
    for item in items:
        if not isinstance(item, Mapping):
            raise ValueError("导出数据「checks.items」的每一项必须是字典")
        grid.data(
            (
                item.get("title") or item.get("code") or "未命名检查项",
                _CHECK_LABELS[_check_status(item.get("status"))],
                _num_or_text(item.get("left")),
                _num_or_text(item.get("right")),
                _num_or_text(item.get("diff")),
                item.get("message") or "",
            ),
            formats=formats,
        )


# ---------------- 对外入口 ----------------

def build_workbook(payload: Mapping[str, Any]) -> Workbook:
    """按契约 payload 生成三页签工作簿；结构缺失抛 ValueError（中文原因）。"""
    if not isinstance(payload, Mapping):
        raise ValueError("导出数据必须是字典")
    batch = _section(payload, "batch", required=True)
    lines = _items(payload, "lines")
    workbook = Workbook()
    builders = (
        lambda grid: _batch_sheet(grid, batch, payload),
        lambda grid: _detail_sheet(grid, batch, lines),
        lambda grid: _checks_sheet(grid, batch, payload),
    )
    for index, (title, builder) in enumerate(zip(SHEET_TITLES, builders)):
        ws = workbook.active if index == 0 else workbook.create_sheet(title)
        ws.title = title
        grid = _Grid(ws)
        builder(grid)
        _fit_widths(ws)
    return workbook


def export_union_workbook(payload: Mapping[str, Any]) -> bytes:
    """返回工作簿字节；调用方负责下载响应头。"""
    buffer = io.BytesIO()
    build_workbook(payload).save(buffer)
    return buffer.getvalue()


def cn_period_cn(period: str) -> str:
    """期间在中文文件名里保持 ``YYYY-MM``（去掉路径非法字符），空值写占位。"""
    cleaned = re.sub(r'[\\/:*?"<>|\r\n\t]', "", str(period or "")).strip()
    return cleaned[:7] or "未填期间"


def download_names(payload: Mapping[str, Any], stamp: str | None = None) -> tuple[str, str]:
    """返回（中文文件名, 纯 ASCII 兜底名）；中文名供 filename*，ASCII 名供 filename。"""
    batch = payload.get("batch") or {}
    exported = payload.get("exported_at")
    if isinstance(exported, datetime):
        stamp = stamp or exported.strftime("%Y%m%d_%H%M%S")
    if isinstance(exported, date) and not stamp:
        stamp = exported.strftime("%Y%m%d")
    stamp = stamp or datetime.now().strftime("%Y%m%d_%H%M%S")
    entity = str(batch.get("entity") or "未填主体")
    period = str(batch.get("period") or "未填期间")
    cn_entity = re.sub(r'[\\/:*?"<>|\r\n\t]', "", entity).strip()[:40] or "未填主体"
    ascii_entity = re.sub(r"[^A-Za-z0-9._-]+", "", entity).strip("._-")[:40] or "entity"
    ascii_period = re.sub(r"[^0-9-]+", "", period)[:7] or "period"
    return (
        f"工会经费计提_{cn_entity}_{cn_period_cn(period)}_{stamp}.xlsx",
        f"union_{ascii_entity}_{ascii_period}_{stamp}.xlsx",
    )
