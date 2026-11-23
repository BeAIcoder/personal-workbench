"""finance_lease 请求 / 响应 / 计算引擎与导入导出 DTO 契约。

本文件是模块内唯一契约源：

- 状态枚举与中文标签只在这里定义，前端只消费后端返回值；
- 日期严格 ``YYYY-MM-DD``（拒绝带时间与其它格式），期间 ``YYYY-MM``；
- 金额一律 ``Decimal``（API 以字符串序列化，禁止 float），利率按小数录入；
- 计算引擎、导入器、导出器均通过本文件的 DTO 与服务层对接，引擎不读数据库；
- 本文件不实现任何会计计算，DTO 只固定调用接缝。
"""
from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

# ---------- 状态唯一事实源 ----------

CONTRACT_STATUSES: tuple[str, ...] = ("draft", "archived")
ContractStatus = Literal["draft", "archived"]
CONTRACT_STATUS_LABELS: dict[str, str] = {"draft": "草稿", "archived": "已归档"}

CALCULATION_STATUSES: tuple[str, ...] = ("calculated", "confirmed")
CalculationStatus = Literal["calculated", "confirmed"]
CALCULATION_STATUS_LABELS: dict[str, str] = {"calculated": "已测算", "confirmed": "已确认"}

CHECK_STATUSES: tuple[str, ...] = ("ok", "failed", "unknown")
CheckStatus = Literal["ok", "failed", "unknown"]
CHECK_STATUS_LABELS: dict[str, str] = {"ok": "通过", "failed": "差异", "unknown": "未知"}


# ---------- 严格日期 / 期间 ----------

_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_PERIOD_PATTERN = r"^\d{4}-(?:0[1-9]|1[0-2])$"
_CENT = Decimal("0.01")


def _strict_date(value: object) -> object:
    """只接受 YYYY-MM-DD 字符串或 date 对象；datetime 与宽松格式一律拒绝。"""
    if isinstance(value, datetime):
        raise ValueError("日期必须为 YYYY-MM-DD，不接受带时间的值")
    if isinstance(value, date):
        return value
    if isinstance(value, str) and _DATE_PATTERN.fullmatch(value):
        return value
    raise ValueError("日期必须为 YYYY-MM-DD 格式")


StrictDate = Annotated[date, BeforeValidator(_strict_date)]
StrictPeriod = Annotated[str, Field(pattern=_PERIOD_PATTERN)]


def validate_payment_sequences(items: list[object]) -> None:
    """付款期数必须从 1 连续编号且不重复（重复/跳号都是非法输入，不静默重排）。"""
    sequences = [int(getattr(item, "sequence")) for item in items]
    if len(sequences) != len(set(sequences)):
        duplicate = next(seq for seq in sequences if sequences.count(seq) > 1)
        raise ValueError(f"付款期数重复：第 {duplicate} 期（同一合同内期数必须唯一）")
    if not sequences:
        return
    expected = set(range(1, len(sequences) + 1))
    missing = sorted(expected - set(sequences))
    if missing:
        raise ValueError(f"付款期数必须从 1 连续编号，缺少第 {missing[0]} 期")
    extra = sorted(set(sequences) - expected)
    if extra:
        raise ValueError(f"付款期数必须从 1 连续编号，第 {extra[0]} 期越界或跳号")


def _strip_text_fields(data: object) -> object:
    """模型入参统一修剪首尾空白（不静默丢弃空项，空白必填项由字段校验拒绝）。"""
    if isinstance(data, dict):
        return {
            key: (value.strip() if isinstance(value, str) else value)
            for key, value in data.items()
        }
    return data


# ---------- 合同 ----------


class LeaseContractCreate(BaseModel):
    """新建合同：合同号与承租人主体必填，利率按小数录入（0.035000 = 3.5%）。"""

    model_config = ConfigDict(extra="forbid")

    contract_no: str = Field(max_length=64)
    name: str = Field(default="", max_length=200)
    lessee: str = Field(max_length=200)
    project: str = Field(default="", max_length=200)
    lessor: str = Field(default="", max_length=200)
    start_date: StrictDate
    end_date: StrictDate
    annual_rate: Decimal = Field(ge=0, le=1, max_digits=9, decimal_places=6)
    rate_basis: str = Field(default="", max_length=2000)
    remark: str = Field(default="", max_length=2000)
    attachment_note: str = Field(default="", max_length=2000)

    @model_validator(mode="before")
    @classmethod
    def _strip_text(cls, data):
        return _strip_text_fields(data)

    @field_validator("contract_no")
    @classmethod
    def _contract_no_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("合同号不能为空白")
        return value

    @field_validator("lessee")
    @classmethod
    def _lessee_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("承租人主体不能为空白")
        return value

    @model_validator(mode="after")
    def _date_order(self):
        if self.end_date < self.start_date:
            raise ValueError("合同结束日早于起始日")
        return self


