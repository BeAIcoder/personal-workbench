# -*- coding: utf-8 -*-
"""E2E 侦察：启动后端（独立测试库）→ 逐页访问 → 收集控制台/页面错误 → 截图 → 输出可操作元素。"""
import os
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
E2E_DIR = ROOT / "dev" / "e2e"
SHOTS = E2E_DIR / "shots"
SHOTS.mkdir(parents=True, exist_ok=True)
DB = E2E_DIR / "e2e.db"
PORT = 8123
BASE = f"http://127.0.0.1:{PORT}"
STARTED_AT = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

if DB.exists():
    DB.unlink()

env = os.environ.copy()
env["DATABASE_URL"] = f"sqlite:///{DB.as_posix()}"
env["PYTHONIOENCODING"] = "utf-8"

server = subprocess.Popen(
    [str(BACKEND / "venv" / "Scripts" / "python.exe"), "-m", "uvicorn",
     "app.main:app", "--host", "127.0.0.1", "--port", str(PORT)],
    cwd=str(BACKEND), env=env,
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
)

def wait_ready(timeout=60):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            urllib.request.urlopen(f"{BASE}/api/dashboard/summary", timeout=2)
            return True
        except Exception:
            time.sleep(1)
    return False

ROUTES = [
    ("/", "dashboard"),
    ("/tasks", "tasks"),
    ("/schedules", "schedules"),
    ("/notes", "notes"),
    ("/assistant", "assistant"),
    ("/jobs", "jobs"),
    ("/knowledge", "knowledge"),
    ("/mcp", "mcp"),
    ("/skill-packs", "skill-packs"),
    ("/expert-packs", "expert-packs"),
    ("/finance/board", "fin-board"),
    ("/finance/cashflow", "fin-cashflow"),
    ("/finance/expense", "fin-expense"),
    ("/finance/hello", "fin-hello"),
    ("/finance/invoice", "fin-invoice"),
    ("/finance/ledger-bip", "fin-ledger-bip"),
    ("/finance/ledger-personal", "fin-ledger-personal"),
    ("/finance/pos", "fin-pos"),
    ("/finance/tax", "fin-tax"),
    ("/finance/vault", "fin-vault"),
]

try:
    if not wait_ready():
        print("FATAL: server not ready")
        sys.exit(2)

    from playwright.sync_api import sync_playwright

    report = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        for route, name in ROUTES:
            console_errors, page_errors, failed_reqs = [], [], []
            page.on("console", lambda m, e=console_errors: e.append(m.text) if m.type == "error" else None)
            page.on("pageerror", lambda e, l=page_errors: l.append(str(e)))
            page.on("requestfailed", lambda r, l=failed_reqs: l.append(f"{r.method} {r.url}"))
            resp = page.goto(f"{BASE}/#{route}", wait_until="networkidle", timeout=30000)
            page.wait_for_timeout(800)
            page.screenshot(path=str(SHOTS / f"{name}.png"))
            status = resp.status if resp else "?"
            # 可操作元素概览
            buttons = [b.inner_text().strip().replace("\n", " ")[:20] for b in page.locator("button:visible").all()[:12]]
            report.append({
                "route": route, "name": name, "status": status,
                "console_errors": console_errors, "page_errors": page_errors,
                "failed_reqs": [f for f in failed_reqs if "favicon" not in f],
                "buttons": buttons,
            })
        browser.close()

    print("=" * 70)
    bad = 0
    for r in report:
        issues = r["console_errors"] + r["page_errors"] + r["failed_reqs"]
        flag = "OK " if not issues else "BAD"
        if issues:
            bad += 1
        print(f"[{flag}] {r['name']:22s} status={r['status']} buttons={r['buttons'][:6]}")
        for e in issues[:5]:
            print(f"      !! {e[:220]}")
    print("=" * 70)
    print(f"pages={len(report)} bad={bad}")

    # 归档到调试中心（backend/data/debug/runs/），失败不阻断原有输出
    try:
        from debug_reporter import save_run

        run_id = save_run(
            "recon",
            base=BASE,
            started_at=STARTED_AT,
            finished_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            pages=report,
            shots_dir=SHOTS,
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
