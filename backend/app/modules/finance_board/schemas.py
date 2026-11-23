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


class RunAcceptedOut(BaseModel):
    """异步运行受理回执（202）：run_id 用于轮询 GET /runs/{run_id}。"""

    run_id: str
    status: str = "running"


class RunStatusOut(BaseModel):
    """异步运行状态：running 只有 elapsed_sec；终态才有 snapshot_id 或 error。"""

    run_id: str
    status: str
    # 成功时为快照 id（与 /snapshots 列表同一主键），失败/运行中为 None
    snapshot_id: int | None = None
    # 失败原因（与同步 /run 的 503 detail 同源文案）
    error: str | None = None
    # 运行中为实时耗时，终态为本次运行实际耗时
    elapsed_sec: float | None = None
    # 管线日志尾部（有界），失败时便于前端直接展示
    log_tail: str | None = None


class RunResultOut(BaseModel):
    id: int
    stamp: str
    status: str
    elapsed_sec: float = 0.0
    # 实际执行尝试次数（重试计数，前端/定时任务可展示；忙态等待不计入）
    attempts: int = 1
    message: str = ""
