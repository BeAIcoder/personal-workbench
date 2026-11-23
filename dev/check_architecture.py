#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""架构检查（AST/正则，无第三方依赖）：模块边界、金额 round、文件体量。

策略：存量基线 ratchet。
- `dev/architecture-baseline.json` 记录当前允许的存量违反；
- 新增跨模块 import、round 次数增加、文件变大 → 失败；
- 清理后基线可下调（脚本只提示，不自动改）。
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "dev/architecture-baseline.json"
SOFT_LIMIT = 800
HARD_LIMIT = 1200

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

IMPORT_RE = re.compile(
    r"(?:from\s+\.\.([A-Za-z_][A-Za-z0-9_]*)"
    r"|from\s+app\.modules\.([A-Za-z_][A-Za-z0-9_]*)"
    r"|import\s+app\.modules\.([A-Za-z_][A-Za-z0-9_]*))"
)
REQUIRES_RE = re.compile(r"requires\s*=\s*\[([^\]]*)\]")


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def module_dirs() -> list[Path]:
    root = ROOT / "backend/app/modules"
    return sorted(
        p for p in root.iterdir()
        if p.is_dir() and p.name != "_template" and not p.name.startswith((".", "__"))
    )


def requires_of(module_dir: Path) -> set[str]:
    manifest = module_dir / "manifest.py"
    if not manifest.is_file():
        return set()
    match = REQUIRES_RE.search(read(manifest))
    if not match:
        return set()
    return set(re.findall(r"['\"]([^'\"]+)['\"]", match.group(1)))


def cross_module_imports() -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for module_dir in module_dirs():
        current = module_dir.name
        allowed = requires_of(module_dir)
        for path in module_dir.rglob("*.py"):
            text = read(path)
            relative_parts = path.relative_to(module_dir).parts
            found: set[str] = set()
            for match in IMPORT_RE.finditer(text):
                parent_relative, absolute_a, absolute_b = match.groups()
                other = parent_relative or absolute_a or absolute_b or ""
                if not other or other == current:
                    continue
                # `from ..x` 只在文件直接位于模块根时才是跨模块；
                # 在 routers/services 子包里，`..models` 是同一模块的 models。
                if parent_relative and len(relative_parts) > 1:
                    continue
                if other in allowed:
                    continue
                if other in {"registry", "spec", "__init__"}:
                    continue
                found.add(other)
            if found:
                key = str(path.relative_to(ROOT)).replace("\\", "/")
                result[key] = sorted(found)
    return result


def round_calls() -> dict[str, int]:
    result: dict[str, int] = {}
    modules = ROOT / "backend/app/modules"
    for path in modules.rglob("*.py"):
        if not path.parts[len(modules.parts)].startswith("finance_"):
            continue
        try:
            tree = ast.parse(read(path))
        except SyntaxError:
            continue
        count = sum(
            1 for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "round"
        )
        if count:
            key = str(path.relative_to(ROOT)).replace("\\", "/")
            result[key] = count
    return result


def file_lines() -> dict[str, int]:
    result: dict[str, int] = {}
    roots = [
        (ROOT / "backend/app", "*.py"),
        (ROOT / "frontend/src", "*.vue"),
        (ROOT / "frontend/src", "*.js"),
    ]
    for root, pattern in roots:
        for path in root.rglob(pattern):
            try:
                lines = len(read(path).splitlines())
            except OSError:
                continue
            if lines > HARD_LIMIT:
                key = str(path.relative_to(ROOT)).replace("\\", "/")
                result[key] = lines
    return result


def load_baseline() -> dict:
    if not BASELINE.is_file():
        return {"cross_module_imports": {}, "round_calls": {}, "god_files": {}}
    return json.loads(read(BASELINE))


def compare(current: dict, baseline: dict, label: str, fails: list[str], infos: list[str]) -> None:
    for key, value in current.items():
        previous = baseline.get(key)
        if isinstance(value, list):
            previous = previous or []
            added = sorted(set(value) - set(previous))
            if added:
                fails.append(f"{label} 新增：{key} -> {', '.join(added)}")
            removed = sorted(set(previous) - set(value))
            if removed:
                infos.append(f"{label} 可下调：{key} 移除 {', '.join(removed)}")
        else:
            if previous is None:
                fails.append(f"{label} 新增：{key}={value}")
            elif value > previous:
                fails.append(f"{label} 增长：{key} {previous} -> {value}")
            elif value < previous:
                infos.append(f"{label} 可下调：{key} {previous} -> {value}")
    for key in baseline:
        if key not in current:
            infos.append(f"{label} 可删除基线：{key}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write-baseline", action="store_true", help="写入当前基线")
    args = parser.parse_args()

    current = {
        "cross_module_imports": cross_module_imports(),
        "round_calls": round_calls(),
        "god_files": file_lines(),
    }
    if args.write_baseline:
        BASELINE.write_text(
            json.dumps(current, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(f"已写入基线：{BASELINE}（跨模块 {len(current['cross_module_imports'])}，"
              f"round {sum(current['round_calls'].values())} 次，大文件 {len(current['god_files'])}）")
        return 0

    baseline = load_baseline()
    fails: list[str] = []
    infos: list[str] = []
    compare(current["cross_module_imports"], baseline.get("cross_module_imports", {}), "跨模块 import", fails, infos)
    compare(current["round_calls"], baseline.get("round_calls", {}), "财务 round", fails, infos)
    compare(current["god_files"], baseline.get("god_files", {}), "大文件", fails, infos)

    for line in infos:
        print(f"[INFO] {line}")
    for line in fails:
        print(f"[FAIL] {line}")
    if fails:
        print(f"\n架构检查失败：{len(fails)} 项（如为有意变更，请更新 dev/architecture-baseline.json）")
        return 1
    print(
        "架构检查通过（基线：跨模块 "
        f"{len(baseline.get('cross_module_imports', {}))} 文件，"
        f"round {sum(baseline.get('round_calls', {}).values())} 次，"
        f"大文件 {len(baseline.get('god_files', {}))} 个；"
        f"软上限 {SOFT_LIMIT} / 硬上限 {HARD_LIMIT}）"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
