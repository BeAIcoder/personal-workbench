# -*- coding: utf-8 -*-
"""business_dq 数据质量巡检模块测试（合成数据，临时库）。

存量坏数据经 ORM 直接插入（写守卫本来就会拦住这类数据——巡检的价值恰在存量巡检）。
日期类断言用固定 as_of=2026-11-01，可复算。
"""
import json
from datetime import date

from app.database import SessionLocal
from app.modules import get_loaded_modules
from app.modules.business_dq.service import dq_summary

from app.modules.business_leasing.models import LeasingLead
from app.modules.business_operations.models import OpsBill, OpsContract, OpsWorkOrder
from app.modules.business_legal.models import LegalCase
from app.modules.business_hr.models import HrEmployee, HrPayroll
from app.modules.business_planning.models import PlanCampaign
from app.modules.business_facility.models import FacilityCompliance

AS_OF = "2026-11-01"


def _plant(model, **fields):
    with SessionLocal() as db:
        db.add(model(**fields))
        db.commit()


def test_manifest_loaded_and_tool_preset():
    loaded = {m.name: m for m in get_loaded_modules()}
    assert "business_dq" in loaded
    manifest = loaded["business_dq"]
    assert manifest.prefix == "/api/business/dq"
    assert manifest.tables == []
    assert manifest.preset_experts == ["realestate", "cre_gm"]
    tool = next(item for item in manifest.agent_tools if item["name"] == "dq_summary")
    assert tool["readonly"] is True
    assert callable(tool["func"])


def test_empty_db_safe_summary(client):
    resp = client.get("/api/business/dq/summary", params={"as_of": AS_OF})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["as_of"] == AS_OF
    assert data["overall_score"] == 100
    assert set(data["domains"]) == {"leasing", "operations", "hr", "legal", "planning", "facility", "finance"}
    for key in ("leasing", "operations", "hr", "legal", "planning", "facility"):
        assert data["domains"][key]["available"] is True
        assert data["domains"][key]["score"] == 100
    assert data["top_issues"] == []


def test_orphan_lead_unit_and_bill_contract(client):
    _plant(LeasingLead, company="孤儿线索", stage="negotiating", unit_id=99999)
    _plant(OpsBill, contract_id=99999, period="2026-10", status="pending")
    resp = client.get("/api/business/dq/summary", params={"as_of": AS_OF})
    data = resp.json()
    issues = {(i["domain"], i["check"], i["level"]): i for i in data["top_issues"]}
    assert issues[("leasing", "孤儿引用", "warning")]["count"] == 1
    assert issues[("operations", "孤儿引用", "error")]["count"] == 1
    assert "铺位已不存在" in issues[("leasing", "孤儿引用", "warning")]["suggestion"]
    lease_score = data["domains"]["leasing"]["score"]
    ops_score = data["domains"]["operations"]["score"]
    assert lease_score == 97 and ops_score == 90
    assert data["overall_score"] is not None and data["overall_score"] < 100


def test_date_inversion_and_duplicates(client):
    _plant(OpsContract, contract_no="HT-001", tenant="甲", start_date=date(2026, 5, 10), end_date=date(2026, 5, 1), status="active")
    _plant(LegalCase, case_no="AJ-001", title="测试案件")
    _plant(LegalCase, case_no="AJ-001", title="测试案件2")
    resp = client.get("/api/business/dq/summary", params={"as_of": AS_OF})
    data = resp.json()
    issues = {(i["domain"], i["check"], i["level"]): i for i in data["top_issues"]}
    assert issues[("operations", "日期有效性", "error")]["count"] == 1
    assert issues[("legal", "重复", "warning")]["count"] == 1
    assert "租期" in issues[("operations", "日期有效性", "error")]["suggestion"]


def test_hr_future_hire_and_salary(client):
    _plant(HrEmployee, emp_no="E1", name="张三", hire_date=date(2027, 6, 1), base_salary=-500.0)
    resp = client.get("/api/business/dq/summary", params={"as_of": AS_OF})
    data = resp.json()
    issues = {(i["domain"], i["check"], i["level"]): i for i in data["top_issues"]}
    assert issues[("hr", "日期有效性", "warning")]["count"] == 1
    assert issues[("hr", "金额异常", "error")]["count"] == 1
    assert data["domains"]["hr"]["score"] == 87


