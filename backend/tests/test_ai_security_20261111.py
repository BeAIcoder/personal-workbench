"""T2C（2026-11-11 全量整改）：AI 安全与 MCP 可靠性回归。

覆盖四个缺陷：
- SCH-01：定时任务 ``mode`` 入参白名单（非法值 422 且不落库）；
- PRB-01：模型探测总时长预算（挂起厂商响应及时收尾、单请求超时受剩余预算约束）；
- MCP-01/L-028：入站行大小上限 + 有界指数退避自动重连与工具注册表刷新；
- SEC-01：旧 ``agent_settings.api_key`` 只做非空计数后清除（幂等、不读取值、不打印）。

全部用例使用临时测试库与合成数据：不访问真实厂商、不启动真实 MCP 服务器、
不写真实 ``backend/data``。
"""
import asyncio
import io
import json
import logging
import sys
import textwrap
import threading
import time

import pytest
from sqlalchemy import event

from app.agents import mcp as mcp_mod
from app.agents import model_probe, secrets_box
from app.agents.secrets_box import seal
from app.agents.tools import TOOL_SPECS, register_module_tools
from app.database import Base, SessionLocal, engine
from app.models import AgentSettings, McpServer, ModelProvider, ProviderModel


@pytest.fixture(autouse=True)
def _ensure_tables():
    """非 client 用例也保证表存在（conftest 对 client 用例 teardown 只 drop 不重建）。"""
    Base.metadata.create_all(bind=engine)
    yield


def _job_payload(**kw):
    payload = {
        "name": "模式白名单任务",
        "prompt": "汇总今日任务",
        "schedule_type": "interval",
        "interval_minutes": 60,
    }
    payload.update(kw)
    return payload


def _mk_model_row(context_size=128000):
    with SessionLocal() as db:
        prov = ModelProvider(
            name="合成探测供应商",
            protocol="openai",
            base_url="https://api.example.invalid/v1",
            api_key=seal("sk-synthetic-probe"),
        )
        db.add(prov)
        db.flush()
        pm = ProviderModel(provider_id=prov.id, model="synthetic-model", context_size=context_size, max_tokens=4096)
        db.add(pm)
        db.commit()
        return pm.id


def _probe_error(error="ConnectError: 合成不可达"):
    return {
        "ok": False,
        "status_code": None,
        "error": error,
        "content": "",
        "reasoning_content": "",
        "finish_reason": None,
        "usage": None,
    }


# ---------- SCH-01：mode 入参白名单 ----------

@pytest.mark.parametrize("bad_mode", ["yolo", "", "READONLY", "readonly ", "readonly; rm -rf /", "deep\n"])
def test_job_create_rejects_mode_outside_whitelist(client, bad_mode):
    resp = client.post("/api/jobs", json=_job_payload(mode=bad_mode))
    assert resp.status_code == 422, resp.text
    assert client.get("/api/jobs").json() == [], "非法 mode 不得落库"


@pytest.mark.parametrize("good_mode", ["readonly", "standard", "deep"])
def test_job_create_accepts_whitelisted_mode(client, good_mode):
    resp = client.post("/api/jobs", json=_job_payload(mode=good_mode))
    assert resp.status_code == 201, resp.text
    assert resp.json()["mode"] == good_mode


def test_job_update_rejects_mode_outside_whitelist(client):
    job = client.post("/api/jobs", json=_job_payload(mode="readonly")).json()
    resp = client.put(f"/api/jobs/{job['id']}", json={"mode": "yolo"})
    assert resp.status_code == 422, resp.text
    assert client.get("/api/jobs").json()[0]["mode"] == "readonly", "422 后库内 mode 不得被改写"

    ok = client.put(f"/api/jobs/{job['id']}", json={"mode": "deep"})
    assert ok.status_code == 200
    assert ok.json()["mode"] == "deep"


# ---------- PRB-01：探测总时长预算 ----------

def test_probe_requests_carry_budget_bounded_timeout(client, monkeypatch):
    """每次厂商请求的 timeout 必须来自总预算（不得沿用固定 30s）。"""
    mid = _mk_model_row()
    seen_timeouts = []

    def fake_chat(base_url, api_key, model, messages, max_tokens, reasoning_effort=None, extra=None, timeout=30):
        seen_timeouts.append(timeout)
        return _probe_error()

    monkeypatch.setattr(model_probe, "_chat_once", fake_chat)
    monkeypatch.setattr(model_probe, "PROBE_TOTAL_BUDGET_SECONDS", 5.0, raising=False)
    monkeypatch.setattr(model_probe.time, "sleep", lambda _: None)

    with SessionLocal() as db:
        row = db.get(ProviderModel, mid)
        model_probe.probe_all(db, row)

    assert seen_timeouts, "探测未发出任何请求"
    assert max(seen_timeouts) <= 5.0, f"单请求超时 {max(seen_timeouts)}s 超出总预算 5s"
    assert all(t > 0 for t in seen_timeouts)


