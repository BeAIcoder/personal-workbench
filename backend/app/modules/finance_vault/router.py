"""finance_vault 模块接口：/api/finance/vault（前缀由注册器按 manifest.prefix 挂载）。"""
import asyncio
import logging

import duckdb
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import Response
from urllib.parse import quote
from sqlalchemy.orm import Session

from ...database import get_db
from . import cleaning
from . import service
from .schemas import (
    AggIn,
    AggOut,
    AggregateSaveIn,
    CleanIn,
    DatasetListOut,
    IngestIn,
    IngestOut,
    ProfileOut,
    PreviewOut,
    QueryIn,
    QueryOut,
    RenameIn,
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
    sheet: str = Query("", description="xlsx 工作表名（含纯数字名）或 0-based 序号（默认第一个）"),
    keep_text: bool = Query(True, description="保留编码/账号为文本（默认开启）"),
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
            keep_text=keep_text,
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
    except duckdb.Error as exc:
        # 登记表有行但 DuckDB 视图缺失/损坏：属数据仓状态不一致，409 让用户重建数据集
        raise HTTPException(
            status_code=409,
            detail=f"数据集视图不可用（登记信息与数据仓不一致），请重新入仓该数据集：{exc}",
        ) from exc


@router.get("/tables/{table}/profile", response_model=ProfileOut, summary="数据集列画像（类型/缺失率/唯一率/高频值 + 清洗建议）")
async def table_profile(table: str):
    try:
        return await asyncio.to_thread(cleaning.profile_table, table)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except duckdb.Error as exc:
        raise HTTPException(status_code=409, detail=f"数据集视图不可用，请重新入仓：{exc}") from exc


@router.post("/tables/{table}/clean/preview", summary="清洗预览（dry-run，不写库）")
async def clean_preview(table: str, payload: CleanIn):
    try:
        return await asyncio.to_thread(cleaning.clean_preview, table, payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except duckdb.Error as exc:
        raise HTTPException(status_code=409, detail=f"数据集视图不可用，请重新入仓：{exc}") from exc


@router.post("/tables/{table}/clean/apply", summary="应用清洗：结果落新数据集 + 写配方日志（可复用重放）")
async def clean_apply(table: str, payload: CleanIn):
    try:
        return await asyncio.to_thread(
            cleaning.clean_apply, table, payload.model_dump(),
            payload.target_table, payload.allow_overwrite,
        )
    except service.DatasetConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except duckdb.Error as exc:
        raise HTTPException(status_code=409, detail=f"数据集视图不可用，请重新入仓：{exc}") from exc


@router.get("/tables/{table}/clean/log", summary="清洗日志（配方+逐算子报告，按 id 倒序）")
async def clean_log(table: str, limit: int = Query(20, ge=1, le=50)):
    try:
        return {"table": table, "items": await asyncio.to_thread(cleaning.get_clean_logs, table, limit)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/tables/{table}/aggregate", response_model=AggOut, summary="单表聚合：维度分组 + 指标汇总 + 过滤 + 排序")
async def table_aggregate(table: str, payload: AggIn):
    try:
        return await asyncio.to_thread(cleaning.aggregate_query, table, payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except duckdb.Error as exc:
        raise HTTPException(status_code=409, detail=f"数据集视图不可用，请重新入仓：{exc}") from exc


@router.post("/tables/{table}/aggregate/export", summary="聚合结果导出 xlsx（safe_cell 防公式注入）")
async def table_aggregate_export(table: str, payload: AggIn):
    try:
        data, filename = await asyncio.to_thread(cleaning.export_aggregate_xlsx, table, payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except duckdb.Error as exc:
        raise HTTPException(status_code=409, detail=f"数据集视图不可用，请重新入仓：{exc}") from exc
    ascii_name = "".join(c for c in filename if 32 <= ord(c) <= 126 and c not in '"\\') or "aggregate.xlsx"
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'{quote(filename)}'},
    )


@router.post("/tables/{table}/aggregate/save", summary="聚合结果另存为数据池数据集")
async def table_aggregate_save(table: str, payload: AggregateSaveIn):
    try:
        return await asyncio.to_thread(
            cleaning.save_aggregate_as_dataset, table, payload.model_dump(),
            payload.target_table, payload.allow_overwrite)
    except service.DatasetConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except duckdb.Error as exc:
        raise HTTPException(status_code=409, detail=f"数据集视图不可用，请重新入仓：{exc}") from exc


@router.get("/tables/{table}/usage", summary="数据集使用情况预检（物理修订表/清洗日志引用/一致性，供删除二次确认）")
def table_usage(table: str):
    try:
        return service.dataset_usage(table)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/tables/{table}", summary="安全删除数据集（confirm=数据集名二次确认；被引用需 force=true）")
def delete_table(
    table: str,
    confirm: str = Query(..., description="二次确认：必须与数据集名完全一致"),
    force: bool = Query(False, description="被清洗日志引用或存在未登记残留时仍需删除"),
):
    try:
        return service.delete_dataset(table, confirm, force=force)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except service.DatasetConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except duckdb.Error as exc:
        raise HTTPException(status_code=409, detail=f"数据仓操作失败，请稍后重试：{exc}") from exc


@router.post("/tables/{table}/rename", summary="重命名数据集（视图+物理修订表+登记+清洗日志引用同步）")
def rename_table(table: str, payload: RenameIn):
    try:
        return service.rename_dataset(table, payload.new_table)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except service.DatasetConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except duckdb.Error as exc:
        raise HTTPException(status_code=409, detail=f"数据仓操作失败，请稍后重试：{exc}") from exc
