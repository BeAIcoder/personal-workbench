# -*- coding: utf-8 -*-
"""finance_deferred 模型 / schema / 服务 / 真实 API 集成测试。

覆盖：三张 ``mod_deferred_*`` 表与唯一约束、项目 CRUD（严格输入/重复 409/归档只读/
删除守卫）、输入哈希与幂等测算、失败测算不留半套结果、引擎结构校验、确认状态机、
风险警告（超租赁到期日/末期尾差）的显式确认、过期阻断确认与导出、xlsx 导出真实值
（项目参数/月度摊销/校验三页签，openpyxl 回读）、公式注入防护、中文错误映射与
``deferred_summary`` 工具真实调用。

Stub 只用于异常注入（引擎失败、结构不合法、规则版本变化）；正常测算一律调用真实
``calculation.compute``，导出调用真实 ``exporter``。只用临时 SQLite 与合成数据。
"""
from __future__ import annotations

import io
from datetime import date
from decimal import Decimal

import pytest
from openpyxl import load_workbook
from pydantic import ValidationError
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from app.database import Base, SessionLocal, engine
from app.modules.finance_deferred import models, schemas, service

API = "/api/finance/deferred"
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

ITEM_BODY = {
    "project_code": "SYN-DEF-001",
    "name": "合成改良项目",
    "project": "合成项目",
    "amount_incl_tax": "1356.00",
    "tax_rate": "0.13",
    "start_month": "2026-01",
    "months": 12,
    "lease_end_date": "2026-12-31",
    "remark": "合成备注",
}


@pytest.fixture(autouse=True)
def _ensure_tables():
    """非 client 直触库：保证三表存在，并在每个用例前清空本模块数据。"""
    if not inspect(engine).has_table("mod_deferred_item"):
        Base.metadata.create_all(bind=engine)
    with SessionLocal() as session:
        for model in (models.DeferredPeriod, models.DeferredRun, models.DeferredItem):
            session.query(model).delete()
        session.commit()
    yield


@pytest.fixture()
def db():
    with SessionLocal() as session:
        yield session


class BoomEngine:
    rule_version = "deferred-test-v1"

    def compute(self, payload):
        raise RuntimeError("合成引擎失败")


class BadStructureEngine:
    """返回月份标签错位的结构（模拟引擎坏输出，服务必须拒绝且不落库）。"""

    rule_version = "deferred-test-v1"

    def compute(self, payload):
        from app.modules.finance_deferred import calculation

        result = calculation.compute(payload)
        periods = [row.model_copy(update={"period_month": f"2025-{row.sequence:02d}"})
                   for row in result.periods]
        return result.model_copy(update={"periods": periods})


class VersionEngine:
    """只改规则版本，用于验证「规则版本已更新」阻断确认。"""

    rule_version = "deferred-test-v2"

    def compute(self, payload):
        from app.modules.finance_deferred import calculation

        return calculation.compute(payload).model_copy(update={"rule_version": self.rule_version})


def count(db, model) -> int:
    return db.query(model).count()


def make_item(db, **overrides) -> schemas.DeferredItemOut:
    body = {**ITEM_BODY, **overrides}
    if "project_code" not in overrides:
        body["project_code"] = f"SYN-DEF-{count(db, models.DeferredItem) + 1:03d}"
    return service.create_item(db, schemas.DeferredItemCreate(**body))


def seed_run(db, engine=None, **overrides):
    item = make_item(db, **overrides)
    run = service.create_run(db, item.id, engine=engine)
    return item, run


# ---------- 表与唯一约束 ----------


def test_three_tables_and_unique_constraints_exist():
    names = set(inspect(engine).get_table_names())
    assert {"mod_deferred_item", "mod_deferred_run", "mod_deferred_period"} <= names
    expected = {
        "mod_deferred_item": {"uq_deferred_item_code"},
        "mod_deferred_run": {"uq_deferred_run_input"},
        "mod_deferred_period": {"uq_deferred_period_month"},
    }
    for table, required in expected.items():
        actual = {item["name"] for item in inspect(engine).get_unique_constraints(table)}
        assert required <= actual


