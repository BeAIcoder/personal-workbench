"""debug_center 模块请求 / 响应模型（Pydantic v2）。"""
from pydantic import BaseModel, Field

# 问题记录的枚举取值（与 service 保持一致）
SEVERITIES = ("P0", "P1", "P2", "P3")
STATUSES = ("open", "in_progress", "done", "wontfix")
SOURCES = ("manual", "e2e", "analysis")


class IssueCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200, description="问题标题")
    severity: str = Field(default="P2", pattern="^P[0-3]$", description="严重度 P0-P3")
    status: str = Field(default="open", pattern="^(open|in_progress|done|wontfix)$")
    source: str = Field(default="manual", pattern="^(manual|e2e|analysis)$")
    page: str = Field(default="", max_length=200, description="相关页面/路由")
    description: str = Field(default="", max_length=4000)
    evidence: str = Field(default="", max_length=200, description="证据（如 run_id），可空")


class IssueUpdate(BaseModel):
    """更新问题：所有字段可选，未传的保持不变。"""

    title: str | None = Field(default=None, min_length=1, max_length=200)
    severity: str | None = Field(default=None, pattern="^P[0-3]$")
    status: str | None = Field(default=None, pattern="^(open|in_progress|done|wontfix)$")
    source: str | None = Field(default=None, pattern="^(manual|e2e|analysis)$")
    page: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    evidence: str | None = Field(default=None, max_length=200)
