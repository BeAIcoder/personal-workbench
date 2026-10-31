# -*- coding: utf-8 -*-
"""business_legal 模块清单：法务管理（合同审查 / 案件 / 用印）。"""
from ..spec import ModuleManifest
from . import models  # noqa: F401
from .service import legal_summary_tool

manifest = ModuleManifest(
    name="business_legal",
    title="法务管理",
    version="0.1.0",
    prefix="/api/business/legal",
    tables=["mod_legal_contract_review", "mod_legal_case", "mod_legal_seal"],
    menu={"group": "职能", "icon": "Document", "order": 81},
    agent_tools=[
        {
            "name": "legal_summary",
            "func": legal_summary_tool,
            "description": "法务看板摘要：待审合同/高风险/30 天到期/7 天内开庭/未结案件（只读）",
            "params": {},
            "required": [],
            "readonly": True,
        }
    ],
    column_patches={},
    requires=[],
    preset_experts=["realestate"],
    enabled=True,
)