def test_planning_date_inversion(client):
    _plant(PlanCampaign, name="周年庆", year=2026, start_date=date(2026, 6, 30), end_date=date(2026, 6, 1))
    resp = client.get("/api/business/dq/summary", params={"as_of": AS_OF})
    data = resp.json()
    issues = {(i["domain"], i["check"], i["level"]): i for i in data["top_issues"]}
    assert issues[("planning", "日期有效性", "warning")]["count"] == 1


def test_facility_orphan_compliance(client):
    _plant(FacilityCompliance, equipment_id=99999, kind="annual_inspection", title="孤儿合规事项", due_date=date(2026, 12, 1))
    resp = client.get("/api/business/dq/summary", params={"as_of": AS_OF})
    data = resp.json()
    issues = {(i["domain"], i["check"], i["level"]): i for i in data["top_issues"]}
    assert issues[("facility", "孤儿引用", "warning")]["count"] == 1


def test_work_order_orphan_contract_and_equipment(client):
    """跨模块勾稽（2026-11-02）：工单→合同 / 工单→设备的孤儿引用。"""
    _plant(OpsWorkOrder, code="WOX1", contract_id=99999)
    _plant(OpsWorkOrder, code="WOX2", equipment_id=88888)
    resp = client.get("/api/business/dq/summary", params={"as_of": AS_OF})
    data = resp.json()
    ops_issues = [i for i in data["top_issues"] if i["domain"] == "operations" and i["check"] == "孤儿引用"]
    assert len(ops_issues) == 2, ops_issues
    samples = {s for i in ops_issues for s in (i.get("sample") or [])}
    assert {"WOX1", "WOX2"} <= samples


def test_ops_amount_and_status_consistency(client):
    """v3 勾稽：超收 / 状态金额不一致 / 履约中合同过期。"""
    _plant(OpsContract, contract_no="HT-V3-1", tenant="甲", status="active",
           start_date=date(2026, 1, 1), end_date=date(2026, 9, 30))
    _plant(OpsBill, contract_id=1, period="2026-10", total_amount=100.0, paid_amount=200.0, status="paid")
    _plant(OpsBill, contract_id=1, period="2026-11", total_amount=100.0, paid_amount=10.0, status="unpaid")
    data = client.get("/api/business/dq/summary", params={"as_of": AS_OF}).json()
    issues = [(i["check"], i["level"], i["count"]) for i in data["top_issues"] if i["domain"] == "operations"]
    assert ("金额异常", "error", 1) in issues
    assert any(c == "状态一致性" and lv == "warning" and cnt >= 1 for c, lv, cnt in issues)
    # 合同过期：end 2026-09-30 < as_of 2026-11-01，且 active
    assert ("状态一致性", "warning", 2) in issues or ("状态一致性", "warning", 1) in issues


def test_hr_payroll_without_attendance(client):
    _plant(HrEmployee, emp_no="E-V3", name="王五", hire_date=date(2026, 1, 1))
    _plant(HrPayroll, emp_id=1, month="2026-10", base_salary=8000.0)
    data = client.get("/api/business/dq/summary", params={"as_of": AS_OF}).json()
    issues = [(i["check"], i["count"]) for i in data["top_issues"] if i["domain"] == "hr"]
    assert ("跨表勾稽", 1) in issues


def test_planning_over_budget_and_facility_overdue(client):
    _plant(PlanCampaign, name="超支活动", year=2026, start_date=date(2026, 5, 1),
           end_date=date(2026, 5, 10), budget_total=100.0, actual_total=200.0)
    _plant(FacilityCompliance, equipment_id=None, kind="annual_inspection",
           title="过期年检", due_date=date(2026, 6, 1), done_date=None)
    data = client.get("/api/business/dq/summary", params={"as_of": AS_OF}).json()
    issues = [(i["domain"], i["check"]) for i in data["top_issues"]]
    assert ("planning", "预算执行") in issues
    assert ("facility", "过期未完成") in issues


