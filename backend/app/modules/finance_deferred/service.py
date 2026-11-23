"""finance_deferred 服务层：项目 CRUD、摊销测算、状态机、风险确认与导出接缝。

职责边界：

- 三表事务边界：项目 draft/archived；测算 calculated → confirmed 单向；
- 同输入（经济要素）测算幂等，历史快照不可覆盖；过期结果阻断当前确认与导出；
- 测算只接收计算引擎输出并校验结构一致性，**不在本文件实现会计计算**；
- 引擎/导出器通过 Protocol 注入，缺失时抛中文 ``DeferredEngineUnavailable``，
  绝不返回假成功；
- 失败测算在写库前抛错，不落半套结果；唯一约束冲突统一映射中文 409。
"""
from __future__ import annotations

import hashlib
import importlib
import json
from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from types import SimpleNamespace
from typing import Iterator, Protocol

from pydantic import ValidationError
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import checks as checks_module
from . import calculation as calculation_module
from .models import DeferredItem, DeferredPeriod, DeferredRun
from .schemas import (
    CHECK_STATUSES, ITEM_STATUS_LABELS, RUN_STATUS_LABELS, AmortizationSummary,
    CalculationInput, ChecksOut, DeferredCheck, DeferredConfirmIn, DeferredExportBundle,
    DeferredExportFile, DeferredExportItem, DeferredItemCreate, DeferredItemListOut,
    DeferredItemOut, DeferredItemPatch, DeferredSummaryItem, DeferredSummaryOut, PeriodRowOut,
    RunOut, RunResult,
)

_CENT = Decimal("0.01")
_RATE = Decimal("0.0001")

# 状态机唯一事实源：归档与确认都是终态
ITEM_TRANSITIONS: dict[str, frozenset[str]] = {
    "draft": frozenset({"archived"}),
    "archived": frozenset(),
}
RUN_TRANSITIONS: dict[str, frozenset[str]] = {
    "calculated": frozenset({"confirmed"}),
    "confirmed": frozenset(),
}


class DeferredNotFoundError(LookupError):
    """项目/测算不存在（路由映射 404）。"""


class DeferredConflictError(Exception):
    """状态迁移、归档只读、删除守卫、风险未确认、过期或唯一约束冲突（路由映射 409）。"""


class DeferredValidationError(ValueError):
    """非法输入（路由映射 400）。"""


class DeferredCalculationError(ValueError):
    """计算引擎输出结构不合法或快照损坏。"""


class DeferredEngineUnavailable(RuntimeError):
    """计算/导出模块尚未接入（路由映射 503，绝不假成功）。"""


class CalculationEngine(Protocol):
    """计算引擎接缝：纯数据输入输出，不读数据库、不写文件。"""

    rule_version: str

    def compute(self, payload: CalculationInput) -> RunResult: ...


class WorkbookExporter(Protocol):
    """底稿导出器接缝：输入完整快照，输出 xlsx 字节与文件名。"""

    def export(self, bundle: DeferredExportBundle) -> DeferredExportFile: ...


def _missing_module(name: str, label: str) -> DeferredEngineUnavailable:
    return DeferredEngineUnavailable(
        f"{label}尚未接入：缺少 app/modules/finance_deferred/{name}.py；"
        "本阶段只定义调用接缝，请由集成阶段接通后再调用"
    )


def resolve_calculation_engine() -> CalculationEngine:
    """默认计算引擎：calculation.py 必须声明 RULE_VERSION 与 compute(payload)。"""
    try:
        module = importlib.import_module(f"{__package__}.calculation")
    except ModuleNotFoundError as exc:
        if exc.name != f"{__package__}.calculation":
            raise
        raise _missing_module("calculation", "计算引擎") from exc
    version, compute = getattr(module, "RULE_VERSION", None), getattr(module, "compute", None)
    if not isinstance(version, str) or not version.strip():
        raise DeferredEngineUnavailable("计算引擎未声明 RULE_VERSION，已拒绝测算")
    if not callable(compute):
        raise DeferredEngineUnavailable("计算引擎未提供 compute(payload)，已拒绝测算")
    return SimpleNamespace(rule_version=version.strip(), compute=compute)


