from __future__ import annotations

import calendar
import re
import zipfile
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

import pymupdf

CCB_HEADER = "中国建设银行单位客户专用回单"
BOC_HEADERS = ("国内支付业务收款回单", "国内支付业务付款回单", "客户付费回单", "利息收入回单")
PINGAN_HEADERS = ("收付款业务回单", "收费回单(付款通知)", "协定存款利息回单")
CATEGORIES = (
    "pending_clearing_income",
    "transfer_income",
    "transfer_expense",
    "bank_interest_income",
    "bank_fee_expense",
)
CATEGORY_LABELS = {
    "pending_clearing_income": "待清算回单收入",
    "transfer_income": "正常转账收入",
    "transfer_expense": "转账支出",
    "bank_interest_income": "银行结息收入",
    "bank_fee_expense": "银行手续费支出",
    "unclassified": "待确认",
}
INCOME_CATEGORIES = {"pending_clearing_income", "transfer_income", "bank_interest_income"}


@dataclass
class SourceFile:
    path: Path
    bank: str
    account_hint: str = ""
    warnings: list[str] = field(default_factory=list)


@dataclass
class ParsedReceipt:
    bank: str = ""
    account_no: str = ""
    category: str = "unclassified"
    kind: str = ""
    direction: str = ""
    rdate: date | None = None
    amount: Decimal = Decimal("0")
    payer: str = ""
    payee: str = ""
    purpose: str = ""
    receipt_no: str = ""
    source_path: str = ""
    source_pdf: str = ""
    source_member: str = ""
    page: int = 0
    clip: tuple[float, float, float, float] = (0, 0, 0, 0)
    raw: str = ""

    @property
    def account_tail(self) -> str:
        return self.account_no[-4:] if self.account_no else ""

    @property
    def counterparty(self) -> str:
        if self.category in INCOME_CATEGORIES:
            return self.payer or self.payee
        return self.payee or self.payer


@dataclass(frozen=True)
class _Block:
    top: float
    bottom: float
    header: str


