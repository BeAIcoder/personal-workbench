"""
A100000 中华人民共和国企业所得税年度纳税申报表（A类）生成服务
依据：国家税务总局2025年第1号公告格式

本模块实现：
1. A100000 年度申报主表数据填充与计算
2. A105000 纳税调整项目明细表生成
3. A107012 研发费用加计扣除优惠明细表生成
4. A107041 高新技术企业优惠情况及明细表生成
5. Excel 导出（符合国家税务总局申报表格式要求）

单位约定：本模块内部统一使用"元"为单位，与 A200000 预缴申报表一致。
输入参数可接受"万元"单位（通过 _wan_to_yuan 转换）。
"""
from __future__ import annotations

import io
import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

# ============================================================
# 单位转换工具
# ============================================================


def _wan_to_yuan(amount_wan: float) -> float:
    """万元转元"""
    return amount_wan * 10_000


def _yuan_to_wan(amount_yuan: float) -> float:
    """元转万元"""
    return amount_yuan / 10_000


def _format_amount(value: float) -> str:
    """格式化金额：保留两位小数，零值统一显示"""
    if value == 0.0:
        return "0.00"
    return f"{value:.2f}"


# ============================================================
# 数据模型定义
# ============================================================

@dataclass
class AnnualEnterpriseInfo:
    """企业基本信息（年度申报用）"""
    name: str  # 纳税人名称
    tax_number: str  # 纳税人识别号
    industry_type: str = ""  # 国民经济行业类型
    tax_authority: str = ""  # 主管税务机关
    address: str = ""  # 地址
    legal_person: str = ""  # 法定代表人
    accounting_method: str = "查账征收"  # 征收方式
    registered_capital: str = ""  # 注册资本（万元）
    total_assets: float = 0.0  # 资产总额（万元）
    employee_count: int = 0  # 从业人数
    is_small_micro: bool = False  # 是否小型微利企业
    is_high_tech: bool = False  # 是否高新技术企业
    high_tech_cert_no: str = ""  # 高新技术企业证书编号
    high_tech_cert_date: str = ""  # 高新技术企业证书日期


@dataclass
class TaxAdjustmentItem:
    """纳税调整项目明细"""
    name: str  # 调整项目名称
    book_amount: float = 0.0  # 账面金额（元）
    tax_amount: float = 0.0  # 税收金额（元）
    adjustment_increase: float = 0.0  # 调增金额（元）
    adjustment_decrease: float = 0.0  # 调减金额（元）
    category: str = ""  # 调整类别
    reason: str = ""  # 调整原因


@dataclass
class A105000FormData:
    """
    A105000 纳税调整项目明细表
    依据：国家税务总局2025年第1号公告

    行次结构（主要行次）：
    第1行  一、收入类调整项目
    第2行    （一）视同销售收入
    第3行    （二）未按权责发生制原则确认的收入
    第4行    （三）投资收益
    第5行    （四）按权益法核算长期股权投资对初始投资成本调整确认收益
    第6行    （五）交易性金融资产初始投资调整
    第7行    （六）非货币性资产交换
    第8行    （七）优先股、永续债等金融工具
    第9行    （八）其他
    第10行 二、扣除类调整项目
    第11行   （一）视同销售成本
    第12行   （二）职工薪酬
    第13行   （三）业务招待费支出
    第14行   （四）广告费和业务宣传费支出
    第15行   （五）捐赠支出
    第16行   （六）利息支出
    第17行   （七）罚金、罚款和被没收财物的损失
    第18行   （八）税收滞纳金、加收利息
    第19行   （九）赞助支出
    第20行   （十）与未实现融资收益相关在当期确认的财务费用
    第21行   （十一）佣金和手续费支出
    第22行   （十二）不征税收入用于支出所形成的费用
    第23行   （十三）跨期扣除项目
    第24行   （十四）与取得收入无关的支出
    第25行   （十五）境外所得分摊的共同支出
    第26行   （十六）其他
    第27行 三、资产类调整项目
    第28行   （一）资产折旧、摊销
    第29行   （二）资产减值准备金
    第30行   （三）资产损失
    第31行   （四）其他
    第32行 四、特殊事项调整项目
    第33行   （一）企业重组及递延纳税事项
    第34行   （二）政策性搬迁
    第35行   （三）特殊行业准备金
    第36行   （四）房地产开发企业特定业务计算的纳税调整额
    第37行   （五）其他
    第38行 五、特别纳税调整应税所得
    第39行 六、其他
    第40行 合计
    """
    enterprise: AnnualEnterpriseInfo
    tax_year: int

    # 收入类调整
    deemed_sales_revenue: float = 0.0  # 视同销售收入
    accrual_basis_revenue: float = 0.0  # 未按权责发生制确认收入
    investment_income: float = 0.0  # 投资收益调整
    equity_method_income: float = 0.0  # 权益法核算长期股权投资调整
    financial_asset_adjustment: float = 0.0  # 交易性金融资产初始投资调整
    non_monetary_exchange: float = 0.0  # 非货币性资产交换
    preferred_perpetual: float = 0.0  # 优先股永续债等
    other_income: float = 0.0  # 其他收入调整

    # 扣除类调整
    deemed_sales_cost: float = 0.0  # 视同销售成本
    employee_compensation: float = 0.0  # 职工薪酬调整
    entertainment_expense_book: float = 0.0  # 业务招待费账面金额
    entertainment_expense_tax: float = 0.0  # 业务招待费税收金额
    advertising_expense_book: float = 0.0  # 广告费账面金额
    advertising_expense_tax: float = 0.0  # 广告费税收金额
    advertising_carry_forward: float = 0.0  # 广告费本年结转以后年度扣除额
    donation_expense_book: float = 0.0  # 捐赠支出账面金额
    donation_expense_tax: float = 0.0  # 捐赠支出税收金额
    donation_carry_forward: float = 0.0  # 捐赠支出结转以后年度扣除额
    interest_expense: float = 0.0  # 利息支出调整
    penalty_loss: float = 0.0  # 罚金罚款和被没收财物损失
    tax_late_fee: float = 0.0  # 税收滞纳金加收利息
    sponsorship_expense: float = 0.0  # 赞助支出
    commission_fee: float = 0.0  # 佣金和手续费支出
    non_taxable_expense: float = 0.0  # 不征税收入用于支出所形成的费用
    cross_period_deduction: float = 0.0  # 跨期扣除项目
    unrelated_expense: float = 0.0  # 与取得收入无关的支出
    other_deduction: float = 0.0  # 其他扣除调整

    # 资产类调整
    asset_depreciation: float = 0.0  # 资产折旧摊销调整
    asset_impairment: float = 0.0  # 资产减值准备金
    asset_loss: float = 0.0  # 资产损失
    other_asset: float = 0.0  # 其他资产调整

    # 特殊事项调整
    enterprise_restructuring: float = 0.0  # 企业重组及递延纳税
    policy_relocation: float = 0.0  # 政策性搬迁
    special_reserve: float = 0.0  # 特殊行业准备金
    real_estate: float = 0.0  # 房地产开发企业特定业务
    other_special: float = 0.0  # 其他特殊事项

    # 特别纳税调整
    special_tax_adjustment: float = 0.0  # 特别纳税调整应税所得

    # 其他调整
    other_adjustments: float = 0.0  # 其他调整

    # 工资薪金总额（用于限额计算参考）
    total_salary: float = 0.0  # 工资薪金总额（元）
    operating_revenue: float = 0.0  # 营业收入（元，用于广告费限额计算）
    accounting_profit: float = 0.0  # 利润总额（元，用于捐赠限额计算）

    # 计算结果
    total_increase: float = 0.0  # 纳税调增合计
    total_decrease: float = 0.0  # 纳税调减合计

    def calculate(self) -> Dict[str, float]:
        """
        计算A105000纳税调整项目明细表所有行次

        Returns:
            所有计算结果字典
        """
        # 收入类调增/调减
        income_increase = (
            self.deemed_sales_revenue
            + self.accrual_basis_revenue
            + max(0, self.investment_income)
            + self.equity_method_income
            + self.financial_asset_adjustment
            + self.non_monetary_exchange
            + self.preferred_perpetual
            + self.other_income
        )
        income_decrease = max(0, -self.investment_income)

        # 扣除类调增/调减
        deduction_increase = (
            self.deemed_sales_cost
            + max(0, self.employee_compensation)
            + max(0, self.entertainment_expense_book - self.entertainment_expense_tax)
            + max(0, self.advertising_expense_book - self.advertising_expense_tax)
            + max(0, self.donation_expense_book - self.donation_expense_tax)
            + self.interest_expense
            + self.penalty_loss
            + self.tax_late_fee
            + self.sponsorship_expense
            + self.commission_fee
            + self.non_taxable_expense
            + self.cross_period_deduction
            + self.unrelated_expense
            + self.other_deduction
        )
        deduction_decrease = (
            max(0, -self.employee_compensation)
        )

        # 资产类调增/调减
        asset_increase = (
            self.asset_depreciation
            + self.asset_impairment
            + self.asset_loss
            + self.other_asset
        )
        asset_decrease = 0.0

        # 特殊事项调增/调减
        special_increase = (
            self.enterprise_restructuring
            + self.policy_relocation
            + self.special_reserve
            + self.real_estate
            + self.other_special
        )
        special_decrease = 0.0

        # 汇总
        self.total_increase = (
            income_increase
            + deduction_increase
            + asset_increase
            + special_increase
            + self.special_tax_adjustment
            + self.other_adjustments
        )
        self.total_decrease = (
            income_decrease
            + deduction_decrease
            + asset_decrease
            + special_decrease
        )

        return {
            "total_increase": self.total_increase,
            "total_decrease": self.total_decrease,
            "net_adjustment": self.total_increase - self.total_decrease,
        }

    def to_row_data(self) -> Dict[int, Dict[str, str]]:
        """
        转换为行次数据格式（用于Excel/PDF渲染）

        Returns:
            {行号: {"item": 项目名称, "book": 账面金额, "tax": 税收金额,
                     "increase": 调增金额, "decrease": 调减金额}}
        """
        self.calculate()

        def _fmt(v: float) -> str:
            return _format_amount(v)

        return {
            1: {"item": "一、收入类调整项目", "book": "", "tax": "", "increase": "", "decrease": ""},
            2: {"item": "  （一）视同销售收入", "book": _fmt(self.deemed_sales_revenue),
                "tax": "", "increase": _fmt(self.deemed_sales_revenue), "decrease": ""},
            3: {"item": "  （二）未按权责发生制原则确认的收入",
                "book": _fmt(self.accrual_basis_revenue), "tax": "",
                "increase": _fmt(self.accrual_basis_revenue), "decrease": ""},
            4: {"item": "  （三）投资收益", "book": _fmt(self.investment_income),
                "tax": "", "increase": _fmt(max(0, self.investment_income)),
                "decrease": _fmt(max(0, -self.investment_income))},
            5: {"item": "  （四）按权益法核算长期股权投资对初始投资成本调整确认收益",
                "book": _fmt(self.equity_method_income), "tax": "",
                "increase": _fmt(self.equity_method_income), "decrease": ""},
            6: {"item": "  （五）交易性金融资产初始投资调整",
                "book": _fmt(self.financial_asset_adjustment), "tax": "",
                "increase": _fmt(self.financial_asset_adjustment), "decrease": ""},
            7: {"item": "  （六）非货币性资产交换", "book": _fmt(self.non_monetary_exchange),
                "tax": "", "increase": _fmt(self.non_monetary_exchange), "decrease": ""},
            8: {"item": "  （七）优先股、永续债等金融工具", "book": _fmt(self.preferred_perpetual),
                "tax": "", "increase": _fmt(self.preferred_perpetual), "decrease": ""},
            9: {"item": "  （八）其他", "book": _fmt(self.other_income), "tax": "",
                "increase": _fmt(self.other_income), "decrease": ""},
            10: {"item": "二、扣除类调整项目", "book": "", "tax": "", "increase": "", "decrease": ""},
            11: {"item": "  （一）视同销售成本", "book": _fmt(self.deemed_sales_cost),
                 "tax": "", "increase": _fmt(self.deemed_sales_cost), "decrease": ""},
            12: {"item": "  （二）职工薪酬", "book": _fmt(self.employee_compensation),
                 "tax": "", "increase": _fmt(max(0, self.employee_compensation)),
                 "decrease": _fmt(max(0, -self.employee_compensation))},
            13: {"item": "  （三）业务招待费支出",
                 "book": _fmt(self.entertainment_expense_book),
                 "tax": _fmt(self.entertainment_expense_tax),
                 "increase": _fmt(max(0, self.entertainment_expense_book - self.entertainment_expense_tax)),
                 "decrease": ""},
            14: {"item": "  （四）广告费和业务宣传费支出",
                 "book": _fmt(self.advertising_expense_book),
                 "tax": _fmt(self.advertising_expense_tax),
                 "increase": _fmt(max(0, self.advertising_expense_book - self.advertising_expense_tax)),
                 "decrease": ""},
            1401: {"item": "    其中：本年结转以后年度扣除额",
                   "book": "", "tax": "", "increase": "",
                   "decrease": _fmt(self.advertising_carry_forward)},
            15: {"item": "  （五）捐赠支出",
                 "book": _fmt(self.donation_expense_book),
                 "tax": _fmt(self.donation_expense_tax),
                 "increase": _fmt(max(0, self.donation_expense_book - self.donation_expense_tax)),
                 "decrease": ""},
            1501: {"item": "    其中：本年结转以后年度扣除额",
                   "book": "", "tax": "", "increase": "",
                   "decrease": _fmt(self.donation_carry_forward)},
            16: {"item": "  （六）利息支出", "book": _fmt(self.interest_expense),
                 "tax": "", "increase": _fmt(self.interest_expense), "decrease": ""},
            17: {"item": "  （七）罚金、罚款和被没收财物的损失",
                 "book": _fmt(self.penalty_loss), "tax": "",
                 "increase": _fmt(self.penalty_loss), "decrease": ""},
            18: {"item": "  （八）税收滞纳金、加收利息",
                 "book": _fmt(self.tax_late_fee), "tax": "",
                 "increase": _fmt(self.tax_late_fee), "decrease": ""},
            19: {"item": "  （九）赞助支出", "book": _fmt(self.sponsorship_expense),
                 "tax": "", "increase": _fmt(self.sponsorship_expense), "decrease": ""},
            20: {"item": "  （十）与未实现融资收益相关在当期确认的财务费用",
                 "book": "", "tax": "", "increase": "", "decrease": ""},
            21: {"item": "  （十一）佣金和手续费支出", "book": _fmt(self.commission_fee),
                 "tax": "", "increase": _fmt(self.commission_fee), "decrease": ""},
            22: {"item": "  （十二）不征税收入用于支出所形成的费用",
                 "book": _fmt(self.non_taxable_expense), "tax": "",
                 "increase": _fmt(self.non_taxable_expense), "decrease": ""},
            23: {"item": "  （十三）跨期扣除项目", "book": _fmt(self.cross_period_deduction),
                 "tax": "", "increase": _fmt(self.cross_period_deduction), "decrease": ""},
            24: {"item": "  （十四）与取得收入无关的支出",
                 "book": _fmt(self.unrelated_expense), "tax": "",
                 "increase": _fmt(self.unrelated_expense), "decrease": ""},
            25: {"item": "  （十五）境外所得分摊的共同支出",
                 "book": "", "tax": "", "increase": "", "decrease": ""},
            26: {"item": "  （十六）其他", "book": _fmt(self.other_deduction),
                 "tax": "", "increase": _fmt(self.other_deduction), "decrease": ""},
            27: {"item": "三、资产类调整项目", "book": "", "tax": "", "increase": "", "decrease": ""},
            28: {"item": "  （一）资产折旧、摊销", "book": _fmt(self.asset_depreciation),
                 "tax": "", "increase": _fmt(self.asset_depreciation), "decrease": ""},
            29: {"item": "  （二）资产减值准备金", "book": _fmt(self.asset_impairment),
                 "tax": "", "increase": _fmt(self.asset_impairment), "decrease": ""},
            30: {"item": "  （三）资产损失", "book": _fmt(self.asset_loss),
                 "tax": "", "increase": _fmt(self.asset_loss), "decrease": ""},
            31: {"item": "  （四）其他", "book": _fmt(self.other_asset),
                 "tax": "", "increase": _fmt(self.other_asset), "decrease": ""},
            32: {"item": "四、特殊事项调整项目", "book": "", "tax": "", "increase": "", "decrease": ""},
            33: {"item": "  （一）企业重组及递延纳税事项",
                 "book": _fmt(self.enterprise_restructuring), "tax": "",
                 "increase": _fmt(self.enterprise_restructuring), "decrease": ""},
            34: {"item": "  （二）政策性搬迁", "book": _fmt(self.policy_relocation),
                 "tax": "", "increase": _fmt(self.policy_relocation), "decrease": ""},
            35: {"item": "  （三）特殊行业准备金", "book": _fmt(self.special_reserve),
                 "tax": "", "increase": _fmt(self.special_reserve), "decrease": ""},
            36: {"item": "  （四）房地产开发企业特定业务计算的纳税调整额",
                 "book": _fmt(self.real_estate), "tax": "",
                 "increase": _fmt(self.real_estate), "decrease": ""},
            37: {"item": "  （五）其他", "book": _fmt(self.other_special),
                 "tax": "", "increase": _fmt(self.other_special), "decrease": ""},
            38: {"item": "五、特别纳税调整应税所得", "book": "", "tax": "",
                 "increase": _fmt(self.special_tax_adjustment), "decrease": ""},
            39: {"item": "六、其他", "book": "", "tax": "",
                 "increase": _fmt(self.other_adjustments),
                 "decrease": _fmt(0)},
            40: {"item": "合计", "book": "", "tax": "",
                 "increase": _fmt(self.total_increase),
                 "decrease": _fmt(self.total_decrease)},
        }


