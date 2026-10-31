# -*- coding: utf-8 -*-
"""business_operations 模块清单：营运管理（合同 / 账单收缴 / 工单）。"""
from ..spec import ModuleManifest
from . import models  # noqa: F401
from .service import ops_summary_tool

manifest = ModuleManifest(
    name="business_operations",
    title="营运管理",
    version="0.1.0",
    prefix="/api/business/operations",
    tables=[
        "mod_ops_contract", "mod_ops_bill", "mod_ops_work_order",
        "mod_ops_work_order_followup",
    ],
    menu={"group": "经营", "icon": "DataLine", "order": 72},
    agent_tools=[
        {
            "name": "ops_summary",
            "func": ops_summary_tool,
            "description": "营运看板摘要：履约合同数、应收/实收/收缴率/欠费、到期合同与未结工单（只读，可按期间筛选账单）",
            "params": {
                "period": ("string", "账单所属期 YYYY-MM，默认全部期间", False),
            },
            "required": [],
            "readonly": True,
        }
    ],
    column_patches={
        "mod_ops_work_order": {
            "due_at": "DATETIME", "parent_id": "INTEGER", "equipment_id": "INTEGER",
        },
    },
    requires=[],
    preset_experts=["operations"],
    enabled=True,
)
