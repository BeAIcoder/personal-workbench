# -*- coding: utf-8 -*-
"""业务不变量回归（2026-10-31 第 2 批）。

覆盖：
- 收款并发不丢单（条件更新；终态实收恒等于成功收款之和）
- 删合同不得硬删已收款账单（409，账单仍在）
- HR 不给入职前月份出薪（与 finance_iit 的拒绝口径对齐）

全部使用合成数据；client 由 conftest 独立库提供，不写真实 backend/data。
"""
import threading

import pytest
from sqlalchemy.exc import OperationalError

from app.database import SessionLocal
from app.modules.business_operations import service as ops

P = "/api/business/operations"


def _contract(client, no="HT-INV-001", **over):
    body = {
        "contract_no": no,
        "tenant": "合成商户甲",
        "unit_no": "A-101",
        "area": 100.0,
        "start_date": "2026-01-01",
        "end_date": "2026-12-31",
        "rent_monthly": 10000.0,
        "property_fee_monthly": 0.0,
        "deposit": 20000.0,
    }
    body.update(over)
    resp = client.post(f"{P}/contracts", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _bill(client, contract_id, period="2026-01"):
    resp = client.post(f"{P}/contracts/{contract_id}/bills", json={"period": period})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _bill_of(client, bill_id):
    rows = client.get(f"{P}/bills", params={"page_size": 200}).json()["items"]
    return next(b for b in rows if b["id"] == bill_id)


def test_pay_bill_never_loses_a_payment_under_concurrency(client):
    """两条并发收款：终态实收必须等于成功收款之和（不静默丢一笔）。"""
    cid = _contract(client)
    bid = _bill(client, cid)
    results = []

    def worker(amount):
        with SessionLocal() as session:
            try:
                ops.pay_bill(session, bid, amount)
                results.append(amount)
            except (ValueError, OperationalError):
                pass

    threads = [threading.Thread(target=worker, args=(100,))]
    threads.append(threading.Thread(target=worker, args=(200,)))
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    total = _bill_of(client, bid)["paid_amount"]
    assert total == sum(results) > 0


def test_contract_delete_with_paid_bills_rejected_409(client):
    """已收款账单是收款凭据：删合同必须 409，账单与实收仍在。"""
    cid = _contract(client, no="HT-INV-003")
    bid = _bill(client, cid, "2026-03")
    resp = client.post(f"{P}/bills/{bid}/payments", json={"amount": 300})
    assert resp.status_code == 200, resp.text

    denied = client.delete(f"{P}/contracts/{cid}")
    assert denied.status_code == 409, denied.text
    assert "已收款账单" in denied.json()["detail"]
    assert _bill_of(client, bid)["paid_amount"] == pytest.approx(300.0)

    # 无已收款账单的合同可正常删除
    cid2 = _contract(client, no="HT-INV-004")
    client.post(f"{P}/contracts/{cid2}/bills", json={"period": "2026-03"})
    ok = client.delete(f"{P}/contracts/{cid2}")
    assert ok.status_code == 200, ok.text


def test_payroll_rejects_hire_month_after_payroll_month(client):
    """入职月份晚于出薪月份：拒绝出薪，不再 max() 兜底成「已入职 1 个月」。"""
    resp = client.post(f"/api/business/hr/employees", json={
        "emp_no": "E-INV-1", "name": "未来入职员工", "hire_date": "2026-12-01",
        "status": "active", "base_salary": 5000.0,
    })
    assert resp.status_code == 201, resp.text
    generated = client.post("/api/business/hr/payroll/generate", json={
        "month": "2026-01", "social_rate": 0, "housing_rate": 0,
    })
    assert generated.status_code == 409, generated.text
    assert "入职月份" in generated.json()["detail"]

