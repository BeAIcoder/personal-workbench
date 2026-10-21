"""
数据库模型定义
企业所得税填报工具 - SQLite 数据模型
"""
from sqlalchemy import (
    Column, Integer, String, Float, DateTime, Text, Boolean,
    ForeignKey, Index, CheckConstraint, UniqueConstraint
)
from sqlalchemy.orm import relationship
from datetime import datetime
from ...database import Base


class Enterprise(Base):
    """企业基本信息表"""
    __tablename__ = "mod_tax_enterprise"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(200), nullable=False, comment="企业名称")
    tax_number = Column(String(50), unique=True, nullable=False, comment="纳税人识别号")
    industry_type = Column(String(100), nullable=True, comment="行业类型")
    legal_person = Column(String(100), nullable=True, comment="法定代表人")
    contact_phone = Column(String(50), nullable=True, comment="联系电话")
    address = Column(String(300), nullable=True, comment="地址")
    tax_type = Column(String(50), nullable=True, comment="征收方式")
    employee_count = Column(Integer, nullable=True, comment="从业人数")
    total_assets = Column(Float, nullable=True, comment="资产总额（万元）")
    is_restricted_industry = Column(Boolean, default=False, comment="是否限制行业")
    created_at = Column(DateTime, default=datetime.now, nullable=False, comment="创建时间")
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, nullable=False, comment="更新时间")

    # 关联关系
    financial_data = relationship("FinancialData", back_populates="enterprise", cascade="all, delete-orphan")
    tax_returns = relationship("TaxReturn", back_populates="enterprise", cascade="all, delete-orphan")

    # 索引
    __table_args__ = (
        Index("idx_enterprise_tax_number", "tax_number"),
        Index("idx_enterprise_name", "name"),
    )

    def __repr__(self):
        return f"<Enterprise(id={self.id}, name='{self.name}', tax_number='{self.tax_number}')>"


class FinancialData(Base):
    """财务数据表"""
    __tablename__ = "mod_tax_financial_data"

    id = Column(Integer, primary_key=True, autoincrement=True)
    enterprise_id = Column(Integer, ForeignKey("mod_tax_enterprise.id"), nullable=False, comment="企业ID")
    year = Column(Integer, nullable=False, comment="年度")
    quarter = Column(Integer, nullable=False, comment="季度（1-4）")
    total_revenue = Column(Float, default=0.0, comment="收入总额（万元）")
    total_cost = Column(Float, default=0.0, comment="成本总额（万元）")
    total_expense = Column(Float, default=0.0, comment="费用总额（万元）")
    profit = Column(Float, default=0.0, comment="利润总额（万元）")
    non_taxable_income = Column(Float, default=0.0, comment="不征税收入（万元）")
    prior_year_loss = Column(Float, default=0.0, comment="以前年度亏损（万元）")
    created_at = Column(DateTime, default=datetime.now, nullable=False, comment="创建时间")
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, nullable=False, comment="更新时间")

    # 关联关系
    enterprise = relationship("Enterprise", back_populates="financial_data")
    tax_adjustments = relationship("TaxAdjustment", back_populates="financial_data", cascade="all, delete-orphan")

    # 约束和索引
    __table_args__ = (
        UniqueConstraint("enterprise_id", "year", "quarter", name="uq_enterprise_year_quarter"),
        CheckConstraint("quarter >= 1 AND quarter <= 4", name="ck_quarter_range"),
        CheckConstraint("year >= 2000 AND year <= 2100", name="ck_year_range"),
        Index("idx_financial_enterprise_year", "enterprise_id", "year"),
        Index("idx_financial_year_quarter", "year", "quarter"),
    )

    def __repr__(self):
        return f"<FinancialData(id={self.id}, enterprise_id={self.enterprise_id}, year={self.year}, quarter={self.quarter})>"


