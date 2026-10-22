# -*- coding: utf-8 -*-
"""MCP 区域深度 E2E 探测（端口 8208，独立库隔离）。

覆盖：服务器 CRUD、env JSON 校验（非法 JSON / 非对象）、编辑保存不清空 env
（上轮修复点回归）、连接/断开（假命令优雅报错、进程秒退）、测试调用面板
（正常 / 参数非法 JSON / 未连接 503 / 参数为数组）、删除确认（取消/确定/
行已过期）、启用开关、超长名称、XSS 名称渲染、args 空格往返。

复用模式：Playwright sync API + hash 路由 + 自起 uvicorn（独立 SQLite）。
运行：系统 python 执行；结束时自动 terminate 服务器。
"""
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[3]  # dev/e2e/probes/mcp.py → 仓库根
BACKEND = ROOT / "backend"
RESULTS = ROOT / "dev" / "e2e" / "probe_results" / "mcp"
SHOTS = RESULTS / "shots"
DB = RESULTS / "mcp.db"
FIN_DIR = RESULTS / "mcp-fin"
DEBUG_DIR = RESULTS / "mcp-debug"
SERVER_LOG = ROOT / "dev" / "e2e" / "probe_results" / "mcp.server.log"
PORT = 8208
BASE = f"http://127.0.0.1:{PORT}"
PY = str(BACKEND / "venv" / "Scripts" / "python.exe")
STARTED_AT = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

# 探测中人工植入的 bug 清单（随修复进展维护；JSON 输出用）
BUGS = [
    # {
    #   "severity": "P2", "title": "...", "page": "/mcp",
    #   "description": "...", "status": "fixed|reported|设计存疑",
    #   "fix_evidence": "...", "test_evidence": "...",
    # },
]

FAKE_SERVER = '''# -*- coding: utf-8 -*-
"""探测用假 MCP stdio 服务器：echo + env_dump 两个工具。"""
import sys, json, os
sys.stdin.reconfigure(encoding="utf-8")
sys.stdout.reconfigure(encoding="utf-8")
def send(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\\n")
    sys.stdout.flush()
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    msg = json.loads(line)
    mid, method = msg.get("id"), msg.get("method")
    if method == "initialize":
        send({"jsonrpc": "2.0", "id": mid, "result": {
            "protocolVersion": "2024-11-05", "capabilities": {},
            "serverInfo": {"name": "probe-fake", "version": "0.1"}}})
    elif method == "tools/list":
        send({"jsonrpc": "2.0", "id": mid, "result": {"tools": [
            {"name": "echo", "description": "回声工具",
             "inputSchema": {"type": "object",
                             "properties": {"text": {"type": "string", "description": "文本"}},
                             "required": ["text"]}},
            {"name": "env_dump", "description": "读取自身环境变量",
             "inputSchema": {"type": "object",
                             "properties": {"key": {"type": "string", "description": "变量名"}},
                             "required": ["key"]}}]}})
    elif method == "tools/call":
        params = msg.get("params") or {}
        name = params.get("name")
        args = params.get("arguments") or {}
        if name == "echo":
            text = "echo:" + str(args.get("text", ""))
        elif name == "env_dump":
            text = os.environ.get(str(args.get("key", "")), "<unset>")
        else:
            send({"jsonrpc": "2.0", "id": mid,
                  "error": {"code": -32601, "message": "unknown tool"}})
            continue
        send({"jsonrpc": "2.0", "id": mid,
              "result": {"content": [{"type": "text", "text": text}], "isError": False}})
    elif isinstance(method, str) and method.startswith("notifications/"):
        pass
'''

DEAD_SERVER = '''# -*- coding: utf-8 -*-
"""握手前即退出的假服务器：验证连接失败路径。"""
import sys
sys.exit(3)
'''


def port_busy() -> bool:
    try:
        urllib.request.urlopen(f"{BASE}/api/dashboard/summary", timeout=2)
        return True
    except urllib.error.HTTPError:
        return True  # 有响应即说明端口被占
    except Exception:
        return False


if port_busy():
    print(f"FATAL: 端口 {PORT} 已被占用（疑似上次泄漏的服务器），请先清理")
    sys.exit(2)

