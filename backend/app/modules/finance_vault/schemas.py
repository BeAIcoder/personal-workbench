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
    sheet: str = Field(default="", description="xlsx 工作表名（含纯数字名）或 0-based 序号（默认第一个）")
    keep_text: bool = Field(default=True, description="保留编码/账号为文本（默认开启）")


class IngestOut(BaseModel):
    table: str
    rows: int
    source: str = ""
    mode: str = "replace"
    columns: list = []
    keep_text: bool = True
    sheet: str = ""
    sheets: list[str] = []
    sheet_count: int = 0
    text_columns: list[str] = []


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


class CleanIn(BaseModel):
    """清洗配方（OpenRefine 式可复用 JSON）：算子开关 + 无效值策略。"""

    ops: dict = Field(default_factory=dict, description="算子开关：normalize_dirty_text/drop_all_empty_rows/drop_all_empty_columns/dedupe_rows/required_columns")
    invalid_policy: str = Field(default="drop_row", description="日期/数值列无效值处理：drop_row=剔除该行 / null_cell=置空该格")
    mark_outliers_iqr: bool = Field(default=False, description="IQR 离群值只标记进报告，不剔除")
    target_table: str = Field(default="", description="结果表名（默认 {table}_clean）")
    allow_overwrite: bool = Field(default=False, description="结果表已存在时是否覆盖")


class AggIn(BaseModel):
    """单表聚合条件。"""

    group_by: list[str] = Field(default_factory=list, description="分组维度列名")
    metrics: list[dict] = Field(default_factory=list, description="指标：[{column, agg}]，agg ∈ sum/avg/count/min/max，count 可用 *")
    filters: list[dict] = Field(default_factory=list, description="过滤：[{column, op, value}]")
    order_by: str = Field(default="", description="排序字段（维度或指标结果列）")
    order_dir: str = Field(default="desc", description="asc / desc")
    limit: int = Field(default=1000, description="结果组数上限（≤10000）")


class AggregateSaveIn(AggIn):
    """聚合结果另存为数据集。"""

    target_table: str = Field(min_length=1, max_length=64, description="新数据集表名（小写字母/数字/下划线）")
    allow_overwrite: bool = Field(default=False, description="目标数据集已存在时是否覆盖（默认拒绝，409）")


class RenameIn(BaseModel):
    """数据集重命名。"""

    new_table: str = Field(min_length=1, max_length=64, description="新数据集表名（小写字母/数字/下划线）")


class AggOut(BaseModel):
    columns: list[str]
    rows: list[list]
    row_count: int
    total_groups: int = 0
    truncated: bool = False
    elapsed_ms: int = 0


class ProfileOut(BaseModel):
    """列画像响应（自由结构，前端按 warnings/columns 渲染）。"""

    table: str
    rows: int
    dup_rows: int | None = None
    sampled: bool = False
    kinds: dict = {}
    columns: list[dict] = []
    warnings: list[str] = []
