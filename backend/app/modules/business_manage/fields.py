# -*- coding: utf-8 -*-
"""经营管理报表的字段规格（单一来源）。

同一份规格驱动三处：
- `/meta` 返回给前端做动态表单；
- 手工填报时的 section / metric_key 校验；
- 导入解析器写入时的字段白名单。

metric_key 约定 `<域>.<实体>.<口径>.<字段>`，域前缀决定它属于哪类报表：
- control：`revenue.` 项目收入、`leasing.` 招商完成；
- weekly：`weekly.` 周报指标（租赁/空铺/招商/续签/收缴/竞品/维修基金）；
- monthly：`monthly.` 月报指标（项目收缴/出租/签约/招商）。
"""
from __future__ import annotations

REPORT_TYPES = (
    {
        "value": "control",
        "label": "内控管理",
        "projects": ["云南区域"],
        "tabs": [
            {"key": "key_work", "label": "重点工作"},
            {"key": "kpi", "label": "责任书"},
            {"key": "revenue", "label": "收入"},
            {"key": "leasing", "label": "招商"},
            {"key": "litigation", "label": "诉讼"},
            {"key": "board_item", "label": "总办会"},
        ],
    },
    {
        "value": "weekly",
        "label": "项目周报",
        "projects": ["云南项目", "曲靖项目"],
        "tabs": [
            {"key": "weekly", "label": "招商与经营"},
            {"key": "warning_tenant", "label": "预警商户"},
            {"key": "brand_promo", "label": "品牌宣发"},
            {"key": "renovation", "label": "品质改造"},
        ],
    },
    {
        "value": "monthly",
        "label": "月度工作情况",
        "projects": ["云南区域"],
        "tabs": [
            {"key": "issues", "label": "问题线索"},
            {"key": "key_work", "label": "重点工作"},
            {"key": "team", "label": "队伍建设"},
            {"key": "highlights", "label": "亮点工作"},
        ],
    },
)

RECORD_SPECS = {
    "key_work": {
        "label": "重点工作周进度",
        "fields": [
            {"name": "name", "label": "工作内容", "type": "text"},
            {"name": "category", "label": "项目/部门", "type": "text"},
            {"name": "content", "label": "策略/待决内容", "type": "textarea"},
            {"name": "progress", "label": "本周进度", "type": "textarea"},
            {"name": "prev_progress", "label": "上周进度", "type": "textarea", "payload": True},
            {"name": "due_on", "label": "预计完成", "type": "date"},
            {"name": "owner", "label": "主责人", "type": "text"},
            {"name": "co_owner", "label": "协办人", "type": "text"},
        ],
    },
    "kpi": {
        "label": "年度经营业绩责任书",
        "fields": [
            {"name": "name", "label": "指标名称", "type": "text"},
            {"name": "category", "label": "指标类型", "type": "text"},
            {"name": "weight", "label": "考核权重", "type": "text", "payload": True},
            {"name": "score_range", "label": "分值区间", "type": "text", "payload": True},
            {"name": "target_text", "label": "年度目标值", "type": "text", "payload": True},
            {"name": "amount", "label": "完成情况", "type": "number"},
            {"name": "rate", "label": "完成率", "type": "number"},
            {"name": "issue", "label": "难点及需支持", "type": "textarea", "payload": True},
            {"name": "due_on", "label": "预计完成", "type": "date"},
            {"name": "owner", "label": "主责人", "type": "text"},
        ],
    },
    "litigation": {
        "label": "诉讼案件进度",
        "fields": [
            {"name": "name", "label": "商户品牌", "type": "text"},
            {"name": "category", "label": "纠纷类型", "type": "text"},
            {"name": "stage", "label": "当前阶段", "type": "text"},
            {"name": "amount", "label": "欠费金额", "type": "number"},
            {"name": "amount2", "label": "诉讼金额", "type": "number"},
            {"name": "recovery", "label": "回款总额", "type": "number"},
            {"name": "balance", "label": "还欠金额", "type": "number"},
            {"name": "occurred_on", "label": "起诉日期", "type": "date"},
            {"name": "due_on", "label": "判决日期", "type": "date"},
            {"name": "owner", "label": "代理律师", "type": "text"},
            {"name": "content", "label": "诉讼请求", "type": "textarea"},
            {"name": "progress", "label": "进度计划", "type": "textarea"},
            {"name": "note", "label": "执行情况说明", "type": "textarea"},
        ],
    },
    "board_item": {
        "label": "拟报总办会决策事项",
        "fields": [
            {"name": "name", "label": "待上会决策事项", "type": "text"},
            {"name": "content", "label": "内部初步决策", "type": "textarea"},
            {"name": "progress", "label": "上会准备进度", "type": "textarea"},
            {"name": "next_plan", "label": "后续推进方案", "type": "textarea", "payload": True},
            {"name": "due_on", "label": "预计完成", "type": "date"},
            {"name": "owner", "label": "主责人", "type": "text"},
            {"name": "co_owner", "label": "协办人", "type": "text"},
        ],
    },
    "warning_tenant": {
        "label": "预警商户",
        "fields": [
            {"name": "category", "label": "业态", "type": "text"},
            {"name": "name", "label": "品牌名称", "type": "text"},
            {"name": "area", "label": "面积", "type": "number"},
            {"name": "content", "label": "预警原因", "type": "text"},
            {"name": "note", "label": "解决措施", "type": "textarea"},
            {"name": "progress", "label": "本周进度", "type": "textarea"},
        ],
    },
    "brand_promo": {
        "label": "品牌宣发",
        "fields": [
            {"name": "name", "label": "品牌", "type": "text"},
            {"name": "category", "label": "类型", "type": "text"},
            {"name": "occurred_on", "label": "开业时间", "type": "date"},
            {"name": "amount", "label": "月均销售", "type": "number"},
            {"name": "pre", "label": "预热期", "type": "textarea", "payload": True},
            {"name": "open", "label": "开业期", "type": "textarea", "payload": True},
            {"name": "re", "label": "复推期", "type": "textarea", "payload": True},
            {"name": "times", "label": "6个月宣发次数", "type": "text", "payload": True},
            {"name": "channels", "label": "渠道及形式", "type": "text", "payload": True},
            {"name": "last_date", "label": "最近宣发日期", "type": "date", "payload": True},
        ],
    },
}

