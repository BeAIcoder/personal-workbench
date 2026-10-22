"""core-request-security 任务聚焦测试（Swarm 白名单内仅此一个测试文件）。

对应审计台账（2026-10-22 全面检查报告）：
- A01：各上传路由先 `await file.read()` 再限长、main.py 无全局 body 上限
  → 共享分块读取 read_upload_limited + ASGI Content-Length/receive 双层限额；
- A02：main.py 仅有 CORS、无 Origin 防护，插件发现可被任意网页驱动
  → Origin 校验（无 Origin 的 CLI / 回环 / 同源 / settings.cors_origins 放行）；
- A05：调试中心 `request.json()` 无局部上限 → 1MB 有界 + 宽容解析不 500；
- A06：Excel/CSV 无统一行数上限 → MAX_IMPORT_ROWS 可测试替换。

口径：限额类测试一律 monkeypatch 小值，不生成巨型文件；请求体防护为 ASGI 级
流测试——直接驱动 app(scope, receive, send)，覆盖 Content-Length 早拒与
chunked 累计两条路径，不经过 TestClient 封装。
"""
import asyncio
import json

import pytest

from app.main import app

P_CLIENT_ERRORS = "/api/debug/client-errors"
EVIL_ORIGIN = "http://evil.example.com"


class _FakeUpload:
    """最小 UploadFile 替身：按 size 分片吐字节，记录每次读取请求的大小。"""

    def __init__(self, data: bytes):
        self._data = data
        self._pos = 0
        self.read_sizes: list[int] = []

    async def read(self, size: int = -1) -> bytes:
        self.read_sizes.append(size)
        if size is None or size < 0:
            size = len(self._data) - self._pos
        chunk = self._data[self._pos : self._pos + size]
        self._pos += len(chunk)
        return chunk


def _hdrs(**pairs: str) -> list[tuple[bytes, bytes]]:
    return [(k.lower().encode("latin-1"), v.encode("latin-1")) for k, v in pairs.items()]


async def _drive_asgi(method: str, path: str, chunks: list[bytes], headers, query_string: bytes = b""):
    """ASGI 级流测试驱动：直接调用 app，返回 (status, headers, body)。

    chunks 为请求体分片；无 Content-Length 头时等价于 chunked 传输。
    """
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("utf-8"),
        "query_string": query_string,
        "root_path": "",
        "headers": headers,
        "client": ("127.0.0.1", 51234),
        "server": ("127.0.0.1", 8000),
    }
    inbox = [
        {"type": "http.request", "body": chunk, "more_body": index < len(chunks) - 1}
        for index, chunk in enumerate(chunks)
    ] or [{"type": "http.request", "body": b"", "more_body": False}]
    sent: list[dict] = []

    async def receive():
        if inbox:
            return inbox.pop(0)
        return {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)

    await app(scope, receive, send)
    start = next((m for m in sent if m["type"] == "http.response.start"), None)
    assert start is not None, "ASGI 响应缺少 http.response.start"
    resp_headers = {k.decode("latin-1"): v.decode("latin-1") for k, v in start.get("headers", [])}
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return start["status"], resp_headers, body


def _post_client_error(origin: str | None = None, payload: bytes = b"{}", host: str | None = None):
    headers = {"content-type": "application/json"}
    if origin:
        headers["origin"] = origin
    if host:
        headers["host"] = host
    return asyncio.run(_drive_asgi("POST", P_CLIENT_ERRORS, [payload], _hdrs(**headers)))


# ---------- 共享分块上传读取（A01） ----------


def test_read_upload_limited_returns_full_content():
    from app.upload import read_upload_limited

    data = b"abcdefgh" * 1000  # 8KB，跨多个分片
    result = asyncio.run(read_upload_limited(_FakeUpload(data), max_bytes=64 * 1024, chunk_size=1000))
    assert result == data


def test_read_upload_limited_raises_over_limit():
    from app.upload import UploadTooLargeError, read_upload_limited

    with pytest.raises(UploadTooLargeError) as excinfo:
        asyncio.run(read_upload_limited(_FakeUpload(b"x" * 5000), max_bytes=1000, chunk_size=512))
    assert excinfo.value.max_bytes == 1000


