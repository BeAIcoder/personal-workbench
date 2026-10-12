"""
税率配置模块
包含企业所得税、增值税、附加税费的税率配置
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from enum import Enum


class TaxType(Enum):
    """税种类型"""
    CORPORATE_INCOME_TAX = "企业所得税"
    VALUE_ADDED_TAX = "增值税"
    ADDITIONAL_TAX = "附加税费"


class EnterpriseType(Enum):
    """企业类型"""
    STANDARD = "标准企业"
    HIGH_TECH = "高新技术企业"
    SMALL_MICRO = "小型微利企业"


class VATTaxpayerType(Enum):
    """增值税纳税人类型"""
    GENERAL = "一般纳税人"
    SMALL_SCALE = "小规模纳税人"


class CityTier(Enum):
    """城建税地区档次"""
    URBAN = "市区"
    COUNTY_TOWN = "县镇"
    OTHER = "其他"


@dataclass
class TaxRateItem:
    """单个税率配置项"""
    name: str
    rate: float  # 税率值，如 0.25 表示 25%
    description: str = ""
    effective_date: str = "2026-01-01"
    is_preferential: bool = False  # 是否为优惠税率
    conditions: Optional[Dict[str, any]] = None  # 适用条件


@dataclass
class TaxRateConfig:
    """税率配置集合"""
    name: str
    tax_type: TaxType
    rates: List[TaxRateItem] = field(default_factory=list)
    description: str = ""


# ============================================================
# 企业所得税税率配置
# ============================================================

CORPORATE_INCOME_TAX_RATES = TaxRateConfig(
    name="企业所得税税率",
    tax_type=TaxType.CORPORATE_INCOME_TAX,
    description="企业所得税适用税率配置",
    rates=[
        TaxRateItem(
            name="基本税率",
            rate=0.25,
            description="企业所得税标准税率",
            is_preferential=False,
        ),
        TaxRateItem(
            name="高新技术企业优惠税率",
            rate=0.15,
            description="经认定的高新技术企业减按15%税率征收企业所得税",
            is_preferential=True,
            conditions={
                "require_certification": True,
                "certification_type": "高新技术企业",
            },
        ),
        TaxRateItem(
            name="小型微利企业实际税负",
            rate=0.05,
            description="小型微利企业减按25%计入应纳税所得额，按20%税率缴纳，实际税负5%",
            is_preferential=True,
            conditions={
                "employee_count_max": 300,
                "total_assets_max": 50_000_000,  # 5000万
                "taxable_income_max": 3_000_000,  # 300万
                "not_restricted_industry": True,
            },
        ),
    ],
)


# ============================================================
# 增值税税率配置
# ============================================================

VALUE_ADDED_TAX_RATES = TaxRateConfig(
    name="增值税税率",
    tax_type=TaxType.VALUE_ADDED_TAX,
    description="增值税适用税率/征收率配置",
    rates=[
        # 一般纳税人税率
        TaxRateItem(
            name="一般纳税人-销售货物",
            rate=0.13,
            description="销售或者进口货物（除另有规定外）",
            is_preferential=False,
            conditions={"taxpayer_type": VATTaxpayerType.GENERAL, "business_type": "销售货物"},
        ),
        TaxRateItem(
            name="一般纳税人-交通运输",
            rate=0.09,
            description="交通运输服务、邮政服务、基础电信服务、建筑服务、不动产租赁服务、销售不动产、转让土地使用权",
            is_preferential=False,
            conditions={"taxpayer_type": VATTaxpayerType.GENERAL, "business_type": "交通运输等"},
        ),
        TaxRateItem(
            name="一般纳税人-现代服务",
            rate=0.06,
            description="现代服务（研发和技术服务、信息技术服务、文化创意服务、物流辅助服务、鉴证咨询服务等）、生活服务、金融服务、无形资产（除土地使用权外）",
            is_preferential=False,
            conditions={"taxpayer_type": VATTaxpayerType.GENERAL, "business_type": "现代服务"},
        ),
        # 小规模纳税人征收率
        TaxRateItem(
            name="小规模纳税人征收率",
            rate=0.01,
            description="2026年小规模纳税人增值税优惠政策，减按1%征收率征收",
            is_preferential=True,
            conditions={"taxpayer_type": VATTaxpayerType.SMALL_SCALE},
        ),
        # 免税政策
        TaxRateItem(
            name="月销售额≤10万免征",
            rate=0.0,
            description="小规模纳税人月销售额不超过10万元（季度不超过30万元）免征增值税",
            is_preferential=True,
            conditions={
                "taxpayer_type": VATTaxpayerType.SMALL_SCALE,
                "monthly_sales_max": 100_000,
                "quarterly_sales_max": 300_000,
            },
        ),
    ],
)


# ============================================================
# 附加税费税率配置
# ============================================================

ADDITIONAL_TAX_RATES = TaxRateConfig(
    name="附加税费税率",
    tax_type=TaxType.ADDITIONAL_TAX,
    description="城建税、教育费附加、地方教育附加税率配置",
    rates=[
        # 城市维护建设税
        TaxRateItem(
            name="城市维护建设税-市区",
            rate=0.07,
            description="纳税人所在地在市区的，按7%征收",
            is_preferential=False,
            conditions={"city_tier": CityTier.URBAN},
        ),
        TaxRateItem(
            name="城市维护建设税-县镇",
            rate=0.05,
            description="纳税人所在地在县镇的，按5%征收",
            is_preferential=False,
            conditions={"city_tier": CityTier.COUNTY_TOWN},
        ),
        TaxRateItem(
            name="城市维护建设税-其他",
            rate=0.01,
            description="纳税人所在地不在市区、县镇的，按1%征收",
            is_preferential=False,
            conditions={"city_tier": CityTier.OTHER},
        ),
        # 教育费附加
        TaxRateItem(
            name="教育费附加",
            rate=0.03,
            description="按实际缴纳的增值税和消费税税额的3%征收",
            is_preferential=False,
        ),
        # 地方教育附加
        TaxRateItem(
            name="地方教育附加",
            rate=0.02,
            description="按实际缴纳的增值税和消费税税额的2%征收",
            is_preferential=False,
        ),
    ],
)


# ============================================================
# 汇总所有税率配置
# ============================================================

ALL_TAX_RATES: List[TaxRateConfig] = [
    CORPORATE_INCOME_TAX_RATES,
    VALUE_ADDED_TAX_RATES,
    ADDITIONAL_TAX_RATES,
]


# ============================================================
# 便捷查询函数
# ============================================================

def get_standard_cit_rate() -> float:
    """获取企业所得税标准税率"""
    return 0.25


def get_high_tech_cit_rate() -> float:
    """获取高新技术企业优惠税率"""
    return 0.15


def get_small_micro_cit_rate() -> float:
    """获取小型微利企业实际税负率"""
    return 0.05


def get_vat_rate_general_sales() -> float:
    """获取一般纳税人销售货物增值税税率"""
    return 0.13


def get_vat_rate_general_transport() -> float:
    """获取一般纳税人交通运输增值税税率"""
    return 0.09


def get_vat_rate_general_service() -> float:
    """获取一般纳税人现代服务增值税税率"""
    return 0.06


def get_vat_rate_small_scale() -> float:
    """获取小规模纳税人征收率（2026年优惠）"""
    return 0.01


def get_vat_exempt_monthly_threshold() -> float:
    """获取增值税月销售额免征门槛"""
    return 100_000


def get_vat_exempt_quarterly_threshold() -> float:
    """获取增值税季度销售额免征门槛"""
    return 300_000


def get_city_maintenance_tax_rate(city_tier: CityTier = CityTier.URBAN) -> float:
    """获取城市维护建设税税率"""
    rates = {
        CityTier.URBAN: 0.07,
        CityTier.COUNTY_TOWN: 0.05,
        CityTier.OTHER: 0.01,
    }
    return rates.get(city_tier, 0.07)


def get_education_surcharge_rate() -> float:
    """获取教育费附加税率"""
    return 0.03


def get_local_education_surcharge_rate() -> float:
    """获取地方教育附加税率"""
    return 0.02


def calculate_additional_tax_rates(vat_amount: float, city_tier: CityTier = CityTier.URBAN) -> Dict[str, float]:
    """
    计算附加税费（基于实际缴纳的增值税额）

    Args:
        vat_amount: 实际缴纳的增值税额
        city_tier: 城建税地区档次

    Returns:
        各项附加税费金额字典
    """
    city_rate = get_city_maintenance_tax_rate(city_tier)
    edu_rate = get_education_surcharge_rate()
    local_edu_rate = get_local_education_surcharge_rate()

    return {
        "城市维护建设税": vat_amount * city_rate,
        "教育费附加": vat_amount * edu_rate,
        "地方教育附加": vat_amount * local_edu_rate,
        "附加税费合计": vat_amount * (city_rate + edu_rate + local_edu_rate),
    }
