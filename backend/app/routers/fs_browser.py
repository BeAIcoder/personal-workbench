# -*- coding: utf-8 -*-
"""本机目录浏览（只读）：给前端「浏览选择目录」弹窗用。

- GET /api/fs/drives —— 允许浏览的根目录（FS_BROWSE_ROOTS，留空默认用户主目录+数据根）
- GET /api/fs/browse?path=... —— 列出该目录下的子目录（不含文件）

安全边界（如实说明，不夸大）：
- 只返回目录名与路径，不读文件内容；只读操作。
- 仅允许访问 FS_BROWSE_ROOTS 白名单内的根目录及其子目录，越界返回 403；
  绝对路径先 resolve，`..` 与符号链接穿越同样按解析后的真实路径判断。
- 该接口是普通 GET，不在 OriginGuard 的拦截范围内（只拦状态变更方法与含
  discover 的路径）；跨站读取依赖 CORS 响应隔离，服务默认仅监听 127.0.0.1。
  本项目没有登录鉴权，因此不要把整个盘符加入白名单，也不要绑定到 0.0.0.0。
"""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from ..config import get_fs_browse_roots

router = APIRouter(prefix="/fs", tags=["系统·目录浏览"])

# 隐藏的系统/回收类条目（不进入目录浏览）
_HIDDEN_PREFIXES = ("$", ".")
_MAX_ENTRIES = 500


def _norm(path: Path) -> str:
    """真实路径 + 平台大小写归一，用于白名单包含判断。"""
    return os.path.normcase(os.path.realpath(str(path)))


def _within(path: Path, root: Path) -> bool:
    try:
        return os.path.commonpath([_norm(path), _norm(root)]) == _norm(root)
    except ValueError:  # 不同盘符没有公共路径
        return False


def _allowed_roots() -> list[Path]:
    roots: list[Path] = []
    for root in get_fs_browse_roots():
        try:
            if root.is_dir():
                roots.append(root)
        except OSError:
            continue
    return roots


def _root_entry(root: Path) -> dict:
    return {"name": str(root), "path": str(root), "is_dir": True}


@router.get("/drives", summary="允许浏览的根目录（目录浏览起点）")
def list_drives():
    return {"drives": [str(root) for root in _allowed_roots()]}


@router.get("/browse", summary="浏览目录（仅子目录，不含文件）")
def browse(path: str = Query(default="", description="目录绝对路径；空=根目录列表")):
    roots = _allowed_roots()
    raw = (path or "").strip()
    if not raw:
        return {"path": "", "parent": "", "entries": [_root_entry(root) for root in roots]}
    candidate = Path(raw)
    if not candidate.is_absolute():
        return JSONResponse(status_code=400, content={"detail": "路径必须是绝对路径"})
    try:
        target = candidate.resolve()
    except (OSError, ValueError):
        return JSONResponse(status_code=400, content={"detail": f"路径无法解析：{raw}"})
    if not any(_within(target, root) for root in roots):
        return JSONResponse(status_code=403, content={
            "detail": "目录不在允许浏览的根目录内；请在后端 .env 配置 FS_BROWSE_ROOTS（逗号分隔的绝对路径）"
        })
    if not target.is_dir():
        if target.exists():
            return JSONResponse(status_code=400, content={"detail": f"不是目录：{raw}"})
        return JSONResponse(status_code=400, content={"detail": f"目录不存在：{raw}"})
    entries: list[dict] = []
    try:
        for child in sorted(target.iterdir(), key=lambda c: c.name.lower()):
            name = child.name
            if name.startswith(_HIDDEN_PREFIXES):
                continue
            try:
                if not child.is_dir():
                    continue
                if not any(_within(child.resolve(), root) for root in roots):
                    continue  # 指向白名单外的符号链接/目录联接不列出
                entries.append({"name": name, "path": str(child), "is_dir": True})
            except OSError:
                continue
            if len(entries) >= _MAX_ENTRIES:
                break
    except OSError as exc:
        return JSONResponse(status_code=400, content={"detail": f"目录无法读取：{exc}"})
    parent = target.parent
    parent_path = (
        str(parent)
        if parent != target and any(_within(parent, root) for root in roots)
        else ""
    )
    return {"path": str(target), "parent": parent_path, "entries": entries}