def test_probe_all_stops_within_total_budget_when_vendor_hangs(client, monkeypatch):
    """厂商每次都挂到超时：三段探测共享一个总预算，耗尽即停并注明原因。"""
    mid = _mk_model_row()
    calls = []

    def slow_send(*args, **kwargs):
        calls.append(kwargs.get("timeout"))
        # 不用 time.sleep：本用例 monkeypatch 了 model_probe.time.sleep，
        # 而 Python 的 time 是共享模块对象，会连带把测试自己的 sleep 也变成空操作。
        threading.Event().wait(0.35)
        return _probe_error("ReadTimeout: 合成挂起")

    monkeypatch.setattr(model_probe, "_send", slow_send)
    monkeypatch.setattr(model_probe, "PROBE_TOTAL_BUDGET_SECONDS", 1.0, raising=False)
    monkeypatch.setattr(model_probe.time, "sleep", lambda _: None)

    started = time.monotonic()
    with SessionLocal() as db:
        row = db.get(ProviderModel, mid)
        out = model_probe.probe_all(db, row)
    elapsed = time.monotonic() - started

    assert elapsed < 2.0, f"总预算 1s 未生效，探测耗时 {elapsed:.2f}s"
    assert len(calls) <= 3, f"预算耗尽后仍在请求厂商：{len(calls)} 次"
    assert "预算" in json.dumps(out, ensure_ascii=False), "预算耗尽未在结果中标注"


# ---------- MCP-01：入站行大小上限 ----------

class _FakeStdout:
    def __init__(self, text):
        self._io = io.StringIO(text)

    def readline(self, size=-1):  # noqa: D401 - 模拟 subprocess 文本管道
        return self._io.readline(size)


class _FakeProc:
    stdin = None

    def __init__(self, text):
        self.stdout = _FakeStdout(text)

    def poll(self):
        return None


def test_mcp_inbound_line_over_limit_is_rejected(monkeypatch):
    """服务器发一条超长单行：必须在限额处拒绝，不能整行读进内存。"""
    monkeypatch.setattr(mcp_mod, "MAX_INBOUND_LINE_CHARS", 64, raising=False)
    server = mcp_mod.McpStdioServer("oversized", "python")
    server.proc = _FakeProc("x" * 10000 + "\n")

    with pytest.raises(mcp_mod.McpError, match="入站"):
        server._read_line(timeout=1)


def test_mcp_inbound_line_within_limit_is_accepted(monkeypatch):
    monkeypatch.setattr(mcp_mod, "MAX_INBOUND_LINE_CHARS", 4096, raising=False)
    line = json.dumps({"jsonrpc": "2.0", "id": 1, "result": {}})
    server = mcp_mod.McpStdioServer("small", "python")
    server.proc = _FakeProc(line + "\n")

    assert server._read_line(timeout=1).strip() == line


# ---------- MCP-01/L-028：有界自动重连与工具注册表刷新 ----------

RECONNECT_SERVER = textwrap.dedent('''
    import json
    import pathlib
    import sys

    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")

    counter = pathlib.Path(sys.argv[1])
    try:
        starts = int(counter.read_text(encoding="utf-8"))
    except Exception:
        starts = 0
    counter.write_text(str(starts + 1), encoding="utf-8")

    def send(obj):
        sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\\n")
        sys.stdout.flush()

    def tool(name, description):
        return {"name": name, "description": description,
                "inputSchema": {"type": "object",
                                "properties": {"text": {"type": "string"}},
                                "required": ["text"]}}

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        msg = json.loads(line)
        mid, method = msg.get("id"), msg.get("method")
        if method == "initialize":
            send({"jsonrpc": "2.0", "id": mid, "result": {
                "protocolVersion": "2024-11-05", "capabilities": {},
                "serverInfo": {"name": "reconnect-fake", "version": "0.1"}}})
        elif method == "tools/list":
            tools = [tool("echo", "回声v1" if starts == 0 else "回声v2")]
            if starts > 0:
                tools.append(tool("extra", "重连后才出现的工具"))
            send({"jsonrpc": "2.0", "id": mid, "result": {"tools": tools}})
        elif method == "tools/call":
            args = (msg.get("params") or {}).get("arguments") or {}
            send({"jsonrpc": "2.0", "id": mid, "result": {
                "content": [{"type": "text", "text": "echo:" + str(args.get("text", ""))}],
                "isError": False}})
''')


