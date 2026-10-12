"""finance_ledger_bip 模块清单：企业记账（BIP）。

数据仓 finance_vault 为可选运行时依赖（入仓失败优雅降级 503），
不写入 requires —— 避免因 vault 未安装导致本模块整体被跳过。
"""
from ..spec import ModuleManifest
from . import models  # noqa: F401  导入即把模块表注册进 Base.metadata（须先于 lifespan 的 create_all）
from .service import bip_balance_query, bip_voucher_query

manifest = ModuleManifest(
    name="finance_ledger_bip",
    title="企业记账（BIP）",
    version="0.1.0",
    prefix="/api/finance/ledger/bip",
    tables=["mod_bip_batch", "mod_bip_voucher", "mod_bip_entry", "mod_bip_balance"],
    menu={"group": "财务", "icon": "OfficeBuilding", "order": 50},
    agent_tools=[
        {
            "name": "bip_voucher_query",
            "func": bip_voucher_query,
            "description": "查询企业记账（BIP）模块的凭证列表，可按期间 YYYY-MM、核算账簿、凭证号、摘要关键词组合过滤",
            "params": {
                "period": ("string", "会计期间，格式 YYYY-MM，默认全部", False),
                "ledger": ("string", "核算账簿名称（模糊匹配），默认全部", False),
                "voucher_no": ("string", "凭证号（模糊匹配），默认全部", False),
                "keyword": ("string", "摘要关键词（含分录摘要），默认全部", False),
                "limit": ("integer", "最多返回条数，默认 10、上限 50", False),
            },
            "required": [],
            "readonly": True,
        },
        {
            "name": "bip_balance_query",
            "func": bip_balance_query,
            "description": "查询企业记账（BIP）模块的科目借贷发生额汇总，按期间 YYYY-MM（默认最近期间）与科目编码前缀过滤",
            "params": {
                "period": ("string", "会计期间，格式 YYYY-MM，默认取数据中最近期间", False),
                "subject_prefix": ("string", "科目编码前缀，如 1002，默认全部", False),
                "limit": ("integer", "最多返回条数，默认 50、上限 200", False),
            },
            "required": [],
            "readonly": True,
        },
    ],
    column_patches={},
    requires=[],
    preset_experts=["finance_specialist"],  # 工具自动预置进财税专员白名单
    enabled=True,
)