class LeaseContractPatch(BaseModel):
    """部分更新：字段仅在草稿状态可改；status 只允许 draft → archived。"""

    model_config = ConfigDict(extra="forbid")

    contract_no: str | None = Field(default=None, max_length=64)
    name: str | None = Field(default=None, max_length=200)
    lessee: str | None = Field(default=None, max_length=200)
    project: str | None = Field(default=None, max_length=200)
    lessor: str | None = Field(default=None, max_length=200)
    start_date: StrictDate | None = None
    end_date: StrictDate | None = None
    annual_rate: Decimal | None = Field(default=None, ge=0, le=1, max_digits=9, decimal_places=6)
    rate_basis: str | None = Field(default=None, max_length=2000)
    remark: str | None = Field(default=None, max_length=2000)
    attachment_note: str | None = Field(default=None, max_length=2000)
    status: ContractStatus | None = None

    @model_validator(mode="before")
    @classmethod
    def _strip_text(cls, data):
        return _strip_text_fields(data)

    @field_validator("contract_no", "lessee")
    @classmethod
    def _required_text_not_blank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("合同号与承租人主体不能改为空白")
        return value

    @model_validator(mode="after")
    def _date_order(self):
        if self.start_date is not None and self.end_date is not None and self.end_date < self.start_date:
            raise ValueError("合同结束日早于起始日")
        return self


class ContractOut(BaseModel):
    """合同输出：附带付款期/测算计数、最近测算与过期标记。"""

    id: int
    contract_no: str
    name: str
    lessee: str
    project: str
    lessor: str
    start_date: date
    end_date: date
    annual_rate: Decimal
    rate_basis: str
    remark: str
    attachment_note: str
    status: ContractStatus
    status_label: str
    payment_count: int
    calculation_count: int
    confirmed_calculation_count: int
    latest_calculation_id: int | None
    latest_calculation_status: CalculationStatus | None
    latest_calculation_stale: bool
    created_at: datetime
    updated_at: datetime


class ContractListOut(BaseModel):
    total: int
    items: list[ContractOut]


# ---------- 付款计划 ----------


class LeasePaymentIn(BaseModel):
    """单个付款期输入：免租期与 0 月期都是合法行，不允许被静默剔除。"""

    model_config = ConfigDict(extra="forbid")

    sequence: int = Field(ge=1, le=2000)
    source_row: int | None = Field(default=None, ge=1, le=1000000)
    period_start: StrictDate
    period_end: StrictDate
    months: int | None = Field(default=None, ge=0, le=1200)
    amount: Decimal = Field(ge=0, max_digits=18, decimal_places=2)
    start_flag: bool = False
    prepay_flag: bool = False
    is_free_rent: bool = False
    note: str = Field(default="", max_length=500)

    @model_validator(mode="after")
    def _period_order(self):
        if self.period_end < self.period_start:
            raise ValueError(f"第 {self.sequence} 期计租结束日早于开始日")
        return self


class LeasePaymentsReplace(BaseModel):
    """整表替换付款计划（PUT）；空列表表示显式清空，测算时会再拒绝空计划。"""

    model_config = ConfigDict(extra="forbid")

    items: list[LeasePaymentIn] = Field(default_factory=list, max_length=1000)

    @model_validator(mode="after")
    def _sequences(self):
        validate_payment_sequences(self.items)
        return self


class PaymentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    contract_id: int
    sequence: int
    source_row: int | None
    period_start: date
    period_end: date
    months: int | None
    amount: Decimal
    start_flag: bool
    prepay_flag: bool
    is_free_rent: bool
    note: str


class PaymentSnapshot(BaseModel):
    """测算输入快照（导出与审计用，不含任何会计计算）。"""

    model_config = ConfigDict(extra="forbid")

    sequence: int = Field(ge=1, le=2000)
    source_row: int | None = Field(default=None, ge=1, le=1000000)
    period_start: StrictDate
    period_end: StrictDate
    months: int | None = Field(default=None, ge=0, le=1200)
    amount: Decimal = Field(ge=0, max_digits=18, decimal_places=2)
    start_flag: bool = False
    prepay_flag: bool = False
    is_free_rent: bool = False
    note: str = Field(default="", max_length=500)


# ---------- 计算引擎 DTO（纯数据、无数据库依赖） ----------


