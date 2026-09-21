"""Agent 团队共享业务工具：直接操作工作台数据（任务 / 日程 / 笔记 / 统计）。

- 全部数据库访问经 SQLAlchemy 参数绑定执行；
- 工具执行轨迹通过 contextvar 收集，由编排器随会话持久化并回传前端；
- 工具返回结构化 dict，出错时返回 {"ok": False, "error": ...} 而非抛异常，
  便于模型自行纠错重试。
"""
from contextvars import ContextVar
from datetime import datetime, time, timedelta
from typing import Any

from sqlalchemy import func, or_
from sqlalchemy.orm import Session as OrmSession  # noqa: F401  (类型提示用途)

from ..database import SessionLocal
from ..models import Note, Schedule, Task

# 当前一轮的工具执行轨迹（orchestrator 每轮重置）
tool_trace: ContextVar[list | None] = ContextVar("agent_tool_trace", default=None)

STATUS_ZH = {"todo": "待办", "in_progress": "进行中", "done": "已完成"}
STATUS_EN = {v: k for k, v in STATUS_ZH.items()}
PRIORITY_ZH = {"low": "低", "medium": "中", "high": "高", "urgent": "紧急"}
PRIORITY_EN = {v: k for k, v in PRIORITY_ZH.items()}


def _record(tool: str, args: dict, result: Any) -> None:
    trace = tool_trace.get()
    if trace is None:
        return

    def short(v: Any, n: int = 60) -> str:
        s = str(v)
        return s if len(s) <= n else s[:n] + "…"

    clean_args = {k: short(v) for k, v in args.items() if v not in (None, "")}
    trace.append({"tool": tool, "args": clean_args, "result": short(result, 120)})


def _fmt(dt: datetime | None) -> str:
    return dt.strftime("%Y-%m-%d %H:%M") if dt else ""


def _parse_dt(value: str) -> datetime | None:
    """解析 'YYYY-MM-DD' 或 'YYYY-MM-DD HH:MM'（兼容 T 分隔）。"""
    v = (value or "").strip()
    if not v:
        return None
    v = v.replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(v, fmt)
        except ValueError:
            continue
    raise ValueError(f"无法解析时间「{value}」，请使用 2026-09-08 或 2026-09-08 14:00 格式")


def _task_out(t: Task) -> dict:
    overdue = bool(t.status != "done" and t.due_date and t.due_date < datetime.now())
    return {
        "id": t.id,
        "title": t.title,
        "status": STATUS_ZH.get(t.status, t.status),
        "priority": PRIORITY_ZH.get(t.priority, t.priority),
        "due_date": _fmt(t.due_date),
        "category": t.category,
        "overdue": overdue,
    }


def _schedule_out(s: Schedule) -> dict:
    return {
        "id": s.id,
        "title": s.title,
        "start": _fmt(s.start_time),
        "end": _fmt(s.end_time),
        "location": s.location,
    }


def _note_out(n: Note) -> dict:
    return {
        "id": n.id,
        "title": n.title,
        "tags": n.tags,
        "updated": _fmt(n.updated_at),
        "snippet": (n.content or "")[:80].replace("\n", " "),
    }


# ---------------- 任务 ----------------

def query_tasks(
    status: str = "",
    priority: str = "",
    keyword: str = "",
    overdue_only: bool = False,
    limit: int = 10,
) -> list[dict]:
    """查询工作台中的任务清单，可按状态、优先级、关键词、是否逾期过滤。"""
    limit = max(1, min(int(limit), 50))
    st = STATUS_EN.get(status.strip(), status.strip().lower())
    pr = PRIORITY_EN.get(priority.strip(), priority.strip().lower())
    with SessionLocal() as db:
        q = db.query(Task)
        if st in STATUS_ZH:
            q = q.filter(Task.status == st)
        if pr in PRIORITY_ZH:
            q = q.filter(Task.priority == pr)
        if overdue_only:
            q = q.filter(Task.status != "done", Task.due_date.isnot(None), Task.due_date < datetime.now())
        if keyword.strip():
            kw = keyword.strip()
            q = q.filter(or_(Task.title.contains(kw, autoescape=True), Task.description.contains(kw, autoescape=True)))
        rows = q.order_by(Task.due_date.asc().nullslast(), Task.created_at.desc()).limit(limit).all()
        result = [_task_out(t) for t in rows]
    _record("query_tasks", {"status": status, "priority": priority, "keyword": keyword, "overdue_only": overdue_only}, f"返回 {len(result)} 条")
    return result


