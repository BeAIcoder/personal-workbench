"""
财务数据导入路由
提供财务报表导入、模板下载等API
"""
import asyncio
import logging
import os
import tempfile
from fastapi import APIRouter, UploadFile, File, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional

from ..services.financial_data_importer import (
    FinancialDataImporter,
    StatementType,
    ImportResult,
)

try:
    # 统一上传防护由 core 任务提供（app/upload.py）：分块限额读取替代全量 file.read()；
    # 共享模块未落地期间名称置空，端点明确 503 失败，绝不退回无界读取。
    from app.upload import read_upload_limited
except ImportError:  # pragma: no cover - 集成共享模块后不存在该分支
    read_upload_limited = None

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/financial-import", tags=["财务数据导入"])

importer = FinancialDataImporter()

# 上传约束：扩展名白名单（大小写不敏感）+ 大小上限（与 finance_expense 模块一致，测试可 monkeypatch 调低）
ALLOWED_EXTENSIONS = (".xlsx", ".xls")
MAX_UPLOAD_BYTES = 20 * 1024 * 1024


async def _read_upload_limited(file: UploadFile) -> bytes:
    """经共享 read_upload_limited 限额读取上传内容。

    超限/读取失败映射 400（沿用「上限」口径）；共享读取器缺失时 503 明确失败，
    不退回全量 await file.read()（审计台账 A01：超大/chunked 上传无界读内存）。
    """
    if read_upload_limited is None:
        logger.error("共享上传模块 app.upload 不可用，拒绝财务报表上传读取")
        raise HTTPException(status_code=503, detail="上传读取组件暂不可用，请稍后重试")
    try:
        return await read_upload_limited(file, MAX_UPLOAD_BYTES)
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("财务报表上传受限读取失败（上限 %s 字节）：%s", MAX_UPLOAD_BYTES, exc)
        raise HTTPException(
            status_code=400,
            detail=f"文件超过 {MAX_UPLOAD_BYTES // 1024 // 1024}MB 大小上限",
        ) from exc


class ImportResponse(BaseModel):
    """导入响应模型"""
    success: bool
    summary: str
    format_detected: Optional[str] = None
    enterprise_info: Optional[dict] = None
    financial_data: Optional[dict] = None
    tax_adjustments: Optional[dict] = None
    taxable_income_estimate: Optional[dict] = None
    errors: list[dict] = []
    warnings: list[dict] = []


@router.post("/import", response_model=ImportResponse, summary="导入财务报表")
async def import_financial_statement(file: UploadFile = File(...)):
    """
    上传Excel财务报表文件并解析。

    支持格式：
    - 标准导入模板（下载后填写的Excel）
    - 财务系统导出的Excel文件
    """
    # Validate file type
    if not file.filename or not file.filename.lower().endswith(ALLOWED_EXTENSIONS):
        raise HTTPException(status_code=400, detail="仅支持.xlsx或.xls格式的Excel文件")

    content = await _read_upload_limited(file)
    if len(content) > MAX_UPLOAD_BYTES:  # 双保险：共享读取器已限额，保留既有口径
        raise HTTPException(
            status_code=400,
            detail=f"文件超过 {MAX_UPLOAD_BYTES // 1024 // 1024}MB 大小上限",
        )

    # Save uploaded file temporarily
    suffix = os.path.splitext(file.filename)[1]
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    try:
        # 解析为同步重活：移入线程池执行，慢解析期间不阻塞事件循环（审计台账 A05）
        result = await asyncio.to_thread(importer.import_from_file, tmp_path)
    except Exception:
        # 服务器保留完整异常日志；对外只给通用中文提示，不回带本机异常路径（审计台账 A09）
        logger.exception("财务报表导入处理失败：%s", file.filename)
        raise HTTPException(status_code=500, detail="文件处理失败，请检查文件格式或稍后重试")
    finally:
        # Clean up temp file
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    if not result.success and not result.errors:
        raise HTTPException(status_code=400, detail="无法解析该文件，请检查文件格式")

    # Build response
    response = ImportResponse(
        success=result.success and not result.errors,
        summary=result.summary,
        format_detected=result.mapped_data.get("format"),
        enterprise_info=result.mapped_data.get("enterprise_info"),
        financial_data=result.mapped_data.get("financial_data"),
        tax_adjustments=result.mapped_data.get("tax_adjustments"),
        taxable_income_estimate=result.mapped_data.get("taxable_income_estimate"),
        errors=[{"field": e.field, "message": e.message, "severity": e.severity} for e in result.errors],
        warnings=[{"field": w.field, "message": w.message, "severity": w.severity} for w in result.warnings],
    )
    return response


