"""finance_tax 模块清单：企业所得税填报（原「企业所得税计算」项目整体迁入）。

移植口径：models/services/config/routers 自原项目复制，仅做三类机械改写——
import 改相对引用、路由去掉 /api 前缀（由 manifest.prefix 提供）、表名加
mod_tax_ 前缀；业务逻辑（税率计算/政策规则/表单生成）零改动。
原项目保留原位独立运行（用户要求保留原有项目）。
"""
from ..spec import ModuleManifest
from . import models  # noqa: F401  导入即把模块表注册进 Base.metadata（须先于 lifespan 的 create_all）
from .tools import tax_returns_summary_tool

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
    agent_tools=[
        {
            "name": "tax_returns_summary",
            "func": tax_returns_summary_tool,
            "description": "企业所得税申报记录摘要：按预缴/年度与草稿/已提交计数，附最新一条申报的关键字段",
            "params": {},
            "required": [],
            "readonly": True,
        }
    ],
    preset_experts=["finance_specialist"],  # 工具自动预置进财税专员白名单
    column_patches={
        # 老库补列：Enterprise 后加的 4 个字段（create_all 不改已有表，宿主 _ensure_sqlite_columns 合并执行）
        "mod_tax_enterprise": {
            "legal_person": "VARCHAR(100)",
            "contact_phone": "VARCHAR(50)",
            "address": "VARCHAR(300)",
            "tax_type": "VARCHAR(50)",
            # 高新资格属性：计算/保存/导出统一取该列（台账 L-050）
            "is_high_tech": "BOOLEAN DEFAULT 0",
        },
        # 申报记录补齐真实利润表与计税口径列（台账 L-051/L-067）
        "mod_tax_return": {
            "operating_revenue": "FLOAT DEFAULT 0",
            "other_income": "FLOAT DEFAULT 0",
            "operating_cost": "FLOAT DEFAULT 0",
            "total_expenses": "FLOAT DEFAULT 0",
            "total_profit": "FLOAT DEFAULT 0",
            "tax_adjustments": "FLOAT DEFAULT 0",
            "tax_preferential": "FLOAT DEFAULT 0",
            "tax_type": "VARCHAR(20) DEFAULT ''",
        },
    },
    requires=[],
    enabled=True,
)
