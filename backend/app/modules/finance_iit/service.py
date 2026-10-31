# -*- coding: utf-8 -*-
"""个税计税服务：工资薪金累计预扣法（按员工×月度逐月累计模拟）。

政策口径：
- 减除费用 5000 元/月（个人所得税法）；
- 专项附加扣除标准：子女教育/3岁以下婴幼儿照护 2000 元/月/孩、赡养老人 3000 元/月
  （非独生≤1500）（国发〔2023〕13号，2023-01-01 起长期）；
- 预扣率表：综合所得年度预扣率（3%~45% 七级）。
"""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from ...config import get_finance_data_dir
from ...finance_iit_common import BRACKETS, MONTHLY_DEDUCTION, calc_cumulative_withholding
from ...utils_money import money

# 专项附加扣除标准（元/月，国发〔2023〕13号）
SPECIAL_DEDUCTION_STANDARDS = {
    "children_education": {"label": "子女教育", "monthly_per_child": 2000},
    "infant_care": {"label": "3岁以下婴幼儿照护", "monthly_per_child": 2000},
    "elderly": {"label": "赡养老人", "monthly_max": 3000, "shared_max": 1500},
    "housing": {"label": "住房贷款利息/住房租金", "monthly_max": 1500},
    "continuing_education": {"label": "继续教育", "monthly_max": 400},
    "medical": {"label": "大病医疗（年度限额8万，月度按实际）", "monthly_max": None},
}


def calc_filing(year: str, rows: list[dict]) -> dict:
    """累计预扣法逐月模拟。

    rows: [{month(1-12), name, salary, social_insurance, special_additional, other_deductions}]
    （金额均为元/月；special_additional 为该员工当月专项附加扣除合计）
    返回 {employees: [{name, months: [...]}], totals: {tax_withheld}}
    """
    from ..finance_tax.config.policy_validity import policy_status

    if not str(year).isdigit() or len(str(year)) != 4:
        raise ValueError("year 须为 4 位年度")
    if not rows:
        raise ValueError("员工月度行不能为空")
    special_policy = policy_status("iit_special_deductions", year)
    if not special_policy["valid"]:
        raise ValueError(special_policy["message"])
    grouped: dict[str, list[dict]] = {}
    display_names: dict[str, str] = {}
    for idx, r in enumerate(rows, 1):
        name = str(r.get("name") or "").strip()
        if not name:
            raise ValueError(f"第 {idx} 行员工姓名为空")
        month = int(r.get("month") or 0)
        if not (1 <= month <= 12):
            raise ValueError(f"第 {idx} 行月份非法（1-12）")
        # 工号优先：同名员工必须按工号分开累计（BUGfix FIL-08）；无工号时回退姓名
        employee_id = str(r.get("employee_id") or "").strip()
        key = employee_id or name
        display_names.setdefault(key, name)
        grouped.setdefault(key, []).append(r)

    employees = []
    total_withheld = 0.0
    warnings: list[str] = []
    for key, rs in grouped.items():
        name = display_names[key]
        rs.sort(key=lambda r: int(r["month"]))
        seen_months: set[int] = set()
        starts: list[int] = []
        for r in rs:
            m = int(r["month"])
            if m in seen_months:
                raise ValueError(f"{name} {m}月存在重复行（同一员工同一月份只能一行）")
            seen_months.add(m)
            start = r.get("employment_start_month")
            if start not in (None, ""):
                s = int(start)
                if not (1 <= s <= 12):
                    raise ValueError(f"{name} 任职起始月非法（1-12）")
                starts.append(s)
        first_month = min(seen_months)
        start_month = min(starts) if starts else first_month
        if start_month > first_month:
            raise ValueError(f"{name} 任职起始月 {start_month} 晚于最早申报月份 {first_month}")
        missing = [m for m in range(start_month, max(seen_months) + 1)
                   if m not in seen_months]
        if missing:
            warnings.append(
                f"{name}（{key}）缺少 {missing} 月数据：按无收入、但累计减除费用仍按任职月份计算"
            )

        months_out = []
        cum_income = Decimal("0.00")
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
            # 累计预扣法：累计收入 − 累计扣除；减除费用按「任职月份数 × 5000」，
            # 缺月不会少计（BUGfix FIL-09）。允许负累计冲减以后月份（FIL-02）。
            current_base = money(
                Decimal(str(salary)) - Decimal(str(social))
                - Decimal(str(special)) - Decimal(str(other))
            )
            cum_income += Decimal(str(current_base))
            months_count = m - start_month + 1
            withholding = calc_cumulative_withholding(cum_income, cum_withheld, months_count)
            monthly = withholding["monthly_withholding"]
            months_out.append({
                "month": m,
                "taxable_income": money(max(0.0, current_base - MONTHLY_DEDUCTION)),
                "cumulative_taxable": withholding["cumulative_taxable"],
                "rate": withholding["rate"],
                "cumulative_withholding": withholding["cumulative_withholding"],
                "monthly_withholding": monthly,
            })
            cum_withheld = money(cum_withheld + monthly)
            total_withheld += monthly
        employees.append({
            "employee_id": key if key != name else "",
            "name": name,
            "employment_start_month": start_month,
            "missing_months": missing,
            "months": months_out,
        })
    return {
        "employees": employees,
        "warnings": warnings,
        "totals": {"tax_withheld": money(total_withheld)},
        "policy": {
            "policy_key": special_policy["policy_key"],
            "status": special_policy["status"],
            "policy_basis": f"个人所得税法+{special_policy['policy_basis']}",
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
