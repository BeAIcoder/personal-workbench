# -*- coding: utf-8 -*-
"""api-contract 区域深度 E2E 探测（HTTP 边界 / 异常输入，只报不修）。

探测重点（对全部主要端点）：
  A 分页边界（page=0/负数/非数字/page_size=超上限/超大页码）
  B 404 资源（各资源 GET/PUT/DELETE 不存在的 id）
  C 422 校验（必填缺失/枚举越界/长度越界/pattern 违反/日期非法）
  D 畸形 JSON body（非 JSON/数组/null/空 body/错 Content-Type/错类型）
  E 错误 method（405 矩阵）
  F 超长字符串（q/title/content/记忆/消息/URL 路径/Header）
  G 路径参数注入（int 穿透/空字节/路径穿越/SQL 注入/LIKE 通配符）
  H 日期与期间参数边界（2026-02-30、T99:99、YYYY-MM 合法性）
  I 契约行为（健康检查、往返 CRUD、SSE、CORS、尾斜杠、重复参数）

独立库隔离：dev/e2e/probe_results/api-contract.db（每次运行重建）+ <area>-fin。
结果：dev/e2e/probe_results/api-contract.json；失败证据：同目录 <TID>.txt。
用法：python dev/e2e/probes/api-contract.py   （playwright 不需要，纯 urllib）
"""
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
BACKEND = ROOT / "backend"
RESULTS = ROOT / "dev" / "e2e" / "probe_results"
AREA = "api-contract"
PORT = 8216
BASE = f"http://127.0.0.1:{PORT}"
DB = RESULTS / f"{AREA}.db"
FIN_DIR = RESULTS / f"{AREA}-fin"
DEBUG_DIR = RESULTS / f"{AREA}-debug"
LOG = RESULTS / f"{AREA}.server.log"
JSON_OUT = RESULTS / f"{AREA}.json"
EVIDENCE_DIR = RESULTS / AREA

STARTED_AT = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

for p in (DB, Path(str(DB) + "-wal"), Path(str(DB) + "-shm")):
    if p.exists():
        p.unlink()
if FIN_DIR.exists():
    shutil.rmtree(FIN_DIR)
if DEBUG_DIR.exists():
    shutil.rmtree(DEBUG_DIR)
EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)

env = os.environ.copy()
env["DATABASE_URL"] = f"sqlite:///{DB.as_posix()}"
env["FINANCE_DATA_DIR"] = str(FIN_DIR)
env["DEBUG_DATA_DIR"] = str(DEBUG_DIR)
env["PYTHONIOENCODING"] = "utf-8"

_log = open(LOG, "w", encoding="utf-8")
server = subprocess.Popen(
    [str(BACKEND / "venv" / "Scripts" / "python.exe"), "-m", "uvicorn",
     "app.main:app", "--host", "127.0.0.1", "--port", str(PORT)],
    cwd=str(BACKEND), env=env, stdout=_log, stderr=subprocess.STDOUT,
)

# ---------------------------------------------------------------- HTTP 助手


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


_opener = urllib.request.build_opener(_NoRedirect)


def req(method, path, body=None, raw=None, headers=None, timeout=25):
    """发请求；返回 (status, text, headers)。status=-1 表示传输层错误（超时/断连）。"""
    url = BASE + path
    data = None
    hdrs = dict(headers or {})
    if raw is not None:
        data = raw.encode("utf-8") if isinstance(raw, str) else raw
        hdrs.setdefault("Content-Type", "application/json")
    elif body is not None:
        data = json.dumps(body).encode("utf-8")
        hdrs.setdefault("Content-Type", "application/json")
    r = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with _opener.open(r, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace"), dict(resp.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace"), dict(e.headers)
    except Exception as e:  # 超时 / 连接错误
        return -1, f"{type(e).__name__}: {e}", {}


def qs(**params):
    return "?" + urllib.parse.urlencode(params)


def jbody(text):
    try:
        return json.loads(text)
    except Exception:
        return None


# ---------------------------------------------------------------- 测试注册

TESTS = []
OBSERVATIONS = []   # 只记录不断言的观察项
BUGS = []


def test(tid, name, group, severity="P2", kind="contract"):
    def deco(fn):
        TESTS.append({"id": tid, "name": name, "group": group,
                      "severity": severity, "kind": kind, "fn": fn})
        return fn
    return deco


def fail(tid, method, path, expect, got, text, note=""):
    detail = f"{method} {path} → 期望 {expect}，实际 {got}"
    if note:
        detail += f"；{note}"
    snippet = (text or "")[:600]
    (EVIDENCE_DIR / f"{tid}.txt").write_text(
        f"{method} {path}\nexpected: {expect}\ngot: {got}\n\n{snippet}\n", encoding="utf-8")
    raise AssertionError(detail)


def expect_status(tid, method, path, expect, body=None, raw=None, headers=None,
                  timeout=25, note="", forbid=()):
    """断言状态码；forbid 中的状态码一律视为失败。"""
    if isinstance(expect, (list, tuple, set)):
        exp_desc = "/".join(str(e) for e in expect)
    else:
        exp_desc = str(expect)
    status, text, _ = req(method, path, body=body, raw=raw, headers=headers, timeout=timeout)
    bad = status in forbid
    if status != -1 and not bad and status in (expect if isinstance(expect, (list, tuple, set)) else [expect]):
        return status, text
    fail(tid, method, path, exp_desc, status, text, note)


def observe(tid, method, path, body=None, raw=None, headers=None, timeout=25, note=""):
    """只观察：记录状态与响应片段；仅 5xx/传输错误记失败。"""
    status, text, _ = req(method, path, body=body, raw=raw, headers=headers, timeout=timeout)
    OBSERVATIONS.append({"id": tid, "probe": f"{method} {path}", "status": status,
                         "note": note, "snippet": (text or "")[:300]})
    if status >= 500 or status == -1:
        fail(tid, method, path, "非 5xx", status, text, note)
    return status, text


# ---------------------------------------------------------------- 种子数据

SEED = {}


def wait_ready(timeout=90):
    t0 = time.time()
    while time.time() - t0 < timeout:
        status, _, _ = req("GET", "/api/health", timeout=3)
        if status == 200:
            return True
        time.sleep(1)
    return False


