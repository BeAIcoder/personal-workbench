# -*- coding: utf-8 -*-
"""finance_dsf 模块接口：/api/finance/dsf。

端点、payload、残保金分档计算与底稿仍在本模块；防重/受控更正/删除与产物清理/
导入导出流程由 ``FilingRouter`` 收敛（税种差异见 ``FilingSpec``）。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ...database import get_db
from ...finance_filing_common import FilingRouter, FilingSpec, normalize_taxpayer_name
from . import service
from .models import DsfFiling

router = APIRouter(tags=["财务模块·残保金填报"])

filings = FilingRouter(FilingSpec(
    model=DsfFiling, tax_label="残保金", subject="用人单位",
    prefix="/api/finance/dsf", period_column="year", period_noun="年度",
    exports_dir=service.exports_dir,
))


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


def _inputs_of(payload: DsfIn) -> dict:
    return {"total_employees": payload.total_employees,
            "total_salary": payload.total_salary,
            "disabled_employees": payload.disabled_employees,
            "ratio": payload.ratio,
            "local_avg_wage": payload.local_avg_wage}


def _calculate(payload: DsfIn) -> dict:
    return service.calc_dsf(payload.year, payload.total_employees,
                            payload.total_salary, payload.disabled_employees,
                            payload.ratio, payload.local_avg_wage)


@router.post("/filings", status_code=201, summary="创建并计算残保金填报")
def create_filing(payload: DsfIn, db: Session = Depends(get_db)):
    filing = filings.create(
        db, period=payload.year,
        taxpayer_name=normalize_taxpayer_name(payload.taxpayer_name),
        fields={"inputs": _inputs_of(payload)},
        calculate=lambda _period: _calculate(payload))
    return _out(filing)


@router.put("/filings/{filing_id}", summary="更正残保金填报（重算；已导出/已提交不可覆盖）")
def revise_filing(filing_id: int, payload: DsfIn, db: Session = Depends(get_db)):
    filing = filings.load(db, filing_id)
    filings.revise(
        db, filing, period_of=lambda: payload.year,
        taxpayer_name=normalize_taxpayer_name(payload.taxpayer_name),
        fields={"inputs": _inputs_of(payload)},
        calculate=lambda _period: _calculate(payload))
    return _out(filing)


@router.get("/filings", summary="填报记录列表")
def list_filings(page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=200),
                 db: Session = Depends(get_db)):
    return filings.page(db, page, page_size, serialize=_out)


@router.get("/filings/{filing_id}", summary="填报详情")
def get_filing(filing_id: int, db: Session = Depends(get_db)):
    return _out(filings.load(db, filing_id))


@router.delete("/filings/{filing_id}", summary="删除填报记录")
def delete_filing(filing_id: int, db: Session = Depends(get_db)):
    return filings.delete(db, filing_id)


@router.post("/filings/{filing_id}/export", summary="生成残保金申报底稿 xlsx")
def export_filing(filing_id: int, db: Session = Depends(get_db)):
    return filings.export(db, filing_id, exporter=service.export_filing_xlsx,
                          not_ready_detail="尚未完成计算，无法导出")


@router.get("/filings/{filing_id}/download", summary="下载残保金申报底稿")
def download_filing(filing_id: int, db: Session = Depends(get_db)):
    return filings.download(db, filing_id)
