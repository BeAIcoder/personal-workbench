"""商户核销对账服务：批次生命周期、状态机与唯一约束 409 守卫。

Task 1 只开放批次 CRUD 与 summary 工具；解析（Task 2）、勾稽匹配与问题收敛
（Task 3）、报告导出（Task 4）、口径参数（Task 5）在各自文件实现，
这里的 create_batch / get_batch / transition_batch / unique_guard 是共享入口。
"""
from __future__ import annotations

import shutil
from contextlib import contextmanager
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Iterator

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .models import ReconBatch, ReconIssue, ReconMatch, ReconRecord, ReconSource, ReconTerm
from .schemas import (
    BATCH_STATUSES,
    SOURCE_KIND_LABELS,
    STATUS_LABELS,
    ReconBatchCreate,
    ReconBatchPatch,
)

_CENT = Decimal("0.01")

# 状态机唯一事实源（计划 76 行）：draft→checking→ready|failed、failed→checking、ready→checking
TRANSITIONS: dict[str, frozenset[str]] = {
    "draft": frozenset({"checking"}),
    "checking": frozenset({"ready", "failed"}),
    "failed": frozenset({"checking"}),
    "ready": frozenset({"checking"}),
}

# 批次删除允许的状态（ready 为审计留痕不可删；checking 禁删防误清）
_DELETABLE_STATUSES = ("draft", "failed")
# PATCH 可维护状态（计划 85 行）
_PATCHABLE_STATUSES = ("draft", "failed")

# checking 中断回收阈值（P2-6，明确常量）：检查超过 2 小时未完成视为上次检查中断，
# 下次 POST check 时先回收 failed 再允许重试；未超时仍 409 防并发
CHECKING_STALE_SECONDS = 2 * 3600
CHECKING_STALE_HOURS = CHECKING_STALE_SECONDS // 3600


def _now() -> datetime:
    """当前时间（可 monkeypatch 的唯一入口；用于中断回收与问题 last_seen）。"""
    return datetime.now()


def is_checking_stale(batch: ReconBatch) -> bool:
    """checking 批次是否已超过中断回收阈值（基于 updated_at，可注入时间测试）。"""
    if batch.status != "checking" or batch.updated_at is None:
        return False
    return (_now() - batch.updated_at).total_seconds() > CHECKING_STALE_SECONDS


def recover_stale_checking(db: Session, batch: ReconBatch) -> None:
    """把超时滞留 checking 的批次回收为 failed（中文「上次检查中断」摘要）。"""
    batch.status = "failed"
    batch.error_summary = (
        f"上次检查中断：检查过程超过 {CHECKING_STALE_HOURS} 小时未完成，已回收为失败；"
        "请排除故障后重新执行勾稽检查"
    )
    db.commit()


class ReconNotFoundError(LookupError):
    """批次不存在（路由映射 404，中文消息）。"""


class ReconConflictError(Exception):
    """非法状态迁移、删除/修改守卫、唯一约束冲突（路由映射 409，中文消息）。"""


@contextmanager
def unique_guard(db: Session, label: str) -> Iterator[None]:
    """把写路径的 IntegrityError 翻译成中文 409 冲突；后续任务写库一律用此守卫。"""
    try:
        yield
    except IntegrityError as exc:
        db.rollback()
        raise ReconConflictError(f"{label}：唯一约束冲突，请检查是否重复提交") from exc


def _label(status: str) -> str:
    return STATUS_LABELS.get(status, status)


def _amount_str(value) -> str:
    """两位小数字符串（ROUND_HALF_UP 量化，禁止 round()）。"""
    if value is None:
        return ""
    return str(Decimal(str(value)).quantize(_CENT, rounding=ROUND_HALF_UP))


def _scope_of(batch: ReconBatch) -> list[str]:
    return [str(item) for item in (batch.contract_scope or [])]


def _dedupe_scope(items: list[str]) -> list[str]:
    """范围是集合语义：保序去重（空白项已被 schema 拒绝）。"""
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _counts(db: Session, batch_id: int) -> dict[str, int]:
    """六类子表计数（COUNT+WHERE，不拉明细）。"""
    return {
        "sources": db.query(func.count(ReconSource.id)).filter(ReconSource.batch_id == batch_id).scalar() or 0,
        "records": db.query(func.count(ReconRecord.id)).filter(ReconRecord.batch_id == batch_id).scalar() or 0,
        "matches": db.query(func.count(ReconMatch.id)).filter(ReconMatch.batch_id == batch_id).scalar() or 0,
        "issues": db.query(func.count(ReconIssue.id)).filter(ReconIssue.batch_id == batch_id).scalar() or 0,
        "terms": db.query(func.count(ReconTerm.id)).filter(ReconTerm.batch_id == batch_id).scalar() or 0,
    }


