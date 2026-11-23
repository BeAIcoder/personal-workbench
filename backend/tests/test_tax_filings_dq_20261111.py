# -*- coding: utf-8 -*-
"""2026-11-11 审计修复：四税种申报防重/受控更正、计税准入门禁、个税标准、DQ 口径。

覆盖台账：
- R11-008 增值税/个税/印花税/残保金按「纳税人+期间」应用层 409 + 数据库唯一索引 + 受控更正；
- R11-019 六个计税入口（增值税一般/小规模、个税、印花、残保金、企业所得税）首行
  require_tax_scope，4 个 blocked 税种继续 fail-closed；
- R11-020 个税住房贷款利息/住房租金拆分、继续教育学历/职业资格拆分（官方依据见断言注释）；
- R11-084 DQ 税种申报状态检查不再是死检查（白名单，含历史 failed）；
- R11-007 DQ 财务数据为空时区分「未导入」与真实勾稽差异；
- R11-034 DQ 最新 ok 快照早于管线 report_data.json mtime → 显式 issue。

全部使用临时 SQLite（conftest client 隔离）与合成数据；不触真实 backend/data、外部 HTTP 与计划任务。
"""
from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timedelta

import pytest
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.exc import IntegrityError

from app.database import SessionLocal
from app.modules.finance_dsf import service as dsf_service
from app.modules.finance_dsf.models import DsfFiling
from app.modules.finance_iit import service as iit_service
from app.modules.finance_iit.models import IitFiling
from app.modules.finance_stamp_duty import service as stamp_service
from app.modules.finance_stamp_duty.models import StampDutyFiling
from app.modules.finance_tax.config import tax_scope
from app.modules.finance_vat import service as vat_service
from app.modules.finance_vat.models import VatFiling

VAT_P = "/api/finance/vat"
IIT_P = "/api/finance/iit"
SD_P = "/api/finance/stamp-duty"
DSF_P = "/api/finance/dsf"

NAME = "合成纳税人甲"
AS_OF = "2026-11-01"


# ---------- 载荷 ----------

def _vat_payload(**over):
    payload = {
        "period": "2026-09", "taxpayer_type": "general", "taxpayer_name": NAME,
        "output_items": [{"business_type": "销售货物", "amount": 100_000.0, "rate": 0.13}],
        "input_vat": 1_000.0,
    }
    payload.update(over)
    return payload


def _iit_payload(**over):
    payload = {
        "year": "2026", "taxpayer_name": NAME,
        "rows": [{"month": 1, "name": "员工甲", "salary": 10_000.0}],
    }
    payload.update(over)
    return payload


def _stamp_payload(**over):
    payload = {
        "period": "2026-09", "taxpayer_name": NAME,
        "items": [{"tax_item": "买卖合同（动产买卖）", "amount": 100_000.0}],
    }
    payload.update(over)
    return payload


def _dsf_payload(**over):
    payload = {
        "year": "2026", "taxpayer_name": NAME,
        "total_employees": 100, "total_salary": 10_000_000.0, "disabled_employees": 0,
    }
    payload.update(over)
    return payload


CASES = [
    pytest.param(VAT_P, VatFiling, _vat_payload, "period", "2026-10", id="vat"),
    pytest.param(IIT_P, IitFiling, _iit_payload, "year", "2027", id="iit"),
    pytest.param(SD_P, StampDutyFiling, _stamp_payload, "period", "2026-10", id="stamp"),
    pytest.param(DSF_P, DsfFiling, _dsf_payload, "year", "2027", id="dsf"),
]


# ---------- R11-008：同期间 409 ----------

def test_vat_duplicate_period_409(client):
    assert client.post(f"{VAT_P}/filings", json=_vat_payload()).status_code == 201
    dup = client.post(f"{VAT_P}/filings", json=_vat_payload())
    assert dup.status_code == 409, dup.text
    detail = dup.json()["detail"]
    assert "已存在" in detail and "更正" in detail
    assert client.post(f"{VAT_P}/filings", json=_vat_payload(taxpayer_name="合成纳税人乙")).status_code == 201
    assert client.post(f"{VAT_P}/filings", json=_vat_payload(period="2026-10")).status_code == 201
    assert client.get(f"{VAT_P}/filings").json()["total"] == 3


def test_iit_duplicate_period_409(client):
    assert client.post(f"{IIT_P}/filings", json=_iit_payload()).status_code == 201
    dup = client.post(f"{IIT_P}/filings", json=_iit_payload())
    assert dup.status_code == 409, dup.text
    assert "已存在" in dup.json()["detail"]
    assert client.post(f"{IIT_P}/filings", json=_iit_payload(taxpayer_name="合成纳税人乙")).status_code == 201
    assert client.post(f"{IIT_P}/filings", json=_iit_payload(year="2027")).status_code == 201
    assert client.get(f"{IIT_P}/filings").json()["total"] == 3


