# -*- coding: utf-8 -*-
"""租赁核算 · 测算底稿 xlsx 导出（数据源 / 评估表 / 期间表 / 校验 / 分录）。

唯一数据源是调用方传入的 payload（后端从测算快照组装），本文件**不读业务库、
不重算金额**：金额按两位小数直写 ``Decimal``，期间利率按 4 位、折现因子按 9 位
原样写入；缺失值写空单元格而不是 0；未提供校验结果时写「未知」，绝不显示通过。

payload 键名即契约::

    contract:    contract_no, lessee_name, project_name, lessor_name,
                 start_date, end_date, annual_rate, rate_basis, note, currency
    calculation: id, status, status_label, calculated_at, rule_version,
                 input_hash, stale, stale_reason
    totals:      asset_total, liability_total, payment_total,
                 unrecognized_finance_cost            # 可选；缺省时汇总写「未提供」
    payments:    sequence, serial_no, project_code, contract_no, lessor_code,
                 contract_type, handover_date, start_date, end_date, amount,
                 currency, invoice_required, opening_flag, opening_payment_flag, note
    assessments: sequence, months, amount, annual_rate, period_rate, discount_factor,
                 present_value, interest_total, interest, liability_balance, source_row
    periods:     period, months（按月展开，恒为 1）, depreciation, payment_due,
                 interest, asset_balance, liability_balance, sequence
    checks:      None 或 {status: pass|fail|unknown, items: [...], counts: {...}}；
                 item 字段：code, title, status, left, right, diff, tolerance, message
    entries:     number, date, summary, account, debit, credit, source
    entries_supported / entries_note：可选文本，覆盖默认支持范围说明

字段 ⇄ 表头 ⇄ 取值列一一对应（``PAYMENT_HEADERS`` / ``_ASSESSMENT_HEADERS`` /
``_PERIOD_HEADERS`` 即唯一列定义，改列名必须同步服务 payload 与本文件读取）：

- 评估表「当期利息」读 ``assessments[].interest``，「利息（非实际当期）」读
  ``interest_total``，「租赁负债余额」读 ``liability_balance``；
- 期间表「使用权资产净值」读 ``periods[].asset_balance``；期间表按「月」展开，
  每月一行，其 ``months`` 恒为 1，表头写「本行月数（每月=1）」，**不是**源底稿
  期间表 R 列的月期总数（评估表的 ``months`` 才是每期月数）。

所有字符串单元格经 ``app.utils_xlsx.safe_cell``（先清 XML 非法控制字符）防公式
注入；金额与日期以原生单元格类型写入，产出可用 openpyxl 重新读取。
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

SHEET_TITLES: tuple[str, ...] = ("数据源", "评估表", "期间表", "校验", "分录")

_CENT = Decimal("0.01")
_MONEY_FMT = "#,##0.00"
_DATE_FMT = "yyyy-mm-dd"
_RATE_FMT = "0.0000"
_PERCENT_FMT = "0.0000%"
_FACTOR_FMT = "0.000000000"
_ILLEGAL_XML = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_HEADER_FILL = PatternFill("solid", fgColor="DCE6F1")
_CHECK_LABELS = {"pass": "通过", "fail": "未通过", "unknown": "未知"}

# 数据源列顺序与 importer.EXPORT_HEADERS 一致，保证导出文件可被原样再导入。
PAYMENT_HEADERS: tuple[tuple[str, str], ...] = (
    ("sequence", "支付期数"),
    ("serial_no", "序列号"),
    ("project_code", "对象"),
    ("contract_no", "合同编号"),
    ("lessor_code", "出租方编号"),
    ("contract_type", "合同类型"),
    ("handover_date", "交房日期"),
    ("start_date", "计租起始日期"),
    ("end_date", "计租结束日期"),
    ("amount", "金额（不含税）"),
    ("currency", "货币"),
    ("invoice_required", "是否提供发票"),
    ("opening_flag", "期初标识"),
    ("opening_payment_flag", "期初支付标识"),
    ("note", "备注"),
)

_ASSESSMENT_HEADERS = (
    "期次", "月期数", "金额（不含税）", "年利率", "期间利率", "累计折现因子",
    "现值", "利息（非实际当期）", "当期利息", "租赁负债余额", "源行号",
)
_PERIOD_HEADERS = ("月份", "本行月数（每月=1）", "月折旧", "应付", "月利息",
                    "使用权资产净值", "租赁负债余额", "对应期次")
_CHECK_HEADERS = ("检查项", "结论", "左值", "右值", "差异", "容差", "说明")
_ENTRY_HEADERS = ("凭证号", "日期", "摘要", "科目", "借方金额", "贷方金额", "来源/定位")

_DEFAULT_SUPPORTED_ENTRIES = (
    "初始确认：按本模块简化口径，使用权资产 = 租赁负债现值（不含预付租金、初始直接费用、复原成本）",
    "月度折旧：使用权资产原值在租赁期内直线摊销，残值 0",
    "月度利息：评估表当期利息在付款期内均摊",
    "租赁付款：按付款期应付额冲减租赁负债",
    "不支持：租金变更、主体变更、终止、转销及自动调整分录（另行评审后再扩展）",
)


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


def _integer(value: Any, label: str) -> int | None:
    number = _decimal(value, label)
    if number is None:
        return None
    if number != number.to_integral_value():
        raise ValueError(f"导出字段「{label}」必须是整数：{value!r}")
    return int(number)


def _as_date(value: Any, label: str) -> date | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value).strip()[:10])
    except ValueError as exc:
        raise ValueError(f"导出字段「{label}」不是 YYYY-MM-DD 日期：{value!r}") from exc


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


def _check_status(value: Any) -> str:
    status = str(value or "").strip().lower()
    if not status:
        return "unknown"
    if status not in _CHECK_LABELS:
        raise ValueError(f"校验结论必须是 pass / fail / unknown：{value!r}")
    return status


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
        self.ws.cell(row=self.row, column=1, value=_safe_text(text)).font = Font(italic=True, color="595959")

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


# ---------------- 公共页头 ----------------

def _header_block(grid: _Grid, contract: Mapping[str, Any], calculation: Mapping[str, Any], subject: str) -> None:
    stale = bool(calculation.get("stale"))
    status = calculation.get("status_label") or calculation.get("status") or "状态未知"
    prefix = "【已过期】" if stale else ""
    grid.title(
        f"{prefix}{subject} · 合同 {contract.get('contract_no') or '（未编号）'}"
        f" · 测算 #{calculation.get('id') if calculation.get('id') is not None else '—'} · {status}"
    )
    grid.kv("承租人主体", contract.get("lessee_name") or "未填")
    grid.kv("项目/资产组", contract.get("project_name") or "未填")
    grid.kv("出租方", contract.get("lessor_name") or "未填")
    start = _as_date(contract.get("start_date"), "start_date")
    end = _as_date(contract.get("end_date"), "end_date")
    grid.kv("租赁期", f"{start or '—'} 至 {end or '—'}")
    rate = _decimal(contract.get("annual_rate"), "annual_rate")
    if rate is not None:
        grid.kv("年利率", rate, _PERCENT_FMT)
    grid.kv("利率依据", contract.get("rate_basis") or "未填")
    grid.kv(
        "规则版本 / 输入哈希",
        f"{calculation.get('rule_version') or '未提供'} / {calculation.get('input_hash') or '未提供'}",
    )
    grid.kv("测算时间", calculation.get("calculated_at") or "未提供")
    if contract.get("note"):
        grid.kv("合同备注", contract.get("note"))
    if stale:
        reason = calculation.get("stale_reason") or "测算输入在生成后被修改"
        grid.note(f"本批次结果已过期：{reason}。仅供追溯查看，不得作为当前结果使用。")


# ---------------- 五类工作表 ----------------

def _data_sheet(grid: _Grid, payload: Mapping[str, Any], contract, calculation, payments) -> None:
    _header_block(grid, contract, calculation, "数据源")
    grid.note("一行 = 一个付款期；金额为不含税应付额；免租期占行填 0；本页可直接用于付款计划再导入。")
    grid.header([label for _, label in PAYMENT_HEADERS])
    formats = {7: _DATE_FMT, 8: _DATE_FMT, 9: _DATE_FMT, 10: _MONEY_FMT}
    for item in payments:
        grid.data(
            (
                _integer(item.get("sequence"), "sequence"),
                item.get("serial_no"),
                item.get("project_code"),
                item.get("contract_no") or contract.get("contract_no"),
                item.get("lessor_code"),
                item.get("contract_type"),
                _as_date(item.get("handover_date"), "handover_date"),
                _as_date(item.get("start_date"), "start_date"),
                _as_date(item.get("end_date"), "end_date"),
                _money(item.get("amount"), "amount"),
                item.get("currency") or contract.get("currency"),
                None if item.get("invoice_required") is None else ("是" if item.get("invoice_required") else "否"),
                "X" if item.get("opening_flag") else None,
                "X" if item.get("opening_payment_flag") else None,
                item.get("note"),
            ),
            formats=formats,
        )


def _totals_block(grid: _Grid, payload: Mapping[str, Any]) -> None:
    totals = _section(payload, "totals")
    grid.section("汇总（来自本次测算快照，本页不重算）")
    for label, key in (
        ("使用权资产原值", "asset_total"),
        ("租赁负债现值", "liability_total"),
        ("租赁付款额（不含税）", "payment_total"),
        ("未确认融资费用", "unrecognized_finance_cost"),
    ):
        value = _money(totals.get(key), key)
        grid.kv(label, value if value is not None else "未提供", _MONEY_FMT)


def _assessment_sheet(grid: _Grid, payload, contract, calculation, assessments) -> None:
    _header_block(grid, contract, calculation, "评估表")
    _totals_block(grid, payload)
    grid.blank()
    grid.section("逐期现值与利息（首行不折现；其余按上期累计折现因子折现）")
    grid.header(_ASSESSMENT_HEADERS)
    formats = {
        2: "0", 3: _MONEY_FMT, 4: _PERCENT_FMT, 5: _RATE_FMT, 6: _FACTOR_FMT,
        7: _MONEY_FMT, 8: _MONEY_FMT, 9: _MONEY_FMT, 10: _MONEY_FMT, 11: "0",
    }
    if not assessments:
        grid.data(("无评估行（本次测算未提供）",))
        return
    for item in assessments:
        grid.data(
            (
                _integer(item.get("sequence"), "sequence"),
                _integer(item.get("months"), "months"),
                _money(item.get("amount"), "amount"),
                _decimal(item.get("annual_rate"), "annual_rate"),
                _decimal(item.get("period_rate"), "period_rate"),
                _decimal(item.get("discount_factor"), "discount_factor"),
                _money(item.get("present_value"), "present_value"),
                _money(item.get("interest_total"), "interest_total"),
                _money(item.get("interest"), "interest"),
                _money(item.get("liability_balance"), "liability_balance"),
                _integer(item.get("source_row"), "source_row"),
            ),
            formats=formats,
        )


def _period_sheet(grid: _Grid, contract, calculation, periods) -> None:
    _header_block(grid, contract, calculation, "期间表")
    grid.note("应付落在付款前一月；月利息 = 当期利息 ÷ 期内月数；本表按月展开，本行月数恒为 1"
              "（不是源底稿月期总数）；数值来自测算快照，不做重算。")
    grid.header(_PERIOD_HEADERS)
    formats = {2: "0", 3: _MONEY_FMT, 4: _MONEY_FMT, 5: _MONEY_FMT, 6: _MONEY_FMT, 7: _MONEY_FMT, 8: "0"}
    if not periods:
        grid.data(("无期间行（本次测算未提供）",))
        return
    for item in periods:
        months = _integer(item.get("months"), "months")
        if months is not None and months != 1:
            raise ValueError(f"期间表按月展开，「本行月数」必须为 1：{item.get('months')!r}")
        grid.data(
            (
                item.get("period"),
                months,
                _money(item.get("depreciation"), "depreciation"),
                _money(item.get("payment_due"), "payment_due"),
                _money(item.get("interest"), "interest"),
                _money(item.get("asset_balance"), "asset_balance"),
                _money(item.get("liability_balance"), "liability_balance"),
                _integer(item.get("sequence"), "sequence"),
            ),
            formats=formats,
        )


def _checks_sheet(grid: _Grid, contract, calculation, payload) -> None:
    _header_block(grid, contract, calculation, "校验")
    checks = payload.get("checks")
    if checks is None:
        grid.title("校验结果 · 未知")
        grid.note("未提供校验结果（unknown）：不得视为通过，请先完成校验或补数后重算。")
        return
    if not isinstance(checks, Mapping):
        raise ValueError("导出数据「checks」必须是字典或 None")
    status = _check_status(checks.get("status"))
    grid.title(f"校验结果 · {_CHECK_LABELS[status]}")
    grid.kv("总结论", _CHECK_LABELS[status])
    items = checks.get("items") or []
    if not isinstance(items, (list, tuple)):
        raise ValueError("导出数据「checks.items」必须是列表")
    counts = checks.get("counts")
    resolved: dict[str, int] = {}
    if isinstance(counts, Mapping):
        for key, aliases in (("pass", ("pass", "ok")), ("fail", ("fail", "failed")),
                             ("unknown", ("unknown",))):
            value = next((counts[a] for a in aliases if a in counts), None)
            if value is not None:
                resolved[key] = int(value)
    if len(resolved) != len(_CHECK_LABELS):
        resolved = {
            key: sum(1 for item in items if str(item.get("status", "")).strip().lower() == key)
            for key in _CHECK_LABELS
        }
    grid.kv("检查项统计（本页）", "通过 {pass} / 未通过 {fail} / 未知 {unknown}".format(**resolved))
    grid.header(_CHECK_HEADERS)
    formats = {3: _MONEY_FMT, 4: _MONEY_FMT, 5: _MONEY_FMT, 6: _MONEY_FMT}
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
                _num_or_text(item.get("tolerance")),
                item.get("message") or "",
            ),
            formats=formats,
        )


def _entries_sheet(grid: _Grid, calculation, payload) -> None:
    _header_block(grid, payload.get("contract") or {}, calculation, "分录")
    grid.note("逐笔底稿分录根据已保存的测算快照生成，未过账/不回写 BIP；空 = 本批次无分录。")
    grid.section("本模块支持的分录类型（仅说明金额来源，不构成科目映射）")
    supported = payload.get("entries_supported") or _DEFAULT_SUPPORTED_ENTRIES
    for text in supported:
        grid.data((text,))
    grid.blank()
    grid.section("根据测算快照生成的逐笔底稿分录")
    if payload.get("entries_note"):
        grid.note(str(payload.get("entries_note")))
    entries = _items(payload, "entries")
    grid.header(_ENTRY_HEADERS)
    if not entries:
        grid.data(("本批次无逐笔底稿分录",))
        return
    formats = {2: _DATE_FMT, 5: _MONEY_FMT, 6: _MONEY_FMT}
    for item in entries:
        grid.data(
            (
                item.get("number"),
                _as_date(item.get("date"), "entries.date"),
                item.get("summary"),
                item.get("account"),
                _money(item.get("debit"), "entries.debit"),
                _money(item.get("credit"), "entries.credit"),
                item.get("source"),
            ),
            formats=formats,
        )


# ---------------- 对外入口 ----------------

def build_workbook(payload: Mapping[str, Any]) -> Workbook:
    """按契约 payload 生成五页签工作簿；结构缺失抛 ValueError（中文原因）。"""
    if not isinstance(payload, Mapping):
        raise ValueError("导出数据必须是字典")
    contract = _section(payload, "contract", required=True)
    calculation = _section(payload, "calculation", required=True)
    payments = _items(payload, "payments", required=True)
    if not payments:
        raise ValueError("付款计划为空，不能导出底稿")
    assessments = _items(payload, "assessments")
    periods = _items(payload, "periods")
    builders = (
        lambda grid: _data_sheet(grid, payload, contract, calculation, payments),
        lambda grid: _assessment_sheet(grid, payload, contract, calculation, assessments),
        lambda grid: _period_sheet(grid, contract, calculation, periods),
        lambda grid: _checks_sheet(grid, contract, calculation, payload),
        lambda grid: _entries_sheet(grid, calculation, payload),
    )
    workbook = Workbook()
    for index, (title, builder) in enumerate(zip(SHEET_TITLES, builders)):
        ws = workbook.active if index == 0 else workbook.create_sheet(title)
        ws.title = title
        grid = _Grid(ws)
        builder(grid)
        _fit_widths(ws)
    return workbook


def export_calculation_workbook(payload: Mapping[str, Any]) -> bytes:
    """返回工作簿字节；调用方负责下载响应头。"""
    buffer = io.BytesIO()
    build_workbook(payload).save(buffer)
    return buffer.getvalue()


def download_names(payload: Mapping[str, Any], stamp: str | None = None) -> tuple[str, str]:
    """返回（中文文件名, 纯 ASCII 兜底名）；中文名供 filename*，ASCII 名供 filename。"""
    contract = payload.get("contract") or {}
    calculation = payload.get("calculation") or {}
    stamp = stamp or datetime.now().strftime("%Y%m%d_%H%M%S")
    number = str(contract.get("contract_no") or "未编号")
    calc_id = str(calculation.get("id") if calculation.get("id") is not None else "draft")
    cn_number = re.sub(r'[\\/:*?"<>|\r\n\t]', "", number).strip()[:40] or "未编号"
    ascii_number = re.sub(r"[^A-Za-z0-9._-]+", "", number).strip("._-")[:40] or "contract"
    ascii_id = re.sub(r"[^A-Za-z0-9._-]+", "", calc_id) or "draft"
    return (
        f"租赁核算_{cn_number}_{ascii_id}_{stamp}.xlsx",
        f"lease_{ascii_number}_{ascii_id}_{stamp}.xlsx",
    )
