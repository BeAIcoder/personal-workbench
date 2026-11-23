#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""契约检查：版本、超时预算、计数口径、命名黑名单。

设计原则：
- 只用标准库，CI 的 contracts job 不需要安装后端依赖；
- 默认只拦“确定错误”（版本/超时/过期注释/旧入口），计数差异只告警，
  因为工作树可能含未提交模块（如 finance_receipt）；`--strict-counts` 才升级为失败；
- 输出不回显敏感值，只打印文件与规则。
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FAILS: list[str] = []
WARNS: list[str] = []
INFOS: list[str] = []

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def fail(message: str) -> None:
    FAILS.append(message)


def warn(message: str) -> None:
    WARNS.append(message)


def info(message: str) -> None:
    INFOS.append(message)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def one(pattern: str, text: str, label: str) -> str | None:
    match = re.search(pattern, text, re.MULTILINE)
    if not match:
        fail(f"{label}：未找到可解析的值（pattern={pattern}）")
        return None
    return match.group(1)


def config_version() -> str | None:
    text = read(ROOT / "backend/app/config.py")
    return one(r'^\s*version:\s*str\s*=\s*"([^"]+)"', text, "config.py 版本")


def package_versions() -> tuple[str | None, str | None]:
    package = json.loads(read(ROOT / "frontend/package.json"))
    lock = json.loads(read(ROOT / "frontend/package-lock.json"))
    return package.get("version"), lock.get("version")


def check_versions() -> str | None:
    version = config_version()
    package_version, lock_version = package_versions()
    if version and package_version and version != package_version:
        fail(f"版本不一致：config.py={version}，package.json={package_version}")
    if version and lock_version and version != lock_version:
        fail(f"版本不一致：config.py={version}，package-lock.json={lock_version}")
    for doc in ("README.md", "使用说明.md"):
        text = read(ROOT / doc)
        if version and f"v{version}" not in text:
            warn(f"{doc} 未出现当前版本 v{version}（若已改用其他写法请同步）")
    return version


def int_const(path: Path, name: str) -> int | None:
    text = read(path)
    match = re.search(rf"^{re.escape(name)}\s*=\s*(\d+)", text, re.MULTILINE)
    if not match:
        fail(f"{path.relative_to(ROOT)} 缺少常量 {name}")
        return None
    return int(match.group(1))


def check_board_budget() -> None:
    service = ROOT / "backend/app/modules/finance_board/service.py"
    values = {
        name: int_const(service, name)
        for name in (
            "STEP_TIMEOUT",
            "TASK_CHANNEL_TIMEOUT",
            "DIRECT_ATTEMPT_BUDGET_SECONDS",
            "TASK_QUERY_TIMEOUT_SECONDS",
            "TASK_TRIGGER_TIMEOUT_SECONDS",
            "_PIPELINE_ATTEMPTS",
            "_RETRY_DELAY_SECONDS",
            "RETRY_LOCK_WAIT_SECONDS",
        )
    }
    if any(value is None for value in values.values()):
        return
    per_attempt = (
        2 * values["TASK_QUERY_TIMEOUT_SECONDS"]
        + values["TASK_TRIGGER_TIMEOUT_SECONDS"]
        + values["DIRECT_ATTEMPT_BUDGET_SECONDS"]
    )
    worst = per_attempt * values["_PIPELINE_ATTEMPTS"] + (
        values["_PIPELINE_ATTEMPTS"] - 1
    ) * (values["_RETRY_DELAY_SECONDS"] + values["RETRY_LOCK_WAIT_SECONDS"])

    board_view = read(ROOT / "frontend/src/modules/finance-board/views/BoardView.vue")
    match = re.search(
        r"api\.post\('/finance/board/run',\s*\{\},\s*\{\s*timeout:\s*(\d+)\s*\}\)",
        board_view,
    )
    if not match:
        fail("BoardView.vue 未找到 /finance/board/run 的 timeout")
        return
    frontend_ms = int(match.group(1))
    if frontend_ms < worst * 1000:
        fail(
            f"看板前端超时 {frontend_ms}ms 小于后端最坏预算 {worst}s"
            f"（per_attempt={per_attempt}s, attempts={values['_PIPELINE_ATTEMPTS']}）"
        )
    if values["DIRECT_ATTEMPT_BUDGET_SECONDS"] < values["STEP_TIMEOUT"]:
        fail("看板 DIRECT_ATTEMPT_BUDGET_SECONDS 小于 STEP_TIMEOUT")
    info(f"看板预算：per_attempt={per_attempt}s，worst={worst}s，前端={frontend_ms / 1000:.0f}s")