def test_read_upload_limited_stops_reading_after_limit():
    """超限即止：不能把整个超大内容读完再抛（分块限额的内存意义所在）。"""
    from app.upload import UploadTooLargeError, read_upload_limited

    fake = _FakeUpload(b"z" * 10_000)
    with pytest.raises(UploadTooLargeError):
        asyncio.run(read_upload_limited(fake, max_bytes=1000, chunk_size=1000))
    assert fake._pos <= 2 * 1000


def test_read_upload_limited_default_chunk_size_is_1mb():
    from app.upload import read_upload_limited

    fake = _FakeUpload(b"y" * (1024 * 1024 + 7))
    result = asyncio.run(read_upload_limited(fake, max_bytes=4 * 1024 * 1024))
    assert len(result) == 1024 * 1024 + 7
    assert fake.read_sizes == [1024 * 1024, 1024 * 1024, 1024 * 1024]


def test_max_import_rows_is_200k():
    from app.import_limits import MAX_IMPORT_ROWS

    assert MAX_IMPORT_ROWS == 200_000


def test_upload_too_large_maps_to_400():
    """UploadTooLargeError 经全局异常处理器收口 400，各上传路由复用即生效。"""
    from fastapi import Request

    from app.main import _upload_too_large_as_400
    from app.upload import UploadTooLargeError

    scope = {"type": "http", "method": "POST", "path": "/api/x", "headers": [], "query_string": b""}
    response = _upload_too_large_as_400(Request(scope), UploadTooLargeError(1000))
    assert response.status_code == 400


# ---------- 全局 ASGI 请求体防护（A01） ----------


def test_global_body_limit_content_length_early_reject():
    """声明了超大 Content-Length 的请求在进路由前即 413，不读取 body。"""
    status, _, _ = asyncio.run(
        _drive_asgi(
            "POST",
            P_CLIENT_ERRORS,
            [],
            _hdrs(**{"content-length": str(999_999_999_999), "content-type": "application/json"}),
        )
    )
    assert status == 413


def test_global_body_limit_chunked_over_limit(monkeypatch):
    """无 Content-Length 的 chunked 请求体：receive 累计超限即 413（monkeypatch 小上限）。"""
    import app.main as main

    monkeypatch.setattr(main, "MAX_REQUEST_BODY_BYTES", 1024)
    status, _, _ = asyncio.run(
        _drive_asgi(
            "POST",
            P_CLIENT_ERRORS,
            [b"x" * 600, b"y" * 600],
            _hdrs(**{"content-type": "application/json"}),
        )
    )
    assert status == 413


def test_global_body_limit_accommodates_100mb_upload():
    """全局上限必须容纳现有最大 100MB 上传批次 + multipart 开销，又不放任到 GB 级。"""
    from app.main import MAX_REQUEST_BODY_BYTES

    assert MAX_REQUEST_BODY_BYTES >= 100 * 1024 * 1024
    assert MAX_REQUEST_BODY_BYTES < 1024 * 1024 * 1024


def test_global_body_limit_allows_normal_requests():
    """正常小请求体照常通过（现有 API 默认兼容），chunked 也不例外。"""
    payload = json.dumps({"message": "hello"}).encode()
    status, _, body = asyncio.run(
        _drive_asgi("POST", P_CLIENT_ERRORS, [payload[:10], payload[10:]], _hdrs(**{"content-type": "application/json"}))
    )
    assert status == 201
    assert json.loads(body)["message"] == "hello"


def test_global_body_limit_content_length_within_limit_passes():
    payload = json.dumps({"message": "small"}).encode()
    status, _, _ = asyncio.run(
        _drive_asgi(
            "POST",
            P_CLIENT_ERRORS,
            [payload],
            _hdrs(**{"content-length": str(len(payload)), "content-type": "application/json"}),
        )
    )
    assert status == 201


def _mw_scope() -> dict:
    return {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/x",
        "raw_path": b"/api/x",
        "query_string": b"",
        "root_path": "",
        "headers": _hdrs(**{"content-type": "application/json"}),
        "client": ("127.0.0.1", 51234),
        "server": ("127.0.0.1", 8000),
    }


