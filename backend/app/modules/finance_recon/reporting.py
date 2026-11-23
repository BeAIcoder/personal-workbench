# -*- coding: utf-8 -*-
"""商户核销对账统一报告视图（Task 3）：口径参数类勾稽计算 + ReportViewModel。

- ``compute_aggregates``：合同重构 / 违约金参考测算 / 账龄分桶 / 结算草稿四类
  「口径参数类」检查与聚合，供 run_checks 与 build_report 同源消费；
- ``build_report``：唯一报告视图模型（ReportViewModel），summary 端点与
  Task 4 的 xlsx / md / docx 导出共用同一份数据，绝不各自再查库拼装；
- 报告动态读取人工 match / issue（无需重跑机检）；terms 改动后批次
  needs_recheck=true，由 service.assert_exportable 拦截导出（409）；
- 全部金额 Decimal 两位字符串，无 float；不含户名/账号等敏感字段（INV-006）。
"""
from __future__ import annotations

import calendar
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy.orm import Session

from . import matching, service
from .anomalies import parse_bill_month
from .models import ReconIssue, ReconSource
from .recon_engine import BatchData, CheckOutcome, IssueDraft, _sum_amounts
from .schemas import (
    BILLING_METHOD_LABELS,
    CHECK_LABELS,
    RECONSTRUCTION_CUTOFF_LABELS,
    ContractTerms,
    terms_completeness,
)

_CENT = Decimal("0.01")
ZERO = Decimal("0")

# 账龄按日历月分桶（避免任何「30 天」口径）：base 取 缴费截止 > 费用期结束 > 账单月月初
AGING_BUCKETS: tuple[tuple[str, str], ...] = (
    ("undue", "未到期"),
    ("m0", "当月"),
    ("m1_3", "1–3个月"),
    ("m4_6", "4–6个月"),
    ("m7_12", "7–12个月"),
    ("m12p", "12个月以上"),
)


# ---------- 口径参数类计算 ----------


def _month_day_due(year: int, month: int, day: int) -> date:
    """费用月指定日到期：超出当月天数时收敛到月末（日历事实，不是猜测）。"""
    return date(year, month, min(day, calendar.monthrange(year, month)[1]))


def _effective_window(terms: ContractTerms, as_of: date) -> tuple[date, date] | None:
    """重构时间窗口（P2-7 必填截止口径）：through_as_of 截断到对账日；
    contract_end 用完整合同期（支持撤场后结算未到期费用）。窗口为空返回 None。"""
    start, end = terms.contract_start, terms.contract_end
    if terms.reconstruction_cutoff == "through_as_of":
        end = min(end, as_of)
    if end < start:
        return None
    return start, end


def _expected_receivable(terms: ContractTerms, as_of: date) -> Decimal:
    """按口径参数重构合同应收：三种计费方式 + 显式一次性费用，零内置折算。

    - 截止口径 reconstruction_cutoff 必填：through_as_of 只算到对账日（未来月份
      与未来一次性费用不计入）；contract_end 算完整合同期（含未到期费用）；
    - additional_charges 为显式录入的一次性费用（保证金/燃气等），各计费方式下
      均纳入重构；through_as_of 下 period_start 晚于对账日的不计入，未标注期间的
      始终计入；
    - actual_days 逐月按该月实际天数折算（无 30 天口径），只在最终合计时量化。
    """
    total = ZERO
    if terms.billing_method == "custom_lines":
        lines = terms.custom_lines
        if terms.reconstruction_cutoff == "through_as_of":
            lines = [line for line in lines if line.period_start <= as_of]
        for line in lines:
            total += Decimal(str(line.amount))
    else:
        window = _effective_window(terms, as_of)
        if window is not None:
            start, end = window
            if terms.billing_method == "whole_month":
                # 整月计费：窗口触及的日历月数（含首尾月），每份月费标准 × 月数
                months = (end.year - start.year) * 12 + (end.month - start.month) + 1
                total = sum(
                    (Decimal(str(fee.monthly_amount)) * months for fee in terms.monthly_fees), ZERO
                )
            else:  # actual_days
                current_y, current_m = start.year, start.month
                while (current_y, current_m) <= (end.year, end.month):
                    days_in_month = calendar.monthrange(current_y, current_m)[1]
                    first = date(current_y, current_m, 1)
                    last = date(current_y, current_m, days_in_month)
                    overlap_start = max(first, start)
                    overlap_end = min(last, end)
                    overlap_days = (overlap_end - overlap_start).days + 1
                    if overlap_days > 0:
                        for fee in terms.monthly_fees:
                            total += (
                                Decimal(str(fee.monthly_amount))
                                * Decimal(overlap_days)
                                / Decimal(days_in_month)
                            )
                    if current_m == 12:
                        current_y, current_m = current_y + 1, 1
                    else:
                        current_m += 1
    cutoff_date = as_of if terms.reconstruction_cutoff == "through_as_of" else None
    for charge in terms.additional_charges:
        if cutoff_date is not None and charge.period_start is not None and charge.period_start > cutoff_date:
            continue  # 未来一次性费用不计入「截至对账日」口径
        total += Decimal(str(charge.amount))
    return total.quantize(_CENT, rounding=ROUND_HALF_UP)