@dataclass
class A107012FormData:
    """
    A107012 研发费用加计扣除优惠明细表
    依据：国家税务总局2025年第1号公告

    主要行次：
    第1行  一、自主研发、合作开发、集中研发项目
    第2行    （一）人员人工费用
    第3行    （二）直接投入费用
    第4行    （三）折旧费用
    第5行    （四）无形资产摊销
    第6行    （五）新产品设计费等
    第7行    （六）其他相关费用
    第8行    （七）可加计扣除的研发费用合计
    第9行    （八）允许扣除的研发费用
    第10行   （九）本年费用化金额
    第11行   （十）本年资本化金额
    第12行 二、委托研发项目
    第13行   （一）委托境内外部机构或个人进行研发活动
    第14行   （二）委托境外外部机构或个人进行研发活动
    第15行 三、作为无形资产研发的加计扣除
    第16行 四、当年形成用于销售研发的加计扣除
    第17行 五、以前年度形成用于销售研发的加计扣除
    第18行 六、加计扣除比例
    第19行 七、本年研发费用加计扣除总额
    """
    enterprise: AnnualEnterpriseInfo
    tax_year: int

    # 自主研发项目费用明细
    personnel_expense: float = 0.0  # 人员人工费用
    direct_input_expense: float = 0.0  # 直接投入费用
    depreciation_expense: float = 0.0  # 折旧费用
    intangible_amortization: float = 0.0  # 无形资产摊销
    new_product_design: float = 0.0  # 新产品设计费等
    other_related_expense: float = 0.0  # 其他相关费用

    # 委托研发项目
    domestic_entrust: float = 0.0  # 委托境内外部机构研发
    overseas_entrust: float = 0.0  # 委托境外外部机构研发

    # 无形资产研发
    intangible_rd: float = 0.0  # 作为无形资产研发的加计扣除

    # 销售研发
    sales_rd_current: float = 0.0  # 当年形成用于销售研发的加计扣除
    sales_rd_prior: float = 0.0  # 以前年度形成用于销售研发的加计扣除

    # 加计扣除比例（默认100%）
    super_deduction_rate: float = 1.0  # 1.0 = 100%, 1.2 = 120%

    # 企业类型（影响加计扣除比例）
    enterprise_type: str = "一般企业"

    # 计算结果
    total_rd_expense: float = 0.0  # 可加计扣除的研发费用合计
    allowed_rd_expense: float = 0.0  # 允许扣除的研发费用
    expense_amount: float = 0.0  # 本年费用化金额
    capitalized_amount: float = 0.0  # 本年资本化金额
    super_deduction_amount: float = 0.0  # 本年研发费用加计扣除总额

    def calculate(self) -> Dict[str, float]:
        """
        计算A107012研发费用加计扣除优惠明细表

        Returns:
            所有计算结果字典
        """
        # 自主研发费用合计（不含其他相关费用）
        rd_base = (
            self.personnel_expense
            + self.direct_input_expense
            + self.depreciation_expense
            + self.intangible_amortization
            + self.new_product_design
        )

        # 其他相关费用限额：不超过可加计扣除研发费用总额的10%
        other_limit = rd_base * 0.10 / 0.90  # 其他相关费用不超过总额的10%
        actual_other = min(self.other_related_expense, other_limit)

        # 可加计扣除的研发费用合计
        self.total_rd_expense = rd_base + actual_other

        # 允许扣除的研发费用（据实扣除部分）
        self.allowed_rd_expense = (
            self.personnel_expense
            + self.direct_input_expense
            + self.depreciation_expense
            + self.intangible_amortization
            + self.new_product_design
            + self.other_related_expense
        )

        # 费用化金额
        self.expense_amount = self.total_rd_expense

        # 资本化金额
        self.capitalized_amount = 0.0

        # 委托研发：按80%计入
        entrust_rd = self.domestic_entrust * 0.80

        # 加计扣除总额 = (自主研发费用合计 + 委托研发*80%) * 加计扣除比例
        self.super_deduction_amount = (
            self.total_rd_expense + entrust_rd
        ) * self.super_deduction_rate

        return {
            "total_rd_expense": self.total_rd_expense,
            "allowed_rd_expense": self.allowed_rd_expense,
            "expense_amount": self.expense_amount,
            "capitalized_amount": self.capitalized_amount,
            "super_deduction_amount": self.super_deduction_amount,
            "deduction_rate": self.super_deduction_rate,
        }

    def to_row_data(self) -> Dict[int, Dict[str, str]]:
        """
        转换为行次数据格式

        Returns:
            {行号: {"item": 项目名称, "amount": 金额}}
        """
        self.calculate()

        def _fmt(v: float) -> str:
            return _format_amount(v)

        return {
            1: {"item": "一、自主研发、合作开发、集中研发项目", "amount": ""},
            2: {"item": "  （一）人员人工费用", "amount": _fmt(self.personnel_expense)},
            3: {"item": "  （二）直接投入费用", "amount": _fmt(self.direct_input_expense)},
            4: {"item": "  （三）折旧费用", "amount": _fmt(self.depreciation_expense)},
            5: {"item": "  （四）无形资产摊销", "amount": _fmt(self.intangible_amortization)},
            6: {"item": "  （五）新产品设计费等", "amount": _fmt(self.new_product_design)},
            7: {"item": "  （六）其他相关费用", "amount": _fmt(self.other_related_expense)},
            8: {"item": "  （七）可加计扣除的研发费用合计",
                "amount": _fmt(self.total_rd_expense)},
            9: {"item": "  （八）允许扣除的研发费用",
                "amount": _fmt(self.allowed_rd_expense)},
            10: {"item": "  （九）本年费用化金额", "amount": _fmt(self.expense_amount)},
            11: {"item": "  （十）本年资本化金额", "amount": _fmt(self.capitalized_amount)},
            12: {"item": "二、委托研发项目", "amount": ""},
            13: {"item": "  （一）委托境内外部机构或个人进行研发活动",
                 "amount": _fmt(self.domestic_entrust)},
            14: {"item": "  （二）委托境外外部机构或个人进行研发活动",
                 "amount": _fmt(self.overseas_entrust)},
            15: {"item": "三、作为无形资产研发的加计扣除",
                 "amount": _fmt(self.intangible_rd)},
            16: {"item": "四、当年形成用于销售研发的加计扣除",
                 "amount": _fmt(self.sales_rd_current)},
            17: {"item": "五、以前年度形成用于销售研发的加计扣除",
                 "amount": _fmt(self.sales_rd_prior)},
            18: {"item": "六、加计扣除比例",
                 "amount": f"{self.super_deduction_rate * 100:.0f}%"},
            19: {"item": "七、本年研发费用加计扣除总额",
                 "amount": _fmt(self.super_deduction_amount)},
        }


