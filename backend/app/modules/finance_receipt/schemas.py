"""finance_receipt 模块请求/响应模型。"""
from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

ReceiptCategory = Literal[
    "pending_clearing_income",
    "transfer_income",
    "transfer_expense",
    "bank_interest_income",
    "bank_fee_expense",
]


class ReceiptScanIn(BaseModel):
    source_dir: str = Field(min_length=1, max_length=500)
    period_label: str = Field(min_length=1, max_length=40)
    start_date: date
    end_date: date
    output_dir: str = Field(default="", max_length=500)


class ReceiptMergeIn(BaseModel):
    file_ids: list[str] = Field(min_length=1, max_length=200)


class ReceiptItemPatch(BaseModel):
    category: ReceiptCategory | None = None
    included: bool | None = None
