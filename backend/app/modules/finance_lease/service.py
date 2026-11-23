"""finance_lease 服务层：合同、付款计划、测算快照、状态机与调用接缝。

职责边界：

- 五表 CRUD 与事务边界、合同 draft/archived、测算 calculated→confirmed；
- 付款计划整表替换，期数必须从 1 连续编号，免租期/0 月期原样保留；
- 测算只接收计算引擎的输出并校验结构一致性，**不在本文件实现会计计算**；
- 计算引擎、导入器、导出器通过 Protocol 注入；默认解析器找不到对应模块时
  抛出中文 ``LeaseEngineUnavailable``，绝不返回假成功或未实现接口；
- 失败测算在写库前抛错，不落半套结果；唯一约束冲突统一映射中文 409。
"""
from __future__ import annotations

import hashlib
import importlib
import json
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from types import SimpleNamespace
from typing import Iterator, Protocol

from pydantic import ValidationError
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import entries as entries_module
from .models import LeaseAssessment, LeaseCalculation, LeaseContract, LeasePayment, LeasePeriod
from .schemas import (
    CALCULATION_STATUS_LABELS, CHECK_STATUSES, CONTRACT_STATUSES, CONTRACT_STATUS_LABELS,
    AssessmentRowOut, CalculationCheck, CalculationInput, CalculationOut, CalculationResult,
    CalculationSummary, ChecksOut, ContractListOut, ContractOut, LeaseContractCreate,
    LeaseContractPatch, LeaseExportBundle, LeaseExportFile, LeasePaymentIn, LeasePaymentsReplace,
    LeaseSummaryItem, LeaseSummaryOut, PaymentImportApplyResult, PaymentImportPreview,
    PaymentImportRow, PaymentInput, PaymentOut, PaymentSnapshot, PeriodRowOut,
    validate_payment_sequences,
)

_CENT = Decimal("0.01")
_RATE = Decimal("0.000001")

# 状态机唯一事实源：合同归档为终态；测算只有 calculated → confirmed
CONTRACT_TRANSITIONS: dict[str, frozenset[str]] = {
    "draft": frozenset({"archived"}),
    "archived": frozenset(),
}
CALCULATION_TRANSITIONS: dict[str, frozenset[str]] = {
    "calculated": frozenset({"confirmed"}),
    "confirmed": frozenset(),
}


class LeaseNotFoundError(LookupError):
    """合同/测算不存在（路由映射 404）。"""


class LeaseConflictError(Exception):
    """状态迁移、归档只读、删除守卫、唯一约束冲突（路由映射 409）。"""


class LeaseValidationError(ValueError):
    """非法输入或导入结果不合法（路由映射 400）。"""


class LeaseCalculationError(ValueError):
    """计算引擎输出结构不合法或快照损坏（按调用方映射 400/500）。"""


class LeaseEngineUnavailable(RuntimeError):
    """计算/导入/导出模块尚未接入（路由映射 503，绝不假成功）。"""


class CalculationEngine(Protocol):
    """计算引擎接缝：纯数据输入输出，不读数据库、不写文件。"""

    rule_version: str

    def compute(self, payload: CalculationInput) -> CalculationResult: ...


class PaymentImporter(Protocol):
    """付款计划导入器接缝：预览阶段只解析，不落库。"""

    def preview(self, content: bytes, filename: str) -> PaymentImportPreview: ...


class WorkbookExporter(Protocol):
    """底稿导出器接缝：输入快照完整、输出 xlsx 字节与文件名。"""

    def export(self, bundle: LeaseExportBundle) -> LeaseExportFile: ...


def _load_module(name: str, label: str):
    """按约定文件名导入兄弟模块；缺失时给出中文说明，其余导入错误原样抛出。"""
    module_name = f"{__package__}.{name}"
    try:
        return importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        if exc.name != module_name:
            raise
        raise LeaseEngineUnavailable(
            f"{label}尚未接入：缺少 app/modules/finance_lease/{name}.py；"
            "本阶段只定义调用接缝，请由集成阶段接通后再调用"
        ) from exc


def resolve_calculation_engine() -> CalculationEngine:
    """默认计算引擎：calculation.py 必须声明 RULE_VERSION 与 compute(payload)。"""
    module = _load_module("calculation", "计算引擎")
    version, compute = getattr(module, "RULE_VERSION", None), getattr(module, "compute", None)
    if not isinstance(version, str) or not version.strip():
        raise LeaseEngineUnavailable("计算引擎未声明 RULE_VERSION，已拒绝测算")
    if not callable(compute):
        raise LeaseEngineUnavailable("计算引擎未提供 compute(payload)，已拒绝测算")
    return SimpleNamespace(rule_version=version.strip(), compute=compute)


