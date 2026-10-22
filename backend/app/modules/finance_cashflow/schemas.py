"""finance_cashflow 模块请求 / 响应模型。"""
from pydantic import BaseModel, Field


class CashflowRunCreate(BaseModel):
    actual_month: int = Field(ge=1, le=12, description="回填月份（1-12）")


class ArtifactOut(BaseModel):
    name: str
    size: int


class CashflowRunOut(BaseModel):
    id: int
    actual_month: int
    status: str
    input_files: list = []
    artifacts: list[ArtifactOut] = []
    error: str = ""
    elapsed_sec: float = 0.0
    log_tail: str = ""
    created_at: str = ""


class CashflowRunListOut(BaseModel):
    total: int
    page: int = 1
    page_size: int = 20
    items: list[CashflowRunOut]
