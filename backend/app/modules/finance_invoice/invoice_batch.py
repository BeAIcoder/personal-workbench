"""finance_invoice 批次层：数据目录、上传落盘、批次状态机、心跳与崩溃恢复、查询与人工修正。

职责：
- 数据目录 get_finance_data_dir()/finance_invoice/{inbox,outbox,tmp} 与批次产物路径；
- 上传约束（扩展名白名单 + 大小/数量限制）、ZIP 解包（防路径穿越）、PDF 落盘；
- 建批次并解析：uploaded → parsed，失败留 failed 记录并清理半成品（BUGfix INV-03）；
- 解析期心跳与崩溃遗留批次恢复；批次/明细查询、删除、人工修正、作废旧导出；
- 项目汇总、整批下载文件名、API 输出组装与 Agent 工具 invoice_batch_stats。

不含：字段解析与金额/日期/文件名口径（invoice_parse）、去重与冲红配对（invoice_pairing）、
项目归类（invoice_classify）、导出打包（invoice_export）。
"""
from __future__ import annotations

import io
import json
import logging
import re
import shutil
import threading
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from ...config import get_finance_data_dir
from ...database import SessionLocal
from . import invoice_parse as parse_mod
from . import projects as project_rules
from .invoice_classify import classify_items
from .invoice_pairing import (
    _external_pair_candidates,
    _refresh_red_counts,
    amount_warnings,
    existing_dedup_keys,
    mark_duplicates,
    pair_red_invoices,
)
from .invoice_parse import (
    _ACTIVE_ITEM_STATUS,
    _date_parts,
    _item_project_name,
    _money,
    _months_suffix,
    parse_amount,
    project_pdf_name,
)
from .models import InvoiceBatch, InvoiceItem

logger = logging.getLogger(__name__)

# ---- 上传约束（扩展名白名单 + 大小限制，与宿主上传口径一致）----
ALLOWED_SUFFIXES = {".pdf", ".zip"}
MAX_FILE_BYTES = 20 * 1024 * 1024  # 单文件 20MB
MAX_BATCH_BYTES = 100 * 1024 * 1024  # 单批次合计 100MB（ZIP 按解压后口径）
MAX_FILES_PER_BATCH = 200

# Windows 落盘安全：非法字符 / 设备保留名（ZIP 成员与导出文件同口径）
_WIN_ILLEGAL_CHARS = set('<>:"/\\|?*')
_RESERVED_NAMES = {"CON", "PRN", "AUX", "NUL"} | {
    f"{prefix}{i}" for prefix in ("COM", "LPT") for i in range(1, 10)
}

# 崩溃恢复：超过阈值仍无心跳的 uploaded 批次视为中断
STALE_BATCH_SECONDS = 30 * 60
# 解析期心跳间隔：长解析每 60s 刷新 updated_at，恢复逻辑据此区分「还在跑」与「已崩溃」
HEARTBEAT_INTERVAL_SECONDS = 60


# ---------------- 数据目录 ----------------

def module_data_dir() -> Path:
    """模块数据目录：get_finance_data_dir()/finance_invoice。"""
    return get_finance_data_dir() / "finance_invoice"


def _ensure_dirs() -> dict[str, Path]:
    base = module_data_dir()
    dirs = {"inbox": base / "inbox", "outbox": base / "outbox", "tmp": base / "tmp"}
    for d in dirs.values():
        d.mkdir(parents=True, exist_ok=True)
    return dirs


def _project_zips_dir(batch_id: int) -> Path:
    return _ensure_dirs()["outbox"] / f"batch_{batch_id}_projects"


def _project_manifest_path(batch_id: int) -> Path:
    return _ensure_dirs()["outbox"] / f"batch_{batch_id}_projects.json"


def _read_project_manifest(batch_id: int) -> dict[str, str]:
    """读导出时写下的 项目名 → 单项目 ZIP 文件名 映射；缺失/损坏返回空字典。"""
    path = _project_manifest_path(batch_id)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning("批次 %s 项目 ZIP 清单读取失败", batch_id)
        return {}
    return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}


