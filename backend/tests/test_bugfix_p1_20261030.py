# -*- coding: utf-8 -*-
"""BUGfix P1 回归（2026-10-30 第二轮）。

覆盖：日程/定时任务显式 null → 422、任务不变量、发票冲红金额不可解析不误配、
非回环绑定守卫、备份/恢复脚本。
"""
import asyncio
import importlib.util
import sqlite3
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.agents import orchestrator
from app.main import LoopbackBindingGuardMiddleware
from app.modules.finance_iit import service as iit_service
from app.modules.finance_invoice import service as invoice_service

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def _load_script(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------- 日程/任务显式 null ----------------

def test_schedule_update_explicit_null_is_422(client):
    sid = client.post("/api/schedules", json={
        "title": "回归日程", "start_time": "2026-10-30T09:00:00",
        "end_time": "2026-10-30T10:00:00",
    }).json()["id"]
    r = client.put(f"/api/schedules/{sid}", json={"title": None})
    assert r.status_code == 422 and "标题" in r.text
    # 正常更新仍可用
    assert client.put(f"/api/schedules/{sid}", json={"location": "会议室B"}).status_code == 200


def test_job_update_explicit_null_and_empty_prompt_are_422(client):
    jid = client.post("/api/jobs", json={
        "name": "回归任务", "prompt": "汇总今日事项",
        "schedule_type": "interval", "interval_minutes": 60,
    }).json()["id"]
    assert client.put(f"/api/jobs/{jid}", json={"name": None}).status_code == 422
    assert client.put(f"/api/jobs/{jid}", json={"prompt": ""}).status_code == 422
    # 列表仍可序列化（不会被脏行打成 500）
    assert client.get("/api/jobs").status_code == 200


# ---------------- 发票冲红：金额不可解析不误配 ----------------

def test_pair_red_invoices_skips_unparseable_amounts():
    red = SimpleNamespace(id=1, amount="", buyer_name="合成商户", paired_item_id=None,
                          status="parsed", invoice_direction="冲红发票")
    pos = SimpleNamespace(id=2, amount="面议", buyer_name="合成商户", paired_item_id=None,
                          status="parsed", invoice_direction="正数发票")
    matched, unmatched = invoice_service.pair_red_invoices([red, pos])
    assert matched == 0 and unmatched == 1
    assert red.paired_item_id is None and pos.paired_item_id is None

    red2 = SimpleNamespace(id=3, amount="-100.00", buyer_name="合成商户", paired_item_id=None,
                           status="parsed", invoice_direction="冲红发票")
    pos2 = SimpleNamespace(id=4, amount="100.00", buyer_name="合成商户", paired_item_id=None,
                           status="parsed", invoice_direction="正数发票")
    matched, unmatched = invoice_service.pair_red_invoices([red2, pos2])
    assert matched == 1 and unmatched == 0
    assert red2.paired_item_id == 4 and pos2.paired_item_id == 3


# ---------------- 非回环绑定守卫 ----------------

class _DummyApp:
    async def __call__(self, scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})


def _call_guard(path: str, server: tuple[str, int]) -> int:
    guard = LoopbackBindingGuardMiddleware(_DummyApp())
    sent: list[dict] = []

    async def receive():
        return {"type": "http.request", "body": b""}

    async def send(message):
        sent.append(message)

    asyncio.run(guard({"type": "http", "path": path, "server": server,
                       "method": "GET", "headers": []}, receive, send))
    return sent[0]["status"]


def test_non_loopback_binding_is_rejected_by_default():
    assert _call_guard("/api/tasks", ("127.0.0.1", 8000)) == 200
    assert _call_guard("/api/tasks", ("0.0.0.0", 8000)) == 503
    # 健康检查始终放行，便于容器/局域网探活
    assert _call_guard("/api/health", ("0.0.0.0", 8000)) == 200
    # 静态页面不拦，用户能看到中文提示（API 503）
    assert _call_guard("/index.html", ("0.0.0.0", 8000)) == 200


# ---------------- 备份 / 恢复 ----------------

