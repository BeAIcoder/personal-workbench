# -*- coding: utf-8 -*-
"""dashboard-search 区域深度 E2E 探测（端口 8217，独立库隔离，可重复运行资产）。

页面：
- DashboardView（hash 路由 `/`）：概览卡片、完成率、近 7 天趋势、三栏最近列表、卡片/列表跳转、加载失败重试；
- AppLayout 顶栏 GlobalSearch（Ctrl+K）：建议分组/摘要、ESC、点击跳转、无结果占位、慢响应竞态、异常降级。

接口：
- GET /api/dashboard/summary（统计口径 + 今日日程边界）
- GET /api/search?q=...（任务/日程/笔记三类命中、摘要、去重、通配符转义、参数边界）

契约预期（脚本编写时的真实契约 + 本次修复，避免为通过而放宽断言）：
- /api/search 缺 q、q=""、纯空白、q>100 → 422。本次修复前 q=" " 会命中所有含空格记录（contains(" ")），
  修复后纯空白与空 q 一致返回 422；q 前后空白会被裁剪（与前端 trim 一致）。
- 命中字段：任务标题+描述、日程标题+地点+备注、笔记标题+内容，各取前 5 条；LIKE 的 % / _ 按字面匹配。
- 概览「今日日程」按与今天有交集计算；今天 00:00 整结束的日程与今天无交集，不计入（本次修复 end_time > day_start）。
- 前端：Ctrl+K 聚焦；ESC 关闭建议；任务/笔记结果跳转并带 q 筛选；日程结果附带 date 定位并按 q 过滤；
  无结果占位项不得改写输入框；慢响应不得覆盖新关键词建议。后三项为本次源码修复（dist 尚未重建，标注待复跑）。

可重复运行：独立库/财务目录/端口，清库清目录，自起自停，服务端日志落盘。
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[3]
BACKEND = ROOT / "backend"
RESULTS = ROOT / "dev" / "e2e" / "probe_results"
AREA = "dashboard-search"
DB = RESULTS / f"{AREA}.db"
FIN_DIR = RESULTS / f"{AREA}-fin"
SHOTS = RESULTS / AREA / "shots"
LOG = RESULTS / f"{AREA}.server.log"
RESULT_JSON = RESULTS / f"{AREA}.json"
PORT = 8217
BASE = f"http://127.0.0.1:{PORT}"
STARTED_AT = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
TODAY = datetime.now().date()

RESULTS.mkdir(parents=True, exist_ok=True)
SHOTS.mkdir(parents=True, exist_ok=True)
if DB.exists():
    DB.unlink()
if FIN_DIR.exists():
    shutil.rmtree(FIN_DIR)

env = os.environ.copy()
env["DATABASE_URL"] = f"sqlite:///{DB.as_posix()}"
env["FINANCE_DATA_DIR"] = str(FIN_DIR)
env["PYTHONIOENCODING"] = "utf-8"
env["SEED_ON_STARTUP"] = "0"  # 空库起点，计数/列表断言可完全由播种数据推导

log_fh = open(LOG, "w", encoding="utf-8")
server = subprocess.Popen(
    [str(BACKEND / "venv" / "Scripts" / "python.exe"), "-m", "uvicorn",
     "app.main:app", "--host", "127.0.0.1", "--port", str(PORT)],
    cwd=str(BACKEND), env=env, stdout=log_fh, stderr=log_fh,
)

results = []          # {"id","name","passed","detail"}
pages = []            # 页面级侦察结果（与 tasks.py 结构一致）
console_errors = []   # 关键用例期间的全局控制台错误
page_errors = []      # 全流程未捕获异常
search_requests = []  # /api/search 请求 URL（校验 trim / 空 q 不发请求）
page = None

# Element Plus 2.14 的 el-autocomplete 建议列表挂在 body 的 popper 内
SUGGESTION = ".el-autocomplete-suggestion:visible li"

# 仅用于复现「慢响应覆盖新建议」的竞态：给 q 含 DSRACE-A 的搜索响应人为延迟 1.8s
# （包装 XHR.onloadend，不阻塞 Playwright 主线程，sync 探针也能稳定复现）
RACE_PATCH = r"""
(() => {
  const origOpen = XMLHttpRequest.prototype.open;
  const origSend = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function (method, url) {
    this.__probeUrl = String(url || '');
    return origOpen.apply(this, arguments);
  };
  XMLHttpRequest.prototype.send = function (body) {
    const url = this.__probeUrl || '';
    if (url.includes('/api/search') && decodeURIComponent(url).includes('DSRACE-A')) {
      const orig = this.onloadend;
      this.onloadend = function (ev) {
        setTimeout(() => { if (orig) orig.call(this, ev); }, 1800);
      };
    }
    return origSend.apply(this, arguments);
  };
})()
"""


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


def wait_until(fn, timeout=6, msg="等待超时"):
    t0 = time.time()
    last = None
    while time.time() - t0 < timeout:
        try:
            value = fn()
            if value:
                return value
        except Exception as e:  # 页面导航/DOM 重渲染期间的瞬时异常
            last = e
        page.wait_for_timeout(100)
    raise AssertionError(f"{msg}" + (f"（最后异常：{last}）" if last else ""))


def wait_ready(timeout=60):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            urllib.request.urlopen(f"{BASE}/api/dashboard/summary", timeout=2)
            return True
        except Exception:
            time.sleep(1)
    return False


def api(method, path, **kw):
    if "json" in kw:
        kw["data"] = json.dumps(kw.pop("json"))
        headers = kw.pop("headers", {})
        headers["Content-Type"] = "application/json"
        kw["headers"] = headers
    return page.request.fetch(f"{BASE}{path}", method=method, **kw)


def _all_items(path, page_size):
    items = []
    page_no = 1
    while True:
        body = api("GET", path, params={"page": page_no, "page_size": page_size}).json()
        items += body["items"]
        if len(items) >= body["total"] or not body["items"]:
            return items
        page_no += 1


def reset_tasks():
    for item in _all_items("/api/tasks", 100):
        api("DELETE", f"/api/tasks/{item['id']}")


def reset_schedules():
    for item in _all_items("/api/schedules", 200):
        api("DELETE", f"/api/schedules/{item['id']}")


def reset_notes():
    for item in _all_items("/api/notes", 100):
        api("DELETE", f"/api/notes/{item['id']}")


def reset_all():
    reset_tasks()
    reset_schedules()
    reset_notes()


def day(offset=0, clock="09:00:00"):
    return f"{(TODAY + timedelta(days=offset)).isoformat()}T{clock}"


def seed_task(title, **kw):
    payload = {"title": title}
    payload.update(kw)
    resp = api("POST", "/api/tasks", json=payload)
    assert resp.status == 201, f"播种任务失败({resp.status}): {title}"
    return resp.json()


def seed_schedule(title, start, end, **kw):
    payload = {"title": title, "start_time": start, "end_time": end}
    payload.update(kw)
    resp = api("POST", "/api/schedules", json=payload)
    assert resp.status == 201, f"播种日程失败({resp.status}): {title}"
    return resp.json()


def seed_note(title, **kw):
    payload = {"title": title}
    payload.update(kw)
    resp = api("POST", "/api/notes", json=payload)
    assert resp.status == 201, f"播种笔记失败({resp.status}): {title}"
    return resp.json()


def open_dashboard():
    """全新挂载首页：hash 同文档导航不会重建组件，reload 保证 GlobalSearch/概览状态隔离。"""
    page.goto(f"{BASE}/#/", wait_until="networkidle", timeout=30000)
    page.reload(wait_until="networkidle", timeout=30000)
    page.wait_for_timeout(350)


def search_input():
    return page.locator("input[placeholder*='全局搜索']")


def suggestion_items():
    return page.locator(SUGGESTION)


def suggestion_texts():
    items = suggestion_items()
    return [items.nth(i).inner_text() for i in range(items.count())]


def wait_suggestions(count, timeout=6):
    wait_until(lambda: suggestion_items().count() >= count,
               timeout=timeout, msg=f"等待至少 {count} 条搜索建议超时")


def type_search(text, expect_response=True, timeout=8):
    """输入并等待 /api/search 响应；空白关键词不会发请求，调用方传 expect_response=False。"""
    inp = search_input()
    inp.fill("")
    if not expect_response:
        inp.fill(text)
        page.wait_for_timeout(700)
        return None
    with page.expect_response(lambda r: "/api/search" in r.url and "q=" in r.url, timeout=timeout * 1000) as info:
        inp.fill(text)
    page.wait_for_timeout(180)
    return info.value


def hash_query(url):
    fragment = url.split("#", 1)[1] if "#" in url else ""
    raw = fragment.split("?", 1)[1] if "?" in fragment else ""
    return {k: v[0] for k, v in parse_qs(raw).items()}


def fmt_dt(value):
    return (value or "")[:16].replace("T", " ")


def fmt_hm(value):
    return (value or "")[11:16]


def js_num(value):
    """按 JS number 的展示方式格式化（10.0 → '10'，28.6 → '28.6'）。"""
    return str(int(value)) if float(value).is_integer() else str(value)


def card_values():
    return [x.inner_text().strip() for x in page.locator(".stat-card .stat-num").all()]


def select_text(index):
    """读取筛选下拉的实际展示文案（EP 2.x 选中值同样渲染在 .el-select__placeholder 内）。"""
    sel = page.locator(".filters .el-select").nth(index)
    span = sel.locator(".el-select__placeholder span")
    text = span.first.inner_text().strip() if span.count() else sel.inner_text().strip()
    return "" if text in ("", "状态", "优先级", "全部状态", "全部优先级") else text


def total_text():
    return page.locator(".el-pagination__total").inner_text()


def visit_page(route, name):
    """独立采集一次页面访问的控制台/页面错误/失败请求（用于结果 JSON 的 pages）。"""
    c_errs, p_errs, f_reqs = [], [], []

    def on_console(msg):
        if msg.type == "error":
            c_errs.append(msg.text)

    def on_pageerror(err):
        p_errs.append(str(err))

    def on_failed(req):
        f_reqs.append(f"{req.method} {req.url}")

    page.on("console", on_console)
    page.on("pageerror", on_pageerror)
    page.on("requestfailed", on_failed)
    try:
        resp = page.goto(f"{BASE}/#{route}", wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(800)
        page.screenshot(path=str(SHOTS / f"page-{name}.png"))
        pages.append({
            # hash 路由在同文档内跳转时 goto 返回 None；页面已加载成功，按 200 记录
            "route": route, "name": name, "status": resp.status if resp else 200,
            "console_errors": c_errs, "page_errors": p_errs,
            "failed_reqs": [f for f in f_reqs if "favicon" not in f],
        })
    finally:
        page.remove_listener("console", on_console)
        page.remove_listener("pageerror", on_pageerror)
        page.remove_listener("requestfailed", on_failed)


def seed_dashboard_dataset():
    """概览一致性数据集：7 任务 / 3 日程（含午夜边界）/ 3 笔记。"""
    seed_task("DS概览-待办1")
    seed_task("DS概览-待办2")
    seed_task("DS概览-进行中", status="in_progress", priority="high")
    seed_task("DS概览-已完成", status="done")
    seed_task("DS概览-逾期", due_date=day(-1), priority="urgent")
    seed_task("DS概览-今日到期", due_date=day(0, "23:59:00"))
    seed_task("DS概览-已完成但逾期", status="done", due_date=day(-2))
    seed_schedule("DS概览-今日全天", day(0, "00:00:00"), day(0, "23:59:59"), all_day=True, color="#E6A23C")
    seed_schedule("DS概览-今日会议", day(0, "09:00:00"), day(0, "10:00:00"), color="#67C23A")
    seed_schedule("DS概览-明日会议", day(1, "09:00:00"), day(1, "10:00:00"))
    # 与今天无交集（今天 00:00 整结束），修复后不应进入今日日程
    seed_schedule("DS概览-午夜整结束", day(-1, "23:00:00"), day(0, "00:00:00"))
    seed_note("DS概览-笔记A", content="A")
    seed_note("DS概览-笔记B", content="B", pinned=True)
    seed_note("DS概览-笔记C", content="C")


try:
    if not wait_ready():
        print("FATAL: server not ready")
        sys.exit(2)

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: page_errors.append(str(e)))
        page.on("request", lambda r: search_requests.append(r.url) if "/api/search" in r.url else None)

        # ================= 页面级侦察 =================
        def probe_dashboard_empty():
            console_errors.clear()
            page_errors.clear()
            reset_all()
            open_dashboard()
            wait_until(lambda: page.locator(".stat-card .stat-num").count() == 4, msg="概览卡片未渲染")
            wait_until(lambda: all(v != "-" for v in card_values()), msg="卡片数据未加载完成")
            assert card_values() == ["0", "0", "0", "0"], f"空库概览卡片异常: {card_values()}"
            assert "完成率 0%" in page.locator(".rate").inner_text(), "空库完成率异常"
            assert page.locator(".el-empty").count() == 3, "空库三栏空态缺失"
            descs = page.locator(".el-empty__description").all_inner_texts()
            for expected in ("暂无临近截止的任务", "今天暂无日程安排", "还没有笔记"):
                assert any(expected in d for d in descs), f"缺少空态文案: {expected} / {descs}"
            wait_until(lambda: page.locator(".charts canvas").count() >= 2, msg="趋势/饼图未渲染")
            assert "数据保存在本机" in page.locator(".today-sub").inner_text(), "副标题缺失"
            assert not page_errors, f"空态首页出现未捕获异常: {page_errors[:2]}"
            assert not console_errors, f"空态首页出现控制台错误: {console_errors[:3]}"
            return "空态卡片全 0 / 完成率 0% / 三栏空态 / 双图表 / 无控制台与页面错误"

        record("DASH-PAGE-001", "首页加载与空态", probe_dashboard_empty)

        visit_page("/", "dashboard")

        # ================= 概览：与 API 一致 =================
        def probe_dashboard_cards_match_api():
            reset_all()
            seed_dashboard_dataset()
            summary = api("GET", "/api/dashboard/summary").json()
            page_errors.clear()
            open_dashboard()
            wait_until(lambda: page.locator(".stat-card .stat-num").count() == 4, msg="概览卡片未渲染")
            wait_until(lambda: all(v != "-" for v in card_values()), msg="卡片数据未加载完成")
            expected = [summary["task"]["todo"], summary["task"]["in_progress"],
                        summary["task"]["overdue"], summary["schedule"]["today_count"]]
            actual = [int(v) for v in card_values()]
            assert actual == expected, f"卡片数字与 API 不一致: UI={actual} API={expected}"
            rate = page.locator(".rate").inner_text()
            assert f"完成率 {js_num(summary['task']['completion_rate'])}%" in rate, \
                f"完成率与 API 不一致: {rate} vs {summary['task']['completion_rate']}"
            assert summary["task"]["total"] == 7 and summary["task"]["done"] == 2, \
                f"概览任务统计口径异常: {summary['task']}"
            assert not page_errors, f"概览页出现未捕获异常: {page_errors[:2]}"
            return f"卡片={actual} 完成率={summary['task']['completion_rate']}%（与 API 一致）"

        record("DASH-UI-001", "概览卡片与完成率对齐 API", probe_dashboard_cards_match_api)

        def probe_dashboard_lists_match_api():
            reset_all()
            seed_dashboard_dataset()
            # 额外任务验证「未完成 + 按截止升序 + 最多 6 条」
            seed_task("DS概览-本周到期", due_date=day(2, "12:00:00"))
            summary = api("GET", "/api/dashboard/summary").json()
            open_dashboard()
            wait_until(lambda: page.locator(".stat-card .stat-num").count() == 4, msg="概览卡片未渲染")
            wait_until(lambda: all(v != "-" for v in card_values()), msg="卡片数据未加载完成")

            upcoming = summary["upcoming_tasks"]
            assert len(upcoming) == 3 and len(upcoming) <= 6, f"upcoming_tasks 条数异常: {len(upcoming)}"
            titles = page.locator(".list-card").nth(0).locator(".mini-item .mini-title").all_inner_texts()
            assert [t.strip() for t in titles] == [t["title"] for t in upcoming], \
                f"即将到期任务与 API 不一致: UI={titles} API={[t['title'] for t in upcoming]}"
            times = page.locator(".list-card").nth(0).locator(".mini-item .mini-time").all_inner_texts()
            for item, text in zip(upcoming, times):
                prefix = "已逾期 " if item["status"] != "done" and item["due_date"] < datetime.now().strftime("%Y-%m-%dT%H:%M:%S") else "截止 "
                assert fmt_dt(item["due_date"]) in text and prefix in text, \
                    f"任务时间展示异常: {item['title']} -> {text!r}"

            schedules = summary["today_schedules"]
            s_titles = page.locator(".list-card").nth(1).locator(".mini-item .mini-title").all_inner_texts()
            assert [t.strip() for t in s_titles] == [s["title"] for s in schedules], \
                f"今日日程与 API 不一致: UI={s_titles} API={[s['title'] for s in schedules]}"
            s_times = page.locator(".list-card").nth(1).locator(".mini-item .mini-time").all_inner_texts()
            for item, text in zip(schedules, s_times):
                expected_time = "全天" if item["all_day"] else fmt_hm(item["start_time"])
                assert text.strip() == expected_time, f"日程时间展示异常: {item['title']} -> {text!r}"

            notes = summary["recent_notes"]
            assert len(notes) == 3, f"recent_notes 条数异常: {len(notes)}"
            n_titles = page.locator(".list-card").nth(2).locator(".mini-item .mini-title").all_inner_texts()
            expected_notes = [("⭐ " if n["pinned"] else "") + n["title"] for n in notes]
            assert [t.strip() for t in n_titles] == expected_notes, \
                f"最近笔记与 API 不一致: UI={n_titles} API={expected_notes}"
            n_times = page.locator(".list-card").nth(2).locator(".mini-item .mini-time").all_inner_texts()
            for item, text in zip(notes, n_times):
                assert text.strip() == fmt_dt(item["updated_at"]), \
                    f"笔记时间展示异常: {item['title']} -> {text!r}"
            return f"三栏列表逐项对齐 API（任务 {len(upcoming)} / 日程 {len(schedules)} / 笔记 {len(notes)}）"

        record("DASH-UI-002", "三栏最近列表与 API 一致", probe_dashboard_lists_match_api)

        def probe_dashboard_card_links():
            reset_all()
            for i in range(2):
                seed_task(f"DS卡片-待办{i}")
            seed_task("DS卡片-进行中", status="in_progress")
            seed_task("DS卡片-逾期", due_date=day(-1))
            seed_schedule("DS卡片-今日日程", day(0, "11:00:00"), day(0, "12:00:00"))
            summary = api("GET", "/api/dashboard/summary").json()

            open_dashboard()
            page.locator(".stat-card").nth(0).click()
            wait_until(lambda: "/#/tasks" in page.url, msg="待办卡片未跳转任务页")
            q = hash_query(page.url)
            assert q.get("status") == "todo", f"待办卡片路由参数异常: {q}"
            wait_until(lambda: f"共 {summary['task']['todo']} 条" in total_text(),
                       msg=f"待办卡片数量与 API 不一致: {total_text()} vs {summary['task']['todo']}")
            assert select_text(0) == "待办", "待办卡片未回填状态筛选"

            open_dashboard()
            page.locator(".stat-card").nth(1).click()
            wait_until(lambda: "status=in_progress" in page.url, msg="进行中卡片未跳转")
            wait_until(lambda: f"共 {summary['task']['in_progress']} 条" in total_text(),
                       msg="进行中卡片数量与 API 不一致")
            assert select_text(0) == "进行中", "进行中卡片未回填状态筛选"

            open_dashboard()
            page.locator(".stat-card").nth(2).click()
            wait_until(lambda: "overdue=true" in page.url, msg="逾期卡片未跳转")
            wait_until(lambda: f"共 {summary['task']['overdue']} 条" in total_text(),
                       msg=f"逾期卡片数量与 API 不一致: {total_text()} vs {summary['task']['overdue']}")
            wait_until(lambda: select_text(2) == "按截止时间",
                       msg=f"逾期卡片未切换按截止时间排序: {select_text(2)!r}")

            open_dashboard()
            page.locator(".stat-card").nth(3).click()
            wait_until(lambda: page.url.rstrip("/").endswith("#/schedules"), msg="今日日程卡片未跳转日程页")
            wait_until(lambda: page.locator(".day-title").count() > 0, msg="日程页未渲染")
            return f"四张卡片路由/筛选正确（待办 {summary['task']['todo']} / 进行中 {summary['task']['in_progress']} / 逾期 {summary['task']['overdue']}）"

        record("DASH-UI-003", "概览卡片跳转与筛选", probe_dashboard_card_links)

        def probe_dashboard_list_links():
            reset_all()
            seed_task("DS列表跳转-任务", due_date=day(1, "09:00:00"))
            seed_schedule("DS列表跳转-日程", day(0, "13:00:00"), day(0, "14:00:00"))
            seed_note("DS列表跳转-笔记", content="跳转")

            open_dashboard()
            page.locator(".list-card").nth(0).locator(".mini-item").first.click()
            wait_until(lambda: "sort=due_asc" in page.url, msg="任务列表项未跳转 due_asc")
            wait_until(lambda: page.locator(".filters .el-select").count() >= 3, msg="任务页未渲染")
            wait_until(lambda: select_text(2) == "按截止时间",
                       msg=f"任务列表项跳转排序异常: {select_text(2)!r}")

            open_dashboard()
            page.locator(".list-card").nth(1).locator(".mini-item").first.click()
            wait_until(lambda: page.url.rstrip("/").endswith("#/schedules"), msg="今日日程项未跳转日程页")
            wait_until(lambda: page.locator(".day-title").count() > 0, msg="日程页未渲染")

            open_dashboard()
            page.locator(".list-card").nth(2).locator(".mini-item").first.click()
            wait_until(lambda: page.url.rstrip("/").endswith("#/notes"), msg="最近笔记项未跳转笔记页")
            wait_until(lambda: page.locator(".note-list-card").count() > 0, msg="笔记页未渲染")
            return "三栏列表项分别跳转 /tasks?sort=due_asc、/schedules、/notes"

        record("DASH-UI-004", "最近列表项跳转", probe_dashboard_list_links)

        def probe_dashboard_xss():
            reset_all()
            payload = '<img src=x onerror="window.__xss=1">DS首页XSS'
            seed_task(payload, due_date=day(1, "09:00:00"))
            seed_schedule(payload, day(0, "15:00:00"), day(0, "16:00:00"))
            seed_note(payload, content="x")
            page_errors.clear()
            open_dashboard()
            wait_until(lambda: page.locator(".stat-card .stat-num").count() == 4, msg="概览未渲染")
            wait_until(lambda: page.locator(".list-card .mini-item").count() >= 3, msg="三栏列表未渲染")
            assert not page.evaluate("window.__xss === 1"), "首页列表标题 XSS 被执行"
            assert page.locator(".list-card img").count() == 0, "首页列表把标题渲染成了 HTML 元素"
            for i in range(3):
                texts = page.locator(".list-card").nth(i).locator(".mini-item .mini-title").all_inner_texts()
                assert any(payload in t for t in texts), f"第 {i} 栏未按字面展示特殊字符标题: {texts}"
            assert not page_errors, f"XSS 用例出现未捕获异常: {page_errors[:2]}"
            return "概览三栏标题按字面转义展示，window.__xss 未触发"

        record("DASH-UI-005", "首页标题 XSS 防护", probe_dashboard_xss)

        def probe_dashboard_load_failure():
            reset_all()
            open_dashboard()
            page_errors.clear()
            page.route("**/api/dashboard/summary", lambda r: r.abort())
            try:
                page.reload(wait_until="networkidle", timeout=30000)
                wait_until(lambda: page.locator(".el-result:visible").count() > 0, msg="加载失败未展示重试结果页")
                box = page.locator(".el-result:visible")
                assert "工作台数据加载失败" in box.inner_text(), f"失败页文案异常: {box.inner_text()[:80]}"
                assert page.locator(".el-message--error").count() > 0, "概览加载失败无错误提示（静默）"
                assert page.locator(".stat-card").count() == 0, "失败态仍渲染卡片"
                assert not page_errors, f"加载失败出现未捕获异常: {page_errors[:2]}"
            finally:
                page.unroute("**/api/dashboard/summary")
            box.locator("button", has_text="重试").click()
            wait_until(lambda: page.locator(".stat-card").count() == 4, msg="重试后卡片未恢复")
            wait_until(lambda: page.locator(".el-result:visible").count() == 0, msg="重试后错误页未消失")
            return "失败有提示 + 重试入口，重试成功后恢复卡片，无未捕获异常"

        record("DASH-UI-006", "概览加载失败与重试", probe_dashboard_load_failure)

        def probe_dashboard_api_contract():
            reset_all()
            seed_dashboard_dataset()
            summary = api("GET", "/api/dashboard/summary").json()
            task = summary["task"]
            assert task["total"] == 7 and task["todo"] == 4 and task["in_progress"] == 1 and task["done"] == 2, \
                f"任务统计口径异常: {task}"
            assert task["completion_rate"] == round(2 / 7 * 100, 1), f"完成率计算异常: {task['completion_rate']}"
            assert task["overdue"] >= 1, "逾期计数丢失"

            # 今日日程：今天 09-10 + 全天，共 2；午夜整结束与明日均排除
            titles = [s["title"] for s in summary["today_schedules"]]
            assert titles == ["DS概览-今日全天", "DS概览-今日会议"], f"今日日程集合/排序异常: {titles}"
            assert summary["schedule"]["today_count"] == len(summary["today_schedules"]) == 2, \
                f"今日日程计数与列表不一致: {summary['schedule']}"
            assert "DS概览-午夜整结束" not in titles, "今天 00:00 整结束的日程被误计入今日日程"
            assert "DS概览-明日会议" not in titles, "明日日程被误计入今日日程"
            assert summary["schedule"]["upcoming_7d"] == 1, \
                f"未来 7 天日程计数异常: {summary['schedule']['upcoming_7d']}"

            upcoming = summary["upcoming_tasks"]
            assert [t["title"] for t in upcoming] == ["DS概览-逾期", "DS概览-今日到期"], \
                f"即将到期任务集合/排序异常: {[t['title'] for t in upcoming]}"
            assert all(t["status"] != "done" for t in upcoming), "已完成任务进入即将到期列表"
            assert all(t["due_date"] for t in upcoming), "无截止任务进入即将到期列表"
            due_values = [t["due_date"] for t in upcoming]
            assert due_values == sorted(due_values), "即将到期任务未按截止升序"

            trend = summary["trend_7d"]
            assert len(trend) == 7 and trend[-1]["date"] == TODAY.isoformat(), f"趋势序列异常: {trend}"
            assert trend[-1]["count"] == 2, f"今天完成数应等于创建的 2 条 done 任务: {trend[-1]}"

            assert summary["note"]["total"] == 3 and summary["note"]["week_added"] == 3, \
                f"笔记统计异常: {summary['note']}"
            assert len(summary["recent_notes"]) == 3, "最近笔记条数异常"
            updated = [n["updated_at"] for n in summary["recent_notes"]]
            assert updated == sorted(updated, reverse=True), "最近笔记未按更新时间倒序"
            return "任务/日程/笔记统计口径、今日边界、趋势、排序均符合契约"

        record("DASH-API-001", "概览统计口径与今日日程边界", probe_dashboard_api_contract)

        # ================= 搜索 API 层 =================
        def probe_search_query_bounds():
            reset_all()
            seed_task("DS边界-单字符任务")
            assert api("GET", "/api/search").status == 422, "缺 q 应 422"
            assert api("GET", "/api/search", params={"q": ""}).status == 422, "q='' 应 422"
            for blank in (" ", "   ", "\t"):
                r = api("GET", "/api/search", params={"q": blank})
                assert r.status == 422, f"qa={blank!r} 应 422（空白关键词不得命中含空格记录），实际 {r.status}: {r.text()[:120]}"
            r = api("GET", "/api/search", params={"q": "x" * 101})
            assert r.status == 422, "q 超 100 字符应 422"
            assert api("GET", "/api/search", params={"q": "x" * 100}).status == 200, "q=100 字符应 200"
            r = api("GET", "/api/search", params={"q": "界"})
            assert r.status == 200 and r.json()["total"] == 1, "单字符搜索应正常命中"
            return "缺省/空/纯空白/101 字符均 422；单字符与 100 字符正常"

        record("SEARCH-API-001", "搜索关键词参数边界", probe_search_query_bounds)

        def probe_search_three_types():
            reset_all()
            seed_task("DS三类任务", description="任务描述不含关键词")
            seed_schedule("DS三类日程", day(3, "09:00:00"), day(3, "10:00:00"),
                          location="DS三类地点", description="日程备注")
            seed_note("普通笔记标题", content="正文里含 DS三类 关键词")
            body = api("GET", "/api/search", params={"q": "DS三类"}).json()
            assert body["total"] == 3, f"三类命中数异常: {body['total']}"
            assert [x["type"] for x in body["tasks"]] == ["task"], "任务 type 异常"
            assert [x["type"] for x in body["schedules"]] == ["schedule"], "日程 type 异常"
            assert [x["type"] for x in body["notes"]] == ["note"], "笔记 type 异常"
            assert body["tasks"][0]["title"] == "DS三类任务"
            assert body["schedules"][0]["title"] == "DS三类日程"
            assert body["notes"][0]["title"] == "普通笔记标题"
            assert body["tasks"][0]["snippet"], "任务未返回描述摘要"
            assert "DS三类" in body["schedules"][0]["snippet"], \
                f"日程摘要应取命中字段上下文: {body['schedules'][0]['snippet']!r}"
            assert "DS三类" in body["notes"][0]["snippet"], "笔记摘要未包含关键词上下文"
            assert body["tasks"][0]["time"], "任务结果缺少 time"
            return "任务/日程/笔记三类命中，type/snippet/time 符合契约"

        record("SEARCH-API-002", "三类命中与上下文摘要", probe_search_three_types)

        def probe_search_fields_and_dedupe():
            reset_all()
            seed_task("DS字段双命中", description="DS字段双命中 同时出现在描述")
            seed_task("普通任务二", category="DS字段分类")
            seed_note("普通笔记二", content="普通正文", tags=["DS字段标签"])
            seed_schedule("普通日程二", day(2, "09:00:00"), day(2, "10:00:00"),
                          description="备注含 DS字段备注")
            seed_schedule("普通日程三", day(2, "11:00:00"), day(2, "12:00:00"),
                          location="DS字段地点")

            body = api("GET", "/api/search", params={"q": "DS字段双命中"}).json()
            assert len(body["tasks"]) == 1, f"标题+描述同时命中应去重为 1 条: {body['tasks']}"

            assert api("GET", "/api/search", params={"q": "DS字段分类"}).json()["total"] == 0, \
                "任务分类不应参与全局搜索"
            assert api("GET", "/api/search", params={"q": "DS字段标签"}).json()["total"] == 0, \
                "笔记标签不应参与全局搜索"
            assert api("GET", "/api/search", params={"q": "DS字段备注"}).json()["total"] == 1, \
                "日程备注应参与搜索"
            assert api("GET", "/api/search", params={"q": "DS字段地点"}).json()["total"] == 1, \
                "日程地点应参与搜索"
            return "标题+描述去重；任务分类/笔记标签不参与；日程备注/地点参与"

        record("SEARCH-API-003", "命中字段范围与去重", probe_search_fields_and_dedupe)

        def probe_search_wildcards():
            reset_all()
            seed_task("折扣100%商品")
            seed_task("A_B 对账")
            seed_task("版本1.2 说明")
            seed_task("方括号[测试]")
            seed_task("星号*说明")

            def titles(q):
                return [t["title"] for t in api("GET", "/api/search", params={"q": q}).json()["tasks"]]

            assert titles("%") == ["折扣100%商品"], f"q=%% 应按字面匹配: {titles('%')}"
            assert titles("_") == ["A_B 对账"], f"q=_ 应按字面匹配: {titles('_')}"
            assert titles("100%") == ["折扣100%商品"], f"q=100%% 字面匹配异常: {titles('100%')}"
            assert titles(".") == ["版本1.2 说明"], f"q=. 不应作为正则: {titles('.')}"
            assert titles("[") == ["方括号[测试]"], f"q=[ 应按字面匹配: {titles('[')}"
            assert titles("*") == ["星号*说明"], f"q=* 应按字面匹配: {titles('*')}"
            return "% / _ / . / [ / * 全部按字面匹配，无通配/正则注入"

        record("SEARCH-API-004", "通配符与特殊字符字面匹配", probe_search_wildcards)

        def probe_search_case_and_trim():
            reset_all()
            seed_task("DSCase 大小写任务")
            body = api("GET", "/api/search", params={"q": "dscase"}).json()
            assert body["total"] == 1, f"ASCII 关键词应忽略大小写: {body['total']}"
            body = api("GET", "/api/search", params={"q": "  dsCase  "}).json()
            assert body["total"] == 1, "前后空白未裁剪，导致漏命中"
            assert body["q"] == "dsCase", f"响应 q 未反映裁剪后的关键词: {body['q']!r}"
            return "ASCII 忽略大小写；首尾空白裁剪后命中，响应 q 同步裁剪"

        record("SEARCH-API-005", "大小写与首尾空白", probe_search_case_and_trim)

        def probe_search_limit_and_order():
            reset_all()
            tasks = [seed_task(f"DS上限任务{i}", description="DS上限") for i in range(6)]
            seed_schedule("DS上限日程A", day(10, "09:00:00"), day(10, "10:00:00"))
            seed_schedule("DS上限日程B", day(5, "09:00:00"), day(5, "10:00:00"))
            seed_note("DS上限笔记1", content="DS上限")
            seed_note("DS上限笔记2", content="DS上限")
            # 按更新时间倒序：更新笔记 1 使其排到笔记 2 之前
            notes = api("GET", "/api/notes", params={"page_size": 100}).json()["items"]
            note1 = next(n for n in notes if n["title"] == "DS上限笔记1")
            api("PUT", f"/api/notes/{note1['id']}", json={"content": "DS上限 更新"})

            body = api("GET", "/api/search", params={"q": "DS上限"}).json()
            task_ids = [t["id"] for t in body["tasks"]]
            assert len(task_ids) == 5, f"任务每类上限应为 5: {len(task_ids)}"
            assert tasks[5]["id"] in task_ids, "最新任务应出现在结果中"
            assert tasks[0]["id"] not in task_ids, "超出每类上限的旧任务不应返回"
            assert task_ids[0] == tasks[5]["id"], f"任务应按更新时间倒序: {task_ids}"
            assert [s["title"] for s in body["schedules"]] == ["DS上限日程B", "DS上限日程A"], \
                f"日程应按开始时间升序: {[s['title'] for s in body['schedules']]}"
            assert [n["title"] for n in body["notes"]] == ["DS上限笔记1", "DS上限笔记2"], \
                f"笔记应按更新时间倒序: {[n['title'] for n in body['notes']]}"
            assert body["total"] == 5 + 2 + 2, f"total 应等于各类返回条数之和: {body['total']}"
            return "每类最多 5 条；任务/笔记更新时间倒序、日程开始升序；total=各分类条数之和"

        record("SEARCH-API-006", "每类上限与组内排序", probe_search_limit_and_order)

        def probe_search_empty_and_xss():
            reset_all()
            xss = '<img src=x onerror="window.__xss=1">DS搜索XSS'
            seed_task(xss)
            body = api("GET", "/api/search", params={"q": "DS搜索XSS"}).json()
            assert body["total"] == 1 and body["tasks"][0]["title"] == xss, \
                "XSS 标题应原样返回（转义由前端负责）"
            empty = api("GET", "/api/search", params={"q": "DS绝对不存在XYZ"}).json()
            assert empty["total"] == 0 and empty["tasks"] == [] and empty["schedules"] == [] and empty["notes"] == [], \
                "空结果结构异常"
            return "XSS 标题原样返回；空结果 total=0 且三类均为空数组"

        record("SEARCH-API-007", "空结果与 XSS 标题透传", probe_search_empty_and_xss)

        # ================= GlobalSearch UI =================
        def probe_ui_ctrl_k_and_grouping():
            reset_all()
            seed_task("DS分组任务", description="DS分组任务描述")
            seed_schedule("DS分组日程", day(2, "09:00:00"), day(2, "10:00:00"), location="DS分组地点")
            seed_note("DS分组笔记", content="DS分组正文")
            page_errors.clear()
            open_dashboard()
            page.keyboard.press("Control+k")
            wait_until(lambda: "全局搜索" in (page.evaluate("document.activeElement && document.activeElement.placeholder") or ""),
                       msg="Ctrl+K 未聚焦全局搜索框")
            search_requests.clear()
            type_search("  DS分组  ")
            wait_suggestions(3)
            texts = suggestion_texts()
            assert len(texts) == 3, f"建议条数异常: {texts}"
            tags = [suggestion_items().nth(i).locator(".el-tag").inner_text().strip() for i in range(3)]
            assert tags == ["任务", "日程", "笔记"], f"建议分组顺序异常: {tags}"
            assert all("DS分组" in t for t in texts), f"建议缺少摘要/标题: {texts}"
            req_q = parse_qs(urlparse(search_requests[-1]).query).get("q", [""])[0]
            assert req_q == "DS分组", f"前端未 trim 搜索词，实际请求 q={req_q!r}"
            assert not page_errors, f"Ctrl+K 用例出现未捕获异常: {page_errors[:2]}"
            return "Ctrl+K 聚焦；建议按任务→日程→笔记分组并展示摘要；请求 q 已 trim"

        record("SEARCH-UI-001", "Ctrl+K 聚焦与建议分组", probe_ui_ctrl_k_and_grouping)

        def probe_ui_escape():
            reset_all()
            seed_task("DSESC任务")
            open_dashboard()
            type_search("DSESC")
            wait_suggestions(1)
            page.keyboard.press("Escape")
            wait_until(lambda: page.locator(".el-autocomplete-suggestion:visible").count() == 0,
                       msg="ESC 未关闭建议列表")
            assert search_input().input_value() == "DSESC", "ESC 不应清空搜索词"
            assert "全局搜索" in (page.evaluate("document.activeElement && document.activeElement.placeholder") or ""), \
                "ESC 后输入框失焦"
            return "ESC 关闭建议且保留输入与焦点"

        record("SEARCH-UI-002", "ESC 关闭建议", probe_ui_escape)

        def probe_ui_task_jump():
            reset_all()
            seed_task("DS任务跳转目标")
            seed_task("DS任务跳转陪跑")
            page_errors.clear()
            open_dashboard()
            type_search("DS任务跳转目标")
            wait_suggestions(1)
            suggestion_items().first.click()
            wait_until(lambda: "/#/tasks" in page.url, msg="点击任务建议未跳转任务页")
            q = hash_query(page.url)
            assert q.get("q") == "DS任务跳转目标", f"任务跳转 q 参数异常: {q}"
            wait_until(lambda: page.locator("input[placeholder='搜索任务标题 / 描述，回车确认']").input_value() == "DS任务跳转目标",
                       msg="任务页搜索框未回填 q")
            wait_until(lambda: page.locator("tr.el-table__row:visible").count() == 1, msg="任务页未按 q 过滤")
            assert "DS任务跳转目标" in page.locator("tr.el-table__row:visible").first.inner_text()
            assert not page_errors, f"任务跳转出现未捕获异常: {page_errors[:2]}"
            return "任务结果跳转 /tasks?q=…，搜索框回填且列表仅剩 1 条"

        record("SEARCH-UI-003", "任务结果跳转与筛选", probe_ui_task_jump)

        def probe_ui_note_jump():
            reset_all()
            seed_note("DS笔记跳转目标", content="目标正文")
            seed_note("DS笔记跳转陪跑", content="陪跑正文")
            open_dashboard()
            type_search("DS笔记跳转目标")
            wait_suggestions(1)
            suggestion_items().first.click()
            wait_until(lambda: "/#/notes" in page.url, msg="点击笔记建议未跳转笔记页")
            q = hash_query(page.url)
            assert q.get("q") == "DS笔记跳转目标", f"笔记跳转 q 参数异常: {q}"
            wait_until(lambda: page.locator("input[placeholder='搜索标题 / 内容']").input_value() == "DS笔记跳转目标",
                       msg="笔记页搜索框未回填 q")
            wait_until(lambda: page.locator(".note-item").count() == 1, msg="笔记列表未按 q 过滤")
            assert "DS笔记跳转目标" in page.locator(".note-item").first.inner_text()
            return "笔记结果跳转 /notes?q=…，搜索框回填且列表仅剩 1 条"

        record("SEARCH-UI-004", "笔记结果跳转与筛选", probe_ui_note_jump)

        def probe_ui_schedule_jump():
            reset_all()
            target = TODAY + timedelta(days=30)
            seed_schedule("DS日程跳转目标", f"{target.isoformat()}T10:00:00", f"{target.isoformat()}T11:00:00",
                          location="DS日程跳转地点")
            open_dashboard()
            type_search("DS日程跳转目标")
            wait_suggestions(1)
            suggestion_items().first.click()
            wait_until(lambda: "/#/schedules" in page.url, msg="点击日程建议未跳转日程页")
            q = hash_query(page.url)
            assert q.get("q") == "DS日程跳转目标", f"日程跳转 q 参数异常: {q}"
            assert q.get("date") == target.isoformat(), \
                f"日程跳转缺少 date 参数（当前 dist 未重建时 q/date 均缺失）: {q}"
            wait_until(lambda: page.locator(".day-title").count() > 0, msg="日程页未渲染")
            title = page.locator(".day-title").inner_text()
            expected_day = f"{target.year}年{target.month:02d}月{target.day:02d}日"
            assert expected_day in title, f"日历未定位到命中日期: {title!r} 期望含 {expected_day}"
            assert "1 项日程" in title, f"当日日程未按 q 过滤: {title!r}"
            wait_until(lambda: page.locator(".event-title").count() == 1, msg="当日列表未显示命中日程")
            assert "DS日程跳转目标" in page.locator(".event-title").first.inner_text()
            hint = page.locator(".search-hint")
            assert hint.count() == 1 and "DS日程跳转目标" in hint.inner_text(), "日程页缺少搜索筛选提示"
            return f"日程结果跳转 /schedules?q=…&date={target.isoformat()} 并定位/过滤（待重建后复跑确认）"

        record("SEARCH-UI-005", "日程结果跳转、定位与筛选", probe_ui_schedule_jump)

        def probe_ui_no_result_placeholder():
            reset_all()
            open_dashboard()
            type_search("DS绝对不存在的词ZZZ")
            wait_suggestions(1)
            text = suggestion_items().first.inner_text()
            assert "未找到相关内容" in text, f"无结果占位项文案异常: {text!r}"
            page_errors.clear()
            suggestion_items().first.click()
            page.wait_for_timeout(500)
            hash_part = page.url.split("#", 1)[1] if "#" in page.url else ""
            assert hash_part in ("", "/"), f"点击占位项发生跳转: {page.url}"
            value = search_input().input_value()
            assert value == "DS绝对不存在的词ZZZ", \
                f"占位项污染搜索框（期望保留关键词，实际 {value!r}）"
            assert page.locator(".el-autocomplete-suggestion:visible").count() == 0, "占位项点击后建议未关闭"
            assert not page_errors, f"占位项用例出现未捕获异常: {page_errors[:2]}"
            return "无结果占位项不跳转、不改写输入框（待重建后复跑确认）"

        record("SEARCH-UI-006", "无结果占位项不改写输入", probe_ui_no_result_placeholder)

        def probe_ui_stale_response():
            reset_all()
            seed_task("DSRACE-A任务")
            seed_task("DSRACE-B任务")
            page.add_init_script(RACE_PATCH)
            open_dashboard()
            inp = search_input()
            inp.fill("DSRACE-A")
            page.wait_for_timeout(500)   # A 已发出，响应被延迟 1.8s
            inp.fill("DSRACE-B")         # B 快速返回
            page.wait_for_timeout(3000)  # 等 A 的迟到响应落地
            assert inp.input_value() == "DSRACE-B", "搜索框值被旧响应改写"
            texts = suggestion_texts()
            assert texts, "竞态用例最终无建议"
            assert all("DSRACE-B" in t for t in texts), f"过期响应覆盖了新关键词建议: {texts}"
            assert not any("DSRACE-A" in t for t in texts), f"建议中出现旧关键词结果: {texts}"
            return f"慢响应被丢弃，最终建议对应最新关键词（{len(texts)} 条，待重建后复跑确认）"

        record("SEARCH-UI-007", "过期响应不覆盖新建议", probe_ui_stale_response)

        def probe_ui_search_failure():
            reset_all()
            seed_task("DS失败恢复任务")
            open_dashboard()
            page_errors.clear()
            page.route("**/api/search*", lambda r: r.abort())
            try:
                type_search("DS失败恢复", expect_response=False)
                wait_until(lambda: page.locator(".el-message--error").count() > 0,
                           timeout=5, msg="搜索请求失败无任何错误提示（静默失败）")
                toast = page.locator(".el-message--error").first.inner_text()
                assert page.locator(".el-autocomplete-suggestion:visible li").count() == 0, "搜索失败后仍展示建议"
                assert not page_errors, f"搜索失败出现未捕获异常: {page_errors[:2]}"
            finally:
                page.unroute("**/api/search*")
            page.locator(".el-message--error").first.wait_for(state="hidden", timeout=6000)
            type_search("DS失败恢复")
            wait_suggestions(1)
            return f"失败有全局提示（非静默，文案 {toast[:24]!r}），恢复后搜索正常"

        record("SEARCH-UI-008", "搜索请求失败反馈与恢复", probe_ui_search_failure)

        def probe_ui_overlong_query():
            reset_all()
            seed_task("DS超长恢复任务")
            open_dashboard()
            page_errors.clear()
            type_search("超" * 120)
            wait_until(lambda: page.locator(".el-message--error").count() > 0, timeout=5,
                       msg="超长关键词 422 无错误提示")
            toast = page.locator(".el-message--error").first.inner_text()
            assert "100" in toast, f"422 提示未说明长度限制: {toast!r}"
            assert page.locator(".el-autocomplete-suggestion:visible li").count() == 0, "422 后仍展示建议"
            assert not page_errors, f"超长关键词出现未捕获异常: {page_errors[:2]}"
            page.locator(".el-message--error").first.wait_for(state="hidden", timeout=6000)
            type_search("DS超长恢复")
            wait_suggestions(1)
            return "120 字关键词 422 有提示、无崩溃，缩短后可恢复搜索"

        record("SEARCH-UI-009", "超长关键词 422 降级", probe_ui_overlong_query)

        def probe_ui_blank_and_clear():
            reset_all()
            seed_task("DS清空任务")
            open_dashboard()
            inp = search_input()
            search_requests.clear()
            inp.fill("   ")
            page.wait_for_timeout(800)
            assert not search_requests, f"纯空白输入不应请求 /api/search: {search_requests}"
            assert page.locator(".el-autocomplete-suggestion:visible").count() == 0, "空白输入仍展示建议"

            type_search("DS清空")
            wait_suggestions(1)
            inp.hover()
            page.locator(".el-autocomplete .el-input__clear").click()
            page.wait_for_timeout(500)
            assert inp.input_value() == "", "清空按钮未清空输入"
            assert page.locator(".el-autocomplete-suggestion:visible").count() == 0, "清空后建议未关闭"
            return "空白输入不发请求/不出建议；clearable 清空后建议关闭"

        record("SEARCH-UI-010", "空白输入与清空建议", probe_ui_blank_and_clear)

        def probe_ui_xss_and_special():
            reset_all()
            xss = '<img src=x onerror="window.__xss=1">DS建议XSS'
            seed_task(xss)
            seed_task("折扣100%DS商品")
            open_dashboard()
            type_search("DS建议XSS")
            wait_suggestions(1)
            text = suggestion_items().first.inner_text()
            assert xss in text, f"建议标题未按字面展示: {text!r}"
            assert not page.evaluate("window.__xss === 1"), "搜索建议标题 XSS 被执行"
            assert page.locator(".el-autocomplete-suggestion:visible img").count() == 0, \
                "建议把标题渲染成了 HTML 元素"
            type_search("100%")
            wait_suggestions(1)
            assert any("折扣100%DS商品" in t for t in suggestion_texts()), "特殊字符 % 搜索未命中"
            return "建议标题按字面转义、无脚本执行；% 按字面搜索命中"

        record("SEARCH-UI-011", "建议 XSS 与特殊字符", probe_ui_xss_and_special)

        def probe_ui_keyboard_select():
            reset_all()
            seed_task("DS键盘选择任务")
            open_dashboard()
            type_search("DS键盘选择")
            wait_suggestions(1)
            page.keyboard.press("ArrowDown")
            page.keyboard.press("Enter")
            wait_until(lambda: "/#/tasks" in page.url, msg="键盘选择后未跳转")
            q = hash_query(page.url)
            assert q.get("q") == "DS键盘选择任务", f"键盘选择路由参数异常: {q}"
            wait_until(lambda: page.locator("tr.el-table__row:visible").count() == 1, msg="键盘选择后列表未过滤")
            return "ArrowDown + Enter 选中建议并跳转正确路由"

        record("SEARCH-UI-012", "键盘选择建议", probe_ui_keyboard_select)

        def probe_ui_ctrl_k_other_page():
            reset_all()
            page.goto(f"{BASE}/#/tasks", wait_until="networkidle", timeout=30000)
            page.wait_for_timeout(400)
            page.keyboard.press("Control+k")
            wait_until(lambda: "全局搜索" in (page.evaluate("document.activeElement && document.activeElement.placeholder") or ""),
                       msg="非首页 Ctrl+K 未聚焦全局搜索框")
            return "任务页按 Ctrl+K 同样聚焦全局搜索框"

        record("SEARCH-UI-013", "非首页 Ctrl+K 聚焦", probe_ui_ctrl_k_other_page)

        def probe_ui_single_char():
            reset_all()
            seed_task("税DS单字任务")
            open_dashboard()
            type_search("税")
            wait_suggestions(1)
            assert any("税DS单字任务" in t for t in suggestion_texts()), "单字符搜索未命中目标"
            return "单字符关键词正常出建议"

        record("SEARCH-UI-014", "单字符搜索", probe_ui_single_char)

        browser.close()

    # ================= 汇总输出 =================
    print("=" * 70)
    passed = sum(1 for r in results if r["passed"])
    print(f"total={len(results)} passed={passed} failed={len(results) - passed}")
    print(f"page_errors={len(page_errors)}")
    for e in page_errors[:10]:
        print(f"  !! pageerror: {e[:200]}")
    for r in results:
        if not r["passed"]:
            print(f"  FAILED {r['id']} {r['name']}: {r['detail']}")

    raw = {
        "area": AREA, "base": BASE,
        "started_at": STARTED_AT,
        "finished_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "pages": pages, "tests": results,
    }
    RESULT_JSON.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"result -> {RESULT_JSON}")
finally:
    server.terminate()
    try:
        server.wait(timeout=10)
    except Exception:
        server.kill()
    log_fh.close()