@dataclass
class A107041FormData:
    """
    A107041 高新技术企业优惠情况及明细表
    依据：国家税务总局2025年第1号公告

    主要行次：
    第1行  一、高新技术企业认定信息
    第2行    （一）高新技术企业证书编号
    第3行    （二）高新技术企业证书取得时间
    第4行    （三）高新技术企业证书有效期
    第5行    （四）产品（服务）收入总额
    第6行    （五）产品（服务）收入占企业总收入比例
    第7行    （六）研发费用总额
    第8行    （七）研发费用占销售收入比例
    第9行    （八）科技人员占企业职工总数比例
    第10行 二、优惠信息
    第11行   （一）减免所得税额
    """
    enterprise: AnnualEnterpriseInfo
    tax_year: int

    # 认定信息
    cert_number: str = ""  # 高新技术企业证书编号
    cert_date: str = ""  # 证书取得时间
    cert_valid_until: str = ""  # 证书有效期
    product_service_income: float = 0.0  # 产品（服务）收入总额
    total_income: float = 0.0  # 企业总收入
    rd_expense_total: float = 0.0  # 研发费用总额
    sales_revenue: float = 0.0  # 销售收入
    tech_personnel_count: int = 0  # 科技人员数
    total_personnel_count: int = 0  # 企业职工总数

    # 优惠信息
    tax_rate: float = 0.15  # 优惠税率
    standard_rate: float = 0.25  # 法定税率
    taxable_income: float = 0.0  # 应纳税所得额
    standard_tax_amount: float = 0.0  # 按法定税率计算的应纳税额
    preferential_tax_amount: float = 0.0  # 按优惠税率计算的应纳税额
    tax_reduction: float = 0.0  # 减免所得税额

    # 计算结果
    product_income_ratio: float = 0.0  # 产品（服务）收入占比
    rd_expense_ratio: float = 0.0  # 研发费用占销售收入比例
    tech_personnel_ratio: float = 0.0  # 科技人员占比

    def calculate(self) -> Dict[str, float]:
        """
        计算A107041高新技术企业优惠情况及明细表

        Returns:
            所有计算结果字典
        """
        # 计算比例
        if self.total_income > 0:
            self.product_income_ratio = self.product_service_income / self.total_income
        if self.sales_revenue > 0:
            self.rd_expense_ratio = self.rd_expense_total / self.sales_revenue
        if self.total_personnel_count > 0:
            self.tech_personnel_ratio = self.tech_personnel_count / self.total_personnel_count

        # 税额计算
        self.standard_tax_amount = self.taxable_income * self.standard_rate
        self.preferential_tax_amount = self.taxable_income * self.tax_rate
        self.tax_reduction = self.standard_tax_amount - self.preferential_tax_amount

        return {
            "product_income_ratio": self.product_income_ratio,
            "rd_expense_ratio": self.rd_expense_ratio,
            "tech_personnel_ratio": self.tech_personnel_ratio,
            "standard_tax_amount": self.standard_tax_amount,
            "preferential_tax_amount": self.preferential_tax_amount,
            "tax_reduction": self.tax_reduction,
        }

    def to_row_data(self) -> Dict[int, Dict[str, str]]:
        """
        转换为行次数据格式

        Returns:
            {行号: {"item": 项目名称, "amount": 金额/信息}}
        """
        self.calculate()

        def _fmt(v: float) -> str:
            return _format_amount(v)

        return {
            1: {"item": "一、高新技术企业认定信息", "amount": ""},
            2: {"item": "  （一）高新技术企业证书编号", "amount": self.cert_number},
            3: {"item": "  （二）高新技术企业证书取得时间", "amount": self.cert_date},
            4: {"item": "  （三）高新技术企业证书有效期", "amount": self.cert_valid_until},
            5: {"item": "  （四）产品（服务）收入总额", "amount": _fmt(self.product_service_income)},
            6: {"item": "  （五）产品（服务）收入占企业总收入比例",
                "amount": f"{self.product_income_ratio * 100:.2f}%"},
            7: {"item": "  （六）研发费用总额", "amount": _fmt(self.rd_expense_total)},
            8: {"item": "  （七）研发费用占销售收入比例",
                "amount": f"{self.rd_expense_ratio * 100:.2f}%"},
            9: {"item": "  （八）科技人员占企业职工总数比例",
                "amount": f"{self.tech_personnel_ratio * 100:.2f}%"},
            10: {"item": "二、优惠信息", "amount": ""},
            11: {"item": "  （一）减免所得税额", "amount": _fmt(self.tax_reduction)},
        }


