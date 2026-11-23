# -*- coding: utf-8 -*-
"""business_operations 营运管理模块测试（合成数据，临时库）。"""
from datetime import date, timedelta

import pytest

from app.modules import get_loaded_modules

P = "/api/business/operations"


def _contract(client, no="HT-001", **over):
    body = {
        "contract_no": no,
        "tenant": "合成商户甲",
        "unit_no": "A-101",
        "area": 100.0,
        "start_date": "2026-01-01",
        "end_date": (date.today() + timedelta(days=20)).isoformat(),
        "rent_monthly": 10000.0,
        "property_fee_monthly": 1200.0,
        "deposit": 20000.0,
    }
    body.update(over)
    return client.post(f"{P}/contracts", json=body)


def test_manifest_loaded():
    loaded = {m.name: m for m in get_loaded_modules()}
    assert "business_operations" in loaded
    m = loaded["business_operations"]
    assert m.prefix == "/api/business/operations"
    from app.modules.business_operations import models as ops_models

    assert {ops_models.OpsContract.__tablename__, ops_models.OpsBill.__tablename__,
            ops_models.OpsWorkOrder.__tablename__,
            ops_models.OpsWorkOrderFollowup.__tablename__} == {
        "mod_ops_contract", "mod_ops_bill", "mod_ops_work_order",
        "mod_ops_work_order_followup"}


def test_contract_crud_expiring_and_validation(client):
    r = _contract(client)
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    assert r.json()["monthly_total"] == pytest.approx(11200.0)
    assert r.json()["expiring"] is True

    assert _contract(client).status_code == 409
    bad = _contract(client, no="HT-002", start_date="2026-05-01", end_date="2026-04-01")
    assert bad.status_code == 409

    exp = client.get(f"{P}/contracts/expiring", params={"days": 30}).json()
    assert [x["id"] for x in exp] == [cid]

    upd = client.put(f"{P}/contracts/{cid}", json={"rent_monthly": 12000})
    assert upd.status_code == 200, upd.text
    assert upd.json()["monthly_total"] == pytest.approx(13200.0)
    assert client.put(f"{P}/contracts/{cid}", json={"status": "bogus"}).status_code == 422
    assert client.delete(f"{P}/contracts/{cid}").status_code == 200
    assert client.get(f"{P}/contracts").json()["total"] == 0


def test_bill_generate_pay_and_summary(client):
    cid = _contract(client).json()["id"]

    r = client.post(f"{P}/contracts/{cid}/bills", json={"period": "2026-10", "other_amount": 300})
    assert r.status_code == 201, r.text
    bill = r.json()
    bid = bill["id"]
    assert bill["total_amount"] == pytest.approx(11500.0)
    assert bill["status"] == "unpaid"

    # 同合同同期间重复生成 → 409
    dup = client.post(f"{P}/contracts/{cid}/bills", json={"period": "2026-10"})
    assert dup.status_code == 409
    # 非法期间 → 422
    assert client.post(f"{P}/contracts/{cid}/bills",
                       json={"period": "2026-13"}).status_code == 422

    s = client.get(f"{P}/summary", params={"period": "2026-10"}).json()
    assert s["receivable"] == pytest.approx(11500.0)
    assert s["received"] == pytest.approx(0.0)
    assert s["collection_rate"] == 0.0
    assert s["arrears"] == pytest.approx(11500.0)

    p1 = client.post(f"{P}/bills/{bid}/payments", json={"amount": 5000})
    assert p1.status_code == 200, p1.text
    assert p1.json()["status"] == "partial"
    assert p1.json()["arrears"] == pytest.approx(6500.0)

    # 超收 → 400
    over = client.post(f"{P}/bills/{bid}/payments", json={"amount": 99999})
    assert over.status_code == 400

    p2 = client.post(f"{P}/bills/{bid}/payments", json={"amount": 6500})
    assert p2.status_code == 200
    assert p2.json()["status"] == "paid"
    assert p2.json()["arrears"] == pytest.approx(0.0)

    # 已收清再收 → 400
    assert client.post(f"{P}/bills/{bid}/payments", json={"amount": 1}).status_code == 400

    s = client.get(f"{P}/summary", params={"period": "2026-10"}).json()
    assert s["received"] == pytest.approx(11500.0)
    assert s["collection_rate"] == pytest.approx(1.0)