def resolve_workbook_exporter() -> WorkbookExporter:
    """默认导出器：exporter.py 必须提供 export(bundle)。"""
    try:
        module = importlib.import_module(f"{__package__}.exporter")
    except ModuleNotFoundError as exc:
        if exc.name != f"{__package__}.exporter":
            raise
        raise _missing_module("exporter", "底稿导出器") from exc
    export = getattr(module, "export", None)
    if not callable(export):
        raise DeferredEngineUnavailable("导出器未提供 export(bundle)，已拒绝导出")
    return SimpleNamespace(export=export)


# ---------- 通用工具 ----------

def _q2(value) -> Decimal:
    return Decimal(str(value or 0)).quantize(_CENT, rounding=ROUND_HALF_UP)


def _q4(value) -> Decimal:
    return Decimal(str(value or 0)).quantize(_RATE, rounding=ROUND_HALF_UP)


def _label(mapping: dict[str, str], value: str) -> str:
    return mapping.get(value, value)


def _get_item(db: Session, item_id: int) -> DeferredItem:
    item = db.get(DeferredItem, item_id)
    if item is None:
        raise DeferredNotFoundError(f"长期待摊项目 {item_id} 不存在")
    return item


def _get_run(db: Session, run_id: int) -> DeferredRun:
    run = db.get(DeferredRun, run_id)
    if run is None:
        raise DeferredNotFoundError(f"测算批次 {run_id} 不存在")
    return run


def _ordered_periods(db: Session, run_id: int) -> list[DeferredPeriod]:
    return (db.query(DeferredPeriod).filter(DeferredPeriod.run_id == run_id)
            .order_by(DeferredPeriod.sequence.asc()).all())


@contextmanager
def _unique_guard(db: Session, label: str) -> Iterator[None]:
    try:
        yield
    except IntegrityError as exc:
        db.rollback()
        raise DeferredConflictError(f"{label}：唯一约束冲突，请检查是否重复提交") from exc


# ---------- 派生金额、哈希与快照 ----------

def derive_amounts(amount_incl_tax, tax_rate) -> tuple[Decimal, Decimal]:
    """净额与税额的唯一派生入口（与计算引擎同一实现，禁止第二份公式）。"""
    net = checks_module.expected_net(amount_incl_tax, tax_rate)
    return net, _q2(Decimal(amount_incl_tax) - net)


def _hash_payload(item: DeferredItem) -> dict:
    """参与 input_hash 的只有经济要素：名称/备注/状态等展示字段不参与。"""
    return {
        "amount_incl_tax": str(_q2(item.amount_incl_tax)),
        "tax_rate": str(_q4(item.tax_rate)),
        "start_month": item.start_month,
        "months": int(item.months),
        "lease_end_date": item.lease_end_date.isoformat(),
    }


