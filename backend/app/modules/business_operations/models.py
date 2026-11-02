# -*- coding: utf-8 -*-
"""business_operations 模块 ORM 模型（表名统一 mod_ 前缀）。"""
from datetime import date, datetime

from sqlalchemy import Date, DateTime, Float, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ...database import Base


class OpsContract(Base):
    """租赁合同台账。"""

    __tablename__ = "mod_ops_contract"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    contract_no: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    tenant: Mapped[str] = mapped_column(String(200), default="", index=True)
    unit_no: Mapped[str] = mapped_column(String(50), default="", index=True)
    # 勾稽：关联招商铺位 mod_leasing_unit.id；为空表示未关联（纯台账）
    unit_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    area: Mapped[float] = mapped_column(Float, default=0.0)
    start_date: Mapped[date] = mapped_column(Date, index=True)
    end_date: Mapped[date] = mapped_column(Date, index=True)
    rent_monthly: Mapped[float] = mapped_column(Float, default=0.0)
    property_fee_monthly: Mapped[float] = mapped_column(Float, default=0.0)
    deposit: Mapped[float] = mapped_column(Float, default=0.0)
    # active 履约中 / terminated 已终止
    status: Mapped[str] = mapped_column(String(20), default="active", index=True)
    signed_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, onupdate=datetime.now
    )


class OpsBill(Base):
    """租金/物业费账单：按合同×期间唯一。"""

    __tablename__ = "mod_ops_bill"
    __table_args__ = (
        UniqueConstraint("contract_id", "period", name="uq_ops_bill_contract_period"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    contract_id: Mapped[int] = mapped_column(Integer, index=True)
    period: Mapped[str] = mapped_column(String(7), index=True)
    rent_amount: Mapped[float] = mapped_column(Float, default=0.0)
    property_amount: Mapped[float] = mapped_column(Float, default=0.0)
    other_amount: Mapped[float] = mapped_column(Float, default=0.0)
    total_amount: Mapped[float] = mapped_column(Float, default=0.0)
    paid_amount: Mapped[float] = mapped_column(Float, default=0.0)
    # unpaid / partial / paid
    status: Mapped[str] = mapped_column(String(20), default="unpaid", index=True)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    paid_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, onupdate=datetime.now
    )


class OpsWorkOrder(Base):
    """报修/投诉/巡场工单。"""

    __tablename__ = "mod_ops_work_order"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(30), default="", index=True)
    # repair 报修 / complaint 投诉 / inspection 巡场
    type: Mapped[str] = mapped_column(String(20), default="repair", index=True)
    location: Mapped[str] = mapped_column(String(100), default="")
    tenant: Mapped[str] = mapped_column(String(200), default="")
    priority: Mapped[str] = mapped_column(String(10), default="medium", index=True)
    # open / processing / done / closed
    status: Mapped[str] = mapped_column(String(20), default="open", index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    assignee: Mapped[str] = mapped_column(String(50), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, index=True)
    due_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    parent_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    equipment_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    contract_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class OpsWorkOrderFollowup(Base):
    """工单处理留痕。"""

    __tablename__ = "mod_ops_work_order_followup"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    work_order_id: Mapped[int] = mapped_column(Integer, index=True)
    handled_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, index=True)
    content: Mapped[str] = mapped_column(Text, default="")
    result: Mapped[str] = mapped_column(Text, default="")
    satisfaction: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
