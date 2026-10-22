# -*- coding: utf-8 -*-
"""E2E 产物安全契约守护（swarm 二轮任务 e2e-artifact-safety）。

被守护的契约：
1. 第一轮 e2e-isolation 新增的运行产物被 .gitignore 覆盖，不污染 git status：
   dev/e2e/debug_data_*/（三旧脚本 + AI 走查的调试归档目录）、
   dev/e2e/finance_data_*/（财务数据隔离目录）、dev/e2e/.e2e-run.lock
   （三脚本运行互斥锁）、*.db-journal（sqlite 运行期散件）；
2. 同一套规则绝不误伤源码与报告：dev/e2e 的 *.py/*.md、backend/tests 的
   测试文件均不被忽略（含存量缺陷：无锚点的 progress.md 规则曾把
   dev/e2e/PROGRESS.md 一并忽略）；
3. ai_walkthrough.py 默认归档到 dev/e2e 下专有的测试调试目录
   （debug_data_ai_verify，与三旧脚本目录两两不同），未显式指定时绝不写
   真实 backend/data/debug；调用方显式给出 --debug-dir 或环境变量
   DEBUG_DATA_DIR 时以调用方为准，且绝不重置/删除调用方目录；仅本脚本
   自有的默认目录在每次运行前重置。

验证方式：git check-ignore --no-index 真实规则匹配 + 模块函数/主流程行为
测试（临时目录 + monkeypatch）。不启动服务、不跑真实走查、不读/不删真实
debug 数据、不在 dev/e2e 留散落产物。
"""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
E2E_DIR = REPO / "dev" / "e2e"
AI_WALKTHROUGH = E2E_DIR / "ai_walkthrough.py"

_spec = importlib.util.spec_from_file_location("e2e_ai_walkthrough", AI_WALKTHROUGH)
wt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(wt)


# ---------- .gitignore 规则覆盖（真实 git 匹配） ----------

def _git_check_ignore(rel_path: str) -> bool:
    """路径是否被 .gitignore 某条规则命中（--no-index：纯规则语义，不看索引）。"""
    proc = subprocess.run(
        ["git", "check-ignore", "--no-index", "-q", rel_path],
        cwd=REPO, capture_output=True, text=True,
    )
    assert proc.returncode in (0, 1), f"git check-ignore 执行失败: {proc.stderr}"
    return proc.returncode == 0


ARTIFACT_PATHS = [
    # 第一轮新增：三旧脚本各自的调试归档目录（runs/<run_id>/result.json、issues.json）
    "dev/e2e/debug_data_recon/runs/20261022-153000_recon/result.json",
    "dev/e2e/debug_data_interactions/issues.json",
    "dev/e2e/debug_data_edge/runs/20261022-153000_edge/result.json",
    # 本轮新增：AI 走查的默认归档目录
    "dev/e2e/debug_data_ai_verify/runs/20261022-153000_ai-verify/result.json",
    # 第一轮新增：财务数据隔离目录（finance_data_edge 为存量已忽略，此处回归守护）
    "dev/e2e/finance_data_recon/invoices/INV-001.json",
    "dev/e2e/finance_data/ledger.json",
    "dev/e2e/finance_data_edge/ledger.json",
    # 第一轮新增：运行互斥锁（dev/e2e 及 staging 子目录均可能落）
    "dev/e2e/.e2e-run.lock",
    "dev/e2e/staging/deep2-20261020/.e2e-run.lock",
    # sqlite 运行期回滚日志（e2e*.db 的散件）
    "dev/e2e/e2e.db-journal",
    "dev/e2e/e2e_recon.db-journal",
]


@pytest.mark.parametrize("rel_path", ARTIFACT_PATHS)
def test_e2e_artifacts_are_ignored(rel_path):
    assert _git_check_ignore(rel_path), \
        f"{rel_path} 未被 .gitignore 覆盖（E2E 运行产物会污染 git status）"


PROTECTED_PATHS = [
    # 源码（dev/e2e 全部入库脚本）
    "dev/e2e/ai_walkthrough.py",
    "dev/e2e/debug_reporter.py",
    "dev/e2e/e2e_recon.py",
    "dev/e2e/e2e_interactions.py",
    "dev/e2e/e2e_edge.py",
    "dev/e2e/perf_probe.py",
    # 报告（含曾被 progress.md 无锚点规则误伤的 dev/e2e/PROGRESS.md）
    "dev/e2e/PROGRESS.md",
    "dev/e2e/QA-REPORT.md",
    "dev/e2e/AI-WALKTHROUGH.md",
    # 契约测试自身与第一轮隔离测试
    "backend/tests/test_swarm_e2e_artifacts.py",
    "backend/tests/test_swarm_e2e_isolation.py",
]


@pytest.mark.parametrize("rel_path", PROTECTED_PATHS)
def test_sources_and_reports_are_not_ignored(rel_path):
    assert not _git_check_ignore(rel_path), \
        f"{rel_path} 被 .gitignore 规则命中（源码/报告绝不能被忽略）"


