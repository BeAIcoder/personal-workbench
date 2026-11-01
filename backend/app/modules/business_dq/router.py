# -*- coding: utf-8 -*-
"""数据质量巡检接口：/api/business/dq（只读）。"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ...database import get_db
from .service import build_profile

router = APIRouter(tags=["职能模块·数据质量"])

AS_OF_PATTERN = r"^\d{4}-\d{2}-\d{2}$"


@router.get("/summary", summary="跨模块数据质量巡检总览")
def summary(
    as_of: str | None = Query(default=None, pattern=AS_OF_PATTERN),
    db: Session = Depends(get_db),
):
    try:
        return build_profile(db, as_of=as_of)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
