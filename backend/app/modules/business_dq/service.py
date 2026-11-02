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

__all__ = ["build_profile", "dq_summary"]


def _issue(level, check, count, sample, suggestion):
    return {"level": level, "check": check, "count": count, "sample": [str(s) for s in sample], "suggestion": suggestion}


def _exists(db, name):
    row = db.execute(
        text("SELECT 1 FROM sqlite_master WHERE type='table' AND name=:t"), {"t": name},
    ).first()
    return row is not None


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
    failed_total = 0
    for tname in ("mod_vat_filing", "mod_dsf_filing", "mod_iit_filing", "mod_stamp_duty_filing"):
        if _exists(db, tname):
            present.append(tname)
            failed_total += int(db.execute(text(
                "SELECT COUNT(*) FROM " + tname + " WHERE status = 'failed'"
            )).scalar() or 0)
    if failed_total:
        issues.append(_issue("warning", "运行状态", failed_total, [], "存在 failed 状态的税种申报记录；建议到对应税种页面复核计算与导出"))
    # 税务域勾稽（2026-11-02 v4）：申报表 vs 财务数据口径
    if _exists(db, "mod_tax_return") and _exists(db, "mod_tax_financial_data"):
        present.append("tax_returns")
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
                samples = [t["table"] for t in vq["tables"] if t["dup_rows"] > 0][:5]
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
        # 连续失败（2026-11-02 v4）：最近两条快照均 failed → error（告警会同步登记问题记录）
        try:
            from ..finance_board.models import BoardSnapshot
            latest_two = db.query(BoardSnapshot).order_by(BoardSnapshot.id.desc()).limit(2).all()
            if len(latest_two) == 2 and all(s.status == "failed" for s in latest_two):
                issues.append(_issue("error", "连续失败", 2, [s.stamp for s in latest_two], "看板管线连续两次运行失败；已登记问题记录，建议检查管线脚本与日志"))
        except Exception:
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
    if as_of and not AS_OF_RE.match(as_of):
        raise ValueError("as_of 需为 YYYY-MM-DD 格式")
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
        if r.get("available"):
            scores.append(r["score"])
    overall = round(sum(scores) / len(scores)) if scores else None
    top = []
    for key in _DOMAIN_LABELS:
        for it in results[key].get("issues", []):
            top.append({"domain": key, **it})
    top.sort(key=lambda x: {"error": 0, "warning": 1, "info": 2}.get(x["level"], 3))
    return {"as_of": day, "overall_score": overall, "domains": results, "top_issues": top[:20]}


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
    return {"as_of": profile["as_of"], "overall_score": profile["overall_score"], "domains": domains}


