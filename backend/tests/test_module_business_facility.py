# -*- coding: utf-8 -*-
"""business_facility 工程物业模块测试（合成数据，临时库）。"""
from io import BytesIO

import pytest
from openpyxl import load_workbook

from app.modules import get_loaded_modules

P = "/api/business/facility"
OPS = "/api/business/operations"


def _equipment(client, code="EQ-001", **over):
    body = {
        "code": code,
        "name": "合成冷水机组",
        "category": "A",
        "location": "B1 制冷机房",
        "install_date": "2024-01-15",
        "status": "running",
        "note": "",
    }
    body.update(over)
    return client.post(f"{P}/equipment", json=body)


def _compliance(client, equipment_id=None, **over):
    body = {
        "equipment_id": equipment_id,
        "kind": "annual_inspection",
        "title": "电梯年度检验",
        "due_date": "2026-11-15",
        "done_date": None,
        "note": "",
    }
    body.update(over)
    return client.post(f"{P}/compliance", json=body)


def test_manifest_loaded_and_tool_preset():
    loaded = {m.name: m for m in get_loaded_modules()}
    assert "business_facility" in loaded
    manifest = loaded["business_facility"]
    assert manifest.prefix == P
    # 表清单以 models.__tablename__ 为准（manifest 不再声明 tables）
    from app.modules.business_facility import models as facility_models

    assert {facility_models.FacilityEquipment.__tablename__,
            facility_models.FacilityCompliance.__tablename__} == {
        "mod_fac_equipment", "mod_fac_compliance"}
    assert manifest.preset_experts == ["property"]
    tool = next(item for item in manifest.agent_tools if item["name"] == "facility_summary")
    assert tool["readonly"] is True


def test_equipment_crud_validation_pagination_and_delete_guard(client):
    first = _equipment(client)
    assert first.status_code == 201, first.text
    eq = first.json()
    assert eq["code"] == "EQ-001"
    assert eq["category_label"] == "A类关键设备"
    assert _equipment(client).status_code == 409
    assert _equipment(client, code="EQ-BAD", category="D").status_code == 422
    assert _equipment(client, code="EQ-BAD", status="broken").status_code == 422
    assert _equipment(client, code="EQ-BAD", name="").status_code == 422

    for index in range(25):
        assert _equipment(client, code=f"EQ-{index + 2:03d}").status_code == 201
    page2 = client.get(f"{P}/equipment", params={"page": 2, "page_size": 20}).json()
    assert page2["total"] == 26
    assert len(page2["items"]) == 6

    comp = _compliance(client, equipment_id=eq["id"]).json()
    blocked = client.delete(f"{P}/equipment/{eq['id']}")
    assert blocked.status_code == 409
    assert "合规" in blocked.json()["detail"]
    assert client.delete(f"{P}/compliance/{comp['id']}").status_code == 200
    assert client.delete(f"{P}/equipment/{eq['id']}").status_code == 200


def test_compliance_derived_status_summary_and_building_level(client):
    eq = _equipment(client).json()
    assert _compliance(client, equipment_id=eq["id"]).status_code == 201
    overdue = _compliance(client, equipment_id=None, kind="fire", title="消防检查",
                          due_date="2026-10-01").json()
    done = _compliance(client, equipment_id=eq["id"], kind="maintenance", title="维保",
                       due_date="2026-10-15", done_date="2026-10-10").json()
    assert overdue["equipment_id"] is None
    assert overdue["status"] == "overdue"
    assert done["status"] == "done"

    rows = client.get(f"{P}/compliance", params={"status": "overdue", "as_of": "2026-10-31"}).json()
    assert [row["id"] for row in rows["items"]] == [overdue["id"]]

    summary = client.get(f"{P}/summary", params={"as_of": "2026-10-31"}).json()
    assert summary["equipment_count"] == 1
    assert summary["category_a"] == 1
    assert summary["compliance_30"] == 1  # 11-15 在 30 天内
    assert summary["overdue"] == 1
    assert summary["building_level"] == 1


def test_compliance_validation_and_equipment_update(client):
    eq = _equipment(client).json()
    assert _compliance(client, equipment_id=9999).status_code == 404
    assert _compliance(client, equipment_id=eq["id"], kind="bogus").status_code == 422

    comp = _compliance(client, equipment_id=eq["id"]).json()
    cleared = client.put(f"{P}/compliance/{comp['id']}", json={"equipment_id": None})
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["equipment_id"] is None
    assert client.put(f"{P}/compliance/{comp['id']}", json={"due_date": None}).status_code == 400


def test_export_content_and_formula_injection(client):
    eq = _equipment(client, code="=1+1", name="+CMD", note="@注入").json()
    _compliance(client, equipment_id=eq["id"], title="-危险公式")

    response = client.get(f"{P}/export/equipment.xlsx")
    assert response.status_code == 200 and response.content[:2] == b"PK"
    ws = load_workbook(BytesIO(response.content), data_only=False).active
    assert ws.max_row == 2
    assert ws["A2"].value == "'=1+1"
    assert ws["B2"].value == "'+CMD"
    assert ws["G2"].value == "'@注入"

    response = client.get(f"{P}/export/compliance.xlsx")
    ws = load_workbook(BytesIO(response.content), data_only=False).active
    assert ws.max_row == 2
    assert ws["C2"].value == "'-危险公式"


def test_work_order_optional_equipment_link(client):
    eq = _equipment(client).json()
    created = client.post(f"{OPS}/work-orders", json={
        "type": "repair", "location": "B1", "description": "冷水机组异响",
        "priority": "urgent", "equipment_id": eq["id"],
    })
    assert created.status_code == 201, created.text
    assert created.json()["equipment_id"] == eq["id"]
    assert created.json()["equipment_code"] == "EQ-001"

    blocked = client.delete(f"{P}/equipment/{eq['id']}")
    assert blocked.status_code == 409
    assert "工单" in blocked.json()["detail"]

    bad = client.post(f"{OPS}/work-orders", json={
        "type": "repair", "location": "B1", "description": "合成",
        "equipment_id": 9999,
    })
    assert bad.status_code == 400
    assert "设备" in bad.json()["detail"]

    cleared = client.put(f"{OPS}/work-orders/{created.json()['id']}", json={"equipment_id": None})
    assert cleared.status_code == 200
    assert cleared.json()["equipment_id"] is None


def test_facility_migration_idempotent(tmp_path, monkeypatch):
    from sqlalchemy import create_engine, text
    from app import main as app_main
    from app.config import settings

    db_path = tmp_path / "old.db"
    engine = create_engine(f"sqlite:///{db_path.as_posix()}")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE mod_ops_work_order (id INTEGER PRIMARY KEY)"))
    monkeypatch.setattr(app_main, "engine", engine)
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{db_path.as_posix()}")
    app_main._ensure_sqlite_columns()
    with engine.begin() as conn:
        first = {row[1] for row in conn.execute(text("PRAGMA table_info(mod_ops_work_order)"))}
    app_main._ensure_sqlite_columns()
    with engine.begin() as conn:
        second = {row[1] for row in conn.execute(text("PRAGMA table_info(mod_ops_work_order)"))}
    assert "equipment_id" in first
    assert first == second