def _active_items(db: Session, batch: InvoiceBatch) -> list[InvoiceItem]:
    """批次内有效明细（parsed/incomplete，含红冲，排除 duplicate/failed），按明细 ID 升序。"""
    return (
        db.query(InvoiceItem)
        .filter(InvoiceItem.batch_id == batch.id, InvoiceItem.status.in_(_ACTIVE_ITEM_STATUS))
        .order_by(InvoiceItem.id.asc())
        .all()
    )


# ---------------- 崩溃恢复 ----------------

def _heartbeat_batch(batch_id: int) -> None:
    """解析期心跳：独立会话刷新批次 updated_at（失败只记日志，不阻断解析）。"""
    try:
        with SessionLocal() as hb:
            hb.query(InvoiceBatch).filter(InvoiceBatch.id == batch_id).update(
                {InvoiceBatch.updated_at: datetime.now()}, synchronize_session=False
            )
            hb.commit()
    except Exception:  # noqa: BLE001 - 心跳失败不得影响主解析流程
        logger.warning("发票批次 %s 心跳写入失败（解析继续）", batch_id, exc_info=True)


class _BatchHeartbeat:
    """解析期后台心跳线程。

    与「每解析完一个文件检查一次」不同，后台线程按固定间隔独立刷新 updated_at，
    因此单个超大 PDF 解析超过恢复阈值（30 分钟）时也不会被列表恢复逻辑误杀；
    解析结束（成功/异常）在 finally 中停止线程。
    """

    def __init__(self, batch_id: int, interval: float):
        self.batch_id = batch_id
        self.interval = max(float(interval), 0.01)
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run, name=f"invoice-heartbeat-{batch_id}", daemon=True
        )

    def _run(self) -> None:
        while not self._stop.wait(self.interval):
            _heartbeat_batch(self.batch_id)

    def start(self) -> "_BatchHeartbeat":
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=max(1.0, self.interval * 2))


def recover_stale_batches(db: Session, max_age_seconds: int = STALE_BATCH_SECONDS) -> int:
    """把解析中途崩溃/进程退出后遗留的 uploaded 批次置 failed，返回处理条数。

    以 ``updated_at`` 判定存活：解析期每 ``HEARTBEAT_INTERVAL_SECONDS`` 秒心跳一次，
    超过阈值仍无更新才是真崩溃。置 failed 后其明细不参与跨批去重，用户可重新上传
    同一批文件（「失败重传」）；半成品 inbox 文件同步清理。重复调用幂等。
    """
    cutoff = datetime.now() - timedelta(seconds=max_age_seconds)
    rows = (
        db.query(InvoiceBatch)
        .filter(
            InvoiceBatch.status == InvoiceBatch.STATUS_UPLOADED,
            func.coalesce(InvoiceBatch.updated_at, InvoiceBatch.created_at) < cutoff,
        )
        .all()
    )
    if not rows:
        return 0
    for batch in rows:
        batch.status = InvoiceBatch.STATUS_FAILED
        batch.error = "解析中途中断（进程退出或重启），已自动标记失败，可重新上传重试"
    db.commit()
    dirs = _ensure_dirs()
    for batch in rows:
        shutil.rmtree(dirs["inbox"] / f"batch_{batch.id}", ignore_errors=True)
    return len(rows)


# ---------------- 上传与批次创建 ----------------