def create_task(
    title: str,
    description: str = "",
    priority: str = "medium",
    due_date: str = "",
    category: str = "",
) -> dict:
    """在工作台创建一条任务。"""
    title = (title or "").strip()
    if not title:
        result = {"ok": False, "error": "任务标题不能为空"}
        _record("create_task", {}, result)
        return result
    pr = PRIORITY_EN.get((priority or "").strip(), (priority or "").strip().lower())
    if pr not in PRIORITY_ZH:
        pr = "medium"
    due = None
    try:
        due = _parse_dt(due_date)
    except ValueError as e:
        result = {"ok": False, "error": str(e)}
        _record("create_task", {"title": title}, result)
        return result
    with SessionLocal() as db:
        task = Task(title=title[:200], description=description or "", priority=pr, due_date=due, category=(category or "")[:50])
        db.add(task)
        db.commit()
        db.refresh(task)
        result = {"ok": True, "task_id": task.id, "title": task.title, "due_date": _fmt(task.due_date)}
    _record("create_task", {"title": title, "priority": priority, "due_date": due_date}, result)
    return result


def complete_task(task_id: int) -> dict:
    """把指定任务标记为已完成。"""
    with SessionLocal() as db:
        task = db.get(Task, int(task_id))
        if task is None:
            result = {"ok": False, "error": f"任务 {task_id} 不存在"}
        else:
            task.status = "done"
            from datetime import datetime as _dt

            task.completed_at = _dt.now()
            db.commit()
            result = {"ok": True, "task_id": task.id, "title": task.title}
    _record("complete_task", {"task_id": task_id}, result)
    return result


# ---------------- 日程 ----------------

def query_schedules(start_date: str = "", end_date: str = "", keyword: str = "", limit: int = 10) -> list[dict]:
    """查询日程安排，可按日期范围（含边界）与关键词过滤。"""
    limit = max(1, min(int(limit), 50))
    with SessionLocal() as db:
        q = db.query(Schedule)
        try:
            start = _parse_dt(start_date)
            end = _parse_dt(end_date)
        except ValueError as e:
            _record("query_schedules", {}, {"ok": False, "error": str(e)})
            return [{"ok": False, "error": str(e)}]
        if start:
            q = q.filter(Schedule.end_time >= start)
        if end:
            q = q.filter(Schedule.start_time <= datetime.combine(end.date(), time(23, 59, 59)))
        if keyword.strip():
            kw = keyword.strip()
            q = q.filter(
                or_(
                    Schedule.title.contains(kw, autoescape=True),
                    Schedule.location.contains(kw, autoescape=True),
                    Schedule.description.contains(kw, autoescape=True),
                )
            )
        rows = q.order_by(Schedule.start_time.asc()).limit(limit).all()
        result = [_schedule_out(s) for s in rows]
    _record("query_schedules", {"start_date": start_date, "end_date": end_date, "keyword": keyword}, f"返回 {len(result)} 条")
    return result


