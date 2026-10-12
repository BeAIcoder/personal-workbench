"""模块请求 / 响应模型模板（Pydantic v2）。"""
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ExampleEntryCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100, description="条目名称")


class ExampleEntryOut(BaseModel):
    id: int
    name: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
