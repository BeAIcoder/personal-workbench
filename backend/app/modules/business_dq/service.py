# -*- coding: utf-8 -*-
"""business_dq 服务：跨模块数据质量巡检（只读、无自有表）。

五维检查以数据驱动检查表实现（domain/检查名/级别/必需表/计数 SQL/样本 SQL/中文建议）。
全部 SQL 只读对账，绝不写任何表；模块表缺失 → 域 available=false + 中文 gap_reasons，不以 0 顶替。
金额列普遍 Float 存储，判定用 0.005 容差；as_of 注入保证未来日期类检查可复算。
"""
from __future__ import annotations

import re
from datetime import date, datetime

from sqlalchemy import text

AS_OF_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TOL = 0.005
_LEVEL_DEDUCT = {"error": 10, "warning": 3, "info": 1}
_DOMAIN_LABELS = {"leasing": "招商", "operations": "营运", "hr": "人事", "legal": "法务", "planning": "企划", "facility": "工程", "finance": "财务"}

# 巡检里硬编码的枚举须与模块服务层同源（契约测试锁定，防止模块加状态而巡检静默漏检）
LEAD_STAGE_VALUES = ("lead", "contact", "offer", "intent", "signed", "lost")
UNIT_STATUS_VALUES = ("vacant", "negotiating", "reserved", "leased")
# 四个税种申报表实际会写的状态（模型默认 draft；路由写 calculated/exported；提交态 submitted）。
# 旧实现只查 status='failed'，但这四个模块从不写 failed → 死检查（R11-084）；
# 改为白名单后，历史 failed 与任何脏值都会被巡检发现。
TAX_FILING_STATUS_VALUES = ("draft", "calculated", "exported", "submitted")


def _sql_values(values) -> str:
    """把受控常量元组拼成 SQL IN 列表（仅用于本模块常量，不接受外部输入）。"""
    return ", ".join(f"'{value}'" for value in values)

__all__ = ["build_profile", "dq_summary"]


def _issue(level, check, count, sample, suggestion):
    return {"level": level, "check": check, "count": count, "sample": [str(s) for s in sample], "suggestion": suggestion}


def _exists(db, name):
    row = db.execute(
        text("SELECT 1 FROM sqlite_master WHERE type='table' AND name=:t"), {"t": name},
    ).first()
    return row is not None


def _columns(db, name) -> set[str]:
    """表的实际列名（列补丁尚未执行时 DQ 也不能整域失败）。"""
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", str(name or "")):
        return set()
    try:
        rows = db.execute(text(f"PRAGMA table_info({name})")).fetchall()
    except Exception:
        return set()
    return {str(row[1]) for row in rows}


def _dom_leasing(db, as_of):
    if not (_exists(db, "mod_leasing_unit") and _exists(db, "mod_leasing_lead") and _exists(db, "mod_leasing_followup")):
        return {"available": False, "gap_reasons": ["招商域表未就绪（模块未安装或迁移未完成）"], "context": {}, "issues": []}
    issues, context = [], {}
    context["units"] = int(db.execute(text("SELECT COUNT(*) FROM mod_leasing_unit")).scalar() or 0)
    context["leads"] = int(db.execute(text("SELECT COUNT(*) FROM mod_leasing_lead")).scalar() or 0)
    context["followups"] = int(db.execute(text("SELECT COUNT(*) FROM mod_leasing_followup")).scalar() or 0)
    issues_out = issues
    n = int(db.execute(text(
        "SELECT COUNT(*) FROM mod_leasing_lead l WHERE l.unit_id IS NOT NULL AND NOT EXISTS "
        "(SELECT 1 FROM mod_leasing_unit u WHERE u.id = l.unit_id)"
    )).scalar() or 0)
    if n:
        samp = db.execute(text(
            "SELECT DISTINCT l.unit_id FROM mod_leasing_lead l WHERE l.unit_id IS NOT NULL AND NOT EXISTS "
            "(SELECT 1 FROM mod_leasing_unit u WHERE u.id = l.unit_id) LIMIT 5"
        )).fetchall()
        issues_out.append(_issue("warning", "孤儿引用", n, [r[0] for r in samp], "线索关联的铺位已不存在；建议清理该线索的铺位关联或恢复铺位数据"))
    # 内部不变量（2026-11-04）：同铺位多份占用中的签约 / 已租但无签约 / 非法枚举
    released_filter = (
        " AND released_at IS NULL" if "released_at" in _columns(db, "mod_leasing_lead") else ""
    )
    n = int(db.execute(text(
        "SELECT COUNT(*) FROM (SELECT unit_id FROM mod_leasing_lead "
        "WHERE stage = 'signed' AND unit_id IS NOT NULL" + released_filter +
        " GROUP BY unit_id HAVING COUNT(*) > 1)"
    )).scalar() or 0)
    if n:
        samp = db.execute(text(
            "SELECT unit_id FROM mod_leasing_lead WHERE stage = 'signed' AND unit_id IS NOT NULL"
            + released_filter + " GROUP BY unit_id HAVING COUNT(*) > 1 LIMIT 5"
        )).fetchall()
        issues_out.append(_issue("error", "重复签约", n, [r[0] for r in samp], "同一铺位存在多份占用中的签约线索；只会有一份合同生效，建议释放其余线索"))
    n = int(db.execute(text(
        "SELECT COUNT(*) FROM mod_leasing_unit u WHERE u.status = 'leased' AND NOT EXISTS "
        "(SELECT 1 FROM mod_leasing_lead l WHERE l.unit_id = u.id AND l.stage = 'signed'"
        + released_filter + ")"
    )).scalar() or 0)
    if n:
        samp = db.execute(text(
            "SELECT unit_no FROM mod_leasing_unit u WHERE u.status = 'leased' AND NOT EXISTS "
            "(SELECT 1 FROM mod_leasing_lead l WHERE l.unit_id = u.id AND l.stage = 'signed'"
            + released_filter + ") LIMIT 5"
        )).fetchall()
        issues_out.append(_issue("warning", "已租无签约", n, [r[0] for r in samp], "铺位标记为已租但无占用中的签约线索；出租率可能虚高，建议补录签约或改回空置"))
    bad_lead = int(db.execute(text(
        "SELECT COUNT(*) FROM mod_leasing_lead WHERE stage NOT IN (" + _sql_values(LEAD_STAGE_VALUES) + ")"
    )).scalar() or 0)
    bad_unit = int(db.execute(text(
        "SELECT COUNT(*) FROM mod_leasing_unit WHERE status NOT IN (" + _sql_values(UNIT_STATUS_VALUES) + ")"
    )).scalar() or 0)
    n = bad_lead + bad_unit
    if n:
        samp = [r[0] for r in db.execute(text(
            "SELECT company FROM mod_leasing_lead WHERE stage NOT IN (" + _sql_values(LEAD_STAGE_VALUES) + ") LIMIT 3"
        )).fetchall()]
        samp += [r[0] for r in db.execute(text(
            "SELECT unit_no FROM mod_leasing_unit WHERE status NOT IN (" + _sql_values(UNIT_STATUS_VALUES) + ") LIMIT 2"
        )).fetchall()]
        issues_out.append(_issue("error", "非法枚举", n, samp, "线索阶段或铺位状态不在允许取值内；筛选与统计会失真，建议修正为合法枚举"))
    return {"available": True, "gap_reasons": [], "context": context, "issues": issues_out}


