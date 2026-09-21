"""Pydantic 请求 / 响应模型（数据校验）。"""
from datetime import datetime
from typing import Generic, List, Literal, Optional, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    """统一分页返回结构。"""

    items: List[T]
    total: int
    page: int
    page_size: int


# ---------- 任务 ----------
TaskStatus = Literal["todo", "in_progress", "done"]
TaskPriority = Literal["low", "medium", "high", "urgent"]


class TaskBase(BaseModel):
    title: str = Field(min_length=1, max_length=200, description="任务标题")
    description: str = Field("", max_length=5000, description="任务描述")
    status: TaskStatus = "todo"
    priority: TaskPriority = "medium"
    due_date: Optional[datetime] = Field(None, description="截止时间，ISO 格式")
    category: str = Field("", max_length=50, description="分类，如：税务申报")

    @field_validator("title")
    @classmethod
    def _clean_title(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("标题不能为空白")
        return v


class TaskCreate(TaskBase):
    pass


class TaskUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=200)
    description: Optional[str] = Field(None, max_length=5000)
    status: Optional[TaskStatus] = None
    priority: Optional[TaskPriority] = None
    due_date: Optional[datetime] = None
    category: Optional[str] = Field(None, max_length=50)


class TaskOut(TaskBase):
    id: int
    completed_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ---------- 日程 ----------
class ScheduleBase(BaseModel):
    title: str = Field(min_length=1, max_length=200, description="日程标题")
    description: str = Field("", max_length=5000, description="备注")
    location: str = Field("", max_length=200, description="地点")
    start_time: datetime = Field(description="开始时间")
    end_time: datetime = Field(description="结束时间")
    all_day: bool = Field(False, description="是否全天")
    color: str = Field("#409EFF", max_length=20, description="标记颜色")

    @field_validator("title")
    @classmethod
    def _clean_title(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("标题不能为空白")
        return v

    @model_validator(mode="after")
    def _check_time(self):
        if self.end_time <= self.start_time:
            raise ValueError("结束时间必须晚于开始时间")
        return self


class ScheduleCreate(ScheduleBase):
    pass


class ScheduleUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=200)
    description: Optional[str] = Field(None, max_length=5000)
    location: Optional[str] = Field(None, max_length=200)
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    all_day: Optional[bool] = None
    color: Optional[str] = Field(None, max_length=20)


class ScheduleOut(ScheduleBase):
    id: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ---------- 笔记 ----------
class NoteBase(BaseModel):
    title: str = Field(min_length=1, max_length=200, description="笔记标题")
    content: str = Field("", max_length=50000, description="笔记内容")
    tags: List[str] = Field(default_factory=list, description="标签列表，最多 10 个")
    pinned: bool = Field(False, description="是否置顶")

    @field_validator("title")
    @classmethod
    def _clean_title(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("标题不能为空白")
        return v

    @field_validator("tags")
    @classmethod
    def _clean_tags(cls, v: List[str]) -> List[str]:
        out: List[str] = []
        for t in v:
            t = t.strip()[:20]
            if t and t not in out:
                out.append(t)
        return out[:10]


class NoteCreate(NoteBase):
    pass


class NoteUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=200)
    content: Optional[str] = Field(None, max_length=50000)
    tags: Optional[List[str]] = None
    pinned: Optional[bool] = None

    @field_validator("tags")
    @classmethod
    def _clean_tags(cls, v: Optional[List[str]]) -> Optional[List[str]]:
        if v is None:
            return None
        out: List[str] = []
        for t in v:
            t = t.strip()[:20]
            if t and t not in out:
                out.append(t)
        return out[:10]


class NoteOut(NoteBase):
    id: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ---------- 全局搜索 ----------
class SearchItem(BaseModel):
    id: int
    type: Literal["task", "schedule", "note"]
    title: str
    snippet: str
    time: Optional[datetime] = None


class SearchResult(BaseModel):
    q: str
    total: int
    tasks: List[SearchItem]
    schedules: List[SearchItem]
    notes: List[SearchItem]
