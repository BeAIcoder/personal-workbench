"""
附加税费计算服务
包含城市维护建设税、教育费附加、地方教育附加、印花税计算，
以及六税两费减半政策的支持。
"""
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP
from typing import Dict, Any, Optional, List
from enum import Enum
import math


# ============================================================
# 枚举定义
# ============================================================

class CityTier(Enum):
    """城建税地区档次"""
    URBAN = "市区"
    COUNTY_TOWN = "县镇"
    OTHER = "其他"


class StampDutyCategory(Enum):
    """印花税税目类别"""
    PURCHASE_SALES = "购销合同"
    PROCESSING = "加工承揽"
    CONSTRUCTION_SURVEY = "建设工程勘察设计"
    CONSTRUCTION_INSTALL = "建筑安装工程承包"
    PROPERTY_LEASE = "财产租赁"
    FREIGHT_TRANSPORT = "货物运输"
    WAREHOUSING = "仓储保管"
    LOAN_CONTRACT = "借款合同"
    PROPERTY_INSURANCE = "财产保险"
    TECHNOLOGY_CONTRACT = "技术合同"
    BUSINESS_BOOKS = "营业账簿"
    LICENSE_PERMIT = "权利许可证照"


class HalfTaxPolicyType(Enum):
    """六税两费减半政策适用对象类型"""
    SMALL_SCALE_TAXPAYER = "小规模纳税人"
    SMALL_MICRO_ENTERPRISE = "小型微利企业"
    INDIVIDUAL_BUSINESS = "个体工商户"


# ============================================================
# 税率常量定义
# ============================================================

# 城市维护建设税税率
CITY_MAINTENANCE_TAX_RATES = {
    CityTier.URBAN: 0.07,       # 市区 7%
    CityTier.COUNTY_TOWN: 0.05,  # 县镇 5%
    CityTier.OTHER: 0.01,        # 其他 1%
}

# 教育费附加税率
EDUCATION_SURCHARGE_RATE = 0.03       # 3%

# 地方教育附加税率
LOCAL_EDUCATION_SURCHARGE_RATE = 0.02  # 2%

# 印花税税率表
STAMP_DUTY_RATES = {
    StampDutyCategory.PURCHASE_SALES:       0.0003,    # 购销合同 0.03%
    StampDutyCategory.PROCESSING:            0.0005,    # 加工承揽 0.05%
    StampDutyCategory.CONSTRUCTION_SURVEY:   0.0005,    # 建设工程勘察设计 0.05%
    StampDutyCategory.CONSTRUCTION_INSTALL:  0.0003,    # 建筑安装工程承包 0.03%
    StampDutyCategory.PROPERTY_LEASE:        0.001,     # 财产租赁 0.1%
    StampDutyCategory.FREIGHT_TRANSPORT:     0.0005,    # 货物运输 0.05%
    StampDutyCategory.WAREHOUSING:           0.001,     # 仓储保管 0.1%
    StampDutyCategory.LOAN_CONTRACT:         0.00005,   # 借款合同 0.005%
    StampDutyCategory.PROPERTY_INSURANCE:    0.001,     # 财产保险 0.1%
    StampDutyCategory.TECHNOLOGY_CONTRACT:   0.0003,    # 技术合同 0.03%
    StampDutyCategory.BUSINESS_BOOKS:        0.00025,   # 营业账簿 0.025%
    # 权利许可证照为固定金额 5元/件，不在税率字典中
}

# 六税两费：可以被减半的税费类型
HALF_TAX_TYPES = [
    "城市维护建设税",
    "教育费附加",
    "地方教育附加",
    "印花税",
]


# ============================================================
# 数据类定义
# ============================================================

@dataclass
class StampDutyItem:
    """印花税单项"""
    category: StampDutyCategory
    amount: float           # 计税金额（合同金额或件数）
    is_per_piece: bool = False  # 是否按件计税（权利许可证照）


