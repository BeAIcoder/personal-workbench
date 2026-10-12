"""finance_expense 模块请求 / 响应模型。"""
from datetime import datetime

from pydantic import BaseModel, Field


class ExpenseRunOut(BaseModel):
    """一次分类执行的摘要信息。"""

    id: int
    original_filename: str
    total_rows: int
    matched_rows: int
    status: str
    duration_sec: float
    artifact_path: str
    created_at: datetime


class ExpenseRowOut(BaseModel):
    """分类明细行。"""

    id: int
    row_no: int
    summary: str
    amount: float
    category: str
    hit: bool


class CategoryStat(BaseModel):
    """按命中分类聚合的统计。"""

    category: str
    count: int
    amount: float


class RunListOut(BaseModel):
    total: int
    items: list[ExpenseRunOut]


class RunDetailOut(BaseModel):
    run: ExpenseRunOut
    stats: list[CategoryStat]
    rows: list[ExpenseRowOut]


class RulesSummaryOut(BaseModel):
    """分类规则摘要（规则权威源为引擎目录下的 classification_rules.json，只读引用）。"""

    engine_dir: str
    rules_file: str
    total_categories: int = Field(description="规则分类条数")
    total_keywords: int = Field(description="全部关键词条数")
    categories: list[str] = Field(description="分类名清单（顺序即匹配优先级）")


class RulesReloadOut(BaseModel):
    ok: bool
    summary: RulesSummaryOut