def resolve_payment_importer() -> PaymentImporter:
    """默认导入器：兼容 preview(...) 或 importer.parse_payment_schedule(...)。"""
    module = _load_module("importer", "付款计划导入器")
    preview = getattr(module, "preview", None)
    if callable(preview):
        return SimpleNamespace(preview=preview)
    parse = getattr(module, "parse_payment_schedule", None)
    if callable(parse):
        return SimpleNamespace(preview=lambda content, filename: PaymentImportPreview.from_schedule(parse(content, filename)))
    raise LeaseEngineUnavailable("导入器未提供 preview 或 parse_payment_schedule，已拒绝导入")


def resolve_workbook_exporter() -> WorkbookExporter:
    """默认导出器：兼容 export(bundle) 或 exporter.export_calculation_workbook(payload)。"""
    module = _load_module("exporter", "底稿导出器")
    export = getattr(module, "export", None)
    if callable(export):
        return SimpleNamespace(export=export)
    build = getattr(module, "export_calculation_workbook", None)
    names = getattr(module, "download_names", None)
    if callable(build):
        return SimpleNamespace(export=lambda bundle: bundle.to_export_file(build, names))
    raise LeaseEngineUnavailable("导出器未提供 export 或 export_calculation_workbook，已拒绝导出")


# ---------- 通用工具 ----------

def _q2(value) -> Decimal:
    return Decimal(str(value or 0)).quantize(_CENT, rounding=ROUND_HALF_UP)


def _q6(value) -> Decimal:
    return Decimal(str(value or 0)).quantize(_RATE, rounding=ROUND_HALF_UP)


def _label(mapping: dict[str, str], value: str) -> str:
    return mapping.get(value, value)


def _get_contract(db: Session, contract_id: int) -> LeaseContract:
    contract = db.get(LeaseContract, contract_id)
    if contract is None:
        raise LeaseNotFoundError(f"租赁合同 {contract_id} 不存在")
    return contract


def _get_calculation(db: Session, calculation_id: int) -> LeaseCalculation:
    calculation = db.get(LeaseCalculation, calculation_id)
    if calculation is None:
        raise LeaseNotFoundError(f"测算批次 {calculation_id} 不存在")
    return calculation


def _ordered_payments(db: Session, contract_id: int) -> list[LeasePayment]:
    return (db.query(LeasePayment).filter(LeasePayment.contract_id == contract_id)
            .order_by(LeasePayment.sequence.asc()).all())


def _assert_payment_rows(rows: list[object]) -> None:
    try:
        validate_payment_sequences(rows)
    except ValueError as exc:
        raise LeaseValidationError(str(exc)) from exc
    for row in rows:
        if getattr(row, "period_end") < getattr(row, "period_start"):
            raise LeaseValidationError(f"第 {getattr(row, 'sequence')} 期计租结束日早于开始日")


@contextmanager
def _unique_guard(db: Session, label: str) -> Iterator[None]:
    try:
        yield
    except IntegrityError as exc:
        db.rollback()
        raise LeaseConflictError(f"{label}：唯一约束冲突，请检查是否重复提交") from exc

# ---------- 输入哈希与快照 ----------
def _hash_payload(contract: LeaseContract, payments: list[LeasePayment]) -> dict:
    """参与 input_hash 的只有经济要素：备注、源行号、合同展示字段不参与。"""
    return {
        "annual_rate": str(_q6(contract.annual_rate)),
        "contract_start": contract.start_date.isoformat(),
        "contract_end": contract.end_date.isoformat(),
        "payments": [
            {
                "sequence": row.sequence, "period_start": row.period_start.isoformat(),
                "period_end": row.period_end.isoformat(), "months": row.months,
                "amount": str(_q2(row.amount)), "start_flag": bool(row.start_flag),
                "prepay_flag": bool(row.prepay_flag), "is_free_rent": bool(row.is_free_rent),
            }
            for row in sorted(payments, key=lambda item: item.sequence)
        ],
    }


