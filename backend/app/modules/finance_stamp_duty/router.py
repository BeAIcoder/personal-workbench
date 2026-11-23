# -*- coding: utf-8 -*-
"""finance_stamp_duty 模块接口：/api/finance/stamp-duty（前缀由注册器按 manifest.prefix 挂载）。

端点、payload、税目税率、按季期间归一与底稿仍在本模块；防重/受控更正/删除与产物
清理/导入导出流程由 ``FilingRouter`` 收敛（税种差异见 ``FilingSpec``）。
"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ...database import get_db
from ...finance_filing_common import FilingRouter, FilingSpec, normalize_taxpayer_name
from . import service
from .models import StampDutyFiling

router = APIRouter(tags=["财务模块·印花税填报"])

filings = FilingRouter(FilingSpec(
    model=StampDutyFiling, tax_label="印花税", subject="纳税人",
    prefix="/api/finance/stamp-duty", period_column="period", period_noun="期间",
    exports_dir=service.module_exports_dir,
))


class ItemIn(BaseModel):
    tax_item: str
    amount: float = Field(ge=0)
    exempt: bool = False


class FilingIn(BaseModel):
    period: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$", description="所属期 YYYY-MM")
    taxpayer_name: str = Field(default="", max_length=200)
    half_discount: bool = False
    # 申报周期：quarter 时必须填季末月（03/06/09/12）；month 为按月/按次（默认，兼容旧请求）
    filing_period: Literal["month", "quarter"] = "month"
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


def _normalized_period(period: str, filing_period: str) -> str:
    """按季申报归一到季末月；按月/按次保持原期间（与增值税小规模同口径）。

    印花税按季申报必须填季末月：否则 2026-07 与 2026-09 会被当成两个期间，
    同一季度可用不同月份绕过「纳税人+期间」防重（复审 M-3）。
    """
    if filing_period != "quarter":
        return period
    if int(period[5:7]) not in (3, 6, 9, 12):
        raise ValueError("按季申报的所属期须为季末月（03/06/09/12）")
    return period


def _period_of(payload: FilingIn) -> str:
    try:
        return _normalized_period(payload.period, payload.filing_period)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _items_of(payload: FilingIn) -> list[dict]:
    return [i.model_dump() for i in payload.items]


def _calculate(payload: FilingIn, period: str) -> dict:
    calc = service.calc_filing(period, _items_of(payload), payload.half_discount)
    calc["filing_period"] = payload.filing_period
    return calc


def _fields_of(payload: FilingIn) -> dict:
    return {"half_discount": payload.half_discount, "items": _items_of(payload)}


@router.post("/filings", status_code=201, summary="创建并计算印花税填报")
def create_filing(payload: FilingIn, db: Session = Depends(get_db)):
    period = _period_of(payload)  # 按季申报先校验季末月，防同季不同月绕过防重
    filing = filings.create(
        db, period=period,
        taxpayer_name=normalize_taxpayer_name(payload.taxpayer_name),
        fields=_fields_of(payload),
        calculate=lambda period: _calculate(payload, period))
    return _out(filing)


@router.put("/filings/{filing_id}", summary="更正印花税填报（重算；已导出/已提交不可覆盖）")
def revise_filing(filing_id: int, payload: FilingIn, db: Session = Depends(get_db)):
    filing = filings.load(db, filing_id)
    # 季末月校验由 FilingRouter 在「已提交/已导出」守卫之后求值（409 优先于 400）
    filings.revise(
        db, filing, period_of=lambda: _period_of(payload),
        taxpayer_name=normalize_taxpayer_name(payload.taxpayer_name),
        fields=_fields_of(payload),
        calculate=lambda period: _calculate(payload, period))
    return _out(filing)


@router.get("/filings", summary="填报记录列表（分页，按 id 倒序）")
def list_filings(page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=200),
                 db: Session = Depends(get_db)):
    return filings.page(db, page, page_size, serialize=_out)


@router.get("/filings/{filing_id}", summary="填报记录详情")
def get_filing(filing_id: int, db: Session = Depends(get_db)):
    return _out(filings.load(db, filing_id))


@router.delete("/filings/{filing_id}", summary="删除填报记录")
def delete_filing(filing_id: int, db: Session = Depends(get_db)):
    return filings.delete(db, filing_id)


@router.post("/filings/{filing_id}/export", summary="生成申报表底稿 xlsx")
def export_filing(filing_id: int, db: Session = Depends(get_db)):
    return filings.export(db, filing_id, exporter=service.export_filing_xlsx,
                          not_ready_detail="该记录尚未完成计算，无法导出",
                          also_not_ready=lambda f: f.status == "draft")


@router.get("/filings/{filing_id}/download", summary="下载申报表底稿")
def download_filing(filing_id: int, db: Session = Depends(get_db)):
    return filings.download(db, filing_id)
