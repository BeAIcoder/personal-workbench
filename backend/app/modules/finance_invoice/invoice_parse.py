"""finance_invoice 解析层：引擎对接、字段解析、金额/日期/文件名口径与明细归一。

职责：
- PDF 文本提取与字段正则解析直挂发票工具引擎（engine.py）；
- 销方名称补充提取：引擎只解析购方，销方由本模块正则补齐；
- 金额 / 日期 / 文件名的处理规则镜像引擎 invoice_processor/src/utils.py（两处口径一致）；
- 明细归属归一（空项目名 → 「未归类」）与项目清单 / 项目 PDF 的文件名构造。

不含：批次落盘与状态机（invoice_batch）、去重与冲红配对（invoice_pairing）、
项目归类（invoice_classify）、导出打包（invoice_export）。
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Optional

from . import engine as engine_mod
from . import projects as project_rules
from .models import InvoiceItem

logger = logging.getLogger(__name__)

# 销方名称补充提取（引擎仅解析购方）：匹配清洁后文本中的「销 名称：xxx」等写法
_SELLER_PATTERNS = [
    re.compile(r"销\s+名称[：:]\s*([^\s]{2,30})"),
    re.compile(r"销\s*售\s*方[^\n\r]{0,20}?名称[：:]\s*([^\s]{2,30})"),
    re.compile(r"销售方[^\n\r]{0,20}?名称[：:]\s*([^\s]{2,30})"),
    re.compile(r"卖\s*方[^\n\r]{0,20}?名称[：:]\s*([^\s]{2,30})"),
]
_SELLER_STOP_WORDS = ("发票", "代码", "号码", "校验", "金额", "合计", "人民币", "税率", "税额")

# 金额 / 日期 / 文件名的处理规则镜像引擎 invoice_processor/src/utils.py（保持两处口径一致）
_YM_PATTERNS = [
    re.compile(r"(\d{4})年(\d{1,2})月\d{1,2}日"),
    re.compile(r"(\d{4})-(\d{1,2})-\d{1,2}"),
    re.compile(r"(\d{4})\.(\d{1,2})\.\d{1,2}"),
    re.compile(r"(\d{4})/(\d{1,2})/\d{1,2}"),
]
_ILLEGAL_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1F]')
# 同引擎口径：保留中英文、数字、连字符与下划线（一-鿿 即 CJK 统一表意区间 4E00-9FFF）
_NON_WORD_CHARS = re.compile(r"[^\w一-鿿\-_]")
_AMOUNT_NUMBER_FORMAT = "#,##0.00"

# 可参与导出 / 冲红配对的明细状态
_ACTIVE_ITEM_STATUS = (InvoiceItem.STATUS_PARSED, InvoiceItem.STATUS_INCOMPLETE)


@dataclass
class ParsedInvoice:
    """单张发票的解析结果（引擎 InvoiceInfo 的模块内表示 + 销方名称）。"""

    invoice_number: str = ""
    check_code: str = ""
    buyer_name: str = ""
    seller_name: str = ""
    amount: str = ""
    tax_amount: str = ""
    tax_rate: str = ""
    invoice_date: str = ""
    invoice_type: str = ""
    invoice_direction: str = ""
    project_name: str = ""
    brand_name: str = ""
    remark: str = ""
    extraction_success: bool = True
    raw_text: str = ""  # 清洁后全文（仅内存内用于项目归类，不落库）


# ---------------- 纯函数工具（镜像引擎口径） ----------------

_MONEY = Decimal("0.01")


def _money(value) -> float:
    """金额统一 Decimal ROUND_HALF_UP 两位（避免 round() 银行家舍入与二进制误差）。"""
    number = Decimal(str(value))
    if not number.is_finite():
        raise ValueError("金额不是有限数值")
    return float(number.quantize(_MONEY, rounding=ROUND_HALF_UP))


def parse_amount(amount_str: Optional[str]) -> float:
    """金额原文 → float（两位 HALF_UP）；支持千分位逗号、货币符号、负数（冲红），失败返回 0.0。"""
    value = _parse_amount_strict(amount_str)
    return 0.0 if value is None else value


def _parse_amount_strict(amount_str: Optional[str]) -> Optional[float]:
    """金额原文 → float（两位 HALF_UP）；空值/不可解析/非有限值返回 None（不得把失败当 0）。"""
    if not amount_str:
        return None
    cleaned = str(amount_str).replace(",", "").replace("¥", "").replace("￥", "")
    cleaned = cleaned.replace(" ", "").strip()
    if not cleaned:
        return None
    try:
        number = Decimal(cleaned)
    except (InvalidOperation, ValueError, TypeError):
        return None
    if not number.is_finite():
        return None
    return float(number.quantize(_MONEY, rounding=ROUND_HALF_UP))


def sanitize_filename(name: str) -> str:
    """剔除文件名非法字符（同引擎 sanitize_filename）。"""
    name = _ILLEGAL_FILENAME_CHARS.sub("", name or "").strip()
    return name or "未命名"


def _date_parts(date_str: str) -> Optional[tuple[int, int]]:
    """开票日期 → (年, 月)；无法解析返回 None。"""
    if not date_str:
        return None
    for pattern in _YM_PATTERNS:
        match = pattern.search(date_str)
        if match:
            try:
                return int(match.group(1)), int(match.group(2))
            except (TypeError, ValueError):
                return None
    return None


def parse_date_to_year_month(date_str: str) -> str:
    """开票日期 → 「yy年M月」（同引擎 parse_date_to_year_month，用于导出目录命名）。"""
    parts = _date_parts(date_str)
    if parts is None:
        return ""
    year, month = parts
    return f"{year % 100}年{month}月"


def parse_date_to_year_month_long(date_str: str) -> str:
    """开票日期 → 「yyyy年M月」（下载文件名用，避免跨世纪歧义）。"""
    parts = _date_parts(date_str)
    if parts is None:
        return ""
    year, month = parts
    return f"{year}年{month}月"


def _months_suffix(months: list[tuple[int, int]]) -> str:
    """月份集合 → 文件名后缀：单月「2026年10月」，同/跨年多个月「2026年10-11月」「2026年12月-2027年1月」。"""
    if not months:
        return ""
    first, last = months[0], months[-1]
    if len(months) == 1:
        return f"{first[0]}年{first[1]}月"
    if first[0] == last[0]:
        return f"{first[0]}年{first[1]}-{last[1]}月"
    return f"{first[0]}年{first[1]}月-{last[0]}年{last[1]}月"


def extract_seller_name(text: str) -> str:
    """从清洁后的发票文本补充提取销方名称（引擎只解析购方）；失败返回空串。"""
    for pattern in _SELLER_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        name = re.sub(r"\s+", "", match.group(1)).strip(" :：")
        if 1 < len(name) <= 30 and not any(kw in name for kw in _SELLER_STOP_WORDS):
            return name
    return ""


def dedup_key(invoice_number: str, check_code: str) -> str:
    """去重幂等键：发票号码 + 校验码（同引擎 FileHandler.deduplicate_invoices）；无有效号码返回空串。"""
    number = (invoice_number or "").strip()
    if not number or number == "未知":
        return ""
    code = (check_code or "").strip()
    return f"{number}|{code}" if code else f"{number}|"


def build_renamed_filename(item: InvoiceItem) -> str:
    """引擎 FileHandler.rename_invoice_file 同款命名：{购方}{品牌}{金额}{类型}.pdf，超长截断。"""
    buyer = _NON_WORD_CHARS.sub("", sanitize_filename((item.buyer_name or "")[:15]))
    brand = _NON_WORD_CHARS.sub("", sanitize_filename(item.brand_name or ""))[:10] if item.brand_name else ""
    amount = re.sub(r"[^\d.]", "", (item.amount or "").replace(",", "")) or "0"
    inv_type = item.invoice_type or ""
    if brand:
        name = f"{buyer}{brand}{amount}{inv_type}.pdf"
    else:
        name = f"{buyer}{amount}{inv_type}.pdf"
    if len(name) > 100:
        if brand:
            name = f"{buyer[:8]}{brand[:8]}{amount}{inv_type}.pdf"
        else:
            name = f"{buyer[:10]}{amount}{inv_type}.pdf"
    return name


def _item_project_name(item: InvoiceItem) -> str:
    """明细所属项目（空项目名归一为「未归类」，与 project_summary 同口径）。"""
    return (item.project_name or "").strip() or project_rules.UNCLASSIFIED


def _item_months(items: list[InvoiceItem]) -> list[tuple[int, int]]:
    return sorted({p for p in (_date_parts(item.invoice_date) for item in items) if p})


def _project_safe_name(project: str) -> str:
    """项目名 → 目录/文件名安全形式（同引擎清洗口径：保留中英文数字连字符下划线，截断 30）。"""
    return _NON_WORD_CHARS.sub("", sanitize_filename(project or ""))[:30] or project_rules.UNCLASSIFIED


def project_pdf_name(project: str, items: list[InvoiceItem]) -> str:
    """项目 PDF 文件名：「项目名2026年10月发票.pdf」（跨月用月份区间，与项目 ZIP 同口径）。"""
    return f"{_project_safe_name(project)}{_months_suffix(_item_months(items))}发票.pdf"


# ---------------- 引擎解析 ----------------

def _require_engine() -> None:
    """批次创建前置检查：引擎不可用抛 EngineUnavailableError（路由层转 503）。"""
    engine_mod.get_engine()


def parse_invoice_text(processor, text: str) -> ParsedInvoice:
    """对已提取的 PDF 文本做字段解析：引擎正则 + 本模块销方补充。processor 为引擎 PDFInvoiceProcessor。"""
    cleaned = processor._clean_text(text)
    info = processor._parse_invoice_info(cleaned)
    return ParsedInvoice(
        invoice_number=(info.invoice_number or "")[:50],
        check_code=(info.check_code or "")[:50],
        buyer_name=(info.buyer_name or "")[:200],
        seller_name=extract_seller_name(cleaned)[:200],
        amount=(info.amount or "")[:50],
        tax_amount=(info.tax_amount or "")[:50],
        tax_rate=(info.tax_rate or "")[:20],
        invoice_date=(info.invoice_date or "")[:30],
        invoice_type=(info.invoice_type or "")[:10],
        invoice_direction=(info.invoice_direction or "")[:10],
        project_name=(info.project_name or "")[:100],
        brand_name=(info.brand_name or "")[:50],
        remark=(info.remarks or "")[:500],
        extraction_success=bool(info.extraction_success),
        raw_text=cleaned,
    )


def _parse_pdf_with_engine(pdf_path: Path) -> Optional[ParsedInvoice]:
    """用引擎解析单个 PDF；解析失败返回 None（不抛异常，单文件失败不拖垮批次）。"""
    handle = engine_mod.get_engine()
    processor = handle.processor_cls(handle.config)
    try:
        text = processor._extract_text_once(str(pdf_path))
    except Exception as exc:
        logger.warning("PDF 文本提取失败 %s：%s", pdf_path, exc)
        return None
    if not text.strip():
        logger.warning("PDF 文本为空：%s", pdf_path)
        return None
    cleaned = processor._clean_text(text)
    if not cleaned.strip():
        return None
    try:
        info = processor._parse_invoice_info(cleaned)
    except Exception as exc:
        logger.warning("发票字段解析失败 %s：%s", pdf_path, exc)
        return None
    return ParsedInvoice(
        invoice_number=(info.invoice_number or "")[:50],
        check_code=(info.check_code or "")[:50],
        buyer_name=(info.buyer_name or "")[:200],
        seller_name=extract_seller_name(cleaned)[:200],
        amount=(info.amount or "")[:50],
        tax_amount=(info.tax_amount or "")[:50],
        tax_rate=(info.tax_rate or "")[:20],
        invoice_date=(info.invoice_date or "")[:30],
        invoice_type=(info.invoice_type or "")[:10],
        invoice_direction=(info.invoice_direction or "")[:10],
        project_name=(info.project_name or "")[:100],
        brand_name=(info.brand_name or "")[:50],
        remark=(info.remarks or "")[:500],
        extraction_success=bool(info.extraction_success),
        raw_text=cleaned,
    )


def _pdf_text(item: InvoiceItem) -> str:
    """重新归类时按需补取 PDF 全文（仅在项目名/备注都不足以判断时调用）。"""
    path = Path(item.stored_path or "")
    if not path.is_file():
        return ""
    try:
        handle = engine_mod.get_engine()
        processor = handle.processor_cls(handle.config)
        return processor._clean_text(processor._extract_text_once(str(path))) or ""
    except Exception as exc:  # 引擎异常/PDF 损坏都不影响归类主流程
        logger.warning("补取发票全文失败 %s：%s", path.name, exc)
        return ""