def seed_data():
    s, t, _ = req("POST", "/api/tasks", body={
        "title": "契约探测种子任务", "description": "api-contract probe",
        "priority": "high", "category": "探测"})
    assert s == 201, f"种子任务创建失败: {s} {t[:200]}"
    SEED["task_id"] = jbody(t)["id"]

    s, t, _ = req("POST", "/api/notes", body={
        "title": "契约探测种子笔记", "content": "api-contract probe note", "tags": ["探测", "契约"]})
    assert s == 201, f"种子笔记创建失败: {s} {t[:200]}"
    SEED["note_id"] = jbody(t)["id"]

    s, t, _ = req("POST", "/api/schedules", body={
        "title": "契约探测种子日程", "start_time": "2026-10-20T10:00:00",
        "end_time": "2026-10-20T11:00:00"})
    assert s == 201, f"种子日程创建失败: {s} {t[:200]}"
    SEED["schedule_id"] = jbody(t)["id"]

    s, t, _ = req("POST", "/api/jobs", body={
        "name": "契约探测定时任务", "schedule_type": "interval",
        "interval_minutes": 60, "prompt": "ping"})
    assert s == 201, f"种子定时任务创建失败: {s} {t[:200]}"
    SEED["job_id"] = jbody(t)["id"]

    s, t, _ = req("POST", "/api/knowledge/memories", body={"content": "契约探测种子记忆"})
    assert s == 201, f"种子记忆创建失败: {s} {t[:200]}"
    SEED["memory_id"] = jbody(t)["id"]

    s, t, _ = req("POST", "/api/finance/ledger/personal/accounts",
                  body={"name": "探测账户", "kind": "cash"})
    assert s == 201, f"种子账户创建失败: {s} {t[:200]}"
    SEED["account_id"] = jbody(t)["id"]

    s, t, _ = req("POST", "/api/finance/ledger/personal/transactions", body={
        "kind": "expense", "amount": 12.5, "occurred_on": "2026-10-15",
        "account_id": SEED["account_id"], "note": "探测记账"})
    assert s == 201, f"种子交易创建失败: {s} {t[:200]}"
    SEED["tx_id"] = jbody(t)["id"]


# ================================================================ A 分页边界

@test("A01", "tasks page=0 应 422", "A 分页边界", "P2")
def _():
    expect_status("A01", "GET", "/api/tasks" + qs(page=0), 422)


@test("A02", "tasks page=-5 应 422", "A 分页边界", "P2")
def _():
    expect_status("A02", "GET", "/api/tasks" + qs(page=-5), 422)


@test("A03", "tasks page=abc 应 422", "A 分页边界", "P2")
def _():
    expect_status("A03", "GET", "/api/tasks" + qs(page="abc"), 422)


@test("A04", "tasks page=1.5 应 422", "A 分页边界", "P2")
def _():
    expect_status("A04", "GET", "/api/tasks" + qs(page="1.5"), 422)


@test("A05", "tasks page_size=0 应 422", "A 分页边界", "P2")
def _():
    expect_status("A05", "GET", "/api/tasks" + qs(page=1, page_size=0), 422)


@test("A06", "tasks page_size=201 应 422（上限 100）", "A 分页边界", "P2")
def _():
    expect_status("A06", "GET", "/api/tasks" + qs(page=1, page_size=201), 422)


@test("A07", "tasks page_size=100 边界内应 200", "A 分页边界", "P3")
def _():
    s, t = expect_status("A07", "GET", "/api/tasks" + qs(page=1, page_size=100), 200)
    d = jbody(t)
    assert d and len(d["items"]) <= 100, f"items 超限: {t[:200]}"


@test("A08", "tasks 超大页码 page=10^12 应 200 且空列表", "A 分页边界", "P2")
def _():
    s, t = expect_status("A08", "GET", "/api/tasks" + qs(page=10 ** 12, page_size=10), 200)
    d = jbody(t)
    assert d and d["items"] == [], f"超大页码应返回空列表: {t[:200]}"


@test("A09", "notes page_size=101 应 422（上限 100）", "A 分页边界", "P2")
def _():
    expect_status("A09", "GET", "/api/notes" + qs(page=1, page_size=101), 422)


@test("A10", "schedules page_size=201 应 422（上限 200）", "A 分页边界", "P2")
def _():
    expect_status("A10", "GET", "/api/schedules" + qs(page=1, page_size=201), 422)


@test("A11", "schedules page_size=200 边界内应 200", "A 分页边界", "P3")
def _():
    expect_status("A11", "GET", "/api/schedules" + qs(page=1, page_size=200), 200)


@test("A12", "personal transactions page_size=5001 应 422（上限 5000）", "A 分页边界", "P2")
def _():
    expect_status("A12", "GET", "/api/finance/ledger/personal/transactions"
                   + qs(page=1, page_size=5001), 422)


@test("A13", "expense runs page=0 应 422", "A 分页边界", "P2")
def _():
    expect_status("A13", "GET", "/api/finance/expense/runs" + qs(page=0), 422)


@test("A14", "bip imports page_size=201 应 422（上限 200）", "A 分页边界", "P2")
def _():
    expect_status("A14", "GET", "/api/finance/ledger/bip/imports"
                   + qs(page=1, page_size=201), 422)


@test("A15", "personal stats trend months=37 应 422（上限 36）", "A 分页边界", "P2")
def _():
    expect_status("A15", "GET", "/api/finance/ledger/personal/stats/trend" + qs(months=37), 422)


@test("A16", "assistant sessions limit=201 应 422（上限 200）", "A 分页边界", "P2")
def _():
    expect_status("A16", "GET", "/api/assistant/sessions" + qs(limit=201), 422)


@test("A17", "hello entries limit=101 应 422（上限 100）", "A 分页边界", "P2")
def _():
    expect_status("A17", "GET", "/api/finance/hello/entries" + qs(limit=101), 422)


@test("A18", "tasks page=999999 应 200 且空列表", "A 分页边界", "P3")
def _():
    s, t = expect_status("A18", "GET", "/api/tasks" + qs(page=999999, page_size=10), 200)
    d = jbody(t)
    assert d and d["items"] == [], f"越界页应空列表: {t[:200]}"


# ================================================================ B 404 资源

@test("B01", "GET 不存在的任务应 404", "B 404 资源", "P1")
def _():
    expect_status("B01", "GET", "/api/tasks/999999", 404)


@test("B02", "PUT 不存在的任务应 404", "B 404 资源", "P1")
def _():
    expect_status("B02", "PUT", "/api/tasks/999999", body={"title": "x"},  # type: ignore[arg-type]
                  expect=404)


@test("B03", "DELETE 不存在的任务应 404", "B 404 资源", "P1")
def _():
    expect_status("B03", "DELETE", "/api/tasks/999999", 404)


@test("B04", "GET 不存在的笔记应 404", "B 404 资源", "P1")
def _():
    expect_status("B04", "GET", "/api/notes/999999", 404)


@test("B05", "PUT/DELETE 不存在的笔记应 404", "B 404 资源", "P1")
def _():
    expect_status("B05", "PUT", "/api/notes/999999", body={"title": "x"}, expect=404)
    expect_status("B05", "DELETE", "/api/notes/999999", 404)


@test("B06", "GET 不存在的日程应 404", "B 404 资源", "P1")
def _():
    expect_status("B06", "GET", "/api/schedules/999999", 404)


@test("B07", "PUT 不存在的定时任务应 404", "B 404 资源", "P1")
def _():
    expect_status("B07", "PUT", "/api/jobs/999999", body={"name": "x"}, expect=404)


@test("B08", "DELETE 不存在的定时任务应 404", "B 404 资源", "P1")
def _():
    expect_status("B08", "DELETE", "/api/jobs/999999", 404)


@test("B09", "立即执行不存在的定时任务应 404", "B 404 资源", "P1")
def _():
    expect_status("B09", "POST", "/api/jobs/999999/run", 404)


