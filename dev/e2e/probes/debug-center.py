# -*- coding: utf-8 -*-
"""debug-center 区域深度 E2E 探测（调试中心：问题记录 / 测试运行 / 前端报错）。

覆盖：
- /debug/issues、/debug/runs、/debug/errors 三页侦察（控制台/页面错误/失败请求 + 截图）；
- 问题记录：种子与汇总卡片数字核对、状态/严重度/组合筛选、内联状态下拉即改、
  筛选态下改状态行应消失（回归）、空标题前端校验、新建/编辑/删除（取消+确认双路径）、
  严重度排序；
- 测试运行：列表倒序、详情抽屉（描述/页面表/用例表）、页面行展开错误清单、
  截图网格点击放大与关闭、删除（取消+确认）、删除后 API 404；
- 前端报错：空态、page.evaluate 人为抛错走完整采集链路（window error / unhandledrejection）、
  5 秒同消息去重、行展开堆栈与 UA、清空（取消+确认）、自动刷新开关、汇总卡片联动；
- API 直测：空白标题 422（回归）、非法枚举 422、run_id/截图路径穿越 400/404、
  client-errors 垃圾输入不 500、summary 数字一致性。

可重复运行：独立测试库 + 自有端口 8215；DEBUG_DATA_DIR 指向
dev/e2e/probe_results/debug-center-data/（绝不写真实 backend/data/debug/）。
"""
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
BACKEND = ROOT / "backend"
E2E_DIR = ROOT / "dev" / "e2e"
PROBE_DIR = E2E_DIR / "probe_results" / "debug-center"
SHOTS = PROBE_DIR / "shots"
DB = PROBE_DIR / "debug-center.db"
FIN_DIR = PROBE_DIR / "debug-center-fin"
DEBUG_DIR = PROBE_DIR / "debug-center-data"
RESULT_JSON = E2E_DIR / "probe_results" / "debug-center.json"
PORT = 8215
BASE = f"http://127.0.0.1:{PORT}"
STARTED_AT = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

# 1x1 红色 PNG（真实可渲染，供 el-image 预览）
PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)

# ---------- 环境准备：干净库 + 预置两个测试运行 ----------
for d in (PROBE_DIR, SHOTS):
    d.mkdir(parents=True, exist_ok=True)
if DB.exists():
    DB.unlink()
for d in (FIN_DIR, DEBUG_DIR):
    if d.exists():
        shutil.rmtree(d)


def write_run(run_id, kind, pages, tests, bad_pages, failed_tests, n_shots):
    run_dir = DEBUG_DIR / "runs" / run_id
    shots = run_dir / "shots"
    shots.mkdir(parents=True, exist_ok=True)
    for i in range(n_shots):
        (shots / f"shot-{i}.png").write_bytes(PNG_1X1)
    result = {
        "id": run_id,
        "kind": kind,
        "base": BASE,
        "started_at": "2026-10-20 09:00:00",
        "finished_at": "2026-10-20 09:00:42",
        "totals": {"pages": len(pages), "bad_pages": bad_pages,
                   "tests": len(tests), "failed_tests": failed_tests},
        "pages": pages,
        "tests": tests,
        "extra": {},
    }
    (run_dir / "result.json").write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")


write_run(
    "20261020-090000_recon", "recon",
    pages=[
        {"route": "/tasks", "name": "tasks", "status": 200,
         "console_errors": ["TypeError: boom is not a function"], "page_errors": [], "failed_reqs": []},
        {"route": "/notes", "name": "notes", "status": 200,
         "console_errors": [], "page_errors": [], "failed_reqs": []},
    ],
    tests=[], bad_pages=1, failed_tests=0, n_shots=1,
)
write_run(
    "20261020-093000_interactions", "interactions",
    pages=[],
    tests=[
        {"id": "TC-1", "name": "创建任务", "passed": True, "detail": ""},
        {"id": "TC-2", "name": "删除任务", "passed": False, "detail": "AssertionError: 行未消失"},
        {"id": "TC-3", "name": "编辑任务", "passed": False, "detail": "Timeout 3000ms"},
    ],
    bad_pages=0, failed_tests=2, n_shots=2,
)

