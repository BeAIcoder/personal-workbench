# -*- coding: utf-8 -*-
"""法务服务：合同审查、案件跟踪、用印台账、风险看板与导出。"""
from __future__ import annotations

import io
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy.orm import Session

from ...database import SessionLocal
from ...utils_xlsx import safe_cell
from .models import LegalCase, LegalContractReview, LegalSeal

REVIEW_STATUSES = ("pending", "reviewing", "approved", "rejected", "archived")
REVIEW_STATUS_LABELS = {
    "pending": "待审", "reviewing": "审查中", "approved": "已通过",
    "rejected": "已驳回", "archived": "已归档",
}
RISK_LEVELS = ("low", "medium", "high")
RISK_LABELS = {"low": "低", "medium": "中", "high": "高"}
CASE_TYPES = ("litigation", "arbitration", "dispute")
CASE_TYPE_LABELS = {"litigation": "诉讼", "arbitration": "仲裁", "dispute": "纠纷"}
CASE_STAGES = ("filed", "hearing", "judgment", "appeal", "closed")
CASE_STAGE_LABELS = {
    "filed": "立案", "hearing": "开庭", "judgment": "判决",
    "appeal": "上诉", "closed": "结案",
}
REVIEW_TRANSITIONS = {
    "pending": {"reviewing"},
    "reviewing": {"approved", "rejected"},
    "approved": {"archived"},
    "rejected": {"archived"},
    "archived": set(),
}
CASE_TRANSITIONS = {
    "filed": {"hearing"},
    "hearing": {"judgment"},
    "judgment": {"appeal", "closed"},
    "appeal": {"closed"},
    "closed": set(),
}


def _check_transition(current: str, target: str, transitions: dict, label: str) -> None:
    if target != current and target not in transitions.get(current, set()):
        raise ValueError(f"{label}不允许从 {current} 变更为 {target}")


