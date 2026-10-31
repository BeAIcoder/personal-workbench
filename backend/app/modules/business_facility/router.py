# -*- coding: utf-8 -*-
"""工程物业接口：/api/business/facility。"""
from __future__ import annotations

import io
from datetime import date
from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ...database import get_db
from . import service

router = APIRouter(tags=["经营模块·工程物业"])

Category = Literal["A", "B", "C"]
EquipmentStatus = Literal["running", "maintenance", "scrapped"]
ComplianceKind = Literal["annual_inspection", "fire", "maintenance", "other"]


class EquipmentIn(BaseModel):
    code: str = Field(min_length=1, max_length=50)
    name: str = Field(min_length=1, max_length=200)
    category: Category = "C"
    location: str = Field(default="", max_length=200)
    install_date: date | None = None
    status: EquipmentStatus = "running"
    note: str = Field(default="", max_length=2000)


class EquipmentUpdate(BaseModel):
    code: str | None = Field(default=None, min_length=1, max_length=50)
    name: str | None = Field(default=None, min_length=1, max_length=200)
    category: Category | None = None
    location: str | None = Field(default=None, max_length=200)
    install_date: date | None = None
    status: EquipmentStatus | None = None
    note: str | None = Field(default=None, max_length=2000)


class ComplianceIn(BaseModel):
    equipment_id: int | None = Field(default=None, ge=1)
    kind: ComplianceKind = "other"
    title: str = Field(min_length=1, max_length=200)
    due_date: date
    done_date: date | None = None
    note: str = Field(default="", max_length=2000)


class ComplianceUpdate(BaseModel):
    equipment_id: int | None = Field(default=None, ge=1)
    kind: ComplianceKind | None = None
    title: str | None = Field(default=None, min_length=1, max_length=200)
    due_date: date | None = None
    done_date: date | None = None
    note: str | None = Field(default=None, max_length=2000)


def _download(data: bytes, filename: str) -> StreamingResponse:
    ascii_name = "".join(c for c in filename if 32 <= ord(c) <= 126 and c not in '"\\') or "export.xlsx"
    return StreamingResponse(
        io.BytesIO(data),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename=\"{ascii_name}\"; "
                                        f"filename*=UTF-8''{quote(filename)}"},
    )


@router.get("/summary", summary="工程看板（设备等级/30 天到期/逾期）")
def summary(as_of: date | None = None, db: Session = Depends(get_db)):
    try:
        return service.facility_summary(db, as_of=as_of)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/equipment", summary="设备台账（分页）")
def equipment(page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=200),
              category: str = "", status: str = "", keyword: str = "",
              db: Session = Depends(get_db)):
    return service.equipment_list(db, category=category, status=status, keyword=keyword,
                                  page=page, page_size=page_size)


@router.post("/equipment", status_code=201, summary="新建设备")
def create_equipment(payload: EquipmentIn, db: Session = Depends(get_db)):
    try:
        return service.equipment_save(db, payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.put("/equipment/{equipment_id}", summary="更新设备")
def update_equipment(equipment_id: int, payload: EquipmentUpdate, db: Session = Depends(get_db)):
    data = payload.model_dump(exclude_unset=True, exclude_none=True)
    if "install_date" in payload.model_fields_set and payload.install_date is None:
        data["install_date"] = None
    try:
        return service.equipment_save(db, data, equipment_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.delete("/equipment/{equipment_id}", summary="删除设备（有合规记录时拒绝）")
def delete_equipment(equipment_id: int, db: Session = Depends(get_db)):
    try:
        service.equipment_delete(db, equipment_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"ok": True}


@router.get("/compliance", summary="合规到期台账（分页，状态派生）")
def compliance(page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=200),
               kind: str = "", status: str = "", equipment_id: int | None = None,
               as_of: date | None = None, db: Session = Depends(get_db)):
    try:
        return service.compliance_list(db, kind=kind, status=status,
                                       equipment_id=equipment_id, as_of=as_of,
                                       page=page, page_size=page_size)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/compliance", status_code=201, summary="新增合规事项")
def create_compliance(payload: ComplianceIn, db: Session = Depends(get_db)):
    try:
        return service.compliance_save(db, payload.model_dump())
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.put("/compliance/{compliance_id}", summary="更新合规事项")
def update_compliance(compliance_id: int, payload: ComplianceUpdate,
                      db: Session = Depends(get_db)):
    data = payload.model_dump(exclude_unset=True)
    if "due_date" in data and data["due_date"] is None:
        raise HTTPException(status_code=400, detail="到期日期不能为空")
    try:
        return service.compliance_save(db, data, compliance_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/compliance/{compliance_id}", summary="删除合规事项")
def delete_compliance(compliance_id: int, db: Session = Depends(get_db)):
    try:
        service.compliance_delete(db, compliance_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"ok": True}


@router.get("/export/equipment.xlsx", summary="导出设备台账")
def export_equipment(db: Session = Depends(get_db)):
    return _download(service.equipment_export(db), "设备台账.xlsx")


@router.get("/export/compliance.xlsx", summary="导出合规到期台账")
def export_compliance(kind: str = "", status: str = "", equipment_id: int | None = None,
                      as_of: date | None = None, db: Session = Depends(get_db)):
    try:
        data = service.compliance_export(db, kind=kind, status=status,
                                         equipment_id=equipment_id, as_of=as_of)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _download(data, "合规到期台账.xlsx")
