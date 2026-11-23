"""finance_deferred 模块清单：长期待摊（仅租入资产改良支出）。

菜单唯一源是前端 src/modules/finance-deferred/manifest.js；表清单以 models.py 的
__tablename__（mod_deferred_* 前缀）为准。本模块只提供只读摘要工具 deferred_summary。
"""
from ..spec import ModuleManifest
from . import models  # noqa: F401  导入即把模块表注册进 Base.metadata（须先于 lifespan 的 create_all）
from .tools import deferred_summary_tool

manifest = ModuleManifest(
    name="finance_deferred",
    title="长期待摊",
    version="0.1.0",
    prefix="/api/finance/deferred",
    agent_tools=[
        {
            "name": "deferred_summary",
            "func": deferred_summary_tool,
            "description": "长期待摊摘要：项目状态、最近测算、摊销截止与校验计数（只读）",
            "params": {
                "item_id": ("integer", "可选：只查看指定项目；省略时返回全部项目摘要"),
            },
            "required": [],
            "readonly": True,
        }
    ],
    column_patches={},
    requires=[],
    preset_experts=["finance_specialist"],
    enabled=True,
)
