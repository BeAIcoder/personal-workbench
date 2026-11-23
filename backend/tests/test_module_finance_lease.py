# -*- coding: utf-8 -*-
"""finance_lease 模型 / schema / 服务 / 真实 API 集成测试。

覆盖范围：

- 五张 ``mod_lease_*`` 表与唯一约束真实生效；
- 合同 draft/archived、归档只读、删除守卫、付款计划严格输入与 source_row；
- input_hash 稳定性与失败测算不留半套结果；
- 真实计算引擎 + 真实 IO 的 API 链路：建合同 → 付款（含 xlsx 预览/确认导入）
  → 测算 → checks → 确认 → 导出（五页签、totals、分录借贷平衡）；
- 409/未知/过期、中文错误映射、``lease_summary`` 工具真实调用。

Stub 只用于异常注入（引擎失败、非法结构、不可用）和规则版本变化；正常测算一律
调用真实 ``calculation.compute``，导入/导出调用真实 ``importer/exporter``。
测试只用临时 SQLite 与合成名称/合同号，不写真实 data，也不依赖真实外部任务。
"""
from __future__ import annotations

import io
from datetime import date
from decimal import Decimal

import pytest
from openpyxl import Workbook, load_workbook
from pydantic import ValidationError
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from app.database import Base, SessionLocal, engine
from app.modules.finance_lease import importer, schemas, service
from app.modules.finance_lease.models import (
    LeaseAssessment,
    LeaseCalculation,
    LeaseContract,
    LeasePayment,
    LeasePeriod,
)

CONTRACT_BODY = {
    "contract_no": "SYN-LEASE-001",
    "name": "合成租赁合同",
    "lessee": "示例承租人",
    "project": "示例项目",
    "lessor": "示例出租方",
    "start_date": "2026-01-01",
    "end_date": "2026-12-31",
    "annual_rate": "0.035",
    "rate_basis": "合成测试利率",
}
TWO_PERIODS = [
    {"sequence": 1, "source_row": 7, "period_start": "2026-01-01", "period_end": "2026-06-30",
     "amount": "0.00", "is_free_rent": True},
    {"sequence": 2, "source_row": 8, "period_start": "2026-07-01", "period_end": "2026-12-31",
     "amount": "100.00", "prepay_flag": True},
]


@pytest.fixture(autouse=True)
def _ensure_tables():
    """非 client 直触库：保证本模块五表存在，并在每个用例前清空本模块数据。"""
    if not inspect(engine).has_table("mod_lease_contract"):
        Base.metadata.create_all(bind=engine)
    with SessionLocal() as session:
        for model in (LeasePeriod, LeaseAssessment, LeaseCalculation, LeasePayment, LeaseContract):
            session.query(model).delete()
        session.commit()
    yield


@pytest.fixture()
def db():
    with SessionLocal() as session:
        yield session


class StubEngine:
    """合成引擎：只产出结构一致的合成数值，不代表任何真实会计口径。"""

    def __init__(self, rule_version="rules-test-v1", check_status="ok",
                 bad_sequence=False, bad_rule_version=False, duplicate_month=False):
        self.rule_version = rule_version
        self.check_status = check_status
        self.bad_sequence = bad_sequence
        self.bad_rule_version = bad_rule_version
        self.duplicate_month = duplicate_month

    def compute(self, payload):
        assessment, periods, total = [], [], Decimal("0")
        for index, payment in enumerate(payload.payments, start=1):
            total += payment.amount
            assessment.append({
                "sequence": 99 if self.bad_sequence and index == 1 else payment.sequence,
                "period_start": payment.period_start.isoformat(),
                "period_end": payment.period_end.isoformat(),
                "months": payment.months if payment.months is not None else 6,
                "amount": str(payment.amount),
                "period_rate": "0.010000", "discount_factor": "1.000000000",
                "present_value": str(payment.amount), "unamortized_finance": "0.00",
                "interest_u": "0.00", "liability_balance": "0.00",
            })
            periods.append({
                "period_month": "2026-01" if self.duplicate_month and index == 2 else f"2026-{index:02d}",
                "sequence": payment.sequence, "depreciation": str(payment.amount),
                "payable": str(payment.amount), "interest": "0.00",
                "lease_payment": str(payment.amount), "unamortized_finance": "0.00",
                "liability_balance": "0.00",
            })
        return {
            "rule_version": "rules-test-other" if self.bad_rule_version else self.rule_version,
            "assessment": assessment, "periods": periods,
            "summary": {
                "total_payment": str(total), "present_value": str(total), "total_interest": "0.00",
                "total_depreciation": str(total), "payment_count": len(assessment),
                "month_count": len(periods), "first_period": periods[0]["period_month"],
                "last_period": periods[-1]["period_month"],
            },
            "checks": [{"key": "sums_balance", "title": "合计勾稽", "status": self.check_status}],
        }


