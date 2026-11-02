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
    download_name: str = ""  # 整批 ZIP 建议文件名（「发票汇总2026年10月.zip」）


class ExportResult(BaseModel):
    ok: bool
    batch_id: int
    status: str
    export_file: str
    zip_size: int
    projects: int
    project_zips: dict[str, str] = {}  # 项目名 → 单项目 ZIP 文件名（拆分打包）


class ProjectSummary(BaseModel):
    """项目汇总（批次详情「项目拆分」卡片）。"""

    project: str
    count: int
    amount: float  # 项目净额（冲红为负）
    manual: int = 0  # 其中人工修正过的张数
    zip_file: str = ""  # 单项目 ZIP 文件名（未导出为空）
    zip_ready: bool = False


class ReclassifyResult(BaseModel):
    """重新归类结果。"""

    ok: bool
    batch_id: int
    status: str
    changed: int
    unchanged: int
    manual_kept: int
    unclassified: int
    sources: dict[str, int] = {}
    projects: list[ProjectSummary] = []

