# -*- coding: utf-8 -*-
"""商户核销对账勾稽引擎（Task 3）：数据勾稽检查、机检问题生命周期与检查编排。

无 UI 依赖、无默认口径（计划 82 行：引擎不内置 30 天折算 / 1‰ 日违约金 /
缴费日 20 或任何样本费率；费率、天数、到期规则全部来自批次口径参数或源数据）。

职责拆分（单文件 800 行软上限）：
- 本文件：数据装载、数据级勾稽（账单=欠费 / 核销+欠费推算应收 / 商户级保证金
  核对）、机检问题生命周期（_sync_issues）与 run_checks 编排；
- ``anomalies.py``：行级/认领异常检查（零金额/空费用/同秒/倒挂/错位/同合同重复）；
- ``reporting.py``：口径参数类计算（重构/违约金/账龄/结算）+ 统一 ReportViewModel；
- ``matching.py``：认领↔核销匹配与人工复核（含问题人工裁定）；
- ``schemas.py``：ContractTerms 固定 schema、terms_completeness、引擎输出契约
  （CheckOutcome/IssueDraft/ReconResult）与中文标签（叶子模块，供全链共用）。

缺数据语义（P1-3）：账单/欠费/核销任一侧无记录时对应检查一律 unknown 并给中文
原因，不按 0 参与对比、不产生伪 mismatch 问题；只有双侧都有记录才做容差对比。

``run_checks`` 延迟导入 reporting（reporting 模块级导入 engine，反向函数级
导入，避免循环依赖）；中断的 checking 批次超过明确阈值（service.
CHECKING_STALE_SECONDS，2 小时）后下次执行先回收 failed，未超时仍 409 防并发。

金额一律 Decimal 两位（ROUND_HALF_UP），输出序列化为字符串，禁止 float/round()。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy.orm import Session

from . import anomalies, matching, service
from .models import ReconBatch, ReconIssue, ReconRecord, ReconTerm
from .schemas import CheckOutcome, ContractTerms, IssueDraft, ReconResult

_CENT = Decimal("0.01")
ZERO = Decimal("0")

# 商户级保证金核对的显式判定：费用类型/预收类别含「保证金」（核销侧）、
# 认领用途/摘要含「保证金」（认领侧）；不做模糊关键词猜测
_DEPOSIT_MARKER = "保证金"


# ---------- 数据装载 ----------


@dataclass
class BatchData:
    """批次事实行视图：范围内逐合同分组；认领全量（认领行无合同号字段）。"""

    batch_id: int
    scope: list[str]
    writeoff_in: list[ReconRecord] = field(default_factory=list)
    arrear_in: list[ReconRecord] = field(default_factory=list)
    bill_in: list[ReconRecord] = field(default_factory=list)
    claims: list[ReconRecord] = field(default_factory=list)
    out_of_scope: dict[str, int] = field(default_factory=dict)
    by_contract: dict[str, dict[str, list[ReconRecord]]] = field(default_factory=dict)


def load_data(db: Session, batch: ReconBatch) -> BatchData:
    """装载批次事实行：范围外行仅计数（可查看、不参与勾稽汇总，计划 76 行）。"""
    scope = service._scope_of(batch)
    scope_set = set(scope)
    data = BatchData(batch_id=batch.id, scope=scope)
    rows = (
        db.query(ReconRecord)
        .filter(ReconRecord.batch_id == batch.id)
        .order_by(ReconRecord.id)
        .all()
    )
    for row in rows:
        if row.kind == "claim":
            data.claims.append(row)
            continue
        if row.kind not in ("writeoff", "arrear", "bill"):
            continue
        if row.contract_no not in scope_set:
            data.out_of_scope[row.kind] = data.out_of_scope.get(row.kind, 0) + 1
            continue
        bucket = {"writeoff": data.writeoff_in, "arrear": data.arrear_in, "bill": data.bill_in}[row.kind]
        bucket.append(row)
        data.by_contract.setdefault(row.contract_no, {"writeoff": [], "arrear": [], "bill": []})[row.kind].append(row)
    for contract in scope:
        data.by_contract.setdefault(contract, {"writeoff": [], "arrear": [], "bill": []})
    return data


def load_terms(db: Session, batch_id: int) -> dict[str, ContractTerms]:
    """口径参数装载：JSON 经固定 schema 校验；非法参数按缺参处理（unknown），不猜值。"""
    from pydantic import ValidationError

    out: dict[str, ContractTerms] = {}
    for row in db.query(ReconTerm).filter(ReconTerm.batch_id == batch_id).all():
        try:
            out[row.contract_no] = ContractTerms.model_validate(row.terms or {})
        except ValidationError:
            continue
    return out


def _sum_amounts(rows: list[ReconRecord], field_name: str) -> tuple[Decimal, bool]:
    """金额合计：None 视为未填（不计入、不归零）；返回 (合计, 是否存在已填值)。"""
    total = ZERO
    has_any = False
    for row in rows:
        value = getattr(row, field_name)
        if value is None:
            continue
        has_any = True
        total += Decimal(str(value))
    return total, has_any


def _diff_check(
    key: str,
    scope: str,
    left: Decimal,
    right: Decimal,
    tolerance: Decimal,
    *,
    left_label: str,
    right_label: str,
    issue_category: str | None,
    drafts: list[IssueDraft],
    note: str = "",
) -> CheckOutcome:
    """双侧对比检查：|差| ≤ 批次容差 → pass，否则 diff 并登记问题（差值带符号）。"""
    delta = (left - right).quantize(_CENT, rounding=ROUND_HALF_UP)
    state = "pass" if abs(delta) <= tolerance else "diff"
    message = (
        f"{left_label} {service._amount_str(left)} vs {right_label} {service._amount_str(right)}"
        f"（容差 {service._amount_str(tolerance)}）"
    )
    if state == "diff":
        message += f"，差异 {service._amount_str(delta)}，需人工确认"
    if note:
        message += f"；{note}"
    if state == "diff" and issue_category:
        drafts.append(
            IssueDraft(
                category=issue_category,
                scope=scope,
                severity="warn",
                evidence=[message],
                amount_impact=delta,
            )
        )
    return CheckOutcome(
        key=key,
        scope=scope,
        state=state,
        message=message,
        left=service._amount_str(left),
        right=service._amount_str(right),
        delta=service._amount_str(delta),
        tolerance=service._amount_str(tolerance),
    )


# ---------- 数据级勾稽检查（只有双侧都有记录才比较；缺一侧 unknown，不按 0） ----------


def _bill_vs_arrear(data: BatchData, tolerance: Decimal, drafts: list[IssueDraft]) -> list[CheckOutcome]:
    outcomes: list[CheckOutcome] = []
    total_bill = ZERO
    total_arrear = ZERO
    total_known = False
    for contract in data.scope:
        rows = data.by_contract.get(contract, {})
        bills = rows.get("bill", [])
        arrears = rows.get("arrear", [])
        if not bills or not arrears:
            missing = []
            if not bills:
                missing.append("账单")
            if not arrears:
                missing.append("欠费")
            outcomes.append(
                CheckOutcome(
                    key="bill_vs_arrear", scope=contract, state="unknown",
                    message=f"该合同无{'、'.join(missing)}记录，无法核对（不按 0 参与对比）",
                )
            )
            continue
        left, left_has = _sum_amounts(bills, "amount_1")
        right, _ = _sum_amounts(arrears, "amount_1")
        if not left_has:
            outcomes.append(
                CheckOutcome(
                    key="bill_vs_arrear", scope=contract, state="unknown",
                    message="账单记录未填累计应付，无法核对",
                )
            )
            continue
        total_bill += left
        total_arrear += right
        total_known = True
        outcomes.append(
            _diff_check(
                "bill_vs_arrear", contract, left, right, tolerance,
                left_label="账单累计应付", right_label="欠费合计",
                issue_category="bill_arrear_mismatch", drafts=drafts,
            )
        )
    if not total_known:
        outcomes.append(
            CheckOutcome(
                key="bill_vs_arrear", scope="总计", state="unknown",
                message="范围内无账单与欠费记录齐备的合同，无法汇总核对",
            )
        )
    else:
        # 总计行只做汇总展示，不重复登记问题（逐合同 diff 已逐条进问题清单）
        outcomes.append(
            _diff_check(
                "bill_vs_arrear", "总计", total_bill, total_arrear, tolerance,
                left_label="账单累计应付", right_label="欠费合计",
                issue_category=None, drafts=drafts,
            )
        )
    return outcomes


def _receivable_identity(data: BatchData, tolerance: Decimal, drafts: list[IssueDraft]) -> list[CheckOutcome]:
    """核销+欠费推算应收：费用期应收合计 ≈ 费用期核销合计 + 欠费合计（逐合同+总计）。

    核销/欠费任一侧无记录 → unknown（不按 0 参与对比，P1-3）。
    """
    outcomes: list[CheckOutcome] = []
    total_left = ZERO
    total_right = ZERO
    total_known = False
    for contract in data.scope:
        rows = data.by_contract.get(contract, {})
        writeoffs = rows.get("writeoff", [])
        arrears = rows.get("arrear", [])
        if not writeoffs or not arrears:
            missing = []
            if not writeoffs:
                missing.append("核销")
            if not arrears:
                missing.append("欠费")
            outcomes.append(
                CheckOutcome(
                    key="receivable_identity", scope=contract, state="unknown",
                    message=f"该合同无{'、'.join(missing)}记录，无法推算应收（不按 0 参与对比）",
                )
            )
            continue
        left, left_has = _sum_amounts(writeoffs, "amount_1")
        cleared, _ = _sum_amounts(writeoffs, "amount_2")
        arrear_total, _ = _sum_amounts(arrears, "amount_1")
        right = cleared + arrear_total
        if not left_has:
            outcomes.append(
                CheckOutcome(
                    key="receivable_identity", scope=contract, state="unknown",
                    message="核销明细未填费用期应收，无法推算",
                )
            )
            continue
        total_left += left
        total_right += right
        total_known = True
        outcomes.append(
            _diff_check(
                "receivable_identity", contract, left, right, tolerance,
                left_label="费用期应收合计", right_label="核销+欠费推算",
                issue_category="receivable_mismatch", drafts=drafts,
            )
        )
    if total_known:
        outcomes.append(
            _diff_check(
                "receivable_identity", "总计", total_left, total_right, tolerance,
                left_label="费用期应收合计", right_label="核销+欠费推算",
                issue_category=None, drafts=drafts,
            )
        )
    return outcomes


def _deposit_reconciliation(
    data: BatchData, tolerance: Decimal, drafts: list[IssueDraft]
) -> CheckOutcome:
    """商户级显式保证金核对（P1-2/I1）：范围内显式保证金核销合计 vs 认领明确
    保证金收款合计（与参考脚本同列口径）。

    - 核销侧 = ``writeoff.amount_2``（费用期核销，参考 recon_check.py dep_cleared）；
    - 认领侧 = ``claim.amount_1``（收款金额，参考 recon_check.py dep_cash）；
    - 显式判定：核销侧费用类型/预收类别含「保证金」，认领侧用途/摘要含「保证金」；
      本核对按显式标记汇总，某一侧无显式记录即按 0 参与（对账恒等式的空账边），
      与缺源文件的 unknown 语义（P1-3）不同：单边数据本身就是可核对的差异事实；
    - 双侧均无显式保证金 → info（无需核对）；
    - 仅认领或仅核销 → diff + pending（不得 info/pass）；
    - 双侧合计差异超容差 → diff + pending；同合同重复名义核销由 anomalies 另行登记。
    """
    deposit_wos: list[tuple[str, ReconRecord]] = []
    for contract in data.scope:
        for row in data.by_contract.get(contract, {}).get("writeoff", []):
            details = row.details or {}
            if _DEPOSIT_MARKER in (row.fee_type or "") or _DEPOSIT_MARKER in (details.get("prepaid_type") or ""):
                deposit_wos.append((contract, row))
    deposit_claims = []
    for claim in data.claims:
        details = claim.details or {}
        if _DEPOSIT_MARKER in (details.get("purpose") or "") or _DEPOSIT_MARKER in (details.get("memo") or ""):
            deposit_claims.append(claim)
    if not deposit_wos and not deposit_claims:
        return CheckOutcome(
            key="deposit_reconciliation", scope="batch", state="info",
            message="无显式保证金核销与认领记录，无需核对",
        )
    wo_total = sum((Decimal(str(row.amount_2 or 0)) for _, row in deposit_wos), ZERO)
    claim_total = sum((Decimal(str(c.amount_1 or 0)) for c in deposit_claims), ZERO)
    wo_docs = "、".join(
        f"{contract}#{row.doc_no or row.id} {service._amount_str(row.amount_2 or ZERO)}"
        for contract, row in deposit_wos
    )
    claim_docs = "、".join((c.doc_no or f"#{c.id}") for c in deposit_claims)
    if not deposit_wos:
        message = (
            f"认领侧明确保证金收款合计 {service._amount_str(claim_total)}（{claim_docs}），"
            "但核销侧无明确标明「保证金」的核销记录，需人工确认（保证金已收未核销或认领用途标注有误）"
        )
        delta = (ZERO - claim_total).quantize(_CENT, rounding=ROUND_HALF_UP)
        drafts.append(
            IssueDraft(
                category="deposit_mismatch", scope="merchant", severity="warn",
                evidence=[message],
                amount_impact=delta,
            )
        )
        return CheckOutcome(
            key="deposit_reconciliation", scope="batch", state="diff", message=message,
            left="0.00", right=service._amount_str(claim_total),
            delta=service._amount_str(delta), tolerance=service._amount_str(tolerance),
        )
    if not deposit_claims:
        message = (
            f"核销侧明确保证金核销合计 {service._amount_str(wo_total)}（{wo_docs}），"
            "但认领侧无明确标明「保证金」用途/摘要的收款记录，需人工确认是否重复名义核销"
        )
        delta = wo_total.quantize(_CENT, rounding=ROUND_HALF_UP)
        drafts.append(
            IssueDraft(
                category="deposit_mismatch", scope="merchant", severity="warn",
                evidence=[message],
                amount_impact=delta,
            )
        )
        return CheckOutcome(
            key="deposit_reconciliation", scope="batch", state="diff", message=message,
            left=service._amount_str(wo_total), right="0.00",
            delta=service._amount_str(delta), tolerance=service._amount_str(tolerance),
        )
    delta = (wo_total - claim_total).quantize(_CENT, rounding=ROUND_HALF_UP)
    state = "pass" if abs(delta) <= tolerance else "diff"
    message = (
        f"核销侧明确保证金核销合计 {service._amount_str(wo_total)}（{wo_docs}）vs "
        f"认领侧明确保证金收款合计 {service._amount_str(claim_total)}（{claim_docs}）；"
        + (f"差异 {service._amount_str(delta)}，疑似重复名义核销，需人工确认" if state == "diff" else "合计一致")
    )
    if state == "diff":
        drafts.append(
            IssueDraft(
                category="deposit_mismatch", scope="merchant", severity="warn",
                evidence=[message],
                amount_impact=delta,
            )
        )
    return CheckOutcome(
        key="deposit_reconciliation", scope="batch", state=state, message=message,
        left=service._amount_str(wo_total), right=service._amount_str(claim_total),
        delta=service._amount_str(delta), tolerance=service._amount_str(tolerance),
    )


# ---------- compute_all：数据级纯计算（run_checks 与报告共用） ----------


def compute_all(
    data: BatchData,
    terms_map: dict[str, ContractTerms],
    as_of: date,
    tolerance: Decimal,
) -> tuple[list[CheckOutcome], list[IssueDraft]]:
    """数据级勾稽纯计算：检查结论 + 问题草稿（口径参数类检查见 reporting.compute_aggregates）。"""
    tolerance_q = Decimal(str(tolerance)).quantize(_CENT, rounding=ROUND_HALF_UP)
    drafts: list[IssueDraft] = []
    checks: list[CheckOutcome] = []
    checks.extend(_bill_vs_arrear(data, tolerance_q, drafts))
    checks.extend(_receivable_identity(data, tolerance_q, drafts))
    checks.append(_deposit_reconciliation(data, tolerance_q, drafts))
    checks.extend(anomalies.collect_anomaly_checks(data, drafts))
    checks.append(
        CheckOutcome(
            key="claim_matching", scope="batch", state="info",
            message="认领覆盖情况由匹配复核实时给出（见 matches / summary）",
        )
    )
    return checks, drafts


# ---------- 机检问题生命周期（human 行绝不触碰） ----------


def _sync_issues(db: Session, batch_id: int, drafts: list[IssueDraft]) -> dict:
    """按指纹收敛机检问题（指纹=类别+scope，不含金额参数）：

    - 同一轮同指纹草稿先在内存合并（证据去重拼接），DB 唯一约束
      (batch_id, origin, fingerprint) 兜底，冲突经 unique_guard 回滚映射 409；
    - 新指纹 → pending/active；pending 再命中 → 更新证据/金额影响/last_seen；
    - resolved 再命中 → 回 pending/active（同一行复用）；
    - confirmed/dismissed 再命中 → 恢复 active=true 并刷新 last_seen（状态与说明冻结）；
    - pending 不再命中 → resolved + active=false；
    - confirmed/dismissed 不再命中 → 仅 active=false。
    """
    merged: dict[str, IssueDraft] = {}
    for draft in drafts:
        first = merged.get(draft.fingerprint)
        if first is None:
            merged[draft.fingerprint] = draft
        else:
            for item in draft.evidence:
                if item not in first.evidence:
                    first.evidence.append(item)
    now = service._now()
    existing = {
        row.fingerprint: row
        for row in db.query(ReconIssue)
        .filter(ReconIssue.batch_id == batch_id, ReconIssue.origin == "machine")
        .all()
    }
    stats = {
        "created": 0, "pending_updated": 0, "reopened": 0,
        "frozen_active": 0, "resolved": 0, "frozen_inactive": 0,
    }
    with service.unique_guard(db, "机检问题同步"):
        for fingerprint, draft in merged.items():
            row = existing.get(fingerprint)
            if row is None:
                db.add(
                    ReconIssue(
                        batch_id=batch_id,
                        origin="machine",
                        fingerprint=fingerprint,
                        category=draft.category,
                        severity=draft.severity,
                        evidence=list(draft.evidence),
                        amount_impact=draft.amount_impact,
                        status="pending",
                        active=True,
                        last_seen=now,
                    )
                )
                stats["created"] += 1
            elif row.status == "pending":
                row.severity = draft.severity
                row.evidence = list(draft.evidence)
                row.amount_impact = draft.amount_impact
                row.last_seen = now
                row.active = True
                stats["pending_updated"] += 1
            elif row.status == "resolved":
                row.status = "pending"
                row.severity = draft.severity
                row.evidence = list(draft.evidence)
                row.amount_impact = draft.amount_impact
                row.last_seen = now
                row.active = True
                stats["reopened"] += 1
            else:  # confirmed/dismissed：状态与说明冻结；恢复 active 并刷新 last_seen
                row.active = True
                row.last_seen = now
                stats["frozen_active"] += 1
        for fingerprint, row in existing.items():
            if fingerprint in merged:
                continue
            if row.status == "pending":
                row.status = "resolved"
                row.active = False
                stats["resolved"] += 1
            elif row.status in ("confirmed", "dismissed"):
                row.active = False
                stats["frozen_inactive"] += 1
        db.flush()
    return stats


# ---------- run_checks 编排 ----------


def run_checks(db: Session, batch_id: int) -> ReconResult:
    """勾稽检查编排：中断回收 → precheck → checking → 计算 → 同步匹配/问题 → ready。

    - checking 批次：超过 service.CHECKING_STALE_SECONDS（2 小时）视为上次检查中断，
      先回收 failed（中文摘要）再允许重试；未超时仍 409 防并发；
    - precheck 失败 ValueError（路由 400）；非法迁移 ReconConflictError（409）；
    - 计算/写入任何异常：整体回滚 → 批次 failed + 可诊断摘要 → 返回 failed 结果，
      批次绝不滞留 checking（无半提交状态）；
    - 成功：ready、needs_recheck=false、ever_ready=true（审计留痕标记持久化）；
      问题/匹配/状态同一事务提交。
    """
    from . import reporting  # 延迟导入：reporting 模块级导入本文件，避免循环

    batch = service._get_batch(db, batch_id)
    if batch.status == "checking":
        if service.is_checking_stale(batch):
            service.recover_stale_checking(db, batch)
        else:
            raise service.ReconConflictError(
                "批次正在检查中，请等待当前检查完成；"
                f"超过 {service.CHECKING_STALE_HOURS} 小时未完成的中断检查会在下次执行时自动回收"
            )
    service.precheck_for_check(db, batch_id)
    service.transition_batch(db, batch_id, "checking")
    try:
        batch = service._get_batch(db, batch_id)
        data = load_data(db, batch)
        terms_map = load_terms(db, batch_id)
        tolerance = Decimal(str(batch.tolerance)).quantize(_CENT, rounding=ROUND_HALF_UP)
        checks, drafts = compute_all(data, terms_map, batch.as_of, tolerance)
        param_checks, param_drafts, aggregate = reporting.compute_aggregates(
            data, terms_map, batch.as_of, tolerance
        )
        checks.extend(param_checks)
        match_stats = matching.sync_suggestions(db, batch_id)
        coverage = matching.claim_coverage(db, batch_id)
        coverage_drafts = [
            IssueDraft(
                category="claim_unmatched",
                scope=f"record:{claim['record_id']}",
                severity="warn",
                evidence=[
                    f"认领 {claim['doc_no'] or claim['record_id']} 未匹配任何已确认核销"
                    f"（未匹配余额 {claim['remainder']}；待确认建议另计）；请人工匹配，不默认计入预收"
                ],
                amount_impact=Decimal(claim["remainder"]),
            )
            for claim in coverage["unmatched_claims"]
        ]
        issue_stats = _sync_issues(db, batch_id, drafts + param_drafts + coverage_drafts)
        batch.status = "ready"
        batch.needs_recheck = False
        batch.ever_ready = True
        totals = dict(aggregate["totals"])
        totals.update(
            {
                "matched_confirmed_total": coverage["totals"]["confirmed_total"],
                "matched_suggested_total": coverage["totals"]["suggested_total"],
                "unmatched_claim_total": coverage["totals"]["unmatched_total"],
            }
        )
        db.commit()
        return ReconResult(
            batch_id=batch_id,
            status="ready",
            error_summary="",
            needs_recheck=False,
            checks=[item.as_dict() for item in checks],
            totals=totals,
            aging=aggregate["aging"],
            settlement=aggregate["settlement"],
            reconstruction=aggregate["reconstruction"],
            unknown_reasons=aggregate["unknown_reasons"],
            issue_stats=issue_stats,
            match_stats=match_stats,
        )
    except service.ReconConflictError as exc:
        # I2：唯一约束/写入冲突等可判定冲突单独回收失败诊断后原样上抛，
        # 由路由映射真实 409（不得吞成 200 + status=failed）
        db.rollback()
        failed = db.get(ReconBatch, batch_id)
        if failed is not None:
            failed.status = "failed"
            failed.error_summary = f"勾稽检查失败：{exc}"[:2000]
            db.commit()
        raise
    except Exception as exc:  # noqa: BLE001  统一失败回收：failed + 可诊断摘要
        db.rollback()
        failed = db.get(ReconBatch, batch_id)
        if failed is not None:
            failed.status = "failed"
            failed.error_summary = f"勾稽检查失败：{type(exc).__name__}: {exc}"[:2000]
            db.commit()
        return ReconResult(
            batch_id=batch_id,
            status="failed",
            error_summary=f"勾稽检查失败：{type(exc).__name__}: {exc}"[:2000],
        )
