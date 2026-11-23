# -*- coding: utf-8 -*-
"""finance_union 请求 / 响应 / 计算与导出 DTO 契约（模块内唯一契约源）。

口径冻结（首期，不允许在别处再定义一遍）：

- 状态：批次只有 ``draft`` / ``confirmed``，合法转移只有 draft → confirmed；
- 校验结论三态：``ok`` / ``failed`` / ``unknown``——缺数据必须是 unknown，不得按 0 通过；
- 期间严格 ``YYYY-MM``；金额与费率一律 ``Decimal``（JSON 输出为字符串，禁止 float）；
- 费率按小数录入（2% 填 0.02），**请求必填**，本模块任何位置都不提供默认费率；
- 行级不提供费率覆盖：一个批次一个费率，逐行只用批次费率。

计算派生值（本期计提 / 上期差异 / 本期建议入账）**不落库**，由 ``calculation.py``
按当前输入实时推导；DTO 只固定调用接缝，不在本文件实现算法。
"""
from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic.fields import FieldInfo  # noqa: F401  仅用于 amount_input 返回类型注解

# ---------- 状态与结论的唯一事实源 ----------

BATCH_STATUSES: tuple[str, ...] = ("draft", "confirmed")
BatchStatus = Literal["draft", "confirmed"]
BATCH_STATUS_LABELS: dict[str, str] = {"draft": "草稿", "confirmed": "已确认"}

CHECK_STATUSES: tuple[str, ...] = ("ok", "failed", "unknown")
CheckStatus = Literal["ok", "failed", "unknown"]
CHECK_STATUS_LABELS: dict[str, str] = {"ok": "通过", "failed": "差异", "unknown": "未知"}

LINE_STATUSES: tuple[str, ...] = ("ok", "unknown")
LineStatus = Literal["ok", "unknown"]
LINE_STATUS_LABELS: dict[str, str] = {"ok": "可计算", "unknown": "缺数据"}

# ---------- 严格期间 ----------

PERIOD_PATTERN = r"^\d{4}-(?:0[1-9]|1[0-2])$"
_PERIOD_RE = re.compile(PERIOD_PATTERN)

def amount_input(description: str = "") -> "FieldInfo":
    """金额入参统一约束：2 位小数、非负、可为空（空 = 未录入，不是 0）。"""
    return Field(default=None, ge=0, max_digits=18, decimal_places=2, description=description)


def validate_period(value: str) -> str:
    """``YYYY-MM`` 严格校验（用于服务层/工具再验一次，不只依赖请求模型）。"""
    if not isinstance(value, str) or not _PERIOD_RE.fullmatch(value):
        raise ValueError("期间必须为 YYYY-MM 格式（例如 2026-11）")
    return value


def _strip_text(value: object) -> object:
    return value.strip() if isinstance(value, str) else value


# ---------- 批次 ----------


class UnionBatchCreate(BaseModel):
    """新建批次：主体、期间与计提率必填；费率不提供默认值。"""

    model_config = ConfigDict(extra="forbid")

    entity: str = Field(min_length=1, max_length=200, description="计提主体")
    period: str = Field(pattern=PERIOD_PATTERN, description="所属期间 YYYY-MM")
    rate: Decimal = Field(
        gt=0, le=1, max_digits=9, decimal_places=6,
        description="计提率（小数，2% 填 0.02）；必填，不提供默认值",
    )
    remark: str = Field(default="", max_length=2000)

    @model_validator(mode="before")
    @classmethod
    def _strip(cls, data):
        if isinstance(data, dict):
            return {key: _strip_text(value) for key, value in data.items()}
        return data

    @field_validator("entity")
    @classmethod
    def _entity_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("计提主体不能为空白")
        return value

    @field_validator("remark")
    @classmethod
    def _remark_strip(cls, value: str) -> str:
        return value.strip()


