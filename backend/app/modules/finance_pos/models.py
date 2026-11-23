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


class PosVoucherBatch(Base):
    """POS 刷卡凭证批次：回单×对账单匹配合并生成的凭证文件批次。

    文件落 get_finance_data_dir()/finance_pos/vouchers/<批次目录>/，
    元数据（文件清单/匹配统计）存本表 JSON 列，列表接口直接读库。
    """

    __tablename__ = "mod_pos_voucher_batch"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    # 统计月份 "YYYY-MM"
    month: Mapped[str] = mapped_column(String(7), index=True)
    # 状态：running / ok / failed
    status: Mapped[str] = mapped_column(String(20), default="running", index=True)
    error: Mapped[str] = mapped_column(Text, default="")
    log_tail: Mapped[str] = mapped_column(Text, default="")
    # 凭证文件清单 [{name, size, kind, ...}]
    stats: Mapped[list] = mapped_column(JSON, default=list)
    # 匹配统计 {receipts_total, matched, unmatched, ..., unmatched_list}
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
    batch_dir: Mapped[str] = mapped_column(String(500), default="")
    elapsed_sec: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)