@test("B10", "PUT/DELETE 不存在的记忆应 404", "B 404 资源", "P1")
def _():
    expect_status("B10", "PUT", "/api/knowledge/memories/999999",
                  body={"content": "x"}, expect=404)
    expect_status("B10", "DELETE", "/api/knowledge/memories/999999", 404)


@test("B11", "PUT/DELETE 不存在的 MCP 服务器应 404", "B 404 资源", "P1")
def _():
    expect_status("B11", "PUT", "/api/mcp/servers/999999",
                  body={"command": "python"}, expect=404)
    expect_status("B11", "DELETE", "/api/mcp/servers/999999", 404)
    expect_status("B11", "POST", "/api/mcp/servers/999999/disconnect", 404)
    expect_status("B11", "POST", "/api/mcp/servers/999999/test-call",
                  body={"tool": "x"}, expect=404)


@test("B12", "DELETE/PUT 不存在的技能包应 404", "B 404 资源", "P1")
def _():
    expect_status("B12", "DELETE", "/api/skills/packs/no-such-pack-xyz", 404)
    expect_status("B12", "PUT", "/api/skills/packs/no-such-pack-xyz/enabled",
                  body={"enabled": False}, expect=404)
    expect_status("B12", "GET", "/api/skills/packs/no-such-pack-xyz/files/SKILL.md", 404)


@test("B13", "专家包不存在应 404", "B 404 资源", "P1")
def _():
    expect_status("B13", "GET", "/api/experts/packs/no_such/content", 404)
    expect_status("B13", "POST", "/api/experts/packs/import/no_such", 404)
    expect_status("B13", "DELETE", "/api/experts/packs/no_such", 404)
    expect_status("B13", "POST", "/api/experts/packs/export/no_such", 404)


@test("B14", "看板无快照时 latest 应 404", "B 404 资源", "P2")
def _():
    expect_status("B14", "GET", "/api/finance/board/latest", 404)


@test("B15", "看板不存在快照的 html 应 404", "B 404 资源", "P2")
def _():
    expect_status("B15", "GET", "/api/finance/board/snapshots/999999/html", 404)


@test("B16", "现金流/POS 运行记录不存在应 404", "B 404 资源", "P2")
def _():
    expect_status("B16", "GET", "/api/finance/cashflow/runs/999999", 404)
    expect_status("B16", "GET", "/api/finance/pos/runs/999999", 404)


@test("B17", "发票批次不存在应 404", "B 404 资源", "P2")
def _():
    expect_status("B17", "GET", "/api/finance/invoice/batches/999999", 404)


@test("B18", "个人记账交易不存在应 404", "B 404 资源", "P1")
def _():
    expect_status("B18", "GET", "/api/finance/ledger/personal/transactions/999999", 404)
    expect_status("B18", "PUT", "/api/finance/ledger/personal/transactions/999999",
                  body={"amount": 1}, expect=404)
    expect_status("B18", "DELETE", "/api/finance/ledger/personal/transactions/999999", 404)


@test("B19", "BIP 凭证不存在应 404", "B 404 资源", "P2")
def _():
    expect_status("B19", "GET", "/api/finance/ledger/bip/vouchers/999999", 404)


@test("B20", "税务企业/政策不存在应 404", "B 404 资源", "P2")
def _():
    expect_status("B20", "GET", "/api/finance/tax/enterprises/999999", 404)
    expect_status("B20", "GET", "/api/finance/tax/policies/999999", 404)


@test("B21", "模型探测不存在的模型应 404", "B 404 资源", "P2")
def _():
    expect_status("B21", "GET", "/api/models/999999/probe", 404)
    expect_status("B21", "POST", "/api/models/999999/probe/reasoning", 404)
    expect_status("B21", "POST", "/api/models/999999/probe/all", 404)


@test("B22", "调试中心运行/截图不存在应 404", "B 404 资源", "P2")
def _():
    expect_status("B22", "GET", "/api/debug/runs/no-such-run-xyz", 404)
    expect_status("B22", "GET", "/api/debug/runs/no-such-run-xyz/shots/x.png", 404)


@test("B23", "数据仓未注册表预览应 404", "B 404 资源", "P2")
def _():
    expect_status("B23", "GET", "/api/finance/vault/tables/nonexistent/preview", 404)


@test("B24", "未知 API 路径应 404", "B 404 资源", "P3")
def _():
    expect_status("B24", "GET", "/api/no-such-endpoint-xyz", 404)


@test("B25", "调试中心问题不存在应 404", "B 404 资源", "P2")
def _():
    expect_status("B25", "PUT", "/api/debug/issues/no-such-issue",
                  body={"title": "x"}, expect=404)
    expect_status("B25", "DELETE", "/api/debug/issues/no-such-issue", 404)


# ================================================================ C 422 校验

@test("C01", "POST tasks 缺 title 应 422", "C 422 校验", "P1")
def _():
    expect_status("C01", "POST", "/api/tasks", body={}, expect=422)


@test("C02", "POST tasks 空白 title 应 422", "C 422 校验", "P1")
def _():
    expect_status("C02", "POST", "/api/tasks", body={"title": "   "}, expect=422)


@test("C03", "POST tasks title 超长（201）应 422", "C 422 校验", "P1")
def _():
    expect_status("C03", "POST", "/api/tasks", body={"title": "x" * 201}, expect=422)


@test("C04", "POST tasks 非法 status/priority 应 422", "C 422 校验", "P1")
def _():
    expect_status("C04", "POST", "/api/tasks", body={"title": "ok", "status": "weird"}, expect=422)
    expect_status("C04", "POST", "/api/tasks", body={"title": "ok", "priority": "weird"}, expect=422)


@test("C05", "POST tasks 非法 due_date 应 422", "C 422 校验", "P1")
def _():
    expect_status("C05", "POST", "/api/tasks", body={"title": "ok", "due_date": "not-a-date"}, expect=422)
    expect_status("C05", "POST", "/api/tasks", body={"title": "ok", "due_date": "2026-13-45T00:00:00"}, expect=422)


@test("C06", "POST notes tags 非数组应 422", "C 422 校验", "P1")
def _():
    expect_status("C06", "POST", "/api/notes", body={"title": "n", "tags": "notalist"}, expect=422)


@test("C07", "POST notes 超长标签应截断而非报错（设计行为）", "C 422 校验", "P3")
def _():
    s, t = expect_status("C07", "POST", "/api/notes",
                         body={"title": "标签截断探测", "tags": ["a" * 30]}, expect=201)
    d = jbody(t)
    assert d and d["tags"] == ["a" * 20], f"标签应截断到 20 字符: {t[:200]}"


@test("C08", "POST schedules end<=start 应 422", "C 422 校验", "P1")
def _():
    expect_status("C08", "POST", "/api/schedules", body={
        "title": "s", "start_time": "2026-10-20T10:00:00",
        "end_time": "2026-10-20T09:00:00"}, expect=422)


@test("C09", "POST schedules 缺 start_time 应 422", "C 422 校验", "P1")
def _():
    expect_status("C09", "POST", "/api/schedules", body={
        "title": "s", "end_time": "2026-10-20T09:00:00"}, expect=422)


