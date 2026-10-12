"""
A200000 企业所得税月（季）度预缴纳税申报表（A类）生成服务
依据：国家税务总局公告2025年第17号修订格式

本模块实现：
1. A200000预缴申报表数据填充与计算逻辑
2. Excel格式导出（openpyxl）
3. PDF格式导出（reportlab）
"""
from __future__ import annotations

import io
import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

# ============================================================
# 数据模型定义
# ============================================================


@dataclass
class EnterpriseInfo:
    """企业基本信息"""
    name: str  # 纳税人名称
    tax_number: str  # 纳税人识别号（统一社会信用代码）
    industry_type: str = ""  # 国民经济行业类型
    tax_authority: str = ""  # 主管税务机关
    address: str = ""  # 地址
    legal_person: str = ""  # 法定代表人（负责人）
    accounting_method: str = "查账征收"  # 企业所得税征收方式
    start_date: str = ""  # 营业期限起始日期
    end_date: str = ""  # 营业期限终止日期
    registered_capital: str = ""  # 注册资本（万元）
    total_assets: float = 0.0  # 资产总额（万元）
    employee_count: int = 0  # 从业人数
    is_small_micro: bool = False  # 是否小型微利企业
    is_high_tech: bool = False  # 是否高新技术企业


@dataclass
class TaxPeriod:
    """税款所属期间"""
    year: int
    quarter: int  # 1-4，4表示年度申报
    month: Optional[int] = None  # 月度预缴时使用

    @property
    def start_date(self) -> str:
        """所属期起始日期"""
        if self.month:
            # 月度预缴
            return f"{self.year}-{self.month:02d}-01"
        quarter_starts = {1: "01", 2: "04", 3: "07", 4: "01"}
        quarter_ends = {1: "03", 2: "06", 3: "09", 4: "12"}
        return f"{self.year}-{quarter_starts[self.quarter]}-01"

    @property
    def end_date(self) -> str:
        """所属期终止日期"""
        if self.month:
            # 月度预缴：月末
            import calendar
            _, last_day = calendar.monthrange(self.year, self.month)
            return f"{self.year}-{self.month:02d}-{last_day:02d}"
        quarter_starts = {1: "01", 2: "04", 3: "07", 4: "01"}
        quarter_ends = {1: "03", 2: "06", 3: "09", 4: "12"}
        import calendar
        _, last_day = calendar.monthrange(self.year, int(quarter_ends[self.quarter]))
        return f"{self.year}-{quarter_ends[self.quarter]}-{last_day:02d}"

    @property
    def display(self) -> str:
        """显示文本"""
        if self.month:
            return f"{self.year}年{self.month}月01日 至 {self.end_date}"
        return f"{self.year}年{self.quarter}季度"


