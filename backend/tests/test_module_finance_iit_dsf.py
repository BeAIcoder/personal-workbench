# -*- coding: utf-8 -*-
"""finance_iit 与 finance_dsf 模块测试：累计预扣法 + 残保金分档 + API。"""
from __future__ import annotations

import pytest

from app.modules.finance_iit import service as iit
from app.modules.finance_dsf import service as dsf

IIT_P = "/api/finance/iit"
DSF_P = "/api/finance/dsf"


# ---------- 个税累计预扣法 ----------

def test_iit_single_month_low_income():
    # 月薪 8000，社保 1500，专项附加 2000 → 当月应纳税所得额 8000-5000-1500-2000=-500 → 0
    calc = iit.calc_filing("2026", [{"month": 1, "name": "员工甲", "salary": 8000,
                                     "social_insurance": 1500, "special_additional": 2000}])
    emp = calc["employees"][0]
    assert emp["months"][0]["monthly_withholding"] == 0.0


def test_iit_cumulative_bracket_jump():
    # 月薪 36000 × 12 月，无扣除：累计 432000 → 跨档（20%→25%）
    rows = [{"month": m, "name": "员工乙", "salary": 36000} for m in range(1, 13)]
    calc = iit.calc_filing("2026", rows)
    emp = calc["employees"][0]
    # 1月：(36000-5000)×3% = 930
    assert emp["months"][0]["monthly_withholding"] == 930.0
    # 12月累计 432000 → 25% 档
    assert emp["months"][11]["rate"] == 0.25


def test_iit_multiple_employees_and_totals():
    rows = [
        {"month": 1, "name": "甲", "salary": 10000, "special_additional": 1000},
        {"month": 1, "name": "乙", "salary": 20000},
        {"month": 2, "name": "甲", "salary": 10000, "special_additional": 1000},
    ]
    calc = iit.calc_filing("2026", rows)
    names = {e["name"] for e in calc["employees"]}
    assert names == {"甲", "乙"}
    assert calc["totals"]["tax_withheld"] > 0


def test_iit_validation():
    with pytest.raises(ValueError):
        iit.calc_filing("2026", [])
    with pytest.raises(ValueError):
        iit.calc_filing("bad", [{"month": 1, "name": "甲", "salary": 1}])
    with pytest.raises(ValueError):
        iit.calc_filing("2026", [{"month": 13, "name": "甲", "salary": 1}])


def test_iit_standards_table():
    assert iit.SPECIAL_DEDUCTION_STANDARDS["children_education"]["monthly_per_child"] == 2000
    assert iit.SPECIAL_DEDUCTION_STANDARDS["elderly"]["monthly_max"] == 3000


# ---------- 残保金 ----------

def test_dsf_exempt_30_or_below():
    calc = dsf.calc_dsf("2026", 28, 2_000_000, 0)
    assert calc["exempt"] is True and calc["payable"] == 0.0


def test_dsf_tiered_90pct_reduction():
    # 100 人，比例 1.5% → 差额 1.5 人 - 0 安排 → <1% → 减收 90%
    calc = dsf.calc_dsf("2026", 100, 10_000_000, 0)
    assert calc["tier"] == "减收90%"
    # base = 1.5 × 100000 = 150000 → payable = 15000
    assert calc["payable"] == 15000.0


def test_dsf_tiered_50pct_reduction():
    # 100 人安排 1 人（1%）→ 减收 50%
    calc = dsf.calc_dsf("2026", 100, 10_000_000, 1)
    assert calc["tier"] == "减收50%"
    # base = (1.5-1) × 100000 = 50000 → payable = 25000
    assert calc["payable"] == 25000.0


def test_dsf_full_compliance():
    calc = dsf.calc_dsf("2026", 200, 20_000_000, 3)
    assert calc["tier"] == "达标" and calc["payable"] == 0.0


def test_dsf_expired_year_rejected():
    with pytest.raises(ValueError) as exc:
        dsf.calc_dsf("2028", 100, 10_000_000, 0)
    assert "2027-12-31" in str(exc.value)


# ---------- API ----------

def test_api_iit_lifecycle(client):
    r = client.post(f"{IIT_P}/filings", json={
        "year": "2026", "taxpayer_name": "脱敏测试企业",
        "rows": [{"month": 1, "name": "员工甲", "salary": 9000,
                  "social_insurance": 1600, "special_additional": 2000}]})
    assert r.status_code == 201, r.text
    fid = r.json()["id"]
    assert r.json()["calc_result"]["totals"]["tax_withheld"] >= 0

    exp = client.post(f"{IIT_P}/filings/{fid}/export")
    assert exp.status_code == 200
    dl = client.get(f"{IIT_P}/filings/{fid}/download")
    assert dl.status_code == 200 and len(dl.content) > 1000
    assert client.delete(f"{IIT_P}/filings/{fid}").status_code == 200


def test_api_dsf_lifecycle(client):
    r = client.post(f"{DSF_P}/filings", json={
        "year": "2026", "total_employees": 100, "total_salary": 10_000_000,
        "disabled_employees": 0})
    assert r.status_code == 201
    fid = r.json()["id"]
    assert r.json()["calc_result"]["payable"] == 15000.0

    exp = client.post(f"{DSF_P}/filings/{fid}/export")
    assert exp.status_code == 200
    dl = client.get(f"{DSF_P}/filings/{fid}/download")
    assert dl.status_code == 200 and len(dl.content) > 1000
    assert client.delete(f"{DSF_P}/filings/{fid}").status_code == 200
