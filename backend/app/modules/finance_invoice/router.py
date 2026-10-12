"""finance_invoice 模块接口：/api/finance/invoice（前缀由注册器按 manifest.prefix 挂载）。"""
import logging

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from ...database import get_db
from . import service
from .engine import EngineUnavailableError, engine_status
from .schemas import ExportResult, InvoiceBatchDetail, InvoiceBatchOut, InvoiceItemOut, InvoiceItemPatch

logger = logging.getLogger(__name__)

router = APIRouter(tags=["财务模块·发票处理"])


@router.get("/engine/status", summary="发票解析引擎可用性自检（降级探测，永不 503）")
def invoice_engine_status():
    return {"module": "finance_invoice", **engine_status()}


@router.post(
    "/batches",
    response_model=InvoiceBatchDetail,
    status_code=status.HTTP_201_CREATED,
    summary="上传发票建批次并解析（多个 PDF 或一个 ZIP；引擎缺失时 503 降级）",
)
async def create_batch(
    name: str = Form(""),
    files: list[UploadFile] = File(...),
    db: Session = Depends(get_db),
):
    uploads = [(f.filename or "file.pdf", await f.read()) for f in files]
    try:
        batch = service.create_batch(db, uploads, name=name)
    except EngineUnavailableError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=f"发票解析引擎不可用：{exc}")
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return service.batch_detail_dict(db, batch)


@router.get("/batches", response_model=list[InvoiceBatchOut], summary="批次列表（按 id 倒序）")
def list_batches(limit: int = Query(50, ge=1, le=200), db: Session = Depends(get_db)):
    return [service.batch_to_dict(b) for b in service.list_batches(db, limit)]


@router.get("/batches/{batch_id}", response_model=InvoiceBatchDetail, summary="批次详情（含明细）")
def batch_detail(batch_id: int, db: Session = Depends(get_db)):
    batch = service.get_batch(db, batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="批次不存在")
    return service.batch_detail_dict(db, batch)


@router.patch("/items/{item_id}", response_model=InvoiceItemOut, summary="人工修正发票明细（项目归类/备注）")
def patch_item(item_id: int, payload: InvoiceItemPatch, db: Session = Depends(get_db)):
    item = service.get_item(db, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="明细不存在")
    item = service.patch_item(db, item, project_name=payload.project_name, remark=payload.remark)
    partner = service.get_item(db, item.paired_item_id) if item.paired_item_id else None
    return service.item_to_dict(item, partner)


@router.post("/batches/{batch_id}/export", response_model=ExportResult, summary="导出批次（重命名复制 + 清单 xlsx + ZIP 打包）")
def export_batch(batch_id: int, db: Session = Depends(get_db)):
    batch = service.get_batch(db, batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="批次不存在")
    try:
        return service.export_batch(db, batch)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/batches/{batch_id}/download", summary="下载批次导出 ZIP（未导出返回 409）")
def download_batch(batch_id: int, db: Session = Depends(get_db)):
    batch = service.get_batch(db, batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="批次不存在")
    path = service.export_zip_path(batch)
    if path is None:
        raise HTTPException(status_code=409, detail="批次尚未导出或导出文件缺失")
    return FileResponse(path, media_type="application/zip", filename=path.name)