def create_schedule(
    title: str,
    date: str,
    start_time: str = "09:00",
    end_time: str = "10:00",
    location: str = "",
    description: str = "",
) -> dict:
    """在工作台创建一条日程。date 为 YYYY-MM-DD；start_time/end_time 为 HH:MM。"""
    title = (title or "").strip()
    try:
        day = _parse_dt(date)
        if day is None:
            raise ValueError("缺少日期 date（格式 YYYY-MM-DD）")
        sh, sm = (start_time or "09:00").split(":")[:2]
        eh, em = (end_time or "10:00").split(":")[:2]
        start = day.replace(hour=int(sh), minute=int(sm), second=0, microsecond=0)
        end = day.replace(hour=int(eh), minute=int(em), second=0, microsecond=0)
    except (ValueError, IndexError) as e:
        result = {"ok": False, "error": f"时间解析失败：{e}"}
        _record("create_schedule", {"title": title}, result)
        return result
    if end <= start:
        result = {"ok": False, "error": f"结束时间({end_time})必须晚于开始时间({start_time})"}
        _record("create_schedule", {"title": title}, result)
        return result
    with SessionLocal() as db:
        schedule = Schedule(
            title=title[:200] or "未命名日程",
            description=description or "",
            location=(location or "")[:200],
            start_time=start,
            end_time=end,
            color="#409EFF",
        )
        db.add(schedule)
        db.commit()
        db.refresh(schedule)
        result = {"ok": True, "schedule_id": schedule.id, "title": schedule.title, "time": f"{_fmt(start)} - {end.strftime('%H:%M')}"}
    _record("create_schedule", {"title": title, "date": date, "start_time": start_time, "end_time": end_time}, result)
    return result


# ---------------- 笔记 ----------------

def search_notes(keyword: str = "", tag: str = "", limit: int = 5) -> list[dict]:
    """搜索工作台笔记，可按关键词（标题/内容）与标签过滤。"""
    limit = max(1, min(int(limit), 20))
    with SessionLocal() as db:
        q = db.query(Note)
        if keyword.strip():
            kw = keyword.strip()
            q = q.filter(or_(Note.title.contains(kw, autoescape=True), Note.content.contains(kw, autoescape=True)))
        if tag.strip():
            q = q.filter(Note.tags.like(f'%"{tag.strip()}"%'))
        rows = q.order_by(Note.pinned.desc(), Note.updated_at.desc()).limit(limit).all()
        result = [_note_out(n) for n in rows]
    _record("search_notes", {"keyword": keyword, "tag": tag}, f"返回 {len(result)} 条")
    return result


def create_note(title: str, content: str = "", tags: str = "") -> dict:
    """在工作台新建一条笔记，tags 为逗号分隔的标签字符串。"""
    title = (title or "").strip()
    if not title:
        result = {"ok": False, "error": "笔记标题不能为空"}
        _record("create_note", {}, result)
        return result
    tag_list = [t.strip()[:20] for t in (tags or "").split(",") if t.strip()][:10]
    with SessionLocal() as db:
        note = Note(title=title[:200], content=content or "", tags=tag_list)
        db.add(note)
        db.commit()
        db.refresh(note)
        result = {"ok": True, "note_id": note.id, "title": note.title}
    _record("create_note", {"title": title, "tags": tags}, result)
    return result


# ---------------- 统计 ----------------

def workbench_stats() -> dict:
    """获取工作台概览统计：任务各状态数量、逾期数、今日日程数、笔记总数。"""
    now = datetime.now()
    day_start = datetime.combine(now.date(), datetime.min.time())
    tomorrow = day_start + timedelta(days=1)
    with SessionLocal() as db:
        counts = dict(db.query(Task.status, func.count()).group_by(Task.status).all())
        overdue = (
            db.query(Task).filter(Task.status != "done", Task.due_date.isnot(None), Task.due_date < now).count()
        )
        today_schedules = (
            db.query(Schedule).filter(Schedule.start_time < tomorrow, Schedule.end_time >= day_start).count()
        )
        note_total = db.query(Note).count()
        result = {
            "task": {
                "todo": counts.get("todo", 0),
                "in_progress": counts.get("in_progress", 0),
                "done": counts.get("done", 0),
                "overdue": overdue,
            },
            "schedule": {"today": today_schedules},
            "note": {"total": note_total},
        }
    _record("workbench_stats", {}, "统计完成")
    return result


