"""通用工具函数。"""
from datetime import date, datetime, time

from fastapi import HTTPException


def parse_dt(value: str, end_of_day: bool = False) -> datetime:
    """把查询参数解析为 datetime。

    支持 "2026-09-07" 与 "2026-09-07T09:30:00" 两种格式；
    仅传日期时，end_of_day=True 表示取当天最后一刻（用于闭区间过滤）。
    非法格式抛 422，避免穿透成 500。
    """
    v = value.strip()
    try:
        if "T" not in v and " " not in v:
            d = date.fromisoformat(v)
            if end_of_day:
                return datetime.combine(d, time(23, 59, 59, 999999))
            return datetime.combine(d, time.min)
        return datetime.fromisoformat(v)
    except ValueError:
        raise HTTPException(status_code=422, detail="日期格式错误，应为 YYYY-MM-DD")


def escape_like(value: str) -> str:
    """转义 LIKE 模式中的特殊字符（%、_、\\），配合 .like(pattern, escape="\\") 使用。"""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def tag_like_pattern(tag: str) -> str:
    """笔记标签（JSON 数组形如 ["报销"]）的 LIKE 匹配模式，特殊字符已转义。"""
    return f'%"{escape_like(tag)}"%'
