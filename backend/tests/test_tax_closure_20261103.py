# -*- coding: utf-8 -*-
"""Task 3：企业所得税、四填报与申报记录闭环回归（2026-11-03）。

覆盖台账 L-050～L-067：
- 高新属性进入计算/保存/导出同一链（第 9 行法定 25% + 第 11 行减免）；
- 高新与小微同时符合且未明确选择时阻断（不代为选择、不叠加）；
- A200000 第 1/2/3 行取真实利润表数据，不再拿应纳税所得额冒充利润总额；
- 年度记录在 A100000 未接入前 409，不导伪 Q4；
- draft 可更正/删除，submitted 受保护，重复 409 文案指向可执行操作；
- 小微判定展示字段映射、used_by_year、列表排序、年度全串校验、money 舍入；
- 研发加计与残保金按各自 policy_key 校验有效期；
- 设备扣除区分购置额与当年折旧、广告费返回结转额、调整明细契约移除；
- 税务 xlsx 用户文本 safe_cell、删除填报清理导出产物。

全部合成数据；client 由 conftest 提供（临时 SQLite + 临时目录）。
"""
from __future__ import annotations

import io
import os
import pathlib

import pytest

from app.modules.finance_tax.config.policy_validity import POLICY_REGISTRY, PolicyValidity, policy_status
from app.modules.finance_tax.models import Enterprise, TaxReturn
from app.modules.finance_tax.routers import tax_returns as tr

P = "/api/finance/tax"


def _enterprise(client, tax_number, **kw):
    payload = {
        "name": "闭环测试企业",
        "tax_number": tax_number,
        "industry_type": "一般行业",
        "employee_count": 50,
        "total_assets": 100.0,
        "is_restricted_industry": False,
    }
    payload.update(kw)
    r = client.post(f"{P}/enterprises/", json=payload)
    assert r.status_code == 200, r.text
    return r.json()


def _save(client, enterprise_id, **kw):
    payload = {
        "enterprise_id": enterprise_id,
        "tax_period": "2026",
        "quarter": "Q1",
        "report_type": "A类",
        "total_revenue": 100.0,
    }
    payload.update(kw)
    return client.post(f"{P}/tax-returns/", json=payload)


def _exported_rows(client, return_id):
    """导出 xlsx 并解析行次→金额（第 9 行税率返回字符串）。"""
    import openpyxl

    resp = client.get(f"{P}/tax-returns/{return_id}/export/excel")
    assert resp.status_code == 200, resp.text
    ws = openpyxl.load_workbook(io.BytesIO(resp.content)).active
    rows = {}
    for row in ws.iter_rows(min_row=1, values_only=True):
        label = row[0]
        if isinstance(label, int) or (isinstance(label, str) and label.isdigit()):
            key = int(label)
            if key in (4001, 4002, 4003):
                continue
            amount = row[2]
            rows[key] = amount if key == 9 else float(str(amount).replace(",", ""))
    return rows


# --------------------------------------------------------------------------
# L-050 高新属性进入同一计算链
# --------------------------------------------------------------------------

def test_enterprise_high_tech_field_roundtrip(client):
    ent = _enterprise(client, "91110103HT000001", is_high_tech=True)
    assert ent["is_high_tech"] is True

    off = client.put(f"{P}/enterprises/{ent['id']}", json={"is_high_tech": False})
    assert off.status_code == 200, off.text
    assert off.json()["is_high_tech"] is False

    legacy = _enterprise(client, "91110103HT000002")
    assert legacy["is_high_tech"] is False


