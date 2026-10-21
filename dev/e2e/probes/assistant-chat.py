# -*- coding: utf-8 -*-
"""assistant-chat 区域深度 E2E 探测（端口 8219，独立库隔离，正式可重复运行资产）。

范围：/assistant 聊天体验（不重复 assistant-settings 的设置抽屉覆盖）
- 页面空态 / 会话列表 / 新建 / 重命名（含取消）/ 删除（取消+确认）/ 归档与恢复
- 会话排序、时间、消息计数、切换会话加载历史（含思考/工具轨迹折叠）
- 导出 Markdown（响应头 + 内容 + UI 下载）
- 无 LLM 环境下的发送降级（普通消息 / 空白拦截 / 超长 422 / SSE [DONE]）
- 附件-only 提示、文本/图片上传预览、切换会话后图片预览不失效（回归点）
- 模型 chip 未配置摘要、专家选择器
- 会话标题与消息 XSS
- 会话/附件接口边界与失败反馈

隔离：
- DATABASE_URL=sqlite:///dev/e2e/probe_results/assistant-chat.db
- FINANCE_DATA_DIR=dev/e2e/probe_results/assistant-chat-fin
- 上传目录：backend/app/config.py 的 UPLOAD_DIR 是模块级常量，没有环境变量入口。
  为遵守「不写 backend/data/」，本探针通过 PYTHONPATH 注入一个仅本次服务进程使用的
  sitecustomize，把 app.config.UPLOAD_DIR 重定向到 dev/e2e/probe_results/assistant-chat-uploads。
  服务命令仍为 backend/venv/Scripts/python.exe -m uvicorn app.main:app。
- 清空 LLM_PROVIDER/LLM_BASE_URL/LLM_API_KEY/LLM_MODEL，避免 .env 兜底产生真实外呼。

结果写 dev/e2e/probe_results/assistant-chat.json；失败截图放
dev/e2e/probe_results/assistant-chat/shots/。
"""
import json
import os
import re
import shutil
import sqlite3
import struct
import subprocess
import sys
import time
import urllib.request
import zlib
from datetime import datetime
from pathlib import Path

# 控制台中文/符号在 GBK 代码页下打印会崩：替换式输出，不影响判断
try:
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[3]
BACKEND = ROOT / "backend"
RESULTS = ROOT / "dev" / "e2e" / "probe_results"
AREA = "assistant-chat"
PORT = 8219
BASE = f"http://127.0.0.1:{PORT}"
DB = RESULTS / f"{AREA}.db"
FIN_DIR = RESULTS / f"{AREA}-fin"
UPLOAD_DIR = RESULTS / f"{AREA}-uploads"
PP_DIR = RESULTS / f"{AREA}-pp"
SHOTS = RESULTS / AREA / "shots"
LOG = RESULTS / f"{AREA}.server.log"
STARTED_AT = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

RESULTS.mkdir(parents=True, exist_ok=True)
if SHOTS.exists():
    shutil.rmtree(SHOTS)  # 清掉上次失败截图，避免陈旧文件冒充本次证据
SHOTS.mkdir(parents=True, exist_ok=True)
for p in (DB,):
    if p.exists():
        p.unlink()
for d in (FIN_DIR, UPLOAD_DIR, PP_DIR):
    if d.exists():
        shutil.rmtree(d)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
PP_DIR.mkdir(parents=True, exist_ok=True)

# 仅服务进程可见的 sitecustomize：把上传目录改道到 probe_results，避免写 backend/data/
(PP_DIR / "sitecustomize.py").write_text(
    "import os\n"
    "import sys\n"
    "from pathlib import Path\n"
    "\n"
    "_upload = os.environ.get('ASSISTANT_CHAT_UPLOAD_DIR')\n"
    "if _upload:\n"
    "    sys.path.insert(0, os.getcwd())\n"
    "    import app.config as _config\n"
    "    _config.UPLOAD_DIR = Path(_upload)\n",
    encoding="utf-8",
)

env = os.environ.copy()
env["DATABASE_URL"] = f"sqlite:///{DB.as_posix()}"
env["FINANCE_DATA_DIR"] = str(FIN_DIR)
env["PYTHONIOENCODING"] = "utf-8"
env["SEED_ON_STARTUP"] = "0"
# 无可用 LLM：显式空串覆盖 backend/.env 的兜底配置
for _k in ("LLM_PROVIDER", "LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL"):
    env[_k] = ""
env["ASSISTANT_CHAT_UPLOAD_DIR"] = str(UPLOAD_DIR)
env["PYTHONPATH"] = str(PP_DIR) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")

log_fh = open(LOG, "w", encoding="utf-8")
server = subprocess.Popen(
    [str(BACKEND / "venv" / "Scripts" / "python.exe"), "-m", "uvicorn",
     "app.main:app", "--host", "127.0.0.1", "--port", str(PORT)],
    cwd=str(BACKEND), env=env, stdout=log_fh, stderr=log_fh,
)

results = []  # {"id","name","passed","detail"}
page = None
console_errors = []
page_errors = []
requests_seen = []
error_baseline = [0, 0]  # 当前用例窗口内的 pageerror / console error 起始下标


def is_noise_console(msg):
    """浏览器对 4xx/abort 的资源错误与控制台无关，不算前端应用异常。"""
    low = (msg or "").lower()
    return (
        "favicon" in low
        or "failed to load resource" in low
        or "net::err_" in low
        or "err_aborted" in low
    )


def app_console_errors():
    return [e for e in console_errors if not is_noise_console(e)]


def shot(tag):
    try:
        if page is not None:
            page.screenshot(path=str(SHOTS / f"{tag}.png"))
    except Exception:
        pass


def cleanup_after_failure():
    """失败兜底：关闭确认框/下拉/抽屉，避免遮罩导致后续用例级联超时。"""
    if page is None:
        return
    try:
        box = page.locator(".el-message-box:visible").first
        if box.count():
            btn = box.locator("button", has_text="取消")
            if not btn.count():
                btn = box.locator(".el-message-box__headerbtn")
            if btn.count():
                btn.first.click(timeout=1500)
                page.wait_for_timeout(200)
        if page.locator(".el-select-dropdown:visible").count():
            neutral = page.locator(".chat-card .el-card__header, .sessions-card .el-card__header").first
            if neutral.count():
                neutral.click(timeout=1500)
                page.wait_for_timeout(200)
        for _ in range(2):
            drawers = page.locator(".el-drawer:visible")
            if not drawers.count():
                break
            btn = drawers.last.locator(".el-drawer__close-btn")
            if not btn.count():
                break
            btn.first.click(timeout=1500)
            page.wait_for_timeout(200)
    except Exception:
        pass


def record(tid, name, fn):
    # 每个用例只看自己执行窗口内新增的异常，避免上游用例的网络失败级联误报；
    # 全流程兜底仍由 CHAT-PAGE-003 检查全局数组。
    error_baseline[0] = len(page_errors)
    error_baseline[1] = len(app_console_errors())
    try:
        detail = fn() or ""
        results.append({"id": tid, "name": name, "passed": True, "detail": detail})
        print(f"[PASS] {tid} {name}" + (f" | {detail}" if detail else ""), flush=True)
    except Exception as e:
        detail = str(e)[:400]
        results.append({"id": tid, "name": name, "passed": False, "detail": detail})
        print(f"[FAIL] {tid} {name}: {detail}", flush=True)
        shot(f"{tid}-FAIL")
        cleanup_after_failure()


