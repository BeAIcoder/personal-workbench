# -*- coding: utf-8 -*-
"""Task 3 Fix round 1 回归：A200000 第 8 行勾稽、季度必填、分位舍入、历史记录。

对应复核 I-1～I-4 与 M-1～M-5：

- I-1 季度预缴不纳入纳税调整：第 8 行严格等于 行3-行4-行5-行6；调整只留痕不计税；
  第 14 行由落库税额反推第 11 行，保证导出税额与库 `tax_amount` 一分不差。
- I-2 A 类预缴创建/更正必须带季度；历史「预缴 + quarter=None」给出补季度更正路径，
  年度 guard 同时检查 return_type（不把缺季度的预缴当年度）。
- I-3 计算服务 / A200000FormData / 导出统一 Decimal/money，10.70 元边界不差 1 分。
- I-4 历史记录利润字段全 0 且应纳税所得额非 0 → 导出 409 提示补录；旧 `tax_type`
  不改变导出税额；导出税额高于法定税率可表达范围时 409。
- M-1 计算接口以企业档案的高新资格为准（忽略请求体临时开关）。
- M-3 企业更新显式 null 不再自伤 500。
- M-4 元→万元→元 更正回填保留分位。
- M-5 删除填报先 commit 再清产物；产物清理只认导出目录内的文件名。

全部合成数据；client 由 conftest 提供（临时 SQLite + 临时目录）。
"""
from __future__ import annotations

import pathlib

import pytest

from app.database import SessionLocal
from app.modules.finance_tax.models import Enterprise, TaxReturn
from app.modules.finance_tax.routers import tax_returns as tr
from app.modules.finance_tax.services.tax_form_generator import (
    A200000FormData,
    EnterpriseInfo,
    TaxPeriod,
)

P = "/api/finance/tax"