def _batch_out(batch: ReconBatch, *, detail: bool = False, db: Session | None = None) -> dict:
    out = {
        "id": batch.id,
        "name": batch.name,
        "object_tag": batch.object_tag or "",
        "as_of": batch.as_of.isoformat() if batch.as_of else "",
        "tolerance": _amount_str(batch.tolerance) or _amount_str(Decimal("0.01")),
        "status": batch.status,
        "status_label": _label(batch.status),
        "needs_recheck": bool(batch.needs_recheck),
        "ever_ready": bool(batch.ever_ready),
        "error_summary": batch.error_summary or "",
        "created_at": batch.created_at.isoformat(timespec="seconds") if batch.created_at else "",
        "updated_at": batch.updated_at.isoformat(timespec="seconds") if batch.updated_at else "",
    }
    if detail and db is not None:
        out["contract_scope"] = _scope_of(batch)
        out["counts"] = _counts(db, batch.id)
    return out


def _get_batch(db: Session, batch_id: int) -> ReconBatch:
    batch = db.get(ReconBatch, batch_id)
    if batch is None:
        raise ReconNotFoundError(f"批次 {batch_id} 不存在")
    return batch


def _batch_upload_dir(batch_id: int) -> Path:
    """批次上传目录：固定 UPLOAD_DIR/recon/<batch_id>/，解析后校验层级防越界。

    batch_id 是整数路径参数，无分隔符注入面；这里仍按纵深防御做 resolved
    父目录与名称比对，任何不吻合一律拒绝清理。
    """
    from ... import config as app_config

    root = Path(app_config.UPLOAD_DIR).resolve() / "recon"
    batch_dir = (root / str(batch_id)).resolve()
    if batch_dir.parent != root or batch_dir.name != str(batch_id):
        raise ReconConflictError("批次上传目录非法，已拒绝清理")
    return batch_dir


def _remove_batch_dir(batch_dir: Path) -> None:
    """删除批次上传目录（只接受 _batch_upload_dir 产出的固定路径）。

    失败行为：目录不存在视为无上传历史，直接跳过；rmtree 抛 OSError 时向上
    抛出，由 delete_batch 回滚数据库并映射中文 409，绝不静默吞错。
    """
    if not batch_dir.exists():
        return
    if batch_dir.is_symlink() or not batch_dir.is_dir():
        raise ReconConflictError(f"批次上传目录异常，已拒绝清理：{batch_dir.name}")
    shutil.rmtree(batch_dir)


def create_batch(db: Session, payload: ReconBatchCreate) -> dict:
    """新建批次（draft）：容差显式落库；范围保序去重；不默认到任何期间。"""
    batch = ReconBatch(
        name=payload.name.strip(),
        object_tag=(payload.object_tag or "").strip(),
        as_of=payload.as_of,
        tolerance=payload.tolerance,
        contract_scope=_dedupe_scope(payload.contract_scope),
        status="draft",
        needs_recheck=False,
    )
    with unique_guard(db, "批次创建"):
        db.add(batch)
        db.commit()
    db.refresh(batch)
    return _batch_out(batch, detail=True, db=db)


def list_batches(db: Session, limit: int = 50) -> dict:
    """批次列表（最新在前，精简视图；total 为全量计数）。"""
    rows = db.query(ReconBatch).order_by(ReconBatch.id.desc()).limit(limit).all()
    total = db.query(func.count(ReconBatch.id)).scalar() or 0
    return {"total": total, "items": [_batch_out(batch) for batch in rows]}


def get_batch(db: Session, batch_id: int) -> dict:
    """批次详情：含合同范围与子表计数。"""
    batch = _get_batch(db, batch_id)
    return _batch_out(batch, detail=True, db=db)


