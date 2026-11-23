# -*- coding: utf-8 -*-
"""finance_lease Agent 工具：唯一只读摘要 ``lease_summary``。

工具函数自行建立会话（不依赖 FastAPI 依赖），按合同返回草稿/归档数量、最近测算、
校验计数与待处理差异；不返回明细金额、不写库、不触发计算。
"""
from __future__ import annotations

from . import service


def lease_summary_tool(contract_id: int | None = None) -> dict:
    """只读摘要：省略 contract_id 返回全部合同，否则只看指定合同。"""
    from ...database import SessionLocal

    if contract_id is not None:
        try:
            contract_id = int(contract_id)
        except (TypeError, ValueError):
            return {"ok": False, "error": "contract_id 必须是整数"}
    try:
        with SessionLocal() as db:
            summary = service.lease_summary(db, contract_id=contract_id)
    except service.LeaseNotFoundError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, **summary.model_dump(mode="json")}
