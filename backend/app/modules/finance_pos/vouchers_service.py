# -*- coding: utf-8 -*-
"""finance_pos 刷卡凭证服务：银行回单 × 刷卡对账单 匹配合并生成凭证 PDF。

数据流（移植自本机归档里已验证的「回单对账合并」本地管线）：
1. 扫描月度目录（finance_pos_statement_dir/{YYYY年}/{MM}）：
   - SHOP.*.zip / SHOP.*.xlsx —— 建行 SHOP 日对账单
   - 云南*回单*.pdf —— 银行回单 PDF（多张回单叠放一页，按「单位客户专用回单」头分块）
2. 解析对账单 xlsx：按终端提取每日小计（交易金额/手续费/划账金额/笔数）
3. parse_receipts（pypdfium2 文本层）：仅保留用途含「收单清算款」的 POS 清算回单，
   按 (文件, 回单号) 去重（兼容双层文本 PDF），记录 Y 边界供裁切
4. LibreOffice 无头转换：对每个（日×terminal）对账单节打 A4 横向 fitToWidth XML 补丁后转 PDF
5. 双键匹配：报表日期 ≤ 回单清算日 ≤ 报表日期+2 且 金额命中优先、日期最近
6. 生成凭证：
   - 每笔交易凭证 = {当日该终端对账单页} + {裁切回单页}
   - 月度手续费凭证 = {凭证首页（当月逐日逐终端汇总表）} + {当月全部对账单页}
"""
from __future__ import annotations

import io
import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

import openpyxl
from sqlalchemy.orm import Session

from ...config import get_finance_data_dir, settings
from ...database import SessionLocal
from .models import PosVoucherBatch

logger = logging.getLogger(__name__)

# ── 常量与正则 ──────────────────────────────────────────────────────────

# 月度目录布局：{基目录}/{YYYY年}/{MM}
MONTH_PATTERN = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
# SHOP 文件日期（SHOP.105731065130066.20260908.zip → 202608
SHOP_DATE_PATTERN = re.compile(r"\.(\d{8})\.zip$", re.IGNORECASE)
SHOP_XLSX_DATE_PATTERN = re.compile(r"\.(\d{8})\.xlsx$", re.IGNORECASE)
# 回单 PDF 文件通配（默认值，可被 finance_pos_receipt_glob 覆盖）
DEFAULT_RECEIPT_GLOBS = ("云南*回单*.pdf",)
# 匹配参数默认值（与本地已验证管线一致，可被 finance_pos_match_* 覆盖）
DEFAULT_DATE_TOLERANCE_DAYS = 2
DEFAULT_AMOUNT_TOLERANCE = Decimal("0.01")

# 终端尾号 → 项目名（内置默认，可被 finance_pos_project_map 覆盖）
DEFAULT_PROJECT_BY_POS_CODE = {
    "0002": "金阶",
    "0005": "园城",
    "0006": "脱敏街区",
}


def project_map() -> dict[str, str]:
    """终端尾号→项目名：finance_pos_project_map（"0002=金阶,…"）优先，空则内置默认。"""
    raw = (settings.finance_pos_project_map or "").strip()
    if not raw:
        return dict(DEFAULT_PROJECT_BY_POS_CODE)
    result: dict[str, str] = {}
    for part in raw.split(","):
        part = part.strip()
        if "=" not in part:
            continue
        code, name = part.split("=", 1)
        if re.fullmatch(r"\d{4}", code.strip()) and name.strip():
            result[code.strip()] = name.strip()
    return result or dict(DEFAULT_PROJECT_BY_POS_CODE)


def receipt_globs() -> tuple[str, ...]:
    """回单文件名通配：finance_pos_receipt_glob（逗号分隔）优先，空则内置默认。"""
    raw = (settings.finance_pos_receipt_glob or "").strip()
    if not raw:
        return DEFAULT_RECEIPT_GLOBS
    globs = tuple(g.strip() for g in raw.split(",") if g.strip())
    return globs or DEFAULT_RECEIPT_GLOBS


def date_tolerance_days() -> int:
    v = settings.finance_pos_match_date_tolerance
    return DEFAULT_DATE_TOLERANCE_DAYS if v is None or int(v) < 0 else int(v)


def amount_tolerance() -> Decimal:
    try:
        v = Decimal(str(settings.finance_pos_match_amount_tolerance or "").strip() or "0.01")
    except InvalidOperation:
        return DEFAULT_AMOUNT_TOLERANCE
    return v if v >= 0 else DEFAULT_AMOUNT_TOLERANCE


MIN_REPORT_DATE_TOLERANCE_DAYS = 10


def report_date_tolerance_days() -> int:
    """报表日期相对交易日允许的最大滞后（天）。

    长假（国庆/春节）后 SHOP 报表可能延后到节后工作日：09-30 交易、10-09 报表
    是合法样本（滞后 9 天），固定 3 天会让整月生成 400。阈值由既有
    ``finance_pos_match_date_tolerance`` 派生并设下限 10，不新增配置项。
    """
    return max(MIN_REPORT_DATE_TOLERANCE_DAYS, date_tolerance_days() + 7)

# 回单文本层分块/字段正则（建行「中国建设银行单位客户专用回单」）
RECEIPT_HEADER = "单位客户专用回单"
POS_PURPOSE_KEYWORD = "收单清算款"
RECEIPT_NO_PATTERN = re.compile(r"No[：:](\S+)")
DATE_CN_PATTERN = re.compile(r"(\d{4})年(\d{2})月(\d{2})日")
GROSS_PATTERN = re.compile(r"交易金额[：:]\s*￥?\s*([\d,]+\.\d{2})")
FEE_PATTERN = re.compile(r"手续费[：:]\s*￥?\s*([\d,]+\.\d{2})")
NET_PATTERN = re.compile(r"交易净计金额[:\uff1a]\s*￥?\s*([\d,]+\.\d{2})")
# 用途行：收单清算款105731065130066 + 终端尾号4位
PURPOSE_TERMINAL_PATTERN = re.compile(r"收单清算款\d{15,17}(\d{4})")
# 备用：用途只带尾号（部分版式无商户号）
PURPOSE_TERMINAL_ALT = re.compile(r"收单清算款(\d{4})\b")

# 对账单 xlsx 关键锚点
DETAIL_HEADER = "终端交易明细"
TERMINAL_HEADER_LABEL = "终端号"
SUBTOTAL_LABEL = "终端小计"
REPORT_DATE_LABEL = "报表日期"
POS_CODE_IN_SUBTOTAL = re.compile(r"#(\d{4})")
TERMINAL_CELL_PATTERN = re.compile(r"^\d{6,8}$")
# 金额列一律按表头锚点定位（真实 SHOP 列序可能变化，锁死列位会静默读成 0 元）
AMOUNT_HEADER_ALIASES: dict[str, tuple[str, ...]] = {
    "gross": ("交易金额",),
    "fee": ("银行手续费", "手续费"),
    "net": ("划账金额", "到账金额", "交易净计金额"),
}
# 笔数列可选锚点（旧模板无该表头时回退旧扫描）
COUNT_HEADER_ALIASES: tuple[str, ...] = ("笔数", "交易笔数")
# 表头别名只允许加单位后缀，禁止「手续费率」「交易金额合计」被误绑定
HEADER_UNIT_SUFFIXES: tuple[str, ...] = ("（元）", "(元)", "（人民币）", "(人民币)", "元")
AMOUNT_RELATION_TOLERANCE = Decimal("0.01")

# LibreOffice 转换参数
SOFFICE_STEP_TIMEOUT = 600
SOFFICE_CHUNK = 20
VOUCHERS_SUBDIR = "vouchers"


