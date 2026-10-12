"""模块 ORM 模型模板：表名统一 mod_ 前缀，与宿主既有表隔离。"""
from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from ...database import Base


class ExampleEntry(Base):
    """示例表（替换为自己的模型）。"""

    __tablename__ = "mod_example_entry"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
