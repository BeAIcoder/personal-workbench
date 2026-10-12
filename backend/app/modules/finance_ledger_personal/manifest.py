"""finance_ledger_personal 模块清单：个人记账（账户 / 两级分类 / 交易 / 月度预算）。"""
from ..spec import ModuleManifest
from . import models  # noqa: F401  导入即把模块表注册进 Base.metadata（须先于 lifespan 的 create_all）
from .service import pl_add_transaction, pl_monthly_summary, pl_recent

manifest = ModuleManifest(
    name="finance_ledger_personal",
    title="个人记账",
    version="1.0.0",
    prefix="/api/finance/ledger/personal",
    tables=["mod_pl_account", "mod_pl_category", "mod_pl_transaction", "mod_pl_budget"],
    menu={"group": "财务", "icon": "Notebook", "order": 40},
    agent_tools=[
        {
            "name": "pl_add_transaction",
            "func": pl_add_transaction,
            "description": "个人记账：记一笔收入或支出（账户按名匹配，分类仅匹配已有分类，找不到按未分类记）",
            "params": {
                "kind": ("string", "类型：收入 或 支出", True),
                "amount": ("number", "金额（元），大于 0，最多两位小数", True),
                "date": ("string", "日期 YYYY-MM-DD，默认今天", False),
                "account": ("string", "账户名（如 现金/银行卡/微信/支付宝），默认排序第一的账户", False),
                "category": ("string", "分类名或 父>子 路径（如 餐饮 或 餐饮>工作餐），可空", False),
                "note": ("string", "备注", False),
                "tags": ("string", "逗号分隔标签，如：工作,报销", False),
            },
            "required": ["kind", "amount"],
            "readonly": False,
        },
        {
            "name": "pl_monthly_summary",
            "func": pl_monthly_summary,
            "description": "个人记账：查询某月收支汇总（收入/支出/结余）与支出分类 TOP5，month 默认当月",
            "params": {
                "month": ("string", "月份 YYYY-MM，默认当月", False),
            },
            "required": [],
            "readonly": True,
        },
        {
            "name": "pl_recent",
            "func": pl_recent,
            "description": "个人记账：查询最近的交易流水，可按备注关键词过滤",
            "params": {
                "limit": ("integer", "最多返回条数，默认 10", False),
                "keyword": ("string", "备注关键词，可空", False),
            },
            "required": [],
            "readonly": True,
        },
    ],
    column_patches={},
    requires=[],
    enabled=True,
)
