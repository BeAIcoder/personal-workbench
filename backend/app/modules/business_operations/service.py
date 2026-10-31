# -*- coding: utf-8 -*-
"""营运管理服务：合同台账、账单生成与收款、工单流转、看板与导出。"""
from __future__ import annotations

import io
import re
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ...database import SessionLocal
from .models import OpsBill, OpsContract, OpsWorkOrder, OpsWorkOrderFollowup

PERIOD_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
CONTRACT_STATUSES = ("active", "terminated")
BILL_STATUSES = ("unpaid", "partial", "paid")
WORK_ORDER_TYPES = ("repair", "complaint", "inspection")
WORK_ORDER_STATUSES = ("open", "processing", "done", "closed")
PRIORITIES = ("low", "medium", "high", "urgent")

# 状态机迁移矩阵（非法跳步/回跳 → 400 中文拒绝）
WORK_ORDER_TRANSITIONS = {
    "open": {"processing", "done"},
    "processing": {"done"},
    "done": {"closed", "processing"},  # done→processing 视为评审返工
    "closed": set(),  # 终态
}
CONTRACT_TRANSITIONS = {"active": {"terminated"}, "terminated": set()}

CONTRACT_STATUS_LABELS = {"active": "履约中", "terminated": "已终止"}
BILL_STATUS_LABELS = {"unpaid": "未收", "partial": "部分收款", "paid": "已收清"}
WORK_ORDER_TYPE_LABELS = {"repair": "报修", "complaint": "投诉", "inspection": "巡场"}
WORK_ORDER_STATUS_LABELS = {
    "open": "待处理", "processing": "处理中", "done": "已完成", "closed": "已关闭",
}
PRIORITY_LABELS = {"low": "低", "medium": "中", "high": "高", "urgent": "紧急"}


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


def _contract_dict(c: OpsContract) -> dict:
    today = date.today()
    days = (c.end_date - today).days if c.end_date else None
    return {
        "id": c.id,
        "contract_no": c.contract_no,
        "tenant": c.tenant,
        "unit_no": c.unit_no,
        "unit_id": c.unit_id,
        "area": money(c.area),
        "start_date": c.start_date.isoformat() if c.start_date else None,
        "end_date": c.end_date.isoformat() if c.end_date else None,
        "rent_monthly": money(c.rent_monthly),
        "property_fee_monthly": money(c.property_fee_monthly),
        "monthly_total": money((c.rent_monthly or 0) + (c.property_fee_monthly or 0)),
        "deposit": money(c.deposit),
        "status": c.status,
        "status_label": CONTRACT_STATUS_LABELS.get(c.status, c.status),
        "signed_at": c.signed_at.isoformat() if c.signed_at else None,
        "days_to_expire": days,
        "expiring": bool(c.status == "active" and days is not None and 0 <= days <= 30),
        "expired": bool(c.status == "active" and days is not None and days < 0),
        "note": c.note or "",
    }


def contract_list(db: Session, status: str = "", tenant: str = "", keyword: str = "",
                  page: int = 1, page_size: int = 20) -> dict:
    q = db.query(OpsContract)
    if status:
        q = q.filter(OpsContract.status == status)
    if tenant:
        q = q.filter(OpsContract.tenant.contains(tenant, autoescape=True))
    if keyword:
        q = q.filter(OpsContract.contract_no.contains(keyword, autoescape=True))
    total = q.count()
    rows = (q.order_by(OpsContract.end_date.asc())
            .offset((page - 1) * page_size).limit(page_size).all())
    return {"total": total, "items": [_contract_dict(c) for c in rows],
            "page": page, "page_size": page_size}


def _resolve_unit(db: Session, unit_id: int | None, exclude_contract_id: int | None = None) -> tuple[int | None, str]:
    """勾稽铺位：unit_id 必须真实存在，且同铺位不允许两份生效合同（防重复出租）。

    返回 (unit_id, unit_no)；unit_id 为 None 时返回 (None, "")。
    """
    if unit_id is None:
        return None, ""
    try:
        from ..business_leasing.models import LeasingUnit
    except ImportError:
        raise ValueError("招商模块不可用，无法关联铺位") from None
    unit = db.get(LeasingUnit, int(unit_id))
    if unit is None:
        raise ValueError(f"铺位不存在：unit_id={unit_id}")
    dup = (db.query(OpsContract)
           .filter(OpsContract.unit_id == int(unit_id),
                   OpsContract.status == "active",
                   OpsContract.id != (exclude_contract_id or -1))
           .first())
    if dup is not None:
        raise ValueError(
            f"铺位 {unit.unit_no} 已存在生效合同（{dup.contract_no}），不能重复出租；"
            "请先终止旧合同或更换铺位")
    return int(unit.id), str(unit.unit_no or "")