def test_work_order_lifecycle(client):
    r = client.post(f"{P}/work-orders", json={
        "type": "repair", "location": "A栋1F", "tenant": "合成商户甲",
        "priority": "high", "description": "空调漏水",
    })
    assert r.status_code == 201, r.text
    w = r.json()
    assert w["code"].startswith("WO")
    assert w["status"] == "open" and w["closed_at"] is None

    upd = client.put(f"{P}/work-orders/{w['id']}", json={"status": "done", "assignee": "王五"})
    assert upd.status_code == 200, upd.text
    assert upd.json()["status"] == "done"
    assert upd.json()["closed_at"] is not None

    reopen = client.put(f"{P}/work-orders/{w['id']}", json={"status": "processing"})
    assert reopen.json()["closed_at"] is None

    assert client.put(f"{P}/work-orders/{w['id']}", json={"status": "bogus"}).status_code == 422
    assert client.delete(f"{P}/work-orders/{w['id']}").status_code == 200


def test_work_order_link_contract(client):
    """工单↔合同勾稽（2026-11-02）：关联合同校验、列表回显合同号、解除关联。"""
    c = _contract(client, no="HT-WO-001")
    assert c.status_code == 201, c.text
    contract_id = c.json()["id"]
    r = client.post(f"{P}/work-orders", json={
        "type": "repair", "location": "C栋3F", "description": "玻璃破损",
        "contract_id": contract_id,
    })
    assert r.status_code == 201, r.text
    w = r.json()
    assert w["contract_id"] == contract_id
    assert w["contract_no"] == "HT-WO-001"
    # 列表回显
    listed = client.get(f"{P}/work-orders", params={"page": 1, "page_size": 10}).json()
    hit = next(i for i in listed["items"] if i["id"] == w["id"])
    assert hit["contract_no"] == "HT-WO-001"
    # 不存在的合同拒绝
    bad = client.post(f"{P}/work-orders", json={"description": "x", "contract_id": 99999})
    assert bad.status_code == 400, bad.text
    # 显式 null 解除关联
    cleared = client.put(f"{P}/work-orders/{w['id']}", json={"contract_id": None})
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["contract_id"] is None and cleared.json()["contract_no"] == ""


def test_work_order_edit_done_preserves_closed_at(client):
    r = client.post(f"{P}/work-orders", json={
        "type": "repair", "location": "B栋2F", "description": "电梯异响",
    })
    assert r.status_code == 201, r.text
    w = r.json()
    done = client.put(f"{P}/work-orders/{w['id']}", json={"status": "done"})
    assert done.status_code == 200, done.text
    closed_at = done.json()["closed_at"]
    assert closed_at is not None

    # 编辑已完成工单但不改状态：closed_at 不被重写
    edit = client.put(f"{P}/work-orders/{w['id']}",
                      json={"assignee": "王五", "description": "电梯异响（已换件）"})
    assert edit.status_code == 200, edit.text
    assert edit.json()["status"] == "done"
    assert edit.json()["closed_at"] == closed_at


def test_export_xlsx(client):
    _contract(client)
    r = client.get(f"{P}/export/contracts.xlsx")
    assert r.status_code == 200 and r.content[:2] == b"PK"
    r = client.get(f"{P}/export/bills.xlsx")
    assert r.status_code == 200 and r.content[:2] == b"PK"


