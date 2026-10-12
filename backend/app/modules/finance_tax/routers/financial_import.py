"""
财务数据导入路由
提供财务报表导入、模板下载等API
"""
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

router = APIRouter(prefix="/financial-import", tags=["财务数据导入"])

importer = FinancialDataImporter()


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
    if not file.filename or not file.filename.endswith((".xlsx", ".xls")):
        raise HTTPException(status_code=400, detail="仅支持.xlsx或.xls格式的Excel文件")

    # Save uploaded file temporarily
    suffix = os.path.splitext(file.filename)[1]
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        content = await file.read()
        tmp.write(content)
        tmp_path = tmp.name

    try:
        result = importer.import_from_file(tmp_path)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"文件处理失败：{str(e)}")
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
    if not file.filename or not file.filename.endswith((".xlsx", ".xls")):
        raise HTTPException(status_code=400, detail="仅支持.xlsx或.xls格式的Excel文件")

    suffix = os.path.splitext(file.filename)[1]
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        content = await file.read()
        tmp.write(content)
        tmp_path = tmp.name

    try:
        result = importer.import_from_file(tmp_path)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"文件处理失败：{str(e)}")
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
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"模板生成失败：{str(e)}")

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
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"模板生成失败：{str(e)}")

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
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"模板生成失败：{str(e)}")

    return StreamingResponse(
        iter([template_bytes]),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": 'attachment; filename="income_statement_template.xlsx"',
        },
    )