@dataclass
class A200000FormData:
    """
    A200000 预缴申报表数据
    依据：国家税务总局公告2025年第17号

    行次结构与数据来源说明：
    第1行  营业收入：利润表"营业收入"金额
    第2行  营业成本：利润表"营业成本"金额
    第3行  利润总额：利润表"利润总额"金额
    第4行  减：免税收入、减计收入等
    第5行  减：所得减免
    第6行  减：弥补以前年度亏损
    第7行  减：减免所得税额（预缴时不适用，年度汇算使用）
    第8行  实际利润额 / 按照上一纳税年度应纳税所得额平均额确定的应纳税所得额
    第9行  税率（25%）
    第10行 应纳所得税额
    第11行 减：减免所得税额
    第12行 减：实际已缴纳的所得税额
    第13行 减：特定业务预缴（征）所得税额
    第14行 应补（退）所得税额
    第15行 减：以前年度多缴的所得税额在本年抵减
    第16行 应补（退）所得税额
    """
    # 基本信息
    enterprise: EnterpriseInfo
    tax_period: TaxPeriod

    # 填报日期
    fill_date: str = ""

    # 主表数据（单位：元）
    operating_revenue: float = 0.0  # 第1行 营业收入
    operating_cost: float = 0.0  # 第2行 营业成本
    total_profit: float = 0.0  # 第3行 利润总额

    # 免税/减计收入（第4行明细）
    tax_exempt_income: float = 0.0  # 免税收入
    reduced_income: float = 0.0  # 减计收入
    other_income_reduction: float = 0.0  # 其他减计收入

    # 所得减免（第5行明细）
    income_reduction_amount: float = 0.0  # 所得减免金额
    agriculture_forestry_reduction: float = 0.0  # 农林牧渔业项目所得减免
    infrastructure_reduction: float = 0.0  # 公共基础设施项目所得减免
    environmental_protection_reduction: float = 0.0  # 环境保护节能节水项目所得减免
    technology_transfer_reduction: float = 0.0  # 符合条件的技术转让所得减免

    # 弥补以前年度亏损（第6行）
    prior_year_loss: float = 0.0  # 弥补以前年度亏损

    # 预缴方式
    prepay_method: str = "actual_profit"  # actual_profit: 按实际利润预缴
    # prior_year_avg: 按上一纳税年度平均额预缴

    # 已缴税款
    actual_paid_tax: float = 0.0  # 第12行 实际已缴纳的所得税额
    specific_prepaid_tax: float = 0.0  # 第13行 特定业务预缴所得税额
    prior_year_overpaid: float = 0.0  # 第15行 以前年度多缴在本年抵减

    # 计算结果（自动生成）
    actual_profit: float = 0.0  # 第8行 实际利润额
    tax_rate: float = 0.25  # 第9行 税率
    tax_payable: float = 0.0  # 第10行 应纳所得税额
    tax_reduction: float = 0.0  # 第11行 减免所得税额
    tax_to_pay_or_refund: float = 0.0  # 第14行 应补（退）所得税额
    final_tax_to_pay: float = 0.0  # 第16行 最终应补（退）所得税额

    # 纳税调整项目（辅助信息，用于明细表）
    tax_adjustments: List[Dict[str, Any]] = field(default_factory=list)
    # 每个调整项: {name, book_amount, tax_amount, adjustment_amount, type: "increase"/"decrease"}

    # 小型微利企业优惠信息
    small_micro_reduction: float = 0.0  # 减免所得税额（小型微利企业优惠）

    def __post_init__(self):
        """初始化后自动计算"""
        if not self.fill_date:
            self.fill_date = datetime.now().strftime("%Y-%m-%d")

    def calculate(self) -> Dict[str, float]:
        """
        计算A200000预缴申报表所有行次

        计算公式（依据国家税务总局2025年第17号公告）：
        第4行 = 免税收入 + 减计收入 + 其他减计收入
        第8行（实际利润额）= 第3行 - 第4行 - 第5行 - 第6行
        第10行（应纳所得税额）= 第8行 * 第9行
        第14行（应补退税额）= 第10行 - 第11行 - 第12行 - 第13行
        第16行（最终应补退税额）= 第14行 - 第15行

        Returns:
            所有计算结果字典
        """
        # 第4行 免税收入、减计收入等
        row4_income_reduction = (
            self.tax_exempt_income
            + self.reduced_income
            + self.other_income_reduction
        )

        # 第5行 所得减免
        row5_income_deduction = (
            self.income_reduction_amount
            + self.agriculture_forestry_reduction
            + self.income_reduction_amount
            + self.infrastructure_reduction
            + self.environmental_protection_reduction
            + self.technology_transfer_reduction
        )

        # 第8行 实际利润额
        self.actual_profit = (
            self.total_profit
            - row4_income_reduction
            - row5_income_deduction
            - self.prior_year_loss
        )
        self.actual_profit = max(0, self.actual_profit)

        # 第9行 税率
        if self.enterprise.is_small_micro:
            # 小型微利企业：25%计入应纳税所得额，20%税率，实际5%
            self.tax_rate = 0.25
            # 减免税额 = 实际利润 * (25% - 25% * 20%) = 实际利润 * 20%
            self.small_micro_reduction = self.actual_profit * 0.20
        elif self.enterprise.is_high_tech:
            self.tax_rate = 0.15
            self.small_micro_reduction = 0.0
        else:
            self.tax_rate = 0.25
            self.small_micro_reduction = 0.0

        # 第10行 应纳所得税额
        self.tax_payable = self.actual_profit * self.tax_rate

        # 第11行 减免所得税额
        self.tax_reduction = self.small_micro_reduction

        # 第14行 应补（退）所得税额
        self.tax_to_pay_or_refund = (
            self.tax_payable
            - self.tax_reduction
            - self.actual_paid_tax
            - self.specific_prepaid_tax
        )

        # 第16行 最终应补（退）所得税额
        self.final_tax_to_pay = self.tax_to_pay_or_refund - self.prior_year_overpaid

        return {
            "row4_income_reduction": row4_income_reduction,
            "row5_income_deduction": row5_income_deduction,
            "row8_actual_profit": self.actual_profit,
            "row9_tax_rate": self.tax_rate,
            "row10_tax_payable": self.tax_payable,
            "row11_tax_reduction": self.tax_reduction,
            "row14_tax_to_pay_or_refund": self.tax_to_pay_or_refund,
            "row16_final_tax_to_pay": self.final_tax_to_pay,
        }

    def to_row_data(self) -> Dict[int, Dict[str, str]]:
        """
        转换为行次数据格式（用于Excel/PDF渲染）

        Returns:
            {行号: {"item": 项目名称, "amount": 金额字符串, "calc_method": 计算方式}}
        """
        calc_results = self.calculate()

        row4_total = calc_results["row4_income_reduction"]
        row5_total = calc_results["row5_income_deduction"]

        rows = {
            1: {"item": "一、营业收入", "amount": _format_amount(self.operating_revenue),
                "calc_method": ""},
            2: {"item": "    减：营业成本", "amount": _format_amount(self.operating_cost),
                "calc_method": ""},
            3: {"item": "    利润总额", "amount": _format_amount(self.total_profit),
                "calc_method": ""},
            4: {"item": "    减：免税收入、减计收入等", "amount": _format_amount(row4_total),
                "calc_method": ""},
            4001: {"item": "      其中：免税收入", "amount": _format_amount(self.tax_exempt_income),
                   "calc_method": ""},
            4002: {"item": "            减计收入", "amount": _format_amount(self.reduced_income),
                   "calc_method": ""},
            4003: {"item": "            其他减计收入",
                   "amount": _format_amount(self.other_income_reduction), "calc_method": ""},
            5: {"item": "    减：所得减免", "amount": _format_amount(row5_total),
                "calc_method": ""},
            6: {"item": "    减：弥补以前年度亏损",
                "amount": _format_amount(self.prior_year_loss), "calc_method": ""},
            7: {"item": "    减：减免所得税额（预缴时不适用）", "amount": _format_amount(0),
                "calc_method": ""},
            8: {"item": "二、实际利润额",
                "amount": _format_amount(self.actual_profit),
                "calc_method": "行3-行4-行5-行6"},
            9: {"item": "    税率",
                "amount": f"{self.tax_rate * 100:.0f}%",
                "calc_method": "法定税率25%"},
            10: {"item": "三、应纳所得税额",
                 "amount": _format_amount(self.tax_payable),
                 "calc_method": "行8*行9"},
            11: {"item": "    减：减免所得税额",
                 "amount": _format_amount(self.tax_reduction),
                 "calc_method": "小微企业优惠减免等"},
            12: {"item": "    减：实际已缴纳的所得税额",
                 "amount": _format_amount(self.actual_paid_tax),
                 "calc_method": ""},
            13: {"item": "    减：特定业务预缴（征）所得税额",
                 "amount": _format_amount(self.specific_prepaid_tax),
                 "calc_method": ""},
            14: {"item": "四、应补（退）所得税额",
                 "amount": _format_amount(self.tax_to_pay_or_refund),
                 "calc_method": "行10-行11-行12-行13"},
            15: {"item": "    减：以前年度多缴的所得税额在本年抵减",
                 "amount": _format_amount(self.prior_year_overpaid),
                 "calc_method": ""},
            16: {"item": "五、应补（退）所得税额",
                 "amount": _format_amount(self.final_tax_to_pay),
                 "calc_method": "行14-行15"},
        }

        return rows