# ---------------- 工具注册表 ----------------
# params: {参数名: (JSON类型, 中文说明, 是否必填)}

TOOL_SPECS: dict[str, dict] = {
    "query_tasks": {
        "func": query_tasks,
        "description": "查询工作台中的任务清单，可按状态、优先级、关键词、是否逾期过滤",
        "params": {
            "status": ("string", "状态：todo(待办)/in_progress(进行中)/done(已完成)，默认全部", False),
            "priority": ("string", "优先级：low/medium/high/urgent，默认全部", False),
            "keyword": ("string", "标题或描述关键词", False),
            "overdue_only": ("boolean", "是否只看已逾期", False),
            "limit": ("integer", "最多返回条数，默认10", False),
        },
        "required": [],
    },
    "create_task": {
        "func": create_task,
        "description": "在工作台创建一条待办任务",
        "params": {
            "title": ("string", "任务标题", True),
            "description": ("string", "任务描述", False),
            "priority": ("string", "优先级：low/medium/high/urgent，默认 medium", False),
            "due_date": ("string", "截止时间，格式 2026-09-08 或 2026-09-08 17:00，可空", False),
            "category": ("string", "分类，如：招商跟进/工程整改", False),
        },
        "required": ["title"],
    },
    "complete_task": {
        "func": complete_task,
        "description": "把指定任务标记为已完成",
        "params": {"task_id": ("integer", "任务ID", True)},
        "required": ["task_id"],
    },
    "query_schedules": {
        "func": query_schedules,
        "description": "查询工作台日程安排，可按日期范围与关键词过滤",
        "params": {
            "start_date": ("string", "起始日期 YYYY-MM-DD，可空", False),
            "end_date": ("string", "结束日期 YYYY-MM-DD，可空", False),
            "keyword": ("string", "标题/地点/备注关键词", False),
            "limit": ("integer", "最多返回条数，默认10", False),
        },
        "required": [],
    },
    "create_schedule": {
        "func": create_schedule,
        "description": "在工作台创建一条日程",
        "params": {
            "title": ("string", "日程标题", True),
            "date": ("string", "日期 YYYY-MM-DD", True),
            "start_time": ("string", "开始时间 HH:MM，默认 09:00", False),
            "end_time": ("string", "结束时间 HH:MM，默认 10:00", False),
            "location": ("string", "地点", False),
            "description": ("string", "备注", False),
        },
        "required": ["title", "date"],
    },
    "search_notes": {
        "func": search_notes,
        "description": "搜索工作台笔记，可按关键词与标签过滤",
        "params": {
            "keyword": ("string", "标题或内容关键词", False),
            "tag": ("string", "标签", False),
            "limit": ("integer", "最多返回条数，默认5", False),
        },
        "required": [],
    },
    "create_note": {
        "func": create_note,
        "description": "在工作台新建一条笔记（纪要、清单、知识沉淀）",
        "params": {
            "title": ("string", "笔记标题", True),
            "content": ("string", "笔记内容，支持多行文本", False),
            "tags": ("string", "逗号分隔的标签，如：招商,纪要", False),
        },
        "required": ["title"],
    },
    "workbench_stats": {
        "func": workbench_stats,
        "description": "获取工作台概览统计（任务各状态数、逾期数、今日日程数、笔记总数）",
        "params": {},
        "required": [],
    },
}


def build_tools(names: list[str]) -> list:
    """按名称构造 FunctionTool 列表（显式 JSON Schema，含中文参数说明）。"""
    from agentscope.tool import FunctionTool

    tools = []
    for name in names:
        spec = TOOL_SPECS[name]
        properties = {
            pname: {"type": ptype, "description": desc}
            for pname, (ptype, desc, _req) in spec["params"].items()
        }
        schema = {
            "type": "object",
            "properties": properties,
            "required": spec["required"],
        }
        tools.append(FunctionTool(spec["func"], name=name, description=spec["description"], input_schema=schema))
    return tools
