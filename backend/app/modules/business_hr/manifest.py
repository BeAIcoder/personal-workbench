# -*- coding: utf-8 -*-
"""business_hr 模块清单：行政人事（员工 / 考勤 / 薪资）。"""
from ..spec import ModuleManifest
from . import models  # noqa: F401
from .service import hr_summary_tool

manifest = ModuleManifest(
    name="business_hr",
    title="行政人事",
    version="0.1.0",
    prefix="/api/business/hr",
    tables=["mod_hr_employee", "mod_hr_attendance", "mod_hr_payroll"],
    menu={"group": "职能", "icon": "User", "order": 82},
    agent_tools=[
        {
            "name": "hr_summary",
            "func": hr_summary_tool,
            "description": "人事看板摘要：在职/试用/30 天到期人数，可按月份读取薪资汇总（只读）",
            "params": {
                "month": ("string", "薪资所属月 YYYY-MM，默认不汇总薪资", False),
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
