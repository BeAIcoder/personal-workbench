# -*- coding: utf-8 -*-
"""行政人事服务：员工花名册、考勤、薪资核算（累计预扣法个税）、看板与导出。

薪资口径（可在文档/界面调整）：
- 月计薪天数 21.75，日工时 8 小时，加班 1.5 倍；
- 事假按日工资扣减（leave_days）；
- 个人社保默认 10.5%（养老 8% + 医疗 2% + 失业 0.5%），公积金默认 7%，
  基数取员工 social_base（为空时取 base_salary）；
- 个税按累计预扣法：累计应纳税所得额 = Σ(应发 - 扣款 - 社保 - 公积金) - 5000×月数，
  再套综合所得年度预扣率表，减去已预扣。最终以财务个税模块/税务申报为准。
"""
from __future__ import annotations

import io
import re
from datetime import date, timedelta

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ...database import SessionLocal
from ...finance_iit_common import BRACKETS, MONTHLY_DEDUCTION, calc_cumulative_withholding
from ...utils_money import money
from ...utils_xlsx import safe_cell
from .models import HrAttendance, HrEmployee, HrPayroll

PERIOD_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
EMPLOYEE_STATUSES = ("active", "probation", "left")
STATUS_LABELS = {"active": "在职", "probation": "试用", "left": "离职"}
PAYROLL_STATUS_LABELS = {"draft": "草稿", "confirmed": "已确认"}

WORK_DAYS = 21.75
HOURS_PER_DAY = 8.0
OVERTIME_MULTIPLIER = 1.5
DEFAULT_SOCIAL_RATE = 0.105
DEFAULT_HOUSING_RATE = 0.07


def _as_date(value, field: str = "日期") -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise ValueError(f"{field}格式非法，应为 YYYY-MM-DD") from exc


def effective_social_base(e: HrEmployee) -> float | None:
    """社保基数口径：显式填写（含 0 = 不缴社保）按填写值；未填返回 None（出薪时回退基本工资）。

    兼容改造前的数据：`social_base` 列是 NOT NULL 且默认 0，旧记录的 0 只代表"没填过"；
    旧记录的非 0 值一律视为显式填写（当年就是用户手填的）。显式 0 由 social_base_set=1 标记。
    """
    value = money(e.social_base)
    if value > 0:
        return value
    return 0.0 if int(e.social_base_set or 0) == 1 else None


# ---------------- 员工 ----------------

def employee_to_dict(e: HrEmployee) -> dict:
    today = date.today()

    def days(d):
        return (d - today).days if d else None

    probation_days = days(e.probation_end)
    contract_days = days(e.contract_end)
    expiring_types = []
    if e.status != "left":
        if probation_days is not None and 0 <= probation_days <= 30:
            expiring_types.append("试用期")
        if contract_days is not None and 0 <= contract_days <= 30:
            expiring_types.append("合同")
    return {
        "id": e.id, "emp_no": e.emp_no, "name": e.name,
        "department": e.department or "", "position": e.position or "",
        "hire_date": e.hire_date.isoformat() if e.hire_date else None,
        "probation_end": e.probation_end.isoformat() if e.probation_end else None,
        "contract_end": e.contract_end.isoformat() if e.contract_end else None,
        "probation_days": probation_days,
        "contract_days": contract_days,
        "expiring_types": expiring_types,
        "expiring": bool(expiring_types),
        "status": e.status,
        "status_label": STATUS_LABELS.get(e.status, e.status),
        "phone": e.phone or "",
        "base_salary": money(e.base_salary),
        "social_base": effective_social_base(e),
        "special_additional": money(e.special_additional),
        "note": e.note or "",
        "created_at": str(e.created_at),
    }


def employee_list(db: Session, department: str = "", status: str = "", keyword: str = "",
                  page: int = 1, page_size: int = 20) -> dict:
    q = db.query(HrEmployee)
    if department:
        q = q.filter(HrEmployee.department == department)
    if status:
        q = q.filter(HrEmployee.status == status)
    if keyword:
        q = q.filter(HrEmployee.name.contains(keyword, autoescape=True))
    total = q.count()
    rows = (q.order_by(HrEmployee.emp_no)
            .offset((page - 1) * page_size).limit(page_size).all())
    return {"total": total, "items": [employee_to_dict(e) for e in rows],
            "page": page, "page_size": page_size}


