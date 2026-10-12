"""
税收政策服务模块
提供税率和税收优惠规则的查询服务
"""
from typing import Dict, List, Optional, Any

from ..config.tax_rates import (
    TaxType,
    EnterpriseType,
    VATTaxpayerType,
    CityTier,
    TaxRateConfig,
    TaxRateItem,
    ALL_TAX_RATES,
    CORPORATE_INCOME_TAX_RATES,
    VALUE_ADDED_TAX_RATES,
    ADDITIONAL_TAX_RATES,
    get_standard_cit_rate,
    get_high_tech_cit_rate,
    get_small_micro_cit_rate,
    get_vat_rate_general_sales,
    get_vat_rate_general_transport,
    get_vat_rate_general_service,
    get_vat_rate_small_scale,
    get_vat_exempt_monthly_threshold,
    get_vat_exempt_quarterly_threshold,
    get_city_maintenance_tax_rate,
    get_education_surcharge_rate,
    get_local_education_surcharge_rate,
    calculate_additional_tax_rates,
)
from ..config.tax_rules import (
    DeductionCategory,
    IndustryType,
    SMALL_MICRO_CRITERIA,
    RD_EXPENSE_SUPER_DEDUCTION_RULES,
    DEDUCTION_LIMIT_CALCULATORS,
    get_rd_deduction_rate,
    calculate_rd_expense_deduction,
    calculate_entertainment_expense_limit,
    calculate_advertising_expense_limit,
    calculate_charitable_donation_limit,
    calculate_employee_welfare_limit,
    calculate_employee_education_limit,
    calculate_labor_union_fund_limit,
)


