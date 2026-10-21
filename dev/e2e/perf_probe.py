# -*- coding: utf-8 -*-
"""个人工作台性能基线探针（端口 8230，独立库，可重复运行）。

目的不是压测并发，而是在接近真实单机使用量下回答三个问题：
1. 核心页面依赖的 API 冷/热态 p95 是否会话化地变慢；
2. 慢点来自 SQL 扫描、Python O(N)、响应体积还是前端资源；
3. 优化后是否有可比较的数字，而不是凭感觉「感觉变快了」。

用法：
    D:/DevEnv/runtime/python/python.exe dev/e2e/perf_probe.py

运行模式：
    --seed-only  只重建独立性能库（调试数据规模时用）
    --no-browser 跳过 Playwright 首屏测量
    --quick      小数据量自检（约 1/10 规模）

结果：
    dev/e2e/probe_results/perf/result.json
    dev/e2e/probe_results/perf/report.md
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
OUT = ROOT / "dev" / "e2e" / "probe_results" / "perf"
DB = OUT / "perf.db"
FIN_DIR = OUT / "finance-data"
DEBUG_DIR = OUT / "debug-data"
UPLOAD_DIR = OUT / "uploads"
LOG = OUT / "server.log"
SEED_LOG = OUT / "seed.log"
PORT = 8230
BASE = f"http://127.0.0.1:{PORT}"
BACKEND_PY = str(BACKEND / "venv" / "Scripts" / "python.exe")


def env_for_seed() -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "DATABASE_URL": f"sqlite:///{DB.as_posix()}",
            "FINANCE_DATA_DIR": str(FIN_DIR),
            "DEBUG_DATA_DIR": str(DEBUG_DIR),
            "UPLOAD_DIR": str(UPLOAD_DIR),
            "SEED_ON_STARTUP": "0",
            "PYTHONIOENCODING": "utf-8",
            "LLM_API_KEY": "",
            "LLM_BASE_URL": "",
            "LLM_MODEL": "",
            "LLM_PROVIDER": "",
        }
    )
    return env


SEED_SCRIPT = r'''
# -*- coding: utf-8 -*-
"""在独立 SQLite 库中构造真实规模数据。只由 perf_probe.py 以子进程调用。"""
import json
import os
import random
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, os.environ["BACKEND_DIR"])

from app.database import Base, SessionLocal, engine
from app.main import app  # noqa: F401  导入即加载所有模块表
from app.models import AgentSession, ChatMessage, Note, Schedule, Task
from app.modules.finance_ledger_personal import service as pl_service
from app.modules.finance_ledger_personal.models import PlTransaction

SCALE = float(sys.argv[1]) if len(sys.argv) > 1 else 1.0
N_TASKS = max(1, int(5000 * SCALE))
N_NOTES = max(1, int(2000 * SCALE))
N_SCHEDULES = max(1, int(1000 * SCALE))
N_SESSIONS = max(1, int(200 * SCALE))
N_MSG = 20
N_TX = max(1, int(20000 * SCALE))

random.seed(20261020)
statuses = ["todo", "in_progress", "done"]
priorities = ["low", "medium", "high", "urgent"]
categories = ["税务", "报销", "开发", "生活", "学习"]
tags = ["税务", "报销", "合同", "客户", "AI", "月度"]
kinds = ["expense", "income"]
now = datetime.now()
today = now.date()

Base.metadata.create_all(bind=engine)
db = SessionLocal()
try:
    pl_service.ensure_seed(db)
    accounts = [r.id for r in db.query(pl_service.PlAccount).all()]
    cats = [r.id for r in db.query(pl_service.PlCategory).all()]
    if not accounts or not cats:
        raise SystemExit("记账默认账户/分类灌入失败")

    def insert(model, rows):
        table = model.__table__
        for i in range(0, len(rows), 1000):
            db.execute(table.insert(), rows[i:i + 1000])
        db.commit()

    tasks = []
    for i in range(1, N_TASKS + 1):
        created = now - timedelta(days=random.randint(0, 540), hours=random.randint(0, 23))
        status = random.choices(statuses, weights=[35, 20, 45])[0]
        due_offset = random.randint(-90, 120)
        due = created + timedelta(days=due_offset) if i % 7 else None
        completed = due + timedelta(hours=1) if status == "done" and due else (
            created + timedelta(days=random.randint(1, 20)) if status == "done" else None
        )
        tasks.append({
            "title": f"性能任务 {i:05d}：{'处理发票' if i % 3 == 0 else '核对申报'}-{'浙江税地科技' if i % 11 == 0 else '客户' + str(i % 40)}",
            "description": f"性能样本说明 {i}，包含关键词 发票 申报 报销 合同。" + ("x" * (i % 160)),
            "status": status,
            "priority": random.choice(priorities),
            "due_date": due,
            "category": random.choice(categories),
            "completed_at": completed,
            "created_at": created,
            "updated_at": max(created, completed or created),
        })
    insert(Task, tasks)

    notes = []
    for i in range(1, N_NOTES + 1):
        created = now - timedelta(days=random.randint(0, 365), minutes=random.randint(0, 1439))
        content = (
            f"性能笔记正文 {i}。主题：{'税务申报' if i % 4 == 0 else '费用报销'}。"
            + ("补充说明与待办事项。" * (2 + i % 25))
        )
        chosen = random.sample(tags, k=random.randint(0, 3))
        notes.append({
            "title": f"性能笔记 {i:05d}：{'月结' if i % 5 == 0 else '会议'}纪要",
            "content": content,
            "tags": chosen,
            "pinned": i % 97 == 0,
            "created_at": created,
            "updated_at": created + timedelta(hours=i % 48),
        })
    insert(Note, notes)

    schedules = []
    for i in range(1, N_SCHEDULES + 1):
        start = now + timedelta(days=random.randint(-120, 120), hours=random.randint(8, 18))
        if i % 50 == 0:
            start = now.replace(hour=9, minute=30, second=0, microsecond=0)
        end = start + timedelta(minutes=random.choice([30, 60, 90]))
        schedules.append({
            "title": f"性能日程 {i:04d}：{'客户沟通' if i % 3 == 0 else '资料准备'}",
            "description": f"日程备注 {i}",
            "location": f"会议室 {i % 8 + 1}" if i % 2 else "线上",
            "start_time": start,
            "end_time": end,
            "all_day": False,
            "color": "#409EFF",
            "created_at": now - timedelta(days=random.randint(0, 240)),
            "updated_at": now - timedelta(days=random.randint(0, 30)),
        })
    insert(Schedule, schedules)

    sessions = []
    messages = []
    for i in range(1, N_SESSIONS + 1):
        sid = f"perf{i:06d}"
        created = now - timedelta(days=random.randint(0, 200))
        sessions.append({
            "session_id": sid,
            "title": f"性能会话 {i:04d}：{'发票核对' if i % 4 == 0 else 'AI 备会'}",
            "archived": i % 31 == 0,
            "created_at": created,
            "updated_at": created + timedelta(hours=N_MSG),
        })
        for j in range(1, N_MSG + 1):
            ts = created + timedelta(minutes=j * 3)
            role = "user" if j % 2 else "assistant"
            messages.append({
                "session_id": sid,
                "role": role,
                "agent_name": "" if role == "user" else "finance_specialist",
                "content": f"性能消息 {i}/{j}：" + ("上下文注入测试，关键词 发票 报销。" * (j % 7 + 1)),
                "trace": [{"tool": "pl_monthly_summary", "args": {"month": "2026-10"}, "result": "ok"}] if j % 5 == 0 else [],
                "thinking": "" if j % 3 else "思考样本" * (j % 10),
                "created_at": ts,
                "updated_at": ts,
            })
        if len(sessions) >= 1000:
            insert(AgentSession, sessions)
            insert(ChatMessage, messages)
            sessions, messages = [], []
    if sessions:
        insert(AgentSession, sessions)
    if messages:
        insert(ChatMessage, messages)

    tx = []
    for i in range(1, N_TX + 1):
        d = today - timedelta(days=random.randint(0, 720))
        tx.append({
            "kind": random.choices(kinds, weights=[82, 18])[0],
            "amount": round(random.uniform(5, 3000), 2),
            "occurred_on": d,
            "account_id": random.choice(accounts),
            "category_id": random.choice(cats),
            "note": f"性能流水 {i:06d}：{'交通' if i % 3 == 0 else '餐饮'}报销",
            "tags": random.sample(tags, k=random.randint(0, 2)),
            "created_at": datetime.combine(d, datetime.min.time()),
            "updated_at": datetime.combine(d, datetime.min.time()),
        })
    insert(PlTransaction, tx)

    counts = {
        "tasks": db.query(Task).count(),
        "notes": db.query(Note).count(),
        "schedules": db.query(Schedule).count(),
        "sessions": db.query(AgentSession).count(),
        "messages": db.query(ChatMessage).count(),
        "transactions": db.query(PlTransaction).count(),
    }
    print(json.dumps(counts, ensure_ascii=False))
finally:
    db.close()
'''


def prepare_dirs(clear: bool) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    if clear:
        for p in (DB, LOG, SEED_LOG):
            if p.exists():
                p.unlink()
        for d in (FIN_DIR, DEBUG_DIR, UPLOAD_DIR):
            if d.exists():
                shutil.rmtree(d)
    for d in (FIN_DIR, DEBUG_DIR, UPLOAD_DIR):
        d.mkdir(parents=True, exist_ok=True)


def seed(scale: int) -> dict:
    env = env_for_seed()
    env["BACKEND_DIR"] = str(BACKEND)
    print(f"[seed] 构造数据（scale={scale}）...", flush=True)
    t0 = time.perf_counter()
    proc = subprocess.run(
        [BACKEND_PY, "-c", SEED_SCRIPT, str(scale)],
        cwd=str(BACKEND),
        env=env,
        text=True,
        capture_output=True,
        encoding="utf-8",
    )
    SEED_LOG.write_text((proc.stdout or "") + "\n" + (proc.stderr or ""), encoding="utf-8")
    if proc.returncode:
        raise RuntimeError(f"数据灌入失败 rc={proc.returncode}\n{proc.stderr[-3000:]}")
    counts = json.loads(proc.stdout.strip().splitlines()[-1])
    print(f"[seed] 完成，耗时 {time.perf_counter() - t0:.1f}s：{counts}", flush=True)
    return counts


def wait_ready(timeout: int = 90) -> None:
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            with urllib.request.urlopen(f"{BASE}/api/health", timeout=2) as r:
                if r.status == 200:
                    return
        except Exception:
            time.sleep(0.5)
    raise RuntimeError("后端未在约定时间就绪")


def percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    xs = sorted(values)
    pos = (len(xs) - 1) * q
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return xs[lo]
    return xs[lo] * (hi - pos) + xs[hi] * (pos - lo)


def request_once(path: str, method: str = "GET") -> tuple[float, int, int]:
    # 兜底编码：路径里若还有非 ASCII（中文关键词等），http.client 会以 ascii 抛错。
    # safe 保留 URL 结构字符，已手工 quote 过的 %xx 不会被二次编码。
    safe_path = urllib.parse.quote(path, safe="/?&=:%-_~.")
    url = f"{BASE}{safe_path}"
    req = urllib.request.Request(url, method=method)
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read()
            code = resp.status
    except urllib.error.HTTPError as exc:
        # 5xx 是被测系统的真实故障，不是基准脚本的故障：记录状态码继续跑完，
        # 让报告把失败接口列出来（历史上深分页 500 就是在这里直接把脚本炸掉的）
        body = exc.read() if exc.fp else b""
        code = exc.code
    except OSError as exc:
        return (time.perf_counter() - t0) * 1000, -1, len(str(exc).encode("utf-8"))
    return (time.perf_counter() - t0) * 1000, code, len(body)


def benchmark(path: str, samples: int = 30, warmup: int = 5) -> dict:
    for _ in range(warmup):
        request_once(path)
    values: list[float] = []
    sizes: list[int] = []
    codes: list[int] = []
    for _ in range(samples):
        ms, code, size = request_once(path)
        values.append(ms)
        sizes.append(size)
        codes.append(code)
    return {
        "path": path,
        "samples": samples,
        "p50_ms": round(percentile(values, 0.5), 2),
        "p95_ms": round(percentile(values, 0.95), 2),
        "p99_ms": round(percentile(values, 0.99), 2),
        "max_ms": round(max(values), 2),
        "avg_body_bytes": int(sum(sizes) / len(sizes)),
        "status": max(set(codes), key=codes.count),
        "failed_samples": sum(1 for c in codes if c >= 400 or c < 0),
    }


def run_api_benchmarks() -> list[dict]:
    current_month = date.today().strftime("%Y-%m")
    start = (date.today().replace(day=1) - timedelta(days=35)).isoformat()
    end = (date.today().replace(day=1) + timedelta(days=35)).isoformat()
    paths = [
        "/api/health",
        "/api/dashboard/summary",
        "/api/tasks?page=1&page_size=20",
        "/api/tasks?page=250&page_size=20",
        "/api/tasks?page=1&page_size=20&q=" + urllib.parse.quote("发票") + "&status=todo",
        "/api/notes?page=1&page_size=20",
        "/api/notes/tags",
        "/api/schedules?start=%s&end=%s&page_size=200" % (start, end),
        "/api/search?q=" + urllib.parse.quote("发票"),
        "/api/assistant/sessions?limit=50",
        "/api/assistant/history/perf000001?limit=50",
        "/api/knowledge/memories",
        "/api/jobs",
        f"/api/finance/ledger/personal/transactions?month={current_month}&page=1&page_size=20",
        f"/api/finance/ledger/personal/transactions?month={current_month}&page=20&page_size=500",
        f"/api/finance/ledger/personal/stats/monthly?month={current_month}",
        "/api/finance/ledger/personal/stats/trend?months=24",
        f"/api/finance/ledger/personal/budgets?month={current_month}",
    ]
    out = []
    for p in paths:
        print(f"[bench] {p}", flush=True)
        out.append(benchmark(p))
    return out


def query_plans() -> list[dict]:
    """读取已播种库的执行计划，定位「索引是否被用上」而非只靠耗时猜。"""
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    cases = [
        (
            "dashboard: 近 7 天完成趋势（按 completed_at 范围取回后在 Python 侧按天计数）",
            "SELECT completed_at FROM tasks WHERE completed_at >= ? AND completed_at < ?",
            (datetime.now().strftime("%Y-%m-%d 00:00:00"), (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d 00:00:00")),
        ),
        (
            "tasks: created_desc 分页",
            "SELECT * FROM tasks ORDER BY created_at DESC LIMIT 20 OFFSET 4980",
            (),
        ),
        (
            "notes: 列表排序（pinned desc + updated_at desc）",
            "SELECT * FROM notes ORDER BY pinned DESC, updated_at DESC LIMIT 20 OFFSET 1980",
            (),
        ),
        (
            "notes: LIKE 标题/内容搜索",
            "SELECT * FROM notes WHERE title LIKE ? COLLATE NOCASE OR content LIKE ? COLLATE NOCASE ORDER BY updated_at DESC LIMIT 20",
            ("%发票%", "%发票%"),
        ),
        (
            "ledger: 当月分类聚合",
            "SELECT kind, category_id, SUM(amount) FROM mod_pl_transaction WHERE occurred_on >= ? AND occurred_on < ? GROUP BY kind, category_id",
            (date.today().replace(day=1).isoformat(), (date.today().replace(day=1) + timedelta(days=32)).replace(day=1).isoformat()),
        ),
        (
            "assistant: 会话更新排序",
            "SELECT * FROM agent_sessions ORDER BY updated_at DESC LIMIT 50",
            (),
        ),
    ]
    out = []
    for name, sql, params in cases:
        rows = conn.execute("EXPLAIN QUERY PLAN " + sql, params).fetchall()
        out.append({
            "case": name,
            "plan": [r["detail"] for r in rows],
        })
    conn.close()
    return out


def frontend_probe() -> dict:
    """真实浏览器首屏（hash 路由首页）：加载时间 + 资源体积。"""
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        return {"skipped": True, "reason": f"Playwright 不可用：{exc}"}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)[:300]))
        page.goto(BASE + "/", wait_until="load", timeout=60000)
        page.wait_for_selector(".page-title, .stat-card", timeout=15000)
        perf = page.evaluate(
            """() => {
              const nav = performance.getEntriesByType('navigation')[0] || {};
              const resources = performance.getEntriesByType('resource') || [];
              const byType = {};
              for (const r of resources) {
                const t = r.initiatorType || 'other';
                byType[t] = byType[t] || {count: 0, bytes: 0};
                byType[t].count += 1;
                byType[t].bytes += (r.encodedBodySize || r.transferSize || 0);
              }
              const top = resources
                .filter(r => ['script','link','css','xmlhttprequest','fetch'].includes(r.initiatorType))
                .sort((a,b)=>(b.encodedBodySize||0)-(a.encodedBodySize||0))
                .slice(0,8)
                .map(r=>({url:new URL(r.name).pathname.replace(/^\\/assets\\//,''), bytes:r.encodedBodySize, ms:Math.round(r.duration)}));
              return {
                domContentLoaded_ms: Math.round(nav.domContentLoadedEventEnd || 0),
                load_ms: Math.round(nav.loadEventEnd || 0),
                resource_total_bytes: resources.reduce((s,r)=>s+(r.encodedBodySize||0),0),
                by_type: byType,
                top_resources: top
              };
            }"""
        )
        # 页面内一次核心 API 的真实浏览器往返（比纯 urllib 更接近用户感受）
        api_ms = page.evaluate(
            """async () => {
              const t0=performance.now();
              const r=await fetch('/api/dashboard/summary',{cache:'no-store'});
              await r.json();
              return Math.round(performance.now()-t0);
            }"""
        )
        browser.close()
    return {
        "skipped": False,
        "page_errors": errors,
        "api_roundtrip_ms": api_ms,
        **perf,
    }


def write_report(counts: dict, api_rows: list[dict], plans: list[dict], web: dict, started_at: str) -> dict:
    slow = sorted(api_rows, key=lambda x: -x["p95_ms"])[:5]
    thresholds = {"health": 20, "list": 120, "aggregate": 250}
    breaches = []
    for row in api_rows:
        if row.get("failed_samples"):
            breaches.append({
                **row,
                "budget_ms": 0,
                "note": f"{row['failed_samples']}/{row['samples']} 次请求返回 {row['status']}（正确性问题，不是延迟问题）",
            })
            continue
        budget = thresholds["health"] if row["path"] == "/api/health" else (
            thresholds["aggregate"] if any(k in row["path"] for k in ("dashboard", "stats", "tags", "search")) else thresholds["list"]
        )
        if row["p95_ms"] > budget:
            breaches.append({**row, "budget_ms": budget})

    md = [
        "# 性能基线报告",
        "",
        f"- 开始时间：{started_at}",
        f"- 数据规模：{json.dumps(counts, ensure_ascii=False)}",
        f"- 采样：每个接口预热 5 次后取 30 次；本机 loopback，非并发压力测试。",
        "",
        "## API 延迟（p95 降序，仅列最慢 8 项）",
        "",
        "| 接口 | p50(ms) | p95(ms) | p99(ms) | max(ms) | 响应体 |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in sorted(api_rows, key=lambda x: -x["p95_ms"])[:8]:
        md.append(
            f"| `{row['path']}` | {row['p50_ms']} | {row['p95_ms']} | {row['p99_ms']} | {row['max_ms']} | {row['avg_body_bytes']:,} B |"
        )
    md += ["", "## 全部结果", "", "| 接口 | p50(ms) | p95(ms) | p99(ms) | 响应体 |", "| --- | ---: | ---: | ---: | ---: |"]
    for row in api_rows:
        md.append(f"| `{row['path']}` | {row['p50_ms']} | {row['p95_ms']} | {row['p99_ms']} | {row['avg_body_bytes']:,} B |")
    md += ["", "## 超预算 / 报错接口", ""]
    if breaches:
        for row in breaches:
            if row.get("note"):
                md.append(f"- `{row['path']}`：{row['note']}")
            else:
                md.append(f"- `{row['path']}`：{row['p95_ms']}ms > 预算 {row['budget_ms']}ms（响应体 {row['avg_body_bytes']:,}B）")
    else:
        md.append("- 无。")

    md += ["", "## SQL 执行计划", ""]
    for row in plans:
        md.append(f"### {row['case']}")
        for detail in row["plan"]:
            md.append(f"- `{detail}`")
        md.append("")

    if web.get("skipped"):
        md += ["## 前端首屏", "", f"- 跳过：{web.get('reason')}", ""]
    else:
        md += [
            "## 前端首屏（真实浏览器）",
            "",
            f"- DOMContentLoaded：{web['domContentLoaded_ms']:,}ms",
            f"- load：{web['load_ms']:,}ms",
            f"- 资源体积：{web['resource_total_bytes']:,}B",
            f"- 页面内 dashboard API 往返：{web['api_roundtrip_ms']}ms",
            f"- page errors：{len(web['page_errors'])}",
            "",
            "| 资源 | 体积 | 请求耗时 |",
            "| --- | ---: | ---: |",
        ]
        for item in web["top_resources"]:
            md.append(f"| `{item['url']}` | {item['bytes']:,} B | {item['ms']}ms |")
        md.append("")

    md += [
        "## 判读口径",
        "",
        "- `p95` 是本机单请求热态延迟，不包含浏览器解析和渲染，不能等同于线上并发容量。",
        "- 执行计划用于区分索引命中与全表扫描；耗时异常但计划已命中索引时，优先看响应体积和 Python 侧聚合。",
        "- 本报告可重复生成；修改索引或查询后比较 `result.json` 即可量化收益。",
    ]
    report = "\n".join(md)
    (OUT / "report.md").write_text(report, encoding="utf-8")
    return {"slowest": slow, "breaches": breaches}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true", help="1/10 数据量，仅验证脚本")
    ap.add_argument("--seed-only", action="store_true")
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--reuse", action="store_true", help="保留已播种库，只重新测")
    args = ap.parse_args()

    scale = 0.1 if args.quick else 1
    started_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    prepare_dirs(clear=not args.reuse)

    if args.reuse and DB.exists():
        conn = sqlite3.connect(DB)
        counts = {
            "tasks": conn.execute("SELECT count(*) FROM tasks").fetchone()[0],
            "notes": conn.execute("SELECT count(*) FROM notes").fetchone()[0],
            "schedules": conn.execute("SELECT count(*) FROM schedules").fetchone()[0],
            "sessions": conn.execute("SELECT count(*) FROM agent_sessions").fetchone()[0],
            "messages": conn.execute("SELECT count(*) FROM chat_messages").fetchone()[0],
            "transactions": conn.execute("SELECT count(*) FROM mod_pl_transaction").fetchone()[0],
        }
        conn.close()
    else:
        counts = seed(scale)
    if args.seed_only:
        return 0

    env = env_for_seed()
    log_fh = LOG.open("w", encoding="utf-8")
    server = subprocess.Popen(
        [BACKEND_PY, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(PORT)],
        cwd=str(BACKEND),
        env=env,
        stdout=log_fh,
        stderr=log_fh,
    )
    try:
        wait_ready()
        api_rows = run_api_benchmarks()
        plans = query_plans()
        web = {"skipped": True, "reason": "--no-browser"} if args.no_browser else frontend_probe()
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
        log_fh.close()

    summary = write_report(counts, api_rows, plans, web, started_at)
    result = {
        "area": "performance",
        "base": BASE,
        "started_at": started_at,
        "finished_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "scale": scale,
        "data": counts,
        "api": api_rows,
        "query_plans": plans,
        "browser": web,
        "summary": summary,
    }
    (OUT / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[done] 报告：{OUT / 'report.md'}")
    print(json.dumps(summary["breaches"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
