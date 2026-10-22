# -*- coding: utf-8 -*-
"""finance-board / finance-cashflow / finance-expense 区域深度 E2E 探测（端口 8221）。

覆盖：
- 看板：空态、快照列表/最新快照/HTML 端点、成功与失败快照归档、管线成功/失败/不可用/
  并发 409、iframe 渲染、失败快照禁用、零值与负值展示、stamp 与快照 HTML 的 XSS 防护。
  真实微信管线依赖本机微信目录且会改写原项目 data/output，探针不执行它；
  管线外壳用 dev/e2e/probe_results/finance-bce-board-pipeline 下的隔离合成管线验证
  （真实代码路径：子进程 → 归档 → DB → UI），并另测真实 503 降级。
- 现金流：空态、创建运行（合法/缺参/非法月份/非法类型/zip-slip/解压炸弹）、文件名
  清洗与超长名、真实 V1 引擎失败时 503 + failed + 日志（不伪造成功）、重跑、产物下载
  409/404、running 状态守卫、分页与真实 total、UI 新建/执行失败/日志/空态/XSS/加载失败。
- 费用：规则摘要/重载、上传校验（缺文件/类型/空文件/20MB 超限）、非法 xlsx 落 failed
  且清理 inbox、真实引擎成功分类（7 行合成明细账）与统计/明细对拍、下载魔数与中文
  文件名、分页边界、路径穿越文件名、UI 上传/结果页/图表/未命中筛选/分页/异常 id/
  XSS/规则摘要不可用降级。

可重复运行：独立库 finance-bce.db、数据目录 finance-bce-fin/、调试目录
finance-bce-debug/、合成管线 finance-bce-board-pipeline/，每次重建；服务自起自停。
"""
import io
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
BACKEND = ROOT / "backend"
RESULTS = ROOT / "dev" / "e2e" / "probe_results"
AREA = "finance-board-cashflow-expense"
DB = RESULTS / "finance-bce.db"
FIN_DIR = RESULTS / "finance-bce-fin"
DEBUG_DIR = RESULTS / "finance-bce-debug"
BOARD_PIPE = RESULTS / "finance-bce-board-pipeline"
SHOTS = RESULTS / AREA / "shots"
LOG = RESULTS / f"{AREA}.server.log"
PORT = 8221
BASE = f"http://127.0.0.1:{PORT}"
STARTED_AT = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

RESULTS.mkdir(parents=True, exist_ok=True)
SHOTS.mkdir(parents=True, exist_ok=True)
for p in (DB,):
    if p.exists():
        p.unlink()
for d in (FIN_DIR, DEBUG_DIR, BOARD_PIPE):
    if d.exists():
        shutil.rmtree(d)


def _write_board_stub() -> None:
    """隔离合成看板管线：4 个脚本，成功时产出内嵌标记的 HTML；fail.flag 令 m02 失败，
    slow.flag 令 m03 睡 3 秒（并发 409 用例）。"""
    scripts = BOARD_PIPE / "scripts"
    (BOARD_PIPE / "output").mkdir(parents=True, exist_ok=True)
    scripts.mkdir(parents=True, exist_ok=True)
    (scripts / "m01_scan.py").write_text(
        "print('probe m01 scan ok')\n", encoding="utf-8")
    (scripts / "m02_extract.py").write_text(
        "import sys\nfrom pathlib import Path\n"
        "root = Path(__file__).resolve().parent.parent\n"
        "if (root / 'fail.flag').exists():\n"
        "    print('probe m02 forced failure', file=sys.stderr)\n"
        "    sys.exit(3)\n"
        "print('probe m02 extract ok')\n", encoding="utf-8")
    (scripts / "m03_aggregate.py").write_text(
        "import time\nfrom pathlib import Path\n"
        "root = Path(__file__).resolve().parent.parent\n"
        "if (root / 'slow.flag').exists():\n"
        "    time.sleep(3)\n"
        "print('probe m03 aggregate ok')\n", encoding="utf-8")
    (scripts / "m04_html.py").write_text(
        "import time\nfrom pathlib import Path\n"
        "root = Path(__file__).resolve().parent.parent\n"
        "out = root / 'output'\n"
        "out.mkdir(exist_ok=True)\n"
        "html = '''<!doctype html><html><head><meta charset=\"utf-8\"><title>隔离合成看板</title></head>"
        "<body><h1>ISOLATED-STUB-BOARD</h1><div id=\"run-ts\">RUN_TS</div>"
        "<script>try{parent.__board_xss=1}catch(e){}</script></body></html>'''\n"
        "html = html.replace('RUN_TS', str(time.time()))\n"
        "(out / '经营财务报表监控看板.html').write_text(html, encoding='utf-8')\n"
        "print('probe m04 html ok')\n", encoding="utf-8")


_write_board_stub()

env = os.environ.copy()
env["DATABASE_URL"] = f"sqlite:///{DB.as_posix()}"
env["FINANCE_DATA_DIR"] = str(FIN_DIR)
env["DEBUG_DATA_DIR"] = str(DEBUG_DIR)
env["FINANCE_BOARD_PIPELINE_PATH"] = str(BOARD_PIPE)
env["PYTHONIOENCODING"] = "utf-8"
env["SEED_ON_STARTUP"] = "0"

log_fh = open(LOG, "w", encoding="utf-8")
server = subprocess.Popen(
    [str(BACKEND / "venv" / "Scripts" / "python.exe"), "-m", "uvicorn",
     "app.main:app", "--host", "127.0.0.1", "--port", str(PORT)],
    cwd=str(BACKEND), env=env, stdout=log_fh, stderr=log_fh,
)

results = []   # {"id","name","passed","detail"}
pages = []     # {"route","name","status","console_errors","page_errors","failed_reqs"}
console_errors = []
page_errors = []
failed_reqs = []
page = None


def shot(tag):
    try:
        if page is not None:
            page.screenshot(path=str(SHOTS / f"{tag}.png"))
    except Exception:
        pass


def record(tid, name, fn):
    try:
        detail = fn() or ""
        results.append({"id": tid, "name": name, "passed": True, "detail": detail})
        print(f"[PASS] {tid} {name}" + (f" | {detail}" if detail else ""), flush=True)
    except Exception as e:
        results.append({"id": tid, "name": name, "passed": False, "detail": str(e)[:400]})
        print(f"[FAIL] {tid} {name}: {str(e)[:400]}", flush=True)
        shot(f"{tid}-FAIL")


def wait_ready(timeout=60):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            urllib.request.urlopen(f"{BASE}/api/dashboard/summary", timeout=2)
            return True
        except Exception:
            time.sleep(1)
    return False


# ---------------- HTTP ----------------