env = os.environ.copy()
env["DATABASE_URL"] = f"sqlite:///{DB.as_posix()}"
env["FINANCE_DATA_DIR"] = str(FIN_DIR)
env["DEBUG_DATA_DIR"] = str(DEBUG_DIR)
env["PYTHONIOENCODING"] = "utf-8"
env["SEED_ON_STARTUP"] = "0"  # 干净业务库；debug_center 自有种子（11 条问题）不受影响

server = subprocess.Popen(
    [str(BACKEND / "venv" / "Scripts" / "python.exe"), "-m", "uvicorn",
     "app.main:app", "--host", "127.0.0.1", "--port", str(PORT)],
    cwd=str(BACKEND), env=env,
    stdout=open(PROBE_DIR / "debug-center.server.log", "w"),
    stderr=subprocess.STDOUT,
)

results = []  # (id, name, passed, detail)
pages_report = []


def record(tid, name, fn):
    try:
        fn()
        results.append((tid, name, True, ""))
        print(f"[PASS] {tid} {name}")
    except Exception as e:
        results.append((tid, name, False, str(e)[:400]))
        print(f"[FAIL] {tid} {name}: {str(e)[:400]}")
        try:
            page.screenshot(path=str(SHOTS / f"FAIL-{tid}.png"))
        except Exception:
            pass


def wait_ready(timeout=60):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            urllib.request.urlopen(f"{BASE}/api/debug/summary", timeout=2)
            return True
        except Exception:
            time.sleep(1)
    return False


def api(path, method="GET", payload=None, raw_body=None):
    """直接打 API，返回 (status, json|None)。"""
    data = raw_body
    headers = {}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(f"{BASE}/api{path}", data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            body = resp.read()
            try:
                return resp.status, json.loads(body)
            except json.JSONDecodeError:
                return resp.status, None
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read())
        except Exception:
            return e.code, None


page = None


def goto(route):
    url = f"{BASE}/#{route}"
    # 仅 hash 变化的 page.goto 是 same-document 导航，组件不会重建、筛选会残留；
    # 同 URL 时强制 reload，保证每条用例从干净状态开始。
    if page.url == url:
        page.reload(wait_until="networkidle", timeout=30000)
    else:
        page.goto(url, wait_until="networkidle", timeout=30000)
    page.wait_for_timeout(700)


def visit_page(route, name):
    console_errors, page_errors, failed_reqs = [], [], []
    page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: page_errors.append(str(e)))
    page.on("requestfailed", lambda r: failed_reqs.append(f"{r.method} {r.url}"))
    resp = page.goto(f"{BASE}/#{route}", wait_until="networkidle", timeout=30000)
    page.wait_for_timeout(900)
    page.screenshot(path=str(SHOTS / f"{name}.png"))
    pages_report.append({
        "route": route, "name": name, "status": resp.status if resp else "?",
        "console_errors": console_errors, "page_errors": page_errors,
        "failed_reqs": [f for f in failed_reqs if "favicon" not in f],
    })
    page.remove_listener("console", page.listeners("console")[-1]) if False else None


def summary_api():
    return api("/debug/summary")[1]


def sum_card_texts():
    """issues 页汇总卡片数字文本（P0..P3 + 最近运行 + 报错数）。"""
    return [t.strip() for t in page.locator(".summary-row .sum-num").all_inner_texts()]


def sum_card_num(index):
    """前四张卡片文本形如 "P0\\n4"（带严重度 tag），取末尾数字。"""
    m = re.search(r"(\d+)\s*$", sum_card_texts()[index])
    assert m, f"卡片 {index} 无数字: {sum_card_texts()[index]!r}"
    return int(m.group(1))


def issue_rows():
    return page.locator(".el-table__body tr:visible")


