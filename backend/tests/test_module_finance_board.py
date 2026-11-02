"""finance_board 模块测试。

管线不可在测试中真实运行（依赖本机微信文件目录与耗时），因此：
- 用合成数据验证快照登记/查询/ latest / HTML 端点（monkeypatch 快照目录）；
- 验证管线目录推导、PipelineUnavailableError 降级（503）；
- 验证模块注册与菜单。
不写入真实数据目录，不触发真实管线。
"""
import json
from pathlib import Path

import pytest

from app.modules import get_loaded_modules
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
    assert "mod_board_snapshot" in loaded["finance_board"].tables


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
