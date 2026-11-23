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


class VaultCleanLog(Base):
    """数据池清洗运行日志：配方 JSON + 逐算子报告（可复用重放，2026-11-07）。"""

    __tablename__ = "mod_vault_clean_log"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    # 清洗源数据集表名（索引便于按表取历史配方）
    source_table: Mapped[str] = mapped_column(String(64), index=True)
    # 清洗结果数据集表名（preview 不落日志，apply 才写）
    result_table: Mapped[str] = mapped_column(String(64), default="")
    rows_in: Mapped[int] = mapped_column(Integer, default=0)
    rows_kept: Mapped[int] = mapped_column(Integer, default=0)
    # 清洗配方（算子开关 + invalid_policy，OpenRefine 式可复用）
    config_json: Mapped[dict] = mapped_column(JSON, default=dict)
    # 逐算子统计报告（Data-Juicer 式 ops:[{op, hit_rows}] + 样本/警告）
    report_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
