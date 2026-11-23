# -*- coding: utf-8 -*-
"""business_facility 模块清单：工程物业（设备 / 合规到期）。"""
from ..spec import ModuleManifest
from . import models  # noqa: F401
from .service import facility_summary_tool

manifest = ModuleManifest(
    name="business_facility",
    title="工程物业",
    version="0.1.0",
    prefix="/api/business/facility",
    agent_tools=[
        {
            "name": "facility_summary",
            "func": facility_summary_tool,
            "description": "工程物业摘要：设备总数/ABC 等级、30 天到期、逾期与楼宇级合规事项（只读）",
            "params": {},
            "required": [],
            "readonly": True,
        }
    ],
    column_patches={},
    requires=[],
    preset_experts=["property"],
    enabled=True,
)
