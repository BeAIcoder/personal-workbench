# -*- coding: utf-8 -*-
"""租赁核算 · 付款计划 xlsx 导入解析（预览与确认共用同一份解析）。

解析口径：一律按**表头标签文本锚点**定位，不锁工作表顺序、不锁行号；表头行在前
``MAX_HEADER_SCAN_ROWS`` 行内扫描，必须命中「支付期数 / 计租起始日期 / 计租结束日期 /
金额（不含税）」四个核心标签（同义写法见 ``_FIELD_ALIASES``）。一行 = 一个付款期，
只跳过**整行空白**：免租期（金额 0）与 0 月期（起止同日）原样保留；遇到合计行或
只填说明文字的尾部行即停止解析并记 warning。无法确定的取值一律记 error 且
``valid=False``——不猜金额、不补 0、不自动改期次、不静默四舍五入金额。

``ValueError`` 表示文件本身不可识别（非 xlsx、超限、找不到表头、表头列重复），路由
映射 400；``ScheduleImport.valid is False`` 表示行级数据有问题，apply 必须拒绝落库。
本模块只解析输入，不落库、不调计算引擎。
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

import openpyxl
from app.utils_xlsx import safe_cell
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_ROWS = 20_000
MAX_HEADER_SCAN_ROWS = 50
MAX_BLANK_STREAK = 20

TEMPLATE_FILENAME = "租赁核算-付款计划-模板.xlsx"
DATA_SHEET_NAME = "数据源"
GUIDE_SHEET_NAME = "填写说明"

_CENT = Decimal("0.01")

# 导出与模板共用的列顺序（字段名, 表头文本）；导入按表头文本反查字段。
EXPORT_HEADERS: tuple[tuple[str, str], ...] = (
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

# 同义表头；``_ALIAS_TO_FIELD`` 由本表生成，别名不允许跨字段重复（见 _build_alias_index）。
_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "sequence": ("支付期数", "期次", "付款期次", "期数"),
    "serial_no": ("序列号", "合同内序号"),
    "project_code": ("对象", "项目/资产组", "资产组", "项目"),
    "contract_no": ("合同编号", "合同号", "租赁合同号"),
    "lessor_code": ("出租方编号", "出租方代码", "出租方"),
    "contract_type": ("合同类型", "租赁类型"),
    "handover_date": ("交房日期", "交房日", "交付日期"),
    "start_date": ("计租起始日期", "计租起始日", "起始日", "开始日期", "租赁开始日", "起租日"),
    "end_date": ("计租结束日期", "计租结束日", "结束日", "截止日", "租赁结束日", "到期日"),
    "amount": ("金额（不含税）", "金额(不含税)", "金额", "租金", "租金（不含税）", "付款额", "付款金额"),
    "currency": ("货币", "币种"),
    "invoice_required": ("是否提供发票", "提供发票", "是否开票"),
    "opening_flag": ("期初标识", "首期标识", "首期标记"),
    "opening_payment_flag": ("期初支付标识", "期初支付", "期初付款标识", "预付标识"),
    "note": ("备注", "说明"),
}

REQUIRED_FIELDS: frozenset[str] = frozenset({"sequence", "start_date", "end_date", "amount"})

_TOTAL_LABELS = frozenset({"合计", "总计", "小计"})
_TRUE_WORDS = frozenset({"x", "y", "yes", "true", "1", "是", "√", "✓", "首期", "期初"})
_FALSE_WORDS = frozenset({"", "n", "no", "false", "0", "-", "—", "/", "否", "无"})

_DATE_RE = re.compile(r"^(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})日?$")
_ILLEGAL_XML = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _raw_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _text(value: Any) -> str | None:
    """文本字段归一：去首尾空白，空串视为 None（不补默认值）。"""
    text = _raw_text(value).strip()
    return text or None


def _iso(value: date | None) -> str | None:
    return value.isoformat() if value else None


def _normalize_label(value: Any) -> str:
    text = _raw_text(value)
    if not text:
        return ""
    text = text.replace("（", "(").replace("）", ")").replace("：", ":")
    text = re.sub(r"\s+", "", text)
    return text.rstrip("*:").lower()


def _build_alias_index() -> dict[str, str]:
    index: dict[str, str] = {}
    for name, aliases in _FIELD_ALIASES.items():
        for alias in aliases:
            key = _normalize_label(alias)
            if not key:
                continue
            if key in index and index[key] != name:
                raise RuntimeError(f"付款计划表头别名冲突：{alias!r} 同时映射到 {index[key]} 与 {name}")
            index[key] = name
    return index


_ALIAS_TO_FIELD = _build_alias_index()


def _blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _decimal(value: Any) -> Decimal:
    if isinstance(value, bool):
        raise InvalidOperation("布尔值不是数字")
    if isinstance(value, Decimal):
        number = value
    elif isinstance(value, int):
        number = Decimal(value)
    elif isinstance(value, float):
        number = Decimal(str(value))
    else:
        number = Decimal(str(value).strip().replace(",", "").replace("，", ""))
    if not number.is_finite():
        raise InvalidOperation("不是有限数字")
    return number


def _parse_date(value: Any) -> tuple[date | None, str | None]:
    if _blank(value):
        return None, "日期为空"
    if isinstance(value, datetime):
        return value.date(), None
    if isinstance(value, date):
        return value, None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        number = float(value)
        if not 20000 <= number <= 80000:
            return None, f"日期序列号超出范围：{value!r}"
        return (datetime(1899, 12, 30) + timedelta(days=number)).date(), None
    text = _raw_text(value).strip()
    head = text.split(" ", 1)[0]
    match = _DATE_RE.match(head)
    if not match:
        return None, f"无法识别的日期：{text!r}（请用 YYYY-MM-DD）"
    year, month, day = (int(part) for part in match.groups())
    try:
        return date(year, month, day), None
    except ValueError:
        return None, f"日期不存在：{text!r}"


def _parse_amount(value: Any) -> tuple[Decimal | None, str | None]:
    if _blank(value):
        return None, "金额为空（免租期请填 0，不要留空）"
    if isinstance(value, str):
        text = value.strip().replace("¥", "").replace("￥", "").replace("$", "").replace(" ", "")
        if text.endswith("%"):
            return None, f"金额不能是百分比：{value!r}"
    else:
        text = value
    try:
        number = _decimal(text)
    except (InvalidOperation, ValueError):
        return None, f"金额不是数字：{value!r}"
    if number < 0:
        return None, f"金额不能为负：{value!r}（本模块不支持租赁激励/冲减行）"
    quantized = number.quantize(_CENT, rounding=ROUND_HALF_UP)
    if quantized != number:
        return None, f"金额超过 2 位小数：{_raw_text(value)}（请先按分核对，导入不做四舍五入）"
    return quantized, None


def _parse_sequence(value: Any) -> tuple[int | None, str | None]:
    if _blank(value):
        return None, "支付期数为空"
    try:
        number = _decimal(value)
    except (InvalidOperation, ValueError):
        return None, f"支付期数不是整数：{value!r}"
    if number != number.to_integral_value() or number < 1:
        return None, f"支付期数必须是 ≥1 的整数：{value!r}"
    return int(number), None


def _parse_flag(value: Any, label: str) -> tuple[bool, str | None]:
    text = _normalize_label(value)
    if text in _TRUE_WORDS:
        return True, None
    if text in _FALSE_WORDS:
        return False, None
    return False, f"{label}取值无法识别：{_raw_text(value)!r}（允许 X/是/√ 或 否/空）"


# ---------------- 数据结构 ----------------

@dataclass(frozen=True)
class Issue:
    """一行数据问题；``row`` 为源文件行号（1 起算），行级以下为 None。"""

    row: int | None
    field: str | None
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {"row": self.row, "field": self.field, "message": self.message}


@dataclass(frozen=True)
class PaymentRow:
    """付款期 DTO：``source_row`` 保留源文件行号，供问题定位与留痕。"""

    sequence: int | None
    start_date: date | None
    end_date: date | None
    amount: Decimal | None
    months: int | None = None
    serial_no: str | None = None
    project_code: str | None = None
    contract_no: str | None = None
    lessor_code: str | None = None
    contract_type: str | None = None
    handover_date: date | None = None
    currency: str | None = None
    invoice_required: bool = False
    opening_flag: bool = False
    opening_payment_flag: bool = False
    note: str | None = None
    source_row: int = 0
    issues: tuple[Issue, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            "start_date": _iso(self.start_date),
            "end_date": _iso(self.end_date),
            "amount": None if self.amount is None else f"{self.amount:.2f}",
            "months": self.months,
            "serial_no": self.serial_no,
            "project_code": self.project_code,
            "contract_no": self.contract_no,
            "lessor_code": self.lessor_code,
            "contract_type": self.contract_type,
            "handover_date": _iso(self.handover_date),
            "currency": self.currency,
            "invoice_required": self.invoice_required,
            "opening_flag": self.opening_flag,
            "opening_payment_flag": self.opening_payment_flag,
            "note": self.note,
            "source_row": self.source_row,
            "issues": [issue.to_dict() for issue in self.issues],
        }


@dataclass
class ScheduleImport:
    """一次解析的完整结果（预览直接序列化；apply 只在 ``valid`` 时落库）。"""

    filename: str
    sheet_name: str
    header_row: int
    rows: list[PaymentRow] = field(default_factory=list)
    errors: list[Issue] = field(default_factory=list)
    warnings: list[Issue] = field(default_factory=list)
    ignored_columns: list[str] = field(default_factory=list)
    stopped_at_row: int | None = None
    contract_no: str | None = None
    project_code: str | None = None
    lessor_code: str | None = None
    contract_type: str | None = None
    currency: str | None = None
    handover_date: date | None = None

    @property
    def valid(self) -> bool:
        return not self.errors

    @property
    def row_count(self) -> int:
        return len(self.rows)

    @property
    def total_amount(self) -> Decimal:
        total = sum((row.amount for row in self.rows if row.amount is not None), Decimal("0"))
        return total.quantize(_CENT, rounding=ROUND_HALF_UP)

    @property
    def zero_amount_periods(self) -> int:
        return sum(1 for row in self.rows if row.amount is not None and row.amount == 0)

    @property
    def zero_month_periods(self) -> int:
        return sum(
            1 for row in self.rows
            if row.start_date is not None and row.start_date == row.end_date
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "filename": self.filename, "sheet_name": self.sheet_name,
            "header_row": self.header_row, "valid": self.valid,
            "row_count": self.row_count,
            "total_amount": f"{self.total_amount:.2f}",
            "zero_amount_periods": self.zero_amount_periods,
            "zero_month_periods": self.zero_month_periods,
            "opening_payment_rows": [
                row.sequence for row in self.rows if row.opening_payment_flag
            ],
            "contract_no": self.contract_no,
            "project_code": self.project_code,
            "lessor_code": self.lessor_code,
            "contract_type": self.contract_type,
            "currency": self.currency,
            "handover_date": self.handover_date.isoformat() if self.handover_date else None,
            "stopped_at_row": self.stopped_at_row,
            "ignored_columns": list(self.ignored_columns), "rows": [row.to_dict() for row in self.rows],
            "errors": [i.to_dict() for i in self.errors], "warnings": [i.to_dict() for i in self.warnings],
            "error_count": len(self.errors),
            "warning_count": len(self.warnings),
        }


@dataclass
class _SheetScan:
    sheet_name: str
    header_row: int
    mapping: dict[str, int]
    rows: list[tuple[int, tuple[Any, ...]]]
    ignored_columns: list[str]
    stopped_at_row: int | None
    stop_reason: str | None = None


def _header_map(values: tuple[Any, ...]) -> tuple[dict[str, int], list[str], list[tuple[str, int]]]:
    mapping: dict[str, int] = {}
    ignored: list[str] = []
    duplicates: list[tuple[str, int]] = []
    for column, raw in enumerate(values, start=1):
        label = _normalize_label(raw)
        if not label:
            continue
        name = _ALIAS_TO_FIELD.get(label)
        if name is None:
            ignored.append(_raw_text(raw).strip())
        elif name in mapping:
            duplicates.append((_raw_text(raw).strip(), column))
        else:
            mapping[name] = column
    return mapping, ignored, duplicates


def _value_at(values: tuple[Any, ...], column: int | None) -> Any:
    return values[column - 1] if column and 0 <= column - 1 < len(values) else None


def _is_total_row(values: tuple[Any, ...], mapping: dict[str, int]) -> bool:
    """合计行：期次列以合计/总计/小计开头，或非备注列恰好等于这三个词。"""
    sequence_text = _normalize_label(_value_at(values, mapping.get("sequence")))
    if sequence_text and sequence_text.startswith(("合计", "总计", "小计")):
        return True
    for name, column in mapping.items():
        if name in {"note", "serial_no"}:
            continue
        text = _normalize_label(_value_at(values, column))
        if text in _TOTAL_LABELS:
            return True
    return False


def _footer_like_row(values: tuple[Any, ...], mapping: dict[str, int]) -> bool:
    """日期与金额全空，且期次为空或写成文字：按尾部说明行处理（不当作数据行）。"""
    core = ("start_date", "end_date", "amount")
    if any(not _blank(_value_at(values, mapping.get(name))) for name in core):
        return False
    sequence = _value_at(values, mapping.get("sequence"))
    if _blank(sequence):
        return any(not _blank(_value_at(values, column)) for column in mapping.values())
    text = _raw_text(sequence).strip().replace(",", "")
    return not re.fullmatch(r"\d+(\.0+)?", text)


def _scan_sheet(ws, sheet_name: str) -> _SheetScan | None:
    mapping: dict[str, int] = {}
    ignored: list[str] = []
    header_row = 0
    collected: list[tuple[int, tuple[Any, ...]]] = []
    stopped_at_row: int | None = None
    stop_reason: str | None = None
    blank_streak = 0
    for index, values in enumerate(ws.iter_rows(values_only=True), start=1):
        if not header_row:
            if index > MAX_HEADER_SCAN_ROWS:
                return None
            candidate, unmatched, duplicates = _header_map(tuple(values))
            if not REQUIRED_FIELDS <= set(candidate):
                continue
            if duplicates:
                labels = "、".join(f"第 {column} 列「{label}」" for label, column in duplicates)
                raise ValueError(f"工作表「{sheet_name}」表头重复：{labels} 与前文列映射到同一字段")
            mapping, ignored, header_row = candidate, unmatched, index
            continue
        if index - header_row > MAX_ROWS:
            raise ValueError(f"数据行超过 {MAX_ROWS} 行上限，请拆分文件后再导入")
        if all(_blank(_value_at(values, column)) for column in mapping.values()):
            blank_streak += 1
            if blank_streak >= MAX_BLANK_STREAK:
                stopped_at_row, stop_reason = index, "blank"
                break
            continue
        blank_streak = 0
        if _is_total_row(tuple(values), mapping):
            stopped_at_row, stop_reason = index, "total"
            break
        if _footer_like_row(tuple(values), mapping):
            stopped_at_row, stop_reason = index, "footer"
            break
        collected.append((index, tuple(values)))
    if not header_row:
        return None
    return _SheetScan(sheet_name, header_row, mapping, collected, ignored, stopped_at_row, stop_reason)


def _candidate_sheets(workbook):
    data_sheets, other_sheets = [], []
    for ws in workbook.worksheets:
        title = (ws.title or "").strip()
        if title in {GUIDE_SHEET_NAME, "说明", "使用说明"}:
            continue
        (data_sheets if title.startswith(DATA_SHEET_NAME) else other_sheets).append(ws)
    return data_sheets + other_sheets


def _parse_row(source_row: int, values: tuple[Any, ...], mapping: dict[str, int]) -> PaymentRow:
    issues: list[Issue] = []

    def cell(name: str) -> Any:
        return _value_at(values, mapping.get(name))

    sequence, error = _parse_sequence(cell("sequence"))
    if error:
        issues.append(Issue(source_row, "sequence", error))
    dates: dict[str, date | None] = {"handover_date": None}
    for name in ("start_date", "end_date"):
        dates[name], error = _parse_date(cell(name))
        if error:
            issues.append(Issue(source_row, name, error))
    if dates["start_date"] and dates["end_date"] and dates["end_date"] < dates["start_date"]:
        issues.append(Issue(
            source_row, "end_date",
            f"计租结束日期早于起始日期：{dates['end_date']} < {dates['start_date']}",
        ))
    if not _blank(cell("handover_date")):
        dates["handover_date"], error = _parse_date(cell("handover_date"))
        if error:
            issues.append(Issue(source_row, "handover_date", error))

    amount, error = _parse_amount(cell("amount"))
    if error:
        issues.append(Issue(source_row, "amount", error))

    months = None
    if dates["start_date"] and dates["end_date"] and dates["end_date"] >= dates["start_date"]:
        left, right = dates["start_date"] - timedelta(days=1), dates["end_date"] + timedelta(days=1)
        months = max(0, (right.year - left.year) * 12 + right.month - left.month - int(right.day < left.day))

    flags: dict[str, bool] = {}
    for name, label in (
        ("invoice_required", "是否提供发票"),
        ("opening_flag", "期初标识"),
        ("opening_payment_flag", "期初支付标识"),
    ):
        flags[name], error = _parse_flag(cell(name), label)
        if error:
            issues.append(Issue(source_row, name, error))

    return PaymentRow(
        sequence=sequence,
        start_date=dates["start_date"],
        end_date=dates["end_date"],
        amount=amount,
        months=months,
        serial_no=_text(cell("serial_no")),
        project_code=_text(cell("project_code")),
        contract_no=_text(cell("contract_no")),
        lessor_code=_text(cell("lessor_code")),
        contract_type=_text(cell("contract_type")),
        handover_date=dates["handover_date"],
        currency=_text(cell("currency")),
        invoice_required=flags["invoice_required"],
        opening_flag=flags["opening_flag"],
        opening_payment_flag=flags["opening_payment_flag"],
        note=_text(cell("note")),
        source_row=source_row,
        issues=tuple(issues),
    )


def _distinct(rows: list[PaymentRow], name: str) -> list[str]:
    values: list[str] = []
    for row in rows:
        value = getattr(row, name)
        text = None if value is None else (value.isoformat() if isinstance(value, date) else str(value))
        if text and text not in values:
            values.append(text)
    return values


def _first(rows: list[PaymentRow], name: str):
    for row in rows:
        value = getattr(row, name)
        if value is not None:
            return value
    return None


def _check_header_level(rows: list[PaymentRow], scan: _SheetScan, result: ScheduleImport) -> None:
    """合同级字段一致性：合同编号/对象多值属结构性错误，其余多值只告警。"""
    contract_nos = _distinct(rows, "contract_no")
    if len(contract_nos) > 1:
        result.errors.append(Issue(
            scan.header_row, "contract_no",
            "文件中包含多个合同编号（" + "、".join(contract_nos) + "）：付款计划按单份合同导入，请按合同拆分文件",
        ))
    project_codes = _distinct(rows, "project_code")
    if len(project_codes) > 1:
        result.errors.append(Issue(
            scan.header_row, "project_code",
            "文件中包含多个对象（项目/资产组）：" + "、".join(project_codes) + "；一份合同只允许一个对象",
        ))
    for name, label in (("lessor_code", "出租方编号"), ("contract_type", "合同类型"),
                        ("currency", "货币"), ("handover_date", "交房日期")):
        values = _distinct(rows, name)
        if len(values) > 1:
            result.warnings.append(Issue(
                scan.header_row, name,
                f"{label}列存在多个取值（" + "、".join(values) + f"），预览按「{values[0]}」展示，落库以合同主档为准"))
    if contract_nos:
        blank_rows = [row.source_row for row in rows if row.contract_no is None]
        if blank_rows:
            result.warnings.append(Issue(
                blank_rows[0], "contract_no",
                f"共 {len(blank_rows)} 行合同编号为空（首见第 {blank_rows[0]} 行），按当前合同处理"))
        result.contract_no = contract_nos[0]
    result.project_code = project_codes[0] if project_codes else None
    result.lessor_code = _first(rows, "lessor_code")
    result.contract_type = _first(rows, "contract_type")
    result.currency = _first(rows, "currency")
    result.handover_date = _first(rows, "handover_date")


def _check_sequences(rows: list[PaymentRow], result: ScheduleImport) -> None:
    sequences = [row.sequence for row in rows]
    if any(value is None for value in sequences):
        return
    seen: dict[int, int] = {}
    duplicated = False
    for row in rows:
        assert row.sequence is not None
        if row.sequence in seen:
            duplicated = True
            result.errors.append(Issue(
                row.source_row, "sequence",
                f"支付期数重复：{row.sequence}（另见第 {seen[row.sequence]} 行）",
            ))
        else:
            seen[row.sequence] = row.source_row
    if duplicated:
        return
    expected = list(range(1, len(rows) + 1))
    if sequences != expected:
        offset = next(i for i, (actual, want) in enumerate(zip(sequences, expected)) if actual != want)
        result.errors.append(Issue(
            rows[offset].source_row, "sequence",
            f"支付期数必须从 1 开始连续编号：第 {rows[offset].source_row} 行应为 {expected[offset]}，实际为 {sequences[offset]}",
        ))


def _check_flags(rows: list[PaymentRow], result: ScheduleImport) -> None:
    misplaced = [row.source_row for row in rows[1:] if row.opening_flag]
    if misplaced:
        result.warnings.append(Issue(
            misplaced[0], "opening_flag",
            f"期初标识只应出现在首期行：第 {misplaced[0]} 行等 {len(misplaced)} 行也填了 X",
        ))
    prepaid = [row for row in rows if row.opening_payment_flag]
    if len(prepaid) > 1:
        result.warnings.append(Issue(
            prepaid[1].source_row, "opening_payment_flag",
            "多行填写了期初支付标识：" + "、".join(str(row.sequence) for row in prepaid) + "；计算口径以首期为准",
        ))


def _build_import(scan: _SheetScan, filename: str) -> ScheduleImport:
    result = ScheduleImport(filename=filename, sheet_name=scan.sheet_name, header_row=scan.header_row)
    result.ignored_columns = scan.ignored_columns
    result.stopped_at_row = scan.stopped_at_row
    if scan.ignored_columns:
        result.warnings.append(Issue(
            None, None, "已忽略未识别列：" + "、".join(scan.ignored_columns),
        ))
    if scan.stopped_at_row:
        reason = {"blank": "之后连续多行为空", "total": "为合计行",
                  "footer": "核心字段为空、按尾部说明行处理"}.get(
                      scan.stop_reason or "", "之后的解析已停止")
        result.warnings.append(Issue(
            scan.stopped_at_row, None,
            f"第 {scan.stopped_at_row} 行{reason}，付款计划解析在此停止",
        ))

    for source_row, values in scan.rows:
        result.rows.append(_parse_row(source_row, values, scan.mapping))
    if not result.rows:
        result.errors.append(Issue(
            scan.header_row, None, f"表头（第 {scan.header_row} 行）之后没有付款计划数据行",
        ))
        return result

    for row in result.rows:
        result.errors.extend(row.issues)

    _check_sequences(result.rows, result)
    _check_header_level(result.rows, scan, result)
    _check_flags(result.rows, result)
    return result


def parse_payment_schedule(content: bytes, filename: str = "上传文件.xlsx") -> ScheduleImport:
    """解析付款计划 xlsx；preview 与 apply 必须调用同一函数。"""
    if not content:
        raise ValueError("文件为空，请选择 .xlsx 文件")
    if len(content) > MAX_UPLOAD_BYTES:
        raise ValueError(f"文件超过 {MAX_UPLOAD_BYTES // 1024 // 1024}MB 上限")
    try:
        workbook = openpyxl.load_workbook(io.BytesIO(content), data_only=True, read_only=True)
    except Exception as exc:  # noqa: BLE001 统一转中文，避免泄露底层异常
        raise ValueError("文件不是可读取的 xlsx（请用 Excel 另存为 .xlsx 后重试）") from exc
    try:
        checked: list[str] = []
        for ws in _candidate_sheets(workbook):
            scan = _scan_sheet(ws, ws.title)
            if scan is None:
                checked.append(ws.title)
                continue
            return _build_import(scan, filename)
        raise ValueError(
            "未找到付款计划表头：同一行需包含「支付期数 / 计租起始日期 / 计租结束日期 / 金额（不含税）」。"
            f"已检查工作表：{'、'.join(checked) or '（无）'}"
        )
    finally:
        workbook.close()


# ---------------- 合成模板 ----------------

_TEMPLATE_RULES = (
    "1. 一行 = 一个付款期；「支付期数」从 1 连续编号（重复/不连续会拒绝）；日期一律 YYYY-MM-DD，末期允许起止同日（0 月期）。",
    "2. 「金额（不含税）」必填：含税合同请先除以（1+税率）再填；免租期必须占一行并填 0，不能留空。",
    "3. 期初支付的首期单独一行，并在首期行「期初支付标识」填 X；「期初标识」只填首期。",
    "4. 「是否提供发票」「期初标识」「期初支付标识」可填 X/是/√ 或 否/空；其它取值会报错。",
    "5. 不要在同一份文件里混入多个合同编号或多个对象；导入按单份合同进行。",
    "6. 不要在表头后追加自定义合计行；如需合计请另行计算，合计行会让解析提前停止。",
    "7. 年利率、折现率、承租人主体等合同信息在合同页面维护，不在本表填写。",
    "8. 示例行标注「示例-请替换」，下载后请替换或删除再上传；删除示例行不影响解析。",
)

_TEMPLATE_COLUMNS = (
    ("支付期数", "必填", "整数，从 1 开始连续编号"),
    ("序列号", "可选", "同一合同内的行序号，仅用于定位"),
    ("对象", "建议", "项目/资产组编号；一份合同只能有一个"),
    ("合同编号", "可选", "与当前合同比对用，可留空"),
    ("出租方编号", "可选", "出租方代码"),
    ("合同类型", "可选", "按合同台账口径填写"),
    ("交房日期", "可选", "YYYY-MM-DD"),
    ("计租起始日期", "必填", "YYYY-MM-DD"),
    ("计租结束日期", "必填", "YYYY-MM-DD，可等于起始日期（0 月期）"),
    ("金额（不含税）", "必填", "不含税应付额，最多 2 位小数，可为 0（免租期）"),
    ("货币", "可选", "默认 CNY"),
    ("是否提供发票", "可选", "X/是/√ 或 否/空"),
    ("期初标识", "可选", "仅首期填 X"),
    ("期初支付标识", "可选", "期初支付的首期填 X"),
    ("备注", "可选", "自由文本；示例行在此标注"),
)

_TEMPLATE_BASE = dict(
    project_code="DEMO-A01", contract_no="DEMO-LT-001", lessor_code="D001",
    contract_type="3", handover_date=date(2026, 1, 1), currency="CNY", invoice_required="是",
)
_TEMPLATE_EXAMPLES = (
    {**_TEMPLATE_BASE, "sequence": 1, "serial_no": "1", "start_date": date(2026, 1, 1),
     "end_date": date(2026, 6, 30), "amount": Decimal("10000.00"), "opening_flag": "X",
     "opening_payment_flag": "X", "note": "示例-请替换：首期期初支付（合成演示数据）"},
    {**_TEMPLATE_BASE, "sequence": 2, "serial_no": "2", "start_date": date(2026, 7, 1),
     "end_date": date(2026, 12, 31), "amount": Decimal("10000.00"), "opening_flag": None,
     "opening_payment_flag": None, "note": "示例-请替换（合成演示数据）"},
    {**_TEMPLATE_BASE, "sequence": 3, "serial_no": "3", "start_date": date(2027, 1, 1),
     "end_date": date(2027, 6, 30), "amount": Decimal("10000.00"), "opening_flag": None,
     "opening_payment_flag": None, "note": "示例-请替换（合成演示数据）"},
)

_DATE_FORMAT = "yyyy-mm-dd"
_MONEY_FORMAT = "#,##0.00"
_HEADER_FILL = PatternFill("solid", fgColor="DCE6F1")


def _style_header(ws, row: int, width: int) -> None:
    for column in range(1, width + 1):
        cell = ws.cell(row=row, column=column)
        cell.font, cell.fill = Font(bold=True), _HEADER_FILL
        cell.alignment = Alignment(vertical="center", wrap_text=True)


def _fit_widths(ws, min_width: int = 8, max_width: int = 40) -> None:
    widths: dict[int, int] = {}
    for row in ws.iter_rows():
        for cell in row:
            if cell.value is not None:
                length = sum(2 if ord(ch) > 127 else 1 for ch in str(cell.value))
                widths[cell.column] = max(widths.get(cell.column, 0), length)
    for column, width in widths.items():
        ws.column_dimensions[get_column_letter(column)].width = max(min_width, min(max_width, width + 2))


def _template_cell(value: Any) -> Any:
    return safe_cell(value) if isinstance(value, str) else value


def template_workbook_bytes() -> bytes:
    """生成合成导入模板（填写说明 + 数据源）；示例数据全为合成值。"""
    workbook = openpyxl.Workbook()
    guide = workbook.active
    guide.title = GUIDE_SHEET_NAME
    guide["A1"] = "租赁核算 · 付款计划导入模板（合成示例，不含真实数据）"
    guide["A1"].font = Font(bold=True, size=14)
    guide["A2"] = "上传入口：财务 ▸ 租赁核算 ▸ 合同与付款 ▸ 付款计划导入。预览不落库，确认后才替换当前合同的付款计划。"
    guide["A3"] = "铁律：金额必须不含税；免租期必须占行（金额 0）；期初支付的首期必须单独一行。"
    guide["A3"].font = Font(bold=True, color="C00000")
    guide.cell(row=5, column=1, value="填写规则").font = Font(bold=True, size=12)
    for line, text in enumerate(_TEMPLATE_RULES, start=6):
        guide.cell(row=line, column=1, value=_template_cell(text))
    column_title_row = 6 + len(_TEMPLATE_RULES) + 1
    guide.cell(row=column_title_row, column=1, value="列说明").font = Font(bold=True, size=12)
    for column, text in enumerate(("列名", "是否必填", "填写规则"), start=1):
        cell = guide.cell(row=column_title_row + 1, column=column, value=text)
        cell.font, cell.fill = Font(bold=True), _HEADER_FILL
    for line, values in enumerate(_TEMPLATE_COLUMNS, start=column_title_row + 2):
        for column, text in enumerate(values, start=1):
            guide.cell(row=line, column=column, value=_template_cell(text))
    for column, width in (("A", 26), ("B", 12), ("C", 62)):
        guide.column_dimensions[column].width = width
    for line in range(2, guide.max_row + 1):
        guide.cell(row=line, column=1).alignment = Alignment(vertical="top", wrap_text=True)

    ws = workbook.create_sheet(DATA_SHEET_NAME)
    ws["A1"] = "数据源（一行 = 一个付款期）· 合成示例"
    ws["A1"].font = Font(bold=True, size=13)
    ws["A2"] = "金额为不含税应付额；免租期填 0；期初支付首期单独一行；日期格式 YYYY-MM-DD。"
    header_row = 3
    for column, (_, label) in enumerate(EXPORT_HEADERS, start=1):
        ws.cell(row=header_row, column=column, value=_template_cell(label))
    _style_header(ws, header_row, len(EXPORT_HEADERS))
    for line, example in enumerate(_TEMPLATE_EXAMPLES, start=header_row + 1):
        for column, (name, _) in enumerate(EXPORT_HEADERS, start=1):
            value = example[name]
            cell = ws.cell(row=line, column=column, value=_template_cell(value))
            if isinstance(value, date):
                cell.number_format = _DATE_FORMAT
            elif isinstance(value, Decimal):
                cell.number_format = _MONEY_FORMAT
    ws.freeze_panes = f"A{header_row + 1}"
    _fit_widths(ws)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()