def test_mcp_auto_reconnect_refreshes_tool_registry(client, tmp_path):
    script = tmp_path / "reconnect_mcp_server.py"
    script.write_text(RECONNECT_SERVER, encoding="utf-8")
    counter = tmp_path / "starts.txt"

    with SessionLocal() as db:
        db.add(McpServer(name="autorec", command=sys.executable,
                         args=[str(script), str(counter)], env={}, enabled=True))
        db.commit()

    server = mcp_mod.McpStdioServer("autorec", sys.executable, [str(script), str(counter)])
    server.start()
    mcp_mod._servers["autorec"] = server
    try:
        mcp_mod._register_server_tools(server, register_module_tools)
        assert "mcp_autorec__echo" in TOOL_SPECS
        assert "mcp_autorec__extra" not in TOOL_SPECS

        # 模拟进程崩溃：调用工具时应自动重连，而不是只报「请手动重连」
        server.proc.kill()
        server.proc.wait(timeout=5)
        assert not server.is_alive()

        result = asyncio.run(TOOL_SPECS["mcp_autorec__echo"]["func"](text="hi"))
        assert result["ok"] is True, result
        assert result["result"] == "echo:hi"
        assert server.is_alive(), "自动重连未恢复子进程"

        # 工具注册表随重连刷新：新增工具出现、旧描述更新
        assert "mcp_autorec__extra" in TOOL_SPECS, "重连后未刷新工具注册表"
        assert "回声v2" in TOOL_SPECS["mcp_autorec__echo"]["description"]

        with SessionLocal() as db:
            row = db.query(McpServer).filter(McpServer.name == "autorec").one()
            assert row.status == "connected"
            assert any(t.get("name") == "extra" for t in (row.tools_json or []))
    finally:
        mcp_mod._servers.pop("autorec", None)
        server.stop()
        for name in [n for n in TOOL_SPECS if n.startswith("mcp_autorec__")]:
            TOOL_SPECS.pop(name, None)


def test_mcp_auto_reconnect_attempts_are_bounded(client, monkeypatch):
    """重连失败必须有界：固定次数 + 指数退避，不无限重试；文案报告实际次数。"""
    monkeypatch.setattr(mcp_mod, "RECONNECT_MAX_ATTEMPTS", 3, raising=False)
    monkeypatch.setattr(mcp_mod, "RECONNECT_BASE_DELAY", 0.01, raising=False)
    monkeypatch.setattr(mcp_mod, "RECONNECT_MAX_DELAY", 0.02, raising=False)

    _seed_mcp_row("dead", enabled=True)
    server = mcp_mod.McpStdioServer("dead", "python")
    attempts = {"n": 0}

    def boom():
        attempts["n"] += 1
        raise mcp_mod.McpError("合成启动失败")

    server.start = boom
    server._ever_started = True
    mcp_mod._servers["dead"] = server
    try:
        started = time.monotonic()
        result = asyncio.run(mcp_mod._make_executor("dead", "echo")())
        elapsed = time.monotonic() - started
    finally:
        _cleanup_mcp("dead")

    assert attempts["n"] == 3, f"重连次数未受上限约束：{attempts['n']}"
    assert elapsed < 2.0, f"退避耗时超界：{elapsed:.2f}s"
    assert result["ok"] is False
    assert "已尝试 3 次" in result["error"]


# ---------- SEC-01：旧 agent_settings.api_key 清除迁移 ----------

