"""附加税费 / 企业所得税计算引擎缺陷回归用例（审计新增）。

先 RED 后 GREEN：这些用例对应审计发现的真实缺陷，修复前必须失败。
"""
import math

import pytest

from app.modules.finance_tax.services.additional_tax_calculator import (
    StampDutyCategory,
    StampDutyItem,
    calculate_all_additional_taxes,
    calculate_stamp_duty,
    calculate_surcharges,
)
from app.modules.finance_tax.config.tax_rules import (
    calculate_entertainment_expense_limit,
)


# ------------------------------------------------------------
# BUG-A1：六税两费减半按"合计"打折，与按分项打折结果不一致
# 税法口径：减半是对各税费分别减半。合计打折在分项含奇数分时
# 会与分项打折产生 1 分钱差异，且 half_discount_amount 与实际
# 分项减半额不符。
# ------------------------------------------------------------
def test_surcharges_half_policy_item_level_consistency():
    # 构造：城建税 3.335 -> round 3.33（银行家舍入），教育附加 1.43，地方 0.95
    # 原始 total = 5.71
    vat_paid = 47.642857142857146  # 使各分项出现 .335
    result = calculate_surcharges(
        vat_paid=vat_paid,
        is_small_scale_taxpayer=True,
    )
    assert result.half_policy_applied is True

    item_level_sum = (
        round(result.city_maintenance_tax, 2)
        + round(result.education_surcharge, 2)
        + round(result.local_education_surcharge, 2)
    )
    # 分项减半之和必须等于合计（否则前端明细加起来对不上总数）
    assert item_level_sum == pytest.approx(result.additional_tax_total, abs=1e-9)
    # 减半额必须等于 原总额 - 实收总额
    assert result.half_discount_amount == pytest.approx(
        result.original_total - result.additional_tax_total, abs=1e-9
    )


# ------------------------------------------------------------
# BUG-A2：印花税减半时分项打折，但 stamp_duty_total 用合计打折，
# 且分项之和与 total 会出现 1 分钱差异（同上）
# ------------------------------------------------------------
def test_stamp_duty_half_policy_item_level_consistency():
    items = [
        StampDutyItem(StampDutyCategory.PURCHASE_SALES, 100000.01),  # 30.00
        StampDutyItem(StampDutyCategory.BUSINESS_BOOKS, 1000000.01),  # 250.00
    ]
    result = calculate_stamp_duty(items=items, is_small_micro_enterprise=True)
    assert result.half_policy_applied is True

    item_sum = round(sum(i["tax_amount"] for i in result.items), 2)
    assert item_sum == pytest.approx(result.stamp_duty_total, abs=1e-9)
    assert result.half_discount_amount == pytest.approx(
        result.original_total - result.stamp_duty_total, abs=1e-9
    )


# ------------------------------------------------------------
# BUG-A3：calculate_all_additional_taxes 汇总时，additional_tax_total
# 只把两类各自减半后的结果相加，口径正确；但 original_total /
# half_discount_amount 未做二次舍入，可能出现长尾小数
# （下游直接展示会出现 0.10000000000000009 之类）
# ------------------------------------------------------------
def test_all_additional_taxes_no_float_tail():
    result = calculate_all_additional_taxes(
        vat_paid=12345.67,
        consumption_tax_paid=0.01,
        stamp_duty_items=[StampDutyItem(StampDutyCategory.LOAN_CONTRACT, 1000000.0)],
        is_small_scale_taxpayer=True,
    )
    for value in (
        result.tax_base,
        result.original_total,
        result.additional_tax_total,
        result.half_discount_amount,
        result.stamp_duty_total,
    ):
        assert value == round(value, 2), f"存在长尾小数: {value!r}"


# ------------------------------------------------------------
# BUG-A4：_round_tax 使用内置 round()（银行家舍入）。
# 中国税务口径为四舍五入（ROUND_HALF_UP）：2.675 应进位到 2.68，
# 而 round(2.675, 2) = 2.67（二进制表示误差）。
# 这是典型的"差一分钱"缺陷，在税费计算里属于 P0。
# ------------------------------------------------------------
def test_round_half_up_semantics_for_tax():
    from app.modules.finance_tax.services.additional_tax_calculator import (
        calculate_stamp_duty_item,
    )

    # 100000 * 0.00025 = 25.0 -> 营业账簿
    assert calculate_stamp_duty_item(StampDutyCategory.BUSINESS_BOOKS, 100000) == 25.0
    # 构造 .005 边界：amount * rate 恰好为 x.xx5
    # 0.0003 税率下 amount=100016.666... 不易构造，改用 5 元/件：
    # amount=1 -> 5.0 无小数；改用 city tax：47.642857142857146*0.07=3.335
    got = calculate_stamp_duty_item(StampDutyCategory.BUSINESS_BOOKS, 100000)
    assert got == 25.0

    # 直接的 round 语义验证
    from app.modules.finance_tax.services.additional_tax_calculator import _round_tax

    assert _round_tax(2.675) == 2.68, "税费必须四舍五入（ROUND_HALF_UP），而非银行家舍入"
    assert _round_tax(1.005) == 1.01
    assert _round_tax(0.125) == 0.13


# ------------------------------------------------------------
# BUG-A5：calculate_stamp_duty 中 ratio 变量计算后未使用（死代码），
# 说明分项减半金额未按比例分摊 —— 但更严重的是：减半后分项之和
# 与 stamp_duty_total 可能不等（见 BUG-A2）。此处断言文档语义：
# 减半后各分项之和 == 合计。
# ------------------------------------------------------------
def test_stamp_duty_items_sum_equals_total_after_half():
    items = [
        StampDutyItem(StampDutyCategory.PURCHASE_SALES, 33333.33),
        StampDutyItem(StampDutyCategory.PROPERTY_LEASE, 12345.67),
        StampDutyItem(StampDutyCategory.LICENSE_PERMIT, 3, is_per_piece=True),
    ]
    result = calculate_stamp_duty(items=items, is_individual_business=True)
    total_from_items = round(sum(i["tax_amount"] for i in result.items), 2)
    assert total_from_items == result.stamp_duty_total


# ------------------------------------------------------------
# BUG-A6：业务招待费限额 —— 收入为 0 或负数时，limit_by_revenue 为 0，
# 导致 allowed_deduction=0、全额调增。税法上收入为 0 时 60% 限额仍适用。
# 现行实现把"收入为0"误判为"全额不得扣除"。
# ------------------------------------------------------------
def test_entertainment_limit_when_revenue_zero():
    result = calculate_entertainment_expense_limit(
        expense_amount=1000.0, revenue=0.0
    )
    # 收入为 0 时，60% 限额 600 元仍应准予扣除
    assert result["allowed_deduction"] == pytest.approx(600.0)
    assert result["tax_adjustment_increase"] == pytest.approx(400.0)


def test_entertainment_limit_normal_case_unchanged():
    result = calculate_entertainment_expense_limit(
        expense_amount=1000.0, revenue=10_000_000.0
    )
    # 收入 1 亿：5‰ = 50万 > 60% = 600，取 600
    assert result["allowed_deduction"] == pytest.approx(600.0)
    assert result["tax_adjustment_increase"] == pytest.approx(400.0)
