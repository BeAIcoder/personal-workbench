# -*- coding: utf-8 -*-
"""Task 8：有基准证据的低风险 Minor 与性能项。

覆盖：
- BIP 余额唯一索引新名 `uq_bip_balance_scope` 的冲突分类；
- 数据仓质量缓存键跟随 DuckDB 文件亚秒变化；
- 删除已终止合同不得误释放同租户更新的 signed 线索；
- 记忆 Markdown 批量导入只预取一次既有内容；
- RAG 检索复用索引内的词频，不再逐 chunk 重新分词。
"""
from __future__ import annotations

import os

import pytest
from sqlalchemy import event
from sqlalchemy.exc import IntegrityError

from app.agents import memory, rag
from app.database import SessionLocal
from app.models import Note
from app.modules.business_leasing.models import LeasingLead
from app.modules.business_leasing import service as leasing_svc
from app.modules.business_facility import service as facility_svc
from app.modules.business_operations import service as ops_svc
from app.modules.finance_ledger_bip import service as bip_svc
from app.modules.finance_vault import service as vault_svc

OPS = "/api/business/operations"
LEASING = "/api/business/leasing"


def _integrity(message: str) -> IntegrityError:
    return IntegrityError("stmt", {}, Exception(message))


def test_bip_balance_new_index_name_is_unique_conflict():
    """新索引名也必须按「余额并发写入」分类（409，可重试）。"""
    msg, conflict = bip_svc._classify_constraint_error(
        _integrity("UNIQUE constraint failed: index 'uq_bip_balance_scope'")
    )
    assert conflict is True
    assert "余额" in msg


def test_quality_cache_key_tracks_subsecond_file_change(tmp_path, monkeypatch):
    """同一秒内重建数据仓也要失效缓存：键包含 DuckDB 文件 mtime_ns。"""
    db_path = tmp_path / "vault.duckdb"
    db_path.write_bytes(b"x")
    monkeypatch.setattr(vault_svc, "_db_path", lambda: db_path)
    datasets = [{"table": "t", "rows": 1, "updated_at": "2026-11-04 10:00:00"}]

    first = vault_svc._quality_cache_key(datasets)
    stat = db_path.stat()
    os.utime(db_path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
    second = vault_svc._quality_cache_key(datasets)

    assert first != second


def _make_unit(client, unit_no: str) -> int:
    resp = client.post(f"{LEASING}/units", json={
        "building": "A栋", "floor": "1F", "unit_no": unit_no, "area": 100.0,
    })
    assert resp.status_code in (200, 201), resp.text
    return resp.json()["id"]


def _make_signed_lead(unit_id: int, company: str) -> int:
    with SessionLocal() as db:
        row = LeasingLead(company=company, stage="signed", unit_id=unit_id)
        db.add(row)
        db.commit()
        return row.id


def _make_contract(client, unit_id: int, no: str) -> int:
    resp = client.post(f"{OPS}/contracts", json={
        "contract_no": no, "tenant": "同租户", "unit_no": "",
        "area": 100.0, "start_date": "2026-01-01", "end_date": "2026-12-31",
        "rent_monthly": 10000.0, "property_fee_monthly": 1200.0, "deposit": 0.0,
        "unit_id": unit_id,
    })
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def test_deleting_terminated_contract_keeps_newer_same_tenant_lead(client):
    """已终止合同在终止时已完成释放；删除它不能误释放之后新签的同租户线索。"""
    unit_id = _make_unit(client, "U-REL-8")
    _make_signed_lead(unit_id, "同租户")
    contract_id = _make_contract(client, unit_id, "HT-REL-8")

    terminated = client.put(f"{OPS}/contracts/{contract_id}", json={"status": "terminated"})
    assert terminated.status_code == 200, terminated.text
    newer_lead_id = _make_signed_lead(unit_id, "同租户")

    assert client.delete(f"{OPS}/contracts/{contract_id}").status_code == 200
    with SessionLocal() as db:
        assert db.get(LeasingLead, newer_lead_id).released_at is None


def test_remote_option_search_matches_identifiers(client):
    """关联下拉按标签搜索：铺位 unit_no/building、设备 code、工单 code 都要命中。"""
    unit = client.post(f"{LEASING}/units", json={
        "building": "B栋", "floor": "3F", "unit_no": "U-8801", "area": 88.0,
    }).json()
    equipment = client.post("/api/business/facility/equipment", json={
        "code": "EQ-8801", "name": "冷却塔", "category": "B", "location": "屋面",
        "install_date": "2026-01-01", "status": "running", "note": "",
    })
    assert equipment.status_code == 201, equipment.text
    equipment = equipment.json()
    order = client.post(f"{OPS}/work-orders", json={
        "type": "repair", "priority": "medium", "status": "open",
        "location": "3F", "tenant": "", "assignee": "", "description": "空调异响",
    })
    assert order.status_code == 201, order.text
    order = order.json()

    with SessionLocal() as db:
        assert any(row["id"] == unit["id"] for row in leasing_svc.unit_list(db, keyword="U-8801")["items"])
        assert any(row["id"] == unit["id"] for row in leasing_svc.unit_list(db, keyword="B栋")["items"])
        assert any(row["id"] == equipment["id"] for row in facility_svc.equipment_list(db, keyword="EQ-8801")["items"])
        assert any(row["id"] == equipment["id"] for row in facility_svc.equipment_list(db, keyword="冷却塔")["items"])
        assert any(row["id"] == order["id"] for row in ops_svc.work_order_list(db, keyword=order["code"])["items"])
        assert any(row["id"] == order["id"] for row in ops_svc.work_order_list(db, keyword="空调异响")["items"])


def test_memory_markdown_import_prefetches_existing_once(client):
    """30 条批量导入不能每条全表扫一次：SELECT memories 只允许一次。"""
    text = "## project\n" + "\n".join(f"- 批量记忆条目编号-{i:03d}-唯一" for i in range(30)) + "\n"
    statements: list[str] = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    with SessionLocal() as db:
        bind = db.get_bind()
        event.listen(bind, "before_cursor_execute", capture)
        try:
            result = memory.import_markdown(db, text)
        finally:
            event.remove(bind, "before_cursor_execute", capture)

    assert result == {"added": 30, "skipped": 0}
    selects = [s for s in statements if "FROM MEMORIES" in s.upper()]
    assert len(selects) == 1


def test_rag_search_reuses_cached_chunk_tf(client):
    """检索只对查询分词一次；chunk 词频来自索引缓存。"""
    with SessionLocal() as db:
        db.add(Note(title="免租期纪要", content="本周拜访品牌客户，重点沟通租金阶梯与免租期方案。"))
        db.commit()
    rag.rebuild_index()

    calls: list[str] = []
    real_bigrams = rag._bigrams

    def counting(text):
        calls.append(text)
        return real_bigrams(text)

    rag._bigrams = counting
    try:
        hits = rag.search("免租期方案", top_k=3)
    finally:
        rag._bigrams = real_bigrams

    assert hits, "应命中免租期纪要"
    assert len(calls) == 1, f"只应对查询分词一次，实际调用 {len(calls)} 次"