def employee_save(db: Session, data: dict, employee_id: int | None = None) -> dict:
    e = db.get(HrEmployee, employee_id) if employee_id else HrEmployee()
    if e is None:
        raise LookupError("员工不存在")
    for field in ("emp_no", "name", "department", "position", "phone", "note"):
        if field in data and data[field] is not None:
            setattr(e, field, str(data[field]).strip())
    for field in ("base_salary", "special_additional"):
        if field in data and data[field] is not None:
            if float(data[field]) < 0:
                raise ValueError(f"{field} 不能为负数")
            setattr(e, field, money(data[field]))
    if "social_base" in data:
        value = data["social_base"]
        if value is None:
            # 留空 = 按基本工资（0 是显式"不缴社保"，必须由 social_base_set 区分）
            e.social_base = 0.0
            e.social_base_set = 0
        else:
            if float(value) < 0:
                raise ValueError("social_base 不能为负数")
            e.social_base = money(value)
            e.social_base_set = 1
    for field in ("hire_date", "probation_end", "contract_end"):
        if field in data:
            setattr(e, field, _as_date(data[field], field))
    if "status" in data and data["status"] is not None:
        if data["status"] not in EMPLOYEE_STATUSES:
            raise ValueError(f"未知员工状态：{data['status']}")
        e.status = data["status"]
    if not e.emp_no or not e.name:
        raise ValueError("工号与姓名不能为空")
    if e.hire_date is None:
        e.hire_date = date.today()
    if employee_id is None:
        db.add(e)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ValueError("工号已存在") from exc
    db.refresh(e)
    return employee_to_dict(e)


def employee_delete(db: Session, employee_id: int) -> None:
    e = db.get(HrEmployee, employee_id)
    if e is None:
        raise LookupError("员工不存在")
    confirmed = (db.query(HrPayroll)
                 .filter(HrPayroll.emp_id == employee_id, HrPayroll.status == "confirmed")
                 .count())
    if confirmed:
        # 已确认薪资是已发放凭据，与 payroll_delete 同口径：不能经员工删除绕过
        raise ValueError(
            f"该员工有 {confirmed} 条已确认薪资，删除会丢失已发放凭据，已拒绝；"
            "已确认薪资不支持删除，请把员工状态改为「离职」保留历史"
        )
    db.query(HrAttendance).filter(HrAttendance.emp_id == employee_id).delete()
    db.query(HrPayroll).filter(HrPayroll.emp_id == employee_id).delete()
    db.delete(e)
    db.commit()


def employees_expiring(db: Session, days: int = 30) -> list[dict]:
    today = date.today()
    limit = today + timedelta(days=days)
    rows = (db.query(HrEmployee)
            .filter(HrEmployee.status != "left")
            .filter(
                ((HrEmployee.probation_end.isnot(None)) & (HrEmployee.probation_end >= today)
                 & (HrEmployee.probation_end <= limit))
                | ((HrEmployee.contract_end.isnot(None)) & (HrEmployee.contract_end >= today)
                   & (HrEmployee.contract_end <= limit))
            )
            .order_by(HrEmployee.contract_end.asc()).all())
    return [employee_to_dict(e) for e in rows]


# ---------------- 考勤 ----------------

def attendance_to_dict(a: HrAttendance, e: HrEmployee | None = None) -> dict:
    return {
        "id": a.id, "emp_id": a.emp_id,
        "emp_no": e.emp_no if e else "", "name": e.name if e else "",
        "department": e.department if e else "",
        "month": a.month,
        "work_days": money(a.work_days), "actual_days": money(a.actual_days),
        "leave_days": money(a.leave_days), "overtime_hours": money(a.overtime_hours),
        "late_count": int(a.late_count or 0), "note": a.note or "",
    }