class VoucherGenerationError(RuntimeError):
    """凭证生成运行期失败（引擎/环境类，路由映射 503）。"""


# ── 数据结构 ────────────────────────────────────────────────────────────

@dataclass
class TerminalDaySubtotal:
    """对账单里一个终端一天的小计（来自「终端小计」行）。"""
    trading_date: date        # 交易日（来自 SHOP 文件名）
    report_date: date         # 报表日期（xlsx「报表日期」单元格，一般为 T+1）
    terminal: str             # 终端尾号4位（0002/0005/0006）
    project: str              # 项目名
    gross: Decimal            # 交易金额合计
    fee: Decimal              # 手续费合计
    net: Decimal              # 划账金额合计
    count: int                # 交易笔数
    source_file: str          # 来源 SHOP 文件名


@dataclass
class ReceiptBlock:
    """一张 POS 清算回单（从回单 PDF 文本层按头分块提取）。"""
    receipt_no: str           # 回单编号
    bank: str                 # 银行/账户标识（建行0354 等，取自文件名）
    receipt_date: date        # 清算日期
    gross: Decimal            # 交易金额
    fee: Decimal              # 手续费
    net: Decimal              # 交易净计金额（=划账金额）
    count: int                # 交易笔数
    terminal: str             # 终端尾号（0002/0005/0006）
    project: str              # 项目名
    pdf_name: str             # 来源 PDF 文件名
    page_index: int           # 来源页码（0-based）
    y_top: float              # 回单块顶部 Y（PDF 坐标，Y 向上）
    y_next: float             # 下一回单块顶部 Y（块底界）
    raw: str = ""             # 块文本（调试用）


def _parse_decimal(raw) -> Decimal:
    """'17,223.33' → Decimal('17223.33')；空/破折号/解析失败 → 0。"""
    if raw is None:
        return Decimal("0")
    if isinstance(raw, Decimal):
        return raw
    if isinstance(raw, (int, float)):
        return Decimal(str(raw))
    cleaned = str(raw).strip().replace(",", "").replace("￥", "").strip()
    if cleaned in ("", "-", "—"):
        return Decimal("0")
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return Decimal("0")


def _parse_cn_date(text: str) -> date | None:
    """'2026年09月09日' → date(2026,9,9)。"""
    m = DATE_CN_PATTERN.search(text)
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def _project_of(terminal: str) -> str:
    return project_map().get(terminal, "未知项目")


def _safe_voucher_stem(name: str) -> str:
    """凭证文件名词干清洗（Windows 非法字符/控制字符/设备名/超长）。"""
    raw = "".join("_" if ch in '<>:"/\\|?*' or ord(ch) < 32 else ch for ch in name)
    raw = raw.strip(" .")
    stem = raw[:100].rstrip(" .")
    reserved = {"CON", "PRN", "AUX", "NUL"} | {f"{p}{i}" for p in ("COM", "LPT") for i in range(1, 10)}
    if stem.upper() in reserved:
        stem = f"_{stem}"
    if not stem:
        raise ValueError("非法的凭证文件名")
    return stem


# ── 对账单解析 ─────────────────────────────────────────────────────────

def _match_header(texts: list[str], alias: str) -> int | None:
    """表头精确匹配；仅允许「别名 + 单位后缀」，避免前缀近似列被误绑定。"""
    if alias in texts:
        return texts.index(alias)
    for suffix in HEADER_UNIT_SUFFIXES:
        candidate = alias + suffix
        if candidate in texts:
            return texts.index(candidate)
    return None


def _locate_header_columns(texts: list[str]) -> dict[str, int]:
    """在表头行按标签定位「终端号 / 交易金额 / 手续费 / 到账金额」列索引。

    找不到终端号列返回 {}（继续向下找表头行）；终端号已找到但金额列缺失时
    直接报错——宁可拒绝也不能按旧列位读出 0 元凭证。笔数列可选，返回在
    ``count`` 键供 ``_subtotal_count`` 使用。
    """
    found: dict[str, int] = {}
    if TERMINAL_HEADER_LABEL in texts:
        found["terminal"] = texts.index(TERMINAL_HEADER_LABEL)
    for key, aliases in AMOUNT_HEADER_ALIASES.items():
        for alias in aliases:
            idx = _match_header(texts, alias)
            if idx is not None:
                found[key] = idx
                break
    if "terminal" not in found:
        return {}
    missing = [k for k in AMOUNT_HEADER_ALIASES if k not in found]
    if missing:
        labels = "/".join(a[0] for k, a in AMOUNT_HEADER_ALIASES.items() if k in missing)
        raise ValueError(f"对账单表头缺少金额列（{labels}），无法按锚点读取金额")
    for alias in COUNT_HEADER_ALIASES:
        idx = _match_header(texts, alias)
        if idx is not None:
            found["count"] = idx
            break
    return found


def _subtotal_count(values: list, col_map: dict[str, int]) -> int:
    """笔数：按「笔数」表头取右侧首个非空值；旧模板无表头时回退 17–23 列扫描。"""
    count_col = col_map.get("count")
    if count_col is not None:
        for j in range(count_col + 1, min(count_col + 6, len(values))):
            cell = values[j]
            if cell is not None and str(cell).strip() not in ("", "笔数"):
                return int(_parse_decimal(cell))
        return 0
    for j in range(17, min(23, len(values))):
        if values[j] is not None and str(values[j]).strip() not in ("", "笔数"):
            return int(_parse_decimal(values[j]))
    return 0


def _cell_at(values: list, index: int):
    return values[index] if 0 <= index < len(values) else None


@dataclass
class StatementParseResult:
    """一份 SHOP 对账单的解析产物。"""
    subtotals: list[TerminalDaySubtotal]
    xlsx_bytes: bytes         # 原始 xlsx 字节（供转换补丁复用）
    xlsx_name: str            # zip 内 xlsx 名


