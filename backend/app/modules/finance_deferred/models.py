"""finance_deferred 模块 ORM 模型（表名统一 mod_deferred_ 前缀）。

三表边界：

- ``mod_deferred_item``：项目主档，项目编码唯一，status 仅 draft/archived；
- ``mod_deferred_run``：成功测算批次，``(item_id, input_hash, rule_version)`` 唯一，
  只保存完整成功结果（汇总 + 校验 + 输入快照），calculated → confirmed 单向；
- ``mod_deferred_period``：月份级摊销行，``(run_id, period_month)`` 唯一，
  逐月保存月摊金额、累计摊销、余额与剩余月数（末期尾差如实留在余额，不吸差）。

金额列一律 ``Numeric(18, 2)``；税率按小数存储（0.0600 = 6%）。
失败测算不允许落半套结果：测算行与其期间行在同一事务提交。
"""
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from ...database import Base


class DeferredItem(Base):
    """长期待摊项目：仅租入资产改良支出，项目编码唯一，归档后只读。"""

    __tablename__ = "mod_deferred_item"
    __table_args__ = (UniqueConstraint("project_code", name="uq_deferred_item_code"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_code: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(200), default="")
    project: Mapped[str] = mapped_column(String(200), default="")
    # 含税金额：源合同/发票口径，逐月摊销的起点
    amount_incl_tax: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0"))
    # 税率按小数存储（0.0600 = 6%）
    tax_rate: Mapped[Decimal] = mapped_column(Numeric(6, 4), default=Decimal("0"))
    # 净额与税额是含税金额与税率的派生值，入库只为查询与展示；口径由 calculation 固化
    amount_net: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0"))
    tax_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0"))
    # YYYY-MM（严格校验由 schema 完成）
    start_month: Mapped[str] = mapped_column(String(7))
    months: Mapped[int] = mapped_column(Integer, default=1)
    lease_end_date: Mapped[date] = mapped_column(Date)
    remark: Mapped[str] = mapped_column(Text, default="")
    # 状态唯一事实源在 schemas.ITEM_STATUSES；这里只存值
    status: Mapped[str] = mapped_column(String(20), default="draft", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)


class DeferredRun(Base):
    """成功测算批次：输入哈希 + 规则版本 + 汇总 + 校验 + 风险确认。

    status 仅 calculated/confirmed，合法转移只有 calculated → confirmed；
    已确认快照不再提供任何写入口，同一输入重算幂等返回既有行。
    """

    __tablename__ = "mod_deferred_run"
    __table_args__ = (
        UniqueConstraint("item_id", "input_hash", "rule_version", name="uq_deferred_run_input"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    item_id: Mapped[int] = mapped_column(Integer, index=True)
    input_hash: Mapped[str] = mapped_column(String(64))
    rule_version: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20), default="calculated", index=True)
    # 测算时点的项目参数快照（金额/日期以字符串保存，禁止 float）
    input_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
    checks: Mapped[list] = mapped_column(JSON, default=list)
    # 风险警告（摊销超租赁到期日、末期尾差）的显式确认留痕；未确认时为假
    risk_acknowledged: Mapped[bool] = mapped_column(Boolean, default=False)
    risk_note: Mapped[str] = mapped_column(Text, default="")
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)


class DeferredPeriod(Base):
    """月份级摊销行：一行 = 一个摊销月份（按批次与月份唯一）。"""

    __tablename__ = "mod_deferred_period"
    __table_args__ = (
        UniqueConstraint("run_id", "period_month", name="uq_deferred_period_month"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(Integer, index=True)
    # YYYY-MM
    period_month: Mapped[str] = mapped_column(String(7))
    sequence: Mapped[int] = mapped_column(Integer)
    monthly_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0"))
    cumulative: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0"))
    # 余额 = 净额 − 累计摊销；末期尾差如实留在这里，不吸差、不自动转销
    balance: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0"))
    remaining_months: Mapped[int] = mapped_column(Integer, default=0)
