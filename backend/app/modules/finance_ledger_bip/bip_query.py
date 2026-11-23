"""批次 / 凭证查询与 Agent 工具（由 service.py 机械拆分）。

批次列表、凭证组合筛选、凭证详情、bip_voucher_query / bip_balance_query
两个只读 Agent 工具（余额汇总委托 bip_balance.balance_summary）。
"""
from __future__ import annotations

import json

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from ...database import SessionLocal
from .bip_balance import balance_summary
from .bip_common import TYPE_LABELS, _r2
from .models import BipBalance, BipBatch, BipEntry, BipVoucher

# ---------------- 批次与查询 ----------------

def _batch_to_dict(b: BipBatch) -> dict:
    """批次 ORM → 字典（warnings 由 JSON 文本还原为数组）。"""
    try:
        warnings = json.loads(b.warnings or "[]")
    except (TypeError, ValueError):
        warnings = []
    return {
        "id": b.id,
        "filename": b.filename,
        "file_type": b.file_type,
        "type_label": TYPE_LABELS.get(b.file_type, b.file_type),
        "period": b.period,
        "total_rows": b.total_rows,
        "inserted_vouchers": b.inserted_vouchers,
        "skipped_vouchers": b.skipped_vouchers,
        "inserted_entries": b.inserted_entries,
        "inserted_balances": b.inserted_balances,
        "updated_balances": b.updated_balances,
        "unchanged_balances": b.unchanged_balances,
        "merged_vouchers": b.merged_vouchers,
        "merged_entries": b.merged_entries,
        "ambiguous_entries": b.ambiguous_entries,
        "skipped_rows": b.skipped_rows,
        "invalid_amount_rows": b.invalid_amount_rows,
        "warnings": warnings,
        "stored_path": b.stored_path,
        "created_at": b.created_at,
    }


