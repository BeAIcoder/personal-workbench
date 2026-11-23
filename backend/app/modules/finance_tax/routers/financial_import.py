"""
财务数据导入路由
提供财务报表导入、模板下载等API
"""
import asyncio
import logging
import os
import re
import tempfile

from fastapi import APIRouter, UploadFile, File, HTTPException, Depends, Form
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session
from typing import Optional

from ....database import get_db
from ..models import Enterprise, FinancialData
from ..services.financial_data_importer import (
    FinancialDataImporter,
    StatementType,
    ImportResult,
    quantize_wan,
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
    # 是否真正写入数据库；false 表示仅解析预览（无企业身份/存在校验错误）
    persisted: bool = False
    enterprise_id: Optional[int] = None
    financial_data_id: Optional[int] = None


_TAX_NUMBER_RE = re.compile(r"^[0-9A-Z]{15,20}$")


def _as_text(value) -> str:
    """只接受真实字符串：直接调用端点时 Form/Query 等参数默认对象按「未提供」处理。"""
    return value.strip() if isinstance(value, str) else ""


def _normalize_tax_number(value) -> str:
    """税号归一：去空白并转大写（统一社会信用代码/纳税人识别号均为大写字母数字）。"""
    text = _as_text(value)
    return re.sub(r"\s+", "", text).upper() if text else ""


def _persist_by_default() -> bool:
    """HTTP 请求的落库默认值：私有依赖，不进入 OpenAPI 查询参数，客户端不可覆盖。"""
    return True


def _resolve_persist(persist) -> bool:
    """是否落库：仅显式的 True 落库。

    HTTP 由私有依赖注入 True；直接调用端点函数（未经 FastAPI 注入）时 persist 是
    Depends 标记，按预览处理；显式内部调用可传 persist=True/False。
    """
    return persist is True


def _raise_for_period_error(result: ImportResult) -> None:
    """期间缺失/非法由解析器标记为 period 错误：导入入口统一中文 400。"""
    for err in result.errors:
        if err.field == "period":
            raise HTTPException(status_code=400, detail=err.message)


def _identity_from_result(result: ImportResult, tax_number=None,
                          enterprise_name=None) -> tuple[str, str]:
    info = result.mapped_data.get("enterprise_info") or {}
    tax = _normalize_tax_number(_as_text(tax_number) or info.get("tax_number"))
    name = _as_text(enterprise_name) or _as_text(info.get("name"))
    return tax, name


def _ensure_file_identity_matches(enterprise: Enterprise, result: ImportResult) -> None:
    """指定企业导入时校验文件表头身份，防止把 B 公司数据写入 A 公司。

    只在文件明确给出身份（税号或编制单位）且与所选企业不同时中文 400；
    文件未写身份时不做推断。
    """
    info = result.mapped_data.get("enterprise_info") or {}
    file_tax = _normalize_tax_number(info.get("tax_number"))
    if file_tax:
        selected_tax = _normalize_tax_number(enterprise.tax_number)
        if file_tax != selected_tax:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"文件表头的纳税人识别号「{file_tax}」与所选企业「{enterprise.name}」"
                    f"（税号 {enterprise.tax_number}）不一致，已拒绝落库；"
                    "请确认企业选择或报表归属"
                ),
            )
    file_name = _as_text(info.get("name"))
    if file_name:
        selected_name = _as_text(enterprise.name)
        if file_name != selected_name:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"文件表头的编制单位「{file_name}」与所选企业「{selected_name}」不一致，"
                    "已拒绝落库；请确认企业选择或报表归属"
                ),
            )


def _find_or_create_enterprise(db: Session, tax_number: str, name: str) -> Optional[Enterprise]:
    """按税号 upsert 企业；只有名称时匹配既有企业（不凭名称新建，避免重复建档）。"""
    tax_number = _normalize_tax_number(tax_number)
    if tax_number:
        # 先匹配既有企业：历史税号可能不合规，仍应允许把数据导入该企业
        enterprise = db.query(Enterprise).filter(Enterprise.tax_number == tax_number).first()
        if enterprise is None:
            # 历史数据可能以小写录入：大小写不敏感兜底，避免重复建档
            enterprise = (
                db.query(Enterprise)
                .filter(func.upper(Enterprise.tax_number) == tax_number)
                .first()
            )
        if enterprise is not None:
            return enterprise
        # 格式护栏只针对新建建档：历史企业已在上方匹配返回
        if not _TAX_NUMBER_RE.fullmatch(tax_number):
            raise HTTPException(
                status_code=400,
                detail=(
                    "纳税人识别号格式不正确（应为 15–20 位数字或字母）："
                    f"「{tax_number}」"
                ),
            )
        if not name:
            raise HTTPException(
                status_code=400,
                detail="新建企业需要企业名称：请在报表表头填写编制单位，或提供企业名称后重试",
            )
        # 与 POST /enterprises 的默认口径一致：导入建档默认查账征收
        enterprise = Enterprise(name=name, tax_number=tax_number, tax_type="查账征收")
        db.add(enterprise)
        db.flush()
        return enterprise
    if name:
        matches = db.query(Enterprise).filter(Enterprise.name == name).limit(2).all()
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise HTTPException(
                status_code=409,
                detail=f"存在多家名称相同的企业「{name}」，请提供纳税人识别号后重试",
            )
    return None


