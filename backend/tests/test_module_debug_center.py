"""debug_center 模块测试。

隔离方式：DEBUG_DATA_DIR 指到 tmp_path（service 运行时读取环境变量），
不碰真实 backend/data/debug；数据库沿用 conftest 的临时 SQLite。

覆盖：模块注册、种子问题生成与幂等、issue CRUD、run 写入/列表/详情/截图/删除
（含路径穿越拒绝）、client-errors 追加/读取/清空（含垃圾输入容错）、summary 计数。
"""
import json
from pathlib import Path

import pytest

from app.modules import get_loaded_modules
from app.modules.debug_center import service as svc

P = "/api/debug"


@pytest.fixture()
def debug_dir(monkeypatch, tmp_path):
    """DEBUG_DATA_DIR 重定向到 tmp_path（须先于 client 夹具，保证 lifespan 种子也落在临时目录）。"""
    base = tmp_path / "debug_data"
    monkeypatch.setenv("DEBUG_DATA_DIR", str(base))
    return base


def _write_run(base: Path, run_id: str = "20261013-153000_recon", kind: str = "recon",
               bad_pages: int = 1, failed_tests: int = 0) -> Path:
    """在调试目录里手工落一个 run（与 dev/e2e/debug_reporter.py 同一格式）。"""
    run_dir = base / "runs" / run_id
    shots = run_dir / "shots"
    shots.mkdir(parents=True, exist_ok=True)
    (shots / "tasks.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")
    result = {
        "id": run_id,
        "kind": kind,
        "base": "http://127.0.0.1:8123",
        "started_at": "2026-10-13 15:30:00",
        "finished_at": "2026-10-13 15:30:42",
        "totals": {"pages": 2, "bad_pages": bad_pages, "tests": 3, "failed_tests": failed_tests},
        "pages": [{"route": "/tasks", "name": "tasks", "status": 200,
                   "console_errors": ["boom"], "page_errors": [], "failed_reqs": []}],
        "tests": [],
        "extra": {},
    }
    (run_dir / "result.json").write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    return run_dir


# ---------- 模块注册 ----------


def test_module_registration():
    loaded = {m.name: m for m in get_loaded_modules()}
    assert "debug_center" in loaded
    manifest = loaded["debug_center"]
    assert manifest.prefix == "/api/debug"
    assert manifest.tables == []
    assert manifest.menu["group"] == "调试" and manifest.menu["order"] == 80
    assert manifest.validate() == []


# ---------- 种子问题 ----------


def test_seed_issues_written_and_idempotent(debug_dir):
    assert svc.ensure_seed_issues() == 11
    path = debug_dir / "issues.json"
    assert path.is_file()
    issues = json.loads(path.read_text(encoding="utf-8"))
    assert len(issues) == 11
    assert all(i["source"] == "analysis" and i["status"] == "open" and i["evidence"] == "" for i in issues)
    assert {i["severity"] for i in issues} == {"P0", "P1", "P2"}
    # 幂等：文件已存在不再触碰
    assert svc.ensure_seed_issues() == 0
    assert len(json.loads(path.read_text(encoding="utf-8"))) == 11


def test_seed_via_api_on_first_use(debug_dir, client):
    """lifespan 种子 + 接口首用幂等：/issues 返回 11 条种子。"""
    resp = client.get(f"{P}/issues")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 11
    titles = [i["title"] for i in data["items"]]
    assert any("技能包停用开关" in t for t in titles)


# ---------- issue CRUD ----------


def test_issue_crud_and_filters(debug_dir, client):
    # 新建
    resp = client.post(f"{P}/issues", json={
        "title": "E2E 页面报错：tasks", "severity": "P1", "source": "e2e",
        "page": "/tasks", "description": "console 报错", "evidence": "20261013-153000_recon",
    })
    assert resp.status_code == 201
    issue = resp.json()
    assert issue["id"] and issue["status"] == "open" and issue["created_at"] == issue["updated_at"]

    # 列表 + 筛选
    lst = client.get(f"{P}/issues", params={"severity": "P1"}).json()
    assert any(i["id"] == issue["id"] for i in lst["items"])
    assert all(i["severity"] == "P1" for i in lst["items"])
    lst = client.get(f"{P}/issues", params={"status": "done"}).json()
    assert all(i["status"] == "done" for i in lst["items"])

    # 更新（updated_at 自动刷新，未传字段不变）
    upd = client.put(f"{P}/issues/{issue['id']}", json={"status": "in_progress"})
    assert upd.status_code == 200
    updated = upd.json()
    assert updated["status"] == "in_progress"
    assert updated["title"] == issue["title"]
    assert updated["updated_at"] >= issue["updated_at"]

    # 非法枚举被 schema 拒绝
    bad = client.put(f"{P}/issues/{issue['id']}", json={"severity": "P9"})
    assert bad.status_code == 422

    # 删除与 404
    assert client.delete(f"{P}/issues/{issue['id']}").json() == {"ok": True}
    assert client.delete(f"{P}/issues/{issue['id']}").status_code == 404
    assert client.put(f"{P}/issues/{issue['id']}", json={"status": "done"}).status_code == 404


# ---------- run 归档 ----------