def _lease_terms(db: Session, contract: OpsContract) -> dict:
    """从招商签约线索读取租决参数（免租期 / 年递增率 / 租金形式）。

    勾稽链：合同.unit_id → LeasingLead.unit_id 且 status='signed'（取最新一条）。
    未关联铺位或无签约线索时返回空 dict（保持旧的固定租金出账行为）。
    """
    if contract.unit_id is None:
        return {}
    try:
        from ..business_leasing.models import LeasingLead
    except ImportError:
        return {}
    lead = (db.query(LeasingLead)
            .filter(LeasingLead.unit_id == contract.unit_id,
                    LeasingLead.stage == "signed")
            .order_by(LeasingLead.id.desc())
            .first())
    if lead is None:
        return {}
    return {
        "free_rent_months": int(lead.free_rent_months or 0),
        # 递增率为百分数语义：5 表示每年 5%
        "escalation_rate": float(lead.escalation_rate or 0.0),
        "rent_form": str(lead.rent_form or "fixed"),
    }


def contract_save(db: Session, data: dict, contract_id: int | None = None) -> dict:
    c = db.get(OpsContract, contract_id) if contract_id else OpsContract()
    if c is None:
        raise LookupError("合同不存在")
    for field in ("contract_no", "tenant", "unit_no", "note"):
        if field in data and data[field] is not None:
            setattr(c, field, str(data[field]).strip())
    for field in ("area", "rent_monthly", "property_fee_monthly", "deposit"):
        if field in data and data[field] is not None:
            if float(data[field]) < 0:
                raise ValueError(f"{field} 不能为负数")
            setattr(c, field, money(data[field]))
    for field in ("start_date", "end_date", "signed_at"):
        if field in data:
            setattr(c, field, _as_date(data[field], field))
    if "unit_id" in data:
        resolved_id, resolved_no = _resolve_unit(
            db, data.get("unit_id"), exclude_contract_id=contract_id)
        c.unit_id = resolved_id
        # 关联铺位时铺位号以招商台账为准（口径一致）；未填 unit_no 才回填
        if resolved_id is not None and not str(data.get("unit_no") or "").strip():
            c.unit_no = resolved_no
    if "status" in data and data["status"] is not None:
        new_status = data["status"]
        if new_status not in CONTRACT_STATUSES:
            raise ValueError(f"未知合同状态：{new_status}")
        if (contract_id is not None and new_status != c.status
                and new_status not in CONTRACT_TRANSITIONS.get(c.status, set())):
            raise ValueError(f"非法状态迁移：{CONTRACT_STATUS_LABELS.get(c.status, c.status)} 不能改为 "
                             f"{CONTRACT_STATUS_LABELS.get(new_status, new_status)}，"
                             f"已终止合同不能重新启用（请走新合同）")
        c.status = new_status
    if not c.contract_no:
        raise ValueError("合同编号不能为空")
    if c.start_date is None or c.end_date is None:
        raise ValueError("合同起止日期不能为空")
    if c.end_date < c.start_date:
        raise ValueError("合同结束日期不能早于开始日期")
    if contract_id is None:
        db.add(c)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ValueError("合同编号已存在") from exc
    db.refresh(c)
    return _contract_dict(c)


def contract_delete(db: Session, contract_id: int) -> None:
    c = db.get(OpsContract, contract_id)
    if c is None:
        raise LookupError("合同不存在")
    paid_count = (
        db.query(OpsBill)
        .filter(OpsBill.contract_id == contract_id, OpsBill.paid_amount > 0)
        .count()
    )
    if paid_count:
        # P1 修复：已收款账单是收款凭据，硬删会让实收从库中消失且不可恢复
        raise ValueError(
            f"合同下有 {paid_count} 张已收款账单，删除会丢失收款记录；"
            "请先完成退款/冲正或导出留底后再删除"
        )
    db.query(OpsBill).filter(OpsBill.contract_id == contract_id).delete()
    db.delete(c)
    db.commit()


