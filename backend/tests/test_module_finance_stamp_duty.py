# -*- coding: utf-8 -*-
"""finance_stamp_duty 模块测试：计税三态 + API 生命周期。"""
from __future__ import annotations

import pytest

from app.modules.finance_stamp_duty import service as svc

P = "/api/finance/stamp-duty"


def test_tax_items_table():
    items = svc.list_tax_items()
    names = [i["tax_item"] for i in items]
    assert "买卖合同（动产买卖）" in names
    assert "借款合同" in names
    by_name = {i["tax_item"]: i["rate"] for i in items}
    assert by_name["买卖合同（动产买卖）"] == 0.3
    assert by_name["营业账簿（实收资本+资本公积）"] == 0.25


def test_calc_normal():
    calc = svc.calc_filing("2026-09", [
        {"tax_item": "买卖合同（动产买卖）", "amount": 1_000_000},
        {"tax_item": "借款合同", "amount": 2_000_000},
    ], half_discount=False)
    # 100万×0.3‰=300 + 200万×0.05‰=100
    assert calc["total_tax"] == 400.0
    assert calc["half_saved"] == 0.0
    assert calc["policy"]["status"] == "not_applied"


def test_calc_half_discount_applies():
    calc = svc.calc_filing("2026-09", [
        {"tax_item": "买卖合同（动产买卖）", "amount": 1_000_000},
    ], half_discount=True)
    assert calc["raw_total"] == 300.0
    assert calc["total_tax"] == 150.0
    assert calc["half_saved"] == 150.0
    assert calc["policy"]["status"] == "valid"
    assert "12号" in calc["policy"]["policy_basis"]


def test_calc_exempt_item():
    calc = svc.calc_filing("2026-09", [
        {"tax_item": "买卖合同（动产买卖）", "amount": 1_000_000, "exempt": True},
    ], half_discount=False)
    assert calc["total_tax"] == 0.0


def test_calc_unknown_tax_item_rejected():
    with pytest.raises(ValueError):
        svc.calc_filing("2026-09", [{"tax_item": "不存在的税目", "amount": 100}], False)


def test_calc_half_discount_expired_rejected():
    # 2028 年六税两费减半已到期：拒绝按减半计税
    with pytest.raises(ValueError) as exc:
        svc.calc_filing("2028-01", [{"tax_item": "买卖合同（动产买卖）", "amount": 100}], True)
    assert "2027-12-31" in str(exc.value)


def test_calc_empty_items_rejected():
    with pytest.raises(ValueError):
        svc.calc_filing("2026-09", [], False)


# ---------- API 层 ----------

def test_api_lifecycle(client):
    r = client.get(f"{P}/tax-items")
    assert r.status_code == 200 and len(r.json()) >= 13

    r = client.post(f"{P}/filings", json={
        "period": "2026-09", "taxpayer_name": "脱敏测试企业",
        "half_discount": True,
        "items": [{"tax_item": "买卖合同（动产买卖）", "amount": 1_000_000}]})
    assert r.status_code == 201, r.text
    body = r.json()
    fid = body["id"]
    assert body["calc_result"]["total_tax"] == 150.0
    assert body["status"] == "calculated"

    lst = client.get(f"{P}/filings")
    assert lst.status_code == 200 and lst.json()["total"] >= 1

    exp = client.post(f"{P}/filings/{fid}/export")
    assert exp.status_code == 200 and exp.json()["ok"] is True

    dl = client.get(f"{P}/filings/{fid}/download")
    assert dl.status_code == 200 and len(dl.content) > 1000

    assert client.delete(f"{P}/filings/{fid}").status_code == 200


def test_api_validation_errors(client):
    r = client.post(f"{P}/filings", json={
        "period": "2026-09", "items": [{"tax_item": "不存在", "amount": 1}]})
    assert r.status_code == 400 and "税目" in r.json()["detail"]

    r = client.post(f"{P}/filings", json={
        "period": "bad-period", "items": [{"tax_item": "买卖合同（动产买卖）", "amount": 1}]})
    assert r.status_code == 422