@dataclass
class AdditionalTaxResult:
    """附加税费计算结果"""
    # 城建税
    city_maintenance_tax: float = 0.0
    city_tier: CityTier = CityTier.URBAN

    # 教育费附加
    education_surcharge: float = 0.0

    # 地方教育附加
    local_education_surcharge: float = 0.0

    # 合计
    additional_tax_total: float = 0.0

    # 计税依据
    tax_base: float = 0.0  # 实际缴纳的增值税 + 消费税

    # 六税两费减半信息
    half_policy_applied: bool = False
    half_policy_types: List[str] = field(default_factory=list)
    original_total: float = 0.0
    half_discount_amount: float = 0.0


@dataclass
class StampDutyResult:
    """印花税计算结果"""
    items: List[Dict[str, Any]] = field(default_factory=list)
    stamp_duty_total: float = 0.0

    # 六税两费减半信息
    half_policy_applied: bool = False
    half_policy_types: List[str] = field(default_factory=list)
    original_total: float = 0.0
    half_discount_amount: float = 0.0


@dataclass
class FullAdditionalTaxResult:
    """完整附加税费计算结果（包含所有税费）"""
    # 计税依据
    vat_paid: float = 0.0                # 实际缴纳的增值税
    consumption_tax_paid: float = 0.0    # 实际缴纳的消费税
    tax_base: float = 0.0                # 合计计税依据

    # 城建税及附加
    city_maintenance_tax: float = 0.0
    education_surcharge: float = 0.0
    local_education_surcharge: float = 0.0

    # 印花税
    stamp_duty_items: List[Dict[str, Any]] = field(default_factory=list)
    stamp_duty_total: float = 0.0

    # 合计
    additional_tax_total: float = 0.0

    # 六税两费减半信息
    half_policy_applied: bool = False
    half_policy_types: List[str] = field(default_factory=list)
    original_total: float = 0.0
    half_discount_amount: float = 0.0


# ============================================================
# 核心计算函数
# ============================================================

def _round_tax(amount: float) -> float:
    """税费四舍五入到分（2位小数）

    税务口径为 ROUND_HALF_UP（四舍五入），而 Python 内置 round() 采用
    银行家舍入（ROUND_HALF_EVEN），且受二进制浮点表示误差影响
    （round(2.675, 2) == 2.67），会导致税费金额差 1 分并跨行放大。
    因此统一走 Decimal 十进制舍入。
    """
    if amount is None:
        return 0.0
    quantized = Decimal(str(float(amount))).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    return float(quantized)


def _check_half_policy_eligible(
    is_small_scale_taxpayer: bool = False,
    is_small_micro_enterprise: bool = False,
    is_individual_business: bool = False,
) -> tuple[bool, List[str]]:
    """
    检查是否符合六税两费减半政策条件

    Args:
        is_small_scale_taxpayer: 是否为小规模纳税人
        is_small_micro_enterprise: 是否为小型微利企业
        is_individual_business: 是否为个体工商户

    Returns:
        (是否符合条件, 符合条件的类型列表)
    """
    eligible_types = []
    if is_small_scale_taxpayer:
        eligible_types.append(HalfTaxPolicyType.SMALL_SCALE_TAXPAYER.value)
    if is_small_micro_enterprise:
        eligible_types.append(HalfTaxPolicyType.SMALL_MICRO_ENTERPRISE.value)
    if is_individual_business:
        eligible_types.append(HalfTaxPolicyType.INDIVIDUAL_BUSINESS.value)

    return len(eligible_types) > 0, eligible_types


def calculate_city_maintenance_tax(
    tax_base: float,
    city_tier: CityTier = CityTier.URBAN,
) -> float:
    """
    计算城市维护建设税

    Args:
        tax_base: 计税依据（实际缴纳的增值税 + 消费税）
        city_tier: 地区档次（市区/县镇/其他）

    Returns:
        城市维护建设税金额（四舍五入到分）
    """
    if tax_base <= 0:
        return 0.0
    rate = CITY_MAINTENANCE_TAX_RATES.get(city_tier, 0.07)
    return _round_tax(tax_base * rate)


