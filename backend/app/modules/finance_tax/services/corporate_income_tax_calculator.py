"""
企业所得税完整计算引擎
实现应纳税所得额计算、纳税调整项计算、税额计算、小型微利企业判定、弥补亏损等功能

数据单位约定：本模块所有输入输出均以"万元"为单位（与数据库模型保持一致）。
内部调用 tax_rules.py 中函数时会自动转换为"元"。
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any
from enum import Enum

from ..config.tax_rules import (
    SMALL_MICRO_CRITERIA,
    IndustryType,
    DeductionCategory,
    calculate_entertainment_expense_limit,
    calculate_advertising_expense_limit,
    calculate_charitable_donation_limit,
    calculate_employee_welfare_limit,
    calculate_employee_education_limit,
    calculate_labor_union_fund_limit,
    calculate_rd_expense_deduction,
    get_rd_deduction_rate,
)
from ..config.tax_rates import (
    CORPORATE_INCOME_TAX_RATES,
    get_standard_cit_rate,
    get_high_tech_cit_rate,
    get_small_micro_cit_rate,
)
from ....utils_money import money


# ============================================================
# 企业类型枚举
# ============================================================

class EnterpriseTaxType(Enum):
    """企业所得税适用类型"""
    STANDARD = "一般企业"          # 25%
    HIGH_TECH = "高新技术企业"     # 15%
    SMALL_MICRO = "小型微利企业"   # 5% 实际税负


# ============================================================
# 数据类定义
# ============================================================

@dataclass
class TaxAdjustmentInput:
    """纳税调整项输入数据（单位：万元）"""
    # 业务招待费
    business_entertainment: float = 0.0
    # 广告费和业务宣传费
    advertising_expense: float = 0.0
    # 公益性捐赠支出
    donation_expense: float = 0.0
    # 研发费用
    rnd_expense: float = 0.0
    # 职工福利费
    welfare_expense: float = 0.0
    # 职工教育经费
    education_expense: float = 0.0
    # 工会经费
    union_expense: float = 0.0
    # 罚款、滞纳金等（不得税前扣除，全额调增）
    penalties: float = 0.0
    # 设备购置金额（用于500万以下一次性扣除判断）
    equipment_purchase: float = 0.0
    # 设备当年已提折旧（万元）：一次性扣除为税会差异，调减只取「购置额 − 当年折旧」
    equipment_depreciation: float = 0.0


@dataclass
class EnterpriseInfo:
    """企业基本信息"""
    employee_count: int = 0              # 从业人数
    total_assets: float = 0.0            # 资产总额（万元）
    industry: str = "一般行业"            # 行业名称
    is_restricted_industry: bool = False # 是否限制行业
    is_high_tech: bool = False           # 是否高新技术企业
    enterprise_type: str = "一般企业"     # 企业类型（用于研发加计扣除）
    advertising_industry: IndustryType = IndustryType.GENERAL  # 广告费行业类型
    tax_year: str = ""                   # 所属年度（可选）：小微/研发加计政策有效期校验用
    # 高新与小微同时符合时的显式选择："high_tech" / "small_micro" / ""（不选=阻断）
    preferential_choice: str = ""


@dataclass
class LossCarryForward:
    """以前年度亏损信息（单位：万元）"""
    # 各年度可弥补亏损额，key为年度（如2021, 2022），value为可弥补亏损额
    # 亏损最多可向后结转5年（高新技术企业 10 年，由 carryforward_years 指定）
    losses_by_year: Dict[int, float] = field(default_factory=dict)
    # 当前申报年度：传入后按「结转年限」排除过期亏损（BUGfix TAX-07）
    tax_year: int | None = None
    # 结转年限：一般企业 5 年；高新技术企业 10 年
    carryforward_years: int = 5

    def get_total_available_loss(self) -> float:
        """获取可弥补亏损总额"""
        return sum(self.losses_by_year.values())


@dataclass
class CalculationResult:
    """计算结果"""
    # 小型微利企业判定
    is_small_micro: bool = False
    small_micro_details: Dict[str, Any] = field(default_factory=dict)

    # 企业类型判定
    enterprise_tax_type: EnterpriseTaxType = EnterpriseTaxType.STANDARD
    applicable_tax_rate: float = 0.25

    # 纳税调整明细
    adjustments_detail: Dict[str, Any] = field(default_factory=dict)
    total_adjustment_increase: float = 0.0  # 纳税调增合计（万元）
    total_adjustment_decrease: float = 0.0  # 纳税调减合计（万元）

    # 弥补亏损
    loss_carry_forward_used: float = 0.0    # 本期弥补的以前年度亏损（万元）
    loss_used_by_year: Dict[int, float] = field(default_factory=dict)  # 各年度实际弥补额
    loss_carry_forward_remaining: Dict[int, float] = field(default_factory=dict)
    loss_carry_forward_expired: Dict[int, float] = field(default_factory=dict)  # 已过期不可弥补

    # 核心计算结果
    accounting_profit: float = 0.0          # 利润总额（万元）
    taxable_income_before_loss: float = 0.0 # 弥补亏损前应纳税所得额（万元）
    taxable_income_after_loss: float = 0.0  # 弥补亏损后应纳税所得额（万元）

    # 税额计算
    tax_amount: float = 0.0                 # 应纳所得税额（万元）
    policy_status: str = ""                 # 优惠政策有效期状态（valid，未校验时空）


# ============================================================
# 单位转换工具
# ============================================================

def _money_wan(amount_wan: float) -> float:
    """万元金额按「元」取整到分后折回万元（复核 I-3）。

    旧实现统一用 round(万元, 2)，等于 100 元粒度：10.70 元应税会被截成 0，
    计算页与落库/导出因此差到元级。这里改为先折元、按分 HALF_UP，再折回万元。
    """
    return money(float(amount_wan or 0) * 10000.0) / 10000.0



def wan_to_yuan(amount_wan: float) -> float:
    """万元转元"""
    return amount_wan * 10_000


def yuan_to_wan(amount_yuan: float) -> float:
    """元转万元"""
    return amount_yuan / 10_000


# ============================================================
# 核心计算引擎
# ============================================================

class CorporateIncomeTaxCalculator:
    """
    企业所得税完整计算引擎

    计算流程：
    1. 小型微利企业自动判定
    2. 纳税调整项计算（各项限额调整、加计扣除）
    3. 应纳税所得额计算
    4. 弥补亏损计算
    5. 税额计算
    """

    # --- 1. 小型微利企业判定 ---

    @staticmethod
    def check_small_micro_enterprise(
        employee_count: int,
        total_assets_wan: float,
        taxable_income_wan: float,
        is_restricted_industry: bool,
        industry_name: str = "一般行业",
    ) -> Dict[str, Any]:
        """
        判定是否符合小型微利企业条件

        Args:
            employee_count: 从业人数
            total_assets_wan: 资产总额（万元）
            taxable_income_wan: 应纳税所得额（万元）
            is_restricted_industry: 是否限制行业
            industry_name: 行业名称

        Returns:
            判定结果详情
        """
        # 行业名称映射：限制行业使用特定名称
        if is_restricted_industry:
            effective_industry = "国家限制和禁止行业"
        else:
            effective_industry = industry_name

        result = SMALL_MICRO_CRITERIA.is_qualified(
            employee_count=employee_count,
            total_assets=wan_to_yuan(total_assets_wan),
            taxable_income=wan_to_yuan(taxable_income_wan),
            industry=effective_industry,
        )

        return result

    @staticmethod
    def determine_enterprise_tax_type(
        is_high_tech: bool,
        small_micro_result: Dict[str, Any],
        preferential_choice: str = "",
    ) -> EnterpriseTaxType:
        """
        确定企业适用的所得税类型

        优先级：高新技术企业 > 小型微利企业 > 一般企业。
        两类优惠**不得叠加**（台账 L-056）：同时符合且未显式选择时抛 ValueError
        阻断，由用户明确选择后再计税——本引擎不代为选择，也不臆造叠加口径。

        Args:
            is_high_tech: 是否高新技术企业
            small_micro_result: 小型微利企业判定结果
            preferential_choice: 同时符合时的选择："high_tech" / "small_micro"

        Returns:
            企业类型
        """
        both = bool(is_high_tech) and bool(small_micro_result.get("qualified", False))
        if both:
            choice = (preferential_choice or "").strip()
            if choice == "small_micro":
                return EnterpriseTaxType.SMALL_MICRO
            if choice == "high_tech":
                return EnterpriseTaxType.HIGH_TECH
            raise ValueError(
                "该企业同时符合高新技术企业（15%）与小型微利企业（5%）条件，两项优惠不得叠加。"
                "请明确选择其一（preferential_choice=high_tech 或 small_micro）后重试；"
                "本系统不代为选择，适用哪一项请按主管税务机关口径确认"
            )
        if is_high_tech:
            return EnterpriseTaxType.HIGH_TECH
        elif small_micro_result.get("qualified", False):
            return EnterpriseTaxType.SMALL_MICRO
        else:
            return EnterpriseTaxType.STANDARD

    @staticmethod
    def get_applicable_tax_rate(enterprise_tax_type: EnterpriseTaxType) -> float:
        """
        获取适用税率

        Args:
            enterprise_tax_type: 企业类型

        Returns:
            适用税率
        """
        rate_map = {
            EnterpriseTaxType.STANDARD: get_standard_cit_rate(),      # 25%
            EnterpriseTaxType.HIGH_TECH: get_high_tech_cit_rate(),    # 15%
            EnterpriseTaxType.SMALL_MICRO: get_small_micro_cit_rate(),# 5%
        }
        return rate_map.get(enterprise_tax_type, get_standard_cit_rate())

    # --- 2. 纳税调整项计算 ---

    @staticmethod
    def calculate_business_entertainment_adjustment(
        expense_amount_wan: float,
        revenue_wan: float,
    ) -> Dict[str, Any]:
        """
        计算业务招待费纳税调整

        规则：按照发生额的60%扣除，但最高不得超过当年销售（营业）收入的5

        Args:
            expense_amount_wan: 业务招待费发生额（万元）
            revenue_wan: 销售（营业）收入（万元）

        Returns:
            调整明细
        """
        result = calculate_entertainment_expense_limit(
            expense_amount=wan_to_yuan(expense_amount_wan),
            revenue=wan_to_yuan(revenue_wan),
        )
        # 转换为万元单位
        return {
            "category": DeductionCategory.ENTERTAINMENT.value,
            "expense_amount_wan": expense_amount_wan,
            "revenue_wan": revenue_wan,
            "limit_by_60_percent_wan": yuan_to_wan(result["limit_by_60_percent"]),
            "limit_by_revenue_5_per_mille_wan": yuan_to_wan(result["limit_by_revenue_5_per_mille"]),
            "allowed_deduction_wan": yuan_to_wan(result["allowed_deduction"]),
            "tax_adjustment_increase_wan": yuan_to_wan(result["tax_adjustment_increase"]),
        }

    @staticmethod
    def calculate_advertising_expense_adjustment(
        expense_amount_wan: float,
        revenue_wan: float,
        industry: IndustryType = IndustryType.GENERAL,
    ) -> Dict[str, Any]:
        """
        计算广告费和业务宣传费纳税调整

        Args:
            expense_amount_wan: 广告费发生额（万元）
            revenue_wan: 销售（营业）收入（万元）
            industry: 行业类型

        Returns:
            调整明细
        """
        result = calculate_advertising_expense_limit(
            expense_amount=wan_to_yuan(expense_amount_wan),
            revenue=wan_to_yuan(revenue_wan),
            industry=industry,
        )
        return {
            "category": DeductionCategory.ADVERTISING.value,
            "expense_amount_wan": expense_amount_wan,
            "revenue_wan": revenue_wan,
            "industry": industry.value,
            "deduction_rate": result["deduction_rate"],
            "allowed_deduction_wan": yuan_to_wan(result["allowed_deduction"]),
            "tax_adjustment_increase_wan": yuan_to_wan(result["tax_adjustment_increase"]),
            "carry_forward_wan": yuan_to_wan(result.get("carry_forward_to_next_year", 0)),
        }

    @staticmethod
    def calculate_donation_expense_adjustment(
        donation_amount_wan: float,
        accounting_profit_wan: float,
    ) -> Dict[str, Any]:
        """
        计算公益性捐赠纳税调整

        规则：在年度利润总额12%以内的部分准予扣除

        Args:
            donation_amount_wan: 公益性捐赠发生额（万元）
            accounting_profit_wan: 年度会计利润总额（万元）

        Returns:
            调整明细
        """
        result = calculate_charitable_donation_limit(
            donation_amount=wan_to_yuan(donation_amount_wan),
            accounting_profit=wan_to_yuan(accounting_profit_wan),
        )
        return {
            "category": DeductionCategory.CHARITABLE_DONATION.value,
            "donation_amount_wan": donation_amount_wan,
            "accounting_profit_wan": accounting_profit_wan,
            "limit_12_percent_wan": yuan_to_wan(result["limit_12_percent"]),
            "allowed_deduction_wan": yuan_to_wan(result["allowed_deduction"]),
            "tax_adjustment_increase_wan": yuan_to_wan(result["tax_adjustment_increase"]),
        }

    @staticmethod
    def calculate_welfare_expense_adjustment(
        expense_amount_wan: float,
        total_salary_wan: float,
    ) -> Dict[str, Any]:
        """
        计算职工福利费纳税调整

        规则：不超过工资薪金总额14%的部分准予扣除

        Args:
            expense_amount_wan: 职工福利费发生额（万元）
            total_salary_wan: 工资薪金总额（万元）

        Returns:
            调整明细
        """
        result = calculate_employee_welfare_limit(
            expense_amount=wan_to_yuan(expense_amount_wan),
            total_salary=wan_to_yuan(total_salary_wan),
        )
        return {
            "category": DeductionCategory.EMPLOYEE_WELFARE.value,
            "expense_amount_wan": expense_amount_wan,
            "total_salary_wan": total_salary_wan,
            "limit_14_percent_wan": yuan_to_wan(result["limit_14_percent"]),
            "allowed_deduction_wan": yuan_to_wan(result["allowed_deduction"]),
            "tax_adjustment_increase_wan": yuan_to_wan(result["tax_adjustment_increase"]),
        }

    @staticmethod
    def calculate_education_expense_adjustment(
        expense_amount_wan: float,
        total_salary_wan: float,
    ) -> Dict[str, Any]:
        """
        计算职工教育经费纳税调整

        规则：不超过工资薪金总额8%的部分准予扣除，超过部分准予结转

        Args:
            expense_amount_wan: 职工教育经费发生额（万元）
            total_salary_wan: 工资薪金总额（万元）

        Returns:
            调整明细
        """
        result = calculate_employee_education_limit(
            expense_amount=wan_to_yuan(expense_amount_wan),
            total_salary=wan_to_yuan(total_salary_wan),
        )
        return {
            "category": DeductionCategory.EMPLOYEE_EDUCATION.value,
            "expense_amount_wan": expense_amount_wan,
            "total_salary_wan": total_salary_wan,
            "limit_8_percent_wan": yuan_to_wan(result["limit_8_percent"]),
            "allowed_deduction_wan": yuan_to_wan(result["allowed_deduction"]),
            "tax_adjustment_increase_wan": yuan_to_wan(result["tax_adjustment_increase"]),
            "carry_forward_wan": yuan_to_wan(result["carry_forward_to_next_year"]),
        }

    @staticmethod
    def calculate_union_fund_adjustment(
        expense_amount_wan: float,
        total_salary_wan: float,
    ) -> Dict[str, Any]:
        """
        计算工会经费纳税调整

        规则：不超过工资薪金总额2%的部分准予扣除

        Args:
            expense_amount_wan: 工会经费发生额（万元）
            total_salary_wan: 工资薪金总额（万元）

        Returns:
            调整明细
        """
        result = calculate_labor_union_fund_limit(
            expense_amount=wan_to_yuan(expense_amount_wan),
            total_salary=wan_to_yuan(total_salary_wan),
        )
        return {
            "category": DeductionCategory.LABOR_UNION.value,
            "expense_amount_wan": expense_amount_wan,
            "total_salary_wan": total_salary_wan,
            "limit_2_percent_wan": yuan_to_wan(result["limit_2_percent"]),
            "allowed_deduction_wan": yuan_to_wan(result["allowed_deduction"]),
            "tax_adjustment_increase_wan": yuan_to_wan(result["tax_adjustment_increase"]),
        }

    @staticmethod
    def calculate_rd_expense_super_deduction(
        actual_rd_expense_wan: float,
        enterprise_type: str = "一般企业",
        tax_year: str | int | None = None,
    ) -> Dict[str, Any]:
        """
        计算研发费用加计扣除（纳税调减项）

        Args:
            actual_rd_expense_wan: 实际发生的研发费用（万元）
            enterprise_type: 企业类型
            tax_year: 所属年度（可选）；120% 档按 44 号公告校验有效期（台账 L-055）

        Returns:
            加计扣除明细
        """
        result = calculate_rd_expense_deduction(
            actual_rd_expense=wan_to_yuan(actual_rd_expense_wan),
            enterprise_type=enterprise_type,
            tax_year=tax_year,
        )
        return {
            "category": DeductionCategory.RD_EXPENSE.value,
            "actual_rd_expense_wan": actual_rd_expense_wan,
            "enterprise_type": enterprise_type,
            "deduction_rate": result["deduction_rate"],
            "super_deduction_amount_wan": yuan_to_wan(result["super_deduction_amount"]),
            "total_deduction_before_tax_wan": yuan_to_wan(result["total_deduction_before_tax"]),
            # 加计扣除部分作为纳税调减（基础部分已在利润中扣除）
            "tax_adjustment_decrease_wan": yuan_to_wan(result["super_deduction_amount"]),
        }

    @staticmethod
    def calculate_equipment_one_time_deduction(
        equipment_purchase_wan: float,
        equipment_depreciation_wan: float = 0.0,
    ) -> Dict[str, Any]:
        """
        计算设备器具一次性扣除

        规则：企业在2018年1月1日至2027年12月31日期间新购进的设备、器具，
        单位价值不超过500万元的，允许一次性计入当期成本费用在计算应纳税所得额时扣除。

        税会差异（台账 L-057）：会计上已按当年折旧计入成本费用，一次性扣除属于
        税会差异，纳税调减只取「购置额 − 当年已提折旧」，不能整额再扣一次。

        Args:
            equipment_purchase_wan: 设备购置金额（万元）
            equipment_depreciation_wan: 当年已计入损益的折旧（万元）

        Returns:
            扣除明细
        """
        threshold_wan = 500.0  # 500万元
        purchase = float(equipment_purchase_wan or 0.0)
        depreciation = min(max(0.0, float(equipment_depreciation_wan or 0.0)), max(0.0, purchase))

        if purchase <= 0:
            return {
                "category": "设备一次性扣除",
                "equipment_purchase_wan": purchase,
                "current_year_depreciation_wan": 0.0,
                "threshold_wan": threshold_wan,
                "eligible": False,
                "one_time_deduction_wan": 0.0,
                "tax_adjustment_decrease_wan": 0.0,
                "reason": "设备购置金额为零",
            }

        if purchase <= threshold_wan:
            return {
                "category": "设备一次性扣除",
                "equipment_purchase_wan": purchase,
                "current_year_depreciation_wan": _money_wan(depreciation),
                "threshold_wan": threshold_wan,
                "eligible": True,
                "one_time_deduction_wan": purchase,
                "tax_adjustment_decrease_wan": _money_wan(purchase - depreciation),
                "reason": (
                    f"设备价值{purchase}万元不超过{threshold_wan}万元限额，可一次性扣除；"
                    f"当年已提折旧 {depreciation:.2f} 万元不再重复调减"
                ),
            }
        else:
            # 超过500万需按折旧年限分期扣除，不做一次性扣除调整
            return {
                "category": "设备一次性扣除",
                "equipment_purchase_wan": purchase,
                "current_year_depreciation_wan": _money_wan(depreciation),
                "threshold_wan": threshold_wan,
                "eligible": False,
                "one_time_deduction_wan": 0.0,
                "tax_adjustment_decrease_wan": 0.0,
                "reason": f"设备价值{purchase}万元超过{threshold_wan}万元限额，需分期扣除",
            }

    @staticmethod
    def calculate_all_adjustments(
        adjustments: TaxAdjustmentInput,
        revenue_wan: float,
        accounting_profit_wan: float,
        total_salary_wan: float,
        enterprise_type: str = "一般企业",
        advertising_industry: IndustryType = IndustryType.GENERAL,
        tax_year: str | int | None = None,
    ) -> Dict[str, Any]:
        """
        计算所有纳税调整项

        Args:
            adjustments: 纳税调整项输入
            revenue_wan: 营业收入（万元）
            accounting_profit_wan: 利润总额（万元）
            total_salary_wan: 工资薪金总额（万元）
            enterprise_type: 企业类型
            advertising_industry: 广告费行业类型
            tax_year: 所属年度（可选）；研发加计等高比例档按年度校验政策有效期

        Returns:
            包含所有调整明细和合计的字典
        """
        details = {}

        # --- 纳税调增项 ---

        # 1. 业务招待费调整
        details["business_entertainment"] = CorporateIncomeTaxCalculator.calculate_business_entertainment_adjustment(
            adjustments.business_entertainment, revenue_wan
        )

        # 2. 广告费调整
        details["advertising_expense"] = CorporateIncomeTaxCalculator.calculate_advertising_expense_adjustment(
            adjustments.advertising_expense, revenue_wan, advertising_industry
        )

        # 3. 捐赠支出调整
        details["donation_expense"] = CorporateIncomeTaxCalculator.calculate_donation_expense_adjustment(
            adjustments.donation_expense, accounting_profit_wan
        )

        # 4. 职工福利费调整
        details["welfare_expense"] = CorporateIncomeTaxCalculator.calculate_welfare_expense_adjustment(
            adjustments.welfare_expense, total_salary_wan
        )

        # 5. 职工教育经费调整
        details["education_expense"] = CorporateIncomeTaxCalculator.calculate_education_expense_adjustment(
            adjustments.education_expense, total_salary_wan
        )

        # 6. 工会经费调整
        details["union_expense"] = CorporateIncomeTaxCalculator.calculate_union_fund_adjustment(
            adjustments.union_expense, total_salary_wan
        )

        # 7. 罚款、滞纳金（全额调增，不得税前扣除）
        details["penalties"] = {
            "category": "罚款及滞纳金",
            "amount_wan": adjustments.penalties,
            "tax_adjustment_increase_wan": adjustments.penalties,
            "reason": "罚款、滞纳金等不得税前扣除，全额纳税调增",
        }

        # --- 纳税调减项 ---

        # 8. 研发费用加计扣除
        details["rd_expense"] = CorporateIncomeTaxCalculator.calculate_rd_expense_super_deduction(
            adjustments.rnd_expense, enterprise_type, tax_year
        )

        # 9. 设备一次性扣除（调减＝购置额 − 当年已提折旧）
        details["equipment_one_time"] = CorporateIncomeTaxCalculator.calculate_equipment_one_time_deduction(
            adjustments.equipment_purchase, adjustments.equipment_depreciation
        )

        # --- 计算合计 ---
        total_increase = sum(
            d.get("tax_adjustment_increase_wan", 0.0)
            for d in details.values()
        )
        total_decrease = sum(
            d.get("tax_adjustment_decrease_wan", 0.0)
            for d in details.values()
        )

        return {
            "details": details,
            "total_adjustment_increase_wan": _money_wan(total_increase),
            "total_adjustment_decrease_wan": _money_wan(total_decrease),
            "net_adjustment_wan": _money_wan(total_increase - total_decrease),
        }

    # --- 3. 应纳税所得额计算 ---

    @staticmethod
    def calculate_taxable_income_before_loss(
        accounting_profit_wan: float,
        total_adjustment_increase_wan: float,
        total_adjustment_decrease_wan: float,
    ) -> float:
        """
        计算弥补亏损前的应纳税所得额

        公式：应纳税所得额 = 利润总额 + 纳税调增项 - 纳税调减项

        Args:
            accounting_profit_wan: 利润总额（万元）
            total_adjustment_increase_wan: 纳税调增合计（万元）
            total_adjustment_decrease_wan: 纳税调减合计（万元）

        Returns:
            应纳税所得额（万元）
        """
        taxable_income = (
            accounting_profit_wan
            + total_adjustment_increase_wan
            - total_adjustment_decrease_wan
        )
        return _money_wan(taxable_income)

    # --- 4. 弥补亏损计算 ---

    @staticmethod
    def calculate_loss_carry_forward(
        taxable_income_before_loss_wan: float,
        losses: LossCarryForward,
    ) -> Dict[str, Any]:
        """
        计算弥补亏损

        规则：企业纳税年度发生的亏损，准予向以后年度结转，用以后年度的所得弥补，
        但结转年限最长不得超过5年。弥补顺序为先亏先补（按年度从早到晚）。

        Args:
            taxable_income_before_loss_wan: 弥补亏损前应纳税所得额（万元）
            losses: 以前年度亏损信息

        Returns:
            弥补亏损结果
        """
        if taxable_income_before_loss_wan <= 0:
            # 本年亏损，无需弥补
            return {
                "taxable_income_before_loss_wan": taxable_income_before_loss_wan,
                "loss_used_wan": 0.0,
                "taxable_income_after_loss_wan": taxable_income_before_loss_wan,
                "remaining_losses": dict(losses.losses_by_year),
                "used_by_year": {},
                "expired_losses": {},
            }

        remaining_income = taxable_income_before_loss_wan
        remaining_losses = dict(losses.losses_by_year)
        used_by_year = {}
        expired_losses: Dict[int, float] = {}

        # 按年度从早到晚弥补（先亏先补）；传入当前年度时，超过结转年限的亏损
        # 不得再弥补（2020 年亏损可在 2021-2025 弥补，2026 年起过期）。
        current_year = losses.tax_year
        max_years = max(1, int(losses.carryforward_years or 5))
        for year in sorted(remaining_losses.keys()):
            if remaining_income <= 0:
                break
            available_loss = remaining_losses[year]
            if available_loss <= 0:
                continue
            if current_year is not None and (int(current_year) - int(year)) > max_years:
                expired_losses[int(year)] = _money_wan(available_loss)
                remaining_losses[year] = 0.0
                continue

            used = min(remaining_income, available_loss)
            used_by_year[year] = _money_wan(used)
            remaining_income -= used
            remaining_losses[year] = _money_wan(available_loss - used)

        total_used = sum(used_by_year.values())

        return {
            "taxable_income_before_loss_wan": taxable_income_before_loss_wan,
            "loss_used_wan": _money_wan(total_used),
            "taxable_income_after_loss_wan": _money_wan(max(0, remaining_income)),
            "remaining_losses": {
                y: v for y, v in remaining_losses.items()
            },
            "used_by_year": used_by_year,
            "expired_losses": expired_losses,
        }

    # --- 5. 税额计算 ---

    @staticmethod
    def _ensure_rate_effective(
        enterprise_tax_type: EnterpriseTaxType,
        tax_year: str | int | None,
    ) -> None:
        """税率版本化门禁：所属年度早于税率登记生效日则拒绝。

        小微优惠由 policy_status（含公告号与到期日）单独校验，此处不重复。
        tax_year 缺省或非法时保持向后兼容（交由 policy_status 判断）。
        """
        if tax_year is None or enterprise_tax_type == EnterpriseTaxType.SMALL_MICRO:
            return
        year = str(tax_year).strip()
        if not (year.isdigit() and len(year) == 4):
            return
        item_name = {
            EnterpriseTaxType.STANDARD: "基本税率",
            EnterpriseTaxType.HIGH_TECH: "高新技术企业优惠税率",
        }.get(enterprise_tax_type)
        if item_name is None:
            return
        item = next((r for r in CORPORATE_INCOME_TAX_RATES.rates if r.name == item_name), None)
        if item is None:
            return
        if item.effective_date and year < item.effective_date[:4]:
            raise ValueError(
                f"{item.name}（{item.policy_basis}）自 {item.effective_date} 起适用，"
                f"所属年度 {year} 不能适用；如需计算历史年度，请先补登该年度税率与政策依据"
            )

    @staticmethod
    def calculate_tax_amount(
        taxable_income_wan: float,
        enterprise_tax_type: EnterpriseTaxType,
        tax_year: str | int | None = None,
    ) -> Dict[str, Any]:
        """
        计算应纳所得税额

        Args:
            taxable_income_wan: 应纳税所得额（万元）
            enterprise_tax_type: 企业类型
            tax_year: 所属年度（可选）；传参时对小微优惠做政策有效期校验
                （过期/未生效时抛 ValueError 中文提示，不静默套用 5%）

        Returns:
            税额计算结果（含 policy_status 政策状态，未传 tax_year 时不校验）
        """
        if taxable_income_wan <= 0:
            return {
                "taxable_income_wan": taxable_income_wan,
                "enterprise_tax_type": enterprise_tax_type.value,
                "applicable_rate": 0.0,
                "tax_amount_wan": 0.0,
                "reason": "应纳税所得额为零或负数，无需缴纳企业所得税",
            }

        # 税率版本化（准入制）：所属年度必须落在税率登记的有效区间内。
        # 未登记区间的历史年度一律拒绝，不用现行税率倒推历史年度。
        CorporateIncomeTaxCalculator._ensure_rate_effective(enterprise_tax_type, tax_year)
        rate = CorporateIncomeTaxCalculator.get_applicable_tax_rate(enterprise_tax_type)
        policy_status_value = ""
        reason = ""
        if enterprise_tax_type == EnterpriseTaxType.SMALL_MICRO:
            if tax_year is not None:
                from ..config.policy_validity import policy_status
                status = policy_status("small_micro_5pct", tax_year)
                if not status["valid"]:
                    raise ValueError(status["message"])
                policy_status_value = status["status"]
            reason = (
                f"小型微利企业优惠：应纳税所得额{taxable_income_wan:.2f}万元 "
                f"× 25%（计入应纳税所得额比例）× 20%（优惠税率）= 实际税负5%"
            )
            if tax_year is not None:
                from ..config.policy_validity import POLICY_REGISTRY
                reason += f"（{POLICY_REGISTRY['small_micro_5pct'].policy_basis}，至 2027-12-31）"
        elif enterprise_tax_type == EnterpriseTaxType.HIGH_TECH:
            reason = "高新技术企业优惠税率15%"
        else:
            reason = f"一般企业所得税标准税率25%"

        return {
            "taxable_income_wan": taxable_income_wan,
            "enterprise_tax_type": enterprise_tax_type.value,
            "applicable_rate": rate,
            # 税额按「元」取整到分后折回万元（复核 I-3）：旧实现 round(万元, 2)
            # 等于 100 元粒度，计算页与落库/导出会差到元级。
            "tax_amount_wan": money(taxable_income_wan * 10000.0 * rate) / 10000.0,
            "reason": reason,
            "policy_status": policy_status_value,
        }

    # ============================================================
    # 完整计算入口
    # ============================================================

    @staticmethod
    def calculate(
        accounting_profit_wan: float,
        revenue_wan: float,
        adjustments: TaxAdjustmentInput,
        total_salary_wan: float,
        enterprise_info: EnterpriseInfo,
        losses: Optional[LossCarryForward] = None,
    ) -> CalculationResult:
        """
        企业所得税完整计算入口

        执行完整的计算流程：
        1. 小型微利企业判定
        2. 纳税调整项计算
        3. 应纳税所得额计算
        4. 弥补亏损计算
        5. 税额计算

        Args:
            accounting_profit_wan: 利润总额（万元）
            revenue_wan: 营业收入（万元）
            adjustments: 纳税调整项输入
            total_salary_wan: 工资薪金总额（万元）
            enterprise_info: 企业基本信息
            losses: 以前年度亏损信息（可选）

        Returns:
            完整计算结果
        """
        result = CalculationResult()
        result.accounting_profit = accounting_profit_wan

        # 第1步：纳税调整项计算（需要在判定小微企业前获取应纳税所得额）
        adj_result = CorporateIncomeTaxCalculator.calculate_all_adjustments(
            adjustments=adjustments,
            revenue_wan=revenue_wan,
            accounting_profit_wan=accounting_profit_wan,
            total_salary_wan=total_salary_wan,
            enterprise_type=enterprise_info.enterprise_type,
            advertising_industry=enterprise_info.advertising_industry,
            tax_year=enterprise_info.tax_year or None,
        )

        result.adjustments_detail = adj_result["details"]
        result.total_adjustment_increase = adj_result["total_adjustment_increase_wan"]
        result.total_adjustment_decrease = adj_result["total_adjustment_decrease_wan"]

        # 第2步：计算弥补亏损前的应纳税所得额
        taxable_income_before_loss = CorporateIncomeTaxCalculator.calculate_taxable_income_before_loss(
            accounting_profit_wan=accounting_profit_wan,
            total_adjustment_increase_wan=result.total_adjustment_increase,
            total_adjustment_decrease_wan=result.total_adjustment_decrease,
        )
        result.taxable_income_before_loss = taxable_income_before_loss

        # 第3步：弥补亏损（先算，因为小微判定要用弥补后的应纳税所得额）
        if losses is not None and losses.losses_by_year:
            loss_result = CorporateIncomeTaxCalculator.calculate_loss_carry_forward(
                taxable_income_before_loss_wan=taxable_income_before_loss,
                losses=losses,
            )
            result.loss_carry_forward_used = loss_result["loss_used_wan"]
            result.loss_used_by_year = dict(loss_result.get("used_by_year") or {})
            result.loss_carry_forward_remaining = loss_result["remaining_losses"]
            result.loss_carry_forward_expired = loss_result.get("expired_losses", {})
            result.taxable_income_after_loss = loss_result["taxable_income_after_loss_wan"]
        else:
            result.loss_carry_forward_used = 0.0
            result.loss_used_by_year = {}
            result.taxable_income_after_loss = taxable_income_before_loss

        # 第4步：小型微利企业判定（使用弥补亏损后的应纳税所得额）
        # 《企业所得税法》第五条的「应纳税所得额」已扣除允许弥补的亏损，
        # 小微条件应基于弥补后金额（BUGfix TAX-08）。
        small_micro_result = CorporateIncomeTaxCalculator.check_small_micro_enterprise(
            employee_count=enterprise_info.employee_count,
            total_assets_wan=enterprise_info.total_assets,
            taxable_income_wan=max(0.0, result.taxable_income_after_loss),
            is_restricted_industry=enterprise_info.is_restricted_industry,
            industry_name=enterprise_info.industry,
        )
        result.is_small_micro = small_micro_result["qualified"]
        result.small_micro_details = small_micro_result

        # 第5步：确定企业类型和适用税率（高新与小微并存须显式选择，不叠加）
        enterprise_tax_type = CorporateIncomeTaxCalculator.determine_enterprise_tax_type(
            is_high_tech=enterprise_info.is_high_tech,
            small_micro_result=small_micro_result,
            preferential_choice=enterprise_info.preferential_choice,
        )
        result.enterprise_tax_type = enterprise_tax_type
        result.applicable_tax_rate = CorporateIncomeTaxCalculator.get_applicable_tax_rate(enterprise_tax_type)

        # 第6步：税额计算（使用弥补亏损后的应纳税所得额）
        tax_result = CorporateIncomeTaxCalculator.calculate_tax_amount(
            taxable_income_wan=result.taxable_income_after_loss,
            enterprise_tax_type=enterprise_tax_type,
            tax_year=enterprise_info.tax_year or None,
        )
        result.tax_amount = tax_result["tax_amount_wan"]
        result.policy_status = tax_result.get("policy_status", "")

        return result
