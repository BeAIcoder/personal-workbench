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
    tables=["mod_leasing_unit", "mod_leasing_lead", "mod_leasing_followup"],
    menu={"group": "经营", "icon": "Shop", "order": 71},
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
    column_patches={},
    requires=[],
    preset_experts=["leasing"],
    enabled=True,
)
