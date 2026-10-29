# -*- coding: utf-8 -*-
"""增值税计税服务：一般纳税人（销项-进项）/ 小规模（1% 征收率+季度30万免征）。"""
from __future__ import annotations

from ...config import get_finance_data_dir
from pathlib import Path

# 一般纳税人适用税率白名单（增值税暂行条例）
GENERAL_RATES = [0.13, 0.09, 0.06]

# 小规模免征门槛（19号公告）：季度 30 万 / 月 10 万
QUARTERLY_EXEMPT = 300_000.0
MONTHLY_EXEMPT = 100_000.0


def calc_general(period: str, output_items: list[dict], input_vat: float) -> dict:
    """一般纳税人：销项按业务类型×税率，减进项税额。

    output_items: [{business_type, amount, rate}]（amount 不含税销售额，rate∈{0.13,0.09,0.06}）
    input_vat: 当期可抵扣进项税额合计
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
        amount = float(amount)
        vat = round(amount * rate, 2)
        output_vat += vat
        sales_total += amount
        details.append({"business_type": btype, "amount": amount,
                        "rate": rate, "output_vat": vat})
    input_vat = float(input_vat or 0)
    if input_vat < 0:
        raise ValueError("进项税额非法（须 ≥ 0）")
    payable = round(max(0.0, round(output_vat, 2) - input_vat), 2)
    return {
        "mode": "general",
        "sales_total": round(sales_total, 2),
        "output_vat": round(output_vat, 2),
        "input_vat": round(input_vat, 2),
        "payable": payable,
        "details": details,
        "policy": {"policy_basis": "《中华人民共和国增值税暂行条例》", "status": "valid"},
        "warnings": [],
    }


def calc_small_scale(period: str, sales_amount: float, levy_rate: float = 0.01) -> dict:
    """小规模纳税人：季度销售额 ≤30 万免征；>30 万全额按征收率计税。

    levy_rate 默认 0.01（3% 减按 1%，19号公告至 2027-12-31）；
    政策过期年份拒绝套用 1%（明确报错），不静默改用 3%。
    """
    from ..finance_tax.config.policy_validity import policy_status

    year = str(period)[:4]
    sales_amount = float(sales_amount or 0)
    if sales_amount < 0:
        raise ValueError("销售额非法（须 ≥ 0）")
    if levy_rate == 0.01:
        status = policy_status("vat_small_scale_1pct", year)
        if not status["valid"]:
            raise ValueError(status["message"])
        basis = status["policy_basis"]
    else:
        basis = "《中华人民共和国增值税暂行条例》"
    quarterly = sales_amount  # 小规模按季申报：sales_amount 即季度销售额
    exempt = quarterly <= QUARTERLY_EXEMPT
    if exempt:
        payable = 0.0
        note = f"季度销售额 {quarterly:,.2f} 元 ≤ 30 万元，免征增值税（19号公告）"
    else:
        payable = round(quarterly * levy_rate, 2)
        note = f"季度销售额超 30 万元，全额按 {levy_rate:.0%} 征收率计税"
    return {
        "mode": "small_scale",
        "sales_total": round(sales_amount, 2),
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
            ws.append([d["business_type"], d["amount"], f"{d['rate']:.0%}", d["output_vat"]])
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