def test_stamp_duplicate_period_409(client):
    assert client.post(f"{SD_P}/filings", json=_stamp_payload()).status_code == 201
    dup = client.post(f"{SD_P}/filings", json=_stamp_payload())
    assert dup.status_code == 409, dup.text
    assert "已存在" in dup.json()["detail"]
    assert client.post(f"{SD_P}/filings", json=_stamp_payload(taxpayer_name="合成纳税人乙")).status_code == 201
    assert client.post(f"{SD_P}/filings", json=_stamp_payload(period="2026-10")).status_code == 201
    assert client.get(f"{SD_P}/filings").json()["total"] == 3


def test_dsf_duplicate_period_409(client):
    assert client.post(f"{DSF_P}/filings", json=_dsf_payload()).status_code == 201
    dup = client.post(f"{DSF_P}/filings", json=_dsf_payload())
    assert dup.status_code == 409, dup.text
    assert "已存在" in dup.json()["detail"]
    assert client.post(f"{DSF_P}/filings", json=_dsf_payload(taxpayer_name="合成纳税人乙")).status_code == 201
    assert client.post(f"{DSF_P}/filings", json=_dsf_payload(year="2027")).status_code == 201
    assert client.get(f"{DSF_P}/filings").json()["total"] == 3


def test_blank_taxpayer_name_is_same_taxpayer(client):
    """空白纳税人名归一为「未命名」，否则 SQLite 侧同一申报可用空格绕过防重。"""
    assert client.post(f"{VAT_P}/filings", json=_vat_payload(taxpayer_name="  ")).status_code == 201
    dup = client.post(f"{VAT_P}/filings", json=_vat_payload(taxpayer_name=""))
    assert dup.status_code == 409, dup.text
    listed = client.get(f"{VAT_P}/filings").json()["items"]
    assert listed[0]["taxpayer_name"] == ""


# ---------- 数据库唯一索引 + SQLite NULL 语义 ----------

def _inspect_table(table: str) -> tuple[list[list[str]], dict[str, bool]]:
    with SessionLocal() as db:
        insp = sa_inspect(db.get_bind())
        uniques = [list(i["column_names"]) for i in insp.get_indexes(table) if i.get("unique")]
        nullables = {c["name"]: bool(c["nullable"]) for c in insp.get_columns(table)}
    return uniques, nullables


@pytest.mark.parametrize("table,key_columns", [
    ("mod_vat_filing", ["taxpayer_name", "period"]),
    ("mod_iit_filing", ["taxpayer_name", "year"]),
    ("mod_stamp_duty_filing", ["taxpayer_name", "period"]),
    ("mod_dsf_filing", ["taxpayer_name", "year"]),
])
def test_filing_table_unique_index_and_not_null(client, table, key_columns):
    """唯一性必须落在数据库层（SQLite NULL 在唯一索引里互不相等，故键列必须 NOT NULL）。"""
    uniques, nullables = _inspect_table(table)
    assert key_columns in uniques, (table, uniques)
    for column in key_columns:
        assert nullables[column] is False, (table, column)


def _filing_row(model, key: str, value, **over):
    """按模型构造一条申报行（四个模块的输入字段名不同：inputs/rows/items）。"""
    row = {
        "taxpayer_name": NAME,
        "calc_result": {},
        "status": "calculated",
        key: value,
    }
    if model is IitFiling:
        row["rows"] = []
    elif model is StampDutyFiling:
        row["items"] = []
        row["half_discount"] = False
    else:
        row["inputs"] = {}
    if model is VatFiling:
        row["taxpayer_type"] = "general"
    row.update(over)
    return model(**row)


@pytest.mark.parametrize("model,key,value", [
    (VatFiling, "period", "2026-09"),
    (IitFiling, "year", "2026"),
    (StampDutyFiling, "period", "2026-09"),
    (DsfFiling, "year", "2026"),
])
def test_db_unique_index_rejects_duplicate_rows(client, model, key, value):
    with SessionLocal() as db:
        db.add(_filing_row(model, key, value))
        db.commit()
    with SessionLocal() as db:
        db.add(_filing_row(model, key, value))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()


@pytest.mark.parametrize("model,key", [
    (VatFiling, "period"), (IitFiling, "year"),
    (StampDutyFiling, "period"), (DsfFiling, "year"),
])
def test_key_columns_reject_null(client, model, key):
    """SQLite 唯一索引把 NULL 视为互不相等，键列必须 NOT NULL 才能保证防重。"""
    with SessionLocal() as db:
        db.add(_filing_row(model, key, None))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()