def _penalty_for_contract(
    terms: ContractTerms, arrears: list, as_of: date
) -> tuple[str, str | None, str, int]:
    """违约金参考测算：返回 (state, 金额或 None, 说明, 未测算行数)。

    - waived → 0.00（仅记录，不再测算）；参数不全 → unknown；行级缺日期 → 未测算行数；
    - 起算日按 start_rule（缴费到期 / 费用期结束次日）；到期日按 due_rule
      （源文件截止 / 费用月指定日 / 费用期结束次日）；天数 = max(0, 对账日 − 起算日)；
    - 结果只作参考（不进结算净额）；cap 封顶；无任何默认费率。
    """
    penalty = terms.penalty
    if penalty.waived:
        return "waived", "0.00", "违约金已豁免（人工参数标记 waived，仅作记录）", 0
    if penalty.rate_per_day is None or penalty.start_rule is None:
        missing = []
        if penalty.rate_per_day is None:
            missing.append("日违约金率")
        if penalty.start_rule is None:
            missing.append("违约金起算规则")
        return "unknown", None, f"违约金参数不完整（缺：{'、'.join(missing)}），未测算", 0
    rate = Decimal(str(penalty.rate_per_day))
    raw = ZERO
    uncomputed = 0
    undue_rows = 0
    for row in arrears:
        if row.amount_1 is None:
            uncomputed += 1  # 欠费本金未填：该行无法测算，不按 0 计
            continue
        period_end = row.fee_period_end
        period_start = row.fee_period_start
        base: date | None = None
        due: date | None = None
        if penalty.start_rule == "period_end_next_day":
            if period_end is not None:
                base = period_end + timedelta(days=1)  # 费用期结束次日
        else:  # due_date
            if terms.due_rule == "source_due_date":
                due = row.due_date
            elif terms.due_rule == "expense_month_day":
                if terms.due_day is not None and period_start is not None:
                    due = _month_day_due(period_start.year, period_start.month, terms.due_day)
            elif terms.due_rule == "period_end_next_day":
                if period_end is not None:
                    due = period_end + timedelta(days=1)  # 费用期结束次日
            if due is not None:
                base = due
        if base is None:
            uncomputed += 1
            continue
        days = (as_of - base).days
        if days < 0:
            undue_rows += 1
            days = 0
        # P1-1：违约金 = 欠费本金 × 日费率 × 逾期天数（参考口径 recon_check.py:225）
        raw += Decimal(str(row.amount_1)) * rate * Decimal(days)
    if penalty.cap is not None and raw > Decimal(str(penalty.cap)):
        raw = Decimal(str(penalty.cap))
    value = str(raw.quantize(_CENT, rounding=ROUND_HALF_UP))
    note = "违约金为参考测算（欠费本金×日费率×逾期天数），不计入结算净额"
    if undue_rows:
        note += f"；{undue_rows} 行未到期未计违约金"
    if uncomputed:
        note += f"；{uncomputed} 行缺少到期/费用期信息未测算"
        if uncomputed == len(arrears):
            return "unknown", None, f"欠费行均缺少到期/费用期信息，无法测算违约金（{note}）", uncomputed
    return "computed", value, note, uncomputed


