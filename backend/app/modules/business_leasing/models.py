# -*- coding: utf-8 -*-
"""business_leasing 模块 ORM 模型（表名统一 mod_ 前缀）。"""
from datetime import date, datetime

from sqlalchemy import Date, DateTime, Float, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ...database import Base


class LeasingUnit(Base):
    """铺位台账：招商的最小单元。"""

    __tablename__ = "mod_leasing_unit"
    __table_args__ = (
        UniqueConstraint("building", "floor", "unit_no", name="uq_leasing_unit"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    building: Mapped[str] = mapped_column(String(100), default="", index=True)
    floor: Mapped[str] = mapped_column(String(20), default="")
    unit_no: Mapped[str] = mapped_column(String(50), default="", index=True)
    # 建筑面积（㎡）
    area: Mapped[float] = mapped_column(Float, default=0.0)
    # 租金单价（元/㎡/月）与物业费单价（元/㎡/月）
    rent_price: Mapped[float] = mapped_column(Float, default=0.0)
    property_fee: Mapped[float] = mapped_column(Float, default=0.0)
    # vacant 空置 / negotiating 在谈 / reserved 已定 / leased 已租
    status: Mapped[str] = mapped_column(String(20), default="vacant", index=True)
    opened_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, onupdate=datetime.now
    )


class LeasingLead(Base):
    """招商线索：客户 + 当前阶段 + 下次跟进时间。"""

    __tablename__ = "mod_leasing_lead"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    company: Mapped[str] = mapped_column(String(200), default="", index=True)
    contact: Mapped[str] = mapped_column(String(100), default="")
    phone: Mapped[str] = mapped_column(String(50), default="")
    industry: Mapped[str] = mapped_column(String(100), default="")
    demand_area: Mapped[float] = mapped_column(Float, default=0.0)
    budget: Mapped[float] = mapped_column(Float, default=0.0)
    # lead 线索 / contact 已联系 / offer 已报价 / intent 意向 / signed 已签约 / lost 已流失
    stage: Mapped[str] = mapped_column(String(20), default="lead", index=True)
    unit_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # fixed 固定租金 / percentage 抽成租金 / guaranteed_percentage 保底+抽成
    rent_form: Mapped[str | None] = mapped_column(String(40), nullable=True)
    free_rent_months: Mapped[int | None] = mapped_column(Integer, nullable=True)
    escalation_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    fitout_subsidy: Mapped[float | None] = mapped_column(Float, nullable=True)
    lease_term_months: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # 释放时间：合同终止后解除铺位占用，但保留已签约历史（NULL = 仍在占用）
    released_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    owner: Mapped[str] = mapped_column(String(50), default="", index=True)
    next_action_at: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, onupdate=datetime.now
    )


class LeasingFollowup(Base):
    """跟进记录：一条线索可有多条，按时间倒序展示。"""

    __tablename__ = "mod_leasing_followup"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    lead_id: Mapped[int] = mapped_column(Integer, index=True)
    at: Mapped[date] = mapped_column(Date, default=date.today)
    channel: Mapped[str] = mapped_column(String(20), default="电话")
    content: Mapped[str] = mapped_column(Text, default="")
    result: Mapped[str] = mapped_column(String(100), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