class BoomEngine:
    rule_version = "rules-test-v1"

    def compute(self, payload):
        raise RuntimeError("合成引擎失败")


def make_contract(db, **overrides) -> schemas.ContractOut:
    body = {**CONTRACT_BODY, "contract_no": f"SYN-LEASE-{count(db, LeaseContract) + 1:03d}", **overrides}
    return service.create_contract(db, schemas.LeaseContractCreate(**body))


def set_payments(db, contract_id, items=None):
    return service.replace_payments(
        db, contract_id, schemas.LeasePaymentsReplace(items=TWO_PERIODS if items is None else items))


def seed_calculation(db, engine=None):
    contract = make_contract(db)
    set_payments(db, contract.id)
    calculation = service.create_calculation(db, contract.id, engine=engine)
    return contract, calculation


def count(db, model) -> int:
    return db.query(model).count()


# ---------- 表与唯一约束 ----------


def test_five_tables_and_unique_constraints_exist():
    names = set(inspect(engine).get_table_names())
    assert {"mod_lease_contract", "mod_lease_payment", "mod_lease_calculation",
            "mod_lease_assessment", "mod_lease_period"} <= names
    expected = {
        "mod_lease_contract": {"uq_lease_contract_no"},
        "mod_lease_payment": {"uq_lease_payment_contract_sequence"},
        "mod_lease_calculation": {"uq_lease_calculation_input"},
        "mod_lease_assessment": {"uq_lease_assessment_sequence"},
        "mod_lease_period": {"uq_lease_period_month"},
    }
    for table, required in expected.items():
        actual = {item["name"] for item in inspect(engine).get_unique_constraints(table)}
        assert required <= actual


def test_unique_constraints_reject_duplicates(db):
    contract = make_contract(db)
    db.add(LeaseContract(**{**CONTRACT_BODY, "start_date": date(2026, 1, 1),
                            "end_date": date(2026, 12, 31), "annual_rate": Decimal("0.035")}))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()

    set_payments(db, contract.id)
    db.add(LeasePayment(contract_id=contract.id, sequence=1, period_start=date(2026, 1, 1),
                        period_end=date(2026, 6, 30), amount=Decimal("1.00")))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()

    calculation = service.create_calculation(db, contract.id)
    raw = db.get(LeaseCalculation, calculation.id)
    db.add(LeaseCalculation(contract_id=contract.id, input_hash=raw.input_hash,
                            rule_version=raw.rule_version, status="calculated"))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()

    assessment = db.query(LeaseAssessment).first()
    db.add(LeaseAssessment(calculation_id=assessment.calculation_id, sequence=assessment.sequence,
                           period_start=assessment.period_start, period_end=assessment.period_end,
                           amount=Decimal("1.00")))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()

    period = db.query(LeasePeriod).first()
    db.add(LeasePeriod(calculation_id=period.calculation_id, period_month=period.period_month,
                       sequence=period.sequence))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


# ---------- 合同 ----------


def test_contract_create_strict_inputs_and_duplicate(db):
    with pytest.raises(ValidationError):
        schemas.LeaseContractCreate(**{**CONTRACT_BODY, "contract_no": "   "})
    with pytest.raises(ValidationError):
        schemas.LeaseContractCreate(**{**CONTRACT_BODY, "start_date": "2026/01/01"})
    with pytest.raises(ValidationError):
        schemas.LeaseContractCreate(**{**CONTRACT_BODY, "start_date": "2026-01-01T00:00:00"})
    with pytest.raises(ValidationError):
        schemas.LeaseContractCreate(**{**CONTRACT_BODY, "end_date": "2025-12-31"})
    with pytest.raises(ValidationError):
        schemas.LeaseContractCreate(**{**CONTRACT_BODY, "annual_rate": "1.5"})
    with pytest.raises(ValidationError):
        schemas.LeaseContractCreate(**{**CONTRACT_BODY, "unexpected": 1})

    contract = make_contract(db)
    assert contract.status == "draft" and contract.status_label == "草稿"
    with pytest.raises(service.LeaseConflictError, match="唯一约束冲突"):
        make_contract(db, contract_no=contract.contract_no)


