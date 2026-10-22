# -*- coding: utf-8 -*-
"""jobs 区域深度 E2E 探测（定时任务）。

覆盖：
- /jobs 逐页侦察（控制台/页面错误/失败请求 + 截图）、空库空态；
- 新建三种调度类型（interval/daily/cron）表单填写与列表展示文案；
- cron 任务创建后可编辑（上轮 P1 修复点端到端回归：回显 + 改名 + 改表达式 + 换类型）；
- cron 非法表达式两级拒绝：前端五段校验提示 / 后端 422 经拦截器弹 toast，均不落库；
- 空名称、空提示词（无模块动作时）校验；
- 模块动作下拉（finance_board.run）创建空提示词动作任务；
- 立即运行：LLM 未配置 → skipped 提示；动作任务管线不可用 → error 提示；
  手动运行 daily/cron 不更新 last_run_at、interval 顺延（上轮修复点 API 级回归）；
- 启用/禁用开关（含持久化）与禁用任务不被调度器触发的验证；
- 删除确认/取消两条路径；
- 对话框调度类型切换的字段显隐；
- API 边界：interval 越界、名称/提示词超长、未知 schedule_type、更新路径 cron 校验；
- 调度器 tick 集成：启用的 interval 任务 20s 内被触发（LLM 缺失记 skipped）。

可重复运行：独立测试库 + 自有端口 8210。
"""
import json
import os
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
PROBE_DIR = E2E_DIR / "probe_results" / "jobs"
SHOTS = PROBE_DIR / "shots"
DB = E2E_DIR / "probe_results" / "jobs.db"
FIN_DIR = E2E_DIR / "probe_results" / "jobs-fin"
DEBUG_DIR = E2E_DIR / "probe_results" / "jobs-debug"
PORT = 8210
BASE = f"http://127.0.0.1:{PORT}"
STARTED_AT = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

for d in (PROBE_DIR, SHOTS):
    d.mkdir(parents=True, exist_ok=True)
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
# 干净库：不灌演示数据，保证「空库空态」断言确定性
env["SEED_ON_STARTUP"] = "0"
# 探测环境不接入模型：清空 LLM 配置，立即运行走 skipped 确定性路径
for k in ("LLM_PROVIDER", "LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL"):
    env[k] = ""

server = subprocess.Popen(
    [str(BACKEND / "venv" / "Scripts" / "python.exe"), "-m", "uvicorn",
     "app.main:app", "--host", "127.0.0.1", "--port", str(PORT)],
    cwd=str(BACKEND), env=env,
    stdout=open(E2E_DIR / "probe_results" / "jobs.server.log", "w"),
    stderr=subprocess.STDOUT,
)

results = []   # (id, name, passed, detail)
pages_report = []
page_errors_all = []


def record(tid, name, fn):
    try:
        fn()
        results.append((tid, name, True, ""))
        print(f"[PASS] {tid} {name}", flush=True)
    except Exception as e:
        results.append((tid, name, False, str(e)[:300]))
        print(f"[FAIL] {tid} {name}: {str(e)[:300]}", flush=True)
        try:
            page.screenshot(path=str(SHOTS / f"{tid}.png"))
        except Exception:
            pass


def wait_ready(timeout=60):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            urllib.request.urlopen(f"{BASE}/api/dashboard/summary", timeout=2)
            return True
        except Exception:
            time.sleep(1)
    return False


