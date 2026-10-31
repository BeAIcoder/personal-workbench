# -*- coding: utf-8 -*-
"""税种准入清单路由（只读）。

用途：让前端与运维能直接看到「哪些税种可算、哪些明确不做、为什么」，
避免把"未实现"误当成"已支持"，也避免有人绕过 registry 直接实现。

准入门禁 ``require_tax_scope`` 用于未来新增计税入口；本路由只做展示。
"""
from fastapi import APIRouter, HTTPException

from ..config.tax_scope import TAX_SCOPE_REGISTRY, list_tax_scopes, tax_scope_status

router = APIRouter(prefix="/scope", tags=["税种准入"])


@router.get("", summary="税种准入清单")
def list_scopes():
    """全部税种：admitted（可计算）在前，blocked（未准入）附原因。"""
    return {"tax_scopes": list_tax_scopes()}


@router.get("/{tax_key}", summary="单个税种准入状态")
def get_scope(tax_key: str):
    if tax_key not in TAX_SCOPE_REGISTRY:
        raise HTTPException(status_code=404, detail=f"税种 {tax_key} 未登记")
    return tax_scope_status(tax_key)
