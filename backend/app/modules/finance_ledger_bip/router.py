"""finance_ledger_bip 模块接口：/api/finance/ledger/bip（前缀由注册器按 manifest.prefix 挂载）。"""
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy.orm import Session

from ...database import get_db
from . import service
from .schemas import (
    BalanceSummaryOut,
    BatchListOut,
    ConfirmIn,
    ConfirmOut,
    EntryListOut,
    ExportIn,
    ExportOut,
    PreviewOut,
    VoucherDetailOut,
    VoucherListOut,
)

router = APIRouter(tags=["财务模块·企业记账（BIP）"])


def _bad_request(exc: ValueError) -> HTTPException:
    return HTTPException(status_code=400, detail=str(exc))


# ---------------- 导入向导 ----------------

@router.post(
    "/imports/preview",
    response_model=PreviewOut,
    summary="上传 BIP 导出文件并预览（类型识别 + 列映射建议 + 前 50 行；xlsx/xls，上限 10MB）",
)
async def preview_import(file: UploadFile = File(...)):
    # 防路径穿越：只取文件名部分；扩展名白名单与大小限制在 service 层校验
    filename = Path(file.filename or "upload.xlsx").name
    content = await file.read()
    try:
        return service.preview_import(filename, content)
    except ValueError as exc:
        raise _bad_request(exc) from exc


@router.post(
    "/imports/confirm",
    response_model=ConfirmOut,
    summary="确认导入（借贷平衡校验记 warning 不阻断；重复凭证跳过计数）",
)
def confirm_import(payload: ConfirmIn, db: Session = Depends(get_db)):
    try:
        return service.confirm_import(
            db,
            token=payload.token,
            file_type=payload.file_type,
            user_mapping=payload.mapping,
            period=payload.period,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise _bad_request(exc) from exc


@router.get("/imports", response_model=BatchListOut, summary="导入批次列表（按时间倒序分页）")
def list_imports(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    db: Session = Depends(get_db),
):
    return service.list_batches(db, page=page, page_size=page_size)


# ---------------- 凭证与分录查询 ----------------

@router.get("/vouchers", response_model=VoucherListOut, summary="凭证组合筛选（期间/账簿/凭证号/摘要关键词）+ 分页")
def list_vouchers(
    period: str | None = Query(None, description="会计期间 YYYY-MM"),
    ledger: str | None = Query(None, description="核算账簿（模糊匹配）"),
    voucher_no: str | None = Query(None, description="凭证号（模糊匹配）"),
    keyword: str | None = Query(None, description="摘要关键词（含分录摘要）"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    db: Session = Depends(get_db),
):
    return service.query_vouchers(
        db, period=period, ledger=ledger, voucher_no=voucher_no, keyword=keyword,
        page=page, page_size=page_size,
    )


@router.get("/vouchers/{voucher_id}", response_model=VoucherDetailOut, summary="凭证详情（分录借贷两栏对称）")
def voucher_detail(voucher_id: int, db: Session = Depends(get_db)):
    try:
        return service.get_voucher_detail(db, voucher_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/entries", response_model=EntryListOut, summary="分录查询（科目编码前缀/期间等组合筛选）")
def list_entries(
    subject_prefix: str | None = Query(None, description="科目编码前缀，如 1002"),
    period: str | None = Query(None, description="会计期间 YYYY-MM"),
    ledger: str | None = Query(None, description="核算账簿（模糊匹配）"),
    voucher_no: str | None = Query(None, description="凭证号（模糊匹配）"),
    keyword: str | None = Query(None, description="分录摘要关键词"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    return service.query_entries(
        db, subject_prefix=subject_prefix, period=period, ledger=ledger,
        voucher_no=voucher_no, keyword=keyword, page=page, page_size=page_size,
    )


@router.get("/balances/summary", response_model=BalanceSummaryOut, summary="科目发生额汇总（按期间聚合凭证分录）")
def balances_summary(
    period: str = Query(..., description="会计期间 YYYY-MM"),
    db: Session = Depends(get_db),
):
    try:
        return service.balance_summary(db, period)
    except ValueError as exc:
        raise _bad_request(exc) from exc


# ---------------- 数据仓联动 ----------------

@router.post(
    "/vouchers/export-to-vault",
    response_model=ExportOut,
    summary="凭证数据入仓（依赖 finance_vault 模块；不可用时返回 503）",
)
def export_to_vault(payload: ExportIn, db: Session = Depends(get_db)):
    try:
        return service.export_vouchers_to_vault(db, period=payload.period)
    except service.VaultUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise _bad_request(exc) from exc
