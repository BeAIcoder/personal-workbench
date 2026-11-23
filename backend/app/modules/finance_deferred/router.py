# -*- coding: utf-8 -*-
"""finance_deferred 模块接口：/api/finance/deferred（前缀由注册器按 manifest.prefix 挂载）。

项目 CRUD；测算创建/列表/详情/期间行/校验；确认（风险需显式确认）；xlsx 导出；
摘要。路由只做参数、状态码与中文异常映射，业务规则全部在 service。

导出同时返回 ASCII ``filename`` 与 UTF-8 ``filename*``，中文名由前端 saveBlob 落名。
"""
from __future__ import annotations

import io
from contextlib import contextmanager
from urllib.parse import quote

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from ...database import get_db
from . import service
from .schemas import (
    ChecksOut,
    DeferredConfirmIn,
    DeferredItemCreate,
    DeferredItemListOut,
    DeferredItemOut,
    DeferredItemPatch,
    DeferredSummaryOut,
    PeriodRowOut,
    RunOut,
)

router = APIRouter(tags=["财务模块·长期待摊"])

_XLSX_MEDIA = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@contextmanager
def _translated():
    """把服务层中文异常映射成 HTTP：404 / 409 / 503，其余业务错误 400。"""
    try:
        yield
    except service.DeferredNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except service.DeferredConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except service.DeferredEngineUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _download(file) -> StreamingResponse:
    ascii_name = file.filename_ascii or "".join(
        char for char in file.filename if 32 <= ord(char) <= 126 and char not in '"\\'
    )
    ascii_name = ascii_name or "deferred.xlsx"
    return StreamingResponse(
        io.BytesIO(file.content),
        media_type=_XLSX_MEDIA,
        headers={
            "Content-Disposition": f"attachment; filename=\"{ascii_name}\"; "
                                   f"filename*=UTF-8''{quote(file.filename)}"
        },
    )


# ---------- 项目主档 ----------


@router.post("/items", status_code=201, response_model=DeferredItemOut,
             summary="新建长期待摊项目（草稿）")
def create_item(payload: DeferredItemCreate, db: Session = Depends(get_db)):
    with _translated():
        return service.create_item(db, payload)


@router.get("/items", response_model=DeferredItemListOut,
            summary="项目列表（分页/状态/关键词）")
def list_items(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    status: str | None = Query(default=None),
    keyword: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    with _translated():
        return service.list_items(
            db, status=status or None, keyword=keyword or None, page=page, page_size=page_size
        )


@router.get("/items/{item_id}", response_model=DeferredItemOut, summary="项目详情与计数")
def get_item(item_id: int, db: Session = Depends(get_db)):
    with _translated():
        return service.get_item(db, item_id)


@router.patch("/items/{item_id}", response_model=DeferredItemOut,
              summary="维护项目参数（归档后只读）")
def update_item(item_id: int, payload: DeferredItemPatch, db: Session = Depends(get_db)):
    with _translated():
        return service.update_item(db, item_id, payload)


@router.delete("/items/{item_id}", summary="删除项目（有测算历史 409）")
def delete_item(item_id: int, db: Session = Depends(get_db)):
    with _translated():
        return service.delete_item(db, item_id)


# ---------- 测算 ----------


@router.post("/items/{item_id}/runs", status_code=201, response_model=RunOut,
             summary="创建/幂等返回摊销测算")
def create_run(item_id: int, db: Session = Depends(get_db)):
    with _translated():
        return service.create_run(db, item_id)


@router.get("/items/{item_id}/runs", response_model=list[RunOut],
            summary="测算列表（最新在前）")
def list_runs(item_id: int, db: Session = Depends(get_db)):
    with _translated():
        return service.list_runs(db, item_id)


@router.get("/runs/{run_id}", response_model=RunOut, summary="测算详情（含过期标记）")
def get_run(run_id: int, db: Session = Depends(get_db)):
    with _translated():
        return service.get_run(db, run_id)


@router.get("/runs/{run_id}/periods", response_model=list[PeriodRowOut],
            summary="月份级摊销行")
def get_periods(run_id: int, db: Session = Depends(get_db)):
    with _translated():
        return service.get_periods(db, run_id)


@router.get("/runs/{run_id}/checks", response_model=ChecksOut, summary="统一校验结果与计数")
def get_checks(run_id: int, db: Session = Depends(get_db)):
    with _translated():
        return service.get_checks(db, run_id)


@router.post("/runs/{run_id}/confirm", response_model=RunOut,
             summary="确认测算（风险警告需显式风险说明）")
def confirm_run(
    run_id: int,
    payload: DeferredConfirmIn | None = Body(default=None),
    db: Session = Depends(get_db),
):
    with _translated():
        return service.confirm_run(db, run_id, payload)


@router.get("/runs/{run_id}/export.xlsx", summary="导出三页签 xlsx 底稿")
def export_run_xlsx(
    run_id: int,
    allow_stale: bool = Query(default=False, description="历史快照显式允许过期导出"),
    db: Session = Depends(get_db),
):
    with _translated():
        file = service.export_run_xlsx(db, run_id, allow_stale=allow_stale)
    return _download(file)


@router.get("/summary", response_model=DeferredSummaryOut, summary="长期待摊摘要（按项目或全部）")
def summary(item_id: int | None = Query(default=None, ge=1), db: Session = Depends(get_db)):
    with _translated():
        return service.deferred_summary(db, item_id=item_id)
