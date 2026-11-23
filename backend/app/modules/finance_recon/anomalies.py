# -*- coding: utf-8 -*-
"""行级/认领异常检查（Task 3 修复轮拆分）：零金额、空费用类型、预收挂空、同秒
混单、审核倒挂、期间错位、保证金同合同重复名义核销。

- 只基于显式字段判定（费用类型/预收类别含「保证金」、显式 amount_4 列、details
  里的时间字段），不按关键词猜测、不按金额推断；
- 输出批次级检查行（scope=batch）+ 逐行问题草稿；机检发现问题 → diff + pending
  问题，未发现 → pass；没有对应数据 → unknown（绝不恒真通过）；
- 依赖方向：本文件只依赖 schemas（CheckOutcome/IssueDraft/CHECK_LABELS 为叶子
  数据类），由 recon_engine 调用，避免 engine↔anomalies 循环导入。
"""
from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING

from .schemas import CheckOutcome, IssueDraft, SOURCE_KIND_LABELS

if TYPE_CHECKING:  # 仅类型标注；运行时不导入 engine，避免循环
    from .recon_engine import BatchData  # noqa: F401

ZERO = Decimal("0")
_CENT = Decimal("0.01")

# 批次级异常检查（scope=batch）：机检发现问题 → diff + pending 问题；未发现 → pass
BATCH_ANOMALY_KEYS: tuple[str, ...] = (
    "claim_same_second",
    "claim_audit_inversion",
    "writeoff_zero_fee",
    "writeoff_missing_fee_type",
    "prepaid_residual",
    "claim_zero_amount",
    "claim_missing_amount",
    "period_misaligned",
    "deposit_duplicate_writeoff",
)

# 各异常检查需要的数据面：缺数据 → unknown，绝不恒真通过
ANOMALY_HAS_DATA: dict[str, str] = {
    "claim_same_second": "claims",
    "claim_audit_inversion": "claims",
    "claim_zero_amount": "claims",
    "claim_missing_amount": "claims",
    "writeoff_zero_fee": "writeoff_in",
    "writeoff_missing_fee_type": "writeoff_in",
    "prepaid_residual": "writeoff_in",
    "period_misaligned": "arrear_or_writeoff",
    "deposit_duplicate_writeoff": "writeoff_in",
}


def parse_bill_month(value) -> tuple[int, int] | None:
    """账单月文本 → (年, 月)；无法识别返回 None（不猜、不误报）。"""
    if not value:
        return None
    match = re.match(r"^\s*(\d{4})[-./年](\d{1,2})", str(value))
    if not match:
        return None
    year, month = int(match.group(1)), int(match.group(2))
    if not 1 <= month <= 12:
        return None
    return year, month


def _claim_anomaly_drafts(data, drafts: list[IssueDraft]) -> dict[str, int]:
    """认领异常：零金额/金额未填、同秒混单、审核倒挂（仅基于显式字段，不猜）。"""
    counts: dict[str, int] = {key: 0 for key in BATCH_ANOMALY_KEYS}
    for claim in data.claims:
        details = claim.details or {}
        txn_time = details.get("txn_time")
        audit_time = details.get("audit_time")
        label = claim.doc_no or f"#{claim.id}"
        if claim.amount_2 is not None and Decimal(str(claim.amount_2)) == ZERO:
            counts["claim_zero_amount"] += 1
            drafts.append(
                IssueDraft(
                    category="claim_zero_amount", scope=f"record:{claim.id}", severity="warn",
                    evidence=[f"认领 {label} 本次认领金额为 0.00，请确认是否为空认领"],
                    amount_impact=ZERO,
                )
            )
        elif claim.amount_2 is None:
            counts["claim_missing_amount"] += 1
            drafts.append(
                IssueDraft(
                    category="claim_missing_amount", scope=f"record:{claim.id}", severity="warn",
                    evidence=[f"认领 {label} 未填本次认领金额，覆盖情况无法判定"],
                )
            )
        if audit_time and txn_time and str(audit_time) < str(txn_time):
            counts["claim_audit_inversion"] += 1
            drafts.append(
                IssueDraft(
                    category="claim_audit_inversion", scope=f"record:{claim.id}", severity="error",
                    evidence=[f"认领 {label} 审核时间 {audit_time} 早于交易时间 {txn_time}"],
                )
            )
    clusters: dict[str, list] = {}
    for claim in data.claims:
        txn_time = (claim.details or {}).get("txn_time")
        if txn_time:
            clusters.setdefault(str(txn_time), []).append(claim)
    for txn_time, group in sorted(clusters.items()):
        if len(group) < 2:
            continue
        counts["claim_same_second"] += 1
        claimed = sum((Decimal(str(c.amount_2 or 0)) for c in group), ZERO)
        docs = "、".join((c.doc_no or f"#{c.id}") for c in group)
        drafts.append(
            IssueDraft(
                category="claim_same_second", scope=f"txn:{txn_time}", severity="warn",
                evidence=[f"交易时间 {txn_time} 同秒存在 {len(group)} 笔认领（{docs}），需人工确认归属"],
                amount_impact=claimed.quantize(_CENT, rounding=ROUND_HALF_UP),
            )
        )
    return counts


