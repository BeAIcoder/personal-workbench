# -*- coding: utf-8 -*-
"""finance_stamp_duty 模块 ORM 模型（表名 mod_ 前缀）。"""
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ...database import Base


class StampDutyFiling(Base):
    """印花税填报记录：所属期 + 税目明细 + 计算结果 + 导出产物。"""

    __tablename__ = "mod_stamp_duty_filing"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    # 所属期：YYYY-MM（按季申报填季末月，如 2026-09；按次填次月）
    period: Mapped[str] = mapped_column(String(7), index=True, nullable=False)
    # 纳税人标识（企业名/姓名，脱敏数据用合成值）
    taxpayer_name: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    # 是否适用六税两费减半（小微/小规模纳税人/个体工商户）
    half_discount: Mapped[bool] = mapped_column(Boolean, default=False)
    # 税目明细 [{tax_item, amount, rate, exempt}]（rate 为千分比小数）
    items: Mapped[list] = mapped_column(JSON, default=list)
    # 计算结果 {total_tax, half_saved, policy_status, warnings, ...}
    calc_result: Mapped[dict] = mapped_column(JSON, default=dict)
    # 状态：draft / calculated / exported
    status: Mapped[str] = mapped_column(String(20), default="draft", index=True)
    # 导出产物相对路径（service 内部目录）
    export_path: Mapped[str] = mapped_column(String(500), default="")
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)

    # 同一纳税人同一所属期一条申报（应用层 409 + 数据库唯一索引；键列 NOT NULL 以避开
    # SQLite 唯一索引不约束 NULL 的语义，BUGfix R11-008）。
    __table_args__ = (
        Index("uq_stamp_duty_filing_taxpayer_period", "taxpayer_name", "period", unique=True),
    )