RECORD_SECTIONS = {
    "control": ("key_work", "kpi", "litigation", "board_item"),
    "weekly": ("warning_tenant", "brand_promo"),
    "monthly": (),
}

TEXT_SPECS = {
    "control": [
        {"key": "revenue_note", "label": "收入备注说明", "subsection": "", "sort": 10},
    ],
    "weekly": [
        {"key": "progress", "label": "重点品牌招商进度", "subsection": "", "sort": 10},
        {"key": "gap_analysis", "label": "收缴缺口分析", "subsection": "", "sort": 20},
        {"key": "promo", "label": "品牌宣发", "subsection": "", "sort": 30},
        {"key": "renovation_progress", "label": "品质提升改造进度", "subsection": "", "sort": 40},
        {"key": "key_issues", "label": "本周重点及困难事项", "subsection": "", "sort": 50},
        {"key": "next_week", "label": "下周重点工作计划", "subsection": "", "sort": 60},
    ],
    "monthly": [
        {"key": "issues", "label": "问题线索处置及案件办理", "subsection": "", "sort": 10},
        {"key": "key_work", "label": "重点工作推进情况", "subsection": "", "sort": 20},
        {"key": "team", "label": "自身建设和队伍建设", "subsection": "", "sort": 30},
        {"key": "highlights", "label": "亮点工作", "subsection": "", "sort": 40},
    ],
}

METRIC_PREFIXES = {
    "control": ("revenue.", "leasing."),
    "weekly": ("weekly.",),
    "monthly": ("monthly.",),
}