def check_job_timeout() -> None:
    scheduler = read(ROOT / "backend/app/scheduler.py")
    match = re.search(r"^AGENT_JOB_TIMEOUT_SECONDS\s*=\s*(\d+)", scheduler, re.MULTILINE)
    if not match:
        fail("scheduler.py 缺少 AGENT_JOB_TIMEOUT_SECONDS")
        return
    backend_seconds = int(match.group(1))

    jobs_js = read(ROOT / "frontend/src/utils/jobs.js")
    match = re.search(r"JOB_RUN_TIMEOUT_MS\s*=\s*(\d+)\s*\*\s*1000", jobs_js)
    if not match:
        fail("jobs.js 缺少 JOB_RUN_TIMEOUT_MS")
        return
    frontend_ms = int(match.group(1)) * 1000
    if frontend_ms < backend_seconds * 1000:
        fail(
            f"定时任务前端超时 {frontend_ms}ms 小于后端预算 {backend_seconds}s"
        )
    info(f"定时任务超时：后端={backend_seconds}s，前端={frontend_ms / 1000:.0f}s")


def module_and_table_counts() -> dict[str, int]:
    modules_dir = ROOT / "backend/app/modules"
    backend_modules = [
        p for p in modules_dir.iterdir()
        if p.is_dir() and p.name != "_template" and not p.name.startswith((".", "__"))
    ]
    frontend_modules = [
        p for p in (ROOT / "frontend/src/modules").iterdir()
        if p.is_dir() and p.name != "_shared" and not p.name.startswith((".", "__"))
    ]
    table_names: set[str] = set()
    for path in (ROOT / "backend/app").rglob("*.py"):
        text = read(path)
        table_names.update(re.findall(r'__tablename__\s*=\s*["\']([^"\']+)["\']', text))
    loaded_mod_tables = {
        name for name in table_names
        if name.startswith("mod_") and name != "mod_example_entry"
    }
    return {
        "backend_modules": len(backend_modules),
        "frontend_modules": len(frontend_modules),
        "mod_tables": len(loaded_mod_tables),
        "host_tables": len(table_names - loaded_mod_tables - {"mod_example_entry"}),
        "finance_modules": len([
            p for p in backend_modules
            if p.name.startswith("finance_") and p.name != "finance_hello"
        ]),
    }