@router.post("/import/{enterprise_id}", response_model=ImportResponse,
             summary="导入财务报表到指定企业")
async def import_financial_statement_to_enterprise(
    enterprise_id: int,
    file: UploadFile = File(...),
):
    """
    上传Excel财务报表文件并解析，关联到指定企业。

    注意：此端点仅解析数据，不自动写入数据库。
    解析结果返回后由前端确认后调用写入接口。
    """
    if not file.filename or not file.filename.lower().endswith(ALLOWED_EXTENSIONS):
        raise HTTPException(status_code=400, detail="仅支持.xlsx或.xls格式的Excel文件")

    content = await _read_upload_limited(file)
    if len(content) > MAX_UPLOAD_BYTES:  # 双保险：共享读取器已限额，保留既有口径
        raise HTTPException(
            status_code=400,
            detail=f"文件超过 {MAX_UPLOAD_BYTES // 1024 // 1024}MB 大小上限",
        )

    suffix = os.path.splitext(file.filename)[1]
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    try:
        # 解析为同步重活：移入线程池执行，慢解析期间不阻塞事件循环（审计台账 A05）
        result = await asyncio.to_thread(importer.import_from_file, tmp_path)
    except Exception:
        # 服务器保留完整异常日志；对外只给通用中文提示，不回带本机异常路径（审计台账 A09）
        logger.exception("财务报表导入处理失败（企业 %s）：%s", enterprise_id, file.filename)
        raise HTTPException(status_code=500, detail="文件处理失败，请检查文件格式或稍后重试")
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    response = ImportResponse(
        success=result.success and not result.errors,
        summary=result.summary,
        format_detected=result.mapped_data.get("format"),
        enterprise_info=result.mapped_data.get("enterprise_info"),
        financial_data=result.mapped_data.get("financial_data"),
        tax_adjustments=result.mapped_data.get("tax_adjustments"),
        taxable_income_estimate=result.mapped_data.get("taxable_income_estimate"),
        errors=[{"field": e.field, "message": e.message, "severity": e.severity} for e in result.errors],
        warnings=[{"field": w.field, "message": w.message, "severity": w.severity} for w in result.warnings],
    )
    return response


@router.get("/template", summary="下载导入模板")
async def download_template():
    """
    下载标准财务报表导入模板。

    模板包含资产负债表、利润表和现金流量表三个sheet，
    用户填写后可通过导入接口上传。
    """
    try:
        template_bytes = importer.generate_template()
    except Exception:
        # 服务器保留完整异常日志；对外只给通用中文提示，不回带本机异常路径（审计台账 A09）
        logger.exception("财务报表模板生成失败")
        raise HTTPException(status_code=500, detail="模板生成失败，请稍后重试")

    return StreamingResponse(
        iter([template_bytes]),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": 'attachment; filename="financial_import_template.xlsx"',
        },
    )


@router.get("/template/balance-sheet", summary="下载资产负债表模板")
async def download_balance_sheet_template():
    """下载仅包含资产负债表的导入模板"""
    try:
        template_bytes = importer.generate_template(include_sheets=[StatementType.BALANCE_SHEET])
    except Exception:
        # 服务器保留完整异常日志；对外只给通用中文提示，不回带本机异常路径（审计台账 A09）
        logger.exception("资产负债表模板生成失败")
        raise HTTPException(status_code=500, detail="模板生成失败，请稍后重试")

    return StreamingResponse(
        iter([template_bytes]),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": 'attachment; filename="balance_sheet_template.xlsx"',
        },
    )


@router.get("/template/income-statement", summary="下载利润表模板")
async def download_income_statement_template():
    """下载仅包含利润表的导入模板"""
    try:
        template_bytes = importer.generate_template(include_sheets=[StatementType.INCOME_STATEMENT])
    except Exception:
        # 服务器保留完整异常日志；对外只给通用中文提示，不回带本机异常路径（审计台账 A09）
        logger.exception("利润表模板生成失败")
        raise HTTPException(status_code=500, detail="模板生成失败，请稍后重试")

    return StreamingResponse(
        iter([template_bytes]),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": 'attachment; filename="income_statement_template.xlsx"',
        },
    )
