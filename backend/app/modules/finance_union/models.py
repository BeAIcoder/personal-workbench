# -*- coding: utf-8 -*-
"""finance_union 模块 ORM 模型（表名统一 ``mod_union_`` 前缀）。

两表边界：

- ``mod_union_batch``：计提批次，``(entity, period)`` 唯一，status 只有 draft/confirmed；
- ``mod_union_line``：批次内的部门行，``(batch_id, department)`` 唯一。

金额列一律 ``Numeric(18, 2)``，费率 ``Numeric(9, 6)``（0.020000 = 2%）。

三列金额输入（current_wage_base / prior_accrual / prior_actual）**允许为空**：
空值是「尚未录入」的事实，不是 0；计算与确认阶段按 unknown 处理，绝不按 0 通过。
计算派生值（本期计提 / 上期差异 / 本期建议入账）不落库——它们由
``calculation.py`` 按当前输入实时推导，避免出现「存储值与输入不一致」的第二事实源。

唯一约束用 ``Index(..., unique=True)`` 而非 ``UniqueConstraint``：键列均 NOT NULL，
可直接建立 SQLite 唯一索引；NULL 语义歧义在 schema 层已被拒绝。
"""
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, Index, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ...database import Base


class UnionBatch(Base):
    """工会经费计提批次：同一主体同一期间只允许一个批次。"""

    __tablename__ = "mod_union_batch"
    __table_args__ = (
        Index("uq_union_batch_entity_period", "entity", "period", unique=True),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    # 计提主体（公司/单位名称，测试只用合成值）
    entity: Mapped[str] = mapped_column(String(200), nullable=False)
    # 所属期间 YYYY-MM（严格校验在 schemas.StrictPeriod）
    period: Mapped[str] = mapped_column(String(7), nullable=False, index=True)
    # 计提率按小数存储（0.020000 = 2%）；请求必填，任何情况下都不暗中给默认值
    rate: Mapped[Decimal] = mapped_column(Numeric(9, 6), nullable=False)
    # 状态唯一事实源在 schemas.BATCH_STATUSES，这里只存值
    status: Mapped[str] = mapped_column(String(20), default="draft", nullable=False, index=True)
    remark: Mapped[str] = mapped_column(Text, default="", nullable=False)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, onupdate=datetime.now, nullable=False
    )


class UnionLine(Base):
    """部门行：一行 = 批次内一个部门的工资基数与上期预提/实际数。"""

    __tablename__ = "mod_union_line"
    __table_args__ = (
        Index("uq_union_line_batch_department", "batch_id", "department", unique=True),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    batch_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    department: Mapped[str] = mapped_column(String(100), nullable=False)
    # 本期工资基数（手工录入，不从 HR 取数）；None = 未录入
    current_wage_base: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    # 上期已计提；None = 未录入（不是 0）
    prior_accrual: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    # 上期实际数（工资总额口径，由使用者填入）；None = 未录入（不是 0）
    prior_actual: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, onupdate=datetime.now, nullable=False
    )