# ---------- ai_walkthrough 归档隔离 ----------

THREE_SCRIPT_DEBUG_DIRS = {"debug_data_recon", "debug_data_interactions", "debug_data_edge"}


def test_default_debug_dir_isolated_under_dev_e2e(monkeypatch):
    """未显式指定时，默认归档目录必须是 dev/e2e 下专有目录，绝不指向 backend/data。"""
    monkeypatch.delenv("DEBUG_DATA_DIR", raising=False)
    assert wt.DEFAULT_DEBUG_DIR.parent == wt.E2E_DIR
    assert "backend/data" not in wt.DEFAULT_DEBUG_DIR.as_posix()
    assert wt.resolve_debug_dir() == wt.DEFAULT_DEBUG_DIR


def test_default_debug_dir_distinct_from_three_scripts(monkeypatch):
    """默认目录不得与三旧脚本的运行目录同名（脚本启动时会 rmtree 重置该目录）。"""
    monkeypatch.delenv("DEBUG_DATA_DIR", raising=False)
    assert wt.resolve_debug_dir().name not in THREE_SCRIPT_DEBUG_DIRS


def test_env_override_wins_over_default(monkeypatch, tmp_path):
    explicit = tmp_path / "caller-debug"
    monkeypatch.setenv("DEBUG_DATA_DIR", str(explicit))
    assert wt.resolve_debug_dir() == explicit


def test_cli_flag_wins_over_env(monkeypatch, tmp_path):
    cli_dir = tmp_path / "cli-debug"
    monkeypatch.setenv("DEBUG_DATA_DIR", str(tmp_path / "env-debug"))
    assert wt.resolve_debug_dir(str(cli_dir)) == cli_dir


def test_main_archives_into_isolated_default_dir(monkeypatch, tmp_path):
    """默认运行：归档进隔离的默认调试目录，运行前重置该目录，不碰真实调试中心。"""
    monkeypatch.delenv("DEBUG_DATA_DIR", raising=False)
    default_dir = tmp_path / "debug_data_ai_verify"
    (default_dir / "runs" / "stale-run").mkdir(parents=True)
    (default_dir / "stray.txt").write_text("stale", encoding="utf-8")
    monkeypatch.setattr(wt, "DEFAULT_DEBUG_DIR", default_dir)
    monkeypatch.setattr(sys, "argv", ["ai_walkthrough.py", "--fail", "AI-VERIFY-001:菜单结构不符"])

    assert wt.main() == 1

    run_dirs = list((default_dir / "runs").glob("*_ai-verify*"))
    assert run_dirs, "默认隔离目录中未生成 ai-verify 归档"
    result = json.loads((run_dirs[0] / "result.json").read_text(encoding="utf-8"))
    assert result["kind"] == "ai-verify"
    assert result["totals"]["failed_tests"] == 1
    assert (default_dir / "issues.json").is_file(), "失败项应自动建问题记录"
    assert not (default_dir / "stray.txt").exists(), "运行前应重置自有的默认目录"
    # 绝不写真实调试中心
    assert not (REPO / "backend" / "data" / "debug" / "runs" / run_dirs[0].name).exists()


def test_main_never_resets_caller_given_dir(monkeypatch, tmp_path):
    """调用方显式给出的隔离路径只写不重置（其中可能有调用方自己的历史归档）。"""
    caller_dir = tmp_path / "caller-debug"
    (caller_dir / "runs" / "keep-run").mkdir(parents=True)
    (caller_dir / "keep.txt").write_text("keep", encoding="utf-8")
    monkeypatch.setenv("DEBUG_DATA_DIR", str(caller_dir))
    monkeypatch.setattr(sys, "argv", ["ai_walkthrough.py"])

    assert wt.main() == 0

    assert (caller_dir / "keep.txt").is_file(), "调用方目录绝不能被重置"
    assert (caller_dir / "runs" / "keep-run").is_dir()
    run_dirs = list((caller_dir / "runs").glob("*_ai-verify*"))
    assert run_dirs, "调用方目录中未生成 ai-verify 归档"


def test_main_cli_debug_dir_wins(monkeypatch, tmp_path):
    """--debug-dir 显式指定时归档到该目录（优先于环境变量）。"""
    env_dir = tmp_path / "env-debug"
    cli_dir = tmp_path / "cli-debug"
    monkeypatch.setenv("DEBUG_DATA_DIR", str(env_dir))
    monkeypatch.setattr(sys, "argv", ["ai_walkthrough.py", "--debug-dir", str(cli_dir)])

    assert wt.main() == 0

    assert list(cli_dir.glob("runs/*_ai-verify*")), "--debug-dir 目录中未生成归档"
    assert not env_dir.exists(), "环境变量目录不应被写入（--debug-dir 优先）"
