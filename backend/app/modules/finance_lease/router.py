# -*- coding: utf-8 -*-
"""finance_lease 模块接口：/api/finance/lease（前缀由注册器按 manifest.prefix 挂载）。

合同 CRUD；付款计划列表/整表替换/导入预览与确认；测算创建/列表/详情/
评估行/期间行/校验；确认；xlsx 导出；摘要。路由只做参数、上传读取、状态码与
中文异常映射，业务规则全部在 service。

上传字段固定为 ``file``；预览只解析不落库，apply 才整表替换。
导出同时返回 ASCII ``filename`` 与 UTF-8 ``filename*``，中文名由前端 saveBlob 落名。
"""
from __future__ import annotations

import io
from contextlib import contextmanager
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from ...database import get_db
from ...upload import UploadTooLargeError, read_upload_limited
from ...utils import repair_upload_mojibake
from . import importer, reconciliation, service
from .reconciliation_schemas import CheckRequest, MappingPutRequest
from .schemas import (
    ContractListOut,
    ContractOut,
    LeaseContractCreate,
    LeaseContractPatch,
    LeasePaymentsReplace,
)

router = APIRouter(tags=["财务模块·租赁核算"])

_XLSX_MEDIA = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@contextmanager
def _translated():
    """把服务层中文异常映射成 HTTP：404 / 409 / 503，其余业务错误 400。"""
    try:
        yield
    except service.LeaseNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except service.LeaseConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except service.LeaseEngineUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


async def _read_payment_file(file: UploadFile) -> tuple[bytes, str]:
    filename = (file.filename or "").rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    filename = repair_upload_mojibake(filename) or "payments.xlsx"
    try:
        content = await read_upload_limited(file, importer.MAX_UPLOAD_BYTES)
    except UploadTooLargeError as exc:
        limit_mb = importer.MAX_UPLOAD_BYTES // 1024 // 1024
        raise HTTPException(status_code=400, detail=f"文件超过 {limit_mb}MB 上限") from exc
    if not content:
        raise HTTPException(status_code=400, detail="上传文件为空")
    return content, filename


def _download(file) -> StreamingResponse:
    ascii_name = file.filename_ascii or "".join(
        char for char in file.filename if 32 <= ord(char) <= 126 and char not in '"\\'
    )
    ascii_name = ascii_name or "lease.xlsx"
    return StreamingResponse(
        io.BytesIO(file.content),
        media_type=_XLSX_MEDIA,
        headers={
            "Content-Disposition": f"attachment; filename=\"{ascii_name}\"; "
                                   f"filename*=UTF-8''{quote(file.filename)}"
        },
    )


# ---------- 合同 ----------


@router.post("/contracts", status_code=201, summary="新建租赁合同（draft）")
def create_contract(payload: LeaseContractCreate, db: Session = Depends(get_db)):
    with _translated():
        return service.create_contract(db, payload)