def test_legacy_table_gets_unique_index_idempotently(tmp_path):
    """历史库没有表级约束：宿主的 _ensure_indexes_on 必须能给已存在的表补唯一索引，且二次执行无动作。"""
    from sqlalchemy import create_engine
    from app.database import Base
    from app.main import _ensure_indexes_on

    bind = create_engine(f"sqlite:///{(tmp_path / 'legacy.db').as_posix()}")
    with bind.begin() as conn:
        conn.exec_driver_sql(
            "CREATE TABLE mod_vat_filing ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, period VARCHAR(7) NOT NULL, "
            "taxpayer_type VARCHAR(20), taxpayer_name VARCHAR(200) NOT NULL, "
            "inputs JSON, calc_result JSON, status VARCHAR(20), export_path VARCHAR(500), "
            "error TEXT, created_at DATETIME, updated_at DATETIME)"
        )
    assert not [i for i in sa_inspect(bind).get_indexes("mod_vat_filing") if i.get("unique")]

    created = _ensure_indexes_on(bind, Base.metadata)
    assert "mod_vat_filing.uq_vat_filing_taxpayer_period" in created
    uniques = [i["column_names"] for i in sa_inspect(bind).get_indexes("mod_vat_filing") if i.get("unique")]
    assert ["taxpayer_name", "period"] in uniques

    assert _ensure_indexes_on(bind, Base.metadata) == []  # 幂等：第二次无可补索引
    with pytest.raises(IntegrityError):
        with bind.begin() as conn:
            conn.exec_driver_sql(
                "INSERT INTO mod_vat_filing (period, taxpayer_name, status) "
                "VALUES ('2026-09', '甲', 'calculated'), ('2026-09', '甲', 'calculated')"
            )


# ---------- 受控更正路径 ----------

def _set_status(model, filing_id: int, status: str) -> None:
    with SessionLocal() as db:
        row = db.get(model, filing_id)
        row.status = status
        db.commit()


@pytest.mark.parametrize("prefix,model,payload,key,next_period", CASES)
def test_revise_updates_same_record(client, prefix, model, payload, key, next_period):
    created = client.post(f"{prefix}/filings", json=payload())
    assert created.status_code == 201, created.text
    fid = created.json()["id"]
    revised = client.put(f"{prefix}/filings/{fid}", json=payload())
    assert revised.status_code == 200, revised.text
    assert revised.json()["id"] == fid
    assert revised.json()["status"] == "calculated"
    assert client.get(f"{prefix}/filings").json()["total"] == 1


def test_vat_revise_recalculates(client):
    created = client.post(f"{VAT_P}/filings", json=_vat_payload())
    fid = created.json()["id"]
    revised = client.put(f"{VAT_P}/filings/{fid}", json=_vat_payload(input_vat=5_000.0))
    assert revised.status_code == 200, revised.text
    assert revised.json()["calc_result"]["input_vat"] == 5_000.0
    assert revised.json()["calc_result"]["payable"] == pytest.approx(13_000.0 - 5_000.0)


@pytest.mark.parametrize("prefix,model,payload,key,next_period", CASES)
def test_revise_rejects_identity_change(client, prefix, model, payload, key, next_period):
    created = client.post(f"{prefix}/filings", json=payload())
    fid = created.json()["id"]
    other_name = client.put(f"{prefix}/filings/{fid}", json=payload(taxpayer_name="合成纳税人乙"))
    assert other_name.status_code == 400, other_name.text
    assert "不允许更换" in other_name.json()["detail"]
    other_period = client.put(f"{prefix}/filings/{fid}", json=payload(**{key: next_period}))
    assert other_period.status_code == 400, other_period.text


@pytest.mark.parametrize("prefix,model,payload,key,next_period", CASES)
def test_revise_blocked_after_export(client, prefix, model, payload, key, next_period):
    created = client.post(f"{prefix}/filings", json=payload())
    fid = created.json()["id"]
    exported = client.post(f"{prefix}/filings/{fid}/export")
    assert exported.status_code == 200, exported.text
    blocked = client.put(f"{prefix}/filings/{fid}", json=payload())
    assert blocked.status_code == 409, blocked.text
    assert "已导出" in blocked.json()["detail"]
    assert client.get(f"{prefix}/filings/{fid}").json()["status"] == "exported"


