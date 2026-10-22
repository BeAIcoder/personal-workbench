"""通用工具函数。"""
from __future__ import annotations
import json
from datetime import date, datetime, time
from typing import Annotated

from fastapi import HTTPException, Path

# SQLite 主键是 int64：路径里传超出范围的 ID 会在驱动层抛 OverflowError
# （「Python int too large to convert to SQLite INTEGER」），穿透成 500 + 堆栈。
# 所有 /{id} 型接口统一用这个入口约束，越界直接 422，不给客户端 500。
DbId = Annotated[int, Path(ge=1, le=2**63 - 1, description="数据库主键（1 ~ int64 上限）")]


def parse_dt(value: str, end_of_day: bool = False) -> datetime:
    """把查询参数解析为 datetime。

    支持 "2026-09-07" 与 "2026-09-07T09:30:00" 两种格式；
    仅传日期时，end_of_day=True 表示取当天最后一刻（用于闭区间过滤）。
    带时区的输入（如 "2026-09-07T09:30:00+08:00"）统一转 naive 本地时间——
    入库列是 naive DateTime，aware 值直接绑定会让 SQLite 字符串比较口径错乱（漏数据）。
    非法格式抛 422，避免穿透成 500。
    """
    v = value.strip()
    try:
        if "T" not in v and " " not in v:
            d = date.fromisoformat(v)
            if end_of_day:
                return datetime.combine(d, time(23, 59, 59, 999999))
            return datetime.combine(d, time.min)
        dt = datetime.fromisoformat(v)
        if dt.tzinfo is not None:
            dt = dt.astimezone().replace(tzinfo=None)
        return dt
    except ValueError:
        raise HTTPException(status_code=422, detail="日期格式错误，应为 YYYY-MM-DD")


def escape_like(value: str) -> str:
    """转义 LIKE 模式中的特殊字符（%、_、\\），配合 .like(pattern, escape="\\") 使用。"""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def tag_like_pattern(tag: str) -> str:
    """笔记标签（JSON 数组）的 LIKE 匹配模式。

    先把标签序列化成 JSON 字符串元素（含两侧引号，引号/反斜杠按 JSON 规则转义，
    如 a"b → "a\\"b"），再做 LIKE 元字符转义。这样含引号/反斜杠的标签也能按字面命中，
    而不会被 JSON 编码差异误伤。
    """
    encoded = json.dumps(tag, ensure_ascii=False)
    return f"%{escape_like(encoded)}%"


def resolve_engine_root(configured: str | None):
    """引擎/数据源路径解析：**接受目录或单一文件**（外部验证第 4 章需求）。

    - 配置为空 → None（调用方走默认推导）；
    - 已配置且是目录 → 原样返回；
    - 已配置且是文件 → 返回其父目录（用户直指引擎入口文件/脚本时，
      引擎根取其所在目录，各模块再按自身必需文件校验）；
    - 已配置但路径不存在 → 原样返回（交由调用方校验并给出中文不可用提示，
      不在这里静默改判，避免掩盖配置错误）。
    """
    from pathlib import Path as _Path

    raw = (configured or "").strip()
    if not raw:
        return None
    p = _Path(raw)
    if p.is_file():
        return p.parent
    return p
