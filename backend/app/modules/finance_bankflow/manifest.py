"""finance_bankflow 模块清单：银行流水 → BIP 导入模板转换（纯转换工具，零新表）。"""
from ..spec import ModuleManifest

manifest = ModuleManifest(
    name="finance_bankflow",
    title="银行流水模板",
    version="0.1.0",
    prefix="/api/finance/bankflow",
    tables=[],
    menu={"group": "财务", "subgroup": "账务处理", "title": "银行流水模板", "icon": "CreditCard", "order": 95},
    agent_tools=[],
    column_patches={},
    requires=[],
    preset_experts=[],
)