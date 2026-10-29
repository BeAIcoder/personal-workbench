# -*- coding: utf-8 -*-
"""finance_iit 模块 ORM 模型。"""
from datetime import datetime

from sqlalchemy import JSON, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ...database import Base


class IitFiling(Base):
    """个税（工资薪金累计预扣）填报记录：年度 + 员工月度行 + 计算引擎结果。"""

    __tablename__ = "mod_iit_filing"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    # 所属年度（YYYY）
    year: Mapped[str] = mapped_column(String(4), index=True)
    # 扣缴义务人（企业名）
    taxpayer_name: Mapped[str] = mapped_column(String(200), default="")
    # 员工月度行 [{month, name, salary, social_insurance, special_additional, other_deductions}]
    rows: Mapped[list] = mapped_column(JSON, default=list)
    # 计算结果 {employees: [{name, months:[{month, taxable_income, monthly_withholding, cumulative_withholding}]}], totals}
    calc_result: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="draft", index=True)
    export_path: Mapped[str] = mapped_column(String(500), default="")
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)