def test_contract_list_filters_and_patch(db):
    first = make_contract(db)
    second = make_contract(db, contract_no="SYN-LEASE-002", name="合成租赁合同二",
                           lessee="示例承租人二")
    page = service.list_contracts(db, keyword="SYN-LEASE", page=1, page_size=10)
    assert page.total == 2 and {item.id for item in page.items} == {first.id, second.id}
    assert service.list_contracts(db, status="archived").total == 0
    with pytest.raises(service.LeaseValidationError):
        service.list_contracts(db, status="unknown")
    with pytest.raises(service.LeaseValidationError):
        service.list_contracts(db, page_size=500)

    patched = service.update_contract(db, first.id, schemas.LeaseContractPatch(
        name="合成租赁合同（改）", annual_rate="0.040", remark="合成备注"))
    assert patched.name.endswith("（改）") and patched.annual_rate == Decimal("0.040000")
    with pytest.raises(service.LeaseValidationError):
        service.update_contract(db, first.id, schemas.LeaseContractPatch())
    with pytest.raises(service.LeaseValidationError):
        service.update_contract(db, first.id, schemas.LeaseContractPatch(end_date="2025-01-01"))

    archived = service.update_contract(db, first.id, schemas.LeaseContractPatch(status="archived"))
    assert archived.status == "archived"
    with pytest.raises(service.LeaseConflictError):
        service.update_contract(db, first.id, schemas.LeaseContractPatch(name="不可改"))
    with pytest.raises(service.LeaseConflictError):
        service.update_contract(db, first.id, schemas.LeaseContractPatch(status="archived"))


def test_archived_contract_rejects_payments_and_calculation(db):
    contract = make_contract(db)
    set_payments(db, contract.id)
    service.update_contract(db, contract.id, schemas.LeaseContractPatch(status="archived"))
    with pytest.raises(service.LeaseConflictError):
        set_payments(db, contract.id)
    with pytest.raises(service.LeaseConflictError):
        service.create_calculation(db, contract.id)
    assert len(service.list_payments(db, contract.id)) == 2


def test_delete_contract_guards_history_and_cascades_payments(db):
    contract = make_contract(db)
    set_payments(db, contract.id)
    service.delete_contract(db, contract.id)
    assert db.get(LeaseContract, contract.id) is None
    assert count(db, LeasePayment) == 0

    contract, _ = seed_calculation(db)
    with pytest.raises(service.LeaseConflictError, match="测算历史"):
        service.delete_contract(db, contract.id)


# ---------- 付款计划 ----------


def test_payment_replace_strict_sequences_and_preserves_rows(db):
    contract = make_contract(db)
    with pytest.raises(ValidationError):
        schemas.LeasePaymentsReplace(items=[TWO_PERIODS[0], {**TWO_PERIODS[1], "sequence": 1}])
    with pytest.raises(ValidationError):
        schemas.LeasePaymentsReplace(items=[{**TWO_PERIODS[0], "sequence": 1},
                                            {**TWO_PERIODS[1], "sequence": 3}])
    with pytest.raises(ValidationError):
        schemas.LeasePaymentsReplace(items=[{**TWO_PERIODS[0], "period_end": "2025-12-31"}])
    with pytest.raises(ValidationError):
        schemas.LeasePaymentsReplace(items=[{**TWO_PERIODS[0], "months": -1}])

    rows = set_payments(db, contract.id, [
        {**TWO_PERIODS[0], "months": 0, "period_end": "2026-01-01"},
        TWO_PERIODS[1],
    ])
    assert [row.sequence for row in rows] == [1, 2]
    assert rows[0].months == 0 and rows[0].is_free_rent is True and rows[0].amount == 0
    assert rows[0].source_row == 7 and rows[1].prepay_flag is True
    assert set_payments(db, contract.id, []) == []
    assert count(db, LeasePayment) == 0


def test_input_hash_stable_and_sensitive(db):
    contract = make_contract(db)
    set_payments(db, contract.id)
    first = service.current_input_hash(db, contract.id)
    service.replace_payments(db, contract.id, schemas.LeasePaymentsReplace(items=list(reversed(TWO_PERIODS))))
    assert service.current_input_hash(db, contract.id) == first
    notes = [{**TWO_PERIODS[0], "note": "合成备注", "source_row": 99}, TWO_PERIODS[1]]
    set_payments(db, contract.id, notes)
    assert service.current_input_hash(db, contract.id) == first
    changed = [{**TWO_PERIODS[0], "amount": "1.00"}, TWO_PERIODS[1]]
    set_payments(db, contract.id, changed)
    assert service.current_input_hash(db, contract.id) != first
    service.update_contract(db, contract.id, schemas.LeaseContractPatch(annual_rate="0.045"))
    assert service.current_input_hash(db, contract.id) != first


