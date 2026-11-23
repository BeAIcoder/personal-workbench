"""finance_cashflow 模块清单：现金流滚动预测自动填报（L1 任务型薄壳）。

模块代码自 2026-10-22 起纳入 git 跟踪；V1 引擎目录与真实业务数据仍在仓外，永不推远端。
"""
from ..spec import ModuleManifest
from . import models  # noqa: F401  导入即把模块表注册进 Base.metadata（须先于 lifespan 的 create_all）
from .service import cashflow_summary_tool

manifest = ModuleManifest(
    name="finance_cashflow",
    title="现金流填报",
    version="0.1.0",
    prefix="/api/finance/cashflow",
    agent_tools=[
        {
            "name": "cashflow_summary",
            "func": cashflow_summary_tool,
            "description": "现金流引擎运行摘要：运行次数、成功次数、最近一次 actual_month 与状态",
            "params": {},
            "required": [],
            "readonly": True,
        }
    ],
    preset_experts=["finance_specialist"],  # 工具自动预置进财税专员白名单
    # 历史库补列：运行记录年份（create_all 不改已有表；旧行默认 0，输出按创建年份兜底）
    column_patches={"mod_cashflow_run": {"actual_year": "INTEGER DEFAULT 0"}},
    requires=[],
    enabled=True,
)