def test_unique_constraints_reject_duplicates(db):
    make_item(db)
    db.add(models.DeferredItem(**{**ITEM_BODY, "amount_incl_tax": Decimal("1.00"),
                                  "tax_rate": Decimal("0"), "months": 1,
                                  "lease_end_date": date(2026, 12, 31)}))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()

    item, run = seed_run(db)
    db.add(models.DeferredRun(item_id=run.item_id, input_hash=run.input_hash,
                              rule_version=run.rule_version, status="calculated"))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()

    period = db.query(models.DeferredPeriod).first()
    db.add(models.DeferredPeriod(run_id=period.run_id, period_month=period.period_month,
                                 sequence=period.sequence))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


# ---------- 项目主档 ----------


def test_item_create_strict_inputs_and_duplicate(db):
    for overrides in (
        {"project_code": "   "},
        {"name": "  "},
        {"project": ""},
        {"start_month": "2026-13"},
        {"start_month": "2026/01"},
        {"lease_end_date": "2026/12/31"},
        {"lease_end_date": "2026-12-31T00:00:00"},
        {"tax_rate": "1.5"},
        {"tax_rate": "-0.01"},
        {"months": 0},
        {"months": 601},
        {"unexpected": 1},
    ):
        with pytest.raises(ValidationError):
            schemas.DeferredItemCreate(**{**ITEM_BODY, **overrides})

    item = make_item(db)
    assert item.status == "draft" and item.status_label == "草稿"
    assert item.amount_net == Decimal("1200.00") and item.tax_amount == Decimal("156.00")
    assert item.amortization_end_month == "2026-12"
    with pytest.raises(service.DeferredConflictError, match="唯一约束冲突"):
        make_item(db, project_code=item.project_code)


def test_item_patch_recomputes_amounts_and_archive_is_read_only(db):
    item = make_item(db)
    patched = service.update_item(db, item.id, schemas.DeferredItemPatch(
        amount_incl_tax="113.00", tax_rate="0.13", name="合成改良项目（改）"))
    assert patched.amount_net == Decimal("100.00") and patched.tax_amount == Decimal("13.00")
    assert patched.name.endswith("（改）")
    with pytest.raises(service.DeferredValidationError):
        service.update_item(db, item.id, schemas.DeferredItemPatch())
    with pytest.raises(service.DeferredValidationError):
        service.update_item(db, item.id, schemas.DeferredItemPatch(name=None))

    archived = service.update_item(db, item.id, schemas.DeferredItemPatch(status="archived"))
    assert archived.status == "archived"
    with pytest.raises(service.DeferredConflictError):
        service.update_item(db, item.id, schemas.DeferredItemPatch(name="不可改"))
    with pytest.raises(service.DeferredConflictError):
        service.update_item(db, item.id, schemas.DeferredItemPatch(status="archived"))


def test_item_list_filters_and_pagination_limits(db):
    first = make_item(db)
    second = make_item(db, name="合成改良项目二", project="合成项目二")
    page = service.list_items(db, keyword="SYN-DEF", page=1, page_size=10)
    assert page.total == 2 and {row.id for row in page.items} == {first.id, second.id}
    assert service.list_items(db, status="archived").total == 0
    with pytest.raises(service.DeferredValidationError):
        service.list_items(db, status="unknown")
    with pytest.raises(service.DeferredValidationError):
        service.list_items(db, page_size=500)
    with pytest.raises(service.DeferredValidationError):
        service.list_items(db, page=0)


def test_delete_item_guards_history(db):
    item = make_item(db)
    service.delete_item(db, item.id)
    assert db.get(models.DeferredItem, item.id) is None

    item, _ = seed_run(db)
    with pytest.raises(service.DeferredConflictError, match="测算历史"):
        service.delete_item(db, item.id)
    assert db.get(models.DeferredItem, item.id) is not None


# ---------- 测算与幂等 ----------


def test_run_idempotent_and_periods_complete(db):
    item, run = seed_run(db)
    assert run.status == "calculated" and run.stale is False
    assert run.summary.amount_net == Decimal("1200.00")
    assert run.summary.monthly_amount == Decimal("100.00")
    assert run.summary.period_count == 12
    assert run.check_counts == {"ok": 9, "warning": 0, "failed": 0, "unknown": 0}
    assert run.all_checks_passed is True and run.has_warnings is False

    again = service.create_run(db, item.id)
    assert again.id == run.id
    assert count(db, models.DeferredRun) == 1

    rows = service.get_periods(db, run.id)
    assert [row.period_month for row in rows][:2] == ["2026-01", "2026-02"]
    assert rows[-1].period_month == "2026-12"
    assert rows[-1].balance == Decimal("0.00") and rows[-1].remaining_months == 0
    assert service.get_checks(db, run.id).all_passed is True


