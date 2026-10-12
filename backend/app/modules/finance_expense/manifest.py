"""finance_expense 模块清单：费用分类汇总（L0 直挂费用分类汇总系统-v2.0 引擎）。"""
from ..spec import ModuleManifest
from . import models  # noqa: F401  导入即把模块表注册进 Base.metadata（须先于 lifespan 的 create_all）
from .service import expense_runs_summary

manifest = ModuleManifest(
    name="finance_expense",
    title="费用分类",
    version="0.1.0",
    prefix="/api/finance/expense",
    tables=["mod_expense_run", "mod_expense_row"],
    menu={"group": "财务", "icon": "Histogram", "order": 10},
    agent_tools=[
        {
            "name": "expense_runs_summary",
            "func": expense_runs_summary,
            "description": "费用分类模块最近运行统计：返回最近 N 次分类执行的行数/命中数/命中率/状态（只读）",
            "params": {"limit": ("integer", "最多返回最近几次运行，默认5，上限20", False)},
            "required": [],
            "readonly": True,
        }
    ],
    column_patches={},
    requires=[],
    enabled=True,
)
