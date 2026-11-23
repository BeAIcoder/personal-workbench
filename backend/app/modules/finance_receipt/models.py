"""finance_receipt 模块 ORM 模型（表名统一 mod_ 前缀）。"""
from datetime import date, datetime

from sqlalchemy import JSON, Boolean, Date, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ...database import Base


class ReceiptBatch(Base):
    """银行回单整理批次：一次账期扫描 + 逐笔复核 + 按账户类别生成产物。"""

    __tablename__ = "mod_receipt_batch"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    period_label: Mapped[str] = mapped_column(String(40), default="")
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    # 状态：scanned 已扫描待复核 / ok 已生成 / failed 生成失败
    status: Mapped[str] = mapped_column(String(20), default="scanned", index=True)
    source_dir: Mapped[str] = mapped_column(String(500), default="")
    output_dir: Mapped[str] = mapped_column(String(500), default="")
    failed_sources: Mapped[list] = mapped_column(JSON, default=list)
    warnings: Mapped[list] = mapped_column(JSON, default=list)
    duplicate_count: Mapped[int] = mapped_column(Integer, default=0)
    # 生成产物清单 [{id, name, kind, account, category, pages, count, size}]
    files: Mapped[list] = mapped_column(JSON, default=list)
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)


class ReceiptItem(Base):
    """批次内的一笔回单：分类、是否纳入、复核标记与来源定位。"""

    __tablename__ = "mod_receipt_item"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    batch_id: Mapped[int] = mapped_column(Integer, index=True)
    category: Mapped[str] = mapped_column(String(40), default="unclassified")
    auto_category: Mapped[str] = mapped_column(String(40), default="unclassified")
    reviewed: Mapped[bool] = mapped_column(Boolean, default=False)
    included: Mapped[bool] = mapped_column(Boolean, default=True)
    bank: Mapped[str] = mapped_column(String(20), default="")
    account_no: Mapped[str] = mapped_column(String(40), default="", index=True)
    rdate: Mapped[date | None] = mapped_column(Date, nullable=True)
    # 金额以两位小数字符串保存，避免 SQLite Decimal 适配问题
    amount: Mapped[str] = mapped_column(String(20), default="0.00")
    payer: Mapped[str] = mapped_column(String(200), default="")
    payee: Mapped[str] = mapped_column(String(200), default="")
    purpose: Mapped[str] = mapped_column(String(200), default="")
    receipt_no: Mapped[str] = mapped_column(String(80), default="")
    source_path: Mapped[str] = mapped_column(String(500), default="")
    source_member: Mapped[str] = mapped_column(String(255), default="")
    source_pdf: Mapped[str] = mapped_column(String(300), default="")
    page: Mapped[int] = mapped_column(Integer, default=0)
    clip: Mapped[list] = mapped_column(JSON, default=list)