def test_legacy_agent_settings_key_purged_idempotently(client, caplog):
    secret = "legacy-合成-key-9911"
    with SessionLocal() as db:
        row = db.get(AgentSettings, 1)
        if row is None:
            row = AgentSettings(id=1)
            db.add(row)
        row.api_key = secret
        db.add(AgentSettings(id=2, api_key="legacy-second-8822"))
        db.commit()

    captured = []

    def _capture(conn, cursor, statement, parameters, context, executemany):
        captured.append((statement, parameters))

    event.listen(engine, "before_cursor_execute", _capture)
    try:
        with caplog.at_level(logging.DEBUG):
            first = secrets_box.purge_legacy_agent_settings_keys()
            second = secrets_box.purge_legacy_agent_settings_keys()
    finally:
        event.remove(engine, "before_cursor_execute", _capture)

    assert first == 2
    assert second == 0, "迁移必须幂等：第二次没有可清除的 Key"
    with SessionLocal() as db:
        assert db.get(AgentSettings, 1).api_key == ""
        assert db.get(AgentSettings, 2).api_key == ""

    # 只做非空计数后 UPDATE 清除：不 SELECT 旧值，也不把值写进日志/参数
    statements = [stmt.lstrip().upper() for stmt, _ in captured]
    assert any(stmt.startswith("UPDATE") for stmt in statements)
    assert not any(stmt.startswith("SELECT") for stmt in statements), "迁移不得读取旧 Key 值"
    assert secret not in json.dumps([p for _, p in captured], ensure_ascii=False, default=str)
    assert secret not in caplog.text


def test_legacy_agent_settings_key_migrated_by_script_not_startup(client, monkeypatch, caplog):
    """启动不再写密钥列：一次性迁移只由 scripts/migrate.py secrets 触发（幂等）。"""
    from app import main as main_mod
    from app.bootstrap import legacy

    secret = "legacy-startup-key-7733"
    with SessionLocal() as db:
        row = db.get(AgentSettings, 1) or AgentSettings(id=1)
        row.api_key = secret
        db.add(row)
        db.commit()

    async def fake_scheduler_loop():
        await asyncio.Event().wait()

    async def fake_shutdown():
        return None

    monkeypatch.setattr(main_mod, "scheduler_loop", fake_scheduler_loop)
    monkeypatch.setattr(main_mod, "scheduler_shutdown", fake_shutdown, raising=False)
    monkeypatch.setattr(main_mod, "ensure_dpapi_available", lambda: None)
    monkeypatch.setattr(mcp_mod, "start_all_from_db", lambda: {})
    monkeypatch.setattr(mcp_mod, "migrate_env_secrets", lambda db: 0)
    monkeypatch.setattr(mcp_mod, "stop_all", lambda: None)

    async def _run():
        async with main_mod.lifespan(main_mod.app):
            pass

    asyncio.run(_run())
    with SessionLocal() as db:
        assert db.get(AgentSettings, 1).api_key == secret, (
            "启动路径不得改写密钥列：一次性迁移已移到 backend/scripts/migrate.py"
        )
    assert legacy.pending_secrets()["agent_settings_keys"] >= 1

    with caplog.at_level(logging.DEBUG):
        first = legacy.purge_legacy_agent_settings_keys()
        second = legacy.purge_legacy_agent_settings_keys()
    assert first >= 1
    assert second == 0, "迁移必须幂等：第二次没有可清除的 Key"
    with SessionLocal() as db:
        assert db.get(AgentSettings, 1).api_key == ""
    assert secret not in caplog.text


# ---------- I3：请求已发出后的超时/断流只重连、不重放 ----------

class _AliveProc:
    """可被 stop() 终止的假进程：terminate 后 poll 返回 0。"""

    stdin = None

    def __init__(self):
        self.stopped = False

    def poll(self):
        return 0 if self.stopped else None

    def terminate(self):
        self.stopped = True

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self.stopped = True


def _seed_mcp_row(name: str, enabled: bool = True, status: str = "stopped") -> None:
    with SessionLocal() as db:
        row = db.query(McpServer).filter(McpServer.name == name).first()
        if row is None:
            db.add(McpServer(name=name, command="python", args=[], env={}, enabled=enabled, status=status))
        else:
            row.enabled = enabled
            row.status = status
        db.commit()


def _cleanup_mcp(name: str) -> None:
    mcp_mod._servers.pop(name, None)
    for tool in [n for n in TOOL_SPECS if n.startswith(f"mcp_{name}__")]:
        TOOL_SPECS.pop(tool, None)


