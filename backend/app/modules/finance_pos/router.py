"""finance_pos 模块接口：/api/finance/pos（前缀由注册器按 manifest.prefix 挂载）。"""
import zipfile
from io import BytesIO
from pathlib import Path

from fastapi import Query, APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy.orm import Session

from ...database import get_db
from ...upload import read_upload_limited
from . import service, vouchers_service
from .schemas import PosRunListOut, PosRunOut
from .schemas import VoucherBatchListOut, VoucherBatchOut, VoucherGenerateIn, VoucherMergeIn

router = APIRouter(tags=["财务模块·POS 对账"])


async def _read_upload(file: UploadFile) -> bytes:
    """经共享 read_upload_limited 分块限额读取单个上传文件。

    超限抛 UploadTooLargeError（main.py 已全局映射 400）：读取期即中断，
    不会把超大文件整体吞进内存后再拒（审计 A01）。
    """
    return await read_upload_limited(file, service.MAX_UPLOAD_BYTES)


@router.post("/runs", response_model=PosRunOut, status_code=201,
             summary="上传输入文件建运行（SHOP 对账单 + 认领记录 + 辅助明细账 + 凭证列表，或一个 ZIP）")
async def create_run(files: list[UploadFile] = File(...), db: Session = Depends(get_db)):
    try:
        uploads: list[tuple[str, bytes]] = []
        total = 0
        for f in files:
            content = await _read_upload(f)
            total += len(content)
            if total > service.MAX_BATCH_BYTES:
                raise ValueError(
                    f"上传批次总大小超过 {service.MAX_BATCH_BYTES // 1024 // 1024}MB 上限，请拆分后上传"
                )
            uploads.append((f.filename or "file.xlsx", content))
        # 归集/落盘/引擎探测是同步重活：丢线程池执行，避免阻塞事件循环、拖垮并发请求
        return await run_in_threadpool(service.create_run, db, uploads)
    except service.EngineUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/runs", response_model=PosRunListOut, summary="运行记录列表（按 id 倒序）")
def list_runs(db: Session = Depends(get_db)):
    items = service.list_runs(db)
    return {"total": len(items), "items": items}


@router.get("/runs/{run_id}", response_model=PosRunOut, summary="运行记录详情")
def run_detail(run_id: int, db: Session = Depends(get_db)):
    return service._out(service.get_run(db, run_id))


@router.post("/runs/{run_id}/execute", response_model=PosRunOut,
             summary="执行运行（台账生成 + 四源差异分析）")
def execute_run(run_id: int, project: str | None = None, db: Session = Depends(get_db)):
    try:
        return service.execute_run(db, run_id, project=project)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except service.RunBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except service.EngineUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/runs/{run_id}", status_code=204, summary="删除运行（running 中拒绝；运行目录一并清理）")
def delete_run(run_id: int, db: Session = Depends(get_db)):
    service.delete_run(db, run_id)


@router.get("/runs/{run_id}/artifacts/{name}", summary="下载单个产物")
def download_artifact(run_id: int, name: str, db: Session = Depends(get_db)):
    run = service.get_run(db, run_id)
    path = service.artifact_path(run, name)
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
        headers={"Content-Disposition": f"attachment; filename=pos_run_{run_id}.zip"},
    )


@router.get("/vouchers/config", summary="凭证目录默认值（源目录/输出目录回退项）")
def voucher_config():
    return vouchers_service.vouchers_config()


@router.post("/vouchers/generate", response_model=VoucherBatchOut,
             summary="生成月度刷卡凭证（回单×对账单匹配合并；源目录/输出目录可人工指定）")
async def generate_vouchers(month: VoucherGenerateIn, db: Session = Depends(get_db)):
    try:
        return await run_in_threadpool(
            vouchers_service.generate_vouchers, db, month.month,
            month.statement_dir or "", month.output_dir or "",
        )
    except vouchers_service.VoucherGenerationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/vouchers", response_model=VoucherBatchListOut, summary="凭证批次列表")
def list_voucher_batches(limit: int = Query(default=50, ge=1, le=200), db: Session = Depends(get_db)):
    items = vouchers_service.list_batches(db, limit)
    return {"total": len(items), "items": items}


@router.get("/vouchers/{batch_id}/files/{name}", summary="下载单个凭证文件")
def download_voucher_file(batch_id: int, name: str, db: Session = Depends(get_db)):
    batch = vouchers_service.get_batch(db, batch_id)
    try:
        path = vouchers_service._batch_file_path(batch, name)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FileResponse(str(path), filename=Path(name).name)


@router.post("/vouchers/{batch_id}/merge", summary="合并选中的凭证 PDF 下载")
def merge_vouchers(batch_id: int, body: VoucherMergeIn, db: Session = Depends(get_db)):
    try:
        data, filename = vouchers_service.merge_files(db, batch_id, body.names)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return StreamingResponse(
        BytesIO(data),
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.delete("/vouchers/{batch_id}", status_code=204, summary="删除凭证批次")
def delete_voucher_batch(batch_id: int, db: Session = Depends(get_db)):
    vouchers_service.delete_batch(db, batch_id)