def test_input_hash_ignores_display_fields_and_tracks_economics(db):
    item, run = seed_run(db)
    first_hash = run.input_hash
    service.update_item(db, item.id, schemas.DeferredItemPatch(name="改名称", remark="改备注"))
    assert service.current_input_hash(db, item.id) == first_hash
    assert count(db, models.DeferredRun) == 1

    service.update_item(db, item.id, schemas.DeferredItemPatch(amount_incl_tax="2260.00"))
    assert service.current_input_hash(db, item.id) != first_hash
    listing = service.list_runs(db, item.id)
    assert listing[0].stale is True
    assert count(db, models.DeferredRun) == 1  # 旧快照保留，不被覆盖

    fresh = service.create_run(db, item.id)
    assert fresh.id != run.id and fresh.summary.amount_net == Decimal("2000.00")


def test_failed_engine_leaves_no_half_results(db):
    item = make_item(db)
    with pytest.raises(RuntimeError):
        service.create_run(db, item.id, engine=BoomEngine())
    assert count(db, models.DeferredRun) == 0
    assert count(db, models.DeferredPeriod) == 0


def test_bad_engine_structure_is_rejected_without_rows(db):
    item = make_item(db)
    with pytest.raises(service.DeferredCalculationError):
        service.create_run(db, item.id, engine=BadStructureEngine())
    assert count(db, models.DeferredRun) == 0
    assert count(db, models.DeferredPeriod) == 0


def test_archived_item_blocks_run(db):
    item = make_item(db)
    service.update_item(db, item.id, schemas.DeferredItemPatch(status="archived"))
    with pytest.raises(service.DeferredConflictError):
        service.create_run(db, item.id)


def test_rule_version_change_blocks_confirm(db, monkeypatch):
    _, run = seed_run(db)
    monkeypatch.setattr(service, "resolve_calculation_engine", lambda: VersionEngine())
    with pytest.raises(service.DeferredConflictError, match="规则版本"):
        service.confirm_run(db, run.id)


# ---------- 确认与风险 ----------


def test_confirm_state_machine_and_no_rewrite(db):
    item, run = seed_run(db)
    confirmed = service.confirm_run(db, run.id)
    assert confirmed.status == "confirmed" and confirmed.confirmed_at is not None
    with pytest.raises(service.DeferredConflictError, match="已确认"):
        service.confirm_run(db, run.id)

    other, other_run = seed_run(db)
    service.update_item(db, other.id, schemas.DeferredItemPatch(months=6))
    with pytest.raises(service.DeferredConflictError, match="已过期"):
        service.confirm_run(db, other_run.id)


def test_expiry_warning_requires_explicit_risk_note(db):
    item, run = make_item(db, lease_end_date="2026-06-30"), None
    run = service.create_run(db, item.id)
    assert run.has_warnings is True
    assert run.check_counts["warning"] == 1
    assert len(service.get_periods(db, run.id)) == 12  # 不缩短年限
    assert run.summary.expiry_after_lease is True

    with pytest.raises(service.DeferredConflictError, match="必须显式确认风险"):
        service.confirm_run(db, run.id)
    with pytest.raises(service.DeferredConflictError, match="风险说明为空"):
        service.confirm_run(db, run.id, schemas.DeferredConfirmIn(risk_acknowledged=True))
    done = service.confirm_run(
        db, run.id,
        schemas.DeferredConfirmIn(risk_acknowledged=True, risk_note="合成：经营层已知悉超出租期"))
    assert done.status == "confirmed"
    assert done.risk_acknowledged is True
    assert done.risk_note.startswith("合成：经营层已知悉")


def test_residual_warning_also_requires_ack(db):
    item = make_item(db, amount_incl_tax="1000.00", tax_rate="0", months=3)
    run = service.create_run(db, item.id)
    assert run.summary.rounding_difference == Decimal("0.01")
    assert run.has_warnings is True
    with pytest.raises(service.DeferredConflictError, match="必须显式确认风险"):
        service.confirm_run(db, run.id)
    confirmed = service.confirm_run(
        db, run.id,
        schemas.DeferredConfirmIn(risk_acknowledged=True, risk_note="接受末期尾差 0.01 元"))
    assert confirmed.status == "confirmed"


