# -*- coding: utf-8 -*-
"""招商管理接口：/api/business/leasing（前缀由注册器按 manifest.prefix 挂载）。"""
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

router = APIRouter(tags=["经营模块·招商管理"])

UnitStatus = Literal["vacant", "negotiating", "reserved", "leased"]
LeadStage = Literal["lead", "contact", "offer", "intent", "signed", "lost"]
RentForm = Literal["fixed", "percentage", "guaranteed_percentage"]


class UnitIn(BaseModel):
    building: str = Field(min_length=1, max_length=100)
    floor: str = Field(default="", max_length=20)
    unit_no: str = Field(min_length=1, max_length=50)
    area: float = Field(default=0, ge=0, le=10_000_000)
    rent_price: float = Field(default=0, ge=0, le=10_000_000)
    property_fee: float = Field(default=0, ge=0, le=10_000_000)
    status: UnitStatus = "vacant"
    opened_at: date | None = None
    note: str = Field(default="", max_length=2000)


class UnitUpdate(BaseModel):
    building: str | None = Field(default=None, min_length=1, max_length=100)
    floor: str | None = Field(default=None, max_length=20)
    unit_no: str | None = Field(default=None, min_length=1, max_length=50)
    area: float | None = Field(default=None, ge=0, le=10_000_000)
    rent_price: float | None = Field(default=None, ge=0, le=10_000_000)
    property_fee: float | None = Field(default=None, ge=0, le=10_000_000)
    status: UnitStatus | None = None
    opened_at: date | None = None
    note: str | None = Field(default=None, max_length=2000)


class LeadIn(BaseModel):
    company: str = Field(min_length=1, max_length=200)
    contact: str = Field(default="", max_length=100)
    phone: str = Field(default="", max_length=50)
    industry: str = Field(default="", max_length=100)
    demand_area: float = Field(default=0, ge=0, le=10_000_000)
    budget: float = Field(default=0, ge=0, le=1_000_000_000)
    stage: LeadStage = "lead"
    unit_id: int | None = Field(default=None, ge=1)
    rent_form: RentForm | None = None
    free_rent_months: int | None = Field(default=None, ge=0, le=120)
    escalation_rate: float | None = Field(default=None, ge=0, le=1_000_000)
    fitout_subsidy: float | None = Field(default=None, ge=0, le=1_000_000_000)
    lease_term_months: int | None = Field(default=None, ge=1, le=600)
    owner: str = Field(default="", max_length=50)
    next_action_at: date | None = None
    note: str = Field(default="", max_length=2000)


class LeadUpdate(BaseModel):
    company: str | None = Field(default=None, min_length=1, max_length=200)
    contact: str | None = Field(default=None, max_length=100)
    phone: str | None = Field(default=None, max_length=50)
    industry: str | None = Field(default=None, max_length=100)
    demand_area: float | None = Field(default=None, ge=0, le=10_000_000)
    budget: float | None = Field(default=None, ge=0, le=1_000_000_000)
    stage: LeadStage | None = None
    unit_id: int | None = Field(default=None, ge=1)
    rent_form: RentForm | None = None
    free_rent_months: int | None = Field(default=None, ge=0, le=120)
    escalation_rate: float | None = Field(default=None, ge=0, le=1_000_000)
    fitout_subsidy: float | None = Field(default=None, ge=0, le=1_000_000_000)
    lease_term_months: int | None = Field(default=None, ge=1, le=600)
    owner: str | None = Field(default=None, max_length=50)
    next_action_at: date | None = None
    note: str | None = Field(default=None, max_length=2000)


class FollowupIn(BaseModel):
    at: date | None = None
    channel: Literal["电话", "微信", "面谈", "邮件", "其他"] = "电话"
    content: str = Field(min_length=1, max_length=2000)
    result: str = Field(default="", max_length=100)