def _aging_buckets(rows: list, as_of: date) -> tuple[dict[str, Decimal], Decimal, int]:
    """账龄分桶（日历月）：返回 (六桶合计, 无法归桶金额, 无法归桶行数)。"""
    buckets = {key: ZERO for key, _ in AGING_BUCKETS}
    unaged = ZERO
    unaged_rows = 0
    for row in rows:
        base = row.due_date or row.fee_period_end
        if base is None:
            parsed = parse_bill_month((row.details or {}).get("bill_month"))
            if parsed is None:
                unaged += Decimal(str(row.amount_1 or 0))
                unaged_rows += 1
                continue
            base = date(parsed[0], parsed[1], 1)
        months = (as_of.year - base.year) * 12 + (as_of.month - base.month)
        if months < 0:
            key = "undue"
        elif months == 0:
            key = "m0"
        elif months <= 3:
            key = "m1_3"
        elif months <= 6:
            key = "m4_6"
        elif months <= 12:
            key = "m7_12"
        else:
            key = "m12p"
        buckets[key] += Decimal(str(row.amount_1 or 0))
    return buckets, unaged, unaged_rows


def _aging_section(
    data: BatchData, as_of: date, drafts: list[IssueDraft]
) -> tuple[list[CheckOutcome], dict]:
    """账龄分桶检查（逐合同 + 总计）：无法归桶的行登记问题并置 unknown。"""
    outcomes: list[CheckOutcome] = []
    grand = ZERO
    by_contract: list[dict] = []
    total_unaged_rows = 0
    has_arrears = False
    for contract in data.scope:
        arrears = data.by_contract.get(contract, {}).get("arrear", [])
        if not arrears:
            continue
        has_arrears = True
        buckets, unaged, unaged_rows = _aging_buckets(arrears, as_of)
        total, _ = _sum_amounts(arrears, "amount_1")
        grand += total
        total_unaged_rows += unaged_rows
        if unaged_rows:
            drafts.append(
                IssueDraft(
                    category="arrear_unaged",
                    scope=f"contract:{contract}",
                    severity="warn",
                    evidence=[
                        f"合同 {contract} 有 {unaged_rows} 行欠费缺少账单月/费用期/缴费截止，无法归入账龄"
                    ],
                    amount_impact=unaged.quantize(_CENT, rounding=ROUND_HALF_UP),
                )
            )
        outcomes.append(
            CheckOutcome(
                key="aging", scope=contract,
                state="unknown" if unaged_rows else "info",
                message=(
                    f"欠费合计 {service._amount_str(total)}，"
                    + (
                        f"{unaged_rows} 行无法归账龄（{service._amount_str(unaged)}），账龄合计不完整"
                        if unaged_rows
                        else "账龄分桶完整"
                    )
                ),
            )
        )
        by_contract.append(
            {
                "contract_no": contract,
                "buckets": [
                    {"key": key, "label": label, "total": service._amount_str(buckets[key])}
                    for key, label in AGING_BUCKETS
                    if buckets[key] != ZERO
                ],
                "unaged_total": service._amount_str(unaged),
            }
        )
    if not has_arrears:
        outcomes.append(
            CheckOutcome(key="aging", scope="总计", state="info", message="范围内无欠费明细，无账龄可归集")
        )
        return outcomes, {
            "buckets": [], "unaged_total": "0.00", "grand_total": "0.00",
            "complete": True, "by_contract": [],
        }
    total_buckets, total_unaged, unaged_rows_total = _aging_buckets(data.arrear_in, as_of)
    outcomes.append(
        CheckOutcome(
            key="aging", scope="总计",
            state="unknown" if unaged_rows_total else "info",
            message=(
                f"欠费合计 {service._amount_str(grand)}，"
                + (
                    f"{unaged_rows_total} 行无法归账龄（{service._amount_str(total_unaged)}），账龄合计不完整"
                    if unaged_rows_total
                    else "账龄分桶合计与欠费合计一致"
                )
            ),
        )
    )
    return outcomes, {
        "buckets": [
            {"key": key, "label": label, "total": service._amount_str(total_buckets[key])}
            for key, label in AGING_BUCKETS
        ],
        "unaged_total": service._amount_str(total_unaged),
        "grand_total": service._amount_str(grand),
        "complete": unaged_rows_total == 0,
        "by_contract": by_contract,
    }


