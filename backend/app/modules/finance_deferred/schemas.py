"""finance_deferred 请求 / 响应 / 计算 DTO 契约（模块内唯一契约源）。

约定：

- 状态枚举与中文标签只在这里定义，前端只消费后端返回值；
- 日期严格 ``YYYY-MM-DD``（拒绝带时间与其它格式），期间 ``YYYY-MM``；
- 金额一律 ``Decimal``（API 以字符串序列化，禁止 float），税率按小数录入
  （0.0600 = 6%）；
- 计算引擎通过本文件 DTO 与服务层对接，引擎不读数据库、不写文件；
- 本文件不实现任何会计计算，只固定调用接缝与校验结论形状。
"""
from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, field_validator, model_validator

# ---------- 状态唯一事实源 ----------

ITEM_STATUSES: tuple[str, ...] = ("draft", "archived")
ItemStatus = Literal["draft", "archived"]
ITEM_STATUS_LABELS: dict[str, str] = {"draft": "草稿", "archived": "已归档"}

RUN_STATUSES: tuple[str, ...] = ("calculated", "confirmed")
RunStatus = Literal["calculated", "confirmed"]
RUN_STATUS_LABELS: dict[str, str] = {"calculated": "已测算", "confirmed": "已确认"}

# warning = 已披露但不阻断（超租赁到期日、末期尾差），确认前必须显式确认风险
CHECK_STATUSES: tuple[str, ...] = ("ok", "warning", "failed", "unknown")
CheckStatus = Literal["ok", "warning", "failed", "unknown"]
CHECK_STATUS_LABELS: dict[str, str] = {
    "ok": "通过", "warning": "警告", "failed": "差异", "unknown": "未知",
}

# ---------- 严格日期 / 期间 ----------

_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_PERIOD_PATTERN = r"^\d{4}-(?:0[1-9]|1[0-2])$"


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


def _strip_text_fields(data: object) -> object:
    """入参统一修剪首尾空白（不静默丢弃空项，空白必填项由字段校验拒绝）。"""
    if isinstance(data, dict):
        return {
            key: (value.strip() if isinstance(value, str) else value)
            for key, value in data.items()
        }
    return data


# ---------- 项目主档 ----------


class DeferredItemCreate(BaseModel):
    """新建项目：编码/名称/项目/含税金额/税率/起摊月/月数/租赁到期日必填。"""

    model_config = ConfigDict(extra="forbid")

    project_code: str = Field(max_length=64)
    name: str = Field(max_length=200)
    project: str = Field(max_length=200)
    amount_incl_tax: Decimal = Field(ge=0, max_digits=18, decimal_places=2)
    tax_rate: Decimal = Field(ge=0, le=1, max_digits=6, decimal_places=4)
    start_month: StrictPeriod
    months: int = Field(ge=1, le=600)
    lease_end_date: StrictDate
    remark: str = Field(default="", max_length=2000)

    @model_validator(mode="before")
    @classmethod
    def _strip_text(cls, data):
        return _strip_text_fields(data)

    @field_validator("project_code", "name", "project")
    @classmethod
    def _required_text_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("项目编码、项目名称与所属项目不能为空白")
        return value


class DeferredItemPatch(BaseModel):
    """部分更新：仅草稿可改；status 只允许 draft → archived。"""

    model_config = ConfigDict(extra="forbid")

    project_code: str | None = Field(default=None, max_length=64)
    name: str | None = Field(default=None, max_length=200)
    project: str | None = Field(default=None, max_length=200)
    amount_incl_tax: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=2)
    tax_rate: Decimal | None = Field(default=None, ge=0, le=1, max_digits=6, decimal_places=4)
    start_month: StrictPeriod | None = None
    months: int | None = Field(default=None, ge=1, le=600)
    lease_end_date: StrictDate | None = None
    remark: str | None = Field(default=None, max_length=2000)
    status: ItemStatus | None = None

    @model_validator(mode="before")
    @classmethod
    def _strip_text(cls, data):
        return _strip_text_fields(data)

    @field_validator("project_code", "name", "project")
    @classmethod
    def _required_text_not_blank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("项目编码、项目名称与所属项目不能改为空白")
        return value


class DeferredItemOut(BaseModel):
    """项目输出：附摊销到期月、测算计数、最近测算与过期标记。"""

    id: int
    project_code: str
    name: str
    project: str
    amount_incl_tax: Decimal
    tax_rate: Decimal
    amount_net: Decimal
    tax_amount: Decimal
    start_month: str
    months: int
    amortization_end_month: str
    lease_end_date: date
    remark: str
    status: ItemStatus
    status_label: str
    run_count: int
    confirmed_run_count: int
    latest_run_id: int | None
    latest_run_status: RunStatus | None
    latest_run_stale: bool
    created_at: datetime
    updated_at: datetime