def _dom_operations(db, as_of):
    if not (_exists(db, "mod_ops_contract") and _exists(db, "mod_ops_bill") and _exists(db, "mod_ops_work_order")):
        return {"available": False, "gap_reasons": ["营运域表未就绪（模块未安装或迁移未完成）"], "context": {}, "issues": []}
    issues, context = [], {}
    context["contracts"] = int(db.execute(text("SELECT COUNT(*) FROM mod_ops_contract")).scalar() or 0)
    context["bills"] = int(db.execute(text("SELECT COUNT(*) FROM mod_ops_bill")).scalar() or 0)
    context["work_orders"] = int(db.execute(text("SELECT COUNT(*) FROM mod_ops_work_order")).scalar() or 0)
    # 日期有效性：租期倒挂
    n = int(db.execute(text(
        "SELECT COUNT(*) FROM mod_ops_contract WHERE start_date IS NOT NULL AND end_date IS NOT NULL AND date(end_date) < date(start_date)"
    )).scalar() or 0)
    if n:
        samp = db.execute(text(
            "SELECT contract_no FROM mod_ops_contract WHERE start_date IS NOT NULL AND end_date IS NOT NULL AND date(end_date) < date(start_date) LIMIT 5"
        )).fetchall()
        issues.append(_issue("error", "日期有效性", n, [r[0] for r in samp], "合同结束日期早于开始日期；建议核对修正租期"))
    # 孤儿引用：账单→合同
    n = int(db.execute(text(
        "SELECT COUNT(*) FROM mod_ops_bill b WHERE NOT EXISTS (SELECT 1 FROM mod_ops_contract c WHERE c.id = b.contract_id)"
    )).scalar() or 0)
    if n:
        samp = db.execute(text(
            "SELECT b.id FROM mod_ops_bill b WHERE NOT EXISTS (SELECT 1 FROM mod_ops_contract c WHERE c.id = b.contract_id) LIMIT 5"
        )).fetchall()
        issues.append(_issue("error", "孤儿引用", n, [r[0] for r in samp], "账单没有对应合同；建议核查出账流程与删除守卫"))
    # 孤儿引用：合同→铺位（unit_id 非空且悬挂）
    n = int(db.execute(text(
        "SELECT COUNT(*) FROM mod_ops_contract c WHERE c.unit_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM mod_leasing_unit u WHERE u.id = c.unit_id)"
    )).scalar() or 0) if _exists(db, "mod_leasing_unit") else 0
    if n:
        samp = db.execute(text(
            "SELECT c.contract_no FROM mod_ops_contract c WHERE c.unit_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM mod_leasing_unit u WHERE u.id = c.unit_id) LIMIT 5"
        )).fetchall()
        issues.append(_issue("warning", "孤儿引用", n, [r[0] for r in samp], "合同关联的铺位已不存在；建议解除关联或恢复铺位"))
    # 孤儿引用：工单→合同（2026-11-02 跨模块勾稽扩展）
    n = int(db.execute(text(
        "SELECT COUNT(*) FROM mod_ops_work_order w WHERE w.contract_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM mod_ops_contract c WHERE c.id = w.contract_id)"
    )).scalar() or 0)
    if n:
        samp = db.execute(text(
            "SELECT w.code FROM mod_ops_work_order w WHERE w.contract_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM mod_ops_contract c WHERE c.id = w.contract_id) LIMIT 5"
        )).fetchall()
        issues.append(_issue("warning", "孤儿引用", n, [r[0] for r in samp], "工单关联的合同已不存在；建议解除关联或恢复合同"))
    # 孤儿引用：工单→设备（facility 存在时）
    n = int(db.execute(text(
        "SELECT COUNT(*) FROM mod_ops_work_order w WHERE w.equipment_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM mod_fac_equipment e WHERE e.id = w.equipment_id)"
    )).scalar() or 0) if _exists(db, "mod_fac_equipment") else 0
    if n:
        samp = db.execute(text(
            "SELECT w.code FROM mod_ops_work_order w WHERE w.equipment_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM mod_fac_equipment e WHERE e.id = w.equipment_id) LIMIT 5"
        )).fetchall()
        issues.append(_issue("warning", "孤儿引用", n, [r[0] for r in samp], "工单关联的设备已不存在；建议解除关联或恢复设备"))
    # 金额异常：超收存量（paid > total，容差 0.005）
    n = int(db.execute(text(
        "SELECT COUNT(*) FROM mod_ops_bill WHERE paid_amount > total_amount + 0.005"
    )).scalar() or 0)
    if n:
        samp = db.execute(text(
            "SELECT period FROM mod_ops_bill WHERE paid_amount > total_amount + 0.005 LIMIT 5"
        )).fetchall()
        issues.append(_issue("error", "金额异常", n, [r[0] for r in samp], "账单实收超过应收；建议核对收款记录并冲正"))
    # 状态与金额不一致：paid 但欠费 / unpaid 却有收款 / partial 但金额边界异常
    n = int(db.execute(text(
        "SELECT COUNT(*) FROM mod_ops_bill WHERE (status = 'paid' AND paid_amount + 0.005 < total_amount) "
        "OR (status = 'unpaid' AND paid_amount > 0.005) "
        "OR (status = 'partial' AND (paid_amount <= 0.005 OR paid_amount >= total_amount - 0.005))"
    )).scalar() or 0)
    if n:
        samp = db.execute(text(
            "SELECT period FROM mod_ops_bill WHERE (status = 'paid' AND paid_amount + 0.005 < total_amount) "
            "OR (status = 'unpaid' AND paid_amount > 0.005) "
            "OR (status = 'partial' AND (paid_amount <= 0.005 OR paid_amount >= total_amount - 0.005)) LIMIT 5"
        )).fetchall()
        issues.append(_issue("warning", "状态一致性", n, [r[0] for r in samp], "账单状态与收款金额不一致；建议按实收重算状态"))
    # 状态一致性：active 合同已过期
    n = int(db.execute(text(
        "SELECT COUNT(*) FROM mod_ops_contract WHERE status = 'active' AND end_date IS NOT NULL AND date(end_date) < date(:as_of)"
    ), {"as_of": as_of}).scalar() or 0)
    if n:
        samp = db.execute(text(
            "SELECT contract_no FROM mod_ops_contract WHERE status = 'active' AND end_date IS NOT NULL AND date(end_date) < date(:as_of) LIMIT 5"
        ), {"as_of": as_of}).fetchall()
        issues.append(_issue("warning", "状态一致性", n, [r[0] for r in samp], "履约中合同已过租期；建议办理续签或终止"))
    # 租决未匹配（Fix round 1）：关联铺位但同租户没有占用中の签约线索 → 出账只能按固定租金，
    # 免租期/递增率/抽成形式静默失效；提示核对租户名称或补录签约
    if _exists(db, "mod_leasing_lead"):
        released = (
            " AND l.released_at IS NULL"
            if "released_at" in _columns(db, "mod_leasing_lead") else ""
        )
        n = int(db.execute(text(
            "SELECT COUNT(*) FROM mod_ops_contract c WHERE c.status = 'active' "
            "AND c.unit_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM mod_leasing_lead l "
            "WHERE l.unit_id = c.unit_id AND l.stage = 'signed'" + released +
            " AND TRIM(l.company) = TRIM(c.tenant))"
        )).scalar() or 0)
        if n:
            samp = db.execute(text(
                "SELECT c.contract_no FROM mod_ops_contract c WHERE c.status = 'active' "
                "AND c.unit_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM mod_leasing_lead l "
                "WHERE l.unit_id = c.unit_id AND l.stage = 'signed'" + released +
                " AND TRIM(l.company) = TRIM(c.tenant)) LIMIT 5"
            )).fetchall()
            issues.append(_issue("info", "租决未匹配", n, [r[0] for r in samp],
                                 "合同已关联铺位但没有同租户的生效签约线索，出账将按固定租金（租决参数不生效）；建议核对租户名称或补录签约"))
    return {"available": True, "gap_reasons": [], "context": context, "issues": issues}