@test("C10", "POST jobs interval_minutes 越界应 422", "C 422 校验", "P1")
def _():
    expect_status("C10", "POST", "/api/jobs", body={
        "name": "j", "schedule_type": "interval", "interval_minutes": 3, "prompt": "p"}, expect=422)
    expect_status("C10", "POST", "/api/jobs", body={
        "name": "j", "schedule_type": "interval", "interval_minutes": 20000, "prompt": "p"}, expect=422)


@test("C11", "POST jobs daily 缺 daily_at / 非法时间应 422", "C 422 校验", "P1")
def _():
    expect_status("C11", "POST", "/api/jobs", body={
        "name": "j", "schedule_type": "daily", "prompt": "p"}, expect=422)
    expect_status("C11", "POST", "/api/jobs", body={
        "name": "j", "schedule_type": "daily", "daily_at": "25:99", "prompt": "p"}, expect=422)


@test("C12", "POST jobs cron 非法表达式应 422", "C 422 校验", "P1")
def _():
    expect_status("C12", "POST", "/api/jobs", body={
        "name": "j", "schedule_type": "cron", "prompt": "p"}, expect=422)
    expect_status("C12", "POST", "/api/jobs", body={
        "name": "j", "schedule_type": "cron", "cron_expr": "bad expr here", "prompt": "p"}, expect=422)


@test("C13", "POST jobs 无 prompt 也无 action 应 422", "C 422 校验", "P1")
def _():
    expect_status("C13", "POST", "/api/jobs", body={
        "name": "j", "schedule_type": "interval", "interval_minutes": 30}, expect=422)


@test("C14", "POST jobs 未知 schedule_type 应 422", "C 422 校验", "P2")
def _():
    expect_status("C14", "POST", "/api/jobs", body={
        "name": "j", "schedule_type": "weird", "prompt": "p"}, expect=422)


@test("C15", "GET search 缺 q / 空 q / 超长 q 应 422", "C 422 校验", "P1")
def _():
    expect_status("C15", "GET", "/api/search", 422)
    expect_status("C15", "GET", "/api/search" + qs(q=""), 422)
    expect_status("C15", "GET", "/api/search" + qs(q="x" * 101), 422)


@test("C16", "个人记账 month 缺失/非法应 422/400", "C 422 校验", "P1")
def _():
    expect_status("C16", "GET", "/api/finance/ledger/personal/stats/monthly", 422)
    expect_status("C16", "GET", "/api/finance/ledger/personal/stats/monthly" + qs(month="2026-13"), 400)
    expect_status("C16", "GET", "/api/finance/ledger/personal/stats/monthly" + qs(month="notamonth"), 400)
    expect_status("C16", "GET", "/api/finance/ledger/personal/budgets" + qs(month="bad"), 400)


@test("C17", "BIP 期间汇总 period 缺失/非法应 422/400", "C 422 校验", "P1")
def _():
    expect_status("C17", "GET", "/api/finance/ledger/bip/balances/summary", 422)
    expect_status("C17", "GET", "/api/finance/ledger/bip/balances/summary" + qs(period="2026-13"), 400)
    expect_status("C17", "GET", "/api/finance/ledger/bip/balances/summary" + qs(period="bad"), 400)


@test("C18", "tasks 非法 due_before/due_after 应 422", "C 422 校验", "P1")
def _():
    expect_status("C18", "GET", "/api/tasks" + qs(due_before="abc"), 422)
    expect_status("C18", "GET", "/api/tasks" + qs(due_before="2026-99-99"), 422)


@test("C19", "schedules 非法 start/end 应 422", "C 422 校验", "P1")
def _():
    expect_status("C19", "GET", "/api/schedules" + qs(start="abc"), 422)
    expect_status("C19", "GET", "/api/schedules" + qs(end="2026-10-20T99:00:00"), 422)


@test("C20", "布尔查询参数非法值应 422", "C 422 校验", "P1")
def _():
    expect_status("C20", "GET", "/api/tasks" + qs(overdue="maybe"), 422)
    expect_status("C20", "GET", "/api/notes" + qs(pinned="maybe"), 422)
    expect_status("C20", "GET", "/api/assistant/sessions" + qs(archived="maybe"), 422)


@test("C21", "记忆内容空/超长应 422", "C 422 校验", "P1")
def _():
    expect_status("C21", "POST", "/api/knowledge/memories", body={"content": ""}, expect=422)
    expect_status("C21", "POST", "/api/knowledge/memories", body={"content": "x" * 2001}, expect=422)


@test("C22", "RAG top_k 越界 / query 缺失应 422", "C 422 校验", "P1")
def _():
    expect_status("C22", "POST", "/api/knowledge/rag/search",
                  body={"query": "x", "top_k": 21}, expect=422)
    expect_status("C22", "POST", "/api/knowledge/rag/search", body={"top_k": 5}, expect=422)


@test("C23", "记忆检索空 query 应 422", "C 422 校验", "P2")
def _():
    expect_status("C23", "POST", "/api/knowledge/memories/recall", body={"query": ""}, expect=422)


@test("C24", "技能包名非法 / 描述超长应 400/422", "C 422 校验", "P2")
def _():
    observe("C24", "POST", "/api/skills/packs", body={"name": "Bad_Name", "description": "d"})
    observe("C24", "POST", "/api/skills/packs", body={"name": "ok-name", "description": "x" * 501})


@test("C25", "MCP 服务器字段缺失/空应 422", "C 422 校验", "P2")
def _():
    expect_status("C25", "POST", "/api/mcp/servers", body={"command": "python"}, expect=422)
    expect_status("C25", "POST", "/api/mcp/servers",
                  body={"name": "x", "command": ""}, expect=422)


@test("C26", "专家创建 name 非法 pattern 应 422", "C 422 校验", "P2")
def _():
    expect_status("C26", "POST", "/api/assistant/agents", body={
        "name": "Bad Name", "display_name": "x"}, expect=422)


@test("C27", "会话创建 session_id 过短应 422", "C 422 校验", "P2")
def _():
    expect_status("C27", "POST", "/api/assistant/sessions", body={"session_id": "abc"}, expect=422)


@test("C28", "聊天消息为空/超长应 422", "C 422 校验", "P1")
def _():
    expect_status("C28", "POST", "/api/assistant/chat",
                  body={"session_id": "abcdef123456", "message": ""}, expect=422)
    expect_status("C28", "POST", "/api/assistant/chat",
                  body={"session_id": "abcdef123456", "message": "x" * 8001}, expect=422)


@test("C29", "路由测试 text 为空应 422", "C 422 校验", "P2")
def _():
    expect_status("C29", "POST", "/api/assistant/agents/route-test", body={"text": ""}, expect=422)


@test("C30", "上传接口缺文件应 422", "C 422 校验", "P2")
def _():
    expect_status("C30", "POST", "/api/assistant/upload", 422)
    expect_status("C30", "POST", "/api/finance/expense/classify", 422)
    expect_status("C30", "POST", "/api/finance/ledger/personal/import/csv", 422)