def contracts_expiring(db: Session, days: int = 30) -> list[dict]:
    today = date.today()
    rows = (db.query(OpsContract)
            .filter(OpsContract.status == "active")
            .filter(OpsContract.end_date >= today)
            .filter(OpsContract.end_date <= today + timedelta(days=days))
            .order_by(OpsContract.end_date.asc()).all())
    return [_contract_dict(c) for c in rows]


# ---------------- 账单 ----------------

def _bill_dict(b: OpsBill, c: OpsContract | None = None) -> dict:
    return {
        "id": b.id,
        "contract_id": b.contract_id,
        "contract_no": c.contract_no if c else "",
        "tenant": c.tenant if c else "",
        "unit_no": c.unit_no if c else "",
        "period": b.period,
        "rent_amount": money(b.rent_amount),
        "property_amount": money(b.property_amount),
        "other_amount": money(b.other_amount),
        "total_amount": money(b.total_amount),
        "paid_amount": money(b.paid_amount),
        "arrears": money((b.total_amount or 0) - (b.paid_amount or 0)),
        "status": b.status,
        "status_label": BILL_STATUS_LABELS.get(b.status, b.status),
        "due_date": b.due_date.isoformat() if b.due_date else None,
        "paid_at": b.paid_at.isoformat() if b.paid_at else None,
        "note": b.note or "",
    }


def generate_bill(db: Session, contract_id: int, period: str,
                  other_amount: float = 0.0, due_date=None) -> dict:
    if not PERIOD_RE.match(str(period or "")):
        raise ValueError("所属期格式非法，应为 YYYY-MM")
    if float(other_amount or 0) < 0:
        raise ValueError("其他费用不能为负数")
    c = db.get(OpsContract, contract_id)
    if c is None:
        raise LookupError("合同不存在")
    if c.status != "active":
        raise ValueError("已终止合同不能生成账单")
    year, month = (int(part) for part in period.split("-"))
    period_start = date(year, month, 1)
    next_month = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    period_end = next_month - timedelta(days=1)
    if period_end < c.start_date or period_start > c.end_date:
        raise ValueError("所属期与合同租期不相交，不能生成账单")
    # 勾稽租决参数：免租期不出租金（物业费照收）、按年递增、抽成合同拒绝按固定租金出账
    rent = money(c.rent_monthly)
    terms = _lease_terms(db, c)
    if terms:
        if terms["rent_form"] in ("percentage", "guaranteed_percentage"):
            raise ValueError(
                "该铺位签约为抽成/保底抽成租金（无固定月租），按固定租金出账会算错；"
                "请解除铺位关联或手工登记其他费用")
        # 合同月序号：起租月为第 1 个月
        month_index = (year - c.start_date.year) * 12 + (month - c.start_date.month) + 1
        if month_index <= terms["free_rent_months"]:
            rent = money(0)
        elif terms["escalation_rate"] > 0:
            years_elapsed = (month_index - 1) // 12
            rent = money(rent * (1.0 + terms["escalation_rate"] / 100.0) ** years_elapsed)
    total = money(rent + (c.property_fee_monthly or 0) + float(other_amount or 0))
    bill = OpsBill(
        contract_id=contract_id,
        period=period,
        rent_amount=rent,
        property_amount=money(c.property_fee_monthly),
        other_amount=money(other_amount),
        total_amount=total,
        paid_amount=0.0,
        status="unpaid",
        due_date=_as_date(due_date, "应收日期") or date.fromisoformat(f"{period}-10"),
    )
    db.add(bill)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ValueError("该合同该期间账单已存在，不能重复生成") from exc
    db.refresh(bill)
    return _bill_dict(bill, c)


def bill_list(db: Session, period: str = "", status: str = "", contract_id: int | None = None,
              page: int = 1, page_size: int = 20) -> dict:
    q = db.query(OpsBill, OpsContract).join(OpsContract, OpsContract.id == OpsBill.contract_id)
    if period:
        q = q.filter(OpsBill.period == period)
    if status:
        q = q.filter(OpsBill.status == status)
    if contract_id:
        q = q.filter(OpsBill.contract_id == contract_id)
    total = q.count()
    rows = (q.order_by(OpsBill.period.desc(), OpsBill.id.desc())
            .offset((page - 1) * page_size).limit(page_size).all())
    return {"total": total, "items": [_bill_dict(b, c) for b, c in rows],
            "page": page, "page_size": page_size}