def parse_statement_zip(data: bytes, source_name: str) -> StatementParseResult:
    """解析 SHOP 对账单（zip 内 xlsx 或裸 xlsx 字节），提取按终端小计。

    锚点：C 列「报表日期:」→ F 列值；「终端交易明细」→ 表头行「终端号」→
    数据行 C 列=终端号；小计行 C 列含「POS编号：…#0005」或 O 列「终端小计」。
    """
    # zip → xlsx 字节（裸 xlsx 直接用）
    xlsx_bytes = data
    xlsx_name = source_name
    if source_name.lower().endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            xlsx_names = [n for n in zf.namelist() if n.lower().endswith(".xlsx")]
            if not xlsx_names:
                raise ValueError(f"对账单压缩包内无 xlsx：{source_name}")
            xlsx_bytes = zf.read(xlsx_names[0])
            xlsx_name = xlsx_names[0]

    m = SHOP_DATE_PATTERN.search(source_name) or SHOP_XLSX_DATE_PATTERN.search(source_name)
    if not m:
        raise ValueError(f"对账单文件名无日期（SHOP.….YYYYMMDD）：{source_name}")
    trading_date = datetime.strptime(m.group(1), "%Y%m%d").date()

    wb = openpyxl.load_workbook(io.BytesIO(xlsx_bytes), data_only=True)
    try:
        ws = wb[wb.sheetnames[0]]
        # 报表日期：任意单元格含「报表日期」，取右侧最近的可解析日期；缺省回退交易日+1
        report_date = trading_date + timedelta(days=1)
        for row in ws.iter_rows(values_only=True):
            values = list(row)
            for idx, cell in enumerate(values):
                if cell is None or REPORT_DATE_LABEL not in str(cell):
                    continue
                for nxt in values[idx + 1: idx + 5]:
                    m2 = re.search(r"(\d{4})/(\d{2})/(\d{2})", str(nxt or ""))
                    if m2:
                        report_date = date(int(m2.group(1)), int(m2.group(2)), int(m2.group(3)))
                        break
                break
            else:
                continue
            break

        # 内容与文件名交叉校验：报表日期应紧随交易日（T+1 清算 + 长假顺延），
        # 改名/放错文件导致月份不一致时拒绝，避免按文件名串月入账。
        lag_days = (report_date - trading_date).days
        tolerance_days = report_date_tolerance_days()
        if lag_days < 0 or lag_days > tolerance_days:
            raise ValueError(
                f"对账单文件名日期与内容报表日期不一致：{source_name} "
                f"文件名 {trading_date} / 报表日期 {report_date}"
                f"（允许滞后 {tolerance_days} 天，含长假顺延）"
            )

        # 终端交易明细区：终端号列与金额列均按表头锚点定位
        in_detail = False
        col_map: dict[str, int] = {}
        current_terminal = ""
        subtotals: list[TerminalDaySubtotal] = []
        for row in ws.iter_rows(values_only=True):
            values = list(row)
            texts = [str(c).strip() if c is not None else "" for c in values]
            if not in_detail:
                if any(DETAIL_HEADER in t for t in texts):
                    in_detail = True
                continue
            if not col_map:
                col_map = _locate_header_columns(texts)
                if not col_map:
                    continue
            terminal_col = col_map["terminal"]
            cell0 = texts[terminal_col] if len(texts) > terminal_col else ""
            if cell0 and TERMINAL_CELL_PATTERN.match(cell0):
                current_terminal = cell0[-4:]
            if any(SUBTOTAL_LABEL in t for t in texts):
                joined = " ".join(texts)
                m3 = POS_CODE_IN_SUBTOTAL.search(joined)
                terminal = m3.group(1) if m3 else current_terminal
                if not terminal:
                    continue
                # 笔数按「笔数」表头锚点取右侧首个非空值（旧模板回退 17–23 扫描）
                count = _subtotal_count(values, col_map)
                gross = _parse_decimal(_cell_at(values, col_map["gross"]))
                fee = _parse_decimal(_cell_at(values, col_map["fee"]))
                net = _parse_decimal(_cell_at(values, col_map["net"]))
                if gross == 0 and fee == 0 and net == 0:
                    if count > 0:
                        raise ValueError(
                            f"对账单金额全为 0（疑似金额列偏移）：{source_name} 终端{terminal}"
                        )
                    current_terminal = ""
                    continue
                if abs(gross - fee - net) > AMOUNT_RELATION_TOLERANCE:
                    raise ValueError(
                        f"对账单金额勾稽不符（交易金额-手续费≠到账）："
                        f"{source_name} 终端{terminal} {gross}-{fee}≠{net}"
                    )
                subtotals.append(TerminalDaySubtotal(
                    trading_date=trading_date,
                    report_date=report_date,
                    terminal=terminal,
                    project=_project_of(terminal),
                    gross=gross,
                    fee=fee,
                    net=net,
                    count=count,
                    source_file=source_name,
                ))
                current_terminal = ""
    finally:
        wb.close()

    if not subtotals:
        raise ValueError(f"对账单内未识别到终端小计行：{source_name}")
    return StatementParseResult(subtotals=subtotals, xlsx_bytes=xlsx_bytes, xlsx_name=xlsx_name)


# ── 回单解析与裁切 ──────────────────────────────────────────────────────

def _receipt_page_spans(page, textpage) -> list[tuple[float, float, str]]:
    """按「单位客户专用回单」头分块一页，返回 [(y_top, y_next, 块文本)]。

    pypdfium2 逐字符取框：头行 bbox.top 作为块顶界，下一头行 top 为底界
    （PDF 坐标 Y 向上）；块文本按字符索引段拼接。双层文本 PDF 同内容渲染
    两遍时会取到重复块，由调用方按 (文件, 回单号) 去重。
    """
    import pypdfium2 as pdfium  # noqa: F401  仅确保依赖可用
    import pypdfium2.raw as C

    n = C.FPDFText_CountChars(textpage)
    if n <= 0:
        return []
    per_char = []
    for i in range(n):
        left = C.c_double()
        right = C.c_double()
        bottom = C.c_double()
        top = C.c_double()
        C.FPDFText_GetCharBox(textpage, i, C.byref(left), C.byref(right), C.byref(bottom), C.byref(top))
        code = C.FPDFText_GetUnicode(textpage, i)
        per_char.append((i, left.value, right.value, bottom.value, top.value, chr(code)))
    text_all = "".join(c[5] for c in per_char)

    # 块边界按 Y 坐标从上到下排序（与文本流顺序无关），块文本按 Y 范围过滤字符——
    # 避免内容流乱序时把相邻回单的文本/边界错配（曾致裁切到同页另一笔回单）
    headers = [(m.start(), per_char[m.start()][4]) for m in re.finditer(RECEIPT_HEADER, text_all)]
    if not headers:
        return []
    order = sorted(headers, key=lambda h: -h[1])
    spans = []
    for k, (i0, y_top) in enumerate(order):
        y_next = order[k + 1][1] if k + 1 < len(order) else 0.0
        seg_chars = [c[5] for c in per_char if y_next < c[4] <= y_top + 1.0]
        spans.append((y_top, y_next, "".join(seg_chars)))
    return spans


def parse_receipts(pdf_path: Path, bank: str) -> list[ReceiptBlock]:
    """解析一份回单 PDF → POS 清算回单列表（按 (文件, 回单号) 去重）。

    仅保留块文本含「收单清算款」且回单日期可解析的块；双层文本 PDF 同一
    回单渲染两遍时按回单号去重，保留坐标更可信（在页面范围内）的一份。
    """
    import pypdfium2 as pdfium

    records: list[ReceiptBlock] = []
    doc = pdfium.PdfDocument(str(pdf_path))
    try:
        for page_index, page in enumerate(doc):
            textpage = page.get_textpage()
            try:
                for y_top, y_next, seg in _receipt_page_spans(page, textpage):
                    if POS_PURPOSE_KEYWORD not in seg:
                        continue
                    receipt_date = _parse_cn_date(seg)
                    if receipt_date is None:
                        continue
                    m_net = NET_PATTERN.search(seg)
                    if not m_net:
                        continue
                    m_no = RECEIPT_NO_PATTERN.search(seg)
                    receipt_no = m_no.group(1) if m_no else f"p{page_index}_{int(y_top)}"
                    m_terminal = PURPOSE_TERMINAL_PATTERN.search(seg) or PURPOSE_TERMINAL_ALT.search(seg)
                    terminal = m_terminal.group(1) if m_terminal else ""
                    records.append(ReceiptBlock(
                        receipt_no=receipt_no,
                        bank=bank,
                        receipt_date=receipt_date,
                        gross=_parse_decimal(m_gross.group(1)) if (m_gross := GROSS_PATTERN.search(seg)) else Decimal("0"),
                        fee=_parse_decimal(m_fee.group(1)) if (m_fee := FEE_PATTERN.search(seg)) else Decimal("0"),
                        net=_parse_decimal(m_net.group(1)),
                        count=int(_parse_decimal(m_count.group(1))) if (m_count := re.search(r"交易笔数[：:]\s*(\d+)\s*笔", seg)) else 0,
                        terminal=terminal,
                        project=_project_of(terminal),
                        pdf_name=pdf_path.name,
                        page_index=page_index,
                        y_top=y_top + 2.0,
                        y_next=max(y_next - 1.0, 0.0),
                        raw=seg[:400],
                    ))
            finally:
                textpage.close()
    finally:
        doc.close()

    # 按 (文件, 回单号) 去重：双层文本 PDF 同一回单渲染两遍 → 保留坐标在页面内的一份
    dedup: dict[str, ReceiptBlock] = {}
    for r in records:
        key = f"{r.pdf_name}|{r.receipt_no}"
        prev = dedup.get(key)
        if prev is None:
            dedup[key] = r
            continue
        keep = r if (prev.y_top <= 0 or prev.y_top > 2000) and (0 < r.y_top <= 2000) else prev
        dedup[key] = keep
    return list(dedup.values())


