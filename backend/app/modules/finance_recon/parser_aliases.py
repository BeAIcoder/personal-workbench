# -*- coding: utf-8 -*-
"""finance_recon xlsx 表头别名声明（唯一事实源，parsers_xlsx 消费）。

表头按别名文本锚点定位，不依赖文件名、固定行号或合同号前缀；新版式 = 在对应
spec 的 aliases 里显式追加，声明顺序即优先级（先精确、后变体）：同角色命中
多个不同别名列时取最高优先级别名的唯一列，精确同名重复列仍显式拒绝。

真实模板已确认变体（2026-11-19 审查）：
- writeoff：重复「应收金额/核销金额」（列组语义：第 1 组=账单级、第 2 组=费用期级）
  +「核销后欠费金额」；
- claim：「本次认领金额」；
- arrear：「欠费合计」与费用列「合计」并存（优先精确「合计」费用列）、「固定点位-固定租金」
  「固定点位-综合管理费」；只有「欠费合计」的变体仍兼容（租金/综管费缺列 → None 不归零）。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Spec:
    """一个语义列：role 供内部取值，label 用于中文报错。"""

    role: str
    label: str
    aliases: tuple[str, ...]
    required: bool


WRITEOFF_SPECS: tuple[Spec, ...] = (
    Spec("seq_no", "序号", ("序号", "编号"), False),
    Spec("region", "区域", ("区域",), False),
    Spec("project", "项目", ("项目",), False),
    Spec("contract", "合同号", ("合同号", "合同编号", "合同"), True),
    Spec("contract_type", "合同类型", ("合同类型",), False),
    Spec("bill_month", "账单月", ("账单月", "账单期间"), False),
    Spec("brand", "品牌", ("品牌",), False),
    Spec("unit_no", "铺位", ("铺位", "铺位号", "铺位编号"), False),
    Spec("doc_no", "单据号", ("单据号", "单据编号", "凭证号"), True),
    Spec("writeoff_date", "核销日期", ("核销日期",), False),
    Spec("cancel_date", "取消日期", ("取消日期",), False),
    Spec("writeoff_type", "核销类型", ("核销类型",), False),
    Spec("fee_type", "费用类型", ("费用类型",), True),
    Spec("fee_period", "费用期", ("费用期", "费用起止", "费用期间"), True),
    Spec("tax_rate", "税率", ("税率",), False),
    Spec("prepaid_type", "预收类别", ("预收类别",), False),
    Spec("left_fee", "核销后欠费", ("核销后欠费", "核销后欠费金额"), True),
    Spec("prepaid", "预收款金额", ("预收款金额", "预收金额"), True),
)

# 应收/核销 双组列：qualified 别名（带限定词）优先，裸重复表头按列组归位
# （第 1 组=账单级、第 2 组=费用期级；同名/同义列合计 >2 组 → 歧义拒绝）
AR_GROUP = {"doc_ar": ("应收(账单)",), "fee_ar": ("应收(费用期)",)}
CL_GROUP = {"doc_cleared": ("核销(账单)",), "fee_cleared": ("核销(费用期)",)}
GROUP_LABELS = {
    "doc_ar": "应收(账单)",
    "fee_ar": "应收(费用期)",
    "doc_cleared": "核销(账单)",
    "fee_cleared": "核销(费用期)",
}
# 裸表头文本集合：同组内任一文本命中都计入列组（真实模板为「应收金额/核销金额」）
BARE_AR_TEXTS = ("应收", "应收金额")
BARE_CL_TEXTS = ("核销", "核销金额")

CLAIM_SPECS: tuple[Spec, ...] = (
    Spec("source", "来源", ("来源",), False),
    Spec("doc_type", "单据类型", ("单据类型",), False),
    Spec("doc_no", "父单据", ("父单据", "父单据号", "父单据编号"), True),
    Spec("sub_doc_no", "子单据", ("子单据", "子单据号", "子单据编号"), False),
    Spec("transfer_id", "传输id", ("传输id", "传输ID"), False),
    Spec("biz_type", "业务类型", ("业务类型",), False),
    Spec("payer_name", "付款户名", ("付款户名",), False),
    Spec("payer_account", "付款账号", ("付款账号",), False),
    Spec("payee_name", "收款户名", ("收款户名",), False),
    Spec("payee_account", "收款账号", ("收款账号",), False),
    Spec("amount_recv", "收款金额", ("收款金额",), True),
    Spec("amount_claim", "本次认领", ("本次认领", "本次认领金额", "认领金额"), True),
    Spec("amount_fee", "手续费", ("手续费",), False),
    Spec("txn_type", "交易类型", ("交易类型",), False),
    Spec("purpose", "用途", ("用途",), False),
    Spec("txn_time", "交易时间", ("交易时间",), True),
    Spec("memo", "摘要", ("摘要",), False),
    Spec("status", "状态", ("状态",), False),
    Spec("claim_time", "认领时间", ("认领时间",), False),
    Spec("claimant", "认领人", ("认领人",), False),
    Spec("audit_time", "审核时间", ("审核时间",), False),
    Spec("auditor", "审核人", ("审核人",), False),
)

ARREAR_SPECS: tuple[Spec, ...] = (
    Spec("contract", "合同号", ("合同", "合同号", "合同编号"), True),
    Spec("bill_month", "账单月", ("账单月", "账单期间"), False),
    Spec("due_date", "缴费截止", ("缴费截止", "缴费截止日", "缴费截止日期"), False),
    Spec("fee_start", "费用开始", ("费用开始", "费用开始日", "费用开始日期"), False),
    Spec("fee_end", "费用结束", ("费用结束", "费用结束日", "费用结束日期"), False),
    # 优先精确「合计」费用列；「欠费合计」为兼容变体（真实模板双列并存时取「合计」）
    Spec("amount_total", "合计", ("合计", "欠费合计"), True),
    # 真实模板费用列带「固定点位-」前缀；缺列（仅欠费合计变体）→ None 不归零
    Spec("amount_rent", "固定租金", ("固定租金", "固定点位-固定租金", "租金"), False),
    Spec("amount_mgmt", "综管费", ("综管费", "固定点位-综合管理费", "综合管理费"), False),
)

SPECS_BY_KIND: dict[str, tuple[Spec, ...]] = {
    "writeoff": WRITEOFF_SPECS,
    "claim": CLAIM_SPECS,
    "arrear": ARREAR_SPECS,
}
