"""科目余额表入库与余额/发生额回退汇总（由 service.py 机械拆分）。

余额表按（核算账簿, 期间, 科目）upsert，只更新本次映射字段；余额行查询给出
筛选后全量合计；凭证分录缺失时 balance_summary 回退余额表本期发生额。
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Any

import pandas as pd
from sqlalchemy import func
from sqlalchemy.orm import Session

from ...utils import escape_like
from .bip_common import (
    FIELD_LABELS,
    MAX_WARNINGS,
    PERIOD_RE,
    _MONEY,
    _parse_amount,
    _r2,
    _text,
)
from .models import BipBalance, BipBatch, BipEntry, BipVoucher

# ---------------- 科目余额表 ----------------

_BALANCE_MONEY_FIELDS = (
    "opening_debit", "opening_credit", "period_debit", "period_credit",
    "closing_debit", "closing_credit", "opening_balance", "closing_balance",
)


def _import_balance(
    db: Session, batch: BipBatch, df: pd.DataFrame, mapping: dict[str, str], period: str
) -> dict[str, Any]:
    """科目余额表入库：按（核算账簿, 期间, 科目编码）upsert（台账 L-072）。

    - 同键已存在 → 只更新本次**已映射**的字段（未映射字段保留旧值并告知），
      内容完全相同则计入 unchanged_balances，不谎报 updated；
    - 跨账簿/跨月各自独立（键含账簿与期间）；旧空账簿历史行在可唯一确定时就地升级；
    - 文件内同键重复 → 按最后一行取值并告警；
    - 缺科目编码 / 金额不可解析 / 非有限值的行跳过并计入无效行，不静默归零。
    """
    code_c, name_c = mapping["subject_code"], mapping["subject_name"]
    ledger_c = mapping.get("ledger")
    amount_fields = (
        "opening_debit", "opening_credit", "period_debit", "period_credit",
        "closing_debit", "closing_credit", "opening_balance", "closing_balance",
    )
    mapped_fields = [f for f in amount_fields if mapping.get(f)]
    if ledger_c:
        # BIP 余额表续行的核算账簿单元格常为空：沿用上一行，避免同科目被误判为
        # 「空账簿行」，或被另一账簿的旧行错误升级/覆盖（复核 C-1 残余）。
        df = df.copy()
        df[ledger_c] = df[ledger_c].map(lambda v: _text(v) or None).ffill()

    payloads: dict[tuple[str, str], dict[str, Any]] = {}
    warnings: list[str] = []
    skipped_rows = invalid_amount_rows = 0
    for idx, row in df.iterrows():
        code = _text(row[code_c])
        ledger = _text(row[ledger_c]) if ledger_c else ""
        if not code:
            skipped_rows += 1
            continue
        try:
            amounts = {field: _parse_amount(row[mapping[field]]) for field in mapped_fields}
        except ValueError as exc:
            skipped_rows += 1
            invalid_amount_rows += 1
            warnings.append(f"数据第 {idx + 1} 行（科目 {code}）{exc}，已跳过该行")
            continue
        key = (ledger, code)
        if key in payloads:
            warnings.append(
                f"账簿 {ledger or '（空）'} / 科目 {code} 在文件内重复出现，按最后一行数据入库"
            )
        payloads[key] = {"subject_name": _text(row[name_c]), **amounts}

    existing: dict[tuple[str, str], BipBalance] = {}
    legacy_by_code: dict[str, BipBalance] = {}
    duplicate_rows = 0
    if payloads:
        codes = {code for _, code in payloads}
        for row_obj in db.query(BipBalance).filter(
            BipBalance.period == period,
            BipBalance.subject_code.in_(codes),
        ).all():
            key = (row_obj.ledger or "", row_obj.subject_code)
            if key in existing:
                # 历史重复行不自动删除（迁移阶段已告警），本次只更正首行
                duplicate_rows += 1
                continue
            existing[key] = row_obj
            if not (row_obj.ledger or "").strip():
                # 旧唯一索引 (period, subject_code) 保证同期间同科目至多一条空账簿行；
                # 若历史异常出现多条，duplicate_rows 已告警，这里只保留首条用于升级。
                legacy_by_code.setdefault(row_obj.subject_code, row_obj)
        if duplicate_rows:
            warnings.append(
                f"期间 {period} 存在 {duplicate_rows} 条历史重复余额行，未自动删除；"
                "本次仅更正首行，请人工核对清理"
            )

    inserted = updated = unchanged = 0
    preserved: set[str] = set()
    for (ledger, code), data in payloads.items():
        row_obj = existing.get((ledger, code))
        adopted = False
        if row_obj is None and ledger:
            legacy = legacy_by_code.pop(code, None)
            if legacy is not None and not (legacy.ledger or "").strip():
                # 迁移前 ledger 为空的历史行：带账簿重导时就地升级，避免同科目两份；
                # pop 掉后，后续另一账簿同科目不会再升级并改标签覆盖（C-1 残余）。
                existing.pop(("", code), None)
                row_obj = legacy
                existing[(ledger, code)] = row_obj
                row_obj.ledger = ledger
                adopted = True
        if row_obj is None:
            db.add(BipBalance(batch_id=batch.id, ledger=ledger, period=period,
                              subject_code=code, **data))
            inserted += 1
            continue

        changed = adopted
        for field, value in data.items():
            if getattr(row_obj, field) != value:
                setattr(row_obj, field, value)
                changed = True
        for field in amount_fields:
            if field not in data and abs(getattr(row_obj, field) or 0.0) > 0.005:
                preserved.add(FIELD_LABELS[field])
        if changed:
            row_obj.batch_id = batch.id  # 记录最近一次更正来源
            updated += 1
        else:
            unchanged += 1

    if preserved:
        warnings.append("本次未映射的字段保留旧值：" + "、".join(sorted(preserved)))
    if len(warnings) > MAX_WARNINGS:
        warnings = warnings[:MAX_WARNINGS] + [f"……其余 {len(warnings) - MAX_WARNINGS} 条告警从略"]
    return {
        "inserted_vouchers": 0,
        "skipped_vouchers": 0,
        "inserted_entries": 0,
        "inserted_balances": inserted,
        "updated_balances": updated,
        "unchanged_balances": unchanged,
        "skipped_rows": skipped_rows,
        "invalid_amount_rows": invalid_amount_rows,
        "warnings": warnings,
    }


def _balance_to_dict(row: BipBalance) -> dict:
    data = {
        "id": row.id,
        "ledger": row.ledger or "",
        "period": row.period or "",
        "subject_code": row.subject_code or "",
        "subject_name": row.subject_name or "",
    }
    data.update({field: _r2(getattr(row, field) or 0) for field in _BALANCE_MONEY_FIELDS})
    return data


def query_balances(
    db: Session,
    period: str,
    subject_prefix: str | None = None,
    ledger: str | None = None,
    page: int = 1,
    page_size: int = 50,
) -> dict:
    """科目余额表行查询：按期间 + 科目编码前缀 / 账簿筛选，分页并给全量合计。

    只有余额表、没有凭证分录的导入也能在此查看期初/本期/期末金额（L-071 补充）；
    totals 对**筛选后的全部行**求和，不受当页条数影响。
    """
    if not PERIOD_RE.fullmatch(period or ""):
        raise ValueError("期间格式应为 YYYY-MM（如 2026-09）")
    page, page_size = max(1, page), max(1, min(page_size, 200))
    q = db.query(BipBalance).filter(BipBalance.period == period)
    if subject_prefix:
        q = q.filter(BipBalance.subject_code.like(escape_like(subject_prefix) + "%", escape="\\"))
    if ledger:
        q = q.filter(BipBalance.ledger.contains(ledger, autoescape=True))
    total = q.count()
    rows = (
        q.order_by(BipBalance.subject_code, BipBalance.ledger, BipBalance.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    agg = q.with_entities(
        *[func.coalesce(func.sum(getattr(BipBalance, field)), 0.0) for field in _BALANCE_MONEY_FIELDS]
    ).one()
    totals = {field: _r2(value) for field, value in zip(_BALANCE_MONEY_FIELDS, agg)}
    return {
        "period": period,
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [_balance_to_dict(row) for row in rows],
        "totals": totals,
    }


# ---------------- 公共只读适配（供兄弟模块对账，不写库） ----------------

# 对账消费方只需期末/期初四列；显式白名单避免把整行 ORM 对象泄给其它模块。
_READ_AMOUNT_FIELDS = ("closing_debit", "closing_credit", "closing_balance", "opening_balance")


def _read_amount(value: Any) -> Decimal:
    """余额列转 Decimal 两位：库内是 Float，经 str() 再量化避免二进制尾差。"""
    number = Decimal(str(value or 0))
    if not number.is_finite():
        raise ValueError("余额行金额不是有限数值")
    return number.quantize(_MONEY, rounding=ROUND_HALF_UP)


def _balance_read_dict(row: BipBalance) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": row.id,
        "ledger": row.ledger or "",
        "period": row.period or "",
        "subject_code": row.subject_code or "",
        "subject_name": row.subject_name or "",
    }
    data.update({field: _read_amount(getattr(row, field)) for field in _READ_AMOUNT_FIELDS})
    return data


def read_balance_rows(
    db: Session, *, ledger: str, period: str, subject_code: str
) -> list[dict[str, Any]]:
    """精确读取单账簿/期间/科目的科目余额行（只读）。

    - 全部条件等值匹配：不做科目前缀、账簿模糊或空账簿回退，避免把别的账簿、
      近似科目算进对账（调用方不得据此猜数）；
    - 历史重复行原样返回（不隐藏、不合并、不改正），由调用方判定「多行歧义」；
    - 不写库：不建批次、不 upsert、不改 batch_id；
    - 金额以 Decimal 两位返回，期间严格 ``YYYY-MM``。
    """
    if not PERIOD_RE.fullmatch(period or ""):
        raise ValueError("期间格式应为 YYYY-MM（如 2026-09）")
    code = (subject_code or "").strip()
    if not code:
        raise ValueError("科目编码不能为空")
    rows = (
        db.query(BipBalance)
        .filter(
            BipBalance.ledger == (ledger or "").strip(),
            BipBalance.period == period,
            BipBalance.subject_code == code,
        )
        .order_by(BipBalance.id.asc())
        .all()
    )
    return [_balance_read_dict(row) for row in rows]


def list_balance_scopes(db: Session) -> dict[str, list[str]]:
    """余额表已存在的账簿与期间清单（只读，供下拉选择；不做任何推断）。"""
    ledgers = sorted(
        {text for (value,) in db.query(BipBalance.ledger).distinct().all()
         if (text := (value or "").strip())}
    )
    periods = sorted(
        {value for (value,) in db.query(BipBalance.period).distinct().all() if value}
    )
    return {"ledgers": ledgers, "periods": periods}


# ---------------- 发生额汇总（仅余额表时回退） ----------------

def balance_summary(db: Session, period: str) -> dict:
    """科目发生额汇总：按期间聚合凭证分录；只有余额表时回退余额表本期发生额。

    凭证列表可能不提供科目列：此时汇总为空，但必须明确提示「未提供科目编码」
    而不是让用户误以为导入失败或金额为零（台账 L-071）。若该期间只导入了科目
    余额表（无凭证分录），则用余额表「本期借方/贷方发生额」生成同一形状的汇总，
    ``source`` 标明数据来源，避免「只有余额表就查不到数」。
    """
    if not PERIOD_RE.fullmatch(period or ""):
        raise ValueError("期间格式应为 YYYY-MM（如 2026-09）")
    rows = (
        db.query(
            BipEntry.subject_code,
            BipEntry.subject_name,
            func.count(BipEntry.id),
            func.coalesce(func.sum(BipEntry.debit), 0.0),
            func.coalesce(func.sum(BipEntry.credit), 0.0),
        )
        .join(BipVoucher, BipVoucher.id == BipEntry.voucher_id)
        .filter(BipVoucher.period == period, BipEntry.subject_code != "")
        .group_by(BipEntry.subject_code, BipEntry.subject_name)
        .order_by(BipEntry.subject_code)
        .all()
    )
    entries_without_subject = (
        db.query(func.count(BipEntry.id))
        .join(BipVoucher, BipVoucher.id == BipEntry.voucher_id)
        .filter(BipVoucher.period == period, BipEntry.subject_code == "")
        .scalar()
    ) or 0
    items = [
        {
            "subject_code": code,
            "subject_name": name,
            "entry_count": int(cnt),
            "debit_total": _r2(d),
            "credit_total": _r2(c),
        }
        for code, name, cnt, d, c in rows
    ]
    source = "entries" if items else "none"
    note = ""
    if items:
        # 分录路径只需余额行数，不为展示字段加载全部余额表 ORM 行（大表省内存/时间）
        balance_row_count = (
            db.query(func.count(BipBalance.id)).filter(BipBalance.period == period).scalar() or 0
        )
    else:
        balance_rows = (
            db.query(BipBalance)
            .filter(BipBalance.period == period)
            .order_by(BipBalance.subject_code, BipBalance.ledger, BipBalance.id)
            .all()
        )
        balance_row_count = len(balance_rows)
        if balance_rows:
            # 只有余额表、没有凭证分录：按科目编码聚合本期发生额（多账簿同科目合并，
            # 名称取首个非空），与分录路径「每科目一行」的汇总语义保持一致
            grouped: dict[str, dict[str, Any]] = {}
            for row in balance_rows:
                code = row.subject_code or ""
                if not code:
                    continue
                bucket = grouped.setdefault(
                    code, {"subject_name": "", "debit": Decimal("0"), "credit": Decimal("0")}
                )
                if not bucket["subject_name"] and row.subject_name:
                    bucket["subject_name"] = row.subject_name
                bucket["debit"] += Decimal(str(row.period_debit or 0))
                bucket["credit"] += Decimal(str(row.period_credit or 0))
            items = [
                {
                    "subject_code": code,
                    "subject_name": bucket["subject_name"],
                    "entry_count": 0,
                    "debit_total": float(bucket["debit"].quantize(_MONEY, rounding=ROUND_HALF_UP)),
                    "credit_total": float(bucket["credit"].quantize(_MONEY, rounding=ROUND_HALF_UP)),
                }
                for code, bucket in sorted(grouped.items())
            ]
            if items:
                source = "balances"
                note = (
                    "该期间未导入凭证分录，以下发生额取自科目余额表（本期借方/贷方发生额，"
                    "同科目多账簿已合并）；期初/期末余额可在余额行查询中查看"
                )
    if entries_without_subject and source != "balances":
        if items:
            note = f"另有 {int(entries_without_subject)} 条分录未提供科目编码，未计入汇总"
        else:
            note = (
                "该期间的分录未提供科目编码（凭证列表来源通常如此），无法按科目汇总；"
                "请导入同期间辅助明细账，或在凭证列表导入时映射科目列"
            )
    return {
        "period": period,
        "items": items,
        "subject_count": len(items),
        "debit_total": _r2(sum(i["debit_total"] for i in items)),
        "credit_total": _r2(sum(i["credit_total"] for i in items)),
        "entries_without_subject": int(entries_without_subject),
        "source": source,
        "balance_row_count": balance_row_count,
        "note": note,
    }
