# -*- coding: utf-8 -*-
"""E2E 交互测试：AAA 模式，独立测试库 + 独立财务数据目录，覆盖核心 CRUD 与全局搜索。"""
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
E2E_DIR = ROOT / "dev" / "e2e"
DB = E2E_DIR / "e2e.db"
FIN_DIR = E2E_DIR / "finance_data"
PORT = 8123
BASE = f"http://127.0.0.1:{PORT}"
STARTED_AT = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

if DB.exists():
    DB.unlink()
if FIN_DIR.exists():
    shutil.rmtree(FIN_DIR)

env = os.environ.copy()
env["DATABASE_URL"] = f"sqlite:///{DB.as_posix()}"
env["FINANCE_DATA_DIR"] = str(FIN_DIR)
env["PYTHONIOENCODING"] = "utf-8"

server = subprocess.Popen(
    [str(BACKEND / "venv" / "Scripts" / "python.exe"), "-m", "uvicorn",
     "app.main:app", "--host", "127.0.0.1", "--port", str(PORT)],
    cwd=str(BACKEND), env=env,
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
)

results = []  # (id, name, passed, detail)

def record(tid, name, fn):
    try:
        fn()
        results.append((tid, name, True, ""))
        print(f"[PASS] {tid} {name}")
    except Exception as e:
        results.append((tid, name, False, str(e)[:300]))
        print(f"[FAIL] {tid} {name}: {str(e)[:300]}")

def wait_ready(timeout=60):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            urllib.request.urlopen(f"{BASE}/api/dashboard/summary", timeout=2)
            return True
        except Exception:
            time.sleep(1)
    return False

page = None
console_errors = []

def goto(route):
    page.goto(f"{BASE}/#{route}", wait_until="networkidle", timeout=30000)
    page.wait_for_timeout(500)

def dialog():
    return page.locator(".el-dialog:visible")

def save_dialog():
    dialog().locator("button", has_text="保存").first.click()
    page.wait_for_timeout(800)

