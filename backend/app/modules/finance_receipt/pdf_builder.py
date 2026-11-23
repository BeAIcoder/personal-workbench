"""银行回单 A4 固定版式排版：每页固定槽位（建行/平安 3 笔、中行 2 笔），矢量裁切重排，页眉标明账户与笔数。"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import openpyxl
import pymupdf
from openpyxl.styles import Font, PatternFill

from ...utils_xlsx import safe_cell
from .parsers import CATEGORY_LABELS

A4_W, A4_H = 595.2756, 841.8898
PAGE_MARGIN_X = 10.0
PAGE_MARGIN_Y = 12.0
HEADER_H = 40.0
CAPACITY = {"建行": 3, "平安": 3, "中行": 2}
DEFAULT_CAPACITY = 3
HEADER_FONT = "china-s"
WIDE_LIMIT = 700.0
TALL_LIMIT = 500.0


@dataclass
class Group:
    bank: str
    account_no: str
    category: str
    items: list = field(default_factory=list)

    @property
    def folder(self) -> str:
        return f"{self.bank}{self.account_no[-4:]}" if self.account_no else self.bank

    @property
    def label(self) -> str:
        return CATEGORY_LABELS.get(self.category, self.category)


def capacity_for(bank: str) -> int:
    return CAPACITY.get(bank, DEFAULT_CAPACITY)


def _is_full_page(clip: list) -> bool:
    x0, y0, x1, y1 = clip
    return (x1 - x0) > WIDE_LIMIT or (y1 - y0) > TALL_LIMIT


def chunk_group(items: list, capacity: int) -> list[list]:
    """固定槽位分页：每页最多 capacity 笔，末页不足仍按同一版式；超大票独占一页。"""
    pages: list[list] = []
    buffer: list = []
    for item in items:
        if _is_full_page(item.clip):
            if buffer:
                pages.append(buffer)
                buffer = []
            pages.append([item])
            continue
        buffer.append(item)
        if len(buffer) == capacity:
            pages.append(buffer)
            buffer = []
    if buffer:
        pages.append(buffer)
    return pages


class SourceCache:
    """按来源缓存打开的 PDF（散放文件或 ZIP 成员），结束后统一关闭。"""

    def __init__(self) -> None:
        self._docs: dict[tuple[str, str], pymupdf.Document] = {}

    def open(self, item) -> pymupdf.Document:
        key = (item.source_path, item.source_member or "")
        doc = self._docs.get(key)
        if doc is not None:
            return doc
        path = Path(item.source_path)
        if not path.is_file():
            raise ValueError(f"源文件缺失或无法读取：{item.source_pdf}")
        try:
            if item.source_member:
                import zipfile

                with zipfile.ZipFile(path) as archive:
                    data = archive.read(item.source_member)
                doc = pymupdf.open(stream=data, filetype="pdf")
            else:
                doc = pymupdf.open(path)
        except Exception as exc:
            raise ValueError(f"源文件缺失或无法读取：{item.source_pdf}") from exc
        self._docs[key] = doc
        return doc

    def close(self) -> None:
        for doc in self._docs.values():
            doc.close()
        self._docs.clear()


def _draw_header(page: pymupdf.Page, group: Group, period: str, on_page: int, total_pages: int,
                 total_count: int) -> None:
    line1 = f"{group.bank}  账号 {group.account_no}  {group.label}"
    line2 = f"账期 {period}  本页 {on_page} 笔  类别共 {total_count} 笔  第 {page.number + 1}/{total_pages} 页"
    page.insert_text((PAGE_MARGIN_X + 2, PAGE_MARGIN_Y + 12), line1, fontname=HEADER_FONT, fontsize=9)
    page.insert_text((PAGE_MARGIN_X + 2, PAGE_MARGIN_Y + 25), line2, fontname=HEADER_FONT, fontsize=8)


def _place(page: pymupdf.Page, item, slot_top: float, slot_h: float, avail_w: float, src: pymupdf.Document) -> None:
    x0, y0, x1, y1 = item.clip
    w, h = x1 - x0, y1 - y0
    scale = min(avail_w / w, (slot_h - 6.0) / h)
    tw, th = w * scale, h * scale
    x = PAGE_MARGIN_X + (avail_w - tw) / 2
    y = slot_top + (slot_h - th) / 2
    page.show_pdf_page(pymupdf.Rect(x, y, x + tw, y + th), src, item.page,
                       clip=pymupdf.Rect(x0, y0, x1, y1))


def render_group(out_path: Path, group: Group, period: str, cache: SourceCache) -> int:
    """渲染一个账户×类别分册，返回页数。空分组由调用方跳过。"""
    chunks = chunk_group(group.items, capacity_for(group.bank))
    total_pages = len(chunks)
    total_count = len(group.items)
    doc = pymupdf.open()
    try:
        for chunk in chunks:
            full = len(chunk) == 1 and _is_full_page(chunk[0].clip)
            width, height = (A4_H, A4_W) if full else (A4_W, A4_H)
            page = doc.new_page(width=width, height=height)
            _draw_header(page, group, period, len(chunk), total_pages, total_count)
            top = PAGE_MARGIN_Y + HEADER_H
            avail_w = width - 2 * PAGE_MARGIN_X
            avail_h = height - top - PAGE_MARGIN_Y
            slots = 1 if full else capacity_for(group.bank)
            slot_h = avail_h / slots
            for slot_index, item in enumerate(chunk):
                src = cache.open(item)
                _place(page, item, top + slot_index * slot_h, slot_h, avail_w, src)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        doc.save(out_path, garbage=3, deflate=True)
    finally:
        doc.close()
    return total_pages


def write_index_xlsx(out_path: Path, period: str, rows: list[dict], summary: list[dict]) -> None:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "分类明细"
    headers = ["序号", "分类", "银行", "账号", "日期", "金额(元)", "付款人", "收款人", "用途", "回单号",
               "来源文件", "页码", "是否纳入"]
    header_fill = PatternFill("solid", fgColor="2F5597")
    for col, text in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col, value=text)
        cell.fill = header_fill
        cell.font = Font(bold=True, color="FFFFFF")
    for index, row in enumerate(rows, 2):
        values = [
            index - 1, row["label"], row["bank"], row["account_no"], row["rdate"],
            float(row["amount"]), row["payer"], row["payee"], row["purpose"], row["receipt_no"],
            row["source_pdf"], row["page"], "是" if row["included"] else "否",
        ]
        for col, value in enumerate(values, 1):
            ws.cell(row=index, column=col, value=safe_cell(value))
    ws.freeze_panes = "A2"
    for col, width in enumerate([6, 16, 8, 22, 12, 13, 22, 22, 26, 12, 30, 6, 8], 1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(col)].width = width

    sheet = wb.create_sheet("分类汇总")
    sheet.append(["账期", safe_cell(period)])
    sheet.append(["分类", "笔数", "金额合计(元)"])
    for item in summary:
        sheet.append([safe_cell(item["label"]), item["count"], float(item["total"])])
    for cell in sheet[2]:
        cell.font = Font(bold=True)
    sheet.column_dimensions["A"].width = 18
    sheet.column_dimensions["C"].width = 16
    out_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out_path)
