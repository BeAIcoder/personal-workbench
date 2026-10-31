# -*- coding: utf-8 -*-
"""经营聚合统一指标字典；仅登记有真实只读来源的指标。"""


def _metric(label, formula, grain, window, source, population, caveats):
    return {
        "label": label,
        "formula": formula,
        "grain": grain,
        "window": window,
        "source": source,
        "population": population,
        "caveats": caveats,
    }


METRIC_DICTIONARY = {
    "leasing.vacancy_rate": _metric(
        "空置率", "空置铺位面积 / 铺位总面积", "项目汇总", "当前快照",
        "business_leasing.service.unit_summary", "全部铺位台账",
        ["总面积为 0 时来源 summary 返回 0"],
    ),
    "leasing.leasing_rate": _metric(
        "出租率", "(已定 + 已租铺位面积) / 铺位总面积", "项目汇总", "当前快照",
        "business_leasing.service.unit_summary", "全部铺位台账",
        ["总面积为 0 时来源 summary 返回 0"],
    ),
    "leasing.opening_rate": _metric(
        "开业率", "已开业面积 / 铺位总面积", "项目汇总", "当前快照",
        "business_leasing.service.unit_summary", "已租且开业日期不晚于当前日期的铺位",
        ["总面积为 0 时来源 summary 返回 0"],
    ),
    "leasing.pipeline_area": _metric(
        "在谈面积", "在谈面积 + 已定面积", "项目汇总", "当前快照",
        "business_leasing.service.unit_summary", "在谈与已定铺位", [],
    ),
    "leasing.conversion_rate": _metric(
        "线索签约转化率", "已签约线索数 / 全部线索数", "项目汇总", "当前快照",
        "business_leasing.service.funnel", "全部招商线索",
        ["无线索时来源 funnel 返回 0"],
    ),
    "leasing.due_followups": _metric(
        "到期跟进数", "下次跟进日期不晚于当前日期的未闭环线索数", "项目汇总", "当前快照",
        "business_leasing.service.leads_due_count", "未签约且未流失线索", [],
    ),
    "operations.receivable": _metric(
        "应收金额", "所选期间账单应收合计", "项目期间汇总", "所选月份",
        "business_operations.service.ops_summary", "所选期间全部账单", [],
    ),
    "operations.received": _metric(
        "实收金额", "所选期间账单已收合计", "项目期间汇总", "所选月份",
        "business_operations.service.ops_summary", "所选期间全部账单", [],
    ),
    "operations.collection_rate": _metric(
        "收缴率", "实收金额 / 应收金额", "项目期间汇总", "所选月份",
        "business_operations.service.ops_summary", "所选期间全部账单",
        ["无应收时来源 summary 返回 0，不代表全部收清"],
    ),
    "operations.arrears": _metric(
        "欠费金额", "应收金额 - 实收金额", "项目期间汇总", "所选月份",
        "business_operations.service.ops_summary", "所选期间全部账单", [],
    ),
    "operations.arrears_contracts": _metric(
        "欠费合同数", "所选期间欠费大于 0 的去重合同数", "项目期间汇总", "所选月份",
        "business_operations.service.ops_summary", "所选期间全部账单", [],
    ),
    "operations.expiry_12m_area_ratio": _metric(
        "12 个月到期面积占比", "as_of 起 12 个月内到期履约合同面积 / 全部履约合同面积",
        "项目快照", "as_of（含）至 as_of 后 12 个月同日（含）",
        "business_gm.service.expiry_concentration + business_operations OpsContract",
        "status=active 的全部营运合同；分母包含已过期但仍为 active 的合同",
        ["履约合同总面积为 0 时返回 null；占比严格大于 25% 才提醒"],
    ),
    "planning.budget_total": _metric(
        "活动预算", "所选年度活动预算合计", "项目年度汇总", "period 派生年度",
        "business_planning.service.summary", "所选年度全部活动", [],
    ),
    "planning.actual_total": _metric(
        "活动实际投入", "所选年度活动实际投入合计", "项目年度汇总", "period 派生年度",
        "business_planning.service.summary", "所选年度全部活动", [],
    ),
    "planning.variance": _metric(
        "预算差异", "预算合计 - 实际投入合计", "项目年度汇总", "period 派生年度",
        "business_planning.service.summary", "所选年度全部活动", [],
    ),
    "planning.over_budget_count": _metric(
        "超支活动数", "实际投入大于预算的活动数", "项目年度汇总", "period 派生年度",
        "business_planning.service.summary", "所选年度全部活动", [],
    ),
    "planning.avg_roi": _metric(
        "平均 ROI", "统一 calculated 口径且 ROI 非空的活动 ROI 算术平均",
        "项目年度汇总", "period 派生年度", "business_planning.service.summary",
        "销售基准、期间销售、商户分摊齐全且总投入非 0 的活动",
        ["手填 ROI 不参与平均；无同口径样本时为 null"],
    ),
    "legal.pending_reviews": _metric(
        "待审合同数", "待审 + 审查中合同审查记录数", "项目汇总", "当前快照",
        "business_legal.service.legal_summary", "全部合同审查记录", [],
    ),
    "legal.high_risk": _metric(
        "高风险审查数", "风险等级为 high 的合同审查记录数", "项目汇总", "当前快照",
        "business_legal.service.legal_summary", "全部合同审查记录", [],
    ),
    "legal.open_cases": _metric(
        "未结案件数", "阶段不为 closed 的案件数", "项目汇总", "当前快照",
        "business_legal.service.legal_summary", "全部案件台账", [],
    ),
    "legal.hearings_7": _metric(
        "7 天内开庭数", "当前日期起 7 天内开庭的未结案件数", "项目汇总", "当前快照",
        "business_legal.service.legal_summary", "有开庭日期且未结案的案件", [],
    ),
    "legal.overdue_hearings": _metric(
        "逾期开庭数", "开庭日期早于当前日期的未结案件数", "项目汇总", "当前快照",
        "business_legal.service.legal_summary", "有开庭日期且未结案的案件", [],
    ),
    "hr.headcount": _metric(
        "在职人数", "在职 + 试用员工数", "项目汇总", "当前快照",
        "business_hr.service.hr_summary", "全部员工花名册", [],
    ),
    "hr.expiring_30": _metric(
        "30 天到期人数", "30 天内合同或试用到期的在职/试用员工数", "项目汇总", "当前快照",
        "business_hr.service.hr_summary", "非离职员工", [],
    ),
    "hr.payroll_net": _metric(
        "实发薪资", "所选月份薪资单实发合计", "项目期间汇总", "所选月份",
        "business_hr.service.hr_summary", "所选月份全部薪资单",
        ["需结合 payroll_count 判断是否为无薪资单"],
    ),
    "hr.payroll_count": _metric(
        "薪资单数", "所选月份薪资单条数", "项目期间汇总", "所选月份",
        "business_hr.service.hr_summary", "所选月份全部薪资单", [],
    ),
    "hr.payroll_draft": _metric(
        "草稿薪资单数", "所选月份 status=draft 的薪资单数", "项目期间汇总", "所选月份",
        "business_hr.service.hr_summary", "所选月份全部薪资单", [],
    ),
    "facility.equipment_count": _metric(
        "设备总数", "设备台账记录数", "项目汇总", "当前快照",
        "business_facility.service.facility_summary", "全部设备台账", [],
    ),
    "facility.category_a": _metric(
        "A类关键设备数", "category=A 的设备数", "项目汇总", "当前快照",
        "business_facility.service.facility_summary", "全部设备台账", [],
    ),
    "facility.compliance_30": _metric(
        "30天内到期合规", "未完成且到期日在 as_of 起 30 天内的合规事项数",
        "项目汇总", "as_of 快照", "business_facility.service.facility_summary",
        "全部合规到期台账", ["逾期事项单独计数，不并入本指标"],
    ),
    "facility.overdue": _metric(
        "逾期合规", "未完成且到期日早于 as_of 的合规事项数", "项目汇总", "as_of 快照",
        "business_facility.service.facility_summary", "全部合规到期台账", [],
    ),
}

