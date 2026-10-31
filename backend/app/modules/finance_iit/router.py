# -*- coding: utf-8 -*-
"""finance_iit 模块接口：/api/finance/iit。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ...database import get_db
from . import service
from .models import IitFiling

router = APIRouter(tags=["财务模块·个人所得税填报"])


class RowIn(BaseModel):
    month: int = Field(ge=1, le=12)
    name: str = Field(max_length=100)
    # 工号/证件号：同名员工靠它区分累计序列（可空，空则按姓名归组）
    employee_id: str = Field(default="", max_length=50)
    # 任职起始月：累计减除费用按「任职月份数 × 5000」；空则按最早申报月
    employment_start_month: int | None = Field(default=None, ge=1, le=12)
    salary: float = Field(ge=0)
    social_insurance: float = Field(default=0.0, ge=0)
    special_additional: float = Field(default=0.0, ge=0, description="当月专项附加扣除合计（元/月）")
    other_deductions: float = Field(default=0.0, ge=0)


class IitIn(BaseModel):
    year: str = Field(pattern=r"^\d{4}$")
    taxpayer_name: str = Field(default="", max_length=200)
    rows: list[RowIn] = Field(min_length=1)


def _out(f: IitFiling) -> dict:
    return {
        "id": f.id, "year": f.year, "taxpayer_name": f.taxpayer_name,
        "rows": f.rows or [], "calc_result": f.calc_result or {},
        "status": f.status, "has_export": bool(f.export_path),
        "created_at": str(f.created_at),
    }


@router.get("/standards", summary="专项附加扣除标准表（国发〔2023〕13号）")
def standards():
    return service.SPECIAL_DEDUCTION_STANDARDS


@router.post("/filings", status_code=201, summary="创建并计算个税扣缴填报")
def create_filing(payload: IitIn, db: Session = Depends(get_db)):
    try:
        calc = service.calc_filing(payload.year, [r.model_dump() for r in payload.rows])
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    filing = IitFiling(year=payload.year, taxpayer_name=payload.taxpayer_name,
                       rows=[r.model_dump() for r in payload.rows],
                       calc_result=calc, status="calculated")
    db.add(filing)
    db.commit()
    db.refresh(filing)
    return _out(filing)


@router.get("/filings", summary="填报记录列表")
def list_filings(page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=200),
                 db: Session = Depends(get_db)):
    q = db.query(IitFiling).order_by(IitFiling.id.desc())
    total = q.count()
    rows = q.offset((page - 1) * page_size).limit(page_size).all()
    return {"total": total, "items": [_out(f) for f in rows],
            "page": page, "page_size": page_size}


@router.get("/filings/{filing_id}", summary="填报详情")
def get_filing(filing_id: int, db: Session = Depends(get_db)):
    f = db.get(IitFiling, filing_id)
    if f is None:
        raise HTTPException(status_code=404, detail="填报记录不存在")
    return _out(f)


@router.delete("/filings/{filing_id}", summary="删除填报记录")
def delete_filing(filing_id: int, db: Session = Depends(get_db)):
    f = db.get(IitFiling, filing_id)
    if f is None:
        raise HTTPException(status_code=404, detail="填报记录不存在")
    db.delete(f)
    db.commit()
    return {"ok": True}


@router.post("/filings/{filing_id}/export", summary="生成扣缴申报底稿 xlsx")
def export_filing(filing_id: int, db: Session = Depends(get_db)):
    f = db.get(IitFiling, filing_id)
    if f is None:
        raise HTTPException(status_code=404, detail="填报记录不存在")
    if not f.calc_result:
        raise HTTPException(status_code=409, detail="尚未完成计算，无法导出")
    path = service.export_filing_xlsx(f)
    f.export_path = str(path)
    f.status = "exported"
    db.commit()
    return {"ok": True, "file": path.name}


@router.get("/filings/{filing_id}/download", summary="下载扣缴申报底稿")
def download_filing(filing_id: int, db: Session = Depends(get_db)):
    f = db.get(IitFiling, filing_id)
    if f is None:
        raise HTTPException(status_code=404, detail="填报记录不存在")
    if not f.export_path:
        raise HTTPException(status_code=409, detail="尚未导出，请先点「生成底稿」")
    p = service.exports_dir() / f.export_path.split("/")[-1].split("\\")[-1]
    if not p.is_file():
        raise HTTPException(status_code=410, detail="导出文件已不存在，请重新导出")
    return FileResponse(str(p), filename=p.name)
