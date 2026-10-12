"""finance_tax 模块路由装配：5 个子路由整体挂载（前缀由 manifest.prefix 提供）。

原「企业所得税计算」项目的 routers（enterprises/financial_import/policies/
tax_returns/textbook_reference）原样迁入，仅修正 import 与去掉 /api 前缀。
"""
from fastapi import APIRouter

from .routers import (
    enterprises,
    financial_import,
    policies,
    tax_returns,
    textbook_reference,
)

router = APIRouter()
router.include_router(enterprises.router)
router.include_router(financial_import.router)
router.include_router(policies.router)
router.include_router(tax_returns.router)
router.include_router(textbook_reference.router)
