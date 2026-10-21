# -*- coding: utf-8 -*-
"""notes 区域深度 E2E 探测（端口 8218，独立库隔离，正式可重复运行资产）。

覆盖：
- 页面加载 / 空态 / 控制台与网络错误
- API 边界：分页参数、搜索/标签长度、标题/正文/标签清洗、null 字段、404、LIKE 转义
- 新建（默认标题、选中定位、连续新建）、保存按钮 dirty 状态、正文/多标签 allow-create
- 空标题 / 纯空格标题保存、200 字符标题
- 新建时未保存内容保护（取消 / 确认）、筛选态新建不误选覆盖（历史回归点）
- 置顶与排序、置顶不吞未保存编辑（数据丢失回归点）
- 搜索（标题 / 内容 / 防抖 / 清空 / 无结果 / 特殊字符 / 超长 422 / 分页复位）
- 标签筛选、分页翻页、删除后页码自愈
- 删除取消 / 确认、删除失败反馈、列表/保存失败反馈
- 路由参数 q / highlight（RAG 命中跳转）
- XSS 标题转义
- AI 总结：未保存拦截 + 未配置 LLM 的友好降级（不强行断言生成任务）
- 笔记删除时清理定时任务悬挂引用（笔记 × 任务联动）
"""
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
BACKEND = ROOT / "backend"
RESULTS = ROOT / "dev" / "e2e" / "probe_results"
AREA = "notes"
DB = RESULTS / f"{AREA}.db"
FIN_DIR = RESULTS / f"{AREA}-fin"
DEBUG_DIR = RESULTS / f"{AREA}-debug"
SHOTS = RESULTS / AREA / "shots"
LOG = RESULTS / f"{AREA}.server.log"
PORT = 8218
BASE = f"http://127.0.0.1:{PORT}"
STARTED_AT = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

RESULTS.mkdir(parents=True, exist_ok=True)
SHOTS.mkdir(parents=True, exist_ok=True)
if DB.exists():
    DB.unlink()
if FIN_DIR.exists():
    shutil.rmtree(FIN_DIR)
if DEBUG_DIR.exists():
    shutil.rmtree(DEBUG_DIR)

env = os.environ.copy()
env["DATABASE_URL"] = f"sqlite:///{DB.as_posix()}"
env["FINANCE_DATA_DIR"] = str(FIN_DIR)
env["DEBUG_DATA_DIR"] = str(DEBUG_DIR)
env["PYTHONIOENCODING"] = "utf-8"
env["SEED_ON_STARTUP"] = "0"  # 探测库保持空库起点，计数类断言才确定
# 本区域要验证「未配置 LLM 时的降级」，显式清空 .env 兜底模型配置
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

results = []   # {"id","name","passed","detail"}
console_errors = []
page_errors = []
failed_reqs = []
page_report = {"console_errors": [], "page_errors": [], "failed_reqs": []}
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
            urllib.request.urlopen(f"{BASE}/api/notes", timeout=2)
            return True
        except Exception:
            time.sleep(1)
    return False


def wait_until(fn, timeout=6000, interval=120, message="等待超时"):
    deadline = time.time() + timeout / 1000.0
    last = None
    while time.time() < deadline:
        try:
            v = fn()
            if v:
                return v
        except Exception as e:  # 轮询期间元素重绘/失效不算失败
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


def api_create(title, **kw):
    payload = {"title": title}
    payload.update(kw)
    return api("POST", "/api/notes", json=payload)


def all_notes():
    items, page_no = [], 1
    while True:
        body = api("GET", "/api/notes", params={"page": page_no, "page_size": 100}).json()
        items += body["items"]
        if len(items) >= body["total"] or not body["items"]:
            break
        page_no += 1
    return items


def reset_notes():
    """清空探测库笔记，保证每个用例计数确定。

    正常走 API；若历史脏数据让列表接口 500（tags=NULL 之类），直接清表兜底，
    保证探针本身可重复运行。
    """
    try:
        ids = [n["id"] for n in all_notes()]
        for i in ids:
            api("DELETE", f"/api/notes/{i}")
    except Exception:
        with sqlite3.connect(DB) as conn:
            conn.execute("DELETE FROM notes")
            conn.commit()
    for job in api("GET", "/api/jobs").json():
        api("DELETE", f"/api/jobs/{job['id']}")
    api("DELETE", "/api/debug/client-errors")
    return len(ids) if "ids" in locals() else 0


def total_api():
    return api("GET", "/api/notes", params={"page_size": 1}).json()["total"]


def client_error_count():
    return api("GET", "/api/debug/client-errors").json()["total"]


def tasks_total():
    return api("GET", "/api/tasks", params={"page_size": 1}).json()["total"]


def open_notes():
    """全新挂载 /notes（hash 路由同文档导航不重置组件状态，reload 保证隔离）。"""
    page.goto(f"{BASE}/#/notes", wait_until="networkidle", timeout=30000)
    page.reload(wait_until="networkidle", timeout=30000)
    page.wait_for_selector(".note-list-card", timeout=10000)
    page.wait_for_timeout(400)


def goto_notes_query(qs):
    page.goto(f"{BASE}/#/notes?{qs}", wait_until="networkidle", timeout=30000)
    page.wait_for_timeout(900)


# ---------- 选择器 ----------
def rows():
    return page.locator(".note-item")


def row(title):
    return page.locator(".note-item", has_text=title).first


def active_title():
    item = page.locator(".note-item.active")
    if not item.count():
        return None
    return item.first.locator(".note-item-title").inner_text().strip()


def header_text():
    return page.locator(".note-list-card .list-header span").first.inner_text().strip()


def header_count():
    m = re.search(r"（(\d+)）", header_text())
    assert m, f"列表标题数字缺失: {header_text()!r}"
    return int(m.group(1))


def search_box():
    return page.locator("input[placeholder='搜索标题 / 内容']")


def tag_filter():
    return page.locator(".note-list-card .el-select").first


def title_input():
    return page.locator(".note-editor-card .title-input input")


def content_input():
    return page.locator(".note-editor-card .content-input textarea")


def save_btn():
    return page.locator(".note-editor-card .editor-header button").last


def pin_btn():
    return page.locator(".note-editor-card .editor-header button").first


def ai_btn():
    return page.locator(".note-editor-card .editor-header button", has_text="AI 总结")


def delete_btn():
    return page.locator(".note-editor-card .editor-header button", has_text="删除")


def editor_empty():
    return page.locator(".note-editor-card .el-empty")


def active_page():
    return page.locator(".pager .el-pager li.is-active").inner_text().strip()


def pager_item(n):
    return page.locator(".pager .el-pager li", has_text=str(n)).first


