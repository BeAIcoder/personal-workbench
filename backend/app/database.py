"""数据库连接与 ORM 基础设施。默认 SQLite，全部通过 SQLAlchemy 访问（自动参数绑定）。"""
import json
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import settings


def _prepare_sqlite_path(url: str) -> None:
    """确保 SQLite 数据库文件所在目录存在。"""
    prefix = "sqlite:///"
    if not url.startswith(prefix):
        return
    db_path = url[len(prefix):]
    if db_path == ":memory:":
        return
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)


_prepare_sqlite_path(settings.database_url)

_engine_kwargs = {
    # 中文按原文存入 JSON 列，便于按标签做 LIKE 检索
    "json_serializer": lambda o: json.dumps(o, ensure_ascii=False),
    "json_deserializer": json.loads,
}
if settings.database_url.startswith("sqlite"):
    _engine_kwargs["connect_args"] = {"check_same_thread": False}

engine = create_engine(settings.database_url, **_engine_kwargs)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


class Base(DeclarativeBase):
    """所有 ORM 模型的基类。"""


def get_db():
    """FastAPI 依赖：每个请求一个会话，请求结束自动关闭。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