def test_bill_generation_requires_active_contract_and_lease_overlap(client):
    cid = _contract(client, start_date="2026-03-15", end_date="2026-05-20").json()["id"]

    before = client.post(f"{P}/contracts/{cid}/bills", json={"period": "2026-02"})
    assert before.status_code == 409
    assert "租期" in before.json()["detail"]
    after = client.post(f"{P}/contracts/{cid}/bills", json={"period": "2026-06"})
    assert after.status_code == 409
    assert "租期" in after.json()["detail"]

    assert client.post(f"{P}/contracts/{cid}/bills",
                       json={"period": "2026-03"}).status_code == 201
    assert client.post(f"{P}/contracts/{cid}/bills",
                       json={"period": "2026-05"}).status_code == 201
    assert client.put(f"{P}/contracts/{cid}", json={"status": "terminated"}).status_code == 200
    terminated = client.post(f"{P}/contracts/{cid}/bills", json={"period": "2026-04"})
    assert terminated.status_code == 409
    assert "终止" in terminated.json()["detail"]


def test_bill_aging_fixed_as_of_boundaries_and_open_items(client):
    cid = _contract(
        client, start_date="2026-01-01", end_date="2027-12-31",
        rent_monthly=100, property_fee_monthly=0,
    ).json()["id"]
    due_dates = [
        "2026-10-31",  # 0 天：未到期
        "2026-11-01",  # 未来：未到期
        "2026-10-30",  # 1 天
        "2026-10-01",  # 30 天
        "2026-09-30",  # 31 天
        "2026-08-02",  # 90 天
        "2026-08-01",  # 91 天
        "2026-07-01",  # 收清后排除
    ]
    bill_ids = []
    for index, due_date in enumerate(due_dates):
        response = client.post(f"{P}/contracts/{cid}/bills", json={
            "period": f"2026-{index + 1:02d}", "due_date": due_date,
        })
        assert response.status_code == 201, response.text
        bill_ids.append(response.json()["id"])
    paid = client.post(f"{P}/bills/{bill_ids[-1]}/payments", json={"amount": 100})
    assert paid.status_code == 200
    assert client.put(f"{P}/contracts/{cid}", json={"status": "terminated"}).status_code == 200

    response = client.get(f"{P}/bills/aging", params={"as_of": "2026-10-31"})
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["as_of"] == "2026-10-31"
    assert data["buckets"] == {
        "not_due": {"amount": 200.0, "count": 2, "contracts": 1},
        "1_30": {"amount": 200.0, "count": 2, "contracts": 1},
        "31_90": {"amount": 200.0, "count": 2, "contracts": 1},
        "over_90": {"amount": 100.0, "count": 1, "contracts": 1},
    }
    assert [item["overdue_days"] for item in data["items"]] == [91, 90, 31, 30, 1, 0, -1]
    assert [item["bucket"] for item in data["items"]] == [
        "over_90", "31_90", "31_90", "1_30", "1_30", "not_due", "not_due",
    ]
    assert bill_ids[-1] not in {item["id"] for item in data["items"]}
    assert sum(bucket["amount"] for bucket in data["buckets"].values()) == pytest.approx(700)
    assert client.get(f"{P}/bills/aging", params={"as_of": "2026-02-30"}).status_code == 400