def test_high_tech_100w_consistent_in_calculate_save_and_export(client):
    """100 万应纳税所得额：计算页、保存、A200000 导出三处一律 15%（15 万）。"""
    ent = _enterprise(client, "91110103HT000003", is_high_tech=True,
                      employee_count=500, total_assets=9000.0)

    calc = client.post(f"{P}/tax-returns/calculate", json={
        "enterprise_id": ent["id"], "year": "2026",
        "revenue": 100.0, "total_costs": 0.0, "total_expenses": 0.0,
        "employee_count": 500, "total_assets": 9000.0, "is_high_tech": True,
    })
    assert calc.status_code == 200, calc.text
    assert calc.json()["applicable_tax_rate"] == pytest.approx(0.15)
    assert calc.json()["tax_amount_wan"] == pytest.approx(15.0)

    saved = _save(client, ent["id"])
    assert saved.status_code in (200, 201), saved.text
    body = saved.json()
    assert body["tax_amount"] == pytest.approx(150_000.0)
    assert body["tax_type"] == "高新技术企业"

    ret = client.get(f"{P}/tax-returns/{body['id']}").json()
    assert ret["tax_amount"] == pytest.approx(150_000.0)

    rows = _exported_rows(client, body["id"])
    assert rows[9] == "25%"
    assert rows[10] == 250_000.0
    assert rows[11] == 100_000.0
    assert rows[14] == 150_000.0


def test_high_tech_and_small_micro_requires_explicit_choice(client):
    """两项优惠不得叠加：未明确选择时阻断，选择后按所选计税并在结果标注。"""
    ent = _enterprise(client, "91110103HT000004", is_high_tech=True,
                      employee_count=20, total_assets=500.0)
    common = {
        "enterprise_id": ent["id"], "tax_period": "2026", "quarter": "Q2",
        "report_type": "A类", "total_revenue": 100.0,
    }
    blocked = _save(client, ent["id"], **{"quarter": "Q2"})
    assert blocked.status_code == 400, blocked.text
    detail = blocked.json()["detail"]
    assert "不" in detail and "叠加" in detail
    assert "high_tech" in detail and "small_micro" in detail

    high = client.post(f"{P}/tax-returns/", json={**common, "preferential_choice": "high_tech"})
    assert high.status_code in (200, 201), high.text
    assert high.json()["tax_type"] == "高新技术企业"
    assert high.json()["tax_amount"] == pytest.approx(150_000.0)

    small = client.post(f"{P}/tax-returns/", json={
        **common, "quarter": "Q3", "preferential_choice": "small_micro",
    })
    assert small.status_code in (200, 201), small.text
    assert small.json()["tax_type"] == "小型微利企业"
    assert small.json()["tax_amount"] == pytest.approx(50_000.0)


# --------------------------------------------------------------------------
# L-051 / L-067 A200000 行次勾稽
# --------------------------------------------------------------------------

def test_a200000_rows_come_from_real_income_statement(client):
    """收入 500 / 成本 200 / 费用 100 万 + 纳税调整 50 万：调整不计入季度预缴。

    复核 I-1：A200000 没有纳税调整行次，季度预缴按实际利润额（200 万）填第 8 行，
    调整只落库留痕并写进「计算方式」，留待年度汇算；第 14 行仍与库税额一致。
    """
    ent = _enterprise(client, "91110103AR000001", employee_count=500, total_assets=9000.0)
    saved = _save(client, ent["id"], **{
        "total_revenue": 500.0, "total_costs": 200.0, "total_expenses": 100.0,
        "tax_adjustments": 50.0,
    })
    assert saved.status_code in (200, 201), saved.text
    ret = saved.json()
    assert ret["operating_revenue"] == pytest.approx(5_000_000.0)
    assert ret["operating_cost"] == pytest.approx(2_000_000.0)
    assert ret["total_profit"] == pytest.approx(2_000_000.0)
    # 季度预缴不纳入纳税调整：应纳税所得额 = 利润总额 200 万（调整 50 万只留痕）
    assert ret["taxable_income"] == pytest.approx(2_000_000.0)
    assert ret["tax_amount"] == pytest.approx(500_000.0)
    assert ret["tax_adjustments"] == pytest.approx(500_000.0)

    rows = _exported_rows(client, ret["id"])
    assert rows[1] == pytest.approx(5_000_000.0)
    assert rows[2] == pytest.approx(2_000_000.0)
    assert rows[3] == pytest.approx(2_000_000.0)
    assert rows[8] == pytest.approx(rows[3] - rows[4] - rows[5] - rows[6])
    assert rows[8] == pytest.approx(2_000_000.0)
    assert rows[14] == pytest.approx(ret["tax_amount"], abs=1e-9)