class TaxPolicyService:
    """税收政策服务类"""

    # ============================================================
    # 税率查询服务
    # ============================================================

    @staticmethod
    def get_all_tax_rates() -> List[Dict[str, Any]]:
        """
        获取所有税率配置

        Returns:
            所有税率配置的列表
        """
        result = []
        for config in ALL_TAX_RATES:
            result.append({
                "name": config.name,
                "tax_type": config.tax_type.value,
                "description": config.description,
                "rates": [
                    {
                        "name": rate.name,
                        "rate": rate.rate,
                        "rate_display": f"{rate.rate * 100:.2f}%" if rate.rate < 1 else f"{rate.rate:.0f}%",
                        "description": rate.description,
                        "is_preferential": rate.is_preferential,
                        "conditions": rate.conditions,
                        "effective_date": rate.effective_date,
                    }
                    for rate in config.rates
                ],
            })
        return result

    @staticmethod
    def get_tax_rates_by_type(tax_type: TaxType) -> Optional[Dict[str, Any]]:
        """
        按税种获取税率配置

        Args:
            tax_type: 税种类型

        Returns:
            该税种的税率配置，不存在则返回None
        """
        for config in ALL_TAX_RATES:
            if config.tax_type == tax_type:
                return {
                    "name": config.name,
                    "tax_type": config.tax_type.value,
                    "description": config.description,
                    "rates": [
                        {
                            "name": rate.name,
                            "rate": rate.rate,
                            "rate_display": f"{rate.rate * 100:.2f}%" if rate.rate < 1 else f"{rate.rate:.0f}%",
                            "description": rate.description,
                            "is_preferential": rate.is_preferential,
                            "conditions": rate.conditions,
                            "effective_date": rate.effective_date,
                        }
                        for rate in config.rates
                    ],
                }
        return None

    @staticmethod
    def get_corporate_income_tax_rates() -> List[Dict[str, Any]]:
        """获取企业所得税税率列表"""
        return [
            {
                "name": rate.name,
                "rate": rate.rate,
                "rate_display": f"{rate.rate * 100}%",
                "description": rate.description,
                "is_preferential": rate.is_preferential,
            }
            for rate in CORPORATE_INCOME_TAX_RATES.rates
        ]

    @staticmethod
    def get_applicable_cit_rate(
        is_high_tech: bool = False,
        is_small_micro: bool = False,
    ) -> Dict[str, Any]:
        """
        根据企业类型获取适用的企业所得税税率

        Args:
            is_high_tech: 是否为高新技术企业
            is_small_micro: 是否为小型微利企业

        Returns:
            包含税率和说明的字典
        """
        def format_rate(rate: float) -> str:
            pct = rate * 100
            if pct == int(pct):
                return f"{int(pct)}%"
            return f"{pct}%"

        if is_high_tech:
            rate = get_high_tech_cit_rate()
            return {
                "rate": rate,
                "rate_display": format_rate(rate),
                "policy_name": "高新技术企业优惠税率",
                "description": "经认定的高新技术企业减按15%税率征收企业所得税",
            }
        elif is_small_micro:
            rate = get_small_micro_cit_rate()
            return {
                "rate": rate,
                "rate_display": format_rate(rate),
                "policy_name": "小型微利企业优惠税率",
                "description": "小型微利企业减按25%计入应纳税所得额，按20%税率缴纳，实际税负5%",
            }
        else:
            rate = get_standard_cit_rate()
            return {
                "rate": rate,
                "rate_display": format_rate(rate),
                "policy_name": "基本税率",
                "description": "企业所得税标准税率",
            }

    @staticmethod
    def get_vat_rates() -> List[Dict[str, Any]]:
        """获取增值税税率列表"""
        return [
            {
                "name": rate.name,
                "rate": rate.rate,
                "rate_display": f"{rate.rate * 100:.2f}%",
                "description": rate.description,
                "is_preferential": rate.is_preferential,
                "conditions": rate.conditions,
            }
            for rate in VALUE_ADDED_TAX_RATES.rates
        ]

    @staticmethod
    def get_vat_rate_by_business_type(
        taxpayer_type: VATTaxpayerType,
        business_type: str = "销售货物",
        monthly_sales: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        根据纳税人类型和业务类型获取适用增值税税率

        Args:
            taxpayer_type: 纳税人类型（一般纳税人/小规模纳税人）
            business_type: 业务类型
            monthly_sales: 月销售额（用于判断小规模纳税人是否免税）

        Returns:
            包含适用税率的字典
        """
        def format_rate(rate: float, suffix: str = "%") -> str:
            pct = rate * 100
            if pct == int(pct):
                return f"{int(pct)}{suffix}"
            return f"{pct}{suffix}"

        if taxpayer_type == VATTaxpayerType.SMALL_SCALE:
            # 小规模纳税人：判断是否达到免征条件
            if monthly_sales is not None and monthly_sales <= get_vat_exempt_monthly_threshold():
                return {
                    "rate": 0.0,
                    "rate_display": "0%（免征）",
                    "policy_name": "小规模纳税人月销售额≤10万免征",
                    "description": "小规模纳税人月销售额不超过10万元免征增值税",
                }
            rate = get_vat_rate_small_scale()
            return {
                "rate": rate,
                "rate_display": format_rate(rate),
                "policy_name": "小规模纳税人征收率",
                "description": "2026年小规模纳税人减按1%征收率征收",
            }

        # 一般纳税人：根据业务类型确定税率
        business_type_rates = {
            "销售货物": get_vat_rate_general_sales(),
            "交通运输": get_vat_rate_general_transport(),
            "现代服务": get_vat_rate_general_service(),
        }

        rate = business_type_rates.get(business_type, get_vat_rate_general_sales())

        return {
            "rate": rate,
            "rate_display": format_rate(rate),
            "policy_name": f"一般纳税人-{business_type}",
            "description": f"一般纳税人{business_type}适用税率",
        }

    @staticmethod
    def get_additional_tax_rates() -> List[Dict[str, Any]]:
        """获取附加税费税率列表"""
        return [
            {
                "name": rate.name,
                "rate": rate.rate,
                "rate_display": f"{rate.rate * 100}%",
                "description": rate.description,
                "is_preferential": rate.is_preferential,
                "conditions": rate.conditions,
            }
            for rate in ADDITIONAL_TAX_RATES.rates
        ]

    @staticmethod
    def calculate_additional_tax(
        vat_amount: float,
        city_tier: CityTier = CityTier.URBAN,
    ) -> Dict[str, Any]:
        """
        计算附加税费

        Args:
            vat_amount: 实际缴纳的增值税额
            city_tier: 城建税地区档次

        Returns:
            各项附加税费计算结果
        """
        result = calculate_additional_tax_rates(vat_amount, city_tier)
        result["city_tier"] = city_tier.value
        result["city_maintenance_rate"] = get_city_maintenance_tax_rate(city_tier)
        result["education_surcharge_rate"] = get_education_surcharge_rate()
        result["local_education_surcharge_rate"] = get_local_education_surcharge_rate()
        return result

    # ============================================================
    # 税收优惠规则查询服务
    # ============================================================

    @staticmethod
    def check_small_micro_enterprise_qualification(
        employee_count: int,
        total_assets: float,
        taxable_income: float,
        industry: str,
    ) -> Dict[str, Any]:
        """
        检查是否符合小型微利企业条件

        Args:
            employee_count: 从业人数
            total_assets: 资产总额（元）
            taxable_income: 应纳税所得额（元）
            industry: 行业名称

        Returns:
            包含资格判断结果和详细信息的字典
        """
        return SMALL_MICRO_CRITERIA.is_qualified(
            employee_count=employee_count,
            total_assets=total_assets,
            taxable_income=taxable_income,
            industry=industry,
        )

    @staticmethod
    def get_rd_deduction_rules() -> List[Dict[str, Any]]:
        """获取所有研发费用加计扣除规则"""
        return [
            {
                "name": rule.name,
                "deduction_rate": rule.deduction_rate,
                "deduction_rate_display": f"{rule.deduction_rate * 100:.0f}%",
                "applicable_enterprises": rule.applicable_enterprises,
                "description": rule.description,
            }
            for rule in RD_EXPENSE_SUPER_DEDUCTION_RULES
        ]

    @staticmethod
    def get_rd_deduction_for_enterprise(
        enterprise_type: str = "一般企业",
    ) -> Dict[str, Any]:
        """
        获取指定企业类型的研发费用加计扣除政策

        Args:
            enterprise_type: 企业类型

        Returns:
            包含加计扣除规则的字典
        """
        rate = get_rd_deduction_rate(enterprise_type)
        return {
            "enterprise_type": enterprise_type,
            "deduction_rate": rate,
            "deduction_rate_display": f"{rate * 100:.0f}%",
            "description": f"研发费用在实际发生额基础上，加计扣除{rate * 100:.0f}%",
        }

    @staticmethod
    def calculate_rd_expense_deduction(
        actual_rd_expense: float,
        enterprise_type: str = "一般企业",
    ) -> Dict[str, Any]:
        """
        计算研发费用加计扣除

        Args:
            actual_rd_expense: 实际发生的研发费用
            enterprise_type: 企业类型

        Returns:
            包含扣除计算结果的字典
        """
        return calculate_rd_expense_deduction(actual_rd_expense, enterprise_type)

    @staticmethod
    def get_deduction_limit_rules() -> List[Dict[str, Any]]:
        """获取所有纳税调整项限额规则说明"""
        rules = [
            {
                "category": DeductionCategory.ENTERTAINMENT.value,
                "name": "业务招待费",
                "rule": "min(发生额×60%, 收入×5‰)",
                "description": "按照发生额的60%扣除，但最高不得超过当年销售（营业）收入的5‰",
            },
            {
                "category": DeductionCategory.ADVERTISING.value,
                "name": "广告费和业务宣传费",
                "rule": "一般企业15%，化妆品/医药/饮料制造30%，烟草0%",
                "description": "不超过当年销售（营业）收入规定比例的部分准予扣除，超过部分准予结转以后年度",
            },
            {
                "category": DeductionCategory.CHARITABLE_DONATION.value,
                "name": "公益性捐赠",
                "rule": "min(发生额, 会计利润×12%)",
                "description": "在年度利润总额12%以内的部分准予扣除，超过部分准予结转以后三年",
            },
            {
                "category": DeductionCategory.EMPLOYEE_WELFARE.value,
                "name": "职工福利费",
                "rule": "min(发生额, 工资薪金×14%)",
                "description": "不超过工资薪金总额14%的部分准予扣除",
            },
            {
                "category": DeductionCategory.EMPLOYEE_EDUCATION.value,
                "name": "职工教育经费",
                "rule": "min(发生额, 工资薪金×8%)",
                "description": "不超过工资薪金总额8%的部分准予扣除，超过部分准予结转以后年度",
            },
            {
                "category": DeductionCategory.LABOR_UNION.value,
                "name": "工会经费",
                "rule": "min(发生额, 工资薪金×2%)",
                "description": "不超过工资薪金总额2%的部分准予扣除",
            },
        ]
        return rules

    @staticmethod
    def calculate_deduction_limit(
        category: DeductionCategory,
        **kwargs,
    ) -> Dict[str, Any]:
        """
        计算各项纳税调整项的扣除限额

        Args:
            category: 扣除类别
            **kwargs: 计算所需参数

        Returns:
            包含扣除计算结果的字典
        """
        calculator = DEDUCTION_LIMIT_CALCULATORS.get(category)
        if calculator is None:
            raise ValueError(f"未知的扣除类别: {category}")
        return calculator(**kwargs)

    @staticmethod
    def calculate_entertainment_expense_limit(
        expense_amount: float,
        revenue: float,
    ) -> Dict[str, Any]:
        """计算业务招待费扣除限额"""
        return calculate_entertainment_expense_limit(expense_amount, revenue)

    @staticmethod
    def calculate_advertising_expense_limit(
        expense_amount: float,
        revenue: float,
        industry: IndustryType = IndustryType.GENERAL,
    ) -> Dict[str, Any]:
        """计算广告费扣除限额"""
        return calculate_advertising_expense_limit(expense_amount, revenue, industry)

    @staticmethod
    def calculate_charitable_donation_limit(
        donation_amount: float,
        accounting_profit: float,
    ) -> Dict[str, Any]:
        """计算公益性捐赠扣除限额"""
        return calculate_charitable_donation_limit(donation_amount, accounting_profit)

    @staticmethod
    def calculate_employee_welfare_limit(
        expense_amount: float,
        total_salary: float,
    ) -> Dict[str, Any]:
        """计算职工福利费扣除限额"""
        return calculate_employee_welfare_limit(expense_amount, total_salary)

    @staticmethod
    def calculate_employee_education_limit(
        expense_amount: float,
        total_salary: float,
    ) -> Dict[str, Any]:
        """计算职工教育经费扣除限额"""
        return calculate_employee_education_limit(expense_amount, total_salary)

    @staticmethod
    def calculate_labor_union_fund_limit(
        expense_amount: float,
        total_salary: float,
    ) -> Dict[str, Any]:
        """计算工会经费扣除限额"""
        return calculate_labor_union_fund_limit(expense_amount, total_salary)

    # ============================================================
    # 综合查询服务
    # ============================================================

    @staticmethod
    def get_policy_summary() -> Dict[str, Any]:
        """
        获取税收政策汇总信息

        Returns:
            包含主要税率和政策摘要的字典
        """
        def format_rate(rate: float) -> str:
            """格式化税率显示，去除多余的小数位"""
            pct = rate * 100
            if pct == int(pct):
                return f"{int(pct)}%"
            return f"{pct:.1f}%"

        return {
            "corporate_income_tax": {
                "standard_rate": format_rate(get_standard_cit_rate()),
                "high_tech_rate": format_rate(get_high_tech_cit_rate()),
                "small_micro_rate": format_rate(get_small_micro_cit_rate()),
            },
            "value_added_tax": {
                "general_sales": format_rate(get_vat_rate_general_sales()),
                "general_transport": format_rate(get_vat_rate_general_transport()),
                "general_service": format_rate(get_vat_rate_general_service()),
                "small_scale": format_rate(get_vat_rate_small_scale()),
                "exempt_threshold_monthly": f"{get_vat_exempt_monthly_threshold():,.0f}元",
                "exempt_threshold_quarterly": f"{get_vat_exempt_quarterly_threshold():,.0f}元",
            },
            "additional_taxes": {
                "city_maintenance": "7%/5%/1%",
                "education_surcharge": format_rate(get_education_surcharge_rate()),
                "local_education_surcharge": format_rate(get_local_education_surcharge_rate()),
            },
            "preferential_policies": {
                "small_micro_criteria": {
                    "employee_max": f"\u2264{SMALL_MICRO_CRITERIA.employee_count_max}人",
                    "assets_max": f"\u2264{SMALL_MICRO_CRITERIA.total_assets_max / 10000:.0f}万元",
                    "income_max": f"\u2264{SMALL_MICRO_CRITERIA.taxable_income_max / 10000:.0f}万元",
                },
                "rd_super_deduction": {
                    "general": f"{int(get_rd_deduction_rate('一般企业') * 100)}%",
                    "integrated_circuit": f"{int(get_rd_deduction_rate('集成电路企业') * 100)}%",
                    "machine_tool": f"{int(get_rd_deduction_rate('工业母机企业') * 100)}%",
                },
            },
        }
