# -*- coding: utf-8 -*-
"""Task 7B：CI 产物/超时、版本四方一致、业务探针端口与失败结果契约。

守护目标：
1. 版本单一源 config.py 与 frontend package.json / package-lock.json（根与 ""）
   完全一致，且发布脚本 read_version() 与 config 一致；
2. CI 产出 JUnit、失败也上传 artifact、作业与每个执行步骤都有有界超时；
3. business-closed-loop / business-gm 支持 E2E_PORT（占用时回退空闲端口），
   主流程异常也必须落 result.json（ok=false + error），不静默丢证据。

验证方式：YAML/JSON/正则与探针源码 AST 静态检查 + 端口选择函数的行为测试
（临时绑定回环端口）。不启动服务、不跑 Playwright、不读真实数据。
"""
import ast
import importlib.util
import json
import os
import re
import socket
import sys
from datetime import datetime
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
CI = REPO / ".github" / "workflows" / "ci.yml"
CONFIG = REPO / "backend" / "app" / "config.py"
PKG = REPO / "frontend" / "package.json"
LOCK = REPO / "frontend" / "package-lock.json"
PROBE_DIR = REPO / "dev" / "e2e" / "probes"
PROBES = ["business-closed-loop.py", "business-gm.py"]


@pytest.fixture(autouse=True)
def _ensure_tables():
    from app.database import Base, engine

    Base.metadata.create_all(bind=engine)
    yield


# ---------- 版本四方一致 ----------

def _config_version() -> str:
    match = re.search(
        r'^\s*version:\s*str\s*=\s*"([^"]+)"',
        CONFIG.read_text(encoding="utf-8"),
        flags=re.M,
    )
    assert match, "config.py 中找不到 version 定义"
    return match.group(1)


def test_version_config_package_and_lock_are_identical():
    version = _config_version()
    package = json.loads(PKG.read_text(encoding="utf-8"))
    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    assert package["version"] == version, (
        f"package.json={package['version']} 与 config.py={version} 不一致"
    )
    assert lock["version"] == version, (
        f"package-lock.json 顶层={lock['version']} 与 config.py={version} 不一致"
    )
    assert lock["packages"][""]["version"] == version, (
        f"package-lock.json packages['']={lock['packages']['']['version']} "
        f"与 config.py={version} 不一致"
    )


def test_package_release_read_version_matches_single_source():
    if str(REPO / "dev") not in sys.path:
        sys.path.insert(0, str(REPO / "dev"))
    spec = importlib.util.spec_from_file_location(
        "t7b_package_release", REPO / "dev" / "package_release.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.read_version() == _config_version(), (
        "package_release.read_version() 必须与 config.py 单一源一致"
    )


# ---------- CI 契约 ----------

def _jobs() -> dict:
    return yaml.safe_load(CI.read_text(encoding="utf-8"))["jobs"]


def _steps() -> list[dict]:
    return [step for job in _jobs().values() for step in (job.get("steps") or [])]


def _step(name: str) -> dict:
    step = next(
        (item for item in _steps() if str(item.get("name", "")).startswith(name)),
        None,
    )
    assert step is not None, f"CI 缺少步骤「{name}」"
    return step


def test_ci_backend_publishes_junit_and_diagnostic_logs():
    script = _step("后端测试").get("run") or ""
    assert "--junitxml" in script, "后端测试未产出 JUnit（失败时无可机读结果）"
    assert "pytest-junit-" in script, "JUnit 文件名应稳定可上传（分片后缀）"
    assert "--durations=20" in script, "缺少最慢用例诊断输出"


def test_ci_frontend_test_and_build_publish_results_separately():
    test_step = _step("前端测试")
    build_step = _step("前端构建")
    script = test_step.get("run") or ""
    assert "vitest run" in script, "前端测试步骤应直接运行 vitest"
    assert "--reporter=junit" in script and "vitest-junit.xml" in script, (
        "前端测试未产出 JUnit"
    )
    assert "npm run build" not in script, "构建必须拆成独立步骤，便于分别限时/定位失败"
    assert "npm run build" in (build_step.get("run") or "")


def test_ci_uploads_junit_artifact_even_when_a_step_fails():
    uploads = [
        step for step in _steps()
        if str(step.get("uses", "")).startswith("actions/upload-artifact")
    ]
    assert uploads, "CI 缺少 actions/upload-artifact"
    paths = [str((upload.get("with") or {}).get("path") or "") for upload in uploads]
    assert any("pytest-junit" in path for path in paths), "缺少后端 JUnit 上传"
    assert any("vitest-junit" in path for path in paths), "缺少前端 JUnit 上传"
    for upload in uploads:
        assert str(upload.get("if", "")).strip() == "always()", (
            "artifact 必须 if: always()，否则失败运行拿不到 JUnit"
        )
        with_ = upload.get("with") or {}
        assert with_.get("if-no-files-found") == "warn", "无文件时不应再掩盖原失败"
        assert with_.get("name"), "artifact 需要稳定名称"


def test_ci_job_and_every_run_step_have_bounded_timeouts():
    for job_name, job in _jobs().items():
        assert 0 < int(job.get("timeout-minutes", 0)) <= 90, f"{job_name} 作业超时必须存在且有界"
        for step in job.get("steps") or []:
            if not step.get("run"):
                continue
            timeout = int(step.get("timeout-minutes", 0))
            assert timeout > 0, f"{job_name}/{step.get('name')} 缺少 timeout-minutes"
            assert timeout <= 45, f"{job_name}/{step.get('name')} 超时过长：{timeout}"