def _upsert_financial_data(db: Session, enterprise: Enterprise,
                           financial: dict) -> FinancialData:
    """按 (企业, 年度, 季度) upsert 财务数据；重复导入更新同一行。"""
    year = financial.get("year")
    quarter = financial.get("quarter")
    row = (
        db.query(FinancialData)
        .filter(
            FinancialData.enterprise_id == enterprise.id,
            FinancialData.year == year,
            FinancialData.quarter == quarter,
        )
        .first()
    )
    values = {
        "total_revenue": quantize_wan(financial.get("total_revenue")),
        "total_cost": quantize_wan(financial.get("total_cost")),
        "total_expense": quantize_wan(financial.get("total_expense")),
        "profit": quantize_wan(financial.get("profit")),
        "non_taxable_income": quantize_wan(financial.get("non_taxable_income")),
        "prior_year_loss": quantize_wan(financial.get("prior_year_loss")),
    }
    if row is None:
        row = FinancialData(
            enterprise_id=enterprise.id, year=year, quarter=quarter, **values
        )
        db.add(row)
    else:
        for key, value in values.items():
            setattr(row, key, value)
    db.flush()
    return row


def _persist_import(db: Session, result: ImportResult, *,
                    enterprise: Optional[Enterprise] = None,
                    tax_number: str = "",
                    enterprise_name: str = "") -> tuple[Optional[Enterprise], Optional[FinancialData], bool]:
    """单事务落库（调用方负责唯一一次 commit/rollback）。

    返回 (企业, 财务数据行, 是否发生写入)；无企业身份时返回 (None, None, False)。
    期间校验在创建企业之前完成：失败时不留半成品企业。
    """
    financial = result.mapped_data.get("financial_data") or {}
    if financial and (financial.get("year") is None or financial.get("quarter") is None):
        raise HTTPException(
            status_code=400,
            detail="未识别到报表期间，无法落库：请在报表表头填写期间（如 2026年1季度 / 2026-03）后重试",
        )

    if enterprise is None:
        enterprise = _find_or_create_enterprise(db, tax_number, enterprise_name)
        if enterprise is None:
            return None, None, False

    row = None
    changed = False
    if financial:
        row = _upsert_financial_data(db, enterprise, financial)
        changed = True

    total_assets = (result.mapped_data.get("enterprise_info") or {}).get("total_assets")
    if total_assets is not None and float(total_assets) > 0:
        enterprise.total_assets = quantize_wan(total_assets)
        changed = True

    db.flush()
    if row is not None:
        # 响应回填 DB 实际值：预览与落库同口径，且避免二次取整差异
        result.mapped_data["financial_data"] = {
            "year": row.year,
            "quarter": row.quarter,
            "total_revenue": row.total_revenue,
            "total_cost": row.total_cost,
            "total_expense": row.total_expense,
            "profit": row.profit,
            "non_taxable_income": row.non_taxable_income,
            "prior_year_loss": row.prior_year_loss,
        }
    return enterprise, row, changed


def _build_response(result: ImportResult, *, persisted: bool = False,
                    enterprise_id: Optional[int] = None,
                    financial_data_id: Optional[int] = None,
                    extra_warnings: list[dict] | None = None) -> ImportResponse:
    return ImportResponse(
        success=result.success and not result.errors,
        summary=result.summary,
        format_detected=result.mapped_data.get("format"),
        enterprise_info=result.mapped_data.get("enterprise_info"),
        financial_data=result.mapped_data.get("financial_data"),
        tax_adjustments=result.mapped_data.get("tax_adjustments"),
        taxable_income_estimate=result.mapped_data.get("taxable_income_estimate"),
        errors=[{"field": e.field, "message": e.message, "severity": e.severity} for e in result.errors],
        warnings=(
            [{"field": w.field, "message": w.message, "severity": w.severity} for w in result.warnings]
            + list(extra_warnings or [])
        ),
        persisted=persisted,
        enterprise_id=enterprise_id,
        financial_data_id=financial_data_id,
    )


async def _parse_upload(file: UploadFile, label: str) -> ImportResult:
    """扩展名/大小门禁 + 临时文件 + 线程池解析（两个导入入口共用）。"""
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
        logger.exception("财务报表导入处理失败%s：%s", label, file.filename)
        raise HTTPException(status_code=500, detail="文件处理失败，请检查文件格式或稍后重试")
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    if not result.success and not result.errors:
        raise HTTPException(status_code=400, detail="无法解析该文件，请检查文件格式")
    return result


