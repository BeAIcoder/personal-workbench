# -*- coding: utf-8 -*-
"""Task 9：CI 与脚本换行契约（不执行 GitHub Actions，只校验配置可复现）。

- CI 必须从 frontend/.nvmrc 取 Node 版本（安全下限 24.22.1），而不是写死 "24"；
- CI 必须显式 UTF-8（Windows runner 默认代码页会让中文断言/日志 mojibake）；
- CI 后端命令与本地分片标准一致（4 分片 + -n 4 --dist loadfile + timeout）；
- `backend/pytest.ini` 登记 slow/fast marker，并提供 `-m "not slow"` 快跑闸门；
- `dev/shard_tests.py` 每行输出一个文件（mapfile 才能得到真正的数组）、
  按展开用例数均衡分片、`--mode fast` 跳过整文件 slow；
- .gitattributes 为 bat/vbs 声明 eol=crlf、sh 声明 eol=lf，且当前脚本实际为 CRLF。
"""
import importlib.util
import re
import subprocess
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "backend"
CI = REPO / ".github" / "workflows" / "ci.yml"
ATTRS = REPO / ".gitattributes"
PYTEST_INI = BACKEND / "pytest.ini"
SHARD = REPO / "dev" / "shard_tests.py"


def _ci() -> dict:
    return yaml.safe_load(CI.read_text(encoding="utf-8"))


def _jobs() -> dict:
    return _ci()["jobs"]


def _steps() -> list[dict]:
    return [step for job in _jobs().values() for step in (job.get("steps") or [])]


def test_ci_reads_node_version_from_nvmrc():
    steps = [s for s in _steps() if str(s.get("uses", "")).startswith("actions/setup-node")]
    assert steps, "CI 缺少 actions/setup-node"
    with_ = steps[0].get("with") or {}
    assert with_.get("node-version-file") == "frontend/.nvmrc", with_
    assert "node-version" not in with_, "写死 node-version 会忽略 .nvmrc 的安全下限"


def test_nvmrc_meets_engine_minimum():
    version = (REPO / "frontend" / ".nvmrc").read_text(encoding="utf-8").strip()
    parts = tuple(int(p) for p in version.split("."))
    assert parts >= (24, 22, 1), f".nvmrc 低于 engines 下限: {version}"


def test_ci_sets_utf8_environment():
    envs = [job.get("env") or {} for job in _jobs().values()]
    assert any(
        env.get("PYTHONIOENCODING") == "utf-8"
        and str(env.get("PYTHONUTF8")) in {"1", "true", "True"}
        for env in envs
    ), "至少一个执行测试的 job 必须显式 UTF-8"


def test_ci_backend_command_matches_local_parallel_standard():
    backend = next(
        step for step in _steps()
        if str(step.get("name", "")).startswith("后端测试")
    )
    script = backend.get("run") or ""
    assert "-n 4" in script, "CI 4 核 runner 应使用 -n 4，避免 -n 12 过度订阅"
    assert "--dist loadfile" in script, "分片内按文件分发，避免 fixture 互相踩踏"
    assert "--timeout=120" in script, "每个用例必须有界超时"
    assert "pytest" in script, script


def test_gitattributes_declares_crlf_for_windows_scripts():
    text = ATTRS.read_text(encoding="utf-8")
    rules = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        pattern, *attrs = line.split()
        rules[pattern] = set(attrs)
    for pattern in ("*.bat", "*.vbs"):
        assert pattern in rules, f".gitattributes 缺少 {pattern} 规则"
        assert "text" in rules[pattern], f"{pattern} 未声明 text"
        assert "eol=crlf" in rules[pattern], f"{pattern} 未声明 eol=crlf"
        assert "filter=sanitize" in rules[pattern], f"{pattern} 丢了脱敏过滤器"
    assert "eol=lf" in rules.get("*.sh", set()), "*.sh 应声明 eol=lf"


