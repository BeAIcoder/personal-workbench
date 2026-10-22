"""finance_vault 模块接口：/api/finance/vault（前缀由注册器按 manifest.prefix 挂载）。"""
import asyncio
import logging

import duckdb
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy.orm import Session

from ...database import get_db
from . import service
from .schemas import (
    DatasetListOut,
    IngestIn,
    IngestOut,
    PreviewOut,
    QueryIn,
    QueryOut,
)

try:
    # 统一上传防护由 core 任务提供（app/upload.py）：分块限额读取替代全量 file.read()；
    # 共享模块未落地期间名称置空，端点明确 503 失败，绝不退回无界读取。
    from ...upload import read_upload_limited
except ImportError:  # pragma: no cover - 集成共享模块后不存在该分支
    read_upload_limited = None

logger = logging.getLogger(__name__)

router = APIRouter(tags=["财务模块·数据仓"])


async def _read_upload(file: UploadFile, max_bytes: int) -> bytes:
    """经共享 read_upload_limited 限额读取上传内容（审计 A01）。

    超限/读取失败映射 400；共享读取器缺失时 503 明确失败，不退回全量 await file.read()。
    """
    if read_upload_limited is None:
        logger.error("共享上传模块 app.upload 不可用，拒绝数据仓上传读取")
        raise HTTPException(status_code=503, detail="上传读取服务暂不可用，请稍后重试")
    try:
        return await read_upload_limited(file, max_bytes)
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("数据仓上传受限读取失败（上限 %s 字节）：%s", max_bytes, exc)
        raise HTTPException(
            status_code=400,
            detail=f"文件读取失败或超过 {max_bytes // 1024 // 1024}MB 大小上限",
        ) from exc


@router.get("/tables", response_model=DatasetListOut, summary="数据集清单（表名/行数/来源/注册时间）")
def list_tables():
    items = service.list_datasets()
    return {"total": len(items), "items": items}


@router.post("/ingest/file", response_model=IngestOut, summary="上传 xlsx/csv 入仓（扩展名白名单 + 大小限制 + 留档）")
async def ingest_file(
    file: UploadFile = File(...),
    table: str = Query(..., description="目标表名（小写字母/数字/下划线）"),
    mode: str = Query("replace", description="replace=整表重建 / append=按列名追加"),
    sheet: str = Query("", description="xlsx 工作表名或序号（默认第一个）"),
):
    content = await _read_upload(file, service.MAX_UPLOAD_BYTES)
    try:
        # 解析入仓为重活：丢线程池执行，避免阻塞事件循环
        return await asyncio.to_thread(
            service.ingest_uploaded_file,
            table,
            file.filename or "upload",
            content,
            mode=mode,
            sheet=sheet,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/query", response_model=QueryOut, summary="只读 SQL 查询（SELECT-only 校验 + 表名白名单 + 强制限流 + 超时中断）")
def run_query(payload: QueryIn):
    try:
        return service.run_query(payload.sql)
    except service.SqlRejected as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except service.QueryTimeout as exc:
        raise HTTPException(status_code=408, detail=str(exc)) from exc
    except duckdb.Error as exc:
        raise HTTPException(status_code=400, detail=f"查询执行失败：{exc}") from exc


@router.get("/tables/{table}/preview", response_model=PreviewOut, summary="已注册表数据预览（前 100 行）")
def preview_table(table: str):
    try:
        return service.preview_table(table)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