# ---------- 探针：端口与失败结果 ----------

def _probe_src(name: str) -> str:
    return (PROBE_DIR / name).read_text(encoding="utf-8")


def _probe_tree(name: str) -> ast.Module:
    return ast.parse(_probe_src(name))


def _function_node(name: str, fn_name: str) -> ast.FunctionDef:
    for node in ast.walk(_probe_tree(name)):
        if isinstance(node, ast.FunctionDef) and node.name == fn_name:
            return node
    raise AssertionError(f"{name} 缺少函数 {fn_name}")


def _load_function(name: str, fn_name: str):
    """只执行目标函数定义（不执行探针主流程），供端口选择行为测试。"""
    source = _probe_src(name)
    segment = ast.get_source_segment(source, _function_node(name, fn_name))
    assert segment, f"{name} 中无法提取 {fn_name}"
    namespace = {"os": os, "socket": socket}
    exec(compile("import os\nimport socket\n" + segment, name, "exec"), namespace)
    return namespace[fn_name]


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.mark.parametrize("probe", PROBES)
def test_probe_has_no_hardcoded_port_and_reads_e2e_port(probe):
    source = _probe_src(probe)
    assert "E2E_PORT" in source, f"{probe} 不识别 E2E_PORT"
    tree = _probe_tree(probe)
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        targets = [target.id for target in node.targets if isinstance(target, ast.Name)]
        if "PORT" in targets:
            assert not isinstance(node.value, ast.Constant), (
                f"{probe} 的 PORT 仍写死为常量 {ast.literal_eval(node.value)!r}"
            )


@pytest.mark.parametrize("probe", PROBES)
def test_probe_honours_free_e2e_port(probe, monkeypatch):
    pick_port = _load_function(probe, "pick_port")
    free = _free_port()
    monkeypatch.setenv("E2E_PORT", str(free))
    assert pick_port() == free, f"{probe} 未使用空闲的 E2E_PORT"


@pytest.mark.parametrize("probe", PROBES)
def test_probe_falls_back_when_e2e_port_is_occupied(probe, monkeypatch):
    pick_port = _load_function(probe, "pick_port")
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as blocker:
        blocker.bind(("127.0.0.1", 0))
        blocker.listen(1)
        occupied = int(blocker.getsockname()[1])
        monkeypatch.setenv("E2E_PORT", str(occupied))
        chosen = pick_port()
    assert chosen != occupied, f"{probe} 复用了被占用端口 {occupied}"
    assert 1024 < chosen < 65536, chosen


@pytest.mark.parametrize("probe", PROBES)
def test_probe_falls_back_when_e2e_port_is_invalid(probe, monkeypatch):
    pick_port = _load_function(probe, "pick_port")
    monkeypatch.setenv("E2E_PORT", "not-a-port")
    chosen = pick_port()
    assert 1024 < chosen < 65536, chosen


@pytest.mark.parametrize("probe", PROBES)
def test_probe_writes_result_json_even_when_main_flow_fails(probe):
    source = _probe_src(probe)
    tree = _probe_tree(probe)
    main_try = next(
        (node for node in tree.body if isinstance(node, ast.Try)), None
    )
    assert main_try is not None, f"{probe} 主流程缺少 try/except 兜底"
    assert main_try.handlers, f"{probe} 没有异常分支"
    assert any(
        isinstance(handler.type, ast.Name) and handler.type.id == "BaseException"
        for handler in main_try.handlers
    ), f"{probe} 异常兜底未覆盖 BaseException（SystemExit/KeyboardInterrupt 会漏写）"
    handler_calls = {
        node.func.id
        for handler in main_try.handlers
        for node in ast.walk(handler)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "write_result" in handler_calls, f"{probe} 异常分支未调用 write_result"
    write_fn = _function_node(probe, "write_result")
    body = ast.get_source_segment(source, write_fn) or ""
    assert "write_text" in body, f"{probe} 的 write_result 未真正落盘"
    assert '"ok"' in body or "'ok'" in body, f"{probe} 结果缺少 ok 字段"
    assert '"error"' in body or "'error'" in body, f"{probe} 失败结果缺少 error 字段"
    assert "result.json" in source, f"{probe} 结果文件名不是 result.json"


@pytest.mark.parametrize("probe", PROBES)
def test_probe_write_result_records_failure(tmp_path, probe):
    source = _probe_src(probe)
    segment = ast.get_source_segment(source, _function_node(probe, "write_result"))
    started = "2026-11-12T00:00:00"
    namespace = {
        "json": json,
        "datetime": datetime,
        "RESULT": tmp_path / "closed-loop-result.json",
        "RESULT_JSON": tmp_path / "gm-result.json",
        "checks": [{"passed": True}, {"passed": False}],
        "results": [{"passed": True}, {"passed": False}],
        "page_errors": ["合成 pageerror"],
        "state": {},
        "requests": [],
        "console_errors": [],
        "BASE": "http://127.0.0.1:1",
        "PERIOD": "2026-11",
        "STARTED": started,
        "STARTED_AT": started,
    }
    exec(compile(segment, probe, "exec"), namespace)
    raw = namespace["write_result"]("RuntimeError: 合成故障")
    result_path = namespace["RESULT"] if probe == PROBES[0] else namespace["RESULT_JSON"]
    data = json.loads(result_path.read_text(encoding="utf-8"))
    assert raw["ok"] is False and data["ok"] is False
    assert data["error"] == "RuntimeError: 合成故障"