METRIC_SOURCES = {
    "leasing.vacancy_rate": ("leasing", "units.vacancy_rate"),
    "leasing.leasing_rate": ("leasing", "units.leasing_rate"),
    "leasing.opening_rate": ("leasing", "units.opening_rate"),
    "leasing.pipeline_area": ("leasing", "units.pipeline_area"),
    "leasing.conversion_rate": ("leasing", "funnel.conversion_rate"),
    "leasing.due_followups": ("leasing", "due_followups"),
    "operations.receivable": ("operations", "receivable"),
    "operations.received": ("operations", "received"),
    "operations.collection_rate": ("operations", "collection_rate"),
    "operations.arrears": ("operations", "arrears"),
    "operations.arrears_contracts": ("operations", "arrears_contracts"),
    "operations.expiry_12m_area_ratio": ("operations", "expiry_12m.area_ratio"),
    "planning.budget_total": ("planning", "budget_total"),
    "planning.actual_total": ("planning", "actual_total"),
    "planning.variance": ("planning", "variance"),
    "planning.over_budget_count": ("planning", "over_budget_count"),
    "planning.avg_roi": ("planning", "avg_roi"),
    "legal.pending_reviews": ("legal", "pending_reviews"),
    "legal.high_risk": ("legal", "high_risk"),
    "legal.open_cases": ("legal", "open_cases"),
    "legal.hearings_7": ("legal", "hearings_7"),
    "legal.overdue_hearings": ("legal", "overdue_hearings"),
    "hr.headcount": ("hr", "headcount"),
    "hr.expiring_30": ("hr", "expiring_30"),
    "hr.payroll_net": ("hr", "payroll_net"),
    "hr.payroll_count": ("hr", "payroll_count"),
    "hr.payroll_draft": ("hr", "payroll_draft"),
    "facility.equipment_count": ("facility", "equipment_count"),
    "facility.category_a": ("facility", "category_a"),
    "facility.compliance_30": ("facility", "compliance_30"),
    "facility.overdue": ("facility", "overdue"),
}

MISSING_SOURCE_METRICS = (
    {
        "key": "operations.noi",
        "domain": "operations",
        "label": "净营业收入（NOI）",
        "value": None,
        "reason": "缺少同源营业收入与经营成本明细，当前无法计算 NOI",
    },
    {
        "key": "operations.sales_efficiency",
        "domain": "operations",
        "label": "销售坪效",
        "value": None,
        "reason": "缺少同源销售额与计效营业面积，当前无法计算销售坪效",
    },
)
