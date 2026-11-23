"""finance_lease × BIP 只读对账：科目映射 + 承租人/账簿/期间级五科目勾稽。

边界（与获批计划一致）：

- **只读 BIP**：全部余额读取经 ``finance_ledger_bip.bip_balance`` 的公共只读适配
  函数（``read_balance_rows`` / ``list_balance_scopes``；manifest 的 ``requires`` 已声明
  该依赖），不 import 其它模块的 models、不写凭证/余额/批次，也不触发任何导入；
- **只写本模块映射表** ``mod_lease_bip_map``：PUT 只替换给定承租人+账簿范围的映射，
  绝不写 BIP；
- **合同侧金额只来自快照**：每合同只采用「当前 input_hash 一致 + confirmed + 覆盖该
  期间」的最新一个测算批次（按 id 取最新），期间行给出累计折旧/剩余付款/月末未确认
  与月末净负债；stale、未确认、无覆盖、无测算一律 unknown 并附中文原因，绝不重复
  汇总历史批次，也不把缺失当 0；
- **汇总口径**：某承租人名下只要有一个合同不可用，各科目位的模块侧期望数就不完整，
  整批科目一律 unknown（不做部分求和与整额 BIP 余额比较，避免假差异）；
- **BIP 实际数**：借方余额 → ``closing_debit - closing_credit``；贷方余额 → 反向。
  只有方向金额全为 0 且方向无关期末余额非 0 时才按映射方向取绝对值，并在结果里
  标注来源；方向金额与期末余额互相矛盾（冲突）、缺行、多行歧义、缺映射均 unknown；
- **容差** 固定 0.01（``reconciliation_schemas.TOLERANCE``），差额按 Decimal
  ROUND_HALF_UP 两位量化，禁止 float 与内建 ``round()``；
- 对账结果**实时计算、不落库**，因此没有持久状态机；聚合结论按
  failed > unknown > ok 的固定优先级得出（``_aggregate_status``）。
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP

from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import service
from .models import LeaseBipMap, LeaseCalculation, LeaseContract, LeasePeriod
from .reconciliation_schemas import (
    DIRECTION_LABELS,
    EXPECTED_SOURCE_SNAPSHOT,
    MAPPING_DIRECTIONS,
    SOURCE_LABELS,
    SUBJECT_KEYS,
    SUBJECT_KEY_LABELS,
    TOLERANCE,
    CheckCounts,
    CheckRequest,
    ContextOut,
    ContractCoverage,
    CoverageSummary,
    DirectionOption,
    MappingListOut,
    MappingOut,
    MappingPutRequest,
    ReconcileOut,
    SubjectKeyOption,
    SubjectResult,
    status_label,
)
from .schemas import CalculationSummary

_CENT = Decimal("0.01")
_REASON_CAP = 20


# ---------- 通用 ----------


def _q2(value) -> Decimal:
    return Decimal(str(value or 0)).quantize(_CENT, rounding=ROUND_HALF_UP)


def _text(value: str | None) -> str | None:
    text = (value or "").strip()
    return text or None


def _bip():
    """延迟导入 BIP 公共只读适配：避免模块加载顺序耦合（manifest 已声明 requires）。"""
    from ..finance_ledger_bip import bip_balance

    return bip_balance


def _subject_key_options() -> list[SubjectKeyOption]:
    return [SubjectKeyOption(key=key, label=SUBJECT_KEY_LABELS[key]) for key in SUBJECT_KEYS]


def _direction_options() -> list[DirectionOption]:
    return [DirectionOption(value=value, label=DIRECTION_LABELS[value])
            for value in MAPPING_DIRECTIONS]


def _mapping_out(row: LeaseBipMap) -> MappingOut:
    return MappingOut(
        id=row.id, lessee=row.lessee, ledger=row.ledger, subject_key=row.subject_key,
        subject_key_label=SUBJECT_KEY_LABELS.get(row.subject_key, row.subject_key),
        subject_code=row.subject_code, direction=row.direction,
        direction_label=DIRECTION_LABELS.get(row.direction, row.direction),
        updated_at=row.updated_at,
    )


def _mapping_query(db: Session, lessee: str | None, ledger: str | None):
    query = db.query(LeaseBipMap)
    if lessee:
        query = query.filter(LeaseBipMap.lessee == lessee)
    if ledger:
        query = query.filter(LeaseBipMap.ledger == ledger)
    return query.order_by(
        LeaseBipMap.lessee.asc(), LeaseBipMap.ledger.asc(), LeaseBipMap.subject_key.asc()
    )


# ---------- 映射（只写本模块表） ----------


def list_mappings(db: Session, *, lessee: str | None = None,
                  ledger: str | None = None) -> MappingListOut:
    rows = _mapping_query(db, _text(lessee), _text(ledger)).all()
    return MappingListOut(
        total=len(rows), items=[_mapping_out(row) for row in rows],
        subject_keys=_subject_key_options(), directions=_direction_options(),
    )


def replace_mappings(db: Session, payload: MappingPutRequest) -> MappingListOut:
    """整批替换某承租人 × 账簿范围的映射：未出现的科目位视为清除（显式空列表=全清）。"""
    lessee, ledger = payload.lessee, payload.ledger
    existing = (db.query(LeaseBipMap)
                .filter(LeaseBipMap.lessee == lessee, LeaseBipMap.ledger == ledger).all())
    by_key = {row.subject_key: row for row in existing}
    wanted = {item.subject_key: item for item in payload.items}
    try:
        for row in existing:
            if row.subject_key not in wanted:
                db.delete(row)
        for key, item in wanted.items():
            row = by_key.get(key)
            if row is None:
                db.add(LeaseBipMap(lessee=lessee, ledger=ledger, subject_key=key,
                                   subject_code=item.subject_code, direction=item.direction))
            else:
                row.subject_code = item.subject_code
                row.direction = item.direction
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise service.LeaseConflictError("科目映射保存失败：唯一约束冲突，请重试") from exc
    except Exception:
        db.rollback()
        raise
    return list_mappings(db, lessee=lessee, ledger=ledger)


# ---------- 上下文 ----------


def get_context(db: Session) -> ContextOut:
    """下拉上下文：承租人取自租赁合同，账簿/期间取自 BIP 余额表（真实存在值）。"""
    lessees = sorted({
        text for (value,) in db.query(LeaseContract.lessee).distinct().all()
        if (text := (value or "").strip())
    })
    scopes = _bip().list_balance_scopes(db)
    mappings = list_mappings(db)
    return ContextOut(
        lessees=lessees, ledgers=scopes["ledgers"], periods=scopes["periods"],
        subject_keys=_subject_key_options(), directions=_direction_options(),
        mappings=mappings.items,
    )


# ---------- 合同侧：选择唯一可用测算批次 ----------


def _summary(calculation: LeaseCalculation) -> CalculationSummary | None:
    """快照汇总解析：损坏时返回 None（调用方按 unknown 处理，绝不猜数）。"""
    try:
        return CalculationSummary.model_validate(calculation.summary or {})
    except ValidationError:
        return None


def _period_rows(db: Session, calculation_id: int) -> list[LeasePeriod]:
    return (db.query(LeasePeriod).filter(LeasePeriod.calculation_id == calculation_id)
            .order_by(LeasePeriod.period_month.asc()).all())


def _coverage_base(contract: LeaseContract, calculation: LeaseCalculation | None) -> dict:
    return {
        "contract_id": contract.id,
        "contract_no": contract.contract_no,
        "name": contract.name or "",
        "contract_status": contract.status,
        "calculation_id": calculation.id if calculation else None,
        "calculation_status": calculation.status if calculation else None,
        "calculation_input_hash": calculation.input_hash if calculation else None,
        "calculation_rule_version": calculation.rule_version if calculation else None,
    }


def _unknown_coverage(base: dict, reason: str, *, stale: bool = False,
                      covers: bool = False) -> ContractCoverage:
    return ContractCoverage(
        **base, selected=False, stale=stale, covers_period=covers, reason=reason,
        present_value=None, accumulated_depreciation=None, remaining_payment=None,
        unrecognized_finance=None, liability_balance=None,
    )


def _contract_coverage(db: Session, contract: LeaseContract,
                       period: str) -> tuple[ContractCoverage, list[LeasePeriod] | None]:
    """每合同只选一个批次：confirmed + 当前 input_hash + 覆盖期间，取最新 id。"""
    calculations = (db.query(LeaseCalculation)
                    .filter(LeaseCalculation.contract_id == contract.id)
                    .order_by(LeaseCalculation.id.desc()).all())
    if not calculations:
        return _unknown_coverage(_coverage_base(contract, None), "该合同没有测算批次"), None

    current_hash = service.current_input_hash(db, contract.id)
    confirmed = [row for row in calculations if row.status == "confirmed"]
    if not confirmed:
        latest = calculations[0]
        return _unknown_coverage(
            _coverage_base(contract, latest),
            "测算尚未确认（最新批次仍为已测算），不能用于对账",
        ), None

    current = [row for row in confirmed if row.input_hash == current_hash]
    if not current:
        latest = confirmed[0]
        return _unknown_coverage(
            _coverage_base(contract, latest),
            "已确认测算已过期：合同或付款计划在测算后变更，请重新测算并确认",
            stale=True,
        ), None

    for calculation in current:  # id 降序：第一个覆盖期间的就是最新批次
        periods = _period_rows(db, calculation.id)
        if periods and periods[0].period_month <= period <= periods[-1].period_month:
            summary = _summary(calculation)
            if summary is None:
                return _unknown_coverage(
                    _coverage_base(contract, calculation),
                    "测算汇总快照损坏，无法取得使用权资产原值，请重新测算", covers=True,
                ), None
            upto = [row for row in periods if row.period_month <= period]
            after = [row for row in periods if row.period_month > period]
            last = upto[-1]
            coverage = ContractCoverage(
                **_coverage_base(contract, calculation), selected=True, stale=False,
                covers_period=True, reason="",
                present_value=_q2(summary.present_value),
                accumulated_depreciation=_q2(sum(row.depreciation for row in upto)),
                remaining_payment=_q2(sum(row.payable for row in after)),
                unrecognized_finance=_q2(last.unamortized_finance),
                liability_balance=_q2(last.liability_balance),
            )
            return coverage, periods

    latest = current[0]
    periods = _period_rows(db, latest.id)
    if not periods:
        reason = "已确认测算缺少期间行，无法确定覆盖范围"
    else:
        reason = (f"已确认测算未覆盖期间 {period}"
                  f"（期间行范围 {periods[0].period_month}~{periods[-1].period_month}）")
    return _unknown_coverage(_coverage_base(contract, latest), reason), None


# ---------- BIP 侧：实际数 ----------


def _actual_from_row(row: dict, direction: str) -> tuple[Decimal | None, str, str]:
    """返回 (实际数|None, 来源键, 问题说明)。问题非空即 unknown，不猜数。"""
    debit, credit, balance = row["closing_debit"], row["closing_credit"], row["closing_balance"]
    if debit != 0 or credit != 0:
        actual = (debit - credit) if direction == "debit" else (credit - debit)
        if balance != 0 and abs(abs(balance) - abs(actual)) > TOLERANCE:
            gap = _q2(abs(abs(balance) - abs(actual)))
            return None, "bip_conflict", (
                f"BIP 方向金额与方向无关期末余额相差 {gap}，来源冲突，无法确定实际数"
            )
        return _q2(actual), "bip_closing_direction", ""
    if balance != 0:
        return _q2(abs(balance)), "bip_closing_balance", ""
    return None, "bip_zero_balance", "BIP 余额行方向金额与方向无关期末余额均为空，无法确定实际数"


def _evaluate_subject(db: Session, *, key: str, mapping: LeaseBipMap | None,
                      expected: Decimal | None, unavailable: str,
                      lessee: str, ledger: str, period: str) -> SubjectResult:
    label = SUBJECT_KEY_LABELS[key]
    base = {
        "subject_key": key,
        "subject_key_label": label,
        "subject_code": mapping.subject_code if mapping else None,
        "direction": mapping.direction if mapping else None,
        "direction_label": (DIRECTION_LABELS.get(mapping.direction, mapping.direction)
                            if mapping else None),
        "expected": expected,
        "expected_source": EXPECTED_SOURCE_SNAPSHOT if expected is not None else "",
    }

    def unknown(source: str, reason: str) -> SubjectResult:
        return SubjectResult(
            **base, actual=None, difference=None, status="unknown",
            status_label=status_label("unknown"), source=source,
            source_label=SOURCE_LABELS.get(source, source), reason=reason,
        )

    if expected is None:
        return unknown("no_calculation", unavailable)
    if mapping is None:
        return unknown("unmapped", f"未配置「{label}」的 BIP 科目映射（承租人「{lessee}」/ 账簿「{ledger}」）")

    rows = _bip().read_balance_rows(
        db, ledger=ledger, period=period, subject_code=mapping.subject_code
    )
    if not rows:
        return unknown("bip_missing",
                       f"BIP 账簿「{ledger}」期间 {period} 没有科目 {mapping.subject_code} 的余额行")
    if len(rows) > 1:
        return unknown("bip_ambiguous",
                       f"BIP 同一账簿/期间/科目存在 {len(rows)} 行余额（多行歧义），请先清理重复行")

    actual, source, problem = _actual_from_row(rows[0], mapping.direction)
    if problem:
        return unknown(source, problem)
    difference = (expected - actual).quantize(_CENT, rounding=ROUND_HALF_UP)
    if abs(difference) <= TOLERANCE:
        return SubjectResult(
            **base, actual=actual, difference=difference, status="ok",
            status_label=status_label("ok"), source=source,
            source_label=SOURCE_LABELS.get(source, source), reason="",
        )
    return SubjectResult(
        **base, actual=actual, difference=difference, status="failed",
        status_label=status_label("failed"), source=source,
        source_label=SOURCE_LABELS.get(source, source),
        reason=f"差异 {difference} 超过容差 {TOLERANCE}",
    )


def _aggregate_status(subjects: list[SubjectResult]) -> str:
    """聚合结论固定优先级：failed > unknown > ok（unknown 不被通过掩盖）。"""
    statuses = {row.status for row in subjects}
    if "failed" in statuses:
        return "failed"
    if "unknown" in statuses or not statuses:
        return "unknown"
    return "ok"


def _distinct_reasons(coverages: list[ContractCoverage]) -> list[str]:
    reasons: list[str] = []
    for coverage in coverages:
        reason = coverage.reason
        if reason and reason not in reasons:
            reasons.append(reason)
    if len(reasons) > _REASON_CAP:
        reasons = reasons[:_REASON_CAP] + [f"……其余 {len(reasons) - _REASON_CAP} 条原因从略"]
    return reasons


# ---------- 对账入口 ----------


def run_reconciliation(db: Session, payload: CheckRequest) -> ReconcileOut:
    """承租人 + 账簿 + 期间级五科目勾稽；结果实时计算，不落库。"""
    lessee, ledger, period = payload.lessee, payload.ledger, payload.period
    contracts = (db.query(LeaseContract).filter(LeaseContract.lessee == lessee)
                 .order_by(LeaseContract.id.asc()).all())

    coverages: list[ContractCoverage] = []
    for contract in contracts:
        coverage, _ = _contract_coverage(db, contract, period)
        coverages.append(coverage)
    selected = [coverage for coverage in coverages if coverage.selected]
    reasons = _distinct_reasons(coverages)

    if not contracts:
        unavailable = f"承租人「{lessee}」下没有租赁合同，无法取得模块侧金额"
        expected_map: dict[str, Decimal] | None = None
    elif len(selected) != len(coverages):
        unavailable = "模块侧期望数不完整，本期间不能对账：" + "；".join(reasons)
        expected_map = None
    else:
        unavailable = ""
        expected_map = {
            "rou_asset": _q2(sum(row.present_value for row in selected)),
            "rou_accum_dep": _q2(sum(row.accumulated_depreciation for row in selected)),
            "lease_payment": _q2(sum(row.remaining_payment for row in selected)),
            "unrecognized_finance": _q2(sum(row.unrecognized_finance for row in selected)),
            "lease_liability": _q2(sum(row.liability_balance for row in selected)),
        }

    mappings = {row.subject_key: row for row in _mapping_query(db, lessee, ledger).all()}
    subjects = [
        _evaluate_subject(
            db, key=key, mapping=mappings.get(key),
            expected=None if expected_map is None else expected_map[key],
            unavailable=unavailable, lessee=lessee, ledger=ledger, period=period,
        )
        for key in SUBJECT_KEYS
    ]

    counts = CheckCounts(
        ok=sum(1 for row in subjects if row.status == "ok"),
        failed=sum(1 for row in subjects if row.status == "failed"),
        unknown=sum(1 for row in subjects if row.status == "unknown"),
    )
    overall = _aggregate_status(subjects)
    return ReconcileOut(
        lessee=lessee, ledger=ledger, period=period, tolerance=TOLERANCE,
        status=overall, status_label=status_label(overall), counts=counts,
        subjects=subjects, contracts=coverages,
        coverage=CoverageSummary(
            contract_count=len(coverages), selected_count=len(selected),
            unknown_count=len(coverages) - len(selected), reasons=reasons,
        ),
        generated_at=datetime.now(),
    )
