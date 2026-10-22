"""finance_cashflow 模块清单：现金流滚动预测自动填报（L1 任务型薄壳）。

模块代码自 2026-10-22 起纳入 git 跟踪；V1 引擎目录与真实业务数据仍在仓外，永不推远端。
"""
from ..spec import ModuleManifest
from . import models  # noqa: F401  导入即把模块表注册进 Base.metadata（须先于 lifespan 的 create_all）

manifest = ModuleManifest(
    name="finance_cashflow",
    title="现金流填报",
    version="0.1.0",
    prefix="/api/finance/cashflow",
    tables=["mod_cashflow_run"],
    menu={"group": "财务", "icon": "TrendCharts", "order": 80},
    agent_tools=[],
    column_patches={},
    requires=[],
    enabled=True,
)
