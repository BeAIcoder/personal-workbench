# -*- coding: utf-8 -*-
"""残保金计税服务：财政部公告2023年第8号（30人以下免征 + 分档减收）。"""
from __future__ import annotations

from ...config import get_finance_data_dir
from pathlib import Path
from decimal import Decimal, ROUND_HALF_UP


def money(value) -> float:
    """金额/人数两位小数，四舍五入（不用二进制 round）。"""
    return float(Decimal(str(value or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

# 分档减缴（财政部 税务总局公告2023年第8号）：
# - 安排比例 ≥1% 且 <规定比例：按规定应缴费额的 50% 缴纳；
# - 安排比例 <1%：按规定应缴费额的 90% 缴纳（即减收 10%，不是按 10% 征收）。
# 修复 BUGfix FIL-01：原实现 base*(1-0.9)=10%，少征约 9 倍。
TIER_HALF = 0.5
TIER_NINETY = 0.9

# 免征门槛：在职职工 30 人（含）以下
EXEMPT_HEADCOUNT = 30


def calc_dsf(year: str, total_employees: int, total_salary: float,
             disabled_employees: int, ratio: float = 0.015,
             local_avg_wage: float | None = None) -> dict:
    """残保金计征。

    year: 缴费所属年度（校验 8号公告有效期）
    total_employees: 上年在职职工总数
    total_salary: 上年在职职工工资总额（元）
    disabled_employees: 实际安排残疾人就业人数
    ratio: 所在地规定比例（默认 1.5%）
    local_avg_wage: 当地上年度社会平均工资（年，元）。财税〔2015〕98 号：
        年平均工资超过当地社平 2 倍的，按 2 倍计征；不传则不计上限并给出 warning。
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
    if local_avg_wage is not None and float(local_avg_wage) <= 0:
        raise ValueError("当地社会平均工资须 > 0")

    status = policy_status("disability_fund_exemption", year)
    if not status["valid"]:
        raise ValueError(status["message"])

    warnings = []
    avg_salary = total_salary / total_employees
    # 差额人数按两位小数（电子税局申报口径；实测税局测算表 0.345 → 0.35 后计征）。
    # 用 Decimal 四舍五入，避免二进制浮点把 0.345 舍成 0.34。
    shortfall = money(max(0.0, total_employees * ratio - disabled_employees))
    # 财税〔2015〕98 号：年平均工资超过当地社平 2 倍的，按 2 倍计征。
    # 未传社平时不封锁计算，但必须显式 warning，避免用户误以为已校验上限。
    wage_cap = float(local_avg_wage) * 2 if local_avg_wage is not None else None
    charge_wage = min(avg_salary, wage_cap) if wage_cap is not None else avg_salary
    if wage_cap is None:
        warnings.append("未提供当地社会平均工资，未校验「年平均工资 2 倍」计征上限")
    elif avg_salary > wage_cap:
        warnings.append(
            f"年平均工资 {avg_salary:,.2f} 元超过当地社平 2 倍 {wage_cap:,.2f} 元，已按 2 倍封顶计征"
        )
    base_amount = shortfall * charge_wage

    if total_employees <= EXEMPT_HEADCOUNT:
        return {
            "exempt": True,
            "payable": 0.0,
            "shortfall_persons": shortfall,
            "avg_salary": round(avg_salary, 2),
            "local_avg_wage": round(float(local_avg_wage), 2) if local_avg_wage is not None else None,
            "wage_cap": round(wage_cap, 2) if wage_cap is not None else None,
            "charge_wage": round(charge_wage, 2),
            "base_amount": round(base_amount, 2),
            "note": (f"在职职工 {total_employees} 人 ≤ 30 人，免征残保金"
                     f"（优惠前应纳 {base_amount:,.2f} 元）"),
            "policy": {"policy_key": "disability_fund_exemption",
                       "policy_basis": status["policy_basis"], "status": "valid"},
            "warnings": warnings,
        }

    actual_ratio = disabled_employees / total_employees
    if actual_ratio >= ratio:
        tier, payable = "达标", 0.0
    elif actual_ratio >= 0.01:
        tier, payable = "减收50%", base_amount * TIER_HALF
        warnings.append("安排比例达 1%（含）以上但未达规定比例，按应缴费额 50% 缴纳（8号公告）")
    else:
        tier, payable = "减收10%", base_amount * TIER_NINETY
        warnings.append("安排比例低于 1%，按应缴费额 90% 缴纳（8号公告，减收 10%）")
    return {
        "exempt": False,
        "tier": tier,
        "shortfall_persons": round(shortfall, 2),
        "avg_salary": round(avg_salary, 2),
        "local_avg_wage": round(float(local_avg_wage), 2) if local_avg_wage is not None else None,
        "wage_cap": round(wage_cap, 2) if wage_cap is not None else None,
        "charge_wage": round(charge_wage, 2),
        "base_amount": round(base_amount, 2),
        "payable": round(payable, 2),
        "actual_ratio": round(actual_ratio, 4),
        "note": (f"差额 {shortfall:.2f} 人 × 计征工资 {charge_wage:,.2f} 元"
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