# ---------- 测算与确认 ----------


def test_calculation_idempotent_and_snapshot_complete(db):
    contract, calculation = seed_calculation(db)
    assert calculation.status == "calculated" and calculation.stale is False
    assert calculation.summary.payment_count == 2
    assert calculation.check_counts["failed"] == 0
    assert calculation.check_counts["unknown"] == 0
    assert len(service.get_assessment(db, calculation.id)) == 2
    assert len(service.get_periods(db, calculation.id)) == 12
    raw = db.get(LeaseCalculation, calculation.id)
    assert raw.input_snapshot["payments"][0]["note"] == ""
    assert raw.input_snapshot["annual_rate"] == "0.035000"

    again = service.create_calculation(db, contract.id)
    assert again.id == calculation.id
    assert count(db, LeaseCalculation) == 1
    checks = service.get_checks(db, calculation.id)
    assert checks.all_passed is True and checks.has_unknown is False


def test_new_inputs_create_new_snapshot_and_mark_old_stale(db):
    contract, first = seed_calculation(db)
    set_payments(db, contract.id, [{**TWO_PERIODS[0], "amount": "5.00"}, TWO_PERIODS[1]])
    second = service.create_calculation(db, contract.id)
    assert second.id != first.id
    listed = {item.id: item for item in service.list_calculations(db, contract.id)}
    assert listed[first.id].stale is True
    assert listed[second.id].stale is False
    assert service.get_calculation(db, first.id).status == "calculated"


def test_failed_or_invalid_engine_leaves_no_rows(db):
    contract = make_contract(db)
    set_payments(db, contract.id)
    with pytest.raises(RuntimeError, match="合成引擎失败"):
        service.create_calculation(db, contract.id, engine=BoomEngine())
    with pytest.raises(service.LeaseCalculationError, match="期数"):
        service.create_calculation(db, contract.id, engine=StubEngine(bad_sequence=True))
    with pytest.raises(service.LeaseCalculationError, match="规则版本"):
        service.create_calculation(db, contract.id, engine=StubEngine(bad_rule_version=True))
    with pytest.raises(service.LeaseCalculationError, match="月份重复"):
        service.create_calculation(db, contract.id, engine=StubEngine(duplicate_month=True))
    assert count(db, LeaseCalculation) == 0
    assert count(db, LeaseAssessment) == 0
    assert count(db, LeasePeriod) == 0


def test_engine_unavailable_is_explicit_and_leaves_no_rows(db, monkeypatch):
    contract = make_contract(db)
    set_payments(db, contract.id)

    def missing(name, package=None):
        raise ModuleNotFoundError(f"No module named '{name}'", name=name)

    monkeypatch.setattr(service.importlib, "import_module", missing)
    with pytest.raises(service.LeaseEngineUnavailable, match="计算引擎尚未接入"):
        service.create_calculation(db, contract.id)
    assert count(db, LeaseCalculation) == 0


def test_confirm_success_and_snapshot_immutable(db):
    contract, calculation = seed_calculation(db)
    confirmed = service.confirm_calculation(db, calculation.id)
    assert confirmed.status == "confirmed" and confirmed.confirmed_at is not None
    with pytest.raises(service.LeaseConflictError, match="已确认"):
        service.confirm_calculation(db, calculation.id)
    with pytest.raises(service.LeaseConflictError, match="测算历史"):
        service.delete_contract(db, contract.id)

    before = [(row.sequence, row.amount) for row in service.get_assessment(db, calculation.id)]
    set_payments(db, contract.id, [{**TWO_PERIODS[0], "amount": "7.00"}, TWO_PERIODS[1]])
    after = [(row.sequence, row.amount) for row in service.get_assessment(db, calculation.id)]
    assert before == after
    assert service.get_calculation(db, calculation.id).status == "confirmed"


