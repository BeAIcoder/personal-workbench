# -*- coding: utf-8 -*-
"""business_legal 模块 ORM 模型（表名统一 mod_ 前缀）。"""
from datetime import date, datetime

from sqlalchemy import Date, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ...database import Base


class LegalContractReview(Base):
    """合同审查台账。"""

    __tablename__ = "mod_legal_contract_review"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(200), default="", index=True)
    contract_type: Mapped[str] = mapped_column(String(50), default="", index=True)
    counterparty: Mapped[str] = mapped_column(String(200), default="", index=True)
    amount: Mapped[float] = mapped_column(Float, default=0.0)
    submit_date: Mapped[date] = mapped_column(Date, index=True)
    reviewer: Mapped[str] = mapped_column(String(50), default="", index=True)
    # pending 待审 / reviewing 审查中 / approved 已通过 / rejected 已驳回 / archived 已归档
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    # low / medium / high
    risk_level: Mapped[str] = mapped_column(String(10), default="low", index=True)
    expire_date: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, onupdate=datetime.now
    )


class LegalCase(Base):
    """诉讼/仲裁/纠纷案件。"""

    __tablename__ = "mod_legal_case"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    case_no: Mapped[str] = mapped_column(String(80), default="", index=True)
    title: Mapped[str] = mapped_column(String(200), default="", index=True)
    # litigation 诉讼 / arbitration 仲裁 / dispute 纠纷
    case_type: Mapped[str] = mapped_column(String(20), default="dispute", index=True)
    counterparty: Mapped[str] = mapped_column(String(200), default="")
    amount: Mapped[float] = mapped_column(Float, default=0.0)
    # filed 立案 / hearing 开庭 / judgment 判决 / appeal 上诉 / closed 结案
    stage: Mapped[str] = mapped_column(String(20), default="filed", index=True)
    filed_date: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    hearing_date: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    result: Mapped[str] = mapped_column(String(300), default="")
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, onupdate=datetime.now
    )


class LegalSeal(Base):
    """用印台账。"""

    __tablename__ = "mod_legal_seal"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    purpose: Mapped[str] = mapped_column(String(200), default="", index=True)
    doc_type: Mapped[str] = mapped_column(String(50), default="", index=True)
    applicant: Mapped[str] = mapped_column(String(50), default="", index=True)
    approver: Mapped[str] = mapped_column(String(50), default="")
    used_at: Mapped[date] = mapped_column(Date, index=True)
    copies: Mapped[int] = mapped_column(Integer, default=1)
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
