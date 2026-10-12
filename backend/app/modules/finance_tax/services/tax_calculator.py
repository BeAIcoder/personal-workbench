"""
税额计算服务
"""
from typing import Dict, Any

class TaxCalculator:
    """企业所得税计算器"""
    
    @staticmethod
    def calculate_taxable_income(
        total_revenue: float,
        other_income: float,
        total_costs: float,
        total_expenses: float,
        tax_adjustments: float,
        tax_preferential: float
    ) -> float:
        """
        计算应纳税所得额
        公式：营业收入 + 其他收入 - 营业成本 - 期间费用 + 纳税调整 - 税收优惠
        """
        taxable_income = (
            total_revenue +
            other_income -
            total_costs -
            total_expenses +
            tax_adjustments -
            tax_preferential
        )
        return max(0, taxable_income)
    
    @staticmethod
    def get_tax_rate(taxable_income: float, is_small_micro: bool = False) -> float:
        """
        获取适用税率
        考虑小微企业优惠政策
        """
        if is_small_micro:
            # 小微企业优惠税率
            if taxable_income <= 1000000:  # 100万以下
                return 0.025  # 实际税率2.5%
            elif taxable_income <= 3000000:  # 300万以下
                return 0.05  # 实际税率5%
        
        # 标准税率
        if taxable_income <= 3000000:  # 300万以下
            return 0.05  # 实际税率5%
        elif taxable_income <= 10000000:  # 1000万以下
            return 0.10  # 实际税率10%
        else:
            return 0.25  # 标准税率25%
    
    @staticmethod
    def calculate_tax(taxable_income: float, tax_rate: float) -> float:
        """计算应纳税额"""
        return max(0, taxable_income * tax_rate)
    
    @staticmethod
    def full_calculation(data: Dict[str, Any]) -> Dict[str, Any]:
        """
        完整税额计算
        返回包含所有计算结果的字典
        """
        taxable_income = TaxCalculator.calculate_taxable_income(
            total_revenue=data.get("total_revenue", 0),
            other_income=data.get("other_income", 0),
            total_costs=data.get("total_costs", 0),
            total_expenses=data.get("total_expenses", 0),
            tax_adjustments=data.get("tax_adjustments", 0),
            tax_preferential=data.get("tax_preferential", 0)
        )
        
        is_small_micro = data.get("is_small_micro", False)
        tax_rate = TaxCalculator.get_tax_rate(taxable_income, is_small_micro)
        tax_amount = TaxCalculator.calculate_tax(taxable_income, tax_rate)
        
        return {
            "taxable_income": taxable_income,
            "tax_rate": tax_rate,
            "tax_amount": tax_amount,
            "is_small_micro": is_small_micro
        }