def test_bip_voucher_imbalance(client):
    """v3 勾稽：按凭证借贷不平。"""
    from app.modules.finance_ledger_bip.models import BipEntry
    _plant(BipEntry, voucher_id=9001, batch_id=1, line_no=1, debit=100.0, credit=0.0)
    _plant(BipEntry, voucher_id=9001, batch_id=1, line_no=2, debit=0.0, credit=90.0)
    data = client.get("/api/business/dq/summary", params={"as_of": AS_OF}).json()
    fin = [(i["check"], i["level"], i["count"]) for i in data["top_issues"] if i["domain"] == "finance"]
    assert ("勾稽平衡", "error", 1) in fin


def test_tax_reconciliation(client):
    """v4 税务域勾稽：申报表 vs 财务数据口径。"""
    from app.modules.finance_tax.models import Enterprise, FinancialData, TaxReturn
    with SessionLocal() as db:
        ent = Enterprise(name="合成税务企业", tax_number="91330100MA2TAX00X")
        db.add(ent)
        db.commit()
        db.refresh(ent)
        db.add(FinancialData(enterprise_id=ent.id, year=2026, quarter=1, profit=100.0))
        db.add(FinancialData(enterprise_id=ent.id, year=2028, quarter=1, profit=50.0))
        db.add(TaxReturn(enterprise_id=ent.id, year=2026, quarter=1, return_type="预缴",
                         status="draft", taxable_income=0.0, tax_amount=5000.0))
        db.add(TaxReturn(enterprise_id=ent.id, year=2027, quarter=None, return_type="年度",
                         status="submitted", taxable_income=100000.0, tax_amount=30000.0))
        db.commit()
    data = client.get("/api/business/dq/summary", params={"as_of": AS_OF}).json()
    fin = [(i["check"], i["level"], i["count"]) for i in data["top_issues"] if i["domain"] == "finance"]
    assert ("税额异常", "error", 1) in fin          # 有税无所得（2026）
    assert ("申报勾稽", "warning", 1) in fin        # 2027 申报无财务数据
    assert ("税额异常", "warning", 1) in fin        # 2027 税额超 25%
    assert ("申报勾稽", "info", 1) in fin           # 2028 财务数据无申报


def test_board_consecutive_failures(client):
    """v4：最近两条快照均 failed → error。"""
    from app.modules.finance_board.models import BoardSnapshot
    _plant(BoardSnapshot, stamp="20261101_010101", status="failed")
    _plant(BoardSnapshot, stamp="20261101_020202", status="failed")
    data = client.get("/api/business/dq/summary", params={"as_of": AS_OF}).json()
    fails = [i for i in data["top_issues"] if i["check"] == "连续失败"]
    assert fails and fails[0]["level"] == "error" and fails[0]["count"] == 2