def crop_receipt(pdf_path: Path, block: ReceiptBlock, output_path: Path) -> Path:
    """按 Y 边界裁切单张回单为独立 PDF（pypdf cropbox，坐标 Y 向上）。"""
    from pypdf import PdfReader, PdfWriter
    from pypdf.generic import RectangleObject

    reader = PdfReader(str(pdf_path))
    if block.page_index >= len(reader.pages):
        raise ValueError(f"回单页码越界：{block.pdf_name} 第 {block.page_index + 1} 页")
    source_page = reader.pages[block.page_index]
    page_w = float(source_page.mediabox.width)
    page_h = float(source_page.mediabox.height)
    y0 = max(float(block.y_next), 0.0)
    y1 = min(float(block.y_top), page_h)
    if y1 - y0 < 30:
        raise ValueError(f"回单裁切高度异常（{y1 - y0:.0f}pt）：{block.receipt_no}")
    writer = PdfWriter()
    page = writer.add_page(source_page)
    crop_box = RectangleObject((0, y0, page_w, y1))
    page.mediabox = crop_box
    page.cropbox = crop_box
    output_path.parent.mkdir(parents=True, exist_ok=True)
    writer.write(str(output_path))
    return output_path


# ── 对账单转换（A4 横排） ───────────────────────────────────────────────

def _patch_statement_xlsx(data: bytes, hide_rows: set[int], last_row: int, last_col: int) -> bytes:
    """对 SHOP 对账单 xlsx 打打印补丁：打印区域 + fitToPage + A4 横向 1 页宽 + 隐藏行。

    移植自已验证的 pos_slips_to_pdf.py：只改页面设置与行可见性，不动样式字节。
    """
    src = zipfile.ZipFile(io.BytesIO(data))
    sheet_names = [n for n in src.namelist() if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", n)]
    if not sheet_names:
        raise ValueError("对账单 xlsx 内无工作表 XML")
    sheet = sorted(sheet_names)[0]
    xml = src.read(sheet).decode("utf-8")
    wbxml = src.read("xl/workbook.xml").decode("utf-8")

    # 重打包前校验成员名（xlsx 内容不可信：拒绝绝对路径/越界段/反斜杠段）
    for info in src.infolist():
        name = info.filename
        if name.startswith("/") or "\\" in name or ".." in name.split("/"):
            src.close()
            raise ValueError(f"对账单 xlsx 内含非法条目名：{name}")

    # 打印区域 = A1 到最后小计行（排除右侧空列/游离单元格，否则缩放基准被撑宽）
    m = re.search(r'<sheet name="([^"]+)"', wbxml)
    sheet_name = m.group(1) if m else "Sheet1"
    from openpyxl.utils import get_column_letter
    ref = f"'{sheet_name}'!$A$1:${get_column_letter(max(last_col, 1))}${max(last_row, 1)}"
    name_tag = f'<definedName name="_xlnm.Print_Area" localSheetId="0">{ref}</definedName>'
    if "<definedNames>" in wbxml:
        wbxml = wbxml.replace("<definedNames>", "<definedNames>" + name_tag, 1)
    elif "<definedNames/>" in wbxml:
        wbxml = wbxml.replace("<definedNames/>", f"<definedNames>{name_tag}</definedNames>", 1)
    else:
        wbxml = wbxml.replace("<calcPr", f"<definedNames>{name_tag}</definedNames><calcPr", 1)

    # 允许缩放以适应页面
    if "<pageSetUpPr" not in xml:
        m = re.search(r"<sheetPr([^>]*)/>", xml)
        if m:
            xml = xml[:m.start()] + f"<sheetPr{m.group(1)}><pageSetUpPr fitToPage=\"1\"/></sheetPr>" + xml[m.end():]
        elif "<sheetPr>" in xml:
            xml = xml.replace("<sheetPr>", '<sheetPr><pageSetUpPr fitToPage="1"/>', 1)
        else:
            xml = re.sub(r"(<worksheet[^>]*>)", r"\1<sheetPr><pageSetUpPr fitToPage=\"1\"/></sheetPr>", xml, count=1)

    # A4 横向 + 1 页宽 + 窄边距
    new_pagesetup = '<pageSetup paperSize="9" orientation="landscape" fitToWidth="1" fitToHeight="0"/>'
    new_margins = '<pageMargins left="0.25" right="0.25" top="0.3" bottom="0.3" header="0" footer="0"/>'
    if "<pageSetup" in xml:
        xml = re.sub(r"<pageSetup[^>]*/>", new_pagesetup, xml, count=1)
    if "<pageMargins" in xml:
        xml = re.sub(r"<pageMargins[^>]*/>", new_margins, xml, count=1)

    # 隐藏其它终端的行（明细行+小计行）与表尾行
    for n in sorted(hide_rows):
        xml = re.sub(rf'<row r="{n}"(?=[\s/>])', f'<row r="{n}" hidden="1"', xml, count=1)

    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
        for item in src.infolist():
            if item.filename == sheet:
                body = xml.encode("utf-8")
            elif item.filename == "xl/workbook.xml":
                body = wbxml.encode("utf-8")
            else:
                body = src.read(item.filename)
            dst.writestr(item, body)
    src.close()
    return out.getvalue()


def _find_soffice_exe() -> str | None:
    """探测 soffice 可执行文件（配置优先，复用 document_text 的探测链）。"""
    from ...document_text import _find_soffice

    return _find_soffice()


def _convert_xlsx_to_pdf(xlsx_files: list[Path], out_dir: Path) -> dict[Path, Path]:
    """批量 LibreOffice 转换 → {源xlsx: PDF}。无 soffice → VoucherGenerationError。"""
    executable = _find_soffice_exe()
    if executable is None:
        raise VoucherGenerationError("未找到 LibreOffice（soffice），无法把对账单转成 A4 横排 PDF")
    if not xlsx_files:
        return {}
    out_dir.mkdir(parents=True, exist_ok=True)
    results: dict[Path, Path] = {}
    profile = out_dir / "_lo_profile"
    remaining = list(xlsx_files)
    while remaining:
        chunk = remaining[:SOFFICE_CHUNK]
        remaining = remaining[SOFFICE_CHUNK:]
        cmd = [executable, "--headless", "--nologo", "--nodefault", "--nolockcheck",
               f"-env:UserInstallation={profile.resolve().as_uri()}",
               "--convert-to", "pdf", "--outdir", str(out_dir.resolve())]
        cmd += [str(f.resolve()) for f in chunk]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                                  timeout=SOFFICE_STEP_TIMEOUT, stdin=subprocess.DEVNULL,
                                  creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except subprocess.TimeoutExpired as exc:
            raise VoucherGenerationError("LibreOffice 转换超时（对账单多时较慢，可重试）") from exc
        except OSError as exc:
            raise VoucherGenerationError("无法启动 LibreOffice") from exc
        for f in chunk:
            pdf = out_dir / f"{f.stem}.pdf"
            if not pdf.is_file():
                raise VoucherGenerationError(f"LibreOffice 未生成 {f.stem}.pdf（stderr: {(proc.stderr or '')[-300:]}）")
            results[f] = pdf
    return results


# ── 匹配 ────────────────────────────────────────────────────────────────

def match_receipts_to_subtotals(
    receipts: list[ReceiptBlock],
    subtotals: list[TerminalDaySubtotal],
) -> tuple[list[tuple[ReceiptBlock, TerminalDaySubtotal, dict]], list[tuple[ReceiptBlock, str]]]:
    """双键匹配：终端一致 + 报表日期 ≤ 清算日 ≤ 报表日期+2；金额命中优先、日期最近。

    返回 (matched=[(回单, 小计, {amount_hit, delta_days})], unmatched=[(回单, 原因)])。
    与本地已验证管线一致：同一小计可承接多笔回单（分批清算场景）。
    """
    matched: list[tuple[ReceiptBlock, TerminalDaySubtotal, dict]] = []
    unmatched: list[tuple[ReceiptBlock, str]] = []
    for r in receipts:
        if not r.terminal:
            unmatched.append((r, "回单用途未带终端号，无法定位对账单"))
            continue
        tol_days = date_tolerance_days()
        tol_amount = amount_tolerance()
        cands = [
            s for s in subtotals
            if s.terminal == r.terminal
            and s.report_date <= r.receipt_date <= s.report_date + timedelta(days=tol_days)
        ]
        if not cands:
            unmatched.append((r, f"终端{r.terminal}容差{tol_days}天内无对账单日"))
            continue
        best = min(cands, key=lambda s: (
            0 if abs(s.net - r.net) <= tol_amount else 1,
            abs((s.report_date - r.receipt_date).days),
        ))
        matched.append((r, best, {
            "amount_hit": abs(best.net - r.net) <= tol_amount,
            "delta_days": (best.report_date - r.receipt_date).days,
        }))
    return matched, unmatched


# ── 凭证生成 ────────────────────────────────────────────────────────────

def _fee_voucher_cover_pdf(subtotals: list[TerminalDaySubtotal], month: str) -> bytes:
    """月度手续费凭证首页（A4 横向）：逐日逐终端 交易/手续费/到账 汇总表 + 合计。"""
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib import colors
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib.styles import ParagraphStyle

    font_name = _register_chinese_font()

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=landscape(A4),
        leftMargin=36, rightMargin=36, topMargin=32, bottomMargin=28,
        title=f"POS刷卡手续费凭证 {month}",
    )
    styles = {
        "title": ParagraphStyle("title", fontName=font_name, fontSize=15, leading=20, alignment=1),
        "sub": ParagraphStyle("sub", fontName=font_name, fontSize=9.5, leading=13, alignment=1,
                              textColor=colors.HexColor("#666666")),
        "note": ParagraphStyle("note", fontName=font_name, fontSize=8.5, leading=12),
    }

    ordered = sorted(subtotals, key=lambda s: (s.report_date, s.terminal))
    header = ["报表日期", "交易日", "终端", "项目", "笔数", "交易金额", "手续费", "到账金额"]
    rows = [header]
    for s in ordered:
        rows.append([
            s.report_date.strftime("%m/%d"),
            s.trading_date.strftime("%m/%d"),
            s.terminal,
            s.project,
            str(s.count or ""),
            f"{s.gross:,.2f}",
            f"{s.fee:,.2f}",
            f"{s.net:,.2f}",
        ])
    totals = (
        sum((s.gross for s in ordered), Decimal("0")),
        sum((s.fee for s in ordered), Decimal("0")),
        sum((s.net for s in ordered), Decimal("0")),
    )
    rows.append(["合计", "", "", "", str(sum(s.count for s in ordered)),
                 f"{totals[0]:,.2f}", f"{totals[1]:,.2f}", f"{totals[2]:,.2f}"])

    table = Table(rows, repeatRows=1, hAlign="LEFT",
                  colWidths=[52, 52, 40, 60, 40, 70, 60, 74])
    table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), font_name),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#F0F0F0")),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E8EEF7")),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#BBBBBB")),
        ("ALIGN", (4, 1), (7, -1), "RIGHT"),
        ("ALIGN", (0, 1), (3, -1), "CENTER"),
    ]))
    story = [
        Paragraph(f"POS 刷卡手续费凭证（{month.replace('-', '年')}月）", styles["title"]),
        Spacer(1, 4),
        Paragraph("按「终端小计」逐日逐终端汇总；附件为当月全部对账单（A4 横向）", styles["sub"]),
        Spacer(1, 8),
        table,
        Spacer(1, 10),
        Paragraph("说明：本凭证由个人工作台按对账单数据自动生成，供记账与归档参考；实际分录按账套习惯录入。",
                  styles["note"]),
    ]
    doc.build(story)
    return buf.getvalue()


