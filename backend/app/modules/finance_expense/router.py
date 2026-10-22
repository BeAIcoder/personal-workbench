"""finance_expense 模块接口：/api/finance/expense（前缀由注册器按 manifest.prefix 挂载）。"""
import asyncio
import gc
import logging
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from ...database import get_db
from . import service
from .schemas import ExpenseRunOut, RunDetailOut, RulesReloadOut, RulesSummaryOut, RunListOut

try:
    # 统一上传防护由 core 任务提供（app/upload.py）：分块限额读取替代全量 file.read()；
    # 共享模块未落地期间名称置空，端点明确 503 失败，绝不退回无界读取。
    from ...upload import read_upload_limited
except ImportError:  # pragma: no cover - 集成共享模块后不存在该分支
    read_upload_limited = None

logger = logging.getLogger(__name__)

router = APIRouter(tags=["财务模块·费用分类"])


async def _read_upload(file: UploadFile, max_bytes: int) -> bytes:
    """经共享 read_upload_limited 限额读取上传内容（审计 A01：替代先读后限的无界读取）。

    超限/读取失败映射 400（沿用「上限」口径）；共享读取器缺失时 503 明确失败，
    不退回全量 await file.read()。
    """
    if read_upload_limited is None:
        logger.error("共享上传模块 app.upload 不可用，拒绝费用分类上传读取")
        raise HTTPException(status_code=503, detail="上传读取服务暂不可用，请稍后重试")
    try:
        return await read_upload_limited(file, max_bytes)
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("费用上传受限读取失败（上限 %s 字节）：%s", max_bytes, exc)
        raise HTTPException(
            status_code=400,
            detail=f"文件读取失败或超过 {max_bytes // 1024 // 1024}MB 大小上限",
        ) from exc


def _discard_stored(path: Path) -> None:
    """分类失败时清理已落盘的上传文件（失败即删，避免 inbox 堆积孤儿文件）。

    引擎失败路径的异常回溯环会暂时持有 Excel 文件句柄（Windows 下句柄被占即禁删），
    故 PermissionError 时先 gc.collect() 释放回溯环再重试一次；仍失败则放弃清理，
    保证原始错误响应不受影响。
    """
    try:
        path.unlink(missing_ok=True)
    except PermissionError:
        gc.collect()
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


@router.post(
    "/classify",
    response_model=ExpenseRunOut,
    status_code=status.HTTP_201_CREATED,
    summary="上传银行存款辅助明细账 xlsx 并执行费用分类",
)
async def classify(file: UploadFile = File(...), db: Session = Depends(get_db)):
    # 防路径穿越：原文件名仅取 basename 入库展示，落盘存储名用 uuid
    original = Path(file.filename or "upload.xlsx").name
    suffix = Path(original).suffix.lower()
    if suffix not in service.ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail="仅支持 .xlsx/.xls 格式的银行明细账文件")
    content = await _read_upload(file, service.MAX_UPLOAD_BYTES)
    if not content:
        raise HTTPException(status_code=400, detail="文件内容为空")
    try:
        # 引擎首载含 os.chdir + pandas import，同为秒级重活，丢线程池避免阻塞事件循环
        await asyncio.to_thread(service.ensure_engine_ready)
    except service.EngineUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc))

    stored = service.module_subdir("inbox") / f"{uuid4().hex}{suffix}"
    stored.write_bytes(content)
    try:
        # pandas 全流程为重活：丢线程池执行，避免阻塞事件循环
        run = await asyncio.to_thread(service.classify_file, db, original, stored)
    except service.EngineUnavailableError as exc:
        _discard_stored(stored)
        raise HTTPException(status_code=503, detail=str(exc))
    except ValueError as exc:
        _discard_stored(stored)
        raise HTTPException(status_code=400, detail=str(exc))
    return service.run_to_dict(run)


@router.get("/runs", response_model=RunListOut, summary="分类运行记录分页列表")
def list_runs(
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
    db: Session = Depends(get_db),
):
    return service.list_runs(db, page, page_size)


@router.get("/runs/{run_id}", response_model=RunDetailOut, summary="运行详情（含分类汇总统计与明细行）")
def get_run(run_id: int, db: Session = Depends(get_db)):
    run = service.get_run(db, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="运行记录不存在")
    return service.run_detail(db, run)


@router.get("/runs/{run_id}/download", summary="下载分类结果 xlsx（中文文件名，RFC5987）")
def download_run(run_id: int, db: Session = Depends(get_db)):
    run = service.get_run(db, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="运行记录不存在")
    path = service.resolve_artifact(run)
    if path is None:
        raise HTTPException(status_code=404, detail="结果文件不存在或已清理")
    base_name = Path(run.original_filename).stem or f"run_{run.id}"
    return FileResponse(
        path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=f"费用分类结果_{base_name}.xlsx",
    )


@router.get("/rules/summary", response_model=RulesSummaryOut, summary="分类规则摘要（条数/分类数/来源路径）")
def rules_summary():
    try:
        return service.rules_summary()
    except service.EngineUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc))


@router.post("/rules/reload", response_model=RulesReloadOut, summary="重读规则 JSON 并返回最新摘要")
def reload_rules():
    try:
        return service.reload_rules()
    except service.EngineUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