def _settlement_rows(
    data: BatchData,
    terms_map: dict[str, ContractTerms],
    as_of: date,
    penalty_by_contract: dict[str, dict],
) -> list[dict]:
    """结算草稿：当前欠费 − 人工确认抵扣；违约金只作参考；含到期/未到期拆分。"""
    rows: list[dict] = []
    for contract in data.scope:
        arrears = data.by_contract.get(contract, {}).get("arrear", [])
        terms = terms_map.get(contract)
        credits_confirmed = ZERO
        credits_all = ZERO
        if terms is not None:
            for credit in terms.credits:
                credits_all += Decimal(str(credit.amount))
                if credit.confirmed:
                    credits_confirmed += Decimal(str(credit.amount))
        if not arrears and credits_confirmed == ZERO:
            continue  # 无欠费且无确认抵扣的合同不出结算行（无资料项不补 0）
        arrear_total, _ = _sum_amounts(arrears, "amount_1")
        due = undue = unmarked = ZERO
        for row in arrears:
            amount = Decimal(str(row.amount_1 or 0))
            if row.due_date is None:
                unmarked += amount
            elif row.due_date <= as_of:
                due += amount
            else:
                undue += amount
        net = max(ZERO, arrear_total - credits_confirmed)
        penalty_info = penalty_by_contract.get(
            contract, {"state": "unknown", "value": None, "note": "未录入口径参数，未测算违约金"}
        )
        rows.append(
            {
                "contract_no": contract,
                "arrear_total": service._amount_str(arrear_total),
                "due_receivable": service._amount_str(due),
                "undue_receivable": service._amount_str(undue),
                "unmarked_receivable": service._amount_str(unmarked),
                "confirmed_credits": service._amount_str(credits_confirmed),
                "unconfirmed_credits": service._amount_str(credits_all - credits_confirmed),
                "settlement_net": str(net.quantize(_CENT, rounding=ROUND_HALF_UP)),
                "penalty_reference": penalty_info["value"],
                "penalty_state": penalty_info["state"],
                "penalty_note": penalty_info["note"],
            }
        )
    return rows


def _reconstruction_section(
    data: BatchData,
    terms_map: dict[str, ContractTerms],
    as_of: date,
    tolerance: Decimal,
    drafts: list[IssueDraft],
) -> tuple[list[CheckOutcome], list[dict]]:
    """合同应收重构检查：参数完整才计算；缺参 unknown 并列出缺失项。

    截止口径必填（through_as_of/contract_end，P2-7）；无核销明细 → unknown
    （不按 0 对比，P1-3）。
    """
    outcomes: list[CheckOutcome] = []
    rows_out: list[dict] = []
    for contract in data.scope:
        terms = terms_map.get(contract)
        writeoffs = data.by_contract.get(contract, {}).get("writeoff", [])
        left, left_has = _sum_amounts(writeoffs, "amount_1")
        base = {
            "contract_no": contract,
            "billing_method": None,
            "billing_method_label": "",
            "reconstruction_cutoff": None,
            "fact_receivable": service._amount_str(left) if left_has else None,
        }
        if terms is None:
            outcomes.append(
                CheckOutcome(
                    key="reconstruction", scope=contract, state="unknown",
                    message="未录入口径参数，无法重构合同应收（可在口径参数页补录后重跑）",
                )
            )
            base.update({"state": "unknown", "missing": ["未录入口径参数"], "expected_receivable": None, "delta": None})
            rows_out.append(base)
            continue
        base["billing_method"] = terms.billing_method
        base["billing_method_label"] = BILLING_METHOD_LABELS.get(terms.billing_method or "", "")
        base["reconstruction_cutoff"] = terms.reconstruction_cutoff
        cutoff_label = RECONSTRUCTION_CUTOFF_LABELS.get(terms.reconstruction_cutoff, terms.reconstruction_cutoff)
        complete, missing = terms_completeness(terms)
        if not complete:
            outcomes.append(
                CheckOutcome(
                    key="reconstruction", scope=contract, state="unknown",
                    message=f"口径参数不完整（缺：{'、'.join(missing)}），无法重构合同应收",
                )
            )
            base.update({"state": "unknown", "missing": missing, "expected_receivable": None, "delta": None})
            rows_out.append(base)
            continue
        expected = _expected_receivable(terms, as_of)
        base["expected_receivable"] = service._amount_str(expected)
        if not writeoffs:
            outcomes.append(
                CheckOutcome(
                    key="reconstruction", scope=contract, state="unknown",
                    message="该合同无核销明细，无法与重构结果对比（不按 0 参与对比）",
                )
            )
            base.update({"state": "unknown", "missing": [], "delta": None})
            rows_out.append(base)
            continue
        if not left_has:
            outcomes.append(
                CheckOutcome(
                    key="reconstruction", scope=contract, state="unknown",
                    message="核销明细未填费用期应收，无法与重构结果对比",
                )
            )
            base.update({"state": "unknown", "missing": [], "delta": None})
            rows_out.append(base)
            continue
        fact = left
        delta = (fact - expected).quantize(_CENT, rounding=ROUND_HALF_UP)
        state = "pass" if abs(delta) <= tolerance else "diff"
        label = base["billing_method_label"]
        message = (
            f"重构应收（{label}，截止口径：{cutoff_label}）{service._amount_str(expected)} vs "
            f"事实应收 {service._amount_str(fact)}（容差 {service._amount_str(tolerance)}）"
        )
        if state == "diff":
            message += f"，差异 {service._amount_str(delta)}，需人工确认"
            drafts.append(
                IssueDraft(
                    category="reconstruction_mismatch",
                    scope=contract,
                    severity="warn",
                    evidence=[message],
                    amount_impact=delta,
                )
            )
        outcomes.append(
            CheckOutcome(
                key="reconstruction", scope=contract, state=state, message=message,
                left=service._amount_str(fact), right=service._amount_str(expected),
                delta=service._amount_str(delta), tolerance=service._amount_str(tolerance),
            )
        )
        base.update({"state": state, "missing": [], "delta": service._amount_str(delta)})
        rows_out.append(base)
    return outcomes, rows_out


