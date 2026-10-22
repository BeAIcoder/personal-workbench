# -*- coding: utf-8 -*-
"""Swarm 第三轮后端加固聚焦测试（独立审查 F1/F2/F3/F4/F5/F6/F7/F8/F9/F11）。

先用真实复现断言当前缺陷，再最小修复；全部使用临时数据库/合成数据，
不调用真实 LLM/外部引擎，不读取真实业务数据。
"""
import csv
import io
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.database import SessionLocal
from app.main import _origin_allowed
from app.models import McpServer, Schedule
from app.modules.finance_board import router as board_router
from app.modules.finance_board import service as board_svc
from app.modules.finance_ledger_bip import service as bip_svc
from app.modules.finance_ledger_bip.models import BipBatch, BipVoucher
from app.modules.finance_ledger_personal import service as personal_svc
from app.modules.finance_ledger_personal.models import PlAccount, PlTransaction
from app.modules.finance_tax.models import Policy
from app.modules.finance_vault import service as vault_svc
from app.routers import dashboard


# ---------- F1 Origin 同源回退不得循环放行 ----------


def test_origin_allowed_rejects_evil_origin_with_evil_host():
    assert _origin_allowed("http://evil.example.com", "evil.example.com") is False
    assert _origin_allowed("http://evil.example.com:8000", "evil.example.com:8000") is False


def test_origin_allowed_keeps_loopback_and_configured_sources():
    assert _origin_allowed("http://127.0.0.1:5173", "127.0.0.1:8000") is True
    assert _origin_allowed("http://localhost:5173", "localhost:8000") is True


# ---------- F2 MCP env 静态加密 ----------


def test_mcp_env_is_sealed_at_rest_and_unsealed_for_server(client):
    payload = {
        "name": "sealprobe",
        "command": sys.executable,
        "args": ["-c", "pass"],
        "env": {"SECRET_TOKEN": "synthetic-secret-value"},
        "enabled": False,
    }
    created = client.post("/api/mcp/servers", json=payload)
    assert created.status_code == 201, created.text
    assert created.json()["env"] == {"SECRET_TOKEN": "***"}
    with SessionLocal() as db:
        row = db.get(McpServer, created.json()["id"])
        stored = json.dumps(row.env, ensure_ascii=False)
        assert "synthetic-secret-value" not in stored
        assert row.env["SECRET_TOKEN"].startswith("dpapi:")

    with SessionLocal() as db:
        row = db.get(McpServer, created.json()["id"])
        from app.agents.mcp import _row_to_server

        assert _row_to_server(row).env["SECRET_TOKEN"] == "synthetic-secret-value"


def test_mcp_update_masked_env_keeps_existing_secret(client):
    payload = {
        "name": "sealupdate",
        "command": sys.executable,
        "args": ["-c", "pass"],
        "env": {"SECRET_TOKEN": "synthetic-secret-value"},
        "enabled": False,
    }
    created = client.post("/api/mcp/servers", json=payload).json()
    updated = client.put(
        f"/api/mcp/servers/{created['id']}",
        json={"env": {"SECRET_TOKEN": "***"}, "enabled": False},
    )
    assert updated.status_code == 200, updated.text
    with SessionLocal() as db:
        row = db.get(McpServer, created["id"])
        from app.agents.mcp import _row_to_server

        assert _row_to_server(row).env["SECRET_TOKEN"] == "synthetic-secret-value"
        assert row.env["SECRET_TOKEN"].startswith("dpapi:")


def test_mcp_migrate_env_secrets_converts_legacy_plaintext(client):
    from app.agents.mcp import migrate_env_secrets

    with SessionLocal() as db:
        db.add(McpServer(name="legacyplain", command="x", args=[], env={"A": "legacy-secret"}, enabled=False))
        db.commit()
        assert migrate_env_secrets(db) == 1
        row = db.query(McpServer).filter(McpServer.name == "legacyplain").one()
        assert row.env["A"].startswith("dpapi:")
        assert "legacy-secret" not in json.dumps(row.env, ensure_ascii=False)


# ---------- F3 看板快照路径边界 ----------


def test_board_snapshot_html_rejects_path_outside_snapshots(client, monkeypatch, tmp_path):
    snapshots = tmp_path / "snapshots"
    snapshots.mkdir()
    outside = tmp_path / "outside.html"
    outside.write_text("<html>synthetic</html>", encoding="utf-8")
    monkeypatch.setattr(board_svc, "get_snapshot", lambda db, sid: SimpleNamespace(html_path=str(outside)))
    monkeypatch.setattr(board_svc, "_snapshots_dir", lambda: snapshots)
    with pytest.raises(HTTPException) as excinfo:
        board_router.snapshot_html(1, db=SessionLocal())
    assert excinfo.value.status_code == 403


