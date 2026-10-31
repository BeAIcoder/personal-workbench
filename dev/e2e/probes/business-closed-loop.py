# -*- coding: utf-8 -*-
"""五业务模块闭环 E2E：隔离服务 + 真实 API + 当前生产构建浏览器。

场景：铺位/签约 → 合同/账单/部分收款/账龄 → 活动 ROI → 缺考勤薪资 →
设备/逾期合规 → 工单设备关联 → GM 六域聚合与页面渲染。
"""
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import date, timedelta
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
BACKEND = ROOT / "backend"
RESULTS = ROOT / ".superpowers" / "sdd" / "starfire-hal-jordan-hawkman" / "task-9-closed-loop"
DB = RESULTS / "workbench.db"
DATA = RESULTS / "data"
FIN = RESULTS / "finance"
DEBUG_DIR = RESULTS / "debug"
TMP = RESULTS / "tmp"
SHOTS = RESULTS / "shots"
LOG = RESULTS / "server.log"
RESULT = RESULTS / "result.json"
PORT = 8270
BASE = f"http://127.0.0.1:{PORT}"
TODAY = date.today()
PERIOD = TODAY.strftime("%Y-%m")
STARTED = datetime.now().isoformat(timespec="seconds")

if RESULTS.exists():
    shutil.rmtree(RESULTS)
for directory in (RESULTS, DATA, FIN, DEBUG_DIR, TMP, SHOTS):
    directory.mkdir(parents=True, exist_ok=True)

env = os.environ.copy()
env.update({
    "DATABASE_URL": f"sqlite:///{DB.as_posix()}",
    "FINANCE_DATA_DIR": str(FIN),
    "DEBUG_DATA_DIR": str(DEBUG_DIR),
    "WORKBENCH_ENV_FILE": "",
    "SEED_ON_STARTUP": "0",
    "PYTHONIOENCODING": "utf-8",
    "LLM_PROVIDER": "", "LLM_BASE_URL": "", "LLM_API_KEY": "", "LLM_MODEL": "",
    "E2E_ISOLATED_DATA": str(DATA), "E2E_PORT": str(PORT),
    "TEMP": str(TMP), "TMP": str(TMP),
})
env["DATABASE_URL"] = f"sqlite:///{DB.as_posix()}"
env["FINANCE_DATA_DIR"] = str(FIN)
env["DEBUG_DATA_DIR"] = str(DEBUG_DIR)
RUNNER = r"""
import os
from pathlib import Path
root = Path(os.environ["E2E_ISOLATED_DATA"])
from app import config as app_config
app_config.DATA_DIR = root
app_config.UPLOAD_DIR = root / "uploads"
from app.routers import assistant as assistant_router
assistant_router.UPLOAD_DIR = app_config.UPLOAD_DIR
from app.agents import expert_packs, skill_store
expert_packs.DATA_DIR = root
skill_store.DATA_DIR = root
import uvicorn
uvicorn.run("app.main:app", host="127.0.0.1", port=int(os.environ["E2E_PORT"]), log_level="info")
"""

log_fh = open(LOG, "w", encoding="utf-8")
server = subprocess.Popen(
    [str(BACKEND / "venv" / "Scripts" / "python.exe"), "-c", RUNNER],
    cwd=str(BACKEND), env=env, stdout=log_fh, stderr=log_fh,
)
checks = []
page = None
page_errors = []


def record(tid, name, fn):
    try:
        detail = fn() or ""
        checks.append({"id": tid, "name": name, "passed": True, "detail": detail})
        print(f"[PASS] {tid} {name} | {detail}", flush=True)
    except Exception as exc:
        checks.append({"id": tid, "name": name, "passed": False, "detail": str(exc)[:600]})
        print(f"[FAIL] {tid} {name}: {str(exc)[:600]}", flush=True)
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


