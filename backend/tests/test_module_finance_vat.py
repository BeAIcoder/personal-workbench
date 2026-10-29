# -*- coding: utf-8 -*-
"""finance_vat 模块测试：双模式计税 + 政策有效期 + API。"""
from __future__ import annotations

import pytest

from app.modules.finance_vat import service as svc

P = "/api/finance/vat"


def test_general_mode():
    calc = svc.calc_general("2026-09", [
        {"business_type": "销售货物", "amount": 1_000_000, "rate": 0.13},
        {"business_type": "现代服务", "amount": 500_000, "rate": 0.06},
    ], input_vat=80_000)
    # 销项：13万 + 3万 = 16万；进项 8 万 → 应纳税 8 万
    assert calc["output_vat"] == 160_000.0
    assert calc["payable"] == 80_000.0


def test_general_negative_payable_clamped():
    calc = svc.calc_general("2026-09", [
        {"business_type": "销售货物", "amount": 100_000, "rate": 0.13},
    ], input_vat=50_000)
    # 销项 1.3 万 < 进项 5 万 → 应纳税额 0（留抵不退，底稿提示）
    assert calc["payable"] == 0.0


def test_general_rate_whitelist():
    with pytest.raises(ValueError):
        svc.calc_general("2026-09", [{"amount": 100, "rate": 0.17}], 0)


def test_small_scale_exempt():
    calc = svc.calc_small_scale("2026-09", sales_amount=299_999)
    assert calc["exempt"] is True
    assert calc["payable"] == 0.0


def test_small_scale_1pct():
    calc = svc.calc_small_scale("2026-09", sales_amount=400_000)
    assert calc["exempt"] is False
    assert calc["payable"] == 4_000.0
    assert "19号" in calc["policy"]["policy_basis"]


def test_small_scale_expired_rejected():
    with pytest.raises(ValueError) as exc:
        svc.calc_small_scale("2028-01", sales_amount=400_000)
    assert "2027-12-31" in str(exc.value)


# ---------- API ----------

def test_api_general_lifecycle(client):
    r = client.post(f"{P}/filings", json={
        "period": "2026-09", "taxpayer_type": "general",
        "output_items": [{"business_type": "销售货物", "amount": 1_000_000, "rate": 0.13}],
        "input_vat": 80_000})
    assert r.status_code == 201, r.text
    fid = r.json()["id"]
    assert r.json()["calc_result"]["payable"] == 50_000.0

    exp = client.post(f"{P}/filings/{fid}/export")
    assert exp.status_code == 200
    dl = client.get(f"{P}/filings/{fid}/download")
    assert dl.status_code == 200 and len(dl.content) > 1000
    assert client.delete(f"{P}/filings/{fid}").status_code == 200


def test_api_small_scale_exempt(client):
    r = client.post(f"{P}/filings", json={
        "period": "2026-09", "taxpayer_type": "small_scale",
        "sales_amount": 250_000})
    assert r.status_code == 201
    assert r.json()["calc_result"]["exempt"] is True


def test_api_validation(client):
    r = client.post(f"{P}/filings", json={
        "period": "2026-09", "taxpayer_type": "general",
        "output_items": [{"amount": 100, "rate": 0.17}]})
    assert r.status_code == 400
