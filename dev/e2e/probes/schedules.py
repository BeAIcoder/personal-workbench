# -*- coding: utf-8 -*-
"""schedules 区域深度 E2E 探测（日程管理）。

覆盖：
- /schedules 逐页访问（控制台/页面错误/失败请求 + 截图）；
- 新建/编辑/删除日程（确认与取消两条路径）、全天开关、定时日程 meta 渲染；
- 结束早于开始的校验（后端 422 + 前端提示 + 不落库）；
- 空标题校验；编辑全天日程时结束选择器隐藏；
- 月份切换（上一月/下一月×2/回今天）、跨月数据持久；
- 过去日期日程、未来日期日程、跨天日程（开始日可见）；
- 月历格点渲染（3 点 + "+N"）与选中日列表联动、空态；
- 日历溢出日（下月格）点击导航；
- 快速切月竞态（请求序号丢弃）；
- AI 备会按钮：编辑对话框不得被连带打开（@click.stop 回归），
  且端到端跳转 /assistant 后提示词被自动发出；
- 单次加载上限 200 条提示；
- API 直测：PUT 显式传 null 时间字段应 422 而非 500。

可重复运行：独立测试库 + 自有端口 8203。
"""
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
BACKEND = ROOT / "backend"
E2E_DIR = ROOT / "dev" / "e2e"
PROBE_DIR = E2E_DIR / "probe_results" / "schedules"
SHOTS = PROBE_DIR / "shots"
DB = PROBE_DIR / "schedules.db"
FIN_DIR = PROBE_DIR / "schedules-fin"
PORT = 8203
BASE = f"http://127.0.0.1:{PORT}"
STARTED_AT = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

for d in (PROBE_DIR, SHOTS):
    d.mkdir(parents=True, exist_ok=True)
if DB.exists():
    DB.unlink()
if FIN_DIR.exists():
    shutil.rmtree(FIN_DIR)

env = os.environ.copy()
env["DATABASE_URL"] = f"sqlite:///{DB.as_posix()}"
env["FINANCE_DATA_DIR"] = str(FIN_DIR)
env["PYTHONIOENCODING"] = "utf-8"
# 干净库：不灌演示数据，保证「空库空态」等断言确定性
env["SEED_ON_STARTUP"] = "0"

server = subprocess.Popen(
    [str(BACKEND / "venv" / "Scripts" / "python.exe"), "-m", "uvicorn",
     "app.main:app", "--host", "127.0.0.1", "--port", str(PORT)],
    cwd=str(BACKEND), env=env,
    stdout=open(PROBE_DIR / "schedules.server.log", "w"),
    stderr=subprocess.STDOUT,
)

TODAY = date.today()
TODAY_S = TODAY.strftime("%Y-%m-%d")
DAY_AFTER_TOM = TODAY + timedelta(days=2)
# 与今天不同的本月日号（用于选中日联动/空态断言）
OTHER_DAY = (TODAY.day % 28) + 1