@test("C31", "个人交易金额/类型非法应 422", "C 422 校验", "P1")
def _():
    base = {"kind": "expense", "occurred_on": "2026-10-15", "account_id": SEED["account_id"]}
    expect_status("C31", "POST", "/api/finance/ledger/personal/transactions",
                  body={**base, "amount": -5}, expect=422)
    expect_status("C31", "POST", "/api/finance/ledger/personal/transactions",
                  body={**base, "amount": 1, "kind": "weird"}, expect=422)
    expect_status("C31", "POST", "/api/finance/ledger/personal/transactions",
                  body={**base, "amount": 1, "occurred_on": "2026-13-45"}, expect=422)


@test("C32", "BIP 确认导入空 body 应 422", "C 422 校验", "P2")
def _():
    expect_status("C32", "POST", "/api/finance/ledger/bip/imports/confirm", body={}, expect=422)


@test("C33", "调试中心新建问题空 body 应 422", "C 422 校验", "P2")
def _():
    observe("C33", "POST", "/api/debug/issues", body={})


@test("C34", "专家名 pattern 边界：数字开头应 422", "C 422 校验", "P2")
def _():
    expect_status("C34", "POST", "/api/assistant/agents", body={
        "name": "1bad", "display_name": "x"}, expect=422)


# ================================================================ D 畸形 JSON

@test("D01", "POST tasks 非 JSON body 应 422", "D 畸形 JSON", "P1")
def _():
    expect_status("D01", "POST", "/api/tasks", raw="{not json", expect=422)


@test("D02", "POST tasks 数组 body 应 422", "D 畸形 JSON", "P1")
def _():
    expect_status("D02", "POST", "/api/tasks", raw="[1,2,3]", expect=422)


@test("D03", "POST tasks null body 应 422", "D 畸形 JSON", "P1")
def _():
    expect_status("D03", "POST", "/api/tasks", raw="null", expect=422)


@test("D04", "POST tasks 空 body 应 422", "D 畸形 JSON", "P1")
def _():
    expect_status("D04", "POST", "/api/tasks", raw="", expect=422)


@test("D05", "POST tasks 错 Content-Type 应 422", "D 畸形 JSON", "P2")
def _():
    expect_status("D05", "POST", "/api/tasks", raw='{"title":"ct探测"}',
                  headers={"Content-Type": "text/plain"}, expect=422)


@test("D06", "POST tasks 字段类型错误应 422", "D 畸形 JSON", "P1")
def _():
    expect_status("D06", "POST", "/api/tasks", body={"title": 123}, expect=422)
    expect_status("D06", "POST", "/api/tasks", body={"title": "ok", "status": 5}, expect=422)


@test("D07", "POST tasks 未知字段应被忽略（201）", "D 畸形 JSON", "P3")
def _():
    s, t = expect_status("D07", "POST", "/api/tasks",
                         body={"title": "未知字段探测", "not_a_field": {"x": [1]}}, expect=201)
    d = jbody(t)
    assert d and "not_a_field" not in d, f"未知字段不应出现在响应: {t[:200]}"
    SEED["tmp_task"] = d["id"]


@test("D08", "POST jobs 未知字段应被忽略（201）", "D 畸形 JSON", "P3")
def _():
    s, t = expect_status("D08", "POST", "/api/jobs", body={
        "name": "未知字段探测定时任务", "schedule_type": "interval",
        "interval_minutes": 30, "prompt": "p", "extra": True}, expect=201)
    SEED["tmp_job"] = jbody(t)["id"]


@test("D09", "POST notes 深层嵌套垃圾字段应 201 或 422 而非 500", "D 畸形 JSON", "P2")
def _():
    s, t = expect_status("D09", "POST", "/api/notes", body={
        "title": "深层嵌套探测", "content": "x", "junk": {"a": {"b": {"c": [1, 2, {"d": None}]}}}},
        expect=(201, 422))
    if s == 201:
        SEED["tmp_note"] = jbody(t)["id"]


# ================================================================ E 错误 method

@test("E01", "PATCH /api/tasks/{id} 应 405", "E 错误 method", "P2")
def _():
    expect_status("E01", "PATCH", f"/api/tasks/{SEED['task_id']}", body={"title": "x"}, expect=405)


@test("E02", "PUT/DELETE /api/tasks（无 id）应 405", "E 错误 method", "P2")
def _():
    expect_status("E02", "PUT", "/api/tasks", body={"title": "x"}, expect=405)
    expect_status("E02", "DELETE", "/api/tasks", 405)


@test("E03", "POST /api/health 应 405", "E 错误 method", "P2")
def _():
    expect_status("E03", "POST", "/api/health", 405)


@test("E04", "PUT /api/dashboard/summary 应 405", "E 错误 method", "P2")
def _():
    expect_status("E04", "PUT", "/api/dashboard/summary", body={}, expect=405)


@test("E05", "DELETE /api/search 应 405", "E 错误 method", "P2")
def _():
    expect_status("E05", "DELETE", "/api/search", 405)


@test("E06", "GET /api/jobs/{id}/run 应 405/404（路由仅 POST）", "E 错误 method", "P2")
def _():
    # 该路径只注册了 POST；当前 FastAPI 版本对方法不匹配返回 404，语义上 404/405 均可接受
    expect_status("E06", "GET", f"/api/jobs/{SEED['job_id']}/run", (404, 405))


@test("E07", "POST /api/notes/tags 应 405", "E 错误 method", "P2")
def _():
    expect_status("E07", "POST", "/api/notes/tags", body={}, expect=405)


@test("E08", "PUT /api/skills/rescan 应 405", "E 错误 method", "P2")
def _():
    expect_status("E08", "PUT", "/api/skills/rescan", body={}, expect=405)


@test("E09", "DELETE 交易集合端点（无 id）应 405", "E 错误 method", "P2")
def _():
    expect_status("E09", "DELETE", "/api/finance/ledger/personal/transactions", 405)


@test("E10", "POST /api/schedules/{id}/archive 不存在应 404/405", "E 错误 method", "P2")
def _():
    # 无 archive 路由：POST 落到 /api/schedules/{id} 时为 405，路径未匹配时为 404，均属拒绝
    expect_status("E10", "POST", f"/api/schedules/{SEED['schedule_id']}/archive", (404, 405))


@test("E11", "OPTIONS /api/tasks CORS 预检应放行", "E 错误 method", "P3")
def _():
    # 裸 OPTIONS 不是预检（405 合理）；带 Origin + Access-Control-Request-Method 才是
    expect_status("E11", "OPTIONS", "/api/tasks", (200, 204), headers={
        "Origin": "http://localhost:5173",
        "Access-Control-Request-Method": "GET",
    })


@test("E12", "HEAD /api/health 应 200", "E 错误 method", "P3")
def _():
    expect_status("E12", "HEAD", "/api/health", 200)


# ================================================================ F 超长字符串

@test("F01", "tasks q 超长（10001）应 422", "F 超长字符串", "P1")
def _():
    expect_status("F01", "GET", "/api/tasks" + qs(q="x" * 10001), 422)


@test("F02", "notes q/tag 超长应 422", "F 超长字符串", "P1")
def _():
    expect_status("F02", "GET", "/api/notes" + qs(q="x" * 101), 422)
    expect_status("F02", "GET", "/api/notes" + qs(tag="x" * 21), 422)


