# -*- coding: utf-8 -*-
"""工程物业：设备台账、合规到期、看板与导出。"""
from __future__ import annotations

import io
from datetime import date, timedelta

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ...database import SessionLocal
from ...utils_xlsx import safe_cell as _cell
from .models import FacilityCompliance, FacilityEquipment

CATEGORIES = ("A", "B", "C")
CATEGORY_LABELS = {"A": "A类关键设备", "B": "B类重要设备", "C": "C类一般设备"}
EQUIPMENT_STATUSES = ("running", "maintenance", "scrapped")
EQUIPMENT_STATUS_LABELS = {"running": "运行", "maintenance": "维修保养", "scrapped": "报废"}
COMPLIANCE_KINDS = ("annual_inspection", "fire", "maintenance", "other")
COMPLIANCE_KIND_LABELS = {
    "annual_inspection": "年检",
    "fire": "消防检查",
    "maintenance": "维保",
    "other": "其他",
}
COMPLIANCE_STATUS_LABELS = {"pending": "待办", "done": "已完成", "overdue": "已逾期"}


def _as_date(value, field: str = "日期") -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise ValueError(f"{field}格式非法，应为 YYYY-MM-DD") from exc


def equipment_to_dict(row: FacilityEquipment) -> dict:
    return {
        "id": row.id,
        "code": row.code,
        "name": row.name,
        "category": row.category,
        "category_label": CATEGORY_LABELS.get(row.category, row.category),
        "location": row.location or "",
        "install_date": row.install_date.isoformat() if row.install_date else None,
        "status": row.status,
        "status_label": EQUIPMENT_STATUS_LABELS.get(row.status, row.status),
        "note": row.note or "",
    }


def equipment_list(db: Session, category: str = "", status: str = "", keyword: str = "",
                   page: int = 1, page_size: int = 20) -> dict:
    q = db.query(FacilityEquipment)
    if category:
        q = q.filter(FacilityEquipment.category == category)
    if status:
        q = q.filter(FacilityEquipment.status == status)
    if keyword:
        q = q.filter(FacilityEquipment.name.contains(keyword, autoescape=True))
    total = q.count()
    rows = (q.order_by(FacilityEquipment.code.asc())
            .offset((page - 1) * page_size).limit(page_size).all())
    return {"total": total, "items": [equipment_to_dict(row) for row in rows],
            "page": page, "page_size": page_size}


def equipment_save(db: Session, data: dict, equipment_id: int | None = None) -> dict:
    row = db.get(FacilityEquipment, equipment_id) if equipment_id else FacilityEquipment()
    if row is None:
        raise LookupError("设备不存在")
    for field in ("code", "name", "location", "note"):
        if field in data and data[field] is not None:
            setattr(row, field, str(data[field]).strip())
    if not (row.code or "").strip():
        raise ValueError("设备编号不能为空")
    if not (row.name or "").strip():
        raise ValueError("设备名称不能为空")
    if "category" in data and data["category"] is not None:
        if data["category"] not in CATEGORIES:
            raise ValueError(f"未知设备等级：{data['category']}")
        row.category = data["category"]
    if "status" in data and data["status"] is not None:
        if data["status"] not in EQUIPMENT_STATUSES:
            raise ValueError(f"未知设备状态：{data['status']}")
        row.status = data["status"]
    if "install_date" in data:
        row.install_date = _as_date(data["install_date"], "安装日期")
    if equipment_id is None:
        db.add(row)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ValueError("设备编号已存在") from exc
    db.refresh(row)
    return equipment_to_dict(row)


def equipment_delete(db: Session, equipment_id: int) -> None:
    row = db.get(FacilityEquipment, equipment_id)
    if row is None:
        raise LookupError("设备不存在")
    if db.query(FacilityCompliance).filter(
            FacilityCompliance.equipment_id == equipment_id).first() is not None:
        raise ValueError("该设备仍有合规记录，请先删除或改派合规记录")
    try:
        from ..business_operations.models import OpsWorkOrder
    except ImportError:
        OpsWorkOrder = None
    if OpsWorkOrder is not None and db.query(OpsWorkOrder).filter(
            OpsWorkOrder.equipment_id == equipment_id).first() is not None:
        raise ValueError("该设备仍被工单引用，请先解除工单关联")
    db.delete(row)
    db.commit()


def _compliance_status(row: FacilityCompliance, as_of: date) -> str:
    if row.done_date is not None:
        return "done"
    if row.due_date < as_of:
        return "overdue"
    return "pending"


def compliance_to_dict(row: FacilityCompliance, equipment: FacilityEquipment | None,
                       as_of: date) -> dict:
    status = _compliance_status(row, as_of)
    return {
        "id": row.id,
        "equipment_id": row.equipment_id,
        "equipment_code": equipment.code if equipment else "",
        "equipment_name": equipment.name if equipment else "",
        "kind": row.kind,
        "kind_label": COMPLIANCE_KIND_LABELS.get(row.kind, row.kind),
        "title": row.title,
        "due_date": row.due_date.isoformat(),
        "done_date": row.done_date.isoformat() if row.done_date else None,
        "status": status,
        "status_label": COMPLIANCE_STATUS_LABELS[status],
        "overdue_days": max(0, (as_of - row.due_date).days) if status == "overdue" else 0,
        "note": row.note or "",
    }