def test_global_body_limit_no_duplicate_413_after_app_swallows_and_rereads(monkeypatch):
    """宽容路由吞掉超限信号后再次读取 body：413 只能发出一次（ASGI 单响应契约）。

    直接驱动 RequestBodyLimitMiddleware：内层应用模拟「except Exception 后继续读」
    的宽容路由。超限时中间件已发出 413 并置 responded，第二次读取不得再发一次响应。
    """
    import app.main as main

    monkeypatch.setattr(main, "MAX_REQUEST_BODY_BYTES", 1024)

    async def tolerant_app(scope, receive, send):
        for _ in range(3):
            try:
                await receive()
            except main._RequestBodyTooLarge:
                pass
        await send({"type": "http.response.start", "status": 201, "headers": []})
        await send({"type": "http.response.body", "body": b"{}"})

    sent: list[dict] = []

    async def send(message):
        sent.append(message)

    inbox = [
        {"type": "http.request", "body": b"x" * 600, "more_body": True},
        {"type": "http.request", "body": b"y" * 600, "more_body": True},
        {"type": "http.request", "body": b"z" * 600, "more_body": False},
    ]

    async def receive():
        if inbox:
            return inbox.pop(0)
        return {"type": "http.disconnect"}

    asyncio.run(main.RequestBodyLimitMiddleware(tolerant_app)(_mw_scope(), receive, send))
    starts = [m["status"] for m in sent if m["type"] == "http.response.start"]
    assert starts == [413]


def test_global_body_limit_no_413_after_app_response_started(monkeypatch):
    """应用响应已开始后才超限：不得补发 413（状态码已定），信号交回上层。"""
    import app.main as main

    monkeypatch.setattr(main, "MAX_REQUEST_BODY_BYTES", 1024)

    async def streaming_app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await receive()  # 超限且不吞：信号穿透到中间件
        await send({"type": "http.response.body", "body": b"partial"})

    sent: list[dict] = []

    async def send(message):
        sent.append(message)

    async def receive():
        return {"type": "http.request", "body": b"x" * 2000, "more_body": False}

    with pytest.raises(main._RequestBodyTooLarge):
        asyncio.run(main.RequestBodyLimitMiddleware(streaming_app)(_mw_scope(), receive, send))
    starts = [m["status"] for m in sent if m["type"] == "http.response.start"]
    assert starts == [200]


# ---------- Origin 校验（A02） ----------


def test_foreign_origin_state_change_rejected():
    status, _, body = _post_client_error(origin=EVIL_ORIGIN)
    assert status == 403
    assert "detail" in json.loads(body)


def test_null_origin_state_change_rejected():
    status, _, _ = _post_client_error(origin="null")
    assert status == 403


def test_foreign_origin_discover_endpoint_rejected():
    """发现接口（目录扫描/插件加载）同样拒绝任意 Origin。"""
    status, _, _ = asyncio.run(
        _drive_asgi("GET", "/api/assistant/plugins/discover", [], _hdrs(origin=EVIL_ORIGIN), query_string=b"dir=no_such_dir")
    )
    assert status == 403


def test_discover_endpoint_without_origin_allowed():
    status, _, _ = asyncio.run(
        _drive_asgi("GET", "/api/assistant/plugins/discover", [], _hdrs(), query_string=b"dir=no_such_dir")
    )
    assert status == 200


def test_state_change_without_origin_allowed_cli():
    """无 Origin 的 CLI/探针请求放行（回归：现有测试与 e2e 探针不带 Origin）。"""
    status, _, _ = _post_client_error(origin=None)
    assert status == 201


def test_state_change_allows_loopback_dev_origins():
    for origin in ("http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:59999", "http://127.0.0.1:8000"):
        status, _, _ = _post_client_error(origin=origin)
        assert status == 201, origin


def test_state_change_allows_configured_cors_origin(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "cors_origins", "http://localhost:5173,https://desk.example.com")
    status, _, _ = _post_client_error(origin="https://desk.example.com")
    assert status == 201