@dataclass
class A100000FormData:
    """
    A100000 中华人民共和国企业所得税年度纳税申报表（A类）
    依据：国家税务总局2025年第1号公告

    表单结构分为四大计算部分：

    一、利润总额计算（第1-13行）
    第1行  营业收入
    第2行    其中：主营业务收入
    第3行          其他业务收入
    第4行  减：营业成本
    第5行    其中：主营业务成本
    第6行          其他业务成本
    第7行  减：税金及附加
    第8行      销售费用
    第9行      管理费用
    第10行     财务费用
    第11行     研发费用
    第12行  加：其他收益
    第13行     投资收益（损失以"-"号表示）
    第14行     公允价值变动收益（损失以"-"号表示）
    第15行     信用减值损失（损失以"-"号表示）
    第16行     资产减值损失（损失以"-"号表示）
    第17行     资产处置收益（损失以"-"号表示）
    第18行  二、营业外收入
    第19行  减：营业外支出
    第20行  三、利润总额

    二、应纳税所得额计算（第21-35行）
    第21行  加：纳税调整增加额
    第22行  减：纳税调整减少额
    第23行  减：免税、减计收入及加计扣除
    第24行  加：境外应税所得抵减境内亏损
    第25行  四、纳税调整后所得
    第26行  减：所得减免
    第27行  减：弥补以前年度亏损
    第28行  减：抵扣应纳税所得额
    第29行  五、应纳税所得额

    三、应纳税额计算（第30-37行）
    第30行  税率（25%）
    第31行  六、应纳所得税额
    第32行  减：减免所得税额
    第33行    其中：高新技术企业减免
    第34行          小型微利企业减免
    第35行    减：抵免所得税额
    第36行  七、实际应纳所得税额

    四、实际应补（退）所得税额（第37-41行）
    第37行  减：本年累计实际已缴纳的所得税额
    第38行  八、本年应补（退）所得税额
    第39行  减：特定业务预缴（征）所得税额
    第40行  九、应补（退）所得税额
    第41行  加：以前年度多缴的所得税额在本年抵减
    """
    enterprise: AnnualEnterpriseInfo
    tax_year: int
    fill_date: str = ""

    # ========== 一、利润总额计算 ==========
    operating_revenue: float = 0.0  # 第1行 营业收入
    main_business_revenue: float = 0.0  # 第2行 主营业务收入
    other_business_revenue: float = 0.0  # 第3行 其他业务收入
    operating_cost: float = 0.0  # 第4行 营业成本
    main_business_cost: float = 0.0  # 第5行 主营业务成本
    other_business_cost: float = 0.0  # 第6行 其他业务成本
    taxes_and_surcharges: float = 0.0  # 第7行 税金及附加
    selling_expenses: float = 0.0  # 第8行 销售费用
    administrative_expenses: float = 0.0  # 第9行 管理费用
    financial_expenses: float = 0.0  # 第10行 财务费用
    rd_expenses: float = 0.0  # 第11行 研发费用
    other_income: float = 0.0  # 第12行 其他收益
    investment_income: float = 0.0  # 第13行 投资收益
    fair_value_change: float = 0.0  # 第14行 公允价值变动收益
    credit_impairment: float = 0.0  # 第15行 信用减值损失
    asset_impairment: float = 0.0  # 第16行 资产减值损失
    asset_disposal_income: float = 0.0  # 第17行 资产处置收益
    non_operating_income: float = 0.0  # 第18行 营业外收入
    non_operating_expenses: float = 0.0  # 第19行 营业外支出
    total_profit: float = 0.0  # 第20行 利润总额

    # ========== 二、应纳税所得额计算 ==========
    tax_adjustment_increase: float = 0.0  # 第21行 纳税调整增加额
    tax_adjustment_decrease: float = 0.0  # 第22行 纳税调整减少额
    tax_exempt_and_super_deduction: float = 0.0  # 第23行 免税减计收入及加计扣除
    foreign_income_domestic_loss: float = 0.0  # 第24行 境外应税所得抵减境内亏损
    tax_adjusted_income: float = 0.0  # 第25行 纳税调整后所得
    income_reduction: float = 0.0  # 第26行 所得减免
    prior_year_loss: float = 0.0  # 第27行 弥补以前年度亏损
    taxable_income_deduction: float = 0.0  # 第28行 抵扣应纳税所得额
    taxable_income: float = 0.0  # 第29行 应纳税所得额

    # ========== 三、应纳税额计算 ==========
    tax_rate: float = 0.25  # 第30行 税率
    tax_amount: float = 0.0  # 第31行 应纳所得税额
    tax_reduction: float = 0.0  # 第32行 减免所得税额
    high_tech_reduction: float = 0.0  # 第33行 高新技术企业减免
    small_micro_reduction: float = 0.0  # 第34行 小型微利企业减免
    tax_credit: float = 0.0  # 第35行 抵免所得税额
    actual_tax_amount: float = 0.0  # 第36行 实际应纳所得税额

    # ========== 四、实际应补（退）所得税额 ==========
    already_paid_tax: float = 0.0  # 第37行 本年累计实际已缴纳的所得税额
    annual_tax_to_pay: float = 0.0  # 第38行 本年应补（退）所得税额
    specific_prepaid_tax: float = 0.0  # 第39行 特定业务预缴所得税额
    final_tax_to_pay_or_refund: float = 0.0  # 第40行 应补（退）所得税额
    prior_year_overpaid: float = 0.0  # 第41行 以前年度多缴在本年抵减

    # 关联附表数据
    a105000_data: Optional[A105000FormData] = None
    a107012_data: Optional[A107012FormData] = None
    a107041_data: Optional[A107041FormData] = None

    def __post_init__(self):
        """初始化后自动设置填报日期"""
        if not self.fill_date:
            self.fill_date = datetime.now().strftime("%Y-%m-%d")

    def calculate(self) -> Dict[str, float]:
        """
        计算A100000年度申报主表所有行次

        计算公式（依据国家税务总局2025年第1号公告）：
        第20行 利润总额 = 营业收入 - 营业成本 - 税金及附加 - 期间费用 + 其他收益 + 投资收益等
        第25行 纳税调整后所得 = 利润总额 + 纳税调增 - 纳税调减 - 免税减计加计扣除 + 境外抵减
        第29行 应纳税所得额 = 纳税调整后所得 - 所得减免 - 弥补亏损 - 抵扣
        第31行 应纳所得税额 = 应纳税所得额 * 税率
        第36行 实际应纳所得税额 = 应纳所得税额 - 减免 - 抵免
        第38行 本年应补退税额 = 实际应纳 - 已缴
        第40行 应补退税额 = 本年应补退 - 特定预缴 + 以前年度多缴

        注意：如果 operating_revenue 和 operating_cost 同时为 0，
        则保留用户直接设置的 total_profit，不进行重算。

        Returns:
            所有计算结果字典
        """
        # --- 第20行 利润总额计算 ---
        # 仅当有营业收入数据时才从明细重算，否则保留直接设置的total_profit
        has_revenue_data = (
            self.operating_revenue != 0.0
            or self.operating_cost != 0.0
            or self.taxes_and_surcharges != 0.0
            or self.selling_expenses != 0.0
            or self.administrative_expenses != 0.0
            or self.financial_expenses != 0.0
            or self.rd_expenses != 0.0
        )
        if has_revenue_data:
            self.total_profit = (
                self.operating_revenue
                - self.operating_cost
                - self.taxes_and_surcharges
                - self.selling_expenses
                - self.administrative_expenses
                - self.financial_expenses
                - self.rd_expenses
                + self.other_income
                + self.investment_income
                + self.fair_value_change
                + self.credit_impairment
                + self.asset_impairment
                + self.asset_disposal_income
                + self.non_operating_income
                - self.non_operating_expenses
            )

        # --- 第25行 纳税调整后所得 ---
        self.tax_adjusted_income = (
            self.total_profit
            + self.tax_adjustment_increase
            - self.tax_adjustment_decrease
            - self.tax_exempt_and_super_deduction
            + self.foreign_income_domestic_loss
        )

        # --- 第29行 应纳税所得额 ---
        self.taxable_income = (
            self.tax_adjusted_income
            - self.income_reduction
            - self.prior_year_loss
            - self.taxable_income_deduction
        )
        self.taxable_income = max(0, self.taxable_income)

        # --- 税率确定 ---
        if self.enterprise.is_high_tech:
            self.tax_rate = 0.15
            # 高新技术企业减免 = 应纳税所得额 * (25% - 15%)
            self.high_tech_reduction = self.taxable_income * (0.25 - 0.15)
            self.small_micro_reduction = 0.0
        elif self.enterprise.is_small_micro:
            # 小型微利企业：25%计入，20%税率，实际税负5%
            self.tax_rate = 0.25
            self.small_micro_reduction = self.taxable_income * 0.20
            self.high_tech_reduction = 0.0
        else:
            self.tax_rate = 0.25
            self.high_tech_reduction = 0.0
            self.small_micro_reduction = 0.0

        # --- 第31行 应纳所得税额 ---
        self.tax_amount = self.taxable_income * self.tax_rate

        # --- 第32行 减免所得税额 ---
        self.tax_reduction = self.high_tech_reduction + self.small_micro_reduction

        # --- 第36行 实际应纳所得税额 ---
        self.actual_tax_amount = max(0, self.tax_amount - self.tax_reduction - self.tax_credit)

        # --- 第38行 本年应补（退）所得税额 ---
        self.annual_tax_to_pay = self.actual_tax_amount - self.already_paid_tax

        # --- 第40行 应补（退）所得税额 ---
        self.final_tax_to_pay_or_refund = (
            self.annual_tax_to_pay
            - self.specific_prepaid_tax
            + self.prior_year_overpaid
        )

        return {
            "row20_total_profit": self.total_profit,
            "row21_tax_adjustment_increase": self.tax_adjustment_increase,
            "row22_tax_adjustment_decrease": self.tax_adjustment_decrease,
            "row25_tax_adjusted_income": self.tax_adjusted_income,
            "row26_income_reduction": self.income_reduction,
            "row27_prior_year_loss": self.prior_year_loss,
            "row29_taxable_income": self.taxable_income,
            "row30_tax_rate": self.tax_rate,
            "row31_tax_amount": self.tax_amount,
            "row32_tax_reduction": self.tax_reduction,
            "row33_high_tech_reduction": self.high_tech_reduction,
            "row34_small_micro_reduction": self.small_micro_reduction,
            "row36_actual_tax_amount": self.actual_tax_amount,
            "row38_annual_tax_to_pay": self.annual_tax_to_pay,
            "row40_final_tax_to_pay_or_refund": self.final_tax_to_pay_or_refund,
        }

    def to_row_data(self) -> Dict[int, Dict[str, str]]:
        """
        转换为行次数据格式（用于Excel/PDF渲染）

        Returns:
            {行号: {"item": 项目名称, "amount": 金额字符串, "calc_method": 计算方式}}
        """
        self.calculate()

        def _fmt(v: float) -> str:
            return _format_amount(v)

        return {
            1: {"item": "一、营业收入", "amount": _fmt(self.operating_revenue), "calc_method": ""},
            2: {"item": "  其中：主营业务收入", "amount": _fmt(self.main_business_revenue), "calc_method": ""},
            3: {"item": "        其他业务收入", "amount": _fmt(self.other_business_revenue), "calc_method": ""},
            4: {"item": "减：营业成本", "amount": _fmt(self.operating_cost), "calc_method": ""},
            5: {"item": "  其中：主营业务成本", "amount": _fmt(self.main_business_cost), "calc_method": ""},
            6: {"item": "        其他业务成本", "amount": _fmt(self.other_business_cost), "calc_method": ""},
            7: {"item": "减：税金及附加", "amount": _fmt(self.taxes_and_surcharges), "calc_method": ""},
            8: {"item": "    销售费用", "amount": _fmt(self.selling_expenses), "calc_method": ""},
            9: {"item": "    管理费用", "amount": _fmt(self.administrative_expenses), "calc_method": ""},
            10: {"item": "    财务费用", "amount": _fmt(self.financial_expenses), "calc_method": ""},
            11: {"item": "    研发费用", "amount": _fmt(self.rd_expenses), "calc_method": ""},
            12: {"item": "加：其他收益", "amount": _fmt(self.other_income), "calc_method": ""},
            13: {"item": "    投资收益", "amount": _fmt(self.investment_income), "calc_method": ""},
            14: {"item": "    公允价值变动收益", "amount": _fmt(self.fair_value_change), "calc_method": ""},
            15: {"item": "    信用减值损失", "amount": _fmt(self.credit_impairment), "calc_method": ""},
            16: {"item": "    资产减值损失", "amount": _fmt(self.asset_impairment), "calc_method": ""},
            17: {"item": "    资产处置收益", "amount": _fmt(self.asset_disposal_income), "calc_method": ""},
            18: {"item": "二、营业外收入", "amount": _fmt(self.non_operating_income), "calc_method": ""},
            19: {"item": "减：营业外支出", "amount": _fmt(self.non_operating_expenses), "calc_method": ""},
            20: {"item": "三、利润总额", "amount": _fmt(self.total_profit),
                 "calc_method": "行1-行4-行7-行8-行9-行10-行11+行12+行13+行14+行15+行16+行17+行18-行19"},
            21: {"item": "加：纳税调整增加额", "amount": _fmt(self.tax_adjustment_increase),
                 "calc_method": "见A105000表"},
            22: {"item": "减：纳税调整减少额", "amount": _fmt(self.tax_adjustment_decrease),
                 "calc_method": "见A105000表"},
            23: {"item": "减：免税、减计收入及加计扣除",
                 "amount": _fmt(self.tax_exempt_and_super_deduction),
                 "calc_method": "见A107012等表"},
            24: {"item": "加：境外应税所得抵减境内亏损",
                 "amount": _fmt(self.foreign_income_domestic_loss), "calc_method": ""},
            25: {"item": "四、纳税调整后所得", "amount": _fmt(self.tax_adjusted_income),
                 "calc_method": "行20+行21-行22-行23+行24"},
            26: {"item": "减：所得减免", "amount": _fmt(self.income_reduction), "calc_method": ""},
            27: {"item": "减：弥补以前年度亏损", "amount": _fmt(self.prior_year_loss), "calc_method": ""},
            28: {"item": "减：抵扣应纳税所得额", "amount": _fmt(self.taxable_income_deduction),
                 "calc_method": ""},
            29: {"item": "五、应纳税所得额", "amount": _fmt(self.taxable_income),
                 "calc_method": "行25-行26-行27-行28"},
            30: {"item": "税率", "amount": f"{self.tax_rate * 100:.0f}%",
                 "calc_method": "法定税率25%"},
            31: {"item": "六、应纳所得税额", "amount": _fmt(self.tax_amount),
                 "calc_method": "行29*行30"},
            32: {"item": "减：减免所得税额", "amount": _fmt(self.tax_reduction),
                 "calc_method": ""},
            33: {"item": "  其中：高新技术企业减免",
                 "amount": _fmt(self.high_tech_reduction), "calc_method": "见A107041表"},
            34: {"item": "        小型微利企业减免",
                 "amount": _fmt(self.small_micro_reduction), "calc_method": ""},
            35: {"item": "减：抵免所得税额", "amount": _fmt(self.tax_credit), "calc_method": ""},
            36: {"item": "七、实际应纳所得税额", "amount": _fmt(self.actual_tax_amount),
                 "calc_method": "行31-行32-行35"},
            37: {"item": "减：本年累计实际已缴纳的所得税额",
                 "amount": _fmt(self.already_paid_tax), "calc_method": ""},
            38: {"item": "八、本年应补（退）所得税额", "amount": _fmt(self.annual_tax_to_pay),
                 "calc_method": "行36-行37"},
            39: {"item": "减：特定业务预缴（征）所得税额",
                 "amount": _fmt(self.specific_prepaid_tax), "calc_method": ""},
            40: {"item": "九、应补（退）所得税额",
                 "amount": _fmt(self.final_tax_to_pay_or_refund),
                 "calc_method": "行38-行39+行41"},
            41: {"item": "加：以前年度多缴的所得税额在本年抵减",
                 "amount": _fmt(self.prior_year_overpaid), "calc_method": ""},
        }