def list_batches(db: Session, page: int = 1, page_size: int = 20) -> dict:
    """导入批次列表（按 id 倒序分页）。"""
    page, page_size = max(1, page), max(1, min(page_size, 200))
    q = db.query(BipBatch)
    total = q.count()
    rows = q.order_by(BipBatch.id.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return {"total": total, "page": page, "page_size": page_size, "items": [_batch_to_dict(b) for b in rows]}


def _voucher_to_dict(v: BipVoucher, entry_count: int, debit_total: float, credit_total: float) -> dict:
    """凭证列表行组装（含分录统计与平衡标记）。"""
    debit_total, credit_total = _r2(debit_total), _r2(credit_total)
    return {
        "id": v.id,
        "period": v.period,
        "voucher_date": v.voucher_date,
        "ledger": v.ledger,
        "voucher_no": v.voucher_no,
        "voucher_full_no": v.voucher_full_no,
        "summary": v.summary,
        "entry_count": int(entry_count),
        "debit_total": debit_total,
        "credit_total": credit_total,
        "balanced": abs(debit_total - credit_total) <= 0.005,
    }


def query_vouchers(
    db: Session,
    period: str | None = None,
    ledger: str | None = None,
    voucher_no: str | None = None,
    keyword: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> dict:
    """凭证组合筛选（期间精确 / 账簿模糊 / 凭证号模糊 / 摘要关键词含分录摘要）+ 分页。"""
    page, page_size = max(1, page), max(1, min(page_size, 200))
    agg = (
        db.query(
            BipEntry.voucher_id.label("vid"),
            func.count(BipEntry.id).label("entry_count"),
            func.coalesce(func.sum(BipEntry.debit), 0.0).label("debit_total"),
            func.coalesce(func.sum(BipEntry.credit), 0.0).label("credit_total"),
        )
        .group_by(BipEntry.voucher_id)
        .subquery()
    )
    q = db.query(
        BipVoucher,
        func.coalesce(agg.c.entry_count, 0),
        func.coalesce(agg.c.debit_total, 0.0),
        func.coalesce(agg.c.credit_total, 0.0),
    ).outerjoin(agg, agg.c.vid == BipVoucher.id)
    if period:
        q = q.filter(BipVoucher.period == period)
    if ledger:
        q = q.filter(BipVoucher.ledger.contains(ledger, autoescape=True))
    if voucher_no:
        q = q.filter(BipVoucher.voucher_no.contains(voucher_no, autoescape=True))
    if keyword:
        q = q.filter(or_(
            BipVoucher.summary.contains(keyword, autoescape=True),
            BipVoucher.id.in_(
                db.query(BipEntry.voucher_id).filter(BipEntry.summary.contains(keyword, autoescape=True))
            ),
        ))
    total = q.count()
    rows = (
        q.order_by(BipVoucher.voucher_date.desc(), BipVoucher.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [_voucher_to_dict(v, c, d, cr) for v, c, d, cr in rows],
    }


def get_voucher_detail(db: Session, voucher_id: int) -> dict:
    """凭证详情：凭证头 + 全部分录（借贷两栏对称）+ 合计与平衡标记。"""
    voucher = db.get(BipVoucher, voucher_id)
    if voucher is None:
        raise LookupError(f"凭证 {voucher_id} 不存在")
    entries = (
        db.query(BipEntry)
        .filter(BipEntry.voucher_id == voucher_id)
        .order_by(BipEntry.line_no, BipEntry.id)
        .all()
    )
    debit_total = _r2(sum(e.debit for e in entries))
    credit_total = _r2(sum(e.credit for e in entries))
    return {
        "id": voucher.id,
        "batch_id": voucher.batch_id,
        "period": voucher.period,
        "voucher_date": voucher.voucher_date,
        "ledger": voucher.ledger,
        "voucher_no": voucher.voucher_no,
        "voucher_full_no": voucher.voucher_full_no,
        "summary": voucher.summary,
        "sources": voucher.sources or "",
        "debit_total": debit_total,
        "credit_total": credit_total,
        "balanced": abs(debit_total - credit_total) <= 0.005,
        "entries": [
            {
                "id": e.id,
                "line_no": e.line_no,
                "summary": e.summary,
                "subject_code": e.subject_code,
                "subject_name": e.subject_name,
                "debit": _r2(e.debit),
                "credit": _r2(e.credit),
                "source_type": e.source_type,
            }
            for e in entries
        ],
    }


# ---------------- Agent 工具（readonly） ----------------

def bip_voucher_query(
    period: str = "", ledger: str = "", voucher_no: str = "", keyword: str = "", limit: int = 10
) -> dict:
    """Agent 工具（readonly）：组合筛选查询 BIP 凭证列表。"""
    with SessionLocal() as db:
        return query_vouchers(
            db,
            period=period or None,
            ledger=ledger or None,
            voucher_no=voucher_no or None,
            keyword=keyword or None,
            page=1,
            page_size=max(1, min(int(limit or 10), 50)),
        )


def bip_balance_query(period: str = "", subject_prefix: str = "", limit: int = 50) -> dict:
    """Agent 工具（readonly）：按期间汇总科目借贷发生额；期间缺省时取数据中最近期间。"""
    try:
        with SessionLocal() as db:
            if not period:
                # 只有余额表、没有凭证分录时也能取到最近期间
                period = (
                    db.query(func.max(BipVoucher.period)).scalar()
                    or db.query(func.max(BipBalance.period)).scalar()
                    or ""
                )
            if not period:
                return {"period": "", "items": [], "subject_count": 0, "debit_total": 0.0,
                        "credit_total": 0.0, "entries_without_subject": 0, "note": ""}
            result = balance_summary(db, period)
    except ValueError as exc:
        return {"error": str(exc), "items": []}
    if subject_prefix:
        items = [i for i in result["items"] if i["subject_code"].startswith(subject_prefix)]
        result = {
            **result,
            "items": items,
            "subject_count": len(items),
            "debit_total": _r2(sum(i["debit_total"] for i in items)),
            "credit_total": _r2(sum(i["credit_total"] for i in items)),
        }
    limit = max(1, min(int(limit or 50), 200))
    result["items"] = result["items"][:limit]
    return result
