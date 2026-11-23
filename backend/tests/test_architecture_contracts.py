# -*- coding: utf-8 -*-
"""架构契约测试：模块边界、manifest 加载、工具参数结构、存量基线。"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_architecture_checker_passes():
    result = subprocess.run(
        [sys.executable, str(ROOT / "dev" / "check_architecture.py")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_all_manifests_load():
    from app.modules.registry import _load_manifest, discover_module_names

    names = discover_module_names()
    assert names
    failures = {name: "manifest 校验/导入失败" for name in names if _load_manifest(name) is None}
    assert not failures, failures


def test_module_tool_param_required_is_consistent():
    from app.modules.registry import _load_manifest, discover_module_names

    mismatches = []
    for name in discover_module_names():
        manifest = _load_manifest(name)
        if manifest is None:
            continue
        for tool in manifest.agent_tools:
            params = tool.get("params") or {}
            required = set(tool.get("required") or [])
            missing = required - set(params)
            if missing:
                mismatches.append((manifest.name, tool.get("name"), "required-not-in-params", sorted(missing)))
            for pname, spec in params.items():
                if isinstance(spec, (tuple, list)) and len(spec) >= 3:
                    if bool(spec[2]) != (pname in required):
                        mismatches.append(
                            (manifest.name, tool.get("name"), pname, spec[2], pname in required)
                        )
    assert not mismatches, mismatches


def test_god_file_budget_has_no_new_offenders():
    import json

    baseline = json.loads(
        (ROOT / "dev/architecture-baseline.json").read_text(encoding="utf-8")
    )
    allowed = set(baseline.get("god_files", {}))
    offenders = []
    for pattern in ("backend/app/**/*.py", "frontend/src/**/*.vue", "frontend/src/**/*.js"):
        for path in ROOT.glob(pattern):
            lines = len(path.read_text(encoding="utf-8", errors="replace").splitlines())
            if lines > 1200:
                key = str(path.relative_to(ROOT)).replace("\\", "/")
                if key not in allowed:
                    offenders.append((key, lines))
    assert not offenders, offenders
