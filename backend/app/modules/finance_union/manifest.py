"""finance_union 模块清单：工会经费计提（手工工资基数版本）。

菜单唯一源是前端 src/modules/finance-union/manifest.js；表清单以 models.py 的
``__tablename__``（mod_union_* 前缀）为准。本模块只贡献一个只读摘要工具
``union_summary``，并预置给财务专家 finance_specialist。

首期不做 HR 取数、不含 60/40 分成、缴纳凭据、税前扣除判断，也不做支付/BIP 回写。
"""
from ..spec import ModuleManifest
from . import models  # noqa: F401  导入即把模块表注册进 Base.metadata（须先于 lifespan 的 create_all）
from .tools import union_summary_tool

manifest = ModuleManifest(
    name="finance_union",
    title="工会经费计提",
    version="0.1.0",
    prefix="/api/finance/union",
    agent_tools=[
        {
            "name": "union_summary",
            "func": union_summary_tool,
            "description": "工会经费计提摘要：按主体/期间返回批次状态计数与可计算行合计（只读）",
            "params": {
                "entity": ("string", "可选：按计提主体筛选（包含匹配）；省略时统计全部"),
                "period": ("string", "可选：按所属期间筛选，格式 YYYY-MM"),
            },
            "required": [],
            "readonly": True,
        }
    ],
    column_patches={},
    requires=[],
    preset_experts=["finance_specialist"],
    enabled=True,
)