def test_a200000_row_arithmetic_holds_without_adjustments(client):
    """无纳税调整时第 8 行必须等于 行3-行4-行5-行6（表内自洽）。"""
    ent = _enterprise(client, "91110103AR000002", employee_count=500, total_assets=9000.0)
    saved = _save(client, ent["id"], **{
        "total_revenue": 500.0, "total_costs": 200.0, "total_expenses": 100.0,
    })
    assert saved.status_code in (200, 201), saved.text
    rows = _exported_rows(client, saved.json()["id"])
    assert rows[8] == pytest.approx(rows[3] - rows.get(4, 0.0) - rows.get(5, 0.0) - rows.get(6, 0.0))


def test_a200000_loss_year_not_clamped_to_zero():
    """亏损年度第 8 行按实际负数展示（旧实现 max(0, …) 把亏损截成 0）。"""
    from app.modules.finance_tax.services.tax_form_generator import (
        A200000FormData,
        EnterpriseInfo,
        TaxPeriod,
    )

    form = A200000FormData(
        enterprise=EnterpriseInfo(name="亏损企业", tax_number="X"),
        tax_period=TaxPeriod(year=2026, quarter=1),
        operating_revenue=1_000_000.0, operating_cost=1_500_000.0, total_profit=-500_000.0,
    )
    results = form.calculate()
    assert results["row8_actual_profit"] == pytest.approx(-500_000.0)
    assert results["row10_tax_payable"] == pytest.approx(0.0)


def test_a200000_row5_does_not_double_count_details():
    """第 5 行是所得减免总额 + 明细（明细仅作拆分），不得两项相加。"""
    from app.modules.finance_tax.services.tax_form_generator import (
        A200000FormData,
        EnterpriseInfo,
        TaxPeriod,
    )

    form = A200000FormData(
        enterprise=EnterpriseInfo(name="减免企业", tax_number="X"),
        tax_period=TaxPeriod(year=2026, quarter=1),
        total_profit=1_000_000.0,
        income_reduction_amount=100_000.0,
        agriculture_forestry_reduction=60_000.0,
        technology_transfer_reduction=40_000.0,
    )
    results = form.calculate()
    assert results["row5_income_deduction"] == pytest.approx(100_000.0)
    assert results["row8_actual_profit"] == pytest.approx(900_000.0)


# --------------------------------------------------------------------------
# L-052 年度记录不再导伪 Q4
# --------------------------------------------------------------------------

def test_annual_return_export_rejected_with_409(client):
    ent = _enterprise(client, "91110103AN000001")
    saved = _save(client, ent["id"], **{"quarter": None, "report_type": "B类"})
    assert saved.status_code in (200, 201), saved.text
    rid = saved.json()["id"]
    assert saved.json()["return_type"] == "年度"

    for kind in ("excel", "pdf"):
        resp = client.get(f"{P}/tax-returns/{rid}/export/{kind}")
        assert resp.status_code == 409, resp.text
        assert "A100000" in resp.json()["detail"]


def test_annual_record_form_builder_refuses_missing_quarter():
    ent = Enterprise(id=1, name="年度企业", tax_number="X", employee_count=10, total_assets=10.0)
    ret = TaxReturn(id=1, enterprise_id=1, year=2026, quarter=None, return_type="年度",
                    status="draft", taxable_income=100_000.0, tax_amount=25_000.0)
    with pytest.raises(ValueError):
        tr._build_a200000_form_data(ret, ent)


# --------------------------------------------------------------------------
# L-053 draft 更正/删除；submitted 受保护；409 文案可执行
# --------------------------------------------------------------------------

