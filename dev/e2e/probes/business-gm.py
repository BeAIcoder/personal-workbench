# -*- coding: utf-8 -*-
"""business-gm 真实浏览器集成探针（隔离后端、当前前端构建、合成 HTTP 故障）。

覆盖：
- GM 自动挂载加载、真实请求 URL/query、六域与 facility 指标渲染；
- GM HTTP 500 的错误结果、旧指标清除与重试恢复；
- Dashboard summary 200 而 GM 500 时，主数据仍渲染且仅提醒区降级。

运行：D:/DevEnv/runtime/python/python.exe dev/e2e/probes/business-gm.py
"""
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
BACKEND = ROOT / "backend"
RESULTS = ROOT / ".superpowers" / "sdd" / "starfire-hal-jordan-hawkman" / "task-8-fix1-browser"
DB = RESULTS / "workbench.db"
DATA_ROOT = RESULTS / "data"
FIN_DIR = RESULTS / "finance"
DEBUG_DIR = RESULTS / "debug"
TMP_DIR = RESULTS / "tmp"
SHOTS = RESULTS / "shots"
LOG = RESULTS / "server.log"
RESULT_JSON = RESULTS / "result.json"
PORT = 8268
BASE = f"http://127.0.0.1:{PORT}"
STARTED_AT = datetime.now().isoformat(timespec="seconds")

if RESULTS.exists():
    shutil.rmtree(RESULTS)
for directory in (RESULTS, DATA_ROOT, FIN_DIR, DEBUG_DIR, TMP_DIR, SHOTS):
    directory.mkdir(parents=True, exist_ok=True)

env = os.environ.copy()
env.update({
    "DATABASE_URL": f"sqlite:///{DB.as_posix()}",
    "FINANCE_DATA_DIR": str(FIN_DIR),
    "DEBUG_DATA_DIR": str(DEBUG_DIR),
    "WORKBENCH_ENV_FILE": "",
    "SEED_ON_STARTUP": "0",
    "PYTHONIOENCODING": "utf-8",
    "LLM_PROVIDER": "",
    "LLM_BASE_URL": "",
    "LLM_API_KEY": "",
    "LLM_MODEL": "",
    "GM_ISOLATED_DATA": str(DATA_ROOT),
    "GM_PORT": str(PORT),
    "TEMP": str(TMP_DIR),
    "TMP": str(TMP_DIR),
})
env["DATABASE_URL"] = f"sqlite:///{DB.as_posix()}"
env["FINANCE_DATA_DIR"] = str(FIN_DIR)
env["DEBUG_DATA_DIR"] = str(DEBUG_DIR)

RUNNER = r"""
import os
from pathlib import Path
root = Path(os.environ["GM_ISOLATED_DATA"])
from app import config as app_config
app_config.DATA_DIR = root
app_config.UPLOAD_DIR = root / "uploads"
from app.routers import assistant as assistant_router
assistant_router.UPLOAD_DIR = app_config.UPLOAD_DIR
from app.agents import expert_packs, skill_store
expert_packs.DATA_DIR = root
skill_store.DATA_DIR = root
import uvicorn
uvicorn.run(
    "app.main:app", host="127.0.0.1", port=int(os.environ["GM_PORT"]),
    log_level="info",
)
"""

log_fh = open(LOG, "w", encoding="utf-8")
server = subprocess.Popen(
    [str(BACKEND / "venv" / "Scripts" / "python.exe"), "-c", RUNNER],
    cwd=str(BACKEND), env=env, stdout=log_fh, stderr=log_fh,
)
results = []
requests = []
page_errors = []
console_errors = []
page = None


def record(tid, name, fn):
    try:
        detail = fn() or ""
        results.append({"id": tid, "name": name, "passed": True, "detail": detail})
        print(f"[PASS] {tid} {name} | {detail}", flush=True)
    except Exception as exc:
        results.append({"id": tid, "name": name, "passed": False, "detail": str(exc)[:500]})
        print(f"[FAIL] {tid} {name}: {str(exc)[:500]}", flush=True)
        if page is not None:
            page.screenshot(path=str(SHOTS / f"{tid}-FAIL.png"))


def wait_ready(timeout=90):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if server.poll() is not None:
            return False
        try:
            urllib.request.urlopen(f"{BASE}/api/health", timeout=2)
            return True
        except Exception:
            time.sleep(1)
    return False


def gm_response(response):
    return "/api/business/gm/summary" in response.url


def dashboard_response(response):
    return "/api/dashboard/summary" in response.url


def assert_no_page_errors():
    assert not page_errors, f"存在未捕获页面异常：{page_errors[:3]}"


