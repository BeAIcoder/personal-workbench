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

from dataclasses import dataclass
import json
import logging
import os
import queue
import subprocess
import sys
import threading
import time
from typing import Any

from sqlalchemy.orm import Session

from ..database import SessionLocal
from ..models import McpServer
from .secrets_box import seal, unseal

logger = logging.getLogger(__name__)

PROTOCOL_VERSION = "2024-11-05"
CALL_TIMEOUT = 30          # 单次工具调用超时（秒）
START_TIMEOUT = 15         # 握手超时（秒）
MAX_MESSAGE_BYTES = 4 * 1024 * 1024
# 单行入站上限（字符）：按 UTF-8 最坏 4 字节/字符折算，字符上限本身即保证
# 字节上限（≤ MAX_MESSAGE_BYTES），避免先读入 4M 字符再编码出 ~16MB 的瞬时峰值。
# 读入后仍按实际 UTF-8 字节数复核一次。
MAX_INBOUND_LINE_CHARS = max(1, MAX_MESSAGE_BYTES // 4)
MAX_RAW_RESULT_CHARS = 60_000
# 自动重连：有界指数退避（最多 N 次，间隔 base*2^n 且不超过 max），仅对
# 曾成功握手过的实例生效，避免把「从未连接」的配置误启动。
RECONNECT_MAX_ATTEMPTS = 3
RECONNECT_BASE_DELAY = 0.5
RECONNECT_MAX_DELAY = 2.0
_INHERITED_ENV_NAMES = (
    "SystemRoot", "SystemDrive", "WINDIR", "ComSpec", "OS",
    "PATH", "PATHEXT", "TEMP", "TMP", "LOCALAPPDATA", "APPDATA", "PROGRAMDATA",
    "PROGRAMFILES", "PROGRAMFILES(X86)", "COMMONPROGRAMFILES", "COMMONPROGRAMFILES(X86)",
    "USERPROFILE", "HOMEDRIVE", "HOMEPATH",
    "NODE_PATH", "NVM_HOME", "NVM_SYMLINK", "PNPM_HOME", "COREPACK_HOME", "NPM_CONFIG_CACHE",
    "PYTHONHOME", "PYTHONPATH", "PYTHONIOENCODING", "PYTHONUTF8", "PYTHONUNBUFFERED",
    "PYTHONDONTWRITEBYTECODE", "VIRTUAL_ENV", "LANG", "LC_ALL", "LC_CTYPE", "TZ",
)


def _child_env(explicit: dict) -> dict[str, str]:
    env = {name: os.environ[name] for name in _INHERITED_ENV_NAMES if name in os.environ}
    env.update({str(key): str(value) for key, value in explicit.items()})
    return env


def _bounded_raw(result: dict) -> dict:
    text = json.dumps(result, ensure_ascii=False, default=str)
    if len(text) <= MAX_RAW_RESULT_CHARS:
        return result
    return {
        "truncated": True,
        "message": "原始结果过大，已截断",
        "preview": text[:MAX_RAW_RESULT_CHARS],
    }


class McpError(RuntimeError):
    """MCP 通信或服务器错误。

    ``sent=True`` 表示 JSON-RPC 请求可能已经到达服务器（发送后超时/断流/
    错误响应）：调用方只能重连，**不得重放**，否则写工具可能重复执行。
    """

    def __init__(self, message: str, *, sent: bool = False):
        super().__init__(message)
        self.sent = sent


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
        self._reconnect_lock = threading.Lock()  # 同一实例只允许一个重连流程
        self._ever_started = False  # 成功握手过才允许自动重连（避免误启动未连接配置）

    # ---------- 生命周期 ----------

    def start(self) -> None:
        if self.proc and self.proc.poll() is None:
            return
        full_env = _child_env(self.env)
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
            self._ever_started = True
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

    def _stop_if_current(self, proc: subprocess.Popen | None) -> None:
        """仅当 ``self.proc`` 仍是 proc 时才停止。

        自动重连可能已换上新进程；旧请求超时/超限时若无条件 stop()，会把刚恢复的
        新进程又杀掉（旧请求的读线程仍在等旧管道）。
        """
        if proc is not None and self.proc is proc:
            self.stop()

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

    def _read_line(self, timeout: float) -> str:
        """读一行 stdout；超时抛 queue.Empty，超过入站上限抛 McpError。

        readline 是不可中断的阻塞调用，放到一次性守护线程里执行：
        超时后调用方放弃等待，线程在进程退出/管道关闭时自然结束。
        读取时传 size 上限（MCP-01）：异常服务器发一条巨行时，不会把整行
        读进内存；超出上限即判协议违规，由调用方终止进程。
        """
        proc = self.proc
        assert proc and proc.stdout
        limit = max(1, int(MAX_INBOUND_LINE_CHARS))
        q: queue.Queue[str] = queue.Queue(maxsize=1)

        def _reader() -> None:
            try:
                q.put(proc.stdout.readline(limit + 1))
            except (ValueError, OSError):
                q.put("")

        threading.Thread(target=_reader, daemon=True).start()
        line = q.get(timeout=timeout)
        if len(line) > limit or len(line.encode("utf-8", "replace")) > MAX_MESSAGE_BYTES:
            raise McpError(f"入站消息超过单行上限（{limit} 字符），已拒绝该行")
        return line

    def _read_response(self, msg_id: int, timeout: float) -> dict:
        assert self.proc and self.proc.stdout
        proc = self.proc  # 固定本次请求对应的进程：重连换新进程后不要误杀
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self._stop_if_current(proc)  # 终止进程关闭管道，释放阻塞在 readline 的读线程
                raise McpError(f"等待响应超时（>{timeout:g}s）", sent=True)
            try:
                line = self._read_line(remaining)
            except queue.Empty:
                self._stop_if_current(proc)  # 同上：超时不回收会导致守护线程永久泄漏
                raise McpError(f"等待响应超时（>{timeout:g}s）", sent=True) from None
            except McpError as exc:
                self._stop_if_current(proc)  # 入站超限：终止进程，避免继续读/占用内存
                raise McpError(str(exc), sent=True) from exc
            if not line:
                raise McpError("服务器关闭了输出流（进程可能已退出）", sent=True)
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
                    raise McpError(f"MCP 错误 {err.get('code')}: {err.get('message')}", sent=True)
                return msg.get("result", {})
            # 其他 id / 通知：继续读

    def _request(self, method: str, params: dict, timeout: float | None = None) -> dict:
        if timeout is None:
            timeout = CALL_TIMEOUT
        with self._lock:
            if not self.is_alive():
                raise McpError("服务器进程未运行", sent=False)
            msg_id = self._next_id()
            try:
                self._send({"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params})
            except McpError:
                raise  # 消息过大：发送前已拒绝，未写出任何字节
            except Exception as exc:  # noqa: BLE001  写管道失败可能已部分发出，保守视为已发送
                raise McpError(f"发送请求失败：{exc}", sent=True) from exc
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
            "raw": _bounded_raw(result),
        }


# ---------- 管理器：DB 行 ↔ 运行连接 ↔ Agent 工具 ----------

_servers: dict[str, McpStdioServer] = {}
_manager_lock = threading.Lock()


def _is_sealed(value: object) -> bool:
    return isinstance(value, str) and value.startswith("dpapi:")


def seal_env_values(env: dict | None) -> dict:
    """把 MCP env 值逐个 DPAPI 密封后落库；空值原样保留（无秘密可护）。"""
    return {str(k): ("" if v in (None, "") else seal(str(v))) for k, v in (env or {}).items()}


def unseal_env_values(env: dict | None) -> dict:
    """读取/连接时还原 env 值；历史明文值按 secrets_box 兼容原样返回。"""
    return {str(k): ("" if v in (None, "") else unseal(str(v))) for k, v in (env or {}).items()}


def migrate_env_secrets(db: Session) -> int:
    """把存量明文 MCP env 一次性迁移为 DPAPI 格式，返回迁移的服务器条数。"""
    count = 0
    for row in db.query(McpServer).all():
        values = row.env or {}
        if values and not all(_is_sealed(v) for v in values.values()):
            row.env = seal_env_values(values)
            count += 1
    if count:
        db.commit()
    return count


def _row_to_server(row: McpServer) -> McpStdioServer:
    return McpStdioServer(
        name=row.name,
        command=row.command,
        args=list(row.args or []),
        env=unseal_env_values(row.env),
    )


def _sync_server_row(server: McpStdioServer, status: str, error: str = "") -> None:
    """把运行状态/工具清单写回 DB（重连成功/失败后调用）；失败只告警不影响调用。"""
    try:
        with SessionLocal() as db:
            row = db.query(McpServer).filter(McpServer.name == server.name).first()
            if row is None:
                return
            row.status = status
            row.last_error = (error or "")[:500]
            row.tools_json = server.tools
            db.commit()
    except Exception:  # noqa: BLE001  状态写库失败不能拖垮工具调用
        logger.warning("MCP 服务器状态写库失败（不影响重连）：%s", server.name, exc_info=True)


@dataclass(frozen=True)
class _ReconnectResult:
    """自动重连结果：attempts=实际尝试次数；reason 为 never_started/detached/disabled/错误文本。"""

    ok: bool
    attempts: int = 0
    reason: str = ""


def _server_still_managed(server: McpStdioServer) -> bool:
    """该实例是否仍挂在管理器里（用户断开/删除/重配会把它 pop 掉）。"""
    with _manager_lock:
        return _servers.get(server.name) is server


def _server_enabled_in_db(name: str) -> bool:
    """DB 行存在且 enabled=True 才允许自动拉起；读库失败按不允许处理（fail-closed）。"""
    try:
        with SessionLocal() as db:
            row = db.query(McpServer).filter(McpServer.name == name).first()
            return bool(row is not None and row.enabled)
    except Exception:  # noqa: BLE001  读不到状态时宁可不重连，避免复活用户已停用的服务器
        logger.warning("MCP 服务器启用状态读取失败，取消自动重连：%s", name, exc_info=True)
        return False


def _reconnect_failure_message(server_name: str, result: _ReconnectResult) -> str:
    """按失败原因给出准确文案（未握手 / 已断开 / 已停用 / 重试 N 次失败）。"""
    if result.reason == "never_started":
        return f"MCP 服务器 {server_name} 从未连接成功，自动重连未尝试；请在管理页手动连接后重试"
    if result.reason == "detached":
        return f"MCP 服务器 {server_name} 已被断开或删除，自动重连已取消；请在管理页重新连接后重试"
    if result.reason == "disabled":
        return f"MCP 服务器 {server_name} 已在管理页停用，自动重连已取消；请启用后再连接"
    if result.attempts:
        detail = f"：{result.reason}" if result.reason else ""
        return (
            f"MCP 服务器 {server_name} 自动重连失败（已尝试 {result.attempts} 次）"
            f"{detail}；请在管理页点击「重连」后重试"
        )
    return f"MCP 服务器 {server_name} 自动重连失败；请在管理页点击「重连」后重试"


def _reconnect_server(server: McpStdioServer) -> _ReconnectResult:
    """有界指数退避自动重连；成功后刷新工具注册表与 DB 状态。

    仅对「曾成功握手过」的实例生效：从未连接成功的配置可能已过期/命令错误，
    自动拉起只会反复失败并带来副作用，应由用户在管理页显式连接。
    锁内与 start() 成功后各复核一次「实例归属 + DB enabled」，避免用户在断开/
    停用后又被自动重连复活（M4）。
    """
    if not getattr(server, "_ever_started", False):
        return _ReconnectResult(False, 0, "never_started")
    try:
        max_attempts = max(1, int(RECONNECT_MAX_ATTEMPTS))
    except (TypeError, ValueError):
        max_attempts = 1
    with server._reconnect_lock:
        if not _server_still_managed(server):
            return _ReconnectResult(False, 0, "detached")
        if not _server_enabled_in_db(server.name):
            return _ReconnectResult(False, 0, "disabled")
        if server.is_alive():
            return _ReconnectResult(True, 0)
        last_error = ""
        for attempt in range(max_attempts):
            # 每次重试前都复核：用户可能在上一轮退避期间断开/停用
            if not _server_still_managed(server):
                return _ReconnectResult(False, attempt, "detached")
            if not _server_enabled_in_db(server.name):
                return _ReconnectResult(False, attempt, "disabled")
            try:
                server.start()
            except Exception as exc:  # noqa: BLE001  单次失败按退避重试
                last_error = str(exc)[:500]
                logger.warning(
                    "MCP 服务器 %s 自动重连第 %d/%d 次失败：%s",
                    server.name, attempt + 1, max_attempts, exc,
                )
                if attempt + 1 < max_attempts:
                    delay = min(RECONNECT_BASE_DELAY * (2 ** attempt), RECONNECT_MAX_DELAY)
                    if delay > 0:
                        time.sleep(delay)
                continue
            # 握手可能耗时数秒：成功后再次复核，用户中途断开/停用则立即放弃
            if not _server_still_managed(server):
                server.stop()
                return _ReconnectResult(False, attempt + 1, "detached")
            if not _server_enabled_in_db(server.name):
                server.stop()
                return _ReconnectResult(False, attempt + 1, "disabled")
            from .tools import register_module_tools

            _register_server_tools(server, register_module_tools)
            _sync_server_row(server, "connected")
            return _ReconnectResult(True, attempt + 1)
        # 归属/启用已变化时不要把 error 写回同名的新实例或已停用行
        if _server_still_managed(server) and _server_enabled_in_db(server.name):
            _sync_server_row(server, "error", last_error or "自动重连失败")
        return _ReconnectResult(False, max_attempts, last_error or "自动重连失败")


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
        for name, server in _servers.items():
            try:
                server.stop()
            except Exception:  # noqa: BLE001
                pass
            _unregister_server_tools(name)
        _servers.clear()


def _tool_name(server: str, tool: str) -> str:
    return f"mcp_{server}__{tool}"


def _tool_readonly(tool: dict) -> bool:
    """只读判定：以 MCP annotations.readOnlyHint 为唯一放行依据，缺省视为可写。

    名称启发式（不含 write/create/...）容易被任意命名绕过，不能作为放行依据；
    未声明注解的工具在只读模式下保守拒绝，宁可少用不可越权。
    """
    annotations = tool.get("annotations")
    if isinstance(annotations, dict):
        hint = annotations.get("readOnlyHint")
        if isinstance(hint, bool):
            return hint
    return False


def _unregister_server_tools(server_name: str) -> None:
    """移除某服务器注册过的全部工具（重连/改名/删除后避免 TOOL_SPECS 残留旧 schema）。"""
    from .tools import TOOL_SPECS

    prefix = _tool_name(server_name, "")
    for name in [n for n in TOOL_SPECS if n.startswith(prefix)]:
        TOOL_SPECS.pop(name, None)


def _register_server_tools(server: McpStdioServer, register) -> None:
    """把服务器工具注册进 TOOL_SPECS（覆盖同名旧定义；只读以 annotations 为准）。"""
    _unregister_server_tools(server.name)
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
            params[pname] = (ptype, str(pspec.get("description") or "")[:200])
            if pname in (schema.get("required") or []):
                required.append(pname)
        specs[_tool_name(server.name, name)] = {
            "func": _make_executor(server.name, name),
            "description": f"[MCP:{server.name}] {str(tool.get('description') or name)[:200]}",
            "params": params,
            "required": required,
            "readonly": _tool_readonly(tool),
        }
    register(specs)


def _make_executor(server_name: str, tool_name: str):
    """生成 Agent 可调的 async 工具函数（线程池执行，不阻塞事件循环）。"""
    import asyncio

    def _unknown_result(tool: str, exc: Exception, recovery: str) -> dict:
        """请求已发出但未拿到结果：只报「结果未知」，绝不让调用方误以为可安全重放。"""
        return {
            "ok": False,
            "result_unknown": True,
            "error": (
                f"MCP 工具 {tool} 调用结果未知（请求已发出后超时或连接中断）：{str(exc)[:200]}。"
                f"{recovery}；为避免重复执行，本次不会自动重试。"
                "请先确认服务器端执行结果，再决定是否重试。"
            ),
        }

    async def _call(**kwargs: Any) -> dict:
        server = _servers.get(server_name)
        if server is None:
            return {"ok": False, "error": f"MCP 服务器 {server_name} 未注册或已删除；请在管理页点击「重连」后重试"}
        if not server.is_alive():
            # 进程已退出：先做有界指数退避自动重连（L-028），失败才提示手动重连
            outcome = await asyncio.to_thread(_reconnect_server, server)
            if not outcome.ok:
                return {"ok": False, "error": _reconnect_failure_message(server_name, outcome)}
        try:
            result = await asyncio.to_thread(server.call_tool, tool_name, kwargs)
        except Exception as exc:  # noqa: BLE001  工具失败转为可读错误，不崩 Agent
            # 非 McpError 的未知异常保守视为「可能已发出」，宁可不重试也不能重复写
            sent = bool(getattr(exc, "sent", True))
            outcome = await asyncio.to_thread(_reconnect_server, server)
            if sent:
                recovery = "连接已自动恢复" if outcome.ok else "自动重连也失败"
                return _unknown_result(tool_name, exc, recovery)
            if not outcome.ok:
                return {"ok": False, "error": _reconnect_failure_message(server_name, outcome)}
            # 请求明确未发出（sent=False）：重连后允许重试一次
            try:
                result = await asyncio.to_thread(server.call_tool, tool_name, kwargs)
            except Exception as retry_exc:  # noqa: BLE001
                retry_sent = bool(getattr(retry_exc, "sent", True))
                retry_outcome = await asyncio.to_thread(_reconnect_server, server)
                if retry_sent:
                    recovery = "连接已自动恢复" if retry_outcome.ok else "自动重连也失败"
                    return _unknown_result(tool_name, retry_exc, recovery)
                return {
                    "ok": False,
                    "error": f"MCP 调用失败（重连后重试一次）：{str(retry_exc)[:200]}",
                }
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
    # 工具名按 mcp_<服务器名>__<工具名> 拼接，「断开/重连时按前缀清理旧工具」依赖
    # 名字里没有 __ 才能唯一切分；否则删除服务器 a__b 会连带清掉 a 的 b__* 工具。
    if "__" in name:
        raise ValueError("服务器名不能包含连续下划线 __（会与工具前缀冲突，导致其他服务器工具被误删）")
    if db.query(McpServer).filter(McpServer.name == name).first():
        raise ValueError(f"服务器名已存在：{name}")
    row = McpServer(name=name, command=command.strip(), args=args or [],
                    env=seal_env_values(env), enabled=enabled, status="stopped")
    db.add(row)
    db.commit()
    db.refresh(row)
    return _server_out(row)


def update_server(db: Session, server_id: int, data: dict) -> dict:
    row = db.get(McpServer, server_id)
    if row is None:
        raise LookupError("服务器不存在")
    # 配置变更前先停掉运行中的旧实例（对齐 delete/disconnect），避免旧进程+旧工具残留
    with _manager_lock:
        running = _servers.pop(row.name, None)
    if running:
        running.stop()
    _unregister_server_tools(row.name)
    # env 更新按界面掩码语义合并：值为 *** 的键保留已存秘密，未掩码键才真正替换
    if "env" in data and data["env"] is not None:
        old_env = unseal_env_values(row.env)
        merged = {
            str(k): (old_env[str(k)] if v == "***" and str(k) in old_env else v)
            for k, v in data["env"].items()
        }
        data["env"] = seal_env_values(merged)
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
    _unregister_server_tools(row.name)
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
    # 先清掉旧工具：连接失败时注册表里不能留下可调用的陈旧定义
    _unregister_server_tools(row.name)
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
    _unregister_server_tools(row.name)
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
