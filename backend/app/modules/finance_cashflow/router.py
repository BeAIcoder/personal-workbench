"""finance_cashflow 模块接口：/api/finance/cashflow（前缀由注册器按 manifest.prefix 挂载）。"""
import zipfile
from io import BytesIO
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy.orm import Session

from ...database import get_db
from . import service
from .schemas import CashflowRunCreate, CashflowRunListOut, CashflowRunOut

router = APIRouter(tags=["财务模块·现金流填报"])


async def _read_upload(file: UploadFile) -> bytes:
    """经共享 read_upload_limited 分块限额读取单个上传文件。

    函数内导入（core 上传契约的补丁点：测试替换 app.upload.read_upload_limited
    即作用于本端点）；共享模块缺失时 ImportError 直接抛出——fail-closed，
    不再回退无界整读。超限抛 UploadTooLargeError（main.py 已全局映射 400），
    读取期即中断，不会把超大文件整体吞进内存后再拒（审计 A01）。
    """
    from ...upload import read_upload_limited

    return await read_upload_limited(file, service.MAX_UPLOAD_BYTES)


@router.post("/runs", response_model=CashflowRunOut, status_code=201,
             summary="上传输入文件建运行（银行辅助明细账+模板等，或一个 ZIP）")
async def create_run(
    actual_month: int = Form(..., ge=1, le=12),
    files: list[UploadFile] = File(...),
    db: Session = Depends(get_db),
):
    try:
        uploads = [(f.filename or "file.xlsx", await _read_upload(f)) for f in files]
        # 归集/落盘是同步重活：丢线程池执行，避免阻塞事件循环、拖垮并发请求
        return await run_in_threadpool(service.create_run, db, uploads, actual_month)
    except service.EngineUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/runs", response_model=CashflowRunListOut, summary="运行记录列表（分页，按 id 倒序）")
def list_runs(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    items, total = service.list_runs(db, page=page, page_size=page_size)
    return {"total": total, "items": items, "page": page, "page_size": page_size}


@router.get("/runs/{run_id}", response_model=CashflowRunOut, summary="运行记录详情")
def run_detail(run_id: int, db: Session = Depends(get_db)):
    return service._out(service.get_run(db, run_id))


@router.post("/runs/{run_id}/execute", response_model=CashflowRunOut,
             summary="执行运行（子进程调引擎，约 1-3 分钟）")
def execute_run(run_id: int, db: Session = Depends(get_db)):
    try:
        return service.execute_run(db, run_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except service.EngineUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/runs/{run_id}/artifacts/{name}", summary="下载单个产物")
def download_artifact(run_id: int, name: str, db: Session = Depends(get_db)):
    run = service.get_run(db, run_id)
    try:
        path = service.artifact_path(run, name)
    except HTTPException:
        raise
    return FileResponse(str(path), filename=Path(name).name)


@router.get("/runs/{run_id}/download", summary="打包下载全部产物（ZIP）")
def download_all(run_id: int, db: Session = Depends(get_db)):
    run = service.get_run(db, run_id)
    if not run.artifacts:
        raise HTTPException(status_code=409, detail="该运行暂无产物")
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for a in run.artifacts:
            p = Path(a["path"])
            if p.is_file():
                zf.write(p, arcname=a["name"])
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename=cashflow_run_{run_id}.zip"},
    )