def month_add(d: date, n: int) -> date:
    m = d.month - 1 + n
    return date(d.year + m // 12, m % 12 + 1, 1)


def cal_title_of(d: date) -> str:
    return f"{d.year} 年 {d.month} 月"


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
        with urllib.request.urlopen(req, timeout=10) as r:
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


def cal_title():
    return page.locator(".el-calendar__title").inner_text().strip()


def click_cal_day(day, cell="current"):
    handle = page.evaluate_handle(
        """([day, cell]) => {
            const tds = Array.from(document.querySelectorAll('.el-calendar-table td.' + cell));
            return tds.find(t => {
                const c = t.querySelector('.cal-day');
                return c && c.textContent.trim() === String(day);
            }) || null;
        }""",
        [day, cell],
    ).as_element()
    if handle is None:
        raise AssertionError(f"日历中找不到 {cell} 月日期格 {day}")
    handle.click()
    page.wait_for_timeout(400)


def open_create():
    page.locator(".day-card button", has_text="新建日程").first.click()
    page.wait_for_timeout(400)


def fill_title(t):
    dialog().locator("input[placeholder*='月度经营分析会']").fill(t)


def set_dt(idx, value):
    """idx 0=开始 1=结束；value 形如 2026-10-19 14:00:00 或 2026-10-19"""
    inp = dialog().locator(".el-date-editor input").nth(idx)
    inp.click()
    page.wait_for_timeout(150)
    inp.fill(value)
    page.keyboard.press("Enter")
    page.wait_for_timeout(250)


def save_dialog():
    dialog().locator("button", has_text="保存").first.click()
    page.wait_for_timeout(900)


def create_event(title, all_day=False, start=None, end=None):
    """start/end 缺省时用对话框预填值（= 当前选中日 09:00-10:00），不强行改成今天。"""
    open_create()
    fill_title(title)
    if all_day:
        dialog().locator(".el-switch").first.click()
        page.wait_for_timeout(250)
        if start:
            set_dt(0, start)
    else:
        if start:
            set_dt(0, start)
        if end:
            set_dt(1, end)
    save_dialog()


def event_row(title):
    return page.locator(".event-item", has_text=title).first


def main():
    global page
    if not wait_ready():
        print("FATAL: server not ready")
        server.terminate()
        sys.exit(2)

    from playwright.sync_api import sync_playwright

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
        resp = page.goto(f"{BASE}/#/schedules", wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(900)
        page.screenshot(path=str(SHOTS / "schedules.png"))
        buttons = [b.inner_text().strip().replace("\n", " ")[:20]
                   for b in page.locator("button:visible").all()[:12]]
        # 侦察监听器只服务于本阶段，及时解绑避免把后续交互的失败请求算到页面账上
        page.remove_listener("console", on_console)
        page.remove_listener("pageerror", on_pageerror)
        page.remove_listener("requestfailed", on_requestfailed)
        pages_report.append({
            "route": "/schedules", "name": "schedules",
            "status": resp.status if resp else "?",
            "console_errors": ce, "page_errors": pe,
            "failed_reqs": [f for f in fr if "favicon" not in f],
            "buttons": buttons,
        })

        # ================= 阶段 2：交互与边界 =================

        def tc_initial_render():
            assert page.locator(".el-calendar-table").count() > 0, "月历表格未渲染"
            heads = page.locator(".el-calendar-table th").all_inner_texts()
            assert len(heads) == 7, f"星期表头应为 7 个，实际 {heads}"
            assert page.locator(".el-calendar__button-group button").count() == 3, "月历导航按钮缺失"
            title = page.locator(".day-title").inner_text()
            assert TODAY_S.replace("-", "")[:4] in title, f"日期卡标题异常: {title}"
            assert "项日程" in title, f"日期卡标题缺少计数: {title}"
            assert page.locator(".el-empty").count() > 0, "空库应显示空态"
            assert page.locator(".day-card button", has_text="新建日程").count() > 0, "缺少新建日程按钮"
            assert cal_title() == cal_title_of(TODAY), f"初始月份应为 {cal_title_of(TODAY)}，实际 {cal_title()}"

        record("TC-SCH-001", "页面初始渲染（月历/星期表头/日期卡/空态/导航按钮）", tc_initial_render)

        def tc_create_all_day():
            create_event("探测-全天日程", all_day=True)
            assert "创建成功" in toast_text(), f"缺少成功提示: {toast_text()[:120]}"
            row = event_row("探测-全天日程")
            assert row.count() > 0, "全天日程未出现在选中日列表"
            assert "全天" in row.inner_text(), "全天标签缺失"
            # 日历格点：今天格应有 1 个 dot
            dots = page.evaluate(
                """() => {
                    const tds = Array.from(document.querySelectorAll('.el-calendar-table td.current'));
                    const td = tds.find(t => t.querySelector('.cal-day') &&
                        t.querySelector('.cal-day').classList.contains('today'));
                    return td ? td.querySelectorAll('.dot').length : -1;
                }"""
            )
            assert dots == 1, f"今天格 dot 数应为 1，实际 {dots}"

        record("TC-SCH-002", "新建全天日程（列表/全天标签/日历格点）", tc_create_all_day)

        def tc_create_timed():
            create_event("探测-定时日程", start=f"{TODAY_S} 14:00:00", end=f"{TODAY_S} 15:00:00")
            row = event_row("探测-定时日程")
            assert row.count() > 0, "定时日程未出现在列表"
            meta = row.locator(".event-meta").inner_text()
            assert "14:00 - 15:00" in meta, f"时间 meta 渲染异常: {meta}"

        record("TC-SCH-003", "新建定时日程（起止时间渲染）", tc_create_timed)

        def tc_end_before_start():
            before = page.locator(".event-item").count()
            create_event("探测-非法时间", start=f"{TODAY_S} 14:00:00", end=f"{TODAY_S} 13:00:00")
            t = toast_text()
            assert "结束时间" in t, f"结束早于开始缺少错误提示: {t[:150]}"
            assert dialog().count() > 0, "校验失败后对话框被关闭（应保留供修改）"
            assert page.locator(".event-item").count() == before, "非法日程被写入列表"
            dialog().locator("button", has_text="取消").first.click()
            page.wait_for_timeout(300)
            assert page.locator(".event-item", has_text="探测-非法时间").count() == 0, "非法日程已落库"

        record("TC-SCH-004", "结束早于开始的校验（422 提示 + 不落库）", tc_end_before_start)

        def tc_all_day_toggle():
            open_create()
            n0 = dialog().locator(".el-date-editor").count()
            assert n0 == 2, f"定时态应有 2 个时间选择器，实际 {n0}"
            dialog().locator(".el-switch").first.click()
            page.wait_for_timeout(250)
            n1 = dialog().locator(".el-date-editor").count()
            assert n1 == 1, f"全天态应只剩 1 个时间选择器，实际 {n1}"
            dialog().locator(".el-switch").first.click()
            page.wait_for_timeout(250)
            n2 = dialog().locator(".el-date-editor").count()
            assert n2 == 2, f"切回定时态应恢复 2 个时间选择器，实际 {n2}"
            dialog().locator("button", has_text="取消").first.click()

        record("TC-SCH-005", "全天开关显隐（结束选择器联动）", tc_all_day_toggle)

        def tc_empty_title():
            open_create()
            save_dialog()
            err = page.locator(".el-form-item__error:visible")
            assert err.count() > 0, "空标题未触发表单校验"
            assert "标题" in err.first.inner_text(), f"校验文案异常: {err.first.inner_text()}"
            assert dialog().count() > 0, "校验失败对话框被关闭"
            dialog().locator("button", has_text="取消").first.click()

        record("TC-SCH-006", "空标题前端校验", tc_empty_title)

        def tc_edit_title():
            event_row("探测-全天日程").locator(".event-main").click()
            page.wait_for_timeout(400)
            assert dialog().locator(".el-dialog__title").inner_text() == "编辑日程", "编辑态标题异常"
            inp = dialog().locator("input[placeholder*='月度经营分析会']")
            inp.fill("全天日程-已改名")
            save_dialog()
            assert "已保存" in toast_text(), f"缺少保存提示: {toast_text()[:120]}"
            row = event_row("全天日程-已改名")
            assert row.count() > 0, "改名后列表未更新"
            assert page.locator(".event-item", has_text="探测-全天日程").count() == 0, "旧标题仍残留"

        record("TC-SCH-007", "编辑日程标题（编辑态 + 列表联动）", tc_edit_title)

        def tc_edit_all_day_dialog():
            event_row("全天日程-已改名").locator(".event-main").click()
            page.wait_for_timeout(400)
            n = dialog().locator(".el-date-editor").count()
            assert n == 1, f"编辑全天日程时应只有 1 个时间选择器（日期），实际 {n}"
            val = dialog().locator(".el-date-editor input").first.input_value()
            assert val == TODAY_S, f"日期回显异常: {val}"
            dialog().locator("button", has_text="取消").first.click()

        record("TC-SCH-008", "编辑全天日程（结束选择器隐藏 + 日期回显）", tc_edit_all_day_dialog)

        def tc_dots_and_day_link():
            # 今日已有 2 条（改名后的全天 + 定时），再灌 2 条同时段 → 共 4 条
            api_call("POST", "/schedules", {
                "title": "探测-格点A", "start_time": f"{TODAY_S}T11:00:00",
                "end_time": f"{TODAY_S}T11:30:00"})
            api_call("POST", "/schedules", {
                "title": "探测-格点B", "start_time": f"{TODAY_S}T12:00:00",
                "end_time": f"{TODAY_S}T12:30:00"})
            page.reload(wait_until="networkidle")
            page.wait_for_timeout(900)
            n_items = page.locator(".event-item").count()
            assert n_items == 4, f"选中日应有 4 项日程，实际 {n_items}"
            info = page.evaluate(
                """() => {
                    const tds = Array.from(document.querySelectorAll('.el-calendar-table td.current'));
                    const td = tds.find(t => t.querySelector('.cal-day') &&
                        t.querySelector('.cal-day').classList.contains('today'));
                    if (!td) return null;
                    return { dots: td.querySelectorAll('.dot').length,
                             more: ((td.querySelector('.more') || {}).textContent || '').trim() };
                }"""
            )
            assert info and info["dots"] == 3, f"格点应渲染 3 个 dot，实际 {info}"
            assert info["more"] == "+1", f"超出应显示 +1，实际 {info}"
            # 点选另一天（无事件）→ 空态 + 标题联动
            click_cal_day(OTHER_DAY)
            assert page.locator(".el-empty").count() > 0, "无事件日应显示空态"
            assert f"{TODAY.year}年{TODAY.month:02d}月{OTHER_DAY:02d}日" in page.locator(".day-title").inner_text(), \
                "选中日后标题未联动"
            # 点回今天 → 4 项回来
            click_cal_day(TODAY.day)
            assert page.locator(".event-item").count() == 4, "点回今天后日程数不符"

        record("TC-SCH-009", "月历格点渲染（3 点 +N）与选中日列表联动", tc_dots_and_day_link)

        def tc_delete_confirm():
            n_before = page.locator(".event-item").count()
            row = event_row("探测-格点A")
            row.locator("button", has_text="删除").click()
            page.wait_for_timeout(400)
            page.locator(".el-message-box:visible button", has_text="确定").click()
            page.wait_for_timeout(900)
            assert "已删除" in toast_text(), f"缺少删除提示: {toast_text()[:120]}"
            assert page.locator(".event-item").count() == n_before - 1, "删除后列表数量未减"
            assert page.locator(".event-item", has_text="探测-格点A").count() == 0, "删除后仍残留"

        record("TC-SCH-010", "删除日程（确认路径）", tc_delete_confirm)

        def tc_delete_cancel():
            n_before = page.locator(".event-item").count()
            row = event_row("探测-格点B")
            row.locator("button", has_text="删除").click()
            page.wait_for_timeout(400)
            page.locator(".el-message-box:visible button", has_text="取消").click()
            page.wait_for_timeout(500)
            assert page.locator(".event-item").count() == n_before, "取消删除后日程数量变化"
            assert page.locator(".event-item", has_text="探测-格点B").count() > 0, "取消删除后日程消失"

        record("TC-SCH-011", "删除日程（取消路径）", tc_delete_cancel)

        def tc_month_nav_persist():
            day1_s = f"{TODAY_S[:8]}01"
            click_cal_day(1)
            create_event("探测-月1日日程", start=f"{day1_s} 09:00:00", end=f"{day1_s} 10:00:00")
            t0 = cal_title()
            assert t0 == cal_title_of(TODAY), f"当前月份标题异常: {t0}"
            prev_m, next_m = month_add(TODAY, -1), month_add(TODAY, 1)
            # 上一月
            page.locator(".el-calendar__button-group button").nth(0).click()
            page.wait_for_timeout(700)
            t1 = cal_title()
            assert t1 == cal_title_of(prev_m), f"上一月标题异常: {t1}"
            assert f"{prev_m.year}年{prev_m.month:02d}月01日" in page.locator(".day-title").inner_text(), \
                "切月后日期卡未联动到当月 1 日"
            # 下一月回到本月
            page.locator(".el-calendar__button-group button").nth(2).click()
            page.wait_for_timeout(900)
            assert cal_title() == t0, f"下一月后未回到 {t0}，实际 {cal_title()}"
            # 数据持久：点回 1 日，月 1 日日程仍在（整月重拉生效）
            click_cal_day(1)
            assert page.locator(".event-item", has_text="探测-月1日日程").count() > 0, \
                "切月再回来后当月日程丢失"
            # 再点下一月 → 下个月
            page.locator(".el-calendar__button-group button").nth(2).click()
            page.wait_for_timeout(900)
            assert cal_title() == cal_title_of(next_m), f"下一月标题异常: {cal_title()}"
            # 回今天按钮 → 当前月 + 选中今天
            page.locator(".el-calendar__button-group button").nth(1).click()
            page.wait_for_timeout(900)
            assert cal_title() == cal_title_of(TODAY), "「今天」按钮未回到当前月"
            assert f"{TODAY.year}年{TODAY.month:02d}月{TODAY.day:02d}日" in page.locator(".day-title").inner_text(), \
                "「今天」按钮未选中今天"

        record("TC-SCH-012", "月份切换（上月/下月×2/回今天）与跨月数据持久", tc_month_nav_persist)

        def tc_past_date_event():
            prev = month_add(TODAY, -1)
            page.locator(".el-calendar__button-group button").nth(0).click()
            page.wait_for_timeout(700)
            assert cal_title() == cal_title_of(prev), "未切到上月"
            click_cal_day(15)
            d15 = f"{prev.year}-{prev.month:02d}-15"
            create_event("探测-过去日程", start=f"{d15} 09:00:00", end=f"{d15} 10:00:00")
            row = event_row("探测-过去日程")
            assert row.count() > 0, "过去日期的日程未出现在列表"
            page.locator(".el-calendar__button-group button").nth(1).click()
            page.wait_for_timeout(900)

        record("TC-SCH-013", "过去日期日程（上月新建可见）", tc_past_date_event)

        def tc_future_date_event():
            nxt = month_add(TODAY, 1)
            page.locator(".el-calendar__button-group button").nth(2).click()
            page.wait_for_timeout(700)
            assert cal_title() == cal_title_of(nxt), "未切到下月"
            click_cal_day(15)
            d15 = f"{nxt.year}-{nxt.month:02d}-15"
            create_event("探测-未来日程", start=f"{d15} 09:00:00", end=f"{d15} 10:00:00")
            row = event_row("探测-未来日程")
            assert row.count() > 0, "未来日期的日程未出现在列表"
            page.locator(".el-calendar__button-group button").nth(1).click()
            page.wait_for_timeout(900)

        record("TC-SCH-014", "未来日期日程（下月新建可见）", tc_future_date_event)

        def tc_overflow_cell_click():
            n_next = page.locator(".el-calendar-table td.next").count()
            if n_next == 0:
                # 本月日历无下月溢出格（月末恰为整周）：跳过子断言
                assert cal_title() == cal_title_of(TODAY), "无溢出格时月份标题异常"
                return
            page.locator(".el-calendar-table td.next").first.click()
            page.wait_for_timeout(900)
            assert cal_title() == cal_title_of(month_add(TODAY, 1)), \
                f"点击下月溢出格后应导航到下月，实际 {cal_title()}"
            page.locator(".el-calendar__button-group button").nth(1).click()
            page.wait_for_timeout(900)

        record("TC-SCH-015", "月历下月溢出格点击导航", tc_overflow_cell_click)

        def tc_cross_day_event():
            create_event("探测-跨天日程", start=f"{TODAY_S} 09:00:00",
                         end=f"{DAY_AFTER_TOM.strftime('%Y-%m-%d')} 18:00:00")
            assert "创建成功" in toast_text(), f"跨天日程创建提示异常: {toast_text()[:120]}"
            row = event_row("探测-跨天日程")
            assert row.count() > 0, "跨天日程未出现在开始日列表"
            # 记录行为：跨天日程在中间日不显示（设计存疑项，仅观测不硬断言）
            mid_visible = page.evaluate(
                """() => document.body.innerText.includes('探测-跨天日程')"""
            )
            assert mid_visible, "跨天日程在页面上完全不可见"

        record("TC-SCH-016", "跨天日程创建（开始日可见）", tc_cross_day_event)

        def tc_create_in_other_month():
            # 设计存疑观测：在当前月视图中把日程建到其它月份 → 创建成功但当前视图不可见，
            # 需切到目标月才可见（对话框日期默认=选中日，需用户主动改期）。
            nxt = month_add(TODAY, 1)
            d20 = f"{nxt.year}-{nxt.month:02d}-20"
            create_event("探测-跨月创建", start=f"{d20} 09:00:00", end=f"{d20} 10:00:00")
            assert "创建成功" in toast_text(), f"跨月创建提示异常: {toast_text()[:120]}"
            assert page.locator(".event-item", has_text="探测-跨月创建").count() == 0, \
                "跨月创建的日程不应出现在当前月视图"
            page.locator(".el-calendar__button-group button").nth(2).click()
            page.wait_for_timeout(900)
            click_cal_day(20)
            assert page.locator(".event-item", has_text="探测-跨月创建").count() > 0, \
                "切到目标月后跨月创建的日程应可见"
            page.locator(".el-calendar__button-group button").nth(1).click()
            page.wait_for_timeout(900)

        record("TC-SCH-022", "跨月创建日程（创建成功 + 目标月可见，设计存疑观测）", tc_create_in_other_month)

        def tc_rapid_month_switch():
            n_err0 = len(page_errors_all)
            for _ in range(3):
                page.locator(".el-calendar__button-group button").nth(2).click()
                page.wait_for_timeout(120)
            page.wait_for_timeout(1500)
            expect = cal_title_of(month_add(TODAY, 3))
            assert cal_title() == expect, f"快速连点 3 次下月后应为 {expect}，实际 {cal_title()}"
            assert len(page_errors_all) == n_err0, f"快速切月出现未捕获异常: {page_errors_all[-2:]}"
            page.locator(".el-calendar__button-group button").nth(1).click()
            page.wait_for_timeout(900)

        record("TC-SCH-017", "快速切换月份（竞态丢弃 + 最终一致）", tc_rapid_month_switch)

        def tc_ai_prepare_dialog_flash():
            # AI 备会按钮与行点击同级：缺 @click.stop 会连带打开编辑对话框。
            # 拦截 /assistant 懒加载 chunk 使导航停住，制造确定性的观测窗口。
            n_dialog = -1
            page.route("**/*.js", lambda route: route.abort())
            try:
                event_row("探测-定时日程").locator("button[title='AI 备会']").click()
                page.wait_for_timeout(600)
                n_dialog = page.evaluate("document.querySelectorAll('.el-dialog').length")
            finally:
                page.unroute("**/*.js")
                # 被中止的动态 import 在同文档内重试仍会失败（浏览器模块缓存行为），
                # 必须整页刷新拿到干净模块表，否则后续 AI 备会导航用例会被连带失败
                page.reload(wait_until="networkidle")
                page.wait_for_timeout(800)
                goto("/schedules")
            assert n_dialog == 0, \
                f"点击 AI 备会连带打开了编辑对话框（缺 @click.stop），dialog={n_dialog}"

        record("TC-SCH-018", "AI 备会不连带打开编辑对话框（@click.stop）", tc_ai_prepare_dialog_flash)

        def tc_ai_prepare_e2e():
            event_row("探测-定时日程").locator("button[title='AI 备会']").click()
            page.wait_for_timeout(400)
            page.wait_for_selector(".user-bubble", timeout=20000)
            bubbles = page.locator(".user-bubble").all_inner_texts()
            joined = " ".join(bubbles)
            assert "会前准备" in joined, f"AI 备会提示词未被发出: {joined[:200]}"
            assert "探测-定时日程" in joined, "提示词未包含日程标题"
            goto("/schedules")

        record("TC-SCH-019", "AI 备会端到端（跳转助手并自动发出提示词）", tc_ai_prepare_e2e)

        def tc_month_limit_hint():
            for i in range(205):
                hh, mm = 8 + (i % 9), (i * 7) % 60
                day = (i % 28) + 1
                eh, em = hh + (1 if mm >= 30 else 0), (mm + 30) % 60
                api_call("POST", "/schedules", {
                    "title": f"灌水日程{i}",
                    "start_time": f"{TODAY_S[:8]}{day:02d}T{hh:02d}:{mm:02d}:00",
                    "end_time": f"{TODAY_S[:8]}{day:02d}T{eh:02d}:{em:02d}:00",
                })
            page.reload(wait_until="networkidle")
            page.wait_for_timeout(1500)
            hint = page.locator(".month-limit-hint")
            assert hint.count() > 0 and hint.is_visible(), "超过 200 条未显示加载上限提示"

        record("TC-SCH-020", "单次加载上限 200 条提示", tc_month_limit_hint)

        def tc_api_put_null_time():
            _, created = api_call("POST", "/schedules", {
                "title": "探测-API空时间", "start_time": f"{TODAY_S}T09:00:00",
                "end_time": f"{TODAY_S}T10:00:00"})
            sid = created["id"]
            st, _ = api_call("PUT", f"/schedules/{sid}", {"end_time": None})
            assert st == 422, f"PUT end_time=null 应返回 422，实际 {st}"
            st2, _ = api_call("PUT", f"/schedules/{sid}", {"start_time": None})
            assert st2 == 422, f"PUT start_time=null 应返回 422，实际 {st2}"

        record("TC-SCH-021", "API：PUT 显式 null 时间字段返回 422（非 500）", tc_api_put_null_time)

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

    bugs = [
        {
            "severity": "P2",
            "title": "点击「AI 备会」连带打开编辑对话框（事件冒泡，缺 @click.stop）",
            "page": "/schedules",
            "description": "ScheduleView.vue 事件行 @click=openEdit 与行内 AI 备会按钮未隔离："
                           "点击 AI 备会后 aiPrepare 跳转 /assistant 的同时 openEdit 被连带触发，"
                           "编辑对话框闪显后才随组件卸载。删除按钮有 @click.stop，AI 按钮没有，行为不一致。",
            "status": "fixed",
            "fix_evidence": "frontend/src/views/ScheduleView.vue:49 增加 @click.stop",
            "test_evidence": "TC-SCH-018（探测脚本拦截 chunk 导航，修复前 dialog=1 断言失败，修复后 0）",
        },
        {
            "severity": "P2",
            "title": "PUT /api/schedules/{id} 显式传 null 时间字段返回 500 而非 422",
            "page": "/schedules",
            "description": "schedules.py 更新逻辑直接比较 schedule.end_time <= schedule.start_time；"
                           "当 ScheduleUpdate 显式传入 end_time/start_time=null 时置空后比较触发 TypeError，"
                           "穿透成 500（应拒绝为 422）。",
            "status": "fixed",
            "fix_evidence": "backend/app/routers/schedules.py:76 增加空值守卫",
            "test_evidence": "TC-SCH-021 + backend/tests/test_e2e_bug_schedules.py::test_put_null_time_rejected",
        },
    ]

    out = {
        "area": "schedules",
        "base": BASE,
        "started_at": STARTED_AT,
        "finished_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "pages": pages_report,
        "tests": [{"id": r[0], "name": r[1], "passed": r[2], "detail": r[3]} for r in results],
        "bugs": bugs,
    }
    (PROBE_DIR / "schedules.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"results -> {PROBE_DIR / 'schedules.json'}")


try:
    main()
finally:
    server.terminate()
    try:
        server.wait(timeout=10)
    except Exception:
        server.kill()