def _compliance_rows(db: Session, kind: str = "", equipment_id: int | None = None,
                     as_of: date | None = None) -> tuple[list[dict], date]:
    cutoff = _as_date(as_of, "截止日期") or date.today()
    q = db.query(FacilityCompliance)
    if kind:
        q = q.filter(FacilityCompliance.kind == kind)
    if equipment_id is not None:
        q = q.filter(FacilityCompliance.equipment_id == equipment_id)
    rows = q.order_by(FacilityCompliance.due_date.asc(), FacilityCompliance.id.asc()).all()
    equipment = {row.id: row for row in db.query(FacilityEquipment).all()}
    items = [compliance_to_dict(row, equipment.get(row.equipment_id), cutoff) for row in rows]
    return items, cutoff


def compliance_list(db: Session, kind: str = "", status: str = "", equipment_id: int | None = None,
                    as_of: date | None = None, page: int = 1, page_size: int = 20) -> dict:
    items, cutoff = _compliance_rows(db, kind=kind, equipment_id=equipment_id, as_of=as_of)
    if status:
        items = [item for item in items if item["status"] == status]
    total = len(items)
    start = (page - 1) * page_size
    return {"total": total, "items": items[start:start + page_size],
            "page": page, "page_size": page_size, "as_of": cutoff.isoformat()}


def compliance_save(db: Session, data: dict, compliance_id: int | None = None) -> dict:
    row = db.get(FacilityCompliance, compliance_id) if compliance_id else FacilityCompliance()
    if row is None:
        raise LookupError("合规记录不存在")
    if "equipment_id" in data:
        equipment_id = data["equipment_id"]
        if equipment_id is not None and db.get(FacilityEquipment, int(equipment_id)) is None:
            raise LookupError("关联设备不存在")
        row.equipment_id = int(equipment_id) if equipment_id is not None else None
    if "kind" in data and data["kind"] is not None:
        if data["kind"] not in COMPLIANCE_KINDS:
            raise ValueError(f"未知合规类型：{data['kind']}")
        row.kind = data["kind"]
    if "title" in data and data["title"] is not None:
        row.title = str(data["title"]).strip()
    if "due_date" in data:
        row.due_date = _as_date(data["due_date"], "到期日期")
    if "done_date" in data:
        row.done_date = _as_date(data["done_date"], "完成日期")
    if "note" in data and data["note"] is not None:
        row.note = str(data["note"]).strip()
    if not (row.title or "").strip():
        raise ValueError("合规事项不能为空")
    if row.due_date is None:
        raise ValueError("到期日期不能为空")
    if compliance_id is None:
        db.add(row)
    db.commit()
    db.refresh(row)
    equipment = db.get(FacilityEquipment, row.equipment_id) if row.equipment_id else None
    return compliance_to_dict(row, equipment, date.today())


def compliance_delete(db: Session, compliance_id: int) -> None:
    row = db.get(FacilityCompliance, compliance_id)
    if row is None:
        raise LookupError("合规记录不存在")
    db.delete(row)
    db.commit()


def facility_summary(db: Session, as_of: date | None = None) -> dict:
    cutoff = _as_date(as_of, "截止日期") or date.today()
    equipment = db.query(FacilityEquipment).all()
    items, _ = _compliance_rows(db, as_of=cutoff)
    pending = [item for item in items if item["status"] == "pending"]
    return {
        "as_of": cutoff.isoformat(),
        "equipment_count": len(equipment),
        "category_a": sum(1 for row in equipment if row.category == "A"),
        "category_b": sum(1 for row in equipment if row.category == "B"),
        "category_c": sum(1 for row in equipment if row.category == "C"),
        "running": sum(1 for row in equipment if row.status == "running"),
        "maintenance": sum(1 for row in equipment if row.status == "maintenance"),
        "scrapped": sum(1 for row in equipment if row.status == "scrapped"),
        "compliance_total": len(items),
        "compliance_30": sum(
            1 for item in pending
            if cutoff <= date.fromisoformat(item["due_date"]) <= cutoff + timedelta(days=30)
        ),
        "overdue": sum(1 for item in items if item["status"] == "overdue"),
        "building_level": sum(1 for item in items if item["equipment_id"] is None),
    }


def _xlsx(title: str, headers: list[str], rows: list[list]) -> bytes:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = title[:31]
    ws.append(headers)
    for row in rows:
        ws.append([_cell(value) for value in row])
    ws.freeze_panes = "A2"
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def equipment_export(db: Session) -> bytes:
    rows = [[row.code, row.name, row.category_label if hasattr(row, "category_label")
             else CATEGORY_LABELS.get(row.category, row.category), row.location,
             row.install_date.isoformat() if row.install_date else "",
             EQUIPMENT_STATUS_LABELS.get(row.status, row.status), row.note]
            for row in db.query(FacilityEquipment).order_by(FacilityEquipment.code).all()]
    headers = ["设备编号", "设备名称", "等级", "位置", "安装日期", "状态", "备注"]
    return _xlsx("设备台账", headers, rows)


def compliance_export(db: Session, kind: str = "", status: str = "",
                      equipment_id: int | None = None, as_of: date | None = None) -> bytes:
    items, _ = _compliance_rows(db, kind=kind, equipment_id=equipment_id, as_of=as_of)
    if status:
        items = [item for item in items if item["status"] == status]
    rows = [[item["equipment_code"], item["kind_label"], item["title"], item["due_date"],
             item["done_date"] or "", item["status_label"], item["note"]]
            for item in items]
    headers = ["设备编号", "合规类型", "事项", "到期日期", "完成日期", "状态", "备注"]
    return _xlsx("合规到期", headers, rows)


def facility_summary_tool() -> dict:
    """工程物业摘要（只读）：设备数量/等级分布、30 天到期与逾期。"""
    with SessionLocal() as db:
        return {"ok": True, **facility_summary(db)}
