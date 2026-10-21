"""数据库连接与 ORM 基础设施。默认 SQLite，全部通过 SQLAlchemy 访问（自动参数绑定）。"""
import json
from pathlib import Path

from sqlalchemy import create_engine, event
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

_is_sqlite = settings.database_url.startswith("sqlite")
_engine_kwargs = {
    # 中文按原文存入 JSON 列，便于按标签做 LIKE 检索
    "json_serializer": lambda o: json.dumps(o, ensure_ascii=False),
    "json_deserializer": json.loads,
}
if _is_sqlite:
    _engine_kwargs["connect_args"] = {"check_same_thread": False}

engine = create_engine(settings.database_url, **_engine_kwargs)


if _is_sqlite:

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_connection, _record):
        """SQLite 连接级 PRAGMA。

        temp_store=2：排序与临时表的中间结果默认落盘到系统临时目录。Windows 上该目录不可写时
        （本机实测 TEMP 指向的目录返回 Permission denied），SQLite 直接抛
        SQLITE_CANTOPEN「unable to open database file」——表现为任务/笔记深翻页这类需要缓冲
        offset+limit 行的排序请求返回 500，而不是慢。改为内存临时库后同一 SQL 不再依赖临时目录；
        本项目是单机 SQLite（万行级），排序中间态的内存开销可忽略。
        """
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA temp_store=2")
        finally:
            cursor.close()


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