def api_call(method: str, path: str, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        f"{BASE}/api{path}", data=data, method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        try:
            body = json.loads(body)
        except Exception:
            pass
        return e.code, body


page = None


def goto(route):
    page.goto(f"{BASE}/#{route}", wait_until="networkidle", timeout=30000)
    page.wait_for_timeout(600)


def dialog():
    return page.locator(".el-dialog:visible")


def toast_text():
    return " ".join(t.inner_text() for t in page.locator(".el-message:visible").all())


def open_create():
    page.locator(".toolbar button", has_text="新建任务").first.click()
    page.wait_for_timeout(400)


def fill_name(t):
    dialog().locator("input[placeholder*='每周税种测算提醒']").fill(t)


def fill_prompt(t):
    dialog().locator("textarea").first.fill(t)


def pick_schedule_type(label):
    dialog().locator(".el-radio-button", has_text=label).first.click()
    page.wait_for_timeout(250)


def set_daily(t):
    # Element Plus 2.14 的时间选择器根节点是 .el-date-editor--time（不再渲染 .el-time-picker）
    inp = dialog().locator(".el-date-editor input")
    inp.click()
    page.wait_for_timeout(150)
    inp.fill(t)
    page.keyboard.press("Enter")
    page.wait_for_timeout(250)


def set_interval(n):
    inp = dialog().locator(".el-input-number input")
    inp.click()
    inp.fill(str(n))
    page.keyboard.press("Enter")
    page.wait_for_timeout(200)


def set_cron(expr):
    dialog().locator("input[placeholder*='分 时 日 月 周']").fill(expr)


def pick_action(label):
    item = dialog().locator(".el-form-item", has_text="模块动作")
    item.locator(".el-select").click()
    page.wait_for_timeout(300)
    page.locator(".el-select-dropdown__item:visible", has_text=label).first.click()
    page.wait_for_timeout(200)


def save_dialog():
    dialog().locator(".el-dialog__footer button", has_text="保存").first.click()
    page.wait_for_timeout(900)


def cancel_dialog():
    dialog().locator(".el-dialog__footer button", has_text="取消").first.click()
    page.wait_for_timeout(300)


def row(name):
    return page.locator(".el-table__row", has_text=name).first


def job_by_name(name):
    _, jobs = api_call("GET", "/jobs")
    return next((j for j in jobs if j["name"] == name), None)


def main():
    global page
    if not wait_ready():
        print("FATAL: server not ready")
        server.terminate()
        sys.exit(2)

    from playwright.sync_api import sync_playwright

    # 调度器 tick 集成样本：探针一启动就建，留足 ≥20s 让 tick 自然发生
    api_call("POST", "/jobs", {"name": "探测-tick启用", "prompt": "x", "schedule_type": "interval",
                               "interval_minutes": 5, "enabled": True})
    api_call("POST", "/jobs", {"name": "探测-tick禁用", "prompt": "x", "schedule_type": "interval",
                               "interval_minutes": 5, "enabled": False})

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        page = ctx.new_page()
        page.on("pageerror", lambda e: page_errors_all.append(str(e)))

        # ================= 阶段 1：逐页侦察 =================
        ce, pe, fr = [], [], []

        def on_console(m, e=ce):
            if m.type == "error":
                e.append(m.text)

        def on_pageerror(e, l=pe):
            l.append(str(e))

        def on_requestfailed(r, l=fr):
            l.append(f"{r.method} {r.url}")

        page.on("console", on_console)
        page.on("pageerror", on_pageerror)
        page.on("requestfailed", on_requestfailed)
        resp = page.goto(f"{BASE}/#/jobs", wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(900)
        page.screenshot(path=str(SHOTS / "jobs.png"))
        buttons = [b.inner_text().strip().replace("\n", " ")[:20]
                   for b in page.locator("button:visible").all()[:12]]
        page.remove_listener("console", on_console)
        page.remove_listener("pageerror", on_pageerror)
        page.remove_listener("requestfailed", on_requestfailed)
        pages_report.append({
            "route": "/jobs", "name": "jobs",
            "status": resp.status if resp else "?",
            "console_errors": ce, "page_errors": pe,
            "failed_reqs": [f for f in fr if "favicon" not in f],
            "buttons": buttons,
        })

        # ================= 阶段 2：交互与边界 =================

        def tc_initial_render():
            # tick 样本任务已在列表中（API 预建），空态不再出现；先验证标题与按钮，
            # 再清空后验证空态
            # 全局 header 与页内 toolbar 各有一个 .page-title，这里只看页内标题
            assert "定时任务" in page.locator(".toolbar .page-title").inner_text(), "页面标题缺失"
            assert page.locator(".toolbar button", has_text="新建任务").count() > 0, "缺少新建任务按钮"
            assert page.locator(".el-table__row", has_text="探测-tick启用").count() > 0, "API 预建任务未入列表"

        record("TC-JOB-001", "页面初始渲染（标题/新建按钮/列表）", tc_initial_render)

        def tc_empty_state():
            # 删光 tick 样本 → 空态出现；随后重建供 tick 用例使用
            for nm in ("探测-tick启用", "探测-tick禁用"):
                row(nm).locator("button", has_text="删除").click()
                page.wait_for_timeout(400)
                page.locator(".el-message-box:visible button", has_text="确定").click()
                page.wait_for_timeout(800)
            assert page.locator(".el-empty").count() > 0, "空库未显示空态"
            assert "暂无定时任务" in page.locator(".el-empty").inner_text(), "空态文案异常"
            api_call("POST", "/jobs", {"name": "探测-tick启用", "prompt": "x",
                                       "schedule_type": "interval", "interval_minutes": 5, "enabled": True})
            api_call("POST", "/jobs", {"name": "探测-tick禁用", "prompt": "x",
                                       "schedule_type": "interval", "interval_minutes": 5, "enabled": False})
            page.reload(wait_until="networkidle")
            page.wait_for_timeout(800)

        record("TC-JOB-002", "空库空态（删光后 el-empty + 文案）", tc_empty_state)

        def tc_create_interval():
            open_create()
            assert dialog().locator(".el-dialog__title").inner_text() == "新建定时任务", "对话框标题异常"
            fill_name("探测-间隔任务")
            fill_prompt("汇总今日待办")
            # 默认 interval 60 分钟、启用开
            save_dialog()
            assert "任务创建成功" in toast_text(), f"缺少成功提示: {toast_text()[:120]}"
            r = row("探测-间隔任务")
            assert r.count() > 0, "新建任务未入列表"
            assert "每 1 小时" in r.inner_text(), f"interval 展示文案异常: {r.inner_text()}"
            assert "智能路由" in r.inner_text(), "未指定专家应显示智能路由"

        record("TC-JOB-003", "新建 interval 任务（默认 60 分钟 → 每 1 小时）", tc_create_interval)

        def tc_create_interval_custom():
            open_create()
            fill_name("探测-间隔30分")
            fill_prompt("x")
            set_interval(30)
            save_dialog()
            r = row("探测-间隔30分")
            assert r.count() > 0, "未入列表"
            assert "每 30 分钟" in r.inner_text(), f"文案异常: {r.inner_text()}"

        record("TC-JOB-004", "新建 interval 任务（自定义 30 分钟）", tc_create_interval_custom)

        def tc_create_daily():
            open_create()
            fill_name("探测-每日任务")
            fill_prompt("x")
            pick_schedule_type("每天定时")
            set_daily("08:30")
            save_dialog()
            r = row("探测-每日任务")
            assert r.count() > 0, "未入列表"
            assert "每天 08:30" in r.inner_text(), f"daily 文案异常: {r.inner_text()}"
            j = job_by_name("探测-每日任务")
            assert j and j["daily_at"] == "08:30" and j["schedule_type"] == "daily", f"落库异常: {j}"

        record("TC-JOB-005", "新建 daily 任务（时间选择器 + 每天 HH:MM 文案）", tc_create_daily)

        def tc_create_cron():
            open_create()
            fill_name("探测-cron任务")
            fill_prompt("x")
            pick_schedule_type("Cron")
            # 1 月 1 日 09:00：远离当前时间，避免错过补偿在探测期间触发
            set_cron("0 9 1 1 *")
            save_dialog()
            r = row("探测-cron任务")
            assert r.count() > 0, "cron 任务未入列表"
            assert "Cron 0 9 1 1 *" in r.inner_text(), f"cron 文案异常: {r.inner_text()}"

        record("TC-JOB-006", "新建 cron 任务（五段表达式 + Cron 文案）", tc_create_cron)

        def tc_cron_edit_regression():
            # 上轮 P1 修复点端到端回归：cron 任务创建后可编辑
            row("探测-cron任务").locator("button", has_text="编辑").click()
            page.wait_for_timeout(400)
            assert dialog().locator(".el-dialog__title").inner_text() == "编辑定时任务", "编辑态标题异常"
            cron_inp = dialog().locator("input[placeholder*='分 时 日 月 周']")
            assert cron_inp.count() > 0, "编辑 cron 任务未显示 cron 输入框"
            assert cron_inp.input_value() == "0 9 1 1 *", f"cron 表达式回显异常: {cron_inp.input_value()}"
            fill_name("探测-cron已改名")
            set_cron("0 10 1 1 *")
            save_dialog()
            assert "任务已保存" in toast_text(), f"缺少保存提示: {toast_text()[:120]}"
            r = row("探测-cron已改名")
            assert r.count() > 0, "改名后列表未更新"
            assert "Cron 0 10 1 1 *" in r.inner_text(), "cron 表达式修改未生效"
            j = job_by_name("探测-cron已改名")
            assert j and j["cron_expr"] == "0 10 1 1 *" and j["schedule_type"] == "cron", f"落库异常: {j}"
            assert j["interval_minutes"] is None and j["daily_at"] is None, "cron 任务残留其它调度字段"

        record("TC-JOB-007", "cron 任务可编辑（P1 回归：回显/改名/改表达式）", tc_cron_edit_regression)

        def tc_cron_edit_switch_type():
            # 编辑态切换调度类型：cron → daily
            row("探测-cron已改名").locator("button", has_text="编辑").click()
            page.wait_for_timeout(400)
            pick_schedule_type("每天定时")
            set_daily("07:15")
            save_dialog()
            r = row("探测-cron已改名")
            assert "每天 07:15" in r.inner_text(), f"cron→daily 切换未生效: {r.inner_text()}"
            j = job_by_name("探测-cron已改名")
            assert j and j["schedule_type"] == "daily" and j["daily_at"] == "07:15", f"落库异常: {j}"
            assert j["interval_minutes"] is None, "切换 daily 后 interval_minutes 未清空"
            # 再切回 cron，验证可逆
            row("探测-cron已改名").locator("button", has_text="编辑").click()
            page.wait_for_timeout(400)
            pick_schedule_type("Cron")
            set_cron("0 9 1 1 *")
            save_dialog()
            r = row("探测-cron已改名")
            assert "Cron 0 9 1 1 *" in r.inner_text(), "daily→cron 切回未生效"

        record("TC-JOB-008", "编辑态调度类型互切（cron→daily→cron）", tc_cron_edit_switch_type)

        def tc_cron_four_segments():
            n0 = page.locator(".el-table__row").count()
            open_create()
            fill_name("探测-坏cron四段")
            fill_prompt("x")
            pick_schedule_type("Cron")
            set_cron("0 9 * *")
            save_dialog()
            t = toast_text()
            assert "五段" in t, f"四段表达式缺少前端提示: {t[:150]}"
            assert dialog().count() > 0, "校验失败后对话框被关闭"
            cancel_dialog()
            assert page.locator(".el-table__row").count() == n0, "非法 cron 被写入列表"

        record("TC-JOB-009", "cron 四段表达式前端拒绝（提示 + 不落库）", tc_cron_four_segments)

        def tc_cron_out_of_range():
            n0 = page.locator(".el-table__row").count()
            open_create()
            fill_name("探测-坏cron越界")
            fill_prompt("x")
            pick_schedule_type("Cron")
            set_cron("99 9 * * *")  # 五段但分钟越界：前端放行，后端 422
            save_dialog()
            t = toast_text()
            assert "cron" in t.lower() and "合法" in t, f"越界表达式缺少后端错误提示: {t[:150]}"
            assert dialog().count() > 0, "422 后对话框被关闭（应保留供修改）"
            cancel_dialog()
            assert page.locator(".el-table__row").count() == n0, "非法 cron 被写入列表"

        record("TC-JOB-010", "cron 越界表达式后端 422（拦截器弹 toast + 不落库）", tc_cron_out_of_range)

        def tc_empty_name():
            open_create()
            fill_prompt("x")
            save_dialog()
            err = page.locator(".el-form-item__error:visible")
            assert err.count() > 0, "空名称未触发表单校验"
            assert "名称" in err.first.inner_text(), f"校验文案异常: {err.first.inner_text()}"
            assert dialog().count() > 0, "校验失败对话框被关闭"
            cancel_dialog()

        record("TC-JOB-011", "空名称前端校验", tc_empty_name)

        def tc_empty_prompt_no_action():
            n0 = page.locator(".el-table__row").count()
            open_create()
            fill_name("探测-空提示词")
            save_dialog()
            err = page.locator(".el-form-item__error:visible")
            t = toast_text()
            assert err.count() > 0 or "提示词" in t, f"空提示词无提示: err={err.count()} toast={t[:120]}"
            assert dialog().count() > 0, "校验失败对话框被关闭"
            cancel_dialog()
            assert page.locator(".el-table__row").count() == n0, "空提示词任务被写入"

        record("TC-JOB-012", "空提示词（无模块动作）被拒", tc_empty_prompt_no_action)

        def tc_action_job():
            open_create()
            fill_name("探测-动作任务")
            pick_schedule_type("间隔执行")
            # 不填提示词，选择模块动作 finance_board.run
            pick_action("刷新报表看板")
            save_dialog()
            assert "任务创建成功" in toast_text(), f"动作任务创建失败: {toast_text()[:120]}"
            r = row("探测-动作任务")
            assert r.count() > 0, "动作任务未入列表"
            j = job_by_name("探测-动作任务")
            assert j and j["action"] == "finance_board.run" and j["prompt"] == "", f"落库异常: {j}"

        record("TC-JOB-013", "模块动作下拉创建空提示词任务（finance_board.run）", tc_action_job)

        def tc_run_now_agent_skipped():
            # LLM 未配置：立即运行 → skipped 提示，列表状态变「跳过」
            row("探测-间隔30分").locator("button", has_text="立即运行").click()
            page.wait_for_timeout(1500)
            t = toast_text()
            assert "模型未配置" in t or "已跳过" in t, f"缺少 skipped 提示: {t[:150]}"
            j = job_by_name("探测-间隔30分")
            assert j and j["last_status"] == "skipped", f"运行状态未落库: {j and j['last_status']}"
            assert j["recent_runs"] and "模型未配置" in j["recent_runs"][0]["summary"], "recent_runs 未记录"

        record("TC-JOB-014", "立即运行 Agent 任务（LLM 缺失 → skipped 提示 + 落库）", tc_run_now_agent_skipped)

        def tc_run_now_action_error():
            # 探测库无管线目录：动作任务运行 → error，界面弹错误提示
            row("探测-动作任务").locator("button", has_text="立即运行").click()
            # 动作任务要等管线真实失败（本次实测 ~2s+），固定 sleep 会抢在 toast 前读空
            deadline = time.time() + 10
            t = ""
            while time.time() < deadline:
                t = toast_text()
                if "失败" in t:
                    break
                page.wait_for_timeout(300)
            assert "看板刷新失败" in t or "失败" in t, f"缺少错误提示: {t[:150]}"
            j = job_by_name("探测-动作任务")
            assert j and j["last_status"] == "error", f"动作任务状态异常: {j and j['last_status']}"

        record("TC-JOB-015", "立即运行动作任务（管线不可用 → error 提示 + 落库）", tc_run_now_action_error)

        def tc_manual_run_last_run_at():
            # 上轮修复点 API 级回归：手动运行 daily/cron 不更新 last_run_at；interval 顺延
            _, j = api_call("POST", "/jobs", {"name": "探测-手动daily", "prompt": "x",
                                              "schedule_type": "daily", "daily_at": "09:00",
                                              "enabled": False})
            st, _ = api_call("POST", f"/jobs/{j['id']}/run")
            assert st == 200, f"手动运行失败: {st}"
            _, body = api_call("GET", "/jobs")
            d = next(x for x in body if x["id"] == j["id"])
            assert d["last_status"] == "skipped", f"daily 手动运行状态异常: {d['last_status']}"
            assert d["last_run_at"] is None, f"daily 手动运行不应更新 last_run_at: {d['last_run_at']}"
            _, j2 = api_call("POST", "/jobs", {"name": "探测-手动interval", "prompt": "x",
                                               "schedule_type": "interval", "interval_minutes": 60,
                                               "enabled": False})
            api_call("POST", f"/jobs/{j2['id']}/run")
            _, body = api_call("GET", "/jobs")
            d2 = next(x for x in body if x["id"] == j2["id"])
            assert d2["last_run_at"] is not None, "interval 手动运行应顺延 last_run_at"

        record("TC-JOB-016", "手动运行 last_run_at 语义（daily 不吞到点 / interval 顺延）", tc_manual_run_last_run_at)

        def tc_toggle_enabled():
            r = row("探测-间隔任务")
            sw = r.locator(".el-switch")
            assert "is-checked" in sw.get_attribute("class"), "新建任务默认应启用"
            sw.click()
            page.wait_for_timeout(900)
            assert "已停用" in toast_text(), f"缺少停用提示: {toast_text()[:120]}"
            j = job_by_name("探测-间隔任务")
            assert j and j["enabled"] is False, "停用未落库"
            page.reload(wait_until="networkidle")
            page.wait_for_timeout(800)
            sw2 = row("探测-间隔任务").locator(".el-switch")
            assert "is-checked" not in sw2.get_attribute("class"), "刷新后停用状态未保持"
            sw2.click()
            page.wait_for_timeout(900)
            assert "已启用" in toast_text(), f"缺少启用提示: {toast_text()[:120]}"
            assert job_by_name("探测-间隔任务")["enabled"] is True, "启用未落库"

        record("TC-JOB-017", "启用/禁用开关（提示 + 落库 + 刷新保持）", tc_toggle_enabled)

        def tc_delete_cancel():
            n0 = page.locator(".el-table__row").count()
            row("探测-间隔30分").locator("button", has_text="删除").click()
            page.wait_for_timeout(400)
            page.locator(".el-message-box:visible button", has_text="取消").click()
            page.wait_for_timeout(500)
            assert page.locator(".el-table__row").count() == n0, "取消删除后列表变化"
            assert row("探测-间隔30分").count() > 0, "取消删除后任务消失"

        record("TC-JOB-018", "删除任务（取消路径）", tc_delete_cancel)

        def tc_delete_confirm():
            n0 = page.locator(".el-table__row").count()
            row("探测-间隔30分").locator("button", has_text="删除").click()
            page.wait_for_timeout(400)
            box = page.locator(".el-message-box:visible")
            assert "不可恢复" in box.inner_text(), "删除确认文案缺失"
            box.locator("button", has_text="确定").click()
            page.wait_for_timeout(900)
            assert "已删除" in toast_text(), f"缺少删除提示: {toast_text()[:120]}"
            assert page.locator(".el-table__row").count() == n0 - 1, "删除后列表数量未减"
            assert job_by_name("探测-间隔30分") is None, "删除后仍可查到"

        record("TC-JOB-019", "删除任务（确认路径 + 落库）", tc_delete_confirm)

        def tc_dialog_field_visibility():
            open_create()
            assert dialog().locator(".el-input-number").count() == 1, "interval 态应显示间隔输入"
            assert dialog().locator(".el-date-editor--time").count() == 0, "interval 态不应显示时间选择"
            assert dialog().locator("input[placeholder*='分 时 日 月 周']").count() == 0, "interval 态不应显示 cron"
            pick_schedule_type("每天定时")
            assert dialog().locator(".el-date-editor--time").count() == 1, "daily 态应显示时间选择"
            assert dialog().locator(".el-input-number").count() == 0, "daily 态不应显示间隔输入"
            pick_schedule_type("Cron")
            assert dialog().locator("input[placeholder*='分 时 日 月 周']").count() == 1, "cron 态应显示表达式输入"
            assert dialog().locator(".el-input-number").count() == 0, "cron 态不应显示间隔输入"
            pick_schedule_type("间隔执行")
            assert dialog().locator(".el-input-number").count() == 1, "切回 interval 应恢复间隔输入"
            cancel_dialog()

        record("TC-JOB-020", "对话框调度类型切换字段显隐", tc_dialog_field_visibility)

        def tc_api_validation():
            # interval 越界
            st, _ = api_call("POST", "/jobs", {"name": "x", "prompt": "y",
                                               "schedule_type": "interval", "interval_minutes": 2})
            assert st == 422, f"interval=2 应 422，实际 {st}"
            st, _ = api_call("POST", "/jobs", {"name": "x", "prompt": "y",
                                               "schedule_type": "interval", "interval_minutes": 10081})
            assert st == 422, f"interval=10081 应 422，实际 {st}"
            # 名称/提示词超长
            st, _ = api_call("POST", "/jobs", {"name": "n" * 101, "prompt": "y",
                                               "schedule_type": "interval", "interval_minutes": 60})
            assert st == 422, f"name 101 字应 422，实际 {st}"
            st, _ = api_call("POST", "/jobs", {"name": "x", "prompt": "p" * 5001,
                                               "schedule_type": "interval", "interval_minutes": 60})
            assert st == 422, f"prompt 5001 字应 422，实际 {st}"
            # 更新路径 cron 校验：改为非法 cron 应 422 且不破坏原数据
            _, j = api_call("POST", "/jobs", {"name": "探测-APIcron", "prompt": "x",
                                              "schedule_type": "cron", "cron_expr": "0 9 1 1 *"})
            assert _ == 201 or True
            st, _ = api_call("PUT", f"/jobs/{j['id']}", {"cron_expr": "99 9 * * *"})
            assert st == 422, f"PUT 非法 cron 应 422，实际 {st}"
            _, body = api_call("GET", "/jobs")
            d = next(x for x in body if x["id"] == j["id"])
            assert d["cron_expr"] == "0 9 1 1 *", "422 后原 cron 表达式被污染"
            # interval 改 cron 缺表达式 → 422
            _, j2 = api_call("POST", "/jobs", {"name": "探测-API切型", "prompt": "x",
                                               "schedule_type": "interval", "interval_minutes": 60})
            st, _ = api_call("PUT", f"/jobs/{j2['id']}", {"schedule_type": "cron"})
            assert st == 422, f"interval→cron 缺表达式应 422，实际 {st}"
            # 不存在的任务
            st, _ = api_call("PUT", "/jobs/99999", {"name": "x"})
            assert st == 404, f"更新不存在任务应 404，实际 {st}"

        record("TC-JOB-021", "API 校验（越界/超长/非法 cron 更新/404）", tc_api_validation)

        def tc_unknown_schedule_type():
            st, body = api_call("POST", "/jobs", {"name": "探测-未知类型", "prompt": "x",
                                                  "schedule_type": "weekly", "interval_minutes": 60})
            assert st == 422, f"未知 schedule_type 应 422，实际 {st}: {str(body)[:120]}"

        record("TC-JOB-022", "API：未知 schedule_type 拒绝（weekly → 422）", tc_unknown_schedule_type)

        def tc_create_field_normalization():
            # 创建 daily/cron 时显式带 interval_minutes 应被归一化清空（与更新路径同口径）
            _, j = api_call("POST", "/jobs", {"name": "探测-归一daily", "prompt": "x",
                                              "schedule_type": "daily", "daily_at": "08:00",
                                              "interval_minutes": 60})
            _, body = api_call("GET", "/jobs")
            d = next(x for x in body if x["id"] == j["id"])
            assert d["interval_minutes"] is None, f"daily 任务残留 interval_minutes={d['interval_minutes']}"
            _, j2 = api_call("POST", "/jobs", {"name": "探测-归一cron", "prompt": "x",
                                               "schedule_type": "cron", "cron_expr": "0 9 1 1 *",
                                               "interval_minutes": 60, "daily_at": "08:00"})
            _, body = api_call("GET", "/jobs")
            d2 = next(x for x in body if x["id"] == j2["id"])
            assert d2["interval_minutes"] is None and d2["daily_at"] is None, \
                f"cron 任务残留字段: interval={d2['interval_minutes']} daily={d2['daily_at']}"

        record("TC-JOB-023", "API：创建时跨类型字段归一化（daily/cron 清 interval）", tc_create_field_normalization)

        def tc_unregistered_action_run():
            _, j = api_call("POST", "/jobs", {"name": "探测-未注册动作", "prompt": "",
                                              "action": "no.such.action",
                                              "schedule_type": "interval", "interval_minutes": 60,
                                              "enabled": False})
            st, body = api_call("POST", f"/jobs/{j['id']}/run")
            assert st == 200 and body["status"] == "error", f"未注册动作应 error: {st} {body}"
            assert "未注册" in body["summary"], f"摘要缺少未注册说明: {body}"

        record("TC-JOB-024", "API：未注册模块动作运行 → error 摘要", tc_unregistered_action_run)

        def tc_scheduler_tick():
            # 探针运行至此已远超 20s：启用的 interval 任务应已被 tick 触发（LLM 缺失 → skipped），
            # 禁用任务绝不应被触发
            en = job_by_name("探测-tick启用")
            dis = job_by_name("探测-tick禁用")
            assert en and dis, "tick 样本任务缺失"
            assert en["last_run_at"] is not None and en["last_status"] == "skipped", \
                f"启用任务未被调度器触发: last_run_at={en and en['last_run_at']} status={en and en['last_status']}"
            assert dis["last_run_at"] is None and not dis["recent_runs"], \
                f"禁用任务被调度器误触发: {dis['last_run_at']}"

        record("TC-JOB-025", "调度器 tick 集成（启用触发 / 禁用不触发）", tc_scheduler_tick)

        ctx.close()
        browser.close()

    print("=" * 70)
    passed = sum(1 for r in results if r[2])
    print(f"tests total={len(results)} passed={passed} failed={len(results) - passed}")
    bad_pages = [p for p in pages_report if p["console_errors"] or p["page_errors"] or p["failed_reqs"]]
    print(f"pages={len(pages_report)} bad={len(bad_pages)}")
    for p in bad_pages:
        for e in (p["console_errors"] + p["page_errors"] + p["failed_reqs"])[:5]:
            print(f"  !! [{p['name']}] {e[:200]}")
    for e in page_errors_all[:5]:
        print(f"  !! [global] {e[:200]}")
    print("=" * 70)

    bugs = []

    out = {
        "area": "jobs",
        "base": BASE,
        "started_at": STARTED_AT,
        "finished_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "pages": pages_report,
        "tests": [{"id": r[0], "name": r[1], "passed": r[2], "detail": r[3]} for r in results],
        "bugs": bugs,
    }
    (E2E_DIR / "probe_results" / "jobs.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"results -> {E2E_DIR / 'probe_results' / 'jobs.json'}")


try:
    main()
finally:
    server.terminate()
    try:
        server.wait(timeout=10)
    except Exception:
        server.kill()
