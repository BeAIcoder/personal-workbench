# -*- coding: utf-8 -*-
"""finance_vat 模块接口：/api/finance/vat。"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ...database import get_db
from . import service
from .models import VatFiling

router = APIRouter(tags=["财务模块·增值税填报"])


class OutputItemIn(BaseModel):
    business_type: str = Field(default="销售货物", max_length=50)
    amount: float = Field(ge=0)
    rate: float


class VatIn(BaseModel):
    period: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
    taxpayer_type: str = Field(pattern=r"^(general|small_scale)$")
    taxpayer_name: str = Field(default="", max_length=200)
    output_items: list[OutputItemIn] = Field(default_factory=list)
    input_vat: float = Field(default=0.0, ge=0)
    input_transfer_out: float = Field(default=0.0, ge=0)
    sales_amount: float = Field(default=0.0, ge=0)
    levy_rate: float = 0.01
    filing_period: Literal["month", "quarter"] = "quarter"


def _out(f: VatFiling) -> dict:
    return {
        "id": f.id, "period": f.period, "taxpayer_type": f.taxpayer_type,
        "taxpayer_name": f.taxpayer_name, "inputs": f.inputs or {},
        "calc_result": f.calc_result or {}, "status": f.status,
        "has_export": bool(f.export_path), "created_at": str(f.created_at),
    }


@router.post("/filings", status_code=201, summary="创建并计算增值税填报")
def create_filing(payload: VatIn, db: Session = Depends(get_db)):
    try:
        if payload.taxpayer_type == "general":
            calc = service.calc_general(
                payload.period, [i.model_dump() for i in payload.output_items],
                payload.input_vat, payload.input_transfer_out)
        else:
            calc = service.calc_small_scale(
                payload.period, payload.sales_amount, payload.levy_rate,
                payload.filing_period)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    inputs = {"output_items": [i.model_dump() for i in payload.output_items],
              "input_vat": payload.input_vat,
              "input_transfer_out": payload.input_transfer_out,
              "sales_amount": payload.sales_amount, "levy_rate": payload.levy_rate,
              "filing_period": payload.filing_period}
    filing = VatFiling(period=payload.period, taxpayer_type=payload.taxpayer_type,
                       taxpayer_name=payload.taxpayer_name, inputs=inputs,
                       calc_result=calc, status="calculated")
    db.add(filing)
    db.commit()
    db.refresh(filing)
    return _out(filing)


@router.get("/filings", summary="填报记录列表")
def list_filings(page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=200),
                 db: Session = Depends(get_db)):
    q = db.query(VatFiling).order_by(VatFiling.id.desc())
    total = q.count()
    rows = q.offset((page - 1) * page_size).limit(page_size).all()
    return {"total": total, "items": [_out(f) for f in rows],
            "page": page, "page_size": page_size}


@router.get("/filings/{filing_id}", summary="填报详情")
def get_filing(filing_id: int, db: Session = Depends(get_db)):
    f = db.get(VatFiling, filing_id)
    if f is None:
        raise HTTPException(status_code=404, detail="填报记录不存在")
    return _out(f)


@router.delete("/filings/{filing_id}", summary="删除填报记录")
def delete_filing(filing_id: int, db: Session = Depends(get_db)):
    f = db.get(VatFiling, filing_id)
    if f is None:
        raise HTTPException(status_code=404, detail="填报记录不存在")
    db.delete(f)
    db.commit()
    return {"ok": True}


@router.post("/filings/{filing_id}/export", summary="生成申报底稿 xlsx")
def export_filing(filing_id: int, db: Session = Depends(get_db)):
    f = db.get(VatFiling, filing_id)
    if f is None:
        raise HTTPException(status_code=404, detail="填报记录不存在")
    if not f.calc_result:
        raise HTTPException(status_code=409, detail="尚未完成计算，无法导出")
    path = service.export_filing_xlsx(f)
    f.export_path = str(path)
    f.status = "exported"
    db.commit()
    return {"ok": True, "file": path.name}


@router.get("/filings/{filing_id}/download", summary="下载申报底稿")
def download_filing(filing_id: int, db: Session = Depends(get_db)):
    f = db.get(VatFiling, filing_id)
    if f is None:
        raise HTTPException(status_code=404, detail="填报记录不存在")
    if not f.export_path:
        raise HTTPException(status_code=409, detail="尚未导出，请先点「生成底稿」")
    p = service.exports_dir() / f.export_path.split("/")[-1].split("\\")[-1]
    if not p.is_file():
        raise HTTPException(status_code=410, detail="导出文件已不存在，请重新导出")
    return FileResponse(str(p), filename=p.name)
