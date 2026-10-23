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


def engine_subprocess_env() -> dict:
    """引擎子进程环境：显式指定可用的 TEMP/TMP/TMPDIR。

    服务经 Git Bash 等非 Windows 原生气壳启动时，继承的 TEMP/TMP 可能指向
    Windows Python 无法创建文件的位置，引擎内 openpyxl 等库创建临时文件时
    tempfile.gettempdir() 会因全部候选目录失败而抛
    「No usable temporary directory found」。这里固定到应用自建临时目录。
    """
    import os

    from .config import get_finance_data_dir

    tmp = get_finance_data_dir() / "_engine_tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["TEMP"] = env["TMP"] = env["TMPDIR"] = str(tmp)
    return env


def repair_upload_mojibake(name: str) -> str:
    """修复上传文件名的 mojibake：GBK 字节被 HTTP 层按 Latin-1 解码所致。

    curl 等客户端以 GBK 编码发送 multipart 文件名时，Starlette 按 Latin-1
    解码 header，中文名会变成「ÃÍÑÃô…」形态的乱码，导致依赖中文文件名的
    引擎匹配失败（如 POS 对账按「*辅助明细账*.xlsx」找文件）。
    """
    if not name or not any("\u0080" <= c <= "\u00ff" for c in name):
        return name
    try:
        fixed = name.encode("latin-1").decode("gbk")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return name
    if any("\u4e00" <= c <= "\u9fff" for c in fixed):
        return fixed
    return name