def probe_gm_mount_and_facility():
    with page.expect_response(gm_response, timeout=20000) as info:
        page.goto(f"{BASE}/#/business/gm?period=2026-01", wait_until="domcontentloaded")
    response = info.value
    assert response.status == 200, f"GM 响应状态异常：{response.status}"
    assert "period=2026-01" in response.url, f"GM 未透传期间：{response.url}"
    requests.append(response.url)
    page.wait_for_selector(".domain-card", state="visible", timeout=15000)
    page.wait_for_function("document.querySelectorAll('.domain-card').length === 6")
    assert "总经理经营聚合" in page.locator("h2").inner_text()
    facility = page.locator(".domain-card", has_text="工程物业").inner_text()
    assert "暂不可用" not in facility, facility
    assert "设备总数" in facility and "设备台账记录数" in facility, facility
    noi = page.locator(".metric-item", has_text="净营业收入").inner_text()
    assert "—" in noi and "缺少同源" in noi, noi
    assert page.locator(".el-result").count() == 0
    assert_no_page_errors()
    page.screenshot(path=str(SHOTS / "gm-loaded.png"), full_page=True)
    return f"自动挂载 GET 200；URL={response.url}；六域与 facility 真实指标渲染"


def probe_gm_error_and_retry():
    def fail_gm(route):
        route.fulfill(
            status=500, content_type="application/json",
            body=json.dumps({"detail": "合成 GM 故障"}, ensure_ascii=False),
        )

    page.route("**/api/business/gm/summary*", fail_gm)
    with page.expect_response(gm_response, timeout=20000) as info:
        page.reload(wait_until="domcontentloaded")
    response = info.value
    assert response.status == 500, response.status
    requests.append(response.url)
    result = page.locator(".el-result[role='alert']")
    result.wait_for(state="visible", timeout=15000)
    assert "经营聚合加载失败" in result.inner_text()
    assert page.locator(".domain-card").count() == 0, "错误态仍展示旧指标"
    assert page.get_by_role("button", name="重试").is_visible()
    page.screenshot(path=str(SHOTS / "gm-error.png"), full_page=True)
    page.unroute("**/api/business/gm/summary*", fail_gm)
    with page.expect_response(gm_response, timeout=20000) as retry_info:
        page.get_by_role("button", name="重试").click()
    assert retry_info.value.status == 200
    page.wait_for_function("document.querySelectorAll('.domain-card').length === 6")
    assert_no_page_errors()
    return "HTTP 500 显示 role=alert 错误结果且清空旧指标；重试恢复真实 GM 200"


def probe_dashboard_failure_isolation():
    seeded = page.request.post(f"{BASE}/api/tasks", data={"title": "合成 Dashboard 任务"})
    assert seeded.status == 201, seeded.status

    def fail_gm(route):
        route.fulfill(
            status=500, content_type="application/json",
            body=json.dumps({"detail": "合成 GM 故障"}, ensure_ascii=False),
        )

    page.route("**/api/business/gm/summary*", fail_gm)
    with page.expect_response(dashboard_response, timeout=20000) as dashboard_info:
        with page.expect_response(gm_response, timeout=20000) as gm_info:
            page.goto(f"{BASE}/#/", wait_until="domcontentloaded")
    dashboard = dashboard_info.value
    gm = gm_info.value
    assert dashboard.status == 200, dashboard.status
    assert gm.status == 500, gm.status
    requests.extend([dashboard.url, gm.url])
    page.wait_for_function("document.querySelectorAll('.stat-card .stat-num').length === 4")
    page.wait_for_function(
        "document.querySelector('.stat-card .stat-num')?.textContent.trim() === '1'"
    )
    assert "经营提醒暂不可用" in page.locator(".gm-entry").inner_text()
    assert page.get_by_text("工作台数据加载失败", exact=True).count() == 0
    assert_no_page_errors()
    page.screenshot(path=str(SHOTS / "dashboard-gm-failure.png"), full_page=True)
    page.unroute("**/api/business/gm/summary*", fail_gm)
    return "Dashboard summary=200、GM=500；主卡片任务数=1，只有经营提醒区降级"


try:
    if not wait_ready():
        raise RuntimeError(f"隔离服务未就绪，日志：{LOG}")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.on("pageerror", lambda error: page_errors.append(str(error)))
        page.on("console", lambda message: console_errors.append(message.text)
                if message.type == "error" else None)
        record("GM-E2E-001", "GM 自动挂载、HTTP 请求与工程指标渲染", probe_gm_mount_and_facility)
        record("GM-E2E-002", "GM 错误结果与重试恢复", probe_gm_error_and_retry)
        record("GM-E2E-003", "Dashboard 在 GM 失败时独立成功", probe_dashboard_failure_isolation)
        browser.close()

    raw = {
        "area": "business-gm-fix1",
        "base": BASE,
        "started_at": STARTED_AT,
        "finished_at": datetime.now().isoformat(timespec="seconds"),
        "tests": results,
        "requests": requests,
        "page_errors": page_errors,
        "console_errors": console_errors,
        "note": "console_errors 可含两次有意的合成 HTTP 500 资源日志；page_errors 必须为空。",
    }
    RESULT_JSON.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    passed = sum(item["passed"] for item in results)
    print(f"total={len(results)} passed={passed} failed={len(results) - passed}")
    print(f"page_errors={len(page_errors)}")
    print(f"result -> {RESULT_JSON}")
    if passed != len(results) or page_errors:
        sys.exit(1)
finally:
    server.terminate()
    try:
        server.wait(timeout=10)
    except Exception:
        server.kill()
    log_fh.close()