def test_board_snapshot_html_allows_snapshot_inside_root(client, monkeypatch, tmp_path):
    snapshots = tmp_path / "snapshots"
    inside_dir = snapshots / "20261022_120000"
    inside_dir.mkdir(parents=True)
    inside = inside_dir / "board.html"
    inside.write_text("<html>synthetic</html>", encoding="utf-8")
    monkeypatch.setattr(board_svc, "get_snapshot", lambda db, sid: SimpleNamespace(html_path=str(inside)))
    monkeypatch.setattr(board_svc, "_snapshots_dir", lambda: snapshots)
    resp = board_router.snapshot_html(1, db=SessionLocal())
    assert resp.status_code == 200
    assert resp.headers.get("x-content-type-options") == "nosniff"


# ---------- F4 BIP 导出入仓先看行数护栏 ----------


def test_bip_export_rejects_over_limit_before_full_fetch(client, monkeypatch):
    monkeypatch.setattr(bip_svc, "MAX_IMPORT_ROWS", 1)

    def _must_not_ingest(*args, **kwargs):
        raise AssertionError("超过行数护栏后不得组装 DataFrame 或调用入仓")

    monkeypatch.setattr(bip_svc, "_load_vault_ingest", lambda: _must_not_ingest)
    with SessionLocal() as db:
        batch = BipBatch(filename="synthetic.xlsx", file_type="voucher")
        db.add(batch)
        db.flush()
        for i in range(2):
            db.add(BipVoucher(batch_id=batch.id, period="2026-09", voucher_date="2026-09-01",
                              ledger="测试账簿", voucher_no=f"V{i:03d}", summary="合成凭证"))
        db.commit()
        with pytest.raises(ValueError, match="上限"):
            bip_svc.export_vouchers_to_vault(db)


# ---------- F5 finance_tax 不再污染 sys.path ----------


def test_tax_routers_do_not_insert_package_dir_into_sys_path():
    tax_dir = str(Path(__file__).resolve().parents[1] / "app" / "modules" / "finance_tax")
    assert tax_dir not in sys.path


# ---------- F6 policies 关键词 %/_ 按字面匹配 ----------


def test_policy_keyword_percent_is_literal(client):
    with SessionLocal() as db:
        db.add(Policy(policy_number="P-LITERAL", title="100%报销政策", summary="含百分号",
                      policy_type="费用", effective_date=datetime(2026, 1, 1)))
        db.add(Policy(policy_number="P-OTHER", title="普通政策", summary="无百分号",
                      policy_type="费用", effective_date=datetime(2026, 1, 1)))
        db.commit()
    resp = client.get("/api/finance/tax/policies/", params={"keyword": "100%"})
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    assert [i["policy_number"] for i in items] == ["P-LITERAL"]


# ---------- F7 个人 CSV 导出防公式注入 ----------


def test_personal_csv_export_neutralizes_formula_cells(client):
    with SessionLocal() as db:
        acc = PlAccount(name="合成账户", kind="cash")
        db.add(acc)
        db.flush()
        db.add(PlTransaction(kind="expense", amount=12.5, occurred_on=datetime(2026, 9, 1).date(),
                             account_id=acc.id, note="=1+1"))
        db.add(PlTransaction(kind="expense", amount=3.5, occurred_on=datetime(2026, 9, 2).date(),
                             account_id=acc.id, note="普通备注"))
        db.commit()
        text = personal_svc.export_csv(db, month="2026-09")
    rows = list(csv.reader(io.StringIO(text)))
    dangerous = [r for r in rows if r and r[-1] == "=1+1"]
    assert not dangerous
    assert any(r and r[-1] == "'=1+1" for r in rows)
    assert any(r and r[-1] == "普通备注" for r in rows)


# ---------- F8 税务列表分页参数有界 ----------


def test_tax_return_list_rejects_unbounded_limit(client):
    assert client.get("/api/finance/tax/tax-returns/", params={"limit": 10**9}).status_code == 422
    assert client.get("/api/finance/tax/tax-returns/", params={"skip": -1}).status_code == 422
    assert client.get("/api/finance/tax/enterprises/", params={"limit": 10**9}).status_code == 422


# ---------- F9 今日日程有界且可见截断 ----------


def test_dashboard_today_schedules_are_bounded_and_truncation_visible(client, monkeypatch):
    monkeypatch.setattr(dashboard, "MAX_TODAY_SCHEDULES", 1)
    now = datetime.now()
    day_start = datetime.combine(now.date(), datetime.min.time())
    with SessionLocal() as db:
        for i in range(2):
            db.add(Schedule(title=f"合成日程{i}", start_time=day_start + timedelta(hours=9 + i),
                            end_time=day_start + timedelta(hours=10 + i)))
        db.commit()
    data = client.get("/api/dashboard/summary").json()
    assert data["schedule"]["today_schedule_total"] == 2
    assert data["schedule"]["today_schedules_truncated"] is True
    assert len(data["today_schedules"]) == 1


# ---------- F11 强制 LIMIT 不得改写字面量 ----------


def test_vault_force_limit_does_not_modify_string_literal():
    final_sql = vault_svc.validate_readonly_sql(
        "SELECT 'LIMIT 999999' AS x FROM t LIMIT 999999", {"t"}
    )
    assert "'LIMIT 999999'" in final_sql
    assert final_sql.rstrip().endswith(f"LIMIT {vault_svc.MAX_QUERY_ROWS + 1}")
