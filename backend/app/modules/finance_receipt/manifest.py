"""finance_receipt 模块清单：银行回单整理（扫描、复核、按账户类别生成 PDF）。"""
from ..spec import ModuleManifest
from . import models  # noqa: F401  导入即把模块表注册进 Base.metadata（须先于 lifespan 的 create_all）
from .service import receipt_summary_tool

manifest = ModuleManifest(
    name="finance_receipt",
    title="银行回单整理",
    version="0.1.0",
    prefix="/api/finance/receipt",
    tables=["mod_receipt_batch", "mod_receipt_item"],
    menu={"group": "财务", "icon": "Document", "order": 96},
    agent_tools=[
        {
            "name": "receipt_summary",
            "func": receipt_summary_tool,
            "description": "银行回单整理摘要：批次数量与最近一次批次的五类分类笔数、待确认数",
            "params": {},
            "required": [],
            "readonly": True,
        }
    ],
    preset_experts=["finance_specialist"],
    column_patches={},
    requires=[],
    enabled=True,
)
