# -*- coding: utf-8 -*-
"""交互健壮性深度探测（端口 8240，独立库隔离，正式可重复运行资产）。

口径说明（重要）：本探针是**自动化交互走查**，不是真人可用性测试。
它验证的是「界面在快速点击 / 请求失败 / 竞态下是否给出正确且一致的反应」，
所有结论来自程序化断言（DOM 状态、请求计数、后端真实数据、前端报错采集接口），
不含任何用户访谈、原话或主观评分。真人可用性测试需要另行按 /可用性测试 流程招募参与者。

它补的是既有探针没覆盖的一类缺陷——提交竞态与失败反馈：
- INTX-001~003 连点「保存」只落 1 条数据（任务 / 日程 / 定时任务三个对话框视图）
- INTX-004 保存失败：对话框保留、提交锁复位、重试即成功（不出现「静默失败 + 数据重复」）
- INTX-005 连点行内「完成」只发 1 次 PUT（请求计数，不是猜）
- INTX-006 月历切月失败：清空旧月份数据（标题与数据不错位）且能恢复
- INTX-007 列表加载失败：错误提示 + loading 复位，且不产生未捕获异常
- INTX-008 提交锁期间按钮进入 loading（禁用态），给用户明确的「已在处理」反馈
- INTX-009 全流程零未捕获 Promise 拒绝、零前端报错上报

用法：
    D:/DevEnv/runtime/python/python.exe dev/e2e/probes/interaction-ux.py
结果：
    dev/e2e/probe_results/interaction-ux.json
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
BACKEND = ROOT / "backend"
RESULTS = ROOT / "dev" / "e2e" / "probe_results"
AREA = "interaction-ux"
DB = RESULTS / f"{AREA}.db"
FIN_DIR = RESULTS / f"{AREA}-fin"
DEBUG_DIR = RESULTS / f"{AREA}-debug"
SHOTS = RESULTS / AREA / "shots"
LOG = RESULTS / f"{AREA}.server.log"
PORT = 8240
BASE = f"http://127.0.0.1:{PORT}"
STARTED_AT = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

RESULTS.mkdir(parents=True, exist_ok=True)
SHOTS.mkdir(parents=True, exist_ok=True)
for _p in (DB,):
    if _p.exists():
        _p.unlink()
for _d in (FIN_DIR, DEBUG_DIR):
    if _d.exists():
        shutil.rmtree(_d)

env = os.environ.copy()
env["DATABASE_URL"] = f"sqlite:///{DB.as_posix()}"
env["FINANCE_DATA_DIR"] = str(FIN_DIR)
env["DEBUG_DATA_DIR"] = str(DEBUG_DIR)
env["PYTHONIOENCODING"] = "utf-8"
env["SEED_ON_STARTUP"] = "0"  # 空库起点，所有计数断言才确定
env["LLM_API_KEY"] = ""
env["LLM_BASE_URL"] = ""
env["LLM_MODEL"] = ""
env["LLM_PROVIDER"] = ""

log_fh = open(LOG, "w", encoding="utf-8")
server = subprocess.Popen(
    [str(BACKEND / "venv" / "Scripts" / "python.exe"), "-m", "uvicorn",
     "app.main:app", "--host", "127.0.0.1", "--port", str(PORT)],
    cwd=str(BACKEND), env=env, stdout=log_fh, stderr=log_fh,
)

results = []
console_errors = []
page_errors = []
req_log = []      # (method, url)：用于「连点只发一次请求」这类硬断言
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


def wait_ready(timeout=90):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            urllib.request.urlopen(f"{BASE}/api/health", timeout=2)
            return True
        except Exception:
            time.sleep(1)
    return False


def wait_until(fn, timeout=8000, interval=120, message="等待超时"):
    deadline = time.time() + timeout / 1000.0
    last = None
    while time.time() < deadline:
        try:
            v = fn()
            if v:
                return v
        except Exception as e:
            last = e
        time.sleep(interval / 1000.0)
    raise AssertionError(f"{message}（最后一次异常：{last}）" if last else message)


def api(method, path, **kw):
    if "json" in kw:
        kw["data"] = json.dumps(kw.pop("json"))
        headers = kw.pop("headers", {})
        headers["Content-Type"] = "application/json"
        kw["headers"] = headers
    return page.request.fetch(f"{BASE}{path}", method=method, **kw)


def client_error_count():
    return api("GET", "/api/debug/client-errors").json()["total"]


def uncaught_since(mark):
    return [t for t in console_errors[mark:] if "Uncaught" in t or "unhandled" in t.lower()]


def fatal_since(mark):
    return page_errors[mark:]


def tasks_total():
    return api("GET", "/api/tasks", params={"page_size": 1}).json()["total"]


def schedules_total(start, end):
    body = api("GET", "/api/schedules", params={"start": start, "end": end, "page_size": 100}).json()
    return body["total"]


def jobs_total():
    return len(api("GET", "/api/jobs").json())


def reset_all():
    """清空任务 / 日程 / 定时任务，并复位前端报错采集与请求日志。"""
    for path, key in (("/api/tasks", "items"),):
        page_no = 1
        while True:
            body = api("GET", path, params={"page": page_no, "page_size": 100}).json()
            for it in body[key]:
                api("DELETE", f"{path}/{it['id']}")
            if not body[key]:
                break
            page_no += 1
    today = datetime.now().date()
    start = (today - timedelta(days=90)).isoformat()
    end = (today + timedelta(days=90)).isoformat()
    body = api("GET", "/api/schedules", params={"start": start, "end": end, "page_size": 100}).json()
    for it in body["items"]:
        api("DELETE", f"/api/schedules/{it['id']}")
    for job in api("GET", "/api/jobs").json():
        api("DELETE", f"/api/jobs/{job['id']}")
    api("DELETE", "/api/debug/client-errors")
    req_log.clear()
    return True


# ---------- 视图打开与通用选择器 ----------
def open_view(route, selector):
    page.goto(f"{BASE}/#{route}", wait_until="networkidle", timeout=30000)
    page.reload(wait_until="networkidle", timeout=30000)
    page.wait_for_selector(selector, timeout=15000)
    page.wait_for_timeout(500)


def open_tasks():
    open_view("tasks", ".el-table")


def open_schedules():
    open_view("schedules", ".el-calendar")


def open_jobs():
    open_view("jobs", ".el-table")


def dlg():
    return page.locator(".el-dialog:visible")


def save_btn():
    return dlg().locator(".el-dialog__footer button", has_text="保存").last


def title_input(placeholder):
    return dlg().get_by_placeholder(placeholder)


def rows():
    return page.locator("tr.el-table__row:visible")


def row(text):
    return page.locator("tr.el-table__row", has_text=text).first


def dots():
    return page.locator(".cal-dots .dot")


def month_button(kind):
    """日历头部的月份切换按钮（kind: prev / today / next）。

    文案来自 Element Plus locale（zh-cn 是「上个月/今天/下个月」，en 是
    「Previous Month/Today/Next Month」），按文本匹配会随语言与版本失效；
    模板里三个 el-button 的顺序是固定的，所以按下标取。
    """
    btns = page.locator(".el-calendar__button-group .el-button")
    if btns.count() < 3:
        try:
            header = page.locator(".el-calendar__header").first.inner_text().strip()
        except Exception:
            header = "<日历头部未渲染>"
        raise AssertionError(f"日历头部没有月份切换按钮组（当前头部：{header!r}）")
    return btns.nth({"prev": 0, "today": 1, "next": 2}[kind])


def err_toasts():
    return page.locator(".el-message--error").count()


def clear_toasts():
    page.evaluate("document.querySelectorAll('.el-message').forEach(e => e.remove())")


def double_click_save(times=6, gap_ms=60):
    """连点保存：模拟真实双击（以及手抖多点的更坏情况）。"""
    btn = save_btn()
    for i in range(times):
        try:
            btn.click(timeout=1500)
        except Exception:
            pass  # 按钮进入 loading 被禁用时点不动，正是期望行为
        page.wait_for_timeout(gap_ms)


def requests_matching(method, pattern, mark=0):
    """req_log 里第 mark 条之后匹配到的请求 URL。

    mark 必须是「len(req_log) 快照」而不是过滤后列表的下标——先过滤再按快照切片
    会把窗口里的请求整段丢掉（这是本探针第一版踩过的坑）。
    """
    rx = re.compile(pattern)
    return [u for (m, u) in req_log[mark:] if m == method and rx.search(u)]


def count_since(method, pattern, mark):
    return len(requests_matching(method, pattern, mark))


try:
    if not wait_ready():
        print("FATAL: server not ready")
        sys.exit(2)

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: page_errors.append(str(e)))
        page.on(
            "request",
            lambda r: req_log.append((r.method, r.url)) if "/api/" in r.url else None,
        )

        # ---------- INTX-001 任务：连点保存只建 1 条 ----------
        def probe_task_double_submit():
            reset_all()
            open_tasks()
            err0, c0, u0 = client_error_count(), len(console_errors), len(page_errors)
            page.locator("button", has_text="新建任务").first.click()
            dlg().wait_for(state="visible", timeout=8000)
            title_input("例如：整理本月进项发票").fill("竞态-任务连点保存")
            mark = len(req_log)
            double_click_save()
            wait_until(lambda: tasks_total() == 1, timeout=8000, message="连点保存后任务数量不是 1")
            fatal = fatal_since(u0)
            uncaught = uncaught_since(c0)
            assert not fatal, f"连点保存出现未捕获异常: {fatal[:2]}"
            assert not uncaught, f"连点保存产生未捕获 Promise 拒绝: {uncaught[:2]}"
            assert client_error_count() == err0, "连点保存触发前端报错采集"
            assert count_since("POST", r"/api/tasks", mark) == 1, (
                f"连点保存发出 {count_since('POST', r'/api/tasks', mark)} 次 POST（应为 1 次）"
            )
            return f"6 连点 → 1 条任务 / 1 次 POST，后端真实数据一致"
        record("INTX-001", "任务新建：连点保存只落 1 条", probe_task_double_submit)

        # ---------- INTX-002 日程：连点保存只建 1 条 ----------
        def probe_schedule_double_submit():
            reset_all()
            open_schedules()
            today = datetime.now().date().isoformat()
            c0, u0 = len(console_errors), len(page_errors)
            page.locator("button", has_text="新建日程").first.click()
            dlg().wait_for(state="visible", timeout=8000)
            title_input("例如：月度经营分析会").fill("竞态-日程连点保存")
            mark = len(req_log)
            double_click_save()
            wait_until(lambda: schedules_total(today, today) == 1, timeout=8000,
                       message=f"连点保存后当天日程数量不是 1（实际 {schedules_total(today, today)}）")
            assert not fatal_since(u0), "连点保存日程出现未捕获异常"
            assert not uncaught_since(c0), "连点保存日程产生未捕获 Promise 拒绝"
            assert count_since("POST", r"/api/schedules", mark) == 1, (
                f"连点保存日程发出了 {count_since('POST', r'/api/schedules', mark)} 次 POST（应为 1 次）"
            )
            return "6 连点 → 当天 1 条日程 / 1 次 POST"
        record("INTX-002", "日程新建：连点保存只落 1 条", probe_schedule_double_submit)

        # ---------- INTX-003 定时任务：连点保存只建 1 条 ----------
        def probe_job_double_submit():
            reset_all()
            open_jobs()
            c0, u0 = len(console_errors), len(page_errors)
            page.locator("button", has_text="新建任务").first.click()
            dlg().wait_for(state="visible", timeout=8000)
            title_input("例如：每周税种测算提醒").fill("竞态-定时任务连点保存")
            dlg().get_by_placeholder("到点后交给 Agent 执行的内容，例如：汇总本周新增进项发票并核对税率").fill("探针用提示词")
            mark = len(req_log)
            double_click_save()
            wait_until(lambda: jobs_total() == 1, timeout=8000, message=f"连点保存后定时任务数量不是 1（实际 {jobs_total()}）")
            assert not fatal_since(u0), "连点保存定时任务出现未捕获异常"
            assert not uncaught_since(c0), "连点保存定时任务产生未捕获 Promise 拒绝"
            assert count_since("POST", r"/api/jobs", mark) == 1, (
                f"连点保存定时任务发出了 {count_since('POST', r'/api/jobs', mark)} 次 POST（应为 1 次）"
            )
            return "6 连点 → 1 条定时任务 / 1 次 POST"
        record("INTX-003", "定时任务新建：连点保存只落 1 条", probe_job_double_submit)

        # ---------- INTX-004 保存失败：对话框保留 + 锁复位 + 重试成功 ----------
        def probe_save_fail_then_retry():
            reset_all()
            open_tasks()
            err0, c0, u0 = client_error_count(), len(console_errors), len(page_errors)
            state = {"n": 0}

            def handler(route):
                state["n"] += 1
                if state["n"] == 1:
                    route.abort()
                else:
                    route.continue_()

            page.route(re.compile(r"/api/tasks$"), handler)
            page.locator("button", has_text="新建任务").first.click()
            dlg().wait_for(state="visible", timeout=8000)
            title_input("例如：整理本月进项发票").fill("异常-保存失败重试")
            save_btn().click(timeout=3000)
            wait_until(lambda: err_toasts() > 0, message="保存失败时前端无任何错误提示（静默失败）")
            assert dlg().count() > 0 and dlg().first.is_visible(), "保存失败后对话框被关掉了（用户输入丢失）"
            btn = save_btn()
            assert "is-loading" not in (btn.first.get_attribute("class") or ""), "保存失败后按钮仍停在 loading（锁未复位，无法重试）"
            clear_toasts()
            btn.first.click(timeout=3000)
            wait_until(lambda: tasks_total() == 1, timeout=8000, message="重试保存后仍未创建成功")
            wait_until(lambda: not dlg().first.is_visible(), message="重试成功后对话框未关闭")
            page.unroute(re.compile(r"/api/tasks$"), handler)
            assert not fatal_since(u0), f"保存失败重试链路出现未捕获异常: {fatal_since(u0)[:2]}"
            assert not uncaught_since(c0), f"保存失败重试链路产生未捕获 Promise 拒绝: {uncaught_since(c0)[:2]}"
            assert client_error_count() == err0, "保存失败重试触发前端报错采集"
            return "失败有提示、对话框保留、锁复位、重试成功且只 1 条"
        record("INTX-004", "保存失败：反馈 + 可重试（不丢输入、不重复建）", probe_save_fail_then_retry)

        # ---------- INTX-005 行内「完成」连点只发 1 次 PUT ----------
        def probe_complete_debounce():
            reset_all()
            api("POST", "/api/tasks", json={"title": "竞态-完成连点"})
            open_tasks()
            mark = len(req_log)
            c0, u0 = len(console_errors), len(page_errors)
            btn = row("竞态-完成连点").locator("button", has_text="完成")
            for _ in range(6):
                try:
                    btn.first.click(timeout=1200)
                except Exception:
                    pass
                page.wait_for_timeout(60)
            wait_until(lambda: count_since("PUT", r"/api/tasks/\d+", mark) >= 1, message="连点完成后没有任何 PUT")
            puts = count_since("PUT", r"/api/tasks/\d+", mark)
            page.wait_for_timeout(1200)
            puts_after = count_since("PUT", r"/api/tasks/\d+", mark)
            body = api("GET", "/api/tasks", params={"page_size": 50}).json()
            target = [t for t in body["items"] if t["title"] == "竞态-完成连点"]
            assert target and target[0]["status"] == "done", "连点后任务未变为已完成"
            assert puts_after <= 2, f"连点「完成」发出 {puts_after} 次 PUT（首次 {puts}），在途标记未生效"
            assert not fatal_since(u0), "完成连点出现未捕获异常"
            assert not uncaught_since(c0), "完成连点产生未捕获 Promise 拒绝"
            return f"6 连点 → ≤2 次 PUT（首击 {puts} 次），状态最终 done"
        record("INTX-005", "行内完成：连点不重复发请求", probe_complete_debounce)

        # ---------- INTX-006 切月失败：清空旧月份且可恢复 ----------
        def probe_month_switch_fail():
            reset_all()
            today = datetime.now()
            nxt = today + timedelta(days=31)
            api("POST", "/api/schedules", json={
                "title": "月份隔离-本月", "start_time": today.strftime("%Y-%m-%dT10:00:00"),
                "end_time": today.strftime("%Y-%m-%dT11:00:00")})
            api("POST", "/api/schedules", json={
                "title": "月份隔离-下月", "start_time": nxt.strftime("%Y-%m-%dT10:00:00"),
                "end_time": nxt.strftime("%Y-%m-%dT11:00:00")})
            open_schedules()
            c0, u0 = len(console_errors), len(page_errors)
            wait_until(lambda: dots().count() > 0, message="本月日历没有出现任何日程圆点（前置数据未生效）")
            dots_now = dots().count()
            err0 = err_toasts()
            handler = lambda r: r.abort() if r.request.method == "GET" else r.continue_()
            page.route(re.compile(r"/api/schedules\?"), handler)
            month_button("next").click()
            wait_until(lambda: err_toasts() > err0, message="切月失败时前端无任何错误提示")
            page.unroute(re.compile(r"/api/schedules\?"), handler)
            page.wait_for_timeout(600)
            dots_after = dots().count()
            assert dots_after == 0, (
                f"切月失败后仍显示上个月的 {dots_after} 个日程圆点：日历标题已是新月份，数据却是旧月份（误导）"
            )
            mask = page.locator(".el-loading-mask:visible")
            assert mask.count() == 0 or not mask.first.is_visible(), "切月失败后 loading 未复位"
            month_button("prev").click()
            wait_until(lambda: dots().count() >= 1, timeout=10000, message="恢复网络后回到本月仍无数据")
            assert dots().count() == dots_now, f"恢复后本月圆点数变化：{dots_now} → {dots().count()}"
            assert not fatal_since(u0), f"切月失败出现未捕获异常: {fatal_since(u0)[:2]}"
            assert not uncaught_since(c0), f"切月失败产生未捕获 Promise 拒绝: {uncaught_since(c0)[:2]}"
            return "失败清空旧月份（不错位）+ loading 复位 + 恢复后数据一致"
        record("INTX-006", "日程切月失败：不留旧月份数据且可恢复", probe_month_switch_fail)

        # ---------- INTX-007 列表加载失败：有反馈、不错乱 ----------
        def probe_list_load_fail():
            reset_all()
            for t in ("加载失败-保留A", "加载失败-保留B"):
                api("POST", "/api/tasks", json={"title": t})
            open_tasks()
            wait_until(lambda: rows().count() == 2, message="前置：任务列表未渲染出 2 行")
            c0, u0 = len(console_errors), len(page_errors)
            err0 = err_toasts()
            handler = lambda r: r.abort() if r.request.method == "GET" else r.continue_()
            page.route(re.compile(r"/api/tasks(\?.*)?$"), handler)
            sel = page.locator(".filters .el-select").first
            sel.click()
            page.wait_for_timeout(300)
            page.locator(".el-select-dropdown__item", has_text="待办").first.click()
            wait_until(lambda: err_toasts() > err0, message="列表加载失败时前端无任何错误提示（静默空白）")
            page.unroute(re.compile(r"/api/tasks(\?.*)?$"), handler)
            mask = page.locator(".el-loading-mask:visible")
            assert mask.count() == 0 or not mask.first.is_visible(), "列表加载失败后 loading 未复位"
            assert rows().count() == 2, f"列表加载失败后原有行被清空（{rows().count()} 行）：与任务页「失败保留上次数据」的口径不一致"
            assert not fatal_since(u0), f"列表加载失败出现未捕获异常: {fatal_since(u0)[:2]}"
            assert not uncaught_since(c0), f"列表加载失败产生未捕获 Promise 拒绝: {uncaught_since(c0)[:2]}"
            # 恢复：重新挂载页面（此时不再注入失败），列表应正常可用
            open_tasks()
            wait_until(lambda: rows().count() == 2, timeout=10000, message="恢复后列表仍不可用")
            return "失败有提示、loading 复位、旧数据保留、恢复后可用"
        record("INTX-007", "任务列表加载失败：反馈与状态一致", probe_list_load_fail)

        # ---------- INTX-008 提交期间的可见反馈（loading / 禁用） ----------
        def probe_saving_feedback():
            reset_all()
            open_tasks()
            slow = re.compile(r"/api/tasks$")
            # 把 POST 挂在途（不在 handler 里 sleep：那样主线程被 handler 占住，
            # 轮询看不到 loading 窗口）。放行前请求一定在途，断言才是确定的。
            held = {}

            def hold(route):
                held["route"] = route

            page.route(slow, hold)
            try:
                page.locator("button", has_text="新建任务").first.click()
                dlg().wait_for(state="visible", timeout=8000)
                title_input("例如：整理本月进项发票").fill("反馈-提交中按钮态")
                save_btn().click(timeout=3000)
                wait_until(lambda: "route" in held, timeout=5000, message="点击保存后请求没有发出（前置失败）")
                page.wait_for_timeout(300)  # 让 Vue 把 :loading 渲染出来
                cls = save_btn().first.get_attribute("class") or ""
                disabled = save_btn().first.is_disabled()
                assert "is-loading" in cls or disabled, f"提交期间保存按钮无「处理中」反馈（class={cls!r}, disabled={disabled}）"
                # 在途期间再点：不应把请求送出去（按钮禁用 / 重入守卫）
                before = len(requests_matching("POST", r"/api/tasks"))
                for _ in range(3):
                    try:
                        save_btn().first.click(timeout=400)
                    except Exception:
                        pass
                assert len(requests_matching("POST", r"/api/tasks")) == before, \
                    "提交在途期间仍能再次发出 POST（重复提交风险）"
                held["route"].continue_()
                held.clear()
            finally:
                page.wait_for_timeout(300)
                page.unroute(slow)
            wait_until(lambda: tasks_total() == 1, timeout=8000, message="提交中反馈用例：任务最终未创建")
            wait_until(lambda: not dlg().first.is_visible(), timeout=5000, message="提交成功后对话框未关闭")
            assert not dlg().first.is_visible(), "提交成功后对话框未关闭"
            return "在途期间按钮 loading/禁用，重复点击不再发请求，放行后正常落库"
        record("INTX-008", "提交中按钮有可见反馈（loading/禁用）", probe_saving_feedback)

        # ---------- INTX-009 全流程无未捕获异常与前端报错 ----------
        def probe_no_uncaught_overall():
            api("DELETE", "/api/debug/client-errors")
            body = api("GET", "/api/debug/client-errors").json()
            assert body["total"] == 0, f"交互走查过程中前端报错采集到 {body['total']} 条未捕获异常"
            uncaught = uncaught_since(0)
            assert not uncaught, f"存在未捕获 Promise 拒绝: {uncaught[:3]}"
            assert not page_errors, f"存在页面级未捕获异常: {page_errors[:3]}"
            return f"0 未捕获异常 / 0 前端报错上报（console_errors 累计 {len(console_errors)} 条，见明细）"
        record("INTX-009", "全流程：零未捕获异常、零前端报错上报", probe_no_uncaught_overall)

        # ---------- 汇总 ----------
        passed = sum(1 for r in results if r["passed"])
        print("=" * 70)
        print(f"total={len(results)} passed={passed} failed={len(results) - passed}")
        for e in page_errors[:10]:
            print(f"  !! pageerror: {e[:200]}")
        for e in console_errors[:10]:
            print(f"  !! console: {e[:200]}")

        raw = {
            "area": AREA,
            "kind": "automated-interaction-walkthrough（自动化走查，非真人可用性测试）",
            "base": BASE,
            "started_at": STARTED_AT,
            "finished_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "pages": [
                {"route": r, "name": r.strip("/"), "status": 200}
                for r in ("/tasks", "/schedules", "/jobs")
            ],
            "tests": results,
            "console_errors": console_errors,
            "page_errors": page_errors,
        }
        out = RESULTS / f"{AREA}.json"
        out.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"result -> {out}")
        sys.exit(0 if passed == len(results) else 1)
finally:
    server.terminate()
    try:
        server.wait(timeout=10)
    except Exception:
        server.kill()
    log_fh.close()
