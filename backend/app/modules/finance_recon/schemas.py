"""finance_recon 模块请求/响应模型与源数据枚举。

金额字段一律 Decimal（API 以两位小数字符串序列化，禁止 float）；日期
YYYY-MM-DD 由 Pydantic date 严格校验，非法值一律 4xx，不做静默回退。
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# 四类源数据 kind 唯一事实源（models/service/后续任务共用）
SOURCE_KINDS: tuple[str, ...] = ("writeoff", "claim", "arrear", "bill")
SourceKind = Literal["writeoff", "claim", "arrear", "bill"]
SOURCE_KIND_LABELS: dict[str, str] = {
    "writeoff": "核销",
    "claim": "认领",
    "arrear": "欠费",
    "bill": "账单",
}

BATCH_STATUSES: tuple[str, ...] = ("draft", "checking", "ready", "failed")
BatchStatus = Literal["draft", "checking", "ready", "failed"]
STATUS_LABELS: dict[str, str] = {
    "draft": "草稿",
    "checking": "检查中",
    "ready": "已就绪",
    "failed": "失败",
}


def _clean_scope_items(value: list[str]) -> list[str]:
    """合同范围项：修剪空白，空白项显式拒绝（不做静默丢弃）。"""
    cleaned: list[str] = []
    for item in value:
        text = item.strip()
        if not text:
            raise ValueError("合同范围不能包含空白项")
        cleaned.append(text)
    return cleaned


class ReconBatchCreate(BaseModel):
    """创建批次：name / as_of 必填；容差默认 0.01 并显式落库；范围可先空（check 前必须勾选）。"""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100)
    object_tag: str = Field(default="", max_length=100)
    as_of: date
    tolerance: Decimal = Field(default=Decimal("0.01"), ge=0, max_digits=18, decimal_places=2)
    contract_scope: list[str] = Field(default_factory=list, max_length=200)

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("批次名称不能为空白")
        return text

    @field_validator("object_tag")
    @classmethod
    def _strip_object_tag(cls, value: str) -> str:
        return value.strip()

    @field_validator("contract_scope")
    @classmethod
    def _scope_items_clean(cls, value: list[str]) -> list[str]:
        return _clean_scope_items(value)


class ReconBatchPatch(BaseModel):
    """部分更新：仅在 draft/failed；可维护名称、对账日、容差与合同范围（其余字段不可改）。"""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=100)
    as_of: date | None = None
    tolerance: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=2)
    contract_scope: list[str] | None = Field(default=None, max_length=200)

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = value.strip()
        if not text:
            raise ValueError("批次名称不能为空白")
        return text

    @field_validator("contract_scope")
    @classmethod
    def _scope_items_clean(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        return _clean_scope_items(value)


# ---------- Task 2：解析输出与四类详情 schema ----------


class ParseSourceError(ValueError):
    """源文件解析失败：消息必须带文件/工作表/行/单元格定位（路由映射 400）。"""


@dataclass
class NormalizedRecord:
    """四类源文件标准化事实行（解析器输出，由 importer 落 ReconRecord）。

    doc_amount_1/doc_amount_2 仅核销表的单据级应收/核销：只用于结构检查，
    不入事实表、不进 details JSON（计划 78 行）；details 是经对应 Pydantic
    schema 校验的非金额 JSON 细节（不含金额与完整账号）。
    """

    kind: str
    row_no: int = 0
    contract_no: str = ""
    doc_no: str = ""
    fee_type: str = ""
    fee_period_start: date | None = None
    fee_period_end: date | None = None
    occur_date: date | None = None
    due_date: date | None = None
    amount_1: Decimal | None = None
    amount_2: Decimal | None = None
    amount_3: Decimal | None = None
    amount_4: Decimal | None = None
    doc_amount_1: Decimal | None = None
    doc_amount_2: Decimal | None = None
    details: dict = field(default_factory=dict)


class _ReconDetailBase(BaseModel):
    """四类详情 JSON 的公共约束：禁止额外键（金额/账号混入即构造失败）。"""

    model_config = ConfigDict(extra="forbid")


def _detail_str(max_length: int = 200):
    return Field(default=None, max_length=max_length)


class WriteoffDetails(_ReconDetailBase):
    """核销明细非金额细节（四个事实金额与单据级金额一律不进本模型）。"""

    seq_no: str | None = _detail_str(64)
    region: str | None = _detail_str()
    project: str | None = _detail_str()
    contract_type: str | None = _detail_str(64)
    bill_month: str | None = _detail_str(32)
    brand: str | None = _detail_str()
    unit_no: str | None = _detail_str()
    writeoff_type: str | None = _detail_str(64)
    writeoff_date: date | None = None
    cancel_date: date | None = None
    tax_rate: str | None = _detail_str(32)
    prepaid_type: str | None = _detail_str(64)


class ClaimDetails(_ReconDetailBase):
    """认领记录非金额细节；账号只留 4 位尾号（完整账号不入库不入 JSON）。"""

    source: str | None = _detail_str(64)
    doc_type: str | None = _detail_str(64)
    sub_doc_no: str | None = _detail_str(80)
    transfer_id: str | None = _detail_str(80)
    biz_type: str | None = _detail_str(64)
    payer_name: str | None = _detail_str()
    payer_account_tail: str | None = _detail_str(16)
    payee_name: str | None = _detail_str()
    payee_account_tail: str | None = _detail_str(16)
    txn_type: str | None = _detail_str(64)
    purpose: str | None = _detail_str()
    txn_time: str | None = _detail_str(32)
    memo: str | None = _detail_str()
    status: str | None = _detail_str(64)
    claim_time: str | None = _detail_str(32)
    claimant: str | None = _detail_str(64)
    audit_time: str | None = _detail_str(32)
    auditor: str | None = _detail_str(64)


class ArrearDetails(_ReconDetailBase):
    """欠费明细非金额细节（合计/固定租金/综管费为事实金额栏，不进 JSON）。"""

    bill_month: str | None = _detail_str(32)


class BillDetails(_ReconDetailBase):
    """账单付款通知单非金额细节（累计应付为事实金额栏，不进 JSON）。"""

    bill_month: str | None = _detail_str(32)
    issue_date: date | None = None


# kind → 详情 schema 唯一映射（解析器构造 details 的唯一入口）
DETAIL_MODELS: dict[str, type[_ReconDetailBase]] = {
    "writeoff": WriteoffDetails,
    "claim": ClaimDetails,
    "arrear": ArrearDetails,
    "bill": BillDetails,
}


# ---------- Task 3：口径参数 / 匹配复核 / 问题复核 ----------

BILLING_METHODS: tuple[str, ...] = ("whole_month", "actual_days", "custom_lines")
BillingMethod = Literal["whole_month", "actual_days", "custom_lines"]
BILLING_METHOD_LABELS: dict[str, str] = {
    "whole_month": "整月计费",
    "actual_days": "按实际自然日",
    "custom_lines": "自定义明细",
}

DUE_RULES: tuple[str, ...] = ("source_due_date", "expense_month_day", "period_end_next_day")
DueRule = Literal["source_due_date", "expense_month_day", "period_end_next_day"]
DUE_RULE_LABELS: dict[str, str] = {
    "source_due_date": "源文件缴费截止日",
    "expense_month_day": "费用月指定日",
    "period_end_next_day": "费用期结束次日",
}

PENALTY_START_RULES: tuple[str, ...] = ("due_date", "period_end_next_day")
PenaltyStartRule = Literal["due_date", "period_end_next_day"]
PENALTY_START_RULE_LABELS: dict[str, str] = {
    "due_date": "缴费到期日",
    "period_end_next_day": "费用期结束次日",
}


def _money_field(**kwargs):
    """金额字段：Decimal + 两位上限；API/JSON 一律字符串序列化，禁止 float。"""
    return Field(default=None, max_digits=18, decimal_places=2, **kwargs)


class MonthlyFeeStandard(BaseModel):
    """月费标准：计费方式为 whole_month / actual_days 时的唯一费率来源（无内置默认）。"""

    model_config = ConfigDict(extra="forbid")

    fee_type: str = Field(min_length=1, max_length=64)
    monthly_amount: Decimal = Field(ge=0, max_digits=18, decimal_places=2)

    @field_validator("fee_type")
    @classmethod
    def _fee_type_not_blank(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("费用类型不能为空白")
        return text


class CustomLine(BaseModel):
    """自定义计费明细：重构口径为 custom_lines 时完全采用用户录入，引擎不做任何折算。"""

    model_config = ConfigDict(extra="forbid")

    fee_type: str = Field(min_length=1, max_length=64)
    period_start: date
    period_end: date
    amount: Decimal = Field(ge=0, max_digits=18, decimal_places=2)

    @field_validator("fee_type")
    @classmethod
    def _fee_type_not_blank(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("费用类型不能为空白")
        return text

    @model_validator(mode="after")
    def _period_order(self):
        if self.period_end < self.period_start:
            raise ValueError("自定义明细费用期结束早于开始")
        return self


class CreditItem(BaseModel):
    """抵扣项：只有 confirmed=True 的人工抵扣才进入结算草稿（机检绝不自动抵扣）。"""

    model_config = ConfigDict(extra="forbid")

    credit_type: str = Field(min_length=1, max_length=64)
    amount: Decimal = Field(ge=0, max_digits=18, decimal_places=2)
    note: str = Field(default="", max_length=200)
    confirmed: bool = False

    @field_validator("credit_type")
    @classmethod
    def _credit_type_not_blank(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("抵扣类型不能为空白")
        return text


class PenaltyConfig(BaseModel):
    """违约金参数：全量显式提供才测算；费率/起算规则无任何默认值（0.001/20 日绝不内置）。"""

    model_config = ConfigDict(extra="forbid")

    rate_per_day: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=8)
    start_rule: PenaltyStartRule | None = None
    cap: Decimal | None = _money_field()
    waived: bool = False


class AdditionalCharge(BaseModel):
    """一次性费用（保证金/燃气/水电等）：显式录入，各计费方式下均纳入合同重构。

    period_start/period_end 可只填其一或不填（不填=不随重构截止口径过滤，始终计入）。
    """

    model_config = ConfigDict(extra="forbid")

    fee_type: str = Field(min_length=1, max_length=64)
    amount: Decimal = Field(ge=0, max_digits=18, decimal_places=2)
    period_start: date | None = None
    period_end: date | None = None
    note: str = Field(default="", max_length=200)

    @field_validator("fee_type")
    @classmethod
    def _fee_type_not_blank(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("费用类型不能为空白")
        return text

    @model_validator(mode="after")
    def _period_order(self):
        if self.period_start is not None and self.period_end is not None and self.period_end < self.period_start:
            raise ValueError("一次性费用期间结束早于开始")
        return self


# 合同重构的时间截止口径（必填显式选择，引擎不擅自决定边界）
RECONSTRUCTION_CUTOFFS: tuple[str, ...] = ("through_as_of", "contract_end")
ReconstructionCutoff = Literal["through_as_of", "contract_end"]
RECONSTRUCTION_CUTOFF_LABELS: dict[str, str] = {
    "through_as_of": "截至对账日",
    "contract_end": "合同截止日（含撤场未到期费用）",
}


class ContractTerms(BaseModel):
    """每批次每合同的人工口径参数（GET/PUT terms 的固定 schema）。

    reconstruction_cutoff 必填：重构应收的时间边界由人工显式选择（through_as_of
    只算到对账日；contract_end 算完整合同期、支持撤场未到期费用），引擎不默认。
    其余字段可选：缺哪个参数，对应检查就 unknown 并给出中文原因；JSON 中金额经
    Decimal 以字符串序列化（pydantic v2 model_dump(mode="json")），绝不出现 float。
    """

    model_config = ConfigDict(extra="forbid")

    contract_start: date | None = None
    contract_end: date | None = None
    billing_method: BillingMethod | None = None
    reconstruction_cutoff: ReconstructionCutoff
    monthly_fees: list[MonthlyFeeStandard] = Field(default_factory=list, max_length=50)
    custom_lines: list[CustomLine] = Field(default_factory=list, max_length=200)
    additional_charges: list[AdditionalCharge] = Field(default_factory=list, max_length=100)
    credits: list[CreditItem] = Field(default_factory=list, max_length=50)
    due_rule: DueRule | None = None
    due_day: int | None = Field(default=None, ge=1, le=31)
    penalty: PenaltyConfig = Field(default_factory=PenaltyConfig)

    @model_validator(mode="after")
    def _contract_period_order(self):
        if self.contract_start is not None and self.contract_end is not None and self.contract_end < self.contract_start:
            raise ValueError("合同截止日早于起始日")
        return self


class TermsUpsert(BaseModel):
    """PUT /terms 请求体：contract_no 与 terms 必填（整体替换该合同的口径参数）。"""

    model_config = ConfigDict(extra="forbid")

    contract_no: str = Field(min_length=1, max_length=64)
    terms: ContractTerms

    @field_validator("contract_no")
    @classmethod
    def _contract_not_blank(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("合同号不能为空白")
        return text


MATCH_STATUSES: tuple[str, ...] = ("suggested", "confirmed", "rejected")
MatchStatus = Literal["suggested", "confirmed", "rejected"]
MATCH_STATUS_LABELS: dict[str, str] = {
    "suggested": "待确认",
    "confirmed": "已确认",
    "rejected": "已排除",
}


class MatchCreate(BaseModel):
    """人工新增认领↔核销匹配（suggested）：分摊金额必填且大于 0。"""

    model_config = ConfigDict(extra="forbid")

    claim_record_id: int
    writeoff_record_id: int
    allocated_amount: Decimal = Field(gt=0, max_digits=18, decimal_places=2)
    note: str = Field(default="", max_length=500)


class MatchPatch(BaseModel):
    """人工裁定匹配：suggested↔confirmed/rejected；confirmed/rejected 的分摊金额冻结。"""

    model_config = ConfigDict(extra="forbid")

    status: MatchStatus | None = None
    allocated_amount: Decimal | None = Field(default=None, gt=0, max_digits=18, decimal_places=2)
    note: str | None = Field(default=None, max_length=500)


ISSUE_STATUSES: tuple[str, ...] = ("pending", "confirmed", "dismissed", "resolved")
IssueStatus = Literal["pending", "confirmed", "dismissed", "resolved"]
ISSUE_STATUS_LABELS: dict[str, str] = {
    "pending": "待确认",
    "confirmed": "已确认",
    "dismissed": "已排除",
    "resolved": "已解除",
}
ISSUE_SEVERITIES: tuple[str, ...] = ("info", "warn", "error")
IssueSeverity = Literal["info", "warn", "error"]

# 机检问题类别中文标签（唯一事实源在 schemas：引擎与人工复核共用，避免相互导入）
CATEGORY_LABELS: dict[str, str] = {
    "bill_arrear_mismatch": "账单与欠费不一致",
    "receivable_mismatch": "应收推算不一致",
    "reconstruction_mismatch": "重构应收与事实不一致",
    "claim_unmatched": "认领未匹配",
    "claim_same_second": "同秒多笔认领",
    "claim_audit_inversion": "认领审核时间倒挂",
    "writeoff_zero_fee": "零金额核销行",
    "writeoff_missing_fee_type": "核销行缺费用类型",
    "prepaid_residual": "预收核销挂空",
    "claim_zero_amount": "零金额认领",
    "claim_missing_amount": "认领金额未填",
    "period_misaligned": "账单月与费用期错位",
    "arrear_unaged": "欠费行无法归账龄",
    "deposit_duplicate_writeoff": "保证金重复名义核销",
    "deposit_mismatch": "保证金核销与认领不一致",
}


def terms_completeness(terms: ContractTerms | None) -> tuple[bool, list[str]]:
    """口径参数完整性：(是否完整, 缺失项中文名)。

    计费方式分派所需参数集：whole_month / actual_days 需要合同起止 + 月费标准；
    custom_lines 只需要自定义明细（完全来自用户录入）。缺参 → 引擎对应检查
    unknown，绝不取默认值。
    """
    if terms is None:
        return False, ["未录入口径参数"]
    missing: list[str] = []
    if terms.billing_method is None:
        missing.append("计费方式")
    elif terms.billing_method in ("whole_month", "actual_days"):
        if terms.contract_start is None:
            missing.append("合同起始日")
        if terms.contract_end is None:
            missing.append("合同截止日")
        if not terms.monthly_fees:
            missing.append("月费标准")
    elif terms.billing_method == "custom_lines" and not terms.custom_lines:
        missing.append("自定义计费明细")
    return not missing, missing


class IssueCreate(BaseModel):
    """人工补记问题/待补资料（origin=human）：机检永不触碰 human 来源。"""

    model_config = ConfigDict(extra="forbid")

    category: str = Field(min_length=1, max_length=64)
    severity: IssueSeverity = "warn"
    note: str = Field(default="", max_length=2000)
    amount_impact: Decimal | None = _money_field()
    evidence: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("category")
    @classmethod
    def _category_not_blank(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("问题类别不能为空白")
        return text


class IssuePatch(BaseModel):
    """人工裁定问题：状态/说明/级别；转 confirmed/dismissed 必须给出说明。"""

    model_config = ConfigDict(extra="forbid")

    status: IssueStatus | None = None
    severity: IssueSeverity | None = None
    note: str | None = Field(default=None, max_length=2000)


# ---------- Task 3 引擎输出契约（数据类；置于叶子模块避免 engine↔anomalies 循环） ----------

CHECK_LABELS: dict[str, str] = {
    "bill_vs_arrear": "账单=欠费",
    "receivable_identity": "核销+欠费推算应收",
    "reconstruction": "合同应收重构",
    "penalty_reference": "违约金参考测算",
    "aging": "账龄分桶",
    "settlement": "结算草稿",
    "claim_matching": "认领覆盖",
    "deposit_reconciliation": "商户级保证金核对",
    "claim_same_second": "同秒多笔认领",
    "claim_audit_inversion": "认领审核时间倒挂",
    "writeoff_zero_fee": "零金额核销行",
    "writeoff_missing_fee_type": "核销行缺费用类型",
    "prepaid_residual": "预收核销挂空",
    "claim_zero_amount": "零金额认领",
    "claim_missing_amount": "认领金额未填",
    "period_misaligned": "账单月与费用期错位",
    "deposit_duplicate_writeoff": "保证金重复名义核销",
}


@dataclass
class CheckOutcome:
    """一条勾稽检查结论：state ∈ pass/diff/unknown/info，绝不虚构通过。"""

    key: str
    scope: str
    state: str
    message: str
    left: str | None = None
    right: str | None = None
    delta: str | None = None
    tolerance: str | None = None

    def as_dict(self) -> dict:
        return {
            "key": self.key,
            "label": CHECK_LABELS.get(self.key, self.key),
            "scope": self.scope,
            "state": self.state,
            "message": self.message,
            "left": self.left,
            "right": self.right,
            "delta": self.delta,
            "tolerance": self.tolerance,
        }


@dataclass
class IssueDraft:
    """机检问题草稿：指纹只含类别与 scope 键，不含金额或口径参数（计划 80 行）。"""

    category: str
    scope: str
    severity: str
    evidence: list[str] = field(default_factory=list)
    amount_impact: Decimal | None = None

    @property
    def fingerprint(self) -> str:
        raw = f"{self.category}|{self.scope}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


@dataclass
class ReconResult:
    """run_checks 输出：批次结论 + 检查明细 + 聚合 + 问题/匹配同步统计。"""

    batch_id: int
    status: str
    error_summary: str = ""
    needs_recheck: bool = False
    checks: list[dict] = field(default_factory=list)
    totals: dict = field(default_factory=dict)
    aging: dict = field(default_factory=dict)
    settlement: list[dict] = field(default_factory=list)
    reconstruction: list[dict] = field(default_factory=list)
    unknown_reasons: list[dict] = field(default_factory=list)
    issue_stats: dict = field(default_factory=dict)
    match_stats: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "batch_id": self.batch_id,
            "status": self.status,
            "status_label": STATUS_LABELS.get(self.status, self.status),
            "needs_recheck": self.needs_recheck,
            "error_summary": self.error_summary,
            "checks": self.checks,
            "totals": self.totals,
            "aging": self.aging,
            "settlement": self.settlement,
            "reconstruction": self.reconstruction,
            "unknown_reasons": self.unknown_reasons,
            "issue_stats": self.issue_stats,
            "match_stats": self.match_stats,
        }