def toast_ok(text):
    return wait_until(lambda: page.locator(".el-message--success", has_text=text).count() > 0,
                      timeout=4000, message=f"未出现成功提示 {text!r}")


def toast_err():
    return wait_until(lambda: page.locator(".el-message--error").count() > 0,
                      timeout=4000, message="未出现错误提示")


def msgbox():
    return page.locator(".el-message-box:visible")


def confirm_box(yes=True):
    box = msgbox()
    assert box.count() > 0, "确认框未出现"
    box.locator("button", has_text="确定" if yes else "取消").click()
    page.wait_for_timeout(500)


def select_note(title):
    row(title).click()
    wait_until(lambda: active_title() == title, message=f"未选中笔记 {title!r}")


def pick_tag_filter(label):
    tag_filter().click()
    page.locator(".el-select-dropdown:visible .el-select-dropdown__item", has_text=label).first.click()
    page.wait_for_timeout(700)


def clear_tag_filter():
    tag_filter().hover()
    tag_filter().locator(".el-select__clear").click()
    page.wait_for_timeout(700)


def console_mark():
    return len(console_errors)


def uncaught_since(mark):
    return [t for t in console_errors[mark:] if "Uncaught" in t or "unhandled" in t.lower()]


def add_tag_via_enter(text):
    page.locator(".note-editor-card .tags-select").click()
    page.wait_for_timeout(200)
    inp = page.locator(".note-editor-card .tags-select input")
    inp.fill(text)
    page.keyboard.press("Enter")
    page.wait_for_timeout(300)
    assert page.locator(".note-editor-card .tags-select .el-tag", has_text=text).count() > 0, \
        f"标签 {text!r} 未加入编辑器"


def click_new():
    page.locator(".note-list-card .list-header button", has_text="新建").click()


# 原生 confirm（切换/新建时未保存保护）统一拦截，用例内切换预期
dialog_log = {"messages": [], "accept": False}