class DeferredItemListOut(BaseModel):
    total: int
    items: list[DeferredItemOut]


# ---------- 计算接缝 ----------


class CalculationInput(BaseModel):
    """计算引擎输入：只有经济要素，展示字段不参与。"""

    model_config = ConfigDict(extra="forbid")

    amount_incl_tax: Decimal = Field(ge=0, max_digits=18, decimal_places=2)
    tax_rate: Decimal = Field(ge=0, le=1, max_digits=6, decimal_places=4)
    start_month: StrictPeriod
    months: int = Field(ge=1, le=600)
    lease_end_date: StrictDate


class PeriodRow(BaseModel):
    """月份级摊销行；余额 = 净额 − 累计摊销（末期尾差如实保留）。"""

    period_month: str = Field(pattern=_PERIOD_PATTERN)
    sequence: int = Field(ge=1)
    monthly_amount: Decimal
    cumulative: Decimal
    balance: Decimal
    remaining_months: int = Field(ge=0)


class AmortizationSummary(BaseModel):
    """测算汇总：净额/税额/月摊/累计/尾差/到期月/风险标记，均为引擎输出。"""

    amount_incl_tax: Decimal
    tax_rate: Decimal
    tax_amount: Decimal
    amount_net: Decimal
    monthly_amount: Decimal
    months: int
    start_month: str
    end_month: str
    amortized_total: Decimal
    rounding_difference: Decimal
    lease_end_date: date
    expiry_after_lease: bool
    risk_flags: list[str]
    period_count: int


class DeferredCheck(BaseModel):
    """统一校验项：期望值/实际值/差异为金额时输出两位字符串。"""

    key: str
    title: str
    status: CheckStatus
    expected: Decimal | None = None
    actual: Decimal | None = None
    difference: Decimal | None = None
    message: str = ""


class RunResult(BaseModel):
    """计算引擎输出：期间行 + 汇总 + 校验，服务层只校验结构不重算口径。"""

    rule_version: str
    periods: list[PeriodRow]
    summary: AmortizationSummary
    checks: list[DeferredCheck]


class PeriodRowOut(PeriodRow):
    model_config = ConfigDict(from_attributes=True)

    id: int


class RunOut(BaseModel):
    """测算批次输出：状态、快照标识、汇总、校验与风险确认留痕。"""

    id: int
    item_id: int
    status: RunStatus
    status_label: str
    input_hash: str
    rule_version: str
    summary: AmortizationSummary
    checks: list[DeferredCheck]
    check_counts: dict[str, int]
    all_checks_passed: bool
    has_warnings: bool
    has_unknown_checks: bool
    stale: bool
    risk_acknowledged: bool
    risk_note: str
    confirmed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ChecksOut(BaseModel):
    run_id: int
    input_hash: str
    rule_version: str
    stale: bool
    checks: list[DeferredCheck]
    check_counts: dict[str, int]
    all_passed: bool
    has_warnings: bool
    has_unknown: bool


class DeferredConfirmIn(BaseModel):
    """确认入参：存在风险警告时必须显式确认并写明风险说明。"""

    model_config = ConfigDict(extra="forbid")

    risk_acknowledged: bool = False
    risk_note: str = Field(default="", max_length=2000)

    @model_validator(mode="before")
    @classmethod
    def _strip_text(cls, data):
        return _strip_text_fields(data)


# ---------- 导出 ----------


class DeferredExportItem(BaseModel):
    """导出用项目参数快照（来自测算时的 input_snapshot，不读当前行）。"""

    project_code: str
    name: str
    project: str
    amount_incl_tax: Decimal
    tax_rate: Decimal
    amount_net: Decimal
    tax_amount: Decimal
    start_month: str
    months: int
    lease_end_date: date
    remark: str
    status: str
    status_label: str


class DeferredExportBundle(BaseModel):
    item: DeferredExportItem
    run: RunOut
    periods: list[PeriodRowOut]
    stale: bool
    exported_at: datetime


class DeferredExportFile(BaseModel):
    filename: str
    filename_ascii: str
    content: bytes


# ---------- 摘要 ----------


class DeferredSummaryItem(BaseModel):
    item_id: int
    project_code: str
    name: str
    project: str
    status: str
    status_label: str
    amount_incl_tax: Decimal
    amount_net: Decimal
    monthly_amount: Decimal | None
    start_month: str
    end_month: str
    months: int
    lease_end_date: date
    expiry_after_lease: bool
    latest_run_id: int | None
    latest_run_status: str | None
    latest_rule_version: str | None
    latest_calculated_at: datetime | None
    latest_confirmed_at: datetime | None
    stale: bool
    check_counts: dict[str, int]
    pending_checks: int
    risk_acknowledged: bool


class DeferredSummaryOut(BaseModel):
    total_items: int
    draft_count: int
    archived_count: int
    items_with_pending: int
    items: list[DeferredSummaryItem]
