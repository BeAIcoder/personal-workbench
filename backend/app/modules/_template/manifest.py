"""模块清单模板：把 finance_example 全部替换为你的模块名。

要点：
- 必须先 `from . import models`：导入即把模块表注册进 Base.metadata，
  宿主 lifespan 的 create_all 才能建出模块表（注册器在 lifespan 之前加载本文件）；
- agent_tools 元素与宿主 TOOL_SPECS 同构（func/params/required），外加 readonly 标记；
  readonly=True 的工具在 Agent 只读模式下也可用（由编排层按此标记放行）；
- column_patches：给本模块的已有表补列时声明 {表名: {列名: DDL}}，宿主启动时合并执行。
"""
from ..spec import ModuleManifest
from . import models  # noqa: F401  导入即注册模块表，勿删
from .service import example_ping

manifest = ModuleManifest(
    name="finance_example",                # ← 改成目录名，二者必须一致
    title="模块显示名",
    version="0.1.0",
    prefix="/api/finance/example",         # ← 必须 /api/finance/ 开头
    tables=["mod_example_entry"],          # ← 模块全部表名（mod_ 前缀）
    menu={"group": "财务", "icon": "Coin", "order": 100},
    agent_tools=[
        {
            "name": "example_ping",
            "func": example_ping,
            "description": "工具的一句话说明（Agent 按此决定是否调用）",
            "params": {},                  # {参数名: (JSON类型, 中文说明, 是否必填)}
            "required": [],
            "readonly": True,              # 只读工具标记
        }
    ],
    column_patches={},                     # 如 {"mod_example_entry": {"new_col": "TEXT DEFAULT ''"}}
    requires=[],                           # 依赖的其它模块 name
    enabled=True,
)