def bill_aging(db: Session, as_of=None) -> dict:
    cutoff = _as_date(as_of, "账龄截止日") or date.today()
    rows = (db.query(OpsBill, OpsContract)
            .join(OpsContract, OpsContract.id == OpsBill.contract_id).all())
    buckets = {
        key: {"amount": 0.0, "count": 0, "contracts": set()}
        for key in ("not_due", "1_30", "31_90", "over_90")
    }
    items = []
    for bill, contract in rows:
        arrears = money((bill.total_amount or 0) - (bill.paid_amount or 0))
        if arrears <= 0:
            continue
        overdue_days = (cutoff - bill.due_date).days if bill.due_date else 0
        if overdue_days <= 0:
            bucket = "not_due"
        elif overdue_days <= 30:
            bucket = "1_30"
        elif overdue_days <= 90:
            bucket = "31_90"
        else:
            bucket = "over_90"
        buckets[bucket]["amount"] += arrears
        buckets[bucket]["count"] += 1
        buckets[bucket]["contracts"].add(bill.contract_id)
        items.append({**_bill_dict(bill, contract), "arrears": arrears,
                      "overdue_days": overdue_days, "bucket": bucket})
    items.sort(key=lambda item: (-item["overdue_days"], -item["id"]))
    for bucket in buckets.values():
        bucket["amount"] = money(bucket["amount"])
        bucket["contracts"] = len(bucket["contracts"])
    return {"as_of": cutoff.isoformat(), "buckets": buckets, "items": items}


def pay_bill(db: Session, bill_id: int, amount: float, paid_at=None) -> dict:
    """登记收款。

    并发保护（P1 修复）：旧实现的读-改-写在两个会话交错时可互相覆盖、静默丢一笔实收；
    改为条件更新——仅当 paid_amount 仍等于读到的旧值时才写入，写不进（rowcount=0）
    即说明有并发修改，回滚并要求重试，不再丢单。
    """
    amount = money(amount)
    if amount <= 0:
        raise ValueError("收款金额须大于 0")
    bill = db.get(OpsBill, bill_id)
    if bill is None:
        raise LookupError("账单不存在")
    if bill.status == "paid":
        raise ValueError("该账单已收清，不能重复收款")
    old_paid = money(bill.paid_amount or 0)
    new_paid = money(old_paid + amount)
    if new_paid > money(bill.total_amount) + 0.005:
        raise ValueError(f"收款金额超过应收：应收 {money(bill.total_amount):.2f}，"
                         f"已收 {money(bill.paid_amount):.2f}，本次 {amount:.2f}")
    final_paid = min(new_paid, money(bill.total_amount))
    new_status = "paid" if final_paid >= money(bill.total_amount) - 0.005 else "partial"
    updated = (
        db.query(OpsBill)
        .filter(OpsBill.id == bill_id, OpsBill.paid_amount == old_paid)
        .update(
            {
                "paid_amount": final_paid,
                "status": new_status,
                "paid_at": _as_date(paid_at, "收款日期") or date.today(),
            },
            synchronize_session=False,
        )
    )
    if updated == 0:
        db.rollback()
        raise ValueError("账单已被其他操作修改（并发冲突），请刷新后重试")
    db.commit()
    db.refresh(bill)
    return _bill_dict(bill, db.get(OpsContract, bill.contract_id))


# ---------------- 工单 ----------------

def _equipment_code(db: Session, equipment_id: int | None) -> str:
    if equipment_id is None:
        return ""
    try:
        from ..business_facility.models import FacilityEquipment
    except ImportError:
        return ""
    row = db.get(FacilityEquipment, equipment_id)
    return row.code if row else ""


