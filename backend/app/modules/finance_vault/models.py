"""finance_vault 模块 ORM 模型（表名统一 mod_ 前缀）。"""
from datetime import datetime

from sqlalchemy import JSON, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from ...database import Base


class VaultDataset(Base):
    """数据仓数据集登记表：DuckDB 物理表 ↔ SQLite 元数据。"""

    __tablename__ = "mod_vault_dataset"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    # DuckDB 物理表名（仅小写字母/数字/下划线），唯一
    table_name: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    # 来源说明（上传文件名 / ingest_dataframe 调用方）
    source: Mapped[str] = mapped_column(String(300), default="")
    # 入仓后总行数（ingest 时同步）
    rows: Mapped[int] = mapped_column(Integer, default=0)
    # 列信息 [{name, type}]（DuckDB 类型名）
    columns_json: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)
