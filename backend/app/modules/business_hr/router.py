# -*- coding: utf-8 -*-
"""行政人事接口：/api/business/hr。"""
from __future__ import annotations

import io
from datetime import date
from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ...database import get_db
from . import service

router = APIRouter(tags=["职能模块·行政人事"])

EmployeeStatus = Literal["active", "probation", "left"]


class EmployeeIn(BaseModel):
    emp_no: str = Field(min_length=1, max_length=50)
    name: str = Field(min_length=1, max_length=100)
    department: str = Field(default="", max_length=100)
    position: str = Field(default="", max_length=100)
    hire_date: date | None = None
    probation_end: date | None = None
    contract_end: date | None = None
    status: EmployeeStatus = "active"
    phone: str = Field(default="", max_length=50)
    base_salary: float = Field(default=0, ge=0, le=100_000_000)
    social_base: float = Field(default=0, ge=0, le=100_000_000)
    special_additional: float = Field(default=0, ge=0, le=100_000)
    note: str = Field(default="", max_length=2000)


class EmployeeUpdate(BaseModel):
    emp_no: str | None = Field(default=None, min_length=1, max_length=50)
    name: str | None = Field(default=None, min_length=1, max_length=100)
    department: str | None = Field(default=None, max_length=100)
    position: str | None = Field(default=None, max_length=100)
    hire_date: date | None = None
    probation_end: date | None = None
    contract_end: date | None = None
    status: EmployeeStatus | None = None
    phone: str | None = Field(default=None, max_length=50)
    base_salary: float | None = Field(default=None, ge=0, le=100_000_000)
    social_base: float | None = Field(default=None, ge=0, le=100_000_000)
    special_additional: float | None = Field(default=None, ge=0, le=100_000)
    note: str | None = Field(default=None, max_length=2000)


class AttendanceIn(BaseModel):
    work_days: float = Field(default=21.75, ge=0, le=31)
    actual_days: float = Field(default=21.75, ge=0, le=31)
    leave_days: float = Field(default=0, ge=0, le=31)
    overtime_hours: float = Field(default=0, ge=0, le=500)
    late_count: int = Field(default=0, ge=0, le=31)
    note: str = Field(default="", max_length=2000)


class PayrollGenerateIn(BaseModel):
    month: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
    allowance: float = Field(default=0, ge=0, le=100_000_000)
    social_rate: float = Field(default=service.DEFAULT_SOCIAL_RATE, ge=0, le=1)
    housing_rate: float = Field(default=service.DEFAULT_HOUSING_RATE, ge=0, le=1)


def _download(data: bytes, filename: str) -> StreamingResponse:
    ascii_name = "".join(c for c in filename if 32 <= ord(c) <= 126 and c not in '"\\') or "export.xlsx"
    return StreamingResponse(
        io.BytesIO(data),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename=\"{ascii_name}\"; "
                                        f"filename*=UTF-8''{quote(filename)}"},
    )


@router.get("/summary", summary="人事看板（在职/试用/到期/当月人力成本）")
def summary(month: str = "", db: Session = Depends(get_db)):
    try:
        return service.hr_summary(db, month)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# ---------------- 员工 ----------------

@router.get("/employees/expiring", summary="N 天内试用/合同到期员工")
def expiring(days: int = Query(30, ge=1, le=365), db: Session = Depends(get_db)):
    return service.employees_expiring(db, days)


@router.get("/employees", summary="员工花名册（分页）")
def employees(page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=200),
              department: str = "", status: str = "", keyword: str = "",
              db: Session = Depends(get_db)):
    return service.employee_list(db, department=department, status=status,
                                 keyword=keyword, page=page, page_size=page_size)


@router.post("/employees", status_code=201, summary="新建员工")
def create_employee(payload: EmployeeIn, db: Session = Depends(get_db)):
    try:
        return service.employee_save(db, payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.put("/employees/{employee_id}", summary="更新员工")
def update_employee(employee_id: int, payload: EmployeeUpdate, db: Session = Depends(get_db)):
    data = payload.model_dump(exclude_unset=True, exclude_none=True)
    for field in ("probation_end", "contract_end"):
        if field in payload.model_fields_set and getattr(payload, field) is None:
            data[field] = None
    try:
        return service.employee_save(db, data, employee_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.delete("/employees/{employee_id}", summary="删除员工（连同考勤/薪资）")
def delete_employee(employee_id: int, db: Session = Depends(get_db)):
    try:
        service.employee_delete(db, employee_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"ok": True}


# ---------------- 考勤 ----------------

@router.get("/attendance", summary="月度考勤列表")
def attendance(month: str = Query(...), db: Session = Depends(get_db)):
    try:
        return service.attendance_list(db, month)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.put("/attendance/{emp_id}/{month}", summary="录入/更新月度考勤")
def upsert_attendance(emp_id: int, month: str, payload: AttendanceIn,
                      db: Session = Depends(get_db)):
    try:
        return service.attendance_upsert(db, emp_id, month, payload.model_dump())
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# ---------------- 薪资 ----------------

@router.post("/payroll/generate", status_code=201, summary="按月份生成薪资（累计预扣个税）")
def generate_payroll(payload: PayrollGenerateIn, db: Session = Depends(get_db)):
    try:
        return service.payroll_generate(db, payload.month, payload.allowance,
                                        payload.social_rate, payload.housing_rate)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/payroll", summary="月度薪资列表")
def payroll(month: str = Query(...), db: Session = Depends(get_db)):
    try:
        return service.payroll_list(db, month)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/payroll/{payroll_id}/confirm", summary="确认薪资")
def confirm_payroll(payroll_id: int, db: Session = Depends(get_db)):
    try:
        return service.payroll_confirm(db, payroll_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.delete("/payroll/{payroll_id}", summary="删除薪资（仅草稿）")
def delete_payroll(payroll_id: int, db: Session = Depends(get_db)):
    try:
        service.payroll_delete(db, payroll_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"ok": True}


# ---------------- 导出 ----------------

@router.get("/export/employees.xlsx", summary="导出员工花名册")
def export_employees(db: Session = Depends(get_db)):
    return _download(service.employees_export(db), "员工花名册.xlsx")


@router.get("/export/payroll.xlsx", summary="导出月度薪资表")
def export_payroll(month: str = Query(...), db: Session = Depends(get_db)):
    try:
        data = service.payroll_export(db, month)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _download(data, f"薪资表{month}.xlsx")