def test_draft_return_can_be_corrected(client):
    ent = _enterprise(client, "91110103UP000001", employee_count=500, total_assets=9000.0)
    created = _save(client, ent["id"]).json()
    assert created["tax_amount"] == pytest.approx(250_000.0)

    upd = client.put(f"{P}/tax-returns/{created['id']}", json={
        "enterprise_id": ent["id"], "tax_period": "2026", "quarter": "Q1",
        "report_type": "A类", "total_revenue": 200.0,
    })
    assert upd.status_code == 200, upd.text
    body = upd.json()
    assert body["taxable_income"] == pytest.approx(2_000_000.0)
    assert body["tax_amount"] == pytest.approx(500_000.0)
    assert body["status"] == "draft"


def test_draft_correction_cannot_switch_enterprise(client):
    ent = _enterprise(client, "91110103UP000005")
    other = _enterprise(client, "91110103UP000006")
    rid = _save(client, ent["id"]).json()["id"]
    resp = client.put(f"{P}/tax-returns/{rid}", json={
        "enterprise_id": other["id"], "tax_period": "2026", "quarter": "Q1",
        "report_type": "A类", "total_revenue": 100.0,
    })
    assert resp.status_code == 400, resp.text
    assert "更换企业" in resp.json()["detail"]


def test_submitted_return_is_protected(client):
    ent = _enterprise(client, "91110103UP000002")
    rid = _save(client, ent["id"]).json()["id"]
    assert client.put(f"{P}/tax-returns/{rid}/submit").status_code == 200

    upd = client.put(f"{P}/tax-returns/{rid}", json={
        "enterprise_id": ent["id"], "tax_period": "2026", "quarter": "Q1",
        "report_type": "A类", "total_revenue": 200.0,
    })
    assert upd.status_code == 409, upd.text
    assert "已提交" in upd.json()["detail"]

    dele = client.delete(f"{P}/tax-returns/{rid}")
    assert dele.status_code == 409, dele.text
    assert client.get(f"{P}/tax-returns/{rid}").status_code == 200


def test_draft_return_can_be_deleted(client):
    ent = _enterprise(client, "91110103UP000003")
    rid = _save(client, ent["id"]).json()["id"]
    dele = client.delete(f"{P}/tax-returns/{rid}")
    assert dele.status_code in (200, 204), dele.text
    assert client.get(f"{P}/tax-returns/{rid}").status_code == 404


def test_duplicate_409_points_to_real_actions(client):
    ent = _enterprise(client, "91110103UP000004")
    first = _save(client, ent["id"]).json()
    dup = _save(client, ent["id"])
    assert dup.status_code == 409
    detail = dup.json()["detail"]
    assert str(first["id"]) in detail
    assert "更正" in detail
    assert "删除" in detail


# --------------------------------------------------------------------------
# L-054 小微判定展示字段映射 / used_by_year
# --------------------------------------------------------------------------

def test_small_micro_criteria_display_uses_engine_structure(client):
    ent = _enterprise(client, "91110103SM000001", employee_count=12, total_assets=800.0)
    resp = client.post(f"{P}/tax-returns/calculate", json={
        "enterprise_id": ent["id"], "year": "2026",
        "revenue": 100.0, "total_costs": 0.0, "total_expenses": 0.0,
        "employee_count": 12, "total_assets": 800.0,
    })
    assert resp.status_code == 200, resp.text
    d = resp.json()
    assert d["is_small_micro"] is True
    step = next(s for s in d["calculation_steps"] if s["title"] == "小型微利企业判定")
    assert step["criteria_not_met"] == []
    assert len(step["criteria_met"]) == 4
    assert any("从业人数 12人" in c for c in step["criteria_met"])


def test_loss_used_by_year_returns_engine_used_amounts(client):
    ent = _enterprise(client, "91110103SM000002", employee_count=12, total_assets=800.0)
    resp = client.post(f"{P}/tax-returns/calculate", json={
        "enterprise_id": ent["id"], "year": "2026",
        "revenue": 400.0, "total_costs": 0.0, "total_expenses": 0.0,
        "employee_count": 12, "total_assets": 800.0,
        "prior_year_losses": {"2024": 200.0},
    })
    assert resp.status_code == 200, resp.text
    d = resp.json()
    assert d["loss_carry_forward_used_wan"] == pytest.approx(200.0)
    assert d["loss_used_by_year"] == {"2024": 200.0}


