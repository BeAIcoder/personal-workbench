"""财务模块 Agent 工具名清单（种子/文档用参考表）。

运行时权威来源是 Module Registry：各模块 manifest 的 preset_experts 声名 +
registry._preset_expert_tools 的幂等合并；本文件供专家团文件化种子、
文档与测试引用，新增模块工具时同步此处即可（漏同步不影响运行，只影响种子）。
"""

FINANCE_TOOL_NAMES: list[str] = [
    # finance_hello（机制实证）
    "hello_ping",
    # finance_expense 费用分类
    "expense_runs_summary",
    # finance_invoice 发票处理
    "invoice_batch_stats",
    # finance_vault 数据仓
    "vault_list_tables",
    "vault_query",
    # finance_ledger_personal 个人记账
    "pl_add_transaction",
    "pl_monthly_summary",
    "pl_recent",
    # finance_ledger_bip 企业记账
    "bip_voucher_query",
    "bip_balance_query",
]

# 预置目标专家（与各模块 manifest.preset_experts 保持一致）
PRESET_EXPERT = "finance_specialist"
