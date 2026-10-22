# -*- coding: utf-8 -*-
"""E2E 边界测试：LLM 未配置降级、404 重定向、数据仓 SQL、会话归档页签。

隔离：独立测试库 + 独立财务数据目录 + 独立调试归档目录（均在 dev/e2e 下，
不碰 backend/data 真实数据）；固定端口 8124，与另两个脚本互斥运行。
"""
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

from debug_reporter import acquire_run_lock

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
E2E_DIR = ROOT / "dev" / "e2e"
DB = E2E_DIR / "e2e_edge.db"
FIN_DIR = E2E_DIR / "finance_data_edge"
DEBUG_DIR = E2E_DIR / "debug_data_edge"
PORT = 8124
BASE = f"http://127.0.0.1:{PORT}"
STARTED_AT = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

# 运行互斥：三脚本固定端口 8123/8124，禁止并行执行（并行会互相踩端口/目录）
release_lock = acquire_run_lock()

if DB.exists():
    DB.unlink()
if FIN_DIR.exists():
    shutil.rmtree(FIN_DIR)
if DEBUG_DIR.exists():
    shutil.rmtree(DEBUG_DIR)

# 隔离三件套（dev/e2e 下各自独立，绝不指向 backend/data 真实数据）：
# DEBUG_DATA_DIR 同时写入父进程 os.environ——归档器 debug_reporter 在本进程内
# 读取同一变量，保证归档目录与服务端读取的调试目录一致
os.environ["DEBUG_DATA_DIR"] = str(DEBUG_DIR)
env = os.environ.copy()
env["DATABASE_URL"] = f"sqlite:///{DB.as_posix()}"
env["FINANCE_DATA_DIR"] = str(FIN_DIR)
env["DEBUG_DATA_DIR"] = str(DEBUG_DIR)
env["PYTHONIOENCODING"] = "utf-8"

server = subprocess.Popen(
    [str(BACKEND / "venv" / "Scripts" / "python.exe"), "-m", "uvicorn",
     "app.main:app", "--host", "127.0.0.1", "--port", str(PORT)],
    cwd=str(BACKEND), env=env,
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
)

results = []

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
page_errors = []

def goto(route):
    page.goto(f"{BASE}/#{route}", wait_until="networkidle", timeout=30000)
    page.wait_for_timeout(500)

try:
    if not wait_ready():
        print("FATAL: server not ready"); sys.exit(2)

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.on("pageerror", lambda e: page_errors.append(str(e)))

        def tc_assistant_degrade():
            page_errors.clear()
            goto("/assistant")
            ta = page.locator("textarea[placeholder*='输入问题']")
            ta.fill("你好，测试一下")
            page.locator("button", has_text="发送").first.click()
            page.wait_for_timeout(3000)
            # 未配置 LLM 时应给出友好提示而不是崩溃：页面仍在，无未捕获异常
            assert page.locator("textarea[placeholder*='输入问题']").count() > 0, "页面崩溃"
            fatal = [e for e in page_errors]
            assert not fatal, f"出现未捕获异常: {fatal[:2]}"
        record("TC-E2E-EDGE-001", "助手未配置 LLM 降级", tc_assistant_degrade)

        def tc_404_redirect():
            page_errors.clear()
            goto("/no-such-page-xyz")
            page.wait_for_timeout(800)
            assert "工作台概览" in page.content() or page.locator("text=工作台概览").count() > 0, \
                "404 未重定向到工作台概览"
        record("TC-E2E-EDGE-002", "404 路由重定向", tc_404_redirect)

        def tc_vault_query():
            page_errors.clear()
            goto("/finance/vault/query")
            page.locator("textarea").first.fill("SELECT 1 AS t")
            page.locator("button", has_text="执行").first.click()
            page.wait_for_timeout(2000)
            content = page.content()
            ok = ("t" in content and page.locator("td:visible,th:visible").count() > 0)
            graceful_err = page.locator(".el-message, .el-alert").count() > 0 or page.locator("text=错误").count() > 0
            assert ok or graceful_err, "SQL 执行无结果且无提示"
            fatal = [e for e in page_errors]
            assert not fatal, f"出现未捕获异常: {fatal[:2]}"
        record("TC-E2E-EDGE-003", "数据仓 SQL 查询", tc_vault_query)

        def tc_archive_tab():
            page_errors.clear()
            goto("/assistant")
            tab = page.locator("text=已归档").first
            if tab.count() == 0:
                raise AssertionError("归档页签不存在")
            tab.click()
            page.wait_for_timeout(800)
            fatal = [e for e in page_errors]
            assert not fatal, f"切换归档页签出现异常: {fatal[:2]}"
        record("TC-E2E-EDGE-004", "会话归档页签", tc_archive_tab)

        def tc_invalid_task_submit():
            page_errors.clear()
            goto("/tasks")
            page.locator("button", has_text="新建任务").first.click()
            page.wait_for_timeout(400)
            # 不填标题直接保存，应触发表单校验而不是提交
            page.locator(".el-dialog:visible button", has_text="保存").first.click()
            page.wait_for_timeout(600)
            assert page.locator(".el-dialog:visible").count() > 0, "空标题也能提交（缺少校验）"
            assert page.locator(".el-form-item__error, .el-message").count() > 0, "缺少校验提示"
            page.locator(".el-dialog:visible button", has_text="取消").first.click()
        record("TC-E2E-EDGE-005", "空标题任务校验", tc_invalid_task_submit)

        browser.close()

    print("=" * 70)
    passed = sum(1 for r in results if r[2])
    print(f"total={len(results)} passed={passed} failed={len(results)-passed}")

    # 归档到调试中心（本脚本独立 DEBUG_DIR），失败不阻断原有输出
    try:
        from debug_reporter import save_run

        run_id = save_run(
            "edge",
            base=BASE,
            started_at=STARTED_AT,
            finished_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            tests=results,
            extra={"page_errors": page_errors},
        )
        print(f"run_id={run_id}（已归档到 {DEBUG_DIR}）")
    except Exception as e:
        print(f"调试中心归档跳过：{e}")
finally:
    server.terminate()
    try:
        server.wait(timeout=10)
    except Exception:
        server.kill()
    release_lock()
