# -*- coding: utf-8 -*-
"""经营管理接口：/api/business/manage。

上传约束沿用宿主口径：xlsx 白名单、10MB 上限（共享 read_upload_limited 分块限额），
解析放线程池，慢解析不阻塞事件循环；确认入库单事务，同粒度整批替换。
"""
from __future__ import annotations

import asyncio
import io
import logging
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from ...database import get_db
from ...upload import read_upload_limited
from ...utils import repair_upload_mojibake
from . import fields, importer, service
from .schemas import (
    ConfirmIn,
    MetricBatch,
    RecordIn,
    RecordUpdate,
    ReportCreate,
    ReportUpdate,
    TextBatch,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["经营模块·经营管理"])

XLSX_TYPES = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/octet-stream",
    "application/vnd.ms-excel",
)


def _run(fn, *args, **kwargs):
    """统一异常映射：LookupError → 404，唯一冲突 → 409，ValueError → 400。"""
    try:
        return fn(*args, **kwargs)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except service.ImportConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _download(data: bytes, filename: str) -> StreamingResponse:
    ascii_name = "".join(
        char for char in filename if 32 <= ord(char) <= 126 and char not in '"\\'
    ) or "export.xlsx"
    return StreamingResponse(
        io.BytesIO(data),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f"attachment; filename=\"{ascii_name}\"; "
                                   f"filename*=UTF-8''{quote(filename)}"
        },
    )


@router.get("/meta", summary="字段字典（报表类型/项目/section/字段/指标键目录）")
def meta():
    return fields.meta()


@router.get("/reports", summary="报表批次列表（按期间倒序，含指标/明细/文本条数）")
def list_reports(
    report_type: str = Query(default="", pattern=r"^(control|weekly|monthly)?$"),
    project: str = Query(default="", max_length=100),
    period: str = Query(default="", pattern=r"^(\d{4}-(0[1-9]|1[0-2]))?$"),
    page: int = Query(default=1, ge=1, le=1_000_000),
    page_size: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    return _run(service.list_reports, db, report_type, project, period, page, page_size)


@router.post("/reports", summary="新建空批次（手工填报）")
def create_report(payload: ReportCreate, db: Session = Depends(get_db)):
    return _run(service.create_report, db, payload.model_dump())


@router.get("/reports/{report_id}", summary="报表详情（指标 + 明细 + 叙述）")
def get_report(report_id: int, db: Session = Depends(get_db)):
    return _run(service.get_report, db, report_id)


@router.put("/reports/{report_id}", summary="更新批次表头（标题/备注/期间）")
def update_report(report_id: int, payload: ReportUpdate, db: Session = Depends(get_db)):
    return _run(service.update_report, db, report_id, payload.model_dump(exclude_unset=True))


@router.delete("/reports/{report_id}", summary="删除批次（级联明细/指标/文本）")
def delete_report(report_id: int, db: Session = Depends(get_db)):
    return _run(service.delete_report, db, report_id)


@router.post("/imports/preview", summary="上传报表并预览（识别类型/期间/小节计数，10MB）")
async def preview_import(
    file: UploadFile = File(...),
    report_type: str = Form(...),
):
    filename = (file.filename or "upload.xlsx").rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    filename = repair_upload_mojibake(filename)
    if not filename.lower().endswith(".xlsx"):
        raise HTTPException(status_code=400, detail="只接受 .xlsx 文件（请用 Excel 另存为 .xlsx）")
    if file.content_type and file.content_type not in XLSX_TYPES:
        logger.info("经营管理上传 content-type 非标准 xlsx：%s", file.content_type)
    try:
        content = await read_upload_limited(file, importer.MAX_UPLOAD_BYTES)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("经营管理上传读取失败（上限 %s 字节）：%s", importer.MAX_UPLOAD_BYTES, exc)
        raise HTTPException(
            status_code=400,
            detail=f"文件读取失败或超过 {importer.MAX_UPLOAD_BYTES // 1024 // 1024}MB 上限",
        ) from exc
    from ...database import SessionLocal

    with SessionLocal() as db:
        try:
            return await asyncio.to_thread(service.preview, db, report_type, filename, content)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/imports/confirm", summary="确认导入（同类型+项目+期间整批替换）")
def confirm_import(payload: ConfirmIn, db: Session = Depends(get_db)):
    return _run(service.confirm, db, payload.token, payload.project, payload.period)


@router.put("/reports/{report_id}/metrics", summary="批量写入/修正指标（键必须在规格内）")
def upsert_metrics(report_id: int, payload: MetricBatch, db: Session = Depends(get_db)):
    items = [item.model_dump() for item in payload.items]
    return _run(service.upsert_metrics, db, report_id, items)


@router.post("/reports/{report_id}/records", summary="新增明细行")
def add_record(report_id: int, payload: RecordIn, db: Session = Depends(get_db)):
    return _run(service.add_record, db, report_id, payload.model_dump())


@router.put("/records/{record_id}", summary="修改明细行")
def update_record(record_id: int, payload: RecordUpdate, db: Session = Depends(get_db)):
    return _run(service.update_record, db, record_id, payload.model_dump(exclude_unset=True))


@router.delete("/records/{record_id}", summary="删除明细行")
def delete_record(record_id: int, db: Session = Depends(get_db)):
    return _run(service.delete_record, db, record_id)


@router.put("/reports/{report_id}/texts", summary="批量写入叙述小节")
def upsert_texts(report_id: int, payload: TextBatch, db: Session = Depends(get_db)):
    items = [item.model_dump() for item in payload.items]
    return _run(service.upsert_texts, db, report_id, items)


@router.get("/overview", summary="总览：指标卡 + 图表序列（出租率/收缴/招商/责任书/诉讼/预警）")
def overview(
    project: str = Query(default="", max_length=100),
    months: int = Query(default=12, ge=1, le=36),
    db: Session = Depends(get_db),
):
    return _run(service.overview, db, project, months)


@router.get("/reports/{report_id}/export.xlsx", summary="导出单份报表（指标/明细/文本三表）")
def export_report(report_id: int, db: Session = Depends(get_db)):
    name, data = _run(service.export_report, db, report_id)
    return _download(data, name)


@router.get("/overview/export.xlsx", summary="导出总览（指标卡与各图表序列）")
def export_overview(
    project: str = Query(default="", max_length=100),
    months: int = Query(default=12, ge=1, le=36),
    db: Session = Depends(get_db),
):
    name, data = _run(service.export_overview, db, project, months)
    return _download(data, name)