class TaxAdjustment(Base):
    """纳税调整项目表"""
    __tablename__ = "mod_tax_adjustment"

    id = Column(Integer, primary_key=True, autoincrement=True)
    financial_data_id = Column(Integer, ForeignKey("mod_tax_financial_data.id"), nullable=False, comment="财务数据ID")
    business_entertainment = Column(Float, default=0.0, comment="业务招待费（万元）")
    advertising_expense = Column(Float, default=0.0, comment="广告费（万元）")
    donation_expense = Column(Float, default=0.0, comment="捐赠支出（万元）")
    rnd_expense = Column(Float, default=0.0, comment="研发费用（万元）")
    welfare_expense = Column(Float, default=0.0, comment="福利费（万元）")
    education_expense = Column(Float, default=0.0, comment="教育经费（万元）")
    union_expense = Column(Float, default=0.0, comment="工会经费（万元）")
    penalties = Column(Float, default=0.0, comment="罚款（万元）")
    equipment_purchase = Column(Float, default=0.0, comment="设备购置（万元）")
    created_at = Column(DateTime, default=datetime.now, nullable=False, comment="创建时间")
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, nullable=False, comment="更新时间")

    # 关联关系
    financial_data = relationship("FinancialData", back_populates="tax_adjustments")

    # 索引
    __table_args__ = (
        Index("idx_adjustment_financial_data", "financial_data_id"),
    )

    def __repr__(self):
        return f"<TaxAdjustment(id={self.id}, financial_data_id={self.financial_data_id})>"


class TaxReturn(Base):
    """申报表历史记录表"""
    __tablename__ = "mod_tax_return"

    id = Column(Integer, primary_key=True, autoincrement=True)
    enterprise_id = Column(Integer, ForeignKey("mod_tax_enterprise.id"), nullable=False, comment="企业ID")
    year = Column(Integer, nullable=False, comment="年度")
    quarter = Column(Integer, nullable=True, comment="季度（1-4，年度申报时为NULL）")
    return_type = Column(String(20), nullable=False, comment="申报类型：预缴/年度")
    status = Column(String(20), default="draft", comment="状态：draft-草稿，submitted-已提交")
    taxable_income = Column(Float, default=0.0, comment="应纳税所得额（万元）")
    tax_amount = Column(Float, default=0.0, comment="应纳税额（万元）")
    created_at = Column(DateTime, default=datetime.now, nullable=False, comment="创建时间")
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, nullable=False, comment="更新时间")

    # 关联关系
    enterprise = relationship("Enterprise", back_populates="tax_returns")

    # 约束和索引
    __table_args__ = (
        CheckConstraint("return_type IN ('预缴', '年度')", name="ck_return_type"),
        CheckConstraint("quarter IS NULL OR (quarter >= 1 AND quarter <= 4)", name="ck_return_quarter_range"),
        Index("idx_return_enterprise_year", "enterprise_id", "year"),
        Index("idx_return_type", "return_type"),
    )

    def __repr__(self):
        return f"<TaxReturn(id={self.id}, enterprise_id={self.enterprise_id}, year={self.year}, type='{self.return_type}')>"


class Policy(Base):
    """政策版本管理表"""
    __tablename__ = "mod_tax_policy"

    id = Column(Integer, primary_key=True, autoincrement=True)
    policy_number = Column(String(100), unique=True, nullable=False, comment="政策文号")
    title = Column(String(300), nullable=False, comment="政策名称")
    version = Column(String(20), default="1.0", comment="政策版本号")
    effective_date = Column(DateTime, nullable=False, comment="生效日期")
    expiry_date = Column(DateTime, nullable=True, comment="失效日期")
    content = Column(Text, nullable=True, comment="政策内容")
    summary = Column(Text, nullable=True, comment="政策摘要")
    policy_type = Column(String(50), nullable=False, comment="政策类型")
    tags = Column(String(300), nullable=True, comment="标签（逗号分隔）")
    is_active = Column(Boolean, default=True, comment="是否启用")
    created_at = Column(DateTime, default=datetime.now, nullable=False, comment="创建时间")
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, nullable=False, comment="更新时间")

    # 约束和索引
    __table_args__ = (
        CheckConstraint(
            "expiry_date IS NULL OR expiry_date > effective_date",
            name="ck_policy_date_range"
        ),
        Index("idx_policy_number", "policy_number"),
        Index("idx_policy_type", "policy_type"),
        Index("idx_policy_effective_date", "effective_date"),
        Index("idx_policy_active", "is_active"),
    )

    def __repr__(self):
        return f"<Policy(id={self.id}, number='{self.policy_number}', title='{self.title}')>"