for p in (DB,):
    if p.exists():
        p.unlink()
if FIN_DIR.exists():
    shutil.rmtree(FIN_DIR)
if DEBUG_DIR.exists():
    shutil.rmtree(DEBUG_DIR)
if SERVER_LOG.exists():
    SERVER_LOG.unlink()
SHOTS.mkdir(parents=True, exist_ok=True)
(RESULTS / "fake_server.py").write_text(FAKE_SERVER, encoding="utf-8")
(RESULTS / "dead_server.py").write_text(DEAD_SERVER, encoding="utf-8")

env = os.environ.copy()
env["DATABASE_URL"] = f"sqlite:///{DB.as_posix()}"
env["FINANCE_DATA_DIR"] = str(FIN_DIR)
env["DEBUG_DATA_DIR"] = str(DEBUG_DIR)
env["PYTHONIOENCODING"] = "utf-8"
env["SEED_ON_STARTUP"] = "0"

logf = open(SERVER_LOG, "w", encoding="utf-8", buffering=1)
server = subprocess.Popen(
    [PY, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(PORT)],
    cwd=str(BACKEND), env=env, stdout=logf, stderr=subprocess.STDOUT,
)

results = []  # (id, name, passed, detail)
page_errors = []
console_errors = []
failed_reqs = []
pages_report = []


def record(tid, name, fn):
    n_err = len(page_errors)
    # 清场上一条用例残留的 toast（默认 3s 自动消失），保证本条只匹配新提示
    t0 = time.time()
    while time.time() - t0 < 5:
        if page.locator(".el-message").count() == 0:
            break
        page.wait_for_timeout(200)
    try:
        fn()
        results.append((tid, name, True, ""))
        print(f"[PASS] {tid} {name}")
    except Exception as e:
        detail = str(e)[:400]
        results.append((tid, name, False, detail))
        print(f"[FAIL] {tid} {name}: {detail}")
        try:
            page.screenshot(path=str(SHOTS / f"{tid}.png"))
        except Exception:
            pass
    finally:
        if len(page_errors) > n_err:
            print(f"      !! 新增未捕获异常: {page_errors[-1][:200]}")
        # 失败现场清理：关掉可能残留的对话框/确认框，避免级联影响后续用例
        try:
            if page.locator(".el-message-box:visible").count() > 0:
                page.keyboard.press("Escape")
                page.wait_for_timeout(300)
            if dlg().count() > 0:
                page.keyboard.press("Escape")
                page.wait_for_timeout(300)
        except Exception:
            pass


def wait_ready(timeout=90):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            urllib.request.urlopen(f"{BASE}/api/dashboard/summary", timeout=2)
            return True
        except Exception:
            time.sleep(1)
    return False


