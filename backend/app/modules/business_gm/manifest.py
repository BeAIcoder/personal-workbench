# -*- coding: utf-8 -*-
"""business_gm 模块清单：总经理只读经营聚合（无自有数据表）。"""
from ..spec import ModuleManifest
from .service import gm_summary

manifest = ModuleManifest(
    name="business_gm",
    title="总经理经营聚合",
    version="0.1.0",
    prefix="/api/business/gm",
    tables=[],
    menu={"group": "经营", "icon": "DataAnalysis", "order": 70},
    agent_tools=[
        {
            "name": "gm_summary",
            "func": gm_summary,
            "description": "总经理只读经营聚合：招商/营运/企划/法务/人事/工程可用指标、数据缺口与提醒",
            "params": {
                "period": ("string", "所属期 YYYY-MM，默认当前月；企划按该期间年度汇总", False),
            },
            "required": [],
            "readonly": True,
        }
    ],
    column_patches={},
    requires=[],
    preset_experts=["realestate"],
    enabled=True,
)