def test_failed_and_unknown_checks_block_confirm(db):
    _, run = seed_run(db)
    row = db.get(models.DeferredRun, run.id)
    row.checks = [{"key": "synthetic", "title": "合成差异", "status": "failed"}]
    db.commit()
    with pytest.raises(service.DeferredConflictError, match="校验未通过"):
        service.confirm_run(db, run.id)

    row.checks = [{"key": "synthetic", "title": "合成未知", "status": "unknown"}]
    db.commit()
    with pytest.raises(service.DeferredConflictError, match="校验未通过"):
        service.confirm_run(db, run.id)


def test_empty_checks_snapshot_is_not_treated_as_passed(db):
    _, run = seed_run(db)
    row = db.get(models.DeferredRun, run.id)
    row.checks = []
    db.commit()
    with pytest.raises(service.DeferredCalculationError, match="缺少校验结果"):
        service.get_run(db, run.id)


# ---------- 导出（服务层） ----------


def test_export_stale_gate_and_allow_stale(db):
    item, run = seed_run(db)
    file = service.export_run_xlsx(db, run.id)
    assert file.filename.startswith("长期待摊_") and file.filename_ascii.startswith("deferred_")
    assert file.filename_ascii.isascii() and file.filename_ascii.endswith(".xlsx")

    service.update_item(db, item.id, schemas.DeferredItemPatch(amount_incl_tax="2000.00"))
    with pytest.raises(service.DeferredConflictError, match="已过期"):
        service.export_run_xlsx(db, run.id)
    stale_file = service.export_run_xlsx(db, run.id, allow_stale=True)
    workbook = load_workbook(io.BytesIO(stale_file.content))
    title = next(iter(workbook["项目参数"].iter_rows(values_only=True)))[0]
    assert "【已过期】" in str(title)


def test_export_blocks_failed_checks(db):
    _, run = seed_run(db)
    row = db.get(models.DeferredRun, run.id)
    row.checks = [{"key": "synthetic", "title": "合成差异", "status": "failed"}]
    db.commit()
    with pytest.raises(service.DeferredConflictError, match="校验未通过"):
        service.export_run_xlsx(db, run.id)
    assert service.export_run_xlsx(db, run.id, allow_stale=True).content


# ---------- API 辅助 ----------


def sheet_value(sheet, label):
    for row in sheet.iter_rows(values_only=True):
        if row and row[0] == label:
            return row[1]
    return None


def sheet_rows(sheet, first_header):
    rows = list(sheet.iter_rows(values_only=True))
    header = next(index for index, row in enumerate(rows) if row and row[0] == first_header)
    return [row for row in rows[header + 1:] if row and row[0] is not None]


def api_item(client, project_code="SYN-API-001", **overrides):
    response = client.post(f"{API}/items", json={**ITEM_BODY, "project_code": project_code,
                                                 **overrides})
    assert response.status_code == 201, response.text
    return response.json()


def api_run(client, item_id):
    response = client.post(f"{API}/items/{item_id}/runs")
    assert response.status_code == 201, response.text
    return response.json()


# ---------- 真实 API 集成 ----------