def calculate_education_surcharge(tax_base: float) -> float:
    """
    计算教育费附加

    Args:
        tax_base: 计税依据（实际缴纳的增值税 + 消费税）

    Returns:
        教育费附加金额（四舍五入到分）
    """
    if tax_base <= 0:
        return 0.0
    return _round_tax(tax_base * EDUCATION_SURCHARGE_RATE)


def calculate_local_education_surcharge(tax_base: float) -> float:
    """
    计算地方教育附加

    Args:
        tax_base: 计税依据（实际缴纳的增值税 + 消费税）

    Returns:
        地方教育附加金额（四舍五入到分）
    """
    if tax_base <= 0:
        return 0.0
    return _round_tax(tax_base * LOCAL_EDUCATION_SURCHARGE_RATE)


def _split_half_amounts(
    original_items: List[float],
    half_discount: float,
) -> List[float]:
    """把「减半后实收总额」按原占比分摊到各分项，并保证分项之和 == 合计。

    税务口径：合计减半一次舍入为准（half_discount 已按合计算出），
    分项明细必须能加回该合计，否则前端明细与总数对账不平。
    做法：先按比例分摊，再用「最大余额法」把末位分差补到最后一项上。
    """
    n = len(original_items)
    if n == 0:
        return []
    if n == 1:
        return [_round_tax(original_items[0] - half_discount)]

    target_total = _round_tax(sum(original_items) - half_discount)
    base = sum(original_items)
    if base <= 0:
        return [0.0 for _ in original_items]

    # 1) 按占比分摊（每项四舍五入到分）
    shares: List[float] = []
    remainders: List[float] = []
    for value in original_items:
        share = _round_tax((value / base) * target_total)
        shares.append(share)
        remainders.append((value / base) * target_total - share)

    # 2) 最大余额法补差：把舍入差额按原始余数从大到小补给分项
    diff = _round_tax(target_total - sum(shares))
    if diff != 0.0:
        step = 0.01 if diff > 0 else -0.01
        remaining = int(round(abs(diff) / 0.01))
        order = sorted(range(n), key=lambda i: remainders[i], reverse=(diff > 0))
        idx = 0
        while remaining > 0 and order:
            i = order[idx % len(order)]
            shares[i] = _round_tax(shares[i] + step)
            remaining -= 1
            idx += 1
    return shares


def calculate_surcharges(
    vat_paid: float = 0.0,
    consumption_tax_paid: float = 0.0,
    city_tier: CityTier = CityTier.URBAN,
    is_small_scale_taxpayer: bool = False,
    is_small_micro_enterprise: bool = False,
    is_individual_business: bool = False,
) -> AdditionalTaxResult:
    """
    计算城市维护建设税、教育费附加、地方教育附加

    Args:
        vat_paid: 实际缴纳的增值税
        consumption_tax_paid: 实际缴纳的消费税
        city_tier: 城建税地区档次
        is_small_scale_taxpayer: 是否为小规模纳税人
        is_small_micro_enterprise: 是否为小型微利企业
        is_individual_business: 是否为个体工商户

    Returns:
        附加税费计算结果
    """
    tax_base = vat_paid + consumption_tax_paid
    if tax_base <= 0:
        return AdditionalTaxResult(
            tax_base=0.0,
            city_tier=city_tier,
        )

    # 计算各项附加税费
    city_tax = calculate_city_maintenance_tax(tax_base, city_tier)
    edu_surcharge = calculate_education_surcharge(tax_base)
    local_edu_surcharge = calculate_local_education_surcharge(tax_base)

    original_total = _round_tax(city_tax + edu_surcharge + local_edu_surcharge)

    # 检查六税两费减半政策
    eligible, eligible_types = _check_half_policy_eligible(
        is_small_scale_taxpayer, is_small_micro_enterprise, is_individual_business
    )

    half_discount = 0.0
    final_total = original_total
    final_city_tax = city_tax
    final_edu_surcharge = edu_surcharge
    final_local_edu_surcharge = local_edu_surcharge

    if eligible:
        # 合计口径优先：先按合计算一次减半额（一次舍入）
        half_discount = _round_tax(original_total * 0.5)
        final_total = _round_tax(original_total - half_discount)
        # 分项按合计占比分摊，保证 分项之和 == additional_tax_total
        (
            final_city_tax,
            final_edu_surcharge,
            final_local_edu_surcharge,
        ) = _split_half_amounts(
            [city_tax, edu_surcharge, local_edu_surcharge], half_discount
        )

    return AdditionalTaxResult(
        city_maintenance_tax=final_city_tax,
        education_surcharge=final_edu_surcharge,
        local_education_surcharge=final_local_edu_surcharge,
        additional_tax_total=final_total,
        tax_base=tax_base,
        city_tier=city_tier,
        half_policy_applied=eligible,
        half_policy_types=eligible_types,
        original_total=original_total,
        half_discount_amount=half_discount,
    )