# ============================================================
# 便捷生成函数
# ============================================================

def generate_a100000_annual_return(
    enterprise: AnnualEnterpriseInfo,
    tax_year: int,
    operating_revenue: float = 0.0,
    operating_cost: float = 0.0,
    total_profit: float = 0.0,
    taxes_and_surcharges: float = 0.0,
    selling_expenses: float = 0.0,
    administrative_expenses: float = 0.0,
    financial_expenses: float = 0.0,
    rd_expenses: float = 0.0,
    other_income: float = 0.0,
    investment_income: float = 0.0,
    fair_value_change: float = 0.0,
    credit_impairment: float = 0.0,
    asset_impairment: float = 0.0,
    asset_disposal_income: float = 0.0,
    non_operating_income: float = 0.0,
    non_operating_expenses: float = 0.0,
    tax_adjustment_increase: float = 0.0,
    tax_adjustment_decrease: float = 0.0,
    tax_exempt_and_super_deduction: float = 0.0,
    income_reduction: float = 0.0,
    prior_year_loss: float = 0.0,
    already_paid_tax: float = 0.0,
    specific_prepaid_tax: float = 0.0,
    prior_year_overpaid: float = 0.0,
    tax_credit: float = 0.0,
) -> A100000FormData:
    """
    便捷函数：创建A100000年度申报表数据并自动计算

    Args:
        enterprise: 企业基本信息
        tax_year: 纳税年度
        operating_revenue: 营业收入（元）
        operating_cost: 营业成本（元）
        total_profit: 利润总额（元，如提供则覆盖计算值）
        taxes_and_surcharges: 税金及附加（元）
        selling_expenses: 销售费用（元）
        administrative_expenses: 管理费用（元）
        financial_expenses: 财务费用（元）
        rd_expenses: 研发费用（元）
        other_income: 其他收益（元）
        investment_income: 投资收益（元）
        fair_value_change: 公允价值变动收益（元）
        credit_impairment: 信用减值损失（元）
        asset_impairment: 资产减值损失（元）
        asset_disposal_income: 资产处置收益（元）
        non_operating_income: 营业外收入（元）
        non_operating_expenses: 营业外支出（元）
        tax_adjustment_increase: 纳税调整增加额（元）
        tax_adjustment_decrease: 纳税调整减少额（元）
        tax_exempt_and_super_deduction: 免税减计收入及加计扣除（元）
        income_reduction: 所得减免（元）
        prior_year_loss: 弥补以前年度亏损（元）
        already_paid_tax: 本年累计已缴所得税（元）
        specific_prepaid_tax: 特定业务预缴所得税（元）
        prior_year_overpaid: 以前年度多缴在本年抵减（元）
        tax_credit: 抵免所得税额（元）

    Returns:
        已计算完成的A100000FormData对象
    """
    form_data = A100000FormData(
        enterprise=enterprise,
        tax_year=tax_year,
        operating_revenue=operating_revenue,
        operating_cost=operating_cost,
        taxes_and_surcharges=taxes_and_surcharges,
        selling_expenses=selling_expenses,
        administrative_expenses=administrative_expenses,
        financial_expenses=financial_expenses,
        rd_expenses=rd_expenses,
        other_income=other_income,
        investment_income=investment_income,
        fair_value_change=fair_value_change,
        credit_impairment=credit_impairment,
        asset_impairment=asset_impairment,
        asset_disposal_income=asset_disposal_income,
        non_operating_income=non_operating_income,
        non_operating_expenses=non_operating_expenses,
        tax_adjustment_increase=tax_adjustment_increase,
        tax_adjustment_decrease=tax_adjustment_decrease,
        tax_exempt_and_super_deduction=tax_exempt_and_super_deduction,
        income_reduction=income_reduction,
        prior_year_loss=prior_year_loss,
        already_paid_tax=already_paid_tax,
        specific_prepaid_tax=specific_prepaid_tax,
        prior_year_overpaid=prior_year_overpaid,
        tax_credit=tax_credit,
    )

    # 如果直接提供了total_profit，覆盖计算值
    if total_profit != 0.0:
        form_data.total_profit = total_profit

    form_data.calculate()
    return form_data


def generate_a105000_form(
    enterprise: AnnualEnterpriseInfo,
    tax_year: int,
    **kwargs: float,
) -> A105000FormData:
    """
    便捷函数：创建A105000纳税调整项目明细表

    Args:
        enterprise: 企业基本信息
        tax_year: 纳税年度
        **kwargs: 各调整项目金额（元），参考 A105000FormData 字段

    Returns:
        A105000FormData对象
    """
    form_data = A105000FormData(enterprise=enterprise, tax_year=tax_year, **kwargs)
    form_data.calculate()
    return form_data


def generate_a107012_form(
    enterprise: AnnualEnterpriseInfo,
    tax_year: int,
    **kwargs: float,
) -> A107012FormData:
    """
    便捷函数：创建A107012研发费用加计扣除优惠明细表

    Args:
        enterprise: 企业基本信息
        tax_year: 纳税年度
        **kwargs: 各研发费用项目金额（元），参考 A107012FormData 字段

    Returns:
        A107012FormData对象
    """
    form_data = A107012FormData(enterprise=enterprise, tax_year=tax_year, **kwargs)
    form_data.calculate()
    return form_data


def generate_a107041_form(
    enterprise: AnnualEnterpriseInfo,
    tax_year: int,
    **kwargs: float,
) -> A107041FormData:
    """
    便捷函数：创建A107041高新技术企业优惠情况及明细表

    Args:
        enterprise: 企业基本信息
        tax_year: 纳税年度
        **kwargs: 各项信息，参考 A107041FormData 字段

    Returns:
        A107041FormData对象
    """
    form_data = A107041FormData(enterprise=enterprise, tax_year=tax_year, **kwargs)
    form_data.calculate()
    return form_data


# ============================================================
# 纳税调整自动计算辅助函数
# ============================================================

def auto_calculate_adjustments(
    operating_revenue: float,
    accounting_profit: float,
    total_salary: float,
    business_entertainment: float,
    advertising_expense: float,
    donation_expense: float,
    rnd_expense: float,
    penalties: float = 0.0,
    welfare_expense: float = 0.0,
    education_expense: float = 0.0,
    union_expense: float = 0.0,
    equipment_purchase: float = 0.0,
    enterprise_type: str = "一般企业",
) -> Tuple[float, float]:
    """
    自动计算纳税调整增加额和减少额

    Args:
        operating_revenue: 营业收入（元）
        accounting_profit: 会计利润总额（元）
        total_salary: 工资薪金总额（元）
        business_entertainment: 业务招待费（元）
        advertising_expense: 广告费（元）
        donation_expense: 捐赠支出（元）
        rnd_expense: 研发费用（元）
        penalties: 罚款滞纳金（元）
        welfare_expense: 职工福利费（元）
        education_expense: 职工教育经费（元）
        union_expense: 工会经费（元）
        equipment_purchase: 设备购置（元）
        enterprise_type: 企业类型

    Returns:
        (纳税调整增加额, 纳税调整减少额) 单位：元
    """
    # 导入计算引擎
    from ..services.corporate_income_tax_calculator import (
        CorporateIncomeTaxCalculator,
        TaxAdjustmentInput,
        EnterpriseInfo as CalcEnterpriseInfo,
        wan_to_yuan,
        yuan_to_wan,
    )
    from ..config.tax_rules import IndustryType

    # 将元转换为万元用于计算引擎
    adj_input = TaxAdjustmentInput(
        business_entertainment=yuan_to_wan(business_entertainment),
        advertising_expense=yuan_to_wan(advertising_expense),
        donation_expense=yuan_to_wan(donation_expense),
        rnd_expense=yuan_to_wan(rnd_expense),
        welfare_expense=yuan_to_wan(welfare_expense),
        education_expense=yuan_to_wan(education_expense),
        union_expense=yuan_to_wan(union_expense),
        penalties=yuan_to_wan(penalties),
        equipment_purchase=yuan_to_wan(equipment_purchase),
    )

    calc_result = CorporateIncomeTaxCalculator.calculate_all_adjustments(
        adjustments=adj_input,
        revenue_wan=yuan_to_wan(operating_revenue),
        accounting_profit_wan=yuan_to_wan(accounting_profit),
        total_salary_wan=yuan_to_wan(total_salary),
        enterprise_type=enterprise_type,
        advertising_industry=IndustryType.GENERAL,
    )

    total_increase = wan_to_yuan(calc_result["total_adjustment_increase_wan"])
    total_decrease = wan_to_yuan(calc_result["total_adjustment_decrease_wan"])

    return total_increase, total_decrease