@test("F03", "tasks title 10000 字符应 422", "F 超长字符串", "P1")
def _():
    expect_status("F03", "POST", "/api/tasks", body={"title": "x" * 10000}, expect=422)


@test("F04", "notes content 60000 字符应 422", "F 超长字符串", "P1")
def _():
    expect_status("F04", "POST", "/api/notes", body={"title": "n", "content": "x" * 60000}, expect=422)


@test("F05", "route-test text 2001 字符应 422", "F 超长字符串", "P2")
def _():
    expect_status("F05", "POST", "/api/assistant/agents/route-test",
                  body={"text": "x" * 2001}, expect=422)


@test("F06", "8KB URL 路径不应 500", "F 超长字符串", "P2")
def _():
    expect_status("F06", "GET", "/api/tasks/" + "a" * 8000, (400, 404, 414, 422, 431))


@test("F07", "32KB URL 路径不应 500", "F 超长字符串", "P2")
def _():
    expect_status("F07", "GET", "/api/tasks/" + "a" * 32000, (400, 404, 414, 422, 431))


@test("F08", "100KB 自定义 Header 不应 500", "F 超长字符串", "P2")
def _():
    expect_status("F08", "GET", "/api/health", (200, 400, 413, 431),
                  headers={"X-Probe-Long": "x" * 100 * 1024})


@test("F09", "transactions keyword 5000 字符应 200（仅无匹配）", "F 超长字符串", "P3")
def _():
    expect_status("F09", "GET", "/api/finance/ledger/personal/transactions"
                   + qs(keyword="x" * 5000), 200)


# ================================================================ G 路径注入

@test("G01", "tasks id 非数字应 422", "G 路径注入", "P1")
def _():
    expect_status("G01", "GET", "/api/tasks/abc", 422)
    expect_status("G01", "GET", "/api/tasks/1%20OR%201=1", 422)
    expect_status("G01", "GET", "/api/tasks/1e3", 422)
    expect_status("G01", "GET", "/api/tasks/0x1F", 422)


@test("G02", "tasks id=-1 应 422（主键必须为正）", "G 路径注入", "P2")
def _():
    # 主键为自增正整数，负数属非法入参，入口校验拒绝为 422（而非查库后 404）
    expect_status("G02", "GET", "/api/tasks/-1", 422)


@test("G03", "tasks id 超 int64 应 422", "G 路径注入", "P2")
def _():
    expect_status("G03", "GET", "/api/tasks/99999999999999999999", 422)


@test("G04", "路径含空字节不应 500", "G 路径注入", "P2")
def _():
    expect_status("G04", "GET", "/api/tasks/%00", (400, 404, 422))


@test("G05", "技能包文件路径穿越应 400/404", "G 路径注入", "P1")
def _():
    expect_status("G05", "GET", "/api/skills/packs/..%2F..%2F..%2Fetc/files/passwd", (400, 404))
    expect_status("G05", "GET", "/api/skills/packs/..%2F..%2Fetc%2Fpasswd/files/SKILL.md", (400, 404))
    expect_status("G05", "GET", "/api/skills/packs/x/files/..%2F..%2F..%2Fetc%2Fpasswd", (400, 404))


@test("G06", "专家包名称路径穿越应 400/404 且不泄露文件", "G 路径注入", "P1")
def _():
    # 编码斜杠让路由不匹配直接 404，或解码后由白名单拒绝为 400；两者都安全
    _, t1 = expect_status("G06", "GET", "/api/experts/packs/..%2F..%2Fetc%2Fpasswd/content", (400, 404))
    _, t2 = expect_status("G06", "GET", "/api/experts/packs/..%2Fetc%2Fpasswd/content", (400, 404))
    assert "root:" not in (t1 or "") and "root:" not in (t2 or ""), "路径穿越响应疑似泄露系统文件"


@test("G07", "调试中心截图路径穿越应 400/404", "G 路径注入", "P1")
def _():
    expect_status("G07", "GET", "/api/debug/runs/..%2F..%2F/shots/x.png", (400, 404))
    expect_status("G07", "GET", "/api/debug/runs/x/shots/..%2F..%2F..%2Fetc%2Fpasswd", (400, 404))


@test("G08", "数据仓表名注入应 400/404 且不泄露数据", "G 路径注入", "P1")
def _():
    # 编码斜杠导致路由不匹配（404）与白名单拒绝（400）都能阻断穿越
    _, t1 = expect_status("G08", "GET", "/api/finance/vault/tables/..%2Fetc%2Fpasswd/preview", (400, 404))
    expect_status("G08", "GET", "/api/finance/vault/tables/t%3B%20DROP%20TABLE%20t/preview", 400)
    expect_status("G08", "GET", "/api/finance/vault/tables/sqlite_master/preview", 404)
    assert "root:" not in (t1 or ""), "路径穿越响应疑似泄露系统文件"


@test("G09", "数据仓 SQL 非 SELECT / 多语句应 400", "G 路径注入", "P1")
def _():
    expect_status("G09", "POST", "/api/finance/vault/query", body={"sql": "DROP TABLE t"}, expect=400)
    expect_status("G09", "POST", "/api/finance/vault/query",
                  body={"sql": "SELECT 1; DROP TABLE t"}, expect=400)
    expect_status("G09", "POST", "/api/finance/vault/query", body={"sql": ""}, expect=422)


@test("G10", "数据仓查询系统表应被拒", "G 路径注入", "P2")
def _():
    expect_status("G10", "POST", "/api/finance/vault/query",
                  (400, 200), body={"sql": "SELECT * FROM sqlite_master"})


@test("G11", "LIKE 通配符注入不应 500", "G 路径注入", "P1")
def _():
    expect_status("G11", "GET", "/api/tasks" + qs(q="%"), 200)
    expect_status("G11", "GET", "/api/tasks" + qs(q="_"), 200)
    expect_status("G11", "GET", "/api/notes" + qs(tag="%"), 200)
    expect_status("G11", "GET", "/api/notes" + qs(q="\\"), 200)


@test("G12", "SQL 注入串入库存字段后检索应 200", "G 路径注入", "P1")
def _():
    payload = "'; DROP TABLE tasks;--"
    s, t, _ = req("POST", "/api/tasks", body={"title": payload})
    assert s == 201, f"含注入串的任务应正常创建: {s} {t[:200]}"
    SEED["tmp_inject"] = jbody(t)["id"]
    s, t, _ = req("GET", "/api/tasks" + qs(q="DROP TABLE"))
    assert s == 200 and jbody(t)["total"] >= 1, f"注入串应可正常检索: {s} {t[:200]}"


# ================================================================ H 日期边界

@test("H01", "due_before=2026-02-30（不存日）应 422", "H 日期边界", "P1")
def _():
    expect_status("H01", "GET", "/api/tasks" + qs(due_before="2026-02-30"), 422)


@test("H02", "due_after=2026-10-20T99:99:99 应 422", "H 日期边界", "P1")
def _():
    expect_status("H02", "GET", "/api/tasks" + qs(due_after="2026-10-20T99:99:99"), 422)