def req(method, path, body=None, data=None, headers=None, timeout=60, raw=False):
    hdrs = dict(headers or {})
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        hdrs["Content-Type"] = "application/json"
    r = urllib.request.Request(f"{BASE}{path}", data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            payload = resp.read()
            status = resp.status
            h = {k.lower(): v for k, v in resp.headers.items()}
    except urllib.error.HTTPError as e:
        payload = e.read()
        status = e.code
        h = {k.lower(): v for k, v in e.headers.items()}
    if raw:
        return status, h, payload
    try:
        payload = json.loads(payload.decode("utf-8"))
    except Exception:
        payload = payload.decode("utf-8", "replace")
    return status, h, payload


def multipart(files, fields=()):
    boundary = "----bce" + uuid.uuid4().hex
    buf = io.BytesIO()
    for name, value in fields:
        buf.write(f"--{boundary}\r\n".encode())
        buf.write(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
        buf.write(str(value).encode("utf-8"))
        buf.write(b"\r\n")
    for field, filename, content in files:
        buf.write(f"--{boundary}\r\n".encode())
        buf.write(f'Content-Disposition: form-data; name="{field}"; filename="{filename}"\r\n'.encode())
        buf.write(b"Content-Type: application/octet-stream\r\n\r\n")
        buf.write(content)
        buf.write(b"\r\n")
    buf.write(f"--{boundary}--\r\n".encode())
    return buf.getvalue(), f"multipart/form-data; boundary={boundary}"


def post_files(path, files, fields=(), timeout=60):
    data, ctype = multipart(files, fields)
    return req("POST", path, data=data, headers={"Content-Type": ctype}, timeout=timeout)


# ---------------- DB ----------------

def db_conn():
    conn = sqlite3.connect(str(DB), timeout=10)
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


def db_exec(sql, params=()):
    last = None
    for _ in range(8):
        try:
            with db_conn() as conn:
                cur = conn.execute(sql, params)
                conn.commit()
                last = cur.lastrowid
            return last
        except sqlite3.OperationalError:
            time.sleep(0.2)
    raise RuntimeError(f"DB 写入失败: {sql}")


def db_rows(sql, params=()):
    with db_conn() as conn:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


# ---------------- 数据重置 ----------------

def _rm(path: Path):
    shutil.rmtree(path, ignore_errors=True)


def reset_board():
    db_exec("DELETE FROM mod_board_snapshot")
    _rm(FIN_DIR / "finance_board")


def reset_cashflow():
    db_exec("DELETE FROM mod_cashflow_run")
    _rm(FIN_DIR / "finance_cashflow")


def reset_expense():
    db_exec("DELETE FROM mod_expense_row")
    db_exec("DELETE FROM mod_expense_run")
    _rm(FIN_DIR / "finance_expense")


# ---------------- 浏览器辅助 ----------------

def _navigate(route, reload=True, wait=700):
    """hash 路由导航：同 URL 的 page.goto 不会触发新导航，必须 reload。

    另外 SPA 的 iframe/长请求可能让 networkidle 永不满足，先把 load 作为硬等待，
    networkidle 只做 8s 的软等待（后续断言各自 wait_for），避免整测 30s 超时。
    """
    target = f"{BASE}/#{route}"
    if page.url == target:
        resp = page.reload(wait_until="load", timeout=30000)
    else:
        resp = page.goto(target, wait_until="load", timeout=30000)
        if reload:
            resp = page.reload(wait_until="load", timeout=30000)
    try:
        page.wait_for_load_state("networkidle", timeout=8000)
    except Exception:
        pass
    page.wait_for_timeout(wait)
    return resp


def open_route(route, reload=True, wait=700):
    _navigate(route, reload, wait)


def rows():
    return page.locator("tr.el-table__row:visible")


def row(text):
    return page.locator("tr.el-table__row", has_text=text).first


def dlg():
    return page.locator(".el-dialog:visible")


def toast_texts():
    out = []
    loc = page.locator(".el-message")
    for i in range(loc.count()):
        try:
            if loc.nth(i).is_visible():
                out.append(loc.nth(i).inner_text())
        except Exception:
            pass
    return out


def wait_toast(substr, timeout=12000, kind=None):
    sel = ".el-message" + (f"--{kind}" if kind else "")
    end = time.time() + timeout / 1000
    while time.time() < end:
        loc = page.locator(sel)
        for i in range(loc.count()):
            try:
                if loc.nth(i).is_visible():
                    text = loc.nth(i).inner_text()
                    if substr in text:
                        return text
            except Exception:
                pass
        page.wait_for_timeout(200)
    raise AssertionError(f"未出现包含 {substr!r} 的提示；当前提示={toast_texts()}")


def wait_rows(count, timeout=10000):
    end = time.time() + timeout / 1000
    while time.time() < end:
        if rows().count() == count:
            return
        page.wait_for_timeout(200)
    raise AssertionError(f"等待表格 {count} 行超时，当前 {rows().count()} 行")


def select_value(scope=None):
    sel = (scope or page).locator(".el-select").first
    span = sel.locator(".el-select__placeholder span")
    if span.count():
        return span.first.inner_text().strip()
    ph = sel.locator(".el-select__placeholder")
    return ph.first.inner_text().strip() if ph.count() else ""


def choose_select(scope, option_text, index=0):
    sel = scope.locator(".el-select").nth(index)
    sel.click()
    page.wait_for_timeout(250)
    page.locator(".el-select-dropdown:visible .el-select-dropdown__item",
                 has_text=option_text).first.click()
    page.wait_for_timeout(300)


def page_recon(route, name, reload=True, wait=700):
    ce0, pe0, fr0 = len(console_errors), len(page_errors), len(failed_reqs)
    resp = _navigate(route, reload, wait)
    pages.append({
        "route": route, "name": name,
        "status": resp.status if resp else "?",
        "console_errors": console_errors[ce0:],
        "page_errors": page_errors[pe0:],
        "failed_reqs": [x for x in failed_reqs[fr0:] if "favicon" not in x],
    })
    return resp


# ---------------- 看板 fixture ----------------

BOARD_HTML = "<!doctype html><html><head><meta charset='utf-8'></head><body><h1>SEEDED-BOARD-OK</h1><div>123.45</div></body></html>"


def seed_snapshot(stamp, status="ok", elapsed=1.25, html=None, missing=False,
                  created="2026-10-20 10:00:00"):
    html_path = ""
    if status == "ok":
        # stamp 可能含 XSS/Windows 非法字符，目录名用哈希，原值只入库供 UI 转义验证
        dir_name = "snap_" + hashlib.sha1(stamp.encode("utf-8")).hexdigest()[:16]
        d = FIN_DIR / "finance_board" / "snapshots" / dir_name
        f = d / "board.html"
        if missing:
            html_path = str(f)
        else:
            d.mkdir(parents=True, exist_ok=True)
            f.write_text(html or BOARD_HTML, encoding="utf-8")
            html_path = str(f)
    return db_exec(
        "INSERT INTO mod_board_snapshot (stamp,status,html_path,log_tail,elapsed_sec,created_at)"
        " VALUES (?,?,?,?,?,?)",
        (stamp, status, html_path, "" if status == "ok" else "forced failed", elapsed, created),
    )


def board_snapshot_count():
    return len(req("GET", "/api/finance/board/snapshots")[2]["items"])


# ---------------- 现金流 fixture ----------------

def _row_bytes():
    return b"probe-xlsx-content"


def cash_create(month=9, files=None, timeout=60):
    if files is None:
        files = [("files", "bank.xlsx", _row_bytes())]
    fields = [] if month is None else [("actual_month", str(month))]
    return post_files("/api/finance/cashflow/runs", files, fields, timeout=timeout)


def _make_bank_xlsx() -> bytes:
    import pandas as pd

    cols = ["科目编码", "科目名称", "日期", "摘要", "借方本币", "贷方本币", "辅助核算"]
    rows_data = [
        ("10020101", "银行存款-基本户", "2026-09-01", "支付2026年9月员工工资", 0.0, 1000.00, "合成测试项目"),
        ("10020101", "银行存款-基本户", "2026-09-02", "支付银行账户手续费", 0.0, 235.50, "合成测试项目"),
        ("10020101", "银行存款-基本户", "2026-09-03", "支付9月办公场所电费", 0.0, 680.75, "合成测试项目"),
        ("10020101", "银行存款-基本户", "2026-09-04", "支付办公室绿植租摆费", 0.0, 320.00, "合成测试项目"),
        ("10020101", "银行存款-基本户", "2026-09-05", "支付商铺租赁押金", 0.0, 5000.00, "合成测试项目"),
        ("10020101", "银行存款-基本户", "2026-09-06", "支付广告宣传活动物料费", 0.0, 888.80, "合成测试项目"),
        ("10020101", "其他应收款-往来单位", "2026-09-07", "支付杂项往来结算款", 0.0, 150.00, "合成测试项目"),
    ]
    df = pd.DataFrame(rows_data, columns=cols)
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, startrow=10, sheet_name="明细")
    return buf.getvalue()


BANK_XLSX = _make_bank_xlsx()


def exp_post(filename="合成银行明细账.xlsx", content=None, timeout=120):
    return post_files("/api/finance/expense/classify",
                      [("file", filename, BANK_XLSX if content is None else content)],
                      timeout=timeout)


def exp_classify_ok(filename="合成银行明细账.xlsx"):
    s, _, d = exp_post(filename)
    assert s == 201, f"费用分类应 201，实际 {s}: {str(d)[:240]}"
    return d


# ================= 用例 =================

try:
    if not wait_ready():
        print("FATAL: server not ready")
        sys.exit(2)

    # 服务端启动后建表完成，先做一次空库基线检查
    assert board_snapshot_count() == 0

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        # 故意制造的 4xx/5xx 会让 Chromium 打 "Failed to load resource"，属预期噪声；
        # 真正的 JS 异常由 pageerror 捕获。
        page.on("console", lambda m: console_errors.append(m.text)
                if m.type == "error" and not m.text.startswith("Failed to load resource:") else None)
        page.on("pageerror", lambda e: page_errors.append(str(e)))
        page.on("requestfailed", lambda r: failed_reqs.append(f"{r.method} {r.url}"))

        # ================= 看板 API =================

        def b_api_empty():
            reset_board()
            s, _, d = req("GET", "/api/finance/board/snapshots")
            assert s == 200 and d == {"total": 0, "items": []}, f"空列表异常: {s} {d}"
            s, _, d = req("GET", "/api/finance/board/latest")
            assert s == 404 and "暂无" in d.get("detail", ""), f"空库 latest 应 404: {s} {d}"
            return "空列表 + latest 404"
        record("BCE-B-API-001", "看板空库列表与 latest", b_api_empty)

        def b_api_latest_order():
            reset_board()
            id1 = seed_snapshot("20261020_100001", created="2026-10-20 10:00:01")
            seed_snapshot("20261020_100002", status="failed", created="2026-10-20 10:00:02")
            id2 = seed_snapshot("20261020_100003", created="2026-10-20 10:00:03")
            s, _, d = req("GET", "/api/finance/board/snapshots")
            assert s == 200 and d["total"] == 3
            assert [x["id"] for x in d["items"]] == [id2, id2 - 1, id1], f"应按 id 倒序: {d['items']}"
            s, _, latest = req("GET", "/api/finance/board/latest")
            assert s == 200 and latest["id"] == id2, f"latest 应返回最新成功快照: {s} {latest}"
            return "列表倒序 + latest 跳过 failed"
        record("BCE-B-API-002", "快照列表顺序与 latest", b_api_latest_order)

        def b_api_html():
            reset_board()
            sid = seed_snapshot("20261020_110000")
            s, h, payload = req("GET", f"/api/finance/board/snapshots/{sid}/html", raw=True)
            assert s == 200, f"HTML 端点状态 {s}"
            assert b"SEEDED-BOARD-OK" in payload, "HTML 内容不符"
            assert "text/html" in h.get("content-type", ""), f"Content-Type 异常: {h.get('content-type')}"
            assert "frame-ancestors 'self'" in h.get("content-security-policy", ""), \
                f"缺 CSP frame-ancestors: {h.get('content-security-policy')}"
            return "HTML 200 + text/html + CSP"
        record("BCE-B-API-003", "快照 HTML 端点与响应头", b_api_html)

        def b_api_html_missing():
            reset_board()
            failed_id = seed_snapshot("20261020_120000", status="failed")
            missing_id = seed_snapshot("20261020_120001", status="ok", missing=True)
            s, _, d = req("GET", f"/api/finance/board/snapshots/{failed_id}/html")
            assert s == 404 and "未成功" in d.get("detail", ""), f"失败快照应 404: {s} {d}"
            s, _, d = req("GET", f"/api/finance/board/snapshots/{missing_id}/html")
            assert s == 404 and "文件缺失" in d.get("detail", ""), f"文件缺失应 404: {s} {d}"
            s, _, d = req("GET", "/api/finance/board/snapshots/99999/html")
            assert s == 404, f"不存在快照应 404: {s}"
            return "failed/文件缺失/不存在均 404 且提示明确"
        record("BCE-B-API-004", "快照 HTML 异常分支", b_api_html_missing)

        def b_api_run_success():
            reset_board()
            (BOARD_PIPE / "fail.flag").unlink(missing_ok=True)
            (BOARD_PIPE / "slow.flag").unlink(missing_ok=True)
            s, _, d = req("POST", "/api/finance/board/run", body={}, timeout=60)
            assert s == 200, f"管线运行应 200，实际 {s}: {str(d)[:240]}"
            assert d.get("status") == "ok" and d.get("message"), f"运行结果异常: {d}"
            assert d.get("stamp"), "缺少 stamp"
            s, _, lst = req("GET", "/api/finance/board/snapshots")
            assert s == 200 and lst["total"] == 1 and lst["items"][0]["id"] == d["id"]
            s, _, html = req("GET", f"/api/finance/board/snapshots/{d['id']}/html", raw=True)
            assert s == 200 and b"ISOLATED-STUB-BOARD" in html
            return f"隔离合成管线成功归档快照 {d['stamp']}"
        record("BCE-B-API-005", "运行管线（隔离合成 fixture）", b_api_run_success)

        def b_api_run_failed():
            reset_board()
            (BOARD_PIPE / "fail.flag").write_text("1", encoding="utf-8")
            try:
                s, _, d = req("POST", "/api/finance/board/run", body={}, timeout=60)
                assert s == 503, f"管线失败应 503，实际 {s}: {str(d)[:200]}"
                assert "管线步骤失败" in d.get("detail", ""), f"503 提示异常: {d}"
                s, _, lst = req("GET", "/api/finance/board/snapshots")
                assert s == 200 and lst["total"] == 1, f"失败也应归档快照: {lst}"
                item = lst["items"][0]
                assert item["status"] == "failed" and item["html_path"] == "", f"失败快照字段异常: {item}"
                log = db_rows("SELECT log_tail FROM mod_board_snapshot WHERE id=?", (item["id"],))[0]["log_tail"]
                assert log and "probe m02 forced failure" in log, f"失败日志未入库: {log[:120]!r}"
                # 契约：SnapshotOut 有界暴露 log_tail（入库 ≤4000，读取 ≤500 字符尾部），
                # API 与 DB 双重可查——前端「日志」按钮即读该字段
                assert item["log_tail"] == (log[-500:] if log else ""), \
                    f"API log_tail 应为 DB 值的有界尾部: {item['log_tail'][:60]!r}"
            finally:
                (BOARD_PIPE / "fail.flag").unlink(missing_ok=True)
            return "失败快照入库、html_path 为空、503 明确（日志 API+DB 双查）"
        record("BCE-B-API-006", "管线失败归档 failed 快照", b_api_run_failed)

        def b_api_run_unavailable():
            reset_board()
            scripts = BOARD_PIPE / "scripts"
            off = BOARD_PIPE / "scripts.off"
            scripts.rename(off)
            try:
                s, _, d = req("POST", "/api/finance/board/run", body={}, timeout=30)
                assert s == 503, f"管线目录无效应 503，实际 {s}: {str(d)[:200]}"
                assert "管线目录无效" in d.get("detail", ""), f"提示异常: {d}"
            finally:
                off.rename(scripts)
            return "管线目录无效 → 503 中文提示，非 500"
        record("BCE-B-API-007", "管线不可用降级 503", b_api_run_unavailable)

        def b_api_run_busy():
            reset_board()
            (BOARD_PIPE / "slow.flag").write_text("1", encoding="utf-8")
            results_lock = []

            def call():
                try:
                    s, _, d = req("POST", "/api/finance/board/run", body={}, timeout=60)
                    results_lock.append((s, d))
                except Exception as exc:  # pragma: no cover
                    results_lock.append((0, {"detail": str(exc)}))

            try:
                threads = [threading.Thread(target=call) for _ in range(2)]
                for t in threads:
                    t.start()
                for t in threads:
                    t.join(timeout=70)
            finally:
                (BOARD_PIPE / "slow.flag").unlink(missing_ok=True)
            statuses = sorted(x[0] for x in results_lock)
            assert statuses == [200, 409], f"并发运行应一 200 一 409，实际 {statuses}"
            busy = [d for s, d in results_lock if s == 409][0]
            assert "正在运行中" in busy.get("detail", ""), f"409 提示异常: {busy}"
            return "并发 /run 一 200 一 409"
        record("BCE-B-API-008", "管线并发运行 409", b_api_run_busy)

        # ================= 看板 UI =================

        def b_ui_empty():
            reset_board()
            page_recon("/finance/board", "board-empty")
            assert page.locator(".el-empty").count() > 0, "空库未渲染 el-empty"
            assert "暂无快照" in page.locator(".el-empty").inner_text()
            assert page.locator(".el-table").count() == 0, "空库不应渲染快照表"
            assert page.locator("button", has_text="运行管线刷新").count() == 1
            assert not page_errors[-3:], f"空态出现未捕获异常: {page_errors[-3:]}"
            return "el-empty 文案/运行按钮正常，无未捕获异常"
        record("BCE-B-UI-001", "看板空态与页面骨架", b_ui_empty)

        def b_ui_snapshots():
            reset_board()
            seed_snapshot("20261020_130001", elapsed=1.25)
            sid2 = seed_snapshot("20261020_130002", status="failed", elapsed=0)
            seed_snapshot("20261020_130003", elapsed=-1.5)
            open_route("/finance/board")
            assert rows().count() == 3, f"快照表应 3 行: {rows().count()}"
            assert row("20261020_130001").locator(".el-tag", has_text="成功").count() > 0
            assert row("20261020_130002").locator(".el-tag", has_text="失败").count() > 0
            text = row("20261020_130003").inner_text()
            assert "-1.5" in text, f"负耗时未按值展示: {text[:120]}"
            assert "0" in row("20261020_130002").inner_text(), "0 耗时未展示"
            # 默认选中最新成功快照（20261020_130003），iframe 渲染
            assert select_value(page.locator(".card-head")) == "20261020_130003（成功）", \
                f"默认选中异常: {select_value(page.locator('.card-head'))!r}"
            frame = page.frame_locator("iframe.board-frame")
            frame.locator("h1").wait_for(state="visible", timeout=8000)
            assert "SEEDED-BOARD-OK" in frame.locator("h1").inner_text()
            # 失败快照在“查看”上禁用
            assert row("20261020_130002").locator("button", has_text="查看").is_disabled(), \
                "失败快照查看按钮应禁用"
            return "表格/状态/零负耗时/默认选中/iframe 均正常"
        record("BCE-B-UI-002", "快照列表、默认选中与 iframe", b_ui_snapshots)

        def b_ui_failed_only():
            reset_board()
            seed_snapshot("20261020_140000", status="failed")
            open_route("/finance/board")
            assert rows().count() == 1
            assert page.locator(".el-alert", has_text="请选择一个成功快照查看").count() > 0, \
                "仅失败快照时应提示选择成功快照"
            assert page.locator("iframe.board-frame").count() == 0, "无成功快照不应渲染 iframe"
            # 打开下拉：失败项应禁用
            page.locator(".card-head .el-select").click()
            page.wait_for_timeout(300)
            opt = page.locator(".el-select-dropdown:visible .el-select-dropdown__item").first
            assert "disabled" in (opt.get_attribute("class") or ""), "失败快照下拉项未禁用"
            page.keyboard.press("Escape")
            return "仅失败快照：提示卡 + 下拉禁用 + 无 iframe"
        record("BCE-B-UI-003", "仅失败快照的降级展示", b_ui_failed_only)

        def b_ui_run_success():
            reset_board()
            (BOARD_PIPE / "fail.flag").unlink(missing_ok=True)
            open_route("/finance/board")
            page.locator("button", has_text="运行管线刷新").click()
            wait_toast("运行完成", timeout=20000, kind="success")
            wait_rows(1, timeout=10000)
            assert "成功" in rows().first.inner_text()
            assert page.locator("iframe.board-frame").count() == 1, "运行成功后未渲染 iframe"
            frame = page.frame_locator("iframe.board-frame")
            frame.locator("h1").wait_for(state="visible", timeout=8000)
            assert "ISOLATED-STUB-BOARD" in frame.locator("h1").inner_text()
            assert board_snapshot_count() == 1
            return "UI 触发合成管线成功、列表刷新、iframe 加载"
        record("BCE-B-UI-004", "UI 运行管线成功（隔离合成）", b_ui_run_success)

        def b_ui_run_failure_refresh():
            """真实缺陷回归：管线失败会归档 failed 快照，但前端 catch 未刷新列表。

            修复已落在 BoardView.vue（失败且有响应时重拉）；dist 未重建前本用例保持失败。
            """
            reset_board()
            (BOARD_PIPE / "fail.flag").write_text("1", encoding="utf-8")
            try:
                open_route("/finance/board")
                page.locator("button", has_text="运行管线刷新").click()
                wait_toast("管线步骤失败", timeout=20000, kind="error")
                page.wait_for_timeout(1200)
                assert board_snapshot_count() == 1, "服务端应已归档 1 条 failed 快照"
                assert rows().count() == 1, "管线失败后快照列表未刷新（failed 快照不可见）"
                assert "失败" in rows().first.inner_text()
            finally:
                (BOARD_PIPE / "fail.flag").unlink(missing_ok=True)
            return "失败后 failed 快照即时出现在列表"
        record("BCE-B-UI-005", "管线失败后列表刷新", b_ui_run_failure_refresh)

        def b_ui_unavailable():
            reset_board()
            scripts = BOARD_PIPE / "scripts"
            off = BOARD_PIPE / "scripts.off"
            scripts.rename(off)
            try:
                open_route("/finance/board")
                page.locator("button", has_text="运行管线刷新").click()
                wait_toast("管线目录无效", timeout=15000, kind="error")
                page.wait_for_timeout(600)
                btn = page.locator("button", has_text="运行管线刷新").first
                assert "is-loading" not in (btn.get_attribute("class") or ""), "失败后按钮 loading 未复位"
                assert not page_errors[-3:], f"接口不可用出现未捕获异常: {page_errors[-3:]}"
            finally:
                off.rename(scripts)
            return "503 有错误提示、loading 复位、无未捕获异常"
        record("BCE-B-UI-006", "管线不可用时 UI 降级", b_ui_unavailable)

        def b_ui_xss():
            reset_board()
            evil_stamp = '<img id="xss-stamp" src=x onerror="window.__xss=1">'
            evil_html = ("<!doctype html><html><body><h1>XSS-HOST</h1>"
                         "<script>try{parent.__xss_html=1}catch(e){}</script></body></html>")
            seed_snapshot(evil_stamp, html=evil_html)
            open_route("/finance/board")
            page.wait_for_timeout(800)
            frame = page.frame_locator("iframe.board-frame")
            frame.locator("h1").wait_for(state="visible", timeout=8000)
            assert page.evaluate("window.__xss === undefined"), "stamp XSS 被执行"
            assert page.evaluate("window.__xss_html === undefined"), "iframe 脚本突破 sandbox"
            assert page.locator("#xss-stamp").count() == 0, "stamp 被当作 HTML 渲染"
            assert rows().first.inner_text().find("<img") >= 0, "stamp 未按字面展示"
            page.locator(".card-head .el-select").click()
            page.wait_for_timeout(300)
            assert page.locator("#xss-stamp").count() == 0, "下拉 option 出现 XSS 元素"
            page.keyboard.press("Escape")
            return "stamp 文本转义、iframe sandbox 阻断 parent 访问"
        record("BCE-B-UI-007", "快照 stamp 与快照 HTML XSS 防护", b_ui_xss)

        # ================= 现金流 API =================

        def c_api_empty():
            reset_cashflow()
            s, _, d = req("GET", "/api/finance/cashflow/runs")
            assert s == 200, f"空列表状态异常: {s} {d}"
            assert d["total"] == 0 and d["items"] == [], f"空列表内容异常: {s} {d}"
            # 分页契约（Task 4）：响应带 page/page_size，默认 1/20；老调用不传参仍 200
            assert d["page"] == 1 and d["page_size"] == 20, f"分页默认字段异常: {d}"
            return "空列表 total=0 + 分页默认 page/page_size"
        record("BCE-C-API-001", "现金流空列表", c_api_empty)

        def c_api_validation():
            reset_cashflow()
            s, _, d = cash_create(month=None)
            assert s == 422, f"缺 actual_month 应 422，实际 {s}: {str(d)[:160]}"
            s, _, d = cash_create(month=9, files=[])
            assert s == 422, f"缺文件应 422，实际 {s}: {str(d)[:160]}"
            s, _, d = cash_create(month=0)
            assert s == 422, f"month=0 应 422，实际 {s}"
            s, _, d = cash_create(month=13)
            assert s == 422, f"month=13 应 422，实际 {s}"
            s, _, d = cash_create(month="abc")
            assert s == 422, f"month=abc 应 422，实际 {s}"
            s, _, d = cash_create(month=1.5)
            assert s == 422, f"month=1.5 应 422，实际 {s}"
            return "缺参/月份边界/非整数均 422"
        record("BCE-C-API-002", "创建运行的必填与月份校验", c_api_validation)

        def c_api_file_validation():
            reset_cashflow()
            s, _, d = cash_create(files=[("files", "evil.txt", b"hello")])
            assert s == 400 and "不支持的文件类型" in d.get("detail", ""), f"非法类型应 400: {s} {d}"
            buf = io.BytesIO()
            import zipfile
            with zipfile.ZipFile(buf, "w") as zf:
                zf.writestr("../escape.xlsx", b"bad")
            s, _, d = cash_create(files=[("files", "pack.zip", buf.getvalue())])
            assert s == 400 and "非法路径" in d.get("detail", ""), f"zip-slip 应 400: {s} {d}"
            # 解压炸弹：声明解压后 300MB，实际内容很小
            import struct
            buf2 = io.BytesIO()
            with zipfile.ZipFile(buf2, "w") as zf:
                zf.writestr("big.xlsx", b"tiny")
            data = bytearray(buf2.getvalue())
            fake = 300 * 1024 * 1024
            struct.pack_into("<I", data, 22, fake)
            cd = data.find(b"PK\x01\x02")
            struct.pack_into("<I", data, cd + 24, fake)
            s, _, d = cash_create(files=[("files", "bomb.zip", bytes(data))])
            assert s == 400 and "解压后总大小" in d.get("detail", ""), f"解压炸弹应 400: {s} {d}"
            # 损坏 ZIP：BadZipFile 曾漏捕获成 500
            s, _, d = cash_create(files=[("files", "broken.zip", b"not-a-zip")])
            assert s == 400 and ("ZIP" in d.get("detail", "") or "损坏" in d.get("detail", "")), \
                f"损坏 ZIP 应 400: {s} {d}"
            # ZIP 条目名含 Windows 非法字符：按审计 A04 拒绝，不清洗后落盘
            buf3 = io.BytesIO()
            with zipfile.ZipFile(buf3, "w") as zf:
                zf.writestr("数据<>:|?/bad?.xlsx", b"entry")
            s, _, d = cash_create(files=[("files", "names.zip", buf3.getvalue())])
            assert s == 400 and "非法文件名" in d.get("detail", ""), \
                f"ZIP 非法条目名应 400，实际 {s}: {str(d)[:200]}"
            return "非法扩展名/zip-slip/炸弹/损坏 ZIP/非法条目名均 400"
        record("BCE-C-API-003", "上传文件类型与 ZIP 防护", c_api_file_validation)

        def c_api_create_and_detail():
            reset_cashflow()
            s, _, d = cash_create(month=9, files=[
                ("files", "bank.xlsx", BANK_XLSX),
                ("files", "template.xlsx", b"template-bytes"),
            ])
            assert s == 201, f"创建运行应 201，实际 {s}: {str(d)[:240]}"
            assert d["status"] == "uploaded" and d["actual_month"] == 9
            assert {f["name"] for f in d["input_files"]} == {"bank.xlsx", "template.xlsx"}
            rid = d["id"]
            s, _, detail = req("GET", f"/api/finance/cashflow/runs/{rid}")
            assert s == 200 and detail["id"] == rid and detail["input_files"] == d["input_files"]
            s, _, lst = req("GET", "/api/finance/cashflow/runs")
            assert s == 200 and lst["total"] == 1 and lst["items"][0]["id"] == rid
            return "创建/详情/列表字段一致"
        record("BCE-C-API-004", "创建运行与详情", c_api_create_and_detail)

        def c_api_filename_hygiene():
            reset_cashflow()
            bad = 'bad<>:|?.xlsx'
            s, _, d = cash_create(files=[("files", bad, b"x")])
            assert s == 201, f"含 Windows 非法字符的文件名应被清洗后接受，实际 {s}: {str(d)[:240]}"
            name = d["input_files"][0]["name"]
            assert not any(ch in name for ch in '<>:"|?*'), f"非法字符未清洗: {name!r}"
            long_name = "a" * 240 + ".xlsx"
            s, _, d2 = cash_create(files=[("files", long_name, b"x")])
            assert s == 201, f"超长文件名应被截断后接受，实际 {s}: {str(d2)[:240]}"
            name2 = d2["input_files"][0]["name"]
            assert name2.endswith(".xlsx") and len(name2) <= 160, f"超长名未按预期截断: {len(name2)}"
            # 磁盘上确实存在清洗后的文件
            files = list((FIN_DIR / "finance_cashflow" / "runs").rglob("*.xlsx"))
            assert len(files) == 2, f"落盘文件数异常: {files}"
            return f"非法字符清洗为 {name!r}，超长名截断为 {len(name2)} 字符"
        record("BCE-C-API-005", "文件名非法字符与超长名", c_api_filename_hygiene)

        def c_api_engine_failure():
            reset_cashflow()
            s, _, d = cash_create(files=[("files", "bank.xlsx", BANK_XLSX)])
            assert s == 201
            rid = d["id"]
            s, _, ex = req("POST", f"/api/finance/cashflow/runs/{rid}/execute", body={}, timeout=120)
            assert s == 503, f"真实引擎缺输入应 503，实际 {s}: {str(ex)[:240]}"
            assert "引擎执行失败" in ex.get("detail", ""), f"503 提示异常: {ex}"
            s, _, detail = req("GET", f"/api/finance/cashflow/runs/{rid}")
            assert s == 200 and detail["status"] == "failed", f"运行状态应 failed: {detail}"
            assert detail["error"], "failed 运行缺 error"
            assert detail["log_tail"] and ("未找到" in detail["log_tail"] or "Error" in detail["log_tail"]), \
                f"failed 运行缺日志尾部: {detail['log_tail'][:120]!r}"
            return "真实引擎失败 → 503 + failed + error + log_tail（未伪造成功）"
        record("BCE-C-API-006", "真实引擎失败优雅降级", c_api_engine_failure)

        def c_api_retry_and_404():
            reset_cashflow()
            s, _, d = cash_create()
            rid = d["id"]
            assert req("POST", f"/api/finance/cashflow/runs/{rid}/execute", body={}, timeout=120)[0] == 503
            s, _, ex = req("POST", f"/api/finance/cashflow/runs/{rid}/execute", body={}, timeout=120)
            assert s == 503, f"failed 运行应允许重跑（再次 503 而非 400），实际 {s}: {str(ex)[:160]}"
            assert req("POST", "/api/finance/cashflow/runs/99999/execute", body={})[0] == 404
            s, _, detail = req("GET", f"/api/finance/cashflow/runs/{rid}")
            assert detail["status"] == "failed"
            return "failed 可重跑；不存在运行 404"
        record("BCE-C-API-007", "失败运行重跑与 404", c_api_retry_and_404)

        def c_api_download_empty():
            reset_cashflow()
            s, _, d = cash_create()
            rid = d["id"]
            s, _, payload = req("GET", f"/api/finance/cashflow/runs/{rid}/download", raw=True)
            assert s == 409 and "暂无产物" in payload.decode("utf-8"), f"无产物下载应 409: {s}"
            s, _, payload = req("GET", f"/api/finance/cashflow/runs/{rid}/artifacts/nope.xlsx", raw=True)
            assert s == 404, f"不存在产物应 404: {s}"
            s, _, payload = req("GET", "/api/finance/cashflow/runs/99999/download", raw=True)
            assert s == 404, f"不存在运行下载应 404: {s}"
            return "无产物 409 / 产物不存在 404 / 运行不存在 404"
        record("BCE-C-API-008", "现金流下载异常分支", c_api_download_empty)

        def c_api_running_guard():
            reset_cashflow()
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            rid = db_exec(
                "INSERT INTO mod_cashflow_run (actual_month,status,input_files,artifacts,run_dir,"
                "log_tail,error,elapsed_sec,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (9, "running", "[]", "[]", "", "", "", 0.0, now, now),
            )
            s, _, d = req("POST", f"/api/finance/cashflow/runs/{rid}/execute", body={})
            assert s == 400 and "正在执行中" in d.get("detail", ""), f"running 重入应 400: {s} {d}"
            s, _, lst = req("GET", "/api/finance/cashflow/runs")
            assert lst["items"][0]["status"] == "running"
            return "running 状态 execute 返回 400"
        record("BCE-C-API-009", "running 状态执行守卫", c_api_running_guard)

        def c_api_list_pagination():
            """分页契约（Task 4）：列表按 page/page_size 分页且 total 为数据库真实计数，
            翻页可遍历全部记录——取代旧「固定 50 条上限、total=当页条数、超 50 条不可达」观察项。"""
            reset_cashflow()
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            for i in range(52):
                db_exec(
                    "INSERT INTO mod_cashflow_run (actual_month,status,input_files,artifacts,run_dir,"
                    "log_tail,error,elapsed_sec,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (9, "failed", "[]", "[]", "", "", "fixture", 0.0, now, now),
                )
            # 默认分页：第 1 页 20 条；total 是真实总数 52，不是当页条数
            s, _, d = req("GET", "/api/finance/cashflow/runs")
            assert s == 200 and len(d["items"]) == 20, f"默认每页应 20 条: {len(d['items'])}"
            assert d["total"] == 52, f"total 应为数据库真实计数 52: {d['total']}"
            assert d["page"] == 1 and d["page_size"] == 20, f"分页回显异常: {d}"
            # 第 2 页仍 20 条且 total 不变；第 3 页余 12 条——52 条全量可达
            s, _, d2 = req("GET", "/api/finance/cashflow/runs?page=2")
            assert s == 200 and len(d2["items"]) == 20 and d2["total"] == 52, f"第 2 页异常: {len(d2['items'])}/{d2['total']}"
            s, _, d3 = req("GET", "/api/finance/cashflow/runs?page=3")
            assert s == 200 and len(d3["items"]) == 12 and d3["total"] == 52, f"第 3 页异常: {len(d3['items'])}/{d3['total']}"
            s, _, d4 = req("GET", "/api/finance/cashflow/runs?page=4")
            assert s == 200 and d4["items"] == [] and d4["total"] == 52, f"越界页应空列表: {d4['items']}"
            # 三页 id 两两不同且各自倒序（按 id 倒序分页，无重复无遗漏）
            ids = [x["id"] for x in d["items"] + d2["items"] + d3["items"]]
            assert len(ids) == 52 and len(set(ids)) == 52, f"分页 id 重复或遗漏: {len(set(ids))}"
            for pg in (d["items"], d2["items"], d3["items"]):
                pg_ids = [x["id"] for x in pg]
                assert pg_ids == sorted(pg_ids, reverse=True), f"页内未按 id 倒序: {pg_ids[:5]}"
            # 分页参数边界：page>=1、1<=page_size<=100
            assert req("GET", "/api/finance/cashflow/runs?page=0")[0] == 422, "page=0 应 422"
            assert req("GET", "/api/finance/cashflow/runs?page_size=101")[0] == 422, "page_size=101 应 422"
            s, _, d100 = req("GET", "/api/finance/cashflow/runs?page_size=100")
            assert s == 200 and len(d100["items"]) == 52 and d100["total"] == 52, \
                f"page_size=100 应一次取满: {len(d100['items'])}"
            return "分页 20/20/12 + 真实 total=52 + 参数边界 + 倒序无重复"
        record("BCE-C-API-010", "现金流列表分页与真实 total", c_api_list_pagination)

        # ================= 现金流 UI =================

        def c_ui_empty_dialog():
            reset_cashflow()
            page_recon("/finance/cashflow", "cashflow-empty")
            assert page.locator(".el-table__empty-text").count() > 0
            assert "暂无运行记录" in page.locator(".el-table__empty-text").inner_text()
            page.locator("button", has_text="新建运行").click()
            page.wait_for_timeout(400)
            d = dlg()
            assert d.count() > 0, "新建运行对话框未打开"
            expected_month = datetime.now().month
            assert select_value(d) == f"{expected_month} 月", \
                f"默认月份异常: {select_value(d)!r}"
            assert d.locator("button", has_text="创建").is_disabled(), "未选文件时创建按钮应禁用"
            d.locator("button", has_text="取消").click()
            dlg().wait_for(state="hidden", timeout=5000)
            return f"空态 + 对话框默认 {expected_month} 月 + 未选文件禁用"
        record("BCE-C-UI-001", "现金流空态与新建对话框", c_ui_empty_dialog)

        def c_ui_create():
            reset_cashflow()
            open_route("/finance/cashflow")
            page.locator("button", has_text="新建运行").click()
            page.wait_for_timeout(400)
            d = dlg()
            choose_select(d, "9 月")
            tmp = RESULTS / "_probe_bank.xlsx"
            tmp.write_bytes(BANK_XLSX)
            try:
                d.locator("input[type=file]").set_input_files(str(tmp))
                page.wait_for_timeout(400)
                assert not d.locator("button", has_text="创建").is_disabled(), "选文件后创建按钮未启用"
                d.locator("button", has_text="创建").click()
                wait_toast("已创建", timeout=15000, kind="success")
                page.wait_for_timeout(800)
                assert dlg().count() == 0, "创建成功后对话框未关闭"
            finally:
                tmp.unlink(missing_ok=True)
            assert rows().count() == 1, f"新运行未出现在列表: {rows().count()}"
            text = rows().first.inner_text()
            assert "9" in text and "待执行" in text and "bank.xlsx" in text, f"行内容异常: {text[:160]}"
            assert req("GET", "/api/finance/cashflow/runs")[2]["total"] == 1
            return "UI 新建运行 → 列表待执行"
        record("BCE-C-UI-002", "UI 新建现金流运行", c_ui_create)

        def c_ui_execute_fail():
            reset_cashflow()
            rid = cash_create()[2]["id"]
            open_route("/finance/cashflow")
            assert rows().count() == 1
            assert rows().first.locator("button", has_text="打包下载").is_disabled(), "无产物时下载应禁用"
            rows().first.locator("button", has_text="执行").click()
            wait_toast("引擎执行失败", timeout=60000, kind="error")
            page.wait_for_timeout(1200)
            text = rows().first.inner_text()
            assert "失败" in text, f"执行失败后状态未刷新: {text[:160]}"
            assert rows().first.locator("button", has_text="日志").count() > 0, "失败行未出现日志按钮"
            rows().first.locator("button", has_text="日志").click()
            page.wait_for_timeout(400)
            log = dlg().locator("pre.log")
            assert log.count() > 0 and log.inner_text().strip(), "日志对话框无内容"
            assert "未找到" in log.inner_text() or "Error" in log.inner_text(), \
                f"日志缺少引擎错误: {log.inner_text()[:160]!r}"
            close_btn = dlg().locator("button", has_text="关闭")
            if close_btn.count():
                close_btn.first.click()
            else:
                page.keyboard.press("Escape")
            dlg().wait_for(state="hidden", timeout=5000)
            assert req("GET", f"/api/finance/cashflow/runs/{rid}")[2]["status"] == "failed"
            return "UI 执行真实引擎失败 → 503 提示 + 失败态 + 日志"
        record("BCE-C-UI-003", "UI 执行失败的终态与日志", c_ui_execute_fail)

        def c_ui_xss_filename():
            reset_cashflow()
            evil = "<img src=x onerror=window.__xss_c=1>.xlsx"
            s, _, d = cash_create(files=[("files", evil, b"x")])
            assert s == 201, f"创建失败: {s} {str(d)[:160]}"
            open_route("/finance/cashflow")
            page.wait_for_timeout(600)
            assert page.evaluate("window.__xss_c === undefined"), "文件名 XSS 被执行"
            assert page.locator("img[src=x]").count() == 0, "文件名被当作 HTML 渲染"
            assert rows().first.inner_text().find("<img") >= 0 or rows().first.inner_text().find("_img") >= 0, \
                f"文件名未按字面展示: {rows().first.inner_text()[:160]}"
            return "文件名按文本转义，无脚本执行"
        record("BCE-C-UI-004", "现金流文件名 XSS 防护", c_ui_xss_filename)

        def c_ui_list_fail():
            reset_cashflow()
            cash_create()
            open_route("/finance/cashflow")
            page_errors.clear()
            handler = lambda r: r.abort() if r.request.method == "GET" else r.continue_()
            page.route(re.compile(r"/api/finance/cashflow/runs(\?.*)?$"), handler)
            try:
                page.reload(wait_until="networkidle")
                page.wait_for_timeout(1200)
            finally:
                page.unroute(re.compile(r"/api/finance/cashflow/runs(\?.*)?$"), handler)
            assert wait_toast("网络异常", timeout=5000, kind="error")
            assert rows().count() == 0, "加载失败后不应保留旧行"
            assert page.locator(".el-table__empty-text").count() > 0
            assert not page_errors[-3:], f"列表加载失败出现未捕获异常: {page_errors[-3:]}"
            return "列表失败有错误提示、空态展示、无未捕获异常"
        record("BCE-C-UI-005", "现金流列表加载失败反馈", c_ui_list_fail)

        # ================= 费用 API =================

        def e_api_empty_rules():
            reset_expense()
            s, _, d = req("GET", "/api/finance/expense/runs")
            assert s == 200 and d["total"] == 0 and d["items"] == []
            s, _, rules = req("GET", "/api/finance/expense/rules/summary")
            assert s == 200, f"规则摘要应 200: {s} {str(rules)[:160]}"
            assert rules["total_categories"] > 0 and rules["total_keywords"] > 0
            assert rules["rules_file"].endswith("classification_rules.json")
            assert Path(rules["rules_file"]).is_file(), "规则文件路径不存在"
            s, _, reloaded = req("POST", "/api/finance/expense/rules/reload", body={})
            assert s == 200 and reloaded["ok"] is True
            assert reloaded["summary"]["total_categories"] == rules["total_categories"]
            return f"规则 {rules['total_categories']} 类 / {rules['total_keywords']} 关键词，重载一致"
        record("BCE-E-API-001", "费用空列表与规则摘要/重载", e_api_empty_rules)

        def e_api_upload_validation():
            reset_expense()
            s, _, d = post_files("/api/finance/expense/classify", [])
            assert s == 422, f"缺文件应 422，实际 {s}"
            s, _, d = exp_post(filename="notes.txt", content=b"hello")
            assert s == 400 and "格式" in d.get("detail", ""), f"非法类型应 400: {s} {d}"
            s, _, d = exp_post(filename="empty.xlsx", content=b"")
            assert s == 400 and "空" in d.get("detail", ""), f"空文件应 400: {s} {d}"
            big = b"x" * (20 * 1024 * 1024 + 1)
            s, _, d = exp_post(filename="big.xlsx", content=big)
            assert s == 400 and "上限" in d.get("detail", ""), f"超限应 400: {s} {str(d)[:160]}"
            return "缺文件 422 / 类型 400 / 空文件 400 / 20MB 超限 400"
        record("BCE-E-API-002", "上传校验（缺文件/类型/空/超限）", e_api_upload_validation)

        def e_api_bad_xlsx_failed():
            reset_expense()
            s, _, d = exp_post(filename="普通表.xlsx", content=b"not-an-xlsx")
            assert s == 400, f"非法 xlsx 应 400，实际 {s}: {str(d)[:240]}"
            assert "银行明细账" in d.get("detail", "") or "读取失败" in d.get("detail", ""), \
                f"400 提示异常: {d}"
            # 行数/内容护栏在写运行记录前生效：非法内容不落 running/failed 半成品
            s, _, lst = req("GET", "/api/finance/expense/runs")
            assert s == 200 and lst["total"] == 0 and lst["items"] == [], \
                f"非法内容被写运行记录: {lst}"
            inbox = FIN_DIR / "finance_expense" / "inbox"
            assert not inbox.exists() or not any(inbox.iterdir()), "失败上传文件未清理，inbox 留孤儿"
            return "非法内容 400、不写运行记录、inbox 清理"
        record("BCE-E-API-003", "非法 xlsx 前置拒绝与清理", e_api_bad_xlsx_failed)

        def e_api_classify_success():
            reset_expense()
            run = exp_classify_ok()
            assert run["status"] == "success" and run["total_rows"] == 7
            assert 0 < run["matched_rows"] < 7, f"命中数异常: {run['matched_rows']}"
            s, _, detail = req("GET", f"/api/finance/expense/runs/{run['id']}")
            assert s == 200 and len(detail["rows"]) == 7 and len(detail["stats"]) >= 5
            cats = {r["category"] for r in detail["rows"]}
            assert {"人工成本", "财务费用", "公共事业费", "未分类"} <= cats, f"分类异常: {cats}"
            assert sum(r["count"] for r in detail["stats"]) == 7
            assert abs(sum(r["amount"] for r in detail["stats"]) - 8275.05) < 0.01
            by_cat = {r["category"]: r for r in detail["rows"]}
            assert by_cat["未分类"]["hit"] is False and by_cat["人工成本"]["hit"] is True
            assert Path(FIN_DIR / run["artifact_path"]).is_file(), "产物文件不存在"
            return f"7 行/命中 {run['matched_rows']} 行，统计=明细，产物落盘"
        record("BCE-E-API-004", "真实引擎分类与统计对拍", e_api_classify_success)

        def e_api_download():
            reset_expense()
            run = exp_classify_ok()
            s, h, payload = req("GET", f"/api/finance/expense/runs/{run['id']}/download", raw=True)
            assert s == 200 and payload[:2] == b"PK", f"下载非 xlsx: {s} {payload[:8]!r}"
            assert "spreadsheetml" in h.get("content-type", ""), f"Content-Type 异常: {h.get('content-type')}"
            cd = h.get("content-disposition", "")
            assert "filename*=" in cd and "%E8%B4%B9%E7%94%A8%E5%88%86%E7%B1%BB%E7%BB%93%E6%9E%9C" in cd.upper(), \
                f"中文文件名头异常: {cd}"
            assert req("GET", "/api/finance/expense/runs/99999/download", raw=True)[0] == 404
            return "下载 xlsx 魔数 + RFC5987 中文名 + 404"
        record("BCE-E-API-005", "分类结果下载", e_api_download)

        def e_api_pagination():
            reset_expense()
            ids = []
            for i in range(12):
                ids.append(exp_classify_ok(f"合成明细账{i:02d}.xlsx")["id"])
            s, _, p1 = req("GET", "/api/finance/expense/runs", data=None)
            assert s == 200 and p1["total"] == 12 and len(p1["items"]) == 10
            assert [x["id"] for x in p1["items"]] == sorted(ids, reverse=True)[:10]
            s, _, p2 = req("GET", "/api/finance/expense/runs?page=2&page_size=10")
            assert s == 200 and p2["total"] == 12 and len(p2["items"]) == 2
            s, _, p3 = req("GET", "/api/finance/expense/runs?page=3&page_size=10")
            assert s == 200 and p3["items"] == [] and p3["total"] == 12
            for q in ("page=0", "page=-1", "page_size=0", "page_size=101"):
                assert req("GET", f"/api/finance/expense/runs?{q}")[0] == 422, f"{q} 应 422"
            assert req("GET", "/api/finance/expense/runs?page_size=100")[0] == 200
            assert req("GET", "/api/finance/expense/runs/99999")[0] == 404
            return "12 条分页 10+2、越界页空、参数 422、详情 404"
        record("BCE-E-API-006", "费用分页与边界", e_api_pagination)

        def e_api_path_traversal():
            reset_expense()
            run = exp_classify_ok(filename="../../穿越/evil.xlsx")
            assert run.get("original_filename") == "evil.xlsx", f"路径未取 basename: {run!r}"
            inbox = FIN_DIR / "finance_expense" / "inbox"
            stored = list(inbox.glob("*.xlsx"))
            assert stored, f"上传未落 inbox: run={run!r}, stored={stored!r}"
            assert all(".." not in p.name and p.name != "evil.xlsx" for p in stored), \
                f"落盘名异常: run={run!r}, stored={stored!r}"
            return "文件名仅取 basename，落盘 uuid"
        record("BCE-E-API-007", "上传文件名路径穿越", e_api_path_traversal)

        # ================= 费用 UI =================

        def e_ui_empty():
            reset_expense()
            page_recon("/finance/expense", "expense-empty")
            assert page.locator(".el-descriptions").count() > 0, "规则摘要未渲染"
            assert page.locator(".cat-tag").count() > 0, "规则分类标签未渲染"
            assert page.locator(".el-upload").count() == 1, "上传区缺失"
            assert page.locator(".el-table__empty-text").count() > 0, "运行列表空态缺失"
            assert page.locator("button", has_text="执行分类").is_disabled(), "未选文件时执行按钮应禁用"
            assert not page_errors[-3:], f"空态出现未捕获异常: {page_errors[-3:]}"
            return "规则摘要/分类标签/上传区/空态/禁用按钮正常"
        record("BCE-E-UI-001", "费用页空态与规则展示", e_ui_empty)

        def e_ui_pagination():
            reset_expense()
            for i in range(12):
                exp_classify_ok(f"分页明细账{i:02d}.xlsx")
            open_route("/finance/expense")
            assert "共 12 条" in page.locator(".el-pagination__total").inner_text(), \
                f"总数异常: {page.locator('.el-pagination__total').inner_text()}"
            assert rows().count() == 10, f"首页应 10 行: {rows().count()}"
            page.locator(".el-pager li", has_text="2").first.click()
            page.wait_for_timeout(900)
            assert rows().count() == 2, f"第 2 页应 2 行: {rows().count()}"
            assert page.locator(".el-pager li.is-active").inner_text() == "2"
            return "12 条分页 10+2、总数联动"
        record("BCE-E-UI-002", "费用列表分页", e_ui_pagination)

        def e_ui_wrong_type():
            reset_expense()
            open_route("/finance/expense")
            tmp = RESULTS / "_probe_notes.txt"
            tmp.write_text("hello", encoding="utf-8")
            try:
                page.locator(".el-upload input[type=file]").set_input_files(str(tmp))
                page.wait_for_timeout(400)
                page.locator("button", has_text="执行分类").click()
                wait_toast("仅支持", timeout=10000, kind="error")
                page.wait_for_timeout(600)
            finally:
                tmp.unlink(missing_ok=True)
            assert "/finance/expense/runs/" not in page.url, "非法类型失败后不应跳转结果页"
            assert page.locator("button", has_text="执行分类").count() == 1, "失败后按钮应恢复"
            assert not page_errors[-3:], f"非法上传出现未捕获异常: {page_errors[-3:]}"
            return "非法类型 400 有提示、不跳转、按钮恢复"
        record("BCE-E-UI-003", "UI 非法文件类型上传", e_ui_wrong_type)

        def e_ui_classify_result():
            reset_expense()
            open_route("/finance/expense")
            tmp = RESULTS / "_probe_bank_ui.xlsx"
            tmp.write_bytes(BANK_XLSX)
            try:
                page.locator(".el-upload input[type=file]").set_input_files(str(tmp))
                page.wait_for_timeout(400)
                page.locator("button", has_text="执行分类").click()
                wait_toast("分类完成", timeout=30000, kind="success")
                page.wait_for_timeout(1000)
            finally:
                tmp.unlink(missing_ok=True)
            assert re.search(r"#/finance/expense/runs/\d+$", page.url), f"未跳转结果页: {page.url}"
            assert page.locator(".el-card").first.locator("text=分类结果").count() > 0, "结果页标题缺失"
            assert page.locator(".el-descriptions").count() > 0
            assert page.locator(".el-table tr.el-table__row").count() == 7, \
                f"明细表应 7 行: {page.locator('.el-table tr.el-table__row').count()}"
            # ECharts 画布真实渲染
            page.locator(".el-card canvas").first.wait_for(state="visible", timeout=8000)
            assert page.locator(".el-card canvas").count() >= 1, "饼图 canvas 未渲染"
            return "跳转结果页、描述/7 行明细/饼图 canvas 正常"
        record("BCE-E-UI-004", "UI 分类成功与结果页图表", e_ui_classify_result)

        def e_ui_unmatched_switch():
            reset_expense()
            run = exp_classify_ok()
            open_route(f"/finance/expense/runs/{run['id']}")
            page.locator("tr.el-table__row").first.wait_for(state="visible", timeout=8000)
            assert page.locator("tr.el-table__row").count() == 7
            page.locator(".el-switch").click()
            page.wait_for_timeout(600)
            assert page.locator("tr.el-table__row").count() == 1, \
                f"只看未命中应 1 行: {page.locator('tr.el-table__row').count()}"
            assert "未分类" in page.locator("tr.el-table__row").first.inner_text()
            page.locator(".el-switch").click()
            page.wait_for_timeout(600)
            assert page.locator("tr.el-table__row").count() == 7
            return "只看未命中 7→1→7，分类正确"
        record("BCE-E-UI-005", "结果页未命中筛选", e_ui_unmatched_switch)

        def e_ui_bad_id():
            open_route("/finance/expense/runs/99999")
            assert page.locator(".el-result").count() > 0, "无效运行未渲染错误结果"
            assert "运行记录不存在或加载失败" in page.locator(".el-result").inner_text()
            assert not page_errors[-3:], f"无效 id 出现未捕获异常: {page_errors[-3:]}"
            return "无效 id → el-result 错误态，无未捕获异常"
        record("BCE-E-UI-006", "结果页无效运行 id", e_ui_bad_id)

        def e_ui_xss():
            reset_expense()
            evil_name = "<img src=x onerror=window.__xss_name=1>.xlsx"
            run = exp_classify_ok(filename=evil_name)
            # 再插入一条带 XSS 摘要的明细（模拟真实账簿摘要含尖括号），验证表格转义
            db_exec(
                "INSERT INTO mod_expense_row (run_id,row_no,summary,amount,category,hit)"
                " VALUES (?,?,?,?,?,?)",
                (run["id"], 99, '<img src=y onerror="window.__xss_sum=1">', 12.34, "未分类", 0),
            )
            open_route(f"/finance/expense/runs/{run['id']}")
            page.locator("tr.el-table__row").first.wait_for(state="visible", timeout=8000)
            assert page.evaluate("window.__xss_name === undefined"), "文件名 XSS 被执行"
            assert page.evaluate("window.__xss_sum === undefined"), "摘要 XSS 被执行"
            assert page.locator("img[src=x], img[src=y]").count() == 0, "表格内出现 XSS img 元素"
            assert page.locator("tr.el-table__row").count() == 8, "注入的明细行未展示"
            return "文件名/摘要按字面转义，无脚本执行"
        record("BCE-E-UI-007", "结果页文件名与摘要 XSS 防护", e_ui_xss)

        def e_ui_rules_unavailable():
            reset_expense()
            handler = lambda r: r.abort() if "/rules/summary" in r.request.url else r.continue_()
            page.route("**/api/finance/expense/rules/summary", handler)
            try:
                open_route("/finance/expense")
                page.wait_for_timeout(1000)
                alert = page.locator(".el-alert--error")
                assert alert.count() > 0, "规则摘要失败未显示错误告警"
                assert "费用分类引擎不可用" in alert.inner_text()
                # 选一个合法文件后，引擎错误仍应禁用执行
                tmp = RESULTS / "_probe_bank_disabled.xlsx"
                tmp.write_bytes(BANK_XLSX)
                try:
                    page.locator(".el-upload input[type=file]").set_input_files(str(tmp))
                    page.wait_for_timeout(400)
                    assert page.locator("button", has_text="执行分类").is_disabled(), \
                        "引擎不可用时执行按钮未禁用"
                finally:
                    tmp.unlink(missing_ok=True)
                assert not page_errors[-3:], f"规则摘要失败出现未捕获异常: {page_errors[-3:]}"
            finally:
                page.unroute("**/api/finance/expense/rules/summary", handler)
            return "规则摘要不可用 → 错误告警 + 执行禁用 + 无未捕获异常"
        record("BCE-E-UI-008", "规则接口不可用时降级", e_ui_rules_unavailable)

        # ================= 页面级收尾侦察 =================

        def x_page_recon():
            # 费用结果页已由 BCE-E-UI-004 覆盖；这里补一次干净加载的三页侦察
            reset_board()
            reset_cashflow()
            reset_expense()
            for route, name in (("/finance/board", "board-final"),
                                ("/finance/cashflow", "cashflow-final"),
                                ("/finance/expense", "expense-final")):
                page_recon(route, name)
                assert not page_errors[-3:], f"{route} 出现未捕获异常: {page_errors[-3:]}"
            return "三页干净加载无未捕获异常"
        record("BCE-X-001", "三页最终页面侦察", x_page_recon)

        browser.close()

    # ---------- 汇总输出 ----------
    print("=" * 70)
    passed = sum(1 for r in results if r["passed"])
    print(f"total={len(results)} passed={passed} failed={len(results) - passed}")
    print(f"console_errors={len(console_errors)} page_errors={len(page_errors)}")
    for e in page_errors[:10]:
        print(f"  !! pageerror: {e[:200]}")

    raw = {
        "area": AREA,
        "base": BASE,
        "started_at": STARTED_AT,
        "finished_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "pages": pages,
        "tests": results,
        "meta": {
            "board_pipeline": "isolated synthetic fixture (finance-bce-board-pipeline)；真实微信管线未执行",
            "cashflow_engine": "真实 V1 引擎（合成输入，失败路径）",
            "expense_engine": "真实费用分类引擎（合成银行明细账）",
            "dist_precondition": "探针跑在构建产物上：frontend/dist 必须由当前 src 重新构建"
                                "（启动工作台.bat 的 needs-build 检查会提示），陈旧 dist 会让前端修复类用例失败",
            "observed_gaps": [],
        },
    }
    out = RESULTS / f"{AREA}.json"
    out.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"results -> {out}")
finally:
    server.terminate()
    try:
        server.wait(timeout=10)
    except Exception:
        server.kill()
    log_fh.close()
