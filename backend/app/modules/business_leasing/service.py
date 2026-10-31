# -*- coding: utf-8 -*-
"""招商管理服务：铺位台账、线索漏斗、跟进记录、看板与导出。"""
from __future__ import annotations

import io
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ...database import SessionLocal
from .models import LeasingFollowup, LeasingLead, LeasingUnit

UNIT_STATUS_LABELS = {
    "vacant": "空置",
    "negotiating": "在谈",
    "reserved": "已定",
    "leased": "已租",
}
LEAD_STAGE_LABELS = {
    "lead": "线索",
    "contact": "已联系",
    "offer": "已报价",
    "intent": "意向",
    "signed": "已签约",
    "lost": "已流失",
}
FOLLOWUP_CHANNELS = ("电话", "微信", "面谈", "邮件", "其他")

UNIT_STATUSES = tuple(UNIT_STATUS_LABELS)
LEAD_STAGES = tuple(LEAD_STAGE_LABELS)


def money(value) -> float:
    """金额两位小数，四舍五入（与财税模块同口径，不用二进制 round）。"""
    return float(Decimal(str(value or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _as_date(value) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


# ---------------- 铺位 ----------------

def unit_to_dict(unit: LeasingUnit) -> dict:
    return {
        "id": unit.id,
        "building": unit.building,
        "floor": unit.floor,
        "unit_no": unit.unit_no,
        "area": money(unit.area),
        "rent_price": money(unit.rent_price),
        "property_fee": money(unit.property_fee),
        "status": unit.status,
        "status_label": UNIT_STATUS_LABELS.get(unit.status, unit.status),
        "monthly_rent": money((unit.area or 0) * (unit.rent_price or 0)),
        "note": unit.note or "",
        "created_at": str(unit.created_at),
    }


def unit_list(db: Session, building: str = "", status: str = "", keyword: str = "",
              page: int = 1, page_size: int = 20) -> dict:
    q = db.query(LeasingUnit)
    if building:
        q = q.filter(LeasingUnit.building == building)
    if status:
        q = q.filter(LeasingUnit.status == status)
    if keyword:
        q = q.filter(LeasingUnit.unit_no.contains(keyword, autoescape=True))
    total = q.count()
    rows = (q.order_by(LeasingUnit.building, LeasingUnit.floor, LeasingUnit.unit_no)
            .offset((page - 1) * page_size).limit(page_size).all())
    return {"total": total, "items": [unit_to_dict(u) for u in rows],
            "page": page, "page_size": page_size}


def unit_save(db: Session, data: dict, unit_id: int | None = None) -> dict:
    unit = db.get(LeasingUnit, unit_id) if unit_id else LeasingUnit()
    if unit is None:
        raise LookupError("铺位不存在")
    for field in ("building", "floor", "unit_no", "status", "note"):
        if field in data and data[field] is not None:
            setattr(unit, field, str(data[field]).strip())
    for field in ("area", "rent_price", "property_fee"):
        if field in data and data[field] is not None:
            if float(data[field]) < 0:
                raise ValueError(f"{field} 不能为负数")
            setattr(unit, field, money(data[field]))
    if not unit.building or not unit.unit_no:
        raise ValueError("楼栋与铺位号不能为空")
    if unit.status not in UNIT_STATUSES:
        raise ValueError(f"未知铺位状态：{unit.status}")
    if unit_id is None:
        db.add(unit)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ValueError("同一楼栋/楼层下的铺位号已存在") from exc
    db.refresh(unit)
    return unit_to_dict(unit)


def unit_delete(db: Session, unit_id: int) -> None:
    unit = db.get(LeasingUnit, unit_id)
    if unit is None:
        raise LookupError("铺位不存在")
    db.delete(unit)
    db.commit()


def unit_summary(db: Session) -> dict:
    rows = db.query(LeasingUnit).all()
    total_area = sum(float(u.area or 0) for u in rows)
    vacant = [u for u in rows if u.status == "vacant"]
    vacant_area = sum(float(u.area or 0) for u in vacant)
    by_status: dict[str, int] = {}
    for u in rows:
        by_status[u.status] = by_status.get(u.status, 0) + 1
    return {
        "total_units": len(rows),
        "total_area": money(total_area),
        "vacant_units": len(vacant),
        "vacant_area": money(vacant_area),
        "vacancy_rate": round(vacant_area / total_area, 4) if total_area else 0.0,
        "leased_units": by_status.get("leased", 0),
        "buildings": sorted({u.building for u in rows if u.building}),
        "by_status": [
            {"status": s, "label": UNIT_STATUS_LABELS[s], "count": by_status.get(s, 0)}
            for s in UNIT_STATUSES
        ],
    }


# ---------------- 线索 ----------------

def lead_to_dict(lead: LeasingLead) -> dict:
    return {
        "id": lead.id,
        "company": lead.company,
        "contact": lead.contact or "",
        "phone": lead.phone or "",
        "industry": lead.industry or "",
        "demand_area": money(lead.demand_area),
        "budget": money(lead.budget),
        "stage": lead.stage,
        "stage_label": LEAD_STAGE_LABELS.get(lead.stage, lead.stage),
        "owner": lead.owner or "",
        "next_action_at": lead.next_action_at.isoformat() if lead.next_action_at else None,
        "note": lead.note or "",
        "created_at": str(lead.created_at),
        "updated_at": str(lead.updated_at),
    }


def lead_list(db: Session, stage: str = "", owner: str = "", keyword: str = "",
              page: int = 1, page_size: int = 20) -> dict:
    q = db.query(LeasingLead)
    if stage:
        q = q.filter(LeasingLead.stage == stage)
    if owner:
        q = q.filter(LeasingLead.owner == owner)
    if keyword:
        q = q.filter(LeasingLead.company.contains(keyword, autoescape=True))
    total = q.count()
    rows = (q.order_by(LeasingLead.updated_at.desc())
            .offset((page - 1) * page_size).limit(page_size).all())
    return {"total": total, "items": [lead_to_dict(x) for x in rows],
            "page": page, "page_size": page_size}


def lead_save(db: Session, data: dict, lead_id: int | None = None) -> dict:
    lead = db.get(LeasingLead, lead_id) if lead_id else LeasingLead()
    if lead is None:
        raise LookupError("线索不存在")
    for field in ("company", "contact", "phone", "industry", "owner", "note"):
        if field in data and data[field] is not None:
            setattr(lead, field, str(data[field]).strip())
    for field in ("demand_area", "budget"):
        if field in data and data[field] is not None:
            if float(data[field]) < 0:
                raise ValueError(f"{field} 不能为负数")
            setattr(lead, field, money(data[field]))
    if "stage" in data and data["stage"] is not None:
        if data["stage"] not in LEAD_STAGES:
            raise ValueError(f"未知线索阶段：{data['stage']}")
        lead.stage = data["stage"]
    if "next_action_at" in data:
        lead.next_action_at = _as_date(data["next_action_at"])
    if not lead.company:
        raise ValueError("客户公司名称不能为空")
    if lead_id is None:
        db.add(lead)
    db.commit()
    db.refresh(lead)
    return lead_to_dict(lead)


def lead_delete(db: Session, lead_id: int) -> None:
    lead = db.get(LeasingLead, lead_id)
    if lead is None:
        raise LookupError("线索不存在")
    db.query(LeasingFollowup).filter(LeasingFollowup.lead_id == lead_id).delete()
    db.delete(lead)
    db.commit()


def followup_add(db: Session, lead_id: int, data: dict) -> dict:
    if db.get(LeasingLead, lead_id) is None:
        raise LookupError("线索不存在")
    channel = str(data.get("channel") or "电话")
    if channel not in FOLLOWUP_CHANNELS:
        raise ValueError(f"未知跟进渠道：{channel}")
    content = str(data.get("content") or "").strip()
    if not content:
        raise ValueError("跟进内容不能为空")
    row = LeasingFollowup(
        lead_id=lead_id,
        at=_as_date(data.get("at")) or date.today(),
        channel=channel,
        content=content[:2000],
        result=str(data.get("result") or "").strip()[:100],
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return followup_to_dict(row)


def followup_to_dict(row: LeasingFollowup) -> dict:
    return {
        "id": row.id, "lead_id": row.lead_id, "at": row.at.isoformat(),
        "channel": row.channel, "content": row.content, "result": row.result,
        "created_at": str(row.created_at),
    }


def followup_list(db: Session, lead_id: int) -> list[dict]:
    rows = (db.query(LeasingFollowup)
            .filter(LeasingFollowup.lead_id == lead_id)
            .order_by(LeasingFollowup.at.desc(), LeasingFollowup.id.desc()).all())
    return [followup_to_dict(r) for r in rows]


def funnel(db: Session) -> dict:
    rows = db.query(LeasingLead).all()
    stages = []
    for stage in LEAD_STAGES:
        group = [x for x in rows if x.stage == stage]
        stages.append({
            "stage": stage,
            "label": LEAD_STAGE_LABELS[stage],
            "count": len(group),
            "area": money(sum(float(x.demand_area or 0) for x in group)),
            "budget": money(sum(float(x.budget or 0) for x in group)),
        })
    total = len(rows)
    signed = sum(1 for x in rows if x.stage == "signed")
    active = sum(1 for x in rows if x.stage not in ("signed", "lost"))
    return {
        "total": total,
        "active": active,
        "signed": signed,
        "conversion_rate": round(signed / total, 4) if total else 0.0,
        "stages": stages,
    }


def leads_due(db: Session, today: date | None = None) -> list[dict]:
    today = today or date.today()
    rows = (db.query(LeasingLead)
            .filter(LeasingLead.next_action_at.isnot(None))
            .filter(LeasingLead.next_action_at <= today)
            .filter(LeasingLead.stage.notin_(("signed", "lost")))
            .order_by(LeasingLead.next_action_at.asc()).limit(100).all())
    return [lead_to_dict(x) for x in rows]


# ---------------- 导出 ----------------

def _cell(value):
    """防 Excel 公式注入：用户文本以 = + - @ 开头时加前导单引号。"""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@"):
        return "'" + value
    return value


def _xlsx(title: str, headers: list[str], rows: list[list]) -> bytes:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = title[:31]
    ws.append(headers)
    for row in rows:
        ws.append([_cell(v) for v in row])
    ws.freeze_panes = "A2"
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def export_units_xlsx(db: Session) -> bytes:
    rows = [[u.building, u.floor, u.unit_no, money(u.area), money(u.rent_price),
             money(u.property_fee), UNIT_STATUS_LABELS.get(u.status, u.status),
             money((u.area or 0) * (u.rent_price or 0)), u.note]
            for u in db.query(LeasingUnit).order_by(
                LeasingUnit.building, LeasingUnit.floor, LeasingUnit.unit_no).all()]
    headers = ["楼栋", "楼层", "铺位号", "面积(㎡)", "租金单价(元/㎡/月)",
               "物业费单价(元/㎡/月)", "状态", "月租金(元)", "备注"]
    return _xlsx("铺位台账", headers, rows)


def export_leads_xlsx(db: Session) -> bytes:
    rows = [[x.company, x.contact, x.phone, x.industry, money(x.demand_area),
             money(x.budget), LEAD_STAGE_LABELS.get(x.stage, x.stage), x.owner,
             x.next_action_at.isoformat() if x.next_action_at else "", x.note]
            for x in db.query(LeasingLead).order_by(LeasingLead.id.desc()).all()]
    headers = ["客户公司", "联系人", "电话", "行业", "需求面积(㎡)", "预算(元/月)",
               "阶段", "负责人", "下次跟进", "备注"]
    return _xlsx("招商线索", headers, rows)


# ---------------- Agent 工具（只读） ----------------

def leasing_summary() -> dict:
    """招商看板摘要：铺位空置率 + 线索漏斗（只读）。"""
    with SessionLocal() as db:
        units = unit_summary(db)
        pipe = funnel(db)
        due = len(leads_due(db))
    return {"ok": True, "units": units, "funnel": pipe, "due_followups": due}
