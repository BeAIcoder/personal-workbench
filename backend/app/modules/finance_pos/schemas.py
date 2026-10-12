"""finance_pos 模块请求 / 响应模型。"""
from pydantic import BaseModel


class ArtifactOut(BaseModel):
    name: str
    size: int


class PosRunOut(BaseModel):
    id: int
    status: str
    input_files: list = []
    artifacts: list[ArtifactOut] = []
    error: str = ""
    elapsed_sec: float = 0.0
    log_tail: str = ""
    created_at: str = ""


class PosRunListOut(BaseModel):
    total: int
    items: list[PosRunOut]
