"""finance_invoice 导出层：清单 xlsx、整批/单项目 ZIP、项目 PDF 即时合并与下载路径。

职责：
- 按引擎重命名规则（{购方}{品牌}{金额}{类型}.pdf、{年月}{项目} 目录）复制 PDF；
- 生成每项目「发票清单.xlsx」与「汇总清单.xlsx」，打包整批 ZIP 与按项目拆分 ZIP；
- 单项目 PDF：按当前归类即时合并有效发票原件（无需先导出），缺件/损坏/越界整次失败；
- 下载产物路径收敛在 outbox 内（防路径穿越）。

目录/清单路径与批次产物清理在 invoice_batch；金额/日期/文件名口径在 invoice_parse。
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Optional

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from sqlalchemy.orm import Session

from ...utils_xlsx import safe_cell
from . import projects as project_rules
from .invoice_batch import (
    _active_items,
    _ensure_dirs,
    _project_manifest_path,
    _project_zips_dir,
    _read_project_manifest,
    _unique_path,
)
from .invoice_pairing import amount_warnings
from .invoice_parse import (
    _ACTIVE_ITEM_STATUS,
    _AMOUNT_NUMBER_FORMAT,
    _NON_WORD_CHARS,
    _date_parts,
    _item_project_name,
    _money,
    _months_suffix,
    _project_safe_name,
    build_renamed_filename,
    parse_amount,
    parse_date_to_year_month,
    project_pdf_name,
    sanitize_filename,
)
from .models import InvoiceBatch, InvoiceItem

logger = logging.getLogger(__name__)


class ExportBusyError(RuntimeError):
    """导出文件/目录被占用（Windows 文件锁），由路由层映射 409。"""


class ProjectPdfError(RuntimeError):
    """项目 PDF 合并失败（批次未就绪/缺件/损坏/加密/越界），由路由层映射中文 409。"""


# 项目 PDF 临时文件：响应结束即删；进程中途退出遗留的按阈值兜底清理
PROJECT_PDF_TMP_PREFIX = "invoice_project_"
PROJECT_PDF_STALE_SECONDS = 3600


# ---------------- 导出与下载 ----------------

def _write_project_list(project_dir: Path, project: str, entries: list[dict]) -> Path:
    """生成单项目「发票清单.xlsx」（格式镜像引擎 ExcelGenerator.generate_invoice_list）。"""
    wb = Workbook()
    ws = wb.active
    ws.title = _NON_WORD_CHARS.sub("", sanitize_filename(project or ""))[:31] or "项目清单"
    headers = ["序号", "票号", "购方名称", "发票金额", "发票类型", "备注"]
    for col, header in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col, value=header)
        cell.font = Font(bold=True)
        cell.alignment = Alignment(horizontal="center")
        cell.fill = PatternFill(start_color="CCCCCC", end_color="CCCCCC", fill_type="solid")
    total_amount = 0.0
    special_count = 0
    general_count = 0
    for row, entry in enumerate(entries, 2):
        item: InvoiceItem = entry["item"]
        ws.cell(row=row, column=1, value=row - 1)
        ws.cell(row=row, column=2, value=safe_cell(item.invoice_number))
        ws.cell(row=row, column=3, value=safe_cell(item.buyer_name))
        amount = parse_amount(item.amount)
        amount_cell = ws.cell(row=row, column=4, value=amount)
        amount_cell.number_format = _AMOUNT_NUMBER_FORMAT
        total_amount += amount
        ws.cell(row=row, column=5, value=safe_cell(item.invoice_type))
        if item.invoice_type == "专票":
            special_count += 1
        elif item.invoice_type == "普票":
            general_count += 1
        ws.cell(row=row, column=6, value=safe_cell(item.remark))
    stat_row = len(entries) + 3
    ws.cell(row=stat_row, column=1, value="统计信息")
    stats = [("发票总额", _money(total_amount)), ("发票张数", len(entries)), ("专票张数", special_count), ("普票张数", general_count)]
    for offset, (label, value) in enumerate(stats):
        ws.cell(row=stat_row + offset, column=2, value=label)
        ws.cell(row=stat_row + offset, column=3, value=value)
        for col in range(1, 4):
            ws.cell(row=stat_row + offset, column=col).font = Font(bold=True)
    for column in ws.columns:
        width = max((len(str(c.value)) for c in column if c.value is not None), default=0)
        ws.column_dimensions[column[0].column_letter].width = min(width + 2, 50)
    path = project_dir / "发票清单.xlsx"
    wb.save(path)
    return path


def _write_summary_list(batch_dir: Path, groups: dict[str, list[dict]]) -> Path:
    """生成批次「汇总清单.xlsx」（格式镜像引擎 generate_summary_list）。"""
    wb = Workbook()
    ws = wb.active
    ws.title = "汇总清单"
    headers = ["序号", "项目名称", "发票张数", "发票总额", "专票张数", "普票张数"]
    for col, header in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col, value=header)
        cell.font = Font(bold=True)
        cell.alignment = Alignment(horizontal="center")
        cell.fill = PatternFill(start_color="CCCCCC", end_color="CCCCCC", fill_type="solid")
    total_rows = 0
    total_amount = 0.0
    total_special = 0
    total_general = 0
    for row, (project, entries) in enumerate(groups.items(), 2):
        amounts = [parse_amount(e["item"].amount) for e in entries]
        special = sum(1 for e in entries if e["item"].invoice_type == "专票")
        general = sum(1 for e in entries if e["item"].invoice_type == "普票")
        ws.cell(row=row, column=1, value=row - 1)
        ws.cell(row=row, column=2, value=safe_cell(project))
        ws.cell(row=row, column=3, value=len(entries))
        amount_cell = ws.cell(row=row, column=4, value=_money(sum(amounts)))
        amount_cell.number_format = _AMOUNT_NUMBER_FORMAT
        ws.cell(row=row, column=5, value=special)
        ws.cell(row=row, column=6, value=general)
        total_rows += len(entries)
        total_amount += sum(amounts)
        total_special += special
        total_general += general
    total_row = len(groups) + 2
    ws.cell(row=total_row, column=2, value="合计").font = Font(bold=True)
    ws.cell(row=total_row, column=3, value=total_rows).font = Font(bold=True)
    total_cell = ws.cell(row=total_row, column=4, value=_money(total_amount))
    total_cell.number_format = _AMOUNT_NUMBER_FORMAT
    total_cell.font = Font(bold=True)
    ws.cell(row=total_row, column=5, value=total_special).font = Font(bold=True)
    ws.cell(row=total_row, column=6, value=total_general).font = Font(bold=True)
    for column in ws.columns:
        width = max((len(str(c.value)) for c in column if c.value is not None), default=0)
        ws.column_dimensions[column[0].column_letter].width = min(width + 2, 50)
    path = batch_dir / "汇总清单.xlsx"
    wb.save(path)
    return path


def export_batch(db: Session, batch: InvoiceBatch) -> dict:
    """导出批次：按重命名规则复制 PDF → 项目目录，生成清单 xlsx，打包 ZIP。

    可重复导出（先清空本批次旧导出目录）；无可导出明细抛 ValueError。
    """
    items = (
        db.query(InvoiceItem)
        .filter(InvoiceItem.batch_id == batch.id, InvoiceItem.status.in_(_ACTIVE_ITEM_STATUS))
        .order_by(InvoiceItem.id.asc())
        .all()
    )
    if not items:
        raise ValueError("批次内无可导出的发票明细")

    dirs = _ensure_dirs()
    outroot = dirs["outbox"]
    batch_dir = outroot / f"batch_{batch.id}"
    try:
        if batch_dir.exists():
            shutil.rmtree(batch_dir)  # 仅清理本模块生成的本批次导出目录
        batch_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ExportBusyError("导出目录被占用（可能已在资源管理器中打开），请关闭后重试") from exc

    # 按项目分组（键为未清洗的项目名，目录/文件名另做清洗，保证项目汇总可精确对应）
    groups: dict[str, list[dict]] = {}
    for item in items:
        project = (item.project_name or "").strip() or project_rules.UNCLASSIFIED
        groups.setdefault(project, []).append({"item": item, "dest": None})
    # 清洗后可能重名（非法字符被剔除/超长截断）：目录与 ZIP 名逐项目去重，避免互相覆盖
    safe_names: dict[str, str] = {}
    used_names: set[str] = set()
    for project in groups:
        base = _project_safe_name(project)
        name = base
        counter = 1
        while name in used_names:
            counter += 1
            name = f"{base}_{counter}"
        used_names.add(name)
        safe_names[project] = name

    try:
        for project, entries in groups.items():
            for entry in entries:
                item: InvoiceItem = entry["item"]
                ym = parse_date_to_year_month(item.invoice_date)
                dir_name = f"{ym}{safe_names[project]}" if ym else safe_names[project]
                project_dir = batch_dir / dir_name
                project_dir.mkdir(parents=True, exist_ok=True)
                src = Path(item.stored_path)
                if src.is_file():
                    dest = _unique_path(project_dir / build_renamed_filename(item))
                    shutil.copy2(src, dest)
                    entry["dest"] = dest
                else:
                    logger.warning("批次 %s 明细 %s 的入库文件缺失：%s", batch.id, item.id, src)

        # 跨月项目的清单必须落在各自月份目录且只含本月明细（L-084）
        for project, entries in groups.items():
            by_dir: dict[Path, list[dict]] = {}
            for entry in entries:
                if entry["dest"] is not None:
                    by_dir.setdefault(entry["dest"].parent, []).append(entry)
            for project_dir, dir_entries in by_dir.items():
                _write_project_list(project_dir, project, dir_entries)
        _write_summary_list(batch_dir, groups)

        zip_name = f"batch_{batch.id}_export.zip"
        zip_path = outroot / zip_name
        if zip_path.exists():
            zip_path.unlink()  # 重新导出覆盖旧包
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for file in sorted(batch_dir.rglob("*")):
                if file.is_file():
                    zf.write(file, file.relative_to(batch_dir).as_posix())

        # 按项目拆分打包：每个项目一个 ZIP（内含 {年月}{项目}/ 目录的重命名 PDF + 该项目发票清单）
        project_zips = _write_project_zips(batch.id, batch_dir, groups, safe_names)
    except PermissionError as exc:
        raise ExportBusyError("导出文件被占用（可能已在 Excel/资源管理器中打开），请关闭后重试") from exc

    warnings = amount_warnings(items)
    batch.export_file = zip_path.name
    batch.status = InvoiceBatch.STATUS_EXPORTED
    db.commit()
    db.refresh(batch)
    return {
        "ok": True,
        "batch_id": batch.id,
        "status": batch.status,
        "export_file": batch.export_file,
        "zip_size": zip_path.stat().st_size,
        "projects": len(groups),
        "project_zips": project_zips,
        "amount_warning_count": len(warnings),
        "warnings": [
            f"有 {len(warnings)} 张发票金额无法解析或为空，清单与汇总中按 0 计入，请人工核对"
        ] if warnings else [],
    }


def _write_project_zips(
    batch_id: int,
    batch_dir: Path,
    groups: dict[str, list[dict]],
    safe_names: dict[str, str],
) -> dict[str, str]:
    """按项目生成独立 ZIP（项目名 → 「项目+月份.zip」），并写清单供下载端点定位。"""
    zips_dir = _project_zips_dir(batch_id)
    try:
        if zips_dir.exists():
            shutil.rmtree(zips_dir)  # 仅清理本模块生成的本批次项目 ZIP 目录
        zips_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ExportBusyError("项目 ZIP 目录被占用，请关闭后重试") from exc
    manifest: dict[str, str] = {}
    used: set[str] = set()
    for project, entries in groups.items():
        files = sorted({e["dest"] for e in entries if e["dest"] is not None})
        if not files:
            continue  # 该项目的 PDF 全部缺失：不生成空包
        extra_lists = {f.parent / "发票清单.xlsx" for f in files}
        files = sorted(set(files) | {f for f in extra_lists if f.is_file()})
        base = safe_names.get(project) or _project_safe_name(project)
        # 下载文件名 = 项目 + 月份（单月「项目2026年10月.zip」，跨月「项目2026年10-11月.zip」）
        months = sorted({p for p in (_date_parts(e["item"].invoice_date) for e in entries) if p})
        zip_file = f"{base}{_months_suffix(months)}.zip"
        counter = 1
        while zip_file in used or (zips_dir / zip_file).exists():
            counter += 1
            zip_file = f"{base}{_months_suffix(months)}_{counter}.zip"
        used.add(zip_file)
        with zipfile.ZipFile(zips_dir / zip_file, "w", zipfile.ZIP_DEFLATED) as zf:
            for file in files:
                zf.write(file, file.relative_to(batch_dir).as_posix())
        manifest[project] = zip_file
    _project_manifest_path(batch_id).write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    return manifest


def export_zip_path(batch: InvoiceBatch) -> Optional[Path]:
    """导出 ZIP 的服务端路径（限制在模块 outbox 内，防路径穿越）；不存在返回 None。"""
    if not batch.export_file:
        return None
    outroot = _ensure_dirs()["outbox"].resolve()
    path = (outroot / Path(batch.export_file).name).resolve()
    if path.parent != outroot or not path.is_file():
        return None
    return path


def project_zip_path(batch: InvoiceBatch, project: str) -> Optional[Path]:
    """单项目 ZIP 的服务端路径（限制在 outbox 内，防路径穿越）；未导出/缺失返回 None。"""
    if batch.status != InvoiceBatch.STATUS_EXPORTED:
        return None
    zip_file = _read_project_manifest(batch.id).get(project, "")
    if not zip_file:
        return None
    outbox = _ensure_dirs()["outbox"].resolve()
    path = (_project_zips_dir(batch.id) / Path(zip_file).name).resolve()
    if outbox not in path.parents or not path.is_file():
        return None
    return path


# ---------------- 单项目 PDF 即时合并 ----------------

def _resolve_item_pdf(item: InvoiceItem, inbox: Path) -> Path:
    """校验明细原件：路径非空、可解析、且 resolve() 后位于本批次 inbox 内，否则抛 ValueError。

    越界判定覆盖「任意本机路径」与「其他批次的入库文件」，绝不按 stored_path 直接开文件。
    """
    raw = (item.stored_path or "").strip()
    if not raw:
        raise ValueError("原件路径为空")
    try:
        path = Path(raw).resolve()
        base = inbox.resolve()
    except OSError as exc:
        raise ValueError(f"原件路径无法解析（{type(exc).__name__}）") from exc
    if path == base or base not in path.parents:
        raise ValueError("原件路径超出本批次入库目录，已拒绝读取")
    if not path.is_file():
        raise ValueError("原文件不存在")
    return path


def _merge_problem(item: InvoiceItem, reason: str) -> str:
    """单张发票的失败说明：票号 + 源文件 + 原因（缺件时用户可直接定位重传）。"""
    number = (item.invoice_number or "").strip() or "未知票号"
    source = (item.source_file or "").strip() or "未知文件"
    return f"发票 {number}（{source}）：{reason}"


def _merge_failure_message(problems: list[str], total: int) -> str:
    shown = "；".join(problems[:10])
    tail = f"；另有 {len(problems) - 10} 张未列出" if len(problems) > 10 else ""
    return f"项目 PDF 合并失败：{len(problems)}/{total} 张发票原件不可用，为避免漏票已整次取消。{shown}{tail}"


def cleanup_project_pdf(path: Path) -> None:
    """删除项目 PDF 临时文件（响应后后台任务与合并失败兜底），best-effort 不抛异常。"""
    try:
        Path(path).unlink(missing_ok=True)
    except OSError as exc:
        logger.warning("项目 PDF 临时文件删除失败 %s：%s", Path(path).name, exc)


def _purge_stale_project_pdfs(
    tmp_dir: Path, max_age_seconds: int = PROJECT_PDF_STALE_SECONDS
) -> None:
    """清理合并中断遗留的临时 PDF；只删超过阈值的，避免误删并发请求的在途文件。"""
    cutoff = time.time() - max_age_seconds
    try:
        candidates = list(tmp_dir.glob(f"{PROJECT_PDF_TMP_PREFIX}*.pdf"))
    except OSError as exc:
        logger.warning("项目 PDF 临时目录不可读：%s", exc)
        return
    for path in candidates:
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink(missing_ok=True)
        except OSError:
            continue


def merge_project_pdf(db: Session, batch: InvoiceBatch, project: str) -> tuple[Path, str]:
    """把批次内单个项目的有效发票原件即时合并为一个 PDF，返回 (临时文件, 下载文件名)。

    - 明细口径与 project_summary 一致：parsed/incomplete、含红冲、支持「未归类」，排除 duplicate/failed；
    - 顺序：明细 ID 升序，每张原件保持原页序，不插封面/分隔页；
    - 任一原件缺失/零页/加密不可读/损坏/路径越界都整次失败（ProjectPdfError），绝不静默少票；
    - 只读操作：不修改批次状态、明细与已有导出产物；临时文件由路由在响应后删除。
    """
    from pypdf import PdfReader, PdfWriter  # 延迟导入：只有真正合并时才需要 pypdf

    if batch.status == InvoiceBatch.STATUS_UPLOADED:
        raise ProjectPdfError("批次仍在解析中，请稍后重试")
    if batch.status == InvoiceBatch.STATUS_FAILED:
        raise ProjectPdfError("批次解析失败，请重新上传后再合并项目 PDF")

    wanted = (project or "").strip()
    items = [item for item in _active_items(db, batch) if _item_project_name(item) == wanted]
    if not items:
        raise LookupError("该项目没有可合并的有效发票")

    try:
        dirs = _ensure_dirs()
    except OSError as exc:
        raise ProjectPdfError("数据目录不可用，请检查财务数据目录后重试") from exc
    inbox = dirs["inbox"] / f"batch_{batch.id}"
    tmp_dir = dirs["tmp"]
    _purge_stale_project_pdfs(tmp_dir)

    problems: list[str] = []
    sources: list[Path] = []
    for item in items:
        try:
            sources.append(_resolve_item_pdf(item, inbox))
        except ValueError as exc:
            problems.append(_merge_problem(item, str(exc)))
    if problems:
        raise ProjectPdfError(_merge_failure_message(problems, len(items)))

    writer = PdfWriter()
    for item, path in zip(items, sources):
        try:
            reader = PdfReader(str(path))
            if reader.is_encrypted:
                try:
                    unlocked = bool(reader.decrypt(""))
                except Exception:  # noqa: BLE001 - 加密格式异常一律按不可读处理
                    unlocked = False
                if not unlocked:
                    problems.append(_merge_problem(item, "PDF 已加密，无法读取"))
                    continue
            pages = list(reader.pages)
            if not pages:
                problems.append(_merge_problem(item, "PDF 无有效页面"))
                continue
            for page in pages:
                writer.add_page(page)
        except Exception as exc:  # noqa: BLE001 - 单文件损坏汇总后整次失败
            problems.append(_merge_problem(item, f"PDF 损坏或无法解析（{type(exc).__name__}）"))
            continue
    if problems:
        raise ProjectPdfError(_merge_failure_message(problems, len(items)))

    try:
        handle, tmp_name = tempfile.mkstemp(
            prefix=PROJECT_PDF_TMP_PREFIX, suffix=".pdf", dir=str(tmp_dir)
        )
    except OSError as exc:
        raise ProjectPdfError("合并临时文件创建失败，请稍后重试") from exc
    os.close(handle)
    target = Path(tmp_name)
    try:
        with open(target, "wb") as fh:
            writer.write(fh)
    except Exception as exc:  # noqa: BLE001 - 半成品必须删除后报中文错误
        cleanup_project_pdf(target)
        raise ProjectPdfError(f"合并项目 PDF 失败（{type(exc).__name__}），请稍后重试") from exc
    return target, project_pdf_name(project, items)