def test_summary_reconciles_with_bill_and_contract_lists(client):
    expired_id = _contract(
        client, "HT-EXPIRED", start_date="2026-01-01", end_date="2026-01-31",
        rent_monthly=100, property_fee_monthly=0,
    ).json()["id"]
    current_id = _contract(
        client, "HT-CURRENT", start_date="2026-01-01", end_date="2027-12-31",
        rent_monthly=100, property_fee_monthly=0,
    ).json()["id"]
    terminated_id = _contract(
        client, "HT-TERMINATED", start_date="2026-01-01", end_date="2027-12-31",
        rent_monthly=100, property_fee_monthly=0,
    ).json()["id"]
    for cid in (expired_id, current_id, terminated_id):
        assert client.post(f"{P}/contracts/{cid}/bills", json={"period": "2026-01"}).status_code == 201
    bills = client.get(f"{P}/bills", params={"period": "2026-01", "page_size": 200}).json()["items"]
    by_contract = {item["contract_id"]: item for item in bills}
    assert client.post(f"{P}/bills/{by_contract[expired_id]['id']}/payments",
                       json={"amount": 25}).status_code == 200
    assert client.post(f"{P}/bills/{by_contract[current_id]['id']}/payments",
                       json={"amount": 100}).status_code == 200
    assert client.put(f"{P}/contracts/{terminated_id}", json={"status": "terminated"}).status_code == 200

    summary = client.get(f"{P}/summary", params={"period": "2026-01"}).json()
    detail = client.get(f"{P}/bills", params={"period": "2026-01", "page_size": 200}).json()["items"]
    assert summary["receivable"] == pytest.approx(sum(item["total_amount"] for item in detail))
    assert summary["received"] == pytest.approx(sum(item["paid_amount"] for item in detail))
    assert summary["arrears"] == pytest.approx(sum(item["arrears"] for item in detail))
    assert summary["arrears_contracts"] == len({item["contract_id"] for item in detail if item["arrears"] > 0})
    assert summary["arrears_contracts"] == 2
    active_contracts = client.get(f"{P}/contracts", params={
        "status": "active", "page_size": 200,
    }).json()["items"]
    assert summary["expired_active_contracts"] == sum(item["expired"] for item in active_contracts)
    assert summary["expired_active_contracts"] == 1


def test_work_order_sla_urgent_and_complaint_parent(client):
    parent = client.post(f"{P}/work-orders", json={
        "type": "repair", "description": "公共区域漏水", "code": "MANUAL-IGNORED",
    })
    assert parent.status_code == 201, parent.text
    assert parent.json()["code"] != "MANUAL-IGNORED"
    overdue = client.post(f"{P}/work-orders", json={
        "type": "complaint", "description": "投诉漏水处理进度", "priority": "urgent",
        "parent_id": parent.json()["id"], "due_at": "2026-01-01T09:00:00",
    })
    assert overdue.status_code == 201, overdue.text
    assert overdue.json()["priority"] == "urgent"
    assert overdue.json()["priority_label"] == "紧急"
    assert overdue.json()["parent_id"] == parent.json()["id"]
    assert overdue.json()["parent_code"] == parent.json()["code"]
    assert overdue.json()["due_at"] is not None
    assert overdue.json()["overdue"] is True

    future = client.post(f"{P}/work-orders", json={
        "description": "未来到期", "due_at": "2999-01-01T09:00:00",
    })
    assert future.json()["overdue"] is False
    done = client.put(f"{P}/work-orders/{overdue.json()['id']}", json={"status": "done"})
    assert done.json()["overdue"] is False
    missing_parent = client.post(f"{P}/work-orders", json={
        "type": "complaint", "description": "父工单不存在", "parent_id": 999999,
    })
    assert missing_parent.status_code == 400
    assert "父工单" in missing_parent.json()["detail"]
    self_parent = client.put(f"{P}/work-orders/{parent.json()['id']}",
                             json={"parent_id": parent.json()["id"]})
    assert self_parent.status_code == 400
    assert "父工单" in self_parent.json()["detail"]


def test_work_order_done_to_closed_preserves_first_completion_at(client):
    created = client.post(f"{P}/work-orders", json={"description": "首次完成时间"})
    order_id = created.json()["id"]
    done = client.put(f"{P}/work-orders/{order_id}", json={"status": "done"})
    first_completed_at = done.json()["closed_at"]
    assert first_completed_at is not None

    closed = client.put(f"{P}/work-orders/{order_id}", json={"status": "closed"})
    assert closed.status_code == 200, closed.text
    assert closed.json()["closed_at"] == first_completed_at


