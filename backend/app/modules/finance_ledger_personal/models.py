"""finance_ledger_personal 模块 ORM 模型（表名统一 mod_ 前缀）。

数据模型参考开源记账项目 ezBookkeeping（MIT）的核心实体：
- 账户：现金 / 银行卡 / 电子钱包等，kind 可自定义扩展；
- 收支分类：两级结构（parent_id 为空为一级分类，如 餐饮>工作餐）；
- 交易：收/支、金额、日期、账户、分类（可空）、备注、标签（JSON）；
- 月度预算：按月份 + 分类维度（category_id 为空表示当月总预算）。
"""
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import JSON, Date, DateTime, ForeignKey, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ...database import Base


class PlAccount(Base):
    """账户表。"""

    __tablename__ = "mod_pl_account"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(50))
    kind: Mapped[str] = mapped_column(String(20), default="other")  # cash/bank/ewallet/other
    currency: Mapped[str] = mapped_column(String(8), default="CNY")
    sort: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class PlCategory(Base):
    """收支分类表（两级）。"""

    __tablename__ = "mod_pl_category"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(10), default="expense", index=True)  # expense/income
    name: Mapped[str] = mapped_column(String(50))
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("mod_pl_category.id"), nullable=True)
    sort: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class PlTransaction(Base):
    """交易流水表。"""

    __tablename__ = "mod_pl_transaction"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(10), index=True)  # expense/income
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    occurred_on: Mapped[date] = mapped_column(Date, index=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("mod_pl_account.id"), index=True)
    category_id: Mapped[int | None] = mapped_column(ForeignKey("mod_pl_category.id"), nullable=True, index=True)
    note: Mapped[str] = mapped_column(String(500), default="")
    tags: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)


class PlBudget(Base):
    """月度预算表：month + category_id 唯一（category_id 为空 = 当月总预算）。"""

    __tablename__ = "mod_pl_budget"
    __table_args__ = (UniqueConstraint("month", "category_id", name="uq_mod_pl_budget_month_cat"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    month: Mapped[str] = mapped_column(String(7), index=True)  # YYYY-MM
    category_id: Mapped[int | None] = mapped_column(ForeignKey("mod_pl_category.id"), nullable=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
