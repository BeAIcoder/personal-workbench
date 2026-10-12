"""finance_invoice 模块 ORM 模型（表名统一 mod_ 前缀）。

- mod_invoice_batch：上传批次，状态机 uploaded → parsed → exported；
- mod_invoice_item：发票明细，paired_item_id 为冲红配对指针（红票 ↔ 原票，双向互指）。
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ...database import Base


class InvoiceBatch(Base):
    """发票上传批次。"""

    __tablename__ = "mod_invoice_batch"

    STATUS_UPLOADED = "uploaded"
    STATUS_PARSED = "parsed"
    STATUS_EXPORTED = "exported"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), default="")
    status: Mapped[str] = mapped_column(String(20), default=STATUS_UPLOADED, index=True)
    file_count: Mapped[int] = mapped_column(Integer, default=0)  # 入库 PDF 数（含 ZIP 解出的）
    item_count: Mapped[int] = mapped_column(Integer, default=0)  # 明细总数（含失败/重复）
    parsed_count: Mapped[int] = mapped_column(Integer, default=0)  # 有效明细数（parsed + incomplete）
    failed_count: Mapped[int] = mapped_column(Integer, default=0)
    duplicate_count: Mapped[int] = mapped_column(Integer, default=0)
    red_matched_count: Mapped[int] = mapped_column(Integer, default=0)  # 冲红成功配对的对数
    red_unmatched_count: Mapped[int] = mapped_column(Integer, default=0)  # 未找到原票的冲红张数
    total_amount: Mapped[float] = mapped_column(Float, default=0.0)  # 有效发票净额（冲红为负）
    source_files: Mapped[str] = mapped_column(Text, default="[]")  # 原始上传文件名 JSON 数组
    export_file: Mapped[str] = mapped_column(String(500), default="")  # 导出 ZIP 文件名（位于本模块 outbox）
    error: Mapped[str] = mapped_column(String(500), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)


class InvoiceItem(Base):
    """发票明细（一张发票一行）。"""

    __tablename__ = "mod_invoice_item"

    STATUS_PARSED = "parsed"  # 解析成功
    STATUS_INCOMPLETE = "incomplete"  # 提取不完整（核心字段回退默认值）
    STATUS_DUPLICATE = "duplicate"  # 重复发票（发票号码+校验码幂等，批内 + 跨批次）
    STATUS_FAILED = "failed"  # 解析失败（PDF 损坏/无文本等）

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("mod_invoice_batch.id"), index=True)
    source_file: Mapped[str] = mapped_column(String(255), default="")  # 原始文件名（ZIP 内成员带原路径）
    stored_path: Mapped[str] = mapped_column(String(500), default="")  # 入库 PDF 的服务端绝对路径
    invoice_number: Mapped[str] = mapped_column(String(50), default="", index=True)
    check_code: Mapped[str] = mapped_column(String(50), default="")
    buyer_name: Mapped[str] = mapped_column(String(200), default="")
    seller_name: Mapped[str] = mapped_column(String(200), default="")  # 引擎不解析销方，由本模块补齐
    amount: Mapped[str] = mapped_column(String(50), default="")  # 价税合计原文（冲红为负数）
    tax_amount: Mapped[str] = mapped_column(String(50), default="")
    tax_rate: Mapped[str] = mapped_column(String(20), default="")
    invoice_date: Mapped[str] = mapped_column(String(30), default="")
    invoice_type: Mapped[str] = mapped_column(String(10), default="")  # 专票 / 普票 / 未知
    invoice_direction: Mapped[str] = mapped_column(String(10), default="")  # 正数发票 / 冲红发票
    project_name: Mapped[str] = mapped_column(String(100), default="")
    brand_name: Mapped[str] = mapped_column(String(50), default="")
    remark: Mapped[str] = mapped_column(String(500), default="")
    extraction_success: Mapped[bool] = mapped_column(Boolean, default=True)
    corrected: Mapped[bool] = mapped_column(Boolean, default=False)  # 经人工修正（PATCH）
    status: Mapped[str] = mapped_column(String(20), default=STATUS_PARSED, index=True)
    paired_item_id: Mapped[Optional[int]] = mapped_column(ForeignKey("mod_invoice_item.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)