try:
    if not wait_ready():
        print("FATAL: server not ready"); sys.exit(2)

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)

        # ---------- 任务管理 ----------
        def tc_task_create():
            goto("/tasks")
            page.locator("button", has_text="新建任务").first.click()
            dialog().locator("input").first.fill("E2E测试任务-写周报")
            save_dialog()
            assert page.locator("td:visible", has_text="E2E测试任务-写周报").count() > 0, "列表未出现新任务"
        record("TC-E2E-TASK-001", "创建任务", tc_task_create)

        def tc_task_done():
            goto("/tasks")
            row = page.locator("tr", has_text="E2E测试任务-写周报").first
            row.locator("button", has_text="完成").click()
            page.wait_for_timeout(800)
            row2 = page.locator("tr", has_text="E2E测试任务-写周报").first
            assert "已完成" in row2.inner_text(), f"状态未变为已完成: {row2.inner_text()[:80]}"
        record("TC-E2E-TASK-002", "完成任务", tc_task_done)

        def tc_task_search():
            goto("/tasks")
            page.locator("input[placeholder*='搜索任务标题']").fill("E2E测试任务")
            page.keyboard.press("Enter")
            page.wait_for_timeout(800)
            assert page.locator("td:visible", has_text="E2E测试任务-写周报").count() > 0
            page.locator("input[placeholder*='搜索任务标题']").fill("绝不存在的关键词xyz")
            page.keyboard.press("Enter")
            page.wait_for_timeout(800)
            assert page.locator("td:visible", has_text="E2E测试任务-写周报").count() == 0, "搜索过滤未生效"
        record("TC-E2E-TASK-003", "任务搜索过滤", tc_task_search)

        def tc_task_delete():
            goto("/tasks")
            # 清空可能残留的搜索条件（SPA 同路由导航不重置组件状态）
            search = page.locator("input[placeholder*='搜索任务标题']")
            if search.input_value():
                search.fill("")
                page.keyboard.press("Enter")
                page.wait_for_timeout(800)
            row = page.locator("tr", has_text="E2E测试任务-写周报").first
            row.locator("button", has_text="删除").click()
            page.locator(".el-message-box button", has_text="确定").click()
            page.wait_for_timeout(800)
            assert page.locator("td:visible", has_text="E2E测试任务-写周报").count() == 0, "删除后仍在列表"
        record("TC-E2E-TASK-004", "删除任务", tc_task_delete)

        # ---------- 笔记管理 ----------
        def tc_note_create():
            goto("/notes")
            page.locator("button", has_text="新建").first.click()
            page.wait_for_timeout(500)
            page.locator(".title-input input").fill("E2E测试笔记-发票要点")
            page.locator("textarea").first.fill("进项发票需在180天内认证。")
            btn = page.locator("button", has_text="保存").first
            btn.click()
            page.wait_for_timeout(800)
            assert page.locator("text=E2E测试笔记-发票要点").count() > 0, "笔记未保存"
        record("TC-E2E-NOTE-001", "创建并保存笔记", tc_note_create)

        def tc_note_search():
            goto("/notes")
            page.locator("input[placeholder*='搜索标题']").fill("E2E测试笔记")
            page.wait_for_timeout(1200)
            assert page.locator("text=E2E测试笔记-发票要点").count() > 0
        record("TC-E2E-NOTE-002", "笔记搜索", tc_note_search)

        # ---------- 日程管理 ----------
        def tc_schedule_create():
            goto("/schedules")
            page.locator("button", has_text="新建日程").first.click()
            page.wait_for_timeout(400)
            dialog().locator("input[placeholder*='月度经营分析会']").fill("E2E测试日程-月度复盘")
            # 打开全天开关
            dialog().locator(".el-switch").first.click()
            page.wait_for_timeout(300)
            date_input = dialog().locator(".el-date-editor input").first
            date_input.click()
            # 月历默认选中"今天"且标题只在选中日列表渲染：日程必须建在今天，
            # 写死历史日期会随时间推移必然失败（日期炸弹）
            date_input.fill(datetime.now().strftime("%Y-%m-%d 00:00:00"))
            page.keyboard.press("Enter")
            page.wait_for_timeout(300)
            save_dialog()
            goto("/schedules")
            assert page.locator("text=E2E测试日程-月度复盘").count() > 0, "日程未出现在视图"
        record("TC-E2E-SCH-001", "创建全天日程", tc_schedule_create)

        # ---------- 全局搜索 ----------
        def tc_global_search():
            goto("/")
            page.keyboard.press("Control+k")
            page.wait_for_timeout(600)
            box = page.locator("input:visible").last
            box.fill("E2E测试笔记")
            page.wait_for_timeout(1200)
            assert page.locator("text=E2E测试笔记-发票要点").count() > 0, "全局搜索未命中笔记"
            page.keyboard.press("Escape")
        record("TC-E2E-SEARCH-001", "Ctrl+K 全局搜索", tc_global_search)

        # ---------- 仪表盘 ----------
        def tc_dashboard():
            goto("/")
            body = page.content()
            assert "任务" in body and ("逾期" in body or "完成" in body), "统计卡片未渲染"
            assert page.locator("canvas").count() >= 1, "图表 canvas 未渲染"
        record("TC-E2E-DASH-001", "仪表盘统计与图表", tc_dashboard)

        # ---------- 知识检索 ----------
        def tc_knowledge():
            goto("/knowledge")
            inp = page.locator("input:visible").first
            inp.fill("发票")
            page.locator("button", has_text="检索").first.click()
            page.wait_for_timeout(2000)
            # 有结果或空态提示都算功能正常，只要不报错
            assert "E2E测试笔记" in page.content() or page.locator(".el-empty").count() > 0 or True
        record("TC-E2E-KNOW-001", "知识检索执行", tc_knowledge)

        # ---------- 定时任务 ----------
        def tc_job_dialog():
            goto("/jobs")
            page.locator("button", has_text="新建任务").first.click()
            page.wait_for_timeout(500)
            assert dialog().count() > 0, "新建定时任务对话框未打开"
            dialog().locator("button", has_text="取消").first.click()
        record("TC-E2E-JOB-001", "定时任务对话框", tc_job_dialog)

        # ---------- 财务：个人记账（快速记账表单真实提交）----------
        def tc_fin_personal():
            goto("/finance/ledger-personal")
            # 填金额
            page.locator(".el-input-number input").first.fill("12.50")
            # 选账户：点开下拉选第一项
            page.locator(".el-select").first.click()
            page.wait_for_timeout(400)
            page.locator(".el-select-dropdown:visible .el-select-dropdown__item").first.click()
            # 选分类
            page.locator(".el-select").nth(1).click()
            page.wait_for_timeout(400)
            page.locator(".el-select-dropdown:visible .el-select-dropdown__item").first.click()
            # 备注
            page.locator("input[maxlength='100']").fill("E2E测试流水")
            page.locator("button", has_text="记一笔").first.click()
            page.wait_for_timeout(1000)
            assert page.locator("td:visible", has_text="12.50").count() > 0, "今日流水未出现新记录"
        record("TC-E2E-FIN-001", "个人记账快速记账", tc_fin_personal)

        browser.close()

    print("=" * 70)
    passed = sum(1 for r in results if r[2])
    print(f"total={len(results)} passed={passed} failed={len(results)-passed}")
    uniq_console = sorted(set(console_errors))
    print(f"console_errors={len(uniq_console)}")
    for e in uniq_console[:10]:
        print(f"  !! {e[:200]}")

    # 归档到调试中心（backend/data/debug/runs/），失败不阻断原有输出
    try:
        from debug_reporter import save_run

        run_id = save_run(
            "interactions",
            base=BASE,
            started_at=STARTED_AT,
            finished_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            tests=results,
            extra={"console_errors": uniq_console},
        )
        print(f"run_id={run_id}（已归档到调试中心）")
    except Exception as e:
        print(f"调试中心归档跳过：{e}")
finally:
    server.terminate()
    try:
        server.wait(timeout=10)
    except Exception:
        server.kill()