def _work_order_dict(w: OpsWorkOrder, parent_code: str = "", now: datetime | None = None,
                     equipment_code: str = "") -> dict:
    now = now or datetime.now()
    overdue = bool(w.due_at and w.status in ("open", "processing") and w.due_at < now)
    return {
        "id": w.id, "code": w.code, "type": w.type,
        "type_label": WORK_ORDER_TYPE_LABELS.get(w.type, w.type),
        "location": w.location or "", "tenant": w.tenant or "",
        "priority": w.priority, "priority_label": PRIORITY_LABELS.get(w.priority, w.priority),
        "status": w.status,
        "status_label": WORK_ORDER_STATUS_LABELS.get(w.status, w.status),
        "description": w.description or "", "assignee": w.assignee or "",
        "created_at": str(w.created_at),
        "due_at": str(w.due_at) if w.due_at else None,
        "overdue": overdue,
        "parent_id": w.parent_id,
        "parent_code": parent_code,
        "equipment_id": w.equipment_id,
        "equipment_code": equipment_code,
        "closed_at": str(w.closed_at) if w.closed_at else None,
    }


def _work_order_query(db: Session, status: str = "", type_: str = "", keyword: str = ""):
    q = db.query(OpsWorkOrder)
    if status:
        q = q.filter(OpsWorkOrder.status == status)
    if type_:
        q = q.filter(OpsWorkOrder.type == type_)
    if keyword:
        q = q.filter(OpsWorkOrder.description.contains(keyword, autoescape=True))
    return q


def _parent_codes(db: Session, rows: list[OpsWorkOrder]) -> dict[int, str]:
    parent_ids = {w.parent_id for w in rows if w.parent_id is not None}
    if not parent_ids:
        return {}
    parents = db.query(OpsWorkOrder).filter(OpsWorkOrder.id.in_(parent_ids)).all()
    return {parent.id: parent.code for parent in parents}


def work_order_list(db: Session, status: str = "", type_: str = "", keyword: str = "",
                    page: int = 1, page_size: int = 20) -> dict:
    q = _work_order_query(db, status=status, type_=type_, keyword=keyword)
    total = q.count()
    rows = (q.order_by(OpsWorkOrder.id.desc())
            .offset((page - 1) * page_size).limit(page_size).all())
    parents = _parent_codes(db, rows)
    return {"total": total, "items": [
        _work_order_dict(w, parents.get(w.parent_id, ""),
                         equipment_code=_equipment_code(db, w.equipment_id)) for w in rows
    ], "page": page, "page_size": page_size}


def work_order_save(db: Session, data: dict, order_id: int | None = None) -> dict:
    w = db.get(OpsWorkOrder, order_id) if order_id else OpsWorkOrder()
    if w is None:
        raise LookupError("工单不存在")
    old_status = w.status
    for field in ("location", "tenant", "description", "assignee"):
        if field in data and data[field] is not None:
            setattr(w, field, str(data[field]).strip())
    if "type" in data and data["type"] is not None:
        if data["type"] not in WORK_ORDER_TYPES:
            raise ValueError(f"未知工单类型：{data['type']}")
        w.type = data["type"]
    if "priority" in data and data["priority"] is not None:
        if data["priority"] not in PRIORITIES:
            raise ValueError(f"未知优先级：{data['priority']}")
        w.priority = data["priority"]
    if "due_at" in data:
        w.due_at = data["due_at"]
    if "parent_id" in data:
        parent_id = data["parent_id"]
        if parent_id is not None:
            if parent_id == order_id:
                raise ValueError("父工单不能是工单自身")
            if db.get(OpsWorkOrder, parent_id) is None:
                raise ValueError("父工单不存在")
        w.parent_id = parent_id
    if "equipment_id" in data:
        equipment_id = data["equipment_id"]
        if equipment_id is not None:
            try:
                from ..business_facility.models import FacilityEquipment
            except ImportError as exc:
                raise ValueError("工程设备模块不可用，无法关联设备") from exc
            if db.get(FacilityEquipment, int(equipment_id)) is None:
                raise ValueError("关联设备不存在")
        w.equipment_id = int(equipment_id) if equipment_id is not None else None
    if "status" in data and data["status"] is not None:
        new_status = data["status"]
        if new_status not in WORK_ORDER_STATUSES:
            raise ValueError(f"未知工单状态：{new_status}")
        if (order_id is not None and new_status != old_status
                and new_status not in WORK_ORDER_TRANSITIONS.get(old_status, set())):
            raise ValueError(f"非法状态迁移：{WORK_ORDER_STATUS_LABELS.get(old_status, old_status)} "
                             f"不能改为 {WORK_ORDER_STATUS_LABELS.get(new_status, new_status)}，"
                             f"允许：待处理→处理中/已完成、处理中→已完成、已完成→已关闭/评审返工")
        w.status = new_status
    if not (w.description or "").strip():
        raise ValueError("工单描述不能为空")
    if w.status != old_status:
        if w.status in ("done", "closed") and old_status not in ("done", "closed"):
            w.closed_at = datetime.now()
        elif w.status not in ("done", "closed") and old_status in ("done", "closed"):
            w.closed_at = None
    if order_id is None:
        db.add(w)
        db.flush()
        w.code = f"WO{w.id:05d}"
    db.commit()
    db.refresh(w)
    parent = db.get(OpsWorkOrder, w.parent_id) if w.parent_id else None
    return _work_order_dict(w, parent.code if parent else "",
                            equipment_code=_equipment_code(db, w.equipment_id))


