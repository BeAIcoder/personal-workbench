"""finance_board 模块请求 / 响应模型。"""
from pydantic import BaseModel


class SnapshotOut(BaseModel):
    id: int
    stamp: str
    status: str
    html_path: str = ""
    elapsed_sec: float = 0.0
    created_at: str = ""


class SnapshotListOut(BaseModel):
    total: int
    items: list[SnapshotOut]


class RunResultOut(BaseModel):
    id: int
    stamp: str
    status: str
    elapsed_sec: float = 0.0
    message: str = ""
