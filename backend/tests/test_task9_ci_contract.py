# -*- coding: utf-8 -*-
"""Task 9：CI 与脚本换行契约（不执行 GitHub Actions，只校验配置可复现）。

- CI 必须从 frontend/.nvmrc 取 Node 版本（安全下限 24.22.1），而不是写死 "24"；
- CI 必须显式 UTF-8（Windows runner 默认代码页会让中文断言/日志 mojibake）；
- CI 后端命令与本地标准一致（-n 12）；
- .gitattributes 为 bat/vbs 声明 eol=crlf、sh 声明 eol=lf，且当前脚本实际为 CRLF。
"""
import subprocess
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
CI = REPO / ".github" / "workflows" / "ci.yml"
ATTRS = REPO / ".gitattributes"


def _ci() -> dict:
    return yaml.safe_load(CI.read_text(encoding="utf-8"))


def _steps() -> list[dict]:
    return _ci()["jobs"]["test"]["steps"]


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
    env = _ci()["jobs"]["test"].get("env") or {}
    assert env.get("PYTHONIOENCODING") == "utf-8"
    assert str(env.get("PYTHONUTF8")) in {"1", "true", "True"}


def test_ci_backend_command_matches_local_parallel_standard():
    backend = next(s for s in _steps() if s.get("name") == "后端测试")
    script = backend.get("run") or ""
    assert "-n 12" in script, "CI 应与本地标准一致使用 pytest -n 12"
    assert "pytest tests" in script, script


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