def _validate_uploads(uploads: list[tuple[str, bytes]]) -> None:
    """上传校验：扩展名白名单 + 大小限制 + 文件数限制；违规抛 ValueError（路由层转 400）。"""
    if not uploads:
        raise ValueError("未接收到文件")
    if len(uploads) > MAX_FILES_PER_BATCH:
        raise ValueError(f"单批次文件数超过 {MAX_FILES_PER_BATCH} 上限")
    total = 0
    zip_count = 0
    for fname, content in uploads:
        safe = Path(fname or "").name
        if safe in ("", ".", ".."):
            raise ValueError("存在非法文件名")
        suffix = Path(safe).suffix.lower()
        if suffix not in ALLOWED_SUFFIXES:
            raise ValueError(f"不支持的文件类型：{safe}（仅允许 .pdf / .zip）")
        size = len(content)
        if size == 0:
            raise ValueError(f"空文件：{safe}")
        if size > MAX_FILE_BYTES:
            raise ValueError(f"文件 {safe} 超过 20MB 上限")
        total += size
        if suffix == ".zip":
            zip_count += 1
    if zip_count and len(uploads) > 1:
        raise ValueError("ZIP 上传时仅允许单个 ZIP 文件")
    if total > MAX_BATCH_BYTES:
        raise ValueError("批次总大小超过 100MB 上限")


def _unique_path(path: Path) -> Path:
    """同名冲突时追加 _1/_2 后缀（同引擎 ensure_unique_filepath）。"""
    if not path.exists():
        return path
    stem, suffix = path.stem, path.suffix
    counter = 1
    while True:
        candidate = path.with_name(f"{stem}_{counter}{suffix}")
        if not candidate.exists():
            return candidate
        counter += 1


def _save_pdf(inbox: Path, source_name: str, content: bytes) -> Path:
    """保存单个 PDF 到批次 inbox（文件名净化 + 防路径穿越 + 同名去重）。"""
    raw_name = str(source_name or "").replace("\\", "/").split("/")[-1]
    safe = _windows_safe_member_name(raw_name, context="上传文件名")
    if safe in ("", ".", ".."):
        raise ValueError("非法的文件名")
    dest = _unique_path(inbox / safe)
    if dest.resolve().parent != inbox.resolve():
        raise ValueError("非法的文件名")
    dest.write_bytes(content)
    return dest


def _windows_safe_member_name(name: str, context: str = "ZIP 内") -> str:
    """ZIP 成员/上传文件名必须是 Windows 可落盘的单段名。

    context 用于区分「ZIP 内」与「上传文件名」文案（M4）。设备保留名按第一个点
    前的基名判定：CON.foo.bar 同样保留（M3）。
    """
    if not name or name.strip(" .") == "":
        raise ValueError(f"{context}存在非法文件名")
    if any(ch in _WIN_ILLEGAL_CHARS or ord(ch) < 32 for ch in name):
        raise ValueError(f"{context}存在非法文件名：{name}")
    if name.endswith((" ", ".")):
        raise ValueError(f"{context}存在非法文件名：{name}")
    base = name.split(".", 1)[0].strip(" .").upper()
    if base in _RESERVED_NAMES:
        raise ValueError(f"{context}存在非法文件名：{name}（Windows 设备保留名）")
    if len(name) > 200:
        raise ValueError(f"{context}存在超长文件名：{name[:30]}…")
    return name


def _extract_zip_pdfs(inbox: Path, zip_name: str, content: bytes) -> list[tuple[str, Path]]:
    """解包 ZIP 中的 PDF（拍平目录只保留文件名）；路径穿越/非法成员名直接整包拒绝。"""
    try:
        zf = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile:
        raise ValueError(f"ZIP 文件损坏：{zip_name}")
    results: list[tuple[str, Path]] = []
    with zf:
        members = [m for m in zf.infolist() if not m.is_dir() and m.filename.lower().endswith(".pdf")]
        if not members:
            raise ValueError("ZIP 内未找到 PDF 文件")
        if len(members) > MAX_FILES_PER_BATCH:
            raise ValueError(f"ZIP 内 PDF 数量超过 {MAX_FILES_PER_BATCH} 上限")
        total_unc = 0
        for member in members:
            norm = member.filename.replace("\\", "/")
            parts = [p for p in norm.split("/") if p not in ("", ".")]
            if not parts or any(p == ".." for p in parts) or norm.startswith("/") or re.match(r"^[A-Za-z]:", norm):
                raise ValueError(f"ZIP 内存在非法路径：{member.filename}")
            safe_name = _windows_safe_member_name(parts[-1])
            total_unc += member.file_size
            if total_unc > MAX_BATCH_BYTES:
                raise ValueError("ZIP 解压后总大小超过 100MB 上限")
            data = zf.read(member)
            stored = _save_pdf(inbox, safe_name, data)
            results.append((f"{zip_name}:{member.filename}"[:255], stored))
    return results