def attendance_upsert(db: Session, emp_id: int, month: str, data: dict) -> dict:
    if not PERIOD_RE.match(str(month or "")):
        raise ValueError("月份格式非法，应为 YYYY-MM")
    emp = db.get(HrEmployee, emp_id)
    if emp is None:
        raise LookupError("员工不存在")
    row = (db.query(HrAttendance)
           .filter(HrAttendance.emp_id == emp_id, HrAttendance.month == month).first())
    if row is None:
        row = HrAttendance(emp_id=emp_id, month=month)
        db.add(row)
    for field in ("work_days", "actual_days", "leave_days", "overtime_hours"):
        if field in data and data[field] is not None:
            if float(data[field]) < 0:
                raise ValueError(f"{field} 不能为负数")
            setattr(row, field, money(data[field]))
    if "late_count" in data and data["late_count"] is not None:
        if int(data["late_count"]) < 0:
            raise ValueError("迟到次数不能为负数")
        row.late_count = int(data["late_count"])
    if "note" in data and data["note"] is not None:
        row.note = str(data["note"]).strip()[:2000]
    db.commit()
    db.refresh(row)
    return attendance_to_dict(row, emp)


def attendance_list(db: Session, month: str) -> list[dict]:
    if not PERIOD_RE.match(str(month or "")):
        raise ValueError("月份格式非法，应为 YYYY-MM")
    rows = (db.query(HrAttendance, HrEmployee)
            .join(HrEmployee, HrEmployee.id == HrAttendance.emp_id)
            .filter(HrAttendance.month == month)
            .order_by(HrEmployee.emp_no).all())
    return [attendance_to_dict(a, e) for a, e in rows]


# ---------------- 薪资 ----------------

def payroll_to_dict(p: HrPayroll, e: HrEmployee | None = None) -> dict:
    return {
        "id": p.id, "emp_id": p.emp_id,
        "emp_no": e.emp_no if e else "", "name": e.name if e else "",
        "department": e.department if e else "",
        "month": p.month,
        "base_salary": money(p.base_salary), "allowance": money(p.allowance),
        "overtime_pay": money(p.overtime_pay), "deduction": money(p.deduction),
        "social_insurance": money(p.social_insurance),
        "housing_fund": money(p.housing_fund),
        "special_additional": money(p.special_additional),
        "tax": money(p.tax),
        "gross_pay": money((p.base_salary or 0) + (p.allowance or 0) + (p.overtime_pay or 0)),
        "net_pay": money(p.net_pay),
        "status": p.status,
        "status_label": PAYROLL_STATUS_LABELS.get(p.status, p.status),
        "note": p.note or "",
    }


