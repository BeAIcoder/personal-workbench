# -*- coding: utf-8 -*-
"""finance_dsf 模块接口：/api/finance/dsf。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ...database import get_db
from ...utils_filing import unlink_export_artifact
from . import service
from .models import DsfFiling

router = APIRouter(tags=["财务模块·残保金填报"])


class DsfIn(BaseModel):
    year: str = Field(pattern=r"^\d{4}$")
    taxpayer_name: str = Field(default="", max_length=200)
    total_employees: int = Field(ge=1)
    total_salary: float = Field(ge=0)
    disabled_employees: int = Field(ge=0)
    ratio: float = Field(default=0.015, gt=0, le=0.5)
    local_avg_wage: float | None = Field(default=None, gt=0, description="当地上年度社会平均工资（年，元）")


def _out(f: DsfFiling) -> dict:
    return {
        "id": f.id, "year": f.year, "taxpayer_name": f.taxpayer_name,
        "inputs": f.inputs or {}, "calc_result": f.calc_result or {},
        "status": f.status, "has_export": bool(f.export_path),
        "created_at": str(f.created_at),
    }


@router.post("/filings", status_code=201, summary="创建并计算残保金填报")
def create_filing(payload: DsfIn, db: Session = Depends(get_db)):
    try:
        calc = service.calc_dsf(payload.year, payload.total_employees,
                                payload.total_salary, payload.disabled_employees,
                                payload.ratio, payload.local_avg_wage)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    filing = DsfFiling(
        year=payload.year, taxpayer_name=payload.taxpayer_name,
        inputs={"total_employees": payload.total_employees,
                "total_salary": payload.total_salary,
                "disabled_employees": payload.disabled_employees,
                "ratio": payload.ratio,
                "local_avg_wage": payload.local_avg_wage},
        calc_result=calc, status="calculated")
    db.add(filing)
    db.commit()
    db.refresh(filing)
    return _out(filing)


@router.get("/filings", summary="填报记录列表")
def list_filings(page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=200),
                 db: Session = Depends(get_db)):
    q = db.query(DsfFiling).order_by(DsfFiling.id.desc())
    total = q.count()
    rows = q.offset((page - 1) * page_size).limit(page_size).all()
    return {"total": total, "items": [_out(f) for f in rows],
            "page": page, "page_size": page_size}


@router.get("/filings/{filing_id}", summary="填报详情")
def get_filing(filing_id: int, db: Session = Depends(get_db)):
    f = db.get(DsfFiling, filing_id)
    if f is None:
        raise HTTPException(status_code=404, detail="填报记录不存在")
    return _out(f)


@router.delete("/filings/{filing_id}", summary="删除填报记录")
def delete_filing(filing_id: int, db: Session = Depends(get_db)):
    f = db.get(DsfFiling, filing_id)
    if f is None:
        raise HTTPException(status_code=404, detail="填报记录不存在")
    # 先落库删除、再清产物：commit 失败时不得先丢掉底稿（复核 M-5）
    export_path = f.export_path
    db.delete(f)
    db.commit()
    removed = unlink_export_artifact(export_path, service.exports_dir())
    return {"ok": True, "export_removed": removed}


@router.post("/filings/{filing_id}/export", summary="生成残保金申报底稿 xlsx")
def export_filing(filing_id: int, db: Session = Depends(get_db)):
    f = db.get(DsfFiling, filing_id)
    if f is None:
        raise HTTPException(status_code=404, detail="填报记录不存在")
    if not f.calc_result:
        raise HTTPException(status_code=409, detail="尚未完成计算，无法导出")
    path = service.export_filing_xlsx(f)
    f.export_path = str(path)
    f.status = "exported"
    db.commit()
    return {"ok": True, "file": path.name}


@router.get("/filings/{filing_id}/download", summary="下载残保金申报底稿")
def download_filing(filing_id: int, db: Session = Depends(get_db)):
    f = db.get(DsfFiling, filing_id)
    if f is None:
        raise HTTPException(status_code=404, detail="填报记录不存在")
    if not f.export_path:
        raise HTTPException(status_code=409, detail="尚未导出，请先点「生成底稿」")
    p = service.exports_dir() / f.export_path.split("/")[-1].split("\\")[-1]
    if not p.is_file():
        raise HTTPException(status_code=410, detail="导出文件已不存在，请重新导出")
    return FileResponse(str(p), filename=p.name)