def create_batch(db: Session, uploads: list[tuple[str, bytes]], name: str = "") -> InvoiceBatch:
    """建批次并解析：uploaded → parsed。

    引擎不可用在任何落盘/建库动作之前抛 EngineUnavailableError（503 降级，不留半成品批次）；
    上传校验失败抛 ValueError（400）；单张解析失败只标记该明细，不影响整批。
    """
    parse_mod._require_engine()
    _validate_uploads(uploads)
    # 先恢复崩溃遗留批次：其明细不得再参与跨批去重/配对，否则重传会被误判重复
    recover_stale_batches(db)

    batch = InvoiceBatch(
        name=(name or "").strip()[:100] or f"批次{datetime.now():%Y%m%d-%H%M%S}",
        status=InvoiceBatch.STATUS_UPLOADED,
        source_files=json.dumps([Path(f).name for f, _ in uploads], ensure_ascii=False),
    )
    db.add(batch)
    db.commit()
    db.refresh(batch)

    dirs = _ensure_dirs()
    inbox = dirs["inbox"] / f"batch_{batch.id}"
    inbox.mkdir(parents=True, exist_ok=True)

    # 整个解析生命周期（落盘 + 逐文件解析）都有后台心跳，单文件长解析同样续命
    heartbeat = _BatchHeartbeat(batch.id, HEARTBEAT_INTERVAL_SECONDS).start()
    try:
        stored: list[tuple[str, Path]] = []
        for fname, content in uploads:
            if Path(fname).suffix.lower() == ".zip":
                stored.extend(_extract_zip_pdfs(inbox, Path(fname).name, content))
            else:
                stored.append((Path(fname).name, _save_pdf(inbox, fname, content)))
        batch.file_count = len(stored)

        items: list[InvoiceItem] = []
        texts: dict[InvoiceItem, str] = {}
        for source_name, pdf_path in stored:
            parsed = parse_mod._parse_pdf_with_engine(pdf_path)
            if parsed is None:
                item = InvoiceItem(
                    batch_id=batch.id,
                    source_file=source_name[:255],
                    stored_path=str(pdf_path)[:500],
                    status=InvoiceItem.STATUS_FAILED,
                    extraction_success=False,
                )
            else:
                item = InvoiceItem(
                    batch_id=batch.id,
                    source_file=source_name[:255],
                    stored_path=str(pdf_path)[:500],
                    invoice_number=parsed.invoice_number,
                    check_code=parsed.check_code,
                    buyer_name=parsed.buyer_name,
                    seller_name=parsed.seller_name,
                    amount=parsed.amount,
                    tax_amount=parsed.tax_amount,
                    tax_rate=parsed.tax_rate,
                    invoice_date=parsed.invoice_date,
                    invoice_type=parsed.invoice_type,
                    invoice_direction=parsed.invoice_direction,
                    project_name=parsed.project_name,
                    brand_name=parsed.brand_name,
                    remark=parsed.remark,
                    extraction_success=parsed.extraction_success,
                    status=InvoiceItem.STATUS_PARSED if parsed.extraction_success else InvoiceItem.STATUS_INCOMPLETE,
                )
                texts[item] = parsed.raw_text
            db.add(item)
            items.append(item)
        db.commit()
        for item in items:
            db.refresh(item)

        # 入库即按项目归类规则归口（规则不可用时 resolve 返回 unchanged，保留引擎结果）
        rules = project_rules.load_project_rules()
        if rules:
            resolved = classify_items(
                [(item, texts.get(item, "")) for item in items if item.status in _ACTIVE_ITEM_STATUS],
                rules,
                text_loader=parse_mod._pdf_text,
            )
            for item, (project, _source) in resolved.items():
                if project and item.project_name != project:
                    item.project_name = project[:100]

        duplicate_count = mark_duplicates(items, existing_dedup_keys(db, exclude_batch_id=batch.id))
        # 跨批次蓝红配对：本批红票配历史正票、本批正票配历史红票
        buyers = {i.buyer_name for i in items if i.status in _ACTIVE_ITEM_STATUS and i.buyer_name}
        candidates = _external_pair_candidates(db, batch.id, buyers)
        external_batch_ids = {i.batch_id for i in candidates}
        pair_red_invoices(items, candidates)
        _refresh_red_counts(db, external_batch_ids)
        current_reds = [
            i for i in items
            if i.status in _ACTIVE_ITEM_STATUS and i.invoice_direction == "冲红发票"
        ]

        batch.item_count = len(items)
        batch.parsed_count = sum(1 for i in items if i.status in _ACTIVE_ITEM_STATUS)
        batch.failed_count = sum(1 for i in items if i.status == InvoiceItem.STATUS_FAILED)
        batch.duplicate_count = duplicate_count
        batch.red_matched_count = sum(1 for i in current_reds if i.paired_item_id is not None)
        batch.red_unmatched_count = sum(1 for i in current_reds if i.paired_item_id is None)
        batch.total_amount = _money(
            sum(parse_amount(i.amount) for i in items if i.status in _ACTIVE_ITEM_STATUS)
        )
        batch.status = InvoiceBatch.STATUS_PARSED
        db.commit()
        db.refresh(batch)
        return batch
    except Exception as exc:
        logger.exception("批次 %s 解析流程异常", batch.id)
        batch.status = InvoiceBatch.STATUS_FAILED
        batch.error = f"{type(exc).__name__}: {exc}"[:500]
        db.commit()
        # 失败批次不保留半成品文件（BUGfix INV-03）：数据库留 failed 记录可追溯，
        # inbox 的已解出 PDF 清掉，避免磁盘只增不减。
        shutil.rmtree(inbox, ignore_errors=True)
        raise
    finally:
        heartbeat.stop()