def _on_dialog(d):
    dialog_log["messages"].append(d.message)
    if dialog_log["accept"]:
        d.accept()
    else:
        d.dismiss()


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
        page.on("requestfailed",
                lambda r: failed_reqs.append(f"{r.method} {r.url}") if "favicon" not in r.url else None)
        page.on("dialog", _on_dialog)

        # ---------- 页面级侦察 ----------
        def probe_page():
            console_errors.clear()
            page_errors.clear()
            failed_reqs.clear()
            reset_notes()
            open_notes()
            assert page.locator(".note-list-card").count() == 1, "笔记列表卡片缺失"
            assert page.locator(".note-editor-card").count() == 1, "编辑器卡片缺失"
            assert page.locator(".note-list-card .list-header button", has_text="新建").count() == 1, "新建按钮缺失"
            assert header_count() == 0, f"空库计数异常: {header_text()}"
            assert page.locator(".note-list-card .el-empty").count() == 1, "空库列表未见空态"
            assert page.locator(".note-list-card .el-empty__description", has_text="暂无笔记").count() == 1, \
                "空态文案不是「暂无笔记」"
            assert editor_empty().count() == 1 and "选择左侧笔记" in editor_empty().inner_text(), \
                "空态编辑器引导缺失"
            assert page.locator(".pager .el-pagination").count() == 1, "分页器缺失"
            assert not [e for e in page_errors], f"页面加载报错: {page_errors[:2]}"
            assert not console_errors, f"页面加载控制台报错: {console_errors[:2]}"
            # 页面侦察快照：后续异常态用例会故意制造 422 / 网络失败，不能污染 pages 报告
            page_report.update(
                console_errors=list(console_errors),
                page_errors=list(page_errors),
                failed_reqs=list(failed_reqs),
            )
            return "空态/列表/编辑器/分页器正常，无控制台错误"
        record("NOTES-PAGE-001", "页面加载与空态", probe_page)

        # ---------- API 层边界 ----------
        def probe_api_list_params():
            reset_notes()
            assert api("GET", "/api/notes?page=0").status == 422, "page=0 应 422"
            assert api("GET", "/api/notes?page=-1").status == 422, "page=-1 应 422"
            assert api("GET", "/api/notes?page_size=0").status == 422, "page_size=0 应 422"
            assert api("GET", "/api/notes?page_size=101").status == 422, "page_size=101 应 422"
            assert api("GET", "/api/notes?page_size=100").status == 200, "page_size=100 应 200"
            r = api("GET", "/api/notes?page=99")
            assert r.status == 200 and r.json()["items"] == [], "不存在页应返回空列表而非 500"
            assert api("GET", "/api/notes", params={"q": "x" * 101}).status == 422, "q 超 100 字符应 422"
            assert api("GET", "/api/notes", params={"q": "x" * 100}).status == 200, "q 100 字符应 200"
            assert api("GET", "/api/notes", params={"tag": "x" * 21}).status == 422, "tag 超 20 字符应 422"
            assert api("GET", "/api/notes", params={"tag": "x" * 20}).status == 200, "tag 20 字符应 200"
            assert api("GET", "/api/notes", params={"pinned": "maybe"}).status == 422, "非法 pinned 应 422"
            return "分页 / q / tag / pinned 参数边界均符合预期"
        record("NOTES-API-001", "列表参数边界", probe_api_list_params)

        def probe_api_crud_validation():
            reset_notes()
            assert api_create("").status == 422, "空标题应 422"
            assert api_create("   ").status == 422, "纯空格标题应 422"
            assert api_create("x" * 201).status == 422, "201 字符标题应 422"
            r = api_create("x" * 200)
            assert r.status == 201 and len(r.json()["title"]) == 200, "200 字符标题应接受"
            assert api("POST", "/api/notes", json={"title": "ok", "content": "x" * 50001}).status == 422, \
                "正文 50001 应 422"
            assert api_create("正文边界", content="x" * 50000).status == 201, "正文 50000 应接受"
            body = api_create("标签清洗", tags=[" 报销 ", "报销", "", "x" * 30] + [f"t{i}" for i in range(12)]).json()
            assert body["tags"][0] == "报销" and body["tags"][1] == "x" * 20, f"标签清洗异常: {body['tags']}"
            assert len(body["tags"]) == 10, f"标签应截断为 10 个: {len(body['tags'])}"
            assert api("GET", "/api/notes/999999").status == 404
            assert api("PUT", "/api/notes/999999", json={"title": "x"}).status == 404
            assert api("DELETE", "/api/notes/999999").status == 404
            assert api("PUT", f"/api/notes/{body['id']}", json={"title": ""}).status == 422, "更新空标题应 422"
            return "创建/更新/删除校验与 200/50000 边界正确"
        record("NOTES-API-002", "创建与更新校验边界", probe_api_crud_validation)

        def probe_api_null_fields():
            """显式 null 不能穿透成 500：title/content/tags/pinned 均为非空列。"""
            reset_notes()
            nid = api_create("null 字段基线", content="正文", tags=["税务"], pinned=True).json()["id"]
            bad = []
            for field in ("title", "content", "tags", "pinned"):
                r = api("PUT", f"/api/notes/{nid}", json={field: None})
                if r.status != 422:
                    bad.append(f"{field}->{r.status}")
            assert not bad, f"null 字段未被拦截（应为 422）: {bad}"
            body = api("GET", f"/api/notes/{nid}").json()
            assert body["title"] == "null 字段基线" and body["content"] == "正文", "被拒绝后基线数据被改"
            return "四个非空字段的显式 null 均返回 422 且不落库"
        record("NOTES-API-003", "更新显式 null 字段不 500", probe_api_null_fields)

        def probe_api_whitespace_update():
            """纯空格标题：更新接口必须与创建同口径拒绝（并防响应模型 500）。"""
            reset_notes()
            nid = api_create("空格标题基线").json()["id"]
            r = api("PUT", f"/api/notes/{nid}", json={"title": "   "})
            assert r.status == 422, f"更新纯空格标题应 422，实际 {r.status}: {r.text()[:120]}"
            assert api("GET", f"/api/notes/{nid}").json()["title"] == "空格标题基线", "标题被空格覆盖"
            return "更新纯空格标题 422，原标题保留"
        record("NOTES-API-004", "更新纯空格标题被拒绝", probe_api_whitespace_update)

        def probe_api_search_escape():
            reset_notes()
            api_create("进度100%的核对", content="含百分号")
            api_create("A_B 对账", content="含下划线")
            api_create("反斜杠 C\\D 说明", content="含反斜杠")
            api_create("普通笔记", content="普通内容")
            titles = [n["title"] for n in api("GET", "/api/notes", params={"q": "%"}).json()["items"]]
            assert titles == ["进度100%的核对"], f"q=% 应只字面匹配: {titles}"
            titles = [n["title"] for n in api("GET", "/api/notes", params={"q": "_"}).json()["items"]]
            assert titles == ["A_B 对账"], f"q=_ 应只字面匹配: {titles}"
            titles = [n["title"] for n in api("GET", "/api/notes", params={"q": "\\"}).json()["items"]]
            assert titles == ["反斜杠 C\\D 说明"], f"q=\\ 应只字面匹配: {titles}"
            titles = [n["title"] for n in api("GET", "/api/notes", params={"q": "普通内容"}).json()["items"]]
            assert titles == ["普通笔记"], f"内容搜索失败: {titles}"
            return "标题/内容 + % / _ / 反斜杠 LIKE 字面转义正确"
        record("NOTES-API-005", "搜索通配符与反斜杠转义", probe_api_search_escape)

        def probe_api_tag_filter():
            reset_notes()
            api_create("标签-组合", tags=["税务", "报销"])
            api_create("标签-前缀干扰", tags=["税务筹划"])
            api_create("标签-百分号", tags=["100%完成"])
            api_create("标签-下划线", tags=["a_b"])
            api_create("标签-引号", tags=['a"b'])
            api_create("标签-反斜杠", tags=["a\\b"])
            cases = [("税务", 1), ("税", 0), ("100%完成", 1), ("a_b", 1), ('a"b', 1), ("a\\b", 1)]
            bad = []
            for tag, expect in cases:
                got = api("GET", "/api/notes", params={"tag": tag}).json()
                if got["total"] != expect:
                    bad.append(f"{tag!r}:{got['total']}≠{expect}")
            assert not bad, f"标签过滤异常: {bad}"
            tags = api("GET", "/api/notes/tags").json()["tags"]
            assert len(tags) == len(set(tags)), "标签列表未去重"
            return "标签精确匹配 + % / _ / 引号 / 反斜杠均正确，标签列表去重"
        record("NOTES-API-006", "标签过滤特殊字符", probe_api_tag_filter)

        def probe_api_note_job_link():
            """删除笔记时清理定时任务 last_note_id 悬挂引用（笔记×任务联动回归）。"""
            reset_notes()
            nid = api_create("任务引用笔记").json()["id"]
            job = api("POST", "/api/jobs", json={
                "name": "引用笔记的定时任务", "prompt": "x",
                "schedule_type": "interval", "interval_minutes": 60,
            }).json()
            with sqlite3.connect(DB) as conn:
                conn.execute("UPDATE scheduled_jobs SET last_note_id=? WHERE id=?", (nid, job["id"]))
                conn.commit()
            assert api("GET", "/api/jobs").json()[0]["last_note_id"] == nid, "前置条件未写入"
            assert api("DELETE", f"/api/notes/{nid}").status == 204
            assert api("GET", "/api/jobs").json()[0]["last_note_id"] is None, "删除笔记后悬挂引用未清理"
            return "删除笔记后定时任务 last_note_id 置空"
        record("NOTES-API-007", "删除笔记清理任务引用", probe_api_note_job_link)

        # ---------- UI：新建 / 保存 ----------
        def probe_ui_create_default():
            reset_notes()
            open_notes()
            click_new()
            wait_until(lambda: rows().count() == 1, message="新建后列表未出现")
            wait_until(lambda: active_title() == "未命名笔记", message="新建笔记未被选中")
            assert header_count() == 1, f"列表计数未更新: {header_text()}"
            assert title_input().input_value() == "未命名笔记", "标题输入框未载入"
            assert content_input().input_value() == "", "新笔记正文应为空"
            assert save_btn().inner_text().strip() == "已保存" and save_btn().is_disabled(), \
                "新笔记保存按钮应显示「已保存」且禁用"
            api_total = total_api()
            assert api_total == 1, f"API 侧笔记数={api_total}"
            assert row("未命名笔记").locator(".note-item-snip").inner_text().strip() in ("", "—"), \
                "空正文摘要异常"
            return "默认标题/选中/空正文/保存按钮初始态均正确"
        record("NOTES-UI-001", "UI 新建笔记默认态", probe_ui_create_default)

        def probe_ui_dirty_and_save():
            reset_notes()
            nid = api_create("保存-dirty 基线", content="原始正文").json()["id"]
            open_notes()
            select_note("保存-dirty 基线")
            assert save_btn().inner_text().strip() == "已保存" and save_btn().is_disabled(), "初始应为已保存"
            title_input().fill("保存-dirty 改名")
            wait_until(lambda: save_btn().inner_text().strip() == "保存", message="改名后按钮未变「保存」")
            assert not save_btn().is_disabled(), "脏状态下保存按钮仍禁用"
            title_input().fill("保存-dirty 基线")
            wait_until(lambda: save_btn().inner_text().strip() == "已保存", message="改回原值后未复位")
            assert save_btn().is_disabled(), "改回原值后保存按钮未禁用"
            title_input().fill("保存-dirty 改名")
            save_btn().click()
            toast_ok("已保存")
            wait_until(lambda: active_title() == "保存-dirty 改名", message="保存后列表标题未更新")
            wait_until(lambda: save_btn().inner_text().strip() == "已保存" and save_btn().is_disabled(),
                       message="保存后按钮未复位")
            body = api("GET", f"/api/notes/{nid}").json()
            assert body["title"] == "保存-dirty 改名" and body["content"] == "原始正文", "API 侧保存结果不符"
            return "脏状态切换 / 改回复位 / 保存落库与列表刷新正确"
        record("NOTES-UI-002", "保存按钮 dirty 状态与保存", probe_ui_dirty_and_save)

        def probe_ui_edit_content_tags():
            reset_notes()
            nid = api_create("编辑-正文标签").json()["id"]
            api_create("编辑-既有标签", tags=["共用标签"])
            open_notes()
            select_note("编辑-正文标签")
            # 打开标签下拉，能从全局标签池选择已有标签
            page.locator(".note-editor-card .tags-select").click()
            page.wait_for_timeout(300)
            page.locator(".el-select-dropdown:visible .el-select-dropdown__item", has_text="共用标签").first.click()
            page.wait_for_timeout(300)
            assert page.locator(".note-editor-card .tags-select .el-tag", has_text="共用标签").count() == 1, \
                "选择既有标签失败"
            content_input().fill("第一行\n第二行：多行正文内容")
            add_tag_via_enter("新标签甲")
            add_tag_via_enter("新标签乙")
            save_btn().click()
            toast_ok("已保存")
            wait_until(lambda: "新标签甲" in row("编辑-正文标签").inner_text(), message="列表标签未刷新")
            body = api("GET", f"/api/notes/{nid}").json()
            assert body["content"] == "第一行\n第二行：多行正文内容", f"正文未保存: {body['content']!r}"
            assert body["tags"] == ["共用标签", "新标签甲", "新标签乙"], f"标签未保存: {body['tags']}"
            assert "新标签甲" in row("编辑-正文标签").inner_text(), "列表未展示新标签"
            assert "多行正文" in row("编辑-正文标签").locator(".note-item-snip").inner_text(), "摘要未更新"
            return "多行正文 + allow-create 多标签（含既有标签）保存并即时刷新"
        record("NOTES-UI-003", "编辑正文与多标签 allow-create", probe_ui_edit_content_tags)

        def probe_ui_blank_title_save():
            reset_notes()
            nid = api_create("空标题-基线", content="保留正文").json()["id"]
            open_notes()
            select_note("空标题-基线")
            console_errors.clear()
            page_errors.clear()
            title_input().fill("")
            save_btn().click()
            toast_err()
            assert title_input().input_value() == "", "校验失败后标题被重置"
            assert content_input().input_value() == "保留正文", "校验失败后正文丢失"
            assert not save_btn().is_disabled(), "校验失败后保存按钮不应禁用"
            body = api("GET", f"/api/notes/{nid}").json()
            assert body["title"] == "空标题-基线", "空标题被落库"
            assert not [e for e in page_errors], f"空标题保存出现未捕获异常: {page_errors[:2]}"
            return "空标题被后端 422 拦截，有错误提示且编辑器内容保持"
        record("NOTES-UI-004", "空标题保存反馈", probe_ui_blank_title_save)

        def probe_ui_title_200():
            reset_notes()
            open_notes()
            click_new()
            wait_until(lambda: active_title() == "未命名笔记", message="新建未选中")
            long_title = "长" * 200
            title_input().fill(long_title)
            save_btn().click()
            toast_ok("已保存")
            wait_until(lambda: active_title() == long_title, message="200 字符标题未生效")
            assert len(title_input().input_value()) == 200, "200 字符标题被截断"
            assert page.locator(".note-editor-card .title-input .el-input__count").inner_text().startswith("200 /"), \
                "字数统计未显示 200"
            return "200 字符标题保存与字数统计正常"
        record("NOTES-UI-005", "200 字符标题边界", probe_ui_title_200)

        # ---------- UI：未保存保护 / 连续新建 ----------
        def probe_ui_unsaved_new_guard():
            reset_notes()
            api_create("保护-A", content="")
            api_create("保护-B", content="")
            open_notes()
            select_note("保护-A")
            content_input().fill("A 的未保存内容")
            assert save_btn().inner_text().strip() == "保存", "前置条件：应处于脏状态"
            dialog_log["messages"].clear()
            dialog_log["accept"] = False
            click_new()
            page.wait_for_timeout(500)
            assert dialog_log["messages"], "有未保存修改时新建未弹确认"
            assert "未保存" in dialog_log["messages"][0], f"确认文案未说明未保存: {dialog_log['messages'][0]}"
            assert rows().count() == 2, "取消新建后笔记数变化"
            assert active_title() == "保护-A", "取消新建后选中被切走"
            assert content_input().input_value() == "A 的未保存内容", "取消新建后未保存内容丢失"
            assert total_api() == 2, "取消新建仍创建了笔记"
            # 确认放弃 → 新建并选中（只应确认一次，不得因 select() 的脏检查再弹一次）
            dialog_log["messages"].clear()
            dialog_log["accept"] = True
            click_new()
            wait_until(lambda: rows().count() == 3, message="确认新建后列表未增加")
            wait_until(lambda: active_title() == "未命名笔记", message="确认新建后未选中新笔记")
            assert len(dialog_log["messages"]) == 1, \
                f"确认新建后重复弹出未保存确认 {len(dialog_log['messages'])} 次"
            assert title_input().input_value() == "未命名笔记", "确认新建后编辑器不是新笔记"
            assert content_input().input_value() == "", "新笔记继承了旧内容"
            assert total_api() == 3, "确认新建后 API 计数不符"
            a = [n for n in all_notes() if n["title"] == "保护-A"][0]
            assert a["content"] == "", f"新建操作覆盖了原笔记内容: {a['content']!r}"
            return "取消保留编辑 / 确认新建并选中新笔记，原笔记未被覆盖"
        record("NOTES-UI-006", "新建时未保存内容保护（回归点）", probe_ui_unsaved_new_guard)

        def probe_ui_unsaved_switch_guard():
            reset_notes()
            api_create("切换-A")
            api_create("切换-B")
            open_notes()
            select_note("切换-A")
            content_input().fill("A 的未保存修改")
            dialog_log["messages"].clear()
            dialog_log["accept"] = False
            row("切换-B").click()
            page.wait_for_timeout(500)
            assert dialog_log["messages"], "脏状态切换笔记未弹确认"
            assert active_title() == "切换-A" and content_input().input_value() == "A 的未保存修改", \
                "取消切换后仍被切走或内容丢失"
            dialog_log["accept"] = True
            row("切换-B").click()
            wait_until(lambda: active_title() == "切换-B", message="确认切换后未选中 B")
            assert content_input().input_value() == "", "切换后编辑器残留 A 的内容"
            return "脏状态切换取消保留 / 确认后切换，均正确"
        record("NOTES-UI-007", "切换笔记未保存保护", probe_ui_unsaved_switch_guard)

        def probe_ui_consecutive_create():
            reset_notes()
            open_notes()
            click_new()
            wait_until(lambda: active_title() == "未命名笔记", message="首次新建未选中")
            title_input().fill("连续新建-A")
            save_btn().click()
            toast_ok("已保存")
            wait_until(lambda: active_title() == "连续新建-A", message="A 未保存")
            click_new()
            wait_until(lambda: rows().count() == 2, message="第二次新建列表未增加")
            wait_until(lambda: active_title() == "未命名笔记", message="第二次新建未定位到新笔记")
            assert title_input().input_value() == "未命名笔记" and content_input().input_value() == "", \
                "第二次新建定位到了错误笔记"
            title_input().fill("连续新建-B")
            save_btn().click()
            toast_ok("已保存")
            wait_until(lambda: active_title() == "连续新建-B", message="B 未保存")
            titles = {n["title"] for n in all_notes()}
            assert titles == {"连续新建-A", "连续新建-B"}, f"连续新建结果异常: {titles}"
            return "连续新建每次都定位到刚创建的笔记，不覆盖上一条"
        record("NOTES-UI-008", "连续新建定位", probe_ui_consecutive_create)

        def probe_ui_create_under_tag_filter():
            """历史回归：筛选态下新建笔记不在结果中，不得乱选第一条覆盖原笔记。"""
            reset_notes()
            api_create("筛选-原笔记", content="原内容", tags=["甲"])
            open_notes()
            pick_tag_filter("甲")
            assert rows().count() == 1, "前置条件：标签筛选应只剩 1 条"
            select_note("筛选-原笔记")
            click_new()
            page.wait_for_timeout(1200)
            assert active_title() == "筛选-原笔记", f"筛选态新建后选中被切走: {active_title()!r}"
            assert title_input().input_value() == "筛选-原笔记", "编辑器被新建笔记覆盖"
            assert page.locator(".el-message--warning", has_text="不在当前筛选").count() > 0, \
                "未提示新笔记不在当前筛选结果中"
            assert total_api() == 2, "新笔记未创建"
            original = [n for n in all_notes() if n["title"] == "筛选-原笔记"][0]
            assert original["content"] == "原内容", "原笔记被覆盖"
            clear_tag_filter()
            wait_until(lambda: rows().count() == 2, message="清空标签筛选后未恢复全量")
            return "筛选态新建保留原选中并提示，原笔记零覆盖"
        record("NOTES-UI-009", "筛选态新建不误选覆盖（回归点）", probe_ui_create_under_tag_filter)

        def probe_ui_discard_draft_after_missed_create():
            """确认「放弃修改并新建」但新笔记被筛选隐藏时，旧草稿必须真正丢弃。"""
            reset_notes()
            api_create("放弃-原笔记", content="服务端原文", tags=["乙"])
            open_notes()
            pick_tag_filter("乙")
            select_note("放弃-原笔记")
            content_input().fill("确认放弃的草稿")
            dialog_log["messages"].clear()
            dialog_log["accept"] = True
            click_new()
            wait_until(lambda: total_api() == 2, message="新笔记未创建")
            page.wait_for_timeout(700)
            assert active_title() == "放弃-原笔记", "新笔记被筛选隐藏时应保持原选中"
            assert content_input().input_value() == "服务端原文", \
                f"确认放弃后旧草稿仍未丢弃（继续保存会误改原笔记）: {content_input().input_value()!r}"
            assert save_btn().inner_text().strip() == "已保存" and save_btn().is_disabled(), \
                "确认放弃后保存按钮仍处于可保存草稿状态"
            return "确认放弃后的草稿已回滚到服务端原文，避免误保存"
        record("NOTES-UI-025", "筛选态放弃草稿后新建回滚", probe_ui_discard_draft_after_missed_create)

        # ---------- UI：置顶 ----------
        def probe_ui_pin_sort():
            reset_notes()
            api_create("置顶-旧笔记", pinned=True)
            page.wait_for_timeout(1100)
            api_create("普通-新笔记", pinned=False)
            open_notes()
            assert "置顶-旧笔记" in rows().first.inner_text(), "置顶笔记未排在首位"
            assert row("置顶-旧笔记").locator("svg").count() > 0, "置顶行缺星标图标"
            select_note("普通-新笔记")
            assert "置顶" in pin_btn().inner_text() and "取消" not in pin_btn().inner_text(), "置顶按钮初始态异常"
            pin_btn().click()
            toast_ok("已置顶")
            wait_until(lambda: row("普通-新笔记").locator("svg").count() > 0, message="置顶后星标未出现")
            wait_until(lambda: "取消置顶" in pin_btn().inner_text(), message="按钮未变取消置顶")
            body = [n for n in all_notes() if n["title"] == "普通-新笔记"][0]
            assert body["pinned"] is True, "API 未置顶"
            pins = [n["pinned"] for n in all_notes()]
            assert pins == sorted(pins, reverse=True), f"列表顺序未置顶优先: {[(n['title'], n['pinned']) for n in all_notes()]}"
            pin_btn().click()
            toast_ok("已取消置顶")
            wait_until(lambda: "置顶" in pin_btn().inner_text() and "取消" not in pin_btn().inner_text(),
                       message="取消置顶按钮未复位")
            body = [n for n in all_notes() if n["title"] == "普通-新笔记"][0]
            assert body["pinned"] is False, "API 未取消置顶"
            return "置顶排序 / 星标 / 按钮文案 / API 状态均正确"
        record("NOTES-UI-010", "置顶与排序", probe_ui_pin_sort)

        def probe_ui_pin_preserves_dirty():
            """回归点：置顶不得用服务端返回值覆盖未保存的标题/正文。"""
            reset_notes()
            api_create("置顶-保护编辑", content="原始正文")
            open_notes()
            select_note("置顶-保护编辑")
            content_input().fill("未保存的新正文")
            title_input().fill("置顶-保护编辑-改")
            pin_btn().click()
            page.wait_for_timeout(1000)
            assert content_input().input_value() == "未保存的新正文", \
                f"置顶后未保存正文被覆盖: {content_input().input_value()!r}"
            assert title_input().input_value() == "置顶-保护编辑-改", "置顶后未保存标题被覆盖"
            assert save_btn().inner_text().strip() == "保存" and not save_btn().is_disabled(), \
                "置顶后脏状态丢失（保存按钮变成已保存）"
            return "置顶操作保留未保存标题/正文，脏状态不丢失"
        record("NOTES-UI-011", "置顶不吞未保存编辑（回归点）", probe_ui_pin_preserves_dirty)

        # ---------- UI：搜索 / 标签筛选 ----------
        def probe_ui_search():
            reset_notes()
            api_create("搜索-标题命中", content="普通内容")
            api_create("搜索-普通", content="描述里藏着独家关键词ZQ17")
            api_create("搜索-干扰项", content="其他内容")
            open_notes()
            search_box().fill("独家关键词ZQ17")
            wait_until(lambda: rows().count() == 1, message="内容关键词未过滤")
            assert "搜索-普通" in rows().first.inner_text(), "内容搜索命中错误"
            assert header_count() == 1, f"搜索结果计数异常: {header_text()}"
            search_box().fill("搜索-标题命中")
            wait_until(lambda: rows().count() == 1 and "搜索-标题命中" in rows().first.inner_text(),
                       message="标题关键词未过滤")
            search_box().fill("绝不存在xyz")
            wait_until(lambda: rows().count() == 0, message="无结果列表未清空")
            assert page.locator(".note-list-card .el-empty__description", has_text="暂无笔记").count() == 1, \
                "无结果未见空态"
            search_box().hover()
            page.locator(".note-list-card .el-input__clear").click()
            wait_until(lambda: rows().count() == 3, message="清空搜索后未恢复")
            assert search_box().input_value() == "", "清空后搜索框未复位"
            return "标题/内容搜索、无结果空态、清空恢复均正常"
        record("NOTES-UI-012", "搜索（标题/内容/空态/清空）", probe_ui_search)

        def probe_ui_search_debounce():
            reset_notes()
            api_create("防抖-笔记")
            open_notes()
            reqs = []
            page.on("request", lambda r: reqs.append(r.url) if re.search(r"/api/notes(\?.*)?$", r.url) else None)
            search_box().fill("防")
            page.wait_for_timeout(90)
            search_box().fill("防抖")
            page.wait_for_timeout(90)
            search_box().fill("防抖-笔记")
            wait_until(lambda: rows().count() == 1, message="防抖搜索未生效")
            page.wait_for_timeout(600)
            list_reqs = [u for u in reqs if "q=%E9%98%B2%E6%8A%96-%E7%AC%94%E8%AE%B0" in u]
            assert len(list_reqs) == 1, f"防抖失效：最终关键词请求了 {len(list_reqs)} 次"
            return "连续输入 3 次仅 1 次最终请求（300ms 防抖生效）"
        record("NOTES-UI-013", "搜索防抖", probe_ui_search_debounce)

        def probe_ui_search_special_chars():
            reset_notes()
            api_create("进度100%完成")
            api_create("A_B 对账")
            api_create("干扰-100x完成")
            api_create("干扰-axb")
            open_notes()
            search_box().fill("%")
            wait_until(lambda: rows().count() == 1 and "进度100%完成" in rows().first.inner_text(),
                       message="搜索 % 未按字面匹配")
            search_box().fill("_")
            wait_until(lambda: rows().count() == 1 and "A_B 对账" in rows().first.inner_text(),
                       message="搜索 _ 未按字面匹配")
            return "UI 搜索 % / _ 均按字面匹配，不误命中干扰项"
        record("NOTES-UI-014", "搜索特殊字符（%/ _）", probe_ui_search_special_chars)

        def probe_ui_search_too_long():
            reset_notes()
            api_create("超长搜索-陪跑")
            open_notes()
            page_errors.clear()
            mark = console_mark()
            err0 = client_error_count()
            search_box().fill("超" * 120)
            toast_err()
            page.wait_for_timeout(800)
            uncaught = uncaught_since(mark)
            fatal = [e for e in page_errors]
            assert not fatal, f"超长搜索出现未捕获异常: {fatal[:2]}"
            assert not uncaught, f"超长搜索产生未捕获 Promise 拒绝: {uncaught[:2]}"
            assert client_error_count() == err0, "超长搜索触发了前端报错采集（未捕获异常）"
            assert rows().count() == 1, "超长搜索失败后列表被清空"
            search_box().fill("")
            wait_until(lambda: rows().count() == 1, message="超长搜索失败后无法恢复")
            return "超长搜索 422 有提示、列表保持、无未捕获异常"
        record("NOTES-UI-015", "超长搜索词 422 优雅降级", probe_ui_search_too_long)

        def probe_ui_tag_filter():
            reset_notes()
            api_create("标签-A", tags=["税务", "报销"])
            api_create("标签-B", tags=["结账"])
            api_create("标签-C")
            open_notes()
            tag_filter().click()
            page.wait_for_timeout(300)
            options = page.locator(".el-select-dropdown:visible .el-select-dropdown__item").all_inner_texts()
            for t in ("税务", "报销", "结账"):
                assert any(t == o.strip() for o in options), f"标签下拉缺 {t}: {options}"
            page.keyboard.press("Escape")
            pick_tag_filter("税务")
            assert rows().count() == 1 and "标签-A" in rows().first.inner_text(), "标签筛选失败"
            assert header_count() == 1, f"标签筛选计数异常: {header_text()}"
            pick_tag_filter("结账")
            assert rows().count() == 1 and "标签-B" in rows().first.inner_text(), "切换标签筛选失败"
            clear_tag_filter()
            assert rows().count() == 3, f"清空标签筛选未恢复: {rows().count()}"
            assert tag_filter().locator(".el-select__placeholder span").inner_text().strip() in ("", "全部标签"), \
                "清空后下拉未复位"
            return "标签选项去重展示 / 切换筛选 / clear 复位均正常"
        record("NOTES-UI-016", "标签筛选", probe_ui_tag_filter)

        # ---------- UI：分页 ----------
        def probe_ui_pagination():
            reset_notes()
            for i in range(25):
                api_create(f"分页-笔记{i:02d}")
                page.wait_for_timeout(15)
            open_notes()
            assert header_count() == 25, f"总数异常: {header_text()}"
            assert rows().count() == 20, f"首页应 20 行: {rows().count()}"
            pager_item(2).click()
            wait_until(lambda: active_page() == "2", message="未切到第 2 页")
            wait_until(lambda: rows().count() == 5, message=f"第 2 页应 5 行，实际 {rows().count()}")
            return "25 条 → 首页 20 / 第 2 页 5，页码高亮正确"
        record("NOTES-UI-017", "分页翻页", probe_ui_pagination)

        def probe_ui_search_resets_page():
            reset_notes()
            for i in range(25):
                api_create(f"复位-笔记{i:02d}")
            open_notes()
            pager_item(2).click()
            wait_until(lambda: active_page() == "2", message="未切到第 2 页")
            search_box().fill("复位-笔记00")
            wait_until(lambda: rows().count() == 1, message="搜索未过滤")
            assert active_page() == "1", f"搜索后未回到第 1 页: {active_page()}"
            assert header_count() == 1, "搜索计数未更新"
            search_box().fill("")
            wait_until(lambda: rows().count() == 20, message="清空搜索未恢复首页")
            assert active_page() == "1", "清空搜索后页码异常"
            return "第 2 页搜索自动回第 1 页，清空后仍第 1 页"
        record("NOTES-UI-018", "搜索与分页联动（回第 1 页）", probe_ui_search_resets_page)

        def probe_ui_delete_heals_page():
            """在第 2 页删除唯一一条：页数收缩后应自动回第 1 页，不能停在空白页。"""
            reset_notes()
            for i in range(21):
                api_create(f"自愈-笔记{i:02d}")
            open_notes()
            pager_item(2).click()
            wait_until(lambda: active_page() == "2", message="未切到第 2 页")
            wait_until(lambda: rows().count() == 1, message=f"第 2 页应 1 行，实际 {rows().count()}")
            select_note(rows().first.locator(".note-item-title").inner_text().strip())
            delete_btn().click()
            page.wait_for_timeout(400)
            confirm_box(True)
            wait_until(lambda: header_count() == 20, message="删除后总数未更新")
            wait_until(lambda: active_page() == "1", message=f"删除后仍停留在不存在页码: {active_page()}")
            wait_until(lambda: rows().count() == 20, message="删除后未回到有数据的第 1 页")
            assert page.locator(".note-editor-card .el-empty").count() == 1, "删除后编辑器未回空态"
            return "末页唯一记录删除后自动收敛到第 1 页（20 行）"
        record("NOTES-UI-019", "分页边界-删除后自愈", probe_ui_delete_heals_page)

        # ---------- UI：删除 ----------
        def probe_ui_delete_cancel():
            reset_notes()
            api_create("删除-取消保留")
            open_notes()
            select_note("删除-取消保留")
            delete_btn().click()
            page.wait_for_timeout(400)
            box = msgbox()
            assert box.count() > 0, "删除确认框未出现"
            text = box.inner_text()
            assert "删除-取消保留" in text and "不可恢复" in text, f"确认框文案异常: {text[:80]}"
            confirm_box(False)
            assert row("删除-取消保留").count() == 1, "取消删除后笔记消失"
            assert active_title() == "删除-取消保留", "取消删除后选中丢失"
            assert total_api() == 1, "取消删除仍删除了数据"
            return "取消删除保留笔记与编辑器状态"
        record("NOTES-UI-020", "删除确认-取消", probe_ui_delete_cancel)

        def probe_ui_delete_confirm():
            reset_notes()
            deleted = api_create("删除-确认消失").json()
            api_create("删除-陪跑")
            open_notes()
            select_note("删除-确认消失")
            delete_btn().click()
            page.wait_for_timeout(400)
            confirm_box(True)
            wait_until(lambda: row("删除-确认消失").count() == 0, message="确认删除后行仍在")
            wait_until(lambda: header_count() == 1, message="删除后总数未更新")
            assert editor_empty().count() == 1, "删除当前笔记后编辑器未回空态"
            assert total_api() == 1, "API 侧删除结果不符"
            assert api("GET", f"/api/notes/{deleted['id']}").status == 404, "被删笔记详情仍可访问"
            return "确认删除后列表/计数/编辑器/API 联动正确"
        record("NOTES-UI-021", "删除确认-确定", probe_ui_delete_confirm)

        # ---------- UI：路由 / XSS ----------
        def probe_ui_route_q():
            reset_notes()
            api_create("路由-Q目标")
            api_create("路由-Q陪跑")
            open_notes()
            goto_notes_query("q=%E8%B7%AF%E7%94%B1-Q%E7%9B%AE%E6%A0%87")
            wait_until(lambda: search_box().input_value() == "路由-Q目标", message="路由 q 未回填搜索框")
            wait_until(lambda: rows().count() == 1 and "路由-Q目标" in rows().first.inner_text(),
                       message="路由 q 未过滤列表")
            return "?q= 路由参数回填搜索框并过滤"
        record("NOTES-UI-022", "路由参数 q 预填与过滤", probe_ui_route_q)

        def probe_ui_route_highlight():
            reset_notes()
            target = api_create("路由-Highlight目标", content="RAG 命中正文").json()
            api_create("路由-Highlight干扰")
            open_notes()
            goto_notes_query(f"q=%E5%B9%B2%E6%89%B0&highlight={target['id']}")
            wait_until(lambda: title_input().input_value() == "路由-Highlight目标",
                       message="highlight 未把目标笔记载入编辑器（不在当前搜索结果中时应单独按 id 拉取）")
            assert content_input().input_value() == "RAG 命中正文", "编辑器未载入 highlight 笔记正文"
            assert search_box().input_value() == "干扰", "q 参数未同时生效"
            # 目标被 q 过滤掉时列表无高亮项属于设计：编辑器独立按 id 拉取
            assert page.locator(".note-item.active").count() == 0, "目标不在结果中却出现列表高亮"
            open_notes()  # 重新挂载，清掉上一步的编辑器选中态
            goto_notes_query("highlight=abc")
            page.wait_for_timeout(600)
            assert page.locator(".note-item.active").count() == 0, "非法 highlight 不应选中任何笔记"
            assert editor_empty().count() == 1, "非法 highlight 应显示编辑器空态"
            return "?highlight= 跨筛选定位生效，非法值不误选"
        record("NOTES-UI-023", "路由参数 highlight（RAG 跳转）", probe_ui_route_highlight)

        def probe_ui_xss():
            reset_notes()
            api_create('<img src=x onerror="window.__xss=1">', content='<script>window.__xss2=1</script>')
            open_notes()
            page.wait_for_timeout(600)
            assert page.evaluate("window.__xss === undefined") is True, "标题 XSS 被执行"
            assert page.evaluate("window.__xss2 === undefined") is True, "正文 XSS 被执行"
            item = row('<img src=x onerror="window.__xss=1">')
            assert item.count() == 1, "特殊字符标题未按字面展示"
            assert item.locator("img").count() == 0, "标题被当作 HTML 渲染出 img"
            select_note('<img src=x onerror="window.__xss=1">')
            assert title_input().input_value() == '<img src=x onerror="window.__xss=1">', "标题输入框未按字面展示"
            assert content_input().input_value() == '<script>window.__xss2=1</script>', "正文未按字面展示"
            return "标题/正文特殊字符均按字面转义，无脚本执行"
        record("NOTES-UI-024", "标题/正文 XSS 防护", probe_ui_xss)

        # ---------- AI 总结 ----------
        def probe_ui_ai_guard_dirty():
            reset_notes()
            api_create("AI-脏检查", content="原始")
            open_notes()
            select_note("AI-脏检查")
            content_input().fill("改了没保存")
            ai_btn().click()
            wait_until(lambda: page.locator(".el-message--warning", has_text="请先保存").count() > 0,
                       message="脏状态 AI 总结未提示先保存")
            assert "/notes" in page.url, f"脏状态 AI 总结仍跳转: {page.url}"
            return "未保存时点击 AI 总结被拦截并提示先保存"
        record("NOTES-AI-001", "AI 总结未保存拦截", probe_ui_ai_guard_dirty)

        def probe_ui_ai_unconfigured():
            reset_notes()
            api_create("AI总结-目标笔记", content="核对进项发票与销项发票")
            open_notes()
            select_note("AI总结-目标笔记")
            page_errors.clear()
            tasks_before = tasks_total()
            ai_btn().click()
            try:
                page.wait_for_url(re.compile(r"#/assistant"), timeout=15000)
            except Exception as e:
                raise AssertionError(f"AI 总结未跳转助手页（当前 {page.url}）: {e}")
            bubble = page.locator(".user-bubble", has_text="AI总结-目标笔记").first
            bubble.wait_for(state="visible", timeout=8000)
            # 未配置 LLM：后端 SSE error(need_llm_config) → 错误气泡友好提示，不 500
            err_bubble = page.locator(".error-bubble", has_text="模型未配置").first
            err_bubble.wait_for(state="visible", timeout=12000)
            text = err_bubble.inner_text()
            assert "设置" in text and "API Key" in text, f"降级提示不友好: {text[:120]}"
            fatal = [e for e in page_errors]
            assert not fatal, f"AI 总结降级出现未捕获异常: {fatal[:2]}"
            assert tasks_total() == tasks_before, "未配置 LLM 时不应产生任务"
            return "未配置 LLM 时显示「模型未配置」引导气泡，无 500/未捕获异常/任务副作用"
        record("NOTES-AI-002", "AI 总结未配置 LLM 友好降级", probe_ui_ai_unconfigured)

        # ---------- 异常态：前端反馈 ----------
        def probe_err_list_fail():
            reset_notes()
            api_create("异常-列表加载")
            open_notes()
            page_errors.clear()
            mark = console_mark()
            err0 = client_error_count()
            handler = lambda r: r.abort() if r.request.method == "GET" else r.continue_()
            page.route(re.compile(r"/api/notes(\?.*)?$"), handler)
            search_box().fill("触发失败")
            toast_err()
            page.wait_for_timeout(800)
            page.unroute(re.compile(r"/api/notes(\?.*)?$"), handler)
            loading_mask = page.locator(".note-list-card .el-loading-mask")
            assert loading_mask.count() == 0 or not loading_mask.first.is_visible(), "列表加载失败后 loading 未复位"
            assert not [e for e in page_errors], f"列表加载失败出现未捕获异常: {page_errors[:2]}"
            assert not uncaught_since(mark), f"列表加载失败产生未捕获 Promise 拒绝: {uncaught_since(mark)[:2]}"
            assert client_error_count() == err0, "列表加载失败被采集为未捕获异常"
            search_box().fill("")
            wait_until(lambda: rows().count() == 1, message="加载失败后无法恢复列表")
            return "列表加载失败有错误提示、loading 复位、无未捕获异常，可恢复"
        record("NOTES-ERR-001", "异常态-列表加载失败反馈", probe_err_list_fail)

        def probe_err_save_fail():
            reset_notes()
            nid = api_create("异常-保存失败", content="原始").json()["id"]
            open_notes()
            select_note("异常-保存失败")
            page_errors.clear()
            mark = console_mark()
            content_input().fill("保存失败时保留我")
            handler = lambda r: r.abort() if r.request.method == "PUT" else r.continue_()
            page.route(re.compile(r"/api/notes/\d+$"), handler)
            save_btn().click()
            toast_err()
            page.wait_for_timeout(800)
            page.unroute(re.compile(r"/api/notes/\d+$"), handler)
            assert content_input().input_value() == "保存失败时保留我", "保存失败后编辑内容丢失"
            assert save_btn().inner_text().strip() == "保存" and not save_btn().is_disabled(), \
                "保存失败后按钮未恢复可重试"
            assert not [e for e in page_errors], f"保存失败出现未捕获异常: {page_errors[:2]}"
            assert not uncaught_since(mark), f"保存失败产生未捕获 Promise 拒绝: {uncaught_since(mark)[:2]}"
            assert api("GET", f"/api/notes/{nid}").json()["content"] == "原始", "保存失败却落库"
            save_btn().click()
            toast_ok("已保存")
            return "保存失败有错误提示、内容保留、按钮可重试、无未捕获异常"
        record("NOTES-ERR-002", "异常态-保存失败反馈", probe_err_save_fail)

        def probe_err_delete_fail():
            reset_notes()
            api_create("异常-删除失败")
            open_notes()
            select_note("异常-删除失败")
            page_errors.clear()
            mark = console_mark()
            handler = lambda r: r.abort() if r.request.method == "DELETE" else r.continue_()
            page.route(re.compile(r"/api/notes/\d+$"), handler)
            delete_btn().click()
            page.wait_for_timeout(400)
            confirm_box(True)
            toast_err()
            page.wait_for_timeout(800)
            page.unroute(re.compile(r"/api/notes/\d+$"), handler)
            assert row("异常-删除失败").count() == 1, "删除失败后笔记从列表消失"
            assert active_title() == "异常-删除失败", "删除失败后编辑器状态丢失"
            assert not [e for e in page_errors], f"删除失败出现未捕获异常: {page_errors[:2]}"
            assert not uncaught_since(mark), f"删除失败产生未捕获 Promise 拒绝: {uncaught_since(mark)[:2]}"
            return "删除失败有错误提示、笔记保留、无未捕获异常"
        record("NOTES-ERR-003", "异常态-删除失败反馈", probe_err_delete_fail)

        browser.close()

    # ---------- 汇总输出 ----------
    pages_entry = {
        "route": "/notes", "name": "notes",
        "status": 200,
        "console_errors": page_report["console_errors"],
        "page_errors": page_report["page_errors"],
        "failed_reqs": page_report["failed_reqs"],
    }

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
        "pages": [pages_entry], "tests": results,
    }
    out = RESULTS / f"{AREA}.json"
    out.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"result -> {out}")
finally:
    server.terminate()
    try:
        server.wait(timeout=10)
    except Exception:
        server.kill()
    log_fh.close()
