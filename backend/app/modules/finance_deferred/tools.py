# -*- coding: utf-8 -*-
"""finance_deferred Agent 工具：唯一只读摘要 ``deferred_summary``。

工具函数自行建立会话（不依赖 FastAPI 依赖），按项目返回草稿/归档数量、最近测算、
摊销截止与校验计数；不返回明细金额、不写库、不触发计算。
"""
from __future__ import annotations

from . import service


def deferred_summary_tool(item_id: int | None = None) -> dict:
    """只读摘要：省略 item_id 返回全部项目，否则只看指定项目。"""
    from ...database import SessionLocal

    if item_id is not None:
        try:
            item_id = int(item_id)
        except (TypeError, ValueError):
            return {"ok": False, "error": "item_id 必须是整数"}
    try:
        with SessionLocal() as db:
            summary = service.deferred_summary(db, item_id=item_id)
    except service.DeferredNotFoundError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, **summary.model_dump(mode="json")}
