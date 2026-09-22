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


# ---------- Agent 团队专家配置 ----------
def _clean_str_list(v: Optional[List[str]], item_max: int, count_max: int) -> List[str]:
    """字符串列表清洗：去空白、截断、去重、限量。"""
    out: List[str] = []
    for s in v or []:
        s = str(s).strip()[:item_max]
        if s and s not in out:
            out.append(s)
    return out[:count_max]


class AgentSpecBase(BaseModel):
    name: str = Field(min_length=1, max_length=50, pattern=r"^[a-z][a-z0-9_]*$", description="英文标识（小写字母/数字/下划线）")
    display_name: str = Field(min_length=1, max_length=50, description="中文名称（界面展示）")
    emoji: str = Field("🤖", max_length=8)
    color: str = Field("#409EFF", max_length=20)
    description: str = Field("", max_length=200, description="一句话职责")
    role_prompt: str = Field("", max_length=8000, description="角色系统提示词")
    keywords: List[str] = Field(default_factory=list, description="路由关键词，最多 50 个")
    tools: Optional[List[str]] = Field(None, description="工具白名单；null 表示默认技能池")
    model_id: Optional[int] = Field(None, description="绑定模型 provider_models.id；null 跟随全局激活模型")
    skills: Optional[List[str]] = Field(None, description="技能白名单；null 跟随全局技能池")
    enabled: bool = True
    sort: int = Field(0, ge=0, le=9999)

    @field_validator("name")
    @classmethod
    def _clean_name(cls, v: str) -> str:
        return v.strip().lower()

    @field_validator("display_name")
    @classmethod
    def _clean_display_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("中文名称不能为空白")
        return v

    @field_validator("keywords")
    @classmethod
    def _clean_keywords(cls, v: List[str]) -> List[str]:
        return _clean_str_list(v, 30, 50)

    @field_validator("tools", "skills")
    @classmethod
    def _clean_optional_list(cls, v: Optional[List[str]]) -> Optional[List[str]]:
        if v is None:
            return None
        return _clean_str_list(v, 60, 50)


class AgentSpecCreate(AgentSpecBase):
    pass


class AgentSpecUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=50, pattern=r"^[a-z][a-z0-9_]*$")
    display_name: Optional[str] = Field(None, min_length=1, max_length=50)
    emoji: Optional[str] = Field(None, max_length=8)
    color: Optional[str] = Field(None, max_length=20)
    description: Optional[str] = Field(None, max_length=200)
    role_prompt: Optional[str] = Field(None, max_length=8000)
    keywords: Optional[List[str]] = None
    tools: Optional[List[str]] = None
    model_id: Optional[int] = None
    skills: Optional[List[str]] = None
    enabled: Optional[bool] = None
    sort: Optional[int] = Field(None, ge=0, le=9999)

    @field_validator("name")
    @classmethod
    def _clean_name(cls, v: Optional[str]) -> Optional[str]:
        return v.strip().lower() if v is not None else None

    @field_validator("keywords")
    @classmethod
    def _clean_keywords(cls, v: Optional[List[str]]) -> Optional[List[str]]:
        if v is None:
            return None
        return _clean_str_list(v, 30, 50)

    @field_validator("tools", "skills")
    @classmethod
    def _clean_optional_list(cls, v: Optional[List[str]]) -> Optional[List[str]]:
        if v is None:
            return None
        return _clean_str_list(v, 60, 50)


class AgentSpecOut(AgentSpecBase):
    id: int
    is_builtin: bool
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
