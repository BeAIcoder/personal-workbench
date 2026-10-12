"""finance_hello 模块 ORM 模型（表名统一 mod_ 前缀）。"""
from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from ...database import Base


class HelloEntry(Base):
    """机制实证样例表。"""

    __tablename__ = "mod_hello_entry"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
