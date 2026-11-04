# -*- coding: utf-8 -*-
"""finance_stamp_duty 模块接口：/api/finance/stamp-duty（前缀由注册器按 manifest.prefix 挂载）。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ...database import get_db
from ...utils_filing import unlink_export_artifact
from . import service
from .models import StampDutyFiling

router = APIRouter(tags=["财务模块·印花税填报"])


class ItemIn(BaseModel):
    tax_item: str
    amount: float = Field(ge=0)
    exempt: bool = False


class FilingIn(BaseModel):
    period: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$", description="所属期 YYYY-MM")
    taxpayer_name: str = Field(default="", max_length=200)
    half_discount: bool = False
    items: list[ItemIn] = Field(min_length=1)


def _out(f: StampDutyFiling) -> dict:
    return {
        "id": f.id, "period": f.period, "taxpayer_name": f.taxpayer_name,
        "half_discount": f.half_discount, "items": f.items or [],
        "calc_result": f.calc_result or {}, "status": f.status,
        "has_export": bool(f.export_path), "created_at": str(f.created_at),
    }


@router.get("/tax-items", summary="印花税税目税率表（印花税法）")
def tax_items():
    return service.list_tax_items()


@router.post("/filings", status_code=201, summary="创建并计算印花税填报")
def create_filing(payload: FilingIn, db: Session = Depends(get_db)):
    try:
        calc = service.calc_filing(payload.period, [i.model_dump() for i in payload.items],
                                   payload.half_discount)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    filing = StampDutyFiling(
        period=payload.period, taxpayer_name=payload.taxpayer_name,
        half_discount=payload.half_discount,
        items=[i.model_dump() for i in payload.items],
        calc_result=calc, status="calculated",
    )
    db.add(filing)
    db.commit()
    db.refresh(filing)
    return _out(filing)


@router.get("/filings", summary="填报记录列表（分页，按 id 倒序）")
def list_filings(page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=200),
                 db: Session = Depends(get_db)):
    q = db.query(StampDutyFiling).order_by(StampDutyFiling.id.desc())
    total = q.count()
    rows = q.offset((page - 1) * page_size).limit(page_size).all()
    return {"total": total, "items": [_out(f) for f in rows],
            "page": page, "page_size": page_size}


@router.get("/filings/{filing_id}", summary="填报记录详情")
def get_filing(filing_id: int, db: Session = Depends(get_db)):
    f = db.get(StampDutyFiling, filing_id)
    if f is None:
        raise HTTPException(status_code=404, detail="填报记录不存在")
    return _out(f)


@router.delete("/filings/{filing_id}", summary="删除填报记录")
def delete_filing(filing_id: int, db: Session = Depends(get_db)):
    f = db.get(StampDutyFiling, filing_id)
    if f is None:
        raise HTTPException(status_code=404, detail="填报记录不存在")
    # 先落库删除、再清产物：commit 失败时不得先丢掉底稿（复核 M-5）
    export_path = f.export_path
    db.delete(f)
    db.commit()
    removed = unlink_export_artifact(export_path, service.module_exports_dir())
    return {"ok": True, "export_removed": removed}


@router.post("/filings/{filing_id}/export", summary="生成申报表底稿 xlsx")
def export_filing(filing_id: int, db: Session = Depends(get_db)):
    f = db.get(StampDutyFiling, filing_id)
    if f is None:
        raise HTTPException(status_code=404, detail="填报记录不存在")
    if f.status == "draft" or not f.calc_result:
        raise HTTPException(status_code=409, detail="该记录尚未完成计算，无法导出")
    path = service.export_filing_xlsx(f)
    f.export_path = str(path)
    f.status = "exported"
    db.commit()
    return {"ok": True, "file": path.name}


@router.get("/filings/{filing_id}/download", summary="下载申报表底稿")
def download_filing(filing_id: int, db: Session = Depends(get_db)):
    f = db.get(StampDutyFiling, filing_id)
    if f is None:
        raise HTTPException(status_code=404, detail="填报记录不存在")
    if not f.export_path:
        raise HTTPException(status_code=409, detail="尚未导出，请先点「生成底稿」")
    p = service.module_exports_dir() / f.export_path.split("/")[-1].split("\\")[-1]
    if not p.is_file():
        raise HTTPException(status_code=410, detail="导出文件已不存在，请重新导出")
    return FileResponse(str(p), filename=p.name)
