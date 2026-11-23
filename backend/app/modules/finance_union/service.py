# -*- coding: utf-8 -*-
"""finance_union 服务层：批次/部门行 CRUD、状态机、实时计算与导出组装。

职责边界：

- 两表事务边界；批次 draft → confirmed 是唯一合法转移，确认后整体只读；
- 部门行整批保存（PUT）：先删后插同一事务，部门重复先判 409，唯一约束兜底；
- 派生金额**不落库**：每次读取都由 ``calculation.py`` 按当前输入实时推导，
  因此不存在「批次改了费率但旧金额还留着」的第二事实源；
- 唯一约束冲突统一映射中文 ``UnionConflictError``（路由 409）；
- 缺数据（工资基数 / 上期已计提 / 上期实际）即 unknown，确认一律拒绝，绝不按 0 通过。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Iterable

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import calculation, exporter
from .models import UnionBatch, UnionLine
from .schemas import (
    BATCH_STATUS_LABELS, LINE_STATUS_LABELS, BatchInput, BatchResult, LineInput,
    UnionBatchCreate, UnionBatchDetailOut, UnionBatchListOut, UnionBatchOut, UnionBatchPatch,
    UnionChecksOut, UnionLineIn, UnionLineOut, UnionLinesReplace, UnionPreviewOut,
    UnionSummaryItem, UnionSummaryOut, validate_period,
)

# 状态机唯一事实源：confirmed 是终态，没有任何出边
BATCH_TRANSITIONS: dict[str, frozenset[str]] = {
    "draft": frozenset({"confirmed"}),
    "confirmed": frozenset(),
}


class UnionNotFoundError(LookupError):
    """批次不存在（路由映射 404）。"""


class UnionConflictError(Exception):
    """状态迁移、只读拦截、重复键、唯一约束冲突（路由映射 409）。"""


class UnionValidationError(ValueError):
    """非法输入（路由映射 400）。"""


@dataclass
class UnionExportFile:
    filename: str
    filename_ascii: str
    content: bytes


# ---------- 内部工具 ----------


def _check_period(period: str) -> str:
    try:
        return validate_period(period)
    except ValueError as exc:
        raise UnionValidationError(str(exc)) from exc


def _get_batch(db: Session, batch_id: int) -> UnionBatch:
    batch = db.get(UnionBatch, batch_id)
    if batch is None:
        raise UnionNotFoundError(f"工会经费计提批次不存在：id={batch_id}")
    return batch


def _require_draft(batch: UnionBatch, action: str) -> None:
    if batch.status != "draft":
        raise UnionConflictError(
            f"批次当前状态「{BATCH_STATUS_LABELS.get(batch.status, batch.status)}」，{action}被拒绝；"
            "已确认批次整体只读"
        )


def _lines_of(db: Session, batch_id: int) -> list[UnionLine]:
    return (
        db.query(UnionLine)
        .filter(UnionLine.batch_id == batch_id)
        .order_by(UnionLine.id.asc())
        .all()
    )


def _to_input(line: UnionLine) -> LineInput:
    return LineInput(
        department=line.department,
        current_wage_base=line.current_wage_base,
        prior_accrual=line.prior_accrual,
        prior_actual=line.prior_actual,
    )


def _compute(batch: UnionBatch, lines: Iterable[UnionLine]) -> BatchResult:
    """调用纯计算引擎；批次费率是唯一费率来源。"""
    payload = BatchInput(
        entity=batch.entity,
        period=batch.period,
        rate=Decimal(batch.rate),
        lines=[_to_input(line) for line in lines],
    )
    return calculation.compute(payload)


def _batch_out(batch: UnionBatch, result: BatchResult) -> UnionBatchOut:
    return UnionBatchOut(
        id=batch.id,
        entity=batch.entity,
        period=batch.period,
        rate=Decimal(batch.rate),
        status=batch.status,
        status_label=BATCH_STATUS_LABELS.get(batch.status, batch.status),
        remark=batch.remark or "",
        line_count=result.line_count,
        unknown_line_count=result.unknown_line_count,
        current_accrued_total=result.totals.current_accrued,
        prior_variance_total=result.totals.prior_variance,
        current_book_amount_total=result.totals.current_book_amount,
        confirmable=result.confirmable,
        confirmed_at=batch.confirmed_at,
        created_at=batch.created_at,
        updated_at=batch.updated_at,
    )


def _line_out(line: UnionLine, row) -> UnionLineOut:
    return UnionLineOut(
        id=line.id,
        batch_id=line.batch_id,
        department=line.department,
        current_wage_base=line.current_wage_base,
        prior_accrual=line.prior_accrual,
        prior_actual=line.prior_actual,
        line_status=row.line_status,
        line_status_label=LINE_STATUS_LABELS[row.line_status],
        current_accrued=row.current_accrued,
        prior_variance=row.prior_variance,
        current_book_amount=row.current_book_amount,
        missing_fields=list(row.missing_fields),
        note=row.note,
    )


def _sorted_result(result: BatchResult, lines: list[UnionLine]):
    """按数据库行顺序把计算结果与行一一配对（部门名唯一，不会错配）。"""
    by_department = {row.department: row for row in result.lines}
    return [(line, by_department[line.department]) for line in lines]


def _commit(db: Session, conflict_message: str) -> None:
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise UnionConflictError(conflict_message) from exc


def _duplicate_department(items: list[UnionLineIn]) -> str | None:
    seen: set[str] = set()
    for item in items:
        if item.department in seen:
            return item.department
        seen.add(item.department)
    return None


# ---------- 批次 ----------


def create_batch(db: Session, payload: UnionBatchCreate) -> UnionBatchOut:
    """新建草稿批次：主体 + 期间唯一，费率必填（无默认值）。"""
    entity = payload.entity.strip()
    period = _check_period(payload.period)
    existing = (
        db.query(UnionBatch)
        .filter(UnionBatch.entity == entity, UnionBatch.period == period)
        .first()
    )
    if existing is not None:
        raise UnionConflictError(
            f"主体「{entity}」期间 {period} 已存在批次（id={existing.id}），同一主体同一期间只允许一个批次"
        )
    batch = UnionBatch(
        entity=entity,
        period=period,
        rate=payload.rate,
        status="draft",
        remark=payload.remark,
    )
    db.add(batch)
    _commit(db, f"主体「{entity}」期间 {period} 的批次已存在（唯一约束冲突）")
    db.refresh(batch)
    return _batch_out(batch, _compute(batch, []))


def list_batches(
    db: Session,
    *,
    entity: str | None = None,
    period: str | None = None,
    status: str | None = None,
    page: int = 1,
    page_size: int = 50,
) -> UnionBatchListOut:
    query = db.query(UnionBatch)
    if entity:
        query = query.filter(UnionBatch.entity.contains(entity.strip()))
    if period:
        query = query.filter(UnionBatch.period == _check_period(period))
    if status:
        if status not in BATCH_TRANSITIONS:
            raise UnionValidationError(f"未知状态：{status}（只支持 draft / confirmed）")
        query = query.filter(UnionBatch.status == status)
    total = query.count()
    rows = (
        query.order_by(UnionBatch.period.desc(), UnionBatch.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    items = [_batch_out(batch, _compute(batch, _lines_of(db, batch.id))) for batch in rows]
    return UnionBatchListOut(total=total, page=page, page_size=page_size, items=items)


def get_batch_detail(db: Session, batch_id: int) -> UnionBatchDetailOut:
    batch = _get_batch(db, batch_id)
    lines = _lines_of(db, batch_id)
    result = _compute(batch, lines)
    return UnionBatchDetailOut(
        batch=_batch_out(batch, result),
        lines=[_line_out(line, row) for line, row in _sorted_result(result, lines)],
        totals=result.totals,
        checks=result.checks,
        check_counts=result.check_counts,
        all_passed=result.all_passed,
        has_unknown=result.has_unknown,
        confirmable=result.confirmable,
        confirm_reason=result.confirm_reason,
        rule_version=result.rule_version,
    )


def update_batch(db: Session, batch_id: int, payload: UnionBatchPatch) -> UnionBatchOut:
    """草稿内维护批次；改主体/期间会重新做唯一性预检。"""
    batch = _get_batch(db, batch_id)
    _require_draft(batch, "修改")
    entity = batch.entity
    period = batch.period
    if payload.entity is not None:
        entity = payload.entity.strip()
        if not entity:
            raise UnionValidationError("计提主体不能为空白")
    if payload.period is not None:
        period = _check_period(payload.period)
    if entity != batch.entity or period != batch.period:
        clash = (
            db.query(UnionBatch)
            .filter(
                UnionBatch.entity == entity,
                UnionBatch.period == period,
                UnionBatch.id != batch.id,
            )
            .first()
        )
        if clash is not None:
            raise UnionConflictError(
                f"主体「{entity}」期间 {period} 已存在批次（id={clash.id}），不能重复"
            )
    batch.entity = entity
    batch.period = period
    if payload.rate is not None:
        batch.rate = payload.rate
    if payload.remark is not None:
        batch.remark = payload.remark
    _commit(db, f"主体「{entity}」期间 {period} 的批次已存在（唯一约束冲突）")
    db.refresh(batch)
    return _batch_out(batch, _compute(batch, _lines_of(db, batch.id)))


def delete_batch(db: Session, batch_id: int) -> dict:
    """删除草稿批次（连带部门行）；已确认批次 409。"""
    batch = _get_batch(db, batch_id)
    _require_draft(batch, "删除")
    db.query(UnionLine).filter(UnionLine.batch_id == batch.id).delete()
    db.delete(batch)
    db.commit()
    return {"deleted": True, "id": batch_id}


# ---------- 部门行 ----------


def list_lines(db: Session, batch_id: int) -> list[UnionLineOut]:
    batch = _get_batch(db, batch_id)
    lines = _lines_of(db, batch_id)
    result = _compute(batch, lines)
    return [_line_out(line, row) for line, row in _sorted_result(result, lines)]


def replace_lines(db: Session, batch_id: int, payload: UnionLinesReplace) -> list[UnionLineOut]:
    """整批保存部门行；空列表表示清空（清空后无法确认）。"""
    batch = _get_batch(db, batch_id)
    _require_draft(batch, "编辑部门行")
    duplicate = _duplicate_department(payload.items)
    if duplicate is not None:
        raise UnionConflictError(f"部门「{duplicate}」在本次提交中重复，同一批次内部门必须唯一")
    db.query(UnionLine).filter(UnionLine.batch_id == batch.id).delete()
    for item in payload.items:
        db.add(UnionLine(
            batch_id=batch.id,
            department=item.department,
            current_wage_base=item.current_wage_base,
            prior_accrual=item.prior_accrual,
            prior_actual=item.prior_actual,
        ))
    _commit(db, "部门行保存失败：同一批次内部门必须唯一")
    return list_lines(db, batch_id)


# ---------- 预览 / 校验 / 确认 ----------


def preview(db: Session, batch_id: int) -> UnionPreviewOut:
    """计算预览：只读，不落库、不改状态。"""
    batch = _get_batch(db, batch_id)
    result = _compute(batch, _lines_of(db, batch_id))
    return UnionPreviewOut(
        batch_id=batch.id,
        entity=batch.entity,
        period=batch.period,
        rate=Decimal(batch.rate),
        status=batch.status,
        status_label=BATCH_STATUS_LABELS.get(batch.status, batch.status),
        rule_version=result.rule_version,
        line_count=result.line_count,
        ok_line_count=result.ok_line_count,
        unknown_line_count=result.unknown_line_count,
        lines=result.lines,
        totals=result.totals,
        checks=result.checks,
        check_counts=result.check_counts,
        all_passed=result.all_passed,
        has_unknown=result.has_unknown,
        confirmable=result.confirmable,
        confirm_reason=result.confirm_reason,
    )


def checks(db: Session, batch_id: int) -> UnionChecksOut:
    batch = _get_batch(db, batch_id)
    result = _compute(batch, _lines_of(db, batch_id))
    return UnionChecksOut(
        batch_id=batch.id,
        status=batch.status,
        rule_version=result.rule_version,
        checks=result.checks,
        check_counts=result.check_counts,
        all_passed=result.all_passed,
        has_unknown=result.has_unknown,
        confirmable=result.confirmable,
        confirm_reason=result.confirm_reason,
    )


def confirm_batch(db: Session, batch_id: int) -> UnionBatchOut:
    """draft → confirmed；缺数据/差异时不确认（中文原因）。"""
    batch = _get_batch(db, batch_id)
    if batch.status == "confirmed":
        raise UnionConflictError("该批次已确认，确认后不可重复确认或修改")
    if "confirmed" not in BATCH_TRANSITIONS.get(batch.status, frozenset()):
        raise UnionConflictError(
            f"批次状态「{BATCH_STATUS_LABELS.get(batch.status, batch.status)}」不允许确认"
        )
    lines = _lines_of(db, batch_id)
    result = _compute(batch, lines)
    if not result.confirmable:
        raise UnionConflictError(result.confirm_reason or "当前批次不满足确认条件")
    batch.status = "confirmed"
    batch.confirmed_at = datetime.now()
    db.commit()
    db.refresh(batch)
    return _batch_out(batch, _compute(batch, _lines_of(db, batch_id)))


# ---------- 导出 ----------


def _export_payload(batch: UnionBatch, lines: list[UnionLine], result: BatchResult,
                    exported_at: datetime) -> dict:
    status = "pass" if result.all_passed else (
        "fail" if result.check_counts.get("failed") else "unknown")
    paired = _sorted_result(result, lines)
    return {
        "batch": {
            "id": batch.id,
            "entity": batch.entity,
            "period": batch.period,
            "rate": batch.rate,
            "status": batch.status,
            "status_label": BATCH_STATUS_LABELS.get(batch.status, batch.status),
            "remark": batch.remark or "",
            "confirmed_at": (batch.confirmed_at.isoformat(sep=" ", timespec="seconds")
                             if batch.confirmed_at else ""),
            "created_at": batch.created_at.isoformat(sep=" ", timespec="seconds"),
        },
        "lines": [
            {
                "department": line.department,
                "current_wage_base": line.current_wage_base,
                "prior_accrual": line.prior_accrual,
                "prior_actual": line.prior_actual,
                "line_status": row.line_status,
                "line_status_label": LINE_STATUS_LABELS[row.line_status],
                "current_accrued": row.current_accrued,
                "prior_variance": row.prior_variance,
                "current_book_amount": row.current_book_amount,
                "missing_fields": list(row.missing_fields),
                "note": row.note,
            }
            for line, row in paired
        ],
        "totals": result.totals.model_dump(),
        "checks": {
            "status": status,
            "counts": dict(result.check_counts),
            "items": [
                {
                    "code": check.key,
                    "title": check.title,
                    "status": {"ok": "pass", "failed": "fail", "unknown": "unknown"}[check.status],
                    "left": None if check.expected is None else str(check.expected),
                    "right": None if check.actual is None else str(check.actual),
                    "diff": None if check.difference is None else str(check.difference),
                    "message": check.message,
                }
                for check in result.checks
            ],
        },
        "rule_version": result.rule_version,
        "all_passed": result.all_passed,
        "has_unknown": result.has_unknown,
        "exported_at": exported_at,
    }


def export_xlsx(db: Session, batch_id: int) -> UnionExportFile:
    """导出三页签底稿：真实值完整（含未通过/未知结论），不做任何重算。"""
    batch = _get_batch(db, batch_id)
    lines = _lines_of(db, batch_id)
    result = _compute(batch, lines)
    exported_at = datetime.now()
    payload = _export_payload(batch, lines, result, exported_at)
    filename, ascii_name = exporter.download_names(payload)
    return UnionExportFile(
        filename=filename,
        filename_ascii=ascii_name,
        content=exporter.export_union_workbook(payload),
    )


# ---------- 摘要（Agent 只读工具同源） ----------


def union_summary(
    db: Session, *, entity: str | None = None, period: str | None = None
) -> UnionSummaryOut:
    """只读摘要：按主体/期间筛选批次，返回状态计数与可计算行合计。"""
    query = db.query(UnionBatch)
    if entity:
        query = query.filter(UnionBatch.entity.contains(entity.strip()))
    if period:
        query = query.filter(UnionBatch.period == _check_period(period))
    rows = query.order_by(UnionBatch.period.desc(), UnionBatch.id.desc()).all()

    items: list[UnionSummaryItem] = []
    draft = confirmed = unknown_lines = 0
    total_accrued = total_variance = total_book = Decimal("0.00")
    for batch in rows:
        result = _compute(batch, _lines_of(db, batch.id))
        if batch.status == "confirmed":
            confirmed += 1
        else:
            draft += 1
        unknown_lines += result.unknown_line_count
        total_accrued += result.totals.current_accrued
        total_variance += result.totals.prior_variance
        total_book += result.totals.current_book_amount
        items.append(UnionSummaryItem(
            batch_id=batch.id,
            entity=batch.entity,
            period=batch.period,
            rate=Decimal(batch.rate),
            status=batch.status,
            status_label=BATCH_STATUS_LABELS.get(batch.status, batch.status),
            line_count=result.line_count,
            unknown_line_count=result.unknown_line_count,
            current_accrued_total=result.totals.current_accrued,
            prior_variance_total=result.totals.prior_variance,
            current_book_amount_total=result.totals.current_book_amount,
            confirmable=result.confirmable,
            confirmed_at=batch.confirmed_at,
        ))
    return UnionSummaryOut(
        total_batches=len(rows),
        draft_count=draft,
        confirmed_count=confirmed,
        unknown_line_total=unknown_lines,
        current_accrued_total=total_accrued,
        prior_variance_total=total_variance,
        current_book_amount_total=total_book,
        batches=items,
    )
