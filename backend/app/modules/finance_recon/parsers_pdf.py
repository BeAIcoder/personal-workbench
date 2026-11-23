# -*- coding: utf-8 -*-
"""账单付款通知单 PDF 解析（kind=bill）。

- 只支持有文本层的付款通知单；无文本层（疑似扫描件）明确失败，本模块不做 OCR；
- 文本锚点集中声明为常量（累计应付等），新版式需在 BILL_* 常量处显式扩展；
- 「累计应付」金额：锚点同行冒号后数值优先，否则取锚点行之后的下一数字行
  （参照原 recon_check.read_bills 的锚点-下一行做法，但失败不再静默吞掉）；
- 单 PDF 仅允许一张账单：出现多个「累计应付」锚点即拒绝（不静默取最后一个）；
- 必需字段：账单编号（doc_no）、合同编号（contract_no）、累计应付（amount_1）；
  任一缺失/同标签行无值明确失败，不猜值。

日期/金额文本解析复用 parsers_xlsx 的共享工具（单向依赖，无环）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import pymupdf

from .parsers_xlsx import parse_amount_text, parse_date_text
from .schemas import BillDetails, NormalizedRecord, ParseSourceError

# ---------- 文本锚点常量（集中声明；新增版式在此显式扩展） ----------

BILL_ANCHOR_TOTAL = "累计应付"  # 金额锚点：同行冒号后数值，否则下一数字行
BILL_REQUIRED_LABELS: dict[str, tuple[str, ...]] = {
    "doc_no": ("账单编号", "账单号"),
    "contract_no": ("合同编号", "合同号"),
}
BILL_OPTIONAL_LABELS: dict[str, tuple[str, ...]] = {
    "bill_month": ("账单月", "出账月", "账期"),
    "issue_date": ("出账日期", "账单日期"),
    "due_date": ("缴费截止", "缴费截止日", "缴费截止日期"),
}
# 标签后的取值片段：到首个空白/冒号/标点分隔符为止（含各类括号——括号内的
# 说明文字不是标识，如「账单编号：B001（含税）」只取 B001）
_VALUE_TOKEN = r"[^\s:：，,；;（）()【】「」『』〔〕《》<>]+"
# 标识合法性：至少含一个文字字符（\w 覆盖中英文与数字）；纯标点/符号串
# （如「——」「…」）不是标识，视同“标签后无值”
_PLAUSIBLE_TOKEN = re.compile(r"\w")


@dataclass(frozen=True)
class _Pctx:
    """PDF 报错定位（复用 parse_amount_text/parse_date_text 的 ctx 形参）。"""

    file: str
    page: int
    what: str

    def __str__(self) -> str:
        return f"PDF「{self.file}」第{self.page}页{self.what}"


def _pdf_lines(path: Path) -> list[tuple[int, str]]:
    """按页提取非空文本行，返回 (页码 1-based, 行文本)。无文本层在此显式失败。"""
    try:
        doc = pymupdf.open(path)
    except Exception as exc:  # noqa: BLE001
        raise ParseSourceError(f"无法打开 PDF 文件「{path.name}」：{exc}") from exc
    try:
        if doc.page_count == 0:
            raise ParseSourceError(f"PDF 文件「{path.name}」没有页面，无法解析")
        lines: list[tuple[int, str]] = []
        total_text = 0
        for page_no, page in enumerate(doc, start=1):
            text = page.get_text("text")
            total_text += len(text.strip())
            for line in text.splitlines():
                stripped = line.strip()
                if stripped:
                    lines.append((page_no, stripped))
        if total_text == 0:
            raise ParseSourceError(
                f"PDF 文件「{path.name}」无文本层（疑似扫描件）。"
                "本模块不支持 OCR，请提供有文本层的付款通知单（如从源系统导出的 PDF）"
            )
        return lines
    finally:
        doc.close()


def _line_label_value(line: str, labels: tuple[str, ...]) -> tuple[str, str] | None:
    """返回 (命中的标签, 标签后的取值片段)。

    取值规则：括号内说明不是标识（截断于首个括号/标点）；纯标点/符号串
    （无任何文字字符）视同“标签后无值”，不得当标识入库。
    """
    for label in labels:
        idx = line.find(label)
        if idx < 0:
            continue
        tail = line[idx + len(label):]
        match = re.match(rf"\s*[:：]?\s*({_VALUE_TOKEN})", tail)
        token = match.group(1) if match else ""
        if token and not _PLAUSIBLE_TOKEN.search(token):
            token = ""
        return label, token
    return None


def _first_label_hit(lines: list[tuple[int, str]], labels: tuple[str, ...]) -> tuple[int, str, str] | None:
    for page, line in lines:
        hit = _line_label_value(line, labels)
        if hit is not None:
            return page, hit[0], hit[1]
    return None


def _extract_total(lines: list[tuple[int, str]], file: str) -> tuple[int, Decimal]:
    """「累计应付」金额：同行取值优先，否则取下一非空行；失败显式报错。

    同行取值与标识字段不同：锚点之后的**完整金额文本**（含千分位逗号）直接走
    严格 `parse_amount_text`，不经过为标识设计的取值片段——「累计应付：12,345.67」
    必须解析为 12345.67，而不是在首个逗号处截断成 12（首轮缺陷，已修复）。
    同行文本含数字时即认定为金额行：非法后缀（如「元」「（含税）」）显式失败，
    不回退到下一行；同行无数字（空或纯说明如「（详见附表）」）才看下一行。
    """
    anchors = [(page, idx) for idx, (page, line) in enumerate(lines) if BILL_ANCHOR_TOTAL in line]
    if not anchors:
        raise ParseSourceError(
            f"PDF 文件「{file}」缺少「{BILL_ANCHOR_TOTAL}」锚点："
            "当前版本仅支持含该锚点的付款通知单，新版式需扩展解析器"
        )
    if len(anchors) > 1:
        pages = "、".join(f"第{page}页" for page, _ in anchors)
        raise ParseSourceError(
            f"PDF 文件「{file}」含 {len(anchors)} 个「{BILL_ANCHOR_TOTAL}」锚点（{pages}），"
            "疑似多张账单合并；当前版本仅支持单账单付款通知单，请逐张上传"
        )
    page, idx = anchors[0]
    anchor_line = lines[idx][1]
    pos = anchor_line.find(BILL_ANCHOR_TOTAL)
    tail = anchor_line[pos + len(BILL_ANCHOR_TOTAL):].lstrip(" ：:").strip()
    if tail and any(ch.isdigit() for ch in tail):
        # 同行即金额行：完整金额文本严格解析，非法后缀直接上抛（不回退）
        where = _Pctx(file, page, f"「{BILL_ANCHOR_TOTAL}」同行金额「{tail[:40]}」")
        return page, parse_amount_text(tail, where)
    for next_page, next_line in lines[idx + 1:]:
        where = _Pctx(file, next_page, f"「{BILL_ANCHOR_TOTAL}」锚点后金额行「{next_line[:40]}」")
        try:
            amount = parse_amount_text(next_line, where)
        except ParseSourceError as exc:
            raise ParseSourceError(
                f"PDF 文件「{file}」第{next_page}页「{BILL_ANCHOR_TOTAL}」锚点后的"
                f"金额行无法解析：{exc}"
            ) from exc
        if amount is not None:
            return next_page, amount
        break  # 只看锚点后的下一非空行
    raise ParseSourceError(
        f"PDF 文件「{file}」第{page}页「{BILL_ANCHOR_TOTAL}」锚点后未找到可解析的金额行"
    )


def parse_bill_pdf(path: Path) -> list[NormalizedRecord]:
    """账单 PDF 解析入口：单账单付款通知单 → 单条标准化事实行。"""
    if path.suffix.lower() != ".pdf":
        raise ParseSourceError(f"文件「{path.name}」不是 .pdf（账单源仅支持 PDF 付款通知单）")
    if not path.is_file():
        raise ParseSourceError(f"文件不存在：{path.name}")
    lines = _pdf_lines(path)
    file_name = path.name

    missing: list[str] = []
    values: dict[str, str] = {}
    for role, labels in BILL_REQUIRED_LABELS.items():
        hit = _first_label_hit(lines, labels)
        if hit is None:
            missing.append("、".join(labels))
            continue
        page, label, value = hit
        if not value:
            raise ParseSourceError(f"PDF 文件「{file_name}」第{page}页「{label}」后未找到编号值")
        values[role] = value
    if missing:
        raise ParseSourceError(
            f"PDF 文件「{file_name}」缺少必需字段：{'、'.join(missing)}"
            "（当前版本按集中声明的文本锚点解析，新版式需扩展解析器）"
        )

    total_page, total = _extract_total(lines, file_name)

    bill_month = None
    issue_date = None
    due_date = None
    for role, labels in BILL_OPTIONAL_LABELS.items():
        hit = _first_label_hit(lines, labels)
        if hit is None:
            continue
        page, label, value = hit
        if not value:
            raise ParseSourceError(f"PDF 文件「{file_name}」第{page}页「{label}」后未找到取值")
        if role == "issue_date":
            issue_date = parse_date_text(value, _Pctx(file_name, page, f"「{label}」"))
        elif role == "due_date":
            due_date = parse_date_text(value, _Pctx(file_name, page, f"「{label}」"))
        else:
            bill_month = value

    details = BillDetails(bill_month=bill_month, issue_date=issue_date).model_dump(
        mode="json", exclude_none=True
    )
    return [
        NormalizedRecord(
            kind="bill",
            row_no=1,
            contract_no=values["contract_no"],
            doc_no=values["doc_no"],
            occur_date=issue_date,
            due_date=due_date,
            amount_1=total,
            details=details,
        )
    ]