# --------------------------------------------------------------------------
# L-055 / L-058 研发加计有效期；广告费结转
# --------------------------------------------------------------------------

def test_rd_120pct_policy_expiry_rejected(client):
    ent = _enterprise(client, "91110103RD000001", employee_count=500, total_assets=9000.0)
    payload = {
        "enterprise_id": ent["id"], "revenue": 100.0, "total_costs": 0.0,
        "total_expenses": 0.0, "rnd_expense": 100.0,
        "enterprise_type": "集成电路企业",
        "employee_count": 500, "total_assets": 9000.0,
    }
    ok = client.post(f"{P}/tax-returns/calculate", json={**payload, "year": "2026"})
    assert ok.status_code == 200, ok.text
    rd = ok.json()["adjustments_detail"]["rd_expense"]
    assert rd["super_deduction_amount_wan"] == pytest.approx(120.0)

    expired = client.post(f"{P}/tax-returns/calculate", json={**payload, "year": "2028"})
    assert expired.status_code == 400, expired.text
    assert "2027-12-31" in expired.json()["detail"]


def test_advertising_expense_reports_carry_forward():
    from app.modules.finance_tax.config.tax_rules import (
        IndustryType,
        calculate_advertising_expense_limit,
    )

    result = calculate_advertising_expense_limit(expense_amount=2_000_000.0, revenue=10_000_000.0)
    assert result["allowed_deduction"] == pytest.approx(1_500_000.0)
    assert result["tax_adjustment_increase"] == pytest.approx(500_000.0)
    assert result["carry_forward_to_next_year"] == pytest.approx(500_000.0)

    tobacco = calculate_advertising_expense_limit(
        expense_amount=1_000_000.0, revenue=10_000_000.0, industry=IndustryType.TOBACCO,
    )
    assert tobacco["allowed_deduction"] == 0.0
    assert tobacco["carry_forward_to_next_year"] == 0.0


# --------------------------------------------------------------------------
# L-057 设备扣除区分购置额与当年折旧
# --------------------------------------------------------------------------

def test_equipment_deduction_nets_current_year_depreciation(client):
    ent = _enterprise(client, "91110103EQ000001", employee_count=500, total_assets=9000.0)
    resp = client.post(f"{P}/tax-returns/calculate", json={
        "enterprise_id": ent["id"], "year": "2026",
        "revenue": 500.0, "total_costs": 0.0, "total_expenses": 0.0,
        "equipment_purchase": 100.0, "equipment_depreciation": 20.0,
        "employee_count": 500, "total_assets": 9000.0,
    })
    assert resp.status_code == 200, resp.text
    eq = resp.json()["adjustments_detail"]["equipment_one_time"]
    assert eq["one_time_deduction_wan"] == pytest.approx(100.0)
    assert eq["current_year_depreciation_wan"] == pytest.approx(20.0)
    assert eq["tax_adjustment_decrease_wan"] == pytest.approx(80.0)


def test_equipment_depreciation_cannot_exceed_purchase():
    from app.modules.finance_tax.services.corporate_income_tax_calculator import (
        CorporateIncomeTaxCalculator,
    )

    result = CorporateIncomeTaxCalculator.calculate_equipment_one_time_deduction(
        equipment_purchase_wan=100.0, equipment_depreciation_wan=120.0,
    )
    assert result["one_time_deduction_wan"] == pytest.approx(100.0)
    assert result["tax_adjustment_decrease_wan"] == pytest.approx(0.0)


# --------------------------------------------------------------------------
# L-061 年度全串校验
# --------------------------------------------------------------------------

def test_policy_status_rejects_partial_year_string():
    status = policy_status("small_micro_5pct", "2026abc")
    assert status["valid"] is False
    assert "4 位" in status["message"]
    assert status["status"] == "not_started"


