"""ORM 数据模型：任务、日程、笔记。"""
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, Index, Integer, String, Text
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
    # completed_at：概览页「近 7 天完成趋势」按完成时间范围统计
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    # created_at：任务列表默认排序列，缺索引时 SQLite 走全表扫描 + 临时 B 树排序
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)


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
    # 列表默认 order_by(pinned desc, updated_at desc)：单列 pinned 索引选择性太差（几乎全是 0），
    # 复合索引让排序直接走索引，省掉全表扫描 + 临时 B 树排序（大库上这是列表 p95 的主要开销）
    __table_args__ = (Index("ix_notes_pinned_updated", "pinned", "updated_at"),)


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
    # 归档：归档后不出现在默认会话列表（archived=true 过滤可见）
    archived: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    # updated_at：会话列表 order_by(updated_at desc) 的排序列，缺索引时全表扫描 + 临时 B 树
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now, index=True)


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
    # 预设标识（provider_presets.json 的 preset_key）：适配器只认它，不认中文显示名
    preset_key: Mapped[str] = mapped_column(String(50), default="")
    # 模型列表接口地址（在线同步用；空 = 无官方列表，仅目录兜底）
    models_url: Mapped[str] = mapped_column(String(300), default="")
    # 可选管控面凭证（火山 AK/SK）：seal 后的 JSON {"access_key","secret_key"}；
    # 任何 GET 不回传，保存时空/掩码保留旧值
    sync_credentials: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class ProviderModel(Base):
    """供应商下的模型（含上下文/输出限制、多模态标记与能力探测结果）"""

    __tablename__ = "provider_models"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    provider_id: Mapped[int] = mapped_column(Integer, index=True)
    model: Mapped[str] = mapped_column(String(120))
    context_size: Mapped[int] = mapped_column(Integer, default=128000)
    max_tokens: Mapped[int] = mapped_column(Integer, default=2048)
    multimodal: Mapped[bool] = mapped_column(Boolean, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # 采样温度：空 = 跟随系统默认（TEXT 存储，便于留空与手动填写）
    temperature: Mapped[str] = mapped_column(Text, default="")
    # 模型备注（界面自填，如「主力模型/仅测试用」）
    note: Mapped[str] = mapped_column(Text, default="")
    # 思考能力探测结论：-1 未探测 / 0 不支持 / 1 支持
    supports_thinking: Mapped[int] = mapped_column(Integer, default=-1)
    # 探测出的可用思考档位（JSON list，如 ["low","medium","high"]）
    thinking_levels: Mapped[str] = mapped_column(Text, default="")
    # 探测出的有效上下文 tokens（0 = 未探测）
    probed_context: Mapped[int] = mapped_column(Integer, default=0)
    # 探测出的最大输出 tokens（0 = 未探测）
    probed_max_output: Mapped[int] = mapped_column(Integer, default=0)
    # 视觉（图片理解）能力开关：聊天传图前由编排层校验，false 时带图请求直接 400
    supports_vision: Mapped[bool] = mapped_column(Boolean, default=False)
    # 探测分步详情（JSON：reasoning/context/output 各步的判定与请求记录）
    probe_result: Mapped[str] = mapped_column(Text, default="")
    # 最近一次探测时间（ISO 本地时间，如 2026-10-12T20:30:00）
    probed_at: Mapped[str] = mapped_column(Text, default="")
    # 声明的最大输入 tokens（0 = 未提供；与 context_size 分列：context 含输出份额）
    max_input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    # 工具调用能力：-1 未知 / 0 否 / 1 是（目录/同步/手动）
    supports_tools: Mapped[int] = mapped_column(Integer, default=-1)
    # 能力元数据 JSON：sources/manual_fields/模态/思考默认档/api_family/remote_status/最近同步快照
    capability_meta: Mapped[str] = mapped_column(Text, default="{}")
    # 最近一次能力同步时间（ISO 本地时间；空 = 从未同步）
    synced_at: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class AgentSpecModel(TimestampMixin, Base):
    """Agent 团队专家定义（数据库可配置；内置专家由 seed 灌入，is_builtin=True）"""

    __tablename__ = "agent_specs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    # 英文标识（路由/存储用），唯一
    name: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(50))
    emoji: Mapped[str] = mapped_column(String(8), default="🤖")
    color: Mapped[str] = mapped_column(String(20), default="#409EFF")
    description: Mapped[str] = mapped_column(String(200), default="")
    # 角色系统提示词（COMMON_RULES 由编排器统一拼接）
    role_prompt: Mapped[str] = mapped_column(Text, default="")
    # 关键词路由表（命中数最多者胜出）
    keywords: Mapped[list] = mapped_column(JSON, default=list)
    # 工具白名单；NULL 表示用默认查询工具池；空数组表示不使用任何工具
    tools: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # 绑定模型（逻辑引用 provider_models.id；NULL = 跟随全局激活模型）
    model_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # 专家级技能白名单；NULL 表示跟随全局技能池配置
    skills: Mapped[list | None] = mapped_column(JSON, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    sort: Mapped[int] = mapped_column(Integer, default=0)
    is_builtin: Mapped[bool] = mapped_column(Boolean, default=False)


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


class ScheduledJob(TimestampMixin, Base):
    """定时 Agent 任务：到点自动按提示词执行一次 Agent 对话，结果落笔记。"""

    __tablename__ = "scheduled_jobs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), index=True)
    prompt: Mapped[str] = mapped_column(Text, default="")
    # 指定专家 name；NULL = 智能路由
    agent_name: Mapped[str | None] = mapped_column(String(50), nullable=True)
    mode: Mapped[str] = mapped_column(String(20), default="standard")  # standard / readonly / deep
    # interval：按 interval_minutes 间隔；daily：每天 daily_at（HH:MM，本机时区）；
    # cron：cron_expr 五段表达式（分 时 日 月 周，本机时区）
    schedule_type: Mapped[str] = mapped_column(String(10), default="interval")
    interval_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    daily_at: Mapped[str | None] = mapped_column(String(5), nullable=True)
    cron_expr: Mapped[str] = mapped_column(String(60), default="")
    # 模块动作（空 = Agent 对话任务）：到点执行注册的模块动作而非 LLM 对话，如 finance_board.run
    action: Mapped[str] = mapped_column(String(100), default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    # 运行状态
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_status: Mapped[str] = mapped_column(String(10), default="")  # ok / error / skipped
    last_error: Mapped[str] = mapped_column(Text, default="")
    last_note_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # 最近运行记录（最多 20 条）：[{at, status, summary}]
    recent_runs: Mapped[list] = mapped_column(JSON, default=list)


class McpServer(Base):
    """MCP 服务器配置（stdio）：命令 + 参数 + 环境变量，工具清单缓存。"""

    __tablename__ = "mcp_servers"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    command: Mapped[str] = mapped_column(String(500))
    args: Mapped[list] = mapped_column(JSON, default=list)        # ["--flag", "value"]
    env: Mapped[dict] = mapped_column(JSON, default=dict)         # 敏感值仅本机库
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    # 连接状态：stopped / connected / error
    status: Mapped[str] = mapped_column(String(20), default="stopped")
    last_error: Mapped[str] = mapped_column(Text, default="")
    # 最近一次 tools/list 结果缓存
    tools_json: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class Memory(Base):
    """长期记忆：跨会话保留的事实/偏好/经验，按关键词+新近度检索注入 Agent 上下文。"""

    __tablename__ = "memories"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # 来源：manual（手动保存）/ auto（对话抽取）/ import
    source: Mapped[str] = mapped_column(String(20), default="manual")
    # 来源会话 id（auto 时回溯）
    session_id: Mapped[str] = mapped_column(String(64), default="")
    tags: Mapped[list] = mapped_column(JSON, default=list)
    pinned: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    # 使用统计（检索命中）
    hit_count: Mapped[int] = mapped_column(Integer, default=0)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, index=True)
