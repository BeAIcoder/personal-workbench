# -*- coding: utf-8 -*-
"""17 区探针的三目录隔离静态守护（Swarm 第三轮 DEBUG_DATA_DIR 补齐）。

被守护的契约：每个区域探针在启动 uvicorn 前都必须把 DATABASE_URL、
FINANCE_DATA_DIR、DEBUG_DATA_DIR 指向 probe_results 下该区域自有目录。
缺 DEBUG_DATA_DIR 时，前端异常/拒绝会被后端写进真实 backend/data/debug。
"""
import ast
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
PROBE_DIR = REPO / "dev" / "e2e" / "probes"

AREAS = sorted(p.stem for p in PROBE_DIR.glob("*.py"))


@pytest.mark.parametrize("area", AREAS)
def test_probe_isolates_debug_dir_before_server_start(area):
    src = (PROBE_DIR / f"{area}.py").read_text(encoding="utf-8")
    ast.parse(src)
    assert "DEBUG_DIR" in src, f"{area} 未定义 DEBUG_DIR"
    debug_at = src.find('env["DEBUG_DATA_DIR"]')
    popen_at = src.find("subprocess.Popen")
    assert debug_at != -1, f"{area} 未设置 DEBUG_DATA_DIR（会写真实 backend/data/debug）"
    assert popen_at != -1, f"{area} 未通过 subprocess.Popen 启动服务"
    assert debug_at < popen_at, f"{area} 的 DEBUG_DATA_DIR 必须在启动服务前设置"
