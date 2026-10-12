"""finance_invoice 模块请求 / 响应模型（Pydantic v2）。"""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class InvoiceItemOut(BaseModel):
    """发票明细输出（stored_path 为服务端内部路径，不对外暴露）。"""

    id: int
    batch_id: int
    source_file: str
    invoice_number: str
    check_code: str
    buyer_name: str
    seller_name: str
    amount: str
    tax_amount: str
    tax_rate: str
    invoice_date: str
    invoice_type: str
    invoice_direction: str
    project_name: str
    brand_name: str
    remark: str
    extraction_success: bool
    corrected: bool
    status: str
    paired_item_id: Optional[int] = None
    paired_invoice_number: str = ""  # 配对对方的发票号码（未配对为空）
    created_at: datetime
    updated_at: datetime


class InvoiceItemPatch(BaseModel):
    """人工修正：仅开放项目归类与备注（发票原始字段不允许改）。"""

    project_name: Optional[str] = Field(None, max_length=100, description="项目归类")
    remark: Optional[str] = Field(None, max_length=500, description="备注")


class InvoiceBatchOut(BaseModel):
    id: int
    name: str
    status: str
    file_count: int
    item_count: int
    parsed_count: int
    failed_count: int
    duplicate_count: int
    red_matched_count: int
    red_unmatched_count: int
    total_amount: float
    source_files: list[str]
    export_file: str
    error: str
    created_at: datetime
    updated_at: datetime


class InvoiceBatchDetail(InvoiceBatchOut):
    """批次详情：含全部明细。"""

    items: list[InvoiceItemOut] = []


class ExportResult(BaseModel):
    ok: bool
    batch_id: int
    status: str
    export_file: str
    zip_size: int
    projects: int