@pytest.mark.parametrize("message", [
    "等待响应超时（>30s）",
    "服务器关闭了输出流（进程可能已退出）",
])
def test_mcp_post_send_failure_reconnects_without_replay(client, message):
    """请求已发出后的超时/断流：只自动重连，绝不重放（写工具可能已执行）。"""
    name = "writeonce"
    _seed_mcp_row(name, enabled=True)
    server = mcp_mod.McpStdioServer(name, "python")
    server._ever_started = True
    server.proc = None
    calls = {"tool": 0, "start": 0}

    def call_tool(tool, args):
        calls["tool"] += 1
        raise mcp_mod.McpError(message, sent=True)

    def start():
        calls["start"] += 1
        server.proc = _AliveProc()

    server.call_tool = call_tool
    server.start = start
    mcp_mod._servers[name] = server
    try:
        result = asyncio.run(mcp_mod._make_executor(name, "write_tool")())
    finally:
        _cleanup_mcp(name)

    assert calls["tool"] == 1, "请求已发出后不得重放（写工具会重复执行）"
    assert calls["start"] == 1, "应只做一次自动重连"
    assert result["ok"] is False
    assert "结果未知" in result["error"]
    assert "不会自动重试" in result["error"]


def test_mcp_presend_failure_reconnects_and_retries_once(client):
    """请求明确未发出（sent=False）才允许重连后重试一次。"""
    name = "presend"
    _seed_mcp_row(name, enabled=True)
    server = mcp_mod.McpStdioServer(name, "python")
    server._ever_started = True
    server.proc = None
    calls = {"tool": 0, "start": 0}

    def call_tool(tool, args):
        calls["tool"] += 1
        if calls["tool"] == 1:
            raise mcp_mod.McpError("服务器进程未运行", sent=False)
        return {"text": "done", "is_error": False, "raw": {}}

    def start():
        calls["start"] += 1
        server.proc = _AliveProc()

    server.call_tool = call_tool
    server.start = start
    mcp_mod._servers[name] = server
    try:
        result = asyncio.run(mcp_mod._make_executor(name, "read_tool")())
    finally:
        _cleanup_mcp(name)

    assert result["ok"] is True, result
    assert calls == {"tool": 2, "start": 1}


