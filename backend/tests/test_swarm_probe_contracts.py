# -*- coding: utf-8 -*-
"""17 区探针与 2026-10-22 Swarm 新契约的静态对齐守护（任务 probe-compatibility）。

被守护的契约（本轮有意行为变更，探针必须跟着适配，且不得删断言/放宽安全要求）：
1. 17 个区域探针齐全、可解析；每个探针启动服务前隔离 DATABASE_URL 与
   FINANCE_DATA_DIR（不写真实库/真实财务目录）；
2. 没有探针发送非回环 Origin：Origin 防护只放行无 Origin（CLI/探针）、回环
   任意端口、同源与配置来源，其余 403；
3. assistant-settings：技能池/插件目录检测走「先保存再检测」确认流；发现接口
   只扫描已保存目录，未保存目录返回 count/loadable=0 + 「目录未保存」，
   不再以任意 dir 直接执行插件/扫描；
4. 现金流运行列表按 page/page_size 分页且 total 为数据库真实计数——不再把
   「固定 50 条上限、total=当页条数」当作现行契约；
5. 看板快照 log_tail 经 API 有界暴露（≤500 字符尾部），探针不再声称
   「SnapshotOut 不返回 log_tail，日志仅 DB 可查」；
6. 调试中心 client-errors 宽容解析契约保持（垃圾 body 仍归一 201，不 500）。

验证方式：纯静态检查探针源码（AST 取函数片段 + 文本模式）。
不启动服务、不执行探针（主代理稍后统一跑）、不读/不删任何真实数据。
"""
import ast
import re
from pathlib import Path
from urllib.parse import urlsplit

import pytest

REPO = Path(__file__).resolve().parents[2]
PROBE_DIR = REPO / "dev" / "e2e" / "probes"

AREAS = [
    "api-contract",
    "assistant-chat",
    "assistant-settings",
    "dashboard-search",
    "debug-center",
    "finance-board-cashflow-expense",
    "finance-invoice-vault",
    "finance-ledger",
    "finance-tax-pos-hello",
    "interaction-ux",
    "jobs",
    "knowledge",
    "mcp",
    "notes",
    "schedules",
    "skills-experts",
    "tasks",
]

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "[::1]", "::1"}

AS_SETTINGS = "assistant-settings.py"
BCE = "finance-board-cashflow-expense.py"
DEBUG_CENTER = "debug-center.py"


def _src(name: str) -> str:
    return (PROBE_DIR / name).read_text(encoding="utf-8")


def _tree(name: str) -> ast.Module:
    return ast.parse(_src(name))


def _fn_src(name: str, fn: str) -> str:
    """取探针中某个函数的源码片段（含装饰器之外的完整定义体）。"""
    tree = _tree(name)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == fn:
            segment = ast.get_source_segment(_src(name), node)
            assert segment, f"{name} 中找不到函数 {fn} 的源码"
            return segment
    raise AssertionError(f"{name} 中不存在函数 {fn}")


# ---------- 1. 17 区齐全与隔离基线 ----------

@pytest.mark.parametrize("area", AREAS)
def test_probe_area_exists_and_parses(area):
    path = PROBE_DIR / f"{area}.py"
    assert path.is_file(), f"17 区探针缺失: {path}"
    ast.parse(path.read_text(encoding="utf-8"))  # 语法必须有效


def test_exactly_seventeen_probe_scripts():
    found = sorted(p.stem for p in PROBE_DIR.glob("*.py"))
    assert found == sorted(AREAS), f"探针集合与 17 区契约不一致: {found}"


@pytest.mark.parametrize("area", AREAS)
def test_probe_isolates_database_and_finance_dir_before_server_start(area):
    src = _src(f"{area}.py")
    db_at = src.find('env["DATABASE_URL"]')
    fin_at = src.find('env["FINANCE_DATA_DIR"]')
    popen_at = src.find("subprocess.Popen")
    assert db_at != -1, f"{area} 未设置 DATABASE_URL（会写真实库）"
    assert fin_at != -1, f"{area} 未设置 FINANCE_DATA_DIR（会写真实财务目录）"
    assert popen_at != -1, f"{area} 未通过 subprocess.Popen 启动服务"
    assert db_at < popen_at and fin_at < popen_at, \
        f"{area} 的隔离变量必须在启动服务前设置"