def test_confirm_blocks_stale_failed_unknown_and_rule_change(db):
    contract, calculation = seed_calculation(db)
    set_payments(db, contract.id, [{**TWO_PERIODS[0], "amount": "9.00"}, TWO_PERIODS[1]])
    with pytest.raises(service.LeaseConflictError, match="已过期"):
        service.confirm_calculation(db, calculation.id)

    for status in ("failed", "unknown"):
        contract, calculation = seed_calculation(db, engine=StubEngine(check_status=status))
        with pytest.raises(service.LeaseConflictError, match="校验未全部通过"):
            service.confirm_calculation(db, calculation.id, engine=StubEngine(check_status=status))

    contract, calculation = seed_calculation(db)
    with pytest.raises(service.LeaseConflictError, match="规则版本已更新"):
        service.confirm_calculation(db, calculation.id, engine=StubEngine(rule_version="rules-test-v2"))


def test_confirm_blocks_incomplete_snapshot_and_archived_contract(db):
    contract, calculation = seed_calculation(db)
    row = db.query(LeaseAssessment).filter(LeaseAssessment.calculation_id == calculation.id).first()
    db.delete(row)
    db.commit()
    with pytest.raises(service.LeaseConflictError, match="快照不完整"):
        service.confirm_calculation(db, calculation.id)

    contract, calculation = seed_calculation(db)
    service.update_contract(db, contract.id, schemas.LeaseContractPatch(status="archived"))
    with pytest.raises(service.LeaseConflictError, match="已归档"):
        service.confirm_calculation(db, calculation.id)


def test_missing_records_raise_not_found(db):
    with pytest.raises(service.LeaseNotFoundError):
        service.get_contract(db, 999999)
    with pytest.raises(service.LeaseNotFoundError):
        service.get_calculation(db, 999999)
    with pytest.raises(service.LeaseNotFoundError):
        service.get_assessment(db, 999999)
    with pytest.raises(service.LeaseNotFoundError):
        service.list_payments(db, 999999)


# ---------- 摘要 ----------


def test_summary_counts_latest_and_pending(db):
    failed_contract, failed_calc = seed_calculation(db, engine=StubEngine(check_status="failed"))
    make_contract(db, contract_no="SYN-LEASE-002", name="合成租赁合同二")
    summary = service.lease_summary(db)
    assert summary.total_contracts == 2
    assert summary.draft_count == 2 and summary.archived_count == 0
    assert summary.contracts_with_pending == 1
    item = next(row for row in summary.contracts if row.contract_id == failed_contract.id)
    assert item.latest_calculation_id == failed_calc.id
    assert item.check_counts == {"ok": 0, "failed": 1, "unknown": 0}
    assert item.pending_checks == 1
    single = service.lease_summary(db, contract_id=failed_contract.id)
    assert single.total_contracts == 1 and single.contracts[0].contract_id == failed_contract.id


# ---------- 导入接缝 ----------


def preview(items, errors=None, warnings=None):
    return schemas.PaymentImportPreview(
        sheet_name="数据源",
        items=[schemas.PaymentImportRow(**item) for item in items],
        warnings=warnings or [], errors=errors or [])




# ---------- 真实 API 集成（真实计算引擎 + 真实 IO） ----------

API = "/api/finance/lease"
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
API_CONTRACT = {
    **CONTRACT_BODY,
    "start_date": "2021-01-01",
    "end_date": "2021-12-31",
    "annual_rate": "0.06",
    "rate_basis": "合成快照利率依据",
    "remark": "合成快照备注",
    "attachment_note": "合成快照附件说明",
}
API_PERIODS = [
    {"sequence": 1, "period_start": "2021-01-01", "period_end": "2021-06-30",
     "amount": "103.00"},
    {"sequence": 2, "period_start": "2021-07-01", "period_end": "2021-12-31",
     "amount": "103.00"},
]