@pytest.mark.parametrize("prefix,model,payload,key,next_period", CASES)
def test_revise_blocked_when_submitted(client, prefix, model, payload, key, next_period):
    created = client.post(f"{prefix}/filings", json=payload())
    fid = created.json()["id"]
    _set_status(model, fid, "submitted")
    blocked = client.put(f"{prefix}/filings/{fid}", json=payload())
    assert blocked.status_code == 409, blocked.text
    assert "已提交" in blocked.json()["detail"]
    assert client.get(f"{prefix}/filings/{fid}").json()["status"] == "submitted"


def test_revise_missing_record_404(client):
    assert client.put(f"{VAT_P}/filings/999999", json=_vat_payload()).status_code == 404


def test_duplicate_conflict_after_export_mentions_existing(client):
    """已导出记录被重复 POST：409 提示指向既有记录，并说明删记录会删底稿。"""
    created = client.post(f"{VAT_P}/filings", json=_vat_payload())
    fid = created.json()["id"]
    assert client.post(f"{VAT_P}/filings/{fid}/export").status_code == 200
    dup = client.post(f"{VAT_P}/filings", json=_vat_payload())
    assert dup.status_code == 409, dup.text
    detail = dup.json()["detail"]
    assert f"id={fid}" in detail
    assert "底稿" in detail and "一并删除" in detail


# ---------- 复审 I-3：已提交不可删除，且底稿必须保留 ----------

@pytest.mark.parametrize("prefix,model,payload,key,next_period", CASES)
def test_delete_submitted_409_and_artifact_kept(client, prefix, model, payload, key, next_period):
    created = client.post(f"{prefix}/filings", json=payload())
    assert created.status_code == 201, created.text
    fid = created.json()["id"]
    assert client.post(f"{prefix}/filings/{fid}/export").status_code == 200
    _set_status(model, fid, "submitted")

    blocked = client.delete(f"{prefix}/filings/{fid}")
    assert blocked.status_code == 409, blocked.text
    detail = blocked.json()["detail"]
    assert "已提交" in detail and "删除" in detail and "更正申报" in detail

    kept = client.get(f"{prefix}/filings/{fid}")
    assert kept.status_code == 200, kept.text
    assert kept.json()["status"] == "submitted"
    assert kept.json()["has_export"] is True
    download = client.get(f"{prefix}/filings/{fid}/download")
    assert download.status_code == 200 and len(download.content) > 1000


# ---------- 复审 M-5.5：POST 的 IntegrityError 并发兜底 ----------

@pytest.mark.parametrize("prefix,model,payload,key,next_period", CASES)
def test_post_integrity_error_falls_back_to_409(client, monkeypatch, prefix, model,
                                                payload, key, next_period):
    from sqlalchemy.orm import Session as OrmSession

    def boom(self):
        raise IntegrityError("INSERT INTO filing", {}, Exception("UNIQUE constraint failed"))

    monkeypatch.setattr(OrmSession, "commit", boom)
    try:
        resp = client.post(f"{prefix}/filings", json=payload())
    finally:
        monkeypatch.undo()
    assert resp.status_code == 409, resp.text
    assert "已存在" in resp.json()["detail"] and "刷新" in resp.json()["detail"]
    with SessionLocal() as db:
        assert db.query(model).count() == 0


# ---------- 复审 M-5.7 / M-2：历史重复键跳过唯一索引（只告警、不删数据） ----------

def _create_legacy_vat_table(bind, rows):
    with bind.begin() as conn:
        conn.exec_driver_sql(
            "CREATE TABLE mod_vat_filing ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, period VARCHAR(7) NOT NULL, "
            "taxpayer_type VARCHAR(20), taxpayer_name VARCHAR(200) NOT NULL, "
            "inputs JSON, calc_result JSON, status VARCHAR(20), export_path VARCHAR(500), "
            "error TEXT, created_at DATETIME, updated_at DATETIME)"
        )
        for period, name in rows:
            conn.exec_driver_sql(
                "INSERT INTO mod_vat_filing (period, taxpayer_name, status) "
                "VALUES (?, ?, 'calculated')", (period, name),
            )


def test_legacy_duplicate_rows_skip_unique_index_with_warning(tmp_path, caplog):
    import logging

    from sqlalchemy import create_engine
    from app.database import Base
    from app.main import _ensure_indexes_on

    bind = create_engine(f"sqlite:///{(tmp_path / 'legacy_dup.db').as_posix()}")
    _create_legacy_vat_table(bind, rows=[("2026-09", "合成纳税人甲"),
                                         ("2026-09", "合成纳税人甲")])
    with caplog.at_level(logging.WARNING, logger="app.main"):
        created = _ensure_indexes_on(bind, Base.metadata)

    assert "mod_vat_filing.uq_vat_filing_taxpayer_period" not in created
    uniques = [i for i in sa_inspect(bind).get_indexes("mod_vat_filing") if i.get("unique")]
    assert uniques == []
    with bind.connect() as conn:
        assert conn.exec_driver_sql("SELECT COUNT(*) FROM mod_vat_filing").scalar() == 2
    assert any("已跳过唯一索引" in rec.getMessage() for rec in caplog.records)