def _download(data: bytes, filename: str) -> StreamingResponse:
    ascii_name = "".join(c for c in filename if 32 <= ord(c) <= 126 and c not in '"\\') or "export.xlsx"
    return StreamingResponse(
        io.BytesIO(data),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f"attachment; filename=\"{ascii_name}\"; "
                                   f"filename*=UTF-8''{quote(filename)}"
        },
    )


# ---------------- 铺位 ----------------

@router.get("/units/summary", summary="铺位看板（空置率/各状态数量）")
def units_summary(db: Session = Depends(get_db)):
    return service.unit_summary(db)


@router.get("/units", summary="铺位列表（分页，可按楼栋/状态/铺位号筛选）")
def units(page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=200),
          building: str = "", status: str = "", keyword: str = "",
          db: Session = Depends(get_db)):
    return service.unit_list(db, building=building, status=status, keyword=keyword,
                             page=page, page_size=page_size)


@router.post("/units", status_code=201, summary="新建铺位")
def create_unit(payload: UnitIn, db: Session = Depends(get_db)):
    try:
        return service.unit_save(db, payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.put("/units/{unit_id}", summary="更新铺位")
def update_unit(unit_id: int, payload: UnitUpdate, db: Session = Depends(get_db)):
    data = payload.model_dump(exclude_unset=True)
    try:
        return service.unit_save(db, data, unit_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.delete("/units/{unit_id}", summary="删除铺位（有效签约引用时拒绝）")
def delete_unit(unit_id: int, db: Session = Depends(get_db)):
    try:
        service.unit_delete(db, unit_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except service.ConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"ok": True}


# ---------------- 线索 ----------------

@router.get("/funnel", summary="招商漏斗（各阶段数量/面积/转化率）")
def get_funnel(db: Session = Depends(get_db)):
    return service.funnel(db)


@router.get("/leads/due", summary="下次跟进已到期/今日的线索")
def due_leads(db: Session = Depends(get_db)):
    return service.leads_due(db)


@router.get("/leads", summary="线索列表（分页，可按阶段/负责人/公司筛选）")
def leads(page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=200),
          stage: str = "", owner: str = "", keyword: str = "",
          db: Session = Depends(get_db)):
    return service.lead_list(db, stage=stage, owner=owner, keyword=keyword,
                             page=page, page_size=page_size)


@router.post("/leads", status_code=201, summary="新建线索")
def create_lead(payload: LeadIn, db: Session = Depends(get_db)):
    try:
        return service.lead_save(db, payload.model_dump())
    except service.ConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.put("/leads/{lead_id}", summary="更新线索")
def update_lead(lead_id: int, payload: LeadUpdate, db: Session = Depends(get_db)):
    data = payload.model_dump(exclude_unset=True)
    try:
        return service.lead_save(db, data, lead_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except service.ConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/leads/{lead_id}", summary="删除线索（已签约须先把阶段改为已流失，连同跟进记录）")
def delete_lead(lead_id: int, db: Session = Depends(get_db)):
    try:
        service.lead_delete(db, lead_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except service.ConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"ok": True}


@router.get("/leads/{lead_id}/followups", summary="线索跟进记录")
def list_followups(lead_id: int, db: Session = Depends(get_db)):
    return service.followup_list(db, lead_id)


@router.post("/leads/{lead_id}/followups", status_code=201, summary="新增跟进记录")
def add_followup(lead_id: int, payload: FollowupIn, db: Session = Depends(get_db)):
    try:
        return service.followup_add(db, lead_id, payload.model_dump())
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# ---------------- 导出 ----------------

@router.get("/export/units.xlsx", summary="导出铺位台账")
def export_units(db: Session = Depends(get_db)):
    return _download(service.export_units_xlsx(db), "招商铺位台账.xlsx")


@router.get("/export/leads.xlsx", summary="导出招商线索")
def export_leads(db: Session = Depends(get_db)):
    return _download(service.export_leads_xlsx(db), "招商线索.xlsx")
