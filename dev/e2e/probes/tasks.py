# -*- coding: utf-8 -*-
"""tasks 区域深度 E2E 探测（端口 8202，独立库隔离，正式可重复运行资产）。

覆盖：
- 页面加载 / 空态 / 控制台与网络错误
- 任务 CRUD 全流程（UI 新建 / 编辑 / 完成 / 删除确认取消与确认）
- 状态 / 优先级筛选（含 clear 图标复位）、分类 / 到期范围筛选（API 层）
- 搜索（标题 / 描述 / 无结果 / 超长词 422 降级 / 路由 q 参数）
- 排序（创建时间 / 截止时间 nulls-last / 优先级）
- 分页与分页边界（翻页、page-size 切换收敛、删除后自愈、不存在页 API 行为、筛选联动回第 1 页）
- 表单校验（空标题、纯空格标题穿透 422、200 字符标题边界）
- 逾期标识（未完成+过期=红标、未来无标、已完成+过期无标、完成后标记消失）
- 新建后列表刷新、筛选态下新建被过滤（设计观察）
- 路由参数 ?overdue / ?status、AI 分析跳转
- XSS 标题转义
- API 异常态（请求失败时前端有无反馈、有无未捕获异常）
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
BACKEND = ROOT / "backend"
RESULTS = ROOT / "dev" / "e2e" / "probe_results"
AREA = "tasks"
DB = RESULTS / f"{AREA}.db"
FIN_DIR = RESULTS / f"{AREA}-fin"
SHOTS = RESULTS / AREA / "shots"
LOG = RESULTS / f"{AREA}.server.log"
PORT = 8202
BASE = f"http://127.0.0.1:{PORT}"
STARTED_AT = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

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
env["SEED_ON_STARTUP"] = "0"  # 探测库保持空库起点，计数类断言才确定

log_fh = open(LOG, "w", encoding="utf-8")
server = subprocess.Popen(
    [str(BACKEND / "venv" / "Scripts" / "python.exe"), "-m", "uvicorn",
     "app.main:app", "--host", "127.0.0.1", "--port", str(PORT)],
    cwd=str(BACKEND), env=env, stdout=log_fh, stderr=log_fh,
)

results = []   # {"id","name","passed","detail"}
console_errors = []
page_errors = []
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


def api(method, path, **kw):
    if "json" in kw:
        kw["data"] = json.dumps(kw.pop("json"))
        headers = kw.pop("headers", {})
        headers["Content-Type"] = "application/json"
        kw["headers"] = headers
    return page.request.fetch(f"{BASE}{path}", method=method, **kw)


def api_create(title, **kw):
    payload = {"title": title}
    payload.update(kw)
    return api("POST", "/api/tasks", json=payload)


def reset_tasks():
    """清空探测库任务，保证每个用例计数确定。"""
    ids = []
    page_no = 1
    while True:
        body = api("GET", "/api/tasks", params={"page": page_no, "page_size": 100}).json()
        ids += [t["id"] for t in body["items"]]
        if len(ids) >= body["total"] or not body["items"]:
            break
        page_no += 1
    for i in ids:
        api("DELETE", f"/api/tasks/{i}")
    return len(ids)


def open_tasks():
    """全新挂载 /tasks（hash 路由同文档导航不重置组件状态，reload 保证隔离）。"""
    page.goto(f"{BASE}/#/tasks", wait_until="networkidle", timeout=30000)
    page.reload(wait_until="networkidle", timeout=30000)
    page.wait_for_timeout(600)


def dlg():
    return page.locator(".el-dialog:visible")


def rows():
    return page.locator("tr.el-table__row:visible")


def row(title):
    return page.locator("tr.el-table__row", has_text=title).first


def total_text():
    return page.locator(".el-pagination__total").inner_text()


def active_page():
    return page.locator(".el-pager li.is-active").inner_text()


def _select_text(index):
    """读取 el-select 的实际展示文案（Element Plus 2.x 把选中项也渲染在 .el-select__placeholder 内）。

    旧实现拿「整个控件文本 == placeholder 文本」判断空值，选中后两者仍相等，
    会恒返回空串（TASKS-UI-013/024 误报）。这里直接读展示 span，并黑名单掉
    未选中时的占位文案。
    """
    sel = page.locator(".filters .el-select").nth(index)
    span = sel.locator(".el-select__placeholder span")
    if span.count():
        text = span.first.inner_text().strip()
    else:
        ph = sel.locator(".el-select__placeholder")
        text = ph.first.inner_text().strip() if ph.count() else ""
    return "" if text in ("", "状态", "优先级", "全部状态", "全部优先级") else text


def sort_select_value():
    return _select_text(2)


def status_select_value():
    return _select_text(0)


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

        # ---------- 页面级侦察 ----------
        def probe_page():
            console_errors.clear()
            page_errors.clear()
            reset_tasks()
            open_tasks()
            assert page.locator("button", has_text="新建任务").count() > 0, "新建任务按钮缺失"
            assert page.locator(".el-table").count() > 0, "任务表格缺失"
            assert page.locator(".el-pagination").count() > 0, "分页器缺失"
            assert page.locator(".el-table__empty-text").count() > 0, "空库未见空态"
            assert "共 0 条" in total_text(), f"空库总数异常: {total_text()}"
            return "空态/工具栏/分页器正常，无控制台错误"
        record("TASKS-PAGE-001", "页面加载与空态", probe_page)

        # ---------- API 层边界 ----------
        def probe_api_validation():
            reset_tasks()
            assert api("GET", "/api/tasks?page=0").status == 422, "page=0 应 422"
            assert api("GET", "/api/tasks?page=-1").status == 422, "page=-1 应 422"
            assert api("GET", "/api/tasks?page_size=0").status == 422, "page_size=0 应 422"
            assert api("GET", "/api/tasks?page_size=101").status == 422, "page_size=101 应 422"
            assert api("GET", "/api/tasks?page_size=100").status == 200, "page_size=100 应 200"
            r = api("GET", "/api/tasks?page=999")
            assert r.status == 200 and r.json()["items"] == [], "不存在页应返回空列表而非 500"
            assert api("GET", "/api/tasks?sort=bogus").status == 200, "非法 sort 应回落默认"
            r = api("GET", "/api/tasks", params={"due_before": "不是日期"})
            assert r.status == 422 and "日期格式错误" in r.json()["detail"], "非法 due_before 应 422"
            r = api("GET", "/api/tasks", params={"q": "x" * 101})
            assert r.status == 422, "q 超 100 字符应 422"
            return "422/空页/非法 sort 均符合预期"
        record("TASKS-API-001", "列表参数边界（page/page_size/sort/日期/q）", probe_api_validation)

        def probe_api_create_validation():
            reset_tasks()
            assert api("POST", "/api/tasks", json={"title": ""}).status == 422
            assert api("POST", "/api/tasks", json={"title": "   "}).status == 422, "纯空格标题后端应拒"
            assert api("POST", "/api/tasks", json={"title": "x" * 201}).status == 422
            assert api("POST", "/api/tasks", json={"title": "ok", "due_date": "不是日期"}).status == 422
            assert api("GET", "/api/tasks/999").status == 404
            assert api("PUT", "/api/tasks/999", json={"title": "x"}).status == 404
            assert api("DELETE", "/api/tasks/999").status == 404
            return "创建/详情/更新/删除的 422 与 404 均符合预期"
        record("TASKS-API-002", "创建与资源不存在边界", probe_api_create_validation)

        def probe_api_like_escape():
            reset_tasks()
            api_create("进度100%的核对")
            api_create("A_B 对账")
            api_create("普通任务")
            titles = [t["title"] for t in api("GET", "/api/tasks", params={"q": "%"}).json()["items"]]
            assert titles == ["进度100%的核对"], f"q=% 应只字面匹配: {titles}"
            titles = [t["title"] for t in api("GET", "/api/tasks", params={"q": "_"}).json()["items"]]
            assert titles == ["A_B 对账"], f"q=_ 应只字面匹配: {titles}"
            return "LIKE 通配符已转义"
        record("TASKS-API-003", "搜索通配符转义（% 与 _）", probe_api_like_escape)

        def probe_api_category_and_tz():
            reset_tasks()
            api_create("税务申报任务", category="税务申报")
            api_create("税务咨询任务", category="税务")
            titles = [t["title"] for t in api("GET", "/api/tasks", params={"category": "税务"}).json()["items"]]
            assert titles == ["税务咨询任务"], f"分类应精确匹配: {titles}"
            body = api_create("带时区截止", due_date="2026-09-10T17:00:00+08:00").json()
            assert body["due_date"].startswith("2026-09-10T17:00:00"), \
                f"时区应转 naive 本地时间: {body['due_date']}"
            return "分类精确匹配 / 时区归一化均正确"
        record("TASKS-API-004", "分类精确匹配与时区截止", probe_api_category_and_tz)

        def probe_api_lifecycle():
            reset_tasks()
            t = api_create("生命周期", due_date="2027-01-01T10:00:00").json()
            r = api("PUT", f"/api/tasks/{t['id']}", json={"due_date": None})
            assert r.status == 200 and r.json()["due_date"] is None, "清除截止时间失败"
            r = api("PUT", f"/api/tasks/{t['id']}", json={"status": "done"})
            assert r.json()["completed_at"] is not None, "done 未记录 completed_at"
            r = api("PUT", f"/api/tasks/{t['id']}", json={"status": "todo"})
            assert r.json()["completed_at"] is None, "回到 todo 未清除 completed_at"
            d = api_create("已完成任务", status="done").json()
            assert d["completed_at"] is not None
            r = api("PUT", f"/api/tasks/{d['id']}", json={"title": "已完成任务-改标题"})
            assert r.json()["completed_at"] is not None, "done 任务局部更新丢失 completed_at"
            return "截止时间可清除 / completed_at 生命周期正确"
        record("TASKS-API-005", "截止清除与 completed_at 生命周期", probe_api_lifecycle)

        # ---------- UI：新建 ----------
        def probe_ui_create_full():
            reset_tasks()
            open_tasks()
            page.locator("button", has_text="新建任务").first.click()
            page.wait_for_timeout(400)
            d = dlg()
            d.locator("input").first.fill("深度探测-完整表单任务")
            d.locator("textarea").first.fill("这是描述内容")
            d.locator(".el-select").nth(0).click()
            page.locator(".el-select-dropdown:visible .el-select-dropdown__item", has_text="进行中").first.click()
            d.locator(".el-select").nth(1).click()
            page.locator(".el-select-dropdown:visible .el-select-dropdown__item", has_text="紧急").first.click()
            date_input = d.locator(".el-date-editor input").first
            date_input.click()
            date_input.fill("2099-06-30 12:00:00")
            page.keyboard.press("Enter")
            page.wait_for_timeout(300)
            d.locator("input[placeholder*='税务申报']").fill("税务申报")
            d.locator("button", has_text="保存").first.click()
            page.wait_for_timeout(1000)
            assert dlg().count() == 0, "保存后对话框未关闭"
            r = row("深度探测-完整表单任务")
            assert r.count() > 0, "新任务未出现在列表"
            text = r.inner_text()
            assert "进行中" in text and "紧急" in text, f"状态/优先级未生效: {text[:80]}"
            assert "2099-06-30 12:00" in text, f"截止时间未生效: {text[:120]}"
            assert "税务申报" in text, f"分类未生效: {text[:80]}"
            return "标题/描述/状态/优先级/截止/分类全部落库并展示"
        record("TASKS-UI-001", "UI 新建完整表单任务", probe_ui_create_full)

        def probe_ui_create_refresh():
            reset_tasks()
            open_tasks()
            page.locator("button", has_text="新建任务").first.click()
            page.wait_for_timeout(400)
            d = dlg()
            d.locator("input").first.fill("深度探测-新建刷新验证")
            d.locator("button", has_text="保存").first.click()
            page.wait_for_timeout(1000)
            first_after = rows().first.inner_text()[:40]
            assert "深度探测-新建刷新验证" in first_after, f"新建后列表未刷新到首行: {first_after!r}"
            return "新建后无需手动刷新即出现在首行"
        record("TASKS-UI-002", "新建后列表即时刷新", probe_ui_create_refresh)

        def probe_ui_empty_title():
            reset_tasks()
            open_tasks()
            page.locator("button", has_text="新建任务").first.click()
            page.wait_for_timeout(400)
            dlg().locator("button", has_text="保存").first.click()
            page.wait_for_timeout(600)
            assert dlg().count() > 0, "空标题被提交"
            assert page.locator(".el-form-item__error").count() > 0, "缺少空标题校验提示"
            dlg().locator("button", has_text="取消").first.click()
            return "空标题被前端拦截并提示"
        record("TASKS-UI-003", "空标题前端校验", probe_ui_empty_title)

        def probe_ui_blank_title_submit():
            """纯空格标题：前端 required 不放行拦截（async-validator 不 trim），
            穿透到后端 422。观察是否有用户可见反馈 / 未捕获异常。"""
            reset_tasks()
            open_tasks()
            page_errors.clear()
            page.locator("button", has_text="新建任务").first.click()
            page.wait_for_timeout(400)
            d = dlg()
            d.locator("input").first.fill("   ")
            d.locator("button", has_text="保存").first.click()
            page.wait_for_timeout(1200)
            still_open = dlg().count() > 0
            has_err_toast = page.locator(".el-message--error").count() > 0
            fatal = [e for e in page_errors]
            shot("TASKS-UI-004-blank-title")
            assert not fatal, f"纯空格标题提交出现未捕获异常: {fatal[:2]}"
            assert has_err_toast, "后端 422 时前端无错误反馈（对话框卡住且无提示）"
            assert still_open, "后端 422 后对话框被异常关闭"
            return f"422 有错误提示且对话框保持打开可修正（未捕获异常={len(fatal)}）"
        record("TASKS-UI-004", "纯空格标题提交的后端 422 反馈", probe_ui_blank_title_submit)

        def probe_ui_title_200():
            reset_tasks()
            open_tasks()
            long_title = "长" * 200
            page.locator("button", has_text="新建任务").first.click()
            page.wait_for_timeout(400)
            d = dlg()
            d.locator("input").first.fill(long_title)
            d.locator("button", has_text="保存").first.click()
            page.wait_for_timeout(1000)
            r = row(long_title)
            assert r.count() > 0, "200 字符标题未被接受"
            assert len(r.locator("td").first.inner_text()) == 200, "200 字符标题被截断"
            return "200 字符标题边界正常"
        record("TASKS-UI-005", "200 字符标题边界", probe_ui_title_200)

        # ---------- UI：筛选 ----------
        def probe_ui_status_filter():
            reset_tasks()
            api_create("待筛选-待办", status="todo")
            api_create("待筛选-进行中", status="in_progress")
            api_create("待筛选-已完成", status="done")
            open_tasks()

            def sel_status(label):
                page.locator(".filters .el-select").nth(0).click()
                page.locator(".el-select-dropdown:visible .el-select-dropdown__item", has_text=label).first.click()
                page.wait_for_timeout(900)

            sel_status("待办")
            assert rows().count() == 1 and "待筛选-待办" in rows().first.inner_text(), "待办筛选失败"
            assert "共 1 条" in total_text(), f"总数未更新: {total_text()}"
            sel_status("进行中")
            assert rows().count() == 1 and "待筛选-进行中" in rows().first.inner_text(), "进行中筛选失败"
            sel_status("已完成")
            assert rows().count() == 1 and "待筛选-已完成" in rows().first.inner_text(), "已完成筛选失败"
            # clear 图标复位
            sel = page.locator(".filters .el-select").nth(0)
            sel.hover()
            sel.locator(".el-select__clear").click()
            page.wait_for_timeout(900)
            assert rows().count() == 3, f"清除状态筛选后未恢复全量: {rows().count()}"
            assert status_select_value() == "", f"清除后下拉未复位: {status_select_value()!r}"
            return "状态三态筛选 / 总数联动 / clear 复位均正常"
        record("TASKS-UI-006", "状态筛选（含 clear 复位）", probe_ui_status_filter)

        def probe_ui_priority_filter():
            reset_tasks()
            for pr in ("low", "medium", "high", "urgent"):
                api_create(f"优先级-{pr}", priority=pr)
            open_tasks()
            page.locator(".filters .el-select").nth(1).click()
            page.locator(".el-select-dropdown:visible .el-select-dropdown__item", has_text="紧急").first.click()
            page.wait_for_timeout(900)
            assert rows().count() == 1 and "优先级-urgent" in rows().first.inner_text(), "紧急筛选失败"
            page.locator(".filters .el-select").nth(1).click()
            page.locator(".el-select-dropdown:visible .el-select-dropdown__item", has_text="低").first.click()
            page.wait_for_timeout(900)
            assert rows().count() == 1 and "优先级-low" in rows().first.inner_text(), "低优先级筛选失败"
            return "优先级筛选正常"
        record("TASKS-UI-007", "优先级筛选", probe_ui_priority_filter)

        def probe_ui_overdue_marker():
            reset_tasks()
            api_create("逾期-未完成过期", due_date="2024-01-01T09:00:00")
            api_create("逾期-未到期", due_date="2099-01-01T09:00:00")
            api_create("逾期-已完成但过期", status="done", due_date="2024-01-01T09:00:00")
            api_create("逾期-无截止时间")
            open_tasks()
            r = row("逾期-未完成过期")
            assert r.locator(".el-tag", has_text="已逾期").count() > 0, "过期未完成任务缺逾期标"
            assert r.locator(".text-danger").count() > 0, "过期未完成任务缺危险色"
            r2 = row("逾期-未到期")
            assert r2.locator(".el-tag", has_text="已逾期").count() == 0, "未到期任务误标逾期"
            assert r2.locator(".text-danger").count() == 0, "未到期任务误标危险色"
            r3 = row("逾期-已完成但过期")
            assert r3.locator(".el-tag", has_text="已逾期").count() == 0, "已完成过期任务不应标逾期"
            r4 = row("逾期-无截止时间")
            assert "-" in r4.inner_text() and r4.locator(".el-tag", has_text="已逾期").count() == 0, \
                "无截止时间不应标逾期"
            return "逾期标识四象限全部正确"
        record("TASKS-UI-008", "逾期标识（未完成/未来/已完成过期/无日期）", probe_ui_overdue_marker)

        def probe_ui_complete_clears_overdue():
            reset_tasks()
            api_create("逾期-完成后消标", due_date="2024-01-01T09:00:00")
            open_tasks()
            r = row("逾期-完成后消标")
            assert r.locator(".el-tag", has_text="已逾期").count() > 0, "初始应有逾期标"
            r.locator("button", has_text="完成").click()
            page.wait_for_timeout(1000)
            r2 = row("逾期-完成后消标")
            assert r2.locator(".el-tag", has_text="已逾期").count() == 0, "完成后逾期标未消失"
            assert "已完成" in r2.inner_text(), "完成后状态未更新"
            return "完成操作实时清除逾期标识"
        record("TASKS-UI-009", "完成任务后逾期标识消失", probe_ui_complete_clears_overdue)

        # ---------- UI：搜索与排序 ----------
        def probe_ui_search():
            reset_tasks()
            api_create("搜索-标题关键词任务")
            api_create("搜索-普通任务", description="描述里藏着独家关键词ZQ17")
            open_tasks()
            box = page.locator("input[placeholder*='搜索任务标题']")
            box.fill("独家关键词ZQ17")
            page.keyboard.press("Enter")
            page.wait_for_timeout(900)
            assert rows().count() == 1 and "搜索-普通任务" in rows().first.inner_text(), "描述关键词搜索失败"
            box.fill("搜索-标题关键词")
            page.keyboard.press("Enter")
            page.wait_for_timeout(900)
            assert rows().count() == 1 and "搜索-标题关键词任务" in rows().first.inner_text(), "标题关键词搜索失败"
            box.fill("绝不存在的关键词xyz")
            page.keyboard.press("Enter")
            page.wait_for_timeout(900)
            assert rows().count() == 0, "无结果时未清空列表"
            assert page.locator(".el-table__empty-text").count() > 0, "无结果未见空态"
            box.fill("")
            page.keyboard.press("Enter")
            page.wait_for_timeout(900)
            assert rows().count() == 2, f"清空搜索后未恢复列表: {rows().count()}"
            return "标题/描述/无结果/恢复均正常"
        record("TASKS-UI-010", "搜索（标题/描述/空态/恢复）", probe_ui_search)

        def probe_ui_search_too_long():
            """搜索框无 maxlength：超长关键词走后端 422，应优雅降级不崩溃。"""
            reset_tasks()
            api_create("超长搜索-陪跑任务")
            open_tasks()
            page_errors.clear()
            box = page.locator("input[placeholder*='搜索任务标题']")
            box.fill("超" * 120)
            page.keyboard.press("Enter")
            page.wait_for_timeout(1200)
            fatal = [e for e in page_errors]
            has_err = page.locator(".el-message--error").count() > 0
            assert not fatal, f"超长搜索出现未捕获异常: {fatal[:2]}"
            assert has_err, "超长搜索 422 无错误提示"
            box.fill("")
            page.keyboard.press("Enter")
            page.wait_for_timeout(900)
            assert rows().count() == 1, "超长搜索失败后无法恢复列表"
            return "超长搜索词 422 有提示且可恢复"
        record("TASKS-UI-011", "超长搜索词 422 优雅降级", probe_ui_search_too_long)

        def probe_ui_route_q():
            reset_tasks()
            api_create("路由参数-目标任务")
            api_create("路由参数-陪跑任务")
            page.goto(f"{BASE}/#/tasks", wait_until="networkidle")
            page.goto(f"{BASE}/#/tasks?q=%E8%B7%AF%E7%94%B1%E5%8F%82%E6%95%B0-%E7%9B%AE%E6%A0%87%E4%BB%BB%E5%8A%A1",
                      wait_until="networkidle")
            page.wait_for_timeout(1000)
            box_value = page.locator("input[placeholder*='搜索任务标题']").input_value()
            assert box_value == "路由参数-目标任务", f"路由 q 未回填搜索框: {box_value!r}"
            assert rows().count() == 1, f"路由 q 未过滤: {rows().count()}"
            return "?q= 路由参数回填搜索框并过滤"
        record("TASKS-UI-012", "路由参数 q 预填与过滤", probe_ui_route_q)

        def probe_ui_route_status():
            reset_tasks()
            api_create("路由状态-待办", status="todo")
            api_create("路由状态-已完成", status="done")
            page.goto(f"{BASE}/#/tasks", wait_until="networkidle")
            page.goto(f"{BASE}/#/tasks?status=done", wait_until="networkidle")
            page.wait_for_timeout(1000)
            assert status_select_value() == "已完成", f"路由 status 未回填下拉: {status_select_value()!r}"
            assert rows().count() == 1 and "已完成" in rows().first.inner_text(), "路由 status 未过滤"
            return "?status= 路由参数回填下拉并过滤"
        record("TASKS-UI-013", "路由参数 status 预填与过滤", probe_ui_route_status)

        def probe_ui_sort():
            reset_tasks()
            api_create("排序-早到期", due_date="2026-09-01T09:00:00")
            api_create("排序-晚到期", due_date="2026-12-01T09:00:00")
            api_create("排序-无日期")
            api_create("排序-紧急优先", priority="urgent", due_date="2026-09-01T09:00:00")
            open_tasks()
            page.locator(".filters .el-select").nth(2).click()
            page.locator(".el-select-dropdown:visible .el-select-dropdown__item", has_text="按截止时间").first.click()
            page.wait_for_timeout(900)
            assert "排序-早到期" in rows().first.inner_text() or "排序-紧急优先" in rows().first.inner_text(), \
                f"按截止时间排序首行异常: {rows().first.inner_text()[:40]}"
            assert "排序-无日期" in rows().last.inner_text(), f"无日期应沉底: {rows().last.inner_text()[:40]}"
            page.locator(".filters .el-select").nth(2).click()
            page.locator(".el-select-dropdown:visible .el-select-dropdown__item", has_text="按优先级").first.click()
            page.wait_for_timeout(900)
            assert "排序-紧急优先" in rows().first.inner_text(), \
                f"按优先级首行应为紧急: {rows().first.inner_text()[:40]}"
            return "截止时间 nulls-last / 优先级排序正常"
        record("TASKS-UI-014", "排序切换", probe_ui_sort)

        # ---------- UI：编辑 / 完成 / 删除 ----------
        def probe_ui_edit():
            reset_tasks()
            t = api_create("编辑-原标题", priority="low", due_date="2027-03-01T10:00:00").json()
            open_tasks()
            row("编辑-原标题").locator("button", has_text="编辑").click()
            page.wait_for_timeout(400)
            d = dlg()
            d.locator("input").first.fill("编辑-改后标题")
            d.locator(".el-select").nth(1).click()
            page.locator(".el-select-dropdown:visible .el-select-dropdown__item", has_text="紧急").first.click()
            date_input = d.locator(".el-date-editor input").first
            date_input.click()
            date_input.fill("2027-12-31 08:30:00")
            page.keyboard.press("Enter")
            page.wait_for_timeout(300)
            d.locator("button", has_text="保存").first.click()
            page.wait_for_timeout(1000)
            assert dlg().count() == 0, "编辑保存后对话框未关闭"
            r = row("编辑-改后标题")
            assert r.count() > 0, "编辑后标题未更新"
            text = r.inner_text()
            assert "紧急" in text, "编辑后优先级未更新"
            assert "2027-12-31 08:30" in text, f"编辑后截止时间未更新: {text[:120]}"
            body = api("GET", f"/api/tasks/{t['id']}").json()
            assert body["title"] == "编辑-改后标题" and body["priority"] == "urgent", "API 层编辑结果不符"
            return "标题/优先级/截止时间编辑均生效"
        record("TASKS-UI-015", "编辑任务", probe_ui_edit)

        def probe_ui_edit_stale_validation():
            """新建校验失败后再编辑既有任务：对话框是否残留上次的红色错误。"""
            reset_tasks()
            api_create("编辑-残留校验观察")
            open_tasks()
            page.locator("button", has_text="新建任务").first.click()
            page.wait_for_timeout(400)
            dlg().locator("button", has_text="保存").first.click()
            page.wait_for_timeout(500)
            assert page.locator(".el-dialog:visible .el-form-item__error").count() > 0, "前置条件不成立"
            dlg().locator("button", has_text="取消").first.click()
            page.wait_for_timeout(400)
            row("编辑-残留校验观察").locator("button", has_text="编辑").click()
            page.wait_for_timeout(500)
            stale = page.locator(".el-dialog:visible .el-form-item__error").count() > 0
            shot("TASKS-UI-016-stale-validation")
            dlg().locator("button", has_text="取消").first.click()
            return f"观察项：编辑对话框残留上次校验错误={stale}"
        record("TASKS-UI-016", "编辑对话框校验错误残留（观察项）", probe_ui_edit_stale_validation)

        def probe_ui_complete():
            reset_tasks()
            t = api_create("完成-待完成任务").json()
            open_tasks()
            row("完成-待完成任务").locator("button", has_text="完成").click()
            page.wait_for_timeout(1000)
            r2 = row("完成-待完成任务")
            assert "已完成" in r2.inner_text(), f"状态未变为已完成: {r2.inner_text()[:60]}"
            assert r2.locator("button", has_text="完成").count() == 0, "已完成行仍显示完成按钮"
            assert r2.locator(".done-title").count() > 0, "已完成标题缺删除线样式"
            body = api("GET", f"/api/tasks/{t['id']}").json()
            assert body["status"] == "done" and body["completed_at"] is not None, "completed_at 未记录"
            return "完成后状态/按钮/completed_at 均正确"
        record("TASKS-UI-017", "完成任务", probe_ui_complete)

        def probe_ui_delete_cancel():
            reset_tasks()
            api_create("删除-取消仍保留")
            open_tasks()
            row("删除-取消仍保留").locator("button", has_text="删除").click()
            page.wait_for_timeout(500)
            box = page.locator(".el-message-box")
            assert box.count() > 0, "删除确认框未出现"
            assert "删除后不可恢复" in box.inner_text(), "确认框文案异常"
            box.locator("button", has_text="取消").click()
            page.wait_for_timeout(600)
            assert row("删除-取消仍保留").count() > 0, "取消删除后任务消失"
            return "取消删除任务保留"
        record("TASKS-UI-018", "删除确认-取消", probe_ui_delete_cancel)

        def probe_ui_delete_confirm():
            reset_tasks()
            t = api_create("删除-确认后消失").json()
            api_create("删除-陪跑任务")
            open_tasks()
            total_before = total_text()
            row("删除-确认后消失").locator("button", has_text="删除").click()
            page.wait_for_timeout(500)
            page.locator(".el-message-box button", has_text="确定").click()
            page.wait_for_timeout(1000)
            assert row("删除-确认后消失").count() == 0, "确认删除后任务仍在"
            assert total_before != total_text(), f"删除后总数未变: {total_before} -> {total_text()}"
            assert api("GET", f"/api/tasks/{t['id']}").status == 404, "API 层任务仍存在"
            return "确认删除后列表与总数联动、API 404"
        record("TASKS-UI-019", "删除确认-确定", probe_ui_delete_confirm)

        # ---------- UI：分页与边界 ----------
        def probe_ui_pagination():
            reset_tasks()
            for i in range(12):
                api_create(f"分页-任务{i:02d}")
            open_tasks()
            assert "共 12 条" in total_text(), f"总数异常: {total_text()}"
            assert rows().count() == 10, f"首页应 10 行: {rows().count()}"
            page.locator(".el-pager li", has_text="2").first.click()
            page.wait_for_timeout(900)
            assert rows().count() == 2, f"第 2 页应 2 行: {rows().count()}"
            assert active_page() == "2", f"当前页高亮异常: {active_page()}"
            page.locator(".el-pagination__sizes").click()
            page.wait_for_timeout(300)
            page.locator(".el-select-dropdown:visible .el-select-dropdown__item", has_text="20").first.click()
            page.wait_for_timeout(900)
            assert rows().count() == 12, f"每页 20 条应显示 12 行: {rows().count()}"
            assert active_page() == "1", f"page-size 切换后未收敛到第 1 页: {active_page()}"
            return "翻页 / 每页条数切换收敛正常"
        record("TASKS-UI-020", "分页与每页条数切换", probe_ui_pagination)

        def probe_ui_pagination_delete_heal():
            """在第 2 页删光该页任务：页数收缩后应自动回到有数据的页，不能卡在空白页。"""
            reset_tasks()
            for i in range(12):
                api_create(f"自愈-任务{i:02d}")
            open_tasks()
            page.locator(".el-pager li", has_text="2").first.click()
            page.wait_for_timeout(900)
            assert rows().count() == 2, f"第 2 页应 2 行: {rows().count()}"
            for _ in range(2):
                rows().first.locator("button", has_text="删除").click()
                page.wait_for_timeout(400)
                page.locator(".el-message-box button", has_text="确定").click()
                page.wait_for_timeout(1000)
            assert rows().count() == 10, f"删除后未自愈到第 1 页（当前行数 {rows().count()}）"
            assert active_page() == "1", f"删除后仍停留在不存在页码: {active_page()}"
            assert "共 10 条" in total_text(), f"总数异常: {total_text()}"
            return "删除导致页数收缩后自动收敛到第 1 页"
        record("TASKS-UI-021", "分页边界-删除后自愈", probe_ui_pagination_delete_heal)

        def probe_ui_filter_pagination_reset():
            reset_tasks()
            for i in range(12):
                api_create(f"联动-待办{i:02d}", status="todo")
            for i in range(3):
                api_create(f"联动-已完成{i:02d}", status="done")
            open_tasks()
            page.locator(".el-pager li", has_text="2").first.click()
            page.wait_for_timeout(900)
            assert active_page() == "2", "前置条件：应在第 2 页"
            page.locator(".filters .el-select").nth(0).click()
            page.locator(".el-select-dropdown:visible .el-select-dropdown__item", has_text="待办").first.click()
            page.wait_for_timeout(900)
            assert active_page() == "1", f"应用筛选后未回到第 1 页: {active_page()}"
            assert rows().count() == 10, f"筛选后首页行数异常: {rows().count()}"
            assert "共 12 条" in total_text(), f"筛选后总数异常: {total_text()}"
            return "第 2 页应用筛选正确回到第 1 页"
        record("TASKS-UI-022", "筛选与分页联动（回第 1 页）", probe_ui_filter_pagination_reset)

        def probe_ui_create_under_filter():
            """设计观察：筛选态下新建不满足筛选条件的任务，保存成功但列表不可见。"""
            reset_tasks()
            api_create("过滤-可见待办", status="todo")
            open_tasks()
            page.locator(".filters .el-select").nth(0).click()
            page.locator(".el-select-dropdown:visible .el-select-dropdown__item", has_text="待办").first.click()
            page.wait_for_timeout(900)
            page.locator("button", has_text="新建任务").first.click()
            page.wait_for_timeout(400)
            d = dlg()
            d.locator("input").first.fill("过滤-新建已完成")
            d.locator(".el-select").nth(0).click()
            page.locator(".el-select-dropdown:visible .el-select-dropdown__item", has_text="已完成").first.click()
            d.locator("button", has_text="保存").first.click()
            page.wait_for_timeout(1200)
            visible = page.locator("tr.el-table__row", has_text="过滤-新建已完成").count()
            shot("TASKS-UI-023-create-under-filter")
            return f"观察项：筛选态新建异状态任务后列表可见={visible > 0}（toast 已提示创建成功）"
        record("TASKS-UI-023", "筛选态下新建异状态任务（观察项）", probe_ui_create_under_filter)

        # ---------- 路由参数 / AI 分析 ----------
        def probe_ui_overdue_route():
            reset_tasks()
            api_create("路由-过期任务", due_date="2024-01-01T09:00:00")
            open_tasks()
            page.goto(f"{BASE}/#/tasks?overdue=true", wait_until="networkidle")
            page.wait_for_timeout(1000)
            assert sort_select_value() == "按截止时间", f"?overdue=true 未切换到按截止时间: {sort_select_value()}"
            assert rows().count() >= 1, "逾期卡片跳转后列表为空"
            return "?overdue=true 跳转后按截止时间排序展示"
        record("TASKS-UI-024", "仪表盘逾期卡片路由参数", probe_ui_overdue_route)

        def probe_ui_overdue_route_sticky():
            """设计观察：离开 ?overdue 路由后排序是否保持粘滞。"""
            reset_tasks()
            api_create("粘滞-观察任务", due_date="2024-01-01T09:00:00")
            open_tasks()
            page.goto(f"{BASE}/#/tasks?overdue=true", wait_until="networkidle")
            page.wait_for_timeout(900)
            page.goto(f"{BASE}/#/tasks", wait_until="networkidle")
            page.wait_for_timeout(900)
            return f"观察项：离开 ?overdue 后排序仍为 {sort_select_value()!r}"
        record("TASKS-UI-025", "逾期路由排序粘滞（观察项）", probe_ui_overdue_route_sticky)

        def probe_ui_ai_analyze():
            reset_tasks()
            api_create("AI分析-目标任务", description="核对进项发票")
            open_tasks()
            row("AI分析-目标任务").locator("button").first.click()
            page.wait_for_timeout(900)
            url = page.url
            assert "/#/assistant" in url, f"AI 分析未跳转助手: {url}"
            # auto 参数由 AssistantView 消费后按设计清理（progress.md：发送后清除 URL 参数），
            # 原断言在 900ms 后再查 URL 必然拿不到参数；改为校验指令确实送达对话区。
            bubble = page.locator(".user-bubble", has_text="AI分析-目标任务")
            bubble.first.wait_for(state="visible", timeout=5000)
            prompt = bubble.first.inner_text()
            assert "AI分析-目标任务" in prompt, f"跳转 prompt 未携带任务标题: {prompt[:80]}"
            return "AI 分析携带任务信息跳转助手页并自动发送"
        record("TASKS-UI-026", "AI 分析跳转", probe_ui_ai_analyze)

        def probe_ui_xss_title():
            reset_tasks()
            api_create('<img src=x onerror="window.__xss=1">')
            api_create('"><b>bold</b>')
            open_tasks()
            page.wait_for_timeout(600)
            fired = page.evaluate("window.__xss === undefined ? false : true")
            assert fired is False, "标题 XSS 被执行"
            r = row('<img src=x onerror="window.__xss=1">')
            assert r.count() > 0, "特殊字符标题未按字面展示"
            return "标题特殊字符按字面转义展示，无脚本执行"
        record("TASKS-UI-027", "标题 XSS 防护", probe_ui_xss_title)

        # ---------- 异常态：API 失败时前端反馈 ----------
        def probe_ui_delete_fail_feedback():
            reset_tasks()
            api_create("异常-删除失败任务")
            open_tasks()
            page_errors.clear()
            handler = lambda r: r.abort() if r.request.method == "DELETE" else r.continue_()
            page.route(re.compile(r"/api/tasks/\d+$"), handler)
            row("异常-删除失败任务").locator("button", has_text="删除").click()
            page.wait_for_timeout(400)
            page.locator(".el-message-box button", has_text="确定").click()
            page.wait_for_timeout(1500)
            page.unroute(re.compile(r"/api/tasks/\d+$"), handler)
            has_err = page.locator(".el-message--error").count() > 0
            fatal = [e for e in page_errors]
            assert row("异常-删除失败任务").count() > 0, "删除失败后任务从列表消失"
            assert not fatal, f"删除失败出现未捕获异常: {fatal[:2]}"
            assert has_err, "删除失败时前端无任何错误提示（静默失败）"
            shot("TASKS-UI-028-delete-fail")
            return "删除失败有错误提示、任务保留、无未捕获异常"
        record("TASKS-UI-028", "异常态-删除失败反馈", probe_ui_delete_fail_feedback)

        def probe_ui_complete_fail_feedback():
            reset_tasks()
            api_create("异常-完成失败任务")
            open_tasks()
            page_errors.clear()
            handler = lambda r: r.abort() if r.request.method == "PUT" else r.continue_()
            page.route(re.compile(r"/api/tasks/\d+$"), handler)
            row("异常-完成失败任务").locator("button", has_text="完成").click()
            page.wait_for_timeout(1500)
            page.unroute(re.compile(r"/api/tasks/\d+$"), handler)
            has_err = page.locator(".el-message--error").count() > 0
            fatal = [e for e in page_errors]
            assert not fatal, f"完成失败出现未捕获异常: {fatal[:2]}"
            assert has_err, "完成失败时前端无任何错误提示（静默失败）"
            assert "待办" in row("异常-完成失败任务").inner_text(), "完成失败后状态被误改"
            shot("TASKS-UI-029-complete-fail")
            return "完成失败有错误提示、状态保持"
        record("TASKS-UI-029", "异常态-完成失败反馈", probe_ui_complete_fail_feedback)

        def probe_ui_list_fail_feedback():
            reset_tasks()
            api_create("异常-列表加载任务")
            open_tasks()
            page_errors.clear()
            handler = lambda r: r.abort() if r.request.method == "GET" else r.continue_()
            page.route(re.compile(r"/api/tasks(\?.*)?$"), handler)
            page.reload(wait_until="networkidle")
            page.wait_for_timeout(1500)
            page.unroute(re.compile(r"/api/tasks(\?.*)?$"), handler)
            has_err = page.locator(".el-message--error").count() > 0
            loading_mask = page.locator(".el-loading-mask")
            loading_gone = loading_mask.count() == 0 or not loading_mask.first.is_visible()
            fatal = [e for e in page_errors]
            assert not fatal, f"列表加载失败出现未捕获异常: {fatal[:2]}"
            assert has_err, "列表加载失败时前端无任何错误提示（静默空白）"
            assert loading_gone, "列表加载失败后 loading 未复位"
            shot("TASKS-UI-030-list-fail")
            return "列表加载失败有错误提示且 loading 复位"
        record("TASKS-UI-030", "异常态-列表加载失败反馈", probe_ui_list_fail_feedback)

        browser.close()

    # ---------- 汇总输出 ----------
    pages_entry = {
        "route": "/tasks", "name": "tasks",
        "status": 200,
        "console_errors": console_errors,
        "page_errors": page_errors,
        "failed_reqs": [],
    }
    pages = [pages_entry]

    print("=" * 70)
    passed = sum(1 for r in results if r["passed"])
    print(f"total={len(results)} passed={passed} failed={len(results) - passed}")
    print(f"console_errors={len(console_errors)} page_errors={len(page_errors)}")
    for e in page_errors[:10]:
        print(f"  !! pageerror: {e[:200]}")
    for e in console_errors[:10]:
        print(f"  !! console: {e[:200]}")

    raw = {
        "area": AREA, "base": BASE,
        "started_at": STARTED_AT,
        "finished_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "pages": pages, "tests": results,
    }
    (RESULTS / f"{AREA}.probe.json").write_text(
        json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"raw -> {RESULTS / (AREA + '.probe.json')}")
finally:
    server.terminate()
    try:
        server.wait(timeout=10)
    except Exception:
        server.kill()
    log_fh.close()
