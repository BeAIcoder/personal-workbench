"""FastAPI 应用入口。

启动（在 backend 目录下）：
    python -m uvicorn app.main:app --host 127.0.0.1 --port 8000

接口文档： http://127.0.0.1:8000/docs
"""
from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .config import BASE_DIR, settings
from .database import Base, SessionLocal, engine
from .routers import assistant, dashboard, notes, schedules, search, tasks
from .seed import seed_agent_specs, seed_if_empty

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    # 启动时建表；数据库为空且允许时写入示例数据
    Base.metadata.create_all(bind=engine)
    _ensure_sqlite_columns()
    # 专家团队定义：仅表为空时灌入一次（属于配置数据，不受 seed_on_startup 控制）
    with SessionLocal() as db:
        seed_agent_specs(db)
    if settings.seed_on_startup:
        with SessionLocal() as db:
            seed_if_empty(db)
    yield


def _ensure_sqlite_columns() -> None:
    """轻量迁移：为历史库补齐后加的列（SQLite 的 create_all 不会改已有表）。"""
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
    }
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

# 前端已构建（frontend/dist 存在）时由后端直接托管页面，单端口访问
FRONTEND_DIST = BASE_DIR.parent / "frontend" / "dist"
if FRONTEND_DIST.is_dir():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIST), html=True), name="frontend")