def update_batch(db: Session, batch_id: int, payload: ReconBatchPatch) -> dict:
    """维护批次：仅 draft/failed；曾就绪（ever_ready）的批次为审计留痕，
    即使重跑失败也不可改写基本字段（P2-1）；空 PATCH 显式拒绝。"""
    batch = _get_batch(db, batch_id)
    if batch.ever_ready:
        raise ReconConflictError(
            "曾就绪的批次为审计留痕，名称/对账日/容差/合同范围不可修改"
            "（即使当前为失败状态）；口径参数仍可在 terms 中调整后重跑"
        )
    if batch.status not in _PATCHABLE_STATUSES:
        raise ReconConflictError(
            f"批次当前状态为「{_label(batch.status)}」，仅草稿/失败批次可修改"
        )
    if payload.name is None and payload.as_of is None and payload.tolerance is None and payload.contract_scope is None:
        raise ValueError("未提供任何要更新的字段（可维护：名称、对账日、容差、合同范围）")
    if payload.name is not None:
        batch.name = payload.name.strip()
    if payload.as_of is not None:
        batch.as_of = payload.as_of
    if payload.tolerance is not None:
        batch.tolerance = payload.tolerance
    if payload.contract_scope is not None:
        batch.contract_scope = _dedupe_scope(payload.contract_scope)
    with unique_guard(db, "批次更新"):
        db.commit()
    return _batch_out(batch, detail=True, db=db)


def delete_batch(db: Session, batch_id: int) -> dict:
    """删除批次：仅 draft/failed 且未曾就绪（P2-1 审计留痕）；级联清理五张子表并
    移除 UPLOAD_DIR/recon/<batch_id>/。

    顺序：先定位目录（越界即拒绝）→ 删库内行（未提交）→ 清目录 → 提交。
    目录清理失败（OSError）时整体回滚、批次保持原状可重试，返回中文 409，
    不静默吞错；仅当目录已清、提交又失败才可能留下空目录（本表无唯一约束，实际不可达）。
    """
    batch = _get_batch(db, batch_id)
    if batch.ever_ready:
        raise ReconConflictError(
            "曾就绪的批次为审计留痕，不可删除（即使当前为失败状态）"
        )
    if batch.status not in _DELETABLE_STATUSES:
        extra = "（ready 批次为审计留痕，不可删除）" if batch.status == "ready" else ""
        raise ReconConflictError(f"批次当前状态为「{_label(batch.status)}」，仅草稿/失败批次可删除{extra}")
    batch_dir = _batch_upload_dir(batch_id)
    for model in (ReconSource, ReconRecord, ReconMatch, ReconIssue, ReconTerm):
        db.query(model).filter(model.batch_id == batch_id).delete(synchronize_session=False)
    db.delete(batch)
    try:
        _remove_batch_dir(batch_dir)
        db.commit()
    except OSError as exc:
        db.rollback()
        raise ReconConflictError(
            f"批次删除失败：上传目录清理失败，批次保持原状请重试（{exc.strerror or exc}）"
        ) from exc
    return {"ok": True, "id": batch_id}


def transition_batch(db: Session, batch_id: int, target: str) -> dict:
    """批次状态机（check 入口与后续任务的共享迁移函数）。

    - 未知目标状态 / 非法迁移 → ReconConflictError（中文，路由 409）；
    - 进入 checking 要求显式非空 contract_scope（空 → ValueError，
      由 check 入口映射 400，不在服务层静默放行）。
    """
    batch = _get_batch(db, batch_id)
    if target not in BATCH_STATUSES:
        raise ReconConflictError(f"未知目标状态 {target!r}（合法：{'、'.join(BATCH_STATUSES)}）")
    if target not in TRANSITIONS.get(batch.status, frozenset()):
        legal = "、".join(sorted(TRANSITIONS.get(batch.status, frozenset()))) or "无"
        raise ReconConflictError(
            f"批次当前状态为「{_label(batch.status)}」，"
            f"不允许迁移到「{_label(target)}」（合法目标：{legal}）"
        )
    if target == "checking" and not _scope_of(batch):
        raise ValueError("本批次合同范围为空：check 前必须显式勾选本批合同")
    if target == "checking":
        batch.error_summary = ""  # 重跑进检查前清掉上一轮失败摘要，避免残留旧错
    if target == "ready":
        batch.ever_ready = True  # P2-1：审计留痕标记持久化，只置真不复位
    batch.status = target
    db.commit()
    return _batch_out(batch, detail=True, db=db)


