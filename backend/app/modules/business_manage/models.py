# -*- coding: utf-8 -*-
"""business_manage 模块 ORM 模型（表名统一 mod_ 前缀）。

四张表承载四类经营报表（内控管理 / 项目周报 / 月度工作情况）：
- mod_mg_report：报表批次（类型 + 项目 + 期间）；
- mod_mg_metric：指标事实（报表 × section × metric_key 唯一）；
- mod_mg_record：明细行（诉讼、预警商户、重点工作、责任书指标等）；
- mod_mg_text：叙述块（长文本小节）。
"""
from datetime import date, datetime

from sqlalchemy import Date, DateTime, Float, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ...database import Base


class MgReport(Base):
    """报表批次：同类型 + 同项目 + 同周期起始日只保留一批（重复导入整批覆盖）。

    唯一约束用独立唯一索引表达，而不是 Table 级 UniqueConstraint：老库由启动迁移
    `_ensure_indexes_on` 按 `table.indexes` 补建；UniqueConstraint 不会迁移到已存在的表。
    """

    __tablename__ = "mod_mg_report"
    __table_args__ = (
        Index("uq_mg_report_scope", "report_type", "project", "period_start", unique=True),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    # control 内控管理 / weekly 项目周报 / monthly 月度工作情况
    report_type: Mapped[str] = mapped_column(String(20), index=True)
    project: Mapped[str] = mapped_column(String(100), default="", index=True)
    period: Mapped[str] = mapped_column(String(7), index=True)
    period_start: Mapped[date] = mapped_column(Date, index=True)
    period_end: Mapped[date] = mapped_column(Date)
    title: Mapped[str] = mapped_column(String(200), default="")
    source_file: Mapped[str] = mapped_column(String(255), default="")
    imported_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, onupdate=datetime.now
    )
    note: Mapped[str] = mapped_column(Text, default="")


class MgMetric(Base):
    """指标事实：一个报表的一条指标（数值或文本）。"""

    __tablename__ = "mod_mg_metric"
    __table_args__ = (
        Index("ix_mg_metric_report_section", "report_id", "section"),
        Index("ix_mg_metric_key", "metric_key"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    report_id: Mapped[int] = mapped_column(Integer, index=True)
    section: Mapped[str] = mapped_column(String(40), default="")
    metric_key: Mapped[str] = mapped_column(String(200))
    label: Mapped[str] = mapped_column(String(200), default="")
    value_num: Mapped[float | None] = mapped_column(Float, nullable=True)
    value_text: Mapped[str] = mapped_column(Text, default="")
    unit: Mapped[str] = mapped_column(String(20), default="")
    extra: Mapped[str] = mapped_column(Text, default="{}")
    sort: Mapped[int] = mapped_column(Integer, default=0)


class MgRecord(Base):
    """明细行：一份宽表覆盖内控/周报的表格型数据。"""

    __tablename__ = "mod_mg_record"
    __table_args__ = (Index("ix_mg_record_report_section", "report_id", "section"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    report_id: Mapped[int] = mapped_column(Integer, index=True)
    section: Mapped[str] = mapped_column(String(40), default="")
    seq: Mapped[int] = mapped_column(Integer, default=0)
    name: Mapped[str] = mapped_column(String(200), default="")
    category: Mapped[str] = mapped_column(String(100), default="")
    stage: Mapped[str] = mapped_column(String(50), default="")
    status: Mapped[str] = mapped_column(String(20), default="")
    area: Mapped[float | None] = mapped_column(Float, nullable=True)
    amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    amount2: Mapped[float | None] = mapped_column(Float, nullable=True)
    recovery: Mapped[float | None] = mapped_column(Float, nullable=True)
    balance: Mapped[float | None] = mapped_column(Float, nullable=True)
    rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    occurred_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    due_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    owner: Mapped[str] = mapped_column(String(100), default="")
    co_owner: Mapped[str] = mapped_column(String(100), default="")
    content: Mapped[str] = mapped_column(Text, default="")
    progress: Mapped[str] = mapped_column(Text, default="")
    note: Mapped[str] = mapped_column(Text, default="")
    payload: Mapped[str] = mapped_column(Text, default="{}")
    sort: Mapped[int] = mapped_column(Integer, default=0)


class MgText(Base):
    """叙述块：section 即语义键（gap_analysis / key_issues / issues …）。"""

    __tablename__ = "mod_mg_text"
    __table_args__ = (Index("ix_mg_text_report_section", "report_id", "section"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    report_id: Mapped[int] = mapped_column(Integer, index=True)
    section: Mapped[str] = mapped_column(String(40), default="")
    subsection: Mapped[str] = mapped_column(String(100), default="")
    content: Mapped[str] = mapped_column(Text, default="")
    sort: Mapped[int] = mapped_column(Integer, default=0)
