# -*- coding: utf-8 -*-
"""finance_vat 模块接口：/api/finance/vat。

端点、payload、计税与底稿仍在本模块；防重/受控更正/删除与产物清理/导入导出流程
由 ``FilingRouter`` 收敛（税种差异见 ``FilingSpec``）。
"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ...database import get_db
from ...finance_filing_common import FilingRouter, FilingSpec, normalize_taxpayer_name
from . import service
from .models import VatFiling

router = APIRouter(tags=["财务模块·增值税填报"])

filings = FilingRouter(FilingSpec(
    model=VatFiling, tax_label="增值税", subject="纳税人",
    prefix="/api/finance/vat", period_column="period", period_noun="期间",
    exports_dir=service.exports_dir,
))


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


def _calculate(payload: VatIn) -> dict:
    if payload.taxpayer_type == "general":
        return service.calc_general(
            payload.period, [i.model_dump() for i in payload.output_items],
            payload.input_vat, payload.input_transfer_out)
    return service.calc_small_scale(
        payload.period, payload.sales_amount, payload.levy_rate,
        payload.filing_period)


def _inputs_of(payload: VatIn) -> dict:
    return {"output_items": [i.model_dump() for i in payload.output_items],
            "input_vat": payload.input_vat,
            "input_transfer_out": payload.input_transfer_out,
            "sales_amount": payload.sales_amount, "levy_rate": payload.levy_rate,
            "filing_period": payload.filing_period}


@router.post("/filings", status_code=201, summary="创建并计算增值税填报")
def create_filing(payload: VatIn, db: Session = Depends(get_db)):
    filing = filings.create(
        db, period=payload.period,
        taxpayer_name=normalize_taxpayer_name(payload.taxpayer_name),
        fields={"taxpayer_type": payload.taxpayer_type, "inputs": _inputs_of(payload)},
        calculate=lambda _period: _calculate(payload))
    return _out(filing)


@router.put("/filings/{filing_id}", summary="更正增值税填报（重算；已导出/已提交不可覆盖）")
def revise_filing(filing_id: int, payload: VatIn, db: Session = Depends(get_db)):
    filing = filings.load(db, filing_id)
    filings.revise(
        db, filing, period_of=lambda: payload.period,
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


@router.post("/filings/{filing_id}/export", summary="生成申报底稿 xlsx")
def export_filing(filing_id: int, db: Session = Depends(get_db)):
    return filings.export(db, filing_id, exporter=service.export_filing_xlsx,
                          not_ready_detail="尚未完成计算，无法导出")


@router.get("/filings/{filing_id}/download", summary="下载申报底稿")
def download_filing(filing_id: int, db: Session = Depends(get_db)):
    return filings.download(db, filing_id)
