"""银行回单整理服务：扫描建批、逐笔复核、原单预览（只读源文件，写入仅限本模块表）。"""
from __future__ import annotations

import io
import os
import re
import shutil
import stat
import zipfile
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

import pymupdf
from sqlalchemy.orm import Session

from ...config import get_finance_data_dir, settings
from .models import ReceiptBatch, ReceiptItem
from .pdf_builder import Group, SourceCache, render_group, write_index_xlsx
from .parsers import (
    CATEGORIES,
    CATEGORY_LABELS,
    ParsedReceipt,
    SourceFile,
    deduplicate_receipts,
    discover_sources,
    parse_source,
)
from .schemas import ReceiptItemPatch, ReceiptScanIn

COUNT_KEYS = CATEGORIES + ("unclassified",)


def content_disposition(name: str, disposition: str = "attachment") -> str:
    """ASCII 回退名 + RFC 5987 的 UTF-8 文件名，中文名经代理链路不丢失。"""
    ascii_name = "".join(ch if ch.isascii() and ch not in '"\\' else "_" for ch in name) or "download"
    return f"{disposition}; filename=\"{ascii_name}\"; filename*=utf-8''{quote(name)}"


def config() -> dict:
    return {
        "categories": {key: CATEGORY_LABELS[key] for key in CATEGORIES},
        "default_output_dir": str(get_finance_data_dir() / "finance_receipt"),
        "configured_source_dir": (settings.finance_receipt_source_dir or "").strip(),
        "configured_output_dir": (settings.finance_receipt_output_dir or "").strip(),
    }


def _resolve_output_dir(raw: str) -> str:
    text = (raw or "").strip() or (settings.finance_receipt_output_dir or "").strip()
    if not text:
        return str(get_finance_data_dir() / "finance_receipt")
    return str(Path(text).expanduser())


def _item_out(item: ReceiptItem) -> dict:
    return {
        "id": item.id,
        "batch_id": item.batch_id,
        "category": item.category,
        "category_label": CATEGORY_LABELS.get(item.category, item.category),
        "auto_category": item.auto_category,
        "reviewed": bool(item.reviewed),
        "included": bool(item.included),
        "bank": item.bank,
        "account_no": item.account_no,
        "rdate": item.rdate.isoformat() if item.rdate else None,
        "amount": item.amount,
        "payer": item.payer,
        "payee": item.payee,
        "purpose": item.purpose,
        "receipt_no": item.receipt_no,
        "source_pdf": item.source_pdf,
        "page": item.page + 1,
    }


def _summary(items: list[ReceiptItem], duplicate_count: int) -> dict:
    counts = {key: 0 for key in COUNT_KEYS}
    for item in items:
        counts[item.category if item.category in counts else "unclassified"] += 1
    return {
        "total": len(items),
        "counts": counts,
        "excluded": sum(1 for item in items if not item.included),
        "pending_confirm": sum(1 for item in items if item.included and item.category == "unclassified"),
        "duplicates": duplicate_count,
    }


def _batch_out(batch: ReceiptBatch, items: list[ReceiptItem] | None = None) -> dict:
    out = {
        "id": batch.id,
        "period_label": batch.period_label,
        "start_date": batch.start_date.isoformat(),
        "end_date": batch.end_date.isoformat(),
        "status": batch.status,
        "source_dir": batch.source_dir,
        "output_dir": batch.output_dir,
        "failed_sources": list(batch.failed_sources or []),
        "warnings": list(batch.warnings or []),
        "files": list(batch.files or []),
        "error": batch.error or "",
        "created_at": batch.created_at.isoformat(timespec="seconds") if batch.created_at else "",
        "summary": _summary(items or [], batch.duplicate_count or 0),
    }
    if items is not None:
        out["items"] = [_item_out(item) for item in items]
    return out


def _load_items(db: Session, batch_id: int) -> list[ReceiptItem]:
    return (
        db.query(ReceiptItem)
        .filter(ReceiptItem.batch_id == batch_id)
        .order_by(ReceiptItem.rdate, ReceiptItem.account_no, ReceiptItem.id)
        .all()
    )