def select_option(label):
    page.locator(".el-select-dropdown__item:visible", has_text=label).first.click()
    page.wait_for_timeout(600)


def pick_filter(select_index, label):
    page.locator(".head-ops .el-select").nth(select_index).click()
    page.wait_for_timeout(400)
    select_option(label)


def throw_js(msg):
    page.evaluate("(m) => setTimeout(() => { throw new Error(m) }, 0)", msg)


def reject_js(msg):
    page.evaluate("(m) => setTimeout(() => { Promise.reject(new Error(m)) }, 0)", msg)


def client_error_count():
    return api("/debug/client-errors")[1]["total"]


try:
    if not wait_ready():
        print("FATAL: server not ready")
        sys.exit(2)

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})

        # ---------- 页面侦察 ----------
        for route, name in (("/debug/issues", "debug-issues"),
                            ("/debug/runs", "debug-runs"),
                            ("/debug/errors", "debug-errors")):
            visit_page(route, name)

        # ---------- 问题记录 ----------
        def tc_iss_001():
            goto("/debug/issues")
            _, lst = api("/debug/issues")
            assert lst["total"] == 11, f"种子问题数={lst['total']}，期望 11"
            s = summary_api()
            assert s["open_issues_by_severity"] == {"P0": 4, "P1": 3, "P2": 4, "P3": 0}, \
                f"API 未决计数 {s['open_issues_by_severity']}"
            cards = sum_card_texts()
            assert sum_card_num(0) == 4, f"P0 卡片 {cards[0]!r} 期望 4"
            assert sum_card_num(1) == 3, f"P1 卡片 {cards[1]!r} 期望 3"
            assert sum_card_num(2) == 4, f"P2 卡片 {cards[2]!r} 期望 4"
            assert sum_card_num(3) == 0, f"P3 卡片 {cards[3]!r} 期望 0"
            assert "有失败" in cards[4] and "交互测试" in cards[4], f"最近运行卡片 {cards[4]!r}"
            assert cards[5].strip() == "0", f"前端报错卡片 {cards[5]!r} 期望 0"
            assert "问题记录（11）" in page.locator(".card-head").first.inner_text(), "列表标题总数不是 11"
        record("TC-DC-ISS-001", "种子问题与汇总卡片数字", tc_iss_001)

        def tc_iss_002():
            goto("/debug/issues")
            pick_filter(1, "P0")
            n = issue_rows().count()
            assert n == 4, f"P0 筛选行数={n}，期望 4"
            tags = page.locator(".el-table__body tr:visible .el-tag").all_inner_texts()
            assert all(t == "P0" for t in tags), f"含非 P0 行: {tags}"
        record("TC-DC-ISS-002", "严重度筛选 P0", tc_iss_002)

        def tc_iss_003():
            goto("/debug/issues")
            pick_filter(0, "未处理")
            n = issue_rows().count()
            assert n == 11, f"open 筛选行数={n}，期望 11"
        record("TC-DC-ISS-003", "状态筛选未处理", tc_iss_003)

        def tc_iss_004():
            goto("/debug/issues")
            pick_filter(0, "未处理")
            pick_filter(1, "P1")
            n = issue_rows().count()
            assert n == 3, f"open+P1 组合行数={n}，期望 3"
        record("TC-DC-ISS-004", "状态+严重度组合筛选", tc_iss_004)

        def tc_iss_005():
            goto("/debug/issues")
            first = issue_rows().first
            title = first.locator("td").nth(1).inner_text().strip()
            first.locator(".el-select").click()
            page.wait_for_timeout(400)
            select_option("已完成")
            page.wait_for_timeout(600)
            s = summary_api()
            assert s["open_issues_by_severity"]["P0"] == 3, \
                f"改状态后 P0 未决={s['open_issues_by_severity']['P0']}，期望 3"
            _, lst = api("/debug/issues", payload=None)
            row = [i for i in lst["items"] if i["title"] == title]
            assert row and row[0]["status"] == "done", f"服务端状态未更新: {row}"
        record("TC-DC-ISS-005", "内联状态下拉即改（联动摘要）", tc_iss_005)

        def tc_iss_006():
            goto("/debug/issues")
            pick_filter(0, "未处理")
            n0 = issue_rows().count()
            first = issue_rows().first
            title = first.locator("td").nth(1).inner_text().strip()
            first.locator(".el-select").click()
            page.wait_for_timeout(400)
            select_option("不修复")
            page.wait_for_timeout(800)
            n1 = issue_rows().count()
            still = page.locator("tr:visible", has_text=title).count()
            assert n1 == n0 - 1 and still == 0, \
                f"筛选「未处理」下改为不修复后行仍在列表（{n0}->{n1}, 标题行数={still}），筛选结果失真"
        record("TC-DC-ISS-006", "筛选态下内联改状态行应消失", tc_iss_006)

        def tc_iss_007():
            goto("/debug/issues")
            page.locator("button", has_text="新建问题").first.click()
            dlg = page.locator(".el-dialog:visible")
            dlg.locator("button", has_text="保存").click()
            page.wait_for_timeout(400)
            assert page.locator(".el-message", has_text="请填写标题").count() > 0, "空标题未弹校验提示"
            assert dlg.is_visible(), "空标题保存后对话框被关闭"
            page.keyboard.press("Escape")
        record("TC-DC-ISS-007", "空标题前端校验", tc_iss_007)

        def tc_iss_008():
            goto("/debug/issues")
            page.locator("button", has_text="新建问题").first.click()
            dlg = page.locator(".el-dialog:visible")
            dlg.locator("input").first.fill("探测问题-A")
            dlg.locator(".el-radio-button", has_text="P1").click()
            dlg.locator("textarea").fill("探测描述：E2E 新建")
            dlg.locator("button", has_text="保存").click()
            page.wait_for_timeout(900)
            assert page.locator("tr:visible", has_text="探测问题-A").count() > 0, "新建后列表未出现"
            _, lst = api("/debug/issues")
            assert lst["total"] == 12, f"总数={lst['total']}，期望 12"
            s = summary_api()
            assert s["open_issues_by_severity"]["P1"] == 4, "P1 未决计数未 +1"
        record("TC-DC-ISS-008", "新建问题（列表+摘要联动）", tc_iss_008)

        def tc_iss_009():
            goto("/debug/issues")
            row = page.locator("tr:visible", has_text="探测问题-A").first
            row.locator("button", has_text="编辑").click()
            dlg = page.locator(".el-dialog:visible")
            assert "编辑问题" in dlg.inner_text(), "对话框标题不是编辑问题"
            dlg.locator("input").first.fill("探测问题-A改")
            dlg.locator("button", has_text="保存").click()
            page.wait_for_timeout(900)
            assert page.locator("tr:visible", has_text="探测问题-A改").count() > 0, "编辑后新标题未出现"
            _, lst = api("/debug/issues")
            it = [i for i in lst["items"] if i["title"] == "探测问题-A改"]
            assert it and it[0]["updated_at"] >= it[0]["created_at"], "updated_at 未刷新"
        record("TC-DC-ISS-009", "编辑问题标题", tc_iss_009)

        def tc_iss_010():
            goto("/debug/issues")
            page.locator("tr:visible", has_text="探测问题-A改").first.locator(
                "button", has_text="删除").click()
            box = page.locator(".el-message-box:visible")
            box.locator("button", has_text="取消").click()
            page.wait_for_timeout(500)
            assert page.locator("tr:visible", has_text="探测问题-A改").count() > 0, "取消删除后行消失"
        record("TC-DC-ISS-010", "删除问题-取消路径", tc_iss_010)

        def tc_iss_011():
            goto("/debug/issues")
            page.locator("tr:visible", has_text="探测问题-A改").first.locator(
                "button", has_text="删除").click()
            page.locator(".el-message-box:visible").locator("button", has_text="确定").click()
            page.wait_for_timeout(900)
            assert page.locator("tr:visible", has_text="探测问题-A改").count() == 0, "确认删除后行仍在"
            _, lst = api("/debug/issues")
            assert lst["total"] == 11, f"删除后总数={lst['total']}，期望 11"
        record("TC-DC-ISS-011", "删除问题-确认路径", tc_iss_011)

        def tc_iss_012():
            goto("/debug/issues")
            _, lst = api("/debug/issues")
            sevs = [i["severity"] for i in lst["items"]]
            assert sevs == sorted(sevs), f"严重度未按 P0→P3 排序: {sevs}"
            first_tag = issue_rows().first.locator(".el-tag").first.inner_text().strip()
            assert first_tag == "P0", f"首行级别={first_tag}，期望 P0"
        record("TC-DC-ISS-012", "严重度排序 P0 在前", tc_iss_012)

        # ---------- 测试运行 ----------
        def tc_run_001():
            goto("/debug/runs")
            assert "测试运行（2）" in page.locator(".card-head").first.inner_text(), "列表总数不是 2"
            first = issue_rows().first
            assert "20261020-093000_interactions" in first.inner_text(), "倒序首位不是 interactions 运行"
            assert "交互测试" in first.inner_text(), "类型标签未中文化"
        record("TC-DC-RUN-001", "运行列表倒序", tc_run_001)

        def tc_run_002():
            goto("/debug/runs")
            page.locator("tr:visible", has_text="20261020-090000_recon").first.locator(
                "button", has_text="详情").click()
            page.wait_for_timeout(800)
            drawer = page.locator(".el-drawer:visible")
            assert "20261020-090000_recon" in drawer.locator(".el-drawer__title").inner_text(), "抽屉标题无运行 id"
            assert "页面侦察" in drawer.inner_text(), "详情未显示类型"
            assert BASE in drawer.inner_text(), "详情未显示目标 base"
            assert "页面检查（2）" in drawer.inner_text(), "页面检查表缺失"
        record("TC-DC-RUN-002", "详情抽屉基本信息", tc_run_002)

        def tc_run_003():
            drawer = page.locator(".el-drawer:visible")
            if not drawer.count():
                goto("/debug/runs")
                page.locator("tr:visible", has_text="20261020-090000_recon").first.locator(
                    "button", has_text="详情").click()
                page.wait_for_timeout(800)
            drawer = page.locator(".el-drawer:visible")
            drawer.locator(".el-table__expand-icon").first.click()
            page.wait_for_timeout(500)
            body = drawer.inner_text()
            assert "console 错误（1）" in body and "boom is not a function" in body, \
                f"展开行未显示 console 错误清单: {body[:200]}"
            # 样式必须真的生效：ErrList 是函数式组件，其内部 pre/div 不是父组件根节点，
            # 拿不到 SFC scoped 的 data-v 属性时 .err-pre[data-v-x] 选择器整体失效
            # （表现：长堆栈失去 max-height/overflow，直接把展开行撑爆）。
            style = page.evaluate("""() => {
                const el = document.querySelector('.el-drawer .err-lists pre');
                if (!el) return null;
                const cs = getComputedStyle(el);
                return {
                    bg: cs.backgroundColor, maxH: cs.maxHeight, ovf: cs.overflowY,
                    scopeAttrs: [...el.attributes].map(a => a.name).filter(n => n.startsWith('data-v')),
                };
            }""")
            assert style, "展开行内找不到错误清单 pre 元素"
            assert style["bg"] == "rgb(254, 240, 240)" and style["maxH"] == "180px", (
                f"错误清单样式未生效（scoped 属性丢失）：{style}"
            )
            page.locator(".el-drawer__close-btn").click()
            page.wait_for_timeout(400)
        record("TC-DC-RUN-003", "页面行展开错误清单（含样式生效）", tc_run_003)

        def tc_run_004():
            goto("/debug/runs")
            page.locator("tr:visible", has_text="20261020-090000_recon").first.locator(
                "button", has_text="详情").click()
            page.wait_for_timeout(800)
            drawer = page.locator(".el-drawer:visible")
            assert "截图（1" in drawer.inner_text(), "截图区块缺失"
            drawer.locator(".shot-img img").first.click()
            page.wait_for_timeout(600)
            viewer = page.locator(".el-image-viewer__wrapper:visible")
            assert viewer.count() > 0, "点击截图未打开放大预览"
            page.locator(".el-image-viewer__close").click()
            page.wait_for_timeout(400)
            assert page.locator(".el-image-viewer__wrapper:visible").count() == 0, "预览未关闭"
            page.locator(".el-drawer__close-btn").click()
        record("TC-DC-RUN-004", "截图预览放大与关闭", tc_run_004)

        def tc_run_005():
            goto("/debug/runs")
            page.locator("tr:visible", has_text="20261020-093000_interactions").first.locator(
                "button", has_text="删除").click()
            page.locator(".el-message-box:visible").locator("button", has_text="取消").click()
            page.wait_for_timeout(500)
            assert "测试运行（2）" in page.locator(".card-head").first.inner_text(), "取消删除后列表变了"
        record("TC-DC-RUN-005", "删除运行-取消路径", tc_run_005)

        def tc_run_006():
            goto("/debug/runs")
            page.locator("tr:visible", has_text="20261020-093000_interactions").first.locator(
                "button", has_text="删除").click()
            page.locator(".el-message-box:visible").locator("button", has_text="确定").click()
            page.wait_for_timeout(900)
            assert "测试运行（1）" in page.locator(".card-head").first.inner_text(), "确认删除后总数未减"
            code, _ = api("/debug/runs/20261020-093000_interactions")
            assert code == 404, f"删除后详情 API 状态={code}，期望 404"
            assert not (DEBUG_DIR / "runs" / "20261020-093000_interactions").exists(), "运行目录未物理删除"
        record("TC-DC-RUN-006", "删除运行-确认路径", tc_run_006)

        def tc_run_007():
            goto("/debug/runs")
            # 用例表渲染（recon 运行无 tests，应只显示页面检查；interactions 已删，补验列表稳定）
            assert page.locator(".el-table__body tr:visible").count() == 1, "剩余运行数不为 1"
            _, lst = api("/debug/runs")
            assert lst["total"] == 1 and lst["items"][0]["shots"] == 1, f"列表 API 异常: {lst}"
        record("TC-DC-RUN-007", "删除后列表/API 一致", tc_run_007)

        # ---------- 前端报错采集链路 ----------
        def tc_err_001():
            goto("/debug/errors")
            assert page.locator(".el-empty").count() > 0, "空态未显示 el-empty"
            assert "前端报错（0）" in page.locator(".card-head").first.inner_text(), "初始总数不为 0"
        record("TC-DC-ERR-001", "报错页空态", tc_err_001)

        def tc_err_002():
            goto("/debug/errors")
            c0 = client_error_count()
            throw_js("探测错误-甲-windowerror")
            page.wait_for_timeout(1500)
            c1 = client_error_count()
            assert c1 == c0 + 1, f"window error 未上报（{c0}->{c1}）"
            page.locator("button", has_text="刷新").click()
            page.wait_for_timeout(700)
            row = page.locator("tr:visible", has_text="探测错误-甲-windowerror")
            assert row.count() > 0, "报错行未出现在列表"
            assert "/debug/errors" in row.first.inner_text(), "报错行页面字段不正确"
        record("TC-DC-ERR-002", "JS 错误采集链路（上报→列表）", tc_err_002)

        def tc_err_003():
            c0 = client_error_count()
            reject_js("探测拒绝-乙-unhandledrejection")
            page.wait_for_timeout(1500)
            c1 = client_error_count()
            assert c1 == c0 + 1, f"unhandledrejection 未上报（{c0}->{c1}）"
        record("TC-DC-ERR-003", "unhandledrejection 采集", tc_err_003)

        def tc_err_004():
            c0 = client_error_count()
            throw_js("探测错误-去重")
            page.wait_for_timeout(400)
            throw_js("探测错误-去重")
            page.wait_for_timeout(1500)
            c1 = client_error_count()
            assert c1 == c0 + 1, f"5 秒去重失效（{c0}->{c1}，期望 +1）"
        record("TC-DC-ERR-004", "同消息 5 秒去重", tc_err_004)

        def tc_err_005():
            page.locator("button", has_text="刷新").click()
            page.wait_for_timeout(700)
            first = page.locator(".el-table__body tr:visible").first
            first.locator(".el-table__expand-icon").click()
            page.wait_for_timeout(500)
            expand = page.locator(".el-table__expanded-cell:visible").first.inner_text()
            assert "探测错误" in expand or "Error" in expand, f"展开无堆栈: {expand[:150]}"
            assert "Mozilla" in expand or "Chrome" in expand, f"展开无 UA: {expand[:150]}"
        record("TC-DC-ERR-005", "报错行展开堆栈与 UA", tc_err_005)

        def tc_err_006():
            page.locator("button", has_text="清空").click()
            page.locator(".el-message-box:visible").locator("button", has_text="取消").click()
            page.wait_for_timeout(500)
            assert client_error_count() == 3, f"取消清空后计数变了: {client_error_count()}"
        record("TC-DC-ERR-006", "清空-取消路径", tc_err_006)

        def tc_err_007():
            page.locator("button", has_text="清空").click()
            page.locator(".el-message-box:visible").locator("button", has_text="确定").click()
            page.wait_for_timeout(900)
            assert client_error_count() == 0, f"确认清空后计数={client_error_count()}"
            assert page.locator(".el-empty").count() > 0, "清空后未回空态"
            disabled = page.locator("button", has_text="清空").first.is_disabled()
            assert disabled, "空列表时清空按钮未禁用"
        record("TC-DC-ERR-007", "清空-确认路径", tc_err_007)

        def tc_err_008():
            page.locator(".head-ops .el-switch").click()
            page.wait_for_timeout(300)
            throw_js("探测错误-自动刷新")
            # 自动刷新周期 10s：什么都不点，等它自己出现
            deadline = time.time() + 14
            ok = False
            while time.time() < deadline:
                if page.locator("tr:visible", has_text="探测错误-自动刷新").count() > 0:
                    ok = True
                    break
                page.wait_for_timeout(800)
            page.locator(".head-ops .el-switch").click()  # 关
            assert ok, "自动刷新 14s 内未拉取到新报错"
        record("TC-DC-ERR-008", "自动刷新开关", tc_err_008)

        def tc_err_009():
            goto("/debug/issues")
            s = summary_api()
            assert s["client_errors_count"] == 1, f"API 报错计数={s['client_errors_count']}，期望 1"
            cards = sum_card_texts()
            assert cards[5].strip() == "1", f"报错卡片 {cards[5]!r} 期望 1"
        record("TC-DC-ERR-009", "报错计数联动汇总卡片", tc_err_009)

        # ---------- API 直测 ----------
        def tc_api_001():
            code, body = api("/debug/issues", method="POST", payload={"title": "   "})
            assert code == 422, f"空白标题应 422，实际 {code}: {body}"
            code2, body2 = api("/debug/issues", method="POST",
                               payload={"title": "正常标题", "page": "/t"})
            assert code2 == 201, f"正常创建应 201，实际 {code2}"
            iid = body2["id"]
            code3, _ = api(f"/debug/issues/{iid}", method="PUT", payload={"title": "  "})
            assert code3 == 422, f"更新空白标题应 422，实际 {code3}"
            api(f"/debug/issues/{iid}", method="DELETE")
        record("TC-DC-API-001", "空白标题拒绝（创建/更新）", tc_api_001)

        def tc_api_002():
            code, _ = api("/debug/issues", method="POST",
                          payload={"title": "x", "severity": "P9"})
            assert code == 422, f"非法严重度应 422，实际 {code}"
            code, _ = api("/debug/issues", method="POST",
                          payload={"title": "x", "status": "bad"})
            assert code == 422, f"非法状态应 422，实际 {code}"
            code, _ = api("/debug/issues/no_such_id", method="PUT", payload={"status": "done"})
            assert code == 404, f"更新不存在问题应 404，实际 {code}"
            code, _ = api("/debug/issues/no_such_id", method="DELETE")
            assert code == 404, f"删除不存在问题应 404，实际 {code}"
        record("TC-DC-API-002", "非法枚举 422 / 不存在 404", tc_api_002)

        def tc_api_003():
            code, _ = api("/debug/runs/.._evil")
            assert code == 400, f"非法 run_id 应 400，实际 {code}"
            code, _ = api("/debug/runs/20261020-090000_recon/shots/..%2Fresult.json")
            assert code in (400, 404), f"截图穿越应 400/404，实际 {code}"
            code, _ = api("/debug/runs/no_such_run")
            assert code == 404, f"不存在运行应 404，实际 {code}"
        record("TC-DC-API-003", "路径穿越与不存在的运行", tc_api_003)

        def tc_api_004():
            code, _ = api("/debug/client-errors", method="POST", raw_body=b"this is not json")
            assert code == 201, f"非 JSON body 应 201，实际 {code}"
            code, _ = api("/debug/client-errors", method="POST", payload=["a", "b"])
            assert code == 201, f"数组 body 应 201，实际 {code}"
            code, _ = api("/debug/client-errors", method="POST", payload={"message": 123})
            assert code == 201, f"数字 message 应 201，实际 {code}"
            api("/debug/client-errors", method="DELETE")
        record("TC-DC-API-004", "client-errors 垃圾输入不 500", tc_api_004)

        def tc_api_005():
            s = summary_api()
            _, lst = api("/debug/issues")
            assert s["total_issues"] == lst["total"], "summary 问题总数与列表不一致"
            open_calc = {"P0": 0, "P1": 0, "P2": 0, "P3": 0}
            for i in lst["items"]:
                if i["status"] in ("open", "in_progress"):
                    open_calc[i["severity"]] = open_calc.get(i["severity"], 0) + 1
            assert s["open_issues_by_severity"] == open_calc, \
                f"未决计数不一致: {s['open_issues_by_severity']} vs {open_calc}"
            assert s["runs_count"] == 1, f"runs_count={s['runs_count']}，期望 1"
            assert s["last_run"]["id"] == "20261020-090000_recon", "last_run 指向错误"
        record("TC-DC-API-005", "summary 数字一致性", tc_api_005)

        browser.close()
finally:
    server.terminate()
    try:
        server.wait(timeout=10)
    except subprocess.TimeoutExpired:
        server.kill()

# 汇总输出
passed = sum(1 for r in results if r[2])
print("=" * 70)
print(f"tests={len(results)} passed={passed} failed={len(results) - passed}")
for tid, name, ok, detail in results:
    if not ok:
        print(f"  FAILED {tid} {name}: {detail}")

out = {
    "area": "debug-center",
    "base": BASE,
    "started_at": STARTED_AT,
    "finished_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    "pages": pages_report,
    "tests": [{"id": t, "name": n, "passed": ok, "detail": d} for t, n, ok, d in results],
    "bugs": [],
}
RESULT_JSON.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"result -> {RESULT_JSON}")

# 确认端口释放
time.sleep(1)
import socket
sk = socket.socket()
sk.settimeout(1)
try:
    sk.connect(("127.0.0.1", PORT))
    print(f"WARN: port {PORT} still listening")
except OSError:
    print(f"port {PORT} released")
finally:
    sk.close()