def _writeoff_anomaly_drafts(data, drafts: list[IssueDraft]) -> dict[str, int]:
    """核销/欠费行异常：零金额（看费用期核销 amount_2）、空费用类型、预收挂空、
    期间错位、同合同保证金重复名义核销。

    保证金/预收差异只基于显式费用类型（费用类型或预收类别含「保证金」）与显式
    预收款金额列；商户级保证金合计核对见 recon_engine._deposit_reconciliation。
    """
    counts: dict[str, int] = {key: 0 for key in BATCH_ANOMALY_KEYS}
    for contract in data.scope:
        rows = data.by_contract.get(contract, {})
        for kind in ("writeoff", "arrear"):
            for row in rows.get(kind, []):
                details = row.details or {}
                bill_month = parse_bill_month(details.get("bill_month"))
                start, end = row.fee_period_start, row.fee_period_end
                if bill_month is not None and (start is not None or end is not None):
                    aligned = (
                        (start is not None and (start.year, start.month) == bill_month)
                        or (end is not None and (end.year, end.month) == bill_month)
                    )
                    if not aligned:
                        counts["period_misaligned"] += 1
                        drafts.append(
                            IssueDraft(
                                category="period_misaligned", scope=f"record:{row.id}", severity="warn",
                                evidence=[
                                    f"{SOURCE_KIND_LABELS[kind]} {row.doc_no or row.id}（合同 {contract}）"
                                    f"账单月 {details.get('bill_month')} 与费用期 "
                                    f"{start.isoformat() if start else '?'}~{end.isoformat() if end else '?'} "
                                    "不在同一月份"
                                ],
                            )
                        )
        for row in rows.get("writeoff", []):
            details = row.details or {}
            label = row.doc_no or f"#{row.id}"
            if row.amount_2 is not None and Decimal(str(row.amount_2)) == ZERO:
                counts["writeoff_zero_fee"] += 1
                drafts.append(
                    IssueDraft(
                        category="writeoff_zero_fee", scope=f"record:{row.id}", severity="warn",
                        evidence=[f"核销 {label}（合同 {contract}）费用期核销为 0.00"],
                        amount_impact=ZERO,
                    )
                )
            if not (row.fee_type or "").strip():
                counts["writeoff_missing_fee_type"] += 1
                drafts.append(
                    IssueDraft(
                        category="writeoff_missing_fee_type", scope=f"record:{row.id}", severity="warn",
                        evidence=[f"核销 {label}（合同 {contract}）未填费用类型"],
                    )
                )
            a4 = row.amount_4
            receivable_empty = row.amount_1 is None or Decimal(str(row.amount_1)) == ZERO
            cleared_empty = row.amount_2 is None or Decimal(str(row.amount_2)) == ZERO
            if a4 is not None and Decimal(str(a4)) != ZERO and receivable_empty and cleared_empty:
                counts["prepaid_residual"] += 1
                drafts.append(
                    IssueDraft(
                        category="prepaid_residual", scope=f"record:{row.id}", severity="warn",
                        evidence=[
                            f"核销 {label}（合同 {contract}）预收款金额 {service_amount_str(a4)}"
                            " 但无费用期应收/核销，预收核销挂空需人工确认"
                        ],
                        amount_impact=Decimal(str(a4)).quantize(_CENT, rounding=ROUND_HALF_UP),
                    )
                )
    groups: dict[tuple, list] = {}
    for contract in data.scope:
        for row in data.by_contract.get(contract, {}).get("writeoff", []):
            details = row.details or {}
            explicit = "保证金" in (row.fee_type or "") or "保证金" in (details.get("prepaid_type") or "")
            if not explicit:
                continue
            key = (contract, row.fee_type or "", row.fee_period_start, row.fee_period_end)
            groups.setdefault(key, []).append(row)
    for (contract, fee_type, start, end), group in groups.items():
        if len(group) < 2:
            continue
        counts["deposit_duplicate_writeoff"] += 1
        docs = "、".join((row.doc_no or f"#{row.id}") for row in group)
        # 金额影响取「费用期核销」amount_2（保证金实际被核销的金额），
        # 不是应收 amount_1——口径与同合同核销/欠费勾稽一致
        amount = sum((Decimal(str(row.amount_2 or 0)) for row in group), ZERO)
        period_text = f"{start.isoformat()}~{end.isoformat()}" if start and end else "费用期未填"
        drafts.append(
            IssueDraft(
                category="deposit_duplicate_writeoff",
                scope=f"{contract}|{fee_type}|{period_text}",
                severity="warn",
                evidence=[
                    f"合同 {contract} 费用类型「{fee_type or '保证金'}」{period_text} "
                    f"出现 {len(group)} 行核销（{docs}），疑似重复名义核销，需人工确认"
                ],
                amount_impact=amount.quantize(_CENT, rounding=ROUND_HALF_UP),
            )
        )
    return counts


def service_amount_str(value) -> str:
    """两位小数字符串（与 service._amount_str 同口径；此处复制以保持依赖单向）。"""
    return f"{Decimal(str(value)).quantize(_CENT, rounding=ROUND_HALF_UP)}"


def collect_anomaly_checks(data, drafts: list[IssueDraft]) -> list[CheckOutcome]:
    """批次级异常检查行：发现问题 → diff，未发现 → pass，无数据 → unknown。"""
    counts = _claim_anomaly_drafts(data, drafts)
    for key, value in _writeoff_anomaly_drafts(data, drafts).items():
        counts[key] = counts.get(key, 0) + value
    outcomes: list[CheckOutcome] = []
    for key in BATCH_ANOMALY_KEYS:
        needs = ANOMALY_HAS_DATA.get(key, "")
        present = (
            bool(data.claims) if needs == "claims"
            else bool(data.writeoff_in) if needs == "writeoff_in"
            else bool(data.writeoff_in or data.arrear_in) if needs == "arrear_or_writeoff"
            else True
        )
        if not present:
            outcomes.append(
                CheckOutcome(key=key, scope="batch", state="unknown", message="无对应记录，无法检查")
            )
            continue
        count = counts.get(key, 0)
        outcomes.append(
            CheckOutcome(
                key=key, scope="batch",
                state="diff" if count else "pass",
                message=(f"发现 {count} 处，需人工复核" if count else "未发现"),
            )
        )
    return outcomes