def _register_chinese_font() -> str:
    """reportlab 中文字体注册（pdf_font_path 配置优先 → simsun → simhei → msyh）。"""
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    if "POSFont" in pdfmetrics.getRegisteredFontNames():
        return "POSFont"
    candidates = []
    if (settings.pdf_font_path or "").strip():
        candidates.append(settings.pdf_font_path)
    candidates += [r"C:\Windows\Fonts\simsun.ttc", r"C:\Windows\Fonts\simhei.ttf", r"C:\Windows\Fonts\msyh.ttc"]
    for path in candidates:
        if path and Path(path).is_file():
            pdfmetrics.registerFont(TTFont("POSFont", path))
            return "POSFont"
    raise VoucherGenerationError("未找到可用中文字体（pdf_font_path / simsun / simhei / msyh）")


# ── 生成编排与服务接口 ──────────────────────────────────────────────────

_VOUCHER_LOCK = threading.Lock()
STALE_BATCH_SECONDS = 2 * 3600
BATCH_HEARTBEAT_SECONDS = 60


class _BatchHeartbeat:
    """生成期心跳：独立线程 + 独立会话刷新批次 updated_at，避免长任务被回收。

    多进程场景下列表查询可能来自其它 worker，进程内锁判断不到，心跳是唯一的
    跨进程「仍活跃」信号；单进程下 `_VOUCHER_LOCK` 判断作为第二道保险。
    """

    def __init__(self, batch_id: int, interval: float | None = None):
        self.batch_id = batch_id
        self.interval = max(
            0.01, float(BATCH_HEARTBEAT_SECONDS if interval is None else interval)
        )
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._beat, name=f"pos-voucher-heartbeat-{batch_id}", daemon=True
        )

    def _beat(self) -> None:
        while not self._stop.wait(self.interval):
            try:
                with SessionLocal() as db:
                    db.query(PosVoucherBatch).filter(
                        PosVoucherBatch.id == self.batch_id
                    ).update({"updated_at": datetime.now()}, synchronize_session=False)
                    db.commit()
            except Exception:
                logger.warning("POS 凭证批次 %s 心跳失败", self.batch_id, exc_info=True)

    def start(self) -> "_BatchHeartbeat":
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout=2)

    def __enter__(self) -> "_BatchHeartbeat":
        return self.start()

    def __exit__(self, *_exc) -> None:
        self.stop()


