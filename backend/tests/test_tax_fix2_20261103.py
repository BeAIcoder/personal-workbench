# -*- coding: utf-8 -*-
"""Task 3 Fix round 2 回归：调整行次口径、预缴/年度双口径、历史更正闭环。

对应复审 N-1～N-4：

- N-1 任何纳税调整（正向或负向）都不得伪装成 A200000 第 5 行「所得减免」：
  只要 `taxable_income != total_profit − tax_preferential` 且不是合法的所得减免，
  导出即 409 指向更正；第 5 行只允许来自 `tax_preferential`。
- N-2 计算页拆出「季度预缴税额（不含调整，与保存/导出一致）」：`/calculate` 新增
  `prepay_*` 字段，有调整时预缴数必须等于保存记录的税额，旧字段保持兼容。
- N-3 历史正向调整记录：导出 409 → PUT 更正（按预缴口径重算）→ 导出 200 的端到端链路。
- N-4 残保金底稿用户文本走 `safe_cell`（报告曾声称已做，本轮补齐并锁定）。

全部合成数据；client 由 conftest 提供（临时 SQLite + 临时目录）。
"""
from __future__ import annotations

import io

import pytest

from app.database import SessionLocal
from app.modules.finance_tax.models import TaxReturn

P = "/api/finance/tax"


