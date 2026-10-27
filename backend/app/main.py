"""FastAPI 应用入口。

启动（在 backend 目录下）：
    python -m uvicorn app.main:app --host 127.0.0.1 --port 8000

接口文档： http://127.0.0.1:8000/docs
"""
from contextlib import asynccontextmanager
import asyncio
import logging
from urllib.parse import urlsplit

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .agents import expert_packs
from .agents.secrets_box import ensure_dpapi_available
from .config import BASE_DIR, settings
from .database import Base, SessionLocal, engine
from .modules import collect_column_patches, load_modules
from .routers import (
    assistant,
    dashboard,
    experts,
    jobs,
    knowledge,
    mcp,
    model_probe,
    notes,
    schedules,
    search,
    skills,
    tasks,
)
from .seed import seed_agent_specs, seed_if_empty
from .scheduler import scheduler_loop
from .upload import UploadTooLargeError

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    # 启动强制校验 DPAPI 可用性（N-1）：缺 pywin32 时拒绝启动，
    # 绝不允许 API Key 静默降级为明文
    ensure_dpapi_available()
    # 启动时建表；数据库为空且允许时写入示例数据
    Base.metadata.create_all(bind=engine)
    _ensure_sqlite_columns()
    _ensure_sqlite_indexes()
    # 专家团队定义：仅表为空时灌入一次（属于配置数据，不受 seed_on_startup 控制）
    with SessionLocal() as db:
        seed_agent_specs(db)
    # 专家团文件化：确保专家包目录存在并完成种子复制（失败仅记日志，不影响启动）
    try:
        expert_packs.packs_dir()
    except Exception:
        logger.warning("专家包目录初始化跳过（不影响启动）", exc_info=True)
    # 技能包种子：首次启动把 seed_skills 复制进 data/skills（幂等；失败仅记日志）
    try:
        from .agents.skill_store import ensure_seed_packs

        ensure_seed_packs()
    except Exception:
        logger.warning("技能包种子初始化跳过", exc_info=True)
    # 调试中心种子问题：首次启动写入 data/debug/issues.json（幂等；失败仅记日志）
    try:
        from .modules.debug_center import service as debug_center_svc

        debug_center_svc.ensure_seed_issues()
    except Exception:
        logger.warning("调试中心种子初始化跳过", exc_info=True)
    # 模块工具预置补齐：load_modules 早于本步骤，新库场景下暂存的预置在此生效
    from .modules import apply_pending_presets

    apply_pending_presets()
    if settings.seed_on_startup:
        with SessionLocal() as db:
            seed_if_empty(db)
    # 安全提示：本应用面向本机单人使用设计，监听非回环地址会把 API Key 明文接口与
    # 技能/插件/Bash 执行面暴露给局域网，仅在你明确知晓风险时使用
    if settings.host not in ("127.0.0.1", "localhost", "::1"):
        logger.warning(
            "HOST=%s 为非回环地址：工作台面向本机单人设计，未做鉴权，请勿在不可信网络中开放。",
            settings.host,
        )
    # 定时 Agent 调度器：每 20s 检查一次到期任务（关停时随 lifespan 取消）
    scheduler_task = asyncio.create_task(scheduler_loop())
    # MCP 服务器：启动启用的 stdio 服务器并注册其工具（单服务器失败不影响宿主）
    from .agents import mcp as mcp_manager

    # 存量 MCP env 明文一次性迁移为 DPAPI 格式（与 LLM Key 同口径）；失败仅记日志，
    # 新写入/更新已强制密封，不会因为迁移失败退回明文
    try:
        with SessionLocal() as db:
            migrated_env = mcp_manager.migrate_env_secrets(db)
        if migrated_env:
            logger.info("MCP env 明文迁移：%d 个服务器已密封", migrated_env)
    except Exception:
        logger.warning("MCP env 明文迁移跳过（不影响启动）", exc_info=True)
    try:
        mcp_results = mcp_manager.start_all_from_db()
        for name, status in mcp_results.items():
            logger.info("MCP 服务器 %s：%s", name, status)
    except Exception:
        logger.warning("MCP 服务器批量启动跳过", exc_info=True)
    yield
    scheduler_task.cancel()
    mcp_manager.stop_all()