def test_root_bat_and_vbs_are_crlf():
    files = sorted(REPO.glob("*.bat")) + sorted(REPO.glob("*.vbs"))
    assert files, "未找到根目录 bat/vbs"
    for path in files:
        raw = path.read_bytes()
        assert raw.count(b"\r\n") > 0, f"{path.name} 没有 CRLF 换行"
        assert b"\n" not in raw.replace(b"\r\n", b""), f"{path.name} 含裸 LF（cmd 解析括号块会错乱）"


def test_git_check_attr_reports_script_eol():
    proc = subprocess.run(
        ["git", "check-attr", "text", "eol", "--", "启动工作台.bat", "开机自启动.vbs"],
        cwd=REPO, capture_output=True, text=True, encoding="utf-8",
    )
    assert proc.returncode == 0, proc.stderr
    output = proc.stdout
    assert output.count("eol: crlf") >= 2, output


# ---------- fast/slow 分层（#7） ----------

def _shard(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SHARD), *args],
        cwd=BACKEND,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )


def _pytest_markers() -> str:
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "--markers", "-p", "no:cacheprovider"],
        cwd=BACKEND,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return proc.stdout


def test_pytest_ini_registers_slow_and_fast_markers():
    """marker 必须真正注册（不是只在注释里写），否则 -m "not slow" 会静默选错。"""
    assert PYTEST_INI.is_file(), "缺少 backend/pytest.ini（marker 无处登记）"
    markers = _pytest_markers()
    assert "@pytest.mark.slow" in markers, markers
    assert "@pytest.mark.fast" in markers, markers


def test_pytest_ini_sets_bounded_default_timeout():
    text = PYTEST_INI.read_text(encoding="utf-8")
    assert "testpaths = tests" in text, "pytest.ini 应把默认收集范围钉在 tests"
    assert "timeout = 120" in text, (
        "pytest.ini 应给 pytest-timeout 一个默认上限，否则漏写 --timeout 时会无限挂起"
    )


def test_shard_script_emits_one_path_per_line():
    """Windows 上 Python 文本模式会输出 CRLF；CR 残留会让 pytest 找不到文件。"""
    proc = _shard("--shard", "1", "--total", "4")
    assert proc.returncode == 0, proc.stderr
    assert "\r" not in proc.stdout, "分片脚本输出了 CR（mapfile 会把它留在路径尾部）"
    lines = [line for line in proc.stdout.splitlines() if line]
    assert lines, "分片 1 没有任何测试文件"
    for line in lines:
        assert " " not in line, f"每行只能有一个路径：{line!r}"
        assert (BACKEND / line).is_file(), f"分片输出指向不存在的文件：{line!r}"


def test_shard_script_covers_every_test_file_exactly_once():
    expected = sorted(path.name for path in (BACKEND / "tests").glob("test_*.py"))
    assert expected, "backend/tests 下没有测试文件"
    seen: list[str] = []
    for shard in range(1, 5):
        proc = _shard("--shard", str(shard), "--total", "4")
        assert proc.returncode == 0, proc.stderr
        seen.extend(
            Path(line).name
            for line in proc.stdout.splitlines()
            if line
        )
    assert sorted(seen) == expected, (
        f"分片漏跑/重复：漏 {sorted(set(expected) - set(seen))}，"
        f"重复 {sorted({name for name in seen if seen.count(name) > 1})}"
    )


def _load_shard_module():
    spec = importlib.util.spec_from_file_location("shard_tests_contract", SHARD)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_shard_script_balances_by_collected_test_count():
    """按展开用例数均衡，避免某片被单个大文件拖住；同一输入必须可复现。"""
    module = _load_shard_module()
    files = sorted((BACKEND / "tests").glob("test_*.py"))
    buckets = module.assign(files, 4)
    loads = [sum(module.test_weight(path) for path in bucket) for bucket in buckets]
    assert min(loads) > 0, f"存在空分片：{loads}"
    assert max(loads) / min(loads) <= 1.10, f"分片不均衡：{loads}"

    first = _shard("--shard", "2", "--total", "4").stdout
    second = _shard("--shard", "2", "--total", "4").stdout
    assert first == second, "同一输入两次分片结果不同（不可复现）"


