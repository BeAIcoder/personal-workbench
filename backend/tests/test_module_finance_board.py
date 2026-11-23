"""finance_board 模块测试。

管线不可在测试中真实运行（依赖本机微信文件目录与耗时），因此：
- 用合成数据验证快照登记/查询/ latest / HTML 端点（monkeypatch 快照目录）；
- 验证管线目录推导、PipelineUnavailableError 降级（503）；
- 验证模块注册与菜单。
不写入真实数据目录，不触发真实管线。
"""
import json
import subprocess
import threading
import time
from pathlib import Path

import pytest

from app.modules import get_loaded_modules
from app.modules.finance_board import runs as board_runs
from app.modules.finance_board import service as svc

P = "/api/finance/board"


@pytest.fixture()
def board_dir(monkeypatch, tmp_path):
    """快照目录与登记表重定向到 tmp_path。"""
    base = tmp_path / "board_data"
    monkeypatch.setattr(svc, "get_finance_data_dir", lambda: base)
    return base


def _fake_snapshot(monkeypatch, base: Path, stamp: str = "20261012_120000") -> Path:
    """造一个假快照 HTML 并登记。"""
    snap_dir = base / "finance_board" / "snapshots" / stamp
    snap_dir.mkdir(parents=True, exist_ok=True)
    html = snap_dir / "经营财务报表监控看板.html"
    html.write_text("<html><body>smoke dashboard</body></html>", encoding="utf-8")
    snap = svc._save_snapshot(stamp, "ok", str(html), "fake log", 1.23)
    assert snap.id
    return html


def test_module_registration():
    loaded = {m.name: m for m in get_loaded_modules()}
    assert "finance_board" in loaded
    assert loaded["finance_board"].prefix == "/api/finance/board"
    # 2026-11-17：manifest 的 tables/menu 字段已删除，建表单一源是 models 的 __tablename__
    from app.database import Base
    from app.modules.finance_board.models import BoardSnapshot

    assert BoardSnapshot.__tablename__ == "mod_board_snapshot"
    assert "mod_board_snapshot" in Base.metadata.tables
    assert loaded["finance_board"].removed_fields == ()


def test_pipeline_dir_resolution():
    """默认推导应指向AssetLib内的微信报表监控目录。"""
    try:
        d = svc.pipeline_dir()
        assert d.name == "微信报表监控"
        assert (d / "scripts").is_dir()
    except svc.PipelineUnavailableError:
        pytest.skip("本机未找到微信报表监控目录（配置 finance_board_pipeline_path 可指定）")


def test_run_pipeline_unavailable_returns_503(client, monkeypatch):
    """管线目录无效 → run 返回 503 中文提示（不崩溃）。"""
    monkeypatch.setattr(svc, "pipeline_dir", lambda: Path("Z:/不存在/微信报表监控"))
    resp = client.post(f"{P}/run")
    assert resp.status_code == 503
    assert "管线" in resp.json()["detail"]


def _prepare_pipeline_env(monkeypatch, tmp_path):
    """重定向快照目录 + 造一个假管线目录（scripts 齐、output 空）。"""
    base = tmp_path / "board_data"
    monkeypatch.setattr(svc, "get_finance_data_dir", lambda: base)
    pdir = tmp_path / "pipe"
    (pdir / "scripts").mkdir(parents=True)
    (pdir / "output").mkdir()
    monkeypatch.setattr(svc, "pipeline_dir", lambda: pdir)
    monkeypatch.setattr(svc, "_scheduled_task_available", lambda: False)
    monkeypatch.setattr(svc, "_RETRY_DELAY_SECONDS", 0)
    return pdir


def test_run_pipeline_retry_then_success(client, monkeypatch, tmp_path):
    """v4：首次失败自动重试，第二次成功 → 200 且最新快照 ok。"""
    _prepare_pipeline_env(monkeypatch, tmp_path)
    calls = {"n": 0}

    def fake_run(pdir_, stamp, t0, save_failed=True):
        calls["n"] += 1
        if calls["n"] == 1:
            raise svc.PipelineUnavailableError("模拟第一步失败")
        (pdir_ / "output" / svc.DASHBOARD_NAME).write_text("<html>ok</html>", encoding="utf-8")
        return "fake logs"

    monkeypatch.setattr(svc, "_run_steps_directly", fake_run)
    resp = client.post(f"{P}/run")
    assert resp.status_code == 200, resp.text
    assert calls["n"] == 2
    latest = client.get(f"{P}/latest")
    assert latest.status_code == 200 and latest.json()["status"] == "ok"


def test_run_pipeline_retry_exhausted_alerts(client, monkeypatch, tmp_path):
    """v4：重试耗尽 → 503 + failed 快照 + 调试中心问题记录（去重）。"""
    _prepare_pipeline_env(monkeypatch, tmp_path)

    def fake_run(pdir_, stamp, t0, save_failed=True):
        raise svc.PipelineUnavailableError("模拟持续失败")

    monkeypatch.setattr(svc, "_run_steps_directly", fake_run)
    resp = client.post(f"{P}/run")
    assert resp.status_code == 503
    snaps = client.get(f"{P}/snapshots").json()["items"]
    assert snaps and snaps[0]["status"] == "failed"
    from app.modules.debug_center import service as dbg
    titles = [i["title"] for i in dbg.load_issues()]
    assert titles.count("报表看板管线运行失败") == 1
    client.post(f"{P}/run")
    titles = [i["title"] for i in dbg.load_issues()]
    assert titles.count("报表看板管线运行失败") == 1


