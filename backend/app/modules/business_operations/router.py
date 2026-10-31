# -*- coding: utf-8 -*-
"""营运管理接口：/api/business/operations。"""
from __future__ import annotations

import io
from datetime import date, datetime
from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ...database import get_db
from . import service

router = APIRouter(tags=["经营模块·营运管理"])

ContractStatus = Literal["active", "terminated"]
WorkOrderType = Literal["repair", "complaint", "inspection"]
WorkOrderStatus = Literal["open", "processing", "done", "closed"]
Priority = Literal["low", "medium", "high", "urgent"]


class ContractIn(BaseModel):
    contract_no: str = Field(min_length=1, max_length=50)
    tenant: str = Field(min_length=1, max_length=200)
    unit_no: str = Field(default="", max_length=50)
    area: float = Field(default=0, ge=0, le=10_000_000)
    start_date: date
    end_date: date
    rent_monthly: float = Field(default=0, ge=0, le=1_000_000_000)
    property_fee_monthly: float = Field(default=0, ge=0, le=1_000_000_000)
    deposit: float = Field(default=0, ge=0, le=1_000_000_000)
    status: ContractStatus = "active"
    signed_at: date | None = None
    note: str = Field(default="", max_length=2000)


class ContractUpdate(BaseModel):
    contract_no: str | None = Field(default=None, min_length=1, max_length=50)
    tenant: str | None = Field(default=None, min_length=1, max_length=200)
    unit_no: str | None = Field(default=None, max_length=50)
    area: float | None = Field(default=None, ge=0, le=10_000_000)
    start_date: date | None = None
    end_date: date | None = None
    rent_monthly: float | None = Field(default=None, ge=0, le=1_000_000_000)
    property_fee_monthly: float | None = Field(default=None, ge=0, le=1_000_000_000)
    deposit: float | None = Field(default=None, ge=0, le=1_000_000_000)
    status: ContractStatus | None = None
    signed_at: date | None = None
    note: str | None = Field(default=None, max_length=2000)


class BillGenerateIn(BaseModel):
    period: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
    other_amount: float = Field(default=0, ge=0, le=1_000_000_000)
    due_date: date | None = None


class PaymentIn(BaseModel):
    amount: float = Field(gt=0, le=1_000_000_000)
    paid_at: date | None = None


class WorkOrderIn(BaseModel):
    type: WorkOrderType = "repair"
    location: str = Field(default="", max_length=100)
    tenant: str = Field(default="", max_length=200)
    priority: Priority = "medium"
    status: WorkOrderStatus = "open"
    description: str = Field(min_length=1, max_length=4000)
    assignee: str = Field(default="", max_length=50)
    due_at: datetime | None = None
    parent_id: int | None = Field(default=None, ge=1)
    equipment_id: int | None = Field(default=None, ge=1)


class WorkOrderUpdate(BaseModel):
    type: WorkOrderType | None = None
    location: str | None = Field(default=None, max_length=100)
    tenant: str | None = Field(default=None, max_length=200)
    priority: Priority | None = None
    status: WorkOrderStatus | None = None
    description: str | None = Field(default=None, min_length=1, max_length=4000)
    assignee: str | None = Field(default=None, max_length=50)
    due_at: datetime | None = None
    parent_id: int | None = Field(default=None, ge=1)
    equipment_id: int | None = Field(default=None, ge=1)


class WorkOrderFollowupIn(BaseModel):
    handled_at: datetime | None = None
    content: str = Field(min_length=1, max_length=4000)
    result: str = Field(default="", max_length=2000)
    satisfaction: int | None = Field(default=None, ge=1, le=5)


class WorkOrderFollowupUpdate(BaseModel):
    handled_at: datetime | None = None
    content: str | None = Field(default=None, min_length=1, max_length=4000)
    result: str | None = Field(default=None, max_length=2000)
    satisfaction: int | None = Field(default=None, ge=1, le=5)


def _download(data: bytes, filename: str) -> StreamingResponse:
    ascii_name = "".join(c for c in filename if 32 <= ord(c) <= 126 and c not in '"\\') or "export.xlsx"
    return StreamingResponse(
        io.BytesIO(data),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename=\"{ascii_name}\"; "
                                        f"filename*=UTF-8''{quote(filename)}"},
    )


# ---------------- 合同 ----------------

@router.get("/summary", summary="营运看板（合同/应收/实收/收缴率/欠费/工单）")
def summary(period: str = "", db: Session = Depends(get_db)):
    try:
        return service.ops_summary(db, period=period)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/contracts/expiring", summary="N 天内到期合同（默认 30 天）")
def expiring(days: int = Query(30, ge=1, le=365), db: Session = Depends(get_db)):
    return service.contracts_expiring(db, days)


@router.get("/contracts", summary="合同列表（分页）")
def contracts(page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=200),
              status: str = "", tenant: str = "", keyword: str = "",
              db: Session = Depends(get_db)):
    return service.contract_list(db, status=status, tenant=tenant, keyword=keyword,
                                 page=page, page_size=page_size)