@router.get("/contracts", response_model=ContractListOut, summary="合同列表（分页/状态/关键词）")
def list_contracts(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    status: str | None = Query(default=None),
    keyword: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    with _translated():
        return service.list_contracts(
            db, status=status or None, keyword=keyword or None, page=page, page_size=page_size
        )


@router.get("/contracts/{contract_id}", response_model=ContractOut, summary="合同详情与计数")
def get_contract(contract_id: int, db: Session = Depends(get_db)):
    with _translated():
        return service.get_contract(db, contract_id)


@router.patch("/contracts/{contract_id}", response_model=ContractOut, summary="维护合同（归档后只读）")
def update_contract(contract_id: int, payload: LeaseContractPatch, db: Session = Depends(get_db)):
    with _translated():
        return service.update_contract(db, contract_id, payload)


@router.delete("/contracts/{contract_id}", summary="删除合同（有测算历史 409）")
def delete_contract(contract_id: int, db: Session = Depends(get_db)):
    with _translated():
        return service.delete_contract(db, contract_id)


# ---------- 付款计划 ----------


@router.get("/contracts/{contract_id}/payments", summary="付款计划列表（按期数升序）")
def list_payments(contract_id: int, db: Session = Depends(get_db)):
    with _translated():
        return service.list_payments(db, contract_id)


@router.put("/contracts/{contract_id}/payments", summary="整表替换付款计划（仅草稿）")
def replace_payments(contract_id: int, payload: LeasePaymentsReplace, db: Session = Depends(get_db)):
    with _translated():
        return service.replace_payments(db, contract_id, payload)


@router.post(
    "/contracts/{contract_id}/payments/import/preview",
    summary="预览付款计划 xlsx（只解析，不落库）",
)
async def preview_payment_import(
    contract_id: int, file: UploadFile = File(...), db: Session = Depends(get_db)
):
    content, filename = await _read_payment_file(file)
    with _translated():
        return service.preview_payment_import(db, contract_id, content, filename)


@router.post(
    "/contracts/{contract_id}/payments/import/apply",
    summary="确认导入付款计划 xlsx（无错误才整表替换）",
)
async def apply_payment_import(
    contract_id: int, file: UploadFile = File(...), db: Session = Depends(get_db)
):
    content, filename = await _read_payment_file(file)
    with _translated():
        return service.apply_payment_import(db, contract_id, content, filename)


# ---------- 测算 ----------


@router.post("/contracts/{contract_id}/calculations", status_code=201, summary="创建/幂等返回测算")
def create_calculation(contract_id: int, db: Session = Depends(get_db)):
    with _translated():
        return service.create_calculation(db, contract_id)


@router.get("/contracts/{contract_id}/calculations", summary="测算列表（最新在前）")
def list_calculations(contract_id: int, db: Session = Depends(get_db)):
    with _translated():
        return service.list_calculations(db, contract_id)


@router.get("/calculations/{calculation_id}", summary="测算详情（含过期标记）")
def get_calculation(calculation_id: int, db: Session = Depends(get_db)):
    with _translated():
        return service.get_calculation(db, calculation_id)


@router.get("/calculations/{calculation_id}/assessment", summary="付款期级评估行")
def get_assessment(calculation_id: int, db: Session = Depends(get_db)):
    with _translated():
        return service.get_assessment(db, calculation_id)


@router.get("/calculations/{calculation_id}/periods", summary="月份级期间行")
def get_periods(calculation_id: int, db: Session = Depends(get_db)):
    with _translated():
        return service.get_periods(db, calculation_id)


@router.get("/calculations/{calculation_id}/checks", summary="统一校验结果与计数")
def get_checks(calculation_id: int, db: Session = Depends(get_db)):
    with _translated():
        return service.get_checks(db, calculation_id)


@router.post("/calculations/{calculation_id}/confirm", summary="确认测算（不可改写快照）")
def confirm_calculation(calculation_id: int, db: Session = Depends(get_db)):
    with _translated():
        return service.confirm_calculation(db, calculation_id)


# ---------- 导出 / 摘要 ----------


@router.get("/calculations/{calculation_id}/export.xlsx", summary="导出五页签 xlsx 底稿")
def export_calculation_xlsx(
    calculation_id: int,
    allow_stale: bool = Query(default=False, description="历史快照显式允许过期导出"),
    db: Session = Depends(get_db),
):
    with _translated():
        file = service.export_calculation_xlsx(db, calculation_id, allow_stale=allow_stale)
    return _download(file)


@router.get("/summary", summary="租赁核算摘要（按合同或全部）")
def summary(contract_id: int | None = Query(default=None, ge=1), db: Session = Depends(get_db)):
    with _translated():
        return service.lease_summary(db, contract_id=contract_id)


# ---------- BIP 只读对账（映射只写本模块表，余额只读） ----------


@router.get("/reconciliation/mappings", summary="BIP 对账科目映射列表（本模块映射表）")
def list_reconciliation_mappings(
    lessee: str | None = Query(default=None),
    ledger: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    with _translated():
        return reconciliation.list_mappings(db, lessee=lessee or None, ledger=ledger or None)


@router.put("/reconciliation/mappings", summary="整批替换 BIP 对账科目映射（绝不写 BIP）")
def put_reconciliation_mappings(payload: MappingPutRequest, db: Session = Depends(get_db)):
    with _translated():
        return reconciliation.replace_mappings(db, payload)


@router.get("/reconciliation/context", summary="BIP 对账下拉上下文（合同承租人 + BIP 账簿/期间）")
def reconciliation_context(db: Session = Depends(get_db)):
    with _translated():
        return reconciliation.get_context(db)


@router.post("/reconciliation/check", summary="BIP 只读对账（承租人/账簿/期间五科目勾稽）")
def reconciliation_check(payload: CheckRequest, db: Session = Depends(get_db)):
    with _translated():
        return reconciliation.run_reconciliation(db, payload)