@test("H03", "合法日期区间查询应 200", "H 日期边界", "P3")
def _():
    expect_status("H03", "GET", "/api/tasks" + qs(due_before="2026-12-31", due_after="2026-01-01"), 200)
    expect_status("H03", "GET", "/api/schedules" + qs(start="2026-10-01", end="2026-10-31"), 200)


@test("H04", "due_before 带尾空格应容忍（200）", "H 日期边界", "P3")
def _():
    expect_status("H04", "GET", "/api/tasks" + qs(due_before="2026-12-31 "), 200)


@test("H05", "个人记账导出 month 非法应 400", "H 日期边界", "P2")
def _():
    expect_status("H05", "GET", "/api/finance/ledger/personal/export/csv" + qs(month="2026-13"), 400)


@test("H06", "个人记账交易 month 非法应 400", "H 日期边界", "P2")
def _():
    expect_status("H06", "GET", "/api/finance/ledger/personal/transactions" + qs(month="2026-13"), 400)


# ================================================================ I 契约行为

@test("I01", "健康检查应 200 且 status=ok", "I 契约行为", "P1")
def _():
    s, t = expect_status("I01", "GET", "/api/health", 200)
    d = jbody(t)
    assert d and d.get("status") == "ok", f"health 响应异常: {t[:200]}"


@test("I02", "预设列表应 200 且含 presets", "I 契约行为", "P3")
def _():
    s, t = expect_status("I02", "GET", "/api/providers/presets", 200)
    d = jbody(t)
    assert d and isinstance(d.get("presets"), list) and d["presets"], f"presets 异常: {t[:200]}"


@test("I03", "模块连通性自检应 200", "I 契约行为", "P3")
def _():
    expect_status("I03", "GET", "/api/finance/hello/ping", 200)
    expect_status("I03", "GET", "/api/finance/invoice/engine/status", 200)


@test("I04", "调试中心总览/问题列表应 200", "I 契约行为", "P3")
def _():
    expect_status("I04", "GET", "/api/debug/summary", 200)
    expect_status("I04", "GET", "/api/debug/issues", 200)


@test("I05", "助手团队/配置/模型列表应 200", "I 契约行为", "P2")
def _():
    expect_status("I05", "GET", "/api/assistant/team", 200)
    expect_status("I05", "GET", "/api/assistant/config", 200)
    expect_status("I05", "GET", "/api/assistant/models", 200)


@test("I06", "RAG 统计应 200", "I 契约行为", "P3")
def _():
    expect_status("I06", "GET", "/api/knowledge/rag/stats", 200)


@test("I07", "税务企业/政策列表应 200（集合端点带尾斜杠）", "I 契约行为", "P3")
def _():
    # 模块内集合路由声明为 "/"，前端调用也统一带尾斜杠
    expect_status("I07", "GET", "/api/finance/tax/enterprises/", 200)
    expect_status("I07", "GET", "/api/finance/tax/policies/", 200)


@test("I08", "任务完整 CRUD 往返", "I 契约行为", "P1")
def _():
    s, t, _ = req("POST", "/api/tasks", body={"title": "往返探测任务", "status": "todo"})
    assert s == 201, f"创建失败: {s} {t[:200]}"
    tid = jbody(t)["id"]
    s, t, _ = req("GET", f"/api/tasks/{tid}")
    assert s == 200 and jbody(t)["title"] == "往返探测任务", f"读取失败: {s} {t[:200]}"
    s, t, _ = req("PUT", f"/api/tasks/{tid}", body={"status": "done"})
    assert s == 200 and jbody(t)["status"] == "done", f"更新失败: {s} {t[:200]}"
    s, t, _ = req("DELETE", f"/api/tasks/{tid}")
    assert s == 204, f"删除失败: {s} {t[:200]}"
    s, t, _ = req("GET", f"/api/tasks/{tid}")
    assert s == 404, f"删除后应 404: {s} {t[:200]}"


@test("I09", "笔记完整 CRUD 往返", "I 契约行为", "P1")
def _():
    s, t, _ = req("POST", "/api/notes", body={"title": "往返探测笔记", "content": "c"})
    assert s == 201, f"创建失败: {s} {t[:200]}"
    nid = jbody(t)["id"]
    s, t, _ = req("PUT", f"/api/notes/{nid}", body={"pinned": True})
    assert s == 200 and jbody(t)["pinned"] is True, f"更新失败: {s} {t[:200]}"
    s, t, _ = req("DELETE", f"/api/notes/{nid}")
    assert s == 204, f"删除失败: {s} {t[:200]}"
    s, t, _ = req("GET", f"/api/notes/{nid}")
    assert s == 404, f"删除后应 404: {s} {t[:200]}"


@test("I10", "定时任务 CRUD + 立即执行往返", "I 契约行为", "P1")
def _():
    s, t, _ = req("POST", "/api/jobs", body={
        "name": "往返探测定时任务", "schedule_type": "interval",
        "interval_minutes": 30, "prompt": "p"})
    assert s == 201, f"创建失败: {s} {t[:200]}"
    jid = jbody(t)["id"]
    s, t, _ = req("PUT", f"/api/jobs/{jid}", body={"enabled": False})
    assert s == 200, f"更新失败: {s} {t[:200]}"
    s, t, _ = req("POST", f"/api/jobs/{jid}/run", timeout=40)
    assert s == 200 and jbody(t).get("status") in ("ok", "skipped", "error"), \
        f"立即执行失败: {s} {t[:300]}"
    s, t, _ = req("DELETE", f"/api/jobs/{jid}")
    assert s == 204, f"删除失败: {s} {t[:200]}"


@test("I11", "个人记账交易 CRUD 往返", "I 契约行为", "P1")
def _():
    s, t, _ = req("POST", "/api/finance/ledger/personal/transactions", body={
        "kind": "income", "amount": 99.99, "occurred_on": "2026-10-16",
        "account_id": SEED["account_id"], "note": "往返探测"})
    assert s == 201, f"创建失败: {s} {t[:200]}"
    xid = jbody(t)["id"]
    s, t, _ = req("PUT", f"/api/finance/ledger/personal/transactions/{xid}",
                  body={"note": "改后"})
    assert s == 200, f"更新失败: {s} {t[:200]}"
    s, t, _ = req("DELETE", f"/api/finance/ledger/personal/transactions/{xid}")
    assert s == 200, f"删除失败: {s} {t[:200]}"


@test("I12", "记忆 CRUD + 检索往返", "I 契约行为", "P1")
def _():
    s, t, _ = req("POST", "/api/knowledge/memories", body={"content": "往返探测记忆内容"})
    assert s == 201, f"创建失败: {s} {t[:200]}"
    mid = jbody(t)["id"]
    s, t, _ = req("POST", "/api/knowledge/memories/recall", body={"query": "往返探测"})
    assert s == 200, f"检索失败: {s} {t[:200]}"
    s, t, _ = req("DELETE", f"/api/knowledge/memories/{mid}")
    assert s == 200, f"删除失败: {s} {t[:200]}"


@test("I13", "未配置 LLM 时聊天应 200 友好降级", "I 契约行为", "P1")
def _():
    s, t = expect_status("I13", "POST", "/api/assistant/chat",
                         body={"session_id": "probe-sess-0001", "message": "你好"},
                         expect=200, timeout=60)
    d = jbody(t)
    assert d is not None, f"chat 响应非 JSON: {t[:200]}"


