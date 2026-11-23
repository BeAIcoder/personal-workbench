# -*- coding: utf-8 -*-
"""finance_lease × BIP 只读对账测试（真实 service / API + 临时 SQLite）。

覆盖：

- 映射 PUT 的整批替换语义、未知科目位/重复科目位/同一科目多映射/空白范围 4xx，
  以及「只写本模块映射表、绝不写 BIP」；
- 上下文 = 合同承租人 + BIP 余额表真实账簿/期间；
- 对账五科目：借方向、贷方向、方向无关期末余额回退（标明来源）、缺映射、缺 BIP
  余额行、多行歧义、方向金额与期末余额冲突、全零行（缺数据按 unknown 不按 0 通过）；
- 合同覆盖：stale 排除、未确认、无覆盖、重复历史批次不重复汇总、多合同汇总、
  部分合同不可用时不做部分求和；
- 0.01 容差边界（=0.01 通过，=0.02 差异）；
- 真实引擎 + 真实 API 全链路，且对账前后 BIP 库内容逐行不变。

全部数据为合成值（合成承租人/合成账簿/合成科目号），不读真实底稿、不碰 backend/data。
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import inspect

from app.database import Base, SessionLocal, engine
from app.modules.finance_lease import reconciliation, service
from app.modules.finance_lease.models import (
    LeaseAssessment,
    LeaseBipMap,
    LeaseCalculation,
    LeaseContract,
    LeasePayment,
    LeasePeriod,
)
from app.modules.finance_lease.reconciliation_schemas import CheckRequest, MappingPutRequest
from app.modules.finance_ledger_bip.models import BipBalance, BipBatch

API = "/api/finance/lease"
LESSEE = "合成承租人甲"
LEDGER = "合成账簿"
PERIOD = "2026-03"

# 合成五科目：1801 资产（借）/1802 累计折旧（贷）/2701 剩余付款（贷）/
# 2702 未确认融资费用（借）/2703 租赁负债净额（贷）
CODES = {
    "rou_asset": ("1801", "debit"),
    "rou_accum_dep": ("1802", "credit"),
    "lease_payment": ("2701", "credit"),
    "unrecognized_finance": ("2702", "debit"),
    "lease_liability": ("2703", "credit"),
}

# 与合计自洽的 6 个月合成期间行：现值 570、累计折旧 95/月、月付 100、
# 未确认融资费用 30 起按 6/月递减；2026-03 月末：折旧 285、剩余付款 300、
# 未确认 18、净负债 282。
PERIODS = [
    ("2026-01", "95.00", "100.00", "30.00", "470.00"),
    ("2026-02", "95.00", "100.00", "24.00", "376.00"),
    ("2026-03", "95.00", "100.00", "18.00", "282.00"),
    ("2026-04", "95.00", "100.00", "12.00", "188.00"),
    ("2026-05", "95.00", "100.00", "6.00", "94.00"),
    ("2026-06", "95.00", "100.00", "0.00", "0.00"),
]
PRESENT_VALUE = "570.00"


@pytest.fixture(autouse=True)
def _ensure_tables():
    """非 client 用例直触库：保证六表 + BIP 两表存在，并在每个用例前清空。"""
    if not inspect(engine).has_table("mod_lease_contract"):
        Base.metadata.create_all(bind=engine)
    with SessionLocal() as session:
        session.query(BipBalance).delete()
        session.query(BipBatch).delete()
        for model in (LeaseBipMap, LeasePeriod, LeaseAssessment, LeaseCalculation,
                      LeasePayment, LeaseContract):
            session.query(model).delete()
        session.commit()
    yield


@pytest.fixture()
def db():
    with SessionLocal() as session:
        yield session


# ---------- 合成数据 ----------


def add_contract(db, *, contract_no="SYN-LEASE-901", lessee=LESSEE,
                 start=date(2026, 1, 1), end=date(2026, 12, 31),
                 amount="600.00") -> LeaseContract:
    contract = LeaseContract(
        contract_no=contract_no, name="合成租赁合同", lessee=lessee, project="合成项目",
        lessor="合成出租方", start_date=start, end_date=end,
        annual_rate=Decimal("0.035"), rate_basis="合成利率", status="draft",
    )
    db.add(contract)
    db.flush()
    db.add(LeasePayment(contract_id=contract.id, sequence=1, period_start=start,
                        period_end=end, months=6, amount=Decimal(amount)))
    db.commit()
    db.refresh(contract)
    return contract


def add_calculation(db, contract, *, periods=None, status="confirmed", rule="rules-syn-1",
                    digest=None, present_value=PRESENT_VALUE) -> LeaseCalculation:
    rows = PERIODS if periods is None else periods
    payload = {
        "total_payment": str(sum(Decimal(row[2]) for row in rows)),
        "present_value": present_value,
        "total_interest": "0.00",
        "total_depreciation": str(sum(Decimal(row[1]) for row in rows)),
        "payment_count": 1,
        "month_count": len(rows),
        "first_period": rows[0][0],
        "last_period": rows[-1][0],
    }
    calculation = LeaseCalculation(
        contract_id=contract.id,
        input_hash=digest or service.current_input_hash(db, contract.id),
        rule_version=rule, status=status, input_snapshot={}, summary=payload,
        checks=[{"key": "sums_balance", "title": "合成勾稽", "status": "ok"}],
        confirmed_at=datetime.now() if status == "confirmed" else None,
    )
    db.add(calculation)
    db.flush()
    for index, (month, depreciation, payable, unamortized, liability) in enumerate(rows, start=1):
        db.add(LeasePeriod(
            calculation_id=calculation.id, period_month=month, sequence=1,
            depreciation=Decimal(depreciation), payable=Decimal(payable),
            interest=Decimal("0.00"), lease_payment=Decimal("0.00"),
            unamortized_finance=Decimal(unamortized), liability_balance=Decimal(liability),
        ))
    db.commit()
    db.refresh(calculation)
    return calculation


def add_balance(db, *, ledger=LEDGER, period=PERIOD, code, debit="0.00", credit="0.00",
                balance="0.00", name="合成科目") -> BipBalance:
    batch = db.query(BipBatch).order_by(BipBatch.id.asc()).first()
    if batch is None:
        batch = BipBatch(filename="合成余额表.xlsx", file_type="balance", period=period,
                         total_rows=1)
        db.add(batch)
        db.flush()
    row = BipBalance(batch_id=batch.id, ledger=ledger, period=period, subject_code=code,
                     subject_name=name, closing_debit=float(Decimal(debit)),
                     closing_credit=float(Decimal(credit)),
                     closing_balance=float(Decimal(balance)))
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def seed_balances(db, *, ledger=LEDGER, period=PERIOD, asset="570.00", accum="285.00",
                  payment="300.00", unrecognized="18.00", liability="282.00"):
    add_balance(db, ledger=ledger, period=period, code="1801", debit=asset)
    add_balance(db, ledger=ledger, period=period, code="1802", credit=accum)
    add_balance(db, ledger=ledger, period=period, code="2701", credit=payment)
    add_balance(db, ledger=ledger, period=period, code="2702", debit=unrecognized)
    add_balance(db, ledger=ledger, period=period, code="2703", credit=liability)


def seed_mappings(db, *, lessee=LESSEE, ledger=LEDGER, skip=()) -> None:
    for key, (code, direction) in CODES.items():
        if key in skip:
            continue
        db.add(LeaseBipMap(lessee=lessee, ledger=ledger, subject_key=key,
                           subject_code=code, direction=direction))
    db.commit()


def run_check(db, *, lessee=LESSEE, ledger=LEDGER, period=PERIOD):
    return reconciliation.run_reconciliation(
        db, CheckRequest(lessee=lessee, ledger=ledger, period=period)
    )


def subject_map(result) -> dict:
    return {row.subject_key: row for row in result.subjects}


def bip_snapshot(db) -> list[tuple]:
    rows = (db.query(BipBalance).order_by(BipBalance.id.asc()).all())
    return [(row.id, row.batch_id, row.ledger, row.period, row.subject_code,
             row.closing_debit, row.closing_credit, row.closing_balance) for row in rows]


# ---------- 映射 CRUD ----------


def test_put_mappings_replaces_scope_and_only_writes_own_table(client, db):
    before_bip = bip_snapshot(db)
    body = {"lessee": LESSEE, "ledger": LEDGER, "items": [
        {"subject_key": key, "subject_code": code, "direction": direction}
        for key, (code, direction) in CODES.items()
    ]}
    saved = client.put(f"{API}/reconciliation/mappings", json=body)
    assert saved.status_code == 200, saved.text
    payload = saved.json()
    assert payload["total"] == 5
    assert {item["subject_key"] for item in payload["items"]} == set(CODES)
    assert payload["items"][0]["subject_key_label"]

    # 再 PUT 只留 3 条 → 其余科目位视为清除
    body["items"] = body["items"][:3]
    again = client.put(f"{API}/reconciliation/mappings", json=body).json()
    assert again["total"] == 3
    listed = client.get(f"{API}/reconciliation/mappings",
                        params={"lessee": LESSEE, "ledger": LEDGER}).json()
    assert listed["total"] == 3

    # 全清
    cleared = client.put(f"{API}/reconciliation/mappings",
                         json={"lessee": LESSEE, "ledger": LEDGER, "items": []})
    assert cleared.status_code == 200
    assert cleared.json()["total"] == 0
    assert bip_snapshot(db) == before_bip


def test_put_mappings_rejects_bad_scope_and_duplicates(client):
    base = {"lessee": LESSEE, "ledger": LEDGER}
    unknown_key = client.put(f"{API}/reconciliation/mappings", json={
        **base, "items": [{"subject_key": "not_a_slot", "subject_code": "1", "direction": "debit"}],
    })
    assert unknown_key.status_code == 422
    assert "科目位" in unknown_key.text

    bad_direction = client.put(f"{API}/reconciliation/mappings", json={
        **base, "items": [{"subject_key": "rou_asset", "subject_code": "1801", "direction": "both"}],
    })
    assert bad_direction.status_code == 422

    duplicate_code = client.put(f"{API}/reconciliation/mappings", json={
        **base, "items": [
            {"subject_key": "rou_asset", "subject_code": "1801", "direction": "debit"},
            {"subject_key": "rou_accum_dep", "subject_code": "1801", "direction": "credit"},
        ],
    })
    assert duplicate_code.status_code == 422
    assert "多个科目位" in duplicate_code.text

    duplicate_key = client.put(f"{API}/reconciliation/mappings", json={
        **base, "items": [
            {"subject_key": "rou_asset", "subject_code": "1801", "direction": "debit"},
            {"subject_key": "rou_asset", "subject_code": "1802", "direction": "debit"},
        ],
    })
    assert duplicate_key.status_code == 422

    blank_scope = client.put(f"{API}/reconciliation/mappings", json={
        "lessee": "   ", "ledger": LEDGER, "items": [],
    })
    assert blank_scope.status_code == 422


def test_context_lists_contract_lessees_and_bip_scopes(client, db):
    add_contract(db)
    add_balance(db, code="1801", debit="1.00")
    context = client.get(f"{API}/reconciliation/context")
    assert context.status_code == 200
    payload = context.json()
    assert payload["lessees"] == [LESSEE]
    assert payload["ledgers"] == [LEDGER]
    assert payload["periods"] == [PERIOD]
    assert [item["key"] for item in payload["subject_keys"]] == list(CODES)
    assert [item["value"] for item in payload["directions"]] == ["debit", "credit"]
    assert payload["mappings"] == []


# ---------- 对账主路径 ----------


def test_check_all_five_subjects_ok(client, db):
    contract = add_contract(db)
    add_calculation(db, contract)
    seed_balances(db)
    seed_mappings(db)

    response = client.post(f"{API}/reconciliation/check",
                           json={"lessee": LESSEE, "ledger": LEDGER, "period": PERIOD})
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["counts"] == {"ok": 5, "failed": 0, "unknown": 0}
    assert Decimal(str(payload["tolerance"])) == Decimal("0.01")
    by_key = {row["subject_key"]: row for row in payload["subjects"]}
    assert Decimal(by_key["rou_asset"]["expected"]) == Decimal("570.00")
    assert Decimal(by_key["rou_accum_dep"]["expected"]) == Decimal("285.00")
    assert Decimal(by_key["lease_payment"]["expected"]) == Decimal("300.00")
    assert Decimal(by_key["unrecognized_finance"]["expected"]) == Decimal("18.00")
    assert Decimal(by_key["lease_liability"]["expected"]) == Decimal("282.00")
    assert all(row["source"] == "bip_closing_direction" for row in payload["subjects"])
    assert all(row["expected_source"] == "lease_snapshot" for row in payload["subjects"])
    assert payload["coverage"] == {
        "contract_count": 1, "selected_count": 1, "unknown_count": 0, "reasons": [],
    }
    coverage = payload["contracts"][0]
    assert coverage["selected"] is True and coverage["reason"] == ""
    assert coverage["calculation_id"] is not None
    assert Decimal(coverage["accumulated_depreciation"]) == Decimal("285.00")
    assert Decimal(coverage["remaining_payment"]) == Decimal("300.00")


def test_check_credit_and_debit_directions(db):
    """贷方科目取 credit-debit，借方科目取 debit-credit；方向写反必须暴露为差异。"""
    contract = add_contract(db)
    add_calculation(db, contract)
    seed_balances(db)
    seed_mappings(db)
    result = run_check(db)
    by_key = subject_map(result)
    assert by_key["rou_accum_dep"].actual == Decimal("285.00")
    assert by_key["unrecognized_finance"].actual == Decimal("18.00")

    # 把累计折旧方向错配为 debit → 实际变成 -285，差异 570（不得静默通过）
    mapping = db.query(LeaseBipMap).filter_by(subject_key="rou_accum_dep").one()
    mapping.direction = "debit"
    db.commit()
    wrong = subject_map(run_check(db))["rou_accum_dep"]
    assert wrong.status == "failed"
    assert wrong.actual == Decimal("-285.00")
    assert wrong.difference == Decimal("570.00")


def test_directionless_closing_balance_used_with_source_label(db):
    contract = add_contract(db)
    add_calculation(db, contract)
    seed_balances(db)
    seed_mappings(db)
    # 租赁负债净额改为方向金额全空、只有方向无关期末余额
    row = db.query(BipBalance).filter_by(subject_code="2703").one()
    row.closing_debit = 0.0
    row.closing_credit = 0.0
    row.closing_balance = -282.0  # 模板给的是绝对值口径，负号应被忽略
    db.commit()

    subject = subject_map(run_check(db))["lease_liability"]
    assert subject.status == "ok"
    assert subject.actual == Decimal("282.00")
    assert subject.source == "bip_closing_balance"
    assert "期末余额" in subject.source_label


def test_missing_mapping_is_unknown_with_reason(db):
    contract = add_contract(db)
    add_calculation(db, contract)
    seed_balances(db)
    seed_mappings(db, skip=("unrecognized_finance",))

    result = run_check(db)
    subject = subject_map(result)["unrecognized_finance"]
    assert subject.status == "unknown"
    assert subject.actual is None
    assert subject.source == "unmapped"
    assert "未配置" in subject.reason
    assert result.counts.unknown == 1 and result.counts.ok == 4
    assert result.status == "unknown"


def test_missing_bip_row_is_unknown_and_ledger_must_match_exactly(db):
    contract = add_contract(db)
    add_calculation(db, contract)
    seed_balances(db)
    # 另一账簿的同科目余额不得被模糊匹配进来
    add_balance(db, ledger="合成账簿（其他）", code="1801", debit="570.00")
    db.query(BipBalance).filter_by(subject_code="2701", ledger=LEDGER).delete()
    db.commit()
    seed_mappings(db)

    subject = subject_map(run_check(db))["lease_payment"]
    assert subject.status == "unknown"
    assert subject.source == "bip_missing"
    assert "没有科目 2701" in subject.reason

    # 账簿必须等值匹配：给近似账簿配同一套映射后，五科目仍是「BIP 缺行」，
    # 不得借用「合成账簿」或「合成账簿（其他）」的任何余额
    seed_mappings(db, ledger="合成账簿X")
    other_ledger = run_check(db, ledger="合成账簿X")
    assert other_ledger.status == "unknown"
    assert {row.source for row in other_ledger.subjects} == {"bip_missing"}


def test_duplicate_balance_rows_are_ambiguous_unknown(db, monkeypatch):
    """唯一索引保证新库不会同键两行；迁移遗留库可能有多行，必须判歧义而不是取首行。"""
    contract = add_contract(db)
    add_calculation(db, contract)
    seed_balances(db)
    seed_mappings(db)

    duplicate = [
        {"id": 1, "ledger": LEDGER, "period": PERIOD, "subject_code": "1801",
         "subject_name": "合成科目", "closing_debit": Decimal("570.00"),
         "closing_credit": Decimal("0.00"), "closing_balance": Decimal("0.00"),
         "opening_balance": Decimal("0.00")},
        {"id": 2, "ledger": LEDGER, "period": PERIOD, "subject_code": "1801",
         "subject_name": "合成科目", "closing_debit": Decimal("570.00"),
         "closing_credit": Decimal("0.00"), "closing_balance": Decimal("0.00"),
         "opening_balance": Decimal("0.00")},
    ]
    real = reconciliation._bip()

    class FakeBip:
        list_balance_scopes = staticmethod(real.list_balance_scopes)

        @staticmethod
        def read_balance_rows(db, *, ledger, period, subject_code):
            return duplicate if subject_code == "1801" else real.read_balance_rows(
                db, ledger=ledger, period=period, subject_code=subject_code)

    monkeypatch.setattr(reconciliation, "_bip", lambda: FakeBip)

    subject = subject_map(run_check(db))["rou_asset"]
    assert subject.status == "unknown"
    assert subject.source == "bip_ambiguous"
    assert "2 行" in subject.reason


def test_conflict_between_direction_and_balance_is_unknown(db):
    contract = add_contract(db)
    add_calculation(db, contract)
    seed_balances(db, asset="570.00")
    row = db.query(BipBalance).filter_by(subject_code="1801").one()
    row.closing_balance = 560.0  # 与借方 570 相差 10 元 → 冲突
    db.commit()
    seed_mappings(db)

    subject = subject_map(run_check(db))["rou_asset"]
    assert subject.status == "unknown"
    assert subject.source == "bip_conflict"
    assert "冲突" in subject.reason


def test_all_zero_balance_row_is_unknown_not_pass(db):
    contract = add_contract(db)
    add_calculation(db, contract, periods=[("2026-01", "0.00", "0.00", "0.00", "0.00")],
                    present_value="0.00")
    add_balance(db, period="2026-01", code="1801", debit="0.00", credit="0.00",
                balance="0.00")
    seed_mappings(db, skip=("rou_accum_dep", "lease_payment", "unrecognized_finance",
                            "lease_liability"))

    subject = subject_map(run_check(db, period="2026-01"))["rou_asset"]
    assert subject.status == "unknown"
    assert subject.source == "bip_zero_balance"
    assert subject.actual is None
    assert "无法确定实际数" in subject.reason


# ---------- 覆盖：stale / 未确认 / 无覆盖 / 历史批次 ----------


def test_stale_confirmed_calculation_is_excluded(db):
    contract = add_contract(db)
    add_calculation(db, contract, digest="f" * 64)  # 与当前 input_hash 不一致
    seed_balances(db)
    seed_mappings(db)

    result = run_check(db)
    assert result.status == "unknown"
    assert {row.status for row in result.subjects} == {"unknown"}
    assert {row.source for row in result.subjects} == {"no_calculation"}
    assert all(row.expected is None for row in result.subjects)
    coverage = result.contracts[0]
    assert coverage.selected is False
    assert coverage.stale is True
    assert "已过期" in coverage.reason
    assert result.coverage.reasons == [coverage.reason]


def test_unconfirmed_calculation_is_unknown(db):
    contract = add_contract(db)
    add_calculation(db, contract, status="calculated")
    seed_balances(db)
    seed_mappings(db)

    result = run_check(db)
    coverage = result.contracts[0]
    assert coverage.selected is False
    assert coverage.calculation_status == "calculated"
    assert "未确认" in coverage.reason
    assert subject_map(result)["rou_asset"].status == "unknown"


def test_calculation_without_target_period_is_not_covered(db):
    contract = add_contract(db)
    add_calculation(db, contract, periods=PERIODS[:3])  # 只覆盖 2026-01~2026-03
    seed_balances(db)
    seed_mappings(db)

    result = run_check(db, period="2026-06")
    coverage = result.contracts[0]
    assert coverage.selected is False
    assert coverage.covers_period is False
    assert "未覆盖期间 2026-06" in coverage.reason
    assert subject_map(result)["rou_asset"].status == "unknown"


def test_duplicate_history_batches_use_latest_only(db):
    contract = add_contract(db)
    add_calculation(db, contract, rule="rules-syn-1", present_value="500.00")
    add_calculation(db, contract, rule="rules-syn-2", present_value="570.00")
    seed_balances(db)
    seed_mappings(db)

    result = run_check(db)
    assert result.contracts[0].calculation_rule_version == "rules-syn-2"
    assert subject_map(result)["rou_asset"].expected == Decimal("570.00")  # 不得 500+570


def test_multi_contract_aggregation(db):
    first = add_contract(db, contract_no="SYN-LEASE-901")
    second = add_contract(db, contract_no="SYN-LEASE-902", amount="600.00")
    add_calculation(db, first)
    add_calculation(db, second, present_value="430.00")
    # 两个合同各 570 / 430：合计原值 1000，累计折旧 570，剩余付款 600，未确认 36，净负债 564
    seed_balances(db, asset="1000.00", accum="570.00", payment="600.00",
                  unrecognized="36.00", liability="564.00")
    seed_mappings(db)

    result = run_check(db)
    assert result.status == "ok"
    assert result.coverage.contract_count == 2
    assert result.coverage.selected_count == 2
    by_key = subject_map(result)
    assert by_key["rou_asset"].expected == Decimal("1000.00")
    assert by_key["lease_liability"].expected == Decimal("564.00")
    assert [row.contract_no for row in result.contracts] == ["SYN-LEASE-901", "SYN-LEASE-902"]


def test_one_unusable_contract_blocks_partial_sum(db):
    usable = add_contract(db, contract_no="SYN-LEASE-901")
    stale = add_contract(db, contract_no="SYN-LEASE-902")
    add_calculation(db, usable)
    add_calculation(db, stale, digest="e" * 64)
    seed_balances(db)
    seed_mappings(db)

    result = run_check(db)
    assert result.status == "unknown"
    assert all(row.expected is None for row in result.subjects)
    assert result.coverage.selected_count == 1
    assert result.coverage.unknown_count == 1
    assert any("已过期" in reason for reason in result.coverage.reasons)


# ---------- 容差 ----------


@pytest.mark.parametrize(
    ("asset", "expected_status"),
    [("570.01", "ok"), ("569.99", "ok"), ("570.02", "failed"), ("569.98", "failed")],
)
def test_tolerance_boundary(db, asset, expected_status):
    contract = add_contract(db)
    add_calculation(db, contract)
    seed_balances(db, asset=asset)
    seed_mappings(db)

    subject = subject_map(run_check(db))["rou_asset"]
    assert subject.status == expected_status
    if expected_status == "failed":
        assert abs(subject.difference) == Decimal("0.02")
        assert "超过容差 0.01" in subject.reason
    else:
        assert abs(subject.difference) <= Decimal("0.01")


# ---------- 真实引擎 / API 全链路 + BIP 库不变 ----------


def api_contract(client, contract_no="SYN-API-911"):
    response = client.post(f"{API}/contracts", json={
        "contract_no": contract_no, "name": "合成租赁合同", "lessee": LESSEE,
        "project": "合成项目", "lessor": "合成出租方", "start_date": "2026-01-01",
        "end_date": "2026-12-31", "annual_rate": "0.035", "rate_basis": "合成利率",
    })
    assert response.status_code == 201, response.text
    return response.json()


def test_api_real_engine_check_and_bip_untouched(client, db):
    contract = api_contract(client)
    payments = client.put(f"{API}/contracts/{contract['id']}/payments", json={"items": [
        {"sequence": 1, "period_start": "2026-01-01", "period_end": "2026-06-30",
         "months": 6, "amount": "300.00"},
        {"sequence": 2, "period_start": "2026-07-01", "period_end": "2026-12-31",
         "months": 6, "amount": "300.00"},
    ]})
    assert payments.status_code == 200, payments.text
    calculation = client.post(f"{API}/contracts/{contract['id']}/calculations")
    assert calculation.status_code == 201, calculation.text
    calculation_id = calculation.json()["id"]
    confirmed = client.post(f"{API}/calculations/{calculation_id}/confirm")
    assert confirmed.status_code == 200, confirmed.text

    periods = client.get(f"{API}/calculations/{calculation_id}/periods").json()
    summary = confirmed.json()["summary"]
    target = "2026-03"  # 期中：剩余付款/未确认/净负债都非零，能验证五个科目位
    upto = [row for row in periods if row["period_month"] <= target]
    after = [row for row in periods if row["period_month"] > target]
    expected = {
        "rou_asset": Decimal(str(summary["present_value"])),
        "rou_accum_dep": sum(Decimal(row["depreciation"]) for row in upto),
        "lease_payment": sum(Decimal(row["payable"]) for row in after),
        "unrecognized_finance": Decimal(upto[-1]["unamortized_finance"]),
        "lease_liability": Decimal(upto[-1]["liability_balance"]),
    }
    # 模块侧恒等式：净负债 = 剩余付款 − 未确认融资费用余额
    assert expected["lease_liability"] == (
        expected["lease_payment"] - expected["unrecognized_finance"]
    )
    seed_balances(
        db, period=target,
        asset=str(expected["rou_asset"]), accum=str(expected["rou_accum_dep"]),
        payment=str(expected["lease_payment"]),
        unrecognized=str(expected["unrecognized_finance"]),
        liability=str(expected["lease_liability"]),
    )
    before_bip = bip_snapshot(db)

    saved = client.put(f"{API}/reconciliation/mappings", json={
        "lessee": LESSEE, "ledger": LEDGER,
        "items": [{"subject_key": key, "subject_code": code, "direction": direction}
                  for key, (code, direction) in CODES.items()],
    })
    assert saved.status_code == 200, saved.text

    response = client.post(f"{API}/reconciliation/check",
                           json={"lessee": LESSEE, "ledger": LEDGER, "period": target})
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "ok", [
        (row["subject_key"], row["status"], row["source"], row["reason"])
        for row in payload["subjects"]
    ] + [(row["contract_no"], row["reason"]) for row in payload["contracts"]]
    by_key = {row["subject_key"]: row for row in payload["subjects"]}
    for key, value in expected.items():
        assert Decimal(by_key[key]["expected"]) == value.quantize(Decimal("0.01"))
        assert Decimal(by_key[key]["actual"]) == value.quantize(Decimal("0.01"))
        assert Decimal(by_key[key]["difference"]) == Decimal("0.00")
    assert bip_snapshot(db) == before_bip
    assert db.query(BipBatch).count() == 1


def test_check_rejects_bad_period_and_blank_scope(client):
    assert client.post(f"{API}/reconciliation/check", json={
        "lessee": LESSEE, "ledger": LEDGER, "period": "2026-13",
    }).status_code == 422
    assert client.post(f"{API}/reconciliation/check", json={
        "lessee": "  ", "ledger": LEDGER, "period": PERIOD,
    }).status_code == 422
    assert client.post(f"{API}/reconciliation/check", json={
        "lessee": LESSEE, "ledger": LEDGER, "period": "2026-3",
    }).status_code == 422


def test_no_contract_for_lessee_is_unknown_with_reason(db):
    seed_balances(db)
    seed_mappings(db)

    result = run_check(db, lessee="合成承租人乙")
    assert result.status == "unknown"
    assert result.coverage.contract_count == 0
    assert all(row.expected is None and row.status == "unknown" for row in result.subjects)
    assert "没有租赁合同" in result.subjects[0].reason


def test_mapping_put_does_not_change_other_scope(client, db):
    db.add(LeaseBipMap(lessee=LESSEE, ledger="合成账簿B", subject_key="rou_asset",
                       subject_code="9999", direction="debit"))
    db.commit()
    response = client.put(f"{API}/reconciliation/mappings", json={
        "lessee": LESSEE, "ledger": LEDGER,
        "items": [{"subject_key": "rou_asset", "subject_code": "1801", "direction": "debit"}],
    })
    assert response.status_code == 200
    other = client.get(f"{API}/reconciliation/mappings", params={"ledger": "合成账簿B"}).json()
    assert other["total"] == 1
    assert other["items"][0]["subject_code"] == "9999"


def test_mapping_unique_conflict_maps_to_chinese_conflict(db, monkeypatch):
    """唯一约束冲突必须映射中文 LeaseConflictError（路由 409），不能冒出 500。"""
    from sqlalchemy.exc import IntegrityError

    def boom(*args, **kwargs):
        raise IntegrityError("INSERT INTO mod_lease_bip_map", {}, Exception("UNIQUE failed"))

    monkeypatch.setattr(db, "commit", boom)
    payload = MappingPutRequest.model_validate({
        "lessee": LESSEE, "ledger": LEDGER,
        "items": [{"subject_key": "rou_asset", "subject_code": "1801", "direction": "debit"}],
    })
    with pytest.raises(service.LeaseConflictError) as excinfo:
        reconciliation.replace_mappings(db, payload)
    assert "唯一约束冲突" in str(excinfo.value)


def test_put_mappings_conflict_returns_409(client, monkeypatch):
    def boom(db, payload):
        raise service.LeaseConflictError("科目映射保存失败：唯一约束冲突，请重试")

    monkeypatch.setattr(reconciliation, "replace_mappings", boom)
    response = client.put(f"{API}/reconciliation/mappings", json={
        "lessee": LESSEE, "ledger": LEDGER, "items": [],
    })
    assert response.status_code == 409
    assert "唯一约束冲突" in response.json()["detail"]


def test_mapping_update_in_place_keeps_single_row(db):
    payload = {"lessee": LESSEE, "ledger": LEDGER, "items": [
        {"subject_key": "rou_asset", "subject_code": "1801", "direction": "debit"}]}
    reconciliation.replace_mappings(db, MappingPutRequest.model_validate(payload))
    payload["items"][0] = {"subject_key": "rou_asset", "subject_code": "1801A", "direction": "credit"}
    result = reconciliation.replace_mappings(db, MappingPutRequest.model_validate(payload))
    assert result.total == 1
    assert result.items[0].subject_code == "1801A"
    assert result.items[0].direction == "credit"
    assert db.query(LeaseBipMap).count() == 1