def _kpi_env(monkeypatch, tmp_path, kpi):
    """造「看板快照 HTML + 管线数据源」环境，KPI 由调用方给定。"""
    from app.modules.finance_board import service as board_svc
    from app.modules.finance_board.models import BoardSnapshot
    payload = {"kpi": kpi, "op": {"years": [2026]}}
    pdir = tmp_path / "pipe"
    (pdir / "data").mkdir(parents=True)
    (pdir / "data" / "report_data.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    html = tmp_path / "board.html"
    html.write_text("<html><script>\nconst R = " + json.dumps(payload, ensure_ascii=False) + ";\n</script></html>", encoding="utf-8")
    _plant(BoardSnapshot, stamp="20261102_140000", status="ok", html_path=str(html))
    monkeypatch.setattr(board_svc, "pipeline_dir", lambda: pdir)
    monkeypatch.setattr(board_svc, "_MIN_HTML_BYTES", 0)


def _seed_business_ledger():
    from app.modules.business_leasing.models import LeasingUnit
    _plant(LeasingUnit, building="A", floor="1F", unit_no="A-101", area=100.0, status="leased")
    _plant(OpsContract, contract_no="HT-KPI", tenant="甲", status="active",
           start_date=date(2026, 1, 1), end_date=date(2027, 12, 31), area=100.0)
    _plant(OpsBill, contract_id=1, period="2026-10", total_amount=100.0, paid_amount=100.0, status="paid")


def test_board_kpi_vs_business_db_warning(client, monkeypatch, tmp_path):
    """v5.1 双源对账：看板 KPI 与业务库差异大 → warning。"""
    _seed_business_ledger()
    _kpi_env(monkeypatch, tmp_path, {"出租率": 88.5, "开业率": 88.0, "收缴率": 65.0,
                                     "空铺数": 30.0, "累计应收_万元": 2058.6})
    data = client.get("/api/business/dq/summary", params={"as_of": AS_OF}).json()
    hit = [i for i in data["top_issues"] if i["check"] == "口径对照"]
    assert hit and hit[0]["level"] == "warning"
    samples = hit[0].get("sample") or []
    assert any("出租率" in s for s in samples)
    assert any("收缴率" in s for s in samples)


def test_board_kpi_vs_business_db_consistent_info(client, monkeypatch, tmp_path):
    """v5.1 双源对账：看板 KPI 与业务库近似一致 → info。"""
    _seed_business_ledger()
    _kpi_env(monkeypatch, tmp_path, {"出租率": 100.0, "开业率": 100.0, "收缴率": 100.0,
                                     "空铺数": 0.0, "累计应收_万元": 0.01})
    data = client.get("/api/business/dq/summary", params={"as_of": AS_OF}).json()
    hit = [i for i in data["top_issues"] if i["check"] == "口径对照"]
    assert hit and hit[0]["level"] == "info"


def test_dq_board_artifact_mismatch(client, monkeypatch, tmp_path):
    """v5：产物内容巡检失败（HTML 无内嵌数据块）→ dq 财务域 warning。"""
    from app.modules.finance_board import service as board_svc
    from app.modules.finance_board.models import BoardSnapshot
    html = tmp_path / "board.html"
    html.write_text("<html>no data marker</html>", encoding="utf-8")
    _plant(BoardSnapshot, stamp="20261102_130000", status="ok", html_path=str(html))
    monkeypatch.setattr(board_svc, "_MIN_HTML_BYTES", 0)
    data = client.get("/api/business/dq/summary", params={"as_of": AS_OF}).json()
    hit = [i for i in data["top_issues"] if i["check"] == "产物一致性"]
    assert hit and hit[0]["level"] == "warning"
    assert "内嵌数据" in (hit[0].get("sample") or [])


def test_board_stale_snapshot(client):
    """看板陈旧快照巡检：最近 ok 快照超过 7 天 → warning。"""
    from datetime import datetime, timedelta
    from app.modules.finance_board.models import BoardSnapshot
    _plant(BoardSnapshot, stamp="20261020_120000", status="ok",
           created_at=datetime.now() - timedelta(days=10))
    resp = client.get("/api/business/dq/summary", params={"as_of": AS_OF})
    data = resp.json()
    stale = [i for i in data["top_issues"] if i["check"] == "陈旧快照"]
    assert stale and stale[0]["level"] == "warning" and stale[0]["count"] >= 10


def test_dq_summary_tool_compact_and_readonly(client):
    _plant(HrEmployee, emp_no="E2", name="李四", hire_date=date(2027, 6, 1), base_salary=-500.0)
    out = dq_summary(as_of=AS_OF)
    assert out["overall_score"] < 100
    hr = out["domains"]["hr"]
    assert hr["available"] is True and hr["error"] == 1 and hr["warning"] == 1
    assert hr["top_issues"] and hr["top_issues"][0]["level"] in ("error", "warning")


def test_as_of_invalid_rejected(client):
    resp = client.get("/api/business/dq/summary", params={"as_of": "2026/11/01"})
    assert resp.status_code == 422
    resp = client.get("/api/business/dq/summary")  # 缺省（不带 as_of）必须 200
    assert resp.status_code == 200


def test_score_aggregation(client):
    for i in range(12):
        _plant(LegalCase, case_no="DUP", title=f"重复案件{i}")
    resp = client.get("/api/business/dq/summary", params={"as_of": AS_OF})
    data = resp.json()
    assert data["domains"]["legal"]["score"] == 97
    assert data["overall_score"] is not None# __N10B__