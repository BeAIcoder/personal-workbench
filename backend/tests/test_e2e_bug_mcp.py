"""MCP 区域 E2E 探测守护测试（test_e2e_bug_mcp）。

对应 dev/e2e/probes/mcp.py 深度探测守护的后端契约：
- PUT 不带 env 键（前端编辑未动 env 时省略）→ 真实环境变量必须保留；
- PUT 显式 env={} → 才清空；
- 更新配置会停掉运行中的旧实例并复位状态（避免旧进程+旧工具残留）；
- 未连接时 test-call 抛 McpError（路由层转 503）；
- 连接失败（假命令）落 status=error + last_error，供列表页优雅展示。
"""
import sys
import textwrap

import pytest

from app.agents import mcp as mcp_agent
from app.agents.mcp import McpError, connect_server, create_server, delete_server, update_server
from app.database import SessionLocal

P = "/api/mcp"

FAKE_SERVER = textwrap.dedent('''
    import sys, json
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
                "serverInfo": {"name": "guard", "version": "0.1"}}})
        elif method == "tools/list":
            send({"jsonrpc": "2.0", "id": mid, "result": {"tools": [{
                "name": "echo", "description": "回声工具",
                "inputSchema": {"type": "object",
                                "properties": {"text": {"type": "string"}},
                                "required": ["text"]}}]}})
        elif method == "tools/call":
            args = (msg.get("params") or {}).get("arguments") or {}
            send({"jsonrpc": "2.0", "id": mid, "result": {
                "content": [{"type": "text", "text": "echo:" + str(args.get("text", ""))}],
                "isError": False}})
        elif isinstance(method, str) and method.startswith("notifications/"):
            pass
''')


@pytest.fixture()
def db(client):
    """依赖 conftest 的 client（负责建表/清表），再开一个同库会话做 DB 级断言。"""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def fake_server(tmp_path):
    script = tmp_path / "guard_mcp_server.py"
    script.write_text(FAKE_SERVER, encoding="utf-8")
    return script


def _cleanup(name):
    with SessionLocal() as s:
        row = s.query(mcp_agent.McpServer).filter(mcp_agent.McpServer.name == name).first()
        if row:
            delete_server(s, row.id)


def test_update_without_env_key_preserves_env(db, fake_server):
    """前端编辑未动 env 时省略 env 键（exclude_unset）：真实环境变量必须保留。"""
    name = "guardenv"
    _cleanup(name)
    try:
        create_server(db, name, sys.executable, [str(fake_server)],
                      {"TOKEN": "secret-xyz"}, True)
        sid = db.query(mcp_agent.McpServer).filter(mcp_agent.McpServer.name == name).first().id
        update_server(db, sid, {"command": sys.executable})  # 只改命令，不带 env
        row = db.query(mcp_agent.McpServer).filter(mcp_agent.McpServer.name == name).first()
        assert row.env == {"TOKEN": "secret-xyz"}, "未传 env 却被清空"
    finally:
        _cleanup(name)


def test_update_empty_env_clears(db, fake_server):
    """用户主动清空 env（显式 env={}）：必须落库为 {}。"""
    name = "guardclear"
    _cleanup(name)
    try:
        create_server(db, name, sys.executable, [str(fake_server)], {"TOKEN": "x"}, True)
        sid = db.query(mcp_agent.McpServer).filter(mcp_agent.McpServer.name == name).first().id
        update_server(db, sid, {"env": {}})
        row = db.query(mcp_agent.McpServer).filter(mcp_agent.McpServer.name == name).first()
        assert row.env == {}, "显式清空未生效"
    finally:
        _cleanup(name)


def test_update_stops_running_instance(db, fake_server):
    """更新配置先停运行中的旧实例并复位状态（探测 TC-MCP-008 的后端契约）。"""
    name = "guardstop"
    _cleanup(name)
    try:
        create_server(db, name, sys.executable, [str(fake_server)], {}, True)
        sid = db.query(mcp_agent.McpServer).filter(mcp_agent.McpServer.name == name).first().id
        out = connect_server(db, sid)
        assert out["status"] == "connected"
        assert name in mcp_agent._servers
        assert mcp_agent._servers[name].is_alive()
        update_server(db, sid, {"command": sys.executable})
        assert name not in mcp_agent._servers, "旧实例未停"
        row = db.query(mcp_agent.McpServer).filter(mcp_agent.McpServer.name == name).first()
        assert row.status == "stopped"
    finally:
        _cleanup(name)


def test_test_call_requires_connection(db, fake_server):
    """未连接时 test_call 抛 McpError（路由层转 503，前端展示「服务器未连接」）。"""
    name = "guardcall"
    _cleanup(name)
    try:
        create_server(db, name, sys.executable, [str(fake_server)], {}, True)
        with pytest.raises(McpError, match="未连接"):
            mcp_agent.test_call(name, "echo", {"text": "hi"})
    finally:
        _cleanup(name)


def test_connect_failure_sets_error_status(client):
    """假命令连接失败：503 + status=error + last_error（探测 TC-MCP-006 契约）。"""
    r = client.post(f"{P}/servers", json={
        "name": "guardbad", "command": "definitely-not-a-command-xyz",
        "args": [], "env": {}, "enabled": True})
    assert r.status_code == 201, r.text
    sid = r.json()["id"]
    resp = client.post(f"{P}/servers/{sid}/connect")
    assert resp.status_code == 503
    assert "命令不存在" in resp.json()["detail"]
    item = next(i for i in client.get(f"{P}/servers").json()["items"] if i["id"] == sid)
    assert item["status"] == "error"
    assert item["last_error"]
    client.delete(f"{P}/servers/{sid}")