# ============================================================
# Excel 导出模块
# ============================================================

def export_a100000_excel(
    form_data: A100000FormData,
    output_path: Optional[str] = None,
) -> bytes:
    """
    导出A100000年度申报主表为Excel文件

    格式符合国家税务总局2025年第1号公告要求：
    - A4纸张大小
    - 表头：表单名称、纳税人识别号、税款所属期间等
    - 表体：行次、项目、金额、计算方式
    - 表尾：纳税人声明、签章栏

    Args:
        form_data: A100000申报表数据
        output_path: 输出文件路径（可选）

    Returns:
        Excel文件的字节数据
    """
    try:
        import openpyxl
        from openpyxl.styles import Font, Alignment, Border, Side, PatternFill
    except ImportError:
        raise ImportError("openpyxl未安装，请运行: pip install openpyxl")

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "A100000年度申报主表"

    # 设置打印页面为A4
    ws.page_setup.paperSize = ws.PAPERSIZE_A4
    ws.page_setup.orientation = "portrait"
    ws.page_margins.left = 0.7
    ws.page_margins.right = 0.7
    ws.page_margins.top = 0.75
    ws.page_margins.bottom = 0.75

    # 样式定义
    title_font = Font(name="宋体", size=16, bold=True)
    subtitle_font = Font(name="宋体", size=10, bold=True)
    header_font = Font(name="宋体", size=10, bold=True)
    normal_font = Font(name="宋体", size=10)
    small_font = Font(name="宋体", size=9)
    amount_font = Font(name="Arial", size=10)
    bold_font = Font(name="宋体", size=10, bold=True)

    thin_border = Border(
        left=Side(style="thin"), right=Side(style="thin"),
        top=Side(style="thin"), bottom=Side(style="thin"),
    )
    header_fill = PatternFill(start_color="D9E1F2", end_color="D9E1F2", fill_type="solid")
    title_fill = PatternFill(start_color="F2F2F2", end_color="F2F2F2", fill_type="solid")
    center_align = Alignment(horizontal="center", vertical="center", wrap_text=True)
    left_align = Alignment(horizontal="left", vertical="center", wrap_text=True)
    right_align = Alignment(horizontal="right", vertical="center")

    # 列宽设置
    ws.column_dimensions["A"].width = 8
    ws.column_dimensions["B"].width = 55
    ws.column_dimensions["C"].width = 22
    ws.column_dimensions["D"].width = 35

    # 第1行：表单标题
    ws.merge_cells("A1:D1")
    cell = ws["A1"]
    cell.value = f"中华人民共和国企业所得税年度纳税申报表（A类） {form_data.tax_year}年度"
    cell.font = title_font
    cell.alignment = center_align
    cell.fill = title_fill
    for col in ["A", "B", "C", "D"]:
        ws[f"{col}1"].border = thin_border
    ws.row_dimensions[1].height = 35

    # 第2行：表单编号
    ws.merge_cells("A2:D2")
    cell = ws["A2"]
    cell.value = "A100000"
    cell.font = subtitle_font
    cell.alignment = Alignment(horizontal="right", vertical="center")
    cell.border = thin_border
    ws.row_dimensions[2].height = 20

    # 第3-6行：企业基本信息
    info_rows = [
        ("税款所属期间", f"{form_data.tax_year}年1月1日 至 {form_data.tax_year}年12月31日"),
        ("纳税人识别号（统一社会信用代码）", form_data.enterprise.tax_number),
        ("纳税人名称", form_data.enterprise.name),
        ("金额单位：元（列至角分）", ""),
    ]
    start_row = 3
    for i, (label, value) in enumerate(info_rows):
        row = start_row + i
        ws.cell(row=row, column=1, value=label).font = small_font
        ws.cell(row=row, column=1).alignment = center_align
        ws.cell(row=row, column=1).border = thin_border
        ws.merge_cells(start_row=row, start_column=2, end_row=row, end_column=3)
        ws.cell(row=row, column=2, value=value).font = normal_font
        ws.cell(row=row, column=2).alignment = center_align
        ws.cell(row=row, column=2).border = thin_border
        ws.cell(row=row, column=3).border = thin_border
        ws.cell(row=row, column=4).border = thin_border
        ws.row_dimensions[row].height = 22

    # 表头行
    header_row = start_row + len(info_rows)
    headers = ["行次", "项目", "金额", "计算方式"]
    for col_idx, header in enumerate(headers, 1):
        cell = ws.cell(row=header_row, column=col_idx, value=header)
        cell.font = header_font
        cell.alignment = center_align
        cell.fill = header_fill
        cell.border = thin_border
    ws.row_dimensions[header_row].height = 25

    # 数据行
    row_data = form_data.to_row_data()
    important_rows = {1, 4, 20, 25, 29, 31, 36, 38, 40}
    section_rows = {1, 10, 18, 20, 21, 25, 29, 30, 31, 32, 36, 37, 38, 40}
    data_start_row = header_row + 1
    current_row = data_start_row

    for row_num in sorted(row_data.keys()):
        data = row_data[row_num]
        is_important = row_num in important_rows

        ws.cell(row=current_row, column=1, value=str(row_num) if row_num <= 100 else "").font = small_font
        ws.cell(row=current_row, column=1).alignment = center_align
        ws.cell(row=current_row, column=1).border = thin_border

        ws.cell(row=current_row, column=2, value=data["item"]).font = normal_font
        ws.cell(row=current_row, column=2).alignment = left_align
        ws.cell(row=current_row, column=2).border = thin_border

        ws.cell(row=current_row, column=3, value=data["amount"]).font = amount_font
        ws.cell(row=current_row, column=3).alignment = right_align
        ws.cell(row=current_row, column=3).border = thin_border

        ws.cell(row=current_row, column=4, value=data["calc_method"]).font = small_font
        ws.cell(row=current_row, column=4).alignment = left_align
        ws.cell(row=current_row, column=4).border = thin_border

        if is_important:
            for col in range(1, 5):
                ws.cell(row=current_row, column=col).font = bold_font

        if row_num == 20:
            for col in range(1, 5):
                ws.cell(row=current_row, column=col).border = Border(
                    left=Side(style="thin"), right=Side(style="thin"),
                    top=Side(style="thin"), bottom=Side(style="medium"),
                )
        if row_num == 40:
            for col in range(1, 5):
                ws.cell(row=current_row, column=col).border = Border(
                    left=Side(style="thin"), right=Side(style="thin"),
                    top=Side(style="thin"), bottom=Side(style="double"),
                )

        ws.row_dimensions[current_row].height = 20
        current_row += 1

    # 表尾：纳税人声明
    statement_row = current_row + 2
    ws.merge_cells(start_row=statement_row + 1, start_column=1, end_row=statement_row + 1, end_column=4)
    ws.cell(row=statement_row + 1, column=1,
            value="谨声明：本纳税申报表是根据国家税收法律法规及相关规定填报的，是真实的、可靠的、完整的。").font = small_font

    sign_row = statement_row + 3
    ws.merge_cells(start_row=sign_row, start_column=1, end_row=sign_row, end_column=2)
    ws.cell(row=sign_row, column=1, value="纳税人公章：").font = small_font
    ws.cell(row=sign_row, column=1).border = Border(bottom=Side(style="thin"))
    ws.merge_cells(start_row=sign_row, start_column=3, end_row=sign_row, end_column=4)
    ws.cell(row=sign_row, column=3, value="填表日期：      年    月    日").font = small_font
    ws.cell(row=sign_row, column=3).border = Border(bottom=Side(style="thin"))

    if output_path:
        os.makedirs(os.path.dirname(output_path) if os.path.dirname(output_path) else ".", exist_ok=True)
        wb.save(output_path)

    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    return buffer.read()