def test_runs_list_detail_shots_delete(debug_dir, client):
    _write_run(debug_dir)
    _write_run(debug_dir, run_id="20261013-160000_interactions", kind="interactions", bad_pages=0, failed_tests=2)

    lst = client.get(f"{P}/runs").json()
    assert lst["total"] == 2
    first = lst["items"][0]  # 目录名倒序 = 时间倒序
    assert first["id"] == "20261013-160000_interactions"
    assert first["kind"] == "interactions"
    assert first["totals"] == {"pages": 2, "bad_pages": 0, "tests": 3, "failed_tests": 2}
    assert first["shots"] == 1

    detail = client.get(f"{P}/runs/20261013-153000_recon").json()
    assert detail["kind"] == "recon"
    assert detail["pages"][0]["console_errors"] == ["boom"]
    assert detail["shots"] == ["tasks.png"]

    shot = client.get(f"{P}/runs/20261013-153000_recon/shots/tasks.png")
    assert shot.status_code == 200
    assert shot.content.startswith(b"\x89PNG")

    assert client.get(f"{P}/runs/no_such_run").status_code == 404
    assert client.get(f"{P}/runs/20261013-153000_recon/shots/missing.png").status_code == 404

    assert client.delete(f"{P}/runs/20261013-153000_recon").json() == {"ok": True}
    assert not (debug_dir / "runs" / "20261013-153000_recon").exists()
    assert client.delete(f"{P}/runs/20261013-153000_recon").status_code == 404


def test_run_path_traversal_rejected(debug_dir, client):
    _write_run(debug_dir)
    # service 层：run_id / 文件名穿越一律 ValueError
    with pytest.raises(ValueError):
        svc.get_run("../../etc")
    with pytest.raises(ValueError):
        svc.get_run("../issues")
    with pytest.raises(ValueError):
        svc.delete_run("..")
    with pytest.raises(ValueError):
        svc.shot_path("20261013-153000_recon", "../result.json")
    with pytest.raises(ValueError):
        svc.shot_path("20261013-153000_recon", "..\\..\\issues.json")
    with pytest.raises(ValueError):
        svc.shot_path("20261013-153000_recon", "/abs/path.png")
    # API 层：非法 run_id 400；编码斜杠穿越进不了路由（404），绝不返回文件
    assert client.get(f"{P}/runs/.._evil").status_code == 400
    assert client.get(f"{P}/runs/20261013-153000_recon/shots/x%2F..%2Fresult.json").status_code in (400, 404)


# ---------- client-errors ----------


def test_client_errors_append_read_clear(debug_dir, client):
    assert client.get(f"{P}/client-errors").json()["total"] == 0

    resp = client.post(f"{P}/client-errors", json={
        "ts": "2026-10-13 15:31:00", "page": "/tasks",
        "message": "TypeError: x is undefined", "stack": "at foo (a.js:1)", "ua": "Mozilla/5.0",
    })
    assert resp.status_code == 201
    entry = resp.json()
    assert entry["page"] == "/tasks" and entry["message"].startswith("TypeError")

    client.post(f"{P}/client-errors", json={"page": "/notes"})  # 缺字段补默认
    lst = client.get(f"{P}/client-errors").json()
    assert lst["total"] == 2
    assert lst["items"][1]["message"] == ""

    # limit 尾部读取
    lst = client.get(f"{P}/client-errors", params={"limit": 1}).json()
    assert lst["total"] == 1 and lst["items"][0]["page"] == "/notes"

    assert client.delete(f"{P}/client-errors").json() == {"ok": True}
    assert client.get(f"{P}/client-errors").json()["total"] == 0


def test_client_errors_garbage_input_never_500(debug_dir, client):
    """垃圾输入容错：非 JSON body / 数组 / 字符串 / 缺字段字典一律不 500。"""
    resp = client.post(f"{P}/client-errors", content=b"this is not json",
                       headers={"Content-Type": "application/json"})
    assert resp.status_code == 201
    assert client.post(f"{P}/client-errors", json=["not", "a", "dict"]).status_code == 201
    assert client.post(f"{P}/client-errors", json="just a string").status_code == 201
    assert client.post(f"{P}/client-errors", json={"message": 12345, "stack": None}).status_code == 201
    lst = client.get(f"{P}/client-errors").json()
    assert lst["total"] == 4
    assert lst["items"][2]["message"] == "just a string"
    assert lst["items"][3]["message"] == "12345"  # 非字符串归一为 str


# ---------- summary ----------


def test_summary_counts(debug_dir, client):
    svc.ensure_seed_issues()  # 4×P0 + 3×P1 + 4×P2 全部 open
    _write_run(debug_dir, bad_pages=2, failed_tests=0)
    _write_run(debug_dir, run_id="20261013-160000_interactions", kind="interactions", bad_pages=0, failed_tests=3)
    client.post(f"{P}/client-errors", json={"page": "/tasks", "message": "boom"})
    client.post(f"{P}/client-errors", json={"page": "/notes", "message": "bang"})

    data = client.get(f"{P}/summary").json()
    assert data["open_issues_by_severity"] == {"P0": 4, "P1": 3, "P2": 4, "P3": 0}
    assert data["total_issues"] == 11
    assert data["runs_count"] == 2
    assert data["last_run"]["id"] == "20261013-160000_interactions"
    assert data["last_run"]["kind"] == "interactions"
    assert data["last_run"]["bad_pages"] == 0
    assert data["last_run"]["failed_tests"] == 3
    assert data["client_errors_count"] == 2

    # 把一条 P0 置为 done 后不再计入未决
    p0 = client.get(f"{P}/issues", params={"severity": "P0"}).json()["items"][0]
    client.put(f"{P}/issues/{p0['id']}", json={"status": "done"})
    data = client.get(f"{P}/summary").json()
    assert data["open_issues_by_severity"]["P0"] == 3
    assert data["total_issues"] == 11


def test_summary_empty_state(debug_dir, client):
    """无任何数据时 summary 不报错，last_run 为 null。"""
    data = client.get(f"{P}/summary").json()
    # lifespan 已播种子：问题数可能为 11，但 run / 报错必为空
    assert data["runs_count"] == 0
    assert data["last_run"] is None
    assert data["client_errors_count"] == 0