def test_backup_and_restore_roundtrip(tmp_path):
    backup_mod = _load_script("backup_db")
    restore_mod = _load_script("restore_backup")

    data = tmp_path / "data"
    (data / "uploads").mkdir(parents=True)
    (data / "finance" / "pos").mkdir(parents=True)
    (data / "skills" / "demo").mkdir(parents=True)
    (data / "uploads" / "a.txt").write_text("附件", encoding="utf-8")
    (data / "finance" / "pos" / "b.xlsx").write_bytes(b"PK")
    (data / "skills" / "demo" / "SKILL.md").write_text("demo", encoding="utf-8")
    (data / "workbuddy_import.json").write_text("{}", encoding="utf-8")
    (data / "uploads" / "skip.tmp").write_text("tmp", encoding="utf-8")

    db_path = data / "workbench.db"
    con = sqlite3.connect(db_path)
    con.execute("CREATE TABLE t (x INTEGER)")
    con.execute("INSERT INTO t VALUES (42)")
    con.commit()
    con.close()

    backups = tmp_path / "backups"
    db_backup = backup_mod.backup_database(db_path, backups, "20261030_120000")
    assert db_backup.is_file()
    zip_path, warnings = backup_mod.backup_data_zip(db_backup, data, backups, "20261030_120000")
    assert warnings == []
    with zipfile.ZipFile(zip_path) as zf:
        names = set(zf.namelist())
    assert "workbench.db" in names
    assert "uploads/a.txt" in names
    assert "finance/pos/b.xlsx" in names
    assert "skills/demo/SKILL.md" in names
    assert "workbuddy_import.json" in names
    assert "uploads/skip.tmp" not in names

    # 恢复到新目录（无 workbench.db，无需 force）；显式指定财务目录，避免测试环境
    # 的 FINANCE_DATA_DIR 把 finance/ 分流到全局临时目录
    target = tmp_path / "restore"
    finance_target = target / "finance"
    restored = restore_mod.restore(zip_path, target, finance_target=finance_target)
    assert set(restored) == names
    got = sqlite3.connect(target / "workbench.db")
    assert got.execute("SELECT x FROM t").fetchone()[0] == 42
    got.close()
    assert (target / "uploads" / "a.txt").read_text(encoding="utf-8") == "附件"

    # 已有 workbench.db 且未 force → 拒绝
    with pytest.raises(FileExistsError):
        restore_mod.restore(zip_path, target, finance_target=finance_target)
    # force → 原子换入，旧数据整体快照为 <target>.before_restore_*
    restore_mod.restore(zip_path, target, force=True, finance_target=finance_target)
    snapshots = list(target.parent.glob("restore.before_restore_*"))
    assert snapshots, "force 恢复应留下整目录快照"
    # 快照内容与旧库一致（可继续作为回滚点）
    prev = sqlite3.connect(snapshots[-1] / "workbench.db")
    assert prev.execute("SELECT x FROM t").fetchone()[0] == 42
    prev.close()


# ---------------- AGT-02：同会话运行锁 ----------------

def test_session_run_lock_serializes_and_cleans_up():
    async def scenario():
        lock = orchestrator.session_run_lock("sess-lock-test")
        events = []

        async def worker(tag):
            if lock.locked():
                events.append(f"busy-{tag}")
                return
            async with lock:
                events.append(f"start-{tag}")
                await asyncio.sleep(0.05)
                events.append(f"end-{tag}")

        await asyncio.gather(worker("a"), worker("b"))
        return events

    events = asyncio.run(scenario())
    assert events == ["start-a", "busy-b", "end-a"]
    orchestrator.invalidate()  # 未持有的运行锁随配置失效清理
    assert "sess-lock-test" not in orchestrator._session_run_locks


# ---------------- FIL-08/09：工号分组与月份连续 ----------------

def test_iit_same_name_different_employee_id_stay_separate():
    calc = iit_service.calc_filing("2026", [
        {"month": 1, "name": "张伟", "employee_id": "E001", "salary": 20_000},
        {"month": 1, "name": "张伟", "employee_id": "E002", "salary": 20_000},
    ])
    assert len(calc["employees"]) == 2
    assert {e["employee_id"] for e in calc["employees"]} == {"E001", "E002"}
    assert all(e["months"][0]["monthly_withholding"] == pytest.approx(450.0)
               for e in calc["employees"])
    assert calc["totals"]["tax_withheld"] == pytest.approx(900.0)


def test_iit_missing_month_keeps_deduction_and_warns():
    calc = iit_service.calc_filing("2026", [
        {"month": 1, "name": "员工甲", "employment_start_month": 1, "salary": 20_000},
        {"month": 3, "name": "员工甲", "employment_start_month": 1, "salary": 20_000},
    ])
    emp = calc["employees"][0]
    assert emp["missing_months"] == [2]
    assert emp["employment_start_month"] == 1
    # 3 月累计：40000 − 5000×3 = 25000（旧逻辑只按 2 行算 30000）
    assert emp["months"][1]["cumulative_taxable"] == pytest.approx(25_000.0)
    assert calc["warnings"] and "缺少 [2] 月" in calc["warnings"][0]


def test_iit_duplicate_month_rejected():
    with pytest.raises(ValueError):
        iit_service.calc_filing("2026", [
            {"month": 1, "name": "员工甲", "salary": 10_000},
            {"month": 1, "name": "员工甲", "salary": 10_000},
        ])