def payroll_generate(db: Session, month: str, allowance: float = 0.0,
                     social_rate: float = DEFAULT_SOCIAL_RATE,
                     housing_rate: float = DEFAULT_HOUSING_RATE) -> dict:
    if not PERIOD_RE.match(str(month or "")):
        raise ValueError("月份格式非法，应为 YYYY-MM")
    if allowance < 0:
        raise ValueError("补贴不能为负数")
    if not (0 <= social_rate <= 1) or not (0 <= housing_rate <= 1):
        raise ValueError("社保/公积金比例须在 0-1 之间")
    exists = db.query(HrPayroll).filter(HrPayroll.month == month).count()
    if exists:
        raise ValueError(f"{month} 薪资已生成（{exists} 条），请先删除草稿或更换月份")

    employees = (db.query(HrEmployee)
                 .filter(HrEmployee.status.in_(("active", "probation")))
                 .order_by(HrEmployee.emp_no).all())
    if not employees:
        raise ValueError("没有在职/试用员工，无法生成薪资")

    allowance = money(allowance)
    target_year = int(month[:4])
    target_month = int(month[5:])
    created = []
    warnings = []
    missing_detail: list[str] = []
    for emp in employees:
        if emp.hire_date and (emp.hire_date.year, emp.hire_date.month) > (target_year, target_month):
            # P1 修复：入职晚于出薪月份时不再用 max() 兜底成「已入职 1 个月」计税，
            # 与 finance_iit 的拒绝口径对齐
            raise ValueError(
                f"员工 {emp.emp_no} {emp.name} 入职月份"
                f"（{emp.hire_date.year}-{emp.hire_date.month:02d}）晚于出薪月份"
                f"（{target_year}-{target_month:02d}），不能为其出薪；"
                "请修正入职日期或更换出薪月份"
            )
        att = (db.query(HrAttendance)
               .filter(HrAttendance.emp_id == emp.id, HrAttendance.month == month).first())
        if att is None:
            warnings.append(
                f"{month} 缺少考勤记录：{emp.emp_no} {emp.name}；"
                "已按无请假、无加班生成薪资，请补录考勤后复核"
            )
        leave_days = float(att.leave_days or 0) if att else 0.0
        overtime_hours = float(att.overtime_hours or 0) if att else 0.0

        base = money(emp.base_salary)
        daily = base / WORK_DAYS if base else 0.0
        overtime_pay = money(daily / HOURS_PER_DAY * OVERTIME_MULTIPLIER * overtime_hours)
        deduction = money(daily * leave_days)
        # 0 是合法社保基数（不缴社保）；只有「未填」才回退基本工资
        social_base = effective_social_base(emp)
        if social_base is None:
            social_base = base
        social = money(social_base * social_rate)
        housing = money(social_base * housing_rate)
        gross = money(base + allowance + overtime_pay)
        current_base = money(gross - deduction - social - housing)

        hire_start_month = (
            emp.hire_date.month
            if emp.hire_date and emp.hire_date.year == target_year else 1
        )
        priors = (db.query(HrPayroll)
                  .filter(HrPayroll.emp_id == emp.id)
                  .filter(HrPayroll.month.like(f"{month[:4]}-%"))
                  .filter(HrPayroll.month < month).all())
        missing_months = [
            f"{target_year}-{m:02d}"
            for m in range(hire_start_month, target_month)
            if f"{target_year}-{m:02d}" not in {p.month for p in priors}
        ]
        if missing_months:
            # 累计预扣的减除费用按「任职月份数」计算；缺月不补会让累计收入偏低、个税被低估
            missing_detail.append(f"{emp.emp_no}（{'、'.join(missing_months)}）")
        prior_base = sum(
            float(p.base_salary or 0) + float(p.allowance or 0) + float(p.overtime_pay or 0)
            - float(p.deduction or 0) - float(p.social_insurance or 0)
            - float(p.housing_fund or 0) - float(p.special_additional or 0)
            for p in priors
        )
        prior_tax = sum(float(p.tax or 0) for p in priors)
        months_count = max(len(priors) + 1, target_month - hire_start_month + 1)
        special_monthly = money(emp.special_additional or 0)
        current_base = money(current_base - special_monthly)
        withholding = calc_cumulative_withholding(
            prior_base + current_base, prior_tax, months_count,
        )
        tax = withholding["monthly_withholding"]
        net = money(gross - deduction - social - housing - tax)

        row = HrPayroll(
            emp_id=emp.id, month=month, base_salary=base, allowance=allowance,
            overtime_pay=overtime_pay, deduction=deduction,
            social_insurance=social, housing_fund=housing,
            special_additional=special_monthly, tax=tax,
            net_pay=net, status="draft",
        )
        db.add(row)
        created.append((row, emp))

    if missing_detail:
        # 聚合展示：一次列清全部缺月员工（最多 5 人），避免逐人弹窗刷屏
        shown = "；".join(missing_detail[:5])
        more = f"；等 {len(missing_detail)} 人" if len(missing_detail) > 5 else ""
        warnings.append(
            f"历史月份薪资未生成（{len(missing_detail)} 人）：{shown}{more}；"
            "累计预扣按任职月份减除，缺月会使累计收入偏低、个税被低估，请补生成或核对累计数"
        )
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        # 并发：两个请求都通过了上面的 count 查重，后提交者撞 (emp_id, month) 唯一约束
        raise ValueError(
            "该月薪资已生成（并发冲突），请刷新后重试；如需重算请先删除草稿"
        ) from exc
    items = []
    for row, emp in created:
        db.refresh(row)
        items.append(payroll_to_dict(row, emp))
    return {
        "month": month,
        "count": len(items),
        "total_gross": money(sum(x["gross_pay"] for x in items)),
        "total_tax": money(sum(x["tax"] for x in items)),
        "total_net": money(sum(x["net_pay"] for x in items)),
        "items": items,
        "warnings": warnings,
    }


def payroll_list(db: Session, month: str) -> list[dict]:
    if not PERIOD_RE.match(str(month or "")):
        raise ValueError("月份格式非法，应为 YYYY-MM")
    rows = (db.query(HrPayroll, HrEmployee)
            .join(HrEmployee, HrEmployee.id == HrPayroll.emp_id)
            .filter(HrPayroll.month == month)
            .order_by(HrEmployee.emp_no).all())
    return [payroll_to_dict(p, e) for p, e in rows]