def _dom_hr(db, as_of):
    if not (_exists(db, "mod_hr_employee") and _exists(db, "mod_hr_attendance") and _exists(db, "mod_hr_payroll")):
        return {"available": False, "gap_reasons": ["人事域表未就绪（模块未安装或迁移未完成）"], "context": {}, "issues": []}
    issues, context = [], {}
    context["employees"] = int(db.execute(text("SELECT COUNT(*) FROM mod_hr_employee")).scalar() or 0)
    context["attendance_months"] = int(db.execute(text("SELECT COUNT(DISTINCT month) FROM mod_hr_attendance")).scalar() or 0)
    # 日期有效性：未来入职
    n = int(db.execute(text(
        "SELECT COUNT(*) FROM mod_hr_employee WHERE hire_date IS NOT NULL AND date(hire_date) > :as_of"
    ), {"as_of": as_of}).scalar() or 0)
    if n:
        samp = db.execute(text(
            "SELECT emp_no FROM mod_hr_employee WHERE hire_date IS NOT NULL AND date(hire_date) > :as_of LIMIT 5"
        ), {"as_of": as_of}).fetchall()
        issues.append(_issue("warning", "日期有效性", n, [r[0] for r in samp], "入职日期晚于检查截止日；建议核对入职信息"))
    # 金额异常：负薪资
    n = int(db.execute(text(
        "SELECT COUNT(*) FROM mod_hr_employee WHERE base_salary IS NOT NULL AND base_salary < 0"
    )).scalar() or 0)
    if n:
        samp = db.execute(text(
            "SELECT emp_no FROM mod_hr_employee WHERE base_salary IS NOT NULL AND base_salary < 0 LIMIT 5"
        )).fetchall()
        issues.append(_issue("error", "金额异常", n, [r[0] for r in samp], "存在负数底薪；建议核对薪资档案并修正"))
    # 跨表勾稽：有薪资但同月无考勤（2026-11-02 v3）
    n = int(db.execute(text(
        "SELECT COUNT(*) FROM mod_hr_payroll p WHERE NOT EXISTS "
        "(SELECT 1 FROM mod_hr_attendance a WHERE a.emp_id = p.emp_id AND a.month = p.month)"
    )).scalar() or 0)
    if n:
        samp = db.execute(text(
            "SELECT p.month FROM mod_hr_payroll p WHERE NOT EXISTS "
            "(SELECT 1 FROM mod_hr_attendance a WHERE a.emp_id = p.emp_id AND a.month = p.month) LIMIT 5"
        )).fetchall()
        issues.append(_issue("warning", "跨表勾稽", n, [r[0] for r in samp], "存在无考勤记录的薪资单；建议补录考勤或说明出勤依据"))
    return {"available": True, "gap_reasons": [], "context": context, "issues": issues}


