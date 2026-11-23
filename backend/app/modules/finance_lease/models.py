"""finance_lease 模块 ORM 模型（表名统一 mod_lease_ 前缀）。

六表边界（与获批计划一致；第六表是 BIP 只读对账的科目映射）：

- ``mod_lease_contract``：合同主档，合同号唯一，status 仅 draft/archived；
- ``mod_lease_payment``：付款期，``(contract_id, sequence)`` 唯一，保留源行号；
- ``mod_lease_calculation``：成功测算批次，``(contract_id, input_hash, rule_version)``
  唯一，只保存完整成功结果；
- ``mod_lease_assessment``：付款期级现值/折现因子/当期利息 U/负债滚动；
- ``mod_lease_period``：月份级折旧/应付/利息与对应付款期；
- ``mod_lease_bip_map``：BIP 只读对账的科目映射（承租人 × 核算账簿 × 科目位唯一）。

金额列一律 ``Numeric(18, 2)``；期间利率 6 位、折现因子 9 位，只作为存储精度，
实际计算精度由已冻结规则矩阵与计算引擎决定（本文件不实现任何会计计算）。
失败测算不允许落半套结果：计算行与其评估行/期间行在同一事务提交。
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


class LeaseContract(Base):
    """承租人租赁合同主档：合同号唯一，归档后只读。"""

    __tablename__ = "mod_lease_contract"
    __table_args__ = (UniqueConstraint("contract_no", name="uq_lease_contract_no"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    contract_no: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(200), default="")
    lessee: Mapped[str] = mapped_column(String(200), default="")
    project: Mapped[str] = mapped_column(String(200), default="")
    lessor: Mapped[str] = mapped_column(String(200), default="")
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    # 年利率按小数存储（0.035000 = 3.5%），精度由 schema 与规则矩阵约束
    annual_rate: Mapped[Decimal] = mapped_column(Numeric(9, 6), default=Decimal("0"))
    rate_basis: Mapped[str] = mapped_column(Text, default="")
    remark: Mapped[str] = mapped_column(Text, default="")
    attachment_note: Mapped[str] = mapped_column(Text, default="")
    # 状态唯一事实源在 schemas.CONTRACT_STATUSES；这里只存值
    status: Mapped[str] = mapped_column(String(20), default="draft", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)


class LeasePayment(Base):
    """付款期：一行 = 一个付款期；免租期与 0 月期都必须占行，不得静默删除。"""

    __tablename__ = "mod_lease_payment"
    __table_args__ = (
        UniqueConstraint("contract_id", "sequence", name="uq_lease_payment_contract_sequence"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    contract_id: Mapped[int] = mapped_column(Integer, index=True)
    # 支付期数：服务层强制 1..N 连续编号
    sequence: Mapped[int] = mapped_column(Integer)
    # 源底稿行号：只用于差异定位，不参与 input_hash
    source_row: Mapped[int | None] = mapped_column(Integer, nullable=True)
    period_start: Mapped[date] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date)
    # 该付款期覆盖的月数；源模板无此列时由计算引擎按计租起止推导，末期 0 月期合法
    months: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    # 不含税金额（源底稿口径）
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0"))
    start_flag: Mapped[bool] = mapped_column(Boolean, default=False)
    prepay_flag: Mapped[bool] = mapped_column(Boolean, default=False)
    is_free_rent: Mapped[bool] = mapped_column(Boolean, default=False)
    note: Mapped[str] = mapped_column(String(500), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)


class LeaseCalculation(Base):
    """成功测算批次：输入哈希 + 规则版本 + 结果快照 + 校验结果。

    status 仅 calculated/confirmed，合法转移只有 calculated → confirmed；
    已确认快照不再提供任何写入口，重算同一输入幂等返回既有行。
    """

    __tablename__ = "mod_lease_calculation"
    __table_args__ = (
        UniqueConstraint(
            "contract_id", "input_hash", "rule_version", name="uq_lease_calculation_input"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    contract_id: Mapped[int] = mapped_column(Integer, index=True)
    input_hash: Mapped[str] = mapped_column(String(64))
    rule_version: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20), default="calculated", index=True)
    # 测算时点的合同经济输入与付款计划快照（金额/日期以字符串保存，禁止 float）
    input_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    # 汇总值与校验结果只保存引擎输出，服务层不重算会计口径
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
    checks: Mapped[list] = mapped_column(JSON, default=list)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)


class LeaseAssessment(Base):
    """付款期级评估行：现值、折现因子、当期利息 U 与负债滚动。"""

    __tablename__ = "mod_lease_assessment"
    __table_args__ = (
        UniqueConstraint("calculation_id", "sequence", name="uq_lease_assessment_sequence"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    calculation_id: Mapped[int] = mapped_column(Integer, index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    source_row: Mapped[int | None] = mapped_column(Integer, nullable=True)
    period_start: Mapped[date] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date)
    months: Mapped[int] = mapped_column(Integer, default=0)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0"))
    period_rate: Mapped[Decimal] = mapped_column(Numeric(12, 6), default=Decimal("0"))
    discount_factor: Mapped[Decimal] = mapped_column(Numeric(20, 9), default=Decimal("1"))
    present_value: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0"))
    unamortized_finance: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0"))
    interest_u: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0"))
    liability_balance: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0"))


class LeasePeriod(Base):
    """月份级期间行：折旧、应付、利息与对应付款期（按测算批次与月份唯一）。"""

    __tablename__ = "mod_lease_period"
    __table_args__ = (
        UniqueConstraint("calculation_id", "period_month", name="uq_lease_period_month"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    calculation_id: Mapped[int] = mapped_column(Integer, index=True)
    # YYYY-MM（严格校验由 schema/service 完成）
    period_month: Mapped[str] = mapped_column(String(7))
    sequence: Mapped[int] = mapped_column(Integer)
    depreciation: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0"))
    payable: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0"))
    interest: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0"))
    lease_payment: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0"))
    unamortized_finance: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0"))
    liability_balance: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0"))


class LeaseBipMap(Base):
    """BIP 只读对账的科目映射：承租人 × 核算账簿 × 科目位（subject_key）唯一。

    科目位只有五类（唯一事实源 ``reconciliation_schemas.SUBJECT_KEYS``），
    ``direction`` 表示该 BIP 科目的余额方向（debit/credit），用于把
    ``closing_debit``/``closing_credit`` 折算成带方向的金额；方向金额都为空时
    才按该方向取方向无关期末余额的绝对值（来源在结果中标明）。

    本表只保存「租赁核算 → BIP 科目」的对照关系，不写任何 BIP 数据：
    BIP 余额/凭证一律只读，映射写入也只落在本模块这张表。
    """

    __tablename__ = "mod_lease_bip_map"
    __table_args__ = (
        UniqueConstraint("lessee", "ledger", "subject_key", name="uq_lease_bip_map_scope"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    # 承租人主体：与 mod_lease_contract.lessee 同口径（等值匹配，不做模糊匹配）
    lessee: Mapped[str] = mapped_column(String(200), index=True)
    # BIP 核算账簿名称：与 mod_bip_balance.ledger 等值匹配
    ledger: Mapped[str] = mapped_column(String(100))
    # 科目位：rou_asset / rou_accum_dep / lease_payment / unrecognized_finance / lease_liability
    subject_key: Mapped[str] = mapped_column(String(40))
    subject_code: Mapped[str] = mapped_column(String(50))
    # 余额方向：debit / credit（唯一事实源 reconciliation_schemas.MAPPING_DIRECTIONS）
    direction: Mapped[str] = mapped_column(String(10), default="debit")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)