def _reap_stale_batches(db: Session, *, skip_if_busy: bool = True) -> int:
    """回收超时的 running 批次：进程崩溃/重启后无人落库，生成与查询时都需自愈。

    ``skip_if_busy``：列表查询等锁外调用在检测到本进程正在生成（锁被占用）时
    直接跳过——长任务即便超过 STALE_BATCH_SECONDS 也不得被误杀；生成入口已经
    持锁，传 False 正常回收历史残留。
    """
    if skip_if_busy and _VOUCHER_LOCK.locked():
        return 0
    cutoff = datetime.now() - timedelta(seconds=STALE_BATCH_SECONDS)
    stale = (
        db.query(PosVoucherBatch)
        .filter(PosVoucherBatch.status == "running", PosVoucherBatch.updated_at < cutoff)
        .all()
    )
    for row in stale:
        row.status = "failed"
        row.error = "上次生成中断（进程退出/重启），已自动标记失败，可重新生成"
    if stale:
        db.commit()
        logger.warning("回收超时 POS 凭证批次 %s 个", len(stale))
    return len(stale)


def _files_out(batch: PosVoucherBatch) -> list[dict]:
    return [
        {"name": f["name"], "size": f["size"], "kind": f.get("kind", "txn"),
         "receipt_date": f.get("receipt_date", ""), "terminal": f.get("terminal", ""),
         "project": f.get("project", ""), "net_amount": f.get("net_amount", "")}
        for f in (batch.stats or [])
    ]


def _batch_out(batch: PosVoucherBatch) -> dict:
    return {
        "id": batch.id,
        "month": batch.month,
        "status": batch.status,
        "error": batch.error,
        "summary": batch.summary or {},
        "files": _files_out(batch),
        "output_dir": str(Path(batch.batch_dir or "").parent) if batch.batch_dir else "",
        "elapsed_sec": batch.elapsed_sec,
        "log_tail": (batch.log_tail or "")[-800:],
        "created_at": batch.created_at.strftime("%Y-%m-%d %H:%M:%S") if batch.created_at else "",
    }


def _resolve_batch_dir(batch: PosVoucherBatch) -> Path:
    """批次目录校验：路径来自本服务写入的 DB 行——目录存在 + 含 manifest.json 即可
    （批次目录允许人工指定输出到任意绝对路径，不再限定工作台数据目录内）。"""
    raw = (batch.batch_dir or "").strip()
    if not raw:
        raise ValueError("批次目录缺失，请删除该批次并重新生成")
    path = Path(raw).resolve()
    if not path.is_dir() or not (path / "manifest.json").is_file():
        raise ValueError("批次目录无效或已被移动/删除，请删除该批次并重新生成")
    return path


def _list_month_files(month_dir: Path) -> tuple[list[Path], list[Path]]:
    """扫描月度目录 → (对账单文件列表, 回单PDF列表)。"""
    statements = sorted(
        p for p in month_dir.iterdir()
        if p.is_file() and (SHOP_DATE_PATTERN.search(p.name) or SHOP_XLSX_DATE_PATTERN.search(p.name))
    )
    globs = receipt_globs()
    receipts = sorted(
        p for p in month_dir.iterdir()
        if p.is_file() and any(Path(p.name).match(g) for g in globs)
    )
    return statements, receipts


def _bank_from_name(name: str) -> str:
    """'云南0354建行2026.9回单.pdf' → '建行0354'；匹配不到 → '建行'。"""
    m = re.search(r"云南(\d{4})建行", name)
    return f"建行{m.group(1)}" if m else "建行"


def _vouchers_root() -> Path:
    """凭证批次根目录（finance_data_dir/finance_pos/vouchers）。"""
    d = get_finance_data_dir() / "finance_pos" / VOUCHERS_SUBDIR
    d.mkdir(parents=True, exist_ok=True)
    return d


def _month_dir(month: str, override_dir: str = "") -> Path:
    """解析月度源目录；未配置/目录不存在 → ValueError(400)。

    override_dir（前端人工指定）优先：
    - 目录下直接有对账单文件 → 直接用作月度目录
    - 否则按归档布局拼 {override}/{YYYY}年/{MM}
    均无 → 回退 .env 的 FINANCE_POS_STATEMENT_DIR 按同样规则解析。
    """
    candidates: list[Path] = []
    base_raw = (override_dir or "").strip() or (settings.finance_pos_statement_dir or "").strip()
    if not base_raw:
        raise ValueError(
            "尚未指定凭证源目录：请在页面填写，或在 backend/.env 配置 FINANCE_POS_STATEMENT_DIR"
        )
    base = Path(base_raw)
    if not base.is_absolute():
        raise ValueError(f"源目录必须是绝对路径：{base_raw}")
    candidates.append(base)
    candidates.append(base / f"{month[:4]}年" / month[5:7])
    for d in candidates:
        if d.is_dir() and any(SHOP_DATE_PATTERN.search(p.name) or SHOP_XLSX_DATE_PATTERN.search(p.name)
                              for p in d.iterdir() if p.is_file()):
            return d
    for d in candidates:
        if d.is_dir():
            raise ValueError(f"目录内没有对账单（SHOP.*.zip/xlsx）：{d}")
    raise ValueError(f"源目录不存在：{candidates[-1]}")


def _terminal_row_layout(ws) -> tuple[dict[str, list[int]], dict[str, int], set[int], int]:
    """工作表行布局：({终端: 数据行}, {终端: 小计行}, 尾部行, 最右非空列)。

    行号 1-based，与 sheet XML 的 r= 一致，供打印补丁隐藏行用。
    """
    data_rows: dict[str, list[int]] = {}
    subtotal_rows: dict[str, int] = {}
    current = ""
    pending: list[int] = []
    last_col = 1
    last_sub = 0
    # 终端号列按表头锚点定位（列序变化时仍能隐藏非本终端行）；无表头回退 C 列
    terminal_col = 2
    for row in ws.iter_rows(values_only=True):
        texts = [str(c).strip() if c is not None else "" for c in row]
        if TERMINAL_HEADER_LABEL in texts:
            terminal_col = texts.index(TERMINAL_HEADER_LABEL)
            break
    for idx, row in enumerate(ws.iter_rows(values_only=True), start=1):
        texts = [str(c).strip() if c is not None else "" for c in row]
        cell0 = texts[terminal_col] if len(texts) > terminal_col else ""
        if cell0 and TERMINAL_CELL_PATTERN.match(cell0):
            current = cell0[-4:]
            pending.append(idx)
        elif cell0.startswith("POS编号") or any(SUBTOTAL_LABEL in t for t in texts):
            code = POS_CODE_IN_SUBTOTAL.search(cell0)
            term = code.group(1) if code else current
            data_rows[term] = pending
            subtotal_rows[term] = idx
            pending = []
            current = ""
            last_sub = idx
        for j, t in enumerate(texts):
            if t:
                last_col = max(last_col, j + 1)
    tail_rows = set(range(last_sub + 1, ws.max_row + 1))
    return data_rows, subtotal_rows, tail_rows, last_col


def generate_vouchers(db: Session, month: str, statement_dir: str = "", output_dir: str = "") -> dict:
    """生成月度刷卡凭证：解析→转换→匹配→合并，同步完成。

    全程持有进程内锁：解析/转换/合成都互斥，避免并发批次争抢 LibreOffice
    与源文件；同时保证 running 批次一定由本进程写入、可被超时回收。
    statement_dir/output_dir 可人工指定（前端传入，空则回退配置/默认数据目录）。
    返回批次详情（文件清单 + 匹配统计）。
    """
    month = (month or "").strip()
    if not MONTH_PATTERN.match(month):
        raise ValueError("月份格式应为 YYYY-MM（如 2026-09）")
    with _VOUCHER_LOCK:
        return _generate_locked(db, month, statement_dir, output_dir)