def _dom_legal(db, as_of):
    if not (_exists(db, "mod_legal_case") and _exists(db, "mod_legal_contract_review") and _exists(db, "mod_legal_seal")):
        return {"available": False, "gap_reasons": ["法务域表未就绪（模块未安装或迁移未完成）"], "context": {}, "issues": []}
    issues, context = [], {}
    context["cases"] = int(db.execute(text("SELECT COUNT(*) FROM mod_legal_case")).scalar() or 0)
    context["reviews"] = int(db.execute(text("SELECT COUNT(*) FROM mod_legal_contract_review")).scalar() or 0)
    context["seals"] = int(db.execute(text("SELECT COUNT(*) FROM mod_legal_seal")).scalar() or 0)
    # 重复：案件号
    n = int(db.execute(text(
        "SELECT COUNT(*) FROM (SELECT case_no FROM mod_legal_case WHERE case_no IS NOT NULL AND case_no != '' GROUP BY case_no HAVING COUNT(*) > 1)"
    )).scalar() or 0)
    if n:
        samp = db.execute(text(
            "SELECT case_no FROM mod_legal_case WHERE case_no IS NOT NULL AND case_no != '' GROUP BY case_no HAVING COUNT(*) > 1 LIMIT 5"
        )).fetchall()
        issues.append(_issue("warning", "重复", n, [r[0] for r in samp], "存在重复案件号；建议合并或区分编号规则"))
    # 日期有效性：开庭早于立案
    n = int(db.execute(text(
        "SELECT COUNT(*) FROM mod_legal_case WHERE hearing_date IS NOT NULL AND filed_date IS NOT NULL AND date(hearing_date) < date(filed_date)"
    )).scalar() or 0)
    if n:
        samp = db.execute(text(
            "SELECT case_no FROM mod_legal_case WHERE hearing_date IS NOT NULL AND filed_date IS NOT NULL AND date(hearing_date) < date(filed_date) LIMIT 5"
        )).fetchall()
        issues.append(_issue("warning", "日期有效性", n, [r[0] for r in samp], "开庭日期早于立案日期；建议核对案件时间线"))
    return {"available": True, "gap_reasons": [], "context": context, "issues": issues}


