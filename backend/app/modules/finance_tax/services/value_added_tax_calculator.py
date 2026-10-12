"""
增值税计算引擎

实现一般纳税人、小规模纳税人、混合销售、视同销售、出口退税等
增值税计算逻辑，遵循2026年增值税法新规。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple


# ============================================================
# 枚举与数据类定义
# ============================================================

class TaxpayerType(Enum):
    """增值税纳税人类型"""
    GENERAL = "一般纳税人"
    SMALL_SCALE = "小规模纳税人"


class VATRate(Enum):
    """增值税税率档位"""
    RATE_13 = 0.13  # 销售/进口货物等
    RATE_9 = 0.09   # 交通运输、建筑、不动产等
    RATE_6 = 0.06   # 现代服务、生活服务等
    RATE_0 = 0.0    # 零税率（出口等）
    LEVY_RATE_3 = 0.03  # 小规模纳税人标准征收率
    LEVY_RATE_1 = 0.01  # 小规模纳税人优惠征收率（2026年政策）


class BusinessType(Enum):
    """业务类型（用于混合销售判定）"""
    SALES_OF_GOODS = "销售货物"
    TRANSPORTATION = "交通运输"
    CONSTRUCTION = "建筑服务"
    REAL_ESTATE = "不动产销售"
    MODERN_SERVICES = "现代服务"
    LIFE_SERVICES = "生活服务"
    FINANCIAL_SERVICES = "金融服务"
    INTANGIBLE_ASSETS = "无形资产转让"


class DeemedSalesType(Enum):
    """视同销售类型"""
    GIFT_TO_OTHERS = "无偿赠送他人"
    INVESTMENT = "作为投资提供给其他单位"
    DISTRIBUTION_TO_SHAREHOLDERS = "分配给股东或投资者"
    EMPLOYEE_WELFARE = "用于集体福利或个人消费"
    NON_VATABLE_PROJECTS = "用于非增值税应税项目"
    FREE_TRANSFER = "无偿转让无形资产或不动产"


class ExportRebateMethod(Enum):
    """出口退税方法"""
    EXEMPT_AND_REFUND = "免退税"           # 外贸企业
    EXEMPT_CREDIT_AND_REFUND = "免抵退税"  # 生产企业


@dataclass
class SalesItem:
    """销售/收入明细项"""
    description: str
    amount: float                    # 含税销售额
    business_type: BusinessType
    quantity: float = 1.0
    unit_price: float = 0.0


@dataclass
class InputInvoiceItem:
    """进项发票明细项"""
    description: str
    invoice_amount: float            # 发票金额（不含税）
    tax_amount: float                # 进项税额
    vat_rate: VATRate
    is_deductible: bool = True       # 是否可抵扣
    non_deductible_reason: str = ""  # 不可抵扣原因


@dataclass
class DeemedSalesItem:
    """视同销售明细项"""
    description: str
    deemed_sales_type: DeemedSalesType
    cost_amount: float               # 成本金额
    profit_rate: float = 0.10        # 成本利润率（默认10%）
    vat_rate: VATRate = VATRate.RATE_13


@dataclass
class ExportItem:
    """出口货物明细项"""
    description: str
    fob_amount: float                # FOB出口销售额（人民币）
    export_rebate_rate: float        # 出口退税率
    purchase_amount: float = 0.0     # 采购金额（外贸企业用）
    purchase_tax_rate: float = 0.13  # 采购环节税率


@dataclass
class MixedSalesResult:
    """混合销售判定结果"""
    primary_business: BusinessType
    primary_business_revenue: float
    secondary_business: BusinessType
    secondary_business_revenue: float
    applicable_vat_rate: VATRate
    reasoning: str


@dataclass
class GeneralTaxpayerResult:
    """一般纳税人计算结果"""
    output_tax: float                # 销项税额
    input_tax: float                 # 进项税额
    input_tax_transferred_out: float  # 进项税额转出
    deductible_input_tax: float      # 可抵扣进项税额
    vat_payable: float               # 应纳税额
    sales_items: List[Dict] = field(default_factory=list)
    input_items: List[Dict] = field(default_factory=list)


@dataclass
class SmallScaleResult:
    """小规模纳税人计算结果"""
    total_sales: float               # 总销售额（不含税）
    monthly_sales: float             # 月均销售额
    is_exempt: bool                  # 是否免征
    applicable_levy_rate: float      # 适用征收率
    vat_payable: float               # 应纳税额
    exempt_amount: float = 0.0       # 免征税额


@dataclass
class DeemedSalesResult:
    """视同销售计算结果"""
    total_deemed_sales: float        # 视同销售总额（不含税）
    total_output_tax: float          # 视同销售销项税额
    items: List[Dict] = field(default_factory=list)


@dataclass
class ExportRebateResult:
    """出口退税计算结果"""
    rebate_method: ExportRebateMethod
    export_sales: float              # 出口销售额
    exempt_tax: float                # 免征税额
    credit_tax: float                # 抵减税额（免抵退税用）
    refundable_tax: float            # 应退税额
    non_refundable_tax: float        # 不予退还税额（需转出进项）


@dataclass
class VATCalculationSummary:
    """增值税计算汇总"""
    taxpayer_type: TaxpayerType
    vat_payable: float
    additional_taxes: Dict[str, float] = field(default_factory=dict)
    details: Dict = field(default_factory=dict)


# ============================================================
# 税率与常量配置
# ============================================================

# 业务类型到增值税税率的映射（一般纳税人）
BUSINESS_TYPE_TO_VAT_RATE: Dict[BusinessType, VATRate] = {
    BusinessType.SALES_OF_GOODS: VATRate.RATE_13,
    BusinessType.TRANSPORTATION: VATRate.RATE_9,
    BusinessType.CONSTRUCTION: VATRate.RATE_9,
    BusinessType.REAL_ESTATE: VATRate.RATE_9,
    BusinessType.MODERN_SERVICES: VATRate.RATE_6,
    BusinessType.LIFE_SERVICES: VATRate.RATE_6,
    BusinessType.FINANCIAL_SERVICES: VATRate.RATE_6,
    BusinessType.INTANGIBLE_ASSETS: VATRate.RATE_6,
}

# 小规模纳税人免征门槛
MONTHLY_EXEMPT_THRESHOLD = 100_000   # 月销售额10万元
QUARTERLY_EXEMPT_THRESHOLD = 300_000  # 季度销售额30万元

# 小规模纳税人优惠征收率（2026年政策：减按1%）
SMALL_SCALE_PREFERENTIAL_RATE = 0.01
SMALL_SCALE_STANDARD_RATE = 0.03

# 城建税及附加税率
CITY_MAINTENANCE_TAX_RATE = 0.07
EDUCATION_SURCHARGE_RATE = 0.03
LOCAL_EDUCATION_SURCHARGE_RATE = 0.02


# ============================================================
# 核心计算引擎
# ============================================================

class ValueAddedTaxCalculator:
    """
    增值税计算引擎

    支持一般纳税人、小规模纳税人、混合销售判定、视同销售、出口退税
    等增值税计算场景，遵循2026年增值税法新规。
    """

    # ----------------------------------------------------------
    # 一般纳税人计算
    # ----------------------------------------------------------

    @staticmethod
    def calculate_general_taxpayer(
        sales_items: List[SalesItem],
        input_invoices: List[InputInvoiceItem],
        input_tax_transferred_out: float = 0.0,
    ) -> GeneralTaxpayerResult:
        """
        一般纳税人应纳税额计算

        公式：应纳税额 = 销项税额 - 可抵扣进项税额 - 进项税额转出

        Args:
            sales_items: 销售明细列表
            input_invoices: 进项发票列表
            input_tax_transferred_out: 进项税额转出金额

        Returns:
            GeneralTaxpayerResult 计算结果
        """
        # 计算销项税额
        total_output_tax = 0.0
        sales_details = []

        for item in sales_items:
            vat_rate = BUSINESS_TYPE_TO_VAT_RATE.get(item.business_type, VATRate.RATE_13)
            # 含税销售额换算为不含税，再计算销项税额
            amount_excl_tax = item.amount / (1 + vat_rate.value)
            output_tax = amount_excl_tax * vat_rate.value
            total_output_tax += output_tax

            sales_details.append({
                "description": item.description,
                "amount_incl_tax": item.amount,
                "amount_excl_tax": round(amount_excl_tax, 2),
                "vat_rate": vat_rate.value,
                "output_tax": round(output_tax, 2),
            })

        # 计算可抵扣进项税额
        total_input_tax = 0.0
        deductible_input_tax = 0.0
        input_details = []

        for invoice in input_invoices:
            total_input_tax += invoice.tax_amount
            if invoice.is_deductible:
                deductible_input_tax += invoice.tax_amount
            input_details.append({
                "description": invoice.description,
                "invoice_amount": invoice.invoice_amount,
                "tax_amount": invoice.tax_amount,
                "vat_rate": invoice.vat_rate.value,
                "is_deductible": invoice.is_deductible,
                "non_deductible_reason": invoice.non_deductible_reason,
            })

        # 应纳税额 = 销项税额 - 可抵扣进项税额 - 进项税额转出
        vat_payable = total_output_tax - deductible_input_tax - input_tax_transferred_out

        return GeneralTaxpayerResult(
            output_tax=round(total_output_tax, 2),
            input_tax=round(total_input_tax, 2),
            input_tax_transferred_out=round(input_tax_transferred_out, 2),
            deductible_input_tax=round(deductible_input_tax, 2),
            vat_payable=round(max(0, vat_payable), 2),
            sales_items=sales_details,
            input_items=input_details,
        )

    @staticmethod
    def calculate_output_tax(
        amount_incl_tax: float,
        vat_rate: VATRate,
    ) -> Tuple[float, float]:
        """
        从含税销售额计算不含税金额和销项税额

        Args:
            amount_incl_tax: 含税销售额
            vat_rate: 适用税率

        Returns:
            (不含税金额, 销项税额)
        """
        amount_excl_tax = amount_incl_tax / (1 + vat_rate.value)
        output_tax = amount_excl_tax * vat_rate.value
        return round(amount_excl_tax, 2), round(output_tax, 2)

    # ----------------------------------------------------------
    # 小规模纳税人计算
    # ----------------------------------------------------------

    @staticmethod
    def calculate_small_scale(
        monthly_sales: float,
        calculation_period: str = "monthly",
        quarterly_sales: float = 0.0,
    ) -> SmallScaleResult:
        """
        小规模纳税人增值税计算

        2026年政策：
        - 月销售额 <= 10万（季度 <= 30万）：免征增值税
        - 月销售额 > 10万：减按1%征收率计算

        Args:
            monthly_sales: 月销售额（不含税）
            calculation_period: 计算周期，"monthly" 或 "quarterly"
            quarterly_sales: 季度销售额（仅季度申报时使用）

        Returns:
            SmallScaleResult 计算结果
        """
        total_sales = monthly_sales
        is_exempt = False

        if calculation_period == "monthly":
            total_sales = monthly_sales
            is_exempt = monthly_sales <= MONTHLY_EXEMPT_THRESHOLD
        elif calculation_period == "quarterly":
            total_sales = quarterly_sales
            is_exempt = quarterly_sales <= QUARTERLY_EXEMPT_THRESHOLD

        if is_exempt:
            return SmallScaleResult(
                total_sales=round(total_sales, 2),
                monthly_sales=round(monthly_sales, 2),
                is_exempt=True,
                applicable_levy_rate=0.0,
                vat_payable=0.0,
                exempt_amount=round(total_sales * SMALL_SCALE_PREFERENTIAL_RATE, 2),
            )

        # 超过免征额，减按1%征收
        vat_payable = total_sales * SMALL_SCALE_PREFERENTIAL_RATE

        return SmallScaleResult(
            total_sales=round(total_sales, 2),
            monthly_sales=round(monthly_sales, 2),
            is_exempt=False,
            applicable_levy_rate=SMALL_SCALE_PREFERENTIAL_RATE,
            vat_payable=round(vat_payable, 2),
            exempt_amount=0.0,
        )

    # ----------------------------------------------------------
    # 混合销售判定
    # ----------------------------------------------------------

    @staticmethod
    def determine_mixed_sales(
        goods_revenue: float,
        services_revenue: float,
        goods_business_type: BusinessType = BusinessType.SALES_OF_GOODS,
        services_business_type: BusinessType = BusinessType.MODERN_SERVICES,
    ) -> MixedSalesResult:
        """
        混合销售判定（2026年增值税法新规）

        规则：按纳税人"主要业务"判定适用税率。
        - 从事货物生产、批发或零售的企业：按销售货物税率（13%）
        - 其他企业：按服务税率

        主要业务判定标准：比较货物销售额和服务销售额，
        占比较大的一方为主要业务。

        Args:
            goods_revenue: 货物销售额
            services_revenue: 服务销售额
            goods_business_type: 货物业务类型
            services_business_type: 服务业务类型

        Returns:
            MixedSalesResult 判定结果
        """
        total_revenue = goods_revenue + services_revenue

        if total_revenue == 0:
            return MixedSalesResult(
                primary_business=goods_business_type,
                primary_business_revenue=0,
                secondary_business=services_business_type,
                secondary_business_revenue=0,
                applicable_vat_rate=VATRate.RATE_13,
                reasoning="销售额为零，默认按销售货物税率",
            )

        goods_ratio = goods_revenue / total_revenue
        services_ratio = services_revenue / total_revenue

        if goods_revenue >= services_revenue:
            # 货物销售为主
            primary = goods_business_type
            primary_rev = goods_revenue
            secondary = services_business_type
            secondary_rev = services_revenue
            applicable_rate = BUSINESS_TYPE_TO_VAT_RATE.get(primary, VATRate.RATE_13)
            reasoning = (
                f"货物销售占比{goods_ratio:.1%}，为主要业务，"
                f"适用税率{applicable_rate.value:.0%}"
            )
        else:
            # 服务为主
            primary = services_business_type
            primary_rev = services_revenue
            secondary = goods_business_type
            secondary_rev = goods_revenue
            applicable_rate = BUSINESS_TYPE_TO_VAT_RATE.get(primary, VATRate.RATE_6)
            reasoning = (
                f"服务销售占比{services_ratio:.1%}，为主要业务，"
                f"适用税率{applicable_rate.value:.0%}"
            )

        return MixedSalesResult(
            primary_business=primary,
            primary_business_revenue=round(primary_rev, 2),
            secondary_business=secondary,
            secondary_business_revenue=round(secondary_rev, 2),
            applicable_vat_rate=applicable_rate,
            reasoning=reasoning,
        )

    @staticmethod
    def calculate_mixed_sales_tax(
        total_amount_incl_tax: float,
        mixed_sales_result: MixedSalesResult,
    ) -> Tuple[float, float]:
        """
        按混合销售判定结果计算应纳税额

        Args:
            total_amount_incl_tax: 混合销售总含税金额
            mixed_sales_result: 混合销售判定结果

        Returns:
            (不含税金额, 销项税额)
        """
        vat_rate = mixed_sales_result.applicable_vat_rate
        amount_excl_tax = total_amount_incl_tax / (1 + vat_rate.value)
        output_tax = amount_excl_tax * vat_rate.value
        return round(amount_excl_tax, 2), round(output_tax, 2)

    # ----------------------------------------------------------
    # 视同销售计算
    # ----------------------------------------------------------

    @staticmethod
    def calculate_deemed_sales(
        items: List[DeemedSalesItem],
    ) -> DeemedSalesResult:
        """
        视同销售计算

        视同销售情形包括：
        - 将货物交付他人代销
        - 销售代销货物
        - 设有两个以上机构并实行统一核算的纳税人，
          将货物从一个机构移送其他机构用于销售
        - 将自产、委托加工的货物用于集体福利或个人消费
        - 将自产、委托加工或购买的货物作为投资
        - 将自产、委托加工或购买的货物分配给股东或投资者
        - 将自产、委托加工或购买的货物无偿赠送他人

        计税依据：按同类货物近期平均售价；无参考价的，
        按组成计税价格 = 成本 * (1 + 成本利润率)

        Args:
            items: 视同销售明细列表

        Returns:
            DeemedSalesResult 计算结果
        """
        total_deemed_sales = 0.0
        total_output_tax = 0.0
        item_details = []

        for item in items:
            # 组成计税价格 = 成本 * (1 + 成本利润率)
            taxable_value = item.cost_amount * (1 + item.profit_rate)
            output_tax = taxable_value * item.vat_rate.value

            total_deemed_sales += taxable_value
            total_output_tax += output_tax

            item_details.append({
                "description": item.description,
                "deemed_sales_type": item.deemed_sales_type.value,
                "cost_amount": item.cost_amount,
                "profit_rate": item.profit_rate,
                "taxable_value": round(taxable_value, 2),
                "vat_rate": item.vat_rate.value,
                "output_tax": round(output_tax, 2),
            })

        return DeemedSalesResult(
            total_deemed_sales=round(total_deemed_sales, 2),
            total_output_tax=round(total_output_tax, 2),
            items=item_details,
        )

    @staticmethod
    def calculate_deemed_sales_single(
        cost_amount: float,
        profit_rate: float = 0.10,
        vat_rate: VATRate = VATRate.RATE_13,
    ) -> Tuple[float, float]:
        """
        单项视同销售计算

        Args:
            cost_amount: 成本金额
            profit_rate: 成本利润率
            vat_rate: 适用税率

        Returns:
            (组成计税价格, 销项税额)
        """
        taxable_value = cost_amount * (1 + profit_rate)
        output_tax = taxable_value * vat_rate.value
        return round(taxable_value, 2), round(output_tax, 2)

    # ----------------------------------------------------------
    # 出口退税计算
    # ----------------------------------------------------------

    @staticmethod
    def calculate_export_rebate_for_trading_company(
        export_items: List[ExportItem],
    ) -> ExportRebateResult:
        """
        外贸企业出口退税计算（免退税方法）

        外贸企业出口货物，免征出口环节增值税，
        并按购进出口货物的增值税专用发票注明的金额
        和退税率计算退还进项税额。

        应退税额 = 购进金额 * 退税率
        不予退还税额 = 购进金额 * (征税率 - 退税率)

        Args:
            export_items: 出口货物明细列表

        Returns:
            ExportRebateResult 计算结果
        """
        total_export_sales = 0.0
        total_refundable_tax = 0.0
        total_non_refundable_tax = 0.0

        for item in export_items:
            total_export_sales += item.fob_amount

            # 应退税额 = 购进金额 * 退税率
            refundable = item.purchase_amount * item.export_rebate_rate
            total_refundable_tax += refundable

            # 不予退还的税额（征退税率差）需转出进项
            non_refundable = item.purchase_amount * (item.purchase_tax_rate - item.export_rebate_rate)
            total_non_refundable_tax += max(0, non_refundable)

        return ExportRebateResult(
            rebate_method=ExportRebateMethod.EXEMPT_AND_REFUND,
            export_sales=round(total_export_sales, 2),
            exempt_tax=0.0,  # 外贸企业全额免征出口环节增值税
            credit_tax=0.0,
            refundable_tax=round(total_refundable_tax, 2),
            non_refundable_tax=round(total_non_refundable_tax, 2),
        )

    @staticmethod
    def calculate_export_rebate_for_manufacturer(
        export_sales: float,
        export_rebate_rate: float,
        domestic_sales_excl_tax: float,
        domestic_vat_rate: float = 0.13,
        input_tax_total: float = 0.0,
        input_tax_non_deductible: float = 0.0,
    ) -> ExportRebateResult:
        """
        生产企业出口退税计算（免抵退税方法）

        生产企业自营出口自产货物，实行"免、抵、退"税办法：
        - 免：免征出口环节增值税
        - 抵：出口货物所耗用的进项税额可抵减内销货物的应纳税额
        - 退：未抵减完的部分予以退还

        计算步骤：
        1. 免抵退税不得免征和抵扣税额 = 出口货物离岸价 * (征税率 - 退税率)
        2. 当期应纳税额 = 内销销项税额 - (进项税额 - 不得免征和抵扣税额)
        3. 免抵退税额 = 出口货物离岸价 * 退税率
        4. 应退税额 = min(当期留抵税额, 免抵退税额)
        5. 免抵税额 = 免抵退税额 - 应退税额

        Args:
            export_sales: 出口货物FOB销售额（人民币）
            export_rebate_rate: 出口退税率
            domestic_sales_excl_tax: 内销不含税销售额
            domestic_vat_rate: 内销适用税率
            input_tax_total: 当期全部进项税额
            input_tax_non_deductible: 不可抵扣进项税额

        Returns:
            ExportRebateResult 计算结果
        """
        # 步骤1：免抵退税不得免征和抵扣税额
        non_exempt_credit = export_sales * (domestic_vat_rate - export_rebate_rate)
        non_exempt_credit = max(0, non_exempt_credit)

        # 步骤2：内销销项税额
        domestic_output_tax = domestic_sales_excl_tax * domestic_vat_rate

        # 可抵扣进项税额
        deductible_input = input_tax_total - input_tax_non_deductible - non_exempt_credit

        # 当期应纳税额（负数表示留抵）
        tax_payable = domestic_output_tax - deductible_input

        # 步骤3：免抵退税额（退税限额）
        max_refundable = export_sales * export_rebate_rate

        # 步骤4：应退税额 = min(留抵税额, 免抵退税额)
        if tax_payable < 0:
            # 有留抵税额
            remaining_credit = abs(tax_payable)
            refundable_tax = min(remaining_credit, max_refundable)
        else:
            # 无留抵，不退税
            refundable_tax = 0.0

        # 步骤5：免抵税额
        credit_tax = max_refundable - refundable_tax

        return ExportRebateResult(
            rebate_method=ExportRebateMethod.EXEMPT_CREDIT_AND_REFUND,
            export_sales=round(export_sales, 2),
            exempt_tax=0.0,  # 出口环节免征
            credit_tax=round(credit_tax, 2),
            refundable_tax=round(refundable_tax, 2),
            non_refundable_tax=round(non_exempt_credit, 2),
        )

    # ----------------------------------------------------------
    # 附加税费计算
    # ----------------------------------------------------------

    @staticmethod
    def calculate_additional_taxes(
        vat_payable: float,
        city_rate: float = CITY_MAINTENANCE_TAX_RATE,
        edu_rate: float = EDUCATION_SURCHARGE_RATE,
        local_edu_rate: float = LOCAL_EDUCATION_SURCHARGE_RATE,
    ) -> Dict[str, float]:
        """
        基于实际缴纳的增值税计算附加税费

        Args:
            vat_payable: 实际缴纳的增值税额
            city_rate: 城建税税率
            edu_rate: 教育费附加税率
            local_edu_rate: 地方教育附加税率

        Returns:
            各项附加税费金额字典
        """
        city_tax = vat_payable * city_rate
        edu_surcharge = vat_payable * edu_rate
        local_edu_surcharge = vat_payable * local_edu_rate
        total = city_tax + edu_surcharge + local_edu_surcharge

        return {
            "城市维护建设税": round(city_tax, 2),
            "教育费附加": round(edu_surcharge, 2),
            "地方教育附加": round(local_edu_surcharge, 2),
            "附加税费合计": round(total, 2),
        }

    # ----------------------------------------------------------
    # 汇总计算
    # ----------------------------------------------------------

    @staticmethod
    def full_calculation(
        taxpayer_type: TaxpayerType,
        # 一般纳税人参数
        sales_items: Optional[List[SalesItem]] = None,
        input_invoices: Optional[List[InputInvoiceItem]] = None,
        input_tax_transferred_out: float = 0.0,
        # 小规模纳税人参数
        monthly_sales: float = 0.0,
        calculation_period: str = "monthly",
        quarterly_sales: float = 0.0,
        # 视同销售参数
        deemed_sales_items: Optional[List[DeemedSalesItem]] = None,
        # 出口退税参数
        export_items: Optional[List[ExportItem]] = None,
        is_manufacturer: bool = False,
        domestic_sales_excl_tax: float = 0.0,
        # 附加税参数
        city_rate: float = CITY_MAINTENANCE_TAX_RATE,
    ) -> VATCalculationSummary:
        """
        增值税完整计算（汇总所有模块）

        Args:
            taxpayer_type: 纳税人类型
            sales_items: 销售明细（一般纳税人用）
            input_invoices: 进项发票（一般纳税人用）
            input_tax_transferred_out: 进项税额转出
            monthly_sales: 月销售额（小规模纳税人用）
            calculation_period: 计算周期
            quarterly_sales: 季度销售额
            deemed_sales_items: 视同销售明细
            export_items: 出口货物明细
            is_manufacturer: 是否为生产企业（出口退税用）
            domestic_sales_excl_tax: 内销不含税销售额（生产企业出口退税用）
            city_rate: 城建税税率

        Returns:
            VATCalculationSummary 汇总结果
        """
        total_vat_payable = 0.0
        details: Dict = {}

        if taxpayer_type == TaxpayerType.GENERAL:
            # 一般纳税人
            sales = sales_items or []
            invoices = input_invoices or []

            general_result = ValueAddedTaxCalculator.calculate_general_taxpayer(
                sales_items=sales,
                input_invoices=invoices,
                input_tax_transferred_out=input_tax_transferred_out,
            )
            total_vat_payable = general_result.vat_payable
            details["general_taxpayer"] = {
                "output_tax": general_result.output_tax,
                "input_tax": general_result.input_tax,
                "deductible_input_tax": general_result.deductible_input_tax,
                "input_tax_transferred_out": general_result.input_tax_transferred_out,
            }

        elif taxpayer_type == TaxpayerType.SMALL_SCALE:
            # 小规模纳税人
            small_result = ValueAddedTaxCalculator.calculate_small_scale(
                monthly_sales=monthly_sales,
                calculation_period=calculation_period,
                quarterly_sales=quarterly_sales,
            )
            total_vat_payable = small_result.vat_payable
            details["small_scale"] = {
                "total_sales": small_result.total_sales,
                "is_exempt": small_result.is_exempt,
                "applicable_levy_rate": small_result.applicable_levy_rate,
                "exempt_amount": small_result.exempt_amount,
            }

        # 视同销售（合并到应纳税额）
        if deemed_sales_items:
            deemed_result = ValueAddedTaxCalculator.calculate_deemed_sales(
                items=deemed_sales_items,
            )
            total_vat_payable += deemed_result.total_output_tax
            details["deemed_sales"] = {
                "total_deemed_sales": deemed_result.total_deemed_sales,
                "total_output_tax": deemed_result.total_output_tax,
            }

        # 出口退税（抵减应纳税额）
        if export_items:
            if is_manufacturer:
                # 生产企业：免抵退税
                export_result = ValueAddedTaxCalculator.calculate_export_rebate_for_manufacturer(
                    export_sales=sum(item.fob_amount for item in export_items),
                    export_rebate_rate=export_items[0].export_rebate_rate,
                    domestic_sales_excl_tax=domestic_sales_excl_tax,
                    input_tax_total=details.get("general_taxpayer", {}).get("input_tax", 0),
                )
            else:
                # 外贸企业：免退税
                export_result = ValueAddedTaxCalculator.calculate_export_rebate_for_trading_company(
                    export_items=export_items,
                )
            # 应退税额抵减应纳税额
            total_vat_payable = max(0, total_vat_payable - export_result.refundable_tax)
            details["export_rebate"] = {
                "rebate_method": export_result.rebate_method.value,
                "export_sales": export_result.export_sales,
                "refundable_tax": export_result.refundable_tax,
                "non_refundable_tax": export_result.non_refundable_tax,
            }

        total_vat_payable = round(total_vat_payable, 2)

        # 计算附加税费
        additional_taxes = ValueAddedTaxCalculator.calculate_additional_taxes(
            vat_payable=total_vat_payable,
            city_rate=city_rate,
        )

        return VATCalculationSummary(
            taxpayer_type=taxpayer_type,
            vat_payable=total_vat_payable,
            additional_taxes=additional_taxes,
            details=details,
        )