@router.post("/import", response_model=ImportResponse, summary="导入财务报表")
async def import_financial_statement(
    file: UploadFile = File(...),
    tax_number: Optional[str] = Form(None),
    enterprise_name: Optional[str] = Form(None),
    db: Session = Depends(get_db),
    persist: bool = Depends(_persist_by_default),
):
    """
    上传Excel财务报表文件，解析并落库。

    企业身份来源（按优先级）：表单 tax_number/enterprise_name → 报表表头
    「纳税人识别号/编制单位」。有税号时按税号 upsert 企业；仅名称时匹配既有企业。
    无法识别企业身份时仅返回解析结果（persisted=false），不写库。
    期间缺失/非法一律中文 400，绝不回退当前期间。
    """
    result = await _parse_upload(file, "")
    _raise_for_period_error(result)

    persisted = False
    enterprise_id = None
    financial_data_id = None
    extra_warnings: list[dict] = []

    if _resolve_persist(persist) and result.success and not result.errors:
        tax, name = _identity_from_result(result, tax_number, enterprise_name)
        if tax or name:
            try:
                enterprise, row, changed = _persist_import(
                    db, result, tax_number=tax, enterprise_name=name
                )
                if enterprise is not None and changed:
                    db.commit()
                    persisted = True
                    enterprise_id = enterprise.id
                    financial_data_id = row.id if row is not None else None
                else:
                    db.rollback()
                    extra_warnings.append({
                        "field": "enterprise",
                        "message": "未能匹配到企业或没有可落库的数据，本次仅解析未落库",
                        "severity": "warning",
                    })
            except HTTPException:
                db.rollback()
                raise
            except Exception:
                db.rollback()
                logger.exception("财务数据落库失败：%s", file.filename)
                raise HTTPException(status_code=500, detail="财务数据保存失败，请稍后重试")
        else:
            extra_warnings.append({
                "field": "enterprise",
                "message": "未识别到企业身份（纳税人识别号或企业名称），本次仅解析未落库",
                "severity": "warning",
            })
    elif result.errors:
        extra_warnings.append({
            "field": "persist",
            "message": "存在校验错误，本次仅解析未落库",
            "severity": "warning",
        })

    return _build_response(
        result,
        persisted=persisted,
        enterprise_id=enterprise_id,
        financial_data_id=financial_data_id,
        extra_warnings=extra_warnings,
    )


@router.post("/import/{enterprise_id}", response_model=ImportResponse,
             summary="导入财务报表到指定企业")
async def import_financial_statement_to_enterprise(
    enterprise_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    persist: bool = Depends(_persist_by_default),
):
    """
    上传Excel财务报表文件，解析并写入指定企业（按年/季度 upsert）。

    期间缺失/非法中文 400；企业不存在时返回解析预览并明确 warning（不落库）；
    校验错误时只返回解析结果不落库。
    """
    result = await _parse_upload(file, f"（企业 {enterprise_id}）")
    _raise_for_period_error(result)

    persisted = False
    financial_data_id = None
    extra_warnings: list[dict] = []

    if _resolve_persist(persist):
        enterprise = db.query(Enterprise).filter(Enterprise.id == enterprise_id).first()
        if not enterprise:
            # 保持既有「解析预览」契约：企业不存在时明确提示且绝不落库
            extra_warnings.append({
                "field": "enterprise",
                "message": f"企业 {enterprise_id} 不存在，本次仅解析未落库",
                "severity": "warning",
            })
        else:
            # 文件明确写出企业身份且与所选企业不同：中文 400，防止错写
            _ensure_file_identity_matches(enterprise, result)
            if result.success and not result.errors:
                try:
                    enterprise, row, changed = _persist_import(db, result, enterprise=enterprise)
                    if changed:
                        db.commit()
                        persisted = True
                        financial_data_id = row.id if row is not None else None
                    else:
                        db.rollback()
                        extra_warnings.append({
                            "field": "persist",
                            "message": "报表中没有可落库的财务数据或资产信息，本次仅解析",
                            "severity": "warning",
                        })
                except HTTPException:
                    db.rollback()
                    raise
                except Exception:
                    db.rollback()
                    logger.exception("财务数据落库失败（企业 %s）：%s", enterprise_id, file.filename)
                    raise HTTPException(status_code=500, detail="财务数据保存失败，请稍后重试")
            else:
                extra_warnings.append({
                    "field": "persist",
                    "message": "存在校验错误，本次仅解析未落库",
                    "severity": "warning",
                })

    return _build_response(
        result,
        persisted=persisted,
        enterprise_id=enterprise_id if persisted else None,
        financial_data_id=financial_data_id,
        extra_warnings=extra_warnings,
    )


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
