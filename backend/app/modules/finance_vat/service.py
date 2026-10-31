# -*- coding: utf-8 -*-
"""增值税计税服务：一般纳税人（销项-进项）/ 小规模（1% 征收率+季度30万免征）。"""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from ...config import get_finance_data_dir
from ...utils_money import money
from ...utils_xlsx import safe_cell

# 一般纳税人适用税率白名单（增值税暂行条例）
GENERAL_RATES = [0.13, 0.09, 0.06]

# 小规模免征门槛（19号公告）：季度 30 万 / 月 10 万
QUARTERLY_EXEMPT = 300_000.0
MONTHLY_EXEMPT = 100_000.0


# 小规模征收率白名单（BUGfix FIL-04：任意外部传入值不得产生负税额/任意税额）
SMALL_SCALE_LEVY_RATES = (0.01, 0.03, 0.05)


def calc_general(period: str, output_items: list[dict], input_vat: float,
                 input_transfer_out: float = 0.0) -> dict:
    """一般纳税人：销项按业务类型×税率，减可抵扣进项，加进项转出。

    output_items: [{business_type, amount, rate}]（amount 不含税销售额，rate∈{0.13,0.09,0.06}）
    input_vat: 当期可抵扣进项税额合计
    input_transfer_out: 进项税额转出（已抵扣进项要加回，符号为 +）
    """
    if not output_items:
        raise ValueError("销项明细不能为空")
    details = []
    output_vat = 0.0
    sales_total = 0.0
    for idx, it in enumerate(output_items, 1):
        btype = str(it.get("business_type") or "").strip() or "销售货物"
        rate = it.get("rate")
        if rate not in GENERAL_RATES:
            raise ValueError(f"第 {idx} 行税率非法（允许 13%/9%/6%）")
        amount = it.get("amount")
        if amount is None or float(amount) < 0:
            raise ValueError(f"第 {idx} 行销售额非法（须 ≥ 0）")
        vat = money(Decimal(str(amount)) * Decimal(str(rate)))
        amount = float(amount)
        output_vat += vat
        sales_total += amount
        details.append({"business_type": btype, "amount": amount,
                        "rate": rate, "output_vat": vat})
    input_vat = float(input_vat or 0)
    input_transfer_out = float(input_transfer_out or 0)
    if input_vat < 0:
        raise ValueError("进项税额非法（须 ≥ 0）")
    if input_transfer_out < 0:
        raise ValueError("进项税额转出非法（须 ≥ 0）")
    raw = money(money(output_vat) - input_vat + input_transfer_out)
    payable = money(max(0.0, raw))
    carryforward = money(max(0.0, -raw))
    warnings = []
    if carryforward > 0:
        warnings.append(
            f"当期进项大于销项，形成期末留抵税额 {carryforward:,.2f} 元，"
            f"结转下期继续抵扣（本期应纳税额为 0）"
        )
    return {
        "mode": "general",
        "sales_total": money(sales_total),
        "output_vat": money(output_vat),
        "input_vat": money(input_vat),
        "input_transfer_out": money(input_transfer_out),
        "payable": payable,
        "carryforward_vat": carryforward,
        "details": details,
        "policy": {"policy_basis": "《中华人民共和国增值税暂行条例》", "status": "valid"},
        "warnings": warnings,
    }