def test_shard_script_fast_mode_skips_module_level_slow(tmp_path):
    root = tmp_path / "tests"
    root.mkdir()
    (root / "test_fast_case.py").write_text(
        "def test_a():\n    assert True\n", encoding="utf-8",
    )
    (root / "test_slow_file.py").write_text(
        "import pytest\n\n"
        "pytestmark = pytest.mark.slow\n\n\n"
        "def test_b():\n    assert True\n",
        encoding="utf-8",
    )
    (root / "test_single_slow.py").write_text(
        "import pytest\n\n\n"
        "@pytest.mark.slow\n"
        "def test_c():\n    assert True\n\n\n"
        "def test_d():\n    assert True\n",
        encoding="utf-8",
    )

    full = _shard("--root", str(root), "--mode", "full")
    fast = _shard("--root", str(root), "--mode", "fast")
    assert full.returncode == 0 and fast.returncode == 0, full.stderr + fast.stderr

    full_names = sorted(Path(line).name for line in full.stdout.splitlines() if line)
    fast_names = sorted(Path(line).name for line in fast.stdout.splitlines() if line)
    assert full_names == ["test_fast_case.py", "test_single_slow.py", "test_slow_file.py"]
    assert fast_names == ["test_fast_case.py", "test_single_slow.py"], (
        f"fast 只应跳过整文件 slow，单例 slow 交给 -m \"not slow\"：{fast_names}"
    )


def test_ci_backend_runs_four_shards_with_per_shard_artifacts():
    backend = _jobs()["backend"]
    assert backend["strategy"]["matrix"]["shard"] == [1, 2, 3, 4], (
        "CI 必须把后端拆成 4 个分片（4 核 runner 上 -n 12 会过度订阅）"
    )
    script = next(
        step.get("run") or ""
        for step in backend["steps"]
        if str(step.get("name", "")).startswith("后端测试")
    )
    assert "dev/shard_tests.py" in script, "分片必须由单一脚本产出，避免 CI 与本地规则漂移"
    assert "mapfile" in script, "mapfile 需要脚本每行一个路径才成立"
    uploads = [
        step for step in backend["steps"]
        if str(step.get("uses", "")).startswith("actions/upload-artifact")
    ]
    assert len(uploads) == 1, "后端分片应各自上传一份报告"
    with_ = uploads[0].get("with") or {}
    assert "matrix.shard" in str(with_.get("name")), "artifact 名必须带分片号，避免互相覆盖"
    assert "matrix.shard" in str(with_.get("path")), "上传路径必须带分片号"


def test_ci_shard_total_matches_matrix_size():
    """matrix 分片数改了但脚本 --total 没改时，会静默漏跑或多跑。"""
    backend = _jobs()["backend"]
    shards = backend["strategy"]["matrix"]["shard"]
    script = next(
        step.get("run") or ""
        for step in backend["steps"]
        if str(step.get("name", "")).startswith("后端测试")
    )
    match = re.search(r"--total (\d+)", script)
    assert match, f"后端测试步骤缺少 --total：{script}"
    assert "--shard ${{ matrix.shard }}" in script, f"分片号未取自 matrix：{script}"
    assert int(match.group(1)) == len(shards), (
        f"matrix 有 {len(shards)} 个分片，但脚本按 --total {match.group(1)} 切分"
    )


def test_ci_fast_gate_uses_slow_marker_expression():
    backend = _jobs()["backend"]
    script = next(
        step.get("run") or ""
        for step in backend["steps"]
        if str(step.get("name", "")).startswith("后端测试")
    )
    assert 'not slow' in script, "缺 fast 闸门：应支持 pytest -m \"not slow\""
    raw = _ci()
    triggers = raw.get(True) or raw.get("on") or {}
    suite = triggers["workflow_dispatch"]["inputs"]["suite"]
    assert suite["options"] == ["full", "fast"], suite
    assert suite["default"] == "full", "默认必须是全量，防止慢用例被静默漏跑"