# ---------- 复审 M-3：印花税按季申报必须季末月，防同季不同月绕过防重 ----------

def test_stamp_quarter_requires_quarter_end_month(client):
    bad = client.post(f"{SD_P}/filings", json=_stamp_payload(
        period="2026-07", filing_period="quarter"))
    assert bad.status_code == 400, bad.text
    assert "季末月" in bad.json()["detail"]
    assert client.get(f"{SD_P}/filings").json()["total"] == 0


def test_stamp_quarter_dedup_and_monthly_still_allowed(client):
    first = client.post(f"{SD_P}/filings", json=_stamp_payload(
        period="2026-09", filing_period="quarter"))
    assert first.status_code == 201, first.text
    assert first.json()["calc_result"]["filing_period"] == "quarter"

    dup = client.post(f"{SD_P}/filings", json=_stamp_payload(
        period="2026-09", filing_period="quarter"))
    assert dup.status_code == 409, dup.text

    monthly = client.post(f"{SD_P}/filings", json=_stamp_payload(period="2026-07"))
    assert monthly.status_code == 201, monthly.text
    assert monthly.json()["calc_result"]["filing_period"] == "month"


def test_stamp_quarter_revise_validates_period(client):
    created = client.post(f"{SD_P}/filings", json=_stamp_payload(
        period="2026-09", filing_period="quarter"))
    fid = created.json()["id"]
    bad = client.put(f"{SD_P}/filings/{fid}", json=_stamp_payload(
        period="2026-07", filing_period="quarter"))
    assert bad.status_code == 400, bad.text
    assert "季末月" in bad.json()["detail"]


# ---------- R11-019：五个计税入口接入准入门禁 ----------

def _block(monkeypatch, key: str):
    monkeypatch.setitem(
        tax_scope.TAX_SCOPE_REGISTRY, key,
        replace(tax_scope.TAX_SCOPE_REGISTRY[key], status="blocked", reason="测试封禁"),
    )


def _restore(monkeypatch, key: str, scope):
    monkeypatch.setitem(tax_scope.TAX_SCOPE_REGISTRY, key, scope)


def test_six_calc_entries_call_require_tax_scope(monkeypatch):
    from app.modules.finance_tax.services.corporate_income_tax_calculator import (
        CorporateIncomeTaxCalculator, EnterpriseTaxType,
    )

    cases = [
        ("value_added_tax", lambda: vat_service.calc_general(
            "2026-09", [{"business_type": "销售货物", "amount": 100.0, "rate": 0.13}], 0.0)),
        ("value_added_tax", lambda: vat_service.calc_small_scale("2026-09", 400_000.0, 0.01, "quarter")),
        ("individual_income_tax", lambda: iit_service.calc_filing(
            "2026", [{"month": 1, "name": "员工甲", "salary": 10_000.0}])),
        ("stamp_duty", lambda: stamp_service.calc_filing(
            "2026-09", [{"tax_item": "买卖合同（动产买卖）", "amount": 1000.0}], False)),
        ("disability_fund", lambda: dsf_service.calc_dsf("2026", 100, 10_000_000.0, 0)),
        ("corporate_income_tax", lambda: CorporateIncomeTaxCalculator.calculate_tax_amount(
            20.0, EnterpriseTaxType.STANDARD, tax_year="2026")),
    ]
    for key, call in cases:
        original = tax_scope.TAX_SCOPE_REGISTRY[key]
        _block(monkeypatch, key)
        try:
            with pytest.raises(ValueError) as exc:
                call()
            assert "尚未准入" in str(exc.value), (key, str(exc.value))
        finally:
            _restore(monkeypatch, key, original)


def test_scope_guard_runs_before_payload_validation(monkeypatch):
    """门禁必须是首行：封禁时即便参数也非法，也只报「尚未准入」。"""
    original = tax_scope.TAX_SCOPE_REGISTRY["value_added_tax"]
    _block(monkeypatch, "value_added_tax")
    try:
        with pytest.raises(ValueError) as exc:
            vat_service.calc_general("2026-09", [], 0.0)
        assert "尚未准入" in str(exc.value)
        assert "销项明细不能为空" not in str(exc.value)
    finally:
        _restore(monkeypatch, "value_added_tax", original)


