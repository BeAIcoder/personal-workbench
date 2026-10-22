"""finance_cashflow 模块 ORM 模型（表名统一 mod_ 前缀）。

模块代码自 2026-10-22 起纳入 git 跟踪；V1 引擎目录与真实业务数据仍在仓外，永不推远端。
"""
from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ...database import Base


class CashflowRun(Base):
    """现金流自动填报运行记录：上传输入 → 跑原引擎 → 产物归档。"""

    __tablename__ = "mod_cashflow_run"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    # 回填月份（1-12，引擎 --actual-month）
    actual_month: Mapped[int] = mapped_column(Integer)
    # 状态：uploaded / running / ok / failed
    status: Mapped[str] = mapped_column(String(20), default="uploaded", index=True)
    # 上传文件清单 [{name, size}]
    input_files: Mapped[list] = mapped_column(JSON, default=list)
    # 产物清单 [{name, size, path}]（output 目录快照）
    artifacts: Mapped[list] = mapped_column(JSON, default=list)
    # 运行工作目录（input/output 的父目录）
    run_dir: Mapped[str] = mapped_column(String(500), default="")
    # 引擎日志尾部（失败时排错用）
    log_tail: Mapped[str] = mapped_column(Text, default="")
    error: Mapped[str] = mapped_column(Text, default="")
    elapsed_sec: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)
