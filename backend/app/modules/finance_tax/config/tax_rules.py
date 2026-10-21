"""
税收优惠规则配置模块
包含小型微利企业条件、研发费用加计扣除、纳税调整项限额规则等
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Callable, Any
from enum import Enum


class DeductionCategory(Enum):
    """扣除类别"""
    ENTERTAINMENT = "业务招待费"
    ADVERTISING = "广告费"
    CHARITABLE_DONATION = "公益性捐赠"
    EMPLOYEE_WELFARE = "职工福利费"
    EMPLOYEE_EDUCATION = "职工教育经费"
    LABOR_UNION = "工会经费"
    RD_EXPENSE = "研发费用加计扣除"


class IndustryType(Enum):
    """行业类型（用于广告费限额判断）"""
    GENERAL = "一般企业"
    COSMETICS = "化妆品制造或销售"
    PHARMACEUTICAL = "医药制造"
    BEVERAGE = "饮料制造"
    TOBACCO = "烟草"
    RESTRICTED = "限制行业"  # 小型微利企业判断用


# ============================================================
# 小型微利企业认定条件
# ============================================================

@dataclass
class SmallMicroEnterpriseCriteria:
    """小型微利企业认定条件"""
    employee_count_max: int = 300  # 从业人数不超过300人
    total_assets_max: float = 50_000_000  # 资产总额不超过5000万元
    taxable_income_max: float = 3_000_000  # 应纳税所得额不超过300万元
    restricted_industries: List[str] = field(default_factory=lambda: [
        "国家限制和禁止行业",
    ])

    def is_qualified(
        self,
        employee_count: int,
        total_assets: float,
        taxable_income: float,
        industry: str,
    ) -> Dict[str, Any]:
        """
        判断是否符合小型微利企业条件

        Args:
            employee_count: 从业人数
            total_assets: 资产总额（元）
            taxable_income: 应纳税所得额（元）
            industry: 行业名称

        Returns:
            包含判断结果和详细信息的字典
        """
        conditions = {
            "employee_count": {
                "value": employee_count,
                "threshold": self.employee_count_max,
                "passed": employee_count <= self.employee_count_max,
            },
            "total_assets": {
                "value": total_assets,
                "threshold": self.total_assets_max,
                "passed": total_assets <= self.total_assets_max,
            },
            "taxable_income": {
                "value": taxable_income,
                "threshold": self.taxable_income_max,
                "passed": taxable_income <= self.taxable_income_max,
            },
            "industry": {
                "value": industry,
                "restricted": industry in self.restricted_industries,
                "passed": industry not in self.restricted_industries,
            },
        }

        all_passed = all(c["passed"] for c in conditions.values())

        return {
            "qualified": all_passed,
            "conditions": conditions,
            "failed_items": [
                key for key, value in conditions.items()
                if not value["passed"]
            ],
        }


# 小型微利企业认定条件实例
SMALL_MICRO_CRITERIA = SmallMicroEnterpriseCriteria()


# ============================================================
# 研发费用加计扣除规则
# ============================================================

@dataclass
class RDExpenseSuperDeductionRule:
    """研发费用加计扣除规则"""
    name: str
    deduction_rate: float  # 加计扣除比例，1.0 表示 100%，1.2 表示 120%
    applicable_enterprises: List[str]  # 适用企业类型
    description: str


RD_EXPENSE_SUPER_DEDUCTION_RULES: List[RDExpenseSuperDeductionRule] = [
    RDExpenseSuperDeductionRule(
        name="一般企业研发费用加计扣除",
        deduction_rate=1.0,  # 100%加计扣除
        applicable_enterprises=["一般企业"],
        description="一般企业开展研发活动中实际发生的研发费用，未形成无形资产计入当期损益的，在按规定据实扣除的基础上，再按照实际发生额的100%在税前加计扣除",
    ),
    RDExpenseSuperDeductionRule(
        name="集成电路企业研发费用加计扣除",
        deduction_rate=1.2,  # 120%加计扣除
        applicable_enterprises=["集成电路企业"],
        description="集成电路企业和工业母机企业开展研发活动中实际发生的研发费用，未形成无形资产计入当期损益的，在按规定据实扣除的基础上，再按照实际发生额的120%在税前加计扣除",
    ),
    RDExpenseSuperDeductionRule(
        name="工业母机企业研发费用加计扣除",
        deduction_rate=1.2,  # 120%加计扣除
        applicable_enterprises=["工业母机企业"],
        description="集成电路企业和工业母机企业开展研发活动中实际发生的研发费用，未形成无形资产计入当期损益的，在按规定据实扣除的基础上，再按照实际发生额的120%在税前加计扣除",
    ),
]


def get_rd_deduction_rate(enterprise_type: str = "一般企业") -> float:
    """
    获取研发费用加计扣除比例

    Args:
        enterprise_type: 企业类型

    Returns:
        加计扣除比例
    """
    for rule in RD_EXPENSE_SUPER_DEDUCTION_RULES:
        if enterprise_type in rule.applicable_enterprises:
            return rule.deduction_rate
    # 默认返回一般企业比例
    return 1.0


def calculate_rd_expense_deduction(
    actual_rd_expense: float,
    enterprise_type: str = "一般企业",
) -> Dict[str, float]:
    """
    计算研发费用加计扣除金额

    Args:
        actual_rd_expense: 实际发生的研发费用
        enterprise_type: 企业类型

    Returns:
        包含扣除明细的字典
    """
    rate = get_rd_deduction_rate(enterprise_type)
    super_deduction_amount = actual_rd_expense * rate
    total_deduction = actual_rd_expense + super_deduction_amount

    return {
        "actual_rd_expense": actual_rd_expense,
        "deduction_rate": rate,
        "super_deduction_amount": super_deduction_amount,
        "total_deduction_before_tax": total_deduction,
    }


# ============================================================
# 纳税调整项限额规则
# ============================================================

@dataclass
class DeductionLimitRule:
    """扣除限额规则"""
    category: DeductionCategory
    name: str
    description: str
    # 计算限额的函数，返回 (允许扣除金额, 纳税调增金额)
    calculate_limit: Callable[..., Dict[str, float]]


def calculate_entertainment_expense_limit(
    expense_amount: float,
    revenue: float,
) -> Dict[str, float]:
    """
    计算业务招待费税前扣除限额

    规则：按照发生额的60%扣除，但最高不得超过当年销售（营业）收入的5‰

    Args:
        expense_amount: 业务招待费发生额
        revenue: 销售（营业）收入

    Returns:
        包含扣除限额和纳税调增金额的字典
    """
    limit_by_60_percent = expense_amount * 0.6
    limit_by_revenue = revenue * 0.005  # 收入的5‰
    # 收入为 0 时不存在 5‰ 约束（收入为 0 不是"全额不得扣除"），
    # 仅受 60% 限额约束；负收入同样视为无 5‰ 约束。
    if revenue <= 0:
        allowed_deduction = limit_by_60_percent
    else:
        allowed_deduction = min(limit_by_60_percent, limit_by_revenue)
    tax_adjustment = expense_amount - allowed_deduction

    return {
        "expense_amount": expense_amount,
        "revenue": revenue,
        "limit_by_60_percent": limit_by_60_percent,
        "limit_by_revenue_5_per_mille": limit_by_revenue,
        "allowed_deduction": allowed_deduction,
        "tax_adjustment_increase": max(0, tax_adjustment),
    }


def calculate_advertising_expense_limit(
    expense_amount: float,
    revenue: float,
    industry: IndustryType = IndustryType.GENERAL,
) -> Dict[str, float]:
    """
    计算广告费和业务宣传费税前扣除限额

    规则：
    - 一般企业：不超过当年销售（营业）收入15%的部分准予扣除
    - 化妆品、医药、饮料制造：不超过30%的部分准予扣除
    - 烟草：不得扣除（0%）

    Args:
        expense_amount: 广告费和业务宣传费发生额
        revenue: 销售（营业）收入
        industry: 行业类型

    Returns:
        包含扣除限额和纳税调增金额的字典
    """
    rate_map = {
        IndustryType.GENERAL: 0.15,
        IndustryType.COSMETICS: 0.30,
        IndustryType.PHARMACEUTICAL: 0.30,
        IndustryType.BEVERAGE: 0.30,
        IndustryType.TOBACCO: 0.0,
    }

    rate = rate_map.get(industry, 0.15)
    allowed_deduction = min(expense_amount, revenue * rate)
    tax_adjustment = expense_amount - allowed_deduction

    return {
        "expense_amount": expense_amount,
        "revenue": revenue,
        "industry": industry.value,
        "deduction_rate": rate,
        "allowed_deduction": allowed_deduction,
        "tax_adjustment_increase": max(0, tax_adjustment),
    }


def calculate_charitable_donation_limit(
    donation_amount: float,
    accounting_profit: float,
) -> Dict[str, float]:
    """
    计算公益性捐赠税前扣除限额

    规则：企业发生的公益性捐赠支出，在年度利润总额12%以内的部分准予扣除

    Args:
        donation_amount: 公益性捐赠发生额
        accounting_profit: 年度会计利润总额

    Returns:
        包含扣除限额和纳税调增金额的字典
    """
    limit = accounting_profit * 0.12
    allowed_deduction = min(donation_amount, limit)
    tax_adjustment = donation_amount - allowed_deduction

    return {
        "donation_amount": donation_amount,
        "accounting_profit": accounting_profit,
        "limit_12_percent": limit,
        "allowed_deduction": allowed_deduction,
        "tax_adjustment_increase": max(0, tax_adjustment),
    }


def calculate_employee_welfare_limit(
    expense_amount: float,
    total_salary: float,
) -> Dict[str, float]:
    """
    计算职工福利费税前扣除限额

    规则：不超过工资薪金总额14%的部分准予扣除

    Args:
        expense_amount: 职工福利费发生额
        total_salary: 工资薪金总额

    Returns:
        包含扣除限额和纳税调增金额的字典
    """
    limit = total_salary * 0.14
    allowed_deduction = min(expense_amount, limit)
    tax_adjustment = expense_amount - allowed_deduction

    return {
        "expense_amount": expense_amount,
        "total_salary": total_salary,
        "limit_14_percent": limit,
        "allowed_deduction": allowed_deduction,
        "tax_adjustment_increase": max(0, tax_adjustment),
    }


def calculate_employee_education_limit(
    expense_amount: float,
    total_salary: float,
) -> Dict[str, float]:
    """
    计算职工教育经费税前扣除限额

    规则：不超过工资薪金总额8%的部分准予扣除，超过部分准予在以后纳税年度结转

    Args:
        expense_amount: 职工教育经费发生额
        total_salary: 工资薪金总额

    Returns:
        包含扣除限额和纳税调增金额的字典
    """
    limit = total_salary * 0.08
    allowed_deduction = min(expense_amount, limit)
    tax_adjustment = expense_amount - allowed_deduction
    carry_forward = max(0, expense_amount - limit)

    return {
        "expense_amount": expense_amount,
        "total_salary": total_salary,
        "limit_8_percent": limit,
        "allowed_deduction": allowed_deduction,
        "tax_adjustment_increase": max(0, tax_adjustment),
        "carry_forward_to_next_year": carry_forward,
    }


def calculate_labor_union_fund_limit(
    expense_amount: float,
    total_salary: float,
) -> Dict[str, float]:
    """
    计算工会经费税前扣除限额

    规则：不超过工资薪金总额2%的部分准予扣除

    Args:
        expense_amount: 工会经费发生额
        total_salary: 工资薪金总额

    Returns:
        包含扣除限额和纳税调增金额的字典
    """
    limit = total_salary * 0.02
    allowed_deduction = min(expense_amount, limit)
    tax_adjustment = expense_amount - allowed_deduction

    return {
        "expense_amount": expense_amount,
        "total_salary": total_salary,
        "limit_2_percent": limit,
        "allowed_deduction": allowed_deduction,
        "tax_adjustment_increase": max(0, tax_adjustment),
    }


# ============================================================
# 汇总所有纳税调整限额计算函数
# ============================================================

DEDUCTION_LIMIT_CALCULATORS: Dict[DeductionCategory, Callable[..., Dict[str, float]]] = {
    DeductionCategory.ENTERTAINMENT: calculate_entertainment_expense_limit,
    DeductionCategory.ADVERTISING: calculate_advertising_expense_limit,
    DeductionCategory.CHARITABLE_DONATION: calculate_charitable_donation_limit,
    DeductionCategory.EMPLOYEE_WELFARE: calculate_employee_welfare_limit,
    DeductionCategory.EMPLOYEE_EDUCATION: calculate_employee_education_limit,
    DeductionCategory.LABOR_UNION: calculate_labor_union_fund_limit,
}