def api_contract(client, contract_no="SYN-API-001", **overrides):
    body = {**API_CONTRACT, "contract_no": contract_no, **overrides}
    response = client.post(f"{API}/contracts", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def api_payments(client, contract_id, items=None):
    response = client.put(
        f"{API}/contracts/{contract_id}/payments",
        json={"items": API_PERIODS if items is None else items},
    )
    assert response.status_code == 200, response.text
    return response.json()


def api_calculation(client, contract_id):
    response = client.post(f"{API}/contracts/{contract_id}/calculations")
    assert response.status_code == 201, response.text
    return response.json()


def payment_workbook(items) -> bytes:
    """按真实 importer 表头生成合成 xlsx，供上传预览/确认导入。"""
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "数据源"
    headers = [label for _, label in importer.EXPORT_HEADERS]
    sheet.append(headers)
    for item in items:
        values = {label: "" for label in headers}
        values.update({
            "支付期数": item["sequence"],
            "计租起始日期": item["period_start"],
            "计租结束日期": item["period_end"],
            "金额（不含税）": item["amount"],
            "备注": item.get("note", ""),
        })
        sheet.append([values[label] for label in headers])
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def entry_balances(content: bytes):
    """解析导出『分录』页：按凭证号汇总借贷并返回科目集合。"""
    sheet = load_workbook(io.BytesIO(content))["分录"]
    rows = list(sheet.iter_rows(values_only=True))
    header = next(index for index, row in enumerate(rows) if row and row[0] == "凭证号")
    totals, accounts = {}, set()
    for row in rows[header + 1:]:
        if not row or not row[0] or row[0] == "本批次无逐笔底稿分录":
            continue
        number = str(row[0])
        bucket = totals.setdefault(number, [Decimal("0.00"), Decimal("0.00")])
        bucket[0] += Decimal(str(row[4] or 0))
        bucket[1] += Decimal(str(row[5] or 0))
        accounts.add(row[3])
    return totals, accounts


def sheet_value(sheet, label):
    for row in sheet.iter_rows(values_only=True):
        if row and row[0] == label:
            return row[1]
    return None


def sheet_rows(sheet, first_header):
    rows = list(sheet.iter_rows(values_only=True))
    header = next(index for index, row in enumerate(rows) if row and row[0] == first_header)
    return [row for row in rows[header + 1:] if row and row[0] is not None]


def test_manifest_loaded_with_lease_summary_tool():
    from app.modules import get_loaded_modules
    from app.tool_schema import params_errors

    loaded = {module.name: module for module in get_loaded_modules()}
    assert "finance_lease" in loaded
    manifest = loaded["finance_lease"]
    assert manifest.title == "租赁核算"
    assert manifest.prefix == "/api/finance/lease"
    assert manifest.validate() == []
    tool = manifest.agent_tools[0]
    assert tool["name"] == "lease_summary"
    assert tool["readonly"] is True
    assert tool["required"] == []
    assert params_errors("lease_summary", tool["params"], tool["required"]) == []
    assert "finance_specialist" in manifest.preset_experts


def test_api_real_engine_confirmation_and_export(client):
    contract = api_contract(client)
    listing = client.get(f"{API}/contracts", params={"page": 1, "page_size": 200}).json()
    assert listing["total"] == 1 and listing["items"][0]["id"] == contract["id"]
    api_payments(client, contract["id"])
    calculation = api_calculation(client, contract["id"])

    assert calculation["status"] == "calculated"
    assert calculation["all_checks_passed"] is True
    assert calculation["check_counts"]["failed"] == 0
    assert calculation["check_counts"]["unknown"] == 0
    assert calculation["summary"]["present_value"] == "203.00"
    assert calculation["summary"]["total_payment"] == "206.00"
    assert calculation["summary"]["total_interest"] == "3.00"

    assert len(client.get(f"{API}/calculations/{calculation['id']}/assessment").json()) == 2
    assert len(client.get(f"{API}/calculations/{calculation['id']}/periods").json()) == 12
    checks = client.get(f"{API}/calculations/{calculation['id']}/checks").json()
    assert checks["all_passed"] is True and checks["has_unknown"] is False

    again = api_calculation(client, contract["id"])
    assert again["id"] == calculation["id"]
    assert client.get(f"{API}/contracts/{contract['id']}").json()["id"] == contract["id"]
    assert len(client.get(f"{API}/contracts/{contract['id']}/calculations").json()) == 1

    confirmed = client.post(f"{API}/calculations/{calculation['id']}/confirm")
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["status"] == "confirmed"
    assert client.post(f"{API}/calculations/{calculation['id']}/confirm").status_code == 409

    exported = client.get(f"{API}/calculations/{calculation['id']}/export.xlsx")
    assert exported.status_code == 200, exported.text
    disposition = exported.headers["content-disposition"]
    assert "filename=" in disposition and "filename*=UTF-8''" in disposition
    workbook = load_workbook(io.BytesIO(exported.content))
    assert workbook.sheetnames == ["数据源", "评估表", "期间表", "校验", "分录"]
    assert Decimal(str(sheet_value(workbook["评估表"], "使用权资产原值"))) == Decimal("203.00")
    assert Decimal(str(sheet_value(workbook["评估表"], "租赁付款额（不含税）"))) == Decimal("206.00")
    assert Decimal(str(sheet_value(workbook["评估表"], "未确认融资费用"))) == Decimal("3.00")

    assessment_api = client.get(f"{API}/calculations/{calculation['id']}/assessment").json()
    assessment_rows = sheet_rows(workbook["评估表"], "期次")
    assert len(assessment_rows) == len(assessment_api)
    for row, expected in zip(assessment_rows, assessment_api):
        assert Decimal(str(row[8] or 0)) == Decimal(expected["interest_u"])
        assert Decimal(str(row[7] or 0)) == Decimal(expected["unamortized_finance"])

    periods_api = client.get(f"{API}/calculations/{calculation['id']}/periods").json()
    period_rows = sheet_rows(workbook["期间表"], "月份")
    assert len(period_rows) == len(periods_api)
    asset_cost = Decimal(calculation["summary"]["present_value"])
    cumulative = Decimal("0.00")
    for row, expected in zip(period_rows, periods_api):
        cumulative = (cumulative + Decimal(expected["depreciation"])).quantize(Decimal("0.01"))
        assert row[5] is not None
        assert Decimal(str(row[5])) == (asset_cost - cumulative).quantize(Decimal("0.01"))

    assert str(sheet_value(workbook["评估表"], "利率依据")) == "合成快照利率依据"
    original_note = str(sheet_value(workbook["评估表"], "合同备注"))
    assert "合成快照备注" in original_note and "合成快照附件说明" in original_note

    before_hash = calculation["input_hash"]
    patched = client.patch(f"{API}/contracts/{contract['id']}", json={
        "rate_basis": "当前合同新依据",
        "remark": "当前合同新备注",
        "attachment_note": "当前合同新附件",
    })
    assert patched.status_code == 200, patched.text
    assert client.get(f"{API}/calculations/{calculation['id']}").json()["input_hash"] == before_hash
    again_export = client.get(f"{API}/calculations/{calculation['id']}/export.xlsx")
    assert again_export.status_code == 200
    snapshot_workbook = load_workbook(io.BytesIO(again_export.content))
    assert str(sheet_value(snapshot_workbook["评估表"], "利率依据")) == "合成快照利率依据"
    snapshot_note = str(sheet_value(snapshot_workbook["评估表"], "合同备注"))
    assert "合成快照备注" in snapshot_note and "合成快照附件说明" in snapshot_note
    assert "当前合同新备注" not in snapshot_note

    entry_text = [str(cell) for row in workbook["分录"].iter_rows(values_only=True)
                  for cell in row if cell]
    assert any("不支持" in text for text in entry_text)
    totals, accounts = entry_balances(exported.content)
    assert totals and all(debit == credit for debit, credit in totals.values())
    assert {"使用权资产", "折旧费用—使用权资产", "财务费用—租赁负债利息"} <= accounts

def test_api_import_preview_then_apply_real_io(client):
    contract = api_contract(client)
    content = payment_workbook(API_PERIODS)
    preview = client.post(
        f"{API}/contracts/{contract['id']}/payments/import/preview",
        files={"file": ("合成付款计划.xlsx", content, XLSX_MIME)},
    )
    assert preview.status_code == 200, preview.text
    assert len(preview.json()["items"]) == 2
    assert preview.json()["errors"] == []
    assert client.get(f"{API}/contracts/{contract['id']}/payments").json() == []

    applied = client.post(
        f"{API}/contracts/{contract['id']}/payments/import/apply",
        files={"file": ("合成付款计划.xlsx", content, XLSX_MIME)},
    )
    assert applied.status_code == 200, applied.text
    payments = client.get(f"{API}/contracts/{contract['id']}/payments").json()
    assert [row["sequence"] for row in payments] == [1, 2]
    assert payments[0]["source_row"] is not None

    calculation = api_calculation(client, contract["id"])
    assert calculation["all_checks_passed"] is True

def test_api_error_mapping_stale_and_delete_guard(client):
    assert client.get(f"{API}/contracts/999999").status_code == 404

    contract = api_contract(client)
    duplicate = client.post(
        f"{API}/contracts", json={**API_CONTRACT, "contract_no": contract["contract_no"]}
    )
    assert duplicate.status_code == 409
    assert "唯一约束" in duplicate.json()["detail"]

    empty = client.post(f"{API}/contracts/{contract['id']}/calculations")
    assert empty.status_code == 400
    assert "付款计划为空" in empty.json()["detail"]

    api_payments(client, contract["id"])
    calculation = api_calculation(client, contract["id"])
    client.put(
        f"{API}/contracts/{contract['id']}/payments",
        json={"items": [{**API_PERIODS[0], "amount": "104.00"}, API_PERIODS[1]]},
    )
    assert client.get(f"{API}/calculations/{calculation['id']}").json()["stale"] is True
    confirm = client.post(f"{API}/calculations/{calculation['id']}/confirm")
    assert confirm.status_code == 409
    assert "已过期" in confirm.json()["detail"]
    assert client.get(f"{API}/calculations/{calculation['id']}/export.xlsx").status_code == 409
    allowed = client.get(
        f"{API}/calculations/{calculation['id']}/export.xlsx", params={"allow_stale": "true"}
    )
    assert allowed.status_code == 200
    assert client.delete(f"{API}/contracts/{contract['id']}").status_code == 409



def test_api_archived_contract_is_read_only(client):
    contract = api_contract(client)
    api_payments(client, contract["id"])
    archived = client.patch(f"{API}/contracts/{contract['id']}", json={"status": "archived"})
    assert archived.status_code == 200 and archived.json()["status"] == "archived"
    assert client.put(
        f"{API}/contracts/{contract['id']}/payments", json={"items": API_PERIODS}
    ).status_code == 409
    assert client.post(f"{API}/contracts/{contract['id']}/calculations").status_code == 409
    assert client.patch(
        f"{API}/contracts/{contract['id']}", json={"name": "不可改"}
    ).status_code == 409


def test_api_month_mismatch_and_unknown_block_confirm(client):
    contract = api_contract(client, "SYN-API-MONTH")
    api_payments(client, contract["id"], [{**API_PERIODS[0], "months": 5}, API_PERIODS[1]])
    calculation = api_calculation(client, contract["id"])
    checks = {item["key"]: item for item in calculation["checks"]}
    assert checks["month_count_consistency"]["status"] == "failed"
    assert calculation["all_checks_passed"] is False
    blocked = client.post(f"{API}/calculations/{calculation['id']}/confirm")
    assert blocked.status_code == 409
    assert "校验未全部通过" in blocked.json()["detail"]

    unknown_contract = api_contract(client, "SYN-API-UNKNOWN")
    unknown_items = [
        {"sequence": 1, "period_start": "2021-02-01", "period_end": "2021-06-30",
         "amount": "100.00"},
        {"sequence": 2, "period_start": "2021-07-01", "period_end": "2021-11-30",
         "amount": "100.00"},
    ]
    api_payments(client, unknown_contract["id"], unknown_items)
    unknown_calc = api_calculation(client, unknown_contract["id"])
    unknown_checks = {item["key"]: item for item in unknown_calc["checks"]}
    assert unknown_checks["contract_range"]["status"] == "unknown"
    assert unknown_calc["has_unknown_checks"] is True
    assert client.post(f"{API}/calculations/{unknown_calc['id']}/confirm").status_code == 409


def test_lease_summary_tool_real_call(client):
    from app.agents.tools import TOOL_SPECS

    assert "lease_summary" in TOOL_SPECS
    spec = TOOL_SPECS["lease_summary"]
    assert spec["readonly"] is True
    contract = api_contract(client)
    api_payments(client, contract["id"])
    calculation = api_calculation(client, contract["id"])

    result = spec["func"](contract_id=contract["id"])
    assert result["ok"] is True
    assert result["total_contracts"] == 1
    item = result["contracts"][0]
    assert item["contract_id"] == contract["id"]
    assert item["latest_calculation_id"] == calculation["id"]
    assert item["check_counts"]["failed"] == 0

    missing = spec["func"](contract_id=999999)
    assert missing["ok"] is False and "不存在" in missing["error"]
    summary = client.get(f"{API}/summary").json()
    assert summary["total_contracts"] == 1
    assert summary["contracts"][0]["latest_calculation_id"] == calculation["id"]

def test_api_engine_unavailable_maps_503(client, monkeypatch):
    contract = api_contract(client, "SYN-API-503")
    api_payments(client, contract["id"])

    def unavailable(*args, **kwargs):
        raise service.LeaseEngineUnavailable("计算引擎尚未接入（测试注入）")

    monkeypatch.setattr(service, "create_calculation", unavailable)
    response = client.post(f"{API}/contracts/{contract['id']}/calculations")
    assert response.status_code == 503
    assert "尚未接入" in response.json()["detail"]