class PaymentInput(BaseModel):
    """引擎付款期输入：只含经济要素，源行号与备注不参与计算。"""

    model_config = ConfigDict(extra="forbid", from_attributes=True)

    sequence: int = Field(ge=1, le=2000)
    period_start: StrictDate
    period_end: StrictDate
    months: int | None = Field(default=None, ge=0, le=1200)
    amount: Decimal = Field(ge=0, max_digits=18, decimal_places=2)
    start_flag: bool = False
    prepay_flag: bool = False
    is_free_rent: bool = False


class CalculationInput(BaseModel):
    """引擎输入快照：合同经济要素 + 已通过连续性校验的付款计划。"""

    model_config = ConfigDict(extra="forbid")

    contract_start: StrictDate
    contract_end: StrictDate
    annual_rate: Decimal = Field(ge=0, le=1, max_digits=9, decimal_places=6)
    payments: list[PaymentInput] = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def _contract_order(self):
        if self.contract_end < self.contract_start:
            raise ValueError("合同结束日早于起始日")
        validate_payment_sequences(self.payments)
        return self


class AssessmentRow(BaseModel):
    """付款期级评估行；precision 仅约束输出，计算精度由规则矩阵负责。"""

    model_config = ConfigDict(extra="forbid")

    sequence: int = Field(ge=1, le=2000)
    period_start: StrictDate
    period_end: StrictDate
    months: int = Field(ge=0, le=1200)
    amount: Decimal = Field(ge=0, max_digits=18, decimal_places=2)
    period_rate: Decimal = Field(ge=0, max_digits=12, decimal_places=6)
    discount_factor: Decimal = Field(ge=0, max_digits=20, decimal_places=9)
    present_value: Decimal = Field(max_digits=18, decimal_places=2)
    unamortized_finance: Decimal = Field(max_digits=18, decimal_places=2)
    interest_u: Decimal = Field(max_digits=18, decimal_places=2)
    liability_balance: Decimal = Field(max_digits=18, decimal_places=2)


class PeriodRow(BaseModel):
    """月份级期间行；period_month 为 YYYY-MM。"""

    model_config = ConfigDict(extra="forbid")

    period_month: StrictPeriod
    sequence: int = Field(ge=1, le=2000)
    depreciation: Decimal = Field(max_digits=18, decimal_places=2)
    payable: Decimal = Field(max_digits=18, decimal_places=2)
    interest: Decimal = Field(max_digits=18, decimal_places=2)
    lease_payment: Decimal = Field(max_digits=18, decimal_places=2)
    unamortized_finance: Decimal = Field(max_digits=18, decimal_places=2)
    liability_balance: Decimal = Field(max_digits=18, decimal_places=2)


class CalculationSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total_payment: Decimal = Field(max_digits=18, decimal_places=2)
    present_value: Decimal = Field(max_digits=18, decimal_places=2)
    total_interest: Decimal = Field(max_digits=18, decimal_places=2)
    total_depreciation: Decimal = Field(max_digits=18, decimal_places=2)
    payment_count: int = Field(ge=1, le=2000)
    month_count: int = Field(ge=0, le=12000)
    first_period: StrictPeriod | None = None
    last_period: StrictPeriod | None = None


class CalculationCheck(BaseModel):
    """统一检查输出：缺数据必须是 unknown，不得按 0 通过。"""

    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9_]+$")
    title: str = Field(min_length=1, max_length=200)
    status: CheckStatus
    expected: Decimal | None = None
    actual: Decimal | None = None
    difference: Decimal | None = None
    message: str = Field(default="", max_length=2000)


class CalculationResult(BaseModel):
    """引擎成功结果的完整契约；服务层校验结构后整批落库，失败不落半套。"""

    model_config = ConfigDict(extra="forbid")

    rule_version: str = Field(min_length=1, max_length=64)
    assessment: list[AssessmentRow] = Field(min_length=1, max_length=1000)
    periods: list[PeriodRow] = Field(default_factory=list, max_length=12000)
    summary: CalculationSummary
    checks: list[CalculationCheck] = Field(min_length=1, max_length=100)


# ---------- 测算输出 ----------


class CalculationOut(BaseModel):
    id: int
    contract_id: int
    status: CalculationStatus
    status_label: str
    input_hash: str
    rule_version: str
    summary: CalculationSummary
    checks: list[CalculationCheck]
    check_counts: dict[str, int]
    all_checks_passed: bool
    has_unknown_checks: bool
    stale: bool
    confirmed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class AssessmentRowOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    calculation_id: int
    sequence: int
    source_row: int | None
    period_start: date
    period_end: date
    months: int
    amount: Decimal
    period_rate: Decimal
    discount_factor: Decimal
    present_value: Decimal
    unamortized_finance: Decimal
    interest_u: Decimal
    liability_balance: Decimal


class PeriodRowOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    calculation_id: int
    period_month: str
    sequence: int
    depreciation: Decimal
    payable: Decimal
    interest: Decimal
    lease_payment: Decimal
    unamortized_finance: Decimal
    liability_balance: Decimal


class ChecksOut(BaseModel):
    calculation_id: int
    input_hash: str
    rule_version: str
    stale: bool
    checks: list[CalculationCheck]
    check_counts: dict[str, int]
    all_passed: bool
    has_unknown: bool


# ---------- 摘要 ----------


class LeaseSummaryItem(BaseModel):
    contract_id: int
    contract_no: str
    name: str
    status: ContractStatus
    status_label: str
    payment_count: int
    latest_calculation_id: int | None
    latest_calculation_status: CalculationStatus | None
    latest_rule_version: str | None
    latest_calculated_at: datetime | None
    latest_confirmed_at: datetime | None
    stale: bool
    check_counts: dict[str, int]
    pending_checks: int
    latest_summary: CalculationSummary | None


class LeaseSummaryOut(BaseModel):
    total_contracts: int
    draft_count: int
    archived_count: int
    contracts_with_pending: int
    contracts: list[LeaseSummaryItem]


# ---------- 导入接缝 DTO ----------


class PaymentImportRow(BaseModel):
    """预览行：source_row 保留源表行号，确认导入后仍可定位差异。"""

    model_config = ConfigDict(extra="forbid")

    sequence: int = Field(ge=1, le=2000)
    source_row: int | None = Field(default=None, ge=1, le=1000000)
    period_start: StrictDate
    period_end: StrictDate
    months: int | None = Field(default=None, ge=0, le=1200)
    amount: Decimal = Field(ge=0, max_digits=18, decimal_places=2)
    start_flag: bool = False
    prepay_flag: bool = False
    is_free_rent: bool = False
    note: str = Field(default="", max_length=500)

    @model_validator(mode="after")
    def _period_order(self):
        if self.period_end < self.period_start:
            raise ValueError(f"第 {self.sequence} 期计租结束日早于开始日")
        return self


class PaymentImportPreview(BaseModel):
    """预览结果：errors 非空时允许展示，但确认导入必须拒绝（预览不落库）。"""

    sheet_name: str = ""
    header_map: dict[str, str] = Field(default_factory=dict)
    items: list[PaymentImportRow] = Field(default_factory=list, max_length=1000)
    warnings: list[str] = Field(default_factory=list, max_length=200)
    errors: list[str] = Field(default_factory=list, max_length=200)

    @classmethod
    def from_schedule(cls, result) -> "PaymentImportPreview":
        """把 IO 侧 ScheduleImport（鸭子类型）转成统一预览 DTO。"""
        def label(issue):
            return f"第 {issue.row} 行：{issue.message}" if issue.row else issue.message

        items, errors = [], [label(i) for i in result.errors]
        for row in result.rows:
            if row.sequence is None or row.start_date is None or row.end_date is None or row.amount is None:
                errors.append(f"第 {row.source_row} 行缺少期数/计租起止日/金额，已跳过")
                continue
            items.append(PaymentImportRow(
                sequence=row.sequence, source_row=row.source_row or None,
                period_start=row.start_date, period_end=row.end_date,
                months=getattr(row, "months", None), amount=row.amount,
                start_flag=bool(row.opening_flag), prepay_flag=bool(row.opening_payment_flag),
                is_free_rent=False, note=row.note or ""))
        return cls(sheet_name=result.sheet_name or "", items=items,
                   warnings=[label(i) for i in result.warnings], errors=errors)


class PaymentImportApplyResult(BaseModel):
    preview: PaymentImportPreview
    items: list[PaymentOut]


# ---------- 导出接缝 DTO ----------


class LeaseExportBundle(BaseModel):
    """导出器只消费本快照：金额为字符串，日期为 ISO，禁止任何会计重算。"""

    contract_no: str
    name: str
    lessee: str
    project: str
    lessor: str
    start_date: date
    end_date: date
    annual_rate: Decimal
    payments: list[PaymentSnapshot]
    calculation: CalculationOut
    assessment: list[AssessmentRowOut]
    periods: list[PeriodRowOut]
    stale: bool
    exported_at: datetime
    rate_basis: str = ""
    remark: str = ""
    attachment_note: str = ""
    totals: dict[str, str] = Field(default_factory=dict)
    entries: list[dict] = Field(default_factory=list)
    entries_supported: list[str] = Field(default_factory=list)
    entries_note: str = ""

    def to_export_file(self, build, names=None) -> "LeaseExportFile":
        payload = _export_payload(self)
        filename, ascii_name = names(payload) if callable(names) else ("租赁核算底稿.xlsx", None)
        return LeaseExportFile(filename=filename, content=build(payload), filename_ascii=ascii_name)


