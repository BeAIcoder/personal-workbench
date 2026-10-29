# -*- coding: utf-8 -*-
"""个税计税服务：工资薪金累计预扣法（按员工×月度逐月累计模拟）。

政策口径：
- 减除费用 5000 元/月（个人所得税法）；
- 专项附加扣除标准：子女教育/3岁以下婴幼儿照护 2000 元/月/孩、赡养老人 3000 元/月
  （非独生≤1500）（国发〔2023〕13号，2023-01-01 起长期）；
- 预扣率表：综合所得年度预扣率（3%~45% 七级）。
"""
from __future__ import annotations

from ...config import get_finance_data_dir
from pathlib import Path

# 减除费用（元/月）
MONTHLY_DEDUCTION = 5000.0

# 专项附加扣除标准（元/月，国发〔2023〕13号）
SPECIAL_DEDUCTION_STANDARDS = {
    "children_education": {"label": "子女教育", "monthly_per_child": 2000},
    "infant_care": {"label": "3岁以下婴幼儿照护", "monthly_per_child": 2000},
    "elderly": {"label": "赡养老人", "monthly_max": 3000, "shared_max": 1500},
    "housing": {"label": "住房贷款利息/住房租金", "monthly_max": 1500},
    "continuing_education": {"label": "继续教育", "monthly_max": 400},
    "medical": {"label": "大病医疗（年度限额8万，月度按实际）", "monthly_max": None},
}

# 综合所得年度预扣率表 [(上限元, 税率, 速算扣除数)]
BRACKETS = [
    (36_000, 0.03, 0.0),
    (144_000, 0.10, 2_520.0),
    (300_000, 0.20, 16_920.0),
    (420_000, 0.25, 31_920.0),
    (660_000, 0.30, 52_920.0),
    (960_000, 0.35, 85_920.0),
    (float("inf"), 0.45, 181_920.0),
]


def _bracket(cumulative_taxable: float) -> tuple[float, float]:
    """取累计应纳税所得额对应的 (税率, 速算扣除数)。"""
    for cap, rate, quick in BRACKETS:
        if cumulative_taxable <= cap:
            return rate, quick
    return 0.45, 181_920.0


def calc_filing(year: str, rows: list[dict]) -> dict:
    """累计预扣法逐月模拟。

    rows: [{month(1-12), name, salary, social_insurance, special_additional, other_deductions}]
    （金额均为元/月；special_additional 为该员工当月专项附加扣除合计）
    返回 {employees: [{name, months: [...]}], totals: {tax_withheld}}
    """
    if not str(year).isdigit() or len(str(year)) != 4:
        raise ValueError("year 须为 4 位年度")
    if not rows:
        raise ValueError("员工月度行不能为空")
    grouped: dict[str, list[dict]] = {}
    for idx, r in enumerate(rows, 1):
        name = str(r.get("name") or "").strip()
        if not name:
            raise ValueError(f"第 {idx} 行员工姓名为空")
        month = int(r.get("month") or 0)
        if not (1 <= month <= 12):
            raise ValueError(f"第 {idx} 行月份非法（1-12）")
        grouped.setdefault(name, []).append(r)

    employees = []
    total_withheld = 0.0
    for name, rs in grouped.items():
        rs.sort(key=lambda r: int(r["month"]))
        months_out = []
        cum_taxable = 0.0
        cum_withheld = 0.0
        for r in rs:
            m = int(r["month"])
            salary = _num(r.get("salary"), f"{name} {m}月工资")
            social = _num(r.get("social_insurance") or 0, f"{name} {m}月社保") 
            special = _num(r.get("special_additional") or 0, f"{name} {m}月专项附加")
            other = _num(r.get("other_deductions") or 0, f"{name} {m}月其他扣除")
            for v in (social, special, other):
                if v < 0:
                    raise ValueError(f"{name} {m}月扣除项非法（须 ≥ 0）")
            if special > 30_000:
                raise ValueError(f"{name} {m}月专项附加扣除异常（>{30_000}，请核实）")
            cum_taxable += max(0.0, salary - MONTHLY_DEDUCTION - social - special - other)
            rate, quick = _bracket(cum_taxable)
            cumulative = max(0.0, cum_taxable * rate - quick)
            monthly = round(cumulative - cum_withheld, 2)
            months_out.append({
                "month": m,
                "taxable_income": round(max(0.0, salary - 5000.0 - social - special - other), 2),
                "cumulative_taxable": round(cum_taxable, 2),
                "rate": rate,
                "cumulative_withholding": round(cumulative, 2),
                "monthly_withholding": monthly,
            })
            cum_withheld = cumulative
            total_withheld += monthly
        employees.append({"name": name, "months": months_out})
    return {
        "employees": employees,
        "totals": {"tax_withheld": round(total_withheld, 2)},
        "policy": {
            "policy_basis": "个人所得税法+国发〔2023〕13号",
            "monthly_deduction": MONTHLY_DEDUCTION,
            "special_deduction_standards": SPECIAL_DEDUCTION_STANDARDS,
        },
    }


def _num(v, label: str) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        raise ValueError(f"{label} 非法数值")


def exports_dir() -> Path:
    d = get_finance_data_dir() / "iit" / "exports"
    d.mkdir(parents=True, exist_ok=True)
    return d


def export_filing_xlsx(filing) -> Path:
    """生成扣缴申报底稿 xlsx（每员工每月一行 + 汇总行）。"""
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "个税扣缴底稿"
    ws.append(["个人所得税扣缴申报表（底稿）"])
    ws.append([f"所属年度：{filing.year}    扣缴义务人：{filing.taxpayer_name or '—'}"])
    ws.append([])
    ws.append(["员工", "月份", "当月应纳税所得额", "累计应纳税所得额", "预扣率",
               "累计应预扣", "本月应预扣"])
    calc = filing.calc_result or {}
    for emp in calc.get("employees", []):
        for m in emp.get("months", []):
            ws.append([emp["name"], m["month"], m["taxable_income"],
                       m["cumulative_taxable"], f"{m['rate']:.0%}",
                       m["cumulative_withholding"], m["monthly_withholding"]])
    ws.append([])
    ws.append(["合计应预扣预缴", calc.get("totals", {}).get("tax_withheld", 0)])
    ws.append([f"政策依据：{(calc.get('policy') or {}).get('policy_basis', '')}"])
    dest = exports_dir() / f"iit_{filing.year}_{filing.id}.xlsx"
    wb.save(dest)
    return dest