def _ensure_sqlite_columns() -> None:
    """轻量迁移：为历史库补齐后加的列（SQLite 的 create_all 不会改已有表）。

    补丁来源：宿主内置补丁 + 各模块 manifest.column_patches（Module Registry）。
    """
    from sqlalchemy import text

    patches = {
        "chat_messages": {"thinking": "TEXT DEFAULT ''"},
        "agent_sessions": {"archived": "INTEGER DEFAULT 0"},
        "agent_settings": {
            "active_profile_id": "INTEGER",
            "active_model_id": "INTEGER",
            "enable_skills": "INTEGER DEFAULT 0",
            "skills_dir": "TEXT DEFAULT ''",
            "enable_bash": "INTEGER DEFAULT 0",
            "enable_plugins": "INTEGER DEFAULT 0",
            "plugins_dir": "TEXT DEFAULT ''",
            "reasoning_effort": "TEXT DEFAULT ''",
        },
        "scheduled_jobs": {
            "cron_expr": "TEXT DEFAULT ''",
            "action": "TEXT DEFAULT ''",
        },
        "model_providers": {
            "preset_key": "TEXT DEFAULT ''",
            "models_url": "TEXT DEFAULT ''",
            "sync_credentials": "TEXT DEFAULT ''",
        },
        "provider_models": {
            "temperature": "TEXT DEFAULT ''",
            "note": "TEXT DEFAULT ''",
            "supports_thinking": "INTEGER DEFAULT -1",
            "supports_vision": "INTEGER DEFAULT 0",
            "thinking_levels": "TEXT DEFAULT ''",
            "probed_context": "INTEGER DEFAULT 0",
            "probed_max_output": "INTEGER DEFAULT 0",
            "probe_result": "TEXT DEFAULT ''",
            "probed_at": "TEXT DEFAULT ''",
            "max_input_tokens": "INTEGER DEFAULT 0",
            "supports_tools": "INTEGER DEFAULT -1",
            "capability_meta": "TEXT DEFAULT '{}'",
            "synced_at": "TEXT DEFAULT ''",
        },
    }
    for table, columns in collect_column_patches().items():
        patches.setdefault(table, {}).update(columns)
    try:
        with engine.begin() as conn:
            for table, columns in patches.items():
                existing = [row[1] for row in conn.execute(text(f"PRAGMA table_info({table})"))]
                if not existing:
                    continue
                for col, ddl in columns.items():
                    if col not in existing:
                        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}"))
    except Exception:
        logger.warning("SQLite 轻量迁移跳过（非 SQLite 或已迁移过）", exc_info=True)


def _ensure_sqlite_indexes() -> None:
    """轻量迁移：为历史库补齐后加的索引。

    create_all 只会建新表，不会给「已存在的表」补索引；所以模型里后加的 index=True / Index()
    在老库上永远不生效——排序仍走全表扫描 + 临时 B 树。本函数按元数据逐条建缺失索引
    （checkfirst=True，等价于 CREATE INDEX IF NOT EXISTS），对新库/老库都幂等。
    """
    if not settings.database_url.startswith("sqlite"):
        return
    from sqlalchemy import inspect as sa_inspect

    inspector = sa_inspect(engine)
    created = []
    for table in Base.metadata.sorted_tables:
        if not inspector.has_table(table.name):
            continue
        existing = {i["name"] for i in inspector.get_indexes(table.name)}
        for index in table.indexes:
            if index.name in existing:
                continue
            try:
                # 逐条 try：某张表建失败只跳过它，不能连带丢掉后面的索引
                index.create(bind=engine, checkfirst=True)
                created.append(f"{table.name}.{index.name}")
            except Exception:
                logger.warning("索引 %s 补齐失败（不影响启动）", index.name, exc_info=True)
    if created:
        logger.info("SQLite 索引补齐：新建 %d 个（历史库升级后首次启动）：%s", len(created), ", ".join(created))