# ============================================================
# 辅助函数
# ============================================================

def _format_amount(value: float) -> str:
    """格式化金额：保留两位小数"""
    # Handle negative zero: -0.0 == 0.0 is True in Python but format differs
    if value == 0.0:
        return "0.00"
    return f"{value:.2f}"


def _parse_amount(value_str: str) -> float:
    """从字符串解析金额"""
    try:
        return float(value_str.replace(",", ""))
    except ValueError:
        return 0.0


# ============================================================
# Excel 导出模块
# ============================================================

def export_a200000_excel(
    form_data: A200000FormData,
    output_path: Optional[str] = None,
) -> bytes:
    """
    导出A200000预缴申报表为Excel文件（使用openpyxl）

    格式符合国家税务总局申报表要求：
    - A4纸张大小
    - 表头：表单名称、纳税人识别号等
    - 表体：行次、项目、金额、计算方式
    - 表尾：纳税人声明、签章栏

    Args:
        form_data: A200000申报表数据
        output_path: 输出文件路径（可选）

    Returns:
        Excel文件的字节数据
    """
    try:
        import openpyxl
        from openpyxl.styles import (
            Font, Alignment, Border, Side, PatternFill, numbers
        )
        from openpyxl.utils import get_column_letter
    except ImportError:
        raise ImportError(
            "openpyxl未安装，请运行: pip install openpyxl"
        )

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "A200000预缴申报表"

    # 设置打印页面为A4
    ws.page_setup.paperSize = ws.PAPERSIZE_A4
    ws.page_setup.orientation = "portrait"
    ws.page_margins.left = 0.7
    ws.page_margins.right = 0.7
    ws.page_margins.top = 0.75
    ws.page_margins.bottom = 0.75

    # ========================================================
    # 样式定义
    # ========================================================
    title_font = Font(name="宋体", size=16, bold=True)
    subtitle_font = Font(name="宋体", size=10, bold=True)
    header_font = Font(name="宋体", size=10, bold=True)
    normal_font = Font(name="宋体", size=10)
    small_font = Font(name="宋体", size=9)
    amount_font = Font(name="Arial", size=10)
    bold_font = Font(name="宋体", size=10, bold=True)

    thin_border = Border(
        left=Side(style="thin"),
        right=Side(style="thin"),
        top=Side(style="thin"),
        bottom=Side(style="thin"),
    )

    bottom_border = Border(bottom=Side(style="thin"))

    header_fill = PatternFill(start_color="D9E1F2", end_color="D9E1F2", fill_type="solid")
    title_fill = PatternFill(start_color="F2F2F2", end_color="F2F2F2", fill_type="solid")

    center_align = Alignment(horizontal="center", vertical="center", wrap_text=True)
    left_align = Alignment(horizontal="left", vertical="center", wrap_text=True)
    right_align = Alignment(horizontal="right", vertical="center")

    # ========================================================
    # 列宽设置
    # ========================================================
    ws.column_dimensions["A"].width = 8   # 行次
    ws.column_dimensions["B"].width = 50  # 项目
    ws.column_dimensions["C"].width = 20  # 金额（元）
    ws.column_dimensions["D"].width = 30  # 计算方式

    # ========================================================
    # 第1行：表单标题
    # ========================================================
    ws.merge_cells("A1:D1")
    cell = ws["A1"]
    cell.value = "中华人民共和国企业所得税月（季）度预缴纳税申报表（A类）"
    cell.font = title_font
    cell.alignment = center_align
    cell.fill = title_fill
    ws.row_dimensions[1].height = 35

    # 添加边框到合并单元格
    for col in ["A", "B", "C", "D"]:
        ws[f"{col}1"].border = thin_border

    # ========================================================
    # 第2行：表单编号
    # ========================================================
    ws.merge_cells("A2:D2")
    cell = ws["A2"]
    cell.value = "A200000"
    cell.font = subtitle_font
    cell.alignment = Alignment(horizontal="right", vertical="center")
    cell.border = thin_border
    ws.row_dimensions[2].height = 20

    # ========================================================
    # 第3-6行：企业基本信息
    # ========================================================
    info_rows = [
        ("税款所属期间", f"{form_data.tax_period.start_date} 至 {form_data.tax_period.end_date}"),
        ("纳税人识别号（统一社会信用代码）", form_data.enterprise.tax_number),
        ("纳税人名称", form_data.enterprise.name),
        ("企业所得税征收方式", form_data.enterprise.accounting_method),
    ]

    start_row = 3
    for i, (label, value) in enumerate(info_rows):
        row = start_row + i
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=1)
        ws.merge_cells(start_row=row, start_column=2, end_row=row, end_column=3)
        ws.merge_cells(start_row=row, start_column=4, end_row=row, end_column=4)

        ws.cell(row=row, column=1, value=label).font = small_font
        ws.cell(row=row, column=1).alignment = center_align
        ws.cell(row=row, column=1).border = thin_border

        ws.cell(row=row, column=2, value=value).font = normal_font
        ws.cell(row=row, column=2).alignment = center_align
        ws.cell(row=row, column=2).border = thin_border

        ws.cell(row=row, column=3).border = thin_border
        ws.cell(row=row, column=4, value="金额单位：元（列至角分）").font = small_font
        ws.cell(row=row, column=4).alignment = center_align
        ws.cell(row=row, column=4).border = thin_border

        ws.row_dimensions[row].height = 22

    # 如果还有额外信息，继续写入
    if form_data.enterprise.industry_type or form_data.enterprise.tax_authority:
        row = start_row + len(info_rows)
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=1)
        ws.merge_cells(start_row=row, start_column=2, end_row=row, end_column=3)
        ws.merge_cells(start_row=row, start_column=4, end_row=row, end_column=4)

        info_text = ""
        if form_data.enterprise.industry_type:
            info_text = f"行业类型：{form_data.enterprise.industry_type}"
        if form_data.enterprise.tax_authority:
            info_text += f" | 主管税务机关：{form_data.enterprise.tax_authority}"

        ws.cell(row=row, column=1, value="备注信息").font = small_font
        ws.cell(row=row, column=1).alignment = center_align
        ws.cell(row=row, column=1).border = thin_border

        ws.cell(row=row, column=2, value=info_text).font = small_font
        ws.cell(row=row, column=2).alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)
        ws.cell(row=row, column=2).border = thin_border

        ws.cell(row=row, column=3).border = thin_border
        ws.cell(row=row, column=4).border = thin_border

        ws.row_dimensions[row].height = 22

    # ========================================================
    # 表头行
    # ========================================================
    header_row = start_row + len(info_rows) + 1
    headers = ["行次", "项目", "金额（元）", "计算方式"]
    for col_idx, header in enumerate(headers, 1):
        cell = ws.cell(row=header_row, column=col_idx, value=header)
        cell.font = header_font
        cell.alignment = center_align
        cell.fill = header_fill
        cell.border = thin_border
    ws.row_dimensions[header_row].height = 25

    # ========================================================
    # 数据行
    # ========================================================
    row_data = form_data.to_row_data()

    # 定义特殊行次样式（合计/重要行）
    important_rows = {1, 3, 8, 10, 14, 16}
    sub_item_rows = {4001, 4002, 4003}

    data_start_row = header_row + 1
    current_row = data_start_row

    # 按行号排序
    sorted_row_nums = sorted(row_data.keys())

    for row_num in sorted_row_nums:
        data = row_data[row_num]
        is_sub_item = row_num in sub_item_rows
        is_important = row_num in important_rows

        # 行次列
        row_num_display = ""
        if row_num <= 100:
            row_num_display = str(row_num)
        cell = ws.cell(row=current_row, column=1, value=row_num_display)
        cell.font = small_font if is_sub_item else normal_font
        cell.alignment = center_align
        cell.border = thin_border

        # 项目列
        cell = ws.cell(row=current_row, column=2, value=data["item"])
        cell.font = small_font if is_sub_item else normal_font
        cell.alignment = left_align
        cell.border = thin_border
        # 子项缩进已在文本中用空格表示

        # 金额列
        cell = ws.cell(row=current_row, column=3, value=data["amount"])
        cell.font = amount_font
        cell.alignment = right_align
        cell.border = thin_border

        # 计算方式列
        cell = ws.cell(row=current_row, column=4, value=data["calc_method"])
        cell.font = small_font
        cell.alignment = left_align
        cell.border = thin_border

        # 重要行加粗
        if is_important:
            for col in range(1, 5):
                ws.cell(row=current_row, column=col).font = bold_font

        # 第3行（利润总额）后设置细边框分隔
        if row_num == 3:
            for col in range(1, 5):
                ws.cell(row=current_row, column=col).border = Border(
                    left=Side(style="thin"),
                    right=Side(style="thin"),
                    top=Side(style="thin"),
                    bottom=Side(style="medium"),
                )

        # 第8行（实际利润额）后设置细边框分隔
        if row_num == 8:
            for col in range(1, 5):
                ws.cell(row=current_row, column=col).border = Border(
                    left=Side(style="thin"),
                    right=Side(style="thin"),
                    top=Side(style="thin"),
                    bottom=Side(style="medium"),
                )

        # 第16行（最终结果）设置双线底边框
        if row_num == 16:
            for col in range(1, 5):
                ws.cell(row=current_row, column=col).border = Border(
                    left=Side(style="thin"),
                    right=Side(style="thin"),
                    top=Side(style="thin"),
                    bottom=Side(style="double"),
                )

        ws.row_dimensions[current_row].height = 20
        current_row += 1

    # ========================================================
    # 表尾：纳税人声明
    # ========================================================
    statement_row = current_row + 2
    ws.row_dimensions[statement_row].height = 10

    # 声明文本
    ws.merge_cells(start_row=statement_row + 1, start_column=1,
                   end_row=statement_row + 1, end_column=4)
    cell = ws.cell(row=statement_row + 1, column=1,
                   value="谨声明：本纳税申报表是根据国家税收法律法规及相关规定填报的，是真实的、可靠的、完整的。")
    cell.font = small_font
    cell.alignment = left_align

    # 签章行
    sign_row = statement_row + 3
    ws.merge_cells(start_row=sign_row, start_column=1,
                   end_row=sign_row, end_column=2)
    ws.cell(row=sign_row, column=1, value="纳税人公章：").font = small_font
    ws.cell(row=sign_row, column=1).border = bottom_border

    ws.merge_cells(start_row=sign_row, start_column=3,
                   end_row=sign_row, end_column=4)
    ws.cell(row=sign_row, column=3, value="填表日期：      年    月    日").font = small_font
    ws.cell(row=sign_row, column=3).border = bottom_border

    sign_row2 = sign_row + 2
    ws.merge_cells(start_row=sign_row2, start_column=1,
                   end_row=sign_row2, end_column=2)
    ws.cell(row=sign_row2, column=1, value="法定代表人（负责人）签章：").font = small_font
    ws.cell(row=sign_row2, column=1).border = bottom_border

    ws.merge_cells(start_row=sign_row2, start_column=3,
                   end_row=sign_row2, end_column=4)
    ws.cell(row=sign_row2, column=3, value="经办人：").font = small_font
    ws.cell(row=sign_row2, column=3).border = bottom_border

    # ========================================================
    # 保存
    # ========================================================
    if output_path:
        os.makedirs(os.path.dirname(output_path) if os.path.dirname(output_path) else ".", exist_ok=True)
        wb.save(output_path)

    # 返回字节数据
    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    return buffer.read()


# ============================================================
# PDF 导出模块
# ============================================================

def export_a200000_pdf(
    form_data: A200000FormData,
    output_path: Optional[str] = None,
) -> bytes:
    """
    导出A200000预缴申报表为PDF文件（使用reportlab）

    格式符合正式申报表要求：
    - A4纸张
    - 宋体字体
    - 正式表格格式

    Args:
        form_data: A200000申报表数据
        output_path: 输出文件路径（可选）

    Returns:
        PDF文件的字节数据
    """
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.units import mm, cm
        from reportlab.lib.colors import HexColor, black, white
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.platypus import (
            SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer,
            PageTemplate, BaseDocTemplate
        )
        from reportlab.platypus.frames import Frame
        from reportlab.lib.units import inch
    except ImportError:
        raise ImportError(
            "reportlab未安装，请运行: pip install reportlab"
        )

    try:
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        from reportlab.pdfbase.cidfonts import UnicodeCIDFont

        # 注册中文字体
        _register_chinese_fonts()
        font_name = "STSong"
    except Exception:
        # 如果中文字体注册失败，使用CID字体
        font_name = "STSong-Light"

    # 创建文档
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=15 * mm,
        rightMargin=15 * mm,
        topMargin=15 * mm,
        bottomMargin=15 * mm,
        title="A200000企业所得税预缴纳税申报表",
        author="企业所得税填报系统",
    )

    # ========================================================
    # 构建文档元素
    # ========================================================
    story = []

    # 标题
    title_style = ParagraphStyle(
        "Title",
        fontName=font_name,
        fontSize=16,
        leading=22,
        alignment=1,  # 居中
        spaceAfter=2 * mm,
    )
    story.append(Paragraph("中华人民共和国企业所得税月（季）度预缴纳税申报表（A类）", title_style))

    # 表单编号
    subtitle_style = ParagraphStyle(
        "Subtitle",
        fontName=font_name,
        fontSize=10,
        leading=14,
        alignment=2,  # 右对齐
        spaceAfter=4 * mm,
    )
    story.append(Paragraph("A200000", subtitle_style))

    # 基本信息表格
    info_data = [
        ["税款所属期间", f"{form_data.tax_period.start_date} 至 {form_data.tax_period.end_date}", "", "金额单位：元（列至角分）"],
        ["纳税人识别号", form_data.enterprise.tax_number, "", ""],
        ["纳税人名称", form_data.enterprise.name, "", ""],
        ["征收方式", form_data.enterprise.accounting_method, "", ""],
    ]

    info_table = Table(info_data, colWidths=[45 * mm, 55 * mm, 20 * mm, 45 * mm])
    info_table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), font_name),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("ALIGN", (0, 0), (0, -1), "CENTER"),
        ("ALIGN", (1, 0), (1, -1), "CENTER"),
        ("ALIGN", (3, 0), (3, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.5, black),
        ("ROWBACKGROUNDS", (0, 0), (-1, -1), [HexColor("#F5F5F5"), white]),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    story.append(info_table)
    story.append(Spacer(1, 3 * mm))

    # 主表数据
    row_data = form_data.to_row_data()
    sorted_row_nums = sorted(row_data.keys())

    table_data = [["行次", "项目", "金额（元）", "计算方式"]]
    sub_item_rows = {4001, 4002, 4003}
    important_rows = {1, 3, 8, 10, 14, 16}

    for row_num in sorted_row_nums:
        data = row_data[row_num]
        row_num_display = str(row_num) if row_num <= 100 else ""

        # 如果是子项，使用缩进
        item_name = data["item"]
        if row_num in sub_item_rows:
            item_name = f"  {item_name}"

        table_data.append([
            row_num_display,
            item_name,
            data["amount"],
            data["calc_method"],
        ])

    # 主表
    col_widths = [14 * mm, 90 * mm, 35 * mm, 40 * mm]
    main_table = Table(table_data, colWidths=col_widths)

    # 构建表格样式
    table_styles = [
        ("FONTNAME", (0, 0), (-1, -1), font_name),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("GRID", (0, 0), (-1, -1), 0.5, black),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]

    # 表头样式
    table_styles.extend([
        ("BACKGROUND", (0, 0), (-1, 0), HexColor("#D9E1F2")),
        ("FONTNAME", (0, 0), (-1, 0), font_name),
        ("FONTSIZE", (0, 0), (-1, 0), 10),
        ("TEXTCOLOR", (0, 0), (-1, 0), black),
        ("ALIGN", (0, 0), (-1, 0), "CENTER"),
        ("BOLD", (0, 0), (-1, 0), True),
    ])

    # 数据行样式
    for i, row_num in enumerate(sorted_row_nums):
        data_row = i + 1  # 表格行号（从表头之后开始）
        is_sub = row_num in sub_item_rows
        is_important = row_num in important_rows

        # 对齐方式
        table_styles.append(("ALIGN", (0, data_row), (0, data_row), "CENTER"))
        table_styles.append(("ALIGN", (1, data_row), (1, data_row), "LEFT"))
        table_styles.append(("ALIGN", (2, data_row), (3, data_row), "RIGHT"))

        # 子项字体小
        if is_sub:
            table_styles.append(("FONTSIZE", (0, data_row), (-1, data_row), 8))

        # 重要行加粗
        if is_important:
            table_styles.append(("BOLD", (0, data_row), (-1, data_row), True))

        # 第3行和第8行底部设置粗线
        if row_num == 3:
            table_styles.append(("LINEBELOW", (0, data_row), (-1, data_row), 1.5, black))
        if row_num == 8:
            table_styles.append(("LINEBELOW", (0, data_row), (-1, data_row), 1.5, black))

        # 第16行双线
        if row_num == 16:
            table_styles.append(("LINEBELOW", (0, data_row), (-1, data_row), 2.5, black))

    main_table.setStyle(TableStyle(table_styles))
    story.append(main_table)

    # 声明
    story.append(Spacer(1, 8 * mm))
    statement_style = ParagraphStyle(
        "Statement",
        fontName=font_name,
        fontSize=9,
        leading=14,
        alignment=0,
        spaceAfter=6 * mm,
    )
    story.append(Paragraph(
        "谨声明：本纳税申报表是根据国家税收法律法规及相关规定填报的，是真实的、可靠的、完整的。",
        statement_style,
    ))

    # 签章
    sign_style = ParagraphStyle(
        "Sign",
        fontName=font_name,
        fontSize=9,
        leading=16,
        alignment=0,
        spaceAfter=4 * mm,
    )
    story.append(Paragraph("纳税人公章：____________________    填表日期：______年____月____日", sign_style))
    story.append(Paragraph("法定代表人（负责人）签章：________    经办人：________________", sign_style))

    # 构建PDF
    doc.build(story)

    # 获取字节数据
    buffer.seek(0)
    pdf_bytes = buffer.read()

    # 保存到文件
    if output_path:
        os.makedirs(os.path.dirname(output_path) if os.path.dirname(output_path) else ".", exist_ok=True)
        with open(output_path, "wb") as f:
            f.write(pdf_bytes)

    return pdf_bytes


def _register_chinese_fonts():
    """注册中文字体用于PDF生成"""
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont

    # 注册Adobe内置CID字体（不需要安装系统字体）
    try:
        pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    except Exception:
        pass

    # 尝试注册系统字体
    import platform
    system = platform.system()

    if system == "Windows":
        font_paths = [
            (r"C:\Windows\Fonts\simsun.ttc", "STSong"),
            (r"C:\Windows\Fonts\simhei.ttf", "SimHei"),
            (r"C:\Windows\Fonts\msyh.ttc", "MicrosoftYaHei"),
        ]
    elif system == "Darwin":
        font_paths = [
            ("/System/Library/Fonts/PingFang.ttc", "PingFang"),
            ("/System/Library/Fonts/STHeiti Light.ttc", "STHeiti"),
        ]
    else:  # Linux
        font_paths = [
            ("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc", "WenQuanYi"),
            ("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc", "NotoSansCJK"),
        ]

    from reportlab.pdfbase.ttfonts import TTFont

    for font_path, font_name in font_paths:
        try:
            if os.path.exists(font_path):
                pdfmetrics.registerFont(TTFont(font_name, font_path))
                return
        except Exception:
            continue


# ============================================================
# 便捷导出函数
# ============================================================

def generate_a200000_return(
    enterprise: EnterpriseInfo,
    tax_period: TaxPeriod,
    operating_revenue: float = 0.0,
    operating_cost: float = 0.0,
    total_profit: float = 0.0,
    tax_exempt_income: float = 0.0,
    reduced_income: float = 0.0,
    other_income_reduction: float = 0.0,
    income_reduction_amount: float = 0.0,
    agriculture_forestry_reduction: float = 0.0,
    infrastructure_reduction: float = 0.0,
    environmental_protection_reduction: float = 0.0,
    technology_transfer_reduction: float = 0.0,
    prior_year_loss: float = 0.0,
    actual_paid_tax: float = 0.0,
    specific_prepaid_tax: float = 0.0,
    prior_year_overpaid: float = 0.0,
    tax_adjustments: Optional[List[Dict[str, Any]]] = None,
) -> A200000FormData:
    """
    便捷函数：创建A200000预缴申报表数据并自动计算

    Args:
        enterprise: 企业基本信息
        tax_period: 税款所属期间
        operating_revenue: 营业收入（元）
        operating_cost: 营业成本（元）
        total_profit: 利润总额（元）
        tax_exempt_income: 免税收入（元）
        reduced_income: 减计收入（元）
        other_income_reduction: 其他减计收入（元）
        income_reduction_amount: 所得减免金额（元）
        agriculture_forestry_reduction: 农林牧渔业项目所得减免（元）
        infrastructure_reduction: 公共基础设施项目所得减免（元）
        environmental_protection_reduction: 环境保护节能节水项目所得减免（元）
        technology_transfer_reduction: 符合条件的技术转让所得减免（元）
        prior_year_loss: 弥补以前年度亏损（元）
        actual_paid_tax: 实际已缴纳的所得税额（元）
        specific_prepaid_tax: 特定业务预缴所得税额（元）
        prior_year_overpaid: 以前年度多缴在本年抵减（元）
        tax_adjustments: 纳税调整项目列表

    Returns:
        已计算完成的A200000FormData对象
    """
    form_data = A200000FormData(
        enterprise=enterprise,
        tax_period=tax_period,
        operating_revenue=operating_revenue,
        operating_cost=operating_cost,
        total_profit=total_profit,
        tax_exempt_income=tax_exempt_income,
        reduced_income=reduced_income,
        other_income_reduction=other_income_reduction,
        income_reduction_amount=income_reduction_amount,
        agriculture_forestry_reduction=agriculture_forestry_reduction,
        infrastructure_reduction=infrastructure_reduction,
        environmental_protection_reduction=environmental_protection_reduction,
        technology_transfer_reduction=technology_transfer_reduction,
        prior_year_loss=prior_year_loss,
        actual_paid_tax=actual_paid_tax,
        specific_prepaid_tax=specific_prepaid_tax,
        prior_year_overpaid=prior_year_overpaid,
        tax_adjustments=tax_adjustments or [],
    )

    form_data.calculate()
    return form_data


def export_to_file(
    form_data: A200000FormData,
    output_path: str,
    format: str = "excel",
) -> str:
    """
    导出申报表到文件

    Args:
        form_data: 申报表数据
        output_path: 输出文件路径
        format: 导出格式，"excel" 或 "pdf"

    Returns:
        输出文件路径
    """
    fmt = format.lower()
    if fmt == "excel" or fmt == "xlsx":
        export_a200000_excel(form_data, output_path)
    elif fmt == "pdf":
        export_a200000_pdf(form_data, output_path)
    else:
        raise ValueError(f"不支持的导出格式: {format}，支持格式: excel, pdf")

    return output_path
