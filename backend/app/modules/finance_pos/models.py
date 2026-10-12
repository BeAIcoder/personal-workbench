"""finance_pos 模块 ORM 模型（表名统一 mod_ 前缀）。"""
from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ...database import Base


class PosRun(Base):
    """POS 对账运行记录：上传输入 → 跑原引擎（台账+差异分析）→ 产物归档。"""

    __tablename__ = "mod_pos_run"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    # 状态：uploaded / running / ok / failed
    status: Mapped[str] = mapped_column(String(20), default="uploaded", index=True)
    # 上传文件清单 [{name, size}]
    input_files: Mapped[list] = mapped_column(JSON, default=list)
    # 产物清单 [{name, size, path}]
    artifacts: Mapped[list] = mapped_column(JSON, default=list)
    run_dir: Mapped[str] = mapped_column(String(500), default="")
    log_tail: Mapped[str] = mapped_column(Text, default="")
    error: Mapped[str] = mapped_column(Text, default="")
    elapsed_sec: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)
