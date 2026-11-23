# -*- coding: utf-8 -*-
"""finance_iit 模块接口：/api/finance/iit。

端点、payload、累计预扣计算与底稿仍在本模块；防重/受控更正/删除与产物清理/
导入导出流程由 ``FilingRouter`` 收敛（税种差异见 ``FilingSpec``）。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ...database import get_db
from ...finance_filing_common import FilingRouter, FilingSpec, normalize_taxpayer_name
from . import service
from .models import IitFiling

router = APIRouter(tags=["财务模块·个人所得税填报"])

filings = FilingRouter(FilingSpec(
    model=IitFiling, tax_label="个税扣缴", subject="扣缴义务人",
    prefix="/api/finance/iit", period_column="year", period_noun="年度",
    exports_dir=service.exports_dir,
))


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


@router.get("/standards", summary="专项附加扣除标准表（国发〔2018〕41号 + 国发〔2023〕13号）")
def standards():
    return service.SPECIAL_DEDUCTION_STANDARDS


def _rows_of(payload: IitIn) -> list[dict]:
    return [r.model_dump() for r in payload.rows]


@router.post("/filings", status_code=201, summary="创建并计算个税扣缴填报")
def create_filing(payload: IitIn, db: Session = Depends(get_db)):
    filing = filings.create(
        db, period=payload.year,
        taxpayer_name=normalize_taxpayer_name(payload.taxpayer_name),
        fields={"rows": _rows_of(payload)},
        calculate=lambda _period: service.calc_filing(payload.year, _rows_of(payload)))
    return _out(filing)


@router.put("/filings/{filing_id}", summary="更正个税扣缴填报（重算；已导出/已提交不可覆盖）")
def revise_filing(filing_id: int, payload: IitIn, db: Session = Depends(get_db)):
    filing = filings.load(db, filing_id)
    filings.revise(
        db, filing, period_of=lambda: payload.year,
        taxpayer_name=normalize_taxpayer_name(payload.taxpayer_name),
        fields={"rows": _rows_of(payload)},
        calculate=lambda _period: service.calc_filing(payload.year, _rows_of(payload)))
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


@router.post("/filings/{filing_id}/export", summary="生成扣缴申报底稿 xlsx")
def export_filing(filing_id: int, db: Session = Depends(get_db)):
    return filings.export(db, filing_id, exporter=service.export_filing_xlsx,
                          not_ready_detail="尚未完成计算，无法导出")


@router.get("/filings/{filing_id}/download", summary="下载扣缴申报底稿")
def download_filing(filing_id: int, db: Session = Depends(get_db)):
    return filings.download(db, filing_id)