def calc_small_scale(period: str, sales_amount: float, levy_rate: float = 0.01,
                     filing_period: str = "quarter") -> dict:
    """小规模纳税人：月/季销售额未超门槛免征；超门槛全额按征收率计税。

    levy_rate 默认 0.01（3% 减按 1%，19号公告至 2027-12-31）；
    政策过期年份拒绝套用 1% 与免征（明确报错，不静默改用 3%）。
    filing_period: month 月度（10 万门槛）/ quarter 季度（30 万门槛，默认）。
    """
    from ..finance_tax.config.policy_validity import policy_status

    year = str(period)[:4]
    raw_sales_amount = sales_amount or 0
    raw_levy_rate = levy_rate
    sales_amount = float(raw_sales_amount)
    if sales_amount < 0:
        raise ValueError("销售额非法（须 ≥ 0）")
    levy_rate = float(raw_levy_rate)
    if levy_rate not in SMALL_SCALE_LEVY_RATES:
        raise ValueError("征收率非法（仅允许 1%/3%/5%）")
    if filing_period not in ("month", "quarter"):
        raise ValueError("申报周期非法（month/quarter）")

    # 1% 优惠与 30/10 万免征均需政策有效（BUGfix FIL-07）；先判政策，
    # 让过期年度直接得到「政策已到期」的中文提示，而不是先报所属期格式。
    if levy_rate == 0.01:
        status = policy_status("vat_small_scale_1pct", year)
        if not status["valid"]:
            raise ValueError(status["message"])
        basis = status["policy_basis"]
    else:
        basis = "《中华人民共和国增值税暂行条例》"
    exempt_status = policy_status("vat_small_scale_exempt10w", year)
    if not exempt_status["valid"]:
        raise ValueError(exempt_status["message"])

    month = int(str(period)[5:7]) if len(str(period)) >= 7 else 0
    if filing_period == "quarter" and month not in (3, 6, 9, 12):
        raise ValueError("按季申报的所属期须为季末月（03/06/09/12）")
    threshold = MONTHLY_EXEMPT if filing_period == "month" else QUARTERLY_EXEMPT
    label = "月度" if filing_period == "month" else "季度"
    exempt = sales_amount <= threshold
    if exempt:
        payable = 0.0
        note = (f"{label}销售额 {sales_amount:,.2f} 元 ≤ {threshold/10000:.0f} 万元，"
                f"免征增值税（{exempt_status['policy_basis']}）")
    else:
        payable = money(Decimal(str(raw_sales_amount)) * Decimal(str(raw_levy_rate)))
        note = f"{label}销售额超 {threshold/10000:.0f} 万元，全额按 {levy_rate:.0%} 征收率计税"
    return {
        "mode": "small_scale",
        "filing_period": filing_period,
        "exempt_threshold": threshold,
        "sales_total": money(sales_amount),
        "levy_rate": levy_rate,
        "exempt": exempt,
        "payable": payable,
        "note": note,
        "policy": {"policy_basis": basis,
                   "status": "valid" if not exempt else "exempt_applied"},
        "warnings": [],
    }


def exports_dir() -> Path:
    d = get_finance_data_dir() / "vat" / "exports"
    d.mkdir(parents=True, exist_ok=True)
    return d


def export_filing_xlsx(filing) -> Path:
    """生成增值税申报底稿 xlsx。"""
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "增值税申报底稿"
    ws.append(["增值税纳税申报表（底稿）"])
    ws.append([f"所属期：{filing.period}    纳税人：{filing.taxpayer_name or '—'}"])
    ws.append([f"纳税人类型：{'一般纳税人' if filing.taxpayer_type == 'general' else '小规模纳税人'}"])
    ws.append([])
    calc = filing.calc_result or {}
    if filing.taxpayer_type == "general":
        ws.append(["业务类型", "不含税销售额", "税率", "销项税额"])
        for d in calc.get("details", []):
            ws.append([safe_cell(d["business_type"]), d["amount"], f"{d['rate']:.0%}", d["output_vat"]])
        ws.append([])
        ws.append(["销项税额合计", calc.get("output_vat", 0)])
        ws.append(["进项税额合计", calc.get("input_vat", 0)])
        ws.append(["应纳税额", calc.get("payable", 0)])
    else:
        ws.append(["季度销售额", calc.get("sales_total", 0)])
        ws.append(["征收率", f"{(calc.get('levy_rate') or 0):.0%}"])
        ws.append(["免征", "是" if calc.get("exempt") else "否"])
        ws.append(["应纳税额", calc.get("payable", 0)])
        ws.append([calc.get("note", "")])
    ws.append([f"政策依据：{(calc.get('policy') or {}).get('policy_basis', '')}"])
    dest = exports_dir() / f"vat_{filing.period}_{filing.id}.xlsx"
    wb.save(dest)
    return dest
