# -*- coding: utf-8 -*-
"""finance_recon 源文件导入：parse_source 统一入口 + import_source 落库。

- 上传字节经路由层的 read_upload_limited 有界读取后，以 (文件名, 字节) 元组
  进入 import_source；只写 UPLOAD_DIR/recon/<batch_id>/ 固定布局；
- 重复内容（batch_id + sha256 命中唯一约束语义）返回 skipped_duplicates，
  不重复插行、不重复落盘；
- 解析失败：源记录置 failed 并保留可诊断摘要（文件/工作表/行/单元格定位），
  批次保持 draft/failed，批次 error_summary 同步更新，不静默吞错；
- 金额 Decimal 两位（ROUND_HALF_UP）落库，禁止 float；details 经四类 Pydantic
  schema 校验（解析器构造时完成），不含金额与完整账号。
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from sqlalchemy.orm import Session

from . import service
from .models import ReconRecord, ReconSource
from .parsers_pdf import parse_bill_pdf
from .parsers_xlsx import XLSX_KINDS, parse_xlsx_source
from .schemas import SOURCE_KIND_LABELS, SOURCE_KINDS, NormalizedRecord, ParseSourceError

logger = logging.getLogger(__name__)

MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 与 finance_pos 同口径（全局限额 128MB 之内）
MAX_SAFE_STEM = 100  # 落盘文件名词干上限（时间戳前缀 + Windows 255 限制余量）
_WIN_ILLEGAL = set('<>:"/\\|?*')
_RESERVED_NAMES = {"CON", "PRN", "AUX", "NUL"} | {
    f"{prefix}{i}" for prefix in ("COM", "LPT") for i in range(1, 10)
}
DEFAULT_SUFFIXES = {"writeoff": ".xlsx", "claim": ".xlsx", "arrear": ".xlsx", "bill": ".pdf"}


@dataclass
class ImportResult:
    """单文件导入结果（API 响应体）：行数/合同数/金额合计/重复跳过语义。"""

    source_id: int
    kind: str
    original_name: str
    sha256: str
    row_count: int = 0
    contract_count: int = 0
    amount_totals: dict[str, str] = field(default_factory=dict)
    skipped_duplicates: int = 0
    message: str = ""

    def as_dict(self) -> dict:
        return {
            "source_id": self.source_id,
            "kind": self.kind,
            "kind_label": SOURCE_KIND_LABELS.get(self.kind, self.kind),
            "original_name": self.original_name,
            "sha256": self.sha256,
            "row_count": self.row_count,
            "contract_count": self.contract_count,
            "amount_totals": self.amount_totals,
            "skipped_duplicates": self.skipped_duplicates,
            "message": self.message,
        }


def _safe_name(filename: str) -> str:
    """上传文件名 → 安全单段名（保留扩展名；沿用 finance_pos 同款惯例）。"""
    from ...utils import repair_upload_mojibake

    raw = (repair_upload_mojibake(filename or "")).replace("\\", "/").split("/")[-1]
    raw = "".join("_" if ch in _WIN_ILLEGAL or ord(ch) < 32 else ch for ch in raw)
    raw = raw.strip(" .")
    if raw in ("", ".", ".."):
        raise ParseSourceError("非法的文件名")
    suffix = Path(raw).suffix
    stem = raw[: -len(suffix)] if suffix else raw
    stem = stem.strip(" .")
    if stem.upper() in _RESERVED_NAMES:
        stem = f"_{stem}"
    if len(stem) > MAX_SAFE_STEM:
        stem = stem[:MAX_SAFE_STEM].rstrip(" .")
    if not stem:
        raise ParseSourceError("非法的文件名")
    return f"{stem}{suffix}"


def parse_source(path: Path, kind: str) -> list[NormalizedRecord]:
    """四类源文件解析统一入口（Task 3 引擎复用）：xlsx 三类 / bill 走 PDF。"""
    if kind == "bill":
        return parse_bill_pdf(path)
    return parse_xlsx_source(path, kind)


def _record_fingerprint(record: NormalizedRecord) -> str:
    """事实行指纹：kind + 合同/单据/费用类型/费用期（不含金额）。"""
    raw = "|".join(
        (
            record.kind,
            record.contract_no,
            record.doc_no,
            record.fee_type,
            record.fee_period_start.isoformat() if record.fee_period_start else "",
            record.fee_period_end.isoformat() if record.fee_period_end else "",
            str(record.row_no),
        )
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _save_upload(batch_id: int, filename: str, content: bytes) -> Path:
    """上传内容落盘 UPLOAD_DIR/recon/<batch_id>/，带时间戳前缀防重名。"""
    safe = _safe_name(filename)
    batch_dir = service._batch_upload_dir(batch_id)
    batch_dir.mkdir(parents=True, exist_ok=True)
    target = batch_dir / f"{datetime.now():%Y%m%d_%H%M%S_%f}_{safe}"
    try:
        target.write_bytes(content)
    except OSError as exc:
        raise ParseSourceError(f"文件落盘失败：{safe}（{exc}）") from exc
    return target


def _discard_new_file(path: Path) -> None:
    """清理本次导入刚落盘的新文件（并发撞唯一约束时避免孤儿）。

    只删除本次调用创建的那一个路径（调用方传入的 stored_path）；已有
    canonical 源文件的路径由其源记录持有，本函数从不触达。清理失败仅告警，
    不吞掉正在上报的 409（批次删除仍会整目录回收）。
    """
    try:
        if path.is_file() and not path.is_symlink():
            path.unlink()
    except OSError as exc:
        logger.warning("recon 导入冲突清理新文件失败：name=%s err=%s", path.name, exc)


def _summarize(records: list[NormalizedRecord]) -> tuple[int, dict[str, str]]:
    """合同数与四金额栏合计（两位小数字符串；无值栏为空串，不补 0）。"""
    contract_count = len({r.contract_no for r in records if r.contract_no})
    totals: dict[str, str] = {}
    for field_name in ("amount_1", "amount_2", "amount_3", "amount_4"):
        values = [getattr(r, field_name) for r in records if getattr(r, field_name) is not None]
        if not values:
            totals[field_name] = ""
            continue
        total = sum(values, Decimal("0")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        totals[field_name] = f"{total:.2f}"
    return contract_count, totals


def _duplicate_result(db: Session, existing: ReconSource, kind: str, filename: str) -> ImportResult:
    contract_count = (
        db.query(ReconRecord.contract_no)
        .filter(ReconRecord.source_id == existing.id, ReconRecord.contract_no != "")
        .distinct()
        .count()
    )
    base = (
        f"文件「{filename}」内容与已有源文件重复（SHA-256 相同），已跳过导入；"
        f"沿用源记录 #{existing.id}"
    )
    if existing.parse_status != "parsed":
        message = f"{base}。该源解析失败，请先删除再重传（错误：{(existing.error or '原因未知')[:200]}）"
    else:
        message = base
    return ImportResult(
        source_id=existing.id,
        kind=kind,
        original_name=existing.original_name,
        sha256=existing.sha256,
        row_count=existing.row_count,
        contract_count=contract_count,
        skipped_duplicates=1,
        message=message,
    )


def import_source(db: Session, batch_id: int, kind: str, upload: tuple[str, bytes]) -> ImportResult:
    """上传导入：校验 → 去重 → 落盘 → 解析 → 事实行入库 → 源记录落库。

    - kind 非法 ValueError；批次不存在 ReconNotFoundError（404）；
      批次非 draft/failed ReconConflictError（409）；
    - 解析失败：源记录 failed + 批次 error_summary 同步，抛 ParseSourceError（400）；
    - 唯一约束（batch_id+sha256）由 unique_guard 映射中文 409。
    """
    if kind not in SOURCE_KINDS:
        raise ValueError(f"未知源类型 {kind!r}（仅支持：{'、'.join(SOURCE_KINDS)}）")
    filename, content = upload
    if not isinstance(content, (bytes, bytearray)) or not content:
        raise ValueError("上传文件内容为空，请重新选择文件")
    if len(content) > MAX_UPLOAD_BYTES:
        raise ValueError(
            f"文件超过 {MAX_UPLOAD_BYTES // 1024 // 1024}MB 上限（{len(content)} 字节）"
        )
    batch = service._get_batch(db, batch_id)
    # P2-1：编程入口同样拒绝曾就绪（ever_ready）批次的源文件替换——审计留痕与当前
    # 状态无关（failed/ready/checking 均拒）；路由层守卫只是提前拦截，此处是权威落点
    service.assert_sources_replacable(batch)
    if batch.status not in service._SOURCE_WRITABLE_STATUSES:
        raise service.ReconConflictError(
            f"批次当前状态为「{service._label(batch.status)}」，仅草稿/失败批次可上传源文件；"
            "已就绪/检查中批次的源文件不可替换，请另建批次"
        )
    if not filename:
        filename = f"upload{DEFAULT_SUFFIXES[kind]}"

    sha256 = hashlib.sha256(bytes(content)).hexdigest()
    existing = (
        db.query(ReconSource)
        .filter(ReconSource.batch_id == batch_id, ReconSource.sha256 == sha256)
        .one_or_none()
    )
    if existing is not None:
        return _duplicate_result(db, existing, kind, filename)

    stored_path = _save_upload(batch_id, filename, content)
    try:
        records = parse_source(stored_path, kind)
    except Exception as exc:
        # 解析失败：源记录置 failed（保留可诊断摘要），文件留待用户删除排查
        db.add(
            ReconSource(
                batch_id=batch_id,
                kind=kind,
                original_name=filename,
                stored_path=str(stored_path.resolve()),
                sha256=sha256,
                parse_status="failed",
                row_count=0,
                error=str(exc)[:2000],
            )
        )
        batch.error_summary = str(exc)[:2000]
        try:
            with service.unique_guard(db, "源文件导入"):
                db.commit()
        except service.ReconConflictError:
            # 失败源记录没落库（并发撞同批同 sha），本次新文件一并放弃，不留孤儿
            db.rollback()
            _discard_new_file(stored_path)
            raise
        logger.warning("recon 导入失败：batch=%s kind=%s name=%s", batch_id, kind, filename)
        raise
    contract_count, amount_totals = _summarize(records)
    source = ReconSource(
        batch_id=batch_id,
        kind=kind,
        original_name=filename,
        stored_path=str(stored_path.resolve()),
        sha256=sha256,
        parse_status="parsed",
        row_count=len(records),
        error="",
    )
    db.add(source)
    try:
        # add/flush/事实行/提交全部纳入唯一约束守卫：并发同批同 sha 在 flush
        # （或提交）撞 batch_id+sha256 唯一约束时映射中文 409，而不是裸抛 500
        with service.unique_guard(db, "源文件导入"):
            db.flush()
            for record in records:
                db.add(
                    ReconRecord(
                        batch_id=batch_id,
                        source_id=source.id,
                        row_no=record.row_no,
                        kind=kind,
                        contract_no=record.contract_no,
                        doc_no=record.doc_no,
                        fee_type=record.fee_type,
                        fee_period_start=record.fee_period_start,
                        fee_period_end=record.fee_period_end,
                        occur_date=record.occur_date,
                        due_date=record.due_date,
                        amount_1=record.amount_1,
                        amount_2=record.amount_2,
                        amount_3=record.amount_3,
                        amount_4=record.amount_4,
                        fingerprint=_record_fingerprint(record),
                        details=record.details,
                    )
                )
            db.commit()
    except service.ReconConflictError:
        # 冲突发生在本次新文件落盘之后：只清理本次新文件，绝不触碰
        # 已有 canonical 源文件（其路径由对手源记录持有）
        db.rollback()
        _discard_new_file(stored_path)
        raise
    db.refresh(source)
    logger.info(
        "recon 导入完成：batch=%s kind=%s rows=%s sha=%s",
        batch_id,
        kind,
        len(records),
        sha256[:12],
    )
    return ImportResult(
        source_id=source.id,
        kind=kind,
        original_name=source.original_name,
        sha256=sha256,
        row_count=len(records),
        contract_count=contract_count,
        amount_totals=amount_totals,
        skipped_duplicates=0,
        message=f"「{SOURCE_KIND_LABELS[kind]}」源文件导入成功：{len(records)} 行",
    )