def test_work_order_followup_crud_and_cascade(client):
    from sqlalchemy import text
    from app.database import SessionLocal

    order = client.post(f"{P}/work-orders", json={"description": "处理留痕"}).json()
    url = f"{P}/work-orders/{order['id']}/followups"
    assert client.get(url).json() == []
    created = client.post(url, json={
        "handled_at": "2026-10-31T10:30:00", "content": "现场检查",
        "result": "已临时止漏", "satisfaction": 5,
    })
    assert created.status_code == 201, created.text
    followup = created.json()
    assert followup["work_order_id"] == order["id"]
    assert followup["handled_at"] is not None
    assert followup["content"] == "现场检查"
    assert followup["result"] == "已临时止漏"
    assert followup["satisfaction"] == 5

    updated = client.put(f"{url}/{followup['id']}", json={
        "result": "复查无渗漏", "satisfaction": None,
    })
    assert updated.status_code == 200, updated.text
    assert updated.json()["content"] == "现场检查"
    assert updated.json()["result"] == "复查无渗漏"
    assert updated.json()["satisfaction"] is None
    listed = client.get(url).json()
    assert len(listed) == 1 and listed[0]["id"] == followup["id"]

    assert client.delete(f"{url}/{followup['id']}").status_code == 200
    assert client.get(url).json() == []
    second = client.post(url, json={"content": "二次跟进", "result": "待复查"})
    assert second.status_code == 201
    assert client.delete(f"{P}/work-orders/{order['id']}").status_code == 200
    with SessionLocal() as db:
        count = db.execute(text(
            "SELECT COUNT(*) FROM mod_ops_work_order_followup WHERE work_order_id=:id"
        ), {"id": order["id"]}).scalar()
    assert count == 0


def test_parent_delete_requires_unlink_then_preserves_followup_cascade(client):
    from sqlalchemy import text
    from app.database import SessionLocal

    parent = client.post(f"{P}/work-orders", json={"description": "父工单"}).json()
    child = client.post(f"{P}/work-orders", json={
        "type": "complaint", "description": "关联投诉", "parent_id": parent["id"],
    }).json()
    followup_url = f"{P}/work-orders/{parent['id']}/followups"
    assert client.post(followup_url, json={"content": "父单处理留痕"}).status_code == 201

    rejected = client.delete(f"{P}/work-orders/{parent['id']}")
    assert rejected.status_code == 409, rejected.text
    assert "子工单" in rejected.json()["detail"]
    assert "解除" in rejected.json()["detail"]
    assert client.get(f"{P}/work-orders", params={"keyword": "父工单"}).json()["total"] == 1
    assert len(client.get(followup_url).json()) == 1

    unlinked = client.put(f"{P}/work-orders/{child['id']}", json={"parent_id": None})
    assert unlinked.status_code == 200, unlinked.text
    assert unlinked.json()["parent_id"] is None
    assert client.delete(f"{P}/work-orders/{parent['id']}").status_code == 200
    remaining = client.get(f"{P}/work-orders", params={"page_size": 200}).json()["items"]
    assert [item["id"] for item in remaining] == [child["id"]]
    assert remaining[0]["parent_id"] is None
    with SessionLocal() as db:
        count = db.execute(text(
            "SELECT COUNT(*) FROM mod_ops_work_order_followup WHERE work_order_id=:id"
        ), {"id": parent["id"]}).scalar()
    assert count == 0


def test_work_order_export_matches_filtered_list_and_content(client):
    from io import BytesIO
    from openpyxl import load_workbook

    parent = client.post(f"{P}/work-orders", json={"description": "父报修"}).json()
    complaint = client.post(f"{P}/work-orders", json={
        "type": "complaint", "description": "=投诉未处理", "priority": "urgent",
        "parent_id": parent["id"], "due_at": "2026-01-01T09:00:00",
    }).json()
    client.post(f"{P}/work-orders", json={
        "type": "complaint", "description": "其他投诉", "status": "done",
    })
    params = {"type": "complaint", "status": "open", "keyword": "投诉", "page_size": 200}
    listed = client.get(f"{P}/work-orders", params=params).json()
    assert listed["total"] == 1
    assert listed["items"][0]["id"] == complaint["id"]

    exported = client.get(f"{P}/export/work-orders.xlsx", params=params)
    assert exported.status_code == 200 and exported.content[:2] == b"PK"
    sheet = load_workbook(BytesIO(exported.content)).active
    rows = list(sheet.values)
    assert len(rows) == 2
    values = dict(zip(rows[0], rows[1]))
    assert values["工单号"] == complaint["code"]
    assert values["描述"] == "'=投诉未处理"
    assert values["优先级"] == "紧急"
    assert values["是否超期"] == "是"
    assert values["父工单号"] == parent["code"]
    assert values["要求完成时间"]


