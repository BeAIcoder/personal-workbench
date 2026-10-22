"""finance_board 模块请求 / 响应模型。"""
from pydantic import BaseModel


class SnapshotOut(BaseModel):
    id: int
    stamp: str
    status: str
    html_path: str = ""
    elapsed_sec: float = 0.0
    # 管线输出日志尾部（入库已截断，读取再按尾部有界暴露）
    log_tail: str = ""
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