@router.post("/contracts", status_code=201, summary="新建合同")
def create_contract(payload: ContractIn, db: Session = Depends(get_db)):
    try:
        return service.contract_save(db, payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.put("/contracts/{contract_id}", summary="更新合同")
def update_contract(contract_id: int, payload: ContractUpdate, db: Session = Depends(get_db)):
    data = payload.model_dump(exclude_unset=True, exclude_none=True)
    try:
        return service.contract_save(db, data, contract_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.delete("/contracts/{contract_id}", summary="删除合同（连同未收款账单；已收款需先处理）")
def delete_contract(contract_id: int, db: Session = Depends(get_db)):
    try:
        service.contract_delete(db, contract_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"ok": True}


@router.post("/contracts/{contract_id}/bills", status_code=201, summary="按期间生成账单")
def create_bill(contract_id: int, payload: BillGenerateIn, db: Session = Depends(get_db)):
    try:
        return service.generate_bill(db, contract_id, payload.period,
                                     payload.other_amount, payload.due_date)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


# ---------------- 账单 ----------------

@router.get("/bills", summary="账单列表（按期间/状态/合同筛选）")
def bills(page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=200),
          period: str = "", status: str = "", contract_id: int | None = None,
          db: Session = Depends(get_db)):
    return service.bill_list(db, period=period, status=status,
                             contract_id=contract_id, page=page, page_size=page_size)


@router.get("/bills/aging", summary="欠费账龄（固定截止日四桶与未清明细）")
def bill_aging(as_of: str = "", db: Session = Depends(get_db)):
    try:
        return service.bill_aging(db, as_of=as_of)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/bills/{bill_id}/payments", summary="登记收款（累计后自动改状态）")
def pay(bill_id: int, payload: PaymentIn, db: Session = Depends(get_db)):
    try:
        return service.pay_bill(db, bill_id, payload.amount, payload.paid_at)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# ---------------- 工单 ----------------

@router.get("/work-orders", summary="工单列表（按状态/类型/描述筛选）")
def work_orders(page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=200),
                status: str = "", type: str = "", keyword: str = "",
                db: Session = Depends(get_db)):
    return service.work_order_list(db, status=status, type_=type, keyword=keyword,
                                   page=page, page_size=page_size)


@router.post("/work-orders", status_code=201, summary="新建工单")
def create_work_order(payload: WorkOrderIn, db: Session = Depends(get_db)):
    try:
        return service.work_order_save(db, payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.put("/work-orders/{order_id}", summary="更新工单（含状态流转）")
def update_work_order(order_id: int, payload: WorkOrderUpdate, db: Session = Depends(get_db)):
    data = payload.model_dump(exclude_unset=True)
    try:
        return service.work_order_save(db, data, order_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/work-orders/{order_id}", summary="删除工单")
def delete_work_order(order_id: int, db: Session = Depends(get_db)):
    try:
        service.work_order_delete(db, order_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"ok": True}


@router.get("/work-orders/{order_id}/followups", summary="工单跟进记录")
def work_order_followups(order_id: int, db: Session = Depends(get_db)):
    try:
        return service.work_order_followup_list(db, order_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/work-orders/{order_id}/followups", status_code=201, summary="新增工单跟进")
def create_work_order_followup(order_id: int, payload: WorkOrderFollowupIn,
                               db: Session = Depends(get_db)):
    try:
        return service.work_order_followup_save(db, order_id, payload.model_dump())
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.put("/work-orders/{order_id}/followups/{followup_id}", summary="更新工单跟进")
def update_work_order_followup(order_id: int, followup_id: int,
                               payload: WorkOrderFollowupUpdate,
                               db: Session = Depends(get_db)):
    data = payload.model_dump(exclude_unset=True)
    try:
        return service.work_order_followup_save(db, order_id, data, followup_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/work-orders/{order_id}/followups/{followup_id}", summary="删除工单跟进")
def delete_work_order_followup(order_id: int, followup_id: int,
                               db: Session = Depends(get_db)):
    try:
        service.work_order_followup_delete(db, order_id, followup_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"ok": True}


# ---------------- 导出 ----------------

@router.get("/export/contracts.xlsx", summary="导出合同台账")
def export_contracts(db: Session = Depends(get_db)):
    return _download(service.ops_contracts_export(db), "营运合同台账.xlsx")


@router.get("/export/bills.xlsx", summary="导出账单明细")
def export_bills(period: str = "", db: Session = Depends(get_db)):
    return _download(service.ops_bills_export(db, period), "营运账单明细.xlsx")


@router.get("/export/work-orders.xlsx", summary="导出工单（与列表同筛选）")
def export_work_orders(status: str = "", type: str = "", keyword: str = "",
                       db: Session = Depends(get_db)):
    return _download(
        service.ops_work_orders_export(db, status=status, type_=type, keyword=keyword),
        "营运工单台账.xlsx",
    )
