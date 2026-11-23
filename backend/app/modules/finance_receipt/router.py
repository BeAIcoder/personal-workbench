"""finance_receipt 模块接口：/api/finance/receipt（前缀由注册器按 manifest.prefix 挂载）。"""
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response
from sqlalchemy.orm import Session

from ...database import get_db
from . import service
from .schemas import ReceiptItemPatch, ReceiptMergeIn, ReceiptScanIn

router = APIRouter(tags=["财务模块·银行回单整理"])


def _not_found(exc: LookupError) -> HTTPException:
    return HTTPException(status_code=404, detail=str(exc))


def _bad_request(exc: ValueError) -> HTTPException:
    return HTTPException(status_code=400, detail=str(exc))


@router.get("/config", summary="银行回单整理默认目录与五类口径")
def receipt_config():
    return service.config()


@router.post("/scan", summary="按账期扫描回单源目录，建立待复核批次（不生成 PDF）")
async def scan_receipts(body: ReceiptScanIn, db: Session = Depends(get_db)):
    try:
        return await run_in_threadpool(service.scan, db, body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/batches", summary="回单整理批次列表（不含明细）")
def list_batches(limit: int = Query(default=50, ge=1, le=200), db: Session = Depends(get_db)):
    items = service.list_batches(db, limit)
    return {"total": len(items), "items": items}


@router.get("/batches/{batch_id}", summary="批次详情（含逐笔明细）")
def batch_detail(batch_id: int, db: Session = Depends(get_db)):
    try:
        return service.batch_detail(db, batch_id)
    except LookupError as exc:
        raise _not_found(exc) from exc


@router.patch("/batches/{batch_id}/items/{item_id}", summary="逐笔复核：修改分类或是否纳入")
def patch_item(batch_id: int, item_id: int, body: ReceiptItemPatch, db: Session = Depends(get_db)):
    try:
        return service.update_item(db, batch_id, item_id, body)
    except LookupError as exc:
        raise _not_found(exc) from exc


@router.post("/batches/{batch_id}/generate", summary="按已复核的纳入项生成账户×类别 PDF 与清单（原子替换）")
async def generate_batch(batch_id: int, db: Session = Depends(get_db)):
    try:
        return await run_in_threadpool(service.generate, db, batch_id)
    except LookupError as exc:
        raise _not_found(exc) from exc
    except service.ReceiptConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise _bad_request(exc) from exc


@router.get("/batches/{batch_id}/files/{file_id}", summary="下载或预览单个生成文件")
def download_file(
    batch_id: int,
    file_id: str,
    disposition: str = Query(default="attachment", pattern="^(attachment|inline)$"),
    db: Session = Depends(get_db),
):
    try:
        data, name, media = service.file_download(db, batch_id, file_id)
    except LookupError as exc:
        raise _not_found(exc) from exc
    return Response(
        content=data,
        media_type=media,
        headers={"Content-Disposition": service.content_disposition(name, disposition)},
    )


@router.post("/batches/{batch_id}/merge", summary="合并选中的 PDF 分册并下载")
async def merge_files(batch_id: int, body: ReceiptMergeIn, db: Session = Depends(get_db)):
    try:
        data = await run_in_threadpool(service.merge_pdfs, db, batch_id, body.file_ids)
    except LookupError as exc:
        raise _not_found(exc) from exc
    except ValueError as exc:
        raise _bad_request(exc) from exc
    return Response(
        content=data,
        media_type="application/pdf",
        headers={"Content-Disposition": service.content_disposition("银行回单合并.pdf")},
    )


@router.get("/batches/{batch_id}/download", summary="整批产物打包下载（ZIP，保留账户目录）")
async def download_zip(batch_id: int, db: Session = Depends(get_db)):
    try:
        data, name = await run_in_threadpool(service.archive_zip, db, batch_id)
    except LookupError as exc:
        raise _not_found(exc) from exc
    except ValueError as exc:
        raise _bad_request(exc) from exc
    return Response(
        content=data,
        media_type="application/zip",
        headers={"Content-Disposition": service.content_disposition(name)},
    )


@router.get("/batches/{batch_id}/items/{item_id}/preview", summary="单笔原回单预览（PDF）")
async def preview_item(batch_id: int, item_id: int, db: Session = Depends(get_db)):
    try:
        data = await run_in_threadpool(service.preview_item, db, batch_id, item_id)
    except LookupError as exc:
        raise _not_found(exc) from exc
    return Response(
        content=data,
        media_type="application/pdf",
        headers={"Content-Disposition": service.content_disposition("回单预览.pdf", "inline")},
    )