# ---------------- 查询与人工修正 ----------------

def list_batches(db: Session, page: int = 1, page_size: int = 50) -> dict:
    """批次列表（按 id 倒序，返回 total 供前端分页）；列表前先恢复崩溃遗留批次。"""
    recover_stale_batches(db)
    page = max(1, int(page))
    page_size = max(1, min(int(page_size), 200))
    q = db.query(InvoiceBatch)
    total = q.count()
    rows = (q.order_by(InvoiceBatch.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all())
    return {"total": total, "items": rows, "page": page, "page_size": page_size}


def delete_batch(db: Session, batch_id: int) -> None:
    """删除批次及其明细、inbox 文件、outbox 导出 ZIP（BUGfix INV-03）。"""
    batch = db.get(InvoiceBatch, batch_id)
    if batch is None:
        raise LookupError("批次不存在")
    export_file = batch.export_file or ""
    item_ids = [row[0] for row in db.query(InvoiceItem.id).filter(InvoiceItem.batch_id == batch_id).all()]
    affected_batches: set[int] = set()
    if item_ids:
        # 跨批配对可能指向本批明细：删除前解除对方指针，避免留下悬空 paired_item_id
        affected_batches = {
            row[0]
            for row in db.query(InvoiceItem.batch_id)
            .filter(InvoiceItem.paired_item_id.in_(item_ids))
            .distinct()
            .all()
        }
        db.query(InvoiceItem).filter(InvoiceItem.paired_item_id.in_(item_ids)).update(
            {InvoiceItem.paired_item_id: None}, synchronize_session=False
        )
    db.query(InvoiceItem).filter(InvoiceItem.batch_id == batch_id).delete()
    db.delete(batch)
    db.commit()
    if affected_batches:
        _refresh_red_counts(db, affected_batches)
        db.commit()
    dirs = _ensure_dirs()
    shutil.rmtree(dirs["inbox"] / f"batch_{batch_id}", ignore_errors=True)
    shutil.rmtree(dirs["outbox"] / f"batch_{batch_id}", ignore_errors=True)
    shutil.rmtree(_project_zips_dir(batch_id), ignore_errors=True)
    _project_manifest_path(batch_id).unlink(missing_ok=True)
    if export_file:
        try:
            (dirs["outbox"] / Path(export_file).name).unlink(missing_ok=True)
        except OSError:
            logger.warning("批次 %s 导出文件删除失败：%s", batch_id, export_file)


def get_batch(db: Session, batch_id: int) -> Optional[InvoiceBatch]:
    return db.query(InvoiceBatch).filter(InvoiceBatch.id == batch_id).first()


def get_item(db: Session, item_id: int) -> Optional[InvoiceItem]:
    return db.query(InvoiceItem).filter(InvoiceItem.id == item_id).first()


def _remove_export_artifacts(batch_id: int, export_file: str) -> None:
    """删除批次旧导出产物（best-effort；只对存在的目录告警，文件占用不阻断修正）。"""
    dirs = _ensure_dirs()
    batch_dir = dirs["outbox"] / f"batch_{batch_id}"
    if batch_dir.exists():
        try:
            shutil.rmtree(batch_dir, ignore_errors=False)
        except OSError as exc:
            logger.warning("批次 %s 旧导出目录清理失败：%s", batch_id, exc)
    project_zips = _project_zips_dir(batch_id)
    if project_zips.exists():
        try:
            shutil.rmtree(project_zips, ignore_errors=False)
        except OSError as exc:
            logger.warning("批次 %s 旧项目 ZIP 清理失败：%s", batch_id, exc)
    try:
        _project_manifest_path(batch_id).unlink(missing_ok=True)
    except OSError as exc:
        logger.warning("批次 %s 旧项目清单清理失败：%s", batch_id, exc)
    if export_file:
        zip_path = dirs["outbox"] / Path(export_file).name
        if zip_path.exists():
            try:
                zip_path.unlink()
            except OSError as exc:
                logger.warning("批次 %s 旧导出 ZIP 清理失败：%s", batch_id, exc)


def invalidate_export(db: Session, batch: InvoiceBatch) -> bool:
    """明细变更后作废旧导出：回落 parsed、清 export_file、删旧产物。

    返回是否确实作废了已导出批次。旧实现只改明细不退状态，前端仍能下载
    与当前归类/备注不一致的旧 ZIP（L-083）。
    """
    if batch.status != InvoiceBatch.STATUS_EXPORTED:
        return False
    export_file = batch.export_file or ""
    batch.status = InvoiceBatch.STATUS_PARSED
    batch.export_file = ""
    db.commit()
    db.refresh(batch)
    _remove_export_artifacts(batch.id, export_file)
    return True


def patch_item(
    db: Session,
    item: InvoiceItem,
    project_name: Optional[str] = None,
    remark: Optional[str] = None,
) -> InvoiceItem:
    """人工修正项目归类 / 备注；只有值实际变化才打 corrected 并作废旧导出。

    相同值重复提交（前端 blur 双字段 PATCH 的常见形态）不构成人工修正，
    不得误标 corrected 或作废已导出 ZIP（M1）。
    """
    changed = False
    if project_name is not None:
        new_project = project_name.strip()[:100]
        if new_project != (item.project_name or ""):
            item.project_name = new_project
            changed = True
    if remark is not None:
        new_remark = remark.strip()[:500]
        if new_remark != (item.remark or ""):
            item.remark = new_remark
            changed = True
    if changed:
        item.corrected = True
        db.commit()
        db.refresh(item)
        batch = db.get(InvoiceBatch, item.batch_id)
        if batch is not None:
            invalidate_export(db, batch)
    return item


# ---------------- 项目汇总与整批下载名 ----------------

def project_summary(db: Session, batch: InvoiceBatch) -> list[dict]:
    """按项目汇总批次明细（张数 / 净额 / 人工修正数 / 单项目 ZIP 是否就绪 / PDF 文件名）。"""
    items = (
        db.query(InvoiceItem)
        .filter(InvoiceItem.batch_id == batch.id, InvoiceItem.status.in_(_ACTIVE_ITEM_STATUS))
        .order_by(InvoiceItem.id.asc())
        .all()
    )
    agg: dict[str, dict] = {}
    for item in items:
        project = _item_project_name(item)
        row = agg.setdefault(
            project, {"project": project, "count": 0, "amount": 0.0, "manual": 0, "items": []}
        )
        row["count"] += 1
        row["amount"] = _money(row["amount"] + parse_amount(item.amount))
        row["items"].append(item)
        if item.corrected:
            row["manual"] += 1
    manifest = _read_project_manifest(batch.id)
    zips_dir = _project_zips_dir(batch.id)
    exported = batch.status == InvoiceBatch.STATUS_EXPORTED
    rows: list[dict] = []
    for project, row in sorted(agg.items(), key=lambda kv: (-kv[1]["count"], kv[0])):
        project_items = row.pop("items")
        zip_file = manifest.get(project, "")
        ready = bool(exported and zip_file and (zips_dir / zip_file).is_file())
        rows.append(
            {
                **row,
                # 项目 PDF 与项目 ZIP 同源命名（项目 + 月份），前端下载/打印共用
                "pdf_name": project_pdf_name(project, project_items),
                "zip_file": zip_file,
                "zip_ready": ready,
            }
        )
    return rows


def batch_download_name(items: list[InvoiceItem]) -> str:
    """整批 ZIP 的下载文件名：「发票汇总2026年10月.zip」（跨月用月份区间）。"""
    months = sorted({p for p in (_date_parts(item.invoice_date) for item in items) if p})
    return f"发票汇总{_months_suffix(months) or '全部'}.zip"


def batch_download_name_for(db: Session, batch: InvoiceBatch) -> str:
    """按批次有效明细计算整批下载文件名（下载端点用）。"""
    items = (
        db.query(InvoiceItem)
        .filter(InvoiceItem.batch_id == batch.id, InvoiceItem.status.in_(_ACTIVE_ITEM_STATUS))
        .all()
    )
    return batch_download_name(items)


# ---------------- 响应组装 ----------------

def batch_to_dict(batch: InvoiceBatch) -> dict:
    """批次 ORM → API 输出 dict（source_files JSON 文本转数组）。"""
    try:
        source_files = json.loads(batch.source_files or "[]")
    except (ValueError, TypeError):
        source_files = []
    return {
        "id": batch.id,
        "name": batch.name,
        "status": batch.status,
        "file_count": batch.file_count,
        "item_count": batch.item_count,
        "parsed_count": batch.parsed_count,
        "failed_count": batch.failed_count,
        "duplicate_count": batch.duplicate_count,
        "red_matched_count": batch.red_matched_count,
        "red_unmatched_count": batch.red_unmatched_count,
        "total_amount": batch.total_amount,
        "source_files": source_files,
        "export_file": batch.export_file,
        "error": batch.error,
        "created_at": batch.created_at,
        "updated_at": batch.updated_at,
    }


def item_to_dict(item: InvoiceItem, partner: Optional[InvoiceItem] = None) -> dict:
    """明细 ORM → API 输出 dict（stored_path 不对外）。"""
    return {
        "id": item.id,
        "batch_id": item.batch_id,
        "source_file": item.source_file,
        "invoice_number": item.invoice_number,
        "check_code": item.check_code,
        "buyer_name": item.buyer_name,
        "seller_name": item.seller_name,
        "amount": item.amount,
        "tax_amount": item.tax_amount,
        "tax_rate": item.tax_rate,
        "invoice_date": item.invoice_date,
        "invoice_type": item.invoice_type,
        "invoice_direction": item.invoice_direction,
        "project_name": item.project_name,
        "brand_name": item.brand_name,
        "remark": item.remark,
        "extraction_success": item.extraction_success,
        "corrected": item.corrected,
        "status": item.status,
        "paired_item_id": item.paired_item_id,
        "paired_invoice_number": partner.invoice_number if partner else "",
        "created_at": item.created_at,
        "updated_at": item.updated_at,
    }


def batch_detail_dict(db: Session, batch: InvoiceBatch) -> dict:
    """批次详情（含明细；配对对方号码一次性查出，避免 N+1）。"""
    items = db.query(InvoiceItem).filter(InvoiceItem.batch_id == batch.id).order_by(InvoiceItem.id.asc()).all()
    partner_ids = {i.paired_item_id for i in items if i.paired_item_id}
    partners = {}
    if partner_ids:
        partners = {
            p.id: p
            for p in db.query(InvoiceItem).filter(InvoiceItem.id.in_(partner_ids)).all()
        }
    data = batch_to_dict(batch)
    data["items"] = [item_to_dict(i, partners.get(i.paired_item_id)) for i in items]
    warnings = amount_warnings(items)
    data["amount_warning_count"] = len(warnings)
    data["amount_warnings"] = warnings
    # 整批下载文件名（项目+月份口径）：仅详情需要，列表页不查明细
    data["download_name"] = batch_download_name([i for i in items if i.status in _ACTIVE_ITEM_STATUS])
    return data


# ---------------- Agent 工具 ----------------

def invoice_batch_stats() -> dict:
    """Agent 工具（readonly）：发票处理模块统计概览。

    明细统计与去重/配对同口径：failed 批次的明细不计入 items/duplicate/failed/红票
    配对统计，净额也不汇总 failed 批次（否则失败重传前的半成品会污染概览）。
    """
    try:
        with SessionLocal() as db:
            total_batches = db.query(func.count(InvoiceBatch.id)).scalar() or 0
            by_status = dict(
                db.query(InvoiceBatch.status, func.count()).group_by(InvoiceBatch.status).all()
            )
            active_items = (
                db.query(InvoiceItem)
                .join(InvoiceBatch, InvoiceBatch.id == InvoiceItem.batch_id)
                .filter(InvoiceBatch.status != InvoiceBatch.STATUS_FAILED)
            )
            total_items = active_items.count()
            duplicates = active_items.filter(InvoiceItem.status == InvoiceItem.STATUS_DUPLICATE).count()
            failed = active_items.filter(InvoiceItem.status == InvoiceItem.STATUS_FAILED).count()
            red_matched = active_items.filter(
                InvoiceItem.invoice_direction == "冲红发票", InvoiceItem.paired_item_id.isnot(None)
            ).count()
            red_unmatched = active_items.filter(
                InvoiceItem.invoice_direction == "冲红发票", InvoiceItem.paired_item_id.is_(None)
            ).count()
            net = (
                db.query(func.coalesce(func.sum(InvoiceBatch.total_amount), 0.0))
                .filter(InvoiceBatch.status != InvoiceBatch.STATUS_FAILED)
                .scalar()
                or 0.0
            )
        return {
            "ok": True,
            "module": "finance_invoice",
            "batches": total_batches,
            "batch_by_status": by_status,
            "items": total_items,
            "duplicates": duplicates,
            "failed": failed,
            "red_matched": red_matched,
            "red_unmatched": red_unmatched,
            "net_amount": _money(net),
        }
    except Exception as exc:
        logger.exception("invoice_batch_stats 执行失败")
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
