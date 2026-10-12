"""MCP 工具接入测试：假 stdio MCP 服务器（子进程）走通全链路。

不依赖任何真实 MCP Server；假服务器用 sys.executable 起一个换行分隔
JSON-RPC 小脚本，覆盖：建/连/断/删、tools/list 注册进 TOOL_SPECS、
tools/call 执行、连接失败 503、env 脱敏。
"""
import json
import sys
import textwrap
import time
from pathlib import Path

import pytest

from app.agents.tools import TOOL_SPECS
from app.modules import get_loaded_modules

P = "/api/mcp"

FAKE_SERVER = textwrap.dedent('''
    import sys, json
    # 显式 UTF-8：CI（en-US）管道默认 cp1252，写中文会 UnicodeEncodeError 崩进程
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
                "serverInfo": {"name": "fake", "version": "0.1"}}})
        elif method == "tools/list":
            send({"jsonrpc": "2.0", "id": mid, "result": {"tools": [{
                "name": "echo", "description": "回声工具",
                "inputSchema": {"type": "object",
                                "properties": {"text": {"type": "string", "description": "文本"}},
                                "required": ["text"]}}]}})
        elif method == "tools/call":
            args = (msg.get("params") or {}).get("arguments") or {}
            send({"jsonrpc": "2.0", "id": mid, "result": {
                "content": [{"type": "text", "text": "echo:" + str(args.get("text", ""))}],
                "isError": False}})
        elif isinstance(method, str) and method.startswith("notifications/"):
            pass
''')

SILENT_CALL_SERVER = textwrap.dedent('''
    import sys, json
    # 显式 UTF-8：CI（en-US）管道默认 cp1252，写中文会 UnicodeEncodeError 崩进程
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
                "serverInfo": {"name": "silent", "version": "0.1"}}})
        elif method == "tools/list":
            send({"jsonrpc": "2.0", "id": mid, "result": {"tools": [{
                "name": "hang", "description": "收到调用但不回行的工具",
                "inputSchema": {"type": "object", "properties": {}}}]}})
        elif method == "tools/call":
            pass  # 模拟服务器挂起：进程活着但永不回行
''')


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture()
def fake_server(tmp_path):
    script = tmp_path / "fake_mcp_server.py"
    script.write_text(FAKE_SERVER, encoding="utf-8")
    return script


def _create(client, tmp_path, fake_server, **kw):
    payload = {
        "name": "faketest",
        "command": sys.executable,
        "args": [str(fake_server)],
        "env": {"FAKE_TOKEN": "secret"},
        "enabled": True,
    }
    payload.update(kw)
    r = client.post(f"{P}/servers", json=payload)
    assert r.status_code == 201, r.text
    return r.json()


def test_mcp_full_flow(client, tmp_path, fake_server):
    created = _create(client, tmp_path, fake_server)
    assert created["status"] == "stopped"
    assert created["env"] == {"FAKE_TOKEN": "***"}  # 脱敏

    # 连接：tools/list → 注册 mcp_faketest__echo
    resp = client.post(f"{P}/servers/{created['id']}/connect")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "connected"
    assert [t["name"] for t in body["tools"]] == ["echo"]

    tool_name = "mcp_faketest__echo"
    assert tool_name in TOOL_SPECS
    spec = TOOL_SPECS[tool_name]
    assert spec["readonly"] is True  # echo 不含写类关键词
    assert "text" in spec["params"]

    # 测试调用
    call = client.post(f"{P}/servers/{created['id']}/test-call", json={"tool": "echo", "arguments": {"text": "你好"}})
    assert call.status_code == 200
    assert call.json()["text"] == "echo:你好"

    # Agent 工具执行器（async）
    import asyncio

    result = asyncio.run(spec["func"](text="hi"))
    assert result["ok"] is True and result["result"] == "echo:hi"

    # 断开与删除
    dis = client.post(f"{P}/servers/{created['id']}/disconnect")
    assert dis.json()["status"] == "stopped"
    client.delete(f"{P}/servers/{created['id']}")
    assert client.get(f"{P}/servers").json()["items"] == []

    # 清理全局工具注册（避免污染其他测试）
    TOOL_SPECS.pop(tool_name, None)


def test_mcp_connect_failure_503(client, tmp_path):
    """命令不存在 → 连接 503，状态 error。"""
    created = _create(client, tmp_path, Path(sys.executable), command="definitely-not-a-command-xyz")
    resp = client.post(f"{P}/servers/{created['id']}/connect")
    assert resp.status_code == 503
    assert "命令不存在" in resp.json()["detail"]
    client.delete(f"{P}/servers/{created['id']}")


def test_mcp_duplicate_name_rejected(client, tmp_path, fake_server):
    _create(client, tmp_path, fake_server)
    dup = client.post(f"{P}/servers", json={
        "name": "faketest", "command": sys.executable, "args": [str(fake_server)],
    })
    assert dup.status_code == 400 and "已存在" in dup.json()["detail"]
    # 清理
    for item in client.get(f"{P}/servers").json()["items"]:
        client.delete(f"{P}/servers/{item['id']}")


def test_mcp_module_tools_not_in_loaded_modules():
    """MCP 工具不走 Module Registry（宿主级能力），模块清单不含 mcp。"""
    assert "mcp" not in {m.name for m in get_loaded_modules()}


def test_mcp_call_timeout_when_server_hangs(tmp_path, monkeypatch):
    """服务器收到 tools/call 但不回行：CALL_TIMEOUT 内抛 McpError，不永久阻塞。"""
    from app.agents import mcp

    script = tmp_path / "silent_mcp_server.py"
    script.write_text(SILENT_CALL_SERVER, encoding="utf-8")
    monkeypatch.setattr(mcp, "CALL_TIMEOUT", 1)  # 缩短注入；默认值在调用时解析

    server = mcp.McpStdioServer(name="silent", command=sys.executable, args=[str(script)])
    server.start()
    try:
        t0 = time.monotonic()
        with pytest.raises(mcp.McpError, match="超时"):
            server.call_tool("hang", {})
        elapsed = time.monotonic() - t0
        assert elapsed < 10  # 旧行为会永久阻塞在 readline
        # 超时后连接状态可控：进程仍在，可正常 stop 回收
        assert server.is_alive()
    finally:
        server.stop()
    assert not server.is_alive()