def _to_item(batch_id: int, receipt: ParsedReceipt) -> ReceiptItem:
    return ReceiptItem(
        batch_id=batch_id,
        category=receipt.category,
        auto_category=receipt.category,
        reviewed=False,
        included=True,
        bank=receipt.bank,
        account_no=receipt.account_no,
        rdate=receipt.rdate,
        amount=f"{Decimal(receipt.amount).quantize(Decimal('0.01')):.2f}",
        payer=receipt.payer[:200],
        payee=receipt.payee[:200],
        purpose=(receipt.purpose or receipt.kind)[:200],
        receipt_no=receipt.receipt_no[:80],
        source_path=receipt.source_path,
        source_member=receipt.source_member,
        source_pdf=receipt.source_pdf[:300],
        page=receipt.page,
        clip=list(receipt.clip),
    )


def scan(db: Session, body: ReceiptScanIn) -> dict:
    if body.start_date > body.end_date:
        raise ValueError("账期开始日期不能晚于结束日期")
    source_dir = Path(body.source_dir.strip()).expanduser()
    sources: list[SourceFile] = discover_sources(source_dir, body.start_date, body.end_date)
    if not sources:
        raise ValueError("该账期内未找到银行回单文件（请确认源目录与账期起止日期）")

    records: list[ParsedReceipt] = []
    failed: list[dict] = []
    warnings: list[str] = []
    for source in sources:
        try:
            parsed = parse_source(source)
        except ValueError as exc:
            failed.append({"name": source.path.name, "reason": str(exc)})
            continue
        warnings.extend(f"{source.path.name}：{text}" for text in source.warnings)
        records.extend(
            item for item in parsed
            if item.rdate is None or body.start_date <= item.rdate <= body.end_date
        )

    if not records and failed:
        names = "、".join(item["name"] for item in failed[:3])
        raise ValueError(f"所有回单文件均无法读取：{names}")

    unique, duplicates = deduplicate_receipts(records)
    batch = ReceiptBatch(
        period_label=body.period_label.strip(),
        start_date=body.start_date,
        end_date=body.end_date,
        status="scanned",
        source_dir=str(source_dir),
        output_dir=_resolve_output_dir(body.output_dir),
        failed_sources=failed,
        warnings=warnings[:200],
        duplicate_count=len(duplicates),
        created_at=datetime.now(),
    )
    db.add(batch)
    db.flush()
    for receipt in unique:
        db.add(_to_item(batch.id, receipt))
    db.commit()
    db.refresh(batch)
    return _batch_out(batch, _load_items(db, batch.id))


def get_batch(db: Session, batch_id: int) -> ReceiptBatch:
    batch = db.get(ReceiptBatch, batch_id)
    if batch is None:
        raise LookupError("回单整理批次不存在")
    return batch


def batch_detail(db: Session, batch_id: int) -> dict:
    batch = get_batch(db, batch_id)
    return _batch_out(batch, _load_items(db, batch.id))


def list_batches(db: Session, limit: int = 50) -> list[dict]:
    rows = db.query(ReceiptBatch).order_by(ReceiptBatch.id.desc()).limit(limit).all()
    return [_batch_out(row, _load_items(db, row.id)) | {"items": None} for row in rows]


def update_item(db: Session, batch_id: int, item_id: int, patch: ReceiptItemPatch) -> dict:
    get_batch(db, batch_id)
    item = db.get(ReceiptItem, item_id)
    if item is None or item.batch_id != batch_id:
        raise LookupError("回单明细不存在")
    if patch.category is not None:
        item.category = patch.category
    if patch.included is not None:
        item.included = patch.included
    item.reviewed = True
    db.commit()
    db.refresh(item)
    return _item_out(item)


def _source_bytes(item: ReceiptItem) -> bytes:
    path = Path(item.source_path)
    if not path.is_file():
        raise LookupError("源文件已不存在，请重新扫描后再预览")
    if not item.source_member:
        return path.read_bytes()
    import zipfile

    try:
        with zipfile.ZipFile(path) as archive:
            return archive.read(item.source_member)
    except (KeyError, zipfile.BadZipFile) as exc:
        raise LookupError("源文件已不存在或已变化，请重新扫描后再预览") from exc


def preview_item(db: Session, batch_id: int, item_id: int) -> bytes:
    get_batch(db, batch_id)
    item = db.get(ReceiptItem, item_id)
    if item is None or item.batch_id != batch_id:
        raise LookupError("回单明细不存在")
    data = _source_bytes(item)
    x0, y0, x1, y1 = item.clip
    src = pymupdf.open(stream=data, filetype="pdf")
    try:
        out = pymupdf.open()
        page = out.new_page(width=x1 - x0, height=y1 - y0)
        page.show_pdf_page(pymupdf.Rect(0, 0, x1 - x0, y1 - y0), src, item.page, clip=pymupdf.Rect(x0, y0, x1, y1))
        return out.tobytes(garbage=3, deflate=True)
    finally:
        src.close()


class ReceiptConflictError(RuntimeError):
    """生成前置条件不满足（如仍有待确认项），路由映射 409。"""


BATCH_DIR_PREFIX = "receipt_batch_"
XLSX_MEDIA = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _batch_dir(batch: ReceiptBatch) -> Path:
    return Path(batch.output_dir) / f"{BATCH_DIR_PREFIX}{batch.id}"


def _period_slug(label: str) -> str:
    return re.sub(r'[\\/:*?"<>|\s]+', "_", label or "").strip("_") or "账期"


def _force_rmtree(path: Path) -> None:
    def _on_error(func, target, _exc):
        try:
            os.chmod(target, stat.S_IWRITE)
            func(target)
        except OSError:
            pass

    shutil.rmtree(path, onerror=_on_error)


def _groups(items: list[ReceiptItem]) -> list[Group]:
    ordered = sorted(
        items,
        key=lambda i: (i.bank, i.account_no, CATEGORIES.index(i.category) if i.category in CATEGORIES else 99,
                       i.rdate or date.min, i.source_pdf, i.page),
    )
    groups: dict[tuple[str, str, str], Group] = {}
    for item in ordered:
        key = (item.bank, item.account_no, item.category)
        groups.setdefault(key, Group(item.bank, item.account_no, item.category)).items.append(item)
    return list(groups.values())


def generate(db: Session, batch_id: int) -> dict:
    batch = get_batch(db, batch_id)
    items = _load_items(db, batch.id)
    pending = sum(1 for item in items if item.included and item.category == "unclassified")
    if pending:
        raise ReceiptConflictError(f"仍有 {pending} 笔待确认（未分类），请先在复核表中确定分类或排除后再生成")
    included = [item for item in items if item.included]
    if not included:
        raise ValueError("没有纳入生成的回单，请至少保留一笔")

    period = _period_slug(batch.period_label)
    out_root = Path(batch.output_dir)
    final_dir = _batch_dir(batch)
    tmp_dir = out_root / f".receipt_tmp_{batch.id}_{uuid4().hex[:8]}"
    cache = SourceCache()
    files: list[dict] = []
    try:
        out_root.mkdir(parents=True, exist_ok=True)
        for group in _groups(included):
            name = f"{group.folder}_{group.label}_{period}.pdf"
            pages = render_group(tmp_dir / group.folder / name, group, batch.period_label, cache)
            total = sum(Decimal(item.amount) for item in group.items)
            files.append({
                "id": f"f{len(files) + 1:03d}",
                "name": name,
                "folder": group.folder,
                "kind": "category",
                "category": group.category,
                "label": group.label,
                "bank": group.bank,
                "account_no": group.account_no,
                "count": len(group.items),
                "pages": pages,
                "total": f"{total:.2f}",
                "size": (tmp_dir / group.folder / name).stat().st_size,
            })
        rows = [{
            "label": CATEGORY_LABELS.get(item.category, item.category),
            "bank": item.bank,
            "account_no": item.account_no,
            "rdate": item.rdate.isoformat() if item.rdate else "",
            "amount": item.amount,
            "payer": item.payer,
            "payee": item.payee,
            "purpose": item.purpose,
            "receipt_no": item.receipt_no,
            "source_pdf": item.source_pdf,
            "page": item.page + 1,
            "included": item.included,
        } for item in items]
        summary = []
        for key in CATEGORIES:
            subset = [item for item in included if item.category == key]
            summary.append({
                "label": CATEGORY_LABELS[key],
                "count": len(subset),
                "total": sum((Decimal(item.amount) for item in subset), Decimal("0")),
            })
        index_name = f"回单分类明细_{period}.xlsx"
        write_index_xlsx(tmp_dir / index_name, batch.period_label, rows, summary)
        files.append({
            "id": "index",
            "name": index_name,
            "folder": "",
            "kind": "index",
            "category": "",
            "label": "分类明细清单",
            "bank": "",
            "account_no": "",
            "count": len(items),
            "pages": 0,
            "total": "",
            "size": (tmp_dir / index_name).stat().st_size,
        })
    except Exception as exc:
        cache.close()
        if tmp_dir.exists():
            _force_rmtree(tmp_dir)
        batch.status = "failed"
        batch.error = str(exc)[:500]
        db.commit()
        raise
    cache.close()
    if final_dir.exists():
        _force_rmtree(final_dir)
    tmp_dir.rename(final_dir)
    batch.files = files
    batch.status = "ok"
    batch.error = ""
    db.commit()
    db.refresh(batch)
    return batch_detail(db, batch.id)


