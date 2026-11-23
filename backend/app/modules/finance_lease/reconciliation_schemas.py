"""finance_lease × BIP 只读对账契约（科目映射 + 对账结果 DTO）。

本文件是「租赁核算 — BIP 科目余额」对账的唯一契约源：

- 五类科目位 ``SUBJECT_KEYS`` 与中文标签只在这里定义，前端只消费返回值；
- 余额方向 ``MAPPING_DIRECTIONS``（debit/credit）表示 BIP 科目余额方向；
- 对账结论沿用模块既有三态 ``ok/failed/unknown``（复用 ``schemas`` 的唯一事实源），
  缺数据一律 unknown，绝不按 0 通过；
- 金额一律 ``Decimal``（JSON 序列化为字符串），容差固定 ``TOLERANCE`` = 0.01；
- 期间严格 ``YYYY-MM``（复用 ``schemas.StrictPeriod``）。

本文件不查询数据库、不实现任何会计计算，只固定请求/响应结构。
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .schemas import CHECK_STATUS_LABELS, CheckStatus, StrictPeriod

# ---------- 科目位 / 方向（唯一事实源） ----------

SUBJECT_KEYS: tuple[str, ...] = (
    "rou_asset",
    "rou_accum_dep",
    "lease_payment",
    "unrecognized_finance",
    "lease_liability",
)
SUBJECT_KEY_LABELS: dict[str, str] = {
    "rou_asset": "使用权资产原值",
    "rou_accum_dep": "使用权资产累计折旧",
    "lease_payment": "租赁付款额（剩余付款）",
    "unrecognized_finance": "未确认融资费用余额",
    "lease_liability": "租赁负债净额",
}

MAPPING_DIRECTIONS: tuple[str, ...] = ("debit", "credit")
DIRECTION_LABELS: dict[str, str] = {"debit": "借方", "credit": "贷方"}

# 对账容差：固定 0.01（分），不从界面传入，避免「调容差调出通过」
TOLERANCE = Decimal("0.01")

# 实际数来源（结果里显式标注，禁止猜测）
SOURCE_LABELS: dict[str, str] = {
    "bip_closing_direction": "BIP 期末借方/贷方之差",
    "bip_closing_balance": "BIP 期末余额（方向金额均为空，按映射方向取绝对值）",
    "bip_zero_balance": "BIP 余额行方向金额与期末余额均为空",
    "bip_missing": "BIP 缺该科目余额行",
    "bip_ambiguous": "BIP 同科目多行（歧义）",
    "bip_conflict": "BIP 方向金额与期末余额不一致（冲突）",
    "unmapped": "未配置科目映射",
    "no_calculation": "没有可用于该期间的已确认测算",
}

EXPECTED_SOURCE_SNAPSHOT = "lease_snapshot"


# ---------- 映射请求 / 输出 ----------


def _strip_text(data: object) -> object:
    if isinstance(data, dict):
        return {key: (value.strip() if isinstance(value, str) else value) for key, value in data.items()}
    return data


class MappingIn(BaseModel):
    """单条科目映射：科目位 + BIP 科目编码 + 余额方向。"""

    model_config = ConfigDict(extra="forbid")

    subject_key: str = Field(max_length=40)
    subject_code: str = Field(min_length=1, max_length=50)
    direction: str = Field(default="debit", max_length=10)

    @model_validator(mode="before")
    @classmethod
    def _strip(cls, data):
        return _strip_text(data)

    @field_validator("subject_key")
    @classmethod
    def _known_key(cls, value: str) -> str:
        if value not in SUBJECT_KEYS:
            raise ValueError(f"未知科目位「{value}」，可用：{'、'.join(SUBJECT_KEYS)}")
        return value

    @field_validator("subject_code")
    @classmethod
    def _code_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("BIP 科目编码不能为空白")
        return value

    @field_validator("direction")
    @classmethod
    def _known_direction(cls, value: str) -> str:
        if value not in MAPPING_DIRECTIONS:
            raise ValueError("余额方向只能是 debit 或 credit")
        return value


class MappingPutRequest(BaseModel):
    """整批替换某承租人 × 账簿范围的科目映射（PUT 语义：未出现的科目位视为清除）。"""

    model_config = ConfigDict(extra="forbid")

    lessee: str = Field(min_length=1, max_length=200)
    ledger: str = Field(min_length=1, max_length=100)
    items: list[MappingIn] = Field(default_factory=list, max_length=len(SUBJECT_KEYS))

    @model_validator(mode="before")
    @classmethod
    def _strip(cls, data):
        return _strip_text(data)

    @field_validator("lessee", "ledger")
    @classmethod
    def _scope_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("承租人主体与核算账簿不能为空白")
        return value

    @model_validator(mode="after")
    def _unique(self):
        keys = [item.subject_key for item in self.items]
        duplicate = next((key for key in keys if keys.count(key) > 1), None)
        if duplicate is not None:
            raise ValueError(f"科目位「{duplicate}」重复，同一科目位只能配置一个 BIP 科目")
        codes = [item.subject_code for item in self.items]
        repeated = next((code for code in codes if codes.count(code) > 1), None)
        if repeated is not None:
            raise ValueError(f"BIP 科目「{repeated}」不能同时映射到多个科目位，否则对账会重复计数")
        return self


class SubjectKeyOption(BaseModel):
    key: str
    label: str


class DirectionOption(BaseModel):
    value: str
    label: str


class MappingOut(BaseModel):
    id: int
    lessee: str
    ledger: str
    subject_key: str
    subject_key_label: str
    subject_code: str
    direction: str
    direction_label: str
    updated_at: datetime


class MappingListOut(BaseModel):
    total: int
    items: list[MappingOut]
    subject_keys: list[SubjectKeyOption]
    directions: list[DirectionOption]


class ContextOut(BaseModel):
    """下拉上下文：承租人来自租赁合同，账簿/期间来自 BIP 余额表（均为真实存在值）。"""

    lessees: list[str]
    ledgers: list[str]
    periods: list[str]
    subject_keys: list[SubjectKeyOption]
    directions: list[DirectionOption]
    mappings: list[MappingOut]


# ---------- 对账请求 / 输出 ----------


class CheckRequest(BaseModel):
    """对账粒度：承租人 + 核算账簿 + 期间（YYYY-MM）；不指定单个合同。"""

    model_config = ConfigDict(extra="forbid")

    lessee: str = Field(min_length=1, max_length=200)
    ledger: str = Field(min_length=1, max_length=100)
    period: StrictPeriod

    @model_validator(mode="before")
    @classmethod
    def _strip(cls, data):
        return _strip_text(data)

    @field_validator("lessee", "ledger")
    @classmethod
    def _scope_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("承租人主体与核算账簿不能为空白")
        return value


class SubjectResult(BaseModel):
    """单科目对账结果：expected 来自模块快照，actual 来自 BIP 只读余额。"""

    subject_key: str
    subject_key_label: str
    subject_code: str | None
    direction: str | None
    direction_label: str | None
    expected: Decimal | None
    expected_source: str
    actual: Decimal | None
    difference: Decimal | None
    status: CheckStatus
    status_label: str
    source: str
    source_label: str
    reason: str


class ContractCoverage(BaseModel):
    """每合同的覆盖明细：采用哪个批次、为什么采用/未采用、各科目位金额。

    ``selected=true`` 表示该合同金额已计入本期间汇总；``reason`` 为空串。
    未采用的合同 ``reason`` 为中文原因（stale / 未确认 / 无覆盖 / 无测算 …）。
    """

    contract_id: int
    contract_no: str
    name: str
    contract_status: str
    selected: bool
    calculation_id: int | None
    calculation_status: str | None
    calculation_input_hash: str | None
    calculation_rule_version: str | None
    stale: bool
    covers_period: bool
    reason: str
    present_value: Decimal | None
    accumulated_depreciation: Decimal | None
    remaining_payment: Decimal | None
    unrecognized_finance: Decimal | None
    liability_balance: Decimal | None


class CoverageSummary(BaseModel):
    contract_count: int
    selected_count: int
    unknown_count: int
    reasons: list[str]


class CheckCounts(BaseModel):
    ok: int
    failed: int
    unknown: int


class ReconcileOut(BaseModel):
    lessee: str
    ledger: str
    period: str
    tolerance: Decimal
    status: CheckStatus
    status_label: str
    counts: CheckCounts
    subjects: list[SubjectResult]
    contracts: list[ContractCoverage]
    coverage: CoverageSummary
    generated_at: datetime


def status_label(status: str) -> str:
    return CHECK_STATUS_LABELS.get(status, status)