def _money(value: str | None) -> Decimal:
    if not value:
        return Decimal("0")
    try:
        return Decimal(value.replace(",", "")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        return Decimal("0")


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def _content_bottom(page: pymupdf.Page, top: float, bottom: float) -> float:
    content_bottom = top
    for block in page.get_text("blocks"):
        if block[3] > top and block[1] < bottom:
            content_bottom = max(content_bottom, block[3])
    for drawing in page.get_drawings():
        rect = drawing["rect"]
        if rect.y1 > top and rect.y0 < bottom:
            content_bottom = max(content_bottom, rect.y1)
    for info in page.get_image_info():
        rect = pymupdf.Rect(info["bbox"])
        if rect.y1 > top and rect.y0 < bottom:
            content_bottom = max(content_bottom, rect.y1)
    if content_bottom <= top:
        return bottom
    return min(bottom, max(top + 40.0, content_bottom + 2.0))


def _find_blocks(page: pymupdf.Page, headers: tuple[str, ...], bottom_gap: float) -> list[_Block]:
    hits: list[tuple[float, str]] = []
    for header in headers:
        hits.extend((rect.y0, header) for rect in page.search_for(header))
    hits.sort(key=lambda item: item[0])
    blocks: list[_Block] = []
    previous_bottom = 0.0
    for index, (header_y, header) in enumerate(hits):
        top = max(previous_bottom, header_y - 12.0, 0.0)
        if index + 1 < len(hits):
            bottom = min(page.rect.height, hits[index + 1][0] - bottom_gap)
        else:
            bottom = _content_bottom(page, top, page.rect.height - 4.0)
        if bottom > top:
            blocks.append(_Block(top, bottom, header))
            previous_bottom = bottom
    return blocks


def _find_account(text: str, hint: str = "") -> str:
    accounts = re.findall(r"(?<!\d)\d{12,24}(?!\d)", text)
    if hint:
        for account in accounts:
            if account == hint or account.endswith(hint):
                return account
        if len(hint) >= 12:
            return hint
    labelled = re.search(r"(?:收款人账号|付款人账号|存款账号)[：:]\s*(\d{12,24})", text)
    if labelled:
        return labelled.group(1)
    return accounts[0] if accounts else hint


def _ccb_amount(text: str) -> Decimal:
    for pattern in (
        r"[（(]小写[）)]\s*[￥¥]\s*([\d,]+\.\d{2})",
        r"小写[（(]合计[）)]金额[：:]\s*[￥¥]\s*([\d,]+\.\d{2})",
    ):
        match = re.search(pattern, text)
        if match:
            return _money(match.group(1))
    match = re.search(r"[￥¥]\s*([\d,]+\.\d{2})", text)
    return _money(match.group(1)) if match else Decimal("0")


def _ccb_date(text: str) -> date | None:
    match = re.search(r"日\s*(\d{4})\s*(\d{2})\s*(\d{2})", text)
    if not match:
        return None
    try:
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return None


def _party(text: str, side: str) -> str:
    match = re.search(rf"{side}\s*款\s*人\s*全\s*称[：:]?\s*\n?\s*([^\n]+)", text)
    return match.group(1).strip().lstrip("：:　 ") if match else ""


def _purpose_ccb(text: str) -> str:
    match = re.search(r"用\s*途\s*\n?\s*(.+?)(?:\n交易笔数|\n打印柜员|\n$)", text, re.S)
    if not match:
        return ""
    purpose = _compact(match.group(1))
    return re.split(r"交易日期|支付清算|业务类型|附言|实际收款人|汇款备注|收款人账号", purpose)[0][:80]


def classify_receipt(receipt: ParsedReceipt) -> str:
    text = _compact(receipt.raw)
    if "待清算" in _compact(receipt.payer) or "收单清算款" in text:
        return "pending_clearing_income"
    if any(word in text for word in ("活期利息", "利息收入回单", "协定存款利息回单")):
        return "bank_interest_income"
    is_fee = (
        "客户付费回单" in text
        or "收费回单(付款通知)" in text
        or "银企直连手续费" in text
        or ("手续费" in text and any(word in text for word in ("费用名称", "工本费/转账汇款手续费/手续费", "项目名称")))
    )
    if is_fee:
        return "bank_fee_expense"
    if any(word in text for word in ("电子缴税付款凭证", "纳税人全称", "缴款书交易流水号")):
        return "transfer_expense"
    if receipt.direction == "income":
        return "transfer_income"
    if receipt.direction == "expense":
        return "transfer_expense"
    return "unclassified"


def _kind_for(receipt: ParsedReceipt) -> str:
    if receipt.kind:
        return receipt.kind
    return {
        "pending_clearing_income": "收单清算回单",
        "transfer_income": "收款回单",
        "transfer_expense": "付款回单",
        "bank_interest_income": "存款利息回单",
        "bank_fee_expense": "收费回单",
        "unclassified": "待确认回单",
    }[receipt.category]


def _finalize(receipt: ParsedReceipt) -> ParsedReceipt:
    receipt.category = classify_receipt(receipt)
    receipt.kind = _kind_for(receipt)
    if receipt.category == "bank_fee_expense":
        receipt.payee = receipt.payee or "银行手续费"
        receipt.direction = "expense"
    elif receipt.category == "bank_interest_income":
        receipt.payer = receipt.payer or "银行利息"
        receipt.direction = "income"
    elif receipt.category == "pending_clearing_income":
        receipt.direction = "income"
    receipt.purpose = receipt.purpose or receipt.kind
    return receipt


def _parse_ccb(doc: pymupdf.Document, source: SourceFile, member: str = "") -> list[ParsedReceipt]:
    records: list[ParsedReceipt] = []
    for page_no, page in enumerate(doc):
        blocks = _find_blocks(page, (CCB_HEADER,), bottom_gap=10.0)
        if not blocks:
            full_text = page.get_text()
            if "电子缴税付款凭证" not in full_text:
                source.warnings.append(f"第 {page_no + 1} 页未识别到建行回单表头")
                continue
            blocks = [_Block(0, page.rect.height, "电子缴税付款凭证")]
        for block in blocks:
            clip = pymupdf.Rect(0, block.top, page.rect.width, block.bottom)
            text = page.get_text(clip=clip)
            if not text.strip():
                continue
            account = _find_account(text, source.account_hint)
            compact = _compact(text)
            direction = ""
            if "贷方回单" in compact or "收款人回单" in compact:
                direction = "income"
            elif "借方回单" in compact or "付款人回单" in compact:
                direction = "expense"
            no = re.search(r"No[：:]\s*(\d+)", text)
            if block.header == "电子缴税付款凭证":
                no = re.search(r"凭证字号[：:]\s*(\w+)", text)
            receipt = ParsedReceipt(
                bank="建行",
                account_no=account,
                kind="电子缴税付款凭证" if block.header == "电子缴税付款凭证" else "",
                direction=direction,
                rdate=_ccb_date(text),
                amount=_ccb_amount(text),
                payer=_party(text, "付"),
                payee=_party(text, "收"),
                purpose=_purpose_ccb(text),
                receipt_no=no.group(1) if no else "",
                source_path=str(source.path),
                source_pdf=source.path.name if not member else f"{source.path.name}::{member}",
                source_member=member,
                page=page_no,
                clip=(0, block.top, page.rect.width, block.bottom),
                raw=text,
            )
            records.append(_finalize(receipt))
    return records


def _boc_names(text: str) -> tuple[str, str]:
    lines = [line.strip() for line in text.splitlines()]
    account_indexes = [i for i, line in enumerate(lines) if re.search(r"(?:付款人账号|收款人账号)[：:]", line)]
    if not account_indexes:
        return "", ""
    start = account_indexes[0] + 1
    if len(account_indexes) > 1 and account_indexes[1] == start:
        start += 1
    names: list[str] = []
    for line in lines[start:]:
        if "人名称" in line or line.startswith("金额"):
            break
        if line:
            names.append(line)
    own = names[0] if names else ""
    other = "".join(names[1:]) if len(names) > 1 else ""
    return own, other


def _parse_boc(doc: pymupdf.Document, source: SourceFile, member: str = "") -> list[ParsedReceipt]:
    records: list[ParsedReceipt] = []
    for page_no, page in enumerate(doc):
        blocks = _find_blocks(page, BOC_HEADERS, bottom_gap=16.0)
        if not blocks:
            source.warnings.append(f"第 {page_no + 1} 页未识别到中行回单表头")
        for block in blocks:
            clip = pymupdf.Rect(0, block.top, page.rect.width, block.bottom)
            text = page.get_text(clip=clip)
            if not text.strip():
                continue
            date_match = re.search(r"日期[：:]\s*(\d{4})年(\d{2})月(\d{2})日", text)
            amount_match = re.search(r"金额[：:]\s*CNY\s*([\d,]+\.?\d*)", text)
            no_match = re.search(r"回单编号[：:]\s*(\w+)", text)
            own, other = _boc_names(text)
            direction = "income" if block.header in ("国内支付业务收款回单", "利息收入回单") else "expense"
            purpose_match = re.search(r"用\s*途[：:]\s*\n?([^\n]+)", text)
            receipt = ParsedReceipt(
                bank="中行",
                account_no=_find_account(text, source.account_hint),
                kind={
                    "国内支付业务收款回单": "收款回单",
                    "国内支付业务付款回单": "付款回单",
                    "客户付费回单": "客户付费回单",
                    "利息收入回单": "存款利息回单",
                }[block.header],
                direction=direction,
                rdate=date(int(date_match.group(1)), int(date_match.group(2)), int(date_match.group(3))) if date_match else None,
                amount=_money(amount_match.group(1)) if amount_match else Decimal("0"),
                payer=other if direction == "income" else own,
                payee=own if direction == "income" else other,
                purpose=purpose_match.group(1).strip() if purpose_match else "",
                receipt_no=no_match.group(1) if no_match else "",
                source_path=str(source.path),
                source_pdf=source.path.name if not member else f"{source.path.name}::{member}",
                source_member=member,
                page=page_no,
                clip=(0, block.top, page.rect.width, block.bottom),
                raw=text,
            )
            records.append(_finalize(receipt))
    return records


def _pingan_field(text: str, field_name: str) -> str:
    match = re.search(rf"{field_name}[：:]\s*\n?\s*([^\n]+)", text)
    return match.group(1).strip() if match else ""


def _parse_pingan(doc: pymupdf.Document, source: SourceFile, member: str = "") -> list[ParsedReceipt]:
    records: list[ParsedReceipt] = []
    for page_no, page in enumerate(doc):
        blocks = _find_blocks(page, PINGAN_HEADERS, bottom_gap=10.0)
        if not blocks:
            source.warnings.append(f"第 {page_no + 1} 页未识别到平安回单表头")
        for block in blocks:
            clip = pymupdf.Rect(0, block.top, page.rect.width, block.bottom)
            text = page.get_text(clip=clip)
            if not text.strip():
                continue
            account = _find_account(text, source.account_hint)
            payer_account = _pingan_field(text, "付款人账号")
            payee_account = _pingan_field(text, "收款人账号")
            direction = ""
            if account and payer_account == account:
                direction = "expense"
            elif account and payee_account == account:
                direction = "income"
            date_match = re.search(r"记账日期[：:]\s*\n?\s*(\d{4}-\d{2}-\d{2})", text)
            receipt = ParsedReceipt(
                bank="平安",
                account_no=account,
                kind="存款利息回单" if block.header == "协定存款利息回单" else "收费回单" if block.header == "收费回单(付款通知)" else "",
                direction=direction,
                rdate=date.fromisoformat(date_match.group(1)) if date_match else None,
                amount=_money(_pingan_field(text, "小写")),
                payer=_pingan_field(text, "付款人名称"),
                payee=_pingan_field(text, "收款人名称"),
                purpose=_pingan_field(text, r"备\s*注"),
                receipt_no=_pingan_field(text, "回单号"),
                source_path=str(source.path),
                source_pdf=source.path.name if not member else f"{source.path.name}::{member}",
                source_member=member,
                page=page_no,
                clip=(0, block.top, page.rect.width, block.bottom),
                raw=text,
            )
            records.append(_finalize(receipt))
    return records


def _open_pdf(path: Path) -> pymupdf.Document:
    try:
        doc = pymupdf.open(path)
    except Exception as exc:
        raise ValueError(f"PDF 回单无法读取：{path.name}（{exc}）") from exc
    if doc.needs_pass:
        doc.close()
        raise ValueError(f"PDF 回单已加密，请先解密：{path.name}")
    return doc


def parse_source(source: SourceFile) -> list[ParsedReceipt]:
    parser = {"建行": _parse_ccb, "中行": _parse_boc, "平安": _parse_pingan}.get(source.bank)
    if parser is None:
        raise ValueError(f"暂不支持该银行回单：{source.path.name}")
    if source.path.suffix.lower() != ".zip":
        doc = _open_pdf(source.path)
        try:
            records = parser(doc, source)
        finally:
            doc.close()
    else:
        records = []
        try:
            with zipfile.ZipFile(source.path) as archive:
                members = [name for name in archive.namelist() if name.lower().endswith(".pdf")]
                if not members:
                    raise ValueError(f"ZIP 中未找到 PDF 回单：{source.path.name}")
                for member in members:
                    try:
                        doc = pymupdf.open("pdf", archive.read(member))
                    except Exception as exc:
                        raise ValueError(f"ZIP 内 PDF 无法读取：{source.path.name}::{member}（{exc}）") from exc
                    if doc.needs_pass:
                        doc.close()
                        raise ValueError(f"ZIP 内 PDF 已加密：{source.path.name}::{member}")
                    try:
                        records.extend(parser(doc, source, member))
                    finally:
                        doc.close()
        except zipfile.BadZipFile as exc:
            raise ValueError(f"ZIP 回单包无法读取：{source.path.name}") from exc
    for receipt in records:
        if receipt.rdate is None:
            source.warnings.append(f"第 {receipt.page + 1} 页有回单缺少有效交易日期")
        if receipt.amount == 0:
            source.warnings.append(f"第 {receipt.page + 1} 页有回单未识别到金额")
    return records


def _file_date_range(name: str) -> tuple[date, date] | None:
    match = re.search(
        r"(20\d{2})\.(\d{1,2})(?:\.(\d{1,2}))?(?:\s*[-–]\s*(?:(20\d{2})\.)?(\d{1,2})(?:\.(\d{1,2}))?)?",
        name,
    )
    if not match:
        return None
    start_year, start_month = int(match.group(1)), int(match.group(2))
    end_year = int(match.group(4) or start_year)
    end_month = int(match.group(5) or start_month)
    start_day = int(match.group(3) or 1)
    end_day = int(match.group(6) or calendar.monthrange(end_year, end_month)[1])
    try:
        return date(start_year, start_month, start_day), date(end_year, end_month, end_day)
    except ValueError:
        return None


def _zip_date_range(name: str) -> tuple[date, date] | None:
    match = re.search(r"_(\d{8})-(\d{8})_", name)
    if not match:
        return None
    try:
        return date.fromisoformat(f"{match.group(1)[:4]}-{match.group(1)[4:6]}-{match.group(1)[6:]}"), date.fromisoformat(
            f"{match.group(2)[:4]}-{match.group(2)[4:6]}-{match.group(2)[6:]}"
        )
    except ValueError:
        return None


def discover_sources(source_dir: Path, start: date, end: date) -> list[SourceFile]:
    if start > end:
        raise ValueError("账期开始日期不能晚于结束日期")
    if not source_dir.is_dir():
        raise ValueError(f"回单源目录不存在或不是目录：{source_dir}")
    regular: list[SourceFile] = []
    bundles: list[SourceFile] = []
    direct_pingan: list[SourceFile] = []
    for path in sorted(source_dir.iterdir(), key=lambda item: item.name):
        if not path.is_file() or "对账单" in path.name or "待清算刷卡回单" in path.name:
            continue
        lower_name = path.name.lower()
        if lower_name.endswith("回单.pdf") and ("建行" in path.name or "中行" in path.name):
            file_range = _file_date_range(path.name)
            if file_range and file_range[0] <= end and file_range[1] >= start:
                bank = "建行" if "建行" in path.name else "中行"
                hint_match = re.search(r"(\d{4})\s*建行", path.name)
                regular.append(SourceFile(path, bank, hint_match.group(1) if hint_match else ""))
            continue
        if lower_name.endswith(".zip"):
            zip_range = _zip_date_range(path.name)
            account_match = re.search(r"(?<!\d)(\d{14})(?!\d)", path.name)
            if zip_range and account_match and zip_range[0] <= end and zip_range[1] >= start:
                bundles.append(SourceFile(path, "平安", account_match.group(1)))
            continue
        if lower_name.endswith(".pdf") and path.name.startswith("平安银行"):
            account_match = re.search(r"(?<!\d)(\d{14})(?!\d)", path.name)
            if account_match:
                direct_pingan.append(SourceFile(path, "平安", account_match.group(1)))
    return regular + bundles + direct_pingan


def deduplicate_receipts(records: list[ParsedReceipt]) -> tuple[list[ParsedReceipt], list[ParsedReceipt]]:
    unique: list[ParsedReceipt] = []
    duplicates: list[ParsedReceipt] = []
    seen: dict[tuple, ParsedReceipt] = {}
    for receipt in records:
        if receipt.receipt_no:
            key = (receipt.bank, receipt.account_no, receipt.receipt_no)
        else:
            key = (
                receipt.bank,
                receipt.account_no,
                receipt.rdate,
                receipt.amount,
                receipt.direction,
                receipt.payer,
                receipt.payee,
                receipt.purpose,
            )
        if key in seen:
            duplicates.append(receipt)
            continue
        seen[key] = receipt
        unique.append(receipt)
    return unique, duplicates
