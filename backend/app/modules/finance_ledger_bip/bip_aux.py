"""辅助明细账（明细行）解析与分录级查询（由 service.py 机械拆分）。

承载辅助明细账单行 → 分录字段的解析（_entry_from_row）与辅助明细账/分录按
科目编码前缀的明细查询（query_entries）。余额滚动计算不在本模块（余额表入库与
回退汇总见 bip_balance，个人账滚动余额见 finance_ledger_personal）。
"""
from __future__ import annotations

from typing import Any

import pandas as pd
from sqlalchemy.orm import Session

from ...utils import escape_like
from .bip_common import _parse_amount, _split_subject, _text, _r2
from .models import BipEntry, BipVoucher

# ---------------- 辅助明细账单行解析 ----------------

def _entry_from_row(
    row: pd.Series,
    *,
    summary_c: str | None,
    scode_c: str | None,
    sname_c: str | None,
    subject_c: str | None,
    debit_c: str | None,
    credit_c: str | None,
) -> dict[str, Any]:
    """单行 → 分录字段；金额不可解析/非有限值时抛 ValueError（调用方跳过该行）。"""
    code = _text(row[scode_c]) if scode_c else ""
    name = _text(row[sname_c]) if sname_c else ""
    if not code and subject_c:
        code, combined_name = _split_subject(_text(row[subject_c]))
        name = name or combined_name
    return {
        "summary": _text(row[summary_c]) if summary_c else "",
        "subject_code": code,
        "subject_name": name,
        "debit": _parse_amount(row[debit_c]) if debit_c else 0.0,
        "credit": _parse_amount(row[credit_c]) if credit_c else 0.0,
    }


# ---------------- 分录级明细查询 ----------------

def query_entries(
    db: Session,
    subject_prefix: str | None = None,
    period: str | None = None,
    ledger: str | None = None,
    voucher_no: str | None = None,
    keyword: str | None = None,
    page: int = 1,
    page_size: int = 50,
) -> dict:
    """分录查询：按科目编码前缀 / 期间 / 账簿 / 凭证号 / 摘要关键词组合筛选 + 分页。"""
    page, page_size = max(1, page), max(1, min(page_size, 200))
    q = db.query(BipEntry, BipVoucher).join(BipVoucher, BipVoucher.id == BipEntry.voucher_id)
    if subject_prefix:
        # 通配符/反斜杠按字面匹配：escape_like 转义 % _ \ 后配 escape="\\"（审计台账 A08）
        q = q.filter(BipEntry.subject_code.like(escape_like(subject_prefix) + "%", escape="\\"))
    if period:
        q = q.filter(BipVoucher.period == period)
    if ledger:
        q = q.filter(BipVoucher.ledger.contains(ledger, autoescape=True))
    if voucher_no:
        q = q.filter(BipVoucher.voucher_no.contains(voucher_no, autoescape=True))
    if keyword:
        q = q.filter(BipEntry.summary.contains(keyword, autoescape=True))
    total = q.count()
    rows = (
        q.order_by(BipVoucher.voucher_date.desc(), BipVoucher.id.desc(), BipEntry.line_no)
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [
            {
                "id": e.id,
                "voucher_id": v.id,
                "period": v.period,
                "voucher_date": v.voucher_date,
                "ledger": v.ledger,
                "voucher_no": v.voucher_no,
                "line_no": e.line_no,
                "summary": e.summary,
                "subject_code": e.subject_code,
                "subject_name": e.subject_name,
                "debit": _r2(e.debit),
                "credit": _r2(e.credit),
            }
            for e, v in rows
        ],
    }
