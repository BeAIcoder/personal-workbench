"""FastAPI 应用入口。

启动（在 backend 目录下）：
    python -m uvicorn app.main:app --host 127.0.0.1 --port 8000

接口文档： http://127.0.0.1:8000/docs
"""
from contextlib import asynccontextmanager
import asyncio
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

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

# 前端已构建（frontend/dist 存在）时由后端直接托管页面，单端口访问
FRONTEND_DIST = BASE_DIR.parent / "frontend" / "dist"
if FRONTEND_DIST.is_dir():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIST), html=True), name="frontend")
