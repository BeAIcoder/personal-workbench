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
from fastapi.staticfiles import StaticFiles

from .agents.secrets_box import ensure_dpapi_available
from .config import BASE_DIR, settings
from .database import Base, SessionLocal, engine
from .modules import collect_column_patches, load_modules
from .routers import assistant, dashboard, jobs, knowledge, mcp, notes, schedules, search, tasks
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
    # 专家团队定义：仅表为空时灌入一次（属于配置数据，不受 seed_on_startup 控制）
    with SessionLocal() as db:
        seed_agent_specs(db)
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


app = FastAPI(title=settings.app_name, version=settings.version, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health", tags=["系统"], summary="健康检查")
def health():
    return {"status": "ok", "app": settings.app_name, "version": settings.version}


app.include_router(tasks.router, prefix="/api")
app.include_router(schedules.router, prefix="/api")
app.include_router(notes.router, prefix="/api")
app.include_router(search.router, prefix="/api")
app.include_router(dashboard.router, prefix="/api")
app.include_router(assistant.router, prefix="/api")
app.include_router(jobs.router, prefix="/api")
app.include_router(mcp.router, prefix="/api")
app.include_router(knowledge.router, prefix="/api")

# 模块注册机制（Module Registry）：在模块级作用域加载——静态 include_router 之后、
# lifespan 之前。导入各模块 manifest 时连带注册模块表进 Base.metadata，
# lifespan 的 create_all 才能建出模块表；任何模块失败仅记日志跳过，不影响宿主。
load_modules(app)

# 前端已构建（frontend/dist 存在）时由后端直接托管页面，单端口访问
FRONTEND_DIST = BASE_DIR.parent / "frontend" / "dist"
if FRONTEND_DIST.is_dir():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIST), html=True), name="frontend")