def _dom_planning(db, as_of):
    if not (_exists(db, "mod_plan_campaign") and _exists(db, "mod_plan_vendor")):
        return {"available": False, "gap_reasons": ["企划域表未就绪（模块未安装或迁移未完成）"], "context": {}, "issues": []}
    issues, context = [], {}
    context["campaigns"] = int(db.execute(text("SELECT COUNT(*) FROM mod_plan_campaign")).scalar() or 0)
    context["vendors"] = int(db.execute(text("SELECT COUNT(*) FROM mod_plan_vendor")).scalar() or 0)
    # 日期有效性：活动档期倒挂
    n = int(db.execute(text(
        "SELECT COUNT(*) FROM mod_plan_campaign WHERE start_date IS NOT NULL AND end_date IS NOT NULL AND date(end_date) < date(start_date)"
    )).scalar() or 0)
    if n:
        samp = db.execute(text(
            "SELECT name FROM mod_plan_campaign WHERE start_date IS NOT NULL AND end_date IS NOT NULL AND date(end_date) < date(start_date) LIMIT 5"
        )).fetchall()
        issues.append(_issue("warning", "日期有效性", n, [r[0] for r in samp], "活动结束早于开始；建议核对档期"))
    # 金额异常：负预算
    n = int(db.execute(text(
        "SELECT COUNT(*) FROM mod_plan_campaign WHERE budget_total IS NOT NULL AND budget_total < 0"
    )).scalar() or 0)
    if n:
        samp = db.execute(text(
            "SELECT name FROM mod_plan_campaign WHERE budget_total IS NOT NULL AND budget_total < 0 LIMIT 5"
        )).fetchall()
        issues.append(_issue("error", "金额异常", n, [r[0] for r in samp], "存在负数活动预算；建议核对预算录入"))
    # 预算执行：实际支出超预算 20%（info，供复核）
    n = int(db.execute(text(
        "SELECT COUNT(*) FROM mod_plan_campaign WHERE budget_total > 0 AND actual_total > budget_total * 1.2"
    )).scalar() or 0)
    if n:
        samp = db.execute(text(
            "SELECT name FROM mod_plan_campaign WHERE budget_total > 0 AND actual_total > budget_total * 1.2 LIMIT 5"
        )).fetchall()
        issues.append(_issue("info", "预算执行", n, [r[0] for r in samp], "活动实际支出超预算 20%+；建议复核超支原因与审批"))
    return {"available": True, "gap_reasons": [], "context": context, "issues": issues}


def _dom_facility(db, as_of):
    if not (_exists(db, "mod_fac_equipment") and _exists(db, "mod_fac_compliance")):
        return {"available": False, "gap_reasons": ["工程域表未就绪（模块未安装或迁移未完成）"], "context": {}, "issues": []}
    issues, context = [], {}
    context["equipment"] = int(db.execute(text("SELECT COUNT(*) FROM mod_fac_equipment")).scalar() or 0)
    context["compliance"] = int(db.execute(text("SELECT COUNT(*) FROM mod_fac_compliance")).scalar() or 0)
    # 孤儿引用：合规事项→设备（空值=楼宇级，不算孤儿）
    n = int(db.execute(text(
        "SELECT COUNT(*) FROM mod_fac_compliance m WHERE m.equipment_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM mod_fac_equipment e WHERE e.id = m.equipment_id)"
    )).scalar() or 0)
    if n:
        samp = db.execute(text(
            "SELECT m.id FROM mod_fac_compliance m WHERE m.equipment_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM mod_fac_equipment e WHERE e.id = m.equipment_id) LIMIT 5"
        )).fetchall()
        issues.append(_issue("warning", "孤儿引用", n, [r[0] for r in samp], "合规事项关联的设备已不存在；建议改为楼宇级或恢复设备"))
    # 过期未完成：合规事项已然到期且无完成日期（2026-11-02 v3）
    n = int(db.execute(text(
        "SELECT COUNT(*) FROM mod_fac_compliance WHERE due_date IS NOT NULL AND done_date IS NULL AND date(due_date) < date(:as_of)"
    ), {"as_of": as_of}).scalar() or 0)
    if n:
        samp = db.execute(text(
            "SELECT title FROM mod_fac_compliance WHERE due_date IS NOT NULL AND done_date IS NULL AND date(due_date) < date(:as_of) LIMIT 5"
        ), {"as_of": as_of}).fetchall()
        issues.append(_issue("warning", "过期未完成", n, [r[0] for r in samp], "合规事项已到期但未登记完成；建议安排年检/消防/维保并回填"))
    return {"available": True, "gap_reasons": [], "context": context, "issues": issues}


def _board_business_metrics(db) -> dict:
    """业务库口径（只读，双源对账用）：招商出租率 + 营运收缴率/应收。

    只返回「业务库有数据」的指标（招商需有铺位、营运需有应收），空库时缺键 → 对账自动跳过，
    避免把空台账误报成与看板不一致。
    """
    out: dict = {}
    try:
        from ..business_leasing.service import unit_summary
        units = unit_summary(db)
        if (units.get("total_units") or 0) > 0:
            out["leasing_rate"] = units.get("leasing_rate")
    except Exception:
        pass
    try:
        from ..business_operations.service import ops_summary as _ops_summary
        s = _ops_summary(db)
        if (s.get("receivable") or 0) > 0:
            out["collection_rate"] = s.get("collection_rate")
            out["receivable"] = s.get("receivable")
    except Exception:
        pass
    return out