def test_calculate_rejects_partial_year(client):
    ent = _enterprise(client, "91110103YR000001", employee_count=500, total_assets=9000.0)
    resp = client.post(f"{P}/tax-returns/calculate", json={
        "enterprise_id": ent["id"], "year": "2026abc",
        "revenue": 100.0, "total_costs": 0.0, "total_expenses": 0.0,
        "employee_count": 500, "total_assets": 9000.0,
    })
    assert resp.status_code == 400, resp.text
    assert "年度" in resp.json()["detail"]


# --------------------------------------------------------------------------
# L-060 / L-063 内部异常中文化；列表排序；money 舍入
# --------------------------------------------------------------------------

def test_internal_calculation_error_is_chinese_and_hides_details(client, monkeypatch):
    ent = _enterprise(client, "91110103ER000001")
    from app.modules.finance_tax.services.corporate_income_tax_calculator import (
        CorporateIncomeTaxCalculator,
    )

    def boom(*args, **kwargs):
        raise RuntimeError("secret-internal-path-C:/x")

    monkeypatch.setattr(CorporateIncomeTaxCalculator, "calculate", staticmethod(boom))
    resp = client.post(f"{P}/tax-returns/calculate", json={
        "enterprise_id": ent["id"], "year": "2026", "revenue": 100.0,
    })
    assert resp.status_code == 500
    detail = resp.json()["detail"]
    assert "secret-internal-path" not in detail
    assert "计算失败" in detail


def test_tax_return_list_is_stably_ordered(client):
    ent = _enterprise(client, "91110103OR000001")
    ids = []
    for q in ("Q1", "Q2", "Q3"):
        ids.append(_save(client, ent["id"], **{"quarter": q}).json()["id"])
    rows = client.get(f"{P}/tax-returns/", params={"enterprise_id": ent["id"]}).json()
    assert [r["id"] for r in rows] == sorted(ids, reverse=True)


def test_tax_amount_uses_half_up_money_rounding(client):
    """10.70 元应税 × 25% = 2.675 → money() 进位 2.68（内置 round 会截成 2.67）。"""
    ent = _enterprise(client, "91110103MO000001", employee_count=500, total_assets=9000.0)
    resp = _save(client, ent["id"], **{"total_revenue": 0.00107})
    assert resp.status_code in (200, 201), resp.text
    assert resp.json()["taxable_income"] == pytest.approx(10.7)
    assert resp.json()["tax_amount"] == pytest.approx(2.68)


def test_zero_income_saves_and_exports_without_error(client):
    """空值边界：收入全 0 → 应纳税所得额 0、税额 0，导出仍能生成（行次全 0）。"""
    ent = _enterprise(client, "91110103ZE000001", employee_count=500, total_assets=9000.0)
    saved = _save(client, ent["id"], **{"total_revenue": 0.0})
    assert saved.status_code in (200, 201), saved.text
    assert saved.json()["taxable_income"] == pytest.approx(0.0)
    assert saved.json()["tax_amount"] == pytest.approx(0.0)
    rows = _exported_rows(client, saved.json()["id"])
    assert rows[1] == pytest.approx(0.0)
    assert rows[3] == pytest.approx(0.0)
    assert rows[14] == pytest.approx(0.0)


def test_loss_year_saves_and_exports_negative_profit(client):
    """亏损边界：收入 100 万 − 成本 150 万 = −50 万；导出第 3/8 行为负、税额 0。"""
    ent = _enterprise(client, "91110103LO000001", employee_count=500, total_assets=9000.0)
    saved = _save(client, ent["id"], **{"total_revenue": 100.0, "total_costs": 150.0})
    assert saved.status_code in (200, 201), saved.text
    assert saved.json()["taxable_income"] == pytest.approx(-500_000.0)
    assert saved.json()["tax_amount"] == pytest.approx(0.0)
    rows = _exported_rows(client, saved.json()["id"])
    assert rows[3] == pytest.approx(-500_000.0)
    assert rows[8] == pytest.approx(-500_000.0)
    assert rows[10] == pytest.approx(0.0)