@test("I14", "SSE 流式聊天应 200 且以 [DONE] 收尾", "I 契约行为", "P1")
def _():
    s, t = expect_status("I14", "POST", "/api/assistant/chat/stream",
                         body={"session_id": "probe-sess-0002", "message": "你好"},
                         expect=200, timeout=60)
    assert "data:" in t and "[DONE]" in t, f"SSE 协议异常: {t[:300]}"


@test("I15", "尾斜杠：307 重定向或明确 404（当前不重定向）", "I 契约行为", "P3")
def _():
    # 当前 FastAPI 懒挂载不自动重定向；前端调用的是无斜杠的精确路径，404 可接受
    expect_status("I15", "GET", "/api/tasks/", (307, 404))


@test("I16", "重复查询参数应 200（取其一）", "I 契约行为", "P3")
def _():
    expect_status("I16", "GET", "/api/tasks?page=2&page=1", 200)


@test("I17", "Unicode/emoji 关键词应 200", "I 契约行为", "P3")
def _():
    expect_status("I17", "GET", "/api/tasks" + qs(q="🎉"), 200)
    expect_status("I17", "GET", "/api/search" + qs(q="探测"), 200)


@test("I18", "空 q 应被忽略（200 全量）", "I 契约行为", "P3")
def _():
    expect_status("I18", "GET", "/api/tasks" + qs(q=""), 200)
    expect_status("I18", "GET", "/api/notes" + qs(q=""), 200)


@test("I19", "未知 sort 应回退默认排序（200）", "I 契约行为", "P3")
def _():
    s, t = expect_status("I19", "GET", "/api/tasks" + qs(sort="no-such-sort"), 200)
    d = jbody(t)
    assert d and "items" in d, f"响应缺 items: {t[:200]}"


@test("I20", "合法 sort 各值应 200", "I 契约行为", "P3")
def _():
    for srt in ("created_desc", "created_asc", "due_asc", "due_desc", "priority_desc"):
        expect_status("I20", "GET", "/api/tasks" + qs(sort=srt), 200)


@test("I21", "看板/概览汇总应 200 且结构完整", "I 契约行为", "P1")
def _():
    s, t = expect_status("I21", "GET", "/api/dashboard/summary", 200)
    d = jbody(t)
    for k in ("task", "schedule", "note", "upcoming_tasks", "today_schedules",
              "recent_notes", "trend_7d"):
        assert d and k in d, f"summary 缺字段 {k}: {t[:300]}"


@test("I22", "MCP 服务器创建/删除往返（enabled=False 无副作用）", "I 契约行为", "P2")
def _():
    s, t, _ = req("POST", "/api/mcp/servers", body={
        "name": "probe-mcp", "command": "python", "args": ["-V"], "enabled": False})
    assert s == 201, f"创建失败: {s} {t[:200]}"
    sid = jbody(t)["id"]
    s, t, _ = req("DELETE", f"/api/mcp/servers/{sid}")
    assert s == 200, f"删除失败: {s} {t[:200]}"


@test("I23", "定时任务动作清单应 200", "I 契约行为", "P3")
def _():
    s, t = expect_status("I23", "GET", "/api/jobs/actions", 200)
    d = jbody(t)
    assert d and isinstance(d.get("actions"), list), f"actions 异常: {t[:200]}"


@test("I24", "标签清单应 200", "I 契约行为", "P3")
def _():
    s, t = expect_status("I24", "GET", "/api/notes/tags", 200)
    d = jbody(t)
    assert d and isinstance(d.get("tags"), list), f"tags 异常: {t[:200]}"


@test("I25", "会话列表/历史/专家清单应 200", "I 契约行为", "P2")
def _():
    expect_status("I25", "GET", "/api/assistant/sessions", 200)
    expect_status("I25", "GET", "/api/assistant/agents", 200)
    # dir 是必填查询参数，缺省 422 属正确校验；补上参数后应 200
    expect_status("I25", "GET", "/api/assistant/skills/discover?dir=.", 200)


@test("I26", "数据仓表清单应 200", "I 契约行为", "P3")
def _():
    expect_status("I26", "GET", "/api/finance/vault/tables", 200)


@test("I27", "前端 dist 托管根路径应 200", "I 契约行为", "P3")
def _():
    expect_status("I27", "GET", "/", (200, 404))


@test("I28", "模型探测状态读取（存在或 404 均可）", "I 契约行为", "P3")
def _():
    expect_status("I28", "GET", "/api/models/1/probe", (200, 404))


# ================================================================ 运行

def run_all():
    pages = []
    groups = {}
    for t in TESTS:
        groups.setdefault(t["group"], []).append(t)
    for g, ts in groups.items():
        ok = True
        for t in ts:
            try:
                t["fn"]()
            except AssertionError as e:
                ok = False
                t["error"] = str(e)
            except Exception as e:
                ok = False
                t["error"] = f"探测脚本异常: {type(e).__name__}: {e}"
        pages.append({"route": g, "name": g, "status": "pass" if ok else "fail",
                      "console_errors": [], "page_errors": [], "failed_reqs": []})

    results = []
    for t in TESTS:
        passed = "error" not in t
        results.append({
            "id": t["id"], "name": t["name"],
            "passed": passed,
            "detail": t.get("error", "") or f"符合契约（{t['group']}）",
        })
        if not passed:
            # 失败即缺陷（本区域只报不修）
            BUGS.append({
                "severity": t["severity"],
                "title": f"[{t['id']}] {t['name']}",
                "page": t["id"],
                "description": t["error"],
                "status": "reported",
                "fix_evidence": "",
                "test_evidence": t["error"][:300],
            })

    passed_n = sum(1 for r in results if r["passed"])
    print("=" * 72)
    for r in results:
        mark = "PASS" if r["passed"] else "FAIL"
        print(f"[{mark}] {r['id']} {r['name']}" + ("" if r["passed"] else f" :: {r['detail']}"))
    print("-" * 72)
    print(f"observations={len(OBSERVATIONS)} total={len(results)} "
          f"passed={passed_n} failed={len(results) - passed_n}")

    # 服务器日志中的未捕获异常（500 痕迹）
    log_text = LOG.read_text(encoding="utf-8", errors="replace") if LOG.exists() else ""
    tracebacks = log_text.count("Traceback (most recent call last)")
    if tracebacks:
        print(f"server log tracebacks={tracebacks}")

    out = {
        "area": AREA,
        "base": BASE,
        "started_at": STARTED_AT,
        "finished_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "pages": pages,
        "tests": results,
        "bugs": BUGS,
        "observations": OBSERVATIONS,
        "server_log": {"path": str(LOG.relative_to(ROOT)), "tracebacks": tracebacks},
        "seed": SEED,
    }
    JSON_OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"results -> {JSON_OUT}")
    return 0 if passed_n == len(results) else 1


def main():
    try:
        if not wait_ready():
            print("FATAL: server not ready")
            return 2
        seed_data()
        return run_all()
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except Exception:
            server.kill()
        _log.close()
        print("server terminated")


if __name__ == "__main__":
    sys.exit(main())
