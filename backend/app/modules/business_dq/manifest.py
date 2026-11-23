# -*- coding: utf-8 -*-
"""business_dq 模块清单：跨模块数据质量巡检（只读、无自有表）。"""
from ..spec import ModuleManifest
from .service import dq_summary

manifest = ModuleManifest(
    name="business_dq",
    title="数据质量巡检",
    version="0.1.0",
    prefix="/api/business/dq",
    agent_tools=[
        {
            "name": "dq_summary",
            "func": dq_summary,
            "description": "跨模块数据质量巡检：空值/重复/孤儿/日期/金额五维 × 七域（招商/营运/人事/法务/企划/工程/财务），返回总分、各域分数与分级问题清单",
            "params": {
                "as_of": ("string", "截止日期 YYYY-MM-DD，默认今天；用于未来日期类检查的可复算口径"),
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