def api(method, path, body=None, timeout=60):
    req = urllib.request.Request(
        BASE + "/api" + path, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except Exception:
            return e.code, {}


def db_row(name):
    """直接读探测库，验证 env/args/enabled 真实落库（列表接口 env 已脱敏）。"""
    con = sqlite3.connect(DB)
    try:
        row = con.execute(
            "SELECT env, args, enabled, status FROM mcp_servers WHERE name=?",
            (name,),
        ).fetchone()
    finally:
        con.close()
    if row is None:
        return None
    return {"env": json.loads(row[0] or "{}"), "args": json.loads(row[1] or "[]"),
            "enabled": bool(row[2]), "status": row[3]}


page = None


def goto(route):
    page.goto(f"{BASE}/#{route}", wait_until="networkidle", timeout=30000)
    page.wait_for_timeout(600)


def reload_mcp():
    """API 层造完数据后刷新页面（SPA 同路由导航不重置组件状态）。"""
    page.reload(wait_until="networkidle")
    page.wait_for_timeout(600)


def dlg():
    return page.locator(".el-dialog:visible")


def wait_cond(fn, timeout=8000, step=100, what=""):
    t0 = time.time()
    while time.time() - t0 < timeout / 1000:
        if fn():
            return True
        page.wait_for_timeout(step)
    raise AssertionError(f"等待超时: {what or fn}")


def wait_toast(text, timeout=6000):
    def _():
        return page.locator(".el-message__content", has_text=text).count() > 0
    wait_cond(_, timeout, what=f"toast「{text}」")


def toast_texts():
    return [t.inner_text() for t in page.locator(".el-message__content").all()]


def row_by_name(name):
    return page.locator("tr", has_text=name).first


def open_create():
    # 防御：若已有对话框/确认框残留（上一条用例异常留下），先关掉再开
    if dlg().count() > 0:
        page.keyboard.press("Escape")
        page.wait_for_timeout(400)
    if page.locator(".el-message-box:visible").count() > 0:
        page.locator(".el-message-box button", has_text="取消").first.click()
        page.wait_for_timeout(300)
    page.locator("button", has_text="新增服务器").first.click()
    wait_cond(lambda: dlg().count() > 0, what="新增对话框打开")
    page.wait_for_timeout(350)  # 等 dialog-fade 过渡完成再操作


def name_input():
    return dlg().locator("input[placeholder*='工具前缀']")


def cmd_input():
    return dlg().locator("input[placeholder*='如 npx']")


def args_input():
    return dlg().locator("input[placeholder*='空格分隔']")


def env_input():
    return dlg().locator("textarea").first


def save_dialog():
    dlg().locator("button", has_text="保存").first.click()
    wait_cond(lambda: dlg().count() == 0, timeout=8000, what="对话框关闭")


def cancel_dialog():
    dlg().locator("button", has_text="取消").first.click()
    wait_cond(lambda: dlg().count() == 0, timeout=5000, what="对话框关闭")


def call_tool_ui(server_name, tool, arguments, timeout=30000):
    """经「测试调用」面板调用工具，返回结果文本。"""
    row_by_name(server_name).locator("button", has_text="测试调用").click()
    wait_cond(lambda: dlg().count() > 0, what="测试调用对话框打开")
    dlg().locator(".el-select").first.click()
    page.wait_for_timeout(300)
    page.locator(".el-select-dropdown:visible .el-select-dropdown__item",
                 has_text=tool).first.click()
    page.wait_for_timeout(200)
    dlg().locator("textarea").first.fill(arguments)
    dlg().locator("button", has_text="调用").first.click()
    t0 = time.time()
    while time.time() - t0 < timeout / 1000:
        txt = dlg().locator("pre.result").inner_text() if dlg().locator("pre.result").count() else ""
        if txt:
            break
        page.wait_for_timeout(200)
    else:
        txt = ""
    dlg().locator("button", has_text="取消").first.click()  # 收尾关闭，避免遮挡后续操作
    page.wait_for_timeout(300)
    return txt


def create_server_ui(name, command, args_text, env_text):
    open_create()
    name_input().fill(name)
    cmd_input().fill(command)
    args_input().fill(args_text)
    if env_text is not None:
        env_input().fill(env_text)
    save_dialog()


def main():
    global page
    if not wait_ready():
        print("FATAL: server not ready")
        print(open(SERVER_LOG, encoding="utf-8", errors="replace").read()[-2000:])
        sys.exit(2)

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: page_errors.append(str(e)))
        page.on("requestfailed", lambda r: failed_reqs.append(f"{r.method} {r.url}"))

        fake = str(RESULTS / "fake_server.py")
        dead = str(RESULTS / "dead_server.py")

        # ---------- PAGE-01 /mcp 页面侦察 ----------
        def tc_page_mcp():
            goto("/mcp")
            page.screenshot(path=str(SHOTS / "mcp.png"))
            assert page.locator("button", has_text="新增服务器").count() > 0, "缺少新增按钮"
            assert page.locator("text=MCP（Model Context Protocol）").count() > 0, "缺少说明 alert"
            assert page.locator("text=暂无服务器").count() > 0, "空表未渲染"
            status = 200
            pages_report.append({
                "route": "/mcp", "name": "mcp", "status": status,
                "console_errors": [], "page_errors": list(page_errors),
                "failed_reqs": [f for f in failed_reqs if "favicon" not in f],
            })
        record("PAGE-01", "/mcp 页面加载", tc_page_mcp)

        # ---------- TC-MCP-001 创建服务器（合法 env JSON） ----------
        def tc_create_ok():
            create_server_ui("faketest", PY, fake, '{"FAKE_TOKEN":"secret-xyz"}')
            row = row_by_name("faketest")
            assert row.count() > 0, "列表未出现新服务器"
            assert "未连接" in row.inner_text(), f"初始状态应为未连接: {row.inner_text()[:60]}"
            st, body = api("GET", "/mcp/servers")
            item = next((i for i in body["items"] if i["name"] == "faketest"), None)
            assert item, "API 列表缺少 faketest"
            assert item["env"] == {"FAKE_TOKEN": "***"}, f"env 未脱敏: {item['env']}"
            stored = db_row("faketest")["env"]
            assert set(stored) == {"FAKE_TOKEN"}, f"env 键未正确落库: {stored}"
            assert stored["FAKE_TOKEN"].startswith("dpapi:"), "env 未 DPAPI 密封"
            assert "secret-xyz" not in json.dumps(stored, ensure_ascii=False), "env 明文落库"
        record("TC-MCP-001", "创建服务器（合法 env JSON）", tc_create_ok)

        # ---------- TC-MCP-002 非法 env JSON 提示 ----------
        def tc_env_invalid_json():
            before = api("GET", "/mcp/servers")[1]["items"]
            open_create()
            name_input().fill("envbad")
            cmd_input().fill(PY)
            args_input().fill(fake)
            env_input().fill('{"A": oops')
            dlg().locator("button", has_text="保存").first.click()
            wait_toast("环境变量不是合法 JSON")
            wait_cond(lambda: dlg().count() > 0, what="对话框保持打开")
            after = api("GET", "/mcp/servers")[1]["items"]
            assert len(after) == len(before), "非法 JSON 也被提交入库"
            cancel_dialog()
        record("TC-MCP-002", "非法 env JSON 提示且不提交", tc_env_invalid_json)

        # ---------- TC-MCP-003 env 为 JSON 数组（BUG 候选： cryptic 422） ----------
        def tc_env_array():
            before = api("GET", "/mcp/servers")[1]["items"]
            open_create()
            name_input().fill("envarr")
            cmd_input().fill(PY)
            args_input().fill(fake)
            env_input().fill('["A","B"]')
            dlg().locator("button", has_text="保存").first.click()
            page.wait_for_timeout(1200)
            after = api("GET", "/mcp/servers")[1]["items"]
            texts = " ".join(toast_texts())
            friendly = ("JSON 对象" in texts) or ("合法 JSON" in texts)
            if len(after) == len(before) and dlg().count() > 0 and friendly:
                cancel_dialog()
                return  # 友好提示且未提交：符合预期
            raise AssertionError(
                f"数组 env 未得到友好拦截: created={len(after) - len(before)} "
                f"dialog_open={dlg().count() > 0} friendly={friendly} toasts={texts[:120]}")
        record("TC-MCP-003", "env 为数组时友好报错", tc_env_array)

        # ---------- TC-MCP-004 名称/命令必填 ----------
        def tc_required_fields():
            before = api("GET", "/mcp/servers")[1]["items"]
            open_create()
            cmd_input().fill(PY)
            dlg().locator("button", has_text="保存").first.click()
            wait_toast("名称与启动命令必填")
            assert dlg().count() > 0, "必填校验未拦截"
            after = api("GET", "/mcp/servers")[1]["items"]
            assert len(after) == len(before), "缺字段也被提交"
            cancel_dialog()
        record("TC-MCP-004", "名称/命令必填校验", tc_required_fields)

        # ---------- TC-MCP-005 重名 400 ----------
        def tc_duplicate_name():
            open_create()
            name_input().fill("faketest")
            cmd_input().fill(PY)
            args_input().fill(fake)
            dlg().locator("button", has_text="保存").first.click()
            wait_toast("已存在")
            assert dlg().count() > 0, "重名未保持对话框"
            cancel_dialog()
        record("TC-MCP-005", "重名创建被拒", tc_duplicate_name)

        # ---------- TC-MCP-006 连接假命令优雅报错 ----------
        def tc_connect_bad_command():
            create_server_ui("badcmd", "definitely-not-a-command-xyz", "", None)
            row = row_by_name("badcmd")
            row.locator("button", has_text="连接").click()
            wait_toast("命令不存在", timeout=30000)
            wait_cond(lambda: "错误" in row_by_name("badcmd").inner_text(), what="状态变为错误")
            page.wait_for_timeout(500)
            assert page.locator(".el-alert--error", has_text="badcmd").count() > 0, "缺少错误 alert"
            assert page.locator("textarea:visible").count() == 0, "页面异常"
        record("TC-MCP-006", "连接假命令优雅报错", tc_connect_bad_command)

        # ---------- TC-MCP-007 连接 faketest + env 送达子进程 ----------
        def tc_connect_fake():
            row = row_by_name("faketest")
            row.locator("button", has_text="连接").click()
            wait_toast("已连接，注册 2 个工具", timeout=30000)
            wait_cond(lambda: "已连接" in row_by_name("faketest").inner_text(), what="状态已连接")
            result = call_tool_ui("faketest", "env_dump", '{"key":"FAKE_TOKEN"}')
            assert "secret-xyz" in result, f"env 未送达子进程: {result[:120]}"
        record("TC-MCP-007", "连接成功且 env 送达子进程", tc_connect_fake)

        # ---------- TC-MCP-008 编辑只改命令，env 保留（上轮修复点回归） ----------
        def tc_edit_keeps_env():
            row = row_by_name("faketest")
            row.locator("button", has_text="编辑").click()
            wait_cond(lambda: dlg().count() > 0, what="编辑对话框打开")
            assert name_input().input_value() == "faketest"
            assert name_input().is_disabled(), "编辑时名称未禁用"
            # 只改命令（同解释器的正斜杠写法），不碰 env 文本框
            cmd_input().fill(PY.replace("\\", "/"))
            save_dialog()
            wait_cond(lambda: "未连接" in row_by_name("faketest").inner_text(),
                      what="保存后状态复位为未连接")
            row2 = row_by_name("faketest")
            row2.locator("button", has_text="连接").click()
            wait_toast("已连接，注册 2 个工具", timeout=30000)
            result = call_tool_ui("faketest", "env_dump", '{"key":"FAKE_TOKEN"}')
            assert "secret-xyz" in result, f"编辑保存后 env 被清空: {result[:120]}"
            row_db = db_row("faketest")
            stored = row_db["env"]
            assert set(stored) == {"FAKE_TOKEN"}, f"DB env 被清空: {stored}"
            assert stored["FAKE_TOKEN"].startswith("dpapi:"), "保留的 env 未保持密封"
            assert row_db["args"] == [fake], f"args 被破坏: {row_db['args']}"
        record("TC-MCP-008", "编辑只改命令 env 保留（上轮修复回归）", tc_edit_keeps_env)

        # ---------- TC-MCP-009 编辑改 env 生效 ----------
        def tc_edit_changes_env():
            row = row_by_name("faketest")
            row.locator("button", has_text="编辑").click()
            wait_cond(lambda: dlg().count() > 0, what="编辑对话框打开")
            env_input().fill('{"FAKE_TOKEN":"changed-token"}')
            save_dialog()
            row2 = row_by_name("faketest")
            row2.locator("button", has_text="连接").click()
            wait_toast("已连接，注册 2 个工具", timeout=30000)
            result = call_tool_ui("faketest", "env_dump", '{"key":"FAKE_TOKEN"}')
            assert "changed-token" in result, f"env 修改未生效: {result[:120]}"
        record("TC-MCP-009", "编辑修改 env 生效", tc_edit_changes_env)

        # ---------- TC-MCP-010 编辑清空 env（用户主动清空意图） ----------
        def tc_edit_clears_env():
            row = row_by_name("faketest")
            row.locator("button", has_text="编辑").click()
            wait_cond(lambda: dlg().count() > 0, what="编辑对话框打开")
            env_input().fill("")
            save_dialog()
            row2 = row_by_name("faketest")
            row2.locator("button", has_text="连接").click()
            wait_toast("已连接，注册 2 个工具", timeout=30000)
            result = call_tool_ui("faketest", "env_dump", '{"key":"FAKE_TOKEN"}')
            assert "<unset>" in result, f"清空 env 未生效: {result[:120]}"
            assert db_row("faketest")["env"] == {}, "DB env 未清空"
        record("TC-MCP-010", "编辑清空 env 生效", tc_edit_clears_env)

        # ---------- TC-MCP-011 断开连接 ----------
        def tc_disconnect():
            row = row_by_name("faketest")
            row.locator("button", has_text="断开").click()
            wait_toast("已断开")
            wait_cond(lambda: "未连接" in row_by_name("faketest").inner_text(), what="状态未连接")
            test_btn = row_by_name("faketest").locator("button", has_text="测试调用")
            assert test_btn.is_disabled(), "断开后测试调用仍可点"
        record("TC-MCP-011", "断开连接", tc_disconnect)

        # ---------- TC-MCP-012 未连接时测试调用（API 层 503） ----------
        def tc_test_call_disconnected():
            st, body = api("GET", "/mcp/servers")
            item = next(i for i in body["items"] if i["name"] == "faketest")
            st2, body2 = api("POST", f"/mcp/servers/{item['id']}/test-call",
                             {"tool": "echo", "arguments": {"text": "hi"}})
            assert st2 == 503, f"未连接应 503，实际 {st2}: {body2}"
            assert "未连接" in str(body2.get("detail")), f"503 文案不佳: {body2}"
        record("TC-MCP-012", "未连接测试调用 503", tc_test_call_disconnected)

        # ---------- TC-MCP-013 测试调用正常 + 参数非法 JSON ----------
        def tc_test_call_panel():
            row = row_by_name("faketest")
            row.locator("button", has_text="连接").click()
            wait_toast("已连接，注册 2 个工具", timeout=30000)
            result = call_tool_ui("faketest", "echo", '{"text":"你好"}')
            assert "echo:你好" in result, f"echo 调用失败: {result[:120]}"
            # 参数非法 JSON：应提示且不产生结果
            row_by_name("faketest").locator("button", has_text="测试调用").click()
            wait_cond(lambda: dlg().count() > 0, what="测试对话框打开")
            dlg().locator("textarea").first.fill('{"text": oops}')
            dlg().locator("button", has_text="调用").first.click()
            wait_toast("参数不是合法 JSON")
            assert dlg().locator("pre.result").count() == 0, "非法参数仍产生结果"
            dlg().locator("button", has_text="取消").first.click()
        record("TC-MCP-013", "测试调用面板（正常 + 参数非法 JSON）", tc_test_call_panel)

        # ---------- TC-MCP-014 测试调用参数为数组（BUG 候选：cryptic 422） ----------
        def tc_test_call_args_array():
            row_by_name("faketest").locator("button", has_text="测试调用").click()
            wait_cond(lambda: dlg().count() > 0, what="测试对话框打开")
            dlg().locator("textarea").first.fill('[1,2]')
            dlg().locator("button", has_text="调用").first.click()
            page.wait_for_timeout(1500)
            result = dlg().locator("pre.result").inner_text() if dlg().locator("pre.result").count() else ""
            texts = " ".join(toast_texts())
            friendly = ("JSON 对象" in result) or ("合法 JSON" in result) \
                or ("JSON 对象" in texts) or ("合法 JSON" in texts)
            dlg().locator("button", has_text="取消").first.click()
            page.wait_for_timeout(300)
            if friendly:
                return  # 友好提示（toast 或结果面板）：符合预期
            raise AssertionError(f"数组参数未友好拦截: result={result[:100]!r} toasts={texts[:100]!r}")
        record("TC-MCP-014", "测试调用参数为数组时友好报错", tc_test_call_args_array)

        # ---------- TC-MCP-015 删除确认-取消 ----------
        def tc_delete_cancel():
            row = row_by_name("faketest")
            row.locator("button", has_text="删除").click()
            wait_cond(lambda: page.locator(".el-message-box:visible").count() > 0, what="确认框出现")
            page.locator(".el-message-box button", has_text="取消").first.click()
            page.wait_for_timeout(500)
            assert row_by_name("faketest").count() > 0, "取消删除后行消失"
        record("TC-MCP-015", "删除确认-取消保留", tc_delete_cancel)

        # ---------- TC-MCP-016 删除确认-确定 ----------
        def tc_delete_confirm():
            row = row_by_name("faketest")
            row.locator("button", has_text="删除").click()
            wait_cond(lambda: page.locator(".el-message-box:visible").count() > 0, what="确认框出现")
            page.locator(".el-message-box button", has_text="确定").first.click()
            wait_toast("已删除")
            wait_cond(lambda: row_by_name("faketest").count() == 0, what="行消失")
            names = [i["name"] for i in api("GET", "/mcp/servers")[1]["items"]]
            assert "faketest" not in names, "API 层仍存在"
        record("TC-MCP-016", "删除确认-确定删除", tc_delete_confirm)

        # ---------- TC-MCP-017 删除已过期的行（BUG 候选：uncaught rejection） ----------
        def tc_delete_stale_row():
            create_server_ui("stalesrv", PY, fake, None)
            st, body = api("GET", "/mcp/servers")
            sid = next(i["id"] for i in body["items"] if i["name"] == "stalesrv")
            api("DELETE", f"/mcp/servers/{sid}")  # 接口层先删掉，UI 行已过期
            n_err = len(page_errors)
            row = row_by_name("stalesrv")
            row.locator("button", has_text="删除").click()
            wait_cond(lambda: page.locator(".el-message-box:visible").count() > 0, what="确认框出现")
            page.locator(".el-message-box button", has_text="确定").first.click()
            page.wait_for_timeout(1500)
            texts = " ".join(toast_texts())
            assert "服务器不存在" in texts, f"未出现 404 提示: {texts[:120]}"
            new_errs = page_errors[n_err:]
            assert not new_errs, f"出现未捕获异常: {new_errs[:1]}"
            # 清理：行还在（删除失败），再从 UI 删一次（此时应真正不存在→无需）
            if row_by_name("stalesrv").count() > 0:
                api("DELETE", f"/mcp/servers/{sid}")
        record("TC-MCP-017", "删除已过期行无未捕获异常", tc_delete_stale_row)

        # ---------- TC-MCP-018 启用开关 ----------
        def tc_toggle_enabled():
            create_server_ui("togglesrv", PY, fake, None)
            row = row_by_name("togglesrv")
            row.locator(".el-switch").click()
            wait_toast("已停用")
            assert db_row("togglesrv")["enabled"] is False, "停用未落库"
            row = row_by_name("togglesrv")
            row.locator(".el-switch").click()
            wait_toast("已启用")
            assert db_row("togglesrv")["enabled"] is True, "启用未落库"
        record("TC-MCP-018", "启用/停用开关", tc_toggle_enabled)

        # ---------- TC-MCP-019 进程秒退的服务器连接 ----------
        def tc_connect_dead_server():
            create_server_ui("deadsrv", PY, dead, None)
            row = row_by_name("deadsrv")
            row.locator("button", has_text="连接").click()
            wait_toast("输出流", timeout=30000)
            wait_cond(lambda: "错误" in row_by_name("deadsrv").inner_text(), what="状态变为错误")
            assert page.locator(".el-alert--error", has_text="deadsrv").count() > 0, "缺少错误 alert"
        record("TC-MCP-019", "进程秒退服务器连接优雅报错", tc_connect_dead_server)

        # ---------- TC-MCP-020 超长名称（422 边界） ----------
        def tc_long_name():
            long_name = "n" * 60
            open_create()
            name_input().fill(long_name)
            cmd_input().fill(PY)
            args_input().fill(fake)
            dlg().locator("button", has_text="保存").first.click()
            page.wait_for_timeout(1200)
            texts = " ".join(toast_texts())
            names = [i["name"] for i in api("GET", "/mcp/servers")[1]["items"]]
            assert long_name not in names, "超长名称被接受"
            assert dlg().count() > 0, "超长名称提交后对话框被关闭"
            assert texts.strip(), "超长名称无任何提示"
            cancel_dialog()
        record("TC-MCP-020", "超长名称被拒且有提示", tc_long_name)

        # ---------- TC-MCP-021 args 空格往返（BUG 候选：静默损坏） ----------
        def tc_args_spaces_roundtrip():
            spaced = ["-y", "@modelcontextprotocol/server-filesystem", "D:\\my docs\\root"]
            st, body = api("POST", "/mcp/servers", {
                "name": "spacesrv", "command": PY, "args": spaced,
                "env": {}, "enabled": True})
            assert st == 201, f"API 创建失败: {body}"
            reload_mcp()
            row = row_by_name("spacesrv")
            row.locator("button", has_text="编辑").click()
            wait_cond(lambda: dlg().count() > 0, what="编辑对话框打开")
            shown = args_input().input_value()
            # 不改 args，只改命令，模拟用户例行编辑
            cmd_input().fill(PY.replace("\\", "/"))
            save_dialog()
            stored = db_row("spacesrv")
            if stored["args"] == spaced:
                api("DELETE", "/mcp/servers/" + str(
                    next(i["id"] for i in api("GET", "/mcp/servers")[1]["items"]
                         if i["name"] == "spacesrv")))
                return  # 往返无损：符合预期
            raise AssertionError(
                f"args 空格往返损坏: shown={shown!r} -> stored={stored['args']!r}")
        record("TC-MCP-021", "args 含空格编辑往返", tc_args_spaces_roundtrip)

        # ---------- TC-MCP-022 XSS 名称渲染 ----------
        def tc_xss_name():
            evil = 'xss<img src=x onerror=window.__xss=1>'
            st, body = api("POST", "/mcp/servers", {
                "name": evil, "command": PY, "args": [fake], "env": {}, "enabled": True})
            assert st in (201, 400), f"意外状态 {st}: {body}"
            if st == 400:
                return  # 后端拒绝恶意名称也可接受
            reload_mcp()
            page.wait_for_timeout(800)
            assert page.evaluate("() => window.__xss") is None, "XSS 被执行"
            assert page.locator("img[src='x']").count() == 0, "名称未转义渲染"
            row = row_by_name("xss<img")
            assert row.count() > 0 and "xss<img" in row.inner_text(), "名称未按文本渲染"
            sid = next(i["id"] for i in api("GET", "/mcp/servers")[1]["items"]
                       if i["name"] == evil)
            api("DELETE", f"/mcp/servers/{sid}")
        record("TC-MCP-022", "恶意名称转义渲染", tc_xss_name)

        # ---------- 收尾：汇总控制台错误 ----------
        def tc_no_console_errors():
            # "Failed to load resource" 是负向用例（400/404/422/503）的浏览器资源日志，
            # 属预期行为；这里只拦截真正的 JS 异常类控制台错误
            real = [e for e in console_errors if "Failed to load resource" not in e]
            assert not real, f"控制台错误: {real[:3]}"
        record("TC-MCP-023", "全程无 JS 类控制台错误", tc_no_console_errors)

        browser.close()

    passed = sum(1 for r in results if r[2])
    print("=" * 70)
    print(f"total={len(results)} passed={passed} failed={len(results) - passed}")
    uniq_console = sorted(set(console_errors))
    uniq_page = sorted(set(page_errors))
    print(f"console_errors={len(uniq_console)} page_errors={len(uniq_page)}")
    for e in (uniq_console + uniq_page)[:10]:
        print(f"  !! {e[:200]}")

    out = {
        "area": "mcp",
        "base": BASE,
        "started_at": STARTED_AT,
        "finished_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "pages": pages_report,
        "tests": [{"id": r[0], "name": r[1], "passed": r[2], "detail": r[3]} for r in results],
        "console_errors": uniq_console,
        "page_errors": uniq_page,
        "bugs": BUGS,
    }
    (ROOT / "dev" / "e2e" / "probe_results" / "mcp.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"JSON -> dev/e2e/probe_results/mcp.json")
    return 0 if passed == len(results) else 1


try:
    CODE = main()
finally:
    server.terminate()
    try:
        server.wait(timeout=10)
    except Exception:
        server.kill()
    logf.close()

sys.exit(CODE)
