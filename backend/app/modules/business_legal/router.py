# -*- coding: utf-8 -*-
"""法务接口：/api/business/legal。"""
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

router = APIRouter(tags=["职能模块·法务管理"])

ReviewStatus = Literal["pending", "reviewing", "approved", "rejected", "archived"]
RiskLevel = Literal["low", "medium", "high"]
CaseType = Literal["litigation", "arbitration", "dispute"]
CaseStage = Literal["filed", "hearing", "judgment", "appeal", "closed"]


class ReviewIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    contract_type: str = Field(default="", max_length=50)
    counterparty: str = Field(default="", max_length=200)
    amount: float = Field(default=0, ge=0, le=100_000_000_000)
    submit_date: date | None = None
    reviewer: str = Field(default="", max_length=50)
    status: ReviewStatus = "pending"
    risk_level: RiskLevel = "low"
    expire_date: date | None = None
    note: str = Field(default="", max_length=2000)


class ReviewUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    contract_type: str | None = Field(default=None, max_length=50)
    counterparty: str | None = Field(default=None, max_length=200)
    amount: float | None = Field(default=None, ge=0, le=100_000_000_000)
    submit_date: date | None = None
    reviewer: str | None = Field(default=None, max_length=50)
    status: ReviewStatus | None = None
    risk_level: RiskLevel | None = None
    expire_date: date | None = None
    note: str | None = Field(default=None, max_length=2000)


class CaseIn(BaseModel):
    case_no: str = Field(default="", max_length=80)
    title: str = Field(min_length=1, max_length=200)
    case_type: CaseType = "dispute"
    counterparty: str = Field(default="", max_length=200)
    amount: float = Field(default=0, ge=0, le=100_000_000_000)
    stage: CaseStage = "filed"
    filed_date: date | None = None
    hearing_date: date | None = None
    result: str = Field(default="", max_length=300)
    note: str = Field(default="", max_length=2000)


class CaseUpdate(BaseModel):
    case_no: str | None = Field(default=None, max_length=80)
    title: str | None = Field(default=None, min_length=1, max_length=200)
    case_type: CaseType | None = None
    counterparty: str | None = Field(default=None, max_length=200)
    amount: float | None = Field(default=None, ge=0, le=100_000_000_000)
    stage: CaseStage | None = None
    filed_date: date | None = None
    hearing_date: date | None = None
    result: str | None = Field(default=None, max_length=300)
    note: str | None = Field(default=None, max_length=2000)


class SealIn(BaseModel):
    purpose: str = Field(min_length=1, max_length=200)
    doc_type: str = Field(default="", max_length=50)
    applicant: str = Field(default="", max_length=50)
    approver: str = Field(default="", max_length=50)
    used_at: date | None = None
    copies: int = Field(default=1, ge=1, le=10000)
    note: str = Field(default="", max_length=2000)


class SealUpdate(BaseModel):
    purpose: str | None = Field(default=None, min_length=1, max_length=200)
    doc_type: str | None = Field(default=None, max_length=50)
    applicant: str | None = Field(default=None, max_length=50)
    approver: str | None = Field(default=None, max_length=50)
    used_at: date | None = None
    copies: int | None = Field(default=None, ge=1, le=10000)
    note: str | None = Field(default=None, max_length=2000)


def _download(data: bytes, filename: str) -> StreamingResponse:
    ascii_name = "".join(c for c in filename if 32 <= ord(c) <= 126 and c not in '"\\') or "export.xlsx"
    return StreamingResponse(
        io.BytesIO(data),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename=\"{ascii_name}\"; "
                                        f"filename*=UTF-8''{quote(filename)}"},
    )


@router.get("/summary", summary="法务看板（待审/高风险/到期/开庭）")
def summary(db: Session = Depends(get_db)):
    return service.legal_summary(db)


# ---------------- 合同审查 ----------------

@router.get("/contracts", summary="合同审查列表")
def reviews(page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=200),
            status: str = "", risk_level: str = "", keyword: str = "",
            db: Session = Depends(get_db)):
    return service.review_list(db, status=status, risk_level=risk_level,
                               keyword=keyword, page=page, page_size=page_size)


@router.post("/contracts", status_code=201, summary="新建合同审查")
def create_review(payload: ReviewIn, db: Session = Depends(get_db)):
    try:
        return service.review_save(db, payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.put("/contracts/{review_id}", summary="更新合同审查")
def update_review(review_id: int, payload: ReviewUpdate, db: Session = Depends(get_db)):
    data = payload.model_dump(exclude_unset=True, exclude_none=True)
    try:
        return service.review_save(db, data, review_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/contracts/{review_id}", summary="删除合同审查")
def delete_review(review_id: int, db: Session = Depends(get_db)):
    try:
        service.review_delete(db, review_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"ok": True}


# ---------------- 案件 ----------------

@router.get("/cases", summary="案件列表")
def cases(page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=200),
          stage: str = "", case_type: str = "", keyword: str = "",
          db: Session = Depends(get_db)):
    return service.case_list(db, stage=stage, case_type=case_type,
                             keyword=keyword, page=page, page_size=page_size)


@router.post("/cases", status_code=201, summary="新建案件")
def create_case(payload: CaseIn, db: Session = Depends(get_db)):
    try:
        return service.case_save(db, payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.put("/cases/{case_id}", summary="更新案件")
def update_case(case_id: int, payload: CaseUpdate, db: Session = Depends(get_db)):
    data = payload.model_dump(exclude_unset=True, exclude_none=True)
    try:
        return service.case_save(db, data, case_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/cases/{case_id}", summary="删除案件")
def delete_case(case_id: int, db: Session = Depends(get_db)):
    try:
        service.case_delete(db, case_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"ok": True}


# ---------------- 用印 ----------------

@router.get("/seals", summary="用印列表")
def seals(page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=200),
          keyword: str = "", db: Session = Depends(get_db)):
    return service.seal_list(db, keyword=keyword, page=page, page_size=page_size)


@router.post("/seals", status_code=201, summary="新建用印记录")
def create_seal(payload: SealIn, db: Session = Depends(get_db)):
    try:
        return service.seal_save(db, payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.put("/seals/{seal_id}", summary="更新用印记录")
def update_seal(seal_id: int, payload: SealUpdate, db: Session = Depends(get_db)):
    data = payload.model_dump(exclude_unset=True, exclude_none=True)
    try:
        return service.seal_save(db, data, seal_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/seals/{seal_id}", summary="删除用印记录")
def delete_seal(seal_id: int, db: Session = Depends(get_db)):
    try:
        service.seal_delete(db, seal_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"ok": True}


# ---------------- 导出 ----------------

@router.get("/export/contracts.xlsx", summary="导出合同审查台账")
def export_reviews(db: Session = Depends(get_db)):
    return _download(service.reviews_export(db), "法务合同审查台账.xlsx")


@router.get("/export/cases.xlsx", summary="导出案件台账")
def export_cases(db: Session = Depends(get_db)):
    return _download(service.cases_export(db), "法务案件台账.xlsx")