def request(method, path, payload=None, expected=(200, 201)):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(f"{BASE}{path}", data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            body = response.read()
            assert response.status in expected, f"{method} {path}: {response.status} {body[:300]}"
            return json.loads(body) if body else None
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        raise AssertionError(f"{method} {path}: HTTP {exc.code} {body[:500]}") from exc


state = {}


def seed_and_verify_api():
    unit = request("POST", "/api/business/leasing/units", {
        "building": "E2E-A栋", "floor": "1F", "unit_no": "E2E-A101",
        "area": 100, "rent_price": 80, "property_fee": 10,
        "status": "vacant", "opened_at": TODAY.isoformat(), "note": "闭环合成",
    })
    state["unit_id"] = unit["id"]
    lead = request("POST", "/api/business/leasing/leads", {
        "company": "E2E合成商户", "contact": "合成联系人", "phone": "00000000000",
        "industry": "零售", "demand_area": 100, "budget": 12000, "stage": "signed",
        "owner": "合成招商", "next_action_at": TODAY.isoformat(), "note": "",
        "unit_id": unit["id"], "rent_form": "guaranteed_percentage",
        "free_rent_months": 2, "escalation_rate": 3.5,
        "fitout_subsidy": 10000, "lease_term_months": 36,
    })
    assert lead["stage"] == "signed"
    unit_summary = request("GET", "/api/business/leasing/units/summary")
    assert unit_summary["leased_units"] == 1, unit_summary
    request("POST", "/api/business/leasing/leads", {
        "company": "E2E到期待跟进", "stage": "lead", "next_action_at": TODAY.isoformat(),
    })

    contract = request("POST", "/api/business/operations/contracts", {
        "contract_no": "E2E-HT-001", "tenant": "E2E合成商户", "unit_no": "E2E-A101",
        "area": 100, "start_date": TODAY.replace(day=1).isoformat(),
        "end_date": TODAY.replace(month=12, day=31).isoformat(),
        "rent_monthly": 8000, "property_fee_monthly": 1000, "deposit": 16000,
        "status": "active", "signed_at": TODAY.isoformat(),
    })
    bill = request("POST", f"/api/business/operations/contracts/{contract['id']}/bills", {
        "period": PERIOD, "other_amount": 300,
        "due_date": (TODAY - timedelta(days=40)).isoformat(),
    })
    paid = request("POST", f"/api/business/operations/bills/{bill['id']}/payments", {"amount": 3000})
    assert paid["status"] == "partial"
    aging = request("GET", f"/api/business/operations/bills/aging?as_of={TODAY.isoformat()}")
    assert aging["buckets"]["31_90"]["amount"] == 6300, aging

    campaign = request("POST", "/api/business/planning/campaigns", {
        "name": "E2E闭环活动", "year": TODAY.year, "category": "promo",
        "start_date": TODAY.replace(day=1).isoformat(),
        "end_date": TODAY.replace(day=2).isoformat(),
        "owner": "合成企划", "channel": "线下", "target": "合成目标", "status": "done",
        "budget_items": [{"item": "物料", "budget": 2000, "actual": 2000}],
        "grade": "S", "sales_baseline": 10000, "sales_during": 15000,
        "merchant_share": 500, "roi_target": 1.5,
    })
    assert campaign["roi_source"] == "calculated" and campaign["roi"] == 2.0, campaign
    assert campaign["target_met"] is True

    employee = request("POST", "/api/business/hr/employees", {
        "emp_no": "E2E001", "name": "合成员工", "department": "营运",
        "position": "专员", "hire_date": TODAY.replace(day=1).isoformat(),
        "status": "active", "base_salary": 12000, "social_base": 0,
    })
    payroll = request("POST", "/api/business/hr/payroll/generate", {"month": PERIOD})
    assert payroll["warnings"] and "缺少考勤" in payroll["warnings"][0], payroll

    equipment = request("POST", "/api/business/facility/equipment", {
        "code": "E2E-EQ-001", "name": "E2E消防泵", "category": "A",
        "location": "B1", "install_date": TODAY.isoformat(), "status": "running",
    })
    compliance = request("POST", "/api/business/facility/compliance", {
        "equipment_id": equipment["id"], "kind": "fire", "title": "E2E消防检查",
        "due_date": (TODAY - timedelta(days=1)).isoformat(),
    })
    assert compliance["status"] == "overdue"
    order = request("POST", "/api/business/operations/work-orders", {
        "type": "repair", "location": "B1", "tenant": "E2E合成商户",
        "priority": "urgent", "description": "E2E消防泵异响",
        "equipment_id": equipment["id"], "due_at": (TODAY + timedelta(days=1)).isoformat() + "T09:00:00",
    })
    assert order["equipment_code"] == "E2E-EQ-001"
    request("POST", f"/api/business/operations/work-orders/{order['id']}/followups", {
        "content": "E2E已现场处理", "result": "待复测", "satisfaction": 5,
    })

    gm = request("GET", f"/api/business/gm/summary?period={PERIOD}")
    assert gm["domains"]["facility"]["available"] is True
    assert gm["domains"]["facility"]["data"]["equipment_count"] == 1
    assert gm["domains"]["facility"]["data"]["overdue"] == 1
    assert gm["domains"]["operations"]["data"]["arrears"] == 6300
    assert gm["domains"]["leasing"]["data"]["due_followups"] == 1
    assert gm["domains"]["planning"]["data"]["campaigns"] == 1
    state["contract_id"] = contract["id"]
    return "API 闭环：签约锁铺、账龄 6300、ROI=2.0、薪资 warning、逾期合规、工单设备、GM 六域"


def check_facility_page():
    page.goto(f"{BASE}/#/business/facility", wait_until="domcontentloaded")
    page.wait_for_selector("text=E2E-EQ-001", timeout=15000)
    page.get_by_role("tab", name="合规到期").click()
    page.wait_for_selector("text=E2E消防检查", timeout=15000)
    assert page.locator("text=已逾期").first.is_visible()
    assert not page_errors, page_errors[:3]
    page.screenshot(path=str(SHOTS / "facility.png"), full_page=True)
    return "真实浏览器显示设备 E2E-EQ-001 与逾期消防检查"


def check_gm_page():
    page.goto(f"{BASE}/#/business/gm?period={PERIOD}", wait_until="domcontentloaded")
    page.wait_for_function("document.querySelectorAll('.domain-card').length === 6")
    facility = page.locator(".domain-card", has_text="工程物业").inner_text()
    assert "暂不可用" not in facility, facility
    assert "1" in facility, facility
    assert not page_errors, page_errors[:3]
    page.screenshot(path=str(SHOTS / "gm.png"), full_page=True)
    return "GM 六域渲染，工程物业已可用且非占位"


def check_planning_and_operations_pages():
    page.goto(f"{BASE}/#/business/planning", wait_until="domcontentloaded")
    page.wait_for_selector(".el-table__row:has-text('E2E闭环活动')", timeout=15000)
    row = page.locator(".el-table__row", has_text="E2E闭环活动").first.inner_text()
    assert "2" in row and "S" in row, row
    page.screenshot(path=str(SHOTS / "planning.png"), full_page=True)

    page.goto(f"{BASE}/#/business/operations", wait_until="domcontentloaded")
    page.get_by_role("tab", name="工单").click()
    page.wait_for_selector(".el-table__row:has-text('E2E消防泵异响')", timeout=15000)
    order_row = page.locator(".el-table__row", has_text="E2E消防泵异响").first.inner_text()
    assert "E2E-EQ-001" in order_row, order_row
    assert not page_errors, page_errors[:3]
    page.screenshot(path=str(SHOTS / "operations.png"), full_page=True)
    return "企划显示 calculated ROI/等级，营运工单显示关联设备"


try:
    if not wait_ready():
        raise RuntimeError(f"隔离服务未就绪：{LOG}")
    record("BIZ-E2E-001", "API 五模块闭环与 GM 聚合", seed_and_verify_api)

    from playwright.sync_api import sync_playwright
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.on("pageerror", lambda error: page_errors.append(str(error)))
        record("BIZ-E2E-002", "工程设备与逾期合规页面", check_facility_page)
        record("BIZ-E2E-003", "GM 六域聚合页面", check_gm_page)
        record("BIZ-E2E-004", "企划 ROI 与工单设备页面", check_planning_and_operations_pages)
        browser.close()

    raw = {
        "area": "business-closed-loop",
        "base": BASE,
        "period": PERIOD,
        "started_at": STARTED,
        "finished_at": datetime.now().isoformat(timespec="seconds"),
        "checks": checks,
        "page_errors": page_errors,
        "state": state,
    }
    RESULT.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    passed = sum(item["passed"] for item in checks)
    print(f"total={len(checks)} passed={passed} failed={len(checks) - passed}")
    print(f"page_errors={len(page_errors)}")
    print(f"result -> {RESULT}")
    if passed != len(checks) or page_errors:
        sys.exit(1)
finally:
    server.terminate()
    try:
        server.wait(timeout=10)
    except Exception:
        server.kill()
    log_fh.close()