class UnionBatchPatch(BaseModel):
    """部分更新（仅草稿）：主体/期间/费率/备注；已确认批次一律 409。"""

    model_config = ConfigDict(extra="forbid")

    entity: str | None = Field(default=None, min_length=1, max_length=200)
    period: str | None = Field(default=None, pattern=PERIOD_PATTERN)
    rate: Decimal | None = Field(default=None, gt=0, le=1, max_digits=9, decimal_places=6)
    remark: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="before")
    @classmethod
    def _strip(cls, data):
        if isinstance(data, dict):
            return {key: _strip_text(value) for key, value in data.items()}
        return data

    @field_validator("entity")
    @classmethod
    def _entity_not_blank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("计提主体不能改为空白")
        return value

    @field_validator("remark")
    @classmethod
    def _remark_strip(cls, value: str | None) -> str | None:
        return None if value is None else value.strip()

    @model_validator(mode="after")
    def _at_least_one(self):
        if all(getattr(self, name) is None for name in ("entity", "period", "rate", "remark")):
            raise ValueError("至少需要提供一个可修改字段")
        return self


# ---------- 部门行 ----------


class UnionLineIn(BaseModel):
    """部门行输入：三个金额都可为空（= 未录入），显式 0 与空值是两回事。

    行级不接受费率：费率只来自批次（单一事实源），避免同批次多口径。
    负数工资基数/负数上期数一律拒绝；上期差异的负数由计算产生，不由输入给出。
    """

    model_config = ConfigDict(extra="forbid")

    department: str = Field(min_length=1, max_length=100)
    current_wage_base: Decimal | None = amount_input("本期工资基数（手工录入）")
    prior_accrual: Decimal | None = amount_input("上期已计提")
    prior_actual: Decimal | None = amount_input("上期实际数")

    @model_validator(mode="before")
    @classmethod
    def _strip(cls, data):
        if isinstance(data, dict):
            return {key: _strip_text(value) for key, value in data.items()}
        return data

    @field_validator("department")
    @classmethod
    def _department_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("部门名称不能为空白")
        return value


class UnionLinesReplace(BaseModel):
    """整批保存部门行（PUT）：空列表表示显式清空（草稿仍可清空，确认会拒绝）。

    部门重复由服务层判 409（与数据库唯一索引同口径），这里只校验结构形状。
    """

    model_config = ConfigDict(extra="forbid")

    items: list[UnionLineIn] = Field(default_factory=list, max_length=500)


# ---------- 计算输入（纯数据、无数据库依赖） ----------


class LineInput(BaseModel):
    """计算引擎的行输入：只含经济要素，部门名仅作回填。"""

    model_config = ConfigDict(extra="forbid", from_attributes=True)

    department: str = Field(min_length=1, max_length=100)
    current_wage_base: Decimal | None = amount_input("本期工资基数（手工录入）")
    prior_accrual: Decimal | None = amount_input("上期已计提")
    prior_actual: Decimal | None = amount_input("上期实际数")


class BatchInput(BaseModel):
    """计算引擎输入：批次经济要素 + 部门行；引擎不读数据库、不写文件。"""

    model_config = ConfigDict(extra="forbid")

    entity: str = Field(min_length=1, max_length=200)
    period: str = Field(pattern=PERIOD_PATTERN)
    rate: Decimal = Field(gt=0, le=1, max_digits=9, decimal_places=6)
    lines: list[LineInput] = Field(default_factory=list, max_length=500)


# ---------- 计算输出 ----------


class UnionLineResult(BaseModel):
    """单行计算结果：任一输入缺失时 line_status=unknown，三个派生值全为 None。"""

    model_config = ConfigDict(extra="forbid")

    department: str
    current_wage_base: Decimal | None = None
    prior_accrual: Decimal | None = None
    prior_actual: Decimal | None = None
    line_status: LineStatus
    current_accrued: Decimal | None = None
    prior_variance: Decimal | None = None
    current_book_amount: Decimal | None = None
    missing_fields: list[str] = Field(default_factory=list)
    note: str = ""


class UnionTotals(BaseModel):
    """合计：只汇总 line_status=ok 的行；unknown 行数单独上报，绝不按 0 混入。"""

    model_config = ConfigDict(extra="forbid")

    current_wage_base: Decimal
    current_accrued: Decimal
    prior_accrual: Decimal
    prior_actual: Decimal
    prior_variance: Decimal
    current_book_amount: Decimal


