# -*- coding: utf-8 -*-
"""finance_vat 模块 ORM 模型。"""
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ...database import Base


class VatFiling(Base):
    """增值税填报记录。"""

    __tablename__ = "mod_vat_filing"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    # 所属期：YYYY-MM（一般纳税人按月，小规模按季末月）
    period: Mapped[str] = mapped_column(String(7), index=True, nullable=False)
    # 纳税人类型：general / small_scale
    taxpayer_type: Mapped[str] = mapped_column(String(20), default="general")
    taxpayer_name: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    # 输入快照（两种模式字段不同，存 JSON）
    inputs: Mapped[dict] = mapped_column(JSON, default=dict)
    # 计算结果
    calc_result: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="draft", index=True)
    export_path: Mapped[str] = mapped_column(String(500), default="")
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)

    # 同一纳税人同一所属期只允许一条申报（应用层 409 + 数据库唯一索引双保险）。
    # 用 Index(unique=True) 而非 UniqueConstraint：宿主的 _ensure_indexes_on 会给历史库
    # 幂等补「缺失索引」，但不会补表级约束（BUGfix R11-008）。
    # SQLite 唯一索引把 NULL 视为互不相等，故 taxpayer_name/period 显式 NOT NULL。
    __table_args__ = (
        Index("uq_vat_filing_taxpayer_period", "taxpayer_name", "period", unique=True),
    )