def calculate_stamp_duty_item(
    category: StampDutyCategory,
    amount: float,
    is_per_piece: bool = False,
) -> float:
    """
    计算单个印花税项目

    Args:
        category: 印花税税目类别
        amount: 计税金额（合同金额或件数）
        is_per_piece: 是否按件计税（权利许可证照为5元/件）

    Returns:
        印花税额（四舍五入到分）
    """
    if amount <= 0:
        return 0.0

    if is_per_piece or category == StampDutyCategory.LICENSE_PERMIT:
        # 权利许可证照：5元/件
        return _round_tax(amount * 5.0)

    rate = STAMP_DUTY_RATES.get(category, 0.0)
    return _round_tax(amount * rate)


def calculate_stamp_duty(
    items: List[StampDutyItem],
    is_small_scale_taxpayer: bool = False,
    is_small_micro_enterprise: bool = False,
    is_individual_business: bool = False,
) -> StampDutyResult:
    """
    计算印花税（支持多个税目）

    Args:
        items: 印花税项目列表
        is_small_scale_taxpayer: 是否为小规模纳税人
        is_small_micro_enterprise: 是否为小型微利企业
        is_individual_business: 是否为个体工商户

    Returns:
        印花税计算结果
    """
    if not items:
        return StampDutyResult()

    item_results = []
    original_total = 0.0

    for item in items:
        tax_amount = calculate_stamp_duty_item(
            item.category, item.amount, item.is_per_piece
        )
        original_total += tax_amount

        item_results.append({
            "category": item.category.value,
            "amount": item.amount,
            "rate": _get_rate_display(item.category, item.is_per_piece),
            "tax_amount": tax_amount,
        })

    original_total = _round_tax(original_total)

    # 检查六税两费减半政策
    eligible, eligible_types = _check_half_policy_eligible(
        is_small_scale_taxpayer, is_small_micro_enterprise, is_individual_business
    )

    half_discount = 0.0
    final_total = original_total

    if eligible:
        half_discount = _round_tax(original_total * 0.5)
        final_total = _round_tax(original_total - half_discount)
        # 减半金额按合计口径分摊到各项目，保证分项之和 == stamp_duty_total
        if original_total > 0:
            shares = _split_half_amounts(
                [item_result["tax_amount"] for item_result in item_results],
                half_discount,
            )
            for item_result, share in zip(item_results, shares):
                item_result["tax_amount"] = share

    return StampDutyResult(
        items=item_results,
        stamp_duty_total=final_total,
        half_policy_applied=eligible,
        half_policy_types=eligible_types,
        original_total=original_total,
        half_discount_amount=half_discount,
    )


def _get_rate_display(category: StampDutyCategory, is_per_piece: bool = False) -> str:
    """获取印花税税率显示字符串"""
    if is_per_piece or category == StampDutyCategory.LICENSE_PERMIT:
        return "5元/件"
    rate = STAMP_DUTY_RATES.get(category, 0.0)
    # 转换为百分比显示
    percentage = rate * 100
    if percentage < 0.01:
        # 如 0.005%
        return f"{percentage:.3f}%"
    elif percentage < 0.1:
        # 如 0.025%
        return f"{percentage:.3f}%"
    else:
        return f"{percentage:.2f}%"


