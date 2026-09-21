"""通用工具函数。"""
from datetime import date, datetime, time


def parse_dt(value: str, end_of_day: bool = False) -> datetime:
    """把查询参数解析为 datetime。

    支持 "2026-09-07" 与 "2026-09-07T09:30:00" 两种格式；
    仅传日期时，end_of_day=True 表示取当天最后一刻（用于闭区间过滤）。
    """
    v = value.strip()
    if "T" not in v and " " not in v:
        d = date.fromisoformat(v)
        if end_of_day:
            return datetime.combine(d, time(23, 59, 59, 999999))
        return datetime.combine(d, time.min)
    return datetime.fromisoformat(v)
