# -*- coding: utf-8 -*-
"""经营管理模块的出入参模型（路由层校验，业务规则在 service）。"""
from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field

ReportType = Literal["control", "weekly", "monthly"]
Period = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")


class ReportCreate(BaseModel):
    report_type: ReportType
    project: str = Field(min_length=1, max_length=100)
    period: str = Period
    period_start: date | None = None
    period_end: date | None = None
    title: str = Field(default="", max_length=200)
    note: str = Field(default="", max_length=2000)


class ReportUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    note: str | None = Field(default=None, max_length=2000)
    period: str | None = Field(default=None, pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
    period_start: date | None = None
    period_end: date | None = None


class MetricItem(BaseModel):
    section: str = Field(default="", max_length=40)
    metric_key: str = Field(min_length=1, max_length=200)
    label: str = Field(default="", max_length=200)
    value_num: float | None = Field(default=None, ge=-1_000_000_000_000, le=1_000_000_000_000)
    value_text: str = Field(default="", max_length=2000)
    unit: str = Field(default="", max_length=20)
    extra: dict[str, Any] | None = None
    sort: int = Field(default=0, ge=0, le=1_000_000)


class MetricBatch(BaseModel):
    items: list[MetricItem] = Field(min_length=1, max_length=2000)


class RecordIn(BaseModel):
    section: Literal["key_work", "kpi", "litigation", "board_item", "warning_tenant", "brand_promo"]
    seq: int = Field(default=0, ge=0, le=1_000_000)
    name: str = Field(default="", max_length=200)
    category: str = Field(default="", max_length=100)
    stage: str = Field(default="", max_length=50)
    status: str = Field(default="", max_length=20)
    area: float | None = Field(default=None, ge=0, le=1_000_000_000)
    amount: float | None = Field(default=None, ge=-1_000_000_000_000, le=1_000_000_000_000)
    amount2: float | None = Field(default=None, ge=-1_000_000_000_000, le=1_000_000_000_000)
    recovery: float | None = Field(default=None, ge=-1_000_000_000_000, le=1_000_000_000_000)
    balance: float | None = Field(default=None, ge=-1_000_000_000_000, le=1_000_000_000_000)
    rate: float | None = Field(default=None, ge=-10, le=10)
    occurred_on: date | None = None
    due_on: date | None = None
    owner: str = Field(default="", max_length=100)
    co_owner: str = Field(default="", max_length=100)
    content: str = Field(default="", max_length=20000)
    progress: str = Field(default="", max_length=20000)
    note: str = Field(default="", max_length=20000)
    payload: dict[str, Any] = Field(default_factory=dict)
    sort: int = Field(default=0, ge=0, le=1_000_000)


class RecordUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    category: str | None = Field(default=None, max_length=100)
    stage: str | None = Field(default=None, max_length=50)
    status: str | None = Field(default=None, max_length=20)
    area: float | None = Field(default=None, ge=0, le=1_000_000_000)
    amount: float | None = Field(default=None, ge=-1_000_000_000_000, le=1_000_000_000_000)
    amount2: float | None = Field(default=None, ge=-1_000_000_000_000, le=1_000_000_000_000)
    recovery: float | None = Field(default=None, ge=-1_000_000_000_000, le=1_000_000_000_000)
    balance: float | None = Field(default=None, ge=-1_000_000_000_000, le=1_000_000_000_000)
    rate: float | None = Field(default=None, ge=-10, le=10)
    occurred_on: date | None = None
    due_on: date | None = None
    owner: str | None = Field(default=None, max_length=100)
    co_owner: str | None = Field(default=None, max_length=100)
    content: str | None = Field(default=None, max_length=20000)
    progress: str | None = Field(default=None, max_length=20000)
    note: str | None = Field(default=None, max_length=20000)
    payload: dict[str, Any] | None = None


class TextItem(BaseModel):
    section: str = Field(min_length=1, max_length=40)
    subsection: str = Field(default="", max_length=100)
    content: str = Field(default="", max_length=20000)
    sort: int = Field(default=0, ge=0, le=1_000_000)


class TextBatch(BaseModel):
    items: list[TextItem] = Field(min_length=1, max_length=50)


class ConfirmIn(BaseModel):
    token: str = Field(min_length=1, max_length=100)
    project: str = Field(default="", max_length=100)
    period: str = Field(default="", pattern=r"^(\d{4}-(0[1-9]|1[0-2]))?$")