class LeaseExportFile(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    content: bytes
    filename_ascii: str | None = None


def _export_payload(bundle: "LeaseExportBundle") -> dict:
    """转成 exporter.py 的 payload 键名；只搬运快照，不重算任何金额。"""
    calc = bundle.calculation
    status = "pass" if calc.all_checks_passed else ("fail" if calc.check_counts["failed"] else "unknown")
    checks = {"status": status, "counts": calc.check_counts, "items": [
        {"code": c.key, "title": c.title,
         "status": {"ok": "pass", "failed": "fail", "unknown": "unknown"}[c.status],
         "left": None if c.expected is None else str(c.expected),
         "right": None if c.actual is None else str(c.actual),
         "diff": None if c.difference is None else str(c.difference),
         "message": c.message} for c in calc.checks]}

    # D2：期间表净值是快照派生展示值——期初现值减累计已存 depreciation（含本月），
    # 不重算折现/折旧规则，也不吸收尾差；所有中间值按 2 位 Decimal 量化。
    asset_cost = Decimal(str(calc.summary.present_value)).quantize(_CENT, rounding=ROUND_HALF_UP)
    cumulative = Decimal("0.00")
    period_items = []
    for row in bundle.periods:
        cumulative = (cumulative + Decimal(str(row.depreciation))).quantize(
            _CENT, rounding=ROUND_HALF_UP)
        period_items.append({
            "period": row.period_month, "months": 1, "depreciation": str(row.depreciation),
            "payment_due": str(row.payable), "interest": str(row.interest),
            "asset_balance": str((asset_cost - cumulative).quantize(_CENT, rounding=ROUND_HALF_UP)),
            "liability_balance": str(row.liability_balance), "sequence": row.sequence,
        })

    note = bundle.remark
    if bundle.attachment_note and bundle.attachment_note != "未留存":
        note = (f"{note}；附件说明：{bundle.attachment_note}"
                if note and note != "未留存" else f"附件说明：{bundle.attachment_note}")

    return {
        "contract": {"contract_no": bundle.contract_no, "lessee_name": bundle.lessee,
                     "project_name": bundle.project, "lessor_name": bundle.lessor,
                     "start_date": bundle.start_date.isoformat(), "end_date": bundle.end_date.isoformat(),
                     "annual_rate": str(bundle.annual_rate), "currency": "CNY",
                     "rate_basis": bundle.rate_basis, "note": note,
                     "attachment_note": bundle.attachment_note},
        "calculation": {"id": calc.id, "status": calc.status, "status_label": calc.status_label,
                        "calculated_at": calc.created_at.isoformat(timespec="seconds"),
                        "rule_version": calc.rule_version, "input_hash": calc.input_hash,
                        "stale": bundle.stale,
                        "stale_reason": "当前输入已变化，历史快照" if bundle.stale else ""},
        "payments": [{"sequence": p.sequence, "contract_no": bundle.contract_no,
                      "start_date": p.period_start.isoformat(), "end_date": p.period_end.isoformat(),
                      "amount": str(p.amount), "currency": "CNY", "opening_flag": p.start_flag,
                      "opening_payment_flag": p.prepay_flag, "note": p.note} for p in bundle.payments],
        "assessments": [{"sequence": r.sequence, "months": r.months, "amount": str(r.amount),
                         "annual_rate": str(bundle.annual_rate), "period_rate": str(r.period_rate),
                         "discount_factor": str(r.discount_factor), "present_value": str(r.present_value),
                         "interest_total": str(r.unamortized_finance),
                         "interest": str(r.interest_u),
                         "liability_balance": str(r.liability_balance), "source_row": r.source_row}
                        for r in bundle.assessment],
        "periods": period_items,
        "totals": bundle.totals or {
            "asset_total": str(calc.summary.present_value),
            "liability_total": str(calc.summary.present_value),
            "payment_total": str(calc.summary.total_payment),
            "unrecognized_finance_cost": str(calc.summary.total_interest),
        },
        "checks": checks,
        "entries_supported": bundle.entries_supported,
        "entries_note": bundle.entries_note,
        "entries": bundle.entries,
    }