class UnionCheck(BaseModel):
    """统一检查输出：缺数据必须是 unknown，不得按 0 通过。"""

    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9_]+$")
    title: str = Field(min_length=1, max_length=200)
    status: CheckStatus
    expected: Decimal | None = None
    actual: Decimal | None = None
    difference: Decimal | None = None
    message: str = Field(default="", max_length=2000)


class BatchResult(BaseModel):
    """引擎完整输出：预览、校验、导出、确认都以本结构为唯一输入。"""

    model_config = ConfigDict(extra="forbid")

    rule_version: str = Field(min_length=1, max_length=64)
    line_count: int = Field(ge=0, le=500)
    ok_line_count: int = Field(ge=0, le=500)
    unknown_line_count: int = Field(ge=0, le=500)
    lines: list[UnionLineResult] = Field(default_factory=list, max_length=500)
    totals: UnionTotals
    checks: list[UnionCheck] = Field(min_length=1, max_length=100)
    check_counts: dict[str, int] = Field(default_factory=dict)
    all_passed: bool
    has_unknown: bool
    confirmable: bool
    confirm_reason: str = Field(default="", max_length=2000)


# ---------- 响应 ----------


class UnionLineOut(BaseModel):
    """部门行输出：附带实时计算的派生值，未知行不返回金额。"""

    model_config = ConfigDict(extra="forbid")

    id: int | None = None
    batch_id: int | None = None
    department: str
    current_wage_base: Decimal | None
    prior_accrual: Decimal | None
    prior_actual: Decimal | None
    line_status: LineStatus
    line_status_label: str
    current_accrued: Decimal | None
    prior_variance: Decimal | None
    current_book_amount: Decimal | None
    missing_fields: list[str] = Field(default_factory=list)
    note: str = ""


class UnionBatchOut(BaseModel):
    """批次列表项：计数与合计来自实时计算（不缓存派生值）。"""

    id: int
    entity: str
    period: str
    rate: Decimal
    status: BatchStatus
    status_label: str
    remark: str
    line_count: int
    unknown_line_count: int
    current_accrued_total: Decimal
    prior_variance_total: Decimal
    current_book_amount_total: Decimal
    confirmable: bool
    confirmed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class UnionBatchListOut(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[UnionBatchOut]


class UnionBatchDetailOut(BaseModel):
    """批次详情：批次头 + 行 + 合计 + 校验（一次请求给前端完整工作区）。"""

    batch: UnionBatchOut
    lines: list[UnionLineOut]
    totals: UnionTotals
    checks: list[UnionCheck]
    check_counts: dict[str, int]
    all_passed: bool
    has_unknown: bool
    confirmable: bool
    confirm_reason: str
    rule_version: str


class UnionPreviewOut(BaseModel):
    """计算预览（只读）：不落库、不改状态，只按当前输入实时推导。"""

    batch_id: int | None = None
    entity: str
    period: str
    rate: Decimal
    status: BatchStatus
    status_label: str
    rule_version: str
    line_count: int
    ok_line_count: int
    unknown_line_count: int
    lines: list[UnionLineResult]
    totals: UnionTotals
    checks: list[UnionCheck]
    check_counts: dict[str, int]
    all_passed: bool
    has_unknown: bool
    confirmable: bool
    confirm_reason: str


class UnionChecksOut(BaseModel):
    batch_id: int
    status: BatchStatus
    rule_version: str
    checks: list[UnionCheck]
    check_counts: dict[str, int]
    all_passed: bool
    has_unknown: bool
    confirmable: bool
    confirm_reason: str


class UnionSummaryItem(BaseModel):
    batch_id: int
    entity: str
    period: str
    rate: Decimal
    status: BatchStatus
    status_label: str
    line_count: int
    unknown_line_count: int
    current_accrued_total: Decimal
    prior_variance_total: Decimal
    current_book_amount_total: Decimal
    confirmable: bool
    confirmed_at: datetime | None


class UnionSummaryOut(BaseModel):
    total_batches: int
    draft_count: int
    confirmed_count: int
    unknown_line_total: int
    current_accrued_total: Decimal
    prior_variance_total: Decimal
    current_book_amount_total: Decimal
    batches: list[UnionSummaryItem]