def test_router_returns_400_when_scope_blocked(client, monkeypatch):
    original = tax_scope.TAX_SCOPE_REGISTRY["value_added_tax"]
    _block(monkeypatch, "value_added_tax")
    try:
        resp = client.post(f"{VAT_P}/filings", json=_vat_payload())
    finally:
        _restore(monkeypatch, "value_added_tax", original)
    assert resp.status_code == 400, resp.text
    assert "尚未准入" in resp.json()["detail"]


# ---------- CIT 计税入口同样接入准入门禁（2026-11-11 集成收尾） ----------

CIT_P = "/api/finance/tax"


def _cit_enterprise(client, tax_number: str = "91330100MA2CIT01X") -> int:
    resp = client.post(f"{CIT_P}/enterprises/", json={
        "name": "合成所得税企业", "tax_number": tax_number,
        "industry_type": "一般行业", "employee_count": 400,
        "total_assets": 8000.0, "is_restricted_industry": False,
    })
    assert resp.status_code in (200, 201), resp.text
    return resp.json()["id"]


def _cit_calc_payload(enterprise_id: int) -> dict:
    return {
        "enterprise_id": enterprise_id,
        "revenue": 500.0, "total_costs": 0.0, "total_expenses": 0.0,
        "employee_count": 400, "total_assets": 8000.0,
        "year": "2026",
    }


def test_cit_engine_blocked_does_not_calculate(monkeypatch):
    """完整计算入口封禁后必须在计算前抛中文未准入，而不是返回一份结果。"""
    from app.modules.finance_tax.services.corporate_income_tax_calculator import (
        CorporateIncomeTaxCalculator, EnterpriseInfo, TaxAdjustmentInput,
    )

    original = tax_scope.TAX_SCOPE_REGISTRY["corporate_income_tax"]
    _block(monkeypatch, "corporate_income_tax")
    try:
        with pytest.raises(ValueError) as exc:
            CorporateIncomeTaxCalculator.calculate(
                accounting_profit_wan=0.0, revenue_wan=0.0,
                adjustments=TaxAdjustmentInput(), total_salary_wan=0.0,
                enterprise_info=EnterpriseInfo())
        assert "尚未准入" in str(exc.value)
    finally:
        _restore(monkeypatch, "corporate_income_tax", original)


def test_cit_wrapper_blocked_before_zero_income_shortcut(monkeypatch):
    """包装函数对零所得有提前返回：门禁必须在其之前，封禁时零所得也报未准入。"""
    from app.modules.finance_tax.routers.tax_returns import calculate_tax

    original = tax_scope.TAX_SCOPE_REGISTRY["corporate_income_tax"]
    _block(monkeypatch, "corporate_income_tax")
    try:
        with pytest.raises(ValueError) as exc:
            calculate_tax({"total_revenue": 0.0, "other_income": 0.0,
                           "total_costs": 0.0, "total_expenses": 0.0}, None)
        assert "尚未准入" in str(exc.value)
    finally:
        _restore(monkeypatch, "corporate_income_tax", original)


def test_cit_calculate_endpoint_400_when_blocked(client, monkeypatch):
    ent_id = _cit_enterprise(client)
    original = tax_scope.TAX_SCOPE_REGISTRY["corporate_income_tax"]
    _block(monkeypatch, "corporate_income_tax")
    try:
        resp = client.post(f"{CIT_P}/tax-returns/calculate", json=_cit_calc_payload(ent_id))
    finally:
        _restore(monkeypatch, "corporate_income_tax", original)
    assert resp.status_code == 400, resp.text
    assert "尚未准入" in resp.json()["detail"]


def test_cit_save_blocked_400_and_persists_nothing(client, monkeypatch):
    from app.modules.finance_tax.models import TaxReturn

    ent_id = _cit_enterprise(client, "91330100MA2CIT02X")
    original = tax_scope.TAX_SCOPE_REGISTRY["corporate_income_tax"]
    _block(monkeypatch, "corporate_income_tax")
    try:
        resp = client.post(f"{CIT_P}/tax-returns/", json={
            "enterprise_id": ent_id, "tax_period": "2026", "quarter": "Q1",
            "report_type": "A类", "total_revenue": 500.0,
            "total_costs": 0.0, "total_expenses": 0.0,
        })
    finally:
        _restore(monkeypatch, "corporate_income_tax", original)
    assert resp.status_code == 400, resp.text
    assert "尚未准入" in resp.json()["detail"]
    with SessionLocal() as db:
        assert db.query(TaxReturn).count() == 0


@pytest.mark.parametrize("key", ["property_tax", "urban_land_use_tax",
                                 "land_appreciation_tax", "deed_tax"])
def test_blocked_taxes_fail_closed(key):
    assert tax_scope.tax_scope_status(key)["status"] == "blocked"
    with pytest.raises(ValueError) as exc:
        tax_scope.require_tax_scope(key)
    assert "尚未准入" in str(exc.value)