def _enterprise(client, tax_number, **kw):
    payload = {
        "name": "Fix1 测试企业",
        "tax_number": tax_number,
        "industry_type": "一般行业",
        "employee_count": 500,
        "total_assets": 9000.0,
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
    """导出 xlsx 并解析行次→金额（第 9 行税率返回字符串；calcN 为「计算方式」列）。"""
    import io

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
            rows[f"calc{key}"] = row[3] if len(row) > 3 else ""
    return rows


def _insert_legacy(**fields):
    """直接落一条历史申报记录（绕过创建接口的入参校验）。"""
    with SessionLocal() as s:
        ret = TaxReturn(**fields)
        s.add(ret)
        s.commit()
        s.refresh(ret)
        return ret.id


def _row8_of(rows):
    return rows[8]


# --------------------------------------------------------------------------
# I-1 第 8 行严格勾稽；调整只留痕不计入季度预缴
# --------------------------------------------------------------------------

def test_a200000_row8_equation_all_scenarios():
    """有/无调整全场景：第 8 行 == 行3 - 行4 - 行5 - 行6（不允许桥接覆盖）。"""
    cases = [
        # (kwargs, expected_row8)
        ({"total_profit": 1_000_000.0}, 1_000_000.0),
        ({"total_profit": 1_000_000.0, "tax_exempt_income": 100_000.0}, 900_000.0),
        ({"total_profit": 1_000_000.0, "income_reduction_amount": 50_000.0}, 950_000.0),
        ({"total_profit": 1_000_000.0, "prior_year_loss": 200_000.0}, 800_000.0),
        (
            {
                "total_profit": 1_000_000.0,
                "tax_exempt_income": 100_000.0,
                "income_reduction_amount": 50_000.0,
                "prior_year_loss": 200_000.0,
            },
            650_000.0,
        ),
        # 亏损年度按负数展示，不做 max(0, …) 截断
        ({"total_profit": -500_000.0}, -500_000.0),
    ]
    for kwargs, expected in cases:
        form = A200000FormData(
            enterprise=EnterpriseInfo(name="勾稽企业", tax_number="X"),
            tax_period=TaxPeriod(year=2026, quarter=1),
            **kwargs,
        )
        results = form.calculate()
        rows = form.to_row_data()
        row3 = float(rows[3]["amount"])
        row4 = float(rows[4]["amount"])
        row5 = float(rows[5]["amount"])
        row6 = float(rows[6]["amount"])
        assert results["row8_actual_profit"] == pytest.approx(expected)
        assert _row8_of({8: float(rows[8]["amount"])}) == pytest.approx(
            row3 - row4 - row5 - row6
        ), f"第 8 行未勾稽：{kwargs}"


def test_quarterly_save_excludes_tax_adjustments(client):
    """季度预缴：纳税调整只留痕（tax_adjustments），不计入应纳税所得额与税额。"""
    ent = _enterprise(client, "91110103F1000001")
    saved = _save(client, ent["id"], **{
        "total_revenue": 500.0, "total_costs": 200.0, "total_expenses": 100.0,
        "tax_adjustments": 50.0,
    })
    assert saved.status_code in (200, 201), saved.text
    body = saved.json()
    # 利润 200 万；调整 50 万不计入季度预缴（调整留到年度）
    assert body["total_profit"] == pytest.approx(2_000_000.0)
    assert body["taxable_income"] == pytest.approx(2_000_000.0)
    assert body["tax_amount"] == pytest.approx(500_000.0)
    assert body["tax_adjustments"] == pytest.approx(500_000.0)  # 留痕可查

    rows = _exported_rows(client, body["id"])
    assert rows[1] == pytest.approx(5_000_000.0)
    assert rows[2] == pytest.approx(2_000_000.0)
    assert rows[3] == pytest.approx(2_000_000.0)
    assert rows[8] == pytest.approx(2_000_000.0)
    assert rows[8] == pytest.approx(rows[3] - rows[4] - rows[5] - rows[6])
    assert rows[14] == pytest.approx(body["tax_amount"], abs=1e-9)
    # 未计列的调整必须在「计算方式」列说明，而不是静默丢弃
    assert "纳税调整" in str(rows.get("calc8", ""))


def test_a200000_stored_tax_amount_drives_row14():
    """第 11 行由落库税额反推：三种优惠口径下第 14 行都等于落库税额。"""
    cases = [
        ({"is_small_micro": True}, 50_000.0, 200_000.0),
        ({"is_high_tech": True}, 150_000.0, 100_000.0),
        ({}, 250_000.0, 0.0),
    ]
    for flags, stored_tax, expected_reduction in cases:
        form = A200000FormData(
            enterprise=EnterpriseInfo(name="反推企业", tax_number="X", **flags),
            tax_period=TaxPeriod(year=2026, quarter=1),
            total_profit=1_000_000.0,
            stored_tax_amount=stored_tax,
        )
        results = form.calculate()
        assert results["row10_tax_payable"] == pytest.approx(250_000.0)
        assert results["row11_tax_reduction"] == pytest.approx(expected_reduction)
        assert results["row14_tax_to_pay_or_refund"] == pytest.approx(stored_tax, abs=1e-9)


@pytest.mark.parametrize("flags,revenue_wan", [
    ({}, 100.0),                                   # 一般企业 25%
    ({"employee_count": 20, "total_assets": 500.0}, 100.0),   # 小型微利 5%
    ({"is_high_tech": True}, 100.0),               # 高新技术 15%
])
def test_export_row14_matches_db_tax_amount(client, flags, revenue_wan):
    ent = _enterprise(client, f"91110103F3000{len(flags)}{int(revenue_wan)}", **flags)
    saved = _save(client, ent["id"], **{"total_revenue": revenue_wan})
    assert saved.status_code in (200, 201), saved.text
    body = saved.json()
    rows = _exported_rows(client, body["id"])
    assert rows[14] == pytest.approx(body["tax_amount"], abs=1e-9)
    assert rows[9] == "25%"


# --------------------------------------------------------------------------
# I-2 A 类预缴必须带季度；历史缺季度走更正路径；年度 guard 看 return_type
# --------------------------------------------------------------------------

def test_quarterly_create_requires_quarter(client):
    ent = _enterprise(client, "91110103F2000001")
    missing = client.post(f"{P}/tax-returns/", json={
        "enterprise_id": ent["id"], "tax_period": "2026", "report_type": "A类",
        "total_revenue": 100.0,
    })
    assert missing.status_code == 400, missing.text
    detail = missing.json()["detail"]
    assert "季度" in detail and "A类" in detail
    # 不得静默存成不可导出的成功状态
    assert client.get(f"{P}/tax-returns/", params={"enterprise_id": ent["id"]}).json() == []

    ok = _save(client, ent["id"])
    assert ok.status_code in (200, 201), ok.text


def test_quarterly_update_requires_quarter(client):
    ent = _enterprise(client, "91110103F2000002")
    rid = _save(client, ent["id"]).json()["id"]
    resp = client.put(f"{P}/tax-returns/{rid}", json={
        "enterprise_id": ent["id"], "tax_period": "2026", "quarter": None,
        "report_type": "A类", "total_revenue": 100.0,
    })
    assert resp.status_code == 400, resp.text
    assert "季度" in resp.json()["detail"]


def test_legacy_prepayment_without_quarter_gets_correction_path(client):
    """历史「预缴 + quarter=None」：导出 409 指向补季度更正，不被当成年度。"""
    ent = _enterprise(client, "91110103F2000003")
    rid = _insert_legacy(
        enterprise_id=ent["id"], year=2026, quarter=None, return_type="预缴",
        status="draft", taxable_income=1_000_000.0, tax_amount=250_000.0,
        operating_revenue=1_000_000.0, operating_cost=0.0, total_profit=1_000_000.0,
        tax_adjustments=0.0, tax_preferential=0.0, tax_type="一般企业",
    )
    blocked = client.get(f"{P}/tax-returns/{rid}/export/excel")
    assert blocked.status_code == 409, blocked.text
    detail = blocked.json()["detail"]
    assert "季度" in detail and "更正" in detail
    assert "A100000" not in detail  # 不是年度记录，不能推给年度申报表

    fixed = client.put(f"{P}/tax-returns/{rid}", json={
        "enterprise_id": ent["id"], "tax_period": "2026", "quarter": "Q2",
        "report_type": "A类", "total_revenue": 100.0,
    })
    assert fixed.status_code == 200, fixed.text
    assert fixed.json()["quarter"] == 2
    rows = _exported_rows(client, rid)
    assert rows[8] == pytest.approx(rows[3] - rows[4] - rows[5] - rows[6])


def test_annual_guard_checks_return_type_not_only_quarter():
    """return_type=年度 一律按 A100000 拒绝（即使 quarter 有值，也不导预缴表）。"""
    ent = Enterprise(id=1, name="年度企业", tax_number="X", employee_count=10, total_assets=10.0)
    ret = TaxReturn(
        id=1, enterprise_id=1, year=2026, quarter=2, return_type="年度",
        status="draft", taxable_income=100_000.0, tax_amount=25_000.0,
        operating_revenue=100_000.0, total_profit=100_000.0,
    )
    with pytest.raises(ValueError) as exc:
        tr._build_a200000_form_data(ret, ent)
    assert "A100000" in str(exc.value)


# --------------------------------------------------------------------------
# I-3 分位舍入：计算页 / 落库 / 导出同口径
# --------------------------------------------------------------------------

def test_cent_boundary_row14_equals_db_tax_amount(client):
    """10.70 元应税 × 25% = 2.675 → 2.68；导出第 14 行与库税额完全一致。"""
    ent = _enterprise(client, "91110103F4000001")
    saved = _save(client, ent["id"], **{"total_revenue": 0.00107})
    assert saved.status_code in (200, 201), saved.text
    body = saved.json()
    assert body["taxable_income"] == pytest.approx(10.7)
    assert body["tax_amount"] == pytest.approx(2.68)

    calc = client.post(f"{P}/tax-returns/calculate", json={
        "enterprise_id": ent["id"], "year": "2026",
        "revenue": 0.00107, "total_costs": 0.0, "total_expenses": 0.0,
        "employee_count": 500, "total_assets": 9000.0,
    })
    assert calc.status_code == 200, calc.text
    assert calc.json()["tax_amount_wan"] * 10000 == pytest.approx(2.68, abs=1e-9)
    assert calc.json()["taxable_income_after_loss_wan"] * 10000 == pytest.approx(10.7, abs=1e-9)

    rows = _exported_rows(client, body["id"])
    assert rows[8] == pytest.approx(10.7)
    assert rows[10] == pytest.approx(2.68)
    assert rows[14] == pytest.approx(2.68, abs=1e-9)
    assert rows[14] == pytest.approx(body["tax_amount"], abs=1e-9)


def test_calculate_tax_amount_keeps_yuan_precision():
    """引擎税额按元取整（旧实现 round(万元, 2) = 100 元粒度，会丢到分以外）。"""
    from app.modules.finance_tax.services.corporate_income_tax_calculator import (
        CorporateIncomeTaxCalculator,
        EnterpriseTaxType,
    )

    result = CorporateIncomeTaxCalculator.calculate_tax_amount(
        taxable_income_wan=234.56789,
        enterprise_tax_type=EnterpriseTaxType.STANDARD,
    )
    # 2,345,678.90 元 × 25% = 586,419.725 → 586,419.73（旧实现会落到 586,400.00）
    assert result["tax_amount_wan"] * 10000 == pytest.approx(586_419.73, abs=1e-9)


# --------------------------------------------------------------------------
# I-4 历史记录：缺利润表 409 / 无法表达的正向调整 409 / 旧 tax_type 不改税额
# --------------------------------------------------------------------------

def test_legacy_return_without_profit_data_is_rejected(client):
    ent = _enterprise(client, "91110103F5000001")
    rid = _insert_legacy(
        enterprise_id=ent["id"], year=2026, quarter=1, return_type="预缴",
        status="draft", taxable_income=1_000_000.0, tax_amount=250_000.0,
    )
    resp = client.get(f"{P}/tax-returns/{rid}/export/excel")
    assert resp.status_code == 409, resp.text
    detail = resp.json()["detail"]
    assert "利润" in detail and "补录" in detail


def test_legacy_return_with_positive_adjustment_cannot_be_exported(client):
    """利润 200 万但应税 250 万（原含纳税调整）：A200000 无调整行次 → 409 指向更正。"""
    ent = _enterprise(client, "91110103F5000002")
    rid = _insert_legacy(
        enterprise_id=ent["id"], year=2026, quarter=1, return_type="预缴",
        status="draft", taxable_income=2_500_000.0, tax_amount=625_000.0,
        operating_revenue=5_000_000.0, operating_cost=3_000_000.0,
        total_profit=2_000_000.0, tax_adjustments=500_000.0, tax_preferential=0.0,
        tax_type="一般企业",
    )
    resp = client.get(f"{P}/tax-returns/{rid}/export/excel")
    assert resp.status_code == 409, resp.text
    detail = resp.json()["detail"]
    assert "纳税调整" in detail and "更正" in detail


def test_legacy_tax_type_does_not_change_tax_amount(client):
    """旧 tax_type 兼容：导出按落库税额反推减免，不改税额（企业后改高新也不重算）。"""
    ent = _enterprise(client, "91110103F5000003", is_high_tech=True)
    rid = _insert_legacy(
        enterprise_id=ent["id"], year=2026, quarter=1, return_type="预缴",
        status="draft", taxable_income=1_000_000.0, tax_amount=250_000.0,
        operating_revenue=1_000_000.0, total_profit=1_000_000.0,
        tax_adjustments=0.0, tax_preferential=0.0, tax_type="一般企业",
    )
    rows = _exported_rows(client, rid)
    assert rows[11] == pytest.approx(0.0)
    assert rows[14] == pytest.approx(250_000.0, abs=1e-9)


def test_export_rejects_tax_amount_above_legal_rate(client):
    """落库税额 > 第 8 行 ×25% 时表内无法表达减免额（为负）→ 409。"""
    ent = _enterprise(client, "91110103F5000004")
    rid = _insert_legacy(
        enterprise_id=ent["id"], year=2026, quarter=1, return_type="预缴",
        status="draft", taxable_income=1_000_000.0, tax_amount=300_000.0,
        operating_revenue=1_000_000.0, total_profit=1_000_000.0,
        tax_adjustments=0.0, tax_preferential=0.0, tax_type="一般企业",
    )
    resp = client.get(f"{P}/tax-returns/{rid}/export/excel")
    assert resp.status_code == 409, resp.text
    assert "税额" in resp.json()["detail"]


def test_export_rejects_zero_tax_with_positive_income(client):
    """历史舍入缺陷（应税为正、税额 0）不得导出成 100% 减免 → 409 指向更正。"""
    ent = _enterprise(client, "91110103F5000005")
    rid = _insert_legacy(
        enterprise_id=ent["id"], year=2026, quarter=1, return_type="预缴",
        status="draft", taxable_income=10.7, tax_amount=0.0,
        operating_revenue=10.7, total_profit=10.7,
        tax_adjustments=0.0, tax_preferential=0.0, tax_type="一般企业",
    )
    resp = client.get(f"{P}/tax-returns/{rid}/export/excel")
    assert resp.status_code == 409, resp.text
    detail = resp.json()["detail"]
    assert "税额为 0" in detail and "更正" in detail


def test_row11_calc_method_states_implied_percentage(client):
    """第 11 行说明按落库税额反推的百分点（不按企业当前属性猜税率）。"""
    ent = _enterprise(client, "91110103F5000006", employee_count=20, total_assets=500.0)
    saved = _save(client, ent["id"], **{"total_revenue": 100.0})
    assert saved.status_code in (200, 201), saved.text
    body = saved.json()
    assert body["tax_type"] == "小型微利企业"
    rows = _exported_rows(client, body["id"])
    assert rows[11] == pytest.approx(200_000.0)
    assert "20.00 个百分点" in str(rows["calc11"])


# --------------------------------------------------------------------------
# M-1 计算接口以企业档案高新为准
# --------------------------------------------------------------------------

def test_calculate_uses_enterprise_archive_for_high_tech(client):
    plain = _enterprise(client, "91110103F6000001")
    spoofed = client.post(f"{P}/tax-returns/calculate", json={
        "enterprise_id": plain["id"], "year": "2026",
        "revenue": 100.0, "total_costs": 0.0, "total_expenses": 0.0,
        "employee_count": 500, "total_assets": 9000.0, "is_high_tech": True,
    })
    assert spoofed.status_code == 200, spoofed.text
    assert spoofed.json()["applicable_tax_rate"] == pytest.approx(0.25)
    assert spoofed.json()["tax_amount_wan"] == pytest.approx(25.0)

    hitech = _enterprise(client, "91110103F6000002", is_high_tech=True)
    ignored = client.post(f"{P}/tax-returns/calculate", json={
        "enterprise_id": hitech["id"], "year": "2026",
        "revenue": 100.0, "total_costs": 0.0, "total_expenses": 0.0,
        "employee_count": 500, "total_assets": 9000.0, "is_high_tech": False,
    })
    assert ignored.status_code == 200, ignored.text
    assert ignored.json()["applicable_tax_rate"] == pytest.approx(0.15)


# --------------------------------------------------------------------------
# M-3 企业更新显式 null 不再 500
# --------------------------------------------------------------------------

def test_enterprise_update_explicit_null_does_not_500(client):
    ent = _enterprise(client, "91110103F7000001", is_high_tech=True)
    resp = client.put(f"{P}/enterprises/{ent['id']}", json={"is_high_tech": None})
    assert resp.status_code == 200, resp.text
    assert resp.json()["is_high_tech"] is False

    resp2 = client.put(f"{P}/enterprises/{ent['id']}", json={"is_restricted_industry": None})
    assert resp2.status_code == 200, resp2.text
    assert resp2.json()["is_restricted_industry"] is False


def test_enterprise_list_tolerates_legacy_null_flags(client):
    ent = _enterprise(client, "91110103F7000002")
    with SessionLocal() as s:
        row = s.get(Enterprise, ent["id"])
        row.is_high_tech = None
        row.is_restricted_industry = None
        s.commit()
    listed = client.get(f"{P}/enterprises/", params={"limit": 200})
    assert listed.status_code == 200, listed.text
    target = next(e for e in listed.json() if e["id"] == ent["id"])
    assert target["is_high_tech"] is False
    assert target["is_restricted_industry"] is False


# --------------------------------------------------------------------------
# M-4 更正精度：元→万元→元 保留分位
# --------------------------------------------------------------------------

def test_correction_round_trip_preserves_cents(client):
    ent = _enterprise(client, "91110103F8000001")
    created = _save(client, ent["id"], **{"total_revenue": 0.123456})
    assert created.status_code in (200, 201), created.text
    body = created.json()
    assert body["operating_revenue"] == pytest.approx(1234.56)

    updated = client.put(f"{P}/tax-returns/{body['id']}", json={
        "enterprise_id": ent["id"], "tax_period": "2026", "quarter": "Q1",
        "report_type": "A类", "total_revenue": 0.123456,
    })
    assert updated.status_code == 200, updated.text
    assert updated.json()["operating_revenue"] == pytest.approx(1234.56)


# --------------------------------------------------------------------------
# M-5 先 commit 再清产物；产物清理只认导出目录内文件名
# --------------------------------------------------------------------------

def test_unlink_export_artifact_ignores_external_paths(tmp_path):
    from app.utils_filing import unlink_export_artifact

    exports = tmp_path / "exports"
    exports.mkdir()
    outside = tmp_path / "outside.xlsx"
    outside.write_bytes(b"keep")

    assert unlink_export_artifact(str(outside), exports) is False
    assert unlink_export_artifact("../outside.xlsx", exports) is False
    assert unlink_export_artifact("..", exports) is False
    assert unlink_export_artifact("", exports) is False
    assert unlink_export_artifact(None, exports) is False
    assert outside.exists()

    # 目录内同名文件才会被删除（即便传入的是外部绝对路径）
    inside = exports / "outside.xlsx"
    inside.write_bytes(b"drop")
    assert unlink_export_artifact("C:\\evil\\outside.xlsx", exports) is True
    assert not inside.exists()
    assert outside.exists()


def test_filing_delete_unlinks_only_after_commit(client, monkeypatch):
    from app.modules.finance_stamp_duty import router as stamp_router
    from app.modules.finance_stamp_duty.models import StampDutyFiling

    created = client.post("/api/finance/stamp-duty/filings", json={
        "period": "2026-09", "items": [{"tax_item": "买卖合同", "amount": 1_000_000}],
    })
    assert created.status_code == 201, created.text
    fid = created.json()["id"]
    exported = client.post(f"/api/finance/stamp-duty/filings/{fid}/export")
    assert exported.status_code == 200, exported.text
    filename = exported.json()["file"]
    path = pathlib.Path(__import__("os").environ["FINANCE_DATA_DIR"]).rglob(filename)
    artifact = next(iter(path), None)
    assert artifact is not None and artifact.is_file()

    seen = {}

    def spy(export_path, exports_dir):
        with SessionLocal() as s:
            seen["row_exists"] = s.get(StampDutyFiling, fid) is not None
        from app.utils_filing import unlink_export_artifact

        return unlink_export_artifact(export_path, exports_dir)

    monkeypatch.setattr(stamp_router, "unlink_export_artifact", spy)
    deleted = client.delete(f"/api/finance/stamp-duty/filings/{fid}")
    assert deleted.status_code == 200, deleted.text
    assert seen["row_exists"] is False, "先删产物后 commit：产物已清而库行未落定"
    assert not artifact.exists()