app = FastAPI(title=settings.app_name, version=settings.version, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# 全局 ASGI 请求体上限（审计 A01）：容纳现有最大的 100MB 发票批次上传 +
# multipart 开销，同时把声明了超大 Content-Length 的请求体挡在路由之前，
# 避免超大 body 在路由做长度检查前就耗尽内存/临时盘
MAX_REQUEST_BODY_BYTES = 128 * 1024 * 1024


class _RequestBodyTooLarge(Exception):
    """请求体超过全局上限：中间件内部信号，收口为 413。"""


def _content_length(scope: Scope) -> int | None:
    raw = Headers(scope=scope).get("content-length")
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


async def _respond_too_large(scope: Scope, receive: Receive, send: Send, max_bytes: int) -> None:
    response = JSONResponse(status_code=413, content={"detail": f"请求体超过 {max_bytes} 字节上限"})
    await response(scope, receive, send)


class RequestBodyLimitMiddleware:
    """全局请求体护栏（审计 A01）：Content-Length 早拒 + receive 累计。

    双层覆盖：声明了超大 Content-Length 的请求进入路由前直接 413；
    无 Content-Length 的 chunked 请求体在 receive 侧累计计数，超限即 413
    并中断读取。413 由中间件在 receive 内直接发出、并丢弃应用的后续响应——
    否则宽容型路由（如报错采集的 except Exception）会把超限异常吞掉，
    客户端看到的是 200/201 而不是 413。无论路由吞不吞异常、重复读取多少
    次，413 只发一次。上限为模块级常量，测试可 monkeypatch 小值验证两条路径。
    """

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        max_bytes = MAX_REQUEST_BODY_BYTES
        declared = _content_length(scope)
        if declared is not None and declared > max_bytes:
            await _respond_too_large(scope, receive, send, max_bytes)
            return

        total = 0
        responded = False
        response_started = False

        async def receive_with_limit() -> Message:
            nonlocal total, responded
            message = await receive()
            if message["type"] == "http.request":
                total += len(message.get("body", b""))
                if total > max_bytes:
                    # responded 去重：宽容路由吞掉异常后再次读取时不得补发 413
                    # （ASGI 单响应契约）；response_started 时应用响应已开始，
                    # 状态码已定，同样无从补发
                    if not responded and not response_started:
                        responded = True
                        await _respond_too_large(scope, receive, send, max_bytes)
                    raise _RequestBodyTooLarge
            return message

        async def send_watch(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            if responded:
                # 413 已由本中间件发出：丢弃应用后续响应，避免双响应
                return
            await send(message)

        try:
            await self.app(scope, receive_with_limit, send_watch)
        except _RequestBodyTooLarge:
            # 应用自己的响应已开始则无法再改状态码，交回上层记录；
            # 其余情况（含路由吞掉异常后继续跑）413 都已发出，就此收口
            if response_started and not responded:
                raise


app.add_middleware(RequestBodyLimitMiddleware)


# Origin 校验放行的回环开发来源：本机单人工作台，回环任意端口均视为可信开发来源
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
# /api 状态变更方法：可改变本机状态的接口，必须验证请求来源
_STATE_CHANGING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def _split_origin(origin: str) -> tuple[str, str, int | None]:
    parsed = urlsplit(origin)
    try:
        port = parsed.port
    except ValueError:
        port = None
    return parsed.scheme.lower(), (parsed.hostname or "").lower(), port


def _split_host_header(host: str) -> tuple[str, int | None]:
    parsed = urlsplit("//" + host)
    try:
        port = parsed.port
    except ValueError:
        port = None
    return (parsed.hostname or "").lower(), port


def _origin_allowed(origin: str, host_header: str) -> bool:
    """来源可信判定：显式 CORS 配置 / 回环开发来源 / 运维配置的 HOST 同源。

    不再把「请求自带的 Origin」与「请求自带的 Host」互证：攻击者若能同时控制
    两者（DNS rebinding），同源回退会循环放行。唯一保留的同源放行是 Origin 主机
    等于启动配置 `settings.host`（运维显式配置，而非请求头自证）。
    """
    if origin in {o.strip() for o in settings.cors_origins.split(",") if o.strip()}:
        return True
    scheme, host, port = _split_origin(origin)
    if scheme not in ("http", "https"):
        return False
    if host in _LOOPBACK_HOSTS:
        return True
    configured_host = (settings.host or "").lower()
    if configured_host and host == configured_host and port in (None, settings.port):
        return True
    return False


def _needs_origin_guard(method: str, path: str) -> bool:
    """校验范围：/api 状态变更方法 + /api 下的发现接口（目录扫描/插件加载类 GET）。

    普通 /api 读取不做 Origin 硬拦：跨站读取响应已由 CORS 兜底，硬拦只会
    破坏「浏览器直接打开 /api 链接」等既有用法（现有 API 默认兼容）。
    """
    if not path.startswith("/api"):
        return False
    if method in _STATE_CHANGING_METHODS:
        return True
    return "discover" in path.split("/")


class OriginGuardMiddleware:
    """Origin 校验（审计 A02）：工作台面向本机单人、无鉴权，必须防止任意网页
    驱动 /api 状态变更与发现接口（如插件目录扫描）。放行：无 Origin 的
    CLI/探针、回环开发来源、显式配置的 CORS 来源，以及运维配置 HOST 的同源来源；
    其余一律 403。CORS 预选（OPTIONS）不拦，交由 CORSMiddleware 处理。
    """

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            method = scope.get("method", "").upper()
            path = scope.get("path", "")
            if _needs_origin_guard(method, path):
                headers = Headers(scope=scope)
                origin = (headers.get("origin") or "").strip()
                if origin and not _origin_allowed(origin, headers.get("host") or ""):
                    logger.warning("Origin 校验拒绝：%s %s（Origin: %s）", method, path, origin)
                    response = JSONResponse(status_code=403, content={"detail": "请求来源不被信任"})
                    await response(scope, receive, send)
                    return
        await self.app(scope, receive, send)


app.add_middleware(OriginGuardMiddleware)


@app.api_route("/api/health", methods=["GET", "HEAD"], tags=["系统"], summary="健康检查")
def health():
    # HEAD 供容器/负载均衡探活（无响应体），GET 返回完整状态
    return {"status": "ok", "app": settings.app_name, "version": settings.version}


@app.exception_handler(OverflowError)
def _overflow_error_as_422(request, exc):
    """超 int64 的 ID 统一收口为 422，而不是 500 + 堆栈外泄。

    SQLite 主键是 int64，把 9223372036854775808 这类值绑进查询会在驱动层抛
    OverflowError（「Python int too large to convert to SQLite INTEGER」）。核心
    CRUD 路由已在入口用 utils.DbId 拦下；财务/调试等模块路由由各自 manifest 挂载、
    数量多，这里做全局兜底，保证任何一条 /{id} 路径都不会因为越界 ID 而 500。
    """
    logger.warning("参数超出整数范围：%s %s", request.method, request.url.path)
    return JSONResponse(status_code=422, content={"detail": "ID 超出可接受范围"})


@app.exception_handler(UploadTooLargeError)
def _upload_too_large_as_400(request, exc: UploadTooLargeError):
    """上传超限统一收口 400：各上传路由复用 read_upload_limited 即自动生效，
    无需每处自行 try/except。"""
    logger.warning("上传超过限额：%s %s（上限 %d 字节）", request.method, request.url.path, exc.max_bytes)
    return JSONResponse(status_code=400, content={"detail": str(exc)})


app.include_router(tasks.router, prefix="/api")
app.include_router(schedules.router, prefix="/api")
app.include_router(notes.router, prefix="/api")
app.include_router(search.router, prefix="/api")
app.include_router(dashboard.router, prefix="/api")
app.include_router(assistant.router, prefix="/api")
app.include_router(jobs.router, prefix="/api")
app.include_router(mcp.router, prefix="/api")
app.include_router(knowledge.router, prefix="/api")
app.include_router(skills.router, prefix="/api")
app.include_router(experts.router, prefix="/api")
# 模型能力探测：/api/models/{id}/probe/*（同步执行，探测失败以 200 + error 返回）
app.include_router(model_probe.router, prefix="/api")
# 预设列表按契约挂 /api/providers/presets；同时挂一份 /api/models/providers/presets 别名，
# 兼容「router prefix=/models」字面解读出的路径，避免前后端联调歧义
app.include_router(model_probe.presets_router, prefix="/api")
app.include_router(model_probe.presets_router, prefix="/api/models")

# 模块注册机制（Module Registry）：在模块级作用域加载——静态 include_router 之后、
# lifespan 之前。导入各模块 manifest 时连带注册模块表进 Base.metadata，
# lifespan 的 create_all 才能建出模块表；任何模块失败仅记日志跳过，不影响宿主。
load_modules(app)

class FrontendStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope: Scope):
        response = await super().get_response(path, scope)
        if response.status_code == 200 and response.headers.get("content-type", "").startswith("text/html"):
            response.headers["Cache-Control"] = "no-cache"
        return response


# 前端已构建（frontend/dist 存在）时由后端直接托管页面，单端口访问
FRONTEND_DIST = BASE_DIR.parent / "frontend" / "dist"
if FRONTEND_DIST.is_dir():
    app.mount("/", FrontendStaticFiles(directory=str(FRONTEND_DIST), html=True), name="frontend")