def export_a105000_excel(
    form_data: A105000FormData,
    output_path: Optional[str] = None,
) -> bytes:
    """
    导出A105000纳税调整项目明细表为Excel文件

    Args:
        form_data: A105000申报表数据
        output_path: 输出文件路径（可选）

    Returns:
        Excel文件的字节数据
    """
    try:
        import openpyxl
        from openpyxl.styles import Font, Alignment, Border, Side, PatternFill
    except ImportError:
        raise ImportError("openpyxl未安装，请运行: pip install openpyxl")

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "A105000纳税调整项目明细表"

    ws.page_setup.paperSize = ws.PAPERSIZE_A4
    ws.page_setup.orientation = "landscape"

    # 样式定义
    title_font = Font(name="宋体", size=16, bold=True)
    header_font = Font(name="宋体", size=10, bold=True)
    normal_font = Font(name="宋体", size=10)
    small_font = Font(name="宋体", size=9)
    amount_font = Font(name="Arial", size=10)
    bold_font = Font(name="宋体", size=10, bold=True)
    thin_border = Border(
        left=Side(style="thin"), right=Side(style="thin"),
        top=Side(style="thin"), bottom=Side(style="thin"),
    )
    header_fill = PatternFill(start_color="D9E1F2", end_color="D9E1F2", fill_type="solid")
    title_fill = PatternFill(start_color="F2F2F2", end_color="F2F2F2", fill_type="solid")
    center_align = Alignment(horizontal="center", vertical="center", wrap_text=True)
    left_align = Alignment(horizontal="left", vertical="center", wrap_text=True)
    right_align = Alignment(horizontal="right", vertical="center")

    ws.column_dimensions["A"].width = 8
    ws.column_dimensions["B"].width = 50
    ws.column_dimensions["C"].width = 20
    ws.column_dimensions["D"].width = 20
    ws.column_dimensions["E"].width = 20
    ws.column_dimensions["F"].width = 20

    # 标题
    ws.merge_cells("A1:F1")
    cell = ws["A1"]
    cell.value = f"纳税调整项目明细表 {form_data.tax_year}年度"
    cell.font = title_font
    cell.alignment = center_align
    cell.fill = title_fill
    for col in ["A", "B", "C", "D", "E", "F"]:
        ws[f"{col}1"].border = thin_border
    ws.row_dimensions[1].height = 35

    # 表单编号
    ws.merge_cells("A2:F2")
    cell = ws["A2"]
    cell.value = "A105000"
    cell.font = Font(name="宋体", size=10, bold=True)
    cell.alignment = Alignment(horizontal="right", vertical="center")
    cell.border = thin_border
    ws.row_dimensions[2].height = 20

    # 企业基本信息
    info_rows = [
        ("纳税人识别号", form_data.enterprise.tax_number),
        ("纳税人名称", form_data.enterprise.name),
        ("金额单位：元（列至角分）", ""),
    ]
    start_row = 3
    for i, (label, value) in enumerate(info_rows):
        row = start_row + i
        ws.cell(row=row, column=1, value=label).font = small_font
        ws.cell(row=row, column=1).alignment = center_align
        ws.cell(row=row, column=1).border = thin_border
        ws.merge_cells(start_row=row, start_column=2, end_row=row, end_column=3)
        ws.cell(row=row, column=2, value=value).font = normal_font
        ws.cell(row=row, column=2).alignment = center_align
        ws.cell(row=row, column=2).border = thin_border
        ws.cell(row=row, column=3).border = thin_border
        for c in [4, 5, 6]:
            ws.cell(row=row, column=c).border = thin_border
        ws.row_dimensions[row].height = 22

    # 表头
    header_row = start_row + len(info_rows)
    headers = ["行次", "项目", "账面金额", "税收金额", "调增金额", "调减金额"]
    for col_idx, header in enumerate(headers, 1):
        cell = ws.cell(row=header_row, column=col_idx, value=header)
        cell.font = header_font
        cell.alignment = center_align
        cell.fill = header_fill
        cell.border = thin_border
    ws.row_dimensions[header_row].height = 25

    # 数据行
    row_data = form_data.to_row_data()
    section_rows = {1, 10, 27, 32, 38, 39, 40}
    current_row = header_row + 1

    for row_num in sorted(row_data.keys()):
        data = row_data[row_num]
        is_section = row_num in section_rows

        ws.cell(row=current_row, column=1, value=str(row_num) if row_num <= 100 else "").font = small_font
        ws.cell(row=current_row, column=1).alignment = center_align
        ws.cell(row=current_row, column=1).border = thin_border

        ws.cell(row=current_row, column=2, value=data["item"]).font = normal_font
        ws.cell(row=current_row, column=2).alignment = left_align
        ws.cell(row=current_row, column=2).border = thin_border

        for col_idx, key in enumerate(["book", "tax", "increase", "decrease"], 3):
            ws.cell(row=current_row, column=col_idx, value=data[key]).font = amount_font
            ws.cell(row=current_row, column=col_idx).alignment = right_align
            ws.cell(row=current_row, column=col_idx).border = thin_border

        if is_section or row_num == 40:
            for col in range(1, 7):
                ws.cell(row=current_row, column=col).font = bold_font

        if row_num == 40:
            for col in range(1, 7):
                ws.cell(row=current_row, column=col).border = Border(
                    left=Side(style="thin"), right=Side(style="thin"),
                    top=Side(style="thin"), bottom=Side(style="double"),
                )

        ws.row_dimensions[current_row].height = 20
        current_row += 1

    if output_path:
        os.makedirs(os.path.dirname(output_path) if os.path.dirname(output_path) else ".", exist_ok=True)
        wb.save(output_path)

    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    return buffer.read()


def export_a107012_excel(
    form_data: A107012FormData,
    output_path: Optional[str] = None,
) -> bytes:
    """
    导出A107012研发费用加计扣除优惠明细表为Excel文件

    Args:
        form_data: A107012申报表数据
        output_path: 输出文件路径（可选）

    Returns:
        Excel文件的字节数据
    """
    try:
        import openpyxl
        from openpyxl.styles import Font, Alignment, Border, Side, PatternFill
    except ImportError:
        raise ImportError("openpyxl未安装，请运行: pip install openpyxl")

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "A107012研发费用加计扣除"

    ws.page_setup.paperSize = ws.PAPERSIZE_A4
    ws.page_setup.orientation = "portrait"

    title_font = Font(name="宋体", size=16, bold=True)
    header_font = Font(name="宋体", size=10, bold=True)
    normal_font = Font(name="宋体", size=10)
    small_font = Font(name="宋体", size=9)
    amount_font = Font(name="Arial", size=10)
    bold_font = Font(name="宋体", size=10, bold=True)
    thin_border = Border(
        left=Side(style="thin"), right=Side(style="thin"),
        top=Side(style="thin"), bottom=Side(style="thin"),
    )
    header_fill = PatternFill(start_color="D9E1F2", end_color="D9E1F2", fill_type="solid")
    title_fill = PatternFill(start_color="F2F2F2", end_color="F2F2F2", fill_type="solid")
    center_align = Alignment(horizontal="center", vertical="center", wrap_text=True)
    left_align = Alignment(horizontal="left", vertical="center", wrap_text=True)
    right_align = Alignment(horizontal="right", vertical="center")

    ws.column_dimensions["A"].width = 8
    ws.column_dimensions["B"].width = 55
    ws.column_dimensions["C"].width = 22

    # 标题
    ws.merge_cells("A1:C1")
    cell = ws["A1"]
    cell.value = f"研发费用加计扣除优惠明细表 {form_data.tax_year}年度"
    cell.font = title_font
    cell.alignment = center_align
    cell.fill = title_fill
    for col in ["A", "B", "C"]:
        ws[f"{col}1"].border = thin_border
    ws.row_dimensions[1].height = 35

    ws.merge_cells("A2:C2")
    cell = ws["A2"]
    cell.value = "A107012"
    cell.font = Font(name="宋体", size=10, bold=True)
    cell.alignment = Alignment(horizontal="right", vertical="center")
    cell.border = thin_border
    ws.row_dimensions[2].height = 20

    info_rows = [
        ("纳税人识别号", form_data.enterprise.tax_number),
        ("纳税人名称", form_data.enterprise.name),
        ("金额单位：元（列至角分）", ""),
    ]
    start_row = 3
    for i, (label, value) in enumerate(info_rows):
        row = start_row + i
        ws.cell(row=row, column=1, value=label).font = small_font
        ws.cell(row=row, column=1).alignment = center_align
        ws.cell(row=row, column=1).border = thin_border
        ws.merge_cells(start_row=row, start_column=2, end_row=row, end_column=2)
        ws.cell(row=row, column=2, value=value).font = normal_font
        ws.cell(row=row, column=2).alignment = center_align
        ws.cell(row=row, column=2).border = thin_border
        ws.cell(row=row, column=3).border = thin_border
        ws.row_dimensions[row].height = 22

    header_row = start_row + len(info_rows)
    headers = ["行次", "项目", "金额"]
    for col_idx, header in enumerate(headers, 1):
        cell = ws.cell(row=header_row, column=col_idx, value=header)
        cell.font = header_font
        cell.alignment = center_align
        cell.fill = header_fill
        cell.border = thin_border
    ws.row_dimensions[header_row].height = 25

    row_data = form_data.to_row_data()
    section_rows = {1, 12, 15, 16, 17, 18, 19}
    current_row = header_row + 1

    for row_num in sorted(row_data.keys()):
        data = row_data[row_num]
        is_section = row_num in section_rows

        ws.cell(row=current_row, column=1, value=str(row_num)).font = small_font
        ws.cell(row=current_row, column=1).alignment = center_align
        ws.cell(row=current_row, column=1).border = thin_border

        ws.cell(row=current_row, column=2, value=data["item"]).font = normal_font
        ws.cell(row=current_row, column=2).alignment = left_align
        ws.cell(row=current_row, column=2).border = thin_border

        ws.cell(row=current_row, column=3, value=data["amount"]).font = amount_font
        ws.cell(row=current_row, column=3).alignment = right_align
        ws.cell(row=current_row, column=3).border = thin_border

        if is_section:
            for col in range(1, 4):
                ws.cell(row=current_row, column=col).font = bold_font

        ws.row_dimensions[current_row].height = 20
        current_row += 1

    if output_path:
        os.makedirs(os.path.dirname(output_path) if os.path.dirname(output_path) else ".", exist_ok=True)
        wb.save(output_path)

    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    return buffer.read()


