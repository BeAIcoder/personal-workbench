"""finance_bankflow 模块清单：银行流水 → BIP 导入模板转换（纯转换工具，零新表）。"""
from ..spec import ModuleManifest

manifest = ModuleManifest(
    name="finance_bankflow",
    title="银行流水模板",
    version="0.1.0",
    prefix="/api/finance/bankflow",
    agent_tools=[],
    column_patches={},
    requires=[],
    preset_experts=[],
)