"""
财务数据导入服务
支持Excel财务报表解析、数据映射、验证和模板生成
"""
import io
import logging
import re
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from openpyxl import Workbook
from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
from openpyxl.utils import get_column_letter

try:
    # 单次导入行数上限由 core 任务提供（app/import_limits.py）；缺失时失败关闭，
    # 绝不放行无上限导入（与 assistant.py 聊天 Excel 转换同口径）。
    from ....import_limits import MAX_IMPORT_ROWS
except ImportError:  # pragma: no cover - 集成共享模块后不存在该分支
    MAX_IMPORT_ROWS = None

logger = logging.getLogger(__name__)


class ImportRowLimitExceeded(ValueError):
    """导入数据行数超过共享上限 MAX_IMPORT_ROWS：解析阶段即中止，不产出半成品结果。"""


def _enforce_row_limit(row_count: int) -> None:
    """行数防护：超限即抛 ImportRowLimitExceeded（解析阶段中止，无半成品）；
    共享上限缺失时失败关闭，绝不放行无上限导入（与 assistant.py 同口径）。"""
    if MAX_IMPORT_ROWS is None:
        raise ImportRowLimitExceeded("导入行数上限服务暂不可用，请稍后重试")
    if row_count > MAX_IMPORT_ROWS:
        raise ImportRowLimitExceeded(
            f"文件数据行数超过单次导入上限（{MAX_IMPORT_ROWS} 行），请拆分后导入"
        )


# ============================================================
# 数据结构和枚举
# ============================================================

class StatementType(Enum):
    """财务报表类型"""
    BALANCE_SHEET = "balance_sheet"
    INCOME_STATEMENT = "income_statement"
    CASH_FLOW = "cash_flow"


class ImportFormat(Enum):
    """导入格式类型"""
    STANDARD_TEMPLATE = "standard_template"
    FINANCIAL_SYSTEM_EXPORT = "financial_system_export"


@dataclass
class ValidationError:
    """数据验证错误"""
    field: str
    message: str
    severity: str = "error"  # error, warning


@dataclass
class ImportResult:
    """导入结果"""
    success: bool
    statement_type: Optional[str] = None
    format_detected: Optional[str] = None
    mapped_data: Dict[str, Any] = field(default_factory=dict)
    errors: List[ValidationError] = field(default_factory=list)
    warnings: List[ValidationError] = field(default_factory=list)
    summary: str = ""


@dataclass
class ParsedBalanceSheet:
    """解析后的资产负债表数据"""
    # Header
    company_name: str = ""
    period: str = ""
    currency: str = "CNY"

    # Assets (期末余额)
    monetary_fund: float = 0.0           # 货币资金
    accounts_receivable: float = 0.0      # 应收账款
    prepayments: float = 0.0              # 预付款项
    other_receivables: float = 0.0        # 其他应收款
    inventory: float = 0.0                # 存货
    total_current_assets: float = 0.0     # 流动资产合计
    fixed_assets: float = 0.0             # 固定资产
    intangible_assets: float = 0.0        # 无形资产
    right_of_use_assets: float = 0.0      # 使用权资产
    long_term_deferred_expenses: float = 0.0  # 长期待摊费用
    deferred_tax_assets: float = 0.0      # 递延所得税资产
    total_non_current_assets: float = 0.0 # 非流动资产合计
    total_assets: float = 0.0             # 资产总计

    # Liabilities (期末余额)
    short_term_loans: float = 0.0         # 短期借款
    accounts_payable: float = 0.0         # 应付账款
    contract_liabilities: float = 0.0     # 合同负债
    employee_benefits_payable: float = 0.0  # 应付职工薪酬
    taxes_payable: float = 0.0            # 应交税费
    other_payables: float = 0.0           # 其他应付款
    total_current_liabilities: float = 0.0  # 流动负债合计
    lease_liabilities: float = 0.0        # 租赁负债
    deferred_tax_liabilities: float = 0.0 # 递延所得税负债
    total_non_current_liabilities: float = 0.0  # 非流动负债合计
    total_liabilities: float = 0.0        # 负债合计

    # Equity (期末余额)
    paid_in_capital: float = 0.0          # 实收资本
    total_equity: float = 0.0             # 所有者权益合计


@dataclass
class ParsedIncomeStatement:
    """解析后的利润表数据"""
    # Header
    company_name: str = ""
    period: str = ""
    currency: str = "CNY"

    operating_revenue: float = 0.0         # 营业收入
    operating_cost: float = 0.0            # 营业成本
    taxes_and_surcharges: float = 0.0      # 税金及附加
    selling_expenses: float = 0.0          # 销售费用
    administrative_expenses: float = 0.0   # 管理费用
    rd_expenses: float = 0.0               # 研发费用
    financial_expenses: float = 0.0        # 财务费用
    interest_expense: float = 0.0          # 利息费用
    interest_income: float = 0.0           # 利息收入
    other_income: float = 0.0              # 其他收益
    investment_income: float = 0.0         # 投资收益
    non_operating_income: float = 0.0      # 营业外收入
    non_operating_expenses: float = 0.0    # 营业外支出
    total_profit: float = 0.0              # 利润总额
    income_tax_expense: float = 0.0        # 所得税费用
    net_profit: float = 0.0                # 净利润


@dataclass
class ParsedCashFlowStatement:
    """解析后的现金流量表数据"""
    # Header
    company_name: str = ""
    period: str = ""
    currency: str = "CNY"

    cash_from_sales: float = 0.0                    # 销售商品、提供劳务收到的现金
    cash_from_other_operating: float = 0.0           # 收到其他与经营活动有关的现金
    total_operating_cash_inflow: float = 0.0          # 经营活动现金流入小计
    cash_paid_for_goods: float = 0.0                  # 购买商品、接受劳务支付的现金
    cash_paid_to_employees: float = 0.0               # 支付给职工及为职工支付的现金
    cash_paid_for_taxes: float = 0.0                  # 支付的各项税费
    cash_paid_for_other_operating: float = 0.0        # 支付其他与经营活动有关的现金
    total_operating_cash_outflow: float = 0.0         # 经营活动现金流出小计
    net_operating_cash_flow: float = 0.0              # 经营活动产生的现金流量净额
    net_investing_cash_flow: float = 0.0              # 投资活动产生的现金流量净额
    net_financing_cash_flow: float = 0.0              # 筹资活动产生的现金流量净额
    net_cash_increase: float = 0.0                    # 现金及现金等价物净增加额
    beginning_cash_balance: float = 0.0               # 期初现金及现金等价物余额
    ending_cash_balance: float = 0.0                  # 期末现金及现金等价物余额


# ============================================================
# 财务系统导出文件原始XML解析器
# ============================================================

class FinancialSystemXmlParser:
    """
    解析财务系统导出的Excel文件（xlsx）。
    由于部分财务系统导出的xlsx存在样式XML错误，无法通过openpyxl正常读取，
    因此直接使用zip+XML方式解析底层数据。
    """

    SPREADSHEET_NS = {"ss": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}

    @classmethod
    def parse(cls, file_path: str) -> Tuple[List[str], List[Dict[str, Any]]]:
        """
        解析xlsx文件，返回共享字符串表和行数据。

        Returns:
            (shared_strings, rows) where rows is a list of dicts
            mapping cell coordinates (e.g. 'A1') to string/numeric values.
        """
        zf = zipfile.ZipFile(file_path)
        shared_strings = cls._read_shared_strings(zf)
        rows = cls._read_sheet(zf, shared_strings)
        zf.close()
        return shared_strings, rows

    @classmethod
    def _read_shared_strings(cls, zf: zipfile.ZipFile) -> List[str]:
        """读取共享字符串表"""
        try:
            ss_xml = zf.read("xl/sharedStrings.xml")
        except KeyError:
            return []
        root = ET.fromstring(ss_xml)
        return [t.text for t in root.findall(".//ss:t", cls.SPREADSHEET_NS)]

    @classmethod
    def _read_sheet(cls, zf: zipfile.ZipFile, shared_strings: List[str]) -> List[Dict[str, Any]]:
        """读取工作表数据"""
        sheet_xml = zf.read("xl/worksheets/sheet1.xml")
        root = ET.fromstring(sheet_xml)
        rows = []
        for row in root.findall(".//ss:row", cls.SPREADSHEET_NS):
            cells = {}
            for c in row.findall("ss:c", cls.SPREADSHEET_NS):
                coord = c.get("r")
                t_attr = c.get("t")
                v_elem = c.find("ss:v", cls.SPREADSHEET_NS)
                raw_val = v_elem.text if v_elem is not None else None

                # Inline string: value is in ss:is/ss:t, not ss:v
                if t_attr == "inlineStr":
                    is_elem = c.find("ss:is", cls.SPREADSHEET_NS)
                    if is_elem is not None:
                        t_elem = is_elem.find("ss:t", cls.SPREADSHEET_NS)
                        if t_elem is not None and t_elem.text:
                            cells[coord] = t_elem.text
                        else:
                            cells[coord] = None
                    else:
                        cells[coord] = None
                    continue

                if raw_val is None:
                    cells[coord] = None
                    continue

                # Shared string index
                if t_attr == "s":
                    try:
                        idx = int(raw_val)
                        if idx < len(shared_strings):
                            cells[coord] = shared_strings[idx]
                        else:
                            cells[coord] = raw_val
                    except (ValueError, TypeError):
                        cells[coord] = raw_val
                else:
                    # Try numeric conversion
                    try:
                        cells[coord] = float(raw_val)
                    except (ValueError, TypeError):
                        cells[coord] = raw_val
            rows.append(cells)
            # 流式行数防护：超限即中止，不在内存继续累积全表（可检测溢出，审计台账 A06）
            _enforce_row_limit(len(rows))
        return rows


# ============================================================
# 财务系统导出文件解析器
# ============================================================

class FinancialSystemExportParser:
    """解析财务系统导出的财务报表"""

    # 资产负债表项目映射（项目名称 -> ParsedBalanceSheet属性名）
    BALANCE_SHEET_ITEM_MAP = {
        "货币资金": "monetary_fund",
        "应收账款": "accounts_receivable",
        "预付款项": "prepayments",
        "其他应收款": "other_receivables",
        "存货": "inventory",
        "流动资产合计": "total_current_assets",
        "固定资产": "fixed_assets",
        "无形资产": "intangible_assets",
        "使用权资产": "right_of_use_assets",
        "长期待摊费用": "long_term_deferred_expenses",
        "递延所得税资产": "deferred_tax_assets",
        "非流动资产合计": "total_non_current_assets",
        "短期借款": "short_term_loans",
        "应付账款": "accounts_payable",
        "合同负债": "contract_liabilities",
        "应付职工薪酬": "employee_benefits_payable",
        "应交税费": "taxes_payable",
        "其他应付款": "other_payables",
        "流动负债合计": "total_current_liabilities",
        "租赁负债": "lease_liabilities",
        "递延所得税负债": "deferred_tax_liabilities",
        "非流动负债合计": "total_non_current_liabilities",
        "负债合计": "total_liabilities",
        "实收资本": "paid_in_capital",
        "实收资本（股本）": "paid_in_capital",
        "负债和所有者权益总计": "total_assets",
        "资产总计": "total_assets",
    }

    # 利润表项目映射
    INCOME_STATEMENT_ITEM_MAP = {
        "营业收入": "operating_revenue",
        "一、营业总收入": "operating_revenue",
        "营业成本": "operating_cost",
        "其中：营业成本": "operating_cost",
        "税金及附加": "taxes_and_surcharges",
        "销售费用": "selling_expenses",
        "管理费用": "administrative_expenses",
        "研发费用": "rd_expenses",
        "财务费用": "financial_expenses",
        "其中：利息费用": "interest_expense",
        "利息收入": "interest_income",
        "其他收益": "other_income",
        "加：其他收益": "other_income",
        "投资收益": "investment_income",
        "营业外收入": "non_operating_income",
        "加：营业外收入": "non_operating_income",
        "营业外支出": "non_operating_expenses",
        "减：营业外支出": "non_operating_expenses",
        "利润总额": "total_profit",
        "四、利润总额": "total_profit",
        "所得税费用": "income_tax_expense",
        "减：所得税费用": "income_tax_expense",
        "净利润": "net_profit",
        "五、净利润": "net_profit",
    }

    # 现金流量表项目映射
    CASH_FLOW_ITEM_MAP = {
        "销售商品、提供劳务收到的现金": "cash_from_sales",
        "收到其他与经营活动有关的现金": "cash_from_other_operating",
        "经营活动现金流入小计": "total_operating_cash_inflow",
        "购买商品、接受劳务支付的现金": "cash_paid_for_goods",
        "支付给职工及为职工支付的现金": "cash_paid_to_employees",
        "支付的各项税费": "cash_paid_for_taxes",
        "支付其他与经营活动有关的现金": "cash_paid_for_other_operating",
        "经营活动现金流出小计": "total_operating_cash_outflow",
        "经营活动产生的现金流量净额": "net_operating_cash_flow",
        "投资活动产生的现金流量净额": "net_investing_cash_flow",
        "筹资活动产生的现金流量净额": "net_financing_cash_flow",
        "现金及现金等价物净增加额": "net_cash_increase",
        "期初现金及现金等价物余额": "beginning_cash_balance",
        "期末现金及现金等价物余额": "ending_cash_balance",
    }

    @classmethod
    def detect_statement_type(cls, rows: List[Dict[str, Any]]) -> Optional[StatementType]:
        """检测报表类型"""
        for row in rows:
            for val in row.values():
                if val and isinstance(val, str):
                    text = val.strip()
                    if "资产负债表" in text:
                        return StatementType.BALANCE_SHEET
                    if "利润表" in text:
                        return StatementType.INCOME_STATEMENT
                    if "现金流量表" in text:
                        return StatementType.CASH_FLOW
        return None

    @classmethod
    def _get_cell_value(cls, row: Dict[str, Any], col: str) -> Optional[Any]:
        """获取单元格值，尝试多种列格式"""
        for key, val in row.items():
            if key and key.startswith(col):
                return val
        return None

    @classmethod
    def _extract_number(cls, val: Any) -> Optional[float]:
        """从单元格值中提取数字"""
        if val is None:
            return None
        if isinstance(val, (int, float)):
            return float(val)
        if isinstance(val, str):
            text = val.strip().replace(",", "")
            # Handle negative numbers with various formats
            text = text.replace("（", "(").replace("）", ")")
            text = text.replace("－", "-").replace("—", "-")
            try:
                return float(text)
            except ValueError:
                return None
        return None

    @classmethod
    def _parse_header(cls, rows: List[Dict[str, Any]]) -> Dict[str, str]:
        """解析报表头部信息"""
        info = {"company_name": "", "period": "", "currency": "CNY"}
        for row in rows[:5]:
            for val in row.values():
                if val and isinstance(val, str):
                    text = val.strip()
                    if "编制单位" in text:
                        info["company_name"] = text.replace("编制单位：", "").strip()
                    if "时间" in text:
                        info["period"] = text.replace("时间：", "").strip()
                    if "金额单位" in text or "单位：" in text:
                        info["currency"] = text.split("：")[-1].strip()
        return info

    @classmethod
    def parse_balance_sheet(cls, rows: List[Dict[str, Any]]) -> ParsedBalanceSheet:
        """解析资产负债表"""
        result = ParsedBalanceSheet()
        header_info = cls._parse_header(rows)
        result.company_name = header_info["company_name"]
        result.period = header_info["period"]
        result.currency = header_info["currency"]

        # The financial system export uses a two-column layout:
        # Left: item(A) | end_balance(B) | begin_balance(C)
        # Right: item(D) | end_balance(E) | begin_balance(F)
        # Values are numeric, not shared string references.
        for row in rows:
            for col_prefix in ["A", "D"]:
                item_col = col_prefix
                value_col = col_prefix.replace("A", "B").replace("D", "E")
                item_name = cls._get_cell_value(row, item_col)
                if item_name and isinstance(item_name, str):
                    item_name = item_name.strip()
                    attr_name = cls.BALANCE_SHEET_ITEM_MAP.get(item_name)
                    if attr_name:
                        val = cls._extract_number(cls._get_cell_value(row, value_col))
                        if val is not None:
                            # Values in export are in CNY (元), convert to 万元
                            setattr(result, attr_name, round(val / 10000, 4))

        # If total_assets is not found, compute from assets = total_current + total_non_current
        if result.total_assets == 0:
            result.total_assets = round(result.total_current_assets + result.total_non_current_assets, 4)
        if result.total_liabilities == 0:
            result.total_liabilities = round(result.total_current_liabilities + result.total_non_current_liabilities, 4)
        if result.total_equity == 0 and result.total_assets > 0 and result.total_liabilities > 0:
            result.total_equity = round(result.total_assets - result.total_liabilities, 4)

        return result

    @classmethod
    def parse_income_statement(cls, rows: List[Dict[str, Any]]) -> ParsedIncomeStatement:
        """解析利润表"""
        result = ParsedIncomeStatement()
        header_info = cls._parse_header(rows)
        result.company_name = header_info["company_name"]
        result.period = header_info["period"]
        result.currency = header_info["currency"]

        # The financial system export uses columns:
        # Left: item(A) | this_month(B) | cumulative(C) | ...
        # Right: item(F) | this_month(G) | cumulative(H) | ...
        for row in rows:
            for item_col, value_col in [("A", "C"), ("F", "H")]:
                item_name = cls._get_cell_value(row, item_col)
                if item_name and isinstance(item_name, str):
                    item_name = item_name.strip()
                    attr_name = cls.INCOME_STATEMENT_ITEM_MAP.get(item_name)
                    if attr_name:
                        val = cls._extract_number(cls._get_cell_value(row, value_col))
                        if val is not None:
                            # Values in export are in CNY (元), convert to 万元
                            setattr(result, attr_name, round(val / 10000, 4))

        return result

    @classmethod
    def parse_cash_flow(cls, rows: List[Dict[str, Any]]) -> ParsedCashFlowStatement:
        """解析现金流量表"""
        result = ParsedCashFlowStatement()
        header_info = cls._parse_header(rows)
        result.company_name = header_info["company_name"]
        result.period = header_info["period"]
        result.currency = header_info["currency"]

        for row in rows:
            for item_col, value_col in [("A", "C"), ("F", "H")]:
                item_name = cls._get_cell_value(row, item_col)
                if item_name and isinstance(item_name, str):
                    item_name = item_name.strip()
                    attr_name = cls.CASH_FLOW_ITEM_MAP.get(item_name)
                    if attr_name:
                        val = cls._extract_number(cls._get_cell_value(row, value_col))
                        if val is not None:
                            setattr(result, attr_name, round(val / 10000, 4))

        return result


# ============================================================
# 标准模板解析器
# ============================================================

class StandardTemplateParser:
    """解析标准导入模板格式的Excel文件"""

    SHEET_TYPE_MAP = {
        "资产负债表": StatementType.BALANCE_SHEET,
        "利润表": StatementType.INCOME_STATEMENT,
        "现金流量表": StatementType.CASH_FLOW,
    }

    # 标准模板字段名 -> ParsedBalanceSheet属性名
    BALANCE_SHEET_FIELDS = {
        "货币资金": "monetary_fund",
        "应收账款": "accounts_receivable",
        "预付款项": "prepayments",
        "其他应收款": "other_receivables",
        "存货": "inventory",
        "流动资产合计": "total_current_assets",
        "固定资产": "fixed_assets",
        "无形资产": "intangible_assets",
        "使用权资产": "right_of_use_assets",
        "长期待摊费用": "long_term_deferred_expenses",
        "递延所得税资产": "deferred_tax_assets",
        "非流动资产合计": "total_non_current_assets",
        "短期借款": "short_term_loans",
        "应付账款": "accounts_payable",
        "合同负债": "contract_liabilities",
        "应付职工薪酬": "employee_benefits_payable",
        "应交税费": "taxes_payable",
        "其他应付款": "other_payables",
        "流动负债合计": "total_current_liabilities",
        "租赁负债": "lease_liabilities",
        "递延所得税负债": "deferred_tax_liabilities",
        "非流动负债合计": "total_non_current_liabilities",
        "负债合计": "total_liabilities",
        "实收资本": "paid_in_capital",
        "所有者权益合计": "total_equity",
        "资产总计": "total_assets",
    }

    INCOME_STATEMENT_FIELDS = {
        "营业收入": "operating_revenue",
        "营业成本": "operating_cost",
        "税金及附加": "taxes_and_surcharges",
        "销售费用": "selling_expenses",
        "管理费用": "administrative_expenses",
        "研发费用": "rd_expenses",
        "财务费用": "financial_expenses",
        "利息费用": "interest_expense",
        "利息收入": "interest_income",
        "其他收益": "other_income",
        "投资收益": "investment_income",
        "营业外收入": "non_operating_income",
        "营业外支出": "non_operating_expenses",
        "利润总额": "total_profit",
        "所得税费用": "income_tax_expense",
        "净利润": "net_profit",
    }

    CASH_FLOW_FIELDS = {
        "销售商品提供劳务收到的现金": "cash_from_sales",
        "收到其他与经营活动有关的现金": "cash_from_other_operating",
        "经营活动现金流入小计": "total_operating_cash_inflow",
        "购买商品接受劳务支付的现金": "cash_paid_for_goods",
        "支付给职工及为职工支付的现金": "cash_paid_to_employees",
        "支付的各项税费": "cash_paid_for_taxes",
        "支付其他与经营活动有关的现金": "cash_paid_for_other_operating",
        "经营活动现金流出小计": "total_operating_cash_outflow",
        "经营活动产生的现金流量净额": "net_operating_cash_flow",
        "投资活动产生的现金流量净额": "net_investing_cash_flow",
        "筹资活动产生的现金流量净额": "net_financing_cash_flow",
        "现金及现金等价物净增加额": "net_cash_increase",
        "期初现金及现金等价物余额": "beginning_cash_balance",
        "期末现金及现金等价物余额": "ending_cash_balance",
    }

    @classmethod
    def parse(cls, file_path: str) -> Dict[str, Any]:
        """
        解析标准模板文件。

        模板格式：每个sheet包含两列：项目名称、期末金额（单位：万元）
        """
        import pandas as pd

        all_data = {}
        xls = pd.ExcelFile(file_path)

        try:
            for sheet_name in xls.sheet_names:
                df = pd.read_excel(xls, sheet_name=sheet_name, header=0)
                if df.shape[1] < 2:
                    continue

                # 行数防护：单 sheet 超限即抛错（可检测溢出，不生成巨型文件，审计台账 A06）
                _enforce_row_limit(len(df))

                # Normalize column names
                df.columns = [str(c).strip() for c in df.columns]
                item_col = df.columns[0]
                value_col = df.columns[1]

                # Drop rows where item is NaN
                df = df.dropna(subset=[item_col])

                data_dict = {}
                for _, row in df.iterrows():
                    item_name = str(row[item_col]).strip()
                    raw_val = row[value_col] if value_col in df.columns else None
                    val = cls._extract_number(raw_val)
                    if val is not None:
                        data_dict[item_name] = val

                stmt_type = cls.SHEET_TYPE_MAP.get(sheet_name)
                if stmt_type:
                    all_data[stmt_type.value] = data_dict
        finally:
            xls.close()

        return all_data

    @staticmethod
    def _extract_number(val: Any) -> Optional[float]:
        if val is None:
            return None
        try:
            return float(val)
        except (ValueError, TypeError):
            return None


# ============================================================
# 数据验证器
# ============================================================

class FinancialDataValidator:
    """财务数据验证器"""

    @classmethod
    def validate_balance_sheet(cls, data: ParsedBalanceSheet) -> List[ValidationError]:
        """验证资产负债表数据"""
        errors = []

        # 检查总额平衡关系
        if data.total_current_assets > 0 and data.total_non_current_assets > 0:
            expected_total = data.total_current_assets + data.total_non_current_assets
            if data.total_assets > 0 and abs(data.total_assets - expected_total) > 0.01:
                errors.append(ValidationError(
                    field="total_assets",
                    message=f"资产总计({data.total_assets}万元)与流动资产合计+非流动资产合计({expected_total}万元)不一致"
                ))

        if data.total_current_liabilities > 0 and data.total_non_current_liabilities > 0:
            expected_total = data.total_current_liabilities + data.total_non_current_liabilities
            if data.total_liabilities > 0 and abs(data.total_liabilities - expected_total) > 0.01:
                errors.append(ValidationError(
                    field="total_liabilities",
                    message=f"负债合计({data.total_liabilities}万元)与流动负债合计+非流动负债合计({expected_total}万元)不一致"
                ))

        # 资产负债关系检查
        if data.total_assets > 0 and data.total_liabilities > 0 and data.total_equity > 0:
            expected_equity = data.total_assets - data.total_liabilities
            if abs(data.total_equity - expected_equity) > 0.01:
                errors.append(ValidationError(
                    field="total_equity",
                    message=f"所有者权益({data.total_equity}万元)与资产总计-负债合计({expected_equity}万元)不一致",
                    severity="warning"
                ))

        # 负数检查
        negative_fields = [
            ("total_assets", "资产总计"),
            ("total_liabilities", "负债合计"),
        ]
        for attr, label in negative_fields:
            val = getattr(data, attr, 0)
            if val < 0:
                errors.append(ValidationError(
                    field=attr,
                    message=f"{label}不应为负数：{val}万元"
                ))

        # 货币资金与现金流量表一致性检查（可选）
        if data.monetary_fund < 0:
            errors.append(ValidationError(
                field="monetary_fund",
                message=f"货币资金不应为负数：{data.monetary_fund}万元",
                severity="warning"
            ))

        return errors

    @classmethod
    def validate_income_statement(cls, data: ParsedIncomeStatement) -> List[ValidationError]:
        """验证利润表数据"""
        errors = []

        # 收入合理性检查
        if data.operating_revenue < 0:
            errors.append(ValidationError(
                field="operating_revenue",
                message=f"营业收入不应为负数：{data.operating_revenue}万元"
            ))

        # 成本不应超过收入太多（超过3倍视为异常）
        if (data.operating_cost > 0 and data.operating_revenue > 0 and
                data.operating_cost > data.operating_revenue * 3):
            errors.append(ValidationError(
                field="operating_cost",
                message=f"营业成本({data.operating_cost}万元)远高于营业收入({data.operating_revenue}万元)，请核实",
                severity="warning"
            ))

        # 利润总额验算
        if (data.operating_revenue != 0 or data.operating_cost != 0 or
                data.financial_expenses != 0 or data.administrative_expenses != 0 or
                data.selling_expenses != 0):
            expected_profit = (
                data.operating_revenue - data.operating_cost - data.taxes_and_surcharges
                - data.selling_expenses - data.administrative_expenses - data.rd_expenses
                - data.financial_expenses + data.other_income + data.investment_income
                + data.non_operating_income - data.non_operating_expenses
            )
            if data.total_profit != 0 and abs(data.total_profit - expected_profit) > 1:
                errors.append(ValidationError(
                    field="total_profit",
                    message=f"利润总额({data.total_profit}万元)与明细项目计算结果({round(expected_profit, 2)}万元)差异较大，请核实",
                    severity="warning"
                ))

        return errors

    @classmethod
    def validate_cash_flow(cls, data: ParsedCashFlowStatement) -> List[ValidationError]:
        """验证现金流量表数据"""
        errors = []

        # 现金流量净额验算
        if data.total_operating_cash_inflow > 0 or data.total_operating_cash_outflow > 0:
            expected_net = data.total_operating_cash_inflow - data.total_operating_cash_outflow
            if abs(data.net_operating_cash_flow - expected_net) > 0.01:
                errors.append(ValidationError(
                    field="net_operating_cash_flow",
                    message=f"经营活动现金流量净额({data.net_operating_cash_flow}万元)与流入-流出({expected_net}万元)不一致",
                    severity="warning"
                ))

        # 期末现金余额验算
        if data.beginning_cash_balance > 0 or data.net_cash_increase != 0:
            expected_ending = data.beginning_cash_balance + data.net_cash_increase
            if data.ending_cash_balance != 0 and abs(data.ending_cash_balance - expected_ending) > 0.01:
                errors.append(ValidationError(
                    field="ending_cash_balance",
                    message=f"期末现金余额({data.ending_cash_balance}万元)与期初+净增加({expected_ending}万元)不一致",
                    severity="warning"
                ))

        return errors

    @classmethod
    def validate_all(cls, balance_sheet=None, income_statement=None, cash_flow=None) -> List[ValidationError]:
        """综合验证所有报表数据"""
        all_errors = []
        if balance_sheet:
            all_errors.extend(cls.validate_balance_sheet(balance_sheet))
        if income_statement:
            all_errors.extend(cls.validate_income_statement(income_statement))
        if cash_flow:
            all_errors.extend(cls.validate_cash_flow(cash_flow))

        # 跨表一致性检查
        if balance_sheet and income_statement:
            # 货币资金与现金流量表期末现金对比
            if balance_sheet.monetary_fund > 0 and cash_flow and cash_flow.ending_cash_balance > 0:
                diff = abs(balance_sheet.monetary_fund - cash_flow.ending_cash_balance)
                if diff > 1:  # 差异超过1万元
                    all_errors.append(ValidationError(
                        field="cash_consistency",
                        message=f"资产负债表货币资金({balance_sheet.monetary_fund}万元)与现金流量表期末现金({cash_flow.ending_cash_balance}万元)不一致",
                        severity="warning"
                    ))

        return all_errors


# ============================================================
# 数据映射器
# ============================================================

class DataMapper:
    """将解析后的财务数据映射到申报数据模型"""

    @classmethod
    def map_to_enterprise_info(cls, balance_sheet: ParsedBalanceSheet) -> Dict[str, Any]:
        """
        将资产负债表项目映射到企业基本信息。

        Returns:
            dict with keys: total_assets (万元)
        """
        return {
            "total_assets": balance_sheet.total_assets,
        }

    @classmethod
    def map_to_financial_data(cls, income_statement: ParsedIncomeStatement,
                              year: int, quarter: int) -> Dict[str, Any]:
        """
        将利润表项目映射到财务数据表。

        Returns:
            dict compatible with FinancialData model
        """
        total_cost = income_statement.operating_cost + income_statement.taxes_and_surcharges
        total_expense = (
            income_statement.selling_expenses +
            income_statement.administrative_expenses +
            income_statement.rd_expenses +
            income_statement.financial_expenses
        )

        return {
            "year": year,
            "quarter": quarter,
            "total_revenue": income_statement.operating_revenue,
            "total_cost": round(total_cost, 4),
            "total_expense": round(total_expense, 4),
            "profit": income_statement.total_profit,
            "non_taxable_income": 0.0,
            "prior_year_loss": 0.0,
        }

    @classmethod
    def map_to_tax_adjustments(cls, income_statement: ParsedIncomeStatement) -> Dict[str, Any]:
        """
        从利润表提取可用于纳税调整的项目。

        Returns:
            dict compatible with TaxAdjustment model
        """
        return {
            "business_entertainment": 0.0,  # 需要从明细中获取
            "advertising_expense": 0.0,     # 包含在销售费用中
            "donation_expense": 0.0,        # 包含在营业外支出中
            "rnd_expense": income_statement.rd_expenses,
            "welfare_expense": 0.0,         # 包含在管理费用中
            "education_expense": 0.0,       # 包含在管理费用中
            "union_expense": 0.0,           # 包含在管理费用中
            "penalties": 0.0,               # 包含在营业外支出中
            "equipment_purchase": 0.0,
        }

    @classmethod
    def calculate_taxable_income_estimate(cls, income_statement: ParsedIncomeStatement,
                                          adjustments: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        估算应纳税所得额。

        公式：利润总额 + 纳税调增 - 纳税调减 - 不征税收入 - 免税收入 - 弥补亏损

        Returns:
            dict with estimated taxable income and breakdown
        """
        accounting_profit = income_statement.total_profit

        # 默认调增项（基于常见项目估算）
        tax_adjustment_increase = 0.0
        if adjustments:
            tax_adjustment_increase += adjustments.get("business_entertainment", 0) * 0.4  # 招待费约40%调增
            tax_adjustment_increase += adjustments.get("penalties", 0)  # 罚款全额调增
            tax_adjustment_increase += adjustments.get("donation_expense", 0)  # 捐赠可能超限

        # 默认调减项
        tax_adjustment_decrease = 0.0
        if adjustments:
            tax_adjustment_decrease += adjustments.get("rnd_expense", 0)  # 研发费用100%加计扣除
            tax_adjustment_decrease += adjustments.get("equipment_purchase", 0)  # 设备一次性扣除

        taxable_income_before_loss = accounting_profit + tax_adjustment_increase - tax_adjustment_decrease

        return {
            "accounting_profit": accounting_profit,
            "tax_adjustment_increase": round(tax_adjustment_increase, 4),
            "tax_adjustment_decrease": round(tax_adjustment_decrease, 4),
            "taxable_income_before_loss": round(taxable_income_before_loss, 4),
            "taxable_income_after_loss": round(max(0, taxable_income_before_loss), 4),
        }


# ============================================================
# 模板生成器
# ============================================================

class TemplateGenerator:
    """生成标准导入模板Excel文件"""

    # 表头样式常量
    HEADER_FONT = Font(name="微软雅黑", size=11, bold=True, color="FFFFFF")
    HEADER_FILL = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
    HEADER_ALIGNMENT = Alignment(horizontal="center", vertical="center", wrap_text=True)
    CELL_ALIGNMENT = Alignment(horizontal="left", vertical="center", wrap_text=True)
    NUMBER_ALIGNMENT = Alignment(horizontal="right", vertical="center")
    THIN_BORDER = Border(
        left=Side(style="thin"),
        right=Side(style="thin"),
        top=Side(style="thin"),
        bottom=Side(style="thin"),
    )
    NOTE_FONT = Font(name="微软雅黑", size=9, color="FF0000")

    @classmethod
    def generate_template(cls, include_sheets: Optional[List[StatementType]] = None) -> bytes:
        """
        生成标准导入模板Excel文件，返回字节流。

        Args:
            include_sheets: 要包含的报表类型列表，默认为全部
        """
        if include_sheets is None:
            include_sheets = [StatementType.BALANCE_SHEET, StatementType.INCOME_STATEMENT, StatementType.CASH_FLOW]

        wb = Workbook()

        # Remove default sheet
        default_sheet = wb.active
        if default_sheet is not None:
            wb.remove(default_sheet)

        if StatementType.BALANCE_SHEET in include_sheets:
            cls._create_balance_sheet_template(wb)
        if StatementType.INCOME_STATEMENT in include_sheets:
            cls._create_income_statement_template(wb)
        if StatementType.CASH_FLOW in include_sheets:
            cls._create_cash_flow_template(wb)

        output = io.BytesIO()
        wb.save(output)
        output.seek(0)
        return output.getvalue()

    @classmethod
    def _apply_header_style(cls, ws, row: int, cols: int):
        """为表头行应用样式"""
        for col in range(1, cols + 1):
            cell = ws.cell(row=row, column=col)
            cell.font = cls.HEADER_FONT
            cell.fill = cls.HEADER_FILL
            cell.alignment = cls.HEADER_ALIGNMENT
            cell.border = cls.THIN_BORDER

    @classmethod
    def _apply_data_style(cls, ws, start_row: int, end_row: int, col: int, is_number: bool = False):
        """为数据区域应用样式"""
        for row in range(start_row, end_row + 1):
            cell = ws.cell(row=row, column=col)
            cell.border = cls.THIN_BORDER
            cell.alignment = cls.CELL_ALIGNMENT if not is_number else cls.NUMBER_ALIGNMENT
            if is_number:
                cell.number_format = "#,##0.0000"

    @classmethod
    def _create_balance_sheet_template(cls, wb: Workbook):
        """创建资产负债表模板"""
        ws = wb.create_sheet("资产负债表")

        # 说明行
        ws.merge_cells("A1:C1")
        ws["A1"] = "资产负债表导入模板（金额单位：万元，保留4位小数）"
        ws["A1"].font = Font(name="微软雅黑", size=12, bold=True, color="4472C4")

        ws.merge_cells("A2:C2")
        ws["A2"] = "填写说明：请在B列填写各项目的期末金额（单位：万元），不要修改A列的项目名称。"
        ws["A2"].font = cls.NOTE_FONT

        # 表头
        ws["A3"] = "项目名称"
        ws["B3"] = "期末金额（万元）"
        cls._apply_header_style(ws, 3, 2)

        # 数据行
        items = [
            "货币资金", "应收账款", "预付款项", "其他应收款", "存货",
            "流动资产合计",
            "固定资产", "无形资产", "使用权资产", "长期待摊费用", "递延所得税资产",
            "非流动资产合计", "资产总计",
            "短期借款", "应付账款", "合同负债", "应付职工薪酬", "应交税费", "其他应付款",
            "流动负债合计",
            "租赁负债", "递延所得税负债",
            "非流动负债合计", "负债合计",
            "实收资本", "所有者权益合计",
        ]

        for i, item in enumerate(items):
            row = 4 + i
            ws.cell(row=row, column=1, value=item)
            ws.cell(row=row, column=2, value=None)

        end_row = 4 + len(items) - 1
        cls._apply_data_style(ws, 4, end_row, 1)
        cls._apply_data_style(ws, 4, end_row, 2, is_number=True)

        # 设置列宽
        ws.column_dimensions["A"].width = 22
        ws.column_dimensions["B"].width = 22

    @classmethod
    def _create_income_statement_template(cls, wb: Workbook):
        """创建利润表模板"""
        ws = wb.create_sheet("利润表")

        ws.merge_cells("A1:C1")
        ws["A1"] = "利润表导入模板（金额单位：万元，保留4位小数）"
        ws["A1"].font = Font(name="微软雅黑", size=12, bold=True, color="4472C4")

        ws.merge_cells("A2:C2")
        ws["A2"] = "填写说明：请在B列填写各项目的本期累计金额（单位：万元），不要修改A列的项目名称。"
        ws["A2"].font = cls.NOTE_FONT

        ws["A3"] = "项目名称"
        ws["B3"] = "本期金额（万元）"
        cls._apply_header_style(ws, 3, 2)

        items = [
            "营业收入", "营业成本", "税金及附加",
            "销售费用", "管理费用", "研发费用", "财务费用",
            "利息费用", "利息收入",
            "其他收益", "投资收益",
            "营业外收入", "营业外支出",
            "利润总额", "所得税费用", "净利润",
        ]

        for i, item in enumerate(items):
            row = 4 + i
            ws.cell(row=row, column=1, value=item)
            ws.cell(row=row, column=2, value=None)

        end_row = 4 + len(items) - 1
        cls._apply_data_style(ws, 4, end_row, 1)
        cls._apply_data_style(ws, 4, end_row, 2, is_number=True)

        ws.column_dimensions["A"].width = 22
        ws.column_dimensions["B"].width = 22

    @classmethod
    def _create_cash_flow_template(cls, wb: Workbook):
        """创建现金流量表模板"""
        ws = wb.create_sheet("现金流量表")

        ws.merge_cells("A1:C1")
        ws["A1"] = "现金流量表导入模板（金额单位：万元，保留4位小数）"
        ws["A1"].font = Font(name="微软雅黑", size=12, bold=True, color="4472C4")

        ws.merge_cells("A2:C2")
        ws["A2"] = "填写说明：请在B列填写各项目的本期金额（单位：万元），不要修改A列的项目名称。"
        ws["A2"].font = cls.NOTE_FONT

        ws["A3"] = "项目名称"
        ws["B3"] = "本期金额（万元）"
        cls._apply_header_style(ws, 3, 2)

        items = [
            "销售商品提供劳务收到的现金",
            "收到其他与经营活动有关的现金",
            "经营活动现金流入小计",
            "购买商品接受劳务支付的现金",
            "支付给职工及为职工支付的现金",
            "支付的各项税费",
            "支付其他与经营活动有关的现金",
            "经营活动现金流出小计",
            "经营活动产生的现金流量净额",
            "投资活动产生的现金流量净额",
            "筹资活动产生的现金流量净额",
            "现金及现金等价物净增加额",
            "期初现金及现金等价物余额",
            "期末现金及现金等价物余额",
        ]

        for i, item in enumerate(items):
            row = 4 + i
            ws.cell(row=row, column=1, value=item)
            ws.cell(row=row, column=2, value=None)

        end_row = 4 + len(items) - 1
        cls._apply_data_style(ws, 4, end_row, 1)
        cls._apply_data_style(ws, 4, end_row, 2, is_number=True)

        ws.column_dimensions["A"].width = 30
        ws.column_dimensions["B"].width = 22


# ============================================================
# 主导入器
# ============================================================

class FinancialDataImporter:
    """
    财务数据导入器主类。

    支持两种格式：
    1. 标准模板格式：用户下载模板后填写的Excel文件
    2. 财务系统导出格式：直接从财务系统导出的Excel文件
    """

    def __init__(self):
        self.parser = FinancialSystemExportParser()
        self.validator = FinancialDataValidator()
        self.mapper = DataMapper()

    def import_from_file(self, file_path: str) -> ImportResult:
        """
        从文件导入财务数据。

        自动检测文件格式并选择合适的解析器。

        Args:
            file_path: Excel文件路径

        Returns:
            ImportResult containing parsed data, errors and warnings
        """
        result = ImportResult(success=False)

        try:
            # Step 1: Try to detect and parse
            parsed_data = self._auto_detect_and_parse(file_path)
        except ImportRowLimitExceeded as exc:
            # 行数超限：明确错误、无半成品数据（解析阶段已中止）
            result.errors.append(ValidationError(field="file", message=str(exc)))
            return result
        except Exception:
            # 服务器保留完整异常日志；对外只给通用中文提示，不回带本机异常路径（审计台账 A09）
            logger.exception("财务报表解析失败：%s", file_path)
            result.errors.append(ValidationError(
                field="file",
                message="文件解析失败，请检查文件格式或稍后重试"
            ))
            return result

        if not parsed_data:
            result.errors.append(ValidationError(
                field="file",
                message="无法识别的报表格式或内容"
            ))
            return result

        result.success = True
        result.mapped_data = parsed_data

        # Step 2: Validate
        balance_sheet = parsed_data.get("balance_sheet")
        income_statement = parsed_data.get("income_statement")
        cash_flow = parsed_data.get("cash_flow")

        validation_errors = self.validator.validate_all(
            balance_sheet=balance_sheet,
            income_statement=income_statement,
            cash_flow=cash_flow,
        )

        for err in validation_errors:
            if err.severity == "warning":
                result.warnings.append(err)
            else:
                result.errors.append(err)

        # Step 3: Map to declaration data
        if balance_sheet:
            result.mapped_data["enterprise_info"] = self.mapper.map_to_enterprise_info(balance_sheet)

        if income_statement:
            # Default: parse year/quarter from period string
            year, quarter = self._parse_period(income_statement.period or "")
            result.mapped_data["financial_data"] = self.mapper.map_to_financial_data(
                income_statement, year, quarter
            )
            result.mapped_data["tax_adjustments"] = self.mapper.map_to_tax_adjustments(income_statement)
            result.mapped_data["taxable_income_estimate"] = self.mapper.calculate_taxable_income_estimate(
                income_statement, result.mapped_data.get("tax_adjustments")
            )

        # Generate summary
        result.summary = self._generate_summary(result)
        return result

    def _auto_detect_and_parse(self, file_path: str) -> Dict[str, Any]:
        """
        自动检测文件格式并解析。

        检测逻辑：
        1. 尝试用pandas读取（标准模板）
        2. 如果失败，使用XML解析器读取（财务系统导出）
        """
        data = {}

        # Try standard template first
        try:
            standard_data = StandardTemplateParser.parse(file_path)
            if standard_data:
                data["format"] = ImportFormat.STANDARD_TEMPLATE.value
                if StatementType.BALANCE_SHEET.value in standard_data:
                    data["balance_sheet"] = self._dict_to_balance_sheet(
                        standard_data[StatementType.BALANCE_SHEET.value]
                    )
                if StatementType.INCOME_STATEMENT.value in standard_data:
                    data["income_statement"] = self._dict_to_income_statement(
                        standard_data[StatementType.INCOME_STATEMENT.value]
                    )
                if StatementType.CASH_FLOW.value in standard_data:
                    data["cash_flow"] = self._dict_to_cash_flow(
                        standard_data[StatementType.CASH_FLOW.value]
                    )
                if data.get("balance_sheet") or data.get("income_statement") or data.get("cash_flow"):
                    return data
        except ImportRowLimitExceeded:
            raise  # 行数超限必须透出，不得被「尝试下一种解析器」吞掉
        except Exception:
            pass

        # Fallback: try financial system export parser
        try:
            _, rows = FinancialSystemXmlParser.parse(file_path)
            if not rows:
                return {}

            stmt_type = self.parser.detect_statement_type(rows)
            if stmt_type == StatementType.BALANCE_SHEET:
                data["balance_sheet"] = self.parser.parse_balance_sheet(rows)
                data["format"] = ImportFormat.FINANCIAL_SYSTEM_EXPORT.value
            elif stmt_type == StatementType.INCOME_STATEMENT:
                data["income_statement"] = self.parser.parse_income_statement(rows)
                data["format"] = ImportFormat.FINANCIAL_SYSTEM_EXPORT.value
            elif stmt_type == StatementType.CASH_FLOW:
                data["cash_flow"] = self.parser.parse_cash_flow(rows)
                data["format"] = ImportFormat.FINANCIAL_SYSTEM_EXPORT.value
            else:
                # Try all parsers
                data["balance_sheet"] = self.parser.parse_balance_sheet(rows)
                data["income_statement"] = self.parser.parse_income_statement(rows)
                data["cash_flow"] = self.parser.parse_cash_flow(rows)
                data["format"] = ImportFormat.FINANCIAL_SYSTEM_EXPORT.value
        except ImportRowLimitExceeded:
            raise  # 行数超限必须透出，不得被「尝试下一种解析器」吞掉
        except Exception:
            pass

        return data

    def _dict_to_balance_sheet(self, d: Dict[str, float]) -> ParsedBalanceSheet:
        """将字典转换为ParsedBalanceSheet对象"""
        bs = ParsedBalanceSheet()
        for key, attr_name in StandardTemplateParser.BALANCE_SHEET_FIELDS.items():
            if key in d:
                setattr(bs, attr_name, d[key])
        # Compute totals if not present
        if bs.total_assets == 0 and bs.total_current_assets > 0:
            bs.total_assets = round(bs.total_current_assets + bs.total_non_current_assets, 4)
        if bs.total_liabilities == 0 and bs.total_current_liabilities > 0:
            bs.total_liabilities = round(bs.total_current_liabilities + bs.total_non_current_liabilities, 4)
        return bs

    def _dict_to_income_statement(self, d: Dict[str, float]) -> ParsedIncomeStatement:
        """将字典转换为ParsedIncomeStatement对象"""
        is_ = ParsedIncomeStatement()
        for key, attr_name in StandardTemplateParser.INCOME_STATEMENT_FIELDS.items():
            if key in d:
                setattr(is_, attr_name, d[key])
        return is_

    def _dict_to_cash_flow(self, d: Dict[str, float]) -> ParsedCashFlowStatement:
        """将字典转换为ParsedCashFlowStatement对象"""
        cf = ParsedCashFlowStatement()
        for key, attr_name in StandardTemplateParser.CASH_FLOW_FIELDS.items():
            if key in d:
                setattr(cf, attr_name, d[key])
        return cf

    @staticmethod
    def _parse_period(period_str: str) -> Tuple[int, int]:
        """
        从期间字符串中解析年份和季度。

        支持格式：
        - "2026年1季度"
        - "2026年Q1"
        - "2026-03"
        - "2026年3月"

        Returns:
            (year, quarter)
        """
        if not period_str:
            now = datetime.now()
            return now.year, (now.month - 1) // 3 + 1

        # 2026年1季度
        m = re.search(r"(\d{4})[年](\d+)季度", period_str)
        if m:
            return int(m.group(1)), int(m.group(2))

        # 2026年Q1
        m = re.search(r"(\d{4})[年]Q(\d+)", period_str)
        if m:
            return int(m.group(1)), int(m.group(2))

        # 2026-03
        m = re.search(r"(\d{4})-(\d{2})", period_str)
        if m:
            month = int(m.group(2))
            return int(m.group(1)), (month - 1) // 3 + 1

        # 2026年3月
        m = re.search(r"(\d{4})[年](\d+)月", period_str)
        if m:
            month = int(m.group(2))
            return int(m.group(1)), (month - 1) // 3 + 1

        # Default: try to extract 4-digit year
        m = re.search(r"(\d{4})", period_str)
        if m:
            return int(m.group(1)), 1

        now = datetime.now()
        return now.year, (now.month - 1) // 3 + 1

    @staticmethod
    def _generate_summary(result: ImportResult) -> str:
        """生成导入结果摘要"""
        parts = []

        if result.mapped_data.get("balance_sheet"):
            bs = result.mapped_data["balance_sheet"]
            parts.append(f"资产负债表：资产总计{bs.total_assets}万元，负债合计{bs.total_liabilities}万元")

        if result.mapped_data.get("income_statement"):
            inc = result.mapped_data["income_statement"]
            parts.append(f"利润表：营业收入{inc.operating_revenue}万元，利润总额{inc.total_profit}万元")

        if result.mapped_data.get("cash_flow"):
            cf = result.mapped_data["cash_flow"]
            parts.append(f"现金流量表：经营活动净现金流{cf.net_operating_cash_flow}万元")

        if result.mapped_data.get("taxable_income_estimate"):
            tax = result.mapped_data["taxable_income_estimate"]
            parts.append(f"估算应纳税所得额：{tax['taxable_income_after_loss']}万元")

        if result.errors:
            parts.append(f"错误：{len(result.errors)}项")
        if result.warnings:
            parts.append(f"警告：{len(result.warnings)}项")

        return "；".join(parts) if parts else "导入完成"

    @staticmethod
    def generate_template(include_sheets: Optional[List[StatementType]] = None) -> bytes:
        """生成标准导入模板Excel文件"""
        return TemplateGenerator.generate_template(include_sheets)
