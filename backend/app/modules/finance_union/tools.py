# -*- coding: utf-8 -*-
"""finance_union Agent 工具：唯一只读摘要 ``union_summary``。

工具自行建立会话（不依赖 FastAPI 依赖），按主体/期间返回批次状态计数与可计算行
合计；不改状态、不落库、不导出，也不返回逐行明细。
"""
from __future__ import annotations

from . import service


def union_summary_tool(entity: str | None = None, period: str | None = None) -> dict:
    """只读摘要：省略筛选条件时返回全部批次的汇总。"""
    from ...database import SessionLocal

    try:
        with SessionLocal() as db:
            summary = service.union_summary(db, entity=entity or None, period=period or None)
    except service.UnionNotFoundError as exc:
        return {"ok": False, "error": str(exc)}
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, **summary.model_dump(mode="json")}
