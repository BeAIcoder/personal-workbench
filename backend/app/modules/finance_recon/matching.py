# -*- coding: utf-8 -*-
"""认领↔核销候选匹配与人工复核（Task 3）。

设计要点（计划 87 行 / task-3-brief 第 4 条）：
- 建议（suggested）只来自机检：精确单据引用优先（认领.父单据 == 核销.单据号），
  其后是「本次认领金额相等 + 交易日期相等」的确定性候选；两种依据都不命中就
  保持未匹配，绝不默认归为预收；
- 重跑只 upsert suggested：confirmed/rejected 冻结（机检不触碰、rejected 不
  重新推荐）；人工创建或人工改写过的行 origin=manual，机检永不覆盖；
- 分摊多对多：认领侧累计分摊 ≤ 认领金额（本次认领），核销侧累计分摊 ≤ 核销
  金额（费用期核销，缺省回退费用期应收）；只在「确认」时校验，超额 409；
  POST 允许先建超额建议，由确认环节拦下（brief 明确"确认时"校验）；
- 金额一律 Decimal 两位（ROUND_HALF_UP），错误全部中文，经 ReconConflictError
  （409）/ ValueError（400）/ ReconNotFoundError（404）向上传递。
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from . import service
from .models import ReconIssue, ReconMatch, ReconRecord
from .schemas import (
    CATEGORY_LABELS,
    ISSUE_SEVERITIES,
    ISSUE_STATUS_LABELS,
    MATCH_STATUS_LABELS,
    IssueCreate,
    IssuePatch,
    MatchCreate,
    MatchPatch,
)

_CENT = Decimal("0.01")

# 机检建议依据（报告/明细展示用）
BASIS_LABELS: dict[str, str] = {
    "exact_doc": "精确单据引用",
    "amount_date": "金额+日期一致",
}

# 机检只写 suggested；confirmed/rejected 冻结，manual 归属不被覆盖
_MACHINE_WRITABLE_STATUSES = ("suggested",)


@dataclass(frozen=True)
class MatchCandidate:
    """一条认领↔核销匹配建议（机检输出，供 sync_suggestions 与报告复用）。"""

    claim_record_id: int
    writeoff_record_id: int
    allocated_amount: Decimal | None
    basis: str  # exact_doc / amount_date

    @property
    def pair(self) -> tuple[int, int]:
        return (self.claim_record_id, self.writeoff_record_id)


def _q(value: Decimal | None) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value)).quantize(_CENT, rounding=ROUND_HALF_UP)


def _load_pair_records(db: Session, batch_id: int, claim_id: int, writeoff_id: int) -> tuple[ReconRecord, ReconRecord]:
    claim = (
        db.query(ReconRecord)
        .filter(ReconRecord.id == claim_id, ReconRecord.batch_id == batch_id)
        .one_or_none()
    )
    writeoff = (
        db.query(ReconRecord)
        .filter(ReconRecord.id == writeoff_id, ReconRecord.batch_id == batch_id)
        .one_or_none()
    )
    if claim is None or writeoff is None:
        missing = claim_id if claim is None else writeoff_id
        raise service.ReconNotFoundError(
            f"记录 {missing} 不存在或不属于批次 {batch_id}"
        )
    if claim.kind != "claim":
        raise ValueError(f"记录 {claim_id} 不是认领记录（{claim.kind}），不能作为认领侧")
    if writeoff.kind != "writeoff":
        raise ValueError(f"记录 {writeoff_id} 不是核销记录（{writeoff.kind}），不能作为核销侧")
    return claim, writeoff


def _scope_writeoffs(db: Session, batch_id: int, batch) -> list[ReconRecord]:
    scope = set(service._scope_of(batch))
    rows = (
        db.query(ReconRecord)
        .filter(ReconRecord.batch_id == batch_id, ReconRecord.kind == "writeoff")
        .all()
    )
    return [row for row in rows if row.contract_no in scope]


def _suggest_allocated(claim: ReconRecord, writeoff: ReconRecord) -> Decimal | None:
    """建议分摊额 = min(本次认领, 核销金额)；任一侧缺失则留空由人工补填。"""
    claim_amt = _q(claim.amount_2)
    wo_amt = _q(writeoff.amount_2)
    if wo_amt is None:
        wo_amt = _q(writeoff.amount_1)
    if claim_amt is None or wo_amt is None:
        return None
    return min(claim_amt, wo_amt)


def build_candidates(db: Session, batch_id: int) -> list[MatchCandidate]:
    """机检建议候选：精确单据引用优先，其后金额+日期确定性候选。

    - 核销记录限定在本批次合同范围内（范围外数据不参与勾稽/匹配）；
    - 已有 confirmed/rejected 裁定或人工创建（origin=manual）的三元组不再建议；
    - 未命中的认领保持 unmatched，绝不默认归为预收。
    """
    batch = service._get_batch(db, batch_id)
    claims = (
        db.query(ReconRecord)
        .filter(ReconRecord.batch_id == batch_id, ReconRecord.kind == "claim")
        .order_by(ReconRecord.id)
        .all()
    )
    writeoffs = _scope_writeoffs(db, batch_id, batch)
    rows = db.query(ReconMatch).filter(ReconMatch.batch_id == batch_id).all()
    excluded_pairs: set[tuple[int, int]] = {
        (m.claim_record_id, m.writeoff_record_id)
        for m in rows
        if m.status in ("confirmed", "rejected") or m.origin == "manual"
    }

    exact: list[MatchCandidate] = []
    exact_pairs: set[tuple[int, int]] = set()
    for claim in claims:
        if not claim.doc_no:
            continue
        for writeoff in writeoffs:
            pair = (claim.id, writeoff.id)
            if pair in excluded_pairs:
                continue
            if writeoff.doc_no and writeoff.doc_no == claim.doc_no:
                exact.append(
                    MatchCandidate(
                        claim_record_id=claim.id,
                        writeoff_record_id=writeoff.id,
                        allocated_amount=_suggest_allocated(claim, writeoff),
                        basis="exact_doc",
                    )
                )
                exact_pairs.add(pair)

    amount_date: list[MatchCandidate] = []
    for claim in claims:
        for writeoff in writeoffs:
            pair = (claim.id, writeoff.id)
            if pair in exact_pairs or pair in excluded_pairs:
                continue
            if (
                claim.amount_2 is not None
                and writeoff.amount_2 is not None
                and _q(claim.amount_2) == _q(writeoff.amount_2)
                and claim.occur_date is not None
                and claim.occur_date == writeoff.occur_date
            ):
                amount_date.append(
                    MatchCandidate(
                        claim_record_id=claim.id,
                        writeoff_record_id=writeoff.id,
                        allocated_amount=_suggest_allocated(claim, writeoff),
                        basis="amount_date",
                    )
                )
    return exact + amount_date


def sync_suggestions(db: Session, batch_id: int) -> dict:
    """重跑同步机检建议：只增/改 suggested（algorithm 归属），其余行一律冻结。

    - 候选集中没有的 algorithm+suggested 行删除（数据变化后旧建议不残留）；
    - 已存在且分摊额未变化的建议不计数（幂等重跑）；
    - confirmed/rejected/manual 行永不触碰；rejected 三元组不重新推荐；
    - 不在此处提交：由 run_checks 与问题/状态同一事务落库（无半提交状态），
      冲突经 unique_guard 回滚后映射中文 409。
    """
    candidates = build_candidates(db, batch_id)
    candidate_map = {cand.pair: cand for cand in candidates}
    rows = (
        db.query(ReconMatch)
        .filter(ReconMatch.batch_id == batch_id)
        .all()
    )
    upserted = 0
    removed = 0
    frozen_kept = 0
    with service.unique_guard(db, "匹配建议同步"):
        for row in rows:
            pair = (row.claim_record_id, row.writeoff_record_id)
            if pair in candidate_map:
                if row.origin == "algorithm" and row.status == "suggested":
                    new_amount = candidate_map[pair].allocated_amount
                    if new_amount is not None and row.allocated_amount is not None and new_amount == row.allocated_amount:
                        continue  # 分摊未变化：幂等重跑不计数
                    row.allocated_amount = new_amount
                    upserted += 1
                else:
                    frozen_kept += 1  # confirmed/rejected/manual：机检不触碰
                continue
            if row.origin == "algorithm" and row.status == "suggested":
                db.delete(row)
                removed += 1
            else:
                frozen_kept += 1
        for pair, cand in candidate_map.items():
            if pair in {(row.claim_record_id, row.writeoff_record_id) for row in rows}:
                continue
            db.add(
                ReconMatch(
                    batch_id=batch_id,
                    claim_record_id=cand.claim_record_id,
                    writeoff_record_id=cand.writeoff_record_id,
                    allocated_amount=cand.allocated_amount,
                    status="suggested",
                    origin="algorithm",
                    note=f"机检建议（{BASIS_LABELS[cand.basis]}）",
                )
            )
            upserted += 1
        db.flush()
    return {
        "candidates": len(candidates),
        "suggested_upserted": upserted,
        "suggested_removed": removed,
        "frozen_kept": frozen_kept,
    }


def claim_coverage(db: Session, batch_id: int) -> dict:
    """认领覆盖视图：逐条认领的确认/建议分摊与未匹配余额（报告与未匹配问题共用）。

    P2-3：未匹配余额只扣「已确认」分摊——suggested 是待确认建议，不得掩盖
    未匹配余额；仅存在 suggested 时未匹配问题保持 pending；confirmed/suggested
    在 totals 与逐条认领视图中分列（uncovered_after_suggestions 为扣除建议后
    的参考余额）。
    """
    claims = (
        db.query(ReconRecord)
        .filter(ReconRecord.batch_id == batch_id, ReconRecord.kind == "claim")
        .order_by(ReconRecord.id)
        .all()
    )
    rows = db.query(ReconMatch).filter(ReconMatch.batch_id == batch_id).all()
    by_claim: dict[int, list[ReconMatch]] = {}
    for row in rows:
        by_claim.setdefault(row.claim_record_id, []).append(row)

    zero = Decimal("0")
    claims_view: list[dict] = []
    unmatched_claims: list[dict] = []
    claimed_total = confirmed_total = suggested_total = unmatched_total = zero
    for claim in claims:
        matches = by_claim.get(claim.id, [])
        confirmed = sum(
            (m.allocated_amount for m in matches if m.status == "confirmed" and m.allocated_amount is not None),
            zero,
        )
        suggested = sum(
            (m.allocated_amount for m in matches if m.status == "suggested" and m.allocated_amount is not None),
            zero,
        )
        claimed = claim.amount_2
        if claimed is None:
            remainder = None  # 认领金额未填：无法判定覆盖，交人工补录
            remainder_str = None
            remainder_after_suggested_str = None
        else:
            remainder = max(zero, claimed - confirmed)
            remainder_str = service._amount_str(remainder)
            remainder_after_suggested = max(zero, claimed - confirmed - suggested)
            remainder_after_suggested_str = service._amount_str(remainder_after_suggested)
            unmatched_total += remainder
        claimed_total += claimed or zero
        confirmed_total += confirmed
        suggested_total += suggested
        view = {
            "record_id": claim.id,
            "doc_no": claim.doc_no or "",
            "occur_date": claim.occur_date.isoformat() if claim.occur_date else None,
            "claimed_amount": service._amount_str(claimed) or None,
            "confirmed_amount": service._amount_str(confirmed),
            "suggested_amount": service._amount_str(suggested),
            "remainder": remainder_str,
            "uncovered_after_suggestions": remainder_after_suggested_str,
        }
        claims_view.append(view)
        if remainder_str is not None and remainder > 0:
            unmatched_claims.append(view)

    counts = {status: 0 for status in ("suggested", "confirmed", "rejected")}
    manual_suggested = 0
    for row in rows:
        if row.status in counts:
            counts[row.status] += 1
        if row.status == "suggested" and row.origin == "manual":
            manual_suggested += 1
    return {
        "claims": claims_view,
        "unmatched_claims": unmatched_claims,
        "counts": {**counts, "manual_suggested": manual_suggested},
        "totals": {
            "claimed_total": service._amount_str(claimed_total),
            "confirmed_total": service._amount_str(confirmed_total),
            "suggested_total": service._amount_str(suggested_total),
            "unmatched_total": service._amount_str(unmatched_total),
        },
    }


# ---------- 人工复核 API 服务 ----------


def _record_brief(record: ReconRecord | None) -> dict | None:
    if record is None:
        return None
    return {
        "record_id": record.id,
        "kind": record.kind,
        "doc_no": record.doc_no or "",
        "contract_no": record.contract_no or "",
        "fee_type": record.fee_type or "",
        "occur_date": record.occur_date.isoformat() if record.occur_date else None,
        "amount_1": service._amount_str(record.amount_1) or None,
        "amount_2": service._amount_str(record.amount_2) or None,
    }


def _match_out(db: Session, match: ReconMatch) -> dict:
    claim = db.get(ReconRecord, match.claim_record_id)
    writeoff = db.get(ReconRecord, match.writeoff_record_id)
    return {
        "id": match.id,
        "batch_id": match.batch_id,
        "status": match.status,
        "status_label": MATCH_STATUS_LABELS.get(match.status, match.status),
        "origin": match.origin,
        "allocated_amount": service._amount_str(match.allocated_amount) or None,
        "note": match.note or "",
        "claim": _record_brief(claim),
        "writeoff": _record_brief(writeoff),
        "updated_at": match.updated_at.isoformat(timespec="seconds") if match.updated_at else "",
    }


def list_matches(db: Session, batch_id: int) -> dict:
    service._get_batch(db, batch_id)
    rows = (
        db.query(ReconMatch)
        .filter(ReconMatch.batch_id == batch_id)
        .order_by(ReconMatch.id)
        .all()
    )
    return {"total": len(rows), "items": [_match_out(db, row) for row in rows]}


def _allocation_guard(
    db: Session,
    batch_id: int,
    claim: ReconRecord,
    writeoff: ReconRecord,
    new_amount: Decimal,
    exclude_match_id: int | None,
) -> None:
    """确认前校验双侧累计分摊：认领侧 ≤ 本次认领金额，核销侧 ≤ 核销金额。

    只统计已 confirmed 的其他分摊（suggested 不占额度，允许先分后定）；
    任一侧金额缺失（未填）即拒绝确认（P2-2：不把「无法验证」当「不限制」，
    不得跳过上限静默放行）。
    """
    if claim.amount_2 is None:
        raise service.ReconConflictError(
            f"认领记录 #{claim.id} 未填本次认领金额，无法确认分摊，请先补录"
        )
    wo_cap = writeoff.amount_2
    if wo_cap is None:
        wo_cap = writeoff.amount_1
    if wo_cap is None:
        raise service.ReconConflictError(
            f"核销记录 #{writeoff.id} 未填费用期核销/应收金额，无法确认分摊，请先补录"
        )
    sides = [
        ("认领", Decimal(str(claim.amount_2)), ReconMatch.claim_record_id == claim.id, claim.id),
        ("核销", Decimal(str(wo_cap)), ReconMatch.writeoff_record_id == writeoff.id, writeoff.id),
    ]
    for label, cap, condition, record_id in sides:
        total = (
            db.query(func.coalesce(func.sum(ReconMatch.allocated_amount), 0))
            .filter(
                ReconMatch.batch_id == batch_id,
                ReconMatch.status == "confirmed",
                condition,
                ReconMatch.id != exclude_match_id,
            )
            .scalar()
        )
        projected = Decimal(str(total)) + new_amount
        if projected > cap:
            raise service.ReconConflictError(
                f"分摊超额：{label}记录 #{record_id}"
                f"累计分摊 {service._amount_str(projected)} 已超过{label}金额 "
                f"{service._amount_str(cap)}（超出 {service._amount_str(projected - cap)}），"
                "请调整部分分摊后再确认"
            )


def _validate_review_writable(batch) -> None:
    if batch.status == "checking":
        raise service.ReconConflictError(
            f"批次当前状态为「{service._label(batch.status)}」，检查进行中不能调整匹配"
        )


def create_manual_match(db: Session, batch_id: int, payload: MatchCreate | dict[str, Any]) -> dict:
    """人工新增匹配建议（suggested / origin=manual）；唯一三元组 409。"""
    if not isinstance(payload, MatchCreate):
        payload = MatchCreate.model_validate(payload)
    batch = service._get_batch(db, batch_id)
    _validate_review_writable(batch)
    claim, writeoff = _load_pair_records(db, batch_id, payload.claim_record_id, payload.writeoff_record_id)
    scope = set(service._scope_of(batch))
    if writeoff.contract_no not in scope:
        raise ValueError(
            f"核销记录 #{writeoff.id} 的合同「{writeoff.contract_no}」不在本批次范围内，"
            "不能参与匹配（请在批次合同范围勾选后再试）"
        )
    match = ReconMatch(
        batch_id=batch_id,
        claim_record_id=payload.claim_record_id,
        writeoff_record_id=payload.writeoff_record_id,
        allocated_amount=_q(payload.allocated_amount),
        status="suggested",
        origin="manual",
        note=(payload.note or "").strip(),
    )
    db.add(match)
    with service.unique_guard(db, "人工匹配"):
        db.commit()
    db.refresh(match)
    return _match_out(db, match)


def patch_match(db: Session, match_id: int, payload: MatchPatch | dict[str, Any]) -> dict:
    """人工裁定匹配：状态迁移 + 分摊/说明维护；confirmed/rejected 分摊冻结。"""
    if not isinstance(payload, MatchPatch):
        payload = MatchPatch.model_validate(payload)
    if payload.status is None and payload.allocated_amount is None and payload.note is None:
        raise ValueError("未提供任何要更新的字段（可维护：状态、分摊金额、说明）")
    match = db.get(ReconMatch, match_id)
    if match is None:
        raise service.ReconNotFoundError(f"匹配 {match_id} 不存在")
    batch = service._get_batch(db, match.batch_id)
    _validate_review_writable(batch)

    if payload.allocated_amount is not None:
        if match.status != "suggested":
            raise service.ReconConflictError(
                f"匹配当前状态为「{MATCH_STATUS_LABELS.get(match.status, match.status)}」，"
                "已确认/已排除的分摊金额已冻结；请先改回待确认再调整"
            )
        match.allocated_amount = _q(payload.allocated_amount)
    if payload.note is not None:
        match.note = payload.note.strip()
    if payload.status is not None and payload.status != match.status:
        if payload.status == "confirmed":
            claim, writeoff = _load_pair_records(
                db, match.batch_id, match.claim_record_id, match.writeoff_record_id
            )
            # P2-8：确认前复查核销合同是否仍在当前批次范围（scope 可能已收窄）
            scope = set(service._scope_of(batch))
            if writeoff.contract_no not in scope:
                raise service.ReconConflictError(
                    f"核销记录 #{writeoff.id} 的合同「{writeoff.contract_no}」"
                    "已不在本批次范围内，不能确认该匹配；请先调整批次合同范围或改回待确认"
                )
            amount = match.allocated_amount
            if amount is None:
                raise ValueError("该匹配尚未填写分摊金额，确认前请先补填")
            _allocation_guard(
                db, match.batch_id, claim, writeoff, Decimal(str(amount)), exclude_match_id=match.id
            )
        match.status = payload.status
    match.origin = "manual"  # 人工改写即接管归属：机检重跑不再覆盖该行
    db.commit()
    return _match_out(db, match)


# ---------- 人工问题复核（human 来源；机检永不触碰 human 行） ----------


def _issue_out(issue: ReconIssue) -> dict:
    return {
        "id": issue.id,
        "batch_id": issue.batch_id,
        "origin": issue.origin,
        "fingerprint": issue.fingerprint,
        "category": issue.category,
        "category_label": CATEGORY_LABELS.get(issue.category, issue.category),
        "severity": issue.severity,
        "evidence": issue.evidence or [],
        "amount_impact": service._amount_str(issue.amount_impact) or None,
        "status": issue.status,
        "status_label": ISSUE_STATUS_LABELS.get(issue.status, issue.status),
        "active": bool(issue.active),
        "note": issue.note or "",
        "last_seen": issue.last_seen.isoformat(timespec="seconds") if issue.last_seen else None,
        "created_at": issue.created_at.isoformat(timespec="seconds") if issue.created_at else "",
        "updated_at": issue.updated_at.isoformat(timespec="seconds") if issue.updated_at else "",
    }


def list_issues(
    db: Session,
    batch_id: int,
    *,
    origin: str | None = None,
    status: str | None = None,
    active: bool | None = None,
) -> dict:
    """问题清单（机检+人工）：origin/status/active 过滤，稳定按 id 排序。"""
    service._get_batch(db, batch_id)
    query = db.query(ReconIssue).filter(ReconIssue.batch_id == batch_id)
    if origin:
        query = query.filter(ReconIssue.origin == origin)
    if status:
        query = query.filter(ReconIssue.status == status)
    if active is not None:
        query = query.filter(ReconIssue.active.is_(bool(active)))
    rows = query.order_by(ReconIssue.id).all()
    return {"total": len(rows), "items": [_issue_out(row) for row in rows]}


def create_issue(db: Session, batch_id: int, payload: IssueCreate | dict[str, Any]) -> dict:
    """人工补记问题/待补资料（origin=human，pending）：机检永不触碰。"""
    if not isinstance(payload, IssueCreate):
        payload = IssueCreate.model_validate(payload)
    batch = service._get_batch(db, batch_id)
    _validate_review_writable(batch)
    now = datetime.now()
    raw = f"human|{payload.category}|{now.isoformat(timespec='microseconds')}"
    issue = ReconIssue(
        batch_id=batch_id,
        origin="human",
        fingerprint=hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32],
        category=payload.category.strip(),
        severity=payload.severity,
        evidence=list(payload.evidence),
        amount_impact=payload.amount_impact,
        status="pending",
        active=True,
        note=(payload.note or "").strip(),
        last_seen=now,
    )
    db.add(issue)
    db.commit()
    return _issue_out(issue)


def patch_issue(db: Session, issue_id: int, payload: IssuePatch | dict[str, Any]) -> dict:
    """人工裁定问题（机检/人工行均可，全局 issue_id）：状态/说明/级别。

    - 确认或排除必须填写说明（ValueError → 400）；
    - 状态与说明一经裁定，机检重跑永不回改（见 recon_engine._sync_issues）；
    - resolved 置 active=false，pending/confirmed/dismissed 保持 active=true。
    """
    if not isinstance(payload, IssuePatch):
        payload = IssuePatch.model_validate(payload)
    if payload.status is None and payload.severity is None and payload.note is None:
        raise ValueError("未提供任何要更新的字段（可维护：状态、严重级别、说明）")
    issue = db.get(ReconIssue, issue_id)
    if issue is None:
        raise service.ReconNotFoundError(f"问题 {issue_id} 不存在")
    batch = service._get_batch(db, issue.batch_id)
    _validate_review_writable(batch)
    new_status = payload.status or issue.status
    if new_status in ("confirmed", "dismissed"):
        note = (payload.note if payload.note is not None else issue.note) or ""
        if not note.strip():
            raise ValueError(f"将问题标记为「{ISSUE_STATUS_LABELS[new_status]}」必须填写说明")
    if payload.severity is not None:
        if payload.severity not in ISSUE_SEVERITIES:
            raise ValueError(f"非法严重级别 {payload.severity!r}")
        issue.severity = payload.severity
    if payload.note is not None:
        issue.note = payload.note.strip()
    if payload.status is not None and payload.status != issue.status:
        issue.status = payload.status
        issue.active = payload.status != "resolved"
    db.commit()
    return _issue_out(issue)