def test_api_full_chain_exact_case(client):
    item = api_item(client)
    assert item["amount_net"] == "1200.00" and item["amortization_end_month"] == "2026-12"
    listing = client.get(f"{API}/items", params={"page": 1, "page_size": 200}).json()
    assert listing["total"] == 1 and listing["items"][0]["id"] == item["id"]

    run = api_run(client, item["id"])
    assert run["status"] == "calculated" and run["all_checks_passed"] is True
    assert run["summary"]["monthly_amount"] == "100.00"
    periods = client.get(f"{API}/runs/{run['id']}/periods").json()
    assert len(periods) == 12
    assert periods[0]["period_month"] == "2026-01" and periods[-1]["remaining_months"] == 0

    again = api_run(client, item["id"])
    assert again["id"] == run["id"]
    assert len(client.get(f"{API}/items/{item['id']}/runs").json()) == 1

    confirmed = client.post(f"{API}/runs/{run['id']}/confirm")
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["status"] == "confirmed"
    assert client.post(f"{API}/runs/{run['id']}/confirm").status_code == 409

    checks = client.get(f"{API}/runs/{run['id']}/checks").json()
    assert checks["all_passed"] is True and checks["has_warnings"] is False

    exported = client.get(f"{API}/runs/{run['id']}/export.xlsx")
    assert exported.status_code == 200, exported.text
    disposition = exported.headers["content-disposition"]
    assert "filename=" in disposition and "filename*=UTF-8''" in disposition

    workbook = load_workbook(io.BytesIO(exported.content))
    assert workbook.sheetnames == ["项目参数", "月度摊销", "校验"]
    assert Decimal(str(sheet_value(workbook["项目参数"], "含税金额"))) == Decimal("1356.00")
    assert Decimal(str(sheet_value(workbook["项目参数"], "净额（含税÷(1+税率)）"))) == Decimal("1200.00")
    assert Decimal(str(sheet_value(workbook["项目参数"], "税额（含税−净额）"))) == Decimal("156.00")
    assert sheet_value(workbook["项目参数"], "摊销月数") == 12
    assert sheet_value(workbook["项目参数"], "摊销到期月") == "2026-12"
    assert sheet_value(workbook["项目参数"], "风险已确认") == "否"

    rows = sheet_rows(workbook["月度摊销"], "月份")
    data_rows = [row for row in rows if isinstance(row[1], int)]
    assert len(data_rows) == len(periods)
    for row, expected in zip(data_rows, periods):
        assert row[0] == expected["period_month"]
        assert row[1] == expected["sequence"]
        assert Decimal(str(row[2])) == Decimal(expected["monthly_amount"])
        assert Decimal(str(row[3])) == Decimal(expected["cumulative"])
        assert Decimal(str(row[4])) == Decimal(expected["balance"])
        assert row[5] == expected["remaining_months"]
    assert Decimal(str(rows[-1][2])) == Decimal("1200.00")

    check_rows = sheet_rows(workbook["校验"], "检查项")
    assert len(check_rows) == len(run["checks"])
    assert {row[1] for row in check_rows} == {"通过"}
    assert all(row[0] for row in check_rows)
    assert sheet_value(workbook["项目参数"], "项目编码") == "SYN-API-001"


def test_api_warning_requires_ack_then_export_discloses(client):
    item = api_item(client, "SYN-API-WARN", amount_incl_tax="1000.00", tax_rate="0",
                    months=3, lease_end_date="2026-12-31")
    run = api_run(client, item["id"])
    assert run["has_warnings"] is True and run["all_checks_passed"] is True
    assert run["summary"]["rounding_difference"] == "0.01"

    blocked = client.post(f"{API}/runs/{run['id']}/confirm", json={})
    assert blocked.status_code == 409
    assert "风险" in blocked.json()["detail"]
    blank_note = client.post(f"{API}/runs/{run['id']}/confirm",
                             json={"risk_acknowledged": True, "risk_note": "  "})
    assert blank_note.status_code == 409
    assert "风险说明" in blank_note.json()["detail"]

    done = client.post(f"{API}/runs/{run['id']}/confirm",
                       json={"risk_acknowledged": True, "risk_note": "接受末期尾差"})
    assert done.status_code == 200, done.text
    assert done.json()["risk_note"] == "接受末期尾差"

    exported = client.get(f"{API}/runs/{run['id']}/export.xlsx")
    assert exported.status_code == 200
    workbook = load_workbook(io.BytesIO(exported.content))
    assert sheet_value(workbook["项目参数"], "风险已确认") == "是"
    assert sheet_value(workbook["项目参数"], "风险说明") == "接受末期尾差"
    rows = sheet_rows(workbook["月度摊销"], "月份")
    data_rows = [row for row in rows if isinstance(row[1], int)]
    assert Decimal(str(data_rows[-1][4])) == Decimal("0.01")
    assert Decimal(str(rows[-1][4])) == Decimal("0.01")
    statuses = {row[1] for row in sheet_rows(workbook["校验"], "检查项")}
    assert "警告" in statuses

    item_detail = client.get(f"{API}/items/{item['id']}").json()
    assert item_detail["latest_run_status"] == "confirmed"


def test_api_expiry_warning_does_not_shorten_months(client):
    item = api_item(client, "SYN-API-EXPIRY", lease_end_date="2026-06-30")
    run = api_run(client, item["id"])
    assert len(client.get(f"{API}/runs/{run['id']}/periods").json()) == 12
    checks = {row["key"]: row for row in run["checks"]}
    assert checks["lease_expiry_coverage"]["status"] == "warning"
    assert "不自动缩短" in checks["lease_expiry_coverage"]["message"]


