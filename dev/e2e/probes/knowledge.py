# -*- coding: utf-8 -*-
"""knowledge 区域深度 E2E 探测（端口 8207，独立库隔离）。

覆盖（QA 视角，超出既有 e2e 的深度交互/边界/异常态）：
- RAG 检索：中文数字混合词「10月」命中、单字「税」跨笔记命中、纯空格点击
  检索不发请求（前端早退）、纯标点查询返回空态、无命中空态、重建索引、
  统计与后端一致、命中结果跳转笔记高亮；
- 长期记忆：新建/编辑/置顶排序/关键词过滤/过滤无命中空态/删除/取消删除/
  重复创建被拒/空白内容保存校验/hit_count 累计（API 驱动 recall + UI 展示），
  以及空白/纯标点 recall 不返回不累计（上轮修复点验证）。

可重复运行：每次启动前重置独立库与财务目录，种子数据经 HTTP 预置；
结果写入 dev/e2e/probe_results/knowledge.json（保留已登记的 bugs 数组）。
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
E2E_DIR = ROOT / "dev" / "e2e"
RESULT_DIR = E2E_DIR / "probe_results" / "knowledge"
DB = E2E_DIR / "probe_results" / "knowledge.db"
FIN_DIR = E2E_DIR / "probe_results" / "knowledge-fin"
DEBUG_DIR = E2E_DIR / "probe_results" / "knowledge-debug"
LOG = E2E_DIR / "probe_results" / "knowledge.server.log"
SHOTS = RESULT_DIR / "shots"
PORT = 8207
BASE = f"http://127.0.0.1:{PORT}"
STARTED_AT = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

for d in (RESULT_DIR, SHOTS):
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

log_fp = open(LOG, "w", encoding="utf-8")
server = subprocess.Popen(
    [str(BACKEND / "venv" / "Scripts" / "python.exe"), "-m", "uvicorn",
     "app.main:app", "--host", "127.0.0.1", "--port", str(PORT)],
    cwd=str(BACKEND), env=env, stdout=log_fp, stderr=log_fp,
)

results = []      # (id, name, passed, detail)
pages_report = []
console_errors = []
page_errors = []
rag_search_reqs = []   # 记录探测期间实际发出的 rag/search 请求

SEED_NOTES = [
    {"title": "10月税务申报纪要",
     "content": "10月增值税申报注意事项：进项发票需在360天内完成认证，10月征期截止到15日。税率调整以最新公告为准。"},
    {"title": "招商免租期方案",
     "content": "本周拜访了三个品牌客户，重点沟通租金阶梯与免租期方案，对方希望争取更长免租期。"},
    {"title": "报表口径备忘",
     "content": "对外报表金额统一用万元，保留两位小数；内部管理台账用元，便于与总账核对。"},
    {"title": "买菜清单",
     "content": "鸡蛋、牛奶、土豆、西红柿，周末家用。"},
]

SEED_MEMORIES = [
    {"content": "用户偏好：报表金额单位统一用万元，保留两位小数。"},
    {"content": "项目背景：某商业管理公司，租金按季度预收。"},
    {"content": "沟通偏好：先结论后细节，控制在三段以内。"},
]


def api_call(method: str, path: str, payload: dict | None = None):
    body = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        f"{BASE}/api{path}", data=body, method=method,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())


def wait_ready(timeout=60):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            urllib.request.urlopen(f"{BASE}/api/dashboard/summary", timeout=2)
            return True
        except Exception:
            time.sleep(1)
    return False


def record(tid, name, fn):
    try:
        fn()
        results.append((tid, name, True, ""))
        print(f"[PASS] {tid} {name}")
    except Exception as e:
        detail = str(e)[:300]
        results.append((tid, name, False, detail))
        print(f"[FAIL] {tid} {name}: {detail}")
        try:
            if page is not None:
                page.screenshot(path=str(SHOTS / f"{tid}.png"))
        except Exception:
            pass


page = None


def goto(route):
    # hash 路由同文档导航不会重载文档；紧接 page.reload() 存在竞态：hash 尚未提交
    # 就 reload 会重新加载旧地址（上轮 TC-KNOW-009~018 全挂的证据：失败截图停在
    # #/notes 笔记页、server.log 在笔记接口后无任何文档请求）。在 hash 前加一次性
    # 查询参数强制真实导航，确保组件全新挂载且地址已切到目标路由。
    page.goto(f"{BASE}/?e2e={int(time.time() * 1000)}#{route}",
              wait_until="networkidle", timeout=30000)
    assert page.url.endswith(f"#{route}"), f"未导航到 {route}: {page.url}"
    page.locator(".rag-bar").first.wait_for(state="visible", timeout=10000)
    page.wait_for_timeout(300)


def rag_input():
    return page.locator(".rag-bar input").first


def rag_search_btn():
    return page.locator(".rag-bar button", has_text="检索").first


def do_rag_search(q: str, wait_ms: int = 1800):
    rag_input().fill(q)
    rag_search_btn().click()
    page.wait_for_timeout(wait_ms)


def open_memory_tab():
    page.locator(".el-tabs__item", has_text="长期记忆").first.click()
    page.wait_for_timeout(200)
    # 断言页签真的切过去了，避免后续 .mem-bar / tbody 因面板隐藏而 30s 超时，
    # 把「页签未切换」与「数据未渲染」两类问题混为一谈。
    assert page.locator(".el-tabs__item.is-active", has_text="长期记忆").count() == 1, \
        "「长期记忆」页签点击后未激活"
    page.locator(".mem-bar").first.wait_for(state="visible", timeout=5000)


def mem_rows():
    # el-table 固定列会在右侧叠加渲染副本（只含固定列单元格）；
    # 按「内容」文本定位行只会命中主表行，操作按钮也在主表行内。
    return page.locator("tbody tr")


def mem_dialog():
    return page.locator(".el-dialog:visible")


try:
    if not wait_ready():
        print("FATAL: server not ready")
        sys.exit(2)

    # ---------- 种子数据（HTTP 预置，控制检索语料） ----------
    for n in SEED_NOTES:
        api_call("POST", "/notes", n)
    for m in SEED_MEMORIES:
        api_call("POST", "/knowledge/memories", m)
    expected_notes = api_call("GET", "/notes?page=1&page_size=1")["total"]
    print(f"seed done: notes_total={expected_notes}")

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: page_errors.append(str(e)))
        page.on("request", lambda r: rag_search_reqs.append(r.url) if "rag/search" in r.url else None)

        # ---------- 页面侦察 ----------
        def tc_recon():
            c_err, p_err, f_req = [], [], []
            h1 = lambda m, e=c_err: e.append(m.text) if m.type == "error" else None
            h2 = lambda e, l=p_err: l.append(str(e))
            h3 = lambda r, l=f_req: l.append(f"{r.method} {r.url}")
            page.on("console", h1)
            page.on("pageerror", h2)
            page.on("requestfailed", h3)
            try:
                resp = page.goto(f"{BASE}/#/knowledge", wait_until="networkidle", timeout=30000)
                page.wait_for_timeout(1000)
                page.screenshot(path=str(SHOTS / "knowledge.png"))
                pages_report.append({
                    "route": "/knowledge", "name": "knowledge",
                    "status": resp.status if resp else "?",
                    "console_errors": c_err, "page_errors": p_err,
                    "failed_reqs": [f for f in f_req if "favicon" not in f],
                })
                assert not c_err and not p_err, f"console={c_err[:2]} page={p_err[:2]}"
            finally:
                page.remove_listener("console", h1)
                page.remove_listener("pageerror", h2)
                page.remove_listener("requestfailed", h3)
        record("TC-KNOW-RECON-001", "知识页加载无控制台/页面错误", tc_recon)

        # ---------- RAG ----------
        def tc_rag_mixed_token():
            do_rag_search("10月")
            hits = page.locator(".hit")
            assert hits.count() >= 1, "「10月」混合词查询无命中"
            first_title = page.locator(".hit-title").first.inner_text()
            assert "10月税务申报纪要" in first_title, f"首个命中不是探测笔记: {first_title}"
            assert "score" in hits.first.inner_text(), "命中卡片缺少 score 展示"
        record("TC-KNOW-001", "RAG 中文数字混合词「10月」命中", tc_rag_mixed_token)

        def tc_rag_single_char():
            do_rag_search("税")
            titles = [t.inner_text() for t in page.locator(".hit-title").all()]
            assert "10月税务申报纪要" in titles, f"单字「税」未命中探测笔记: {titles}"
            assert len(titles) >= 2, f"单字查询应跨多篇命中: {titles}"
        record("TC-KNOW-002", "RAG 单字「税」跨笔记命中", tc_rag_single_char)

        def tc_rag_whitespace_no_request():
            rag_search_reqs.clear()
            before = page.locator(".hit").count()
            assert before > 0, "前置状态应有命中结果"
            rag_input().fill("   ")
            rag_search_btn().click()
            page.wait_for_timeout(900)
            assert rag_search_reqs == [], f"纯空白查询仍发出请求: {rag_search_reqs}"
            assert page.locator(".hit").count() == before, "纯空白查询改变了结果列表"
            assert page.locator(".el-message--error").count() == 0, "纯空白查询弹出错误提示"
        record("TC-KNOW-003", "RAG 纯空白查询不发请求不改状态", tc_rag_whitespace_no_request)

        def tc_rag_punctuation_empty():
            do_rag_search("。。。", wait_ms=1500)
            assert page.locator(".hit").count() == 0, "纯标点查询竟有命中"
            empty = page.locator(".el-empty")
            assert empty.count() == 1, "纯标点查询未展示空态"
            assert "没有命中" in empty.inner_text(), f"空态文案异常: {empty.inner_text()[:60]}"
        record("TC-KNOW-004", "RAG 纯标点查询返回空态", tc_rag_punctuation_empty)

        def tc_rag_no_hit_empty():
            do_rag_search("量子引力波探测器xyz", wait_ms=1500)
            assert page.locator(".hit").count() == 0, "无关键词竟有命中"
            assert page.locator(".el-empty").count() == 1, "无命中未展示空态"
        record("TC-KNOW-005", "RAG 无命中空态展示", tc_rag_no_hit_empty)

        def tc_rag_rebuild():
            page.locator("button", has_text="重建索引").first.click()
            page.wait_for_timeout(1500)
            assert page.locator(".el-message", has_text="索引已重建").count() >= 1, "重建索引无成功提示"
            stats = page.locator(".stats").inner_text()
            assert "篇笔记" in stats and "块" in stats, f"统计文案异常: {stats[:60]}"
        record("TC-KNOW-006", "重建索引成功并刷新统计", tc_rag_rebuild)

        def tc_rag_stats_consistent():
            stats = page.locator(".stats").inner_text()
            m = re.search(r"索引：\s*(\d+)\s*篇笔记\s*/\s*(\d+)\s*块\s*/\s*(\d+)\s*词", stats)
            assert m, f"统计格式异常: {stats[:80]}"
            assert int(m.group(1)) == expected_notes, \
                f"索引笔记数 {m.group(1)} 与后端 {expected_notes} 不一致"
            assert int(m.group(2)) >= int(m.group(1)), "块数不应少于笔记数"
        record("TC-KNOW-007", "索引统计与后端数据一致", tc_rag_stats_consistent)

        def tc_rag_hit_jump():
            do_rag_search("10月", wait_ms=1500)
            page.locator(".hit-title").first.click()
            page.wait_for_timeout(1200)
            assert "/notes?highlight=" in page.url, f"未跳转到笔记高亮: {page.url}"
            body = page.content()
            assert "10月税务申报纪要" in body, "跳转后未看到被命中的笔记"
        record("TC-KNOW-008", "命中结果跳转笔记并定位", tc_rag_hit_jump)

        # ---------- 长期记忆 ----------
        def tc_mem_create():
            goto("/knowledge")  # 上一步命中跳转后回到知识页
            open_memory_tab()
            page.locator(".mem-bar button", has_text="新建记忆").first.click()
            page.wait_for_timeout(500)
            mem_dialog().locator("textarea").first.fill("E2E探测记忆：知识库检索与记忆联测条目甲")
            mem_dialog().locator("button", has_text="保存").first.click()
            page.wait_for_timeout(1000)
            assert mem_rows().filter(has_text="E2E探测记忆").count() == 1, "新建记忆未出现在列表"
        record("TC-KNOW-009", "新建记忆", tc_mem_create)

        def tc_mem_edit():
            row = mem_rows().filter(has_text="E2E探测记忆").first
            row.locator("button", has_text="编辑").click()
            page.wait_for_timeout(500)
            ta = mem_dialog().locator("textarea").first
            assert "联测条目甲" in ta.input_value(), "编辑弹窗未回填原内容"
            ta.fill("E2E探测记忆：编辑后的内容乙")
            mem_dialog().locator("button", has_text="保存").first.click()
            page.wait_for_timeout(1000)
            assert mem_rows().filter(has_text="编辑后的内容乙").count() == 1, "编辑未生效"
            assert mem_rows().filter(has_text="联测条目甲").count() == 0, "旧内容仍残留"
        record("TC-KNOW-010", "编辑记忆", tc_mem_edit)

        def tc_mem_pin():
            row = mem_rows().filter(has_text="沟通偏好：先结论后细节").first
            row.locator("button", has_text="★").click()
            page.wait_for_timeout(1200)
            first_row = mem_rows().first
            assert "沟通偏好" in first_row.inner_text(), "置顶后未排到列表首位"
            star = first_row.locator("button", has_text="★").first
            assert "warning" in (star.get_attribute("class") or ""), "置顶星标未变为高亮样式"
        record("TC-KNOW-011", "置顶记忆排首位且星标高亮", tc_mem_pin)

        def tc_mem_filter():
            kw = page.locator(".mem-bar input").first
            kw.fill("万元")
            page.wait_for_timeout(1400)
            rows = mem_rows()
            assert rows.count() == 1, f"关键词过滤后应只剩 1 条，实际 {rows.count()}"
            assert "万元" in rows.first.inner_text()
            kw.fill("")
            page.wait_for_timeout(1400)
            assert mem_rows().count() >= 4, "清空关键词后未恢复完整列表"
        record("TC-KNOW-012", "记忆关键词过滤与恢复", tc_mem_filter)

        def tc_mem_filter_empty():
            kw = page.locator(".mem-bar input").first
            kw.fill("绝不存在的关键词xyz")
            page.wait_for_timeout(1400)
            assert mem_rows().count() == 0, "无命中关键词仍有数据行"
            assert page.locator("text=暂无记忆").count() >= 1, "过滤无命中未展示空态文案"
            kw.fill("")
            page.wait_for_timeout(1200)
            assert mem_rows().count() >= 4, "恢复列表失败"
        record("TC-KNOW-013", "记忆过滤无命中空态", tc_mem_filter_empty)

        def tc_mem_delete():
            row = mem_rows().filter(has_text="E2E探测记忆").first
            row.locator("button", has_text="删除").click()
            page.locator(".el-message-box button", has_text="确定").click()
            page.wait_for_timeout(1200)
            assert mem_rows().filter(has_text="E2E探测记忆").count() == 0, "删除后记忆仍在列表"
        record("TC-KNOW-014", "删除记忆", tc_mem_delete)

        def tc_mem_duplicate_reject():
            before = mem_rows().count()
            page.locator(".mem-bar button", has_text="新建记忆").first.click()
            page.wait_for_timeout(500)
            mem_dialog().locator("textarea").first.fill("用户偏好：报表金额单位统一用万元，保留两位小数。")
            mem_dialog().locator("button", has_text="保存").first.click()
            page.wait_for_timeout(1200)
            assert page.locator(".el-message", has_text="已存在基本相同").count() >= 1, "重复记忆未给出拒绝提示"
            assert mem_dialog().count() == 1, "被拒后弹窗应保持打开"
            assert mem_rows().count() == before, "被拒后列表行数发生变化"
            mem_dialog().locator("button", has_text="取消").first.click()
            page.wait_for_timeout(400)
        record("TC-KNOW-015", "重复记忆创建被拒且弹窗保持", tc_mem_duplicate_reject)

        def tc_mem_hit_count():
            row = mem_rows().filter(has_text="报表金额单位统一用万元").first
            before = int(row.locator("td").nth(3).inner_text())
            for _ in range(2):
                r = api_call("POST", "/knowledge/memories/recall", {"query": "万元", "top_k": 3})
                assert r["items"], "recall 应命中万元记忆"
            # 上轮修复点：空白/纯标点查询不返回、不累计 hit_count
            assert api_call("POST", "/knowledge/memories/recall", {"query": "   ", "top_k": 3})["items"] == []
            assert api_call("POST", "/knowledge/memories/recall", {"query": "。", "top_k": 3})["items"] == []
            # 整页刷新后回记忆页签读 UI 展示值
            page.reload(wait_until="networkidle")
            page.wait_for_timeout(900)
            open_memory_tab()
            row2 = mem_rows().filter(has_text="报表金额单位统一用万元").first
            after = int(row2.locator("td").nth(3).inner_text())
            assert after == before + 2, f"hit_count 未按预期累计: {before} -> {after}"
        record("TC-KNOW-016", "recall 命中累计 hit_count 且空白查询不累计", tc_mem_hit_count)

        def tc_mem_blank_guard():
            page.locator(".mem-bar button", has_text="新建记忆").first.click()
            page.wait_for_timeout(500)
            mem_dialog().locator("textarea").first.fill("   ")
            mem_dialog().locator("button", has_text="保存").first.click()
            page.wait_for_timeout(800)
            assert page.locator(".el-message", has_text="内容不能为空").count() >= 1, "空白内容无校验提示"
            assert mem_dialog().count() == 1, "空白内容仍被提交（弹窗应保持）"
            mem_dialog().locator("button", has_text="取消").first.click()
            page.wait_for_timeout(400)
        record("TC-KNOW-017", "空白记忆内容保存校验", tc_mem_blank_guard)

        def tc_mem_cancel_delete():
            before = mem_rows().count()
            row = mem_rows().filter(has_text="项目背景：某商业管理公司").first
            row.locator("button", has_text="删除").click()
            page.locator(".el-message-box button", has_text="取消").click()
            page.wait_for_timeout(900)
            assert mem_rows().count() == before, "取消删除后行数变化"
            assert mem_rows().filter(has_text="项目背景").count() == 1, "取消删除后记忆丢失"
        record("TC-KNOW-018", "取消删除不删除记忆", tc_mem_cancel_delete)

        browser.close()

    # ---------- 结果 JSON（保留已登记 bugs，便于修复后重跑） ----------
    out = RESULT_DIR / "knowledge.json"
    prev_bugs = []
    if out.exists():
        try:
            prev_bugs = json.loads(out.read_text(encoding="utf-8")).get("bugs", [])
        except Exception:
            prev_bugs = []
    payload = {
        "area": "knowledge", "base": BASE,
        "started_at": STARTED_AT,
        "finished_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "pages": pages_report,
        "tests": [
            {"id": t[0], "name": t[1], "passed": t[2], "detail": t[3]} for t in results
        ],
        "console_errors": sorted(set(console_errors)),
        "page_errors": sorted(set(page_errors)),
        "bugs": prev_bugs,
    }
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print("=" * 70)
    passed = sum(1 for r in results if r[2])
    print(f"total={len(results)} passed={passed} failed={len(results) - passed}")
    print(f"console_errors={len(set(console_errors))} page_errors={len(set(page_errors))}")
    for e in sorted(set(console_errors))[:10]:
        print(f"  !! console: {e[:200]}")
    for e in sorted(set(page_errors))[:10]:
        print(f"  !! pageerror: {e[:200]}")
    print(f"results_json={out}")
finally:
    server.terminate()
    try:
        server.wait(timeout=10)
    except Exception:
        server.kill()
    log_fp.close()
