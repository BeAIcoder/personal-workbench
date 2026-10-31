# -*- coding: utf-8 -*-
"""企划管理接口：/api/business/planning。"""
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

router = APIRouter(tags=["经营模块·企划管理"])

Category = Literal["festival", "promo", "brand", "community"]
CampaignStatus = Literal["planning", "approved", "running", "done", "cancelled"]
VendorService = Literal["物料", "美陈", "演艺", "投放", "执行"]


class BudgetItem(BaseModel):
    item: str = Field(min_length=1, max_length=100)
    budget: float = Field(default=0, ge=0, le=1_000_000_000)
    actual: float = Field(default=0, ge=0, le=1_000_000_000)


class Metrics(BaseModel):
    footfall_lift: float | None = Field(default=None, ge=-100, le=100_000)
    sales_lift: float | None = Field(default=None, ge=-100, le=100_000)
    roi: float | None = Field(default=None, ge=-1000, le=100_000)
    review: str = Field(default="", max_length=2000)


class CampaignIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    year: int = Field(ge=2000, le=2100)
    category: Category = "promo"
    start_date: date
    end_date: date
    owner: str = Field(default="", max_length=50)
    channel: str = Field(default="", max_length=100)
    target: str = Field(default="", max_length=300)
    status: CampaignStatus = "planning"
    budget_items: list[BudgetItem] = Field(default_factory=list)
    metrics: Metrics = Field(default_factory=Metrics)
    note: str = Field(default="", max_length=2000)


class CampaignUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    year: int | None = Field(default=None, ge=2000, le=2100)
    category: Category | None = None
    start_date: date | None = None
    end_date: date | None = None
    owner: str | None = Field(default=None, max_length=50)
    channel: str | None = Field(default=None, max_length=100)
    target: str | None = Field(default=None, max_length=300)
    status: CampaignStatus | None = None
    budget_items: list[BudgetItem] | None = None
    metrics: Metrics | None = None
    note: str | None = Field(default=None, max_length=2000)


class VendorIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    service: VendorService = "执行"
    contact: str = Field(default="", max_length=100)
    phone: str = Field(default="", max_length=50)
    rating: int = Field(default=3, ge=1, le=5)
    note: str = Field(default="", max_length=2000)


class VendorUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    service: VendorService | None = None
    contact: str | None = Field(default=None, max_length=100)
    phone: str | None = Field(default=None, max_length=50)
    rating: int | None = Field(default=None, ge=1, le=5)
    note: str | None = Field(default=None, max_length=2000)


def _download(data: bytes, filename: str) -> StreamingResponse:
    ascii_name = "".join(c for c in filename if 32 <= ord(c) <= 126 and c not in '"\\') or "export.xlsx"
    return StreamingResponse(
        io.BytesIO(data),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename=\"{ascii_name}\"; "
                                        f"filename*=UTF-8''{quote(filename)}"},
    )


# ---------------- 活动 ----------------

@router.get("/summary", summary="企划看板（年度预算/实际/超支/平均 ROI）")
def summary(year: int | None = Query(None, ge=2000, le=2100),
            db: Session = Depends(get_db)):
    return service.summary(db, year)


@router.get("/campaigns/calendar", summary="年度企划日历（按月分组）")
def calendar(year: int = Query(..., ge=2000, le=2100), db: Session = Depends(get_db)):
    return service.calendar(db, year)


@router.get("/campaigns", summary="活动列表（按年度/状态/类型/名称筛选）")
def campaigns(page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=200),
              year: int | None = Query(None, ge=2000, le=2100),
              status: str = "", category: str = "", keyword: str = "",
              db: Session = Depends(get_db)):
    return service.campaign_list(db, year=year, status=status, category=category,
                                 keyword=keyword, page=page, page_size=page_size)


@router.post("/campaigns", status_code=201, summary="新建活动")
def create_campaign(payload: CampaignIn, db: Session = Depends(get_db)):
    try:
        return service.campaign_save(db, payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.put("/campaigns/{campaign_id}", summary="更新活动（含预算/复盘）")
def update_campaign(campaign_id: int, payload: CampaignUpdate, db: Session = Depends(get_db)):
    data = payload.model_dump(exclude_unset=True, exclude_none=True)
    try:
        return service.campaign_save(db, data, campaign_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/campaigns/{campaign_id}", summary="删除活动")
def delete_campaign(campaign_id: int, db: Session = Depends(get_db)):
    try:
        service.campaign_delete(db, campaign_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"ok": True}


# ---------------- 供应商 ----------------

@router.get("/vendors", summary="供应商列表（按服务类型/名称筛选）")
def vendors(service_: str = Query("", alias="service"), keyword: str = "",
            db: Session = Depends(get_db)):
    return service.vendor_list(db, service=service_, keyword=keyword)


@router.post("/vendors", status_code=201, summary="新建供应商")
def create_vendor(payload: VendorIn, db: Session = Depends(get_db)):
    try:
        return service.vendor_save(db, payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.put("/vendors/{vendor_id}", summary="更新供应商")
def update_vendor(vendor_id: int, payload: VendorUpdate, db: Session = Depends(get_db)):
    data = payload.model_dump(exclude_unset=True, exclude_none=True)
    try:
        return service.vendor_save(db, data, vendor_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/vendors/{vendor_id}", summary="删除供应商")
def delete_vendor(vendor_id: int, db: Session = Depends(get_db)):
    try:
        service.vendor_delete(db, vendor_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"ok": True}


# ---------------- 导出 ----------------

@router.get("/export/campaigns.xlsx", summary="导出企划活动与预算")
def export_campaigns(year: int | None = Query(None, ge=2000, le=2100),
                     db: Session = Depends(get_db)):
    return _download(service.campaigns_export(db, year), "企划活动与预算.xlsx")
