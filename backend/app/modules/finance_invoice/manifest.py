"""finance_invoice 模块清单：发票处理（L0 直挂发票工具引擎）。"""
from ..spec import ModuleManifest
from . import models  # noqa: F401  导入即把模块表注册进 Base.metadata（须先于 lifespan 的 create_all）
from .service import invoice_batch_stats

manifest = ModuleManifest(
    name="finance_invoice",
    title="发票处理",
    version="0.1.0",
    prefix="/api/finance/invoice",
    tables=["mod_invoice_batch", "mod_invoice_item"],
    menu={"group": "财务", "icon": "Tickets", "order": 20},
    agent_tools=[
        {
            "name": "invoice_batch_stats",
            "func": invoice_batch_stats,
            "description": "发票处理模块统计概览：批次数、各状态批次分布、发票明细数、重复/失败/冲红配对统计与净金额",
            "params": {},
            "required": [],
            "readonly": True,
        }
    ],
    column_patches={},
    requires=[],
    enabled=True,
)