def test_mcp_real_timeout_error_is_marked_sent(tmp_path, monkeypatch):
    from app.agents import mcp as mcp_mod2

    script = tmp_path / "hang_mcp_server.py"
    script.write_text(
        "import sys, json, time\n"
        "sys.stdin.reconfigure(encoding='utf-8')\n"
        "sys.stdout.reconfigure(encoding='utf-8')\n"
        "def send(o):\n"
        "    sys.stdout.write(json.dumps(o) + '\\n'); sys.stdout.flush()\n"
        "for line in sys.stdin:\n"
        "    m = json.loads(line)\n"
        "    mid, method = m.get('id'), m.get('method')\n"
        "    if method == 'initialize':\n"
        "        send({'jsonrpc': '2.0', 'id': mid, 'result': {'protocolVersion': '2024-11-05', 'capabilities': {}, 'serverInfo': {'name': 'hang', 'version': '1'}}})\n"
        "    elif method == 'tools/list':\n"
        "        send({'jsonrpc': '2.0', 'id': mid, 'result': {'tools': [{'name': 'hang', 'description': 'hang', 'inputSchema': {'type': 'object', 'properties': {}}}]}})\n"
        "    elif method == 'tools/call':\n"
        "        time.sleep(60)\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(mcp_mod2, "CALL_TIMEOUT", 0.3)
    server = mcp_mod2.McpStdioServer("realhang", command=sys.executable, args=[str(script)])
    server.start()
    try:
        with pytest.raises(mcp_mod2.McpError) as excinfo:
            server.call_tool("hang", {})
        assert excinfo.value.sent is True, "超时发生在请求发出之后，必须标记 sent"
    finally:
        server.stop()  # 超时路径已 stop；此处兜底清理（进程 terminate/kill 由 stop 负责）


def test_mcp_presend_error_is_marked_not_sent():
    server = mcp_mod.McpStdioServer("never", "python")
    with pytest.raises(mcp_mod.McpError) as excinfo:
        server._request("tools/call", {})
    assert excinfo.value.sent is False


# ---------- M3：未握手与重试失败文案分别准确 ----------

def test_mcp_never_connected_message_does_not_claim_retries(client):
    name = "neverconn"
    _seed_mcp_row(name, enabled=True)
    server = mcp_mod.McpStdioServer(name, "python")
    server._ever_started = False
    starts = []
    server.start = lambda: starts.append(1)
    mcp_mod._servers[name] = server
    try:
        result = asyncio.run(mcp_mod._make_executor(name, "echo")())
    finally:
        _cleanup_mcp(name)

    assert result["ok"] is False
    assert "从未连接" in result["error"]
    assert "最多重试" not in result["error"]
    assert starts == []


# ---------- M4：断开/禁用/启动中禁用竞态 ----------

def test_mcp_reconnect_skips_when_manually_disconnected(client):
    name = "detached"
    _seed_mcp_row(name, enabled=True)
    server = mcp_mod.McpStdioServer(name, "python")
    server._ever_started = True
    starts = []
    server.start = lambda: starts.append(1)

    result = mcp_mod._reconnect_server(server)

    assert result.ok is False
    assert result.attempts == 0
    assert result.reason == "detached"
    assert starts == [], "用户已断开（不在 _servers）时不得自动拉起"


def test_mcp_reconnect_skips_when_db_disabled(client):
    name = "disabledrow"
    _seed_mcp_row(name, enabled=False)
    server = mcp_mod.McpStdioServer(name, "python")
    server._ever_started = True
    server.tools = [{"name": "echo", "description": "x", "inputSchema": {"type": "object", "properties": {}}}]
    starts = []
    server.start = lambda: starts.append(1)
    mcp_mod._servers[name] = server
    try:
        result = mcp_mod._reconnect_server(server)
    finally:
        _cleanup_mcp(name)

    assert result.ok is False
    assert result.attempts == 0
    assert result.reason == "disabled"
    assert starts == [], "DB enabled=False 时不得自动拉起"
    assert f"mcp_{name}__echo" not in TOOL_SPECS, "禁用服务器不得重新注册工具"


def test_mcp_reconnect_aborts_if_disconnected_during_start(client):
    name = "race"
    _seed_mcp_row(name, enabled=True)
    server = mcp_mod.McpStdioServer(name, "python")
    server._ever_started = True
    server.proc = None
    server.tools = [{"name": "echo", "description": "x", "inputSchema": {"type": "object", "properties": {}}}]
    proc = _AliveProc()

    def start():
        server.proc = proc
        mcp_mod._servers.pop(name, None)  # 握手期间用户点了「断开」

    server.start = start
    mcp_mod._servers[name] = server
    try:
        result = mcp_mod._reconnect_server(server)
    finally:
        _cleanup_mcp(name)

    assert result.ok is False
    assert result.reason == "detached"
    assert proc.stopped is True, "启动后复核失败必须停掉刚拉起的进程"
    assert f"mcp_{name}__echo" not in TOOL_SPECS, "复核失败不得注册工具"
    with SessionLocal() as db:
        assert db.query(McpServer).filter(McpServer.name == name).one().status != "connected"


# ---------- I4：429 退避 sleep 受总预算约束 ----------

class _RateLimitResp:
    status_code = 429
    text = "HTTP 429"

    def json(self):
        return {}


def test_probe_429_sleep_is_capped_by_total_budget(client, monkeypatch):
    import httpx

    mid = _mk_model_row()
    sleeps = []
    monkeypatch.setattr(model_probe.time, "sleep", lambda seconds: sleeps.append(seconds))
    # 1s 预算远小于默认 30s 退避：sleep 必须被剩余预算截断
    monkeypatch.setattr(model_probe, "PROBE_TOTAL_BUDGET_SECONDS", 1.0, raising=False)
    monkeypatch.setattr(httpx.Client, "post", lambda self, *args, **kwargs: _RateLimitResp())

    with SessionLocal() as db:
        row = db.get(ProviderModel, mid)
        result = model_probe.probe_reasoning(db, row)

    assert sleeps, "命中 429 应发生退避 sleep"
    assert max(sleeps) <= 1.05, f"429 sleep 超出总预算：{max(sleeps)}s"
    assert result["requests"] >= 1


def test_post_with_retry_stops_when_budget_already_exhausted(monkeypatch):
    import httpx

    calls = []
    sleeps = []
    monkeypatch.setattr(httpx.Client, "post", lambda self, *args, **kwargs: (calls.append(1), _RateLimitResp())[1])
    monkeypatch.setattr(model_probe.time, "sleep", lambda seconds: sleeps.append(seconds))
    model_probe._set_request_deadline(time.monotonic() - 1)
    try:
        resp, error = model_probe._post_with_retry("https://api.example.invalid/v1", {}, {}, 1.0, {})
    finally:
        model_probe._set_request_deadline(None)

    assert len(calls) == 1, "预算已耗尽时不得再重试 429"
    assert sleeps == []
    assert resp.status_code == 429 and error is None


# ---------- M5：入站字符上限同时保证 UTF-8 字节有界 ----------

def test_inbound_char_limit_guarantees_utf8_byte_bound():
    assert mcp_mod.MAX_INBOUND_LINE_CHARS * 4 <= mcp_mod.MAX_MESSAGE_BYTES
