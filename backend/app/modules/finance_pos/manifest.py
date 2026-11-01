"""finance_pos 模块清单：建行 POS 收款对账（L1 任务型薄壳）。"""
from ..spec import ModuleManifest
from . import models  # noqa: F401  导入即把模块表注册进 Base.metadata（须先于 lifespan 的 create_all）
from .service import pos_summary_tool

manifest = ModuleManifest(
    name="finance_pos",
    title="POS 对账",
    version="0.1.0",
    prefix="/api/finance/pos",
    tables=["mod_pos_run"],
    menu={"group": "财务", "icon": "CreditCard", "order": 90},
    agent_tools=[
        {
            "name": "pos_summary",
            "func": pos_summary_tool,
            "description": "POS 对账运行摘要：运行次数、成功次数、最近一次运行状态与产物数",
            "params": {},
            "required": [],
            "readonly": True,
        }
    ],
    preset_experts=["finance_specialist"],  # 工具自动预置进财税专员白名单
    column_patches={},
    requires=[],
    enabled=True,
)
