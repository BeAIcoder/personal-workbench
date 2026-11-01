# -*- coding: utf-8 -*-
"""finance_tax 只读 Agent 工具：申报记录摘要。"""
from ...database import SessionLocal
from .models import TaxReturn


def tax_returns_summary_tool() -> dict:
    """企业所得税申报记录摘要（只读）：按类型/状态计数 + 最新一条。"""
    with SessionLocal() as db:
        total = db.query(TaxReturn).count()
        prepaid = db.query(TaxReturn).filter(TaxReturn.return_type == "预缴").count()
        submitted = db.query(TaxReturn).filter(TaxReturn.status == "submitted").count()
        latest = db.query(TaxReturn).order_by(TaxReturn.id.desc()).first()
        latest_view = None
        if latest is not None:
            latest_view = {
                "id": latest.id,
                "year": latest.year,
                "quarter": latest.quarter,
                "return_type": latest.return_type,
                "status": latest.status,
                "taxable_income": latest.taxable_income,
                "tax_amount": latest.tax_amount,
            }
        return {
            "ok": True,
            "module": "finance_tax",
            "total_returns": total,
            "prepaid": prepaid,
            "annual": total - prepaid,
            "submitted": submitted,
            "draft": total - submitted,
            "latest": latest_view,
        }