# --------------------------------------------------------------------------
# L-059 safe_cell：税务 xlsx 用户文本
# --------------------------------------------------------------------------

def test_a200000_excel_escapes_user_text_formulas(client):
    import openpyxl

    ent = _enterprise(client, "91110103FX000001", name="=1+1", industry_type="@SUM(A1:A2)")
    rid = _save(client, ent["id"]).json()["id"]
    resp = client.get(f"{P}/tax-returns/{rid}/export/excel")
    assert resp.status_code == 200, resp.text
    ws = openpyxl.load_workbook(io.BytesIO(resp.content)).active
    values = [str(c.value) for row in ws.iter_rows() for c in row if c.value is not None]
    # 企业名是独立单元格：必须被转义成文本
    assert any(v == "'=1+1" for v in values)
    # 全表不得出现以 = 或 @ 开头的原生单元格（否则 Excel 会当公式执行）
    assert not any(v.startswith(("=", "@")) for v in values)
    # 行业/机关备注为带标签的文本单元格，内容仍在但不构成公式
    assert any("@SUM" in v for v in values)


# --------------------------------------------------------------------------
# L-064 残保金 policy_key 分支
# --------------------------------------------------------------------------

def test_dsf_branch_uses_its_own_policy_key(client):
    exempt = client.post("/api/finance/dsf/filings", json={
        "year": "2026", "total_employees": 20, "total_salary": 2_000_000,
        "disabled_employees": 0,
    })
    assert exempt.status_code == 201, exempt.text
    assert exempt.json()["calc_result"]["policy"]["policy_key"] == "disability_fund_exemption"

    tiered = client.post("/api/finance/dsf/filings", json={
        "year": "2026", "total_employees": 100, "total_salary": 10_000_000,
        "disabled_employees": 0,
    })
    assert tiered.status_code == 201, tiered.text
    assert tiered.json()["calc_result"]["policy"]["policy_key"] == "disability_fund_tiered"


def test_dsf_tiered_branch_validates_tiered_policy_expiry(monkeypatch):
    from app.modules.finance_dsf import service as dsf

    # 免征 key 设为长期有效：只有非免征分支仍按 tiered key 拒绝
    monkeypatch.setitem(
        POLICY_REGISTRY, "disability_fund_exemption", PolicyValidity("测试公告A", "2023-01-01", None),
    )
    monkeypatch.setitem(
        POLICY_REGISTRY, "disability_fund_tiered", PolicyValidity("测试公告B", "2023-01-01", "2027-12-31"),
    )
    assert dsf.calc_dsf("2028", 20, 2_000_000, 0)["exempt"] is True
    with pytest.raises(ValueError) as exc:
        dsf.calc_dsf("2028", 100, 10_000_000, 0)
    assert "2027-12-31" in str(exc.value)


# --------------------------------------------------------------------------
# L-065 删除填报清理导出产物
# --------------------------------------------------------------------------

@pytest.mark.parametrize("module,create_payload", [
    ("stamp-duty", {"period": "2026-09", "items": [{"tax_item": "买卖合同", "amount": 1_000_000}]}),
    ("vat", {"period": "2026-09", "taxpayer_type": "small_scale", "sales_amount": 400_000}),
    ("iit", {"year": "2026", "rows": [
        {"month": 9, "name": "甲", "salary": 10_000, "social_insurance": 500},
    ]}),
    ("dsf", {"year": "2026", "total_employees": 20, "total_salary": 2_000_000,
             "disabled_employees": 0}),
])
def test_filing_delete_removes_export_artifact(client, module, create_payload):
    base = f"/api/finance/{module}"
    created = client.post(f"{base}/filings", json=create_payload)
    assert created.status_code == 201, created.text
    fid = created.json()["id"]

    exported = client.post(f"{base}/filings/{fid}/export")
    assert exported.status_code == 200, exported.text
    filename = exported.json()["file"]

    download = client.get(f"{base}/filings/{fid}/download")
    assert download.status_code == 200
    assert download.content[:2] == b"PK"

    deleted = client.delete(f"{base}/filings/{fid}")
    assert deleted.status_code in (200, 204), deleted.text
    assert client.get(f"{base}/filings/{fid}/download").status_code == 404
    assert list(pathlib.Path(os.environ["FINANCE_DATA_DIR"]).rglob(filename)) == []