def wait_ready(timeout=60):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            urllib.request.urlopen(f"{BASE}/api/dashboard/summary", timeout=2)
            return True
        except Exception:
            time.sleep(1)
    return False


def api(method, path, **kw):
    if "json" in kw:
        kw["data"] = json.dumps(kw.pop("json"))
        headers = kw.pop("headers", {})
        headers["Content-Type"] = "application/json"
        kw["headers"] = headers
    return page.request.fetch(f"{BASE}{path}", method=method, **kw)


def api_body(resp):
    try:
        return resp.json()
    except Exception:
        return {}


def resp_headers(resp):
    return {str(k).lower(): str(v) for k, v in (resp.headers or {}).items()}


def db_exec(sql, params=()):
    con = sqlite3.connect(str(DB), timeout=15)
    try:
        con.execute("PRAGMA busy_timeout=15000")
        con.execute(sql, params)
        con.commit()
    finally:
        con.close()


def clear_sessions():
    """清空探测库会话/消息，保证计数与排序断言确定。"""
    db_exec("DELETE FROM chat_messages")
    db_exec("DELETE FROM agent_sessions")


def seed_messages(sid, items):
    """直接写 chat_messages（隔离库）造历史：items = [(role, agent, content, thinking, trace)]。"""
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")
    con = sqlite3.connect(str(DB), timeout=15)
    try:
        con.execute("PRAGMA busy_timeout=15000")
        for role, agent, content, thinking, trace in items:
            con.execute(
                "INSERT INTO chat_messages "
                "(session_id, role, agent_name, content, trace, thinking, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (sid, role, agent or "", content or "", json.dumps(trace or [], ensure_ascii=False),
                 thinking or "", stamp, stamp),
            )
        con.commit()
    finally:
        con.close()


def create_session(sid, title):
    r = api("POST", "/api/assistant/sessions", json={"session_id": sid, "title": title})
    assert r.status == 200, f"创建会话失败: {r.status} {r.text()[:120]}"
    return sid


def png_1x1():
    """生成合法 1x1 PNG，避免依赖外部图片资产。"""
    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    raw = b"\x00\xff\x00\x00"  # filter=0 + RGB red
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def goto_assistant():
    """全新挂载 /assistant（同 hash 导航不重置组件状态，reload 保证隔离）。"""
    page.goto(f"{BASE}/#/assistant", wait_until="networkidle", timeout=30000)
    page.reload(wait_until="networkidle", timeout=30000)
    page.locator(".assistant-layout").wait_for(state="visible", timeout=10000)
    page.wait_for_timeout(500)


def session_item(title):
    return page.locator(".session-item").filter(
        has=page.locator(".session-title", has_text=re.compile(f"^{re.escape(title)}$"))
    ).first


def session_titles():
    return [t.strip() for t in page.locator(".sessions-card .session-title").all_inner_texts()]


def click_session(title):
    item = session_item(title)
    item.wait_for(state="visible", timeout=5000)
    item.locator(".session-title").click()


def session_action(title, action):
    item = session_item(title)
    item.wait_for(state="visible", timeout=5000)
    item.hover()
    page.wait_for_timeout(150)
    item.locator(f'[title="{action}"]').click()


def message_box():
    box = page.locator(".el-message-box:visible")
    box.wait_for(state="visible", timeout=5000)
    return box


def confirm_box(btn_text="确定"):
    box = message_box()
    box.locator("button", has_text=btn_text).first.click()
    page.wait_for_timeout(500)


def wait_toast(text, timeout=8000):
    page.locator(".el-message", has_text=text).first.wait_for(state="visible", timeout=timeout)


def clear_toasts():
    try:
        page.evaluate("() => document.querySelectorAll('.el-message').forEach((el) => el.remove())")
        page.wait_for_timeout(150)
    except Exception:
        pass


def wait_count(selector, n, timeout=8000):
    deadline = time.time() + timeout / 1000
    while time.time() < deadline:
        if page.locator(selector).count() == n:
            return True
        page.wait_for_timeout(100)
    raise AssertionError(f"{selector} 期望 {n} 个，实际 {page.locator(selector).count()} 个")


def wait_stream_done(timeout=15000):
    deadline = time.time() + timeout / 1000
    while time.time() < deadline:
        cursor = page.locator(".cursor:visible").count()
        loading = page.locator(".input-row .el-button.is-loading").count()
        if cursor == 0 and loading == 0:
            return
        page.wait_for_timeout(100)
    raise AssertionError("SSE 流未结束（loading/cursor 未复位）")


def send_message(text, via="button"):
    ta = page.locator(".input-row textarea")
    ta.fill(text)
    if via == "enter":
        ta.press("Enter")
    else:
        page.locator(".input-row button", has_text="发送").first.click()


def upload_ui(name, mime, data):
    inp = page.locator(".input-row input[type=file]")
    inp.set_input_files({"name": name, "mimeType": mime, "buffer": data})


def upload_api(sid, name, mime, data):
    return page.request.post(
        f"{BASE}/api/assistant/upload",
        multipart={
            "session_id": sid,
            "file": {"name": name, "mimeType": mime, "buffer": data},
        },
    )


def image_loaded(locator):
    return locator.evaluate("el => el.complete && el.naturalWidth > 0")


def agent_item(label):
    return page.locator(".agent-item").filter(
        has=page.locator(".agent-label", has_text=re.compile(f"^{re.escape(label)}$"))
    ).first


def recent_page_errors():
    return page_errors[error_baseline[0]:]


def recent_console_errors():
    return app_console_errors()[error_baseline[1]:]


def assert_no_app_errors(context=""):
    errs = recent_console_errors()
    assert not errs, f"{context} 出现控制台应用错误: {errs[-3:]}"
    assert not recent_page_errors(), f"{context} 出现未捕获异常: {recent_page_errors()[-3:]}"