def recon_summary_tool() -> dict:
    """商户核销对账摘要（只读 Agent 工具）：批次计数、状态分布与最新批次概况。

    只返回状态/计数与最新批次概况，不返回明细或敏感字段（计划 92 行）。
    """
    from ...database import SessionLocal

    with SessionLocal() as db:
        total = db.query(func.count(ReconBatch.id)).scalar() or 0
        by_status = {status: 0 for status in BATCH_STATUSES}
        for status, count in (
            db.query(ReconBatch.status, func.count(ReconBatch.id)).group_by(ReconBatch.status).all()
        ):
            by_status[status] = count  # 未知状态如实并入，不静默丢弃
        latest = db.query(ReconBatch).order_by(ReconBatch.id.desc()).first()
        latest_view = None
        if latest is not None:
            pending_issues = (
                db.query(func.count(ReconIssue.id))
                .filter(
                    ReconIssue.batch_id == latest.id,
                    ReconIssue.status == "pending",
                    ReconIssue.active.is_(True),
                )
                .scalar()
                or 0
            )
            suggested_matches = (
                db.query(func.count(ReconMatch.id))
                .filter(ReconMatch.batch_id == latest.id, ReconMatch.status == "suggested")
                .scalar()
                or 0
            )
            latest_view = {
                "id": latest.id,
                "name": latest.name,
                "as_of": latest.as_of.isoformat() if latest.as_of else "",
                "status": latest.status,
                "needs_recheck": bool(latest.needs_recheck),
                "counts": _counts(db, latest.id),
                "pending_issues": pending_issues,
                "suggested_matches": suggested_matches,
            }
            # Task 3 扩展：合计 / 检查状态分布（来自统一报告视图，无敏感明细）
            from . import reporting

            report = reporting.build_report(db, latest.id).as_dict()
            latest_view["totals"] = report["totals"]
            latest_view["check_states"] = report["check_states"]
    return {"batches": total, "by_status": by_status, "latest": latest_view}


# ---------- Task 2：源文件删除 / 记录查询 / check 前置校验 ----------

# 源文件上传/删除允许的批次状态（与批次 PATCH/删除同口径）
_SOURCE_WRITABLE_STATUSES = ("draft", "failed")


