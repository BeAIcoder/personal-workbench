# -*- coding: utf-8 -*-
"""business_hr 模块 ORM 模型（表名统一 mod_ 前缀）。"""
from datetime import date, datetime

from sqlalchemy import Date, DateTime, Float, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ...database import Base


class HrEmployee(Base):
    """员工花名册。"""

    __tablename__ = "mod_hr_employee"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    emp_no: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(100), default="", index=True)
    department: Mapped[str] = mapped_column(String(100), default="", index=True)
    position: Mapped[str] = mapped_column(String(100), default="")
    hire_date: Mapped[date] = mapped_column(Date, index=True)
    probation_end: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    contract_end: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    # active 在职 / probation 试用 / left 离职
    status: Mapped[str] = mapped_column(String(20), default="active", index=True)
    phone: Mapped[str] = mapped_column(String(50), default="")
    base_salary: Mapped[float] = mapped_column(Float, default=0.0)
    # 社保基数：0 是合法值（不缴社保），"留空按基本工资"无法用 NOT NULL 数字列表达，
    # 因此用 social_base_set 区分「显式填写（含 0）」与「未填」；旧库该列为 NULL。
    social_base: Mapped[float] = mapped_column(Float, default=0.0)
    social_base_set: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # 个税专项附加扣除（元/月，员工申报口径，出薪时进入累计预扣）
    special_additional: Mapped[float] = mapped_column(Float, default=0.0)
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, onupdate=datetime.now
    )


class HrAttendance(Base):
    """月度考勤：按员工×月份唯一。"""

    __tablename__ = "mod_hr_attendance"
    __table_args__ = (
        UniqueConstraint("emp_id", "month", name="uq_hr_attendance_emp_month"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    emp_id: Mapped[int] = mapped_column(Integer, index=True)
    month: Mapped[str] = mapped_column(String(7), index=True)
    work_days: Mapped[float] = mapped_column(Float, default=21.75)
    actual_days: Mapped[float] = mapped_column(Float, default=21.75)
    leave_days: Mapped[float] = mapped_column(Float, default=0.0)
    overtime_hours: Mapped[float] = mapped_column(Float, default=0.0)
    late_count: Mapped[int] = mapped_column(Integer, default=0)
    note: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, onupdate=datetime.now
    )


class HrPayroll(Base):
    """月度薪资：按员工×月份唯一。"""

    __tablename__ = "mod_hr_payroll"
    __table_args__ = (
        UniqueConstraint("emp_id", "month", name="uq_hr_payroll_emp_month"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    emp_id: Mapped[int] = mapped_column(Integer, index=True)
    month: Mapped[str] = mapped_column(String(7), index=True)
    base_salary: Mapped[float] = mapped_column(Float, default=0.0)
    allowance: Mapped[float] = mapped_column(Float, default=0.0)
    overtime_pay: Mapped[float] = mapped_column(Float, default=0.0)
    deduction: Mapped[float] = mapped_column(Float, default=0.0)
    social_insurance: Mapped[float] = mapped_column(Float, default=0.0)
    housing_fund: Mapped[float] = mapped_column(Float, default=0.0)
    special_additional: Mapped[float] = mapped_column(Float, default=0.0)
    tax: Mapped[float] = mapped_column(Float, default=0.0)
    net_pay: Mapped[float] = mapped_column(Float, default=0.0)
    # draft / confirmed
    status: Mapped[str] = mapped_column(String(20), default="draft", index=True)
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, onupdate=datetime.now
    )
