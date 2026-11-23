"""finance_recon 模块 ORM 模型（表名统一 mod_recon_ 前缀，六表供后续任务直接消费）。

四类源数据 kind：writeoff 核销 / claim 认领 / arrear 欠费 / bill 账单
（枚举唯一事实源见 schemas.SOURCE_KINDS）。金额列一律 Numeric(18, 2)；
事实记录金额不得放进 details JSON，也不得经 float 转换（计划 78 行约定）。

唯一性口径：ReconSource/ReconRecord/ReconMatch/ReconTerm 四表用表级
UniqueConstraint，由宿主 lifespan 的 create_all 一次建成；ReconIssue 用
unique Index（uq_recon_issue_fingerprint）——Index 进 table.indexes，
bootstrap 的 ensure_indexes 可在老库补建（UniqueConstraint 不进
table.indexes，老库永远无约束），重复键由迁移脚本显式报错；重复提交由
服务层守卫捕获 IntegrityError 映射中文 409，不靠应用层 count 查重。
"""
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from ...database import Base


class ReconBatch(Base):
    """商户核销对账批次：显式 as_of、显式合同范围、金额容差与生命周期状态。

    ever_ready：批次曾进入 ready 的持久标记（P2-1 审计留痕）——曾就绪的批次
    即使重跑 failed，也不可删除、不可改基本字段、不可替换源文件。
    """

    __tablename__ = "mod_recon_batch"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), default="")
    object_tag: Mapped[str] = mapped_column(String(100), default="")
    as_of: Mapped[date] = mapped_column(Date)
    # 金额容差默认 0.01，在批次上显式记录（计划 57 行）
    tolerance: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0.01"))
    # 显式勾选的本批合同号（有序去重）；check 前必须非空，绝不按合同号前缀过滤
    contract_scope: Mapped[list] = mapped_column(JSON, default=list)
    # 状态机：draft→checking→ready|failed、failed→checking、ready→checking（转移表见 service.TRANSITIONS）
    status: Mapped[str] = mapped_column(String(20), default="draft", index=True)
    error_summary: Mapped[str] = mapped_column(Text, default="")
    # ready 后口径参数再修改时置位（terms Task 5 / check Task 2 消费）
    needs_recheck: Mapped[bool] = mapped_column(Boolean, default=False)
    # 曾进入 ready 的持久标记：审计留痕守卫（P2-1），只置真不复位
    ever_ready: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)