def test_work_order_code_generated_and_historical_duplicates_remain_readable(client):
    from app.database import SessionLocal
    from app.modules.business_operations.models import OpsWorkOrder

    first = client.post(f"{P}/work-orders", json={
        "description": "自动编号一", "code": "MANUAL-1",
    }).json()
    second = client.post(f"{P}/work-orders", json={
        "description": "自动编号二", "code": first["code"],
    }).json()
    assert first["code"].startswith("WO") and second["code"].startswith("WO")
    assert first["code"] != second["code"]
    assert first["code"] != "MANUAL-1"

    with SessionLocal() as db:
        db.query(OpsWorkOrder).filter(OpsWorkOrder.id == second["id"]).update(
            {"code": first["code"]}
        )
        db.commit()
    listed = client.get(f"{P}/work-orders", params={"page_size": 200}).json()
    assert listed["total"] == 2
    assert [item["code"] for item in listed["items"]].count(first["code"]) == 2


def test_work_order_column_patches_are_idempotent_without_unique_code(tmp_path, monkeypatch):
    import sqlite3
    from sqlalchemy import create_engine, text
    from app import main as app_main

    path = tmp_path / "old_operations.db"
    raw = sqlite3.connect(path)
    raw.execute("""CREATE TABLE mod_ops_work_order (
        id INTEGER PRIMARY KEY, code VARCHAR(30), type VARCHAR(20), location VARCHAR(100),
        tenant VARCHAR(200), priority VARCHAR(10), status VARCHAR(20), description TEXT,
        assignee VARCHAR(50), created_at DATETIME, closed_at DATETIME
    )""")
    raw.execute("INSERT INTO mod_ops_work_order VALUES (1, 'DUP', 'repair', '', '', 'medium', 'open', '一', '', CURRENT_TIMESTAMP, NULL)")
    raw.execute("INSERT INTO mod_ops_work_order VALUES (2, 'DUP', 'repair', '', '', 'medium', 'open', '二', '', CURRENT_TIMESTAMP, NULL)")
    raw.commit()
    raw.close()

    old_engine = create_engine(f"sqlite:///{path.as_posix()}")
    monkeypatch.setattr(app_main, "engine", old_engine)
    try:
        app_main._ensure_sqlite_columns()
        with old_engine.connect() as conn:
            columns = {row[1] for row in conn.execute(text("PRAGMA table_info(mod_ops_work_order)"))}
            assert {"due_at", "parent_id"} <= columns
            first_schema = conn.execute(text(
                "SELECT sql FROM sqlite_master WHERE name='mod_ops_work_order'"
            )).scalar()
            first_rows = conn.execute(text(
                "SELECT id, code FROM mod_ops_work_order ORDER BY id"
            )).fetchall()
        app_main._ensure_sqlite_columns()
        with old_engine.connect() as conn:
            second_schema = conn.execute(text(
                "SELECT sql FROM sqlite_master WHERE name='mod_ops_work_order'"
            )).scalar()
            second_rows = conn.execute(text(
                "SELECT id, code FROM mod_ops_work_order ORDER BY id"
            )).fetchall()
            indexes = conn.execute(text("PRAGMA index_list(mod_ops_work_order)")).fetchall()
        assert first_schema == second_schema
        assert first_rows == second_rows == [(1, "DUP"), (2, "DUP")]
        assert not any(row[2] for row in indexes)
    finally:
        old_engine.dispose()