def money(value) -> float:
    return float(Decimal(str(value or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _as_date(value, field: str = "日期") -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise ValueError(f"{field}格式非法，应为 YYYY-MM-DD") from exc


# ---------------- 合同审查 ----------------

def review_to_dict(c: LegalContractReview) -> dict:
    today = date.today()
    days = (c.expire_date - today).days if c.expire_date else None
    return {
        "id": c.id, "title": c.title, "contract_type": c.contract_type or "",
        "counterparty": c.counterparty or "", "amount": money(c.amount),
        "submit_date": c.submit_date.isoformat() if c.submit_date else None,
        "reviewer": c.reviewer or "",
        "status": c.status,
        "status_label": REVIEW_STATUS_LABELS.get(c.status, c.status),
        "risk_level": c.risk_level,
        "risk_label": RISK_LABELS.get(c.risk_level, c.risk_level),
        "expire_date": c.expire_date.isoformat() if c.expire_date else None,
        "days_to_expire": days,
        "expiring": bool(days is not None and 0 <= days <= 30),
        "overdue": bool(days is not None and days < 0),
        "note": c.note or "",
        "created_at": str(c.created_at),
    }


def review_list(db: Session, status: str = "", risk_level: str = "", keyword: str = "",
                page: int = 1, page_size: int = 20) -> dict:
    q = db.query(LegalContractReview)
    if status:
        q = q.filter(LegalContractReview.status == status)
    if risk_level:
        q = q.filter(LegalContractReview.risk_level == risk_level)
    if keyword:
        q = q.filter(LegalContractReview.title.contains(keyword, autoescape=True))
    total = q.count()
    rows = (q.order_by(LegalContractReview.submit_date.desc(), LegalContractReview.id.desc())
            .offset((page - 1) * page_size).limit(page_size).all())
    return {"total": total, "items": [review_to_dict(c) for c in rows],
            "page": page, "page_size": page_size}


def review_save(db: Session, data: dict, review_id: int | None = None) -> dict:
    c = db.get(LegalContractReview, review_id) if review_id else LegalContractReview()
    if c is None:
        raise LookupError("合同审查记录不存在")
    for field in ("title", "contract_type", "counterparty", "reviewer", "note"):
        if field in data and data[field] is not None:
            setattr(c, field, str(data[field]).strip())
    if "amount" in data and data["amount"] is not None:
        if float(data["amount"]) < 0:
            raise ValueError("合同金额不能为负数")
        c.amount = money(data["amount"])
    if "status" in data and data["status"] is not None:
        if data["status"] not in REVIEW_STATUSES:
            raise ValueError(f"未知审查状态：{data['status']}")
        if review_id is not None:
            _check_transition(c.status, data["status"], REVIEW_TRANSITIONS, "合同审查状态")
        c.status = data["status"]
    if "risk_level" in data and data["risk_level"] is not None:
        if data["risk_level"] not in RISK_LEVELS:
            raise ValueError(f"未知风险等级：{data['risk_level']}")
        c.risk_level = data["risk_level"]
    if "submit_date" in data:
        c.submit_date = _as_date(data["submit_date"], "送审日期")
    if "expire_date" in data:
        c.expire_date = _as_date(data["expire_date"], "到期日期")
    if not c.title:
        raise ValueError("合同标题不能为空")
    if c.submit_date is None:
        c.submit_date = date.today()
    if review_id is None:
        db.add(c)
    db.commit()
    db.refresh(c)
    return review_to_dict(c)


def review_delete(db: Session, review_id: int) -> None:
    c = db.get(LegalContractReview, review_id)
    if c is None:
        raise LookupError("合同审查记录不存在")
    db.delete(c)
    db.commit()


# ---------------- 案件 ----------------

def case_to_dict(c: LegalCase) -> dict:
    today = date.today()
    days = (c.hearing_date - today).days if c.hearing_date else None
    return {
        "id": c.id, "case_no": c.case_no or "", "title": c.title,
        "case_type": c.case_type,
        "case_type_label": CASE_TYPE_LABELS.get(c.case_type, c.case_type),
        "counterparty": c.counterparty or "", "amount": money(c.amount),
        "stage": c.stage,
        "stage_label": CASE_STAGE_LABELS.get(c.stage, c.stage),
        "filed_date": c.filed_date.isoformat() if c.filed_date else None,
        "hearing_date": c.hearing_date.isoformat() if c.hearing_date else None,
        "days_to_hearing": days,
        "hearing_soon": bool(days is not None and 0 <= days <= 7 and c.stage != "closed"),
        "overdue": bool(days is not None and days < 0 and c.stage != "closed"),
        "result": c.result or "", "note": c.note or "",
        "created_at": str(c.created_at),
    }


def case_list(db: Session, stage: str = "", case_type: str = "", keyword: str = "",
              page: int = 1, page_size: int = 20) -> dict:
    q = db.query(LegalCase)
    if stage:
        q = q.filter(LegalCase.stage == stage)
    if case_type:
        q = q.filter(LegalCase.case_type == case_type)
    if keyword:
        q = q.filter(LegalCase.title.contains(keyword, autoescape=True))
    total = q.count()
    rows = (q.order_by(LegalCase.filed_date.desc(), LegalCase.id.desc())
            .offset((page - 1) * page_size).limit(page_size).all())
    return {"total": total, "items": [case_to_dict(c) for c in rows],
            "page": page, "page_size": page_size}


def case_save(db: Session, data: dict, case_id: int | None = None) -> dict:
    c = db.get(LegalCase, case_id) if case_id else LegalCase()
    if c is None:
        raise LookupError("案件不存在")
    for field in ("case_no", "title", "counterparty", "result", "note"):
        if field in data and data[field] is not None:
            setattr(c, field, str(data[field]).strip())
    if "amount" in data and data["amount"] is not None:
        if float(data["amount"]) < 0:
            raise ValueError("标的额不能为负数")
        c.amount = money(data["amount"])
    if "case_type" in data and data["case_type"] is not None:
        if data["case_type"] not in CASE_TYPES:
            raise ValueError(f"未知案件类型：{data['case_type']}")
        c.case_type = data["case_type"]
    if "stage" in data and data["stage"] is not None:
        if data["stage"] not in CASE_STAGES:
            raise ValueError(f"未知案件阶段：{data['stage']}")
        if case_id is not None:
            _check_transition(c.stage, data["stage"], CASE_TRANSITIONS, "案件阶段")
        c.stage = data["stage"]
    for field in ("filed_date", "hearing_date"):
        if field in data:
            setattr(c, field, _as_date(data[field], field))
    if not c.title:
        raise ValueError("案件标题不能为空")
    if case_id is None:
        db.add(c)
    db.commit()
    db.refresh(c)
    return case_to_dict(c)


def case_delete(db: Session, case_id: int) -> None:
    c = db.get(LegalCase, case_id)
    if c is None:
        raise LookupError("案件不存在")
    db.delete(c)
    db.commit()


# ---------------- 用印 ----------------

def seal_to_dict(s: LegalSeal) -> dict:
    return {
        "id": s.id, "purpose": s.purpose, "doc_type": s.doc_type or "",
        "applicant": s.applicant or "", "approver": s.approver or "",
        "used_at": s.used_at.isoformat() if s.used_at else None,
        "copies": int(s.copies or 0), "note": s.note or "",
        "created_at": str(s.created_at),
    }


def seal_list(db: Session, keyword: str = "", page: int = 1, page_size: int = 20) -> dict:
    q = db.query(LegalSeal)
    if keyword:
        q = q.filter(LegalSeal.purpose.contains(keyword, autoescape=True))
    total = q.count()
    rows = (q.order_by(LegalSeal.used_at.desc(), LegalSeal.id.desc())
            .offset((page - 1) * page_size).limit(page_size).all())
    return {"total": total, "items": [seal_to_dict(s) for s in rows],
            "page": page, "page_size": page_size}


def seal_save(db: Session, data: dict, seal_id: int | None = None) -> dict:
    s = db.get(LegalSeal, seal_id) if seal_id else LegalSeal()
    if s is None:
        raise LookupError("用印记录不存在")
    for field in ("purpose", "doc_type", "applicant", "approver", "note"):
        if field in data and data[field] is not None:
            setattr(s, field, str(data[field]).strip())
    if "copies" in data and data["copies"] is not None:
        copies = int(data["copies"])
        if copies <= 0:
            raise ValueError("用印份数须大于 0")
        s.copies = copies
    if "used_at" in data:
        s.used_at = _as_date(data["used_at"], "用印日期")
    if not s.purpose:
        raise ValueError("用印用途不能为空")
    if s.used_at is None:
        s.used_at = date.today()
    if seal_id is None:
        db.add(s)
    db.commit()
    db.refresh(s)
    return seal_to_dict(s)


def seal_delete(db: Session, seal_id: int) -> None:
    s = db.get(LegalSeal, seal_id)
    if s is None:
        raise LookupError("用印记录不存在")
    db.delete(s)
    db.commit()


# ---------------- 看板 / 导出 ----------------

def legal_summary(db: Session) -> dict:
    reviews = db.query(LegalContractReview).all()
    cases = db.query(LegalCase).all()
    today = date.today()
    pending = [c for c in reviews if c.status in ("pending", "reviewing")]
    high_risk = [c for c in reviews if c.risk_level == "high"]
    expiring = [c for c in reviews
                if c.expire_date and 0 <= (c.expire_date - today).days <= 30]
    overdue_reviews = [c for c in reviews
                       if c.expire_date and (c.expire_date - today).days < 0]
    hearings = [c for c in cases
                if c.hearing_date and c.stage != "closed"
                and 0 <= (c.hearing_date - today).days <= 7]
    overdue_hearings = [c for c in cases
                        if c.hearing_date and c.stage != "closed"
                        and (c.hearing_date - today).days < 0]
    open_cases = [c for c in cases if c.stage != "closed"]
    return {
        "reviews_total": len(reviews),
        "pending_reviews": len(pending),
        "high_risk": len(high_risk),
        "expiring_30": len(expiring),
        "overdue_reviews": len(overdue_reviews),
        "cases_total": len(cases),
        "open_cases": len(open_cases),
        "hearings_7": len(hearings),
        "overdue_hearings": len(overdue_hearings),
        "case_amount": money(sum(float(c.amount or 0) for c in open_cases)),
        "by_risk": [
            {"risk": r, "label": RISK_LABELS[r],
             "count": sum(1 for c in reviews if c.risk_level == r)}
            for r in RISK_LEVELS
        ],
    }


def _xlsx(title: str, headers: list[str], rows: list[list]) -> bytes:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = title[:31]
    ws.append(headers)
    for row in rows:
        ws.append([safe_cell(v) for v in row])
    ws.freeze_panes = "A2"
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def reviews_export(db: Session) -> bytes:
    rows = [[c.title, c.contract_type, c.counterparty, money(c.amount),
             c.submit_date.isoformat() if c.submit_date else "", c.reviewer,
             REVIEW_STATUS_LABELS.get(c.status, c.status),
             RISK_LABELS.get(c.risk_level, c.risk_level),
             c.expire_date.isoformat() if c.expire_date else "", c.note]
            for c in db.query(LegalContractReview).order_by(
                LegalContractReview.submit_date.desc()).all()]
    headers = ["合同标题", "合同类型", "对方", "金额", "送审日期", "审查人",
               "状态", "风险等级", "到期日", "备注"]
    return _xlsx("合同审查", headers, rows)


def cases_export(db: Session) -> bytes:
    rows = [[c.case_no, c.title, CASE_TYPE_LABELS.get(c.case_type, c.case_type),
             c.counterparty, money(c.amount),
             CASE_STAGE_LABELS.get(c.stage, c.stage),
             c.filed_date.isoformat() if c.filed_date else "",
             c.hearing_date.isoformat() if c.hearing_date else "",
             c.result, c.note]
            for c in db.query(LegalCase).order_by(LegalCase.id.desc()).all()]
    headers = ["案号", "案件标题", "类型", "对方", "标的额", "阶段",
               "立案日", "开庭日", "结果", "备注"]
    return _xlsx("案件台账", headers, rows)


def legal_summary_tool() -> dict:
    """法务看板摘要（只读）：待审/高风险/到期合同/近期开庭。"""
    with SessionLocal() as db:
        return {"ok": True, **legal_summary(db)}