class ReconSource(Base):
    """批次内一份上传源文件：四类 kind、受控存储路径与解析状态。

    存储路径约定：只写 UPLOAD_DIR/recon/<batch_id>/ 内（计划 58 行），
    删除守卫与 skipped_duplicates 语义由 Task 2 的 sources 端点实现。
    """

    __tablename__ = "mod_recon_source"
    __table_args__ = (UniqueConstraint("batch_id", "sha256", name="uq_recon_source_batch_sha"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    batch_id: Mapped[int] = mapped_column(Integer, index=True)
    kind: Mapped[str] = mapped_column(String(20), index=True)  # writeoff/claim/arrear/bill
    original_name: Mapped[str] = mapped_column(String(255), default="")
    stored_path: Mapped[str] = mapped_column(String(500), default="")
    sha256: Mapped[str] = mapped_column(String(64), default="")
    # 解析状态：pending 待解析 / parsed 已入库 / failed 解析失败（Task 2 推进）
    parse_status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class ReconRecord(Base):
    """四类标准化事实共用行：统一查询字段，kind 细节进 details JSON。

    金额映射（计划 78 行固定）：核销 费用期应收/费用期核销/核销后欠费/预收款金额；
    认领 收款金额/本次认领/手续费/空；欠费 合计/固定租金/综管费/空；账单 累计应付/空/空/空。
    单据级应收、单据级核销只用于结构检查，不入本表；details 只放经 kind 对应
    Pydantic schema 校验的非金额细节（户名/摘要/账号尾号等，Task 2/3 校验）。
    """

    __tablename__ = "mod_recon_record"
    __table_args__ = (UniqueConstraint("source_id", "row_no", name="uq_recon_record_source_row"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    batch_id: Mapped[int] = mapped_column(Integer, index=True)
    source_id: Mapped[int] = mapped_column(Integer, index=True)
    row_no: Mapped[int] = mapped_column(Integer, default=0)
    kind: Mapped[str] = mapped_column(String(20), index=True)
    contract_no: Mapped[str] = mapped_column(String(64), default="", index=True)
    doc_no: Mapped[str] = mapped_column(String(80), default="", index=True)
    fee_type: Mapped[str] = mapped_column(String(64), default="")
    fee_period_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    fee_period_end: Mapped[date | None] = mapped_column(Date, nullable=True)
    occur_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    amount_1: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    amount_2: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    amount_3: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    amount_4: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    fingerprint: Mapped[str] = mapped_column(String(128), default="", index=True)
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class ReconMatch(Base):
    """认领↔核销勾稽匹配：suggested 机检建议 / confirmed·rejected 人工裁定（冻结）。

    重跑只 upsert suggested；confirmed/rejected 不被机检覆盖、rejected 不重新推荐
    （Task 3 消费）；删除源文件的引用守卫（存在 confirmed/rejected 时 409）由 Task 2 实现。
    """

    __tablename__ = "mod_recon_match"
    __table_args__ = (
        UniqueConstraint("batch_id", "claim_record_id", "writeoff_record_id", name="uq_recon_match_triplet"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    batch_id: Mapped[int] = mapped_column(Integer, index=True)
    claim_record_id: Mapped[int] = mapped_column(Integer, index=True)
    writeoff_record_id: Mapped[int] = mapped_column(Integer, index=True)
    allocated_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="suggested", index=True)
    note: Mapped[str] = mapped_column(Text, default="")
    origin: Mapped[str] = mapped_column(String(20), default="algorithm")  # algorithm/manual
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)


class ReconIssue(Base):
    """问题/差异登记：机检来源可随重跑演进（指纹只含 scope 键），人工来源永不触碰机检。

    pending/confirmed/dismissed/resolved 语义、active 布尔与 last_seen 游标由
    Task 3 的重跑收敛逻辑消费；本表只负责承载结构与索引。
    """

    __tablename__ = "mod_recon_issue"
    __table_args__ = (
        # 同批次同来源同指纹唯一（P2-5/I2）：唯一性用 unique Index——bootstrap 的
        # ensure_indexes 遍历 table.indexes，可在老库补建（UniqueConstraint 不进
        # table.indexes，老库永远无约束）；新库由 create_all 只建这一个结构，
        # 不重复建 UniqueConstraint + unique Index。重复键由迁移脚本显式报错，
        # ensure_indexes 遇重复键/建失败按其既有语义告警跳过（不删数据）。
        Index("uq_recon_issue_fingerprint", "batch_id", "origin", "fingerprint", unique=True),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    batch_id: Mapped[int] = mapped_column(Integer, index=True)
    origin: Mapped[str] = mapped_column(String(20), default="machine", index=True)  # machine/human
    fingerprint: Mapped[str] = mapped_column(String(200), default="", index=True)
    category: Mapped[str] = mapped_column(String(64), default="")
    severity: Mapped[str] = mapped_column(String(20), default="warn")
    evidence: Mapped[list] = mapped_column(JSON, default=list)  # 证据引用（表/行/单据号，不含文件内容）
    amount_impact: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_seen: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)


class ReconTerm(Base):
    """每批次每合同的人工口径参数：与 OpsContract 无外键、无同步（计划 81 行）。

    terms JSON 仅作序列化容器；金额字段必须经 Decimal schema 校验并以字符串
    序列化，禁止 float；缺参时对应检查为 unknown 并说明原因（Task 5 实现）。
    """

    __tablename__ = "mod_recon_term"
    __table_args__ = (UniqueConstraint("batch_id", "contract_no", name="uq_recon_term_batch_contract"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    batch_id: Mapped[int] = mapped_column(Integer, index=True)
    contract_no: Mapped[str] = mapped_column(String(64), index=True)
    terms: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)
