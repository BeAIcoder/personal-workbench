# -*- coding: utf-8 -*-
"""business_planning 模块 ORM 模型（表名统一 mod_ 前缀）。"""
from datetime import date, datetime

from sqlalchemy import JSON, Date, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ...database import Base


class PlanCampaign(Base):
    """企划活动：预算明细与复盘指标用 JSON 存放，避免过度建表。"""

    __tablename__ = "mod_plan_campaign"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200), default="", index=True)
    year: Mapped[int] = mapped_column(Integer, index=True)
    # festival 节庆 / promo 促销 / brand 品牌 / community 社区
    category: Mapped[str] = mapped_column(String(20), default="promo", index=True)
    start_date: Mapped[date] = mapped_column(Date, index=True)
    end_date: Mapped[date] = mapped_column(Date, index=True)
    owner: Mapped[str] = mapped_column(String(50), default="", index=True)
    channel: Mapped[str] = mapped_column(String(100), default="")
    target: Mapped[str] = mapped_column(String(300), default="")
    # planning / approved / running / done / cancelled
    status: Mapped[str] = mapped_column(String(20), default="planning", index=True)
    # [{item, budget, actual}]
    budget_items: Mapped[list] = mapped_column(JSON, default=list)
    budget_total: Mapped[float] = mapped_column(Float, default=0.0)
    actual_total: Mapped[float] = mapped_column(Float, default=0.0)
    # {footfall_lift, sales_lift, roi, review}
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    grade: Mapped[str | None] = mapped_column(String(20), nullable=True)
    sales_baseline: Mapped[float | None] = mapped_column(Float, nullable=True)
    sales_during: Mapped[float | None] = mapped_column(Float, nullable=True)
    merchant_share: Mapped[float | None] = mapped_column(Float, nullable=True)
    roi_target: Mapped[float | None] = mapped_column(Float, nullable=True)
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, onupdate=datetime.now
    )


class PlanVendor(Base):
    """企划供应商（物料/美陈/演艺/投放/执行）。"""

    __tablename__ = "mod_plan_vendor"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200), default="", index=True)
    service: Mapped[str] = mapped_column(String(30), default="执行", index=True)
    contact: Mapped[str] = mapped_column(String(100), default="")
    phone: Mapped[str] = mapped_column(String(50), default="")
    rating: Mapped[int] = mapped_column(Integer, default=3)
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
