"""finance_vault 模块请求 / 响应模型。"""
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class DatasetOut(BaseModel):
    """数据集登记信息。"""

    table: str
    source: str
    rows: int
    columns: list = []
    created_at: str = ""
    updated_at: str = ""


class DatasetListOut(BaseModel):
    total: int
    items: list[DatasetOut]


class IngestIn(BaseModel):
    """上传入仓参数。"""

    table: str = Field(min_length=1, max_length=64, description="目标表名（小写字母/数字/下划线）")
    mode: str = Field(default="replace", description="replace=整表重建 / append=按列名追加")
    sheet: str = Field(default="", description="xlsx 工作表名或序号（默认第一个）")


class IngestOut(BaseModel):
    table: str
    rows: int
    source: str = ""
    mode: str = "replace"
    columns: list = []


class QueryIn(BaseModel):
    """只读 SQL 查询。"""

    sql: str = Field(min_length=1, description="SELECT/WITH 只读查询语句")


class QueryOut(BaseModel):
    columns: list[str]
    rows: list[list]
    row_count: int
    truncated: bool = False
    elapsed_ms: int = 0


class PreviewOut(BaseModel):
    table: str
    columns: list[str]
    rows: list[list]
    row_count: int