def check_counts(strict: bool) -> None:
    counts = module_and_table_counts()
    info(
        "计数（工作树）："
        f"后端模块={counts['backend_modules']}，前端模块={counts['frontend_modules']}，"
        f"模块表={counts['mod_tables']}，宿主表={counts['host_tables']}，"
        f"财务生产模块={counts['finance_modules']}"
    )

    agents = read(ROOT / "AGENTS.md")
    match = re.search(r"(\d+) 个模块 / (\d+) 张模块表", agents)
    if match:
        claimed_modules, claimed_tables = int(match.group(1)), int(match.group(2))
        if (claimed_modules, claimed_tables) != (
            counts["backend_modules"], counts["mod_tables"]
        ):
            message = (
                "AGENTS 计数与工作树不一致："
                f"AGENTS={claimed_modules}/{claimed_tables}，"
                f"工作树={counts['backend_modules']}/{counts['mod_tables']}。"
                "若差异来自未提交模块，请在该句标注 as-of 基线。"
            )
            fail(message) if strict else warn(message)

    spec = read(ROOT / "spec.md")
    match = re.search(r"ORM 定义 \*\*(\d+) 张表\*\*", spec)
    if match:
        claimed_total = int(match.group(1))
        actual_total = counts["host_tables"] + counts["mod_tables"]
        if claimed_total != actual_total:
            spec_lines = spec.splitlines()
            spec_index = next((i for i, line in enumerate(spec_lines) if "ORM 定义" in line), 0)
            spec_context = "\n".join(spec_lines[max(0, spec_index - 1): spec_index + 5])
            message = (
                f"spec.md 表数与工作树不一致：spec={claimed_total}，"
                f"工作树={actual_total}（宿主 {counts['host_tables']} + 模块 {counts['mod_tables']}）"
            )
            if "已提交基线" in spec_context or "as-of" in spec_context.lower():
                info(message + "；已标注 as-of 基线，按快照处理")
            else:
                fail(message) if strict else warn(message)

    readme = read(ROOT / "README.md")
    match = re.search(r"财务模块（(\d+)）", readme)
    if match and int(match.group(1)) != counts["finance_modules"]:
        readme_lines = readme.splitlines()
        readme_index = next((i for i, line in enumerate(readme_lines) if "财务模块（" in line), 0)
        readme_context = "\n".join(readme_lines[max(0, readme_index - 1): readme_index + 2])
        message = (
            f"README 财务模块数与工作树不一致：README={match.group(1)}，"
            f"工作树={counts['finance_modules']}（已提交基线可能少 1 个回单模块）"
        )
        if "已提交基线" in readme_context or "as-of" in readme_context.lower():
            info(message + "；已标注 as-of 基线，按快照处理")
        else:
            fail(message) if strict else warn(message)


def check_naming() -> None:
    for doc in ("README.md", "使用说明.md"):
        text = read(ROOT / doc)
        if "数据仓" in text:
            fail(f"{doc} 仍出现旧入口“数据仓”；请改为“数据池清洗”（模板文件名可保留旧名）")
    main = read(ROOT / "backend/app/main.py")
    if "每 20s" in main:
        fail("backend/app/main.py 仍有“每 20s 检查”过期注释；当前是单定时器 + TICK_SECONDS")
    if not (ROOT / "docs/INVARIANTS.md").is_file():
        fail("缺少 docs/INVARIANTS.md")
    if not (ROOT / "docs/README.md").is_file():
        fail("缺少 docs/README.md")


def check_git_ahead() -> None:
    agents = read(ROOT / "AGENTS.md")
    match = re.search(r"2026-11-17 为 (\d+)/0", agents)
    if not match:
        return
    claimed = int(match.group(1))
    try:
        result = subprocess.run(
            ["git", "rev-list", "--left-right", "--count", "origin/master...HEAD"],
            cwd=ROOT, capture_output=True, text=True, timeout=10,
        )
    except Exception:
        warn("无法运行 git，跳过领先提交数检查")
        return
    if result.returncode != 0:
        warn("git rev-list 失败，跳过领先提交数检查")
        return
    ahead = int(result.stdout.split()[1])
    if claimed != ahead:
        warn(
            f"AGENTS 的领先提交数 {claimed} 与实际 {ahead} 不一致；"
            "这是快照值，推送/合并后请更新（不阻断 CI）"
        )


def main() -> int:
    global ROOT
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=str(ROOT), help="仓库根目录")
    parser.add_argument("--strict-counts", action="store_true", help="计数不一致也失败")
    args = parser.parse_args()

    ROOT = Path(args.root).resolve()

    version = check_versions()
    check_board_budget()
    check_job_timeout()
    check_counts(args.strict_counts)
    check_naming()
    check_git_ahead()

    for line in INFOS:
        print(f"[INFO] {line}")
    for line in WARNS:
        print(f"[WARN] {line}")
    for line in FAILS:
        print(f"[FAIL] {line}")
    if FAILS:
        print(f"\n契约检查失败：{len(FAILS)} 项")
        return 1
    print(f"\n契约检查通过（版本 {version or '?'}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
