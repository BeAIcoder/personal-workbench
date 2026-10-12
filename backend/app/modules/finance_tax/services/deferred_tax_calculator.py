"""
递延所得税计算器（中级会计实务）

依据《企业会计准则第18号——所得税》，实现以下功能：
1. 暂时性差异识别（应纳税暂时性差异 / 可抵扣暂时性差异）
2. 递延所得税负债计算
3. 递延所得税资产计算（含未来应纳税所得额验证）
4. 所得税费用计算（当期所得税 + 递延所得税）
5. 亏损结转抵扣计算（5年/10年）
6. 税率变动对递延所得税的影响
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Dict, List, Optional, Tuple


# ============================================================
# 枚举类型
# ============================================================

class DifferenceType(Enum):
    """暂时性差异类型"""
    TAXABLE = "应纳税暂时性差异"       # 资产账面价值 > 计税基础，或负债账面价值 < 计税基础
    DEDUCTIBLE = "可抵扣暂时性差异"    # 资产账面价值 < 计税基础，或负债账面价值 > 计税基础


class ItemCategory(Enum):
    """项目类别"""
    ASSET = "资产"
    LIABILITY = "负债"


class EnterpriseNature(Enum):
    """企业性质（影响亏损结转年限）"""
    STANDARD = "标准企业"                # 亏损结转5年
    HIGH_TECH = "高新技术企业"           # 亏损结转10年
    TECH_SME = "科技型中小企业"          # 亏损结转10年


# ============================================================
# 数据类
# ============================================================

@dataclass
class BalanceSheetItem:
    """资产负债项目"""
    name: str                                    # 项目名称
    category: ItemCategory                       # 资产/负债
    book_value: float                            # 账面价值
    tax_base: float                              # 计税基础
    description: str = ""                        # 说明


@dataclass
class TemporaryDifference:
    """暂时性差异"""
    item: BalanceSheetItem                       # 对应资产负债项目
    difference_type: DifferenceType              # 差异类型
    amount: float                                # 差异金额（绝对值）

    @property
    def signed_amount(self) -> float:
        """有符号差异金额：应纳税为正，可抵扣为负"""
        if self.difference_type == DifferenceType.TAXABLE:
            return self.amount
        return -self.amount


@dataclass
class DeferredTaxItem:
    """递延所得税项目"""
    temporary_difference: TemporaryDifference    # 对应的暂时性差异
    applicable_tax_rate: float                   # 适用税率
    deferred_tax_amount: float                   # 递延所得税金额

    @property
    def is_asset(self) -> bool:
        return self.temporary_difference.difference_type == DifferenceType.DEDUCTIBLE

    @property
    def is_liability(self) -> bool:
        return self.temporary_difference.difference_type == DifferenceType.TAXABLE


@dataclass
class LossCarryforwardRecord:
    """亏损结转记录"""
    loss_year: int                               # 亏损年度
    loss_amount: float                           # 亏损金额
    remaining_amount: float                      # 尚未弥补的亏损余额
    expiry_year: int                             # 结转到期年度
    utilized_by_year: Dict[int, float] = field(default_factory=dict)  # 各年已弥补金额


@dataclass
class TaxRateChangeRecord:
    """税率变动记录"""
    effective_date: date                         # 生效日期
    old_rate: float                              # 原税率
    new_rate: float                              # 新税率
    description: str = ""                        # 说明


@dataclass
class DeferredTaxCalculationResult:
    """递延所得税计算结果"""
    # 暂时性差异
    temporary_differences: List[TemporaryDifference] = field(default_factory=list)
    taxable_differences: List[TemporaryDifference] = field(default_factory=list)
    deductible_differences: List[TemporaryDifference] = field(default_factory=list)

    # 递延所得税
    deferred_tax_items: List[DeferredTaxItem] = field(default_factory=list)
    deferred_tax_liability_total: float = 0.0
    deferred_tax_asset_total: float = 0.0

    # 所得税费用
    current_tax_expense: float = 0.0             # 当期所得税
    deferred_tax_expense: float = 0.0            # 递延所得税费用
    total_income_tax_expense: float = 0.0        # 所得税费用合计

    # 亏损结转
    loss_carryforward_records: List[LossCarryforwardRecord] = field(default_factory=list)

    # 税率变动影响
    tax_rate_change_impact: float = 0.0          # 税率变动对递延所得税的影响金额

    # 详细信息
    details: Dict[str, any] = field(default_factory=dict)


# ============================================================
# 核心计算器
# ============================================================

class DeferredTaxCalculator:
    """
    递延所得税计算器

    依据《企业会计准则第18号——所得税》：
    - 资产账面价值 > 计税基础 → 应纳税暂时性差异 → 递延所得税负债
    - 资产账面价值 < 计税基础 → 可抵扣暂时性差异 → 递延所得税资产
    - 负债账面价值 > 计税基础 → 可抵扣暂时性差异 → 递延所得税资产
    - 负债账面价值 < 计税基础 → 应纳税暂时性差异 → 递延所得税负债
    """

    # 默认亏损结转年限
    DEFAULT_LOSS_CARRYFORWARD_YEARS = 5
    HIGH_TECH_LOSS_CARRYFORWARD_YEARS = 10

    def __init__(
        self,
        current_tax_rate: float = 0.25,
        enterprise_nature: EnterpriseNature = EnterpriseNature.STANDARD,
        future_taxable_income_projections: Optional[Dict[int, float]] = None,
    ):
        """
        初始化递延所得税计算器

        Args:
            current_tax_rate: 当前适用税率（默认25%）
            enterprise_nature: 企业性质
            future_taxable_income_projections: 未来各年应纳税所得额预测 {年份: 金额}
        """
        self.current_tax_rate = current_tax_rate
        self.enterprise_nature = enterprise_nature
        self.future_taxable_income_projections = future_taxable_income_projections or {}
        self.tax_rate_changes: List[TaxRateChangeRecord] = []
        self.existing_deferred_tax_assets: float = 0.0  # 期初递延所得税资产余额
        self.existing_deferred_tax_liabilities: float = 0.0  # 期初递延所得税负债余额

    def set_tax_rate_changes(self, changes: List[TaxRateChangeRecord]) -> None:
        """设置税率变动记录"""
        self.tax_rate_changes = sorted(changes, key=lambda x: x.effective_date)

    def set_existing_balances(
        self,
        deferred_tax_assets: float = 0.0,
        deferred_tax_liabilities: float = 0.0,
    ) -> None:
        """设置期初递延所得税余额"""
        self.existing_deferred_tax_assets = deferred_tax_assets
        self.existing_deferred_tax_liabilities = deferred_tax_liabilities

    # ----------------------------------------------------------
    # 1. 暂时性差异识别
    # ----------------------------------------------------------

    @staticmethod
    def identify_temporary_differences(
        items: List[BalanceSheetItem],
    ) -> List[TemporaryDifference]:
        """
        识别资产负债项目中的暂时性差异

        判断规则：
        - 资产：账面价值 > 计税基础 → 应纳税暂时性差异
        - 资产：账面价值 < 计税基础 → 可抵扣暂时性差异
        - 负债：账面价值 > 计税基础 → 可抵扣暂时性差异
        - 负债：账面价值 < 计税基础 → 应纳税暂时性差异

        Args:
            items: 资产负债项目列表

        Returns:
            暂时性差异列表（仅包含存在差异的项目）
        """
        differences = []

        for item in items:
            diff = item.book_value - item.tax_base

            # 忽略无差异项目（考虑浮点精度）
            if abs(diff) < 1e-6:
                continue

            if item.category == ItemCategory.ASSET:
                # 资产：账面价值 > 计税基础 → 应纳税
                if diff > 0:
                    diff_type = DifferenceType.TAXABLE
                else:
                    diff_type = DifferenceType.DEDUCTIBLE
            else:
                # 负债：账面价值 > 计税基础 → 可抵扣
                if diff > 0:
                    diff_type = DifferenceType.DEDUCTIBLE
                else:
                    diff_type = DifferenceType.TAXABLE

            differences.append(TemporaryDifference(
                item=item,
                difference_type=diff_type,
                amount=abs(diff),
            ))

        return differences

    @staticmethod
    def classify_differences(
        differences: List[TemporaryDifference],
    ) -> Tuple[List[TemporaryDifference], List[TemporaryDifference]]:
        """
        分类暂时性差异

        Returns:
            (应纳税暂时性差异列表, 可抵扣暂时性差异列表)
        """
        taxable = [d for d in differences if d.difference_type == DifferenceType.TAXABLE]
        deductible = [d for d in differences if d.difference_type == DifferenceType.DEDUCTIBLE]
        return taxable, deductible

    # ----------------------------------------------------------
    # 2 & 3. 递延所得税负债/资产计算
    # ----------------------------------------------------------

    def _get_applicable_rate(self, difference: TemporaryDifference, reference_date: Optional[date] = None) -> float:
        """
        获取暂时性差异的适用税率

        考虑已公布的税率变动：如果差异预计转回期间适用新税率，则使用新税率。

        Args:
            difference: 暂时性差异
            reference_date: 参考日期（用于判断适用哪个税率）

        Returns:
            适用税率
        """
        if not self.tax_rate_changes:
            return self.current_tax_rate

        # 查找参考日期之后生效的税率
        applicable_rate = self.current_tax_rate
        for change in self.tax_rate_changes:
            if reference_date is None or change.effective_date >= reference_date:
                applicable_rate = change.new_rate

        return applicable_rate

    def calculate_deferred_tax_liability(
        self,
        taxable_differences: List[TemporaryDifference],
        reference_date: Optional[date] = None,
    ) -> List[DeferredTaxItem]:
        """
        计算递延所得税负债

        公式：递延所得税负债 = 应纳税暂时性差异 × 适用税率

        Args:
            taxable_differences: 应纳税暂时性差异列表
            reference_date: 参考日期

        Returns:
            递延所得税负债项目列表
        """
        items = []
        for diff in taxable_differences:
            rate = self._get_applicable_rate(diff, reference_date)
            deferred_amount = diff.amount * rate
            items.append(DeferredTaxItem(
                temporary_difference=diff,
                applicable_tax_rate=rate,
                deferred_tax_amount=deferred_amount,
            ))
        return items

    def calculate_deferred_tax_asset(
        self,
        deductible_differences: List[TemporaryDifference],
        reference_date: Optional[date] = None,
        verify_future_income: bool = True,
    ) -> List[DeferredTaxItem]:
        """
        计算递延所得税资产

        公式：递延所得税资产 = 可抵扣暂时性差异 × 适用税率
        需验证未来是否有足够应纳税所得额支持确认

        Args:
            deductible_differences: 可抵扣暂时性差异列表（或单个差异对象）
            reference_date: 参考日期
            verify_future_income: 是否验证未来应纳税所得额

        Returns:
            递延所得税资产项目列表（已通过验证的）
        """
        # Support single item or list
        if isinstance(deductible_differences, TemporaryDifference):
            deductible_differences = [deductible_differences]

        items = []
        total_deductible_diff = sum(d.amount for d in deductible_differences)

        if verify_future_income and self.future_taxable_income_projections:
            # 验证未来应纳税所得额是否足以支持递延所得税资产确认
            total_future_income = sum(self.future_taxable_income_projections.values())
            if total_future_income < total_deductible_diff:
                # 未来应纳税所得额不足，按比例确认
                recognition_ratio = total_future_income / total_deductible_diff if total_deductible_diff > 0 else 0
            else:
                recognition_ratio = 1.0
        else:
            recognition_ratio = 1.0

        for diff in deductible_differences:
            rate = self._get_applicable_rate(diff, reference_date)
            deferred_amount = diff.amount * rate * recognition_ratio
            items.append(DeferredTaxItem(
                temporary_difference=diff,
                applicable_tax_rate=rate,
                deferred_tax_amount=deferred_amount,
            ))

        return items

    # ----------------------------------------------------------
    # 4. 所得税费用计算
    # ----------------------------------------------------------

    @staticmethod
    def calculate_income_tax_expense(
        current_tax_expense: float,
        opening_deferred_tax_assets: float,
        closing_deferred_tax_assets: float,
        opening_deferred_tax_liabilities: float,
        closing_deferred_tax_liabilities: float,
    ) -> Dict[str, float]:
        """
        计算所得税费用

        公式：
        - 所得税费用 = 当期所得税 + 递延所得税
        - 递延所得税费用 = (期末递延所得税负债 - 期初递延所得税负债)
                         - (期末递延所得税资产 - 期初递延所得税资产)

        Args:
            current_tax_expense: 当期所得税费用
            opening_deferred_tax_assets: 期初递延所得税资产
            closing_deferred_tax_assets: 期末递延所得税资产
            opening_deferred_tax_liabilities: 期初递延所得税负债
            closing_deferred_tax_liabilities: 期末递延所得税负债

        Returns:
            包含所得税费用各组成部分的字典
        """
        deferred_tax_liability_change = closing_deferred_tax_liabilities - opening_deferred_tax_liabilities
        deferred_tax_asset_change = closing_deferred_tax_assets - opening_deferred_tax_assets
        deferred_tax_expense = deferred_tax_liability_change - deferred_tax_asset_change
        total_income_tax_expense = current_tax_expense + deferred_tax_expense

        return {
            "current_tax_expense": current_tax_expense,
            "opening_deferred_tax_assets": opening_deferred_tax_assets,
            "closing_deferred_tax_assets": closing_deferred_tax_assets,
            "opening_deferred_tax_liabilities": opening_deferred_tax_liabilities,
            "closing_deferred_tax_liabilities": closing_deferred_tax_liabilities,
            "deferred_tax_liability_change": deferred_tax_liability_change,
            "deferred_tax_asset_change": deferred_tax_asset_change,
            "deferred_tax_expense": deferred_tax_expense,
            "total_income_tax_expense": total_income_tax_expense,
        }

    # ----------------------------------------------------------
    # 5. 亏损结转抵扣计算
    # ----------------------------------------------------------

    def get_loss_carryforward_years(self) -> int:
        """
        获取亏损结转年限

        - 标准企业：5年
        - 高新技术企业/科技型中小企业：10年
        """
        if self.enterprise_nature in (EnterpriseNature.HIGH_TECH, EnterpriseNature.TECH_SME):
            return self.HIGH_TECH_LOSS_CARRYFORWARD_YEARS
        return self.DEFAULT_LOSS_CARRYFORWARD_YEARS

    @staticmethod
    def create_loss_carryforward_records(
        losses: Dict[int, float],
        carryforward_years: int = 5,
    ) -> List[LossCarryforwardRecord]:
        """
        创建亏损结转记录

        Args:
            losses: 各年度亏损字典 {年份: 亏损金额}
            carryforward_years: 可结转年限

        Returns:
            亏损结转记录列表
        """
        records = []
        for loss_year, loss_amount in sorted(losses.items()):
            if loss_amount <= 0:
                continue
            records.append(LossCarryforwardRecord(
                loss_year=loss_year,
                loss_amount=loss_amount,
                remaining_amount=loss_amount,
                expiry_year=loss_year + carryforward_years,
                utilized_by_year={},
            ))
        return records

    @staticmethod
    def utilize_loss_carryforward(
        records: List[LossCarryforwardRecord],
        current_year: int,
        current_year_taxable_income: float,
    ) -> Tuple[float, List[LossCarryforwardRecord]]:
        """
        使用亏损结转抵扣当年应纳税所得额

        规则：先亏先补（按亏损发生时间先后顺序弥补）

        Args:
            records: 亏损结转记录列表
            current_year: 当前年度
            current_year_taxable_income: 当年应纳税所得额

        Returns:
            (弥补后应纳税所得额, 更新后的亏损结转记录列表)
        """
        remaining_income = current_year_taxable_income
        updated_records = copy.deepcopy(records)

        # 按亏损年度排序（先亏先补）
        sorted_records = sorted(updated_records, key=lambda r: r.loss_year)

        for record in sorted_records:
            # 跳过已过期或已弥补完毕的亏损
            if current_year > record.expiry_year:
                continue
            if record.remaining_amount <= 0:
                continue

            # 计算当年可弥补金额
            utilizable = min(remaining_income, record.remaining_amount)
            if utilizable <= 0:
                continue

            record.remaining_amount -= utilizable
            record.utilized_by_year[current_year] = record.utilized_by_year.get(current_year, 0) + utilizable
            remaining_income -= utilizable

            if remaining_income <= 0:
                break

        return max(0, remaining_income), updated_records

    def calculate_loss_carryforward_tax_saving(
        self,
        records: List[LossCarryforwardRecord],
        tax_rate: float,
    ) -> float:
        """
        计算亏损结转产生的税收节省

        Args:
            records: 亏损结转记录列表
            tax_rate: 适用税率

        Returns:
            税收节省金额
        """
        total_utilized = sum(
            sum(record.utilized_by_year.values())
            for record in records
        )
        return total_utilized * tax_rate

    # ----------------------------------------------------------
    # 6. 税率变动对递延所得税的影响
    # ----------------------------------------------------------

    def calculate_tax_rate_change_impact(
        self,
        taxable_differences: List[TemporaryDifference],
        deductible_differences: List[TemporaryDifference],
        old_rate: float,
        new_rate: float,
    ) -> Dict[str, float]:
        """
        计算税率变动对递延所得税的影响

        依据准则：税率变动时，应对已确认的递延所得税资产和负债
        按新税率进行重新计量，影响金额计入当期所得税费用。

        Args:
            taxable_differences: 应纳税暂时性差异列表
            deductible_differences: 可抵扣暂时性差异列表
            old_rate: 原税率
            new_rate: 新税率

        Returns:
            包含税率变动影响详情的字典
        """
        rate_change = new_rate - old_rate

        # 对递延所得税负债的影响
        taxable_impact = sum(d.amount for d in taxable_differences) * rate_change

        # 对递延所得税资产的影响
        deductible_impact = sum(d.amount for d in deductible_differences) * rate_change

        # 净影响 = 负债影响 - 资产影响
        net_impact = taxable_impact - deductible_impact

        return {
            "old_rate": old_rate,
            "new_rate": new_rate,
            "rate_change": rate_change,
            "taxable_differences_total": sum(d.amount for d in taxable_differences),
            "deductible_differences_total": sum(d.amount for d in deductible_differences),
            "deferred_tax_liability_impact": taxable_impact,
            "deferred_tax_asset_impact": deductible_impact,
            "net_impact_on_income_tax_expense": net_impact,
        }

    # ----------------------------------------------------------
    # 综合计算
    # ----------------------------------------------------------

    def full_calculation(
        self,
        balance_sheet_items: List[BalanceSheetItem],
        current_year_taxable_income: float,
        current_year: int,
        reference_date: Optional[date] = None,
        historical_losses: Optional[Dict[int, float]] = None,
        verify_future_income: bool = True,
    ) -> DeferredTaxCalculationResult:
        """
        完整递延所得税计算

        Args:
            balance_sheet_items: 资产负债项目列表
            current_year_taxable_income: 当年应纳税所得额
            current_year: 当前年度
            reference_date: 参考日期
            historical_losses: 历史亏损记录 {年份: 金额}
            verify_future_income: 是否验证未来应纳税所得额

        Returns:
            完整的递延所得税计算结果
        """
        result = DeferredTaxCalculationResult()

        # 1. 识别暂时性差异
        differences = self.identify_temporary_differences(balance_sheet_items)
        result.temporary_differences = differences
        result.taxable_differences, result.deductible_differences = self.classify_differences(differences)

        # 2 & 3. 计算递延所得税负债和资产
        dtl_items = self.calculate_deferred_tax_liability(
            result.taxable_differences, reference_date
        )
        dta_items = self.calculate_deferred_tax_asset(
            result.deductible_differences, reference_date, verify_future_income
        )
        result.deferred_tax_items = dtl_items + dta_items

        # 期末递延所得税余额
        closing_dtl = sum(item.deferred_tax_amount for item in dtl_items)
        closing_dta = sum(item.deferred_tax_amount for item in dta_items)
        result.deferred_tax_liability_total = closing_dtl
        result.deferred_tax_asset_total = closing_dta

        # 5. 亏损结转处理
        if historical_losses:
            carryforward_years = self.get_loss_carryforward_years()
            loss_records = self.create_loss_carryforward_records(
                historical_losses, carryforward_years
            )
            remaining_income, updated_records = self.utilize_loss_carryforward(
                loss_records, current_year, current_year_taxable_income
            )
            result.loss_carryforward_records = updated_records

            # 亏损结转后的应纳税所得额用于计算当期所得税
            adjusted_taxable_income = remaining_income
            result.details["original_taxable_income"] = current_year_taxable_income
            result.details["loss_utilized"] = current_year_taxable_income - remaining_income
            result.details["adjusted_taxable_income"] = remaining_income
        else:
            adjusted_taxable_income = max(0, current_year_taxable_income)

        # 4. 计算所得税费用（当期所得税最低为0）
        current_tax = max(0, adjusted_taxable_income) * self.current_tax_rate
        result.current_tax_expense = current_tax

        expense_details = self.calculate_income_tax_expense(
            current_tax_expense=current_tax,
            opening_deferred_tax_assets=self.existing_deferred_tax_assets,
            closing_deferred_tax_assets=closing_dta,
            opening_deferred_tax_liabilities=self.existing_deferred_tax_liabilities,
            closing_deferred_tax_liabilities=closing_dtl,
        )
        result.deferred_tax_expense = expense_details["deferred_tax_expense"]
        result.total_income_tax_expense = expense_details["total_income_tax_expense"]

        # 6. 税率变动影响
        if self.tax_rate_changes:
            # 使用最新税率变动
            latest_change = self.tax_rate_changes[-1]
            rate_impact = self.calculate_tax_rate_change_impact(
                result.taxable_differences,
                result.deductible_differences,
                latest_change.old_rate,
                latest_change.new_rate,
            )
            result.tax_rate_change_impact = rate_impact["net_impact_on_income_tax_expense"]
            result.details["tax_rate_change_impact_details"] = rate_impact

        return result


# ============================================================
# 便捷构建器
# ============================================================

class DeferredTaxItemBuilder:
    """递延所得税项目便捷构建器，用于快速创建常见差异项目"""

    @staticmethod
    def create_fixed_asset_depreciation_diff(
        asset_name: str,
        book_value: float,
        tax_base: float,
        description: str = "",
    ) -> BalanceSheetItem:
        """
        创建固定资产折旧差异项目

        常见场景：
        - 会计采用直线法，税法允许加速折旧 → 账面价值 > 计税基础 → 应纳税差异
        - 会计计提减值，税法不认可 → 账面价值 < 计税基础 → 可抵扣差异
        """
        return BalanceSheetItem(
            name=asset_name,
            category=ItemCategory.ASSET,
            book_value=book_value,
            tax_base=tax_base,
            description=description or "固定资产折旧差异",
        )

    @staticmethod
    def create_impairment_provision_diff(
        asset_name: str,
        book_value: float,
        tax_base: float,
        description: str = "",
    ) -> BalanceSheetItem:
        """
        创建资产减值准备差异项目

        税法规定：资产减值准备在计提时不允许税前扣除，
        实际发生损失时才允许扣除 → 可抵扣暂时性差异
        """
        return BalanceSheetItem(
            name=asset_name,
            category=ItemCategory.ASSET,
            book_value=book_value,
            tax_base=tax_base,
            description=description or "资产减值准备差异",
        )

    @staticmethod
    def create_provision_liability_diff(
        liability_name: str,
        book_value: float,
        tax_base: float = 0.0,
        description: str = "",
    ) -> BalanceSheetItem:
        """
        创建预计负债差异项目

        税法规定：预计负债在实际支付时才允许税前扣除，
        账面价值 > 计税基础(0) → 可抵扣暂时性差异
        """
        return BalanceSheetItem(
            name=liability_name,
            category=ItemCategory.LIABILITY,
            book_value=book_value,
            tax_base=tax_base,
            description=description or "预计负债差异",
        )

    @staticmethod
    def create_developed_intangible_asset_diff(
        asset_name: str,
        book_value: float,
        tax_base: float,
        description: str = "",
    ) -> BalanceSheetItem:
        """
        创建自行研发无形资产差异项目

        税法规定：研发形成无形资产按175%/200%摊销，
        计税基础 > 账面价值 → 可抵扣暂时性差异
        """
        return BalanceSheetItem(
            name=asset_name,
            category=ItemCategory.ASSET,
            book_value=book_value,
            tax_base=tax_base,
            description=description or "自行研发无形资产差异",
        )