def list_records(
    db: Session,
    batch_id: int,
    *,
    kind: str | None = None,
    contract: str | None = None,
    doc: str | None = None,
    page: int = 1,
    page_size: int = 50,
) -> dict:
    """事实记录分页查询：kind/合同/单据过滤（精确匹配），稳定按 id 排序。

    金额两位小数字符串序列化；未填金额为 None（与 0.00 严格区分，不补零）。
    """
    _get_batch(db, batch_id)
    query = db.query(ReconRecord).filter(ReconRecord.batch_id == batch_id)
    if kind:
        query = query.filter(ReconRecord.kind == kind)
    if contract:
        query = query.filter(ReconRecord.contract_no == contract.strip())
    if doc:
        query = query.filter(ReconRecord.doc_no == doc.strip())
    total = query.count()
    rows = (
        query.order_by(ReconRecord.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    source_names = {
        s.id: s.original_name
        for s in db.query(ReconSource).filter(ReconSource.batch_id == batch_id).all()
    }
    items = []
    for row in rows:
        items.append(
            {
                "id": row.id,
                "source_id": row.source_id,
                "source_name": source_names.get(row.source_id, ""),
                "row_no": row.row_no,
                "kind": row.kind,
                "kind_label": SOURCE_KIND_LABELS.get(row.kind, row.kind),
                "contract_no": row.contract_no or "",
                "doc_no": row.doc_no or "",
                "fee_type": row.fee_type or "",
                "fee_period_start": row.fee_period_start.isoformat() if row.fee_period_start else None,
                "fee_period_end": row.fee_period_end.isoformat() if row.fee_period_end else None,
                "occur_date": row.occur_date.isoformat() if row.occur_date else None,
                "due_date": row.due_date.isoformat() if row.due_date else None,
                "amount_1": _amount_str(row.amount_1) or None,
                "amount_2": _amount_str(row.amount_2) or None,
                "amount_3": _amount_str(row.amount_3) or None,
                "amount_4": _amount_str(row.amount_4) or None,
                "fingerprint": row.fingerprint or "",
                "details": row.details or {},
            }
        )
    return {"total": total, "page": page, "page_size": page_size, "items": items}


def _source_stored_path(source: ReconSource, batch_id: int) -> Path | None:
    """源文件磁盘路径：resolved 后必须位于本批次上传目录内，否则拒绝删除。"""
    raw = (source.stored_path or "").strip()
    if not raw:
        return None
    batch_dir = _batch_upload_dir(batch_id)
    resolved = Path(raw).resolve()
    if resolved.parent != batch_dir or resolved.name != Path(raw).name:
        raise ReconConflictError(
            f"源文件「{source.original_name}」存储路径异常，已拒绝删除（请检查数据目录完整性）"
        )
    return resolved


def _remove_source_file(path: Path | None) -> None:
    """删除单个源文件（仅接受 _source_stored_path 校验过的路径）；不存在则跳过。"""
    if path is None or not path.exists():
        return
    if path.is_symlink() or not path.is_file():
        raise ReconConflictError(f"源文件路径异常，已拒绝删除：{path.name}")
    path.unlink()


def delete_source(db: Session, batch_id: int, source_id: int) -> dict:
    """删除源文件：仅 draft/failed；级联 records + suggested matches。

    - 存在 confirmed/rejected 人工引用 → 409（人工结果不得随源删除而丢失）；
    - 磁盘文件同步删除，失败整体回滚并映射中文 409，不静默吞错；
    - draft 批次删掉最后一个 failed 源后清空 error_summary（上传失败的诊断摘要）。
    """
    batch = _get_batch(db, batch_id)
    source = (
        db.query(ReconSource)
        .filter(ReconSource.id == source_id, ReconSource.batch_id == batch_id)
        .one_or_none()
    )
    if source is None:
        raise ReconNotFoundError(f"源文件 {source_id} 不存在或不属于批次 {batch_id}")
    if batch.ever_ready:
        raise ReconConflictError(
            "曾就绪的批次的源文件不可替换（审计留痕），请另建批次"
        )
    if batch.status not in _SOURCE_WRITABLE_STATUSES:
        raise ReconConflictError(
            f"批次当前状态为「{_label(batch.status)}」，仅草稿/失败批次可删除源文件"
            "（已就绪/检查中批次的源文件不可替换，请另建批次）"
        )
    record_ids = [
        row.id
        for row in db.query(ReconRecord.id).filter(ReconRecord.source_id == source_id).all()
    ]
    file_path = _source_stored_path(source, batch_id)
    if record_ids:
        referencing = db.query(ReconMatch).filter(
            (ReconMatch.claim_record_id.in_(record_ids))
            | (ReconMatch.writeoff_record_id.in_(record_ids))
        )
        blocking = referencing.filter(ReconMatch.status.in_(("confirmed", "rejected"))).count()
        if blocking:
            raise ReconConflictError(
                f"存在 {blocking} 条人工确认/排除的匹配引用该源文件的记录，不能删除源文件；"
                "请先在复核中处理这些匹配"
            )
        referencing.filter(ReconMatch.status == "suggested").delete(synchronize_session=False)
        db.query(ReconRecord).filter(ReconRecord.source_id == source_id).delete(
            synchronize_session=False
        )
    deleted_records = len(record_ids)
    was_failed = source.parse_status == "failed"
    db.delete(source)
    try:
        _remove_source_file(file_path)
        # 本机 SQLAlchemy 的 autoflush 不冲刷 pending delete：必须显式 flush
        # 后再统计剩余 failed 源，否则统计会看到尚未删除的当前行
        db.flush()
        if was_failed and batch.status == "draft":
            remaining_failed = (
                db.query(func.count(ReconSource.id))
                .filter(
                    ReconSource.batch_id == batch_id,
                    ReconSource.parse_status == "failed",
                )
                .scalar()
                or 0
            )
            if not remaining_failed:
                batch.error_summary = ""  # 上传失败的诊断摘要随最后一个失败源清除
        db.commit()
    except OSError as exc:
        db.rollback()
        raise ReconConflictError(
            f"源文件删除失败：{exc.strerror or exc}，源文件保持原状请重试"
        ) from exc
    return {"ok": True, "id": source_id, "deleted_records": deleted_records}


# ---------- Task 5：源文件正式列表（前端源清单 / 失败原因 / scope 勾选依赖） ----------


def list_sources(db: Session, batch_id: int) -> dict:
    """源文件正式列表：id/kind/原文件名/parse_status/row_count/error/sha256/created_at，
    附每源检测到的合同号列表（合同范围勾选依据）；绝不返回存储路径。

    合同号取自该源导入的事实记录（去重排序）；解析失败源没有事实行，
    contracts 为空列表——失败原因在 error 字段，不猜值。
    """
    _get_batch(db, batch_id)
    rows = (
        db.query(ReconSource)
        .filter(ReconSource.batch_id == batch_id)
        .order_by(ReconSource.id)
        .all()
    )
    contract_pairs = (
        db.query(ReconRecord.source_id, ReconRecord.contract_no)
        .filter(ReconRecord.batch_id == batch_id, ReconRecord.contract_no != "")
        .all()
    )
    contracts_by_source: dict[int, set[str]] = {}
    for source_id, contract_no in contract_pairs:
        contracts_by_source.setdefault(source_id, set()).add(str(contract_no))
    items = []
    for row in rows:
        items.append(
            {
                "id": row.id,
                "kind": row.kind,
                "kind_label": SOURCE_KIND_LABELS.get(row.kind, row.kind),
                "original_name": row.original_name or "",
                "parse_status": row.parse_status or "pending",
                "row_count": int(row.row_count or 0),
                "error": (row.error or "")[:800],
                "sha256": row.sha256 or "",
                "created_at": row.created_at.isoformat(timespec="seconds") if row.created_at else "",
                "contracts": sorted(contracts_by_source.get(row.id, set())),
            }
        )
    return {"total": len(rows), "items": items}


def precheck_for_check(db: Session, batch_id: int) -> dict:
    """check 前置校验（Task 3 check 路由调用；ValueError 由路由映射 400）。

    校验：合同范围非空（与 transition_batch 同口径）+ 四类来源各至少一份 +
    全部源文件解析成功。返回批次概要供引擎消费。
    """
    batch = _get_batch(db, batch_id)
    scope = _scope_of(batch)
    if not scope:
        raise ValueError("本批次合同范围为空：check 前必须显式勾选本批合同")
    sources = db.query(ReconSource).filter(ReconSource.batch_id == batch_id).all()
    by_kind = {k: 0 for k in ("writeoff", "claim", "arrear", "bill")}
    failed: list[ReconSource] = []
    for source in sources:
        if source.kind in by_kind:
            by_kind[source.kind] += 1
        else:
            failed.append(source)  # 防御：未知 kind 一律视为需处理的异常源
        if source.parse_status != "parsed":
            failed.append(source)
    missing = [
        {"writeoff": "核销", "claim": "认领", "arrear": "欠费", "bill": "账单"}[k]
        for k, count in by_kind.items()
        if count == 0
    ]
    if missing:
        raise ValueError(
            f"缺少源文件：「{'、'.join(missing)}」——四类来源（核销/认领/欠费/账单）"
            "各至少一份才能执行勾稽检查"
        )
    if failed:
        first = failed[0]
        reason = first.error or "解析状态异常"
        raise ValueError(
            f"源文件「{first.original_name}」解析失败：{reason[:300]}；"
            "请先删除该源文件并重新上传，再执行勾稽检查"
        )
    record_count = (
        db.query(func.count(ReconRecord.id)).filter(ReconRecord.batch_id == batch_id).scalar()
        or 0
    )
    return {
        "contract_scope": scope,
        "sources_by_kind": by_kind,
        "record_count": record_count,
        "tolerance": _amount_str(batch.tolerance),
        "as_of": batch.as_of.isoformat() if batch.as_of else "",
        "needs_recheck": bool(batch.needs_recheck),
    }


# ---------- Task 3：口径参数（terms）、导出守卫与 summary 工具扩展 ----------

_EMPTY_TERMS_MISSING = ["未录入口径参数"]


def _terms_item(row: ReconTerm | None, contract_no: str) -> dict:
    """单合同口径参数视图：JSON 经 ContractTerms 固定 schema 校验后字符串序列化。"""
    from pydantic import ValidationError

    from .schemas import ContractTerms, terms_completeness

    if row is None:
        return {
            "contract_no": contract_no,
            "terms": None,
            "complete": False,
            "missing": list(_EMPTY_TERMS_MISSING),
        }
    try:
        terms = ContractTerms.model_validate(row.terms or {})
    except ValidationError:
        return {
            "contract_no": contract_no,
            "terms": None,
            "complete": False,
            "missing": ["口径参数无法解析（存储数据非法），请重新保存"],
        }
    complete, missing = terms_completeness(terms)
    return {
        "contract_no": contract_no,
        "terms": terms.model_dump(mode="json"),
        "complete": complete,
        "missing": missing,
    }


def get_terms(db: Session, batch_id: int) -> dict:
    """口径参数清单（P2-4）：遍历批次合同范围，未录入合同返回完整性/缺失项；
    范围外但已保存的 terms 单独返回（out_of_scope_items），不静默丢弃。"""
    batch = _get_batch(db, batch_id)
    rows = {
        row.contract_no: row
        for row in db.query(ReconTerm)
        .filter(ReconTerm.batch_id == batch_id)
        .all()
    }
    items: list[dict] = []
    in_scope: set[str] = set()
    for contract in _scope_of(batch):
        in_scope.add(contract)
        items.append(_terms_item(rows.get(contract), contract))
    out_of_scope_items = [
        _terms_item(row, contract) for contract, row in rows.items() if contract not in in_scope
    ]
    return {"batch_id": batch_id, "items": items, "out_of_scope_items": out_of_scope_items}


def put_terms(db: Session, batch_id: int, contract_no: str, payload) -> dict:
    """保存单合同口径参数（整体替换）：ready 批次修改只置 needs_recheck=true。

    - checking 批次禁改（409）；draft/failed/ready 可改；
    - ready 批次改后重跑前导出被 assert_exportable 拒绝（409）；
    - terms JSON 仅作序列化容器，金额经 Decimal schema 字符串化，禁止 float。
    """
    from pydantic import ValidationError

    from .schemas import ContractTerms

    batch = _get_batch(db, batch_id)
    if batch.status == "checking":
        raise ReconConflictError(
            f"批次当前状态为「{_label(batch.status)}」，检查进行中不能修改口径参数"
        )
    contract_no = (contract_no or "").strip()
    if not contract_no:
        raise ValueError("合同号不能为空白")
    try:
        terms = payload if isinstance(payload, ContractTerms) else ContractTerms.model_validate(payload)
    except ValidationError as exc:
        first = exc.errors()[0]
        raise ValueError(
            f"口径参数非法（{first.get('loc', ())}：{first.get('msg', '校验失败')}）"
        ) from exc
    row = (
        db.query(ReconTerm)
        .filter(ReconTerm.batch_id == batch_id, ReconTerm.contract_no == contract_no)
        .one_or_none()
    )
    dumped = terms.model_dump(mode="json")
    if row is None:
        row = ReconTerm(batch_id=batch_id, contract_no=contract_no, terms=dumped)
        db.add(row)
    else:
        row.terms = dumped
    if batch.ever_ready:
        # P2-1：曾就绪的批次（即使重跑失败）修改口径后同样需要重跑，导出被守卫拦截
        batch.needs_recheck = True
    elif batch.status == "ready":
        batch.needs_recheck = True  # 口径已变：重跑前导出被拒（409），报告标注需重跑
    with unique_guard(db, "人工口径参数"):
        db.commit()
    return _terms_item(row, contract_no)


def assert_sources_replacable(batch: ReconBatch) -> None:
    """P2-1 审计留痕守卫（权威落点）：曾就绪（ever_ready）的批次即使重跑 failed，
    源文件也不可替换（上传/删除均拒绝）。

    由 ``importer.import_source``（编程入口，服务层强制）与路由上传入口共同调用；
    批次状态层面的守卫由既有上传/删除路径负责。"""
    if batch.ever_ready:
        raise ReconConflictError(
            "曾就绪的批次的源文件不可替换（审计留痕），请另建批次"
        )


def assert_exportable(db: Session, batch_id: int) -> ReconBatch:
    """导出前置守卫：口径参数在最近一次检查通过后又被修改 → 409（Task 4 路由消费）。"""
    batch = _get_batch(db, batch_id)
    if batch.needs_recheck:
        raise ReconConflictError(
            "批次口径参数在上次勾稽检查通过后又被修改，请重新执行勾稽检查后再导出报告"
        )
    return batch