# 指标目录（/meta 用；含占位符，仅作手工填报时的键名提示）
METRIC_CATALOG = {
    "control": [
        {"key": "revenue.<项目>.year.target.current", "label": "年度应收指标（当期）", "unit": "万元"},
        {"key": "revenue.<项目>.year.actual.current", "label": "年度已执行（当期）", "unit": "万元"},
        {"key": "revenue.<项目>.month.rate.current", "label": "当月完成率", "unit": ""},
        {"key": "leasing.year.区域合计.plan", "label": "年度计划招商面积", "unit": "㎡"},
        {"key": "leasing.year.区域合计.done_area", "label": "年度已完成面积", "unit": "㎡"},
    ],
    "weekly": [
        {"key": "weekly.lease.occupancy_rate", "label": "期末出租率", "unit": ""},
        {"key": "weekly.collection.rate", "label": "累计收缴率", "unit": ""},
        {"key": "weekly.leasing.ytd_new_area", "label": "年内新签面积", "unit": "㎡"},
        {"key": "weekly.vacancy.vacancy_rate", "label": "空铺率", "unit": ""},
    ],
    "monthly": [
        {"key": "monthly.<项目>.collection.rate", "label": "合同口径收缴率", "unit": ""},
        {"key": "monthly.<项目>.occupancy.rate", "label": "整体出租率", "unit": ""},
        {"key": "monthly.leasing.new_area", "label": "新签面积", "unit": "㎡"},
    ],
}

SECTION_LABELS = {
    "revenue": "项目收入",
    "leasing": "招商完成",
    "weekly.lease": "租赁指标",
    "weekly.vacancy": "空铺指标",
    "weekly.leasing": "招商指标",
    "weekly.renewal": "续签指标",
    "weekly.collection": "收缴指标",
    "weekly.competitor": "竞品对比",
    "weekly.fund": "维修基金",
    "monthly.collection": "收缴指标",
    "monthly.occupancy": "出租指标",
    "monthly.signing": "签约指标",
    "monthly.leasing": "招商指标",
    "revenue_note": "收入备注",
}

RECORD_NUMERIC_FIELDS = ("area", "amount", "amount2", "recovery", "balance", "rate")
RECORD_DATE_FIELDS = ("occurred_on", "due_on")
RECORD_TEXT_FIELDS = (
    "name", "category", "stage", "status", "owner", "co_owner",
    "content", "progress", "note",
)


def meta() -> dict:
    """前端动态表单所需的完整字段字典。"""
    sections = {}
    for item in REPORT_TYPES:
        value = item["value"]
        sections[value] = {
            "records": [
                {
                    "key": key,
                    "label": RECORD_SPECS[key]["label"],
                    "fields": RECORD_SPECS[key]["fields"],
                }
                for key in RECORD_SECTIONS.get(value, ())
            ],
            "texts": TEXT_SPECS.get(value, []),
            "metrics": [],
            "metric_catalog": METRIC_CATALOG.get(value, []),
        }
    return {"report_types": [dict(item) for item in REPORT_TYPES], "sections": sections}


def record_fields(section: str) -> set[str]:
    spec = RECORD_SPECS.get(section)
    if not spec:
        return set()
    return {field["name"] for field in spec["fields"]}


def payload_fields(section: str) -> set[str]:
    spec = RECORD_SPECS.get(section)
    if not spec:
        return set()
    return {field["name"] for field in spec["fields"] if field.get("payload")}


def text_keys(report_type: str) -> set[str]:
    return {item["key"] for item in TEXT_SPECS.get(report_type, [])}


def metric_section(report_type: str, metric_key: str) -> str:
    """把 metric_key 归到 section（图表与前端分组用）。"""
    if metric_key.startswith("revenue."):
        return "revenue"
    if metric_key.startswith("leasing."):
        return "leasing"
    if metric_key.startswith("weekly.collection."):
        return "weekly.collection"
    if metric_key.startswith("weekly.lease."):
        return "weekly.lease"
    if metric_key.startswith("weekly.vacancy."):
        return "weekly.vacancy"
    if metric_key.startswith("weekly.leasing."):
        return "weekly.leasing"
    if metric_key.startswith("weekly.renewal."):
        return "weekly.renewal"
    if metric_key.startswith("weekly.competitor."):
        return "weekly.competitor"
    if metric_key.startswith("weekly.fund."):
        return "weekly.fund"
    if metric_key.startswith("monthly."):
        parts = metric_key.split(".")
        return "monthly." + (parts[2] if len(parts) > 2 else "other")
    return report_type


def metric_key_allowed(report_type: str, metric_key: str) -> bool:
    if not metric_key or len(metric_key) > 200:
        return False
    prefixes = METRIC_PREFIXES.get(report_type, ())
    return any(metric_key.startswith(prefix) for prefix in prefixes)