def _entry(batch: ReceiptBatch, file_id: str) -> dict:
    for entry in batch.files or []:
        if entry.get("id") == file_id:
            return entry
    raise LookupError("生成文件不存在，请重新生成")


def _entry_path(batch: ReceiptBatch, entry: dict) -> Path:
    path = _batch_dir(batch) / entry.get("folder", "") / entry["name"]
    if not path.is_file():
        raise LookupError("生成文件已被移动或删除，请重新生成")
    return path


def file_download(db: Session, batch_id: int, file_id: str) -> tuple[bytes, str, str]:
    batch = get_batch(db, batch_id)
    entry = _entry(batch, file_id)
    path = _entry_path(batch, entry)
    media = "application/pdf" if entry["kind"] == "category" else XLSX_MEDIA
    return path.read_bytes(), entry["name"], media


def merge_pdfs(db: Session, batch_id: int, file_ids: list[str]) -> bytes:
    batch = get_batch(db, batch_id)
    entries = []
    for file_id in file_ids:
        try:
            entry = _entry(batch, file_id)
        except LookupError as exc:
            raise ValueError(f"所选文件不在该批次产物清单内：{file_id}") from exc
        if entry["kind"] != "category":
            raise ValueError("仅可合并 PDF 分册，清单表请单独下载")
        entries.append(entry)
    out = pymupdf.open()
    try:
        for entry in entries:
            src = pymupdf.open(_entry_path(batch, entry))
            try:
                out.insert_pdf(src)
            finally:
                src.close()
        return out.tobytes(garbage=3, deflate=True)
    finally:
        out.close()


def archive_zip(db: Session, batch_id: int) -> tuple[bytes, str]:
    batch = get_batch(db, batch_id)
    if not batch.files:
        raise ValueError("该批次尚未生成产物，请先生成")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as archive:
        for entry in batch.files:
            path = _entry_path(batch, entry)
            arcname = f"{entry['folder']}/{entry['name']}" if entry.get("folder") else entry["name"]
            archive.write(path, arcname=arcname)
    return buf.getvalue(), f"银行回单整理_{_period_slug(batch.period_label)}.zip"


def receipt_summary_tool() -> dict:
    """银行回单整理摘要（只读 Agent 工具）。"""
    from ...database import SessionLocal

    with SessionLocal() as db:
        total = db.query(ReceiptBatch).count()
        latest = db.query(ReceiptBatch).order_by(ReceiptBatch.id.desc()).first()
        latest_view = None
        if latest is not None:
            items = _load_items(db, latest.id)
            latest_view = {
                "id": latest.id,
                "period_label": latest.period_label,
                "status": latest.status,
                "summary": _summary(items, latest.duplicate_count or 0),
            }
    return {"batches": total, "latest": latest_view}
