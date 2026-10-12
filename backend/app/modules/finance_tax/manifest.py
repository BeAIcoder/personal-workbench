"""finance_tax 模块清单：企业所得税填报（原「企业所得税计算」项目整体迁入）。

移植口径：models/services/config/routers 自原项目复制，仅做三类机械改写——
import 改相对引用、路由去掉 /api 前缀（由 manifest.prefix 提供）、表名加
mod_tax_ 前缀；业务逻辑（税率计算/政策规则/表单生成）零改动。
原项目保留原位独立运行（用户要求保留原有项目）。
"""
from ..spec import ModuleManifest
from . import models  # noqa: F401  导入即把模块表注册进 Base.metadata（须先于 lifespan 的 create_all）

manifest = ModuleManifest(
    name="finance_tax",
    title="企业所得税",
    version="1.0.0",
    prefix="/api/finance/tax",
    tables=[
        "mod_tax_enterprise",
        "mod_tax_financial_data",
        "mod_tax_adjustment",
        "mod_tax_return",
        "mod_tax_policy",
    ],
    menu={"group": "财务", "icon": "OfficeBuilding", "order": 60},
    agent_tools=[],
    column_patches={},
    requires=[],
    enabled=True,
)
