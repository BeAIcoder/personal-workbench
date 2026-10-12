"""finance_hello 模块业务逻辑与 Agent 工具函数。"""
from sqlalchemy import func
from sqlalchemy.orm import Session

from ...database import SessionLocal
from .models import HelloEntry


def create_entry(db: Session, name: str) -> HelloEntry:
    """新建一条样例条目。"""
    entry = HelloEntry(name=name.strip()[:100])
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry


def list_entries(db: Session, limit: int = 20) -> list[HelloEntry]:
    """按 id 倒序返回最近条目。"""
    limit = max(1, min(int(limit), 100))
    return db.query(HelloEntry).order_by(HelloEntry.id.desc()).limit(limit).all()


def hello_ping() -> dict:
    """Agent 工具（readonly）：财务模块连通性自检，返回模块状态与条目数。"""
    with SessionLocal() as db:
        total = db.query(func.count(HelloEntry.id)).scalar() or 0
    return {"ok": True, "module": "finance_hello", "entries": total}