def test_api_stale_blocks_confirm_and_export(client):
    item = api_item(client, "SYN-API-STALE")
    run = api_run(client, item["id"])
    patched = client.patch(f"{API}/items/{item['id']}", json={"months": 6})
    assert patched.status_code == 200, patched.text

    assert client.get(f"{API}/runs/{run['id']}").json()["stale"] is True
    confirm = client.post(f"{API}/runs/{run['id']}/confirm")
    assert confirm.status_code == 409 and "已过期" in confirm.json()["detail"]
    assert client.get(f"{API}/runs/{run['id']}/export.xlsx").status_code == 409
    allowed = client.get(f"{API}/runs/{run['id']}/export.xlsx",
                         params={"allow_stale": "true"})
    assert allowed.status_code == 200
    assert client.delete(f"{API}/items/{item['id']}").status_code == 409


def test_api_archived_item_is_read_only(client):
    item = api_item(client, "SYN-API-ARCH")
    archived = client.patch(f"{API}/items/{item['id']}", json={"status": "archived"})
    assert archived.status_code == 200 and archived.json()["status"] == "archived"
    assert client.patch(f"{API}/items/{item['id']}", json={"name": "不可改"}).status_code == 409
    assert client.post(f"{API}/items/{item['id']}/runs").status_code == 409


def test_api_error_mapping(client):
    assert client.get(f"{API}/items/999999").status_code == 404
    assert client.get(f"{API}/runs/999999").status_code == 404
    assert client.post(f"{API}/runs/999999/confirm").status_code == 404

    invalid = client.post(f"{API}/items", json={**ITEM_BODY, "months": 0})
    assert invalid.status_code == 422
    assert client.get(f"{API}/items", params={"status": "weird"}).status_code == 400

    item = api_item(client, "SYN-API-DUP")
    duplicate = client.post(f"{API}/items",
                            json={**ITEM_BODY, "project_code": item["project_code"]})
    assert duplicate.status_code == 409
    assert "唯一约束" in duplicate.json()["detail"]


def test_api_engine_unavailable_maps_503(client, monkeypatch):
    item = api_item(client, "SYN-API-503")

    def unavailable(*args, **kwargs):
        raise service.DeferredEngineUnavailable("计算引擎尚未接入（测试注入）")

    monkeypatch.setattr(service, "create_run", unavailable)
    response = client.post(f"{API}/items/{item['id']}/runs")
    assert response.status_code == 503
    assert "尚未接入" in response.json()["detail"]


def test_api_export_formula_injection_is_neutralized(client):
    item = api_item(client, "SYN-API-SAFE", name="=SUM(A1:A2)", project="+合成项目")
    run = api_run(client, item["id"])
    exported = client.get(f"{API}/runs/{run['id']}/export.xlsx")
    assert exported.status_code == 200
    workbook = load_workbook(io.BytesIO(exported.content))
    name_cell = sheet_value(workbook["项目参数"], "项目名称")
    project_cell = sheet_value(workbook["项目参数"], "所属项目")
    assert isinstance(name_cell, str) and name_cell.startswith("'=")
    assert isinstance(project_cell, str) and project_cell.startswith("'+")


def test_api_summary_and_readonly_tool(client):
    from app.agents.tools import TOOL_SPECS

    item = api_item(client, "SYN-API-SUM")
    run = api_run(client, item["id"])
    summary = client.get(f"{API}/summary").json()
    assert summary["total_items"] == 1 and summary["draft_count"] == 1
    assert summary["items"][0]["latest_run_id"] == run["id"]
    assert summary["items"][0]["monthly_amount"] == "100.00"

    assert "deferred_summary" in TOOL_SPECS
    spec = TOOL_SPECS["deferred_summary"]
    assert spec["readonly"] is True
    result = spec["func"](item_id=item["id"])
    assert result["ok"] is True and result["total_items"] == 1
    assert result["items"][0]["latest_run_id"] == run["id"]
    missing = spec["func"](item_id=999999)
    assert missing["ok"] is False and "不存在" in missing["error"]
    assert spec["func"](item_id="abc")["ok"] is False


def test_manifest_loaded_with_summary_tool():
    from app.modules import get_loaded_modules
    from app.tool_schema import params_errors

    loaded = {module.name: module for module in get_loaded_modules()}
    assert "finance_deferred" in loaded
    manifest = loaded["finance_deferred"]
    assert manifest.title == "长期待摊"
    assert manifest.prefix == "/api/finance/deferred"
    assert manifest.validate() == []
    tool = manifest.agent_tools[0]
    assert tool["name"] == "deferred_summary"
    assert tool["readonly"] is True and tool["required"] == []
    assert params_errors("deferred_summary", tool["params"], tool["required"]) == []
    assert "finance_specialist" in manifest.preset_experts
