"""finance_invoice 模块接口：/api/finance/invoice（前缀由注册器按 manifest.prefix 挂载）。"""
import asyncio
import logging

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from ...database import get_db
from . import service
from .engine import EngineUnavailableError, engine_status
from .schemas import ExportResult, InvoiceBatchDetail, InvoiceBatchOut, InvoiceItemOut, InvoiceItemPatch

try:
    # 统一上传防护由 core 任务提供（app/upload.py）：分块限额读取替代全量 file.read()；
    # 共享模块未落地期间名称置空，端点明确 503 失败，绝不退回无界读取。
    from ...upload import read_upload_limited
except ImportError:  # pragma: no cover - 集成共享模块后不存在该分支
    read_upload_limited = None

logger = logging.getLogger(__name__)

router = APIRouter(tags=["财务模块·发票处理"])


async def _read_upload(file: UploadFile, max_bytes: int) -> bytes:
    """经共享 read_upload_limited 限额读取单个上传内容（审计 A01）。

    超限/读取失败映射 400；共享读取器缺失时 503 明确失败，不退回全量 await file.read()。
    """
    if read_upload_limited is None:
        logger.error("共享上传模块 app.upload 不可用，拒绝发票上传读取")
        raise HTTPException(status_code=503, detail="上传读取服务暂不可用，请稍后重试")
    try:
        return await read_upload_limited(file, max_bytes)
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("发票上传受限读取失败（上限 %s 字节）：%s", max_bytes, exc)
        raise HTTPException(
            status_code=400,
            detail=f"文件读取失败或超过 {max_bytes // 1024 // 1024}MB 大小上限",
        ) from exc


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
    # 逐文件限额读取并累计合计：单文件沿用 service.MAX_FILE_BYTES，
    # 批次合计超 service.MAX_BATCH_BYTES 即止（后续文件不再读取），
    # 避免旧行为「全量读入内存后」才在 service 层发现超限。
    uploads: list[tuple[str, bytes]] = []
    total = 0
    for f in files:
        content = await _read_upload(f, service.MAX_FILE_BYTES)
        total += len(content)
        if total > service.MAX_BATCH_BYTES:
            raise HTTPException(
                status_code=400,
                detail=f"批次总大小超过 {service.MAX_BATCH_BYTES // 1024 // 1024}MB 上限",
            )
        uploads.append((f.filename or "file.pdf", content))
    try:
        # 逐张 PDF 解析为重活：丢线程池执行，避免阻塞事件循环
        batch = await asyncio.to_thread(service.create_batch, db, uploads, name=name)
    except EngineUnavailableError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=f"发票解析引擎不可用：{exc}")
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except Exception as exc:
        # 解析流程异常：批次已在 service 层置 failed+error 留痕，此处返回明确错误而非裸 500
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"批次解析失败：{exc}")
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