def compute_aggregates(
    data: BatchData,
    terms_map: dict[str, ContractTerms],
    as_of: date,
    tolerance: Decimal,
) -> tuple[list[CheckOutcome], list[IssueDraft], dict]:
    """口径参数类检查与聚合（run_checks 与 build_report 同源）。"""
    drafts: list[IssueDraft] = []
    checks: list[CheckOutcome] = []
    recon_outcomes, recon_rows = _reconstruction_section(data, terms_map, as_of, tolerance, drafts)
    checks.extend(recon_outcomes)

    penalty_by_contract: dict[str, dict] = {}
    for contract in data.scope:
        arrears = data.by_contract.get(contract, {}).get("arrear", [])
        if not arrears:
            continue
        terms = terms_map.get(contract)
        if terms is None:
            penalty_by_contract[contract] = {
                "state": "unknown", "value": None, "note": "未录入口径参数，未测算违约金",
            }
        else:
            state, value, note, _uncomputed = _penalty_for_contract(terms, arrears, as_of)
            penalty_by_contract[contract] = {"state": state, "value": value, "note": note}
        info = penalty_by_contract[contract]
        checks.append(
            CheckOutcome(
                key="penalty_reference", scope=contract,
                state="info" if info["state"] in ("computed", "waived") else "unknown",
                message=info["note"] or "违约金参考测算完成",
                right=info["value"],
            )
        )

    aging_outcomes, aging_view = _aging_section(data, as_of, drafts)
    checks.extend(aging_outcomes)
    settlement_rows = _settlement_rows(data, terms_map, as_of, penalty_by_contract)
    for row in settlement_rows:
        checks.append(
            CheckOutcome(
                key="settlement", scope=row["contract_no"], state="info",
                message=(
                    f"当前欠费 {row['arrear_total']}，确认抵扣 {row['confirmed_credits']}，"
                    f"结算净额 {row['settlement_net']}（到期 {row['due_receivable']}/"
                    f"未到期 {row['undue_receivable']}）；{row['penalty_note']}"
                ),
            )
        )

    wo_recv, wo_recv_has = _sum_amounts(data.writeoff_in, "amount_1")
    wo_cleared, _ = _sum_amounts(data.writeoff_in, "amount_2")
    wo_left, _ = _sum_amounts(data.writeoff_in, "amount_3")
    wo_prepaid, _ = _sum_amounts(data.writeoff_in, "amount_4")
    arrear_total, _ = _sum_amounts(data.arrear_in, "amount_1")
    bill_total, _ = _sum_amounts(data.bill_in, "amount_1")
    claim_recv, _ = _sum_amounts(data.claims, "amount_1")
    claim_claimed, _ = _sum_amounts(data.claims, "amount_2")
    claim_fee, _ = _sum_amounts(data.claims, "amount_3")
    credits_confirmed = ZERO
    for contract in data.scope:  # P3-6：抵扣合计只算本批次范围内合同的口径参数
        terms = terms_map.get(contract)
        if terms is None:
            continue
        for credit in terms.credits:
            if credit.confirmed:
                credits_confirmed += Decimal(str(credit.amount))
    settlement_net = sum((Decimal(str(row["settlement_net"])) for row in settlement_rows), ZERO)
    penalty_total = ZERO
    penalty_any = False
    for info in penalty_by_contract.values():
        if info["state"] == "computed" and info["value"]:
            penalty_total += Decimal(info["value"])
            penalty_any = True
    totals = {
        "bill_total": service._amount_str(bill_total),
        "arrear_total": service._amount_str(arrear_total),
        "writeoff_receivable_total": service._amount_str(wo_recv) if wo_recv_has else "",
        "writeoff_cleared_total": service._amount_str(wo_cleared),
        "writeoff_left_total": service._amount_str(wo_left),
        "writeoff_prepaid_total": service._amount_str(wo_prepaid),
        "claim_recv_total": service._amount_str(claim_recv),
        "claim_claimed_total": service._amount_str(claim_claimed),
        "claim_fee_total": service._amount_str(claim_fee),
        "estimated_receivable_total": service._amount_str(wo_cleared + arrear_total),
        "confirmed_credits_total": service._amount_str(credits_confirmed),
        "settlement_net_total": service._amount_str(settlement_net),
        "penalty_reference_total": service._amount_str(penalty_total) if penalty_any else "",
    }
    unknown_reasons = [
        {"check": item.key, "label": CHECK_LABELS.get(item.key, item.key), "scope": item.scope, "reason": item.message}
        for item in checks
        if item.state == "unknown"
    ]
    aggregate = {
        "totals": totals,
        "aging": aging_view,
        "settlement": settlement_rows,
        "reconstruction": recon_rows,
        "penalty_by_contract": penalty_by_contract,
        "unknown_reasons": unknown_reasons,
    }
    return checks, drafts, aggregate


