# -*- coding: utf-8 -*-
"""2026-11-01 财务域只读工具测试（税种/POS/现金流，合成数据，临时库）。"""
from app.agents.module_tool_preset import FINANCE_TOOL_NAMES
from app.database import SessionLocal
from app.modules import get_loaded_modules
from app.modules.finance_cashflow.service import cashflow_summary_tool
from app.modules.finance_pos.service import pos_summary_tool
from app.modules.finance_tax.tools import tax_returns_summary_tool


def test_tool_preset_names_synced():
    for name in ("tax_returns_summary", "pos_summary", "cashflow_summary"):
        assert name in FINANCE_TOOL_NAMES


def test_manifest_tools_registered():
    loaded = {m.name: m for m in get_loaded_modules()}
    assert loaded["finance_pos"].preset_experts == ["finance_specialist"]
    assert loaded["finance_cashflow"].preset_experts == ["finance_specialist"]
    assert loaded["finance_tax"].preset_experts == ["finance_specialist"]
    tool = next(t for t in loaded["finance_pos"].agent_tools if t["name"] == "pos_summary")
    assert tool["readonly"] is True and callable(tool["func"])
    tool = next(t for t in loaded["finance_tax"].agent_tools if t["name"] == "tax_returns_summary")
    assert tool["readonly"] is True and callable(tool["func"])
    tool = next(t for t in loaded["finance_cashflow"].agent_tools if t["name"] == "cashflow_summary")
    assert tool["readonly"] is True and callable(tool["func"])


def test_pos_and_cashflow_summary_empty(client):
    out = pos_summary_tool()
    assert out["ok"] is True and out["total_runs"] == 0 and out["latest"] is None
    out = cashflow_summary_tool()
    assert out["ok"] is True and out["total_runs"] == 0 and out["latest"] is None


def test_tax_returns_summary_with_row(client):
    from app.modules.finance_tax.models import Enterprise, TaxReturn
    with SessionLocal() as db:
        ent = Enterprise(name="合成企业", tax_number="91330100MA2TEST00X")
        db.add(ent)
        db.commit()
        db.add(TaxReturn(enterprise_id=ent.id, year=2026, quarter=3, return_type="预缴", status="submitted", taxable_income=100000.0, tax_amount=2500.0))
        db.commit()
    out = tax_returns_summary_tool()
    assert out["ok"] is True and out["total_returns"] >= 1
    assert out["prepaid"] >= 1 and out["submitted"] >= 1
    assert out["latest"]["return_type"] == "预缴"
