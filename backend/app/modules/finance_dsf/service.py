# -*- coding: utf-8 -*-
"""残保金计税服务：财政部公告2023年第8号（30人以下免征 + 分档减收）。"""
from __future__ import annotations

from ...config import get_finance_data_dir
from pathlib import Path

# 分档减收（8号公告）：安排比例 ≥1% 且 <规定比例 → 应缴费额减收 50%；
# 安排比例 <1% → 减收 90%（即按 10% 征收）
TIER_HALF = 0.5
TIER_NINETY = 0.9

# 免征门槛：在职职工 30 人（含）以下
EXEMPT_HEADCOUNT = 30


def calc_dsf(year: str, total_employees: int, total_salary: float,
             disabled_employees: int, ratio: float = 0.015) -> dict:
    """残保金计征。

    year: 缴费所属年度（校验 8号公告有效期）
    total_employees: 上年在职职工总数
    total_salary: 上年在职职工工资总额（元）
    disabled_employees: 实际安排残疾人就业人数
    ratio: 所在地规定比例（默认 1.5%）
    """
    from ..finance_tax.config.policy_validity import policy_status

    year = str(year)
    total_employees = int(total_employees or 0)
    disabled_employees = int(disabled_employees or 0)
    total_salary = float(total_salary or 0)
    if total_employees <= 0:
        raise ValueError("在职职工总数须 > 0")
    if total_salary < 0:
        raise ValueError("工资总额非法（须 ≥ 0）")
    if disabled_employees < 0 or disabled_employees > total_employees:
        raise ValueError("实际安排残疾人就业人数非法")
    if not (0 < ratio <= 0.5):
        raise ValueError("规定比例非法（0 < ratio ≤ 0.5）")

    status = policy_status("disability_fund_exemption", year)
    if not status["valid"]:
        raise ValueError(status["message"])

    warnings = []
    if total_employees <= EXEMPT_HEADCOUNT:
        return {
            "exempt": True,
            "payable": 0.0,
            "note": f"在职职工 {total_employees} 人 ≤ 30 人，免征残保金（财政部公告2023年第8号）",
            "policy": {"policy_key": "disability_fund_exemption",
                       "policy_basis": status["policy_basis"], "status": "valid"},
            "warnings": [],
        }

    avg_salary = total_salary / total_employees
    shortfall = max(0.0, total_employees * ratio - disabled_employees)
    base_amount = shortfall * avg_salary
    actual_ratio = disabled_employees / total_employees
    if actual_ratio >= ratio:
        tier, payable = "达标", 0.0
    elif actual_ratio >= 0.01:
        tier, payable = "减收50%", base_amount * (1 - TIER_HALF)
        warnings.append("安排比例达 1%（含）以上但未达规定比例，应缴费额减收 50%（8号公告）")
    else:
        tier, payable = "减收90%", base_amount * (1 - TIER_NINETY)
        warnings.append("安排比例低于 1%，应缴费额减收 90%（8号公告）")
    return {
        "exempt": False,
        "tier": tier,
        "shortfall_persons": round(shortfall, 2),
        "avg_salary": round(avg_salary, 2),
        "base_amount": round(base_amount, 2),
        "payable": round(payable, 2),
        "actual_ratio": round(actual_ratio, 4),
        "note": (f"差额 {shortfall:.2f} 人 × 年平均工资 {avg_salary:,.2f} 元"
                 f"（分档：{tier}）"),
        "policy": {"policy_key": "disability_fund_tiered",
                   "policy_basis": status["policy_basis"], "status": "valid"},
        "warnings": warnings,
    }


def exports_dir() -> Path:
    d = get_finance_data_dir() / "dsf" / "exports"
    d.mkdir(parents=True, exist_ok=True)
    return d


def export_filing_xlsx(filing) -> Path:
    """生成残保金申报底稿 xlsx。"""
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "残保金申报底稿"
    ws.append(["残疾人就业保障金申报表（底稿）"])
    ws.append([f"所属年度：{filing.year}    用人单位：{filing.taxpayer_name or '—'}"])
    ws.append([])
    inputs = filing.inputs or {}
    ws.append(["在职职工总数", inputs.get("total_employees", 0)])
    ws.append(["上年在职职工工资总额", inputs.get("total_salary", 0)])
    ws.append(["实际安排残疾人就业人数", inputs.get("disabled_employees", 0)])
    ws.append(["所在地规定比例", f"{(inputs.get('ratio') or 0.015):.1%}"])
    ws.append([])
    calc = filing.calc_result or {}
    ws.append(["免征", "是" if calc.get("exempt") else "否"])
    if not calc.get("exempt"):
        ws.append(["分档", calc.get("tier", "")])
        ws.append(["应缴费额", calc.get("payable", 0)])
        for w in calc.get("warnings", []):
            ws.append(["提示", w])
    ws.append([f"政策依据：{(calc.get('policy') or {}).get('policy_basis', '')}"])
    dest = exports_dir() / f"dsf_{filing.year}_{filing.id}.xlsx"
    wb.save(dest)
    return dest
