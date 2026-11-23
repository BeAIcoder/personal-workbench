"""finance_invoice 去重与冲红配对（批内 + 跨批次）。

职责：
- 幂等键集合与去重（发票号码 + 校验码，保留首次出现；failed 批次/明细不参与）；
- 金额不可解析明细的显式告警；
- 冲红配对（同购方、金额绝对值相等、一对一），支持跨批双向配对并重算受影响批次统计。

规则与引擎 FileHandler.deduplicate_invoices / match_red_invoices 保持一致，本模块只搬位置不改口径。
"""
from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy.orm import Session

from .invoice_parse import _ACTIVE_ITEM_STATUS, _parse_amount_strict, dedup_key
from .models import InvoiceBatch, InvoiceItem

logger = logging.getLogger(__name__)


def existing_dedup_keys(db: Session, exclude_batch_id: Optional[int] = None) -> set[str]:
    """库内既有有效发票的幂等键集合（跨批次去重用）。

    failed 批次不参与：批次解析流程失败时已落库的 parsed 明细不构成「已入账」，
    否则用户重传同一批文件会被全部判重、永远无法重试（L-093）。
    """
    query = (
        db.query(InvoiceItem.invoice_number, InvoiceItem.check_code)
        .join(InvoiceBatch, InvoiceBatch.id == InvoiceItem.batch_id)
        .filter(
            InvoiceItem.status.in_(_ACTIVE_ITEM_STATUS),
            InvoiceBatch.status != InvoiceBatch.STATUS_FAILED,
        )
    )
    if exclude_batch_id is not None:
        query = query.filter(InvoiceItem.batch_id != exclude_batch_id)
    return {k for k in (dedup_key(n, c) for n, c in query.all()) if k}


def amount_warnings(items: list[InvoiceItem]) -> list[dict]:
    """不可解析金额的明细（parse_amount 会按 0 计入统计，必须显式告警）。"""
    warnings: list[dict] = []
    for item in items:
        if item.status not in _ACTIVE_ITEM_STATUS:
            continue
        raw = (item.amount or "").strip()
        if not raw or _parse_amount_strict(raw) is None:
            warnings.append(
                {
                    "item_id": item.id,
                    "invoice_number": item.invoice_number,
                    "source_file": item.source_file,
                    "amount": item.amount,
                }
            )
    return warnings


def mark_duplicates(items: list[InvoiceItem], existing_keys: Optional[set[str]] = None) -> int:
    """批内 + 跨批次去重：后来者标记为 duplicate，返回重复个数。

    规则同引擎：号码+校验码构成幂等键，保留首次出现；failed 明细不参与。
    """
    seen = set(existing_keys or set())
    duplicates = 0
    for item in items:
        if item.status == InvoiceItem.STATUS_FAILED:
            continue
        key = dedup_key(item.invoice_number, item.check_code)
        if not key:
            continue
        if key in seen:
            item.status = InvoiceItem.STATUS_DUPLICATE
            duplicates += 1
        else:
            seen.add(key)
    return duplicates


def _external_pair_candidates(db: Session, batch_id: int, buyers: set[str]) -> list[InvoiceItem]:
    """其它批次里可参与配对的明细：有效状态、未配对、非 failed 批次、同购方。"""
    if not buyers:
        return []
    return (
        db.query(InvoiceItem)
        .join(InvoiceBatch, InvoiceBatch.id == InvoiceItem.batch_id)
        .filter(
            InvoiceItem.batch_id != batch_id,
            InvoiceItem.status.in_(_ACTIVE_ITEM_STATUS),
            InvoiceItem.paired_item_id.is_(None),
            InvoiceItem.buyer_name.in_(buyers),
            InvoiceBatch.status != InvoiceBatch.STATUS_FAILED,
        )
        .order_by(InvoiceItem.id.asc())
        .all()
    )


def _refresh_red_counts(db: Session, batch_ids: set[int]) -> None:
    """重算受影响批次的冲红统计（跨批配对/删除批次后旧批次数字不再准确）。"""
    for batch_id in sorted(batch_ids):
        batch = db.get(InvoiceBatch, batch_id)
        if batch is None:
            continue
        reds = (
            db.query(InvoiceItem)
            .filter(
                InvoiceItem.batch_id == batch_id,
                InvoiceItem.invoice_direction == "冲红发票",
                InvoiceItem.status.in_(_ACTIVE_ITEM_STATUS),
            )
            .all()
        )
        batch.red_matched_count = sum(1 for r in reds if r.paired_item_id is not None)
        batch.red_unmatched_count = sum(1 for r in reds if r.paired_item_id is None)


def _pair_key(item: InvoiceItem):
    return item.id if item.id is not None else id(item)


def pair_red_invoices(
    items: list[InvoiceItem], candidates: Optional[list[InvoiceItem]] = None
) -> tuple[int, int]:
    """冲红配对：同购方 + 金额绝对值相等 + 一对一（同引擎 match_red_invoices）。

    ``candidates`` 可传其它批次的未配对明细，实现跨批双向配对：本批红票配历史正票、
    本批正票配历史红票。已配对、重复、失败明细一律不参与（即使被误传进 candidates
    也会在函数内再过滤）。配对成功双向写入 paired_item_id；
    返回 (本次建立的对数, 本批仍未配对的冲红张数)。
    """
    current = [i for i in items if i.status in _ACTIVE_ITEM_STATUS]
    current_keys = {_pair_key(i) for i in current}
    external = [
        i for i in (candidates or [])
        if i.status in _ACTIVE_ITEM_STATUS
        and i.paired_item_id is None
        and _pair_key(i) not in current_keys
    ]
    current_reds = [i for i in current if i.invoice_direction == "冲红发票"]
    current_pos = [i for i in current if i.invoice_direction == "正数发票"]
    external_reds = [i for i in external if i.invoice_direction == "冲红发票"]
    external_pos = [i for i in external if i.invoice_direction == "正数发票"]
    used: set = set()

    def find(red: InvoiceItem, pool: list[InvoiceItem]) -> Optional[InvoiceItem]:
        red_amount = _parse_amount_strict(red.amount)
        if red_amount is None:
            return None  # 金额不可解析：不参与配对，避免 0==0 误配（BUGfix INV-02）
        target = abs(red_amount)
        for pos in pool:
            key = _pair_key(pos)
            if key in used or pos.paired_item_id is not None:
                continue
            if pos.buyer_name != red.buyer_name:
                continue
            pos_amount = _parse_amount_strict(pos.amount)
            if pos_amount is None:
                continue
            if abs(pos_amount) == target:
                return pos
        return None

    def link(red: InvoiceItem, pos: InvoiceItem) -> None:
        red.paired_item_id = pos.id
        pos.paired_item_id = red.id
        used.add(_pair_key(red))
        used.add(_pair_key(pos))

    matched = 0
    for red in current_reds:
        if red.paired_item_id is not None:
            used.add(_pair_key(red))
            continue
        partner = find(red, current_pos + external_pos)
        if partner is not None:
            link(red, partner)
            matched += 1
    # 历史红票 ↔ 本批正票：只对仍未被配对的本批正票生效
    for red in external_reds:
        if _pair_key(red) in used:
            continue
        partner = find(red, current_pos)
        if partner is not None:
            link(red, partner)
            matched += 1
    unmatched = sum(1 for r in current_reds if r.paired_item_id is None)
    return matched, unmatched
