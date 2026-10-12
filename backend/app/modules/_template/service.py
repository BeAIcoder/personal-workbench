"""模块业务逻辑与 Agent 工具函数模板。

- 数据库访问统一经 SQLAlchemy 会话（路由层用 Depends(get_db) 注入；
  Agent 工具函数自行 `with SessionLocal() as db`）；
- Agent 工具返回结构化 dict，出错返回 {"ok": False, "error": ...} 而非抛异常，
  便于模型自行纠错重试；
- 跨项目引擎路径等配置从 app.config 读取（settings.finance_*），
  配置为空时在本模块内按默认相对路径推导（config 只存值，推导逻辑放这里）。
"""
from sqlalchemy import func
from sqlalchemy.orm import Session

from ...database import SessionLocal
from .models import ExampleEntry


def create_entry(db: Session, name: str) -> ExampleEntry:
    """新建一条示例条目。"""
    entry = ExampleEntry(name=name.strip()[:100])
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry


def list_entries(db: Session, limit: int = 20) -> list[ExampleEntry]:
    """按 id 倒序返回最近条目。"""
    limit = max(1, min(int(limit), 100))
    return db.query(ExampleEntry).order_by(ExampleEntry.id.desc()).limit(limit).all()


def example_ping() -> dict:
    """Agent 工具（readonly）：模块连通性自检，返回条目数。"""
    with SessionLocal() as db:
        total = db.query(func.count(ExampleEntry.id)).scalar() or 0
    return {"ok": True, "module": "finance_example", "entries": total}
