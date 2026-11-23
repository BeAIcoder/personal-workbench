# -*- coding: utf-8 -*-
"""工程契约测试：把 dev/check_contracts.py 的关键事实变成 pytest。"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _run_script(name: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(ROOT / "dev" / name)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8", errors="replace")


def test_contract_checker_passes():
    result = _run_script("check_contracts.py")
    assert result.returncode == 0, result.stdout + result.stderr


def test_version_has_single_source():
    config = _read("backend/app/config.py")
    match = re.search(r'^\s*version:\s*str\s*=\s*"([^"]+)"', config, re.MULTILINE)
    assert match, "config.py 缺少 version"
    version = match.group(1)
    package = json.loads(_read("frontend/package.json"))
    lock = json.loads(_read("frontend/package-lock.json"))
    assert package["version"] == version
    assert lock["version"] == version
    assert f"v{version}" in _read("使用说明.md")


def test_board_frontend_timeout_covers_backend_budget():
    service = _read("backend/app/modules/finance_board/service.py")

    def const(name: str) -> int:
        match = re.search(rf"^{name}\s*=\s*(\d+)", service, re.MULTILINE)
        assert match, name
        return int(match.group(1))

    per_attempt = (
        2 * const("TASK_QUERY_TIMEOUT_SECONDS")
        + const("TASK_TRIGGER_TIMEOUT_SECONDS")
        + const("DIRECT_ATTEMPT_BUDGET_SECONDS")
    )
    attempts = const("_PIPELINE_ATTEMPTS")
    worst = per_attempt * attempts + (attempts - 1) * (
        const("_RETRY_DELAY_SECONDS") + const("RETRY_LOCK_WAIT_SECONDS")
    )
    view = _read("frontend/src/modules/finance-board/views/BoardView.vue")
    match = re.search(
        r"api\.post\('/finance/board/run',\s*\{\},\s*\{\s*timeout:\s*(\d+)\s*\}\)",
        view,
    )
    assert match, "BoardView 未设置 /finance/board/run 超时"
    assert int(match.group(1)) >= worst * 1000


def test_job_frontend_timeout_covers_backend_budget():
    scheduler = _read("backend/app/scheduler.py")
    match = re.search(r"^AGENT_JOB_TIMEOUT_SECONDS\s*=\s*(\d+)", scheduler, re.MULTILINE)
    assert match
    backend_seconds = int(match.group(1))
    jobs_js = _read("frontend/src/utils/jobs.js")
    match = re.search(r"JOB_RUN_TIMEOUT_MS\s*=\s*(\d+)\s*\*\s*1000", jobs_js)
    assert match
    assert int(match.group(1)) * 1000 >= backend_seconds * 1000


def test_current_docs_do_not_use_old_vault_entry_name():
    for doc in ("README.md", "使用说明.md"):
        assert "数据仓" not in _read(doc), f"{doc} 仍出现旧入口“数据仓”"


def test_scheduler_comment_is_current():
    main = _read("backend/app/main.py")
    assert "每 20s" not in main
    assert "TICK_SECONDS=300" in main