try:
    if not wait_ready():
        print("FATAL: server not ready")
        sys.exit(2)

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: page_errors.append(str(e)))
        page.on("request", lambda r: requests_seen.append(f"{r.method} {r.url}"))

        # ---------- 页面与空态 ----------
        def tc_page_empty():
            clear_sessions()
            goto_assistant()
            desc = page.locator(".sessions-card .el-empty__description").inner_text()
            assert "暂无会话" in desc, f"会话空态文案异常: {desc!r}"
            chat_empty = page.locator(".chat-box .el-empty").inner_text()
            assert "向团队提问" in chat_empty, f"聊天空态文案异常: {chat_empty[:80]!r}"
            assert page.locator(".suggestions button").count() == 3, "示例问题按钮数量异常"
            assert "Agent 团队（8）" in page.locator(".team-card").inner_text(), "内置专家名册未加载"
            assert "模型未配置" in page.locator(".team-card .el-tag").first.inner_text(), "模型未配置徽标缺失"
            assert "尚未配置模型 API" in page.locator(".team-card .el-alert").inner_text(), "未配置提示缺失"
            chip = page.locator(".model-chip").inner_text()
            assert "未配置" in chip and "env 兜底" not in chip, f"模型 chip 未配置摘要异常: {chip!r}"
            assert "当前模型：未配置" in (page.locator(".model-chip").get_attribute("title") or ""), "chip title 异常"
            assert page.locator(".env-fallback-badge").count() == 0, "无 LLM 时不应出现 env 兜底徽标"
            mode = page.locator(".mode-select").nth(0).inner_text()
            assert "标准执行" in mode, f"默认工作模式异常: {mode!r}"
            reason = page.locator(".mode-select").nth(1).inner_text()
            assert "跟随模型" in reason, f"默认思考深度异常: {reason!r}"
            assert page.locator(".session-item").count() == 0, "空库仍渲染会话项"
            assert_no_app_errors("页面空态")
            return "空态/示例问题/专家名册/未配置徽标/模型 chip 均正常"
        record("CHAT-PAGE-001", "页面加载与空态（无 LLM）", tc_page_empty)

        def tc_page_layout():
            clear_sessions()
            create_session("s_layout_probe", "布局探测会话")
            goto_assistant()
            assert page.locator(".left-col .sessions-card").count() == 1, "左列会话卡片缺失"
            assert page.locator(".left-col .team-card").count() == 1, "左列团队卡片缺失"
            assert page.locator(".chat-card").count() == 1, "聊天卡片缺失"
            assert page.locator(".input-row textarea").count() == 1, "输入框缺失"
            assert page.locator(".input-row input[type=file]").count() == 1, "附件入口缺失"
            accept = page.locator(".input-row input[type=file]").get_attribute("accept") or ""
            assert ".png" in accept and ".txt" in accept, f"附件 accept 未含图片/文本: {accept!r}"
            assert page.locator(".session-item.active").count() == 1, "首个会话未被自动选中"
            assert_no_app_errors("三栏布局")
            return "左列双卡 + 聊天区 + 输入区 + 附件入口齐全"
        record("CHAT-PAGE-002", "三栏布局与输入区", tc_page_layout)

        # ---------- 会话生命周期 ----------
        def tc_session_create():
            clear_sessions()
            goto_assistant()
            page.locator(".sessions-card button", has_text="新建").click()
            session_item("新会话").wait_for(state="visible", timeout=5000)
            assert page.locator(".session-item.active .session-title").inner_text().strip() == "新会话", \
                "新建会话未激活"
            assert "向团队提问" in page.locator(".chat-box .el-empty").inner_text(), "新建后聊天区未清空"
            body = api_body(api("GET", "/api/assistant/sessions"))
            assert len(body["sessions"]) == 1, f"后端会话数异常: {body}"
            row = body["sessions"][0]
            assert row["title"] == "新会话" and row["msg_count"] == 0 and row["archived"] is False, f"会话字段异常: {row}"
            assert_no_app_errors("新建会话")
            return "新建会话入列表并激活，聊天区空态，后端 1 条"
        record("CHAT-SESS-001", "新建会话", tc_session_create)

        def tc_session_rename():
            clear_sessions()
            create_session("s_rename_probe", "重命名前-探测")
            goto_assistant()
            session_action("重命名前-探测", "重命名")
            box = message_box()
            title = box.locator(".el-message-box__title").inner_text()
            assert title == "重命名", f"重命名弹窗标题异常: {title!r}"
            inp = box.locator("input").first
            assert inp.input_value() == "重命名前-探测", f"输入框未回填原标题: {inp.input_value()!r}"
            inp.fill("重命名后-探测")
            confirm_box("确定")
            wait_toast("已重命名")
            session_item("重命名后-探测").wait_for(state="visible", timeout=5000)
            assert session_item("重命名前-探测").count() == 0, "旧标题仍在列表"
            body = api_body(api("GET", "/api/assistant/sessions"))
            assert body["sessions"][0]["title"] == "重命名后-探测", "后端未持久化重命名"
            # 取消路径：输入的内容不得落库
            session_action("重命名后-探测", "重命名")
            box = message_box()
            box.locator("input").first.fill("取消不保存")
            box.locator("button", has_text="取消").click()
            page.wait_for_timeout(500)
            assert session_item("重命名后-探测").count() == 1, "取消重命名后标题变化"
            body = api_body(api("GET", "/api/assistant/sessions"))
            assert body["sessions"][0]["title"] == "重命名后-探测", "取消重命名仍写入后端"
            assert_no_app_errors("重命名")
            return "重命名回填/保存/取消均正确"
        record("CHAT-SESS-002", "重命名（保存与取消）", tc_session_rename)

        def tc_session_delete_cancel():
            clear_sessions()
            create_session("s_delete_cancel", "删除取消-探测")
            goto_assistant()
            session_action("删除取消-探测", "删除")
            box = message_box()
            text = box.inner_text()
            assert "删除确认" in text and "删除取消-探测" in text, f"删除确认文案异常: {text[:80]!r}"
            box.locator("button", has_text="取消").click()
            page.wait_for_timeout(500)
            assert session_item("删除取消-探测").count() == 1, "取消删除后会话消失"
            body = api_body(api("GET", "/api/assistant/sessions"))
            assert any(s["session_id"] == "s_delete_cancel" for s in body["sessions"]), "取消删除仍删后端"
            assert_no_app_errors("删除取消")
            return "取消删除保留会话（UI + 后端）"
        record("CHAT-SESS-003", "删除确认-取消", tc_session_delete_cancel)

        def tc_session_delete_confirm():
            clear_sessions()
            create_session("s_delete_confirm", "删除确认-探测")
            seed_messages("s_delete_confirm", [
                ("user", "", "要删掉的问题", "", []),
                ("assistant", "it_finance", "要删掉的回答", "", []),
            ])
            goto_assistant()
            item = session_item("删除确认-探测")
            assert "2 条" in item.inner_text(), f"删除前消息计数异常: {item.inner_text()!r}"
            session_action("删除确认-探测", "删除")
            confirm_box("确定")
            wait_toast("已删除")
            assert session_item("删除确认-探测").count() == 0, "确认删除后列表仍在"
            assert "向团队提问" in page.locator(".chat-box .el-empty").inner_text(), "删除当前会话后聊天区未清空"
            body = api_body(api("GET", "/api/assistant/sessions"))
            assert all(s["session_id"] != "s_delete_confirm" for s in body["sessions"]), "后端会话未删除"
            hist = api_body(api("GET", "/api/assistant/history/s_delete_confirm"))
            assert hist["messages"] == [], "删除会话后消息仍残留"
            assert_no_app_errors("删除确认")
            return "确认删除：列表/后端/消息全部清理，聊天区清空"
        record("CHAT-SESS-004", "删除确认-确定", tc_session_delete_confirm)

        def tc_session_archive_restore():
            clear_sessions()
            create_session("s_archive_a", "归档-A")
            time.sleep(0.04)
            create_session("s_archive_b", "归档-B")
            goto_assistant()
            assert page.locator(".session-item.active .session-title").inner_text().strip() == "归档-B", \
                "默认应选中最新会话"
            session_action("归档-A", "归档")
            wait_toast("已归档")
            assert session_item("归档-A").count() == 0, "归档后仍出现在进行中列表"
            body = api_body(api("GET", "/api/assistant/sessions", params={"archived": "true"}))
            assert any(s["session_id"] == "s_archive_a" for s in body["sessions"]), "后端 archived 未置 true"
            page.locator(".el-radio-button", has_text="已归档").click()
            page.locator(".el-radio-button.is-active", has_text="已归档").wait_for(state="visible", timeout=5000)
            session_item("归档-A").wait_for(state="visible", timeout=5000)
            assert session_item("归档-A").locator('[title="恢复"]').count() == 1, "归档列表缺少恢复图标"
            session_action("归档-A", "恢复")
            wait_toast("已恢复到进行中")
            page.wait_for_timeout(500)
            assert session_item("归档-A").count() == 0, "恢复后仍留在已归档列表"
            page.locator(".el-radio-button", has_text="进行中").click()
            session_item("归档-A").wait_for(state="visible", timeout=5000)
            body = api_body(api("GET", "/api/assistant/sessions", params={"archived": "false"}))
            assert any(s["session_id"] == "s_archive_a" for s in body["sessions"]), "后端恢复未置 archived=false"
            assert_no_app_errors("归档恢复")
            return "非当前会话归档→已归档页签→恢复→进行中，后端同步"
        record("CHAT-SESS-005", "归档与恢复（非当前会话）", tc_session_archive_restore)

        def tc_session_archive_current():
            clear_sessions()
            create_session("s_archive_cur", "归档当前-探测")
            seed_messages("s_archive_cur", [
                ("user", "", "归档前的消息", "", []),
                ("assistant", "it_finance", "归档前的回答", "", []),
            ])
            goto_assistant()
            assert page.locator(".session-item.active").count() == 1, "前置：当前会话未激活"
            assert page.locator(".msg-row").count() == 2, "前置：历史消息未加载"
            session_action("归档当前-探测", "归档")
            wait_toast("已从当前视图中移除")
            assert page.locator(".session-item.active").count() == 0, "归档当前会话后仍有激活项"
            assert "向团队提问" in page.locator(".chat-box .el-empty").inner_text(), "归档当前会话后聊天区未清空"
            page.locator(".el-radio-button", has_text="已归档").click()
            session_item("归档当前-探测").wait_for(state="visible", timeout=5000)
            session_action("归档当前-探测", "恢复")
            wait_toast("已恢复到进行中")
            page.locator(".el-radio-button", has_text="进行中").click()
            session_item("归档当前-探测").wait_for(state="visible", timeout=5000)
            assert page.locator(".session-item.active").count() == 0, "恢复后会话被错误自动选中"
            assert_no_app_errors("归档当前会话")
            return "归档当前会话会打断选中并清空聊天区，恢复后回到进行中"
        record("CHAT-SESS-006", "归档当前会话", tc_session_archive_current)

        def tc_session_archived_empty_new():
            clear_sessions()
            goto_assistant()
            page.locator(".el-radio-button", has_text="已归档").click()
            page.wait_for_timeout(500)
            desc = page.locator(".sessions-card .el-empty__description").inner_text()
            assert "暂无已归档会话" in desc, f"已归档空态文案异常: {desc!r}"
            page.locator(".sessions-card button", has_text="新建").click()
            session_item("新会话").wait_for(state="visible", timeout=5000)
            assert "进行中" in page.locator(".el-radio-button.is-active").inner_text(), "新建后未切回进行中页签"
            assert page.locator(".session-item.active").count() == 1, "新建会话未激活"
            assert_no_app_errors("已归档空态与新建")
            return "已归档空态文案正确；新建自动切回进行中"
        record("CHAT-SESS-007", "已归档空态与新建切页签", tc_session_archived_empty_new)

        def tc_session_sort_count_time():
            clear_sessions()
            create_session("s_sort_1", "排序-最早")
            time.sleep(0.03)
            create_session("s_sort_2", "排序-中间")
            time.sleep(0.03)
            create_session("s_sort_3", "排序-最新")
            seed_messages("s_sort_2", [
                ("user", "", "m1", "", []),
                ("assistant", "it_finance", "m2", "", []),
                ("user", "", "m3", "", []),
            ])
            goto_assistant()
            titles = session_titles()
            assert titles[:3] == ["排序-最新", "排序-中间", "排序-最早"], f"会话排序异常: {titles}"
            item = session_item("排序-中间")
            assert "3 条" in item.inner_text(), f"消息计数异常: {item.inner_text()!r}"
            for t in titles[:3]:
                ts = session_item(t).locator(".session-time").inner_text()
                assert re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}", ts), f"会话时间格式异常 {t}: {ts!r}"
            body = api_body(api("GET", "/api/assistant/sessions"))
            assert [s["session_id"] for s in body["sessions"][:3]] == ["s_sort_3", "s_sort_2", "s_sort_1"], \
                f"后端排序异常: {body['sessions']}"
            assert body["sessions"][1]["msg_count"] == 3, "后端 msg_count 异常"
            assert_no_app_errors("排序计数")
            return "按 updated_at 倒序；msg_count=3；时间格式 YYYY-MM-DD HH:mm"
        record("CHAT-SESS-008", "会话排序/消息计数/时间", tc_session_sort_count_time)

        def tc_session_switch_history():
            clear_sessions()
            create_session("s_hist_a", "历史-A")
            time.sleep(0.03)
            create_session("s_hist_b", "历史-B")
            seed_messages("s_hist_a", [
                ("user", "", "A-问题", "", []),
                ("assistant", "it_finance", "A-回答", "A-思考过程",
                 [{"tool": "workbench_stats", "args": {"x": 1}, "result": {"ok": True}}]),
            ])
            seed_messages("s_hist_b", [("user", "", "B-问题", "", [])])
            goto_assistant()
            click_session("历史-B")
            wait_count(".msg-row", 1)
            assert "B-问题" in page.locator(".user-bubble").first.inner_text(), "B 会话历史未加载"
            click_session("历史-A")
            wait_count(".msg-row", 2)
            rows = page.locator(".msg-row")
            assert "A-问题" in rows.nth(0).inner_text(), "历史消息顺序错误（用户应在先）"
            assert "A-回答" in rows.nth(1).inner_text(), "助手历史消息缺失"
            assert "信息化财务分析师" in page.locator(".agent-chip").inner_text(), "历史助手标签错误"
            assert page.locator(".msg-time").first.inner_text().strip().count(":") == 1, "历史消息时间缺失"
            assert "思考过程" in page.locator(".think-collapse").inner_text(), "历史思考过程未渲染"
            assert "工具调用（1）" in page.locator(".trace-collapse").inner_text(), "历史工具轨迹未渲染"
            click_session("历史-B")
            wait_count(".msg-row", 1)
            assert "B-问题" in page.locator(".user-bubble").first.inner_text(), "切回 B 历史错误"
            assert_no_app_errors("切换会话历史")
            return "A/B 切换各加载正确历史；思考/工具折叠与专家标签正常"
        record("CHAT-SESS-009", "切换会话加载历史", tc_session_switch_history)

        def tc_xss():
            clear_sessions()
            # 后端自动标题会截断到 30 字，这里用恰好 30 字的有效 onerror 载荷；
            # 若标题被当 HTML 注入，window.z 会被置 1。
            xss_title = '<img src=x onerror=window.z=1>'
            create_session("s_xss_probe", xss_title)
            seed_messages("s_xss_probe", [
                ("user", "", '<img src=x onerror="window.__xss_msg=1">', "", []),
                ("assistant", "it_finance",
                 '<script>window.__xss_ai=1</script>\n\n[点我](javascript:window.__xss_link=1)', "", []),
            ])
            goto_assistant()
            click_session(xss_title)
            wait_count(".msg-row", 2)
            page.wait_for_timeout(300)
            flags = page.evaluate(
                "() => ({t: window.z, m: window.__xss_msg, a: window.__xss_ai, l: window.__xss_link})")
            assert all(v is None for v in flags.values()), f"XSS 被执行: {flags}"
            assert "<img" in session_item(xss_title).inner_text(), "会话标题未按字面展示"
            assert "<img" in page.locator(".user-bubble").first.inner_text(), "用户消息 HTML 未转义"
            assert "<script>" in page.locator(".ai-bubble").first.inner_text(), "助手消息 HTML 未转义"
            assert page.locator(".ai-bubble a[href^='javascript']").count() == 0, "javascript: 链接未被拦截"
            assert_no_app_errors("XSS")
            return "标题/用户消息/助手 Markdown 均按字面转义，javascript: 链接被拦截"
        record("CHAT-XSS-001", "会话标题与消息 XSS 防护", tc_xss)

        # ---------- 导出 Markdown ----------
        export_sid = "s_export_probe"
        export_title = "导出-探测会话"

        def prepare_export():
            clear_sessions()
            create_session(export_sid, export_title)
            seed_messages(export_sid, [
                ("user", "", "进项发票共几张？", "", []),
                ("assistant", "it_finance", "共 12 张，其中 2 张待认证。", "先查任务统计，再核对发票状态",
                 [{"tool": "workbench_stats", "args": {"x": 1}, "result": {"task": {"todo": 3}}}]),
            ])

        def tc_export_api():
            prepare_export()
            r = api("GET", f"/api/assistant/sessions/{export_sid}/export")
            assert r.status == 200, f"导出状态异常: {r.status}"
            h = resp_headers(r)
            assert h.get("content-type", "").startswith("text/markdown"), f"Content-Type 异常: {h.get('content-type')}"
            assert 'attachment; filename="session-s_export_probe.md"' == h.get("content-disposition"), \
                f"Content-Disposition 异常: {h.get('content-disposition')!r}"
            text = r.text()
            for expected in (
                "# 会话导出：导出-探测会话",
                "- 会话 ID：s_export_probe",
                "- 消息数：2",
                "## 1. 用户",
                "## 2. 信息化财务分析师",
                "进项发票共几张？",
                "共 12 张，其中 2 张待认证。",
                "<details><summary>思考过程</summary>",
                "先查任务统计，再核对发票状态",
                "<details><summary>工具执行轨迹</summary>",
                "workbench_stats",
            ):
                assert expected in text, f"导出内容缺少: {expected!r}"
            assert_no_app_errors("导出 API")
            return "响应头 text/markdown + attachment 文件名正确；标题/消息/思考/轨迹齐全"
        record("CHAT-EXP-001", "导出 Markdown（响应头与内容）", tc_export_api)

        def tc_export_ui_download():
            prepare_export()
            goto_assistant()
            item = session_item(export_title)
            item.hover()
            page.wait_for_timeout(150)
            with page.expect_download(timeout=10000) as dl_info:
                item.locator('[title="导出 Markdown"]').click()
            dl = dl_info.value
            assert dl.suggested_filename == f"session-{export_sid}.md", \
                f"下载文件名异常: {dl.suggested_filename!r}"
            target = SHOTS / "export.md"
            dl.save_as(str(target))
            text = target.read_text(encoding="utf-8")
            assert "# 会话导出：导出-探测会话" in text and "进项发票共几张？" in text, "下载内容不完整"
            assert_no_app_errors("导出 UI 下载")
            return f"UI 下载触发成功，文件名 {dl.suggested_filename}，内容与 API 一致"
        record("CHAT-EXP-002", "导出 Markdown（UI 下载）", tc_export_ui_download)

        # ---------- 发送与无 LLM 降级 ----------
        def tc_send_unconfigured():
            clear_sessions()
            create_session("s_send_probe", "发送降级-探测")
            goto_assistant()
            clear_toasts()
            with page.expect_response(lambda r: "/api/assistant/chat/stream" in r.url) as info:
                send_message("你好，探测无模型降级")
            resp = info.value
            assert resp.status == 200, f"SSE 状态应为 200，实际 {resp.status}"
            h = resp_headers(resp)
            assert h.get("content-type", "").startswith("text/event-stream"), f"SSE Content-Type 异常: {h.get('content-type')}"
            wait_stream_done()
            wait_count(".msg-row", 2)
            assert "你好，探测无模型降级" in page.locator(".user-bubble").first.inner_text(), "用户气泡缺失"
            ai = page.locator(".ai-bubble").first
            assert "error-bubble" in (ai.get_attribute("class") or ""), "未配置时未渲染错误气泡"
            txt = ai.inner_text()
            assert "模型未配置" in txt and "设置" in txt, f"未配置提示不友好: {txt[:120]!r}"
            assert "系统提示" in txt, "错误气泡未标记系统提示"
            assert page.locator(".el-message--error").count() == 0, "未配置降级不应弹全局错误"
            hist = api_body(api("GET", "/api/assistant/history/s_send_probe"))
            assert hist["messages"] == [], "未配置时不应落库消息（当前实现直接返回 error 事件）"
            assert_no_app_errors("无 LLM 发送")
            return "SSE 200 + error 事件友好提示 + [DONE]；不 500/不落库/无未捕获异常"
        record("CHAT-SEND-001", "普通消息无 LLM 降级（SSE）", tc_send_unconfigured)

        def tc_send_blank():
            clear_sessions()
            create_session("s_blank_probe", "空白拦截-探测")
            goto_assistant()
            clear_toasts()
            requests_seen.clear()
            send_message("   ")
            page.wait_for_timeout(600)
            assert page.locator(".msg-row").count() == 0, "纯空格被发送"
            assert page.locator(".input-row textarea").input_value() == "   ", "空白拦截后草稿被清空"
            assert not any("/api/assistant/chat/stream" in u for u in requests_seen), "纯空格仍发起聊天请求"
            assert page.locator(".el-message").count() == 0, "纯空格不应弹提示"
            page.locator(".input-row button", has_text="发送").first.click()
            page.wait_for_timeout(400)
            assert page.locator(".msg-row").count() == 0, "空输入被发送"
            assert not any("/api/assistant/chat/stream" in u for u in requests_seen), "空输入仍发起聊天请求"
            assert_no_app_errors("空白拦截")
            return "纯空格/空输入均静默拦截：无消息、无请求、无提示"
        record("CHAT-SEND-002", "空白消息拦截", tc_send_blank)

        def tc_send_too_long():
            clear_sessions()
            create_session("s_long_probe", "超长消息-探测")
            goto_assistant()
            clear_toasts()
            with page.expect_response(lambda r: "/api/assistant/chat/stream" in r.url) as info:
                send_message("超" * 8001)
            resp = info.value
            assert resp.status == 422, f"8001 字符应 422，实际 {resp.status}"
            body = api_body(resp)
            assert isinstance(body.get("detail"), list) and body["detail"], f"422 detail 结构异常: {body}"
            wait_stream_done()
            wait_count(".msg-row", 2)
            toast = page.locator(".el-message--error").first
            toast.wait_for(state="visible", timeout=5000)
            assert "422" in toast.inner_text(), f"422 降级提示缺少状态码: {toast.inner_text()!r}"
            assert "HTTP 422" in page.locator(".ai-bubble.error-bubble").inner_text(), "422 未渲染错误气泡"
            assert_no_app_errors("超长消息 422")
            # 失败后可继续发送
            clear_toasts()
            send_message("恢复发送")
            wait_stream_done()
            wait_count(".msg-row", 4)
            assert "模型未配置" in page.locator(".ai-bubble").last.inner_text(), "超长 422 后无法恢复发送"
            assert_no_app_errors("超长消息后恢复")
            return "8001 字符 422 有提示/错误气泡/无未捕获异常，且可继续发送"
        record("CHAT-SEND-003", "超长消息 422 优雅降级", tc_send_too_long)

        def tc_send_enter():
            clear_sessions()
            create_session("s_enter_probe", "回车发送-探测")
            goto_assistant()
            ta = page.locator(".input-row textarea")
            ta.fill("第一行")
            ta.press("Shift+Enter")
            ta.type("第二行")
            assert "\n" in ta.input_value(), "Shift+Enter 未插入换行"
            assert page.locator(".msg-row").count() == 0, "Shift+Enter 误发送"
            ta.press("Enter")
            wait_stream_done()
            wait_count(".msg-row", 2)
            bubble = page.locator(".user-bubble").first.inner_text()
            assert "第一行" in bubble and "第二行" in bubble, f"回车发送内容异常: {bubble!r}"
            assert_no_app_errors("回车发送")
            return "Shift+Enter 换行不发送；Enter 发送多行内容"
        record("CHAT-SEND-004", "Enter 发送 / Shift+Enter 换行", tc_send_enter)

        def tc_send_contract():
            r = api("POST", "/api/assistant/chat", json={"session_id": "s_api_contract", "message": "你好"})
            assert r.status == 200, f"非流式 chat 状态异常: {r.status}"
            body = api_body(r)
            assert body.get("ok") is False and body.get("need_llm_config") is True, f"非流式降级体异常: {body}"
            assert "模型未配置" in (body.get("message") or ""), "非流式降级文案缺失"
            assert api("POST", "/api/assistant/chat", json={"session_id": "s_api_contract", "message": ""}).status == 422
            assert api("POST", "/api/assistant/chat", json={"session_id": "ab", "message": "hi"}).status == 422
            r = api("POST", "/api/assistant/chat/stream", json={"session_id": "s_api_contract", "message": "你好"})
            assert r.status == 200, f"SSE 状态异常: {r.status}"
            assert resp_headers(r).get("content-type", "").startswith("text/event-stream"), "SSE Content-Type 异常"
            text = r.text()
            assert '"need_llm_config": true' in text, f"SSE 缺少 need_llm_config: {text[:200]!r}"
            assert text.rstrip().endswith("data: [DONE]"), f"SSE 未以 [DONE] 收尾: {text[-120:]!r}"
            assert '"type": "error"' in text, "SSE 未返回 error 事件"
            assert api("GET", "/api/assistant/models").json()["models"] == [], "隔离库不应有模型"
            agents = api("GET", "/api/assistant/agents").json()["agents"]
            assert len(agents) == 8 and all(a["is_builtin"] for a in agents), f"内置专家名册异常: {len(agents)}"
            assert api("POST", "/api/assistant/chat", json={"session_id": "bad id!", "message": "hi"}).status == 400
            assert api("POST", "/api/assistant/chat/stream", json={"session_id": "bad id!", "message": "hi"}).status == 400
            assert_no_app_errors("聊天契约")
            return "chat 200 降级 / 空消息与短 id 422 / SSE error+[DONE] / 非法 session_id 400"
        record("CHAT-SEND-005", "聊天接口无 LLM 契约", tc_send_contract)

        # ---------- 附件 ----------
        def tc_upload_api():
            clear_sessions()
            sid = "s_upload_api_probe"
            png = png_1x1()
            r = upload_api(sid, "api-1x1.png", "image/png", png)
            assert r.status == 200, f"图片上传失败: {r.status} {r.text()[:120]}"
            body = api_body(r)
            assert body.get("kind") == "image", f"图片 kind 异常: {body}"
            assert body.get("size") == len(png), f"图片 size 异常: {body.get('size')} != {len(png)}"
            path = Path(body["path"])
            assert path.is_file(), f"上传文件不存在: {path}"
            assert UPLOAD_DIR.resolve() in path.resolve().parents, f"上传文件未落在隔离目录: {path}"
            assert not (BACKEND / "data" / "uploads" / sid).exists(), "上传仍然写入 backend/data/uploads"
            r = upload_api(sid, "api-note.txt", "text/plain", "进项发票核对样本\n".encode("utf-8"))
            assert r.status == 200, f"文本上传失败: {r.status}"
            body = api_body(r)
            assert body.get("kind") == "file", f"文本 kind 异常: {body}"
            r = upload_api(sid, "api-bad.exe", "application/octet-stream", b"MZ")
            assert r.status == 400, f"不支持类型应 400，实际 {r.status}"
            assert "不支持的文件类型" in (api_body(r).get("detail") or ""), "不支持类型 detail 缺失"
            r = upload_api("bad id!", "x.txt", "text/plain", b"x")
            assert r.status == 400, f"非法 session_id 应 400，实际 {r.status}"
            assert "非法的 session_id" in (api_body(r).get("detail") or ""), "非法 session_id detail 缺失"
            assert_no_app_errors("上传 API")
            return "图片/文本 kind/size/path 正确且落在隔离目录；非法类型/会话 400"
        record("CHAT-ATT-001", "上传接口（图片/文本/非法类型/非法会话）", tc_upload_api)

        def tc_upload_ui_preview_remove():
            clear_sessions()
            create_session("s_upload_ui", "附件预览-探测")
            goto_assistant()
            upload_ui("probe-note.txt", "text/plain", "附件样本内容".encode("utf-8"))
            tag = page.locator(".attach-row .el-tag", has_text="probe-note.txt")
            tag.wait_for(state="visible", timeout=8000)
            upload_ui("probe-pixel.png", "image/png", png_1x1())
            img = page.locator(".attach-row .attach-img")
            img.wait_for(state="visible", timeout=8000)
            assert image_loaded(img), "图片附件预览未真正加载"
            assert "probe-pixel.png" in (page.locator(".attach-img-wrap").get_attribute("title") or ""), "图片附件标题缺失"
            page.locator(".attach-img-x").click()
            page.wait_for_timeout(500)
            assert page.locator(".attach-row .attach-img").count() == 0, "移除图片附件后缩略图仍在"
            assert tag.count() == 1, "移除图片误删文本附件"
            assert_no_app_errors("附件预览")
            return "文本 tag + 图片缩略图（naturalWidth>0）；单独移除图片不影响文本"
        record("CHAT-ATT-002", "附件上传预览与移除", tc_upload_ui_preview_remove)

        def tc_attachment_only_hint():
            clear_sessions()
            create_session("s_att_only", "仅附件-探测")
            goto_assistant()
            upload_ui("probe-only.txt", "text/plain", "only attachment".encode("utf-8"))
            tag = page.locator(".attach-row .el-tag", has_text="probe-only.txt")
            tag.wait_for(state="visible", timeout=8000)
            clear_toasts()
            requests_seen.clear()
            page.locator(".input-row button", has_text="发送").first.click()
            page.wait_for_timeout(700)
            wait_toast("请补一句文字说明")
            assert page.locator(".msg-row").count() == 0, "仅附件消息被发送"
            assert not any("/api/assistant/chat/stream" in u for u in requests_seen), "仅附件仍发起聊天请求"
            assert tag.count() == 1, "拦截发送后附件被误清空"
            # 补文字后正常随附件发送
            send_message("请分析这个附件")
            wait_stream_done()
            wait_count(".msg-row", 2)
            bubble = page.locator(".user-bubble").first.inner_text()
            assert "请分析这个附件" in bubble and "（附件：probe-only.txt）" in bubble, f"附件文本未随消息发送: {bubble!r}"
            assert page.locator(".attach-row .el-tag").count() == 0, "发送后附件未清空"
            assert_no_app_errors("仅附件提示")
            return "空文本+附件被拦截并提示，附件保留；补文字后附件名随消息发送"
        record("CHAT-ATT-003", "仅附件消息提示（回归点）", tc_attachment_only_hint)

        def tc_image_switch_regression():
            clear_sessions()
            create_session("s_img_a", "图片切换-A")
            time.sleep(0.03)
            create_session("s_img_b", "图片切换-B")
            goto_assistant()
            click_session("图片切换-A")
            upload_ui("probe-switch.png", "image/png", png_1x1())
            img = page.locator(".attach-row .attach-img")
            img.wait_for(state="visible", timeout=8000)
            assert image_loaded(img), "初始图片预览未加载"
            click_session("图片切换-B")
            wait_count(".msg-row", 0)
            click_session("图片切换-A")
            page.wait_for_timeout(700)
            img = page.locator(".attach-row .attach-img")
            assert img.count() == 1, "切换会话后待发送图片预览消失"
            assert image_loaded(img), "切换会话后图片预览 objectURL 已失效（回归）"
            send_message("请分析这张图片")
            wait_stream_done()
            wait_count(".msg-row", 2)
            bubble_img = page.locator(".user-bubble .msg-img").first
            bubble_img.wait_for(state="visible", timeout=5000)
            assert image_loaded(bubble_img), "发送后用户气泡图片未加载"
            assert_no_app_errors("图片切换回归")
            return "上传图片→切会话→切回：输入区缩略图仍可加载；发送后气泡图片正常"
        record("CHAT-ATT-004", "切换会话后图片预览不失效（回归点）", tc_image_switch_regression)

        def tc_upload_fail_feedback():
            clear_sessions()
            create_session("s_upload_fail", "上传失败-探测")
            goto_assistant()
            clear_toasts()
            handler = lambda r: r.abort()
            page.route(re.compile(r"/api/assistant/upload$"), handler)
            try:
                upload_ui("probe-fail.txt", "text/plain", b"fail")
                page.locator(".el-message--error").first.wait_for(state="visible", timeout=8000)
                page.wait_for_timeout(500)
            finally:
                page.unroute(re.compile(r"/api/assistant/upload$"), handler)
            assert page.locator(".attach-row").count() == 0, "上传失败后残留附件"
            assert page.locator(".input-row .el-button.is-loading").count() == 0, "上传失败后 loading 未复位"
            assert not recent_page_errors(), f"上传失败出现未捕获异常: {recent_page_errors()[-2:]}"
            bad = [e for e in recent_console_errors() if "Unhandled error" in e or "unhandled" in e.lower()]
            assert not bad, f"上传失败出现未处理 Promise 错误: {bad[:2]}"
            return "上传失败有全局错误提示、无附件残留、loading 复位、无未处理异常"
        record("CHAT-ATT-005", "上传失败反馈", tc_upload_fail_feedback)

        # ---------- 模型 chip / 专家选择器 ----------
        def tc_model_chip():
            clear_sessions()
            goto_assistant()
            chip = page.locator(".model-chip")
            chip.click()
            menu = page.locator(".el-dropdown-menu:visible")
            menu.wait_for(state="visible", timeout=5000)
            texts = menu.inner_text()
            assert "暂无模型，请到设置中添加" in texts, f"未配置模型下拉缺省份提示: {texts[:120]!r}"
            assert "供应商与模型管理" in texts, "下拉缺少设置入口"
            menu.locator(".el-dropdown-menu__item", has_text="供应商与模型管理").click()
            drawer = page.locator(".el-drawer:visible")
            drawer.wait_for(state="visible", timeout=5000)
            assert "AI 助手设置" in drawer.inner_text(), "设置抽屉未打开"
            drawer.locator(".el-drawer__close-btn").click()
            page.wait_for_timeout(500)
            assert page.locator(".el-drawer:visible").count() == 0, "设置抽屉未关闭"
            assert_no_app_errors("模型 chip")
            return "chip 显示未配置；下拉含暂无模型提示；设置入口可打开/关闭抽屉"
        record("CHAT-MODEL-001", "模型 chip 未配置摘要与设置入口", tc_model_chip)

        def tc_agent_selector():
            clear_sessions()
            goto_assistant()
            assert page.locator(".agent-item").count() == 9, "专家选择器应有智能路由 + 8 位专家"
            target = agent_item("信息化财务分析师")
            target.click()
            page.wait_for_timeout(250)
            assert "active" in (target.get_attribute("class") or ""), "选择专家后未高亮"
            assert "active" not in (agent_item("智能路由（推荐）").get_attribute("class") or ""), \
                "选择专家后智能路由仍高亮"
            target.click()
            page.wait_for_timeout(250)
            route = agent_item("智能路由（推荐）")
            assert "active" in (route.get_attribute("class") or ""), "再次点击未回落智能路由"
            assert_no_app_errors("专家选择器")
            return "专家项可选中/再次点击回落智能路由，高亮状态正确"
        record("CHAT-AGENT-001", "专家选择器", tc_agent_selector)

        # ---------- 接口边界 ----------
        def tc_api_sessions_contract():
            clear_sessions()
            assert api("GET", "/api/assistant/sessions", params={"archived": "bogus"}).status == 422
            assert api("GET", "/api/assistant/sessions", params={"limit": 0}).status == 422
            assert api("GET", "/api/assistant/sessions", params={"limit": 201}).status == 422
            assert api("GET", "/api/assistant/sessions", params={"limit": 200}).status == 200
            create_session("s_contract_active", "契约-进行中")
            create_session("s_contract_archived", "契约-已归档")
            api("POST", "/api/assistant/sessions/s_contract_archived/archive")
            active = api_body(api("GET", "/api/assistant/sessions", params={"archived": "false"}))["sessions"]
            archived = api_body(api("GET", "/api/assistant/sessions", params={"archived": "true"}))["sessions"]
            assert [s["session_id"] for s in active] == ["s_contract_active"], f"archived=false 过滤异常: {active}"
            assert [s["session_id"] for s in archived] == ["s_contract_archived"], f"archived=true 过滤异常: {archived}"
            create_session("job-contract-123", "内部任务会话")
            create_session("memory-extract", "记忆抽取会话")
            all_rows = api_body(api("GET", "/api/assistant/sessions"))["sessions"]
            ids = {s["session_id"] for s in all_rows}
            assert "job-contract-123" not in ids and "memory-extract" not in ids, "内部会话泄露到列表"
            assert api("POST", "/api/assistant/sessions/sess-missing/archive").status == 404
            assert api("POST", "/api/assistant/sessions/sess-missing/unarchive").status == 404
            assert api("GET", "/api/assistant/sessions/sess-missing/export").status == 404
            assert api("GET", "/api/assistant/sessions/bad%20id!/export").status == 400
            assert api("GET", "/api/assistant/history/bad%20id!").status == 400
            assert api("GET", "/api/assistant/history/sess-missing", params={"limit": 0}).status == 422
            assert api("GET", "/api/assistant/history/sess-missing", params={"limit": 201}).status == 422
            body = api_body(api("GET", "/api/assistant/history/sess-missing"))
            assert body["messages"] == [], "不存在会话历史应返回空列表"
            assert api("DELETE", "/api/assistant/sessions/sess-missing").status == 200, "删除不存在会话应幂等 200"
            assert_no_app_errors("会话契约")
            return "archived 过滤/limit 边界/内部会话隐藏/404/400/空历史均符合契约"
        record("CHAT-API-001", "会话接口边界", tc_api_sessions_contract)

        def tc_api_session_id_charset():
            clear_sessions()
            r = api("POST", "/api/assistant/sessions", json={"session_id": "bad id!", "title": "非法ID"})
            assert r.status == 400, f"创建非法 session_id 应 400，实际 {r.status}"
            assert "非法的 session_id" in (api_body(r).get("detail") or ""), "非法 session_id detail 缺失"
            rows = api_body(api("GET", "/api/assistant/sessions"))["sessions"]
            assert all(s["session_id"] != "bad id!" for s in rows), "非法会话仍落库"
            assert_no_app_errors("session_id 字符集")
            return "创建会话与聊天接口统一校验 session_id 字符集（400），非法 ID 不入库"
        record("CHAT-API-002", "session_id 字符集校验", tc_api_session_id_charset)

        # ---------- 失败反馈 / 无未捕获异常 ----------
        def tc_archive_fail_feedback():
            clear_sessions()
            create_session("s_archive_fail", "归档失败-探测")
            goto_assistant()
            clear_toasts()
            handler = lambda r: r.abort()
            page.route(re.compile(r"/api/assistant/sessions/[^/]+/archive$"), handler)
            try:
                session_action("归档失败-探测", "归档")
                page.locator(".el-message--error").first.wait_for(state="visible", timeout=8000)
                page.wait_for_timeout(600)
            finally:
                page.unroute(re.compile(r"/api/assistant/sessions/[^/]+/archive$"), handler)
            assert session_item("归档失败-探测").count() == 1, "归档失败后会话从列表消失"
            body = api_body(api("GET", "/api/assistant/sessions"))
            assert body["sessions"][0]["archived"] is False, "归档失败却写入 archived"
            assert not recent_page_errors(), f"归档失败出现未捕获异常: {recent_page_errors()[-2:]}"
            bad = [e for e in recent_console_errors() if "Unhandled error" in e or "unhandled" in e.lower()]
            assert not bad, f"归档失败出现未处理 Promise 错误: {bad[:2]}"
            return "归档失败：错误提示、会话保留、archived 不变、无未处理异常"
        record("CHAT-ERR-001", "归档失败反馈", tc_archive_fail_feedback)

        def tc_delete_fail_feedback():
            clear_sessions()
            create_session("s_delete_fail", "删除失败-探测")
            seed_messages("s_delete_fail", [
                ("user", "", "删除失败前的问题", "", []),
                ("assistant", "it_finance", "删除失败前的回答", "", []),
            ])
            goto_assistant()
            assert page.locator(".msg-row").count() == 2, "前置：历史消息未加载"
            clear_toasts()
            handler = lambda r: r.abort() if r.request.method == "DELETE" else r.continue_()
            page.route(re.compile(r"/api/assistant/sessions/[^/]+$"), handler)
            try:
                session_action("删除失败-探测", "删除")
                confirm_box("确定")
                page.locator(".el-message--error").first.wait_for(state="visible", timeout=8000)
                page.wait_for_timeout(600)
            finally:
                page.unroute(re.compile(r"/api/assistant/sessions/[^/]+$"), handler)
            assert session_item("删除失败-探测").count() == 1, "删除失败后会话从列表消失"
            assert page.locator(".msg-row").count() == 2, "删除失败后当前聊天被清空（状态损坏）"
            assert page.locator(".session-item.active").count() == 1, "删除失败后当前会话选中被清空"
            body = api_body(api("GET", "/api/assistant/sessions"))
            assert any(s["session_id"] == "s_delete_fail" for s in body["sessions"]), "删除失败但后端会话消失"
            assert not recent_page_errors(), f"删除失败出现未捕获异常: {recent_page_errors()[-2:]}"
            bad = [e for e in recent_console_errors() if "Unhandled error" in e or "unhandled" in e.lower()]
            assert not bad, f"删除失败出现未处理 Promise 错误: {bad[:2]}"
            return "删除失败：错误提示、列表/聊天/选中均保留、后端会话仍在、无未处理异常"
        record("CHAT-ERR-002", "删除当前会话失败反馈", tc_delete_fail_feedback)

        def tc_session_list_fail_feedback():
            clear_sessions()
            create_session("s_list_fail", "列表失败-探测")
            goto_assistant()
            clear_toasts()
            handler = lambda r: r.abort() if r.request.method == "GET" else r.continue_()
            page.route(re.compile(r"/api/assistant/sessions\?.*$"), handler)
            try:
                page.reload(wait_until="networkidle", timeout=30000)
                page.locator(".el-message--error").first.wait_for(state="visible", timeout=8000)
                page.wait_for_timeout(500)
            finally:
                page.unroute(re.compile(r"/api/assistant/sessions\?.*$"), handler)
            assert "暂无会话" in page.locator(".sessions-card .el-empty__description").inner_text(), "列表失败后未显示空态"
            assert not recent_page_errors(), f"会话列表失败出现未捕获异常: {recent_page_errors()[-2:]}"
            return "会话列表加载失败有全局错误提示、空态兜底、无未捕获异常"
        record("CHAT-ERR-003", "会话列表加载失败反馈", tc_session_list_fail_feedback)

        # ---------- 汇总前检查 ----------
        def tc_no_uncaught():
            bad = app_console_errors()
            assert not bad, f"全流程存在控制台应用错误: {bad[-5:]}"
            assert not page_errors, f"全流程存在未捕获异常: {page_errors[-5:]}"
            return f"控制台应用错误 0；未捕获异常 0（已过滤浏览器 4xx/abort 资源噪音）"
        record("CHAT-PAGE-003", "全流程无未捕获异常", tc_no_uncaught)

        browser.close()

    # ---------- 汇总输出 ----------
    pages_entry = {
        "route": "/assistant",
        "name": "assistant-chat",
        "status": 200,
        "console_errors": sorted({e for e in console_errors if not is_noise_console(e)}),
        "page_errors": sorted(set(page_errors)),
        "failed_reqs": [],
    }
    pages = [pages_entry]

    passed = sum(1 for r in results if r["passed"])
    print("=" * 70)
    print(f"total={len(results)} passed={passed} failed={len(results) - passed}")
    print(f"console_errors={len(pages_entry['console_errors'])} page_errors={len(page_errors)}")
    for e in pages_entry["console_errors"][:10]:
        print(f"  !! console: {e[:200]}")
    for e in pages_entry["page_errors"][:10]:
        print(f"  !! pageerror: {e[:200]}")
    print("=" * 70)

    raw = {
        "area": AREA,
        "base": BASE,
        "started_at": STARTED_AT,
        "finished_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "pages": pages,
        "tests": results,
    }
    (RESULTS / f"{AREA}.json").write_text(
        json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"report -> {RESULTS / (AREA + '.json')}")

    sys.exit(0 if passed == len(results) else 1)
finally:
    server.terminate()
    try:
        server.wait(timeout=10)
    except Exception:
        server.kill()
    log_fh.close()