def test_state_change_allows_configured_host_same_origin(monkeypatch):
    """运维显式配置 HOST 时，该 HOST 的同源来源放行（前端由后端同端口托管不误伤）。"""
    from app.config import settings

    monkeypatch.setattr(settings, "host", "workbench.local")
    monkeypatch.setattr(settings, "port", 8000)
    status, _, _ = _post_client_error(origin="http://workbench.local:8000", host="workbench.local:8000")
    assert status == 201


def test_state_change_rejects_unconfigured_same_origin_host():
    """未配置 HOST 时，Origin/Host 自证同源不再放行（DNS rebinding 防护）。"""
    status, _, _ = _post_client_error(origin="http://workbench.local:8000", host="workbench.local:8000")
    assert status == 403


def test_foreign_origin_plain_read_not_blocked():
    """范围口径：普通 /api 读取（GET）不做 Origin 硬拦，跨站读取由 CORS 兜底。"""
    status, _, _ = asyncio.run(_drive_asgi("GET", "/api/debug/issues", [], _hdrs(origin=EVIL_ORIGIN)))
    assert status == 200


# ---------- CORS / CLI 回归 ----------


def test_cors_preflight_allowed_origin_still_works():
    status, headers, _ = asyncio.run(
        _drive_asgi(
            "OPTIONS",
            P_CLIENT_ERRORS,
            [],
            _hdrs(origin="http://localhost:5173", **{"access-control-request-method": "POST"}),
        )
    )
    assert status == 200
    assert headers.get("access-control-allow-origin") == "http://localhost:5173"


def test_cors_preflight_foreign_origin_has_no_acao():
    status, headers, _ = asyncio.run(
        _drive_asgi(
            "OPTIONS",
            P_CLIENT_ERRORS,
            [],
            _hdrs(origin=EVIL_ORIGIN, **{"access-control-request-method": "POST"}),
        )
    )
    assert status == 400
    assert "access-control-allow-origin" not in headers


def test_cors_actual_request_keeps_headers_and_origin_check_passes():
    status, headers, _ = _post_client_error(origin="http://localhost:5173")
    assert status == 201
    assert headers.get("access-control-allow-origin") == "http://localhost:5173"


def test_foreign_origin_state_change_rejected_without_cors_headers():
    status, headers, _ = _post_client_error(origin=EVIL_ORIGIN)
    assert status == 403
    assert "access-control-allow-origin" not in headers


# ---------- 调试中心有界且宽容 JSON（A05） ----------


def test_client_errors_default_local_bound_is_1mb():
    from app.modules.debug_center import router as debug_router

    assert debug_router.CLIENT_ERROR_MAX_BODY_BYTES == 1 * 1024 * 1024


def test_client_errors_local_bound_rejects_oversized(monkeypatch):
    from app.modules.debug_center import router as debug_router

    monkeypatch.setattr(debug_router, "CLIENT_ERROR_MAX_BODY_BYTES", 1024)
    payload = json.dumps({"message": "x" * 2000}).encode()
    status, _, body = asyncio.run(
        _drive_asgi("POST", P_CLIENT_ERRORS, [payload[:512], payload[512:]], _hdrs(**{"content-type": "application/json"}))
    )
    assert status == 400
    assert "超过" in body.decode("utf-8", "replace")


def test_client_errors_invalid_json_still_tolerated():
    """宽容解析回归：非法 JSON / 空 body 一律 201 归一记录，绝不 500。"""
    status, _, _ = asyncio.run(
        _drive_asgi("POST", P_CLIENT_ERRORS, [b"this is not json"], _hdrs(**{"content-type": "application/json"}))
    )
    assert status == 201
    status, _, _ = asyncio.run(_drive_asgi("POST", P_CLIENT_ERRORS, [b""], _hdrs()))
    assert status == 201


def test_client_errors_chunked_valid_json_tolerated():
    payload = json.dumps({"page": "/tasks", "message": "boom"})
    status, _, body = asyncio.run(
        _drive_asgi(
            "POST",
            P_CLIENT_ERRORS,
            [payload[:10].encode(), payload[10:].encode()],
            _hdrs(**{"content-type": "application/json"}),
        )
    )
    assert status == 201
    assert json.loads(body)["message"] == "boom"
