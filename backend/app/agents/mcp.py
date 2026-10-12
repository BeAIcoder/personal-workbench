"""MCP（Model Context Protocol）工具接入：最小 stdio 客户端 + 服务器管理。

设计（ADR-002 口径）：
- 只做 **stdio 传输**（本地命令起的 MCP Server，如 filesystem/fetch 类）；
  HTTP/SSE 传输留待需要时再接，避免为一个协议引入 SDK 依赖；
- 协议实现按 MCP stdio 规范：换行分隔的 JSON-RPC 2.0，握手
  initialize → notifications/initialized → tools/list → tools/call；
- 每个 Server 一个子进程，同步请求/响应（带超时）；暴露给 Agent 的工具
  为 async 包装（asyncio.to_thread），不阻塞事件循环；
- Server 清单持久化在 `mcp_servers` 表（env 含敏感值，仅本机库，禁入 git）。
"""
from __future__ import annotations

import json
import logging
import subprocess
import sys
import threading
import time
from typing import Any

from sqlalchemy.orm import Session

from ..database import SessionLocal
from ..models import McpServer

logger = logging.getLogger(__name__)

PROTOCOL_VERSION = "2024-11-05"
CALL_TIMEOUT = 30          # 单次工具调用超时（秒）
START_TIMEOUT = 15         # 握手超时（秒）
MAX_MESSAGE_BYTES = 4 * 1024 * 1024


class McpError(RuntimeError):
    """MCP 通信或服务器错误。"""