def _input_hash(item: DeferredItem) -> str:
    canonical = json.dumps(_hash_payload(item), ensure_ascii=False,
                           sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def current_input_hash(db: Session, item_id: int) -> str:
    """当前项目参数的输入哈希（供过期判断与路由层复用）。"""
    return _input_hash(_get_item(db, item_id))


def _snapshot_payload(item: DeferredItem) -> dict:
    return {
        "project_code": item.project_code,
        "name": item.name or "",
        "project": item.project or "",
        "amount_incl_tax": str(_q2(item.amount_incl_tax)),
        "tax_rate": str(_q4(item.tax_rate)),
        "amount_net": str(_q2(item.amount_net)),
        "tax_amount": str(_q2(item.tax_amount)),
        "start_month": item.start_month,
        "months": int(item.months),
        "lease_end_date": item.lease_end_date.isoformat(),
        "remark": item.remark or "",
        "status": item.status,
        "status_label": _label(ITEM_STATUS_LABELS, item.status),
    }


def amortization_end_month(item: DeferredItem) -> str:
    return calculation_module.month_grid(item.start_month, int(item.months))[-1]


# ---------- 输出组装 ----------

def _parsed_run(run: DeferredRun) -> tuple[AmortizationSummary, list[DeferredCheck]]:
    try:
        summary = AmortizationSummary.model_validate(run.summary or {})
        checks = [DeferredCheck.model_validate(item) for item in (run.checks or [])]
    except (ValidationError, TypeError) as exc:
        raise DeferredCalculationError("测算快照损坏：汇总值或校验结果无法解析，请重新测算") from exc
    if not checks:
        raise DeferredCalculationError("测算快照缺少校验结果，不能作为有效结果使用")
    return summary, checks


def _check_counts(checks: list[DeferredCheck]) -> dict[str, int]:
    counts = {status: 0 for status in CHECK_STATUSES}
    for check in checks:
        counts[check.status] += 1
    return counts


def _passes_without_failure(counts: dict[str, int]) -> bool:
    """无差异且无未知即视为可确认（warning 已披露，需显式风险确认）。"""
    return counts["failed"] == 0 and counts["unknown"] == 0


def _run_out(db: Session, run: DeferredRun, *, current_hash: str | None = None) -> RunOut:
    summary, checks = _parsed_run(run)
    counts = _check_counts(checks)
    if current_hash is None:
        current_hash = current_input_hash(db, run.item_id)
    return RunOut(
        id=run.id, item_id=run.item_id, status=run.status,
        status_label=_label(RUN_STATUS_LABELS, run.status), input_hash=run.input_hash,
        rule_version=run.rule_version, summary=summary, checks=checks, check_counts=counts,
        all_checks_passed=_passes_without_failure(counts),
        has_warnings=counts["warning"] > 0, has_unknown_checks=counts["unknown"] > 0,
        stale=run.input_hash != current_hash, risk_acknowledged=bool(run.risk_acknowledged),
        risk_note=run.risk_note or "", confirmed_at=run.confirmed_at,
        created_at=run.created_at, updated_at=run.updated_at,
    )


def _latest_run_map(db: Session, item_ids: list[int]) -> dict[int, DeferredRun]:
    if not item_ids:
        return {}
    rows = (db.query(DeferredRun.item_id, func.max(DeferredRun.id))
            .filter(DeferredRun.item_id.in_(item_ids))
            .group_by(DeferredRun.item_id).all())
    latest_ids = [latest_id for _, latest_id in rows if latest_id is not None]
    if not latest_ids:
        return {}
    return {row.item_id: row for row in
            db.query(DeferredRun).filter(DeferredRun.id.in_(latest_ids)).all()}


def _item_outs(db: Session, items: list[DeferredItem]) -> list[DeferredItemOut]:
    if not items:
        return []
    ids = [item.id for item in items]
    run_counts = dict(db.query(DeferredRun.item_id, func.count(DeferredRun.id))
                      .filter(DeferredRun.item_id.in_(ids))
                      .group_by(DeferredRun.item_id).all())
    confirmed_counts = dict(
        db.query(DeferredRun.item_id, func.count(DeferredRun.id))
        .filter(DeferredRun.item_id.in_(ids), DeferredRun.status == "confirmed")
        .group_by(DeferredRun.item_id).all())
    latest_map = _latest_run_map(db, ids)
    outs: list[DeferredItemOut] = []
    for item in items:
        latest = latest_map.get(item.id)
        outs.append(DeferredItemOut(
            id=item.id, project_code=item.project_code, name=item.name or "",
            project=item.project or "", amount_incl_tax=item.amount_incl_tax,
            tax_rate=item.tax_rate, amount_net=item.amount_net, tax_amount=item.tax_amount,
            start_month=item.start_month, months=item.months,
            amortization_end_month=amortization_end_month(item),
            lease_end_date=item.lease_end_date, remark=item.remark or "",
            status=item.status, status_label=_label(ITEM_STATUS_LABELS, item.status),
            run_count=int(run_counts.get(item.id, 0)),
            confirmed_run_count=int(confirmed_counts.get(item.id, 0)),
            latest_run_id=latest.id if latest else None,
            latest_run_status=latest.status if latest else None,
            latest_run_stale=bool(latest and latest.input_hash != _input_hash(item)),
            created_at=item.created_at, updated_at=item.updated_at,
        ))
    return outs


# ---------- 项目主档 ----------

def create_item(db: Session, payload: DeferredItemCreate) -> DeferredItemOut:
    net, tax = derive_amounts(payload.amount_incl_tax, payload.tax_rate)
    item = DeferredItem(
        project_code=payload.project_code, name=payload.name, project=payload.project,
        amount_incl_tax=payload.amount_incl_tax, tax_rate=payload.tax_rate,
        amount_net=net, tax_amount=tax, start_month=payload.start_month,
        months=payload.months, lease_end_date=payload.lease_end_date,
        remark=payload.remark, status="draft",
    )
    with _unique_guard(db, "项目创建"):
        db.add(item)
        db.commit()
    db.refresh(item)
    return _item_outs(db, [item])[0]


def list_items(db: Session, *, status: str | None = None, keyword: str | None = None,
               page: int = 1, page_size: int = 50) -> DeferredItemListOut:
    if status is not None and status not in ITEM_STATUS_LABELS:
        raise DeferredValidationError(f"未知项目状态：{status}")
    if page < 1:
        raise DeferredValidationError("页码必须大于等于 1")
    if not 1 <= page_size <= 200:
        raise DeferredValidationError("每页数量必须在 1–200 之间")
    query = db.query(DeferredItem)
    if status is not None:
        query = query.filter(DeferredItem.status == status)
    if keyword and keyword.strip():
        text = keyword.strip()
        query = query.filter(DeferredItem.project_code.contains(text, autoescape=True)
                              | DeferredItem.name.contains(text, autoescape=True)
                              | DeferredItem.project.contains(text, autoescape=True))
    total = query.count()
    rows = (query.order_by(DeferredItem.id.desc())
            .offset((page - 1) * page_size).limit(page_size).all())
    return DeferredItemListOut(total=total, items=_item_outs(db, rows))


def get_item(db: Session, item_id: int) -> DeferredItemOut:
    return _item_outs(db, [_get_item(db, item_id)])[0]


def update_item(db: Session, item_id: int, payload: DeferredItemPatch) -> DeferredItemOut:
    item = _get_item(db, item_id)
    if item.status == "archived":
        raise DeferredConflictError("项目已归档，只读；如需修改请新建项目或先由业务确认解除归档口径")
    data = payload.model_dump(exclude_unset=True)
    if not data:
        raise DeferredValidationError("未提供任何要更新的字段")
    target = data.pop("status", None) if "status" in payload.model_fields_set else None
    if "status" in payload.model_fields_set and target is None:
        raise DeferredValidationError("字段 status 不能为 null")
    if target is not None and target not in ITEM_TRANSITIONS.get(item.status, frozenset()):
        raise DeferredConflictError(
            f"项目状态不允许从「{_label(ITEM_STATUS_LABELS, item.status)}」"
            f"变更为「{_label(ITEM_STATUS_LABELS, str(target))}」")
    for field, value in data.items():
        if value is None:
            raise DeferredValidationError(f"字段 {field} 不能为 null")
    for field, value in data.items():
        setattr(item, field, value)
    item.amount_net, item.tax_amount = derive_amounts(item.amount_incl_tax, item.tax_rate)
    if target is not None:
        item.status = target
    with _unique_guard(db, "项目更新"):
        db.commit()
    db.refresh(item)
    return _item_outs(db, [item])[0]


def delete_item(db: Session, item_id: int) -> dict:
    item = _get_item(db, item_id)
    run_count = (db.query(func.count(DeferredRun.id))
                 .filter(DeferredRun.item_id == item_id).scalar() or 0)
    if run_count:
        raise DeferredConflictError(
            f"项目已有 {run_count} 条测算历史，不可删除（审计留痕）；请改为归档")
    db.delete(item)
    db.commit()
    return {"deleted": True, "item_id": item_id}


# ---------- 测算 ----------

def _engine_version(engine: CalculationEngine) -> str:
    version = getattr(engine, "rule_version", None)
    if not isinstance(version, str) or not version.strip():
        raise DeferredCalculationError("计算引擎未声明 rule_version，已拒绝测算")
    if not callable(getattr(engine, "compute", None)):
        raise DeferredCalculationError("计算引擎未提供 compute(payload)，已拒绝测算")
    return version.strip()


def _engine_input(item: DeferredItem) -> CalculationInput:
    return CalculationInput(
        amount_incl_tax=item.amount_incl_tax, tax_rate=item.tax_rate,
        start_month=item.start_month, months=int(item.months),
        lease_end_date=item.lease_end_date)


def _validate_result(raw: object, item: DeferredItem, rule_version: str) -> RunResult:
    """只校验结构一致性与输入对齐，不重算会计口径。"""
    try:
        result = RunResult.model_validate(raw)
    except ValidationError as exc:
        first = exc.errors()[0] if exc.errors() else {}
        location = ".".join(str(part) for part in first.get("loc", ()))
        raise DeferredCalculationError(
            f"计算引擎返回结构不合法：{location} {first.get('msg', '')}".strip()) from exc
    if result.rule_version != rule_version:
        raise DeferredCalculationError(
            f"计算引擎返回的规则版本 {result.rule_version} 与引擎声明 {rule_version} 不一致")

    summary = result.summary
    if (_q2(summary.amount_incl_tax) != _q2(item.amount_incl_tax)
            or _q4(summary.tax_rate) != _q4(item.tax_rate)
            or summary.months != int(item.months)
            or summary.start_month != item.start_month
            or summary.lease_end_date != item.lease_end_date):
        raise DeferredCalculationError("计算引擎汇总与项目参数不一致，已拒绝保存")
    expected_months = calculation_module.month_grid(item.start_month, int(item.months))
    labels = [row.period_month for row in result.periods]
    if labels != expected_months:
        raise DeferredCalculationError(
            "计算引擎期间行与起摊月/摊销月数不一致（月份必须连续且从起摊月开始）")
    monthly = _q2(summary.monthly_amount)
    cumulative = Decimal("0.00")
    for index, row in enumerate(result.periods, start=1):
        cumulative = cumulative + _q2(row.monthly_amount)
        if (_q2(row.monthly_amount) != monthly or row.sequence != index
                or _q2(row.cumulative) != cumulative
                or _q2(row.balance) != _q2(summary.amount_net) - cumulative
                or row.remaining_months != int(item.months) - index):
            raise DeferredCalculationError(f"计算引擎第 {index} 行摊销滚动不自洽，已拒绝保存")
    if summary.period_count != len(result.periods):
        raise DeferredCalculationError("计算引擎汇总期间行数与期间明细不一致")
    if len({check.key for check in result.checks}) != len(result.checks):
        raise DeferredCalculationError("计算引擎校验结果 key 重复")
    return result


def create_run(db: Session, item_id: int, *,
               engine: CalculationEngine | None = None) -> RunOut:
    """生成或幂等返回测算批次；失败计算不写任何结果行。"""
    item = _get_item(db, item_id)
    if item.status != "draft":
        raise DeferredConflictError("项目已归档，不能发起测算")
    engine = engine or resolve_calculation_engine()
    rule_version = _engine_version(engine)
    digest = _input_hash(item)
    existing = (db.query(DeferredRun).filter_by(
        item_id=item_id, input_hash=digest, rule_version=rule_version).one_or_none())
    if existing is not None:
        return _run_out(db, existing, current_hash=digest)

    result = _validate_result(engine.compute(_engine_input(item)), item, rule_version)
    run = DeferredRun(
        item_id=item_id, input_hash=digest, rule_version=rule_version, status="calculated",
        input_snapshot=_snapshot_payload(item),
        summary=result.summary.model_dump(mode="json"),
        checks=[check.model_dump(mode="json") for check in result.checks],
    )
    try:
        db.add(run)
        db.flush()
        db.add_all([
            DeferredPeriod(
                run_id=run.id, period_month=row.period_month, sequence=row.sequence,
                monthly_amount=row.monthly_amount, cumulative=row.cumulative,
                balance=row.balance, remaining_months=row.remaining_months)
            for row in sorted(result.periods, key=lambda row: row.sequence)
        ])
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        existing = (db.query(DeferredRun).filter_by(
            item_id=item_id, input_hash=digest, rule_version=rule_version).one_or_none())
        if existing is not None:
            return _run_out(db, existing, current_hash=digest)
        raise DeferredConflictError("测算保存失败：唯一约束冲突，请重试") from exc
    except Exception:
        db.rollback()
        raise
    db.refresh(run)
    return _run_out(db, run, current_hash=digest)


def list_runs(db: Session, item_id: int) -> list[RunOut]:
    item = _get_item(db, item_id)
    digest = _input_hash(item)
    rows = (db.query(DeferredRun).filter(DeferredRun.item_id == item_id)
            .order_by(DeferredRun.id.desc()).all())
    return [_run_out(db, row, current_hash=digest) for row in rows]


def get_run(db: Session, run_id: int) -> RunOut:
    return _run_out(db, _get_run(db, run_id))


def get_periods(db: Session, run_id: int) -> list[PeriodRowOut]:
    _get_run(db, run_id)
    return [PeriodRowOut.model_validate(row) for row in _ordered_periods(db, run_id)]


def get_checks(db: Session, run_id: int) -> ChecksOut:
    run = _get_run(db, run_id)
    _, checks = _parsed_run(run)
    counts = _check_counts(checks)
    current_hash = current_input_hash(db, run.item_id)
    return ChecksOut(
        run_id=run.id, input_hash=run.input_hash, rule_version=run.rule_version,
        stale=run.input_hash != current_hash, checks=checks, check_counts=counts,
        all_passed=_passes_without_failure(counts), has_warnings=counts["warning"] > 0,
        has_unknown=counts["unknown"] > 0)


def confirm_run(db: Session, run_id: int, payload: DeferredConfirmIn | None = None,
                *, engine: CalculationEngine | None = None) -> RunOut:
    """确认测算：当前输入、规则版本未变、无差异/未知；warning 需显式风险说明。"""
    run = _get_run(db, run_id)
    if "confirmed" not in RUN_TRANSITIONS.get(run.status, frozenset()):
        raise DeferredConflictError("该测算已确认，已确认快照不可重复确认或改写")
    item = _get_item(db, run.item_id)
    if item.status != "draft":
        raise DeferredConflictError("项目已归档，不能确认测算")
    digest = _input_hash(item)
    if digest != run.input_hash:
        raise DeferredConflictError("测算结果已过期：项目参数已变化，请重新测算后再确认")
    engine = engine or resolve_calculation_engine()
    rule_version = _engine_version(engine)
    if rule_version != run.rule_version:
        raise DeferredConflictError(
            f"规则版本已更新（测算 {run.rule_version} / 当前 {rule_version}），"
            "请重新测算后再确认")
    summary, checks = _parsed_run(run)
    counts = _check_counts(checks)
    if counts["failed"] or counts["unknown"]:
        raise DeferredConflictError(
            f"校验未通过（差异 {counts['failed']} 项、未知 {counts['unknown']} 项），不能确认")
    period_count = (db.query(func.count(DeferredPeriod.id))
                    .filter(DeferredPeriod.run_id == run.id).scalar() or 0)
    if period_count != summary.months:
        raise DeferredConflictError("测算快照不完整（期间行数与摊销月数不一致），请重新测算")

    payload = payload or DeferredConfirmIn()
    risk_note = (payload.risk_note or "").strip()
    if counts["warning"]:
        if not payload.risk_acknowledged:
            titles = "；".join(check.title for check in checks if check.status == "warning")
            raise DeferredConflictError(
                f"存在 {counts['warning']} 项风险警告（{titles}），"
                "确认前必须显式确认风险并填写风险说明")
        if not risk_note:
            raise DeferredConflictError(
                "已勾选确认风险，但风险说明为空；请写明接受该风险的依据后再确认")
    run.status = "confirmed"
    run.confirmed_at = datetime.now()
    run.risk_acknowledged = bool(payload.risk_acknowledged)
    run.risk_note = risk_note
    db.commit()
    db.refresh(run)
    return _run_out(db, run, current_hash=digest)


# ---------- 导出 ----------

def _export_item(run: DeferredRun, item: DeferredItem) -> DeferredExportItem:
    # 经济参数取测算快照（历史不可覆盖）；项目状态取当前行，如实反映今天是否已归档
    snapshot = run.input_snapshot or {}
    try:
        return DeferredExportItem(
            project_code=str(snapshot["project_code"]),
            name=str(snapshot.get("name") or ""),
            project=str(snapshot.get("project") or ""),
            amount_incl_tax=Decimal(str(snapshot["amount_incl_tax"])),
            tax_rate=Decimal(str(snapshot["tax_rate"])),
            amount_net=Decimal(str(snapshot["amount_net"])),
            tax_amount=Decimal(str(snapshot["tax_amount"])),
            start_month=str(snapshot["start_month"]),
            months=int(snapshot["months"]),
            lease_end_date=snapshot["lease_end_date"],
            remark=str(snapshot.get("remark") or ""),
            status=item.status,
            status_label=_label(ITEM_STATUS_LABELS, item.status),
        )
    except (KeyError, TypeError, ValueError, ValidationError) as exc:
        raise DeferredCalculationError("测算输入快照损坏：项目参数无法解析，请重新测算") from exc


def _export_bundle(db: Session, run: DeferredRun, item: DeferredItem,
                   stale: bool) -> DeferredExportBundle:
    return DeferredExportBundle(
        item=_export_item(run, item),
        run=_run_out(db, run, current_hash=_input_hash(item)),
        periods=[PeriodRowOut.model_validate(row) for row in _ordered_periods(db, run.id)],
        stale=stale, exported_at=datetime.now())


def export_run_xlsx(db: Session, run_id: int, *, allow_stale: bool = False,
                    exporter: WorkbookExporter | None = None) -> DeferredExportFile:
    """导出摊销底稿：过期或存在差异/未知时 409；warning 允许导出（已披露）。"""
    run = _get_run(db, run_id)
    item = _get_item(db, run.item_id)
    stale = run.input_hash != _input_hash(item)
    _, checks = _parsed_run(run)
    counts = _check_counts(checks)
    if not allow_stale:
        if stale:
            raise DeferredConflictError(
                "测算结果已过期：项目参数已变化，请重新测算后再导出；"
                "历史快照需显式允许后导出")
        if counts["failed"] or counts["unknown"]:
            raise DeferredConflictError(
                f"校验未通过（差异 {counts['failed']} 项、未知 {counts['unknown']} 项），"
                "不能导出为当前底稿")
    exporter = exporter or resolve_workbook_exporter()
    raw = exporter.export(_export_bundle(db, run, item, stale))
    if isinstance(raw, DeferredExportFile):
        return raw
    try:
        return DeferredExportFile.model_validate(raw)
    except ValidationError as exc:
        raise DeferredValidationError("导出器未返回有效文件（需要 filename 与 content）") from exc


# ---------- 摘要 ----------

def deferred_summary(db: Session, *, item_id: int | None = None) -> DeferredSummaryOut:
    items = ([_get_item(db, item_id)] if item_id is not None
             else db.query(DeferredItem).order_by(DeferredItem.id.asc()).all())
    outs = _item_outs(db, items)
    latest_map = _latest_run_map(db, [item.id for item in items])
    summary_items: list[DeferredSummaryItem] = []
    with_pending = 0
    for out in outs:
        latest = latest_map.get(out.id)
        counts = {status: 0 for status in CHECK_STATUSES}
        monthly = None
        end_month = out.amortization_end_month
        expiry = False
        acknowledged = False
        if latest is not None:
            latest_summary, checks = _parsed_run(latest)
            counts = _check_counts(checks)
            monthly = latest_summary.monthly_amount
            end_month = latest_summary.end_month
            expiry = latest_summary.expiry_after_lease
            acknowledged = bool(latest.risk_acknowledged)
        pending = counts["failed"] + counts["unknown"]
        with_pending += 1 if pending else 0
        summary_items.append(DeferredSummaryItem(
            item_id=out.id, project_code=out.project_code, name=out.name, project=out.project,
            status=out.status, status_label=out.status_label,
            amount_incl_tax=out.amount_incl_tax, amount_net=out.amount_net,
            monthly_amount=monthly, start_month=out.start_month, end_month=end_month,
            months=out.months, lease_end_date=out.lease_end_date,
            expiry_after_lease=expiry,
            latest_run_id=latest.id if latest else None,
            latest_run_status=latest.status if latest else None,
            latest_rule_version=latest.rule_version if latest else None,
            latest_calculated_at=latest.created_at if latest else None,
            latest_confirmed_at=latest.confirmed_at if latest else None,
            stale=out.latest_run_stale, check_counts=counts, pending_checks=pending,
            risk_acknowledged=acknowledged))
    return DeferredSummaryOut(
        total_items=len(summary_items),
        draft_count=sum(1 for item in summary_items if item.status == "draft"),
        archived_count=sum(1 for item in summary_items if item.status == "archived"),
        items_with_pending=with_pending, items=summary_items)
