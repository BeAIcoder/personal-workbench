# -*- coding: utf-8 -*-
"""finance_union 模块接口：/api/finance/union（前缀由注册器按 manifest.prefix 挂载）。

批次 CRUD；部门行列表/整批保存；计算预览；校验；确认；xlsx 导出；摘要。
路由只做参数、状态码与中文异常映射，业务规则全部在 service。

导出同时返回 ASCII ``filename`` 与 UTF-8 ``filename*``，中文名由前端 saveBlob 落名。
"""
from __future__ import annotations

import io
from contextlib import contextmanager
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from ...database import get_db
from . import service
from .schemas import (
    UnionBatchCreate,
    UnionBatchDetailOut,
    UnionBatchListOut,
    UnionBatchOut,
    UnionBatchPatch,
    UnionChecksOut,
    UnionLineOut,
    UnionLinesReplace,
    UnionPreviewOut,
    UnionSummaryOut,
)

router = APIRouter(tags=["财务模块·工会经费计提"])

_XLSX_MEDIA = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@contextmanager
def _translated():
    """把服务层中文异常映射成 HTTP：404 / 409，其余业务错误 400。"""
    try:
        yield
    except service.UnionNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except service.UnionConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _download(file: service.UnionExportFile) -> StreamingResponse:
    ascii_name = file.filename_ascii or "".join(
        char for char in file.filename if 32 <= ord(char) <= 126 and char not in '"\\'
    )
    ascii_name = ascii_name or "union.xlsx"
    return StreamingResponse(
        io.BytesIO(file.content),
        media_type=_XLSX_MEDIA,
        headers={
            "Content-Disposition": f"attachment; filename=\"{ascii_name}\"; "
                                   f"filename*=UTF-8''{quote(file.filename)}"
        },
    )


@router.post("/batches", response_model=UnionBatchOut, status_code=201,
             summary="新建计提批次（draft，主体+期间唯一）")
def create_batch(payload: UnionBatchCreate, db: Session = Depends(get_db)):
    with _translated():
        return service.create_batch(db, payload)


@router.get("/batches", response_model=UnionBatchListOut, summary="批次列表（分页/主体/期间/状态）")
def list_batches(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    entity: str | None = Query(default=None),
    period: str | None = Query(default=None),
    status: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    with _translated():
        return service.list_batches(
            db, entity=entity or None, period=period or None, status=status or None,
            page=page, page_size=page_size,
        )


@router.get("/batches/{batch_id}", response_model=UnionBatchDetailOut,
            summary="批次详情（批次头 + 部门行 + 合计 + 校验）")
def get_batch(batch_id: int, db: Session = Depends(get_db)):
    with _translated():
        return service.get_batch_detail(db, batch_id)


@router.patch("/batches/{batch_id}", response_model=UnionBatchOut, summary="维护批次（仅草稿）")
def update_batch(batch_id: int, payload: UnionBatchPatch, db: Session = Depends(get_db)):
    with _translated():
        return service.update_batch(db, batch_id, payload)


@router.delete("/batches/{batch_id}", summary="删除批次（仅草稿，连带部门行）")
def delete_batch(batch_id: int, db: Session = Depends(get_db)):
    with _translated():
        return service.delete_batch(db, batch_id)


@router.get("/batches/{batch_id}/lines", response_model=list[UnionLineOut],
            summary="部门行列表（含实时派生金额）")
def list_lines(batch_id: int, db: Session = Depends(get_db)):
    with _translated():
        return service.list_lines(db, batch_id)


@router.put("/batches/{batch_id}/lines", response_model=list[UnionLineOut],
            summary="整批保存部门行（仅草稿，部门唯一）")
def replace_lines(batch_id: int, payload: UnionLinesReplace, db: Session = Depends(get_db)):
    with _translated():
        return service.replace_lines(db, batch_id, payload)


@router.get("/batches/{batch_id}/preview", response_model=UnionPreviewOut,
            summary="计算预览（只读，不落库）")
def preview(batch_id: int, db: Session = Depends(get_db)):
    with _translated():
        return service.preview(db, batch_id)


@router.get("/batches/{batch_id}/checks", response_model=UnionChecksOut,
            summary="校验结论与计数（ok/failed/unknown）")
def checks(batch_id: int, db: Session = Depends(get_db)):
    with _translated():
        return service.checks(db, batch_id)


@router.post("/batches/{batch_id}/confirm", response_model=UnionBatchOut,
             summary="确认批次（draft → confirmed，缺数据拒绝）")
def confirm_batch(batch_id: int, db: Session = Depends(get_db)):
    with _translated():
        return service.confirm_batch(db, batch_id)


@router.get("/batches/{batch_id}/export.xlsx", summary="导出三页签 xlsx 底稿")
def export_batch_xlsx(batch_id: int, db: Session = Depends(get_db)):
    with _translated():
        file = service.export_xlsx(db, batch_id)
    return _download(file)


@router.get("/summary", response_model=UnionSummaryOut, summary="工会经费计提摘要（按主体/期间）")
def summary(
    entity: str | None = Query(default=None),
    period: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    with _translated():
        return service.union_summary(db, entity=entity or None, period=period or None)
