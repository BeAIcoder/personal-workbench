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


class VoucherGenerateIn(BaseModel):
    month: str
    statement_dir: str = ""
    output_dir: str = ""


class VoucherMergeIn(BaseModel):
    names: list[str]


class VoucherFileOut(BaseModel):
    name: str
    size: int
    kind: str = "txn"
    receipt_date: str = ""
    terminal: str = ""
    project: str = ""
    net_amount: str = ""


class VoucherBatchOut(BaseModel):
    id: int
    month: str
    status: str
    error: str = ""
    summary: dict = {}
    files: list[VoucherFileOut] = []
    output_dir: str = ""
    elapsed_sec: float = 0.0
    log_tail: str = ""
    created_at: str = ""


class VoucherBatchListOut(BaseModel):
    total: int
    items: list[VoucherBatchOut]
