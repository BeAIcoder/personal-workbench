"""finance_tax 模块测试。

后端由「企业所得税计算」项目整体迁入（引擎零改动，974 个原测试在原项目继续跑）。
本文件只验证**工作台挂载形态**：模块注册、路由前缀、企业 CRUD、税额计算
（小型微利 5% 优惠口径）、申报表创建/提交、政策列表——全部使用合成数据。
"""
import pytest

from app.agents.tools import TOOL_SPECS
from app.modules import get_loaded_modules

P = "/api/finance/tax"


def _create_enterprise(client, tax_number="91110108TEST0001", **kw):
    payload = {"name": "测试科技有限公司", "tax_number": tax_number, "industry_type": "软件",
               "employee_count": 5, "total_assets": 200.0}
    payload.update(kw)
    r = client.post(f"{P}/enterprises/", json=payload)
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


def test_module_registration():
    loaded = {m.name: m for m in get_loaded_modules()}
    assert "finance_tax" in loaded
    assert loaded["finance_tax"].prefix == "/api/finance/tax"
    assert "mod_tax_enterprise" in loaded["finance_tax"].tables


def test_enterprise_crud(client):
    eid = _create_enterprise(client)
    lst = client.get(f"{P}/enterprises/")
    assert lst.status_code == 200
    assert any(e["id"] == eid for e in lst.json())

    one = client.get(f"{P}/enterprises/{eid}")
    assert one.status_code == 200 and one.json()["name"] == "测试科技有限公司"

    upd = client.put(f"{P}/enterprises/{eid}", json={"name": "测试科技有限公司（改）"})
    assert upd.status_code == 200 and upd.json()["name"] == "测试科技有限公司（改）"

    dele = client.delete(f"{P}/enterprises/{eid}")
    assert dele.status_code in (200, 204)
    assert client.get(f"{P}/enterprises/{eid}").status_code == 404


def test_tax_calculate_small_micro(client):
    """小型微利企业口径（合成数据，引擎原样）。

    收入 300 / 成本 150 / 费用 50（万元）→ 利润 100；
    纳税调整：招待费超 5‰ 收入限额加回 3.5、工资薪金为 0 时福利/教育/工会全额加回 13、
    研发加计扣除 -20、捐赠未超 12% 利润限额全额扣除 -2 → 应纳税所得额 96.5 万；
    小型微利实际税负 5% → 应纳税额 4.825 万。
    """
    eid = _create_enterprise(client, tax_number="91110108TEST0002")
    r = client.post(f"{P}/tax-returns/calculate", json={
        "enterprise_id": eid, "year": 2026,
        "revenue": 300.0, "other_income": 0.0, "total_costs": 150.0,
        "total_expenses": 50.0,
        "business_entertainment": 5.0, "advertising_expense": 10.0, "donation_expense": 2.0,
        "rnd_expense": 20.0, "welfare_expense": 8.0, "education_expense": 3.0,
        "union_expense": 2.0, "employee_count": 5, "total_assets": 200.0,
    })
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["success"] is True
    assert d["is_small_micro"] is True
    assert d["taxable_income_after_loss_wan"] == pytest.approx(96.5)
    assert d["applicable_tax_rate"] == pytest.approx(0.05)
    assert d["tax_amount_wan"] == pytest.approx(4.83)  # 96.5 × 5% = 4.825，引擎保留两位
    assert d["enterprise_tax_type"]  # 非空类型判定文案
    assert d["calculation_steps"]  # 计算过程可追溯


def test_tax_return_create_and_submit(client):
    eid = _create_enterprise(client, tax_number="91110108TEST0003")
    r = client.post(f"{P}/tax-returns/", json={
        "enterprise_id": eid, "tax_period": "2026", "report_type": "A类",
        "total_revenue": 300.0, "total_costs": 150.0, "total_expenses": 50.0,
    })
    assert r.status_code in (200, 201), r.text
    rid = r.json()["id"]

    lst = client.get(f"{P}/tax-returns/", params={"enterprise_id": eid})
    assert lst.status_code == 200 and any(x["id"] == rid for x in lst.json())

    sub = client.put(f"{P}/tax-returns/{rid}/submit")
    assert sub.status_code == 200
    after = client.get(f"{P}/tax-returns/{rid}")
    assert after.json()["status"] == "submitted"


def test_policies_list(client):
    r = client.get(f"{P}/policies/")
    assert r.status_code == 200
    body = r.json()
    # seed 灌入的内置政策（原项目 seed_policies）
    assert body.get("total", len(body.get("items", body))) >= 0


def test_textbook_reference(client):
    r = client.get(f"{P}/textbook-reference/")
    assert r.status_code == 200


def test_no_agent_tools_registered():
    """本模块未注册 Agent 工具（税务计算入口后续按需再开）。"""
    for name in TOOL_SPECS:
        assert not name.startswith("tax_")