def calculate_all_additional_taxes(
    vat_paid: float = 0.0,
    consumption_tax_paid: float = 0.0,
    city_tier: CityTier = CityTier.URBAN,
    stamp_duty_items: Optional[List[StampDutyItem]] = None,
    is_small_scale_taxpayer: bool = False,
    is_small_micro_enterprise: bool = False,
    is_individual_business: bool = False,
) -> FullAdditionalTaxResult:
    """
    计算所有附加税费（城建税+教育费附加+地方教育附加+印花税）

    Args:
        vat_paid: 实际缴纳的增值税
        consumption_tax_paid: 实际缴纳的消费税
        city_tier: 城建税地区档次
        stamp_duty_items: 印花税项目列表
        is_small_scale_taxpayer: 是否为小规模纳税人
        is_small_micro_enterprise: 是否为小型微利企业
        is_individual_business: 是否为个体工商户

    Returns:
        完整附加税费计算结果
    """
    tax_base = vat_paid + consumption_tax_paid

    # 计算城建税及附加
    surcharge_result = calculate_surcharges(
        vat_paid=vat_paid,
        consumption_tax_paid=consumption_tax_paid,
        city_tier=city_tier,
        is_small_scale_taxpayer=is_small_scale_taxpayer,
        is_small_micro_enterprise=is_small_micro_enterprise,
        is_individual_business=is_individual_business,
    )

    # 计算印花税
    stamp_result = calculate_stamp_duty(
        items=stamp_duty_items or [],
        is_small_scale_taxpayer=is_small_scale_taxpayer,
        is_small_micro_enterprise=is_small_micro_enterprise,
        is_individual_business=is_individual_business,
    )

    # 汇总（二次舍入，避免 0.10000000000000009 类浮点长尾直接透出到展示层）
    original_total = _round_tax(
        surcharge_result.original_total + stamp_result.original_total
    )
    final_total = _round_tax(
        surcharge_result.additional_tax_total + stamp_result.stamp_duty_total
    )
    half_discount = _round_tax(
        surcharge_result.half_discount_amount + stamp_result.half_discount_amount
    )
    tax_base = _round_tax(vat_paid + consumption_tax_paid)

    # 合并减半政策类型（去重）
    all_half_types = list(set(surcharge_result.half_policy_types + stamp_result.half_policy_types))

    return FullAdditionalTaxResult(
        vat_paid=vat_paid,
        consumption_tax_paid=consumption_tax_paid,
        tax_base=tax_base,
        city_maintenance_tax=surcharge_result.city_maintenance_tax,
        education_surcharge=surcharge_result.education_surcharge,
        local_education_surcharge=surcharge_result.local_education_surcharge,
        stamp_duty_items=stamp_result.items,
        stamp_duty_total=stamp_result.stamp_duty_total,
        additional_tax_total=final_total,
        half_policy_applied=len(all_half_types) > 0,
        half_policy_types=all_half_types,
        original_total=original_total,
        half_discount_amount=half_discount,
    )


# ============================================================
# 便捷函数
# ============================================================

def get_city_maintenance_tax_rate(city_tier: CityTier = CityTier.URBAN) -> float:
    """获取城市维护建设税税率"""
    return CITY_MAINTENANCE_TAX_RATES.get(city_tier, 0.07)


def get_education_surcharge_rate() -> float:
    """获取教育费附加税率"""
    return EDUCATION_SURCHARGE_RATE


def get_local_education_surcharge_rate() -> float:
    """获取地方教育附加税率"""
    return LOCAL_EDUCATION_SURCHARGE_RATE


def get_stamp_duty_rate(category: StampDutyCategory) -> float:
    """获取印花税税率"""
    return STAMP_DUTY_RATES.get(category, 0.0)


def get_stamp_duty_per_piece_amount() -> float:
    """获取权利许可证照按件计税额"""
    return 5.0