def _generate_locked(db: Session, month: str, statement_dir: str = "", output_dir: str = "") -> dict:
    # 已持锁：本进程不会再有并发生成，历史残留 running 可直接回收
    _reap_stale_batches(db, skip_if_busy=False)
    month_dir = _month_dir(month, statement_dir)

    # 扫描与解析（全程在锁内，避免并发批次争抢源文件与 LibreOffice）
    log = [f"[{datetime.now():%H:%M:%S}] 扫描 {month_dir}"]
    statements, receipt_pdfs = _list_month_files(month_dir)
    if not statements:
        raise ValueError(f"月度目录下未找到对账单（SHOP.*.zip/xlsx）：{month_dir}")
    log.append(f"对账单 {len(statements)} 份；回单 PDF {len(receipt_pdfs)} 份")

    subtotals = []
    pr_by_source: dict[str, StatementParseResult] = {}
    for p in statements:
        try:
            r = parse_statement_zip(p.read_bytes(), p.name)
        except Exception as exc:
            raise ValueError(f"对账单解析失败：{p.name}（{exc}）") from exc
        subtotals.extend(r.subtotals)
        pr_by_source[p.name] = r

    # 月份过滤：只保留解析出的交易日期落在所选月份的小计（同目录混入他月文件时
    # 不得生成串月手续费凭证），并提示被跳过的文件。
    skipped_other_month = sorted({
        s.source_file for s in subtotals if f"{s.trading_date:%Y-%m}" != month
    })
    if skipped_other_month:
        log.append(
            f"⚠ 跳过非 {month} 对账单 {len(skipped_other_month)} 份："
            f"{'、'.join(skipped_other_month)}"
        )
    subtotals = [s for s in subtotals if f"{s.trading_date:%Y-%m}" == month]
    in_month_sources = {s.source_file for s in subtotals}
    statements = [p for p in statements if p.name in in_month_sources]

    receipts: list[ReceiptBlock] = []
    for p in receipt_pdfs:
        try:
            receipts.extend(parse_receipts(p, _bank_from_name(p.name)))
        except Exception as exc:
            log.append(f"⚠ 回单解析失败（跳过该文件）：{p.name}（{exc}）")
    log.append(f"对账单小计 {len(subtotals)} 条；POS 清算回单 {len(receipts)} 笔")
    if not subtotals:
        raise ValueError(
            f"所选月份 {month} 没有对账单小计（源目录可能只含其它月份文件）"
        )

    out_raw = (output_dir or "").strip() or (settings.finance_pos_output_dir or "").strip()
    out_root = _vouchers_root()
    if out_raw:
        out_root = Path(out_raw)
        if not out_root.is_absolute():
            raise ValueError(f"输出目录必须是绝对路径：{out_raw}")
    batch = PosVoucherBatch(status="running", month=month)
    db.add(batch)
    db.flush()
    batch_dir = out_root / f"{batch.id:06d}_{datetime.now():%Y%m%d_%H%M%S}"
    try:
        batch_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        db.rollback()
        raise ValueError(f"输出目录无法创建：{batch_dir}（{exc}）") from exc
    batch.batch_dir = batch_dir.as_posix()
    db.commit()
    db.refresh(batch)
    heartbeat = _BatchHeartbeat(batch.id).start()

    t0 = time.perf_counter()
    try:
        # (交易日, 终端) 去重：同键多份对账单取第一份并告警
        by_key: dict[tuple[date, str], TerminalDaySubtotal] = {}
        for s in subtotals:
            key = (s.trading_date, s.terminal)
            if key in by_key and by_key[key].net != s.net:
                log.append(f"⚠ 同日同终端金额不一致：{s.trading_date} {s.terminal}")
            by_key.setdefault(key, s)
        subtotal_list = list(by_key.values())
        batch.elapsed_sec = round(time.perf_counter() - t0, 2)
        log.append(f"去重后小计 {len(subtotal_list)} 条")

        # (交易日, 终端) → 打印补丁后的 xlsx → LibreOffice 转 A4 横排 PDF
        patched_dir = batch_dir / "patched"
        patched_dir.mkdir(parents=True, exist_ok=True)
        patched: list[tuple[TerminalDaySubtotal, Path]] = []
        for s in sorted(subtotal_list, key=lambda x: (x.trading_date, x.terminal)):
            stamp = s.trading_date.strftime("%Y%m%d")
            target = patched_dir / f"{stamp}_{s.terminal}.xlsx"
            pr = pr_by_source.get(s.source_file)
            if pr is None:
                raise VoucherGenerationError(f"找不到来源解析结果：{s.source_file}")
            wb = openpyxl.load_workbook(io.BytesIO(pr.xlsx_bytes), data_only=True)
            try:
                ws = wb.worksheets[0]
                data_rows, subtotal_rows, tail_rows, last_col = _terminal_row_layout(ws)
            finally:
                wb.close()
            hide = set(tail_rows)
            for term, rows in data_rows.items():
                if term != s.terminal:
                    hide.update(rows)
                    if term in subtotal_rows:
                        hide.add(subtotal_rows[term])
            last_sub = max(subtotal_rows.values(), default=0)
            target.write_bytes(_patch_statement_xlsx(pr.xlsx_bytes, hide, last_sub, last_col))
            patched.append((s, target))
        pdfs = _convert_xlsx_to_pdf([t for _s, t in patched], batch_dir / "pdf")
        stmt_pdf_by_key = {
            (s.report_date, s.terminal): pdfs[t]
            for s, t in patched
        }

        # 匹配（xlsx 数据驱动，不读 PDF 文本）
        matched, unmatched = match_receipts_to_subtotals(receipts, subtotal_list)
        matched.sort(key=lambda t: (t[0].receipt_date, t[0].terminal))
        # 非本月且未匹配的回单单独标注（T+1 跨月清算已被匹配的不受影响），
        # 不混入本月「未匹配」统计，避免混月目录的统计歧义。
        other_month_receipts = [
            (r, reason) for r, reason in unmatched if f"{r.receipt_date:%Y-%m}" != month
        ]
        if other_month_receipts:
            log.append(f"⚠ 非 {month} 回单 {len(other_month_receipts)} 笔（不计入本月未匹配）")
        unmatched = [
            (r, reason) for r, reason in unmatched if f"{r.receipt_date:%Y-%m}" == month
        ]
        log.append(f"匹配 {len(matched)} 笔；未匹配 {len(unmatched)} 笔")

        # 裁切回单 + 生成每笔交易凭证
        crop_dir = batch_dir / "receipts"
        crop_dir.mkdir(parents=True, exist_ok=True)
        for r, s, info in matched:
            stem = _safe_voucher_stem(f"{s.project}_终端{r.terminal}_回单裁切_{r.receipt_date:%Y%m%d}_{r.net:.2f}")
            crop_out = crop_dir / (stem + ".pdf")
            crop_receipt(month_dir / r.pdf_name, r, crop_out)

        # 生成每笔交易凭证（对账单页 + 回单页）
        files: list[dict] = []
        for r, s, info in matched:
            stmt_pdf = stmt_pdf_by_key.get((s.report_date, s.terminal))
            if stmt_pdf is None:
                log.append(f"⚠ 缺对账单PDF：{r.receipt_date} {r.terminal}（跳过交易凭证）")
                continue
            stem = _safe_voucher_stem(f"{s.project}_终端{s.terminal}_对账单+回单_{r.receipt_date:%Y%m%d}_{r.net:.2f}")
            target = batch_dir / (stem + ".pdf")
            from pypdf import PdfReader, PdfWriter
            writer = PdfWriter()
            stmt_reader = PdfReader(str(stmt_pdf))
            for pg in stmt_reader.pages:
                writer.add_page(pg)
            crop_pdf = crop_dir / (stem.replace("对账单+回单", "回单裁切") + ".pdf")
            if not crop_pdf.is_file():
                log.append(f"⚠ 缺回单裁切件：{stem}（跳过交易凭证）")
                continue
            writer = PdfWriter()
            stmt_reader = PdfReader(str(stmt_pdf))
            for pg in stmt_reader.pages:
                writer.add_page(pg)
            crop_reader = PdfReader(str(crop_pdf))
            for pg in crop_reader.pages:
                writer.add_page(pg)
            writer.write(str(target))
            files.append({
                "name": target.name, "size": target.stat().st_size, "kind": "txn",
                "receipt_date": r.receipt_date.isoformat(), "terminal": s.terminal,
                "project": s.project, "net_amount": f"{r.net:.2f}",
            })

        # 月度手续费凭证 = 凭证首页 + 当月全部对账单页
        if subtotal_list:
            cover = _fee_voucher_cover_pdf(subtotal_list, month)
            fee_target = batch_dir / f"刷卡手续费凭证_{month}.pdf"
            from pypdf import PdfReader, PdfWriter
            writer = PdfWriter()
            writer.append(io.BytesIO(cover))
            for s in sorted(subtotal_list, key=lambda x: (x.trading_date, x.terminal)):
                writer.append(str(stmt_pdf_by_key[(s.report_date, s.terminal)]))
            writer.write(str(fee_target))
            files.append({
                "name": fee_target.name, "size": fee_target.stat().st_size, "kind": "fee",
            })

        # 收尾：manifest + DB
        manifest = {
            "month": month,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "sources": {
                "statements": [p.name for p in statements],
                "receipt_pdfs": [p.name for p in receipt_pdfs],
            },
            "summary": {},
            "files": files,
        }
        amount_mismatch = [
            {"receipt_date": r.receipt_date.isoformat(), "terminal": r.terminal,
             "net_amount": f"{r.net:.2f}", "subtotal_net": f"{s.net:.2f}"}
            for r, s, info in matched if not info["amount_hit"]
        ]
        unmatched_list = [
            {"receipt_date": r.receipt_date.isoformat(), "terminal": r.terminal,
             "net_amount": f"{r.net:.2f}", "reason": reason}
            for r, reason in unmatched
        ]
        other_month_list = [
            {"receipt_date": r.receipt_date.isoformat(), "terminal": r.terminal,
             "net_amount": f"{r.net:.2f}", "reason": reason}
            for r, reason in other_month_receipts
        ]
        summary = {
            "receipts_total": len(receipts),
            "matched": len(matched),
            "unmatched": len(unmatched),
            "statements_total": len(subtotal_list),
            "skipped_other_month": skipped_other_month,
            "other_month_receipts": other_month_list,
            "amount_mismatch": amount_mismatch,
            "unmatched_list": unmatched_list,
        }
        manifest["summary"] = summary
        manifest_path = batch_dir / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")

        batch.elapsed_sec = round(time.perf_counter() - t0, 2)
        batch.stats = files
        batch.summary = summary
        batch.log_tail = "\n".join(log)[-4000:]
        batch.status = "ok" if files else "failed"
        batch.error = "" if files else "未生成任何凭证文件"
        # 中间产物清理：打印补丁 xlsx 与逐终端 PDF 已并入凭证，不再保留
        _force_rmtree(patched_dir)
        _force_rmtree(batch_dir / "pdf")
        db.commit()
        db.refresh(batch)
        return _batch_out(batch)
    except VoucherGenerationError as exc:
        _fail_batch(db, batch.id, str(exc), t0, "\n".join(log))
        raise
    except ValueError as exc:
        _fail_batch(db, batch.id, str(exc)[:500] or "输入问题", t0, "\n".join(log))
        raise
    except Exception as exc:
        _fail_batch(db, batch.id, f"{type(exc).__name__}: {exc}"[:500], t0, "\n".join(log))
        raise VoucherGenerationError(f"凭证生成失败：{exc}") from exc
    finally:
        heartbeat.stop()


