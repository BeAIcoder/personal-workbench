"""finance_ledger_personal 模块请求 / 响应模型。"""
import re
from datetime import date

from pydantic import BaseModel, ConfigDict, Field, field_validator

# 月份格式 YYYY-MM（01-12）
MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
# Numeric(14, 2) 上限：12 位整数 + 2 位小数
MAX_AMOUNT = 999999999999.99


def round_money(cls, v: float) -> float:
    """金额统一保留两位小数；四舍五入后必须仍为正数。"""
    r = round(float(v), 2)
    if r <= 0:
        raise ValueError("金额必须大于 0（最多两位小数）")
    return r


def clean_tags(cls, v: list | None) -> list[str]:
    """标签清洗：去空白、单个截断 20 字、最多 10 个。"""
    out: list[str] = []
    for t in v or []:
        s = str(t).strip()[:20]
        if s and s not in out:
            out.append(s)
    return out[:10]


class _StripModel(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)


# ---------------- 账户 ----------------

class AccountCreate(_StripModel):
    name: str = Field(min_length=1, max_length=50, description="账户名称")
    kind: str = Field(default="other", max_length=20, description="账户类型：cash/bank/ewallet/other")
    sort: int = Field(default=0, description="排序值，越小越靠前")


class AccountUpdate(_StripModel):
    name: str | None = Field(default=None, min_length=1, max_length=50)
    kind: str | None = Field(default=None, max_length=20)
    sort: int | None = None


class AccountOut(BaseModel):
    id: int
    name: str
    kind: str
    currency: str
    sort: int

    model_config = ConfigDict(from_attributes=True)


# ---------------- 分类 ----------------

class CategoryCreate(_StripModel):
    kind: str = Field(pattern="^(expense|income)$", description="expense=支出 / income=收入")
    name: str = Field(min_length=1, max_length=50)
    parent_id: int | None = Field(default=None, description="父分类 id；为空为一级分类")
    sort: int = 0


class CategoryUpdate(_StripModel):
    name: str | None = Field(default=None, min_length=1, max_length=50)
    sort: int | None = None


class CategoryOut(BaseModel):
    id: int
    kind: str
    name: str
    parent_id: int | None
    sort: int

    model_config = ConfigDict(from_attributes=True)


# ---------------- 交易 ----------------

class TransactionCreate(_StripModel):
    kind: str = Field(pattern="^(expense|income)$")
    amount: float = Field(gt=0, le=MAX_AMOUNT)
    occurred_on: date = Field(description="发生日期 YYYY-MM-DD")
    account_id: int
    category_id: int | None = None
    note: str = Field(default="", max_length=500)
    tags: list[str] = Field(default_factory=list)

    _check_amount = field_validator("amount")(round_money)
    _check_tags = field_validator("tags")(clean_tags)


class TransactionUpdate(_StripModel):
    kind: str | None = Field(default=None, pattern="^(expense|income)$")
    amount: float | None = Field(default=None, gt=0, le=MAX_AMOUNT)
    occurred_on: date | None = None
    account_id: int | None = None
    category_id: int | None = None
    note: str | None = Field(default=None, max_length=500)
    tags: list[str] | None = None

    _check_amount = field_validator("amount")(round_money)
    _check_tags = field_validator("tags")(clean_tags)


class TransactionOut(BaseModel):
    id: int
    kind: str
    amount: float
    occurred_on: date
    account_id: int
    account_name: str
    category_id: int | None
    category_name: str
    note: str
    tags: list[str]


class TransactionListOut(BaseModel):
    total: int
    items: list[TransactionOut]


# ---------------- 统计 / 预算 ----------------

class CategoryStat(BaseModel):
    category_id: int | None
    name: str
    kind: str
    amount: float
    percent: float


class MonthlyStatsOut(BaseModel):
    month: str
    income: float
    expense: float
    balance: float
    by_category: list[CategoryStat]


class TrendItem(BaseModel):
    month: str
    income: float
    expense: float
    balance: float


class BudgetItemIn(BaseModel):
    category_id: int | None = Field(default=None, description="一级支出分类 id；空 = 当月总预算")
    amount: float = Field(ge=0, le=MAX_AMOUNT)


class BudgetPut(BaseModel):
    month: str = Field(pattern=MONTH_RE.pattern)
    items: list[BudgetItemIn] = Field(default_factory=list)


class BudgetItemOut(BaseModel):
    category_id: int | None
    name: str
    amount: float
    spent: float
    percent: float


class BudgetOut(BaseModel):
    month: str
    items: list[BudgetItemOut]


class ImportResult(BaseModel):
    imported: int
    skipped: int
    errors: list[str]
    created_accounts: list[str]
    created_categories: list[str]