def _enterprise(client, tax_number, **kw):
    payload = {
        "name": "Fix2 测试企业",
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


def _rows(client, return_id):
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
            rows[key] = row[2] if key == 9 else float(str(row[2]).replace(",", ""))
            rows[f"calc{key}"] = row[3] if len(row) > 3 else ""
    return rows


def _status(resp, expected: int) -> None:
    """断言状态码；失败时只报状态与 content-type，避免把 xlsx 二进制打进日志。"""
    assert resp.status_code == expected, (
        f"status={resp.status_code} content-type={resp.headers.get('content-type')}"
    )


def _insert_legacy(**fields):
    with SessionLocal() as s:
        ret = TaxReturn(**fields)
        s.add(ret)
        s.commit()
        s.refresh(ret)
        return ret.id


# --------------------------------------------------------------------------
# N-1 调整不得伪装第 5 行：正负向一律 409
# --------------------------------------------------------------------------

def test_legacy_negative_adjustment_is_rejected(client):
    """历史调减（应税 150 万 < 利润 200 万）：不得塞进第 5 行所得减免 → 409。"""
    ent = _enterprise(client, "91110103G1000001")
    rid = _insert_legacy(
        enterprise_id=ent["id"], year=2026, quarter=1, return_type="预缴",
        status="draft", taxable_income=1_500_000.0, tax_amount=375_000.0,
        operating_revenue=5_000_000.0, operating_cost=3_000_000.0,
        total_profit=2_000_000.0, tax_adjustments=-500_000.0, tax_preferential=0.0,
        tax_type="一般企业",
    )
    resp = client.get(f"{P}/tax-returns/{rid}/export/excel")
    _status(resp, 409)
    detail = resp.json()["detail"]
    assert "纳税调整" in detail and "更正" in detail
    # 不得把差额说成「所得减免/桥接」
    assert "桥接" not in detail


def test_row5_only_comes_from_tax_preferential(client):
    """第 5 行只来自 tax_preferential：纳税调整既不进第 5 行，也不进第 8 行。"""
    ent = _enterprise(client, "91110103G1000002")
    saved = _save(client, ent["id"], **{
        "total_revenue": 500.0, "total_costs": 200.0, "total_expenses": 100.0,
        "tax_adjustments": 50.0,
    })
    assert saved.status_code in (200, 201), saved.text
    body = saved.json()
    rows = _rows(client, body["id"])
    assert rows[5] == pytest.approx(0.0)          # 调整不伪装所得减免
    assert rows[8] == pytest.approx(2_000_000.0)  # 第 8 行 = 行3
    assert rows[8] == pytest.approx(rows[3] - rows[4] - rows[5] - rows[6])
    assert "纳税调整" in str(rows["calc8"])


def test_tax_preferential_is_the_legal_row5_source(client):
    """所得减免（第 5 行）是合法口径：落库 tax_preferential → 第 5 行同额、第 8 行自洽。"""
    ent = _enterprise(client, "91110103G1000003")
    saved = _save(client, ent["id"], **{
        "total_revenue": 500.0, "total_costs": 200.0, "total_expenses": 100.0,
        "tax_preferential": 10.0,
    })
    assert saved.status_code in (200, 201), saved.text
    body = saved.json()
    assert body["taxable_income"] == pytest.approx(1_900_000.0)
    rows = _rows(client, body["id"])
    assert rows[5] == pytest.approx(100_000.0)
    assert rows[8] == pytest.approx(1_900_000.0)
    assert rows[14] == pytest.approx(body["tax_amount"], abs=1e-9)


# --------------------------------------------------------------------------
# N-3 历史正向调整：409 → PUT 更正 → 导出 200
# --------------------------------------------------------------------------

def test_positive_adjustment_409_then_correction_then_export(client):
    ent = _enterprise(client, "91110103G3000001")
    rid = _insert_legacy(
        enterprise_id=ent["id"], year=2026, quarter=1, return_type="预缴",
        status="draft", taxable_income=2_500_000.0, tax_amount=625_000.0,
        operating_revenue=5_000_000.0, operating_cost=3_000_000.0,
        total_profit=2_000_000.0, tax_adjustments=500_000.0, tax_preferential=0.0,
        tax_type="一般企业",
    )
    blocked = client.get(f"{P}/tax-returns/{rid}/export/excel")
    _status(blocked, 409)
    assert "纳税调整" in blocked.json()["detail"]

    fixed = client.put(f"{P}/tax-returns/{rid}", json={
        "enterprise_id": ent["id"], "tax_period": "2026", "quarter": "Q1",
        "report_type": "A类",
        "total_revenue": 500.0, "total_costs": 300.0, "total_expenses": 0.0,
        "tax_adjustments": 0.0, "tax_preferential": 0.0,
    })
    assert fixed.status_code == 200, fixed.text
    body = fixed.json()
    assert body["taxable_income"] == pytest.approx(2_000_000.0)
    assert body["tax_amount"] == pytest.approx(500_000.0)

    rows = _rows(client, rid)
    assert rows[3] == pytest.approx(2_000_000.0)
    assert rows[5] == pytest.approx(0.0)
    assert rows[8] == pytest.approx(2_000_000.0)
    assert rows[14] == pytest.approx(body["tax_amount"], abs=1e-9)


# --------------------------------------------------------------------------
# N-2 计算页双口径：季度预缴（不含调整）== 保存税额
# --------------------------------------------------------------------------

def test_calculate_prepay_fields_match_saved_tax_with_adjustments(client):
    """有纳税调整时：页面预缴数 == 保存记录的应纳税所得额与税额；年度测算仍含调整。"""
    ent = _enterprise(client, "91110103G2000001")
    calc = client.post(f"{P}/tax-returns/calculate", json={
        "enterprise_id": ent["id"], "year": "2026",
        "revenue": 500.0, "total_costs": 200.0, "total_expenses": 100.0,
        "business_entertainment": 10.0,
        "employee_count": 500, "total_assets": 9000.0,
    })
    assert calc.status_code == 200, calc.text
    d = calc.json()
    # 年度测算：调增 7.5 万 → 应纳税所得额 207.5 万、税额 51.875 万
    assert d["net_adjustment_wan"] == pytest.approx(7.5)
    assert d["taxable_income_after_loss_wan"] == pytest.approx(207.5)
    assert d["tax_amount_wan"] == pytest.approx(51.875)
    # 季度预缴口径：不含调整 → 200 万 / 50 万
    assert d["prepay_taxable_income_wan"] == pytest.approx(200.0)
    assert d["prepay_tax_amount_wan"] == pytest.approx(50.0)
    assert "不含" in d["prepay_note"] and "年度" in d["prepay_note"]

    saved = _save(client, ent["id"], **{
        "total_revenue": 500.0, "total_costs": 200.0, "total_expenses": 100.0,
        "tax_adjustments": 7.5,
    })
    assert saved.status_code in (200, 201), saved.text
    body = saved.json()
    assert d["prepay_taxable_income_wan"] * 10000 == pytest.approx(body["taxable_income"], abs=1e-6)
    assert d["prepay_tax_amount_wan"] * 10000 == pytest.approx(body["tax_amount"], abs=1e-6)


def test_calculate_prepay_fields_equal_annual_without_adjustments(client):
    """无调整时两个口径应当一致（避免页面出现两个不同数字）。"""
    ent = _enterprise(client, "91110103G2000002")
    calc = client.post(f"{P}/tax-returns/calculate", json={
        "enterprise_id": ent["id"], "year": "2026",
        "revenue": 500.0, "total_costs": 200.0, "total_expenses": 100.0,
        "employee_count": 500, "total_assets": 9000.0,
    })
    assert calc.status_code == 200, calc.text
    d = calc.json()
    assert d["prepay_taxable_income_wan"] == pytest.approx(d["taxable_income_after_loss_wan"])
    assert d["prepay_tax_amount_wan"] == pytest.approx(d["tax_amount_wan"])


def test_calculate_both_qualify_requires_choice_not_500(client):
    """档案同时符合高新+小微且请求体 what-if 不算小微时：预缴口径仍要求显式选择（400 非 500）。"""
    ent = _enterprise(client, "91110103G2000003", employee_count=20, total_assets=500.0,
                      is_high_tech=True)
    payload = {
        "enterprise_id": ent["id"], "year": "2026", "revenue": 100.0,
        "employee_count": 500, "total_assets": 9000.0,
    }
    blocked = client.post(f"{P}/tax-returns/calculate", json=payload)
    assert blocked.status_code == 400, blocked.text
    assert "不得叠加" in blocked.json()["detail"]

    chosen = client.post(f"{P}/tax-returns/calculate", json={**payload, "preferential_choice": "high_tech"})
    assert chosen.status_code == 200, chosen.text
    d = chosen.json()
    # 预缴口径按企业档案（20 人/500 万资产）判定，与请求体 what-if 值无关
    assert d["prepay_tax_amount_wan"] == pytest.approx(15.0)
    assert d["prepay_tax_type"] == "高新技术企业"


# --------------------------------------------------------------------------
# N-4 残保金底稿用户文本 safe_cell
# --------------------------------------------------------------------------

def test_dsf_export_escapes_user_text(client):
    created = client.post("/api/finance/dsf/filings", json={
        "year": "2026", "taxpayer_name": "=1+1", "total_employees": 20,
        "total_salary": 2_000_000, "disabled_employees": 0,
    })
    assert created.status_code == 201, created.text
    fid = created.json()["id"]
    exported = client.post(f"/api/finance/dsf/filings/{fid}/export")
    assert exported.status_code == 200, exported.text

    import openpyxl
    import pathlib
    import os

    path = next(pathlib.Path(os.environ["FINANCE_DATA_DIR"]).rglob("dsf_2026_*.xlsx"))
    ws = openpyxl.load_workbook(path).active
    values = [str(c.value) for row in ws.iter_rows() for c in row if c.value is not None]
    assert any("'=1+1" in v for v in values)
    assert not any(v.startswith("=") for v in values)
