# -*- coding: utf-8 -*-
"""business_planning 模块清单：企划管理（活动 / 预算 / 复盘 / 供应商）。"""
from ..spec import ModuleManifest
from . import models  # noqa: F401
from .service import planning_summary_tool

manifest = ModuleManifest(
    name="business_planning",
    title="企划管理",
    version="0.1.0",
    prefix="/api/business/planning",
    tables=["mod_plan_campaign", "mod_plan_vendor"],
    menu={"group": "经营", "icon": "Promotion", "order": 73},
    agent_tools=[
        {
            "name": "planning_summary",
            "func": planning_summary_tool,
            "description": "企划看板摘要：年度预算/实际/超支场次/平均 ROI/各状态数量（只读）",
            "params": {},
            "required": [],
            "readonly": True,
        }
    ],
    column_patches={
        "mod_plan_campaign": {
            "grade": "VARCHAR(20)",
            "sales_baseline": "FLOAT",
            "sales_during": "FLOAT",
            "merchant_share": "FLOAT",
            "roi_target": "FLOAT",
        },
    },
    requires=[],
    preset_experts=["marketing"],
    enabled=True,
)