# ---------- 统一报告视图模型 ----------


@dataclass
class ReportViewModel:
    """统一报告视图（summary 端点与 xlsx/md/docx 导出同源消费；金额全部字符串）。"""

    batch: dict
    generated_at: str
    checks: list[dict]
    totals: dict
    aging: dict
    settlement: list[dict]
    reconstruction: list[dict]
    matches: dict
    issues: dict
    terms: list[dict]
    unknown_reasons: list[dict]
    check_states: dict
    scope_excluded: dict
    pending_count: int
    # Task 4 增补的纯视图字段（xlsx「核销逐笔」sheet 同源消费；不改任何计算口径）：
    # 范围内核销逐行明细，金额两位字符串或 None（未填），含来源文件与源行号可追溯。
    writeoff_rows: list[dict]

    def as_dict(self) -> dict:
        return asdict(self)


def build_report(db: Session, batch_id: int) -> ReportViewModel:
    """构建统一报告视图：勾稽结论 + 动态人工 match/issue + 口径参数视图。

    - 未检查（draft/failed）批次同样可出报告：检查为 unknown/info，不虚构结论；
    - 人工匹配与问题实时读库，无需重跑机检；
    - 不含户名/完整账号等敏感字段（最小字段原则，INV-006）。
    """
    from . import recon_engine

    batch = service._get_batch(db, batch_id)
    data = recon_engine.load_data(db, batch)
    terms_map = recon_engine.load_terms(db, batch_id)
    tolerance = Decimal(str(batch.tolerance)).quantize(_CENT, rounding=ROUND_HALF_UP)
    checks, _drafts = recon_engine.compute_all(data, terms_map, batch.as_of, tolerance)
    param_checks, _param_drafts, aggregate = compute_aggregates(data, terms_map, batch.as_of, tolerance)
    checks.extend(param_checks)

    coverage = matching.claim_coverage(db, batch_id)
    totals = dict(aggregate["totals"])
    totals.update(
        {
            "matched_confirmed_total": coverage["totals"]["confirmed_total"],
            "matched_suggested_total": coverage["totals"]["suggested_total"],
            "unmatched_claim_total": coverage["totals"]["unmatched_total"],
        }
    )

    issue_rows = (
        db.query(ReconIssue)
        .filter(ReconIssue.batch_id == batch_id)
        .order_by(ReconIssue.id)
        .all()
    )
    issue_items = [matching._issue_out(row) for row in issue_rows]
    issue_counts = {status: 0 for status in ("pending", "confirmed", "dismissed", "resolved")}
    machine_total = 0
    human_total = 0
    active_items: list[dict] = []
    for item in issue_items:
        issue_counts[item["status"]] = issue_counts.get(item["status"], 0) + 1
        if item["origin"] == "machine":
            machine_total += 1
        else:
            human_total += 1
        if item["active"]:
            active_items.append(item)

    check_states = {state: 0 for state in ("pass", "diff", "unknown", "info")}
    check_dicts = [item.as_dict() for item in checks]
    for item in check_dicts:
        check_states[item["state"]] = check_states.get(item["state"], 0) + 1
    unknown_reasons = [
        {
            "check": item["key"],
            "label": item["label"],
            "scope": item["scope"],
            "reason": item["message"],
        }
        for item in check_dicts
        if item["state"] == "unknown"
    ]
    terms_view = service.get_terms(db, batch_id)["items"]
    # Task 4：核销逐笔视图行（只取已存在的 data.writeoff_in，不再查库、不改口径）；
    # details 只挑非金额展示字段，户名/账号类细节不入报告（INV-006 最小字段原则）。
    source_names = {
        s.id: s.original_name
        for s in db.query(ReconSource).filter(ReconSource.batch_id == batch_id).all()
    }
    writeoff_rows: list[dict] = []
    for row in data.writeoff_in:
        details = row.details or {}
        writeoff_rows.append(
            {
                "record_id": row.id,
                "source_name": source_names.get(row.source_id, ""),
                "row_no": row.row_no,
                "contract_no": row.contract_no or "",
                "doc_no": row.doc_no or "",
                "fee_type": row.fee_type or "",
                "fee_period_start": row.fee_period_start.isoformat() if row.fee_period_start else None,
                "fee_period_end": row.fee_period_end.isoformat() if row.fee_period_end else None,
                "writeoff_date": details.get("writeoff_date"),
                "bill_month": details.get("bill_month") or "",
                "amount_receivable": service._amount_str(row.amount_1) or None,
                "amount_cleared": service._amount_str(row.amount_2) or None,
                "amount_left": service._amount_str(row.amount_3) or None,
                "amount_prepaid": service._amount_str(row.amount_4) or None,
            }
        )
    view = ReportViewModel(
        batch={
            "id": batch.id,
            "name": batch.name,
            "object_tag": batch.object_tag or "",
            "as_of": batch.as_of.isoformat() if batch.as_of else "",
            "tolerance": service._amount_str(batch.tolerance),
            "status": batch.status,
            "status_label": service._label(batch.status),
            "needs_recheck": bool(batch.needs_recheck),
            "contract_scope": service._scope_of(batch),
        },
        generated_at=datetime.now().isoformat(timespec="seconds"),
        checks=check_dicts,
        totals=totals,
        aging=aggregate["aging"],
        settlement=aggregate["settlement"],
        reconstruction=aggregate["reconstruction"],
        matches={
            "counts": coverage["counts"],
            "totals": coverage["totals"],
            "unmatched_claims": coverage["unmatched_claims"],
            "claims": coverage["claims"],
        },
        issues={
            "items": active_items,
            "counts": {
                **issue_counts,
                "machine_total": machine_total,
                "human_total": human_total,
                "active_total": len(active_items),
            },
        },
        terms=terms_view,
        unknown_reasons=unknown_reasons,
        check_states=check_states,
        scope_excluded=data.out_of_scope,
        pending_count=sum(1 for item in issue_items if item["status"] == "pending" and item["active"]),
        writeoff_rows=writeoff_rows,
    )
    return view
