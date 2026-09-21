"""ORM 数据模型：任务、日程、笔记。"""
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


def _now() -> datetime:
    return datetime.now()


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class Task(TimestampMixin, Base):
    """任务"""

    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(200), index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    # todo / in_progress / done
    status: Mapped[str] = mapped_column(String(20), default="todo", index=True)
    # low / medium / high / urgent
    priority: Mapped[str] = mapped_column(String(20), default="medium", index=True)
    due_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    category: Mapped[str] = mapped_column(String(50), default="", index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Schedule(TimestampMixin, Base):
    """日程"""

    __tablename__ = "schedules"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(200), index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    location: Mapped[str] = mapped_column(String(200), default="")
    start_time: Mapped[datetime] = mapped_column(DateTime, index=True)
    end_time: Mapped[datetime] = mapped_column(DateTime, index=True)
    all_day: Mapped[bool] = mapped_column(Boolean, default=False)
    color: Mapped[str] = mapped_column(String(20), default="#409EFF")


class Note(TimestampMixin, Base):
    """笔记"""

    __tablename__ = "notes"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(200), index=True)
    content: Mapped[str] = mapped_column(Text, default="")
    # 标签列表，如 ["税务", "报销"]
    tags: Mapped[list] = mapped_column(JSON, default=list)
    pinned: Mapped[bool] = mapped_column(Boolean, default=False, index=True)


class ChatMessage(TimestampMixin, Base):
    """AI 助手会话消息"""

    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(64), index=True)
    # user / assistant
    role: Mapped[str] = mapped_column(String(20))
    agent_name: Mapped[str] = mapped_column(String(50), default="", index=True)
    content: Mapped[str] = mapped_column(Text, default="")
    # 该轮工具执行轨迹 [{tool, args, result}]
    trace: Mapped[list] = mapped_column(JSON, default=list)
    # 思考型模型的思考过程（独立于正文展示）
    thinking: Mapped[str] = mapped_column(Text, default="")


class AgentSession(Base):
    """AI 助手会话（标题/时间元数据；消息本体在 chat_messages）"""

    __tablename__ = "agent_sessions"

    session_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    title: Mapped[str] = mapped_column(String(100), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class ModelProvider(Base):
    """模型供应商（ZCode 式：一家供应商一张卡片，可启停）"""

    __tablename__ = "model_providers"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(50))
    # openai / anthropic
    protocol: Mapped[str] = mapped_column(String(20), default="openai")
    base_url: Mapped[str] = mapped_column(String(300), default="")
    api_key: Mapped[str] = mapped_column(String(300), default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class ProviderModel(Base):
    """供应商下的模型（含上下文/输出限制与多模态标记）"""

    __tablename__ = "provider_models"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    provider_id: Mapped[int] = mapped_column(Integer, index=True)
    model: Mapped[str] = mapped_column(String(120))
    context_size: Mapped[int] = mapped_column(Integer, default=128000)
    max_tokens: Mapped[int] = mapped_column(Integer, default=2048)
    multimodal: Mapped[bool] = mapped_column(Boolean, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class AgentSettings(Base):
    """AI 助手运行时配置（单行，id 恒为 1；界面可改，无需手改 .env）"""

    __tablename__ = "agent_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(30), default="")
    base_url: Mapped[str] = mapped_column(String(300), default="")
    api_key: Mapped[str] = mapped_column(String(300), default="")
    model: Mapped[str] = mapped_column(String(120), default="")
    # 上下文 / 生成参数
    context_size: Mapped[int] = mapped_column(Integer, default=128000)
    max_tokens: Mapped[int] = mapped_column(Integer, default=2048)
    temperature: Mapped[float] = mapped_column(Float, default=0.3)
    # 是否多模态（模型图片输入能力开关；聊天界面传图功能后续启用）
    multimodal: Mapped[bool] = mapped_column(Boolean, default=False)
    # Agent 工作模型：ReAct 最大工具调用轮次
    max_iters: Mapped[int] = mapped_column(Integer, default=5)
    # 记忆管理：是否注入历史 + 注入条数
    enable_memory: Mapped[bool] = mapped_column(Boolean, default=True)
    history_inject: Mapped[int] = mapped_column(Integer, default=12)
    # 当前激活的模型（指向 provider_models.id；None = .env 兜底）
    active_model_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # 本地技能池（SKILL.md 格式，如 QwenPaw skill_pool）
    enable_skills: Mapped[bool] = mapped_column(Boolean, default=False)
    skills_dir: Mapped[str] = mapped_column(String(300), default="")
    # 允许 Agent 使用 Bash 执行技能脚本（有安全边界：内置危险文件黑名单）
    enable_bash: Mapped[bool] = mapped_column(Boolean, default=False)
    # QwenPaw 工具型插件（经兼容层加载，实验性）
    enable_plugins: Mapped[bool] = mapped_column(Boolean, default=False)
    plugins_dir: Mapped[str] = mapped_column(String(300), default="")
    # 思考深度：'' 跟随模型 / off / low / medium / high / max
    reasoning_effort: Mapped[str] = mapped_column(String(12), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)
