# -*- coding: utf-8 -*-
"""真实申报数据回归（数值取自 2026 年真实申报表，已脱敏为纯数字）。

来源与用途：
- 2026Q3 印花税申报表：6 个税目、合计 7,543.49 元 → 验证税率表与四舍五入；
- 2026-09 增值税申报表：销项 224,842.61 / 进项 27,479.37 / 应纳 197,363.24，
  附加税 13,815.43 / 5,920.90 / 3,947.26 → 验证一般纳税人公式与附加税；
- 2025 年度企业所得税 A106000：2019 年亏损 415.06 万、2020 年 292.45 万、
  2021 年 284.87 万；2025 年弥补 101.15 万（2019 年已超 5 年未使用）→ 验证 TAX-07；
- 2026 年度残保金税局测算表：23 人 / 工资 2,631,736.68 / 0 残疾 / 社平 87,156，
  优惠前应纳 40,048.17，30 人以下免征 → 验证 FIL-01/FIL-03。

本文件只保留数字与口径，不包含公司名、税号、人名。
"""
import pytest

from app.modules.finance_dsf import service as dsf
from app.modules.finance_stamp_duty import service as stamp
from app.modules.finance_tax.services.additional_tax_calculator import (
    CityTier,
    calculate_surcharges,
)
from app.modules.finance_tax.services.corporate_income_tax_calculator import (
    CorporateIncomeTaxCalculator,
    EnterpriseInfo,
    LossCarryForward,
    TaxAdjustmentInput,
)
from app.modules.finance_vat import service as vat


# ---------------- 印花税：2026Q3 真实申报表 ----------------

def test_stamp_duty_real_q3_2026():
    items = [
        {"tax_item": "借款合同", "amount": 0.0},
        {"tax_item": "技术合同", "amount": 193_600.00},
        {"tax_item": "买卖合同", "amount": 37_840.30},          # 申报表写法（无括号）
        {"tax_item": "承揽合同", "amount": 420_002.60},
        {"tax_item": "建设工程合同", "amount": 360_085.18},
        {"tax_item": "租赁合同", "amount": 7_240_028.10},
    ]
    calc = stamp.calc_filing("2026-09", items, half_discount=False)
    detail = {d["tax_item"]: d["tax"] for d in calc["details"]}
    assert detail["技术合同"] == pytest.approx(58.08)
    assert detail["买卖合同"] == pytest.approx(11.35)
    assert detail["承揽合同"] == pytest.approx(126.00)
    assert detail["建设工程合同"] == pytest.approx(108.03)
    assert detail["租赁合同"] == pytest.approx(7_240.03)
    assert calc["total_tax"] == pytest.approx(7_543.49)


# ---------------- 增值税：2026-09 真实申报表 ----------------

def test_vat_general_real_sep_2026():
    # 本模块 amount 为不含税销售额（与申报表一致）
    output_items = [
        {"business_type": "9%货物", "amount": 29_179.84, "rate": 0.09},
        {"business_type": "9%服务", "amount": 1_906_615.21, "rate": 0.09},
        {"business_type": "6%服务", "amount": 843_683.67, "rate": 0.06},
    ]
    calc = vat.calc_general("2026-09", output_items, input_vat=27_479.37)
    # 申报表按发票逐张取整，模块按税目汇总取整，允许 0.10 元内差异
    assert calc["output_vat"] == pytest.approx(224_842.61, abs=0.10)
    assert calc["payable"] == pytest.approx(197_363.24, abs=0.10)
    assert calc["carryforward_vat"] == 0.0
    assert calc["warnings"] == []

    # 附加税：城建 7% / 教育 3% / 地方教育 2%
    sur = calculate_surcharges(vat_paid=197_363.24, city_tier=CityTier.URBAN)
    assert sur.city_maintenance_tax == pytest.approx(13_815.43)
    assert sur.education_surcharge == pytest.approx(5_920.90)
    assert sur.local_education_surcharge == pytest.approx(3_947.26)