def test_admitted_taxes_still_pass_guard():
    for key in ("corporate_income_tax", "value_added_tax", "individual_income_tax",
                "stamp_duty", "disability_fund"):
        tax_scope.require_tax_scope(key)


def test_vat_scope_does_not_claim_additional_tax():
    """R11-022：附加税未接入前，准入说明不得声称已含附加税口径（不编税率，只如实描述）。"""
    reason = tax_scope.tax_scope_status("value_added_tax")["reason"]
    assert "附加税" in reason and "另行测算" in reason
    assert "含附加税口径" not in reason


# ---------- R11-020：个税专项附加扣除标准 ----------

def test_iit_standards_housing_split():
    """官方依据：国发〔2018〕41号 第十四/十七/二十条（住房贷款利息 1000 元/月、最长 240 个月；
    住房租金 1500/1100/800；两者同一纳税年度不可同时享受）。"""
    table = iit_service.SPECIAL_DEDUCTION_STANDARDS
    assert "housing" not in table
    loan = table["housing_loan_interest"]
    assert loan["monthly"] == 1000
    assert loan["max_months"] == 240
    rent = table["housing_rent"]
    assert rent["monthly_by_city"] == {
        "直辖市省会计划单列市": 1500,
        "市辖区户籍人口超100万": 1100,
        "市辖区户籍人口不超100万": 800,
    }
    for entry in (loan, rent):
        assert "不可同时享受" in entry["note"]
        assert "国发〔2018〕41号" in entry["basis"]


def test_iit_standards_continuing_education_split():
    """官方依据：国发〔2018〕41号 第八条（学历继续教育 400 元/月、同一学历≤48 个月；
    职业资格继续教育取得证书当年 3600 元）。"""
    entry = iit_service.SPECIAL_DEDUCTION_STANDARDS["continuing_education"]
    assert entry["degree"] == {"monthly": 400, "max_months": 48}
    assert entry["professional_qualification"] == {"yearly": 3600}
    assert "国发〔2018〕41号" in entry["basis"]


def test_iit_standards_other_items_unchanged():
    table = iit_service.SPECIAL_DEDUCTION_STANDARDS
    assert table["children_education"]["monthly_per_child"] == 2000
    assert table["infant_care"]["monthly_per_child"] == 2000
    assert table["elderly"]["monthly_max"] == 3000
    assert table["elderly"]["shared_max"] == 1500
    assert table["medical"]["monthly_max"] is None


def test_iit_standards_endpoint_exposes_split(client):
    body = client.get(f"{IIT_P}/standards").json()
    assert "housing_loan_interest" in body and "housing_rent" in body
    assert "housing" not in body
    assert body["housing_loan_interest"]["monthly"] == 1000
    assert body["continuing_education"]["degree"]["max_months"] == 48


# ---------- DQ：税种申报状态（R11-084） ----------

def _plant(model, **fields):
    with SessionLocal() as db:
        db.add(model(**fields))
        db.commit()


def test_dq_tax_filing_status_check_is_not_dead(client):
    empty = client.get("/api/business/dq/summary", params={"as_of": AS_OF}).json()
    assert not [i for i in empty["top_issues"]
                if i["check"] == "运行状态" and "税种申报" in i["suggestion"]]

    _plant(VatFiling, period="2026-09", taxpayer_type="general", taxpayer_name="甲",
           inputs={}, calc_result={"payable": 1.0}, status="exported")
    ok = client.get("/api/business/dq/summary", params={"as_of": AS_OF}).json()
    assert not [i for i in ok["top_issues"]
                if i["check"] == "运行状态" and "税种申报" in i["suggestion"]]

    # 旧实现只查 status='failed'；这里另加一个非 failed 的非法状态，
    # 白名单实现必须两条都命中（死检查只能命中 1 条）。
    _plant(VatFiling, period="2026-10", taxpayer_type="general", taxpayer_name="甲",
           inputs={}, calc_result={}, status="failed")
    _plant(VatFiling, period="2026-11", taxpayer_type="general", taxpayer_name="甲",
           inputs={}, calc_result={}, status="cancelled")
    bad = client.get("/api/business/dq/summary", params={"as_of": AS_OF}).json()
    hit = [i for i in bad["top_issues"]
           if i["check"] == "运行状态" and "税种申报" in i["suggestion"]]
    assert hit and hit[0]["level"] == "warning" and hit[0]["count"] == 2


# ---------- DQ：财务数据为空 vs 真实差异（R11-007） ----------