def _dom_finance(db, as_of):
    issues, context = [], {}
    present = []
    if _exists(db, "mod_bip_entry"):
        present.append("bip")
        n = int(db.execute(text("SELECT COUNT(*) FROM mod_bip_entry WHERE debit IS NULL OR credit IS NULL")).scalar() or 0)
        if n:
            issues.append(_issue("warning", "完整性", n, [], "存在借贷方向缺失的分录；建议核对导入批次"))
        # 勾稽平衡：按凭证借贷不平（容差 0.01，2026-11-02 v3）
        try:
            from sqlalchemy import func as sa_func
            from ..finance_ledger_bip.models import BipEntry
            rows = (db.query(BipEntry.voucher_id, sa_func.sum(BipEntry.debit), sa_func.sum(BipEntry.credit))
                    .group_by(BipEntry.voucher_id).all())
            bad = [r for r in rows if abs(float(r[1] or 0) - float(r[2] or 0)) > 0.01]
            if bad:
                issues.append(_issue("error", "勾稽平衡", len(bad), [r[0] for r in bad[:5]], "存在借贷不平的凭证；建议核对导入完整性或红字冲销记录"))
        except Exception:  # 单检查失败不拖垮财务域
            pass
    if _exists(db, "mod_pl_transaction"):
        present.append("pl")
        n = int(db.execute(text(
            "SELECT COUNT(*) FROM mod_pl_transaction WHERE occurred_on IS NOT NULL AND date(occurred_on) > :as_of"
        ), {"as_of": as_of}).scalar() or 0)
        if n:
            issues.append(_issue("warning", "日期有效性", n, [], "存在未来日期的个人记账；建议核对记账日期"))
    bad_status_total = 0
    for tname in ("mod_vat_filing", "mod_dsf_filing", "mod_iit_filing", "mod_stamp_duty_filing"):
        if _exists(db, tname):
            present.append(tname)
            bad_status_total += int(db.execute(text(
                "SELECT COUNT(*) FROM " + tname + " WHERE status IS NULL OR status NOT IN ("
                + _sql_values(TAX_FILING_STATUS_VALUES) + ")"
            )).scalar() or 0)
    if bad_status_total:
        issues.append(_issue("warning", "运行状态", bad_status_total, [],
                             "存在非预期的税种申报状态（仅允许 draft/calculated/exported/submitted）；"
                             "建议核对该记录来源并修正或重新填报"))
    # 税务域勾稽（2026-11-02 v4）：申报表 vs 财务数据口径。
    # R11-007：财务数据整表为空时不能逐条报「无同期财务数据」——那是「未导入」，
    # 不是勾稽不一致；只有确有财务数据但同企业同年度缺配比才算真实差异。
    if _exists(db, "mod_tax_return") and _exists(db, "mod_tax_financial_data"):
        present.append("tax_returns")
        returns_total = int(db.execute(text("SELECT COUNT(*) FROM mod_tax_return")).scalar() or 0)
        fin_total = int(db.execute(text("SELECT COUNT(*) FROM mod_tax_financial_data")).scalar() or 0)
        if fin_total == 0:
            if returns_total:
                issues.append(_issue(
                    "info", "申报勾稽", returns_total, [],
                    "财务数据尚未导入（mod_tax_financial_data 为空），税会勾稽未执行；"
                    "请先在「财报导入」导入并保存财务数据",
                ))
        else:
            n = int(db.execute(text(
                "SELECT COUNT(*) FROM mod_tax_return r WHERE NOT EXISTS "
                "(SELECT 1 FROM mod_tax_financial_data f WHERE f.enterprise_id = r.enterprise_id AND f.year = r.year)"
            )).scalar() or 0)
            if n:
                issues.append(_issue("warning", "申报勾稽", n, [], "存在无同期财务数据的申报记录；建议补录财务数据或核对申报依据"))
            n = int(db.execute(text(
                "SELECT COUNT(*) FROM mod_tax_financial_data f WHERE NOT EXISTS "
                "(SELECT 1 FROM mod_tax_return r WHERE r.enterprise_id = f.enterprise_id AND r.year = f.year)"
            )).scalar() or 0)
            if n:
                issues.append(_issue("info", "申报勾稽", n, [], "存在财务数据但同年度无申报记录；建议确认是否已申报"))
        n = int(db.execute(text(
            "SELECT COUNT(*) FROM mod_tax_return WHERE tax_amount > 0.01 AND (taxable_income IS NULL OR taxable_income <= 0)"
        )).scalar() or 0)
        if n:
            issues.append(_issue("error", "税额异常", n, [], "有应纳税额但应纳税所得额非正；建议核对申报表数据"))
        n = int(db.execute(text(
            "SELECT COUNT(*) FROM mod_tax_return WHERE taxable_income > 0 AND tax_amount > taxable_income * 0.25 + 0.01"
        )).scalar() or 0)
        if n:
            issues.append(_issue("warning", "税额异常", n, [], "税额超过法定税率 25% 上限；可能含滞纳金或口径错误，建议复核"))
    if _exists(db, "mod_vault_dataset"):
        present.append("vault")
        try:
            from ..finance_vault import service as vault_service  # 惰性导入：模块可独立缺失
            vq = vault_service.vault_quality_overview()
        except Exception:  # 画像失败不拖垮总览
            vq = None
        if isinstance(vq, dict) and vq.get("available"):
            s = vq["summary"]
            if s["empty_tables"]:
                issues.append(_issue("info", "覆盖", s["empty_tables"], [], "数据仓存在 0 行数据集；建议重新上传或清理登记"))
            if s["dup_tables"]:
                samples = [t["table"] for t in vq["tables"] if (t.get("dup_rows") or 0) > 0][:5]
                issues.append(_issue("warning", "重复", s["dup_tables"], samples, "数据仓存在整行重复的数据集；建议按 replace 重新入仓或在源头去重"))
            if s["null_heavy_tables"]:
                samples = [f'{t["table"]}.{t["null_heavy"][0]["column"]}' for t in vq["tables"] if t["null_heavy"]][:5]
                issues.append(_issue("warning", "空值画像", s["null_heavy_tables"], samples, "存在高缺失列（缺失率>30%）；建议核对来源或补全字段"))
        else:
            n = int(db.execute(text("SELECT COUNT(*) FROM mod_vault_dataset WHERE rows = 0")).scalar() or 0)
            if n:
                issues.append(_issue("info", "覆盖", n, [], "数据仓存在 0 行数据集；建议重新上传或清理登记"))
    if _exists(db, "mod_board_snapshot"):
        present.append("board")
        n = int(db.execute(text("SELECT COUNT(*) FROM mod_board_snapshot WHERE status = 'failed'")).scalar() or 0)
        if n:
            issues.append(_issue("warning", "运行状态", n, [], "存在 failed 状态的报表快照；建议到报表看板复核管线日志"))
        try:
            from ..finance_board.models import BoardSnapshot
            row = db.query(BoardSnapshot).filter(BoardSnapshot.status == "ok").order_by(BoardSnapshot.created_at.desc()).first()
            latest = row.created_at if row else None
        except Exception:
            latest = None
        if latest is not None:
            try:
                days = (datetime.now() - latest).days
            except TypeError:
                days = 0
            if days > 7:
                issues.append(_issue("warning", "陈旧快照", days, [str(latest)], "看板最近成功快照已超过 7 天未更新；建议运行管线刷新"))
            # R11-034：快照时间早于管线数据文件 mtime = 快照未包含最新数据（跑管线后未归档）。
            # 60 秒容差覆盖「写数据 → 生成 HTML → 归档」的正常先后关系。
            try:
                from ..finance_board import service as board_service
                data_path = board_service.pipeline_dir() / "data" / "report_data.json"
                if data_path.is_file():
                    src_mtime = datetime.fromtimestamp(data_path.stat().st_mtime)
                    if (src_mtime - latest).total_seconds() > 60:
                        issues.append(_issue(
                            "warning", "快照落后", 1,
                            [f"快照 {latest:%Y-%m-%d %H:%M} 早于数据源 {src_mtime:%Y-%m-%d %H:%M}"],
                            "看板最新成功快照早于管线数据文件 report_data.json；快照未包含最新数据，"
                            "建议重新运行管线并归档新快照",
                        ))
            except Exception:  # 管线目录不可用/时钟异常不拖垮财务域
                pass
        # 连续失败（2026-11-02 v4）：最近两条快照均 failed → error（告警会同步登记问题记录）
        try:
            from ..finance_board.models import BoardSnapshot
            latest_two = db.query(BoardSnapshot).order_by(BoardSnapshot.id.desc()).limit(2).all()
            if len(latest_two) == 2 and all(s.status == "failed" for s in latest_two):
                issues.append(_issue("error", "连续失败", 2, [s.stamp for s in latest_two], "看板管线连续两次运行失败；已登记问题记录，建议检查管线脚本与日志"))
        except Exception:
            pass
        # 产物内容一致性（2026-11-02 v5）：HTML 内嵌数据 vs 管线 report_data.json（带缓存）
        try:
            from ..finance_board import service as board_service
            vr = board_service.verify_snapshot()
            if vr.get("available") and (vr.get("summary") or {}).get("fail"):
                failed_names = [c["name"] for c in vr.get("checks", []) if not c.get("ok")]
                issues.append(_issue("warning", "产物一致性", len(failed_names), failed_names,
                                     "看板 HTML 与管线数据不一致或产物缺失；建议重新运行管线"))
            # 双源口径对照（v5.1）：看板 KPI vs 业务库（招商铺位出租率 / 营运收缴率与应收）
            kpi = (vr or {}).get("kpi") or {}
            if vr.get("available") and kpi:
                bm = _board_business_metrics(db)
                pairs = []
                if bm.get("leasing_rate") is not None and kpi.get("出租率") is not None:
                    pairs.append(("出租率", float(bm["leasing_rate"]) * 100, float(kpi["出租率"]), 5.0))
                if bm.get("collection_rate") is not None and kpi.get("收缴率") is not None:
                    pairs.append(("收缴率", float(bm["collection_rate"]) * 100, float(kpi["收缴率"]), 5.0))
                if bm.get("receivable") is not None and kpi.get("累计应收_万元") is not None:
                    pairs.append(("累计应收(万元)", float(bm["receivable"]) / 10000.0, float(kpi["累计应收_万元"]), None))
                diffs, bad = [], []
                for name, biz, dash, tol in pairs:
                    diff = biz - dash
                    if tol is None:
                        over = abs(diff) > max(1.0, abs(dash) * 0.2)
                        diffs.append(f"{name} 业务库{biz:.1f} vs 看板{dash:.1f}（差{diff:+.1f}）")
                    else:
                        over = abs(diff) > tol
                        diffs.append(f"{name} 业务库{biz:.1f}% vs 看板{dash:.1f}%（差{diff:+.1f}pp）")
                    if over:
                        bad.append(name)
                if diffs:
                    issues.append(_issue("warning" if bad else "info", "口径对照",
                                         len(bad) if bad else len(diffs), diffs[:6],
                                         "看板来源（报表源文件）与业务库（人工台账）为两套口径；差异大时建议核对台账同步"
                                         if bad else "双源口径近似一致"))
        except Exception:  # 巡检失败不拖垮财务域
            pass
    if not present:
        return {"available": False, "gap_reasons": ["财务域表均未就绪"], "context": {}, "issues": []}
    return {"available": True, "gap_reasons": [], "context": {"sources": present}, "issues": issues}