def _fail_batch(db: Session, batch_id: int, message: str, t0: float, log_text: str) -> None:
    """批次置 failed（先 rollback 兼容失败事务）。"""
    try:
        db.rollback()
    except Exception:
        logger.warning("批次 %s 失败回滚异常", batch_id, exc_info=True)
    try:
        row = db.get(PosVoucherBatch, batch_id)
        if row is not None:
            row.status = "failed"
            row.error = message[:500]
            row.elapsed_sec = round(time.perf_counter() - t0, 2)
            row.log_tail = log_text[-4000:]
            db.commit()
    except Exception:
        logger.warning("批次 %s 失败落库失败", batch_id, exc_info=True)


# ── 查询 / 合并 / 删除 ─────────────────────────────────────────────────

def list_batches(db: Session, limit: int = 50) -> list[dict]:
    _reap_stale_batches(db)
    rows = db.query(PosVoucherBatch).order_by(PosVoucherBatch.id.desc()).limit(limit).all()
    return [_batch_out(r) for r in rows]


def vouchers_config() -> dict:
    """凭证目录默认值（前端 placeholder / 回退用）。"""
    return {
        "default_statement_dir": (settings.finance_pos_statement_dir or "").strip(),
        "default_output_dir": str(get_finance_data_dir() / "finance_pos" / VOUCHERS_SUBDIR),
        "configured_output_dir": (settings.finance_pos_output_dir or "").strip(),
    }


def get_batch(db: Session, batch_id: int) -> PosVoucherBatch:
    from fastapi import HTTPException

    b = db.get(PosVoucherBatch, batch_id)
    if b is None:
        raise HTTPException(status_code=404, detail="凭证批次不存在")
    return b


def _batch_file_path(batch: PosVoucherBatch, name: str) -> Path:
    safe = Path(name or "").name
    for f in batch.stats or []:
        if f.get("name") == safe:
            p = _resolve_batch_dir(batch) / safe
            if p.is_file():
                return p
    raise LookupError(f"凭证文件不存在：{name}")


def merge_files(db: Session, batch_id: int, names: list[str]) -> tuple[bytes, str]:
    """按给定顺序合并选中的凭证 PDF → (bytes, 建议文件名)。名字必须都在批次清单内。"""
    from pypdf import PdfReader, PdfWriter

    batch = get_batch(db, batch_id)
    batch_dir = _resolve_batch_dir(batch)
    valid = {f.get("name") for f in batch.stats or []}
    wanted = [Path(n or "").name for n in names]
    if not wanted:
        raise ValueError("请选择要合并的凭证文件")
    bad = [n for n in wanted if n not in valid]
    if bad:
        raise ValueError(f"所选文件不在该批次清单内：{'、'.join(bad[:3])}")
    writer = PdfWriter()
    for n in wanted:
        p = batch_dir / n
        reader = PdfReader(str(p))
        for pg in reader.pages:
            writer.add_page(pg)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue(), f"pos_vouchers_{batch_id}.pdf"


def _force_rmtree(path: Path) -> bool:
    """强制递归删除（对只读文件先去只读再删）；返回是否彻底删净。"""
    import stat as _stat

    def _on_error(func, p, _exc):
        try:
            os.chmod(p, _stat.S_IWRITE)
            func(p)
        except OSError:
            pass

    shutil.rmtree(path, onerror=_on_error)
    return not path.exists()


def delete_batch(db: Session, batch_id: int) -> None:
    """删除批次：目录 + DB 行（目录在本模块 vouchers 内或人工输出目录）。"""
    batch = get_batch(db, batch_id)
    d = _resolve_batch_dir(batch)
    if not _force_rmtree(d):
        logger.warning("批次 %s 目录未能完全清理：%s", batch.id, d)
    db.delete(batch)
    db.commit()