def work_order_delete(db: Session, order_id: int) -> None:
    w = db.get(OpsWorkOrder, order_id)
    if w is None:
        raise LookupError("工单不存在")
    child = db.query(OpsWorkOrder).filter(OpsWorkOrder.parent_id == order_id).first()
    if child is not None:
        raise HTTPException(status_code=409, detail="存在关联子工单，请先解除子工单关联后再删除")
    db.query(OpsWorkOrderFollowup).filter(
        OpsWorkOrderFollowup.work_order_id == order_id
    ).delete()
    db.delete(w)
    db.commit()


def _followup_dict(row: OpsWorkOrderFollowup) -> dict:
    return {
        "id": row.id,
        "work_order_id": row.work_order_id,
        "handled_at": str(row.handled_at),
        "content": row.content or "",
        "result": row.result or "",
        "satisfaction": row.satisfaction,
        "created_at": str(row.created_at),
    }


def work_order_followup_list(db: Session, order_id: int) -> list[dict]:
    if db.get(OpsWorkOrder, order_id) is None:
        raise LookupError("工单不存在")
    rows = (db.query(OpsWorkOrderFollowup)
            .filter(OpsWorkOrderFollowup.work_order_id == order_id)
            .order_by(OpsWorkOrderFollowup.handled_at.desc(),
                      OpsWorkOrderFollowup.id.desc()).all())
    return [_followup_dict(row) for row in rows]


def work_order_followup_save(db: Session, order_id: int, data: dict,
                             followup_id: int | None = None) -> dict:
    if db.get(OpsWorkOrder, order_id) is None:
        raise LookupError("工单不存在")
    row = (db.get(OpsWorkOrderFollowup, followup_id) if followup_id
           else OpsWorkOrderFollowup(work_order_id=order_id))
    if row is None or row.work_order_id != order_id:
        raise LookupError("跟进记录不存在")
    if "handled_at" in data and data["handled_at"] is not None:
        row.handled_at = data["handled_at"]
    elif followup_id is None:
        row.handled_at = datetime.now()
    for field in ("content", "result"):
        if field in data and data[field] is not None:
            setattr(row, field, str(data[field]).strip())
    if "satisfaction" in data:
        row.satisfaction = data["satisfaction"]
    if not (row.content or "").strip():
        raise ValueError("跟进内容不能为空")
    if followup_id is None:
        db.add(row)
    db.commit()
    db.refresh(row)
    return _followup_dict(row)


def work_order_followup_delete(db: Session, order_id: int, followup_id: int) -> None:
    row = db.get(OpsWorkOrderFollowup, followup_id)
    if row is None or row.work_order_id != order_id:
        raise LookupError("跟进记录不存在")
    db.delete(row)
    db.commit()


# ---------------- 看板 ----------------

def ops_summary(db: Session, period: str = "") -> dict:
    if period and not PERIOD_RE.match(period):
        raise ValueError("所属期格式非法，应为 YYYY-MM")
    contracts = db.query(OpsContract).all()
    active = [c for c in contracts if c.status == "active"]
    q = db.query(OpsBill)
    if period:
        q = q.filter(OpsBill.period == period)
    bills = q.all()
    receivable = money(sum(float(b.total_amount or 0) for b in bills))
    received = money(sum(float(b.paid_amount or 0) for b in bills))
    today = date.today()
    expiring = [c for c in active if c.end_date and 0 <= (c.end_date - today).days <= 30]
    expired = [c for c in active if c.end_date and c.end_date < today]
    arrears_contracts = {
        b.contract_id for b in bills
        if money((b.total_amount or 0) - (b.paid_amount or 0)) > 0
    }
    open_orders = (db.query(OpsWorkOrder)
                   .filter(OpsWorkOrder.status.in_(("open", "processing"))).count())
    return {
        "contracts_active": len(active),
        "leased_area": money(sum(float(c.area or 0) for c in active)),
        "receivable": receivable,
        "received": received,
        "collection_rate": round(received / receivable, 4) if receivable else 0.0,
        "arrears": money(receivable - received),
        "arrears_contracts": len(arrears_contracts),
        "expiring_30": len(expiring),
        "expired_active_contracts": len(expired),
        "open_work_orders": open_orders,
    }


