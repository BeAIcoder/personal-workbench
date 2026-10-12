"""finance_ledger_bip 模块请求 / 响应模型。"""
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


# ---------------- 导入向导 ----------------

class PreviewOut(BaseModel):
    """预览响应：文件类型识别结果 + 列映射建议 + 前 50 行数据。"""

    token: str = Field(description="预览标识（确认入库时回传）")
    filename: str
    file_type: str = Field(description="voucher/aux/balance/unknown")
    type_label: str
    columns: list[str]
    mapping: dict[str, str] = Field(description="列映射建议 {规范字段: 实际列名}")
    required_fields: list[str] = Field(description="入库前必须完成映射的规范字段")
    row_count: int
    preview_rows: list[dict[str, Any]]


class ConfirmIn(BaseModel):
    """确认入库请求：token 必填；类型/映射/期间用于人工修正。"""

    token: str = Field(min_length=1)
    file_type: str | None = Field(default=None, description="人工指定类型 voucher/aux/balance")
    mapping: dict[str, str] | None = Field(default=None, description="人工列映射（覆盖自动建议）")
    period: str | None = Field(default=None, description="科目余额表必填，格式 YYYY-MM")


class ConfirmOut(BaseModel):
    batch_id: int
    file_type: str
    type_label: str
    total_rows: int
    inserted_vouchers: int
    skipped_vouchers: int
    inserted_entries: int
    inserted_balances: int
    skipped_rows: int
    warnings: list[str]
    message: str


class BatchOut(BaseModel):
    id: int
    filename: str
    file_type: str
    type_label: str
    period: str
    total_rows: int
    inserted_vouchers: int
    skipped_vouchers: int
    inserted_entries: int
    inserted_balances: int
    skipped_rows: int
    warnings: list[str]
    stored_path: str
    created_at: datetime


class BatchListOut(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[BatchOut]


# ---------------- 凭证与分录 ----------------

class VoucherListItem(BaseModel):
    id: int
    period: str
    voucher_date: str
    ledger: str
    voucher_no: str
    voucher_full_no: str
    summary: str
    entry_count: int
    debit_total: float
    credit_total: float
    balanced: bool


class VoucherListOut(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[VoucherListItem]


class EntryOut(BaseModel):
    id: int
    line_no: int
    summary: str
    subject_code: str
    subject_name: str
    debit: float
    credit: float
    source_type: str


class VoucherDetailOut(BaseModel):
    """凭证详情：分录借贷两栏对称 + 合计与平衡标记。"""

    id: int
    batch_id: int
    period: str
    voucher_date: str
    ledger: str
    voucher_no: str
    voucher_full_no: str
    summary: str
    debit_total: float
    credit_total: float
    balanced: bool
    entries: list[EntryOut]


class EntryListItem(BaseModel):
    id: int
    voucher_id: int
    period: str
    voucher_date: str
    ledger: str
    voucher_no: str
    line_no: int
    summary: str
    subject_code: str
    subject_name: str
    debit: float
    credit: float


class EntryListOut(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[EntryListItem]


# ---------------- 科目汇总与入仓 ----------------

class BalanceSummaryItem(BaseModel):
    subject_code: str
    subject_name: str
    entry_count: int
    debit_total: float
    credit_total: float


class BalanceSummaryOut(BaseModel):
    period: str
    items: list[BalanceSummaryItem]
    subject_count: int
    debit_total: float
    credit_total: float


class ExportIn(BaseModel):
    period: str | None = Field(default=None, description="只导出指定期间 YYYY-MM；空为全量")


class ExportOut(BaseModel):
    ok: bool
    table: str
    mode: str
    rows: int
    result: dict[str, Any]
    message: str
