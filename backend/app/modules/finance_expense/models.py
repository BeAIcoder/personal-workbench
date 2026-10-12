"""finance_expense 模块 ORM 模型（表名统一 mod_ 前缀）。"""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from ...database import Base


class ExpenseRun(Base):
    """一次费用分类执行记录。"""

    __tablename__ = "mod_expense_run"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    original_filename: Mapped[str] = mapped_column(String(300), default="")
    total_rows: Mapped[int] = mapped_column(Integer, default=0)
    matched_rows: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="running")  # running / success / failed
    duration_sec: Mapped[float] = mapped_column(Float, default=0.0)
    artifact_path: Mapped[str] = mapped_column(String(500), default="")  # 相对财务数据目录的路径
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class ExpenseRow(Base):
    """费用分类明细行（含未命中行，便于规则迭代与人工归口）。"""

    __tablename__ = "mod_expense_row"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(Integer, index=True)
    row_no: Mapped[int] = mapped_column(Integer, default=0)
    summary: Mapped[str] = mapped_column(String(500), default="")
    amount: Mapped[float] = mapped_column(Float, default=0.0)
    category: Mapped[str] = mapped_column(String(50), default="未分类")
    hit: Mapped[bool] = mapped_column(Boolean, default=False)
