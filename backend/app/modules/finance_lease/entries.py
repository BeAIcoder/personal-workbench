# -*- coding: utf-8 -*-
"""finance_lease 分录生成：只做首期初始确认与月度折旧/利息，不重算金额。

边界（获批计划首期）：

- 初始确认：借 使用权资产（现值） / 借 租赁负债—未确认融资费用（付款合计与现值
  轧差） / 贷 租赁负债—租赁付款额（付款合计）；简化口径下使用权资产 = 租赁负债现值，
  不含预付租金、初始直接费用、复原成本。
- 月度折旧：借 折旧费用—使用权资产 / 贷 使用权资产—累计折旧。
- 月度利息：借 财务费用—租赁负债利息 / 贷 租赁负债—未确认融资费用。
- 不生成付款实收、租金变更、主体变更、终止、转销及 BIP 回写分录。
- 所有金额直接取自已保存的测算快照（summary / periods），本文件不做任何口径重算；
  生成后按凭证号校验借贷平衡，不平衡抛中文 ``ValueError``，不导出假分录。
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # 仅类型提示，避免 schemas ↔ entries 的运行时循环导入
    from .schemas import LeaseExportBundle

_CENT = Decimal("0.01")

ENTRIES_SUPPORTED: tuple[str, ...] = (
    "初始确认：借 使用权资产 / 借 租赁负债—未确认融资费用 / 贷 租赁负债—租赁付款额"
    "（简化口径：使用权资产 = 租赁负债现值）",
    "月度折旧：借 折旧费用—使用权资产 / 贷 使用权资产—累计折旧",
    "月度利息：借 财务费用—租赁负债利息 / 贷 租赁负债—未确认融资费用",
    "不支持：付款实收、租金变更、主体变更、终止、转销及 BIP 凭证回写",
)

ENTRIES_NOTE = (
    "分录根据已保存的测算快照自动生成逐笔底稿分录，金额不重算；"
    "初始确认的未确认融资费用按付款合计与现值轧差，与评估表利息合计可能存在"
    " 0.01 级舍入尾差，不再自动补调整分录。本模块不生成付款实收、变更、终止分录；"
    "分录仅为底稿分录，未过账、不回写 BIP。"
)


def _amount(value) -> Decimal:
    return Decimal(str(value or 0)).quantize(_CENT, rounding=ROUND_HALF_UP)


def _month_end(period_month: str) -> date:
    year, month = (int(part) for part in period_month.split("-"))
    if month == 12:
        return date(year, 12, 31)
    return date(year, month + 1, 1) - timedelta(days=1)


def _entry(number: str, entry_date: date, summary: str, account: str,
           debit: Decimal = Decimal("0"), credit: Decimal = Decimal("0"), source: str = "") -> dict:
    return {
        "number": number,
        "date": entry_date.isoformat(),
        "summary": summary,
        "account": account,
        "debit": f"{_amount(debit):.2f}",
        "credit": f"{_amount(credit):.2f}",
        "source": source,
    }


def validate_entries(entries: list[dict]) -> None:
    """按凭证号汇总借贷，必须逐张平衡；不平衡直接抛中文错误，不静默导出。"""
    totals: dict[str, list[Decimal]] = {}
    for entry in entries:
        number = str(entry.get("number") or "").strip()
        if not number:
            raise ValueError("分录缺少凭证号，已拒绝导出")
        bucket = totals.setdefault(number, [Decimal("0.00"), Decimal("0.00")])
        bucket[0] += _amount(entry.get("debit"))
        bucket[1] += _amount(entry.get("credit"))
    for number, (debit, credit) in totals.items():
        if debit != credit:
            raise ValueError(f"分录 {number} 借贷不平衡：借方 {debit:.2f} / 贷方 {credit:.2f}")


def build_entries(bundle: "LeaseExportBundle") -> list[dict]:
    """从已保存快照生成受支持分录；金额直接搬运，不重算折现/折旧/利息。"""
    calculation = bundle.calculation
    summary = calculation.summary
    calc_id = calculation.id
    entries: list[dict] = []

    initial_number = f"LEASE-{calc_id}-INIT"
    initial_source = f"测算 #{calc_id} 初始确认"
    initial = (
        ("使用权资产", summary.present_value, Decimal("0")),
        ("租赁负债—未确认融资费用",
         _amount(summary.total_payment) - _amount(summary.present_value), Decimal("0")),
        ("租赁负债—租赁付款额", Decimal("0"), summary.total_payment),
    )
    for account, debit, credit in initial:
        if _amount(debit) == 0 and _amount(credit) == 0:
            continue
        entries.append(_entry(
            initial_number, bundle.start_date, "初始确认：使用权资产与租赁负债",
            account, debit, credit, initial_source))

    for row in bundle.periods:
        entry_date = _month_end(row.period_month)
        source = f"测算 #{calc_id} 期间表 {row.period_month}"
        if _amount(row.depreciation) != 0:
            number = f"LEASE-{calc_id}-DEP-{row.period_month}"
            entries.append(_entry(
                number, entry_date, f"计提使用权资产折旧 {row.period_month}",
                "折旧费用—使用权资产", row.depreciation, Decimal("0"), source))
            entries.append(_entry(
                number, entry_date, f"计提使用权资产折旧 {row.period_month}",
                "使用权资产—累计折旧", Decimal("0"), row.depreciation, source))
        if _amount(row.interest) != 0:
            number = f"LEASE-{calc_id}-INT-{row.period_month}"
            entries.append(_entry(
                number, entry_date, f"计提租赁负债利息 {row.period_month}",
                "财务费用—租赁负债利息", row.interest, Decimal("0"), source))
            entries.append(_entry(
                number, entry_date, f"计提租赁负债利息 {row.period_month}",
                "租赁负债—未确认融资费用", Decimal("0"), row.interest, source))

    validate_entries(entries)
    return entries