# --------------------------------------------------------------------------
# L-062 调整明细契约移除
# --------------------------------------------------------------------------

def test_adjustments_contract_removed(client):
    from app.modules.finance_tax.routers.tax_returns import TaxReturnCreate

    assert "adjustments" not in TaxReturnCreate.model_fields

    ent = _enterprise(client, "91110103AD000001")
    rid = _save(client, ent["id"]).json()["id"]
    assert client.get(f"{P}/tax-returns/{rid}/adjustments").status_code == 404


# --------------------------------------------------------------------------
# L-066 不可达旧税务服务删除
# --------------------------------------------------------------------------

@pytest.mark.parametrize("module_name", [
    "annual_tax_form_generator",
    "deferred_tax_calculator",
    "tax_policy_service",
    "tax_calculator",
])
def test_dead_tax_services_removed(module_name):
    services = pathlib.Path(__file__).resolve().parents[1] / "app" / "modules" / "finance_tax" / "services"
    assert not (services / f"{module_name}.py").exists(), f"不可达旧实现未删除：{module_name}.py"


def test_additional_tax_calculator_is_the_single_surcharge_engine():
    """附加税保留为唯一实现（真实申报对拍使用），不得再被两处重复定义。"""
    from app.modules.finance_tax.services import additional_tax_calculator as calc

    sur = calc.calculate_surcharges(vat_paid=197_363.24, city_tier=calc.CityTier.URBAN)
    assert sur.city_maintenance_tax == pytest.approx(13_815.43)


# --------------------------------------------------------------------------
# 企业字段幂等迁移
# --------------------------------------------------------------------------

def test_enterprise_high_tech_migration_is_idempotent(tmp_path, monkeypatch):
    from sqlalchemy import create_engine, text

    import app.main as main_mod

    eng = create_engine(f"sqlite:///{(tmp_path / 'legacy_tax.db').as_posix()}")
    with eng.begin() as conn:
        conn.execute(text(
            "CREATE TABLE mod_tax_enterprise (id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " name VARCHAR(200) NOT NULL, tax_number VARCHAR(50) NOT NULL,"
            " industry_type VARCHAR(100), employee_count INTEGER, total_assets FLOAT,"
            " is_restricted_industry BOOLEAN DEFAULT 0, created_at DATETIME, updated_at DATETIME)"
        ))
        conn.execute(text(
            "CREATE TABLE mod_tax_return (id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " enterprise_id INTEGER NOT NULL, year INTEGER NOT NULL, quarter INTEGER,"
            " return_type VARCHAR(20) NOT NULL, status VARCHAR(20) DEFAULT 'draft',"
            " taxable_income FLOAT DEFAULT 0, tax_amount FLOAT DEFAULT 0,"
            " created_at DATETIME, updated_at DATETIME)"
        ))
        conn.execute(text(
            "INSERT INTO mod_tax_enterprise (name, tax_number, industry_type)"
            " VALUES ('老企业','OLD001','一般行业')"
        ))

    monkeypatch.setattr(main_mod, "engine", eng)
    main_mod._ensure_sqlite_columns()
    main_mod._ensure_sqlite_columns()  # 二次执行幂等

    with eng.connect() as conn:
        ent_cols = {r[1] for r in conn.execute(text("PRAGMA table_info(mod_tax_enterprise)"))}
        ret_cols = {r[1] for r in conn.execute(text("PRAGMA table_info(mod_tax_return)"))}
        assert "is_high_tech" in ent_cols
        assert {"operating_revenue", "operating_cost", "total_profit", "tax_type"} <= ret_cols
        row = conn.execute(text("SELECT is_high_tech FROM mod_tax_enterprise WHERE tax_number='OLD001'")).first()
        assert row[0] in (0, None)
