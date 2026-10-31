# -*- coding: utf-8 -*-
"""总经理只读经营聚合接口：/api/business/gm。"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ...database import get_db
from .service import build_summary

router = APIRouter(tags=["经营模块·总经理聚合"])


@router.get("/summary", summary="总经理只读经营聚合")
def summary(
    period: str = Query(default="", pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
    db: Session = Depends(get_db),
):
    try:
        return build_summary(db, period=period)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