def _verify_env(monkeypatch, tmp_path, payload, html_payload=None):
    """造「快照 HTML + 管线 report_data.json」环境；返回 (html 路径, 数据文件)。"""
    base = tmp_path / "board_data"
    monkeypatch.setattr(svc, "get_finance_data_dir", lambda: base)
    monkeypatch.setattr(svc, "_MIN_HTML_BYTES", 0)
    pdir = tmp_path / "pipe"
    (pdir / "data").mkdir(parents=True)
    data = dict(payload)
    (pdir / "data" / "report_data.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    html_body = html_payload if html_payload is not None else {k: v for k, v in data.items() if k != "map"}
    snap_dir = base / "finance_board" / "snapshots" / "20261102_120000"
    snap_dir.mkdir(parents=True, exist_ok=True)
    html = snap_dir / svc.DASHBOARD_NAME
    html.write_text(
        "<html><script>\nconst R = " + json.dumps(html_body, ensure_ascii=False) + ";\n</script></html>",
        encoding="utf-8",
    )
    monkeypatch.setattr(svc, "pipeline_dir", lambda: pdir)
    svc._save_snapshot("20261102_120000", "ok", str(html), "fake", 1.0)
    return html, pdir / "data" / "report_data.json"


def test_verify_snapshot_ok(client, monkeypatch, tmp_path):
    """v5：HTML 内嵌数据与 report_data.json 一致（meta 不参与比对）→ 全部检查通过。"""
    kpi = {"出租率": 0.95, "开业率": 0.9, "收缴率": 0.98, "空铺数": 3}
    payload = {"kpi": kpi, "op": {"years": [2025, 2026]},
               "meta": {"generated": "2026-10-01"}, "map": {"a": "b"}}
    html_payload = {"kpi": kpi, "op": {"years": [2025, 2026]}, "meta": {"generated": "2026-11-02"}}
    _verify_env(monkeypatch, tmp_path, payload, html_payload=html_payload)
    result = svc.verify_snapshot()
    assert result["available"] is True
    assert result["summary"]["fail"] == 0, result["checks"]
    assert result["mismatches"] == []
    resp = client.get(f"{P}/verify")
    assert resp.status_code == 200 and resp.json()["summary"]["fail"] == 0


def test_verify_snapshot_flags_map_in_html(client, monkeypatch, tmp_path):
    """v5：旧产物内嵌脱敏映射 map → 敏感键剔除失败。"""
    kpi = {"出租率": 0.95, "开业率": 0.9, "收缴率": 0.98, "空铺数": 3}
    _verify_env(monkeypatch, tmp_path, {"kpi": kpi}, html_payload={"kpi": kpi, "map": {"a": "b"}})
    result = svc.verify_snapshot()
    names = {c["name"] for c in result["checks"] if not c["ok"]}
    assert "敏感键剔除" in names
    assert "数值一致性" not in names


def test_verify_snapshot_detects_mismatch(client, monkeypatch, tmp_path):
    """v5：数据源被改动/渲染不一致 → 数值一致性失败且列出差异路径。"""
    payload = {"kpi": {"出租率": 0.95, "开业率": 0.9, "收缴率": 0.98, "空铺数": 3}}
    html_payload = {"kpi": {"出租率": 0.88, "开业率": 0.9, "收缴率": 0.98, "空铺数": 3}}
    _verify_env(monkeypatch, tmp_path, payload, html_payload=html_payload)
    result = svc.verify_snapshot()
    assert result["available"] is True
    assert result["summary"]["fail"] >= 1
    names = {c["name"] for c in result["checks"] if not c["ok"]}
    assert "数值一致性" in names
    assert any("出租率" in m["path"] for m in result["mismatches"])


def test_board_action_message_includes_attempts(monkeypatch):
    """v5：定时任务动作消息带重试次数。"""
    from app.modules.finance_board import _run_board_action
    monkeypatch.setattr(svc, "run_pipeline", lambda: {"stamp": "S1", "elapsed_sec": 1.5, "attempts": 2})
    out = _run_board_action()
    assert out["status"] == "ok" and "尝试 2 次" in out["message"]


def test_snapshots_list_and_html(client, board_dir, monkeypatch):
    _fake_snapshot(monkeypatch, board_dir)

    lst = client.get(f"{P}/snapshots")
    assert lst.status_code == 200
    assert lst.json()["total"] == 1
    sid = lst.json()["items"][0]["id"]

    latest = client.get(f"{P}/latest")
    assert latest.status_code == 200 and latest.json()["id"] == sid

    html = client.get(f"{P}/snapshots/{sid}/html")
    assert html.status_code == 200
    assert "smoke dashboard" in html.text

    missing = client.get(f"{P}/snapshots/99999/html")
    assert missing.status_code == 404


def test_latest_empty_404(client, board_dir):
    assert client.get(f"{P}/latest").status_code == 404


def test_snapshot_responses_include_log_tail(client, board_dir, monkeypatch):
    """log_tail 随快照列表/latest 响应暴露：失败快照的日志前端可查。"""
    _fake_snapshot(monkeypatch, board_dir)
    svc._save_snapshot("20261012_130000", "failed", "", "== m02_extract.py ==\nboom", 2.5)

    lst = client.get(f"{P}/snapshots").json()
    failed = next(i for i in lst["items"] if i["status"] == "failed")
    assert failed["log_tail"] == "== m02_extract.py ==\nboom"

    latest = client.get(f"{P}/latest").json()
    assert latest["log_tail"] == "fake log"


def test_log_tail_exposure_is_bounded(client, board_dir, monkeypatch):
    """超长日志按尾部有界暴露（不整文件无界返回）；空日志暴露为空串。"""
    huge = "HEAD" + "x" * 20000 + "TAIL-MARKER"
    svc._save_snapshot("20261012_140000", "failed", "", huge, 1.0)
    svc._save_snapshot("20261012_150000", "ok", "", "", 1.0)

    items = {i["stamp"]: i for i in client.get(f"{P}/snapshots").json()["items"]}
    tail = items["20261012_140000"]["log_tail"]
    assert len(tail) <= 500
    assert tail.endswith("TAIL-MARKER")
    assert items["20261012_150000"]["log_tail"] == ""


# ---------------------------------------------------------------------------
# R11-033：verify 差异总数与截断标记（20 条明细上限不得冒充精确总数）
# ---------------------------------------------------------------------------

def _wide_diff_payload(n: int) -> tuple[dict, dict]:
    kpi = {"出租率": 0.95, "开业率": 0.9, "收缴率": 0.98, "空铺数": 3}
    expected = {"kpi": kpi, "wide": {f"k{i:02d}": i for i in range(n)}}
    actual = {"kpi": kpi, "wide": {f"k{i:02d}": i + 100 for i in range(n)}}
    return expected, actual


def test_verify_reports_true_total_and_truncated(client, monkeypatch, tmp_path):
    """>20 处差异：明细仍截 20 条，但必须返回真实总数并标 truncated。"""
    svc._VERIFY_CACHE.clear()
    payload, html_payload = _wide_diff_payload(30)
    _verify_env(monkeypatch, tmp_path, payload, html_payload=html_payload)

    result = svc.verify_snapshot()
    assert result["available"] is True
    assert result["mismatch_total"] == 30
    assert result["truncated"] is True
    assert len(result["mismatches"]) == 20
    detail = next(c["detail"] for c in result["checks"] if c["name"] == "数值一致性")
    assert "至少 20 处" in detail and "30" in detail

    body = client.get(f"{P}/verify").json()
    assert body["mismatch_total"] == 30
    assert body["truncated"] is True


def test_verify_small_mismatch_is_not_truncated(client, monkeypatch, tmp_path):
    """≤20 处差异：total 等于明细条数，truncated=False，detail 为精确值。"""
    svc._VERIFY_CACHE.clear()
    payload, html_payload = _wide_diff_payload(2)
    _verify_env(monkeypatch, tmp_path, payload, html_payload=html_payload)

    result = svc.verify_snapshot()
    assert result["mismatch_total"] == 2
    assert result["truncated"] is False
    assert len(result["mismatches"]) == 2
    detail = next(c["detail"] for c in result["checks"] if c["name"] == "数值一致性")
    assert detail == "差异 2 处"


# ---------------------------------------------------------------------------
# R11-044：计划任务早退快速失败；重试不被 409 掩盖
# ---------------------------------------------------------------------------

def _fake_schtasks_ok(monkeypatch):
    def fake_run(args, **kwargs):
        return subprocess.CompletedProcess(args, 0, stdout=b"", stderr=b"")

    monkeypatch.setattr(svc.subprocess, "run", fake_run)


def test_board_task_not_started_fails_fast(monkeypatch, tmp_path):
    """schtasks 返回 0 但 run.lock 始终不出现：启动宽限后立即失败，不等满通道超时。"""
    pdir = tmp_path / "pipe"
    (pdir / "logs").mkdir(parents=True)
    monkeypatch.setattr(svc, "pipeline_dir", lambda: pdir)
    monkeypatch.setattr(svc, "_task_name", lambda: "合成任务")
    monkeypatch.setattr(svc, "_pipeline_lock_fresh", lambda: False)
    monkeypatch.setattr(svc, "_scheduled_run_state", lambda: {})
    monkeypatch.setattr(svc, "_lock_mtime", lambda: None, raising=False)
    monkeypatch.setattr(svc, "TASK_POLL_INTERVAL_SECONDS", 0.01, raising=False)
    monkeypatch.setattr(svc, "TASK_START_GRACE_SECONDS", 0.05, raising=False)
    monkeypatch.setattr(svc, "TASK_CHANNEL_TIMEOUT", 5)
    _fake_schtasks_ok(monkeypatch)

    t0 = time.monotonic()
    with pytest.raises(svc.PipelineUnavailableError) as excinfo:
        svc._run_steps_via_scheduled_task()
    assert time.monotonic() - t0 < 2, "未在启动宽限后快速失败"
    msg = str(excinfo.value)
    assert "未检测到管线启动" in msg
    assert "logs" in msg


def test_board_task_early_exit_fails_fast(monkeypatch, tmp_path):
    """任务启动后 run.lock 消失且 last_run.json 不更新（脚本崩溃）：快速失败。"""
    pdir = tmp_path / "pipe"
    (pdir / "logs").mkdir(parents=True)
    monkeypatch.setattr(svc, "pipeline_dir", lambda: pdir)
    monkeypatch.setattr(svc, "_task_name", lambda: "合成任务")
    monkeypatch.setattr(svc, "_pipeline_lock_fresh", lambda: False)
    monkeypatch.setattr(svc, "_scheduled_run_state", lambda: {})
    monkeypatch.setattr(svc, "TASK_POLL_INTERVAL_SECONDS", 0.01, raising=False)
    monkeypatch.setattr(svc, "TASK_START_GRACE_SECONDS", 5, raising=False)
    monkeypatch.setattr(svc, "TASK_EXIT_GRACE_SECONDS", 0.02, raising=False)
    monkeypatch.setattr(svc, "TASK_CHANNEL_TIMEOUT", 5)
    lock_calls = {"n": 0}

    def fake_lock_mtime():
        lock_calls["n"] += 1
        # 前两次轮询看得到运行锁（任务在跑），随后锁被清掉但状态文件没更新
        return time.time() if lock_calls["n"] <= 2 else None

    monkeypatch.setattr(svc, "_lock_mtime", fake_lock_mtime, raising=False)
    _fake_schtasks_ok(monkeypatch)

    t0 = time.monotonic()
    with pytest.raises(svc.PipelineUnavailableError) as excinfo:
        svc._run_steps_via_scheduled_task()
    assert time.monotonic() - t0 < 2, "未在提前退出后快速失败"
    msg = str(excinfo.value)
    assert "提前结束" in msg or "未写入运行记录" in msg
    assert "logs" in msg


def test_board_retry_busy_does_not_mask_pipeline_failure(client, monkeypatch, tmp_path):
    """首轮真实失败 + 重试遇残留 run.lock：最终必须是 503 真实失败，不是 409。"""
    _prepare_pipeline_env(monkeypatch, tmp_path)
    calls = {"n": 0}

    def fake_execute(pdir_, stamp, t0):
        calls["n"] += 1
        if calls["n"] == 1:
            raise svc.PipelineUnavailableError("模拟计划任务步骤失败")
        raise svc.PipelineBusyError("报表监控管线正在运行中（计划任务）")

    monkeypatch.setattr(svc, "_execute_attempt", fake_execute)
    monkeypatch.setattr(svc, "_wait_pipeline_lock_release", lambda timeout: False, raising=False)

    resp = client.post(f"{P}/run")
    assert resp.status_code == 503, resp.text
    detail = resp.json()["detail"]
    assert "模拟计划任务步骤失败" in detail
    assert "运行锁" in detail
    assert "尝试 1 次（重试 0 次）" in detail
    assert "尝试 2 次" not in detail
    snaps = client.get(f"{P}/snapshots").json()["items"]
    assert snaps and snaps[0]["status"] == "failed"


def test_board_retry_waits_out_residual_lock_then_succeeds(client, monkeypatch, tmp_path):
    """重试遇残留锁：有界等待锁释放后继续重试成功，attempts 不把忙态算成一次执行。"""
    pdir = _prepare_pipeline_env(monkeypatch, tmp_path)
    calls = {"n": 0}

    def fake_execute(pdir_, stamp, t0):
        calls["n"] += 1
        if calls["n"] == 1:
            raise svc.PipelineUnavailableError("首轮失败")
        if calls["n"] == 2:
            raise svc.PipelineBusyError("残留运行锁")
        (pdir_ / "output" / svc.DASHBOARD_NAME).write_text("<html>ok</html>", encoding="utf-8")
        return "fake logs"

    waits: list[float] = []

    def fake_wait(timeout):
        waits.append(timeout)
        return True

    monkeypatch.setattr(svc, "_execute_attempt", fake_execute)
    monkeypatch.setattr(svc, "_wait_pipeline_lock_release", fake_wait, raising=False)

    resp = client.post(f"{P}/run")
    assert resp.status_code == 200, resp.text
    assert resp.json()["attempts"] == 2
    assert waits and waits[0] > 0
    assert (pdir / "output" / svc.DASHBOARD_NAME).is_file()


# ---------------------------------------------------------------------------
# R11-045：快照入库失败可恢复（HTML 已落盘、返回带 stamp 的 503）
# ---------------------------------------------------------------------------

def test_board_snapshot_persist_failure_returns_503_with_stamp(client, monkeypatch, tmp_path):
    pdir = _prepare_pipeline_env(monkeypatch, tmp_path)

    def fake_run(pdir_, stamp, t0, save_failed=True):
        (pdir_ / "output" / svc.DASHBOARD_NAME).write_text("<html>ok</html>", encoding="utf-8")
        return "fake logs"

    monkeypatch.setattr(svc, "_run_steps_directly", fake_run)
    fail = {"on": True}
    real_save = svc._save_snapshot

    def flaky_save(*args, **kwargs):
        if fail["on"]:
            raise RuntimeError("database is locked")
        return real_save(*args, **kwargs)

    monkeypatch.setattr(svc, "_save_snapshot", flaky_save)

    resp = client.post(f"{P}/run")
    assert resp.status_code == 503, resp.text
    detail = resp.json()["detail"]
    assert "入库失败" in detail and "stamp=" in detail
    snap_root = tmp_path / "board_data" / "finance_board" / "snapshots"
    dirs = [d for d in snap_root.iterdir() if d.is_dir()]
    assert len(dirs) == 1 and (dirs[0] / svc.DASHBOARD_NAME).is_file()
    assert client.get(f"{P}/snapshots").json()["total"] == 0

    # 可恢复：入库恢复后再次运行成功，残留快照目录不阻塞新快照
    fail["on"] = False
    resp2 = client.post(f"{P}/run")
    assert resp2.status_code == 200, resp2.text
    assert client.get(f"{P}/snapshots").json()["total"] == 1


def test_board_failed_snapshot_persist_error_keeps_original_failure(client, monkeypatch, tmp_path):
    """失败快照入库也失败时，不得用 500 掩盖真实管线失败（仍是 503 + 原始原因）。"""
    _prepare_pipeline_env(monkeypatch, tmp_path)

    def always_fail(pdir_, stamp, t0, save_failed=True):
        raise svc.PipelineUnavailableError("模拟管线崩溃")

    def boom(*args, **kwargs):
        raise RuntimeError("disk I/O error")

    monkeypatch.setattr(svc, "_run_steps_directly", always_fail)
    monkeypatch.setattr(svc, "_save_snapshot", boom)

    resp = client.post(f"{P}/run")
    assert resp.status_code == 503, resp.text
    assert "模拟管线崩溃" in resp.json()["detail"]

def test_board_task_channel_reports_success(monkeypatch, tmp_path):
    """正常路径不回归：last_run.json 出现新的 finished 记录即返回步骤摘要。"""
    pdir = tmp_path / "pipe"
    (pdir / "logs").mkdir(parents=True)
    monkeypatch.setattr(svc, "pipeline_dir", lambda: pdir)
    monkeypatch.setattr(svc, "_task_name", lambda: "合成任务")
    monkeypatch.setattr(svc, "_pipeline_lock_fresh", lambda: False)
    monkeypatch.setattr(svc, "_lock_mtime", lambda: None, raising=False)
    monkeypatch.setattr(svc, "TASK_POLL_INTERVAL_SECONDS", 0.01, raising=False)
    monkeypatch.setattr(svc, "TASK_CHANNEL_TIMEOUT", 2)
    after = {"started": "2026-11-11T10:00:00", "finished": "2026-11-11T10:01:00", "ok": True,
             "steps": [{"step": "m01_scan.py", "exit": 0, "seconds": 1.0}]}
    seq = {"n": 0}

    def fake_state():
        seq["n"] += 1
        return {"started": "old", "finished": "old", "ok": True, "steps": []} if seq["n"] == 1 else after

    monkeypatch.setattr(svc, "_scheduled_run_state", fake_state)
    _fake_schtasks_ok(monkeypatch)

    logs = svc._run_steps_via_scheduled_task()
    assert "计划任务通道" in logs
    assert "m01_scan.py exit=0" in logs


# ---------------------------------------------------------------------------
# Fix round（独立审查 M5/M6/M7 + 集成审查 I2）
# ---------------------------------------------------------------------------

def _assert_verify_stable_shape(result):
    """verify 所有分支（含 available=False）都必须带总数/截断字段（M5）。"""
    assert result["mismatch_total"] == 0
    assert result["truncated"] is False


def test_verify_no_snapshot_keeps_stable_shape(client):
    svc._VERIFY_CACHE.clear()
    result = svc.verify_snapshot()
    assert result["available"] is False
    _assert_verify_stable_shape(result)


def test_verify_missing_html_keeps_stable_shape(client, tmp_path):
    svc._VERIFY_CACHE.clear()
    svc._save_snapshot("S_missing", "ok", str(tmp_path / "missing.html"), "", 1.0)
    result = svc.verify_snapshot()
    assert result["available"] is False
    assert any("HTML 文件不存在" in g for g in result["gap_reasons"])
    _assert_verify_stable_shape(result)


def test_verify_pipeline_unavailable_keeps_stable_shape(client, monkeypatch, tmp_path):
    svc._VERIFY_CACHE.clear()
    html = tmp_path / "snap.html"
    html.write_text("<html></html>", encoding="utf-8")
    svc._save_snapshot("S_pipe", "ok", str(html), "", 1.0)

    def boom():
        raise svc.PipelineUnavailableError("无法定位微信报表监控目录")

    monkeypatch.setattr(svc, "pipeline_dir", boom)
    result = svc.verify_snapshot()
    assert result["available"] is False
    assert any("管线数据不可用" in g for g in result["gap_reasons"])
    _assert_verify_stable_shape(result)


def test_verify_read_error_keeps_stable_shape(client, monkeypatch, tmp_path):
    svc._VERIFY_CACHE.clear()
    html, _ = _verify_env(monkeypatch, tmp_path, {"kpi": {"出租率": 0.9}})
    real_read = Path.read_text

    def boom(self, *args, **kwargs):
        if self == html:
            raise OSError("合成读取失败")
        return real_read(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", boom)
    result = svc.verify_snapshot()
    assert result["available"] is False
    assert any("读取快照失败" in g for g in result["gap_reasons"])
    _assert_verify_stable_shape(result)


def test_board_task_state_parses_schtasks_modes(monkeypatch):
    """兼容中文「模式」与英文 Status 两种 schtasks 输出（M7）。"""
    class _Proc:
        returncode = 0
        stderr = b""

        def __init__(self, text):
            self.stdout = text.encode("gbk")

    monkeypatch.setattr(svc.subprocess, "run", lambda *a, **k: _Proc("模式: 已排队"))
    assert svc._scheduled_task_state() == "queued"
    monkeypatch.setattr(svc.subprocess, "run", lambda *a, **k: _Proc("模式: 正在运行"))
    assert svc._scheduled_task_state() == "running"
    monkeypatch.setattr(svc.subprocess, "run", lambda *a, **k: _Proc("Status: Ready"))
    assert svc._scheduled_task_state() == "ready"


def _task_gate_env(monkeypatch, tmp_path, state, timeout=2):
    pdir = tmp_path / "pipe"
    (pdir / "logs").mkdir(parents=True)
    monkeypatch.setattr(svc, "pipeline_dir", lambda: pdir)
    monkeypatch.setattr(svc, "_task_name", lambda: "合成任务")
    monkeypatch.setattr(svc, "_pipeline_lock_fresh", lambda: False)
    monkeypatch.setattr(svc, "_scheduled_run_state", lambda: {})
    monkeypatch.setattr(svc, "_lock_mtime", lambda: None, raising=False)
    monkeypatch.setattr(svc, "_scheduled_task_state", lambda: state, raising=False)
    monkeypatch.setattr(svc, "TASK_POLL_INTERVAL_SECONDS", 0.01, raising=False)
    monkeypatch.setattr(svc, "TASK_START_GRACE_SECONDS", 0.05, raising=False)
    monkeypatch.setattr(svc, "TASK_STATE_RECHECK_SECONDS", 0.01, raising=False)
    monkeypatch.setattr(svc, "TASK_CHANNEL_TIMEOUT", timeout)
    _fake_schtasks_ok(monkeypatch)


def test_board_task_queued_after_grace_returns_busy(monkeypatch, tmp_path):
    """启动宽限后任务仍在排队：按忙态 409，不误报「未启动」（M7）。"""
    _task_gate_env(monkeypatch, tmp_path, "queued")
    t0 = time.monotonic()
    with pytest.raises(svc.PipelineBusyError, match="排队"):
        svc._run_steps_via_scheduled_task()
    assert time.monotonic() - t0 < 1


def test_board_task_ready_after_grace_fails_fast(monkeypatch, tmp_path):
    """启动宽限后任务模式明确为就绪：快速失败，不再盲等通道超时（M7）。"""
    _task_gate_env(monkeypatch, tmp_path, "ready")
    t0 = time.monotonic()
    with pytest.raises(svc.PipelineUnavailableError, match="未检测到管线启动") as excinfo:
        svc._run_steps_via_scheduled_task()
    assert "超过" in str(excinfo.value)
    assert time.monotonic() - t0 < 1


def test_board_task_running_after_grace_keeps_waiting(monkeypatch, tmp_path):
    """启动宽限后任务确在运行（run.lock 尚未落盘）：继续等待而不是误判失败（M7）。"""
    _task_gate_env(monkeypatch, tmp_path, "running", timeout=3)
    after = {"started": "2026-11-12T09:00:00", "finished": "2026-11-12T09:01:00", "ok": True,
             "steps": [{"step": "m01_scan.py", "exit": 0, "seconds": 1.0}]}
    seq = {"n": 0}

    def fake_state():
        seq["n"] += 1
        if seq["n"] == 1:
            return {"started": "old", "finished": "old", "ok": True, "steps": []}
        if seq["n"] >= 6:
            return after
        return {}

    monkeypatch.setattr(svc, "_scheduled_run_state", fake_state)
    logs = svc._run_steps_via_scheduled_task()
    assert "m01_scan.py exit=0" in logs


def test_board_direct_budget_fits_frontend_timeout_1500s():
    """预算契约含每次尝试两次 15s 查询：per_attempt=660s，总 1385s < 前端 1500s（I2）。"""
    assert svc.DIRECT_ATTEMPT_BUDGET_SECONDS == 600
    # 每次尝试的两次 15s 查询都计入：_scheduled_task_available + 宽限 _scheduled_task_state
    per_attempt = (2 * svc.TASK_QUERY_TIMEOUT_SECONDS + svc.TASK_TRIGGER_TIMEOUT_SECONDS
                   + svc.DIRECT_ATTEMPT_BUDGET_SECONDS)
    assert per_attempt == 660
    worst = per_attempt * svc._PIPELINE_ATTEMPTS + (svc._PIPELINE_ATTEMPTS - 1) * (
        svc._RETRY_DELAY_SECONDS + svc.RETRY_LOCK_WAIT_SECONDS)
    assert worst < 1500, f"前端 1500s 覆盖不了最坏预算 {worst}s"


def test_board_run_step_uses_remaining_direct_budget(monkeypatch, tmp_path):
    """直接执行时 _run_step 按总预算剩余收紧 subprocess 超时（I2）。"""
    pdir = tmp_path / "pipe"
    (pdir / "scripts").mkdir(parents=True)
    script = pdir / "scripts" / svc.PIPELINE_STEPS[0]
    script.write_text("# fake", encoding="utf-8")
    timeouts = []

    def fake_run(args, **kwargs):
        timeouts.append(kwargs.get("timeout"))
        return subprocess.CompletedProcess(args, 0, stdout="ok", stderr="")

    monkeypatch.setattr(svc.subprocess, "run", fake_run)
    svc._run_step(script)
    assert timeouts[-1] == svc.STEP_TIMEOUT
    try:
        svc._DIRECT_DEADLINE.value = time.monotonic() + 30
        svc._run_step(script)
    finally:
        try:
            del svc._DIRECT_DEADLINE.value
        except AttributeError:
            pass
    assert 0 < timeouts[-1] <= 30


def test_board_run_step_decodes_engine_output_bytes(monkeypatch, tmp_path):
    """看板子进程统一字节捕获 + decode_engine_output（UTF-8→GB18030）（残余 M-7）。"""
    pdir = tmp_path / "pipe"
    (pdir / "scripts").mkdir(parents=True)
    script = pdir / "scripts" / svc.PIPELINE_STEPS[0]
    script.write_text("# fake", encoding="utf-8")
    seen = {}

    def fake_run(args, **kwargs):
        seen.update(kwargs)
        return subprocess.CompletedProcess(
            args, 0, stdout="中文输出".encode("gb18030"), stderr=b""
        )

    monkeypatch.setattr(svc.subprocess, "run", fake_run)
    out = svc._run_step(script)
    assert out == "中文输出"
    assert "text" not in seen
    assert "encoding" not in seen


def test_board_run_step_decodes_failure_stderr(monkeypatch, tmp_path):
    pdir = tmp_path / "pipe"
    (pdir / "scripts").mkdir(parents=True)
    script = pdir / "scripts" / svc.PIPELINE_STEPS[0]
    script.write_text("# fake", encoding="utf-8")

    def fake_run(args, **kwargs):
        return subprocess.CompletedProcess(
            args, 2, stdout="输出".encode("gb18030"), stderr="错误".encode("gb18030")
        )

    monkeypatch.setattr(svc.subprocess, "run", fake_run)
    with pytest.raises(subprocess.CalledProcessError) as excinfo:
        svc._run_step(script)
    assert excinfo.value.stderr == "错误"
    assert excinfo.value.output == "输出"


def test_board_run_step_deadline_exhausted(monkeypatch, tmp_path):
    pdir = tmp_path / "pipe"
    (pdir / "scripts").mkdir(parents=True)
    script = pdir / "scripts" / svc.PIPELINE_STEPS[0]
    script.write_text("# fake", encoding="utf-8")
    monkeypatch.setattr(svc.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0))
    try:
        svc._DIRECT_DEADLINE.value = time.monotonic() - 1
        with pytest.raises(subprocess.TimeoutExpired):
            svc._run_step(script)
    finally:
        try:
            del svc._DIRECT_DEADLINE.value
        except AttributeError:
            pass


def test_board_run_steps_directly_enforces_attempt_budget(monkeypatch, tmp_path):
    """4 步共用一个总预算：步间剩余耗尽立即中断并复位线程预算（I2）。"""
    pdir = tmp_path / "pipe"
    (pdir / "scripts").mkdir(parents=True)
    for step in svc.PIPELINE_STEPS:
        (pdir / "scripts" / step).write_text("# fake", encoding="utf-8")
    monkeypatch.setattr(svc, "DIRECT_ATTEMPT_BUDGET_SECONDS", 0.02, raising=False)
    calls = []

    def slow_step(script):
        calls.append(script.name)
        time.sleep(0.03)
        return "ok"

    monkeypatch.setattr(svc, "_run_step", slow_step)
    with pytest.raises(svc.PipelineUnavailableError, match="总预算"):
        svc._run_steps_directly(pdir, "S_budget", 0.0, save_failed=False)
    assert calls == [svc.PIPELINE_STEPS[0]]
    assert getattr(svc._DIRECT_DEADLINE, "value", None) is None


# ---------------------------------------------------------------------------
# #4 看板异步运行（POST /runs → 202，GET /runs/{id} 轮询；同步 /run 保留）
# ---------------------------------------------------------------------------

def _wait_run(client, run_id: str, timeout: float = 5.0) -> dict:
    """轮询到终态；超时即判失败（异步运行不应拖过 timeout）。"""
    deadline = time.monotonic() + timeout
    body: dict = {}
    while time.monotonic() < deadline:
        body = client.get(f"{P}/runs/{run_id}").json()
        if body["status"] != board_runs.STATUS_RUNNING:
            return body
        time.sleep(0.01)
    raise AssertionError(f"异步运行未在 {timeout}s 内结束：{body}")


@pytest.fixture(autouse=True)
def _reset_board_runs():
    """registry 是进程内状态：前后清空，避免用例间串号（字段/状态互相污染）。"""
    board_runs.reset_registry()
    yield
    # 用例中途失败时线程可能仍在跑：先等锁释放再清 registry，避免污染后续用例
    deadline = time.monotonic() + 5
    while svc._PIPELINE_LOCK.locked() and time.monotonic() < deadline:
        time.sleep(0.01)
    board_runs.reset_registry()


def test_async_run_returns_202_then_ok(client, monkeypatch, tmp_path):
    """异步成功路径：202 + run_id，终态 ok 且带回 snapshot_id/log_tail。"""
    _prepare_pipeline_env(monkeypatch, tmp_path)

    def fake_run(pdir_, stamp, t0, save_failed=True):
        (pdir_ / "output" / svc.DASHBOARD_NAME).write_text("<html>ok</html>", encoding="utf-8")
        return "fake async logs"

    monkeypatch.setattr(svc, "_run_steps_directly", fake_run)
    resp = client.post(f"{P}/runs")
    assert resp.status_code == 202, resp.text
    accepted = resp.json()
    assert accepted["status"] == "running" and accepted["run_id"]

    final = _wait_run(client, accepted["run_id"])
    assert final["status"] == board_runs.STATUS_OK
    latest = client.get(f"{P}/latest").json()
    assert final["snapshot_id"] == latest["id"]
    assert final["log_tail"] == "fake async logs"
    assert final["error"] is None
    assert final["elapsed_sec"] is not None and final["elapsed_sec"] >= 0


def test_async_run_busy_returns_409_and_reports_running(client, monkeypatch, tmp_path):
    """忙态：运行中再提交 /runs 或同步 /run 都 409；运行中查询只有实时耗时。"""
    _prepare_pipeline_env(monkeypatch, tmp_path)
    started = threading.Event()
    release = threading.Event()
    calls: list[str] = []

    def fake_run(pdir_, stamp, t0, save_failed=True):
        calls.append(stamp)
        started.set()
        release.wait(10)
        (pdir_ / "output" / svc.DASHBOARD_NAME).write_text("<html>ok</html>", encoding="utf-8")
        return "ok logs"

    monkeypatch.setattr(svc, "_run_steps_directly", fake_run)
    first = client.post(f"{P}/runs")
    assert first.status_code == 202, first.text
    run_id = first.json()["run_id"]
    try:
        assert started.wait(5), "异步运行未进入管线"
        running = client.get(f"{P}/runs/{run_id}").json()
        assert running["status"] == board_runs.STATUS_RUNNING
        assert running["snapshot_id"] is None and running["error"] is None
        assert running["log_tail"] is None
        assert running["elapsed_sec"] is not None and running["elapsed_sec"] >= 0

        second = client.post(f"{P}/runs")
        assert second.status_code == 409, second.text
        assert "运行" in second.json()["detail"]
        # 同步入口与异步入口共享同一把互斥锁：也不得并发执行
        assert client.post(f"{P}/run").status_code == 409
    finally:
        release.set()

    final = _wait_run(client, run_id)
    assert final["status"] == board_runs.STATUS_OK
    assert len(calls) == 1, "忙态请求不得真的再跑一次管线"


def test_async_run_rejects_when_external_pipeline_lock_fresh(client, monkeypatch, tmp_path):
    """计划任务通道在跑（run.lock 新鲜）：受理阶段直接 409，而不是 202 后异步失败。"""
    _prepare_pipeline_env(monkeypatch, tmp_path)
    monkeypatch.setattr(svc, "_pipeline_lock_fresh", lambda: True)
    resp = client.post(f"{P}/runs")
    assert resp.status_code == 409, resp.text
    assert "运行" in resp.json()["detail"]
    assert not svc._PIPELINE_LOCK.locked(), "被拒绝的受理不得长期占用运行锁"


def test_async_run_failure_reports_error_and_keeps_failed_snapshot(client, monkeypatch, tmp_path):
    """失败路径（同步 503 的异步等价）：终态 failed + 错误文案，失败快照仍入库。"""
    _prepare_pipeline_env(monkeypatch, tmp_path)

    def always_fail(pdir_, stamp, t0, save_failed=True):
        raise svc.PipelineUnavailableError("模拟管线不可用")

    monkeypatch.setattr(svc, "_run_steps_directly", always_fail)
    resp = client.post(f"{P}/runs")
    assert resp.status_code == 202, resp.text

    final = _wait_run(client, resp.json()["run_id"])
    assert final["status"] == board_runs.STATUS_FAILED
    assert "模拟管线不可用" in final["error"]
    assert "尝试 2 次（重试 1 次）" in final["error"]
    assert final["snapshot_id"] is None
    snaps = client.get(f"{P}/snapshots").json()["items"]
    assert snaps and snaps[0]["status"] == "failed"


def test_async_run_pipeline_dir_invalid_is_reported_not_500(client, monkeypatch, tmp_path):
    """管线目录不可用：受理仍 202，失败原因经 GET 暴露（不得 500/静默成功）。"""
    _prepare_pipeline_env(monkeypatch, tmp_path)
    broken = tmp_path / "缺脚本管线"
    broken.mkdir()
    monkeypatch.setattr(svc, "pipeline_dir", lambda: broken)

    resp = client.post(f"{P}/runs")
    assert resp.status_code == 202, resp.text
    final = _wait_run(client, resp.json()["run_id"])
    assert final["status"] == board_runs.STATUS_FAILED
    assert "管线目录无效" in final["error"]
    assert client.get(f"{P}/snapshots").json()["total"] == 0


def test_async_run_unknown_id_returns_404(client, monkeypatch, tmp_path):
    """未知/过期 run_id → 404 中文提示（registry 只在进程内）。"""
    _prepare_pipeline_env(monkeypatch, tmp_path)
    resp = client.get(f"{P}/runs/not-a-run")
    assert resp.status_code == 404
    assert "重新发起" in resp.json()["detail"]


def test_async_run_registry_is_bounded(client, monkeypatch, tmp_path):
    """完成记录有界：超过 _MAX_RECORDS 时淘汰最旧的完成记录（运行中不淘汰）。"""
    _prepare_pipeline_env(monkeypatch, tmp_path)
    monkeypatch.setattr(board_runs, "_MAX_RECORDS", 1)

    def fake_run(pdir_, stamp, t0, save_failed=True):
        (pdir_ / "output" / svc.DASHBOARD_NAME).write_text("<html>ok</html>", encoding="utf-8")
        return "logs"

    monkeypatch.setattr(svc, "_run_steps_directly", fake_run)
    first = client.post(f"{P}/runs").json()["run_id"]
    assert _wait_run(client, first)["status"] == board_runs.STATUS_OK
    second = client.post(f"{P}/runs").json()["run_id"]
    assert _wait_run(client, second)["status"] == board_runs.STATUS_OK

    assert client.get(f"{P}/runs/{second}").status_code == 200
    assert client.get(f"{P}/runs/{first}").status_code == 404
