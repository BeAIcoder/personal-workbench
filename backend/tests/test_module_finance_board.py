"""finance_board 模块测试。

管线不可在测试中真实运行（依赖本机微信文件目录与耗时），因此：
- 用合成数据验证快照登记/查询/ latest / HTML 端点（monkeypatch 快照目录）；
- 验证管线目录推导、PipelineUnavailableError 降级（503）；
- 验证模块注册与菜单。
不写入真实数据目录，不触发真实管线。
"""
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
