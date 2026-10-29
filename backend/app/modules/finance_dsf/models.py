# -*- coding: utf-8 -*-
"""finance_dsf 模块 ORM 模型。"""
from datetime import datetime

from sqlalchemy import JSON, DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ...database import Base


class DsfFiling(Base):
    """残保金填报记录。"""

    __tablename__ = "mod_dsf_filing"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    # 缴费所属年度（YYYY，指上年度数据对应的申报年度）
    year: Mapped[str] = mapped_column(String(4), index=True)
    taxpayer_name: Mapped[str] = mapped_column(String(200), default="")
    # 输入 {total_employees, total_salary, disabled_employees, ratio}
    inputs: Mapped[dict] = mapped_column(JSON, default=dict)
    calc_result: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="draft", index=True)
    export_path: Mapped[str] = mapped_column(String(500), default="")
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)
