"""finance_ledger_bip 模块 ORM 模型（表名统一 mod_ 前缀）。

金额列一律 Float 并在入库时保留两位小数（本模块定位是导入/查询/分析，
非记账引擎，不做分位级精度承诺）；凭证按（核算账簿, 凭证号）应用层去重。
"""
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ...database import Base


class BipBatch(Base):
    """BIP 导入批次：一次「确认入库」产生一条批次记录，原文件留档 inbox。"""

    __tablename__ = "mod_bip_batch"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    filename: Mapped[str] = mapped_column(String(255), default="")
    # 文件类型：voucher（凭证列表）/ aux（辅助明细账）/ balance（科目余额表）
    file_type: Mapped[str] = mapped_column(String(20), default="unknown")
    period: Mapped[str] = mapped_column(String(7), default="")  # 余额表导入期间 YYYY-MM，其余类型为空
    total_rows: Mapped[int] = mapped_column(Integer, default=0)
    inserted_vouchers: Mapped[int] = mapped_column(Integer, default=0)
    skipped_vouchers: Mapped[int] = mapped_column(Integer, default=0)  # 重复（核算账簿+凭证号）跳过数
    inserted_entries: Mapped[int] = mapped_column(Integer, default=0)
    inserted_balances: Mapped[int] = mapped_column(Integer, default=0)
    skipped_rows: Mapped[int] = mapped_column(Integer, default=0)  # 缺关键字段的无效行数
    warnings: Mapped[str] = mapped_column(Text, default="[]")  # JSON 字符串数组（如借贷不平衡）
    stored_path: Mapped[str] = mapped_column(String(500), default="")  # 原文件留档绝对路径
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class BipVoucher(Base):
    """凭证头：同一（核算账簿, 凭证号）在本模块内唯一（应用层保证）。"""

    __tablename__ = "mod_bip_voucher"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("mod_bip_batch.id"), index=True)
    period: Mapped[str] = mapped_column(String(7), index=True, default="")  # 由凭证日期推导 YYYY-MM
    voucher_date: Mapped[str] = mapped_column(String(10), default="")  # YYYY-MM-DD
    ledger: Mapped[str] = mapped_column(String(100), index=True, default="")  # 核算账簿
    voucher_no: Mapped[str] = mapped_column(String(50), index=True, default="")  # 凭证号
    voucher_full_no: Mapped[str] = mapped_column(String(100), default="")  # 会计凭证号（可空）
    summary: Mapped[str] = mapped_column(String(500), default="")  # 凭证摘要（取首条分录摘要）
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)

    entries: Mapped[list["BipEntry"]] = relationship(
        back_populates="voucher", cascade="all, delete-orphan", order_by="BipEntry.line_no"
    )


class BipEntry(Base):
    """凭证分录：借/贷两栏对称；凭证列表导入的分录无科目字段（为空串）。"""

    __tablename__ = "mod_bip_entry"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    voucher_id: Mapped[int] = mapped_column(ForeignKey("mod_bip_voucher.id"), index=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("mod_bip_batch.id"), index=True)
    line_no: Mapped[int] = mapped_column(Integer, default=0)  # 凭证内行号（从 1 起）
    summary: Mapped[str] = mapped_column(String(500), default="")
    subject_code: Mapped[str] = mapped_column(String(50), index=True, default="")
    subject_name: Mapped[str] = mapped_column(String(200), default="")
    debit: Mapped[float] = mapped_column(Float, default=0.0)
    credit: Mapped[float] = mapped_column(Float, default=0.0)
    source_type: Mapped[str] = mapped_column(String(20), default="")  # voucher / aux

    voucher: Mapped[BipVoucher] = relationship(back_populates="entries")


class BipBalance(Base):
    """科目余额表行：按导入批次 + 期间留存，金额缺失列按 0 处理。"""

    __tablename__ = "mod_bip_balance"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("mod_bip_batch.id"), index=True)
    period: Mapped[str] = mapped_column(String(7), index=True, default="")  # YYYY-MM（导入时指定）
    subject_code: Mapped[str] = mapped_column(String(50), index=True, default="")
    subject_name: Mapped[str] = mapped_column(String(200), default="")
    opening_debit: Mapped[float] = mapped_column(Float, default=0.0)
    opening_credit: Mapped[float] = mapped_column(Float, default=0.0)
    period_debit: Mapped[float] = mapped_column(Float, default=0.0)
    period_credit: Mapped[float] = mapped_column(Float, default=0.0)
    closing_debit: Mapped[float] = mapped_column(Float, default=0.0)
    closing_credit: Mapped[float] = mapped_column(Float, default=0.0)