def ops_bills_export(db: Session, period: str = "") -> bytes:
    if period and not PERIOD_RE.match(period):
        raise ValueError("所属期格式非法，应为 YYYY-MM")
    q = db.query(OpsBill, OpsContract).join(OpsContract, OpsContract.id == OpsBill.contract_id)
    if period:
        q = q.filter(OpsBill.period == period)
    rows = [[c.contract_no, c.tenant, c.unit_no, b.period, money(b.rent_amount),
             money(b.property_amount), money(b.other_amount), money(b.total_amount),
             money(b.paid_amount), money((b.total_amount or 0) - (b.paid_amount or 0)),
             BILL_STATUS_LABELS.get(b.status, b.status),
             b.due_date.isoformat() if b.due_date else ""]
            for b, c in q.order_by(OpsBill.period.desc(), OpsBill.id.desc()).all()]
    headers = ["合同编号", "租户", "铺位", "所属期", "租金", "物业费", "其他",
               "应收合计", "已收", "欠费", "状态", "应收日期"]
    return _xlsx("账单明细", headers, rows)


def ops_work_orders_export(db: Session, status: str = "", type_: str = "",
                           keyword: str = "") -> bytes:
    q = _work_order_query(db, status=status, type_=type_, keyword=keyword)
    work_orders = q.order_by(OpsWorkOrder.id.desc()).all()
    parents = _parent_codes(db, work_orders)
    rows = []
    for order in work_orders:
        item = _work_order_dict(order, parents.get(order.parent_id, ""),
                                equipment_code=_equipment_code(db, order.equipment_id))
        rows.append([
            item["code"], item["type_label"], item["location"], item["tenant"],
            item["description"], item["priority_label"], item["status_label"],
            item["assignee"], item["created_at"], item["due_at"] or "",
            "是" if item["overdue"] else "否", item["closed_at"] or "",
            item["parent_code"], item["equipment_code"],
        ])
    headers = ["工单号", "类型", "位置", "租户", "描述", "优先级", "状态", "责任人",
               "创建时间", "要求完成时间", "是否超期", "首次完成时间", "父工单号", "设备编号"]
    return _xlsx("工单台账", headers, rows)


def ops_contracts_export(db: Session) -> bytes:
    rows = [[c.contract_no, c.tenant, c.unit_no, money(c.area),
             c.start_date.isoformat() if c.start_date else "",
             c.end_date.isoformat() if c.end_date else "",
             money(c.rent_monthly), money(c.property_fee_monthly), money(c.deposit),
             CONTRACT_STATUS_LABELS.get(c.status, c.status), c.note]
            for c in db.query(OpsContract).order_by(OpsContract.end_date).all()]
    headers = ["合同编号", "租户", "铺位", "面积(㎡)", "开始日期", "结束日期",
               "月租金", "月物业费", "押金", "状态", "备注"]
    return _xlsx("合同台账", headers, rows)


def _xlsx(title: str, headers: list[str], rows: list[list]) -> bytes:
    from openpyxl import Workbook

    def cell(v):
        if isinstance(v, str) and v[:1] in ("=", "+", "-", "@"):
            return "'" + v
        return v

    wb = Workbook()
    ws = wb.active
    ws.title = title[:31]
    ws.append(headers)
    for row in rows:
        ws.append([cell(v) for v in row])
    ws.freeze_panes = "A2"
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def ops_summary_tool(period: str = "") -> dict:
    """营运看板摘要（只读）：可按 YYYY-MM 期间筛选账单，缺省保持全部期间。"""
    with SessionLocal() as db:
        return {"ok": True, **ops_summary(db, period=period)}