def _tax_enterprise(tax_number: str):
    from app.modules.finance_tax.models import Enterprise
    with SessionLocal() as db:
        ent = Enterprise(name="合成税务企业", tax_number=tax_number)
        db.add(ent)
        db.commit()
        db.refresh(ent)
        return ent.id


def test_dq_empty_financial_data_reports_not_imported(client):
    from app.modules.finance_tax.models import TaxReturn
    ent_id = _tax_enterprise("91330100MA2DQ01X")
    _plant(TaxReturn, enterprise_id=ent_id, year=2026, quarter=1, return_type="预缴",
           status="draft", taxable_income=1000.0, tax_amount=250.0)
    fin = [i for i in client.get("/api/business/dq/summary", params={"as_of": AS_OF}).json()["top_issues"]
           if i["domain"] == "finance"]
    assert not [i for i in fin if i["check"] == "申报勾稽" and i["level"] == "warning"]
    not_imported = [i for i in fin if i["check"] == "申报勾稽" and "尚未导入" in i["suggestion"]]
    assert not_imported and not_imported[0]["level"] == "info"


def test_dq_imported_financial_data_matching_no_issue(client):
    from app.modules.finance_tax.models import FinancialData, TaxReturn
    ent_id = _tax_enterprise("91330100MA2DQ02X")
    _plant(FinancialData, enterprise_id=ent_id, year=2026, quarter=1, profit=100.0)
    _plant(TaxReturn, enterprise_id=ent_id, year=2026, quarter=1, return_type="预缴",
           status="draft", taxable_income=100000.0, tax_amount=25000.0)
    fin = [i for i in client.get("/api/business/dq/summary", params={"as_of": AS_OF}).json()["top_issues"]
           if i["domain"] == "finance"]
    assert not [i for i in fin if i["check"] == "申报勾稽"]


def test_dq_real_reconciliation_gap_still_warns(client):
    from app.modules.finance_tax.models import FinancialData, TaxReturn
    ent_id = _tax_enterprise("91330100MA2DQ03X")
    _plant(FinancialData, enterprise_id=ent_id, year=2026, quarter=1, profit=100.0)
    _plant(TaxReturn, enterprise_id=ent_id, year=2026, quarter=1, return_type="预缴",
           status="draft", taxable_income=100000.0, tax_amount=25000.0)
    _plant(TaxReturn, enterprise_id=ent_id, year=2027, quarter=None, return_type="年度",
           status="submitted", taxable_income=100000.0, tax_amount=25000.0)
    fin = [i for i in client.get("/api/business/dq/summary", params={"as_of": AS_OF}).json()["top_issues"]
           if i["domain"] == "finance"]
    gap = [i for i in fin if i["check"] == "申报勾稽" and i["level"] == "warning"]
    assert gap and gap[0]["count"] == 1
    assert not [i for i in fin if i["check"] == "申报勾稽" and "尚未导入" in i["suggestion"]]


# ---------- DQ：看板快照落后于管线数据（R11-034） ----------

def _pipeline_env(tmp_path, monkeypatch, data_age: timedelta):
    from app.modules.finance_board import service as board_svc
    pdir = tmp_path / "pipe"
    (pdir / "data").mkdir(parents=True)
    data_path = pdir / "data" / "report_data.json"
    data_path.write_text(json.dumps({"kpi": {}}, ensure_ascii=False), encoding="utf-8")
    when = (datetime.now() - data_age).timestamp()
    import os
    os.utime(data_path, (when, when))
    monkeypatch.setattr(board_svc, "pipeline_dir", lambda: pdir)
    return pdir


def test_dq_snapshot_older_than_pipeline_data(client, monkeypatch, tmp_path):
    from app.modules.finance_board.models import BoardSnapshot
    _pipeline_env(tmp_path, monkeypatch, data_age=timedelta(0))
    _plant(BoardSnapshot, stamp="20261110_120000", status="ok",
           created_at=datetime.now() - timedelta(hours=2))
    data = client.get("/api/business/dq/summary", params={"as_of": AS_OF}).json()
    hit = [i for i in data["top_issues"] if i["check"] == "快照落后"]
    assert hit and hit[0]["level"] == "warning"
    assert "report_data.json" in hit[0]["suggestion"]
    assert any("数据源" in s for s in hit[0]["sample"])


def test_dq_fresh_snapshot_not_flagged(client, monkeypatch, tmp_path):
    from app.modules.finance_board.models import BoardSnapshot
    _pipeline_env(tmp_path, monkeypatch, data_age=timedelta(hours=2))
    _plant(BoardSnapshot, stamp="20261111_090000", status="ok", created_at=datetime.now())
    data = client.get("/api/business/dq/summary", params={"as_of": AS_OF}).json()
    assert not [i for i in data["top_issues"] if i["check"] == "快照落后"]