class McpStdioServer:
    """一个 MCP stdio 服务器子进程的托管连接（同步、带超时）。"""

    def __init__(self, name: str, command: str, args: list[str] | None = None,
                 env: dict | None = None, cwd: str | None = None):
        self.name = name
        self.command = command
        self.args = args or []
        self.env = env or {}
        self.cwd = cwd
        self.proc: subprocess.Popen | None = None
        self.tools: list[dict] = []
        self._id = 0
        self._lock = threading.Lock()  # 串行化请求/响应

    # ---------- 生命周期 ----------

    def start(self) -> None:
        if self.proc and self.proc.poll() is None:
            return
        import os

        full_env = dict(os.environ)
        full_env.update({k: str(v) for k, v in self.env.items()})
        try:
            self.proc = subprocess.Popen(
                [self.command, *self.args],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True, bufsize=1, encoding="utf-8", errors="replace",
                env=full_env, cwd=self.cwd or None,
            )
        except FileNotFoundError as exc:
            raise McpError(f"命令不存在：{self.command}") from exc
        except OSError as exc:
            raise McpError(f"启动失败：{exc}") from exc
        try:
            self._request("initialize", {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "personal-workbench", "version": "1.11.0"},
            }, timeout=START_TIMEOUT)
            self._notify("notifications/initialized", {})
            listed = self._request("tools/list", {}, timeout=START_TIMEOUT)
            self.tools = listed.get("tools", []) or []
        except Exception:
            self.stop()
            raise

    def stop(self) -> None:
        proc = self.proc
        self.proc = None
        if proc is None:
            return
        try:
            if proc.stdin:
                proc.stdin.close()
        except OSError:
            pass
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except (subprocess.TimeoutExpired, OSError):
            try:
                proc.kill()
            except OSError:
                pass

    def is_alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    # ---------- JSON-RPC ----------

    def _next_id(self) -> int:
        self._id += 1
        return self._id

    def _send(self, payload: dict) -> None:
        assert self.proc and self.proc.stdin
        line = json.dumps(payload, ensure_ascii=False)
        if len(line.encode("utf-8")) > MAX_MESSAGE_BYTES:
            raise McpError("消息过大")
        self.proc.stdin.write(line + "\n")
        self.proc.stdin.flush()

    def _read_response(self, msg_id: int, timeout: float) -> dict:
        assert self.proc and self.proc.stdout
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise McpError(f"等待响应超时（>{timeout:g}s）")
            line = self.proc.stdout.readline()
            if not line:
                raise McpError("服务器关闭了输出流（进程可能已退出）")
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue  # 忽略通知/非 JSON 行
            if msg.get("id") == msg_id:
                if "error" in msg:
                    err = msg["error"]
                    raise McpError(f"MCP 错误 {err.get('code')}: {err.get('message')}")
                return msg.get("result", {})
            # 其他 id / 通知：继续读

    def _request(self, method: str, params: dict, timeout: float = CALL_TIMEOUT) -> dict:
        with self._lock:
            if not self.is_alive():
                raise McpError("服务器进程未运行")
            msg_id = self._next_id()
            self._send({"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params})
            return self._read_response(msg_id, timeout)

    def _notify(self, method: str, params: dict) -> None:
        with self._lock:
            if not self.is_alive():
                return
            self._send({"jsonrpc": "2.0", "method": method, "params": params})

    # ---------- 工具 ----------

    def call_tool(self, tool_name: str, arguments: dict) -> dict:
        """调用服务器工具，返回 {text, is_error, raw}。"""
        result = self._request("tools/call", {"name": tool_name, "arguments": arguments or {}})
        texts: list[str] = []
        for block in result.get("content", []) or []:
            if isinstance(block, dict):
                if block.get("type") == "text" and block.get("text"):
                    texts.append(str(block["text"]))
                elif block.get("type") == "resource" and block.get("text"):
                    texts.append(str(block["text"]))
        return {
            "text": "\n".join(texts)[:8000],
            "is_error": bool(result.get("isError")),
            "raw": result,
        }


# ---------- 管理器：DB 行 ↔ 运行连接 ↔ Agent 工具 ----------

_servers: dict[str, McpStdioServer] = {}
_manager_lock = threading.Lock()


def _row_to_server(row: McpServer) -> McpStdioServer:
    return McpStdioServer(
        name=row.name,
        command=row.command,
        args=list(row.args or []),
        env=dict(row.env or {}),
    )


def start_all_from_db() -> dict:
    """启动所有启用的 MCP 服务器并注册其工具（lifespan 调用）。失败不影响宿主。"""
    from .tools import register_module_tools

    results: dict[str, str] = {}
    with SessionLocal() as db:
        rows = db.query(McpServer).filter(McpServer.enabled.is_(True)).all()
        for row in rows:
            try:
                server = _row_to_server(row)
                server.start()
                with _manager_lock:
                    _servers[row.name] = server
                _register_server_tools(server, register_module_tools)
                row.status = "connected"
                row.last_error = ""
                row.tools_json = server.tools
                results[row.name] = f"connected ({len(server.tools)} tools)"
            except Exception as exc:  # noqa: BLE001  单服务器失败不拖垮宿主
                logger.warning("MCP 服务器 %s 启动失败：%s", row.name, exc)
                row.status = "error"
                row.last_error = str(exc)[:500]
                results[row.name] = f"error: {exc}"
        db.commit()
    return results


def stop_all() -> None:
    with _manager_lock:
        for server in _servers.values():
            try:
                server.stop()
            except Exception:  # noqa: BLE001
                pass
        _servers.clear()


def _tool_name(server: str, tool: str) -> str:
    return f"mcp_{server}__{tool}"


def _register_server_tools(server: McpStdioServer, register) -> None:
    """把服务器工具注册进 TOOL_SPECS（只读标记按工具名启发式推断）。"""
    specs: dict[str, dict] = {}
    for tool in server.tools:
        name = tool.get("name")
        if not name:
            continue
        schema = tool.get("inputSchema") or {}
        params: dict[str, tuple] = {}
        required: list[str] = []
        for pname, pspec in (schema.get("properties") or {}).items():
            ptype = pspec.get("type", "string")
            if isinstance(ptype, list):  # ["string","null"] 等
                ptype = next((t for t in ptype if t != "null"), "string")
            if ptype not in ("string", "integer", "number", "boolean", "array", "object"):
                ptype = "string"
            params[pname] = (ptype, str(pspec.get("description") or "")[:200], pname in (schema.get("required") or []))
            if pname in (schema.get("required") or []):
                required.append(pname)
        readonly = not any(k in name.lower() for k in ("write", "create", "delete", "update", "move", "rename", "send", "post"))
        specs[_tool_name(server.name, name)] = {
            "func": _make_executor(server.name, name),
            "description": f"[MCP:{server.name}] {str(tool.get('description') or name)[:200]}",
            "params": params,
            "required": required,
            "readonly": readonly,
        }
    register(specs)


def _make_executor(server_name: str, tool_name: str):
    """生成 Agent 可调的 async 工具函数（线程池执行，不阻塞事件循环）。"""
    import asyncio

    async def _call(**kwargs: Any) -> dict:
        server = _servers.get(server_name)
        if server is None or not server.is_alive():
            return {"ok": False, "error": f"MCP 服务器 {server_name} 未连接"}
        try:
            result = await asyncio.to_thread(server.call_tool, tool_name, kwargs)
        except Exception as exc:  # noqa: BLE001  工具失败转为可读错误，不崩 Agent
            return {"ok": False, "error": str(exc)[:300]}
        if result.get("is_error"):
            return {"ok": False, "error": result.get("text", "")[:500]}
        return {"ok": True, "result": result.get("text", "")}

    _call.__name__ = _tool_name(server_name, tool_name)
    return _call


# ---------- 管理 API 用的服务函数 ----------

def list_servers(db: Session) -> list[dict]:
    rows = db.query(McpServer).order_by(McpServer.id.desc()).all()
    return [_server_out(r) for r in rows]


def _server_out(r: McpServer) -> dict:
    tools = r.tools_json or []
    return {
        "id": r.id,
        "name": r.name,
        "command": r.command,
        "args": r.args or [],
        "env": {k: "***" for k in (r.env or {})},  # 不回显敏感值
        "enabled": r.enabled,
        "status": r.status,
        "last_error": r.last_error,
        "tools": [{"name": t.get("name"), "description": (t.get("description") or "")[:120]} for t in tools],
        "created_at": r.created_at.strftime("%Y-%m-%d %H:%M:%S") if r.created_at else "",
    }


def create_server(db: Session, name: str, command: str, args: list[str],
                  env: dict, enabled: bool) -> dict:
    name = (name or "").strip()
    if not name or not command.strip():
        raise ValueError("name 与 command 必填")
    if db.query(McpServer).filter(McpServer.name == name).first():
        raise ValueError(f"服务器名已存在：{name}")
    row = McpServer(name=name, command=command.strip(), args=args or [],
                    env=env or {}, enabled=enabled, status="stopped")
    db.add(row)
    db.commit()
    db.refresh(row)
    return _server_out(row)


def update_server(db: Session, server_id: int, data: dict) -> dict:
    row = db.get(McpServer, server_id)
    if row is None:
        raise LookupError("服务器不存在")
    for field in ("command", "args", "env", "enabled"):
        if field in data and data[field] is not None:
            setattr(row, field, data[field])
    row.status = "stopped"
    db.commit()
    db.refresh(row)
    return _server_out(row)


def delete_server(db: Session, server_id: int) -> None:
    row = db.get(McpServer, server_id)
    if row is None:
        raise LookupError("服务器不存在")
    with _manager_lock:
        server = _servers.pop(row.name, None)
    if server:
        server.stop()
    db.delete(row)
    db.commit()


def connect_server(db: Session, server_id: int) -> dict:
    """连接（或重连）单个服务器并注册工具。"""
    from .tools import register_module_tools

    row = db.get(McpServer, server_id)
    if row is None:
        raise LookupError("服务器不存在")
    with _manager_lock:
        old = _servers.pop(row.name, None)
    if old:
        old.stop()
    try:
        server = _row_to_server(row)
        server.start()
    except Exception as exc:  # noqa: BLE001
        row.status = "error"
        row.last_error = str(exc)[:500]
        db.commit()
        raise McpError(str(exc)) from exc
    with _manager_lock:
        _servers[row.name] = server
    _register_server_tools(server, register_module_tools)
    row.status = "connected"
    row.last_error = ""
    row.tools_json = server.tools
    db.commit()
    db.refresh(row)
    return _server_out(row)


def disconnect_server(db: Session, server_id: int) -> dict:
    row = db.get(McpServer, server_id)
    if row is None:
        raise LookupError("服务器不存在")
    with _manager_lock:
        server = _servers.pop(row.name, None)
    if server:
        server.stop()
    row.status = "stopped"
    db.commit()
    db.refresh(row)
    return _server_out(row)


def test_call(server_name: str, tool_name: str, arguments: dict) -> dict:
    """管理页「测试调用」：直接对运行中的服务器发一次 tools/call。"""
    with _manager_lock:
        server = _servers.get(server_name)
    if server is None or not server.is_alive():
        raise McpError(f"服务器 {server_name} 未连接")
    return server.call_tool(tool_name, arguments or {})