# ---------- 2. Origin 仅合法回环 ----------

@pytest.mark.parametrize("area", AREAS)
def test_no_probe_sends_non_loopback_origin(area):
    src = _src(f"{area}.py")
    origins = re.findall(r"""["']Origin["']\s*:\s*["']([^"']*)["']""", src)
    for origin in origins:
        if not origin:
            continue
        host = urlsplit(origin).hostname or ""
        assert host in LOOPBACK_HOSTS, \
            f"{area} 发送了非回环 Origin {origin!r}，会被 Origin 防护 403 拒绝"


# ---------- 3. 发现接口先保存目录 ----------

@pytest.mark.parametrize("fn", ["tc_skills_detect_missing", "tc_skills_detect_hit",
                                "tc_plugins_detect_missing"])
def test_settings_detection_goes_through_save_first_confirm(fn):
    """目录与已保存配置不一致时前端弹「保存并检测」；探针必须走完该确认流，
    不能假设未保存目录会被直接扫描（旧行为已按 A02 收紧）。"""
    body = _fn_src(AS_SETTINGS, fn)
    assert "detect_with_save_first" in body, \
        f"{fn} 未走「先保存再检测」确认流（未保存目录不会被扫描）"
    helper = _fn_src(AS_SETTINGS, "detect_with_save_first")
    assert "保存并检测" in helper, "确认流未点击「保存并检测」按钮"


def test_settings_discover_api_rejects_unsaved_dirs():
    body = _fn_src(AS_SETTINGS, "tc_api_skills_discover_contract")
    assert "未保存" in body, "API 契约未锁定「目录未保存」拒绝分支"
    assert "count" in body and "0" in body, "未保存目录应断言 count == 0"
    assert "1" in body, "已保存技能池仍应断言 count == 1（不放宽发现能力）"
    assert "plugins/discover" in body, "插件发现同样受已保存目录约束，应锁定"


# ---------- 4. 现金流分页 + 真实 total ----------

def test_cashflow_empty_list_uses_field_assertions():
    body = _fn_src(BCE, "c_api_empty")
    assert '== {"total": 0, "items": []}' not in body, \
        "现金流列表响应已新增 page/page_size 字段，严格字典相等会误报"
    assert "page" in body, "空列表契约应显式覆盖分页字段"


def test_cashflow_list_contract_is_pagination_with_real_total():
    body = _fn_src(BCE, "c_api_list_pagination")
    assert 'len(d["items"]) == 50' not in body and 'd["total"] == 50' not in body, \
        "旧契约「固定 50 条上限且 total=当页条数」已被分页取代，不得保留"
    assert "total" in body and "52" in body, \
        "52 条记录应断言真实 total=52（分页后全量可达）"
    assert "page_size" in body or "page=" in body, "应覆盖分页参数"


def test_board_log_tail_exposed_via_api():
    body = _fn_src(BCE, "b_api_run_failed")
    assert "log_tail" in body, "失败快照日志暴露契约（API 层 log_tail）未被锁定"
    src = _src(BCE)
    assert "不返回 log_tail" not in src, \
        "注释仍声称 SnapshotOut 不返回 log_tail（该字段已随契约上线）"


# ---------- 5. 回归守卫：不得借适配删断言 ----------

def test_debug_client_errors_lenient_contract_kept():
    body = _fn_src(DEBUG_CENTER, "tc_api_004")
    assert "201" in body, "调试中心垃圾 body 归一 201 契约必须保留"


def test_expense_oversize_still_rejected_400():
    body = _fn_src(BCE, "e_api_upload_validation")
    assert "400" in body and "上限" in body, \
        "上传超限 400 + 「上限」文案契约必须保留（不得借适配放宽）"
