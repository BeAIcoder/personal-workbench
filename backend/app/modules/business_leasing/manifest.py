# -*- coding: utf-8 -*-
"""business_leasing 模块清单：招商管理（铺位 / 线索 / 跟进 / 漏斗）。"""
from ..spec import ModuleManifest
from . import models  # noqa: F401  导入即把模块表注册进 Base.metadata
from .service import leasing_summary

manifest = ModuleManifest(
    name="business_leasing",
    title="招商管理",
    version="0.1.0",
    prefix="/api/business/leasing",
    agent_tools=[
        {
            "name": "leasing_summary",
            "func": leasing_summary,
            "description": "招商看板摘要：铺位总数/空置率/各状态数量 + 线索漏斗（只读）",
            "params": {},
            "required": [],
            "readonly": True,
        }
    ],
    column_patches={
        "mod_leasing_unit": {"opened_at": "DATE"},
        "mod_leasing_lead": {
            "unit_id": "INTEGER",
            "rent_form": "TEXT",
            "free_rent_months": "INTEGER",
            "escalation_rate": "FLOAT",
            "fitout_subsidy": "FLOAT",
            "lease_term_months": "INTEGER",
            "released_at": "DATETIME",
        },
    },
    requires=[],
    preset_experts=["leasing"],
    enabled=True,
)