def export_a107041_excel(
    form_data: A107041FormData,
    output_path: Optional[str] = None,
) -> bytes:
    """
    导出A107041高新技术企业优惠情况及明细表为Excel文件

    Args:
        form_data: A107041申报表数据
        output_path: 输出文件路径（可选）

    Returns:
        Excel文件的字节数据
    """
    try:
        import openpyxl
        from openpyxl.styles import Font, Alignment, Border, Side, PatternFill
    except ImportError:
        raise ImportError("openpyxl未安装，请运行: pip install openpyxl")

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "A107041高新技术企业优惠"

    ws.page_setup.paperSize = ws.PAPERSIZE_A4
    ws.page_setup.orientation = "portrait"

    title_font = Font(name="宋体", size=16, bold=True)
    header_font = Font(name="宋体", size=10, bold=True)
    normal_font = Font(name="宋体", size=10)
    small_font = Font(name="宋体", size=9)
    amount_font = Font(name="Arial", size=10)
    bold_font = Font(name="宋体", size=10, bold=True)
    thin_border = Border(
        left=Side(style="thin"), right=Side(style="thin"),
        top=Side(style="thin"), bottom=Side(style="thin"),
    )
    header_fill = PatternFill(start_color="D9E1F2", end_color="D9E1F2", fill_type="solid")
    title_fill = PatternFill(start_color="F2F2F2", end_color="F2F2F2", fill_type="solid")
    center_align = Alignment(horizontal="center", vertical="center", wrap_text=True)
    left_align = Alignment(horizontal="left", vertical="center", wrap_text=True)
    right_align = Alignment(horizontal="right", vertical="center")

    ws.column_dimensions["A"].width = 8
    ws.column_dimensions["B"].width = 55
    ws.column_dimensions["C"].width = 22

    ws.merge_cells("A1:C1")
    cell = ws["A1"]
    cell.value = f"高新技术企业优惠情况及明细表 {form_data.tax_year}年度"
    cell.font = title_font
    cell.alignment = center_align
    cell.fill = title_fill
    for col in ["A", "B", "C"]:
        ws[f"{col}1"].border = thin_border
    ws.row_dimensions[1].height = 35

    ws.merge_cells("A2:C2")
    cell = ws["A2"]
    cell.value = "A107041"
    cell.font = Font(name="宋体", size=10, bold=True)
    cell.alignment = Alignment(horizontal="right", vertical="center")
    cell.border = thin_border
    ws.row_dimensions[2].height = 20

    info_rows = [
        ("纳税人识别号", form_data.enterprise.tax_number),
        ("纳税人名称", form_data.enterprise.name),
    ]
    start_row = 3
    for i, (label, value) in enumerate(info_rows):
        row = start_row + i
        ws.cell(row=row, column=1, value=label).font = small_font
        ws.cell(row=row, column=1).alignment = center_align
        ws.cell(row=row, column=1).border = thin_border
        ws.merge_cells(start_row=row, start_column=2, end_row=row, end_column=2)
        ws.cell(row=row, column=2, value=value).font = normal_font
        ws.cell(row=row, column=2).alignment = center_align
        ws.cell(row=row, column=2).border = thin_border
        ws.cell(row=row, column=3).border = thin_border
        ws.row_dimensions[row].height = 22

    header_row = start_row + len(info_rows)
    headers = ["行次", "项目", "金额/信息"]
    for col_idx, header in enumerate(headers, 1):
        cell = ws.cell(row=header_row, column=col_idx, value=header)
        cell.font = header_font
        cell.alignment = center_align
        cell.fill = header_fill
        cell.border = thin_border
    ws.row_dimensions[header_row].height = 25

    row_data = form_data.to_row_data()
    section_rows = {1, 10}
    current_row = header_row + 1

    for row_num in sorted(row_data.keys()):
        data = row_data[row_num]
        is_section = row_num in section_rows

        ws.cell(row=current_row, column=1, value=str(row_num)).font = small_font
        ws.cell(row=current_row, column=1).alignment = center_align
        ws.cell(row=current_row, column=1).border = thin_border

        ws.cell(row=current_row, column=2, value=data["item"]).font = normal_font
        ws.cell(row=current_row, column=2).alignment = left_align
        ws.cell(row=current_row, column=2).border = thin_border

        ws.cell(row=current_row, column=3, value=data["amount"]).font = amount_font
        ws.cell(row=current_row, column=3).alignment = right_align
        ws.cell(row=current_row, column=3).border = thin_border

        if is_section:
            for col in range(1, 4):
                ws.cell(row=current_row, column=col).font = bold_font

        ws.row_dimensions[current_row].height = 20
        current_row += 1

    if output_path:
        os.makedirs(os.path.dirname(output_path) if os.path.dirname(output_path) else ".", exist_ok=True)
        wb.save(output_path)

    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    return buffer.read()


def export_annual_forms_excel(
    a100000: A100000FormData,
    a105000: Optional[A105000FormData] = None,
    a107012: Optional[A107012FormData] = None,
    a107041: Optional[A107041FormData] = None,
    output_path: Optional[str] = None,
) -> bytes:
    """
    导出年度申报表为多工作表Excel文件（合并所有相关表）

    Args:
        a100000: A100000年度申报主表
        a105000: A105000纳税调整项目明细表（可选）
        a107012: A107012研发费用加计扣除优惠明细表（可选）
        a107041: A107041高新技术企业优惠情况及明细表（可选）
        output_path: 输出文件路径（可选）

    Returns:
        Excel文件的字节数据
    """
    try:
        import openpyxl
        from openpyxl.styles import Font, Alignment, Border, Side, PatternFill
    except ImportError:
        raise ImportError("openpyxl未安装，请运行: pip install openpyxl")

    wb = openpyxl.Workbook()

    # 第一个工作表：A100000主表
    ws1 = wb.active
    ws1.title = "A100000年度申报主表"
    _build_worksheet(ws1, a100000.to_row_data(), a100000, cols=4,
                     headers=["行次", "项目", "金额", "计算方式"],
                     amount_key="amount", calc_key="calc_method")

    if a105000:
        ws2 = wb.create_sheet("A105000纳税调整项目明细表")
        _build_worksheet(ws2, a105000.to_row_data(), a105000, cols=6,
                         headers=["行次", "项目", "账面金额", "税收金额", "调增金额", "调减金额"],
                         amount_keys=["book", "tax", "increase", "decrease"],
                         calc_key=None)

    if a107012:
        ws3 = wb.create_sheet("A107012研发费用加计扣除")
        _build_worksheet(ws3, a107012.to_row_data(), a107012, cols=3,
                         headers=["行次", "项目", "金额"],
                         amount_key="amount", calc_key=None)

    if a107041:
        ws4 = wb.create_sheet("A107041高新技术企业优惠")
        _build_worksheet(ws4, a107041.to_row_data(), a107041, cols=3,
                         headers=["行次", "项目", "金额/信息"],
                         amount_key="amount", calc_key=None)

    if output_path:
        os.makedirs(os.path.dirname(output_path) if os.path.dirname(output_path) else ".", exist_ok=True)
        wb.save(output_path)

    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    return buffer.read()


def _build_worksheet(
    ws,
    row_data: Dict[int, Dict[str, str]],
    form_data,
    cols: int = 4,
    headers: Optional[List[str]] = None,
    amount_key: str = "amount",
    amount_keys: Optional[List[str]] = None,
    calc_key: Optional[str] = "calc_method",
):
    """通用工作表构建辅助函数"""
    import openpyxl.utils
    from openpyxl.styles import Font, Alignment, Border, Side, PatternFill

    ws.page_setup.paperSize = ws.PAPERSIZE_A4

    title_font = Font(name="宋体", size=14, bold=True)
    header_font = Font(name="宋体", size=10, bold=True)
    normal_font = Font(name="宋体", size=10)
    small_font = Font(name="宋体", size=9)
    amount_font = Font(name="Arial", size=10)
    bold_font = Font(name="宋体", size=10, bold=True)
    thin_border = Border(
        left=Side(style="thin"), right=Side(style="thin"),
        top=Side(style="thin"), bottom=Side(style="thin"),
    )
    header_fill = PatternFill(start_color="D9E1F2", end_color="D9E1F2", fill_type="solid")
    title_fill = PatternFill(start_color="F2F2F2", end_color="F2F2F2", fill_type="solid")
    center_align = Alignment(horizontal="center", vertical="center", wrap_text=True)
    left_align = Alignment(horizontal="left", vertical="center", wrap_text=True)
    right_align = Alignment(horizontal="right", vertical="center")

    # 设置列宽
    ws.column_dimensions["A"].width = 8
    ws.column_dimensions["B"].width = 55
    for i in range(3, cols + 1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(i)].width = 22

    # 标题
    title = getattr(form_data, '__class__', type(form_data)).__name__.replace("FormData", "")
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=cols)
    cell = ws.cell(row=1, column=1, value=f"{title} {getattr(form_data, 'tax_year', '')}年度")
    cell.font = title_font
    cell.alignment = center_align
    cell.fill = title_fill
    for i in range(1, cols + 1):
        ws.cell(row=1, column=i).border = thin_border
    ws.row_dimensions[1].height = 35

    # 表单编号
    ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=cols)
    cell = ws.cell(row=2, column=1, value=title)
    cell.font = Font(name="宋体", size=10, bold=True)
    cell.alignment = Alignment(horizontal="right", vertical="center")
    cell.border = thin_border
    ws.row_dimensions[2].height = 20

    # 企业信息
    ent = getattr(form_data, "enterprise", None)
    if ent:
        info_rows = [
            ("纳税人名称", getattr(ent, "name", "")),
            ("纳税人识别号", getattr(ent, "tax_number", "")),
        ]
    else:
        info_rows = []

    start_row = 3
    for i, (label, value) in enumerate(info_rows):
        row = start_row + i
        ws.cell(row=row, column=1, value=label).font = small_font
        ws.cell(row=row, column=1).alignment = center_align
        ws.cell(row=row, column=1).border = thin_border
        ws.merge_cells(start_row=row, start_column=2, end_row=row, end_column=2)
        ws.cell(row=row, column=2, value=value).font = normal_font
        ws.cell(row=row, column=2).alignment = center_align
        ws.cell(row=row, column=2).border = thin_border
        for c in range(3, cols + 1):
            ws.cell(row=row, column=c).border = thin_border
        ws.row_dimensions[row].height = 22

    # 表头
    header_row = start_row + len(info_rows)
    if headers is None:
        headers = ["行次", "项目", "金额"]
    for col_idx, header in enumerate(headers, 1):
        cell = ws.cell(row=header_row, column=col_idx, value=header)
        cell.font = header_font
        cell.alignment = center_align
        cell.fill = header_fill
        cell.border = thin_border
    ws.row_dimensions[header_row].height = 25

    # 数据行
    current_row = header_row + 1
    for row_num in sorted(row_data.keys()):
        data = row_data[row_num]

        ws.cell(row=current_row, column=1, value=str(row_num) if row_num <= 200 else "").font = small_font
        ws.cell(row=current_row, column=1).alignment = center_align
        ws.cell(row=current_row, column=1).border = thin_border

        ws.cell(row=current_row, column=2, value=data.get("item", "")).font = normal_font
        ws.cell(row=current_row, column=2).alignment = left_align
        ws.cell(row=current_row, column=2).border = thin_border

        if amount_keys:
            for idx, key in enumerate(amount_keys, 3):
                ws.cell(row=current_row, column=idx, value=data.get(key, "")).font = amount_font
                ws.cell(row=current_row, column=idx).alignment = right_align
                ws.cell(row=current_row, column=idx).border = thin_border
        elif amount_key:
            ws.cell(row=current_row, column=3, value=data.get(amount_key, "")).font = amount_font
            ws.cell(row=current_row, column=3).alignment = right_align
            ws.cell(row=current_row, column=3).border = thin_border

        if calc_key and cols >= 4:
            ws.cell(row=current_row, column=4, value=data.get(calc_key, "")).font = small_font
            ws.cell(row=current_row, column=4).alignment = left_align
            ws.cell(row=current_row, column=4).border = thin_border

        ws.row_dimensions[current_row].height = 20
        current_row += 1