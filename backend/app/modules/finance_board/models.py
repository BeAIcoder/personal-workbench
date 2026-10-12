"""finance_board 模块 ORM 模型（表名统一 mod_ 前缀）。"""
from datetime import datetime

from sqlalchemy import JSON, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ...database import Base


class BoardSnapshot(Base):
    """报表看板快照：每次管线运行产出一个自包含 HTML 快照。"""

    __tablename__ = "mod_board_snapshot"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    # 运行时间戳（YYYYmmdd_HHMMSS，同时作为快照目录名）
    stamp: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    # 运行状态：ok / failed
    status: Mapped[str] = mapped_column(String(20), default="ok")
    # 快照 HTML 自包含副本路径（data/finance/finance_board/snapshots/<stamp>/）
    html_path: Mapped[str] = mapped_column(String(500), default="")
    # 管线输出摘要（各阶段最后几行日志）
    log_tail: Mapped[str] = mapped_column(Text, default="")
    # 运行耗时（秒）
    elapsed_sec: Mapped[float] = mapped_column(default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
