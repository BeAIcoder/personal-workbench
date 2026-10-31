# -*- coding: utf-8 -*-
"""business_leasing 招商管理模块测试（合成数据，临时库）。"""
import io
import sqlite3
from datetime import date, timedelta

import pytest
from openpyxl import load_workbook
from sqlalchemy import create_engine, text

from app import main as app_main
from app.modules import get_loaded_modules

P = "/api/business/leasing"


def _unit(client, unit_no="A-101", **overrides):
    payload = {
        "building": "A栋", "floor": "1F", "unit_no": unit_no,
        "area": 100, "rent_price": 80, "property_fee": 12, "status": "vacant",
    }
    payload.update(overrides)
    response = client.post(f"{P}/units", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def _lead(client, company="合成客户", **overrides):
    payload = {"company": company, "stage": "lead"}
    payload.update(overrides)
    response = client.post(f"{P}/leads", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def _unit_row(client, unit_id):
    rows = client.get(f"{P}/units", params={"page_size": 200}).json()["items"]
    return next(row for row in rows if row["id"] == unit_id)


def test_manifest_loaded():
    loaded = {m.name: m for m in get_loaded_modules()}
    assert "business_leasing" in loaded
    m = loaded["business_leasing"]
    assert m.prefix == "/api/business/leasing"
    assert m.tables == ["mod_leasing_unit", "mod_leasing_lead", "mod_leasing_followup"]
    assert m.menu["group"] == "经营"


def test_unit_crud_and_summary(client):
    r = client.post(f"{P}/units", json={
        "building": "A栋", "floor": "1F", "unit_no": "A-101",
        "area": 120.5, "rent_price": 80, "property_fee": 12, "status": "vacant",
    })
    assert r.status_code == 201, r.text
    uid = r.json()["id"]
    assert r.json()["monthly_rent"] == pytest.approx(9640.0)

    r = client.post(f"{P}/units", json={
        "building": "A栋", "floor": "1F", "unit_no": "A-102",
        "area": 100, "rent_price": 80, "status": "leased",
    })
    assert r.status_code == 201, r.text

    summary = client.get(f"{P}/units/summary").json()
    assert summary["total_units"] == 2
    assert summary["total_area"] == pytest.approx(220.5)
    assert summary["vacant_area"] == pytest.approx(120.5)
    assert summary["vacancy_rate"] == pytest.approx(120.5 / 220.5, abs=1e-4)
    assert summary["buildings"] == ["A栋"]

    dup = client.post(f"{P}/units", json={
        "building": "A栋", "floor": "1F", "unit_no": "A-101", "area": 1,
    })
    assert dup.status_code == 409

    upd = client.put(f"{P}/units/{uid}", json={"status": "negotiating", "rent_price": 85})
    assert upd.status_code == 200, upd.text
    assert upd.json()["status"] == "negotiating"
    assert upd.json()["rent_price"] == pytest.approx(85)

    assert client.delete(f"{P}/units/{uid}").status_code == 200
    assert client.delete(f"{P}/units/{uid}").status_code == 404
    assert client.get(f"{P}/units").json()["total"] == 1


def test_lead_funnel_followup_and_due(client):
    r = client.post(f"{P}/leads", json={
        "company": "合成餐饮公司", "contact": "张三", "phone": "13800000000",
        "demand_area": 200, "budget": 30000, "stage": "contact", "owner": "李四",
        "next_action_at": (date.today() - timedelta(days=1)).isoformat(),
    })
    assert r.status_code == 201, r.text
    lid = r.json()["id"]

    unit = _unit(client, "A-102")
    signed = client.post(f"{P}/leads", json={
        "company": "合成零售公司", "stage": "signed", "demand_area": 100,
        "unit_id": unit["id"],
    })
    assert signed.status_code == 201, signed.text

    funnel = client.get(f"{P}/funnel").json()
    assert funnel["total"] == 2 and funnel["signed"] == 1
    assert funnel["conversion_rate"] == pytest.approx(0.5)
    stage = {s["stage"]: s for s in funnel["stages"]}
    assert stage["contact"]["count"] == 1
    assert stage["contact"]["area"] == pytest.approx(200.0)

    due = client.get(f"{P}/leads/due").json()
    assert [x["id"] for x in due] == [lid]

    f = client.post(f"{P}/leads/{lid}/followups", json={
        "at": date.today().isoformat(), "channel": "微信", "content": "发送报价单", "result": "待回复",
    })
    assert f.status_code == 201, f.text
    assert len(client.get(f"{P}/leads/{lid}/followups").json()) == 1

    bad = client.post(f"{P}/leads/{lid}/followups", json={"channel": "飞鸽", "content": "x"})
    assert bad.status_code == 422

    assert client.delete(f"{P}/leads/{lid}").status_code == 200
    assert client.get(f"{P}/leads/{lid}/followups").json() == []


def test_signed_requires_existing_unit(client):
    missing_id = client.post(f"{P}/leads", json={"company": "无铺签约", "stage": "signed"})
    assert missing_id.status_code == 400
    assert "铺位" in missing_id.json()["detail"]

    missing_unit = client.post(f"{P}/leads", json={
        "company": "失效铺位签约", "stage": "signed", "unit_id": 999999,
    })
    assert missing_unit.status_code == 400
    assert "不存在" in missing_unit.json()["detail"]

    old_lead = _lead(client, "历史无线索铺位", stage="intent")
    updated = client.put(f"{P}/leads/{old_lead['id']}", json={"owner": "小王"})
    assert updated.status_code == 200, updated.text
    assert updated.json()["unit_id"] is None


def test_sign_occupies_unit_and_duplicate_signing_conflicts(client):
    unit = _unit(client)
    signed = client.post(f"{P}/leads", json={
        "company": "签约客户", "stage": "signed", "unit_id": unit["id"],
        "rent_form": "guaranteed_percentage", "free_rent_months": 2,
        "escalation_rate": 3.5, "fitout_subsidy": 12000, "lease_term_months": 36,
    })
    assert signed.status_code == 201, signed.text
    assert signed.json()["rent_form"] == "guaranteed_percentage"
    assert signed.json()["free_rent_months"] == 2
    assert signed.json()["escalation_rate"] == pytest.approx(3.5)
    assert signed.json()["fitout_subsidy"] == pytest.approx(12000)
    assert signed.json()["lease_term_months"] == 36
    assert _unit_row(client, unit["id"])["status"] == "leased"

    duplicate = client.post(f"{P}/leads", json={
        "company": "重复签约客户", "stage": "signed", "unit_id": unit["id"],
    })
    assert duplicate.status_code == 409
    assert "已租" in duplicate.json()["detail"] or "签约" in duplicate.json()["detail"]

    historical = _unit(client, "H-101", status="leased")
    historical_sign = client.post(f"{P}/leads", json={
        "company": "占用历史铺位", "stage": "signed", "unit_id": historical["id"],
    })
    assert historical_sign.status_code == 409
    assert client.get(f"{P}/leads").json()["total"] == 1


def test_change_unit_is_atomic_and_cancel_releases_unit(client):
    old_unit = _unit(client, "A-101")
    new_unit = _unit(client, "A-102")
    lead = _lead(client, "换铺客户", stage="signed", unit_id=old_unit["id"])
    assert _unit_row(client, old_unit["id"])["status"] == "leased"

    changed = client.put(f"{P}/leads/{lead['id']}", json={"unit_id": new_unit["id"]})
    assert changed.status_code == 200, changed.text
    assert changed.json()["unit_id"] == new_unit["id"]
    assert _unit_row(client, old_unit["id"])["status"] == "vacant"
    assert _unit_row(client, new_unit["id"])["status"] == "leased"

    cancelled = client.put(f"{P}/leads/{lead['id']}", json={"stage": "lost"})
    assert cancelled.status_code == 200, cancelled.text
    assert _unit_row(client, new_unit["id"])["status"] == "vacant"


def test_failed_change_keeps_old_unit_and_lead_unchanged(client):
    old_unit = _unit(client, "A-101")
    occupied = _unit(client, "A-102", status="leased")
    lead = _lead(client, "原子换铺客户", stage="signed", unit_id=old_unit["id"])
    assert _unit_row(client, old_unit["id"])["status"] == "leased"

    conflict = client.put(f"{P}/leads/{lead['id']}", json={"unit_id": occupied["id"]})
    assert conflict.status_code == 409
    rows = client.get(f"{P}/leads").json()["items"]
    assert rows[0]["stage"] == "signed"
    assert rows[0]["unit_id"] == old_unit["id"]
    assert _unit_row(client, old_unit["id"])["status"] == "leased"
    assert _unit_row(client, occupied["id"])["status"] == "leased"


def test_cancel_does_not_release_unit_referenced_by_another_signed_lead(client):
    from app.database import SessionLocal
    from app.modules.business_leasing.models import LeasingLead

    unit = _unit(client)
    first = _lead(client, "首位签约", stage="signed", unit_id=unit["id"])
    assert _unit_row(client, unit["id"])["status"] == "leased"
    with SessionLocal() as db:
        db.add(LeasingLead(company="历史重复签约", stage="signed", unit_id=unit["id"]))
        db.commit()

    cancelled = client.put(f"{P}/leads/{first['id']}", json={"stage": "lost"})
    assert cancelled.status_code == 200, cancelled.text
    assert _unit_row(client, unit["id"])["status"] == "leased"


def test_signed_lead_delete_requires_cancellation_first(client):
    unit = _unit(client)
    lead = _lead(client, "待删签约", stage="signed", unit_id=unit["id"])
    rejected = client.delete(f"{P}/leads/{lead['id']}")
    assert rejected.status_code == 409
    assert "取消签约" in rejected.json()["detail"]
    assert client.get(f"{P}/leads").json()["total"] == 1
    assert _unit_row(client, unit["id"])["status"] == "leased"

    assert client.put(f"{P}/leads/{lead['id']}", json={"stage": "lost"}).status_code == 200
    assert client.delete(f"{P}/leads/{lead['id']}").status_code == 200


def test_manual_unit_status_cannot_conflict_with_signed_lead(client):
    unit = _unit(client)
    _lead(client, "铺位状态保护", stage="signed", unit_id=unit["id"])
    response = client.put(f"{P}/units/{unit['id']}", json={"status": "vacant"})
    assert response.status_code == 409
    assert "签约" in response.json()["detail"]
    assert _unit_row(client, unit["id"])["status"] == "leased"

    historical = _unit(client, "H-101", status="leased")
    kept = client.put(f"{P}/units/{historical['id']}", json={"note": "历史已租，无签约线索"})
    assert kept.status_code == 200, kept.text
    assert kept.json()["status"] == "leased"
    assert client.get(f"{P}/leads").json()["total"] == 1


@pytest.mark.parametrize("payload", [
    {"rent_form": "unknown"},
    {"free_rent_months": -1},
    {"free_rent_months": 121},
    {"lease_term_months": 0},
    {"lease_term_months": 601},
    {"escalation_rate": -0.01},
    {"fitout_subsidy": -1},
])
def test_rent_decision_validation(client, payload):
    response = client.post(f"{P}/leads", json={"company": "非法租决", **payload})
    assert response.status_code == 422, response.text


def test_summary_leasing_opening_pipeline_and_zero_area(client):
    _unit(client, "V-100", area=100, status="vacant")
    _unit(client, "N-050", area=50, status="negotiating")
    _unit(client, "R-030", area=30, status="reserved")
    _unit(client, "L-020", area=20, status="leased", opened_at=date.today().isoformat())
    _unit(client, "L-010", area=10, status="leased",
          opened_at=(date.today() + timedelta(days=1)).isoformat())
    _unit(client, "L-005", area=5, status="leased")

    summary = client.get(f"{P}/units/summary").json()
    assert summary["total_area"] == pytest.approx(215)
    assert summary["leasing_rate"] == pytest.approx(65 / 215, abs=1e-4)
    assert summary["opening_rate"] == pytest.approx(20 / 215, abs=1e-4)
    assert summary["pipeline_area"] == pytest.approx(80)
    assert "已开业" in summary["rate_note"]

    for row in client.get(f"{P}/units", params={"page_size": 200}).json()["items"]:
        assert client.put(f"{P}/units/{row['id']}", json={"area": 0}).status_code == 200
    zero = client.get(f"{P}/units/summary").json()
    assert zero["leasing_rate"] == 0
    assert zero["opening_rate"] == 0
    assert zero["pipeline_area"] == 0
    assert "总面积为0" in zero["rate_note"].replace(" ", "")


def test_26_rows_paginate_units_and_leads(client):
    for index in range(26):
        _unit(client, f"P-{index:03d}")
        _lead(client, f"分页客户{index:02d}")

    units_1 = client.get(f"{P}/units").json()
    units_2 = client.get(f"{P}/units", params={"page": 2}).json()
    leads_1 = client.get(f"{P}/leads").json()
    leads_2 = client.get(f"{P}/leads", params={"page": 2}).json()
    assert (units_1["total"], len(units_1["items"])) == (26, 20)
    assert (units_2["total"], len(units_2["items"]), units_2["page"]) == (26, 6, 2)
    assert (leads_1["total"], len(leads_1["items"])) == (26, 20)
    assert (leads_2["total"], len(leads_2["items"]), leads_2["page"]) == (26, 6, 2)


def test_export_xlsx_contains_opening_and_rent_decision_columns(client):
    unit = _unit(client, "B-201", area=50, opened_at="2026-10-01")
    _lead(
        client, "导出客户", stage="signed", unit_id=unit["id"],
        rent_form="fixed", free_rent_months=3, escalation_rate=4.25,
        fitout_subsidy=8800, lease_term_months=60,
    )
    unit_response = client.get(f"{P}/export/units.xlsx")
    assert unit_response.status_code == 200
    assert unit_response.content[:2] == b"PK"
    assert "spreadsheetml" in unit_response.headers["content-type"]
    unit_sheet = load_workbook(io.BytesIO(unit_response.content)).active
    unit_rows = list(unit_sheet.values)
    assert "开业日期" in unit_rows[0]
    opened_index = unit_rows[0].index("开业日期")
    assert unit_rows[1][opened_index] == "2026-10-01"

    lead_response = client.get(f"{P}/export/leads.xlsx")
    assert lead_response.status_code == 200
    lead_sheet = load_workbook(io.BytesIO(lead_response.content)).active
    lead_rows = list(lead_sheet.values)
    expected_headers = {"意向铺位", "租金形式", "免租期(月)", "年递增率(%)", "装修补贴(元)", "租期(月)"}
    assert expected_headers <= set(lead_rows[0])
    values = {lead_rows[0][index]: value for index, value in enumerate(lead_rows[1])}
    assert values["意向铺位"] == "A栋 1F B-201"
    assert values["租金形式"] == "固定租金"
    assert values["免租期(月)"] == 3
    assert values["年递增率(%)"] == pytest.approx(4.25)
    assert values["装修补贴(元)"] == pytest.approx(8800)
    assert values["租期(月)"] == 60


def test_column_patches_migration_is_idempotent(tmp_path, monkeypatch):
    path = tmp_path / "old_leasing.db"
    raw = sqlite3.connect(path)
    raw.execute("CREATE TABLE mod_leasing_unit (id INTEGER PRIMARY KEY, unit_no VARCHAR(50))")
    raw.execute("CREATE TABLE mod_leasing_lead (id INTEGER PRIMARY KEY, company VARCHAR(200))")
    raw.execute("INSERT INTO mod_leasing_unit VALUES (1, 'OLD-1')")
    raw.execute("INSERT INTO mod_leasing_lead VALUES (1, '历史客户')")
    raw.commit()
    raw.close()

    old_engine = create_engine(f"sqlite:///{path.as_posix()}")
    monkeypatch.setattr(app_main, "engine", old_engine)
    try:
        app_main._ensure_sqlite_columns()
        with old_engine.connect() as conn:
            unit_columns = {row[1] for row in conn.execute(text("PRAGMA table_info(mod_leasing_unit)"))}
            lead_columns = {row[1] for row in conn.execute(text("PRAGMA table_info(mod_leasing_lead)"))}
            assert "opened_at" in unit_columns
            assert {
                "unit_id", "rent_form", "free_rent_months", "escalation_rate",
                "fitout_subsidy", "lease_term_months",
            } <= lead_columns
            first_schema = conn.execute(text(
                "SELECT name, sql FROM sqlite_master WHERE name IN "
                "('mod_leasing_unit', 'mod_leasing_lead') ORDER BY name"
            )).fetchall()
            first_rows = conn.execute(text("SELECT * FROM mod_leasing_lead")).fetchall()

        app_main._ensure_sqlite_columns()
        with old_engine.connect() as conn:
            second_schema = conn.execute(text(
                "SELECT name, sql FROM sqlite_master WHERE name IN "
                "('mod_leasing_unit', 'mod_leasing_lead') ORDER BY name"
            )).fetchall()
            second_rows = conn.execute(text("SELECT * FROM mod_leasing_lead")).fetchall()
        assert second_schema == first_schema
        assert second_rows == first_rows
    finally:
        old_engine.dispose()


def test_referenced_unit_delete_requires_reassignment_after_cancellation(client):
    unit = _unit(client)
    other_unit = _unit(client, "A-102")
    lead = _lead(client, "铺位删除保护", stage="signed", unit_id=unit["id"])

    rejected = client.delete(f"{P}/units/{unit['id']}")
    assert rejected.status_code == 409
    assert "线索" in rejected.json()["detail"]
    assert _unit_row(client, unit["id"])["status"] == "leased"
    assert client.get(f"{P}/leads").json()["items"][0]["unit_id"] == unit["id"]

    assert client.put(f"{P}/leads/{lead['id']}", json={"stage": "lost"}).status_code == 200
    still_rejected = client.delete(f"{P}/units/{unit['id']}")
    assert still_rejected.status_code == 409
    assert "线索" in still_rejected.json()["detail"]
    assert client.put(f"{P}/leads/{lead['id']}", json={"unit_id": other_unit["id"]}).status_code == 200
    assert client.delete(f"{P}/units/{unit['id']}").status_code == 200


def test_non_signed_lead_reference_blocks_unit_delete_until_lead_deleted(client):
    unit = _unit(client)
    lead = _lead(client, "原始非签约引用", stage="intent", unit_id=unit["id"])

    rejected = client.delete(f"{P}/units/{unit['id']}")
    assert rejected.status_code == 409
    assert "线索" in rejected.json()["detail"]
    assert client.delete(f"{P}/leads/{lead['id']}").status_code == 200
    assert client.delete(f"{P}/units/{unit['id']}").status_code == 200


def test_concurrent_signing_has_exactly_one_winner(client, monkeypatch):
    from threading import Barrier, Event, local
    from concurrent.futures import ThreadPoolExecutor

    from app.database import SessionLocal
    from app.modules.business_leasing import service
    from app.modules.business_leasing.models import LeasingLead, LeasingUnit

    unit = _unit(client, "RACE-101", status="vacant")
    original = service._other_signed_lead
    both_read = Barrier(2)
    first_committed = Event()
    thread_state = local()

    def synchronized_read(db, unit_id, exclude_lead_id=None):
        result = original(db, unit_id, exclude_lead_id)
        both_read.wait(timeout=10)
        if getattr(thread_state, "wait_for_first", False):
            assert first_committed.wait(timeout=10)
        return result

    monkeypatch.setattr(service, "_other_signed_lead", synchronized_read)

    def sign(company, wait_for_first):
        thread_state.wait_for_first = wait_for_first
        with SessionLocal() as db:
            try:
                service.lead_save(db, {
                    "company": company, "stage": "signed", "unit_id": unit["id"],
                })
                if not wait_for_first:
                    first_committed.set()
                return "ok"
            except service.ConflictError as exc:
                return f"409:{exc}"

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(sign, "并发签约A", False), pool.submit(sign, "并发签约B", True)]
        results = [future.result(timeout=20) for future in futures]

    assert results.count("ok") == 1, results
    losers = [result for result in results if result.startswith("409:")]
    assert len(losers) == 1, results
    assert "已租" in losers[0] or "签约" in losers[0]
    with SessionLocal() as db:
        assert db.query(LeasingLead).filter(LeasingLead.stage == "signed").count() == 1
        assert db.get(LeasingUnit, unit["id"]).status == "leased"


def test_manual_non_leased_update_cannot_overwrite_interleaved_sign(client, monkeypatch):
    from app.database import SessionLocal
    from app.modules.business_leasing import service
    from app.modules.business_leasing.models import LeasingLead, LeasingUnit

    unit = _unit(client, "MANUAL-RACE", status="leased")
    original = service._other_signed_lead
    with SessionLocal() as manual_db:
        def interleaved_stale_read(db, unit_id, exclude_lead_id=None):
            stale_result = original(db, unit_id, exclude_lead_id)
            if db is manual_db:
                with SessionLocal() as setup_db:
                    setup_unit = setup_db.get(LeasingUnit, unit_id)
                    setup_unit.status = "vacant"
                    setup_db.commit()
                with SessionLocal() as sign_db:
                    service.lead_save(sign_db, {
                        "company": "交错签约", "stage": "signed", "unit_id": unit_id,
                    })
            return stale_result

        monkeypatch.setattr(service, "_other_signed_lead", interleaved_stale_read)
        with pytest.raises(ValueError, match="签约"):
            service.unit_save(manual_db, {"status": "vacant"}, unit["id"])

    with SessionLocal() as db:
        assert db.get(LeasingUnit, unit["id"]).status == "leased"
        signed = db.query(LeasingLead).filter(LeasingLead.stage == "signed").all()
        assert len(signed) == 1
        assert signed[0].unit_id == unit["id"]