def payroll_confirm(db: Session, payroll_id: int) -> dict:
    p = db.get(HrPayroll, payroll_id)
    if p is None:
        raise LookupError("薪资记录不存在")
    if p.status == "confirmed":
        raise ValueError("该薪资已确认，不能重复确认")
    p.status = "confirmed"
    db.commit()
    db.refresh(p)
    return payroll_to_dict(p, db.get(HrEmployee, p.emp_id))


def payroll_delete(db: Session, payroll_id: int) -> None:
    p = db.get(HrPayroll, payroll_id)
    if p is None:
        raise LookupError("薪资记录不存在")
    if p.status == "confirmed":
        raise ValueError("已确认的薪资不能删除")
    db.delete(p)
    db.commit()


# ---------------- 看板 / 导出 ----------------

def hr_summary(db: Session, month: str = "") -> dict:
    employees = db.query(HrEmployee).all()
    active = [e for e in employees if e.status != "left"]
    probation = [e for e in employees if e.status == "probation"]
    today = date.today()
    expiring = [e for e in active
                if (e.contract_end and 0 <= (e.contract_end - today).days <= 30)
                or (e.probation_end and 0 <= (e.probation_end - today).days <= 30)]
    payrolls = []
    if month:
        if not PERIOD_RE.match(month):
            raise ValueError("月份格式非法，应为 YYYY-MM")
        payrolls = db.query(HrPayroll).filter(HrPayroll.month == month).all()
    return {
        "month": month,
        "headcount": len(active),
        "probation": len(probation),
        "left": len(employees) - len(active),
        "expiring_30": len(expiring),
        "payroll_count": len(payrolls),
        "payroll_net": money(sum(float(p.net_pay or 0) for p in payrolls)),
        "payroll_tax": money(sum(float(p.tax or 0) for p in payrolls)),
        "payroll_draft": sum(1 for p in payrolls if p.status == "draft"),
    }


def _xlsx(title: str, headers: list[str], rows: list[list]) -> bytes:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = title[:31]
    ws.append(headers)
    for row in rows:
        ws.append([safe_cell(v) for v in row])
    ws.freeze_panes = "A2"
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def employees_export(db: Session) -> bytes:
    rows = [[e.emp_no, e.name, e.department, e.position,
             e.hire_date.isoformat() if e.hire_date else "",
             e.probation_end.isoformat() if e.probation_end else "",
             e.contract_end.isoformat() if e.contract_end else "",
             STATUS_LABELS.get(e.status, e.status), e.phone,
             money(e.base_salary),
             "" if effective_social_base(e) is None else effective_social_base(e),
             money(e.special_additional), e.note]
            for e in db.query(HrEmployee).order_by(HrEmployee.emp_no).all()]
    headers = ["工号", "姓名", "部门", "岗位", "入职日期", "试用到期", "合同到期",
               "状态", "手机", "基本工资", "社保基数", "专项附加扣除", "备注"]
    return _xlsx("员工花名册", headers, rows)


def payroll_export(db: Session, month: str) -> bytes:
    rows = [[e.emp_no, e.name, e.department, p.month, money(p.base_salary),
             money(p.allowance), money(p.overtime_pay), money(p.deduction),
             money(p.social_insurance), money(p.housing_fund), money(p.tax),
             money(p.net_pay), PAYROLL_STATUS_LABELS.get(p.status, p.status)]
            for p, e in (db.query(HrPayroll, HrEmployee)
                         .join(HrEmployee, HrEmployee.id == HrPayroll.emp_id)
                         .filter(HrPayroll.month == month)
                         .order_by(HrEmployee.emp_no).all())]
    headers = ["工号", "姓名", "部门", "月份", "基本工资", "补贴", "加班费", "扣款",
               "社保", "公积金", "个税", "实发", "状态"]
    return _xlsx(f"薪资表{month}", headers, rows)


def hr_summary_tool(month: str = "") -> dict:
    """人事看板摘要（只读）：可按 YYYY-MM 月份读取薪资汇总，缺省保持原行为。"""
    with SessionLocal() as db:
        return {"ok": True, **hr_summary(db, month=month)}
