# -*- coding: utf-8 -*-
"""T7C 发布链路契约：版本单一源、映射预检、PII 门禁、xlsx 扫描、依赖锁。

全部使用合成 staging / 临时映射表 / 假 runner，不读真实 backend/data、
不启动打包流程、不访问网络，也不回显任何命中的真实值。
"""
import importlib.util
import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

BACKEND = Path(__file__).resolve().parent.parent
REPO = BACKEND.parent
DEV = REPO / "dev"
CONFIG = BACKEND / "app" / "config.py"
LOCK = BACKEND / "requirements-lock.txt"
REQUIREMENTS = BACKEND / "requirements.txt"


def _load_package_release():
    if str(DEV) not in sys.path:
        sys.path.insert(0, str(DEV))
    spec = importlib.util.spec_from_file_location("release_package_mod", DEV / "package_release.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _config_version() -> str:
    match = re.search(r'^\s*version:\s*str\s*=\s*"([^"]+)"', CONFIG.read_text(encoding="utf-8"), flags=re.M)
    assert match, "config.py 中找不到 version 定义"
    return match.group(1)


def _write_mapping(path: Path, reals: list[str]) -> Path:
    payload = {"mappings": [{"real": real, "placeholder": "<占位>"} for real in reals]}
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


# ---------------------------------------------------------------- 版本单一源

def test_read_version_uses_backend_config_not_package_json(tmp_path, monkeypatch):
    mod = _load_package_release()
    root = tmp_path / "repo"
    config = root / "backend" / "app" / "config.py"
    config.parent.mkdir(parents=True)
    config.write_text('class Settings:\n    version: str = "9.9.9"\n', encoding="utf-8")
    (root / "frontend").mkdir()
    (root / "frontend" / "package.json").write_text('{"version": "1.1.1"}', encoding="utf-8")
    monkeypatch.setattr(mod, "ROOT", root)
    monkeypatch.setattr(mod, "CONFIG_PY", config, raising=False)

    assert mod.read_version() == "9.9.9"


def test_read_version_missing_definition_raises_chinese_package_error(tmp_path, monkeypatch):
    mod = _load_package_release()
    config = tmp_path / "config.py"
    config.write_text('class Settings:\n    app_name: str = "个人工作台"\n', encoding="utf-8")
    monkeypatch.setattr(mod, "CONFIG_PY", config, raising=False)

    with pytest.raises(mod.PackageError) as exc:
        mod.read_version()
    message = str(exc.value)
    assert "版本" in message and "config.py" in message, message


def test_read_version_matches_config_single_source():
    mod = _load_package_release()
    assert mod.read_version() == _config_version()


def test_verify_frontend_version_rejects_lock_drift(tmp_path, monkeypatch):
    mod = _load_package_release()
    root = tmp_path / "repo"
    config = root / "backend" / "app" / "config.py"
    config.parent.mkdir(parents=True)
    config.write_text('    version: str = "1.0.0"\n', encoding="utf-8")
    (root / "frontend").mkdir()
    (root / "frontend" / "package.json").write_text('{"version": "1.0.0"}', encoding="utf-8")
    (root / "frontend" / "package-lock.json").write_text(
        json.dumps({"version": "0.9.0", "packages": {"": {"version": "0.9.0"}}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(mod, "ROOT", root)
    monkeypatch.setattr(mod, "CONFIG_PY", config, raising=False)

    with pytest.raises(mod.PackageError) as exc:
        mod.verify_frontend_version("1.0.0")
    message = str(exc.value)
    assert "版本不一致" in message and "package-lock" in message, message


def test_main_reports_version_error_as_chinese_failure(monkeypatch, capsys):
    mod = _load_package_release()

    def broken() -> str:
        raise mod.PackageError("版本未在 config.py 中定义")

    monkeypatch.setattr(mod, "read_version", broken)
    monkeypatch.setattr(sys, "argv", ["package_release.py", "--skip-smoke"])

    assert mod.main() == 1
    output = capsys.readouterr().out
    assert "打包失败" in output and "版本未在 config.py 中定义" in output, output


# ---------------------------------------------------------------- 映射表预检

def test_load_sanitize_mapping_rejects_empty_mappings(tmp_path, monkeypatch):
    mod = _load_package_release()
    mapping = tmp_path / "map.json"
    mapping.write_text('{"mappings": []}', encoding="utf-8")
    monkeypatch.setattr(mod, "SANITIZE_MAP", mapping)

    with pytest.raises(mod.PackageError) as exc:
        mod.load_sanitize_mapping()
    message = str(exc.value)
    assert "映射" in message and "空" in message, message


def test_load_sanitize_mapping_rejects_broken_json(tmp_path, monkeypatch):
    mod = _load_package_release()
    mapping = tmp_path / "map.json"
    mapping.write_text("{not-json", encoding="utf-8")
    monkeypatch.setattr(mod, "SANITIZE_MAP", mapping)

    with pytest.raises(mod.PackageError) as exc:
        mod.load_sanitize_mapping()
    message = str(exc.value)
    assert "JSON" in message and "映射" in message, message


def test_load_sanitize_mapping_returns_two_char_values(tmp_path, monkeypatch):
    mod = _load_package_release()
    mapping = _write_mapping(tmp_path / "map.json", ["张三", "合成客户全称"])
    monkeypatch.setattr(mod, "SANITIZE_MAP", mapping)

    assert mod.load_sanitize_mapping() == ["张三", "合成客户全称"]


def test_preflight_runs_pii_gate_before_build(tmp_path, monkeypatch):
    mod = _load_package_release()
    venv_python = tmp_path / "python.exe"
    venv_python.write_bytes(b"")
    mapping = _write_mapping(tmp_path / "map.json", ["合成客户全称"])
    calls: list[str] = []
    monkeypatch.setattr(mod, "VENV_PY", venv_python)
    monkeypatch.setattr(mod, "SANITIZE_MAP", mapping)
    monkeypatch.setattr(mod, "resolve_tool", lambda name: f"D:/path/to/tools/{name}.exe")
    monkeypatch.setattr(mod, "git_status", lambda: "", raising=False)
    monkeypatch.setattr(mod, "verify_frontend_version", lambda version: None, raising=False)
    monkeypatch.setattr(mod, "run_pii_gate", lambda: calls.append("pii"), raising=False)

    mod.preflight()

    assert calls == ["pii"]


# ---------------------------------------------------------------- PII 门禁

def _patch_gate_paths(mod, tmp_path, monkeypatch, *, with_terms: bool = True) -> Path:
    python = tmp_path / "python.exe"
    python.write_bytes(b"")
    check = tmp_path / "check_pii.py"
    check.write_text("# synthetic gate", encoding="utf-8")
    terms = tmp_path / "pii-terms.local.txt"
    if with_terms:
        terms.write_text("合成客户全称\n", encoding="utf-8")
    monkeypatch.setattr(mod, "PII_PYTHON", python)
    monkeypatch.setattr(mod, "CHECK_PII", check)
    monkeypatch.setattr(mod, "PII_TERMS", terms)
    return terms


def test_pii_gate_command_targets_archive_with_local_terms(tmp_path, monkeypatch):
    mod = _load_package_release()
    terms = _patch_gate_paths(mod, tmp_path, monkeypatch)

    command = mod.pii_gate_command()

    assert command[0] == str(tmp_path / "python.exe")
    assert command[1] == str(tmp_path / "check_pii.py")
    assert command[command.index("--archive") + 1] == "HEAD"
    assert command[command.index("--terms") + 1] == str(terms)
    assert "--json" in command


def test_pii_gate_command_omits_missing_terms_file(tmp_path, monkeypatch):
    mod = _load_package_release()
    _patch_gate_paths(mod, tmp_path, monkeypatch, with_terms=False)

    assert "--terms" not in mod.pii_gate_command()


def test_run_pii_gate_failure_reports_locations_without_values(tmp_path, monkeypatch):
    mod = _load_package_release()
    _patch_gate_paths(mod, tmp_path, monkeypatch)
    captured: dict = {}
    fake = SimpleNamespace(
        returncode=1,
        stdout=json.dumps([
            {"path": "backend/app/x.py", "rule": "token", "location": "3", "value": "sk-REALVALUE1234567890"},
            {"path": "frontend/src/y.js", "rule": "win_path", "location": "9", "value": "D:/path/to/real"},
        ]),
        stderr="",
    )

    def runner(cmd, **kwargs):
        captured["cmd"] = cmd
        return fake

    with pytest.raises(mod.PackageError) as exc:
        mod.run_pii_gate(runner=runner)

    message = str(exc.value)
    assert "PII 门禁未通过" in message and "2 处" in message, message
    assert "backend/app/x.py:3" in message and "frontend/src/y.js:9" in message, message
    assert "REALVALUE" not in message and "D:/path/to/real" not in message, message
    assert captured["cmd"] == mod.pii_gate_command()


def test_run_pii_gate_scan_error_is_chinese_package_error(tmp_path, monkeypatch):
    mod = _load_package_release()
    _patch_gate_paths(mod, tmp_path, monkeypatch)
    fake = SimpleNamespace(returncode=2, stdout="", stderr="")

    with pytest.raises(mod.PackageError) as exc:
        mod.run_pii_gate(runner=lambda cmd, **kwargs: fake)

    message = str(exc.value)
    assert "PII 扫描失败" in message and "2" in message, message


def test_run_pii_gate_success_passes(tmp_path, monkeypatch):
    mod = _load_package_release()
    _patch_gate_paths(mod, tmp_path, monkeypatch)
    fake = SimpleNamespace(returncode=0, stdout="[]", stderr="")

    assert mod.run_pii_gate(runner=lambda cmd, **kwargs: fake) is None


# ---------------------------------------------------------------- xlsx 单元格

@pytest.fixture()
def scan_ctx(tmp_path, monkeypatch):
    mod = _load_package_release()
    mapping = _write_mapping(tmp_path / "map.json", [])
    monkeypatch.setattr(mod, "SANITIZE_MAP", mapping)
    monkeypatch.setattr(mod, "load_pii_terms", lambda: [])
    monkeypatch.setattr(mod, "structural_check", lambda staging: [])
    staging = tmp_path / "staging"
    staging.mkdir()
    return mod, staging


def _save_xlsx(path: Path, rows: dict[str, str]) -> None:
    import openpyxl

    path.parent.mkdir(parents=True, exist_ok=True)
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "台账"
    for coordinate, value in rows.items():
        sheet[coordinate] = value
    workbook.save(path)
    workbook.close()


def test_scan_secrets_reads_pii_terms_in_xlsx_cells(scan_ctx, monkeypatch):
    mod, staging = scan_ctx
    monkeypatch.setattr(mod, "load_pii_terms", lambda: ["合成客户全称"])
    _save_xlsx(staging / "数据模板" / "台账.xlsx", {"B2": "付款方：合成客户全称"})

    with pytest.raises(mod.PackageError) as exc:
        mod.scan_secrets(staging)
    assert "真实客户/员工名称" in str(exc.value)
    assert "数据模板/台账.xlsx" in str(exc.value)


def test_scan_secrets_reads_sanitize_mapping_in_xlsx_cells(scan_ctx):
    mod, staging = scan_ctx
    mod.SANITIZE_MAP.write_text(
        json.dumps({"mappings": [{"real": "张三", "placeholder": "<占位>"}]}, ensure_ascii=False),
        encoding="utf-8",
    )
    _save_xlsx(staging / "台账.xlsx", {"C3": "经办人张三"})

    with pytest.raises(mod.PackageError) as exc:
        mod.scan_secrets(staging)
    assert "映射表真实值" in str(exc.value)


def test_scan_secrets_fails_closed_on_corrupt_xlsx(scan_ctx):
    mod, staging = scan_ctx
    (staging / "坏.xlsx").write_bytes(b"not-a-zip")

    with pytest.raises(mod.PackageError) as exc:
        mod.scan_secrets(staging)
    assert "xlsx" in str(exc.value) and "损坏" in str(exc.value)


def test_scan_secrets_still_passes_clean_xlsx(scan_ctx):
    mod, staging = scan_ctx
    _save_xlsx(staging / "台账.xlsx", {"A1": "正常文本"})

    assert mod.scan_secrets(staging) is None


# ---------------------------------------------------------------- 依赖锁

def _lock_versions() -> dict[str, str]:
    versions: dict[str, str] = {}
    for line in LOCK.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        assert "==" in line, f"requirements-lock.txt 存在未锁定行：{line}"
        name, version = line.split("==", 1)
        versions[name.strip().lower()] = version.strip()
    return versions


def _requirement_names() -> list[str]:
    names: list[str] = []
    for line in REQUIREMENTS.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        match = re.match(r"([A-Za-z0-9][A-Za-z0-9._-]*)", line)
        assert match, line
        names.append(match.group(1).lower())
    return names


def test_lock_includes_xdist_ocr_and_vector_dependencies():
    versions = _lock_versions()
    for name in ("pytest-xdist", "rapidocr", "onnxruntime", "sqlite-vec"):
        assert name in versions, f"requirements-lock.txt 缺少 {name}"


def test_lock_covers_every_requirement_from_requirements_txt():
    versions = _lock_versions()
    missing = [name for name in _requirement_names() if name not in versions]
    assert not missing, f"requirements-lock.txt 缺少：{missing}"


def test_lock_pins_every_line_exactly():
    bare: list[str] = []
    for line in LOCK.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*==[^\s;]+", line):
            bare.append(line)
    assert not bare, f"requirements-lock.txt 存在非精确锁定行：{bare}"


def _requirement_spec(name: str) -> str:
    for line in REQUIREMENTS.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if re.match(rf"^{re.escape(name)}\b", line, re.IGNORECASE):
            return line[len(name):].strip()
    raise AssertionError(f"requirements.txt 未声明 {name}")


def _major_minor(version: str) -> tuple[int, int]:
    parts = [int(part) for part in re.findall(r"\d+", version)[:2]]
    return (parts + [0, 0])[:2]


def test_pytest_timeout_is_declared_and_pinned_consistently():
    """CI 的 --timeout 依赖 pytest-timeout；requirements 与 lock 必须同时覆盖且兼容。"""
    versions = _lock_versions()
    assert "pytest-timeout" in versions, (
        "requirements-lock.txt 缺少 pytest-timeout：CI 的 --timeout 会直接报 unknown option"
    )
    spec = _requirement_spec("pytest-timeout")
    locked = _major_minor(versions["pytest-timeout"])

    bounds = re.findall(r"(>=|<=|==|>|<)\s*([0-9][0-9.]*)", spec)
    assert bounds, f"pytest-timeout 应带版本约束，实际：{spec!r}"
    for operator, value in bounds:
        target = _major_minor(value)
        if operator == ">=":
            assert locked >= target, f"锁定 {versions['pytest-timeout']} 不满足 {operator}{value}"
        elif operator == ">":
            assert locked > target, f"锁定 {versions['pytest-timeout']} 不满足 {operator}{value}"
        elif operator == "<=":
            assert locked <= target, f"锁定 {versions['pytest-timeout']} 不满足 {operator}{value}"
        elif operator == "<":
            assert locked < target, f"锁定 {versions['pytest-timeout']} 不满足 {operator}{value}"
        else:
            assert locked == target, f"锁定 {versions['pytest-timeout']} 不满足 {operator}{value}"
