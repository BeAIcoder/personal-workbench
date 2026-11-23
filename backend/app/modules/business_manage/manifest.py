# -*- coding: utf-8 -*-
"""business_manage 模块清单：经营管理（内控管理 / 项目周报 / 月度工作情况）。"""
from ..spec import ModuleManifest
from . import models  # noqa: F401
from .service import summary as manage_summary


def manage_summary_tool(project: str = "", period: str = "") -> dict:
    """经营管理摘要（只读）：取最新批次的出租率、收缴率、招商与诉讼口径。"""
    try:
        return {"ok": True, **manage_summary(project=project, period=period)}
    except ValueError as exc:
        return {"ok": False, "message": str(exc)}


manifest = ModuleManifest(
    name="business_manage",
    title="经营管理",
    version="0.1.0",
    prefix="/api/business/manage",
    agent_tools=[
        {
            "name": "manage_summary",
            "func": manage_summary_tool,
            "description": "经营管理报表摘要：最新批次的出租率、累计收缴率、新签面积、"
                           "在诉金额与预警商户数（只读，可按项目/期间筛选）",
            "params": {
                "project": ("string", "项目名，如 云南项目 / 曲靖项目；默认全部"),
                "period": ("string", "报表期间 YYYY-MM，默认最新批次"),
            },
            "required": [],
            "readonly": True,
        }
    ],
    requires=[],
    preset_experts=["operations"],
    enabled=True,
)