def _score_of(issues):
    score = 100
    for it in issues:
        score -= _LEVEL_DEDUCT.get(it["level"], 1)
    return max(0, int(score))


def build_profile(db, as_of=""):
    if as_of:
        if not AS_OF_RE.match(str(as_of)):
            raise ValueError("as_of 需为 YYYY-MM-DD 格式")
        try:
            date.fromisoformat(str(as_of))
        except ValueError as exc:
            # 正则只挡形状，2026-02-30 这类日历上不存在的日期会让 SQLite date() 返回 NULL，
            # 未来/过期类检查全部静默通过 → 输出 100 分假健康
            raise ValueError("as_of 不是有效日期（如 2026-02-30），请填 YYYY-MM-DD 的真实日期") from exc
    day = as_of or str(date.today())
    results = {}
    for fn in (_dom_leasing, _dom_operations, _dom_hr, _dom_legal, _dom_planning, _dom_facility, _dom_finance):
        try:
            r = fn(db, day)
        except Exception as exc:  # 单域异常不拖垮总览；如实报告原因
            r = {"available": False, "gap_reasons": [f"巡检执行失败：{type(exc).__name__}"], "context": {}, "issues": []}
        key = fn.__name__.replace("_dom_", "")
        results[key] = r
    scores = []
    for key in _DOMAIN_LABELS:
        r = results[key]
        issues = r.get("issues", [])
        r["score"] = _score_of(issues) if r.get("available") else None
        # L-176：前端域标签按 error/warning/info 显示，必须与 issues 同源；此前只返回
        # score/issues，UI 的 `?? 0` 导致每域恒显示 E0/W0/I0。
        r["error"] = sum(1 for it in issues if it.get("level") == "error")
        r["warning"] = sum(1 for it in issues if it.get("level") == "warning")
        r["info"] = sum(1 for it in issues if it.get("level") == "info")
        if r.get("available"):
            scores.append(r["score"])
    overall = round(sum(scores) / len(scores)) if scores else None
    top = []
    issue_count = 0
    for key in _DOMAIN_LABELS:
        for it in results[key].get("issues", []):
            issue_count += int(it.get("count") or 0)
            top.append({"domain": key, **it})
    top.sort(key=lambda x: {"error": 0, "warning": 1, "info": 2}.get(x["level"], 3))
    return {
        "as_of": day,
        "overall_score": overall,
        # 真实问题总数（Σ 各检查命中行数），与 top_issues 的 20 条截断无关
        "issue_count": issue_count,
        "domains": results,
        "top_issues": top[:20],
    }


def dq_summary(as_of: str = ""):
    """只读 Agent 工具：数据质量巡检摘要（总分/各域分数/前 10 条分级问题）。"""
    from ...database import SessionLocal

    day = as_of if (as_of and AS_OF_RE.match(as_of)) else str(date.today())
    with SessionLocal() as db:
        profile = build_profile(db, as_of=day)
    domains = {}
    for key in _DOMAIN_LABELS:
        r = profile["domains"][key]
        if not r.get("available"):
            domains[key] = {"available": False, "gap_reasons": r.get("gap_reasons", [])}
            continue
        cnt = {"error": 0, "warning": 0, "info": 0}
        for it in r.get("issues", []):
            cnt[it["level"]] = cnt.get(it["level"], 0) + 1
        domains[key] = {
            "available": True, "score": r["score"],
            "error": cnt["error"], "warning": cnt["warning"], "info": cnt["info"],
            "top_issues": r.get("issues", [])[:3],
        }
    return {"as_of": profile["as_of"], "overall_score": profile["overall_score"],
            "issue_count": profile["issue_count"], "domains": domains}