def test_vat_general_carryforward_when_input_exceeds_output():
    calc = vat.calc_general("2026-09", [
        {"business_type": "服务", "amount": 100.0, "rate": 0.06},
    ], input_vat=10.0)
    assert calc["payable"] == 0.0
    assert calc["carryforward_vat"] == pytest.approx(4.0)
    assert calc["warnings"] and "留抵" in calc["warnings"][0]


def test_vat_small_scale_whitelist_and_month_threshold():
    # 征收率非白名单 → 拒绝（旧实现可传 -0.5 产生负税额）
    with pytest.raises(ValueError):
        vat.calc_small_scale("2026-09", 100_000, levy_rate=-0.5)
    # 按月申报 10 万免征；季末月校验只约束按季
    m = vat.calc_small_scale("2026-08", 100_000, levy_rate=0.01, filing_period="month")
    assert m["exempt"] is True and m["exempt_threshold"] == 100_000.0
    # 按季申报非季末月 → 拒绝
    with pytest.raises(ValueError):
        vat.calc_small_scale("2026-08", 100_000, levy_rate=0.01, filing_period="quarter")


# ---------------- 企业所得税：2025 年度 A106000 亏损结转 ----------------

def test_loss_carryforward_5_year_limit_real_schedule():
    losses = LossCarryForward(
        losses_by_year={2019: 415.06, 2020: 292.45, 2021: 284.87},
        tax_year=2025,
    )
    r2025 = CorporateIncomeTaxCalculator.calculate_loss_carry_forward(101.15, losses)
    # 2019 年亏损距 2025 已 6 年 → 过期；应先补 2020 年
    assert r2025["expired_losses"].get(2019) == pytest.approx(415.06)
    assert r2025["used_by_year"] == {2020: pytest.approx(101.15)}
    assert r2025["remaining_losses"][2020] == pytest.approx(191.30)
    assert r2025["taxable_income_after_loss_wan"] == 0.0

    # 2026 年：2020 年也已满 6 年 → 过期，改用 2021 年
    losses_2026 = LossCarryForward(
        losses_by_year={2020: 191.30, 2021: 284.87},
        tax_year=2026,
    )
    r2026 = CorporateIncomeTaxCalculator.calculate_loss_carry_forward(257.95, losses_2026)
    assert r2026["expired_losses"].get(2020) == pytest.approx(191.30)
    assert r2026["used_by_year"] == {2021: pytest.approx(257.95)}
    assert r2026["remaining_losses"][2021] == pytest.approx(26.92)


def test_small_micro_judged_after_loss_offset():
    """利润 400 万 − 以前年度亏损 200 万 = 200 万，应享小微 5%（10 万），而非 25%（50 万）。"""
    result = CorporateIncomeTaxCalculator.calculate(
        accounting_profit_wan=400.0,
        revenue_wan=1000.0,
        adjustments=TaxAdjustmentInput(),
        total_salary_wan=100.0,
        enterprise_info=EnterpriseInfo(
            employee_count=50, total_assets=1000.0, tax_year="2026",
        ),
        losses=LossCarryForward(losses_by_year={2024: 200.0}, tax_year=2026),
    )
    assert result.taxable_income_before_loss == pytest.approx(400.0)
    assert result.loss_carry_forward_used == pytest.approx(200.0)
    assert result.taxable_income_after_loss == pytest.approx(200.0)
    assert result.is_small_micro is True
    assert result.tax_amount == pytest.approx(10.0)


# ---------------- 残保金：2026 年度税局测算表 ----------------

def test_dsf_real_2026_sheet():
    calc = dsf.calc_dsf(
        "2026", 23, 2_631_736.68, 0, ratio=0.015, local_avg_wage=87_156,
    )
    assert calc["exempt"] is True
    assert calc["shortfall_persons"] == pytest.approx(0.35)
    assert calc["avg_salary"] == pytest.approx(114_423.33, abs=0.01)
    assert calc["wage_cap"] == pytest.approx(174_312.0)
    assert calc["charge_wage"] == pytest.approx(114_423.33, abs=0.01)
    assert calc["base_amount"] == pytest.approx(40_048.17, abs=0.02)
    assert calc["payable"] == 0.0