def _input_hash(contract: LeaseContract, payments: list[LeasePayment]) -> str:
    canonical = json.dumps(_hash_payload(contract, payments), ensure_ascii=False,
                           sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def current_input_hash(db: Session, contract_id: int) -> str:
    """当前合同 + 付款计划的输入哈希（供过期判断与路由层复用）。"""
    contract = _get_contract(db, contract_id)
    return _input_hash(contract, _ordered_payments(db, contract_id))


def _snapshot_payload(contract: LeaseContract, payments: list[LeasePayment]) -> dict:
    return {
        "contract_no": contract.contract_no, "name": contract.name or "",
        "lessee": contract.lessee or "", "project": contract.project or "",
        "lessor": contract.lessor or "", "start_date": contract.start_date.isoformat(),
        "end_date": contract.end_date.isoformat(), "annual_rate": str(_q6(contract.annual_rate)),
        "rate_basis": contract.rate_basis or "", "remark": contract.remark or "",
        "attachment_note": contract.attachment_note or "",
        "payments": [
            {
                "sequence": row.sequence, "source_row": row.source_row,
                "period_start": row.period_start.isoformat(), "period_end": row.period_end.isoformat(),
                "months": row.months, "amount": str(_q2(row.amount)),
                "start_flag": bool(row.start_flag), "prepay_flag": bool(row.prepay_flag),
                "is_free_rent": bool(row.is_free_rent), "note": row.note or "",
            }
            for row in sorted(payments, key=lambda item: item.sequence)
        ],
    }


def _snapshot_payments(calculation: LeaseCalculation) -> list[PaymentSnapshot]:
    raw = (calculation.input_snapshot or {}).get("payments") or []
    try:
        return [PaymentSnapshot.model_validate(item) for item in raw]
    except ValidationError as exc:
        raise LeaseCalculationError("测算输入快照损坏：付款计划无法解析，请重新测算") from exc

# ---------- 输出组装 ----------
def _payment_out(row: LeasePayment) -> PaymentOut:
    return PaymentOut.model_validate(row)


def _assessment_out(row: LeaseAssessment) -> AssessmentRowOut:
    return AssessmentRowOut.model_validate(row)


def _period_out(row: LeasePeriod) -> PeriodRowOut:
    return PeriodRowOut.model_validate(row)


def _parsed_snapshot(calculation: LeaseCalculation) -> tuple[CalculationSummary, list[CalculationCheck]]:
    try:
        summary = CalculationSummary.model_validate(calculation.summary or {})
        checks = [CalculationCheck.model_validate(item) for item in (calculation.checks or [])]
    except (ValidationError, TypeError) as exc:
        raise LeaseCalculationError("测算快照损坏：汇总值或校验结果无法解析，请重新测算") from exc
    if not checks:
        raise LeaseCalculationError("测算快照缺少校验结果，不能作为有效结果使用")
    return summary, checks


def _check_counts(checks: list[CalculationCheck]) -> dict[str, int]:
    counts = {status: 0 for status in CHECK_STATUSES}
    for check in checks:
        counts[check.status] += 1
    return counts


def _checks_all_passed(checks: list[CalculationCheck]) -> bool:
    return bool(checks) and all(check.status == "ok" for check in checks)


def _calculation_out(db: Session, calculation: LeaseCalculation,
                     *, current_hash: str | None = None) -> CalculationOut:
    summary, checks = _parsed_snapshot(calculation)
    counts = _check_counts(checks)
    if current_hash is None:
        current_hash = current_input_hash(db, calculation.contract_id)
    return CalculationOut(
        id=calculation.id, contract_id=calculation.contract_id,
        status=calculation.status, status_label=_label(CALCULATION_STATUS_LABELS, calculation.status),
        input_hash=calculation.input_hash, rule_version=calculation.rule_version,
        summary=summary, checks=checks, check_counts=counts,
        all_checks_passed=_checks_all_passed(checks), has_unknown_checks=counts["unknown"] > 0,
        stale=calculation.input_hash != current_hash, confirmed_at=calculation.confirmed_at,
        created_at=calculation.created_at, updated_at=calculation.updated_at,
    )


def _latest_calculation_map(db: Session, contract_ids: list[int]) -> dict[int, LeaseCalculation]:
    if not contract_ids:
        return {}
    rows = (db.query(LeaseCalculation.contract_id, func.max(LeaseCalculation.id))
            .filter(LeaseCalculation.contract_id.in_(contract_ids))
            .group_by(LeaseCalculation.contract_id).all())
    latest_ids = [latest_id for _, latest_id in rows if latest_id is not None]
    if not latest_ids:
        return {}
    latest_rows = db.query(LeaseCalculation).filter(LeaseCalculation.id.in_(latest_ids)).all()
    return {row.contract_id: row for row in latest_rows}


def _counts_map(db: Session, model, ids: list[int], extra=None) -> dict[int, int]:
    query = db.query(model.contract_id, func.count(model.id)).filter(model.contract_id.in_(ids))
    if extra is not None:
        query = query.filter(extra)
    return {key: int(value) for key, value in query.group_by(model.contract_id).all()}


def _contract_outs(db: Session, contracts: list[LeaseContract]) -> list[ContractOut]:
    if not contracts:
        return []
    ids = [contract.id for contract in contracts]
    payment_counts = _counts_map(db, LeasePayment, ids)
    calculation_counts = _counts_map(db, LeaseCalculation, ids)
    confirmed_counts = _counts_map(db, LeaseCalculation, ids, LeaseCalculation.status == "confirmed")
    latest_map = _latest_calculation_map(db, ids)
    payments_by_contract: dict[int, list[LeasePayment]] = {}
    for row in (db.query(LeasePayment).filter(LeasePayment.contract_id.in_(ids))
                .order_by(LeasePayment.contract_id.asc(), LeasePayment.sequence.asc()).all()):
        payments_by_contract.setdefault(row.contract_id, []).append(row)
    hashes = {contract.id: _input_hash(contract, payments_by_contract.get(contract.id, []))
              for contract in contracts}
    outs: list[ContractOut] = []
    for contract in contracts:
        latest = latest_map.get(contract.id)
        outs.append(ContractOut(
            id=contract.id, contract_no=contract.contract_no, name=contract.name or "",
            lessee=contract.lessee or "", project=contract.project or "", lessor=contract.lessor or "",
            start_date=contract.start_date, end_date=contract.end_date,
            annual_rate=contract.annual_rate, rate_basis=contract.rate_basis or "",
            remark=contract.remark or "", attachment_note=contract.attachment_note or "",
            status=contract.status, status_label=_label(CONTRACT_STATUS_LABELS, contract.status),
            payment_count=payment_counts.get(contract.id, 0),
            calculation_count=calculation_counts.get(contract.id, 0),
            confirmed_calculation_count=confirmed_counts.get(contract.id, 0),
            latest_calculation_id=latest.id if latest else None,
            latest_calculation_status=latest.status if latest else None,
            latest_calculation_stale=bool(latest and latest.input_hash != hashes[contract.id]),
            created_at=contract.created_at, updated_at=contract.updated_at,
        ))
    return outs

# ---------- 合同 ----------

def create_contract(db: Session, payload: LeaseContractCreate) -> ContractOut:
    contract = LeaseContract(
        contract_no=payload.contract_no, name=payload.name, lessee=payload.lessee,
        project=payload.project, lessor=payload.lessor, start_date=payload.start_date,
        end_date=payload.end_date, annual_rate=payload.annual_rate, rate_basis=payload.rate_basis,
        remark=payload.remark, attachment_note=payload.attachment_note, status="draft",
    )
    with _unique_guard(db, "合同创建"):
        db.add(contract)
        db.commit()
    db.refresh(contract)
    return _contract_outs(db, [contract])[0]


def list_contracts(db: Session, *, status: str | None = None, keyword: str | None = None,
                   page: int = 1, page_size: int = 50) -> ContractListOut:
    if status is not None and status not in CONTRACT_STATUSES:
        raise LeaseValidationError(f"未知合同状态：{status}")
    if page < 1:
        raise LeaseValidationError("页码必须大于等于 1")
    if not 1 <= page_size <= 200:
        raise LeaseValidationError("每页数量必须在 1–200 之间")
    query = db.query(LeaseContract)
    if status is not None:
        query = query.filter(LeaseContract.status == status)
    if keyword and keyword.strip():
        text = keyword.strip()
        query = query.filter(LeaseContract.contract_no.contains(text, autoescape=True)
                              | LeaseContract.name.contains(text, autoescape=True)
                              | LeaseContract.lessee.contains(text, autoescape=True))
    total = query.count()
    rows = (query.order_by(LeaseContract.id.desc())
            .offset((page - 1) * page_size).limit(page_size).all())
    return ContractListOut(total=total, items=_contract_outs(db, rows))


def get_contract(db: Session, contract_id: int) -> ContractOut:
    return _contract_outs(db, [_get_contract(db, contract_id)])[0]


def update_contract(db: Session, contract_id: int, payload: LeaseContractPatch) -> ContractOut:
    contract = _get_contract(db, contract_id)
    if contract.status == "archived":
        raise LeaseConflictError("合同已归档，只读；如需修改请新建合同或先由业务确认解除归档口径")
    data = payload.model_dump(exclude_unset=True)
    if not data:
        raise LeaseValidationError("未提供任何要更新的字段")
    target = data.pop("status", None) if "status" in data else None
    if "status" in payload.model_fields_set and target is None:
        raise LeaseValidationError("字段 status 不能为 null")
    if target is not None and target not in CONTRACT_TRANSITIONS.get(contract.status, frozenset()):
        raise LeaseConflictError(
            f"合同状态不允许从「{_label(CONTRACT_STATUS_LABELS, contract.status)}」"
            f"变更为「{_label(CONTRACT_STATUS_LABELS, str(target))}」")
    for field, value in data.items():
        if value is None:
            raise LeaseValidationError(f"字段 {field} 不能为 null")
    start = data.get("start_date", contract.start_date)
    end = data.get("end_date", contract.end_date)
    if end < start:
        raise LeaseValidationError("合同结束日早于起始日")
    if target is not None:
        contract.status = target
    for field, value in data.items():
        setattr(contract, field, value)
    with _unique_guard(db, "合同更新"):
        db.commit()
    db.refresh(contract)
    return _contract_outs(db, [contract])[0]


def delete_contract(db: Session, contract_id: int) -> dict:
    contract = _get_contract(db, contract_id)
    calculation_count = (db.query(func.count(LeaseCalculation.id))
                         .filter(LeaseCalculation.contract_id == contract_id).scalar() or 0)
    if calculation_count:
        raise LeaseConflictError(f"合同已有 {calculation_count} 条测算历史，不可删除（审计留痕）；请改为归档")
    db.query(LeasePayment).filter(LeasePayment.contract_id == contract_id).delete(
        synchronize_session=False)
    db.delete(contract)
    db.commit()
    return {"deleted": True, "contract_id": contract_id}


# ---------- 付款计划 ----------

def list_payments(db: Session, contract_id: int) -> list[PaymentOut]:
    _get_contract(db, contract_id)
    return [_payment_out(row) for row in _ordered_payments(db, contract_id)]


def replace_payments(db: Session, contract_id: int, payload: LeasePaymentsReplace) -> list[PaymentOut]:
    contract = _get_contract(db, contract_id)
    if contract.status == "archived":
        raise LeaseConflictError("合同已归档，付款计划只读")
    _assert_payment_rows(payload.items)
    try:
        db.query(LeasePayment).filter(LeasePayment.contract_id == contract_id).delete(
            synchronize_session=False)
        for item in sorted(payload.items, key=lambda row: row.sequence):
            db.add(LeasePayment(
                contract_id=contract_id, sequence=item.sequence, source_row=item.source_row,
                period_start=item.period_start, period_end=item.period_end, months=item.months,
                amount=item.amount, start_flag=item.start_flag, prepay_flag=item.prepay_flag,
                is_free_rent=item.is_free_rent, note=item.note))
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise LeaseConflictError("付款计划保存失败：期数重复或唯一约束冲突") from exc
    except Exception:
        db.rollback()
        raise
    return list_payments(db, contract_id)


# ---------- 测算 ----------


def _engine_version(engine: CalculationEngine) -> str:
    version = getattr(engine, "rule_version", None)
    if not isinstance(version, str) or not version.strip():
        raise LeaseCalculationError("计算引擎未声明 rule_version，已拒绝测算")
    if not callable(getattr(engine, "compute", None)):
        raise LeaseCalculationError("计算引擎未提供 compute(payload)，已拒绝测算")
    return version.strip()


def _engine_input(contract: LeaseContract, payments: list[LeasePayment]) -> CalculationInput:
    return CalculationInput(
        contract_start=contract.start_date, contract_end=contract.end_date,
        annual_rate=contract.annual_rate, payments=[PaymentInput.model_validate(row) for row in payments])


def _validate_result(raw: object, payments: list[LeasePayment], rule_version: str) -> CalculationResult:
    try:
        result = CalculationResult.model_validate(raw)
    except ValidationError as exc:
        first = exc.errors()[0] if exc.errors() else {}
        location = ".".join(str(part) for part in first.get("loc", ()))
        raise LeaseCalculationError(
            f"计算引擎返回结构不合法：{location} {first.get('msg', '')}".strip()) from exc
    if result.rule_version != rule_version:
        raise LeaseCalculationError(
            f"计算引擎返回的规则版本 {result.rule_version} 与引擎声明 {rule_version} 不一致")
    payment_by_sequence = {row.sequence: row for row in payments}
    sequences = [row.sequence for row in result.assessment]
    if len(sequences) != len(set(sequences)):
        raise LeaseCalculationError("计算引擎评估行期数重复")
    if set(sequences) != set(payment_by_sequence):
        raise LeaseCalculationError("计算引擎评估行期数与付款计划不一致")
    for row in result.assessment:
        payment = payment_by_sequence[row.sequence]
        if (row.period_start != payment.period_start or row.period_end != payment.period_end
                or _q2(row.amount) != _q2(payment.amount)):
            raise LeaseCalculationError(f"计算引擎评估行与付款期第 {payment.sequence} 期不一致")
    months = [row.period_month for row in result.periods]
    if len(months) != len(set(months)):
        raise LeaseCalculationError("计算引擎期间行月份重复")
    for row in result.periods:
        if row.sequence not in payment_by_sequence:
            raise LeaseCalculationError(f"计算引擎期间行引用了不存在的付款期 {row.sequence}")
    if result.summary.payment_count != len(result.assessment):
        raise LeaseCalculationError("计算引擎汇总付款期数与评估行不一致")
    if result.summary.month_count != len(result.periods):
        raise LeaseCalculationError("计算引擎汇总月期数与期间行不一致")
    if len({check.key for check in result.checks}) != len(result.checks):
        raise LeaseCalculationError("计算引擎校验结果 key 重复")
    return result


def _assessment_entity(calculation_id: int, row, source_row: int | None) -> LeaseAssessment:
    return LeaseAssessment(calculation_id=calculation_id, source_row=source_row, **row.model_dump())


def _period_entity(calculation_id: int, row) -> LeasePeriod:
    return LeasePeriod(calculation_id=calculation_id, **row.model_dump())


def create_calculation(db: Session, contract_id: int,
                       *, engine: CalculationEngine | None = None) -> CalculationOut:
    """生成或幂等返回测算批次；失败计算不写任何结果行。"""
    contract = _get_contract(db, contract_id)
    if contract.status != "draft":
        raise LeaseConflictError("合同已归档，不能发起测算")
    payments = _ordered_payments(db, contract_id)
    if not payments:
        raise LeaseValidationError("付款计划为空，不能测算；请先维护至少一个付款期")
    _assert_payment_rows(payments)
    engine = engine or resolve_calculation_engine()
    rule_version = _engine_version(engine)
    digest = _input_hash(contract, payments)
    existing = (db.query(LeaseCalculation).filter_by(
        contract_id=contract_id, input_hash=digest, rule_version=rule_version).one_or_none())
    if existing is not None:
        return _calculation_out(db, existing, current_hash=digest)
    result = _validate_result(engine.compute(_engine_input(contract, payments)), payments, rule_version)
    calculation = LeaseCalculation(
        contract_id=contract_id, input_hash=digest, rule_version=rule_version, status="calculated",
        input_snapshot=_snapshot_payload(contract, payments),
        summary=result.summary.model_dump(mode="json"),
        checks=[check.model_dump(mode="json") for check in result.checks])
    source_rows = {row.sequence: row.source_row for row in payments}
    try:
        db.add(calculation)
        db.flush()
        db.add_all([_assessment_entity(calculation.id, row, source_rows.get(row.sequence))
                    for row in sorted(result.assessment, key=lambda item: item.sequence)])
        db.add_all([_period_entity(calculation.id, row)
                    for row in sorted(result.periods, key=lambda item: item.period_month)])
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        existing = (db.query(LeaseCalculation).filter_by(
            contract_id=contract_id, input_hash=digest, rule_version=rule_version).one_or_none())
        if existing is not None:
            return _calculation_out(db, existing, current_hash=digest)
        raise LeaseConflictError("测算保存失败：唯一约束冲突，请重试") from exc
    except Exception:
        db.rollback()
        raise
    db.refresh(calculation)
    return _calculation_out(db, calculation, current_hash=digest)


def list_calculations(db: Session, contract_id: int) -> list[CalculationOut]:
    contract = _get_contract(db, contract_id)
    digest = _input_hash(contract, _ordered_payments(db, contract_id))
    rows = (db.query(LeaseCalculation).filter(LeaseCalculation.contract_id == contract_id)
            .order_by(LeaseCalculation.id.desc()).all())
    return [_calculation_out(db, row, current_hash=digest) for row in rows]


def get_calculation(db: Session, calculation_id: int) -> CalculationOut:
    return _calculation_out(db, _get_calculation(db, calculation_id))


def get_assessment(db: Session, calculation_id: int) -> list[AssessmentRowOut]:
    _get_calculation(db, calculation_id)
    rows = (db.query(LeaseAssessment).filter(LeaseAssessment.calculation_id == calculation_id)
            .order_by(LeaseAssessment.sequence.asc()).all())
    return [_assessment_out(row) for row in rows]


def get_periods(db: Session, calculation_id: int) -> list[PeriodRowOut]:
    _get_calculation(db, calculation_id)
    rows = (db.query(LeasePeriod).filter(LeasePeriod.calculation_id == calculation_id)
            .order_by(LeasePeriod.period_month.asc()).all())
    return [_period_out(row) for row in rows]


def get_checks(db: Session, calculation_id: int) -> ChecksOut:
    calculation = _get_calculation(db, calculation_id)
    _, checks = _parsed_snapshot(calculation)
    counts = _check_counts(checks)
    current_hash = current_input_hash(db, calculation.contract_id)
    return ChecksOut(
        calculation_id=calculation.id, input_hash=calculation.input_hash,
        rule_version=calculation.rule_version, stale=calculation.input_hash != current_hash,
        checks=checks, check_counts=counts, all_passed=_checks_all_passed(checks),
        has_unknown=counts["unknown"] > 0)


def confirm_calculation(db: Session, calculation_id: int,
                        *, engine: CalculationEngine | None = None) -> CalculationOut:
    """确认测算：必须当前输入、规则版本未变、全部检查通过且快照完整。"""
    calculation = _get_calculation(db, calculation_id)
    if "confirmed" not in CALCULATION_TRANSITIONS.get(calculation.status, frozenset()):
        raise LeaseConflictError("该测算已确认，已确认快照不可重复确认或改写")
    contract = _get_contract(db, calculation.contract_id)
    if contract.status != "draft":
        raise LeaseConflictError("合同已归档，不能确认测算")
    payments = _ordered_payments(db, contract.id)
    if not payments:
        raise LeaseConflictError("付款计划为空，无法确认测算")
    digest = _input_hash(contract, payments)
    if digest != calculation.input_hash:
        raise LeaseConflictError("测算结果已过期：合同或付款计划已变化，请重新测算后再确认")
    engine = engine or resolve_calculation_engine()
    rule_version = _engine_version(engine)
    if rule_version != calculation.rule_version:
        raise LeaseConflictError(
            f"规则版本已更新（测算 {calculation.rule_version} / 当前 {rule_version}），"
            "请重新测算后再确认")
    summary, checks = _parsed_snapshot(calculation)
    counts = _check_counts(checks)
    if counts["ok"] != len(checks):
        raise LeaseConflictError(
            f"校验未全部通过（差异 {counts['failed']} 项、未知 {counts['unknown']} 项），不能确认")
    assessment_count = (db.query(func.count(LeaseAssessment.id))
                        .filter(LeaseAssessment.calculation_id == calculation.id).scalar() or 0)
    period_count = (db.query(func.count(LeasePeriod.id))
                    .filter(LeasePeriod.calculation_id == calculation.id).scalar() or 0)
    if assessment_count != len(payments) or period_count != summary.month_count:
        raise LeaseConflictError("测算快照不完整，不能确认；请重新测算")
    calculation.status = "confirmed"
    calculation.confirmed_at = datetime.now()
    db.commit()
    db.refresh(calculation)
    return _calculation_out(db, calculation, current_hash=digest)


# ---------- 摘要 ----------


def lease_summary(db: Session, *, contract_id: int | None = None) -> LeaseSummaryOut:
    contracts = ([_get_contract(db, contract_id)] if contract_id is not None
                 else db.query(LeaseContract).order_by(LeaseContract.id.asc()).all())
    outs = _contract_outs(db, contracts)
    latest_map = _latest_calculation_map(db, [contract.id for contract in contracts])
    items: list[LeaseSummaryItem] = []
    with_pending = 0
    for out in outs:
        latest = latest_map.get(out.id)
        latest_summary: CalculationSummary | None = None
        counts = {status: 0 for status in CHECK_STATUSES}
        if latest is not None:
            latest_summary, checks = _parsed_snapshot(latest)
            counts = _check_counts(checks)
        pending = counts["failed"] + counts["unknown"]
        with_pending += 1 if pending else 0
        items.append(LeaseSummaryItem(
            contract_id=out.id, contract_no=out.contract_no, name=out.name,
            status=out.status, status_label=out.status_label, payment_count=out.payment_count,
            latest_calculation_id=latest.id if latest else None,
            latest_calculation_status=latest.status if latest else None,
            latest_rule_version=latest.rule_version if latest else None,
            latest_calculated_at=latest.created_at if latest else None,
            latest_confirmed_at=latest.confirmed_at if latest else None,
            stale=out.latest_calculation_stale, check_counts=counts,
            pending_checks=pending, latest_summary=latest_summary))
    return LeaseSummaryOut(
        total_contracts=len(items),
        draft_count=sum(1 for item in items if item.status == "draft"),
        archived_count=sum(1 for item in items if item.status == "archived"),
        contracts_with_pending=with_pending, contracts=items)


# ---------- 导入 / 导出接缝 ----------


def preview_payment_import(db: Session, contract_id: int, content: bytes, filename: str,
                           *, importer: PaymentImporter | None = None) -> PaymentImportPreview:
    """预览付款计划：只解析与校验，不写数据库。"""
    contract = _get_contract(db, contract_id)
    if contract.status == "archived":
        raise LeaseConflictError("合同已归档，付款计划只读")
    importer = importer or resolve_payment_importer()
    try:
        raw = importer.preview(content, filename)
        preview = raw if isinstance(raw, PaymentImportPreview) else PaymentImportPreview.model_validate(raw)
    except ValidationError as exc:
        raise LeaseValidationError("导入器返回结构不合法，已拒绝预览") from exc
    errors = list(preview.errors)
    if not errors:
        try:
            validate_payment_sequences(preview.items)
        except ValueError as exc:
            errors.append(str(exc))
    return preview.model_copy(update={"errors": errors}) if errors != preview.errors else preview


def _payment_in_from_import(row: PaymentImportRow) -> LeasePaymentIn:
    return LeasePaymentIn(
        sequence=row.sequence, source_row=row.source_row, period_start=row.period_start,
        period_end=row.period_end, months=row.months, amount=row.amount,
        start_flag=row.start_flag, prepay_flag=row.prepay_flag,
        is_free_rent=row.is_free_rent, note=row.note)


def apply_payment_import(db: Session, contract_id: int, content: bytes, filename: str,
                         *, importer: PaymentImporter | None = None) -> PaymentImportApplyResult:
    """确认导入：预览无错误才整表替换；解析或校验失败时数据库保持原状。"""
    preview = preview_payment_import(db, contract_id, content, filename, importer=importer)
    if preview.errors:
        raise LeaseValidationError(f"导入校验未通过：{'；'.join(preview.errors[:3])}")
    payments = replace_payments(
        db, contract_id, LeasePaymentsReplace(items=[_payment_in_from_import(row) for row in preview.items]))
    return PaymentImportApplyResult(preview=preview, items=payments)


def _export_bundle(db: Session, calculation: LeaseCalculation, contract: LeaseContract,
                   stale: bool) -> LeaseExportBundle:
    snapshot = calculation.input_snapshot or {}
    try:
        start_date = date.fromisoformat(str(snapshot["start_date"]))
        end_date = date.fromisoformat(str(snapshot["end_date"]))
        annual_rate = Decimal(str(snapshot["annual_rate"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise LeaseCalculationError("测算输入快照损坏：合同要素无法解析，请重新测算") from exc
    bundle = LeaseExportBundle(
        contract_no=str(snapshot.get("contract_no") or contract.contract_no),
        name=str(snapshot.get("name") or contract.name or ""),
        lessee=str(snapshot.get("lessee") or contract.lessee or ""),
        project=str(snapshot.get("project") or contract.project or ""),
        lessor=str(snapshot.get("lessor") or contract.lessor or ""),
        start_date=start_date, end_date=end_date, annual_rate=annual_rate,
        rate_basis=str(snapshot.get("rate_basis") or "未留存"),
        remark=str(snapshot.get("remark") or "未留存"),
        attachment_note=str(snapshot.get("attachment_note") or "未留存"),
        payments=_snapshot_payments(calculation),
        calculation=_calculation_out(db, calculation,
                                     current_hash=current_input_hash(db, contract.id)),
        assessment=[_assessment_out(row) for row in
                    db.query(LeaseAssessment).filter(LeaseAssessment.calculation_id == calculation.id)
                    .order_by(LeaseAssessment.sequence.asc()).all()],
        periods=[_period_out(row) for row in
                 db.query(LeasePeriod).filter(LeasePeriod.calculation_id == calculation.id)
                 .order_by(LeasePeriod.period_month.asc()).all()],
        stale=stale, exported_at=datetime.now())
    bundle.entries = entries_module.build_entries(bundle)
    bundle.entries_supported = list(entries_module.ENTRIES_SUPPORTED)
    bundle.entries_note = entries_module.ENTRIES_NOTE
    return bundle


def export_calculation_xlsx(db: Session, calculation_id: int, *, allow_stale: bool = False,
                            exporter: WorkbookExporter | None = None) -> LeaseExportFile:
    """导出底稿：当前结果存在差异或过期时 409；历史快照需显式 allow_stale。"""
    calculation = _get_calculation(db, calculation_id)
    contract = _get_contract(db, calculation.contract_id)
    stale = calculation.input_hash != _input_hash(contract, _ordered_payments(db, contract.id))
    _, checks = _parsed_snapshot(calculation)
    counts = _check_counts(checks)
    if not allow_stale:
        if stale:
            raise LeaseConflictError(
                "测算结果已过期：当前合同或付款计划已变化，请重新测算后再导出；历史快照需显式允许后导出")
        if counts["ok"] != len(checks):
            raise LeaseConflictError(
                f"当前测算校验未全部通过（差异 {counts['failed']} 项、未知 {counts['unknown']} 项），"
                "不能导出为当前底稿")
    exporter = exporter or resolve_workbook_exporter()
    raw = exporter.export(_export_bundle(db, calculation, contract, stale))
    if isinstance(raw, LeaseExportFile):
        return raw
    try:
        return LeaseExportFile.model_validate(raw)
    except ValidationError as exc:
        raise LeaseValidationError("导出器未返回有效文件（需要 filename 与 content）") from exc
