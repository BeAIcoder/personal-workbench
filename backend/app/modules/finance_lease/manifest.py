"""finance_lease 模块清单：租赁核算（承租人使用权资产 / 租赁负债）。

菜单唯一源是前端 src/modules/finance-lease/manifest.js；表清单以 models.py 的
__tablename__（mod_lease_* 前缀）为准。本模块只提供只读摘要工具 lease_summary。

requires 声明 finance_ledger_bip：BIP 只读对账只经该模块的公共只读适配函数读取
科目余额（bip_balance.read_balance_rows / list_balance_scopes），不 import 其 models、
不写任何 BIP 数据。
"""
from ..spec import ModuleManifest
from . import models  # noqa: F401  导入即把模块表注册进 Base.metadata（须先于 lifespan 的 create_all）
from .tools import lease_summary_tool

manifest = ModuleManifest(
    name="finance_lease",
    title="租赁核算",
    version="0.1.0",
    prefix="/api/finance/lease",
    agent_tools=[
        {
            "name": "lease_summary",
            "func": lease_summary_tool,
            "description": "租赁核算摘要：合同状态、最近测算、校验计数与待处理差异（只读）",
            "params": {
                "contract_id": ("integer", "可选：只查看指定合同；省略时返回全部合同摘要"),
            },
            "required": [],
            "readonly": True,
        }
    ],
    column_patches={},
    requires=["finance_ledger_bip"],
    preset_experts=["finance_specialist"],
    enabled=True,
)
