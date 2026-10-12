"""finance_ledger_personal 模块测试。

覆盖：默认数据 seed（幂等）/ 账户·分类·交易 CRUD / 交易筛选与分页 /
月度统计口径（金额与占比算对）/ 趋势 / CSV 导入导出往返一致 / 预算 PUT-GET /
边界（负数、零、超大金额、空分类、非法日期、非法月份）/ 上传安全（扩展名、
大小、路径穿越）/ Agent 工具注册与行为。

全部使用合成数据：临时 SQLite（conftest）+ 内存构造 CSV；上传留档目录经
monkeypatch 指向 tmp_path，不碰真实业务文件、不写 tmp_path 之外的位置。
"""
import csv
import io
from datetime import date

import pytest

from app.agents.tools import TOOL_SPECS
from app.config import settings
from app.modules import get_loaded_modules
from app.modules.finance_ledger_personal import service

BASE = "/api/finance/ledger/personal"


@pytest.fixture(autouse=True)
def _finance_dir(tmp_path, monkeypatch):
    """CSV 导入留档目录指向 tmp_path（测试不得写 tmp_path 之外的位置）。"""
    monkeypatch.setattr(settings, "finance_data_dir", str(tmp_path / "finance"))


# ---------------- 工具函数 ----------------

def _accounts(client) -> list[dict]:
    return client.get(f"{BASE}/accounts").json()


def _account_id(client, name="现金") -> int:
    return next(a["id"] for a in _accounts(client) if a["name"] == name)


def _categories(client, kind="expense") -> list[dict]:
    return client.get(f"{BASE}/categories", params={"kind": kind}).json()


def _cat_id(client, name, kind="expense", parent_name=None) -> int:
    cats = _categories(client, kind)
    for c in cats:
        if c["name"] != name:
            continue
        if parent_name is None and c["parent_id"] is None:
            return c["id"]
        if parent_name is not None:
            parent = next((p for p in cats if p["id"] == c["parent_id"]), None)
            if parent and parent["name"] == parent_name:
                return c["id"]
    raise AssertionError(f"分类不存在：{kind} {parent_name or ''}>{name}")


def _tx(client, **kw) -> dict:
    """快捷记账：默认 支出 100 元、今天、现金、无分类。"""
    payload = {
        "kind": kw.pop("kind", "expense"),
        "amount": kw.pop("amount", 100.0),
        "occurred_on": kw.pop("occurred_on", date.today().isoformat()),
        "account_id": kw.pop("account_id", _account_id(client)),
        "category_id": kw.pop("category_id", None),
        "note": kw.pop("note", ""),
        "tags": kw.pop("tags", []),
    }
    resp = client.post(f"{BASE}/transactions", json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _csv_bytes(rows: list[list], headers=None) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(headers or service.CSV_HEADERS)
    w.writerows(rows)
    return buf.getvalue().encode("utf-8-sig")


# ---------------- 模块注册 ----------------

def test_manifest_loaded(client):
    loaded = {m.name: m for m in get_loaded_modules()}
    m = loaded["finance_ledger_personal"]
    assert m.prefix == "/api/finance/ledger/personal"
    assert m.tables == ["mod_pl_account", "mod_pl_category", "mod_pl_transaction", "mod_pl_budget"]
    assert m.menu == {"group": "财务", "icon": "Notebook", "order": 40}
    assert {t["name"] for t in m.agent_tools} == {"pl_add_transaction", "pl_monthly_summary", "pl_recent"}


# ---------------- seed ----------------

def test_seed_defaults_and_idempotent(client):
    names = [a["name"] for a in _accounts(client)]
    assert names == ["现金", "银行卡", "微信", "支付宝"]
    expense = _categories(client, "expense")
    income = _categories(client, "income")
    assert len([c for c in expense if c["parent_id"] is None]) == 10
    assert len([c for c in income if c["parent_id"] is None]) == 10
    can_id = _cat_id(client, "餐饮")
    children = [c["name"] for c in expense if c["parent_id"] == can_id]
    assert set(children) >= {"工作餐", "聚餐", "外卖"}
    # 幂等：重复调用不产生重复数据
    _accounts(client)
    _categories(client)
    assert len(_accounts(client)) == 4
    assert len(_categories(client, "expense")) == len(expense)


# ---------------- 账户 CRUD ----------------

def test_account_crud_and_delete_guard(client):
    resp = client.post(f"{BASE}/accounts", json={"name": "测试储蓄卡", "kind": "bank", "sort": 9})
    assert resp.status_code == 201
    acc = resp.json()
    assert acc["kind"] == "bank"
    # 同名拒绝
    assert client.post(f"{BASE}/accounts", json={"name": "测试储蓄卡"}).status_code == 400
    # 更新
    resp = client.put(f"{BASE}/accounts/{acc['id']}", json={"name": "测试工资卡", "sort": 8})
    assert resp.status_code == 200 and resp.json()["name"] == "测试工资卡"
    assert client.put(f"{BASE}/accounts/99999", json={"name": "x"}).status_code == 404
    # 有交易引用时禁止删除
    _tx(client, account_id=acc["id"])
    assert client.delete(f"{BASE}/accounts/{acc['id']}").status_code == 400
    # 删除交易后可删账户
    tx_id = client.get(f"{BASE}/transactions", params={"account_id": acc["id"]}).json()["items"][0]["id"]
    client.delete(f"{BASE}/transactions/{tx_id}")
    assert client.delete(f"{BASE}/accounts/{acc['id']}").status_code == 200
    assert all(a["id"] != acc["id"] for a in _accounts(client))


# ---------------- 分类 CRUD ----------------

def test_category_crud_rules(client):
    can_id = _cat_id(client, "餐饮")
    # 建子分类
    resp = client.post(f"{BASE}/categories", json={"kind": "expense", "name": "下午茶", "parent_id": can_id})
    assert resp.status_code == 201
    child = resp.json()
    # 最多两级
    resp = client.post(f"{BASE}/categories", json={"kind": "expense", "name": "咖啡", "parent_id": child["id"]})
    assert resp.status_code == 400
    # 子分类类型必须与父分类一致
    resp = client.post(f"{BASE}/categories", json={"kind": "income", "name": "餐补", "parent_id": can_id})
    assert resp.status_code == 400
    # 同级重名拒绝
    assert client.post(f"{BASE}/categories", json={"kind": "expense", "name": "下午茶", "parent_id": can_id}).status_code == 400
    # 更新
    resp = client.put(f"{BASE}/categories/{child['id']}", json={"name": "下午茶饮"})
    assert resp.status_code == 200 and resp.json()["name"] == "下午茶饮"
    # 有子分类禁止删除
    assert client.delete(f"{BASE}/categories/{can_id}").status_code == 400
    # 有交易引用禁止删除
    _tx(client, category_id=child["id"])
    assert client.delete(f"{BASE}/categories/{child['id']}").status_code == 400
    tx_id = client.get(f"{BASE}/transactions", params={"category_id": child["id"]}).json()["items"][0]["id"]
    client.delete(f"{BASE}/transactions/{tx_id}")
    assert client.delete(f"{BASE}/categories/{child['id']}").status_code == 200
    assert client.delete(f"{BASE}/categories/99999").status_code == 404


# ---------------- 交易 CRUD / 筛选 / 分页 ----------------

def test_transaction_crud_filters_pagination(client):
    cash = _account_id(client, "现金")
    bank = _account_id(client, "银行卡")
    lunch = _cat_id(client, "工作餐", parent_name="餐饮")
    bus = _cat_id(client, "交通")
    salary = _cat_id(client, "工资", kind="income")
    _tx(client, amount=100, occurred_on="2026-10-01", account_id=cash, category_id=lunch, note="午饭")
    _tx(client, amount=50, occurred_on="2026-10-02", account_id=cash, category_id=bus)
    _tx(client, kind="income", amount=8000, occurred_on="2026-10-15", account_id=bank, category_id=salary)
    _tx(client, amount=30, occurred_on="2026-09-30", account_id=cash, category_id=bus)

    # 月份筛选
    r = client.get(f"{BASE}/transactions", params={"month": "2026-10"}).json()
    assert r["total"] == 3 and len(r["items"]) == 3
    # 分类筛选：一级分类 餐饮 应含子分类 工作餐 的交易
    r = client.get(f"{BASE}/transactions", params={"category_id": _cat_id(client, "餐饮")}).json()
    assert r["total"] == 1 and r["items"][0]["note"] == "午饭"
    assert r["items"][0]["category_name"] == "餐饮>工作餐"
    # 账户筛选
    r = client.get(f"{BASE}/transactions", params={"account_id": bank}).json()
    assert r["total"] == 1 and r["items"][0]["kind"] == "income"
    # 关键词筛选
    r = client.get(f"{BASE}/transactions", params={"keyword": "午饭"}).json()
    assert r["total"] == 1
    # 分页
    r = client.get(f"{BASE}/transactions", params={"page": 1, "page_size": 2}).json()
    assert r["total"] == 4 and len(r["items"]) == 2
    r = client.get(f"{BASE}/transactions", params={"page": 2, "page_size": 2}).json()
    assert len(r["items"]) == 2
    # 日期倒序
    r = client.get(f"{BASE}/transactions", params={"month": "2026-10"}).json()
    assert [i["occurred_on"] for i in r["items"]] == ["2026-10-15", "2026-10-02", "2026-10-01"]
    # 更新
    tx_id = r["items"][2]["id"]
    resp = client.put(f"{BASE}/transactions/{tx_id}", json={"amount": 120.5, "note": "午饭改"})
    assert resp.status_code == 200
    assert resp.json()["amount"] == 120.5 and resp.json()["note"] == "午饭改"
    assert client.put(f"{BASE}/transactions/99999", json={"amount": 1}).status_code == 404
    # 删除
    assert client.delete(f"{BASE}/transactions/{tx_id}").status_code == 200
    assert client.get(f"{BASE}/transactions").json()["total"] == 3
    assert client.delete(f"{BASE}/transactions/99999").status_code == 404


# ---------------- 统计口径 ----------------

def test_monthly_summary_amounts(client):
    cash = _account_id(client, "现金")
    lunch = _cat_id(client, "工作餐", parent_name="餐饮")
    waimai = _cat_id(client, "外卖", parent_name="餐饮")
    bus = _cat_id(client, "交通")
    salary = _cat_id(client, "工资", kind="income")
    hongbao = _cat_id(client, "红包", kind="income")
    _tx(client, amount=100.5, occurred_on="2026-10-01", account_id=cash, category_id=lunch)
    _tx(client, amount=49.5, occurred_on="2026-10-02", account_id=cash, category_id=waimai)
    _tx(client, amount=30, occurred_on="2026-10-03", account_id=cash, category_id=bus)
    _tx(client, amount=20, occurred_on="2026-10-04", account_id=cash)  # 未分类
    _tx(client, kind="income", amount=8000, occurred_on="2026-10-05", account_id=cash, category_id=salary)
    _tx(client, kind="income", amount=200, occurred_on="2026-10-06", account_id=cash, category_id=hongbao)
    _tx(client, amount=999, occurred_on="2026-09-30", account_id=cash, category_id=bus)  # 上月，不计入

    s = client.get(f"{BASE}/stats/monthly", params={"month": "2026-10"}).json()
    assert s["income"] == 8200.0
    assert s["expense"] == 200.0
    assert s["balance"] == 8000.0
    exp = {c["name"]: c for c in s["by_category"] if c["kind"] == "expense"}
    # 子分类归并到一级：餐饮 = 工作餐 + 外卖 = 150.0
    assert exp["餐饮"]["amount"] == 150.0
    assert exp["餐饮"]["percent"] == 75.0
    assert exp["交通"]["amount"] == 30.0 and exp["交通"]["percent"] == 15.0
    assert exp["未分类"]["amount"] == 20.0 and exp["未分类"]["percent"] == 10.0
    assert exp["未分类"]["category_id"] is None
    inc = {c["name"]: c for c in s["by_category"] if c["kind"] == "income"}
    assert inc["工资"]["amount"] == 8000.0
    # 占比按各自类型总额计算，支出占比合计 100
    assert round(sum(c["percent"] for c in exp.values()), 1) == 100.0


def test_trend(client):
    cash = _account_id(client, "现金")
    cur = date.today().strftime("%Y-%m")
    prev = service.shift_month(cur, -1)
    prev2 = service.shift_month(cur, -2)
    _tx(client, amount=10, occurred_on=f"{cur}-05", account_id=cash)
    _tx(client, kind="income", amount=1000, occurred_on=f"{prev}-10", account_id=cash)
    _tx(client, amount=40, occurred_on=f"{prev2}-15", account_id=cash)

    rows = client.get(f"{BASE}/stats/trend", params={"months": 6}).json()
    assert len(rows) == 6
    assert rows[-1]["month"] == cur and rows[-1]["expense"] == 10.0
    assert rows[-2]["month"] == prev and rows[-2]["income"] == 1000.0 and rows[-2]["balance"] == 1000.0
    assert rows[-3]["month"] == prev2 and rows[-3]["expense"] == 40.0
    # 缺月补零
    assert rows[0]["income"] == 0.0 and rows[0]["expense"] == 0.0
    rows = client.get(f"{BASE}/stats/trend", params={"months": 3}).json()
    assert len(rows) == 3 and rows[-1]["month"] == cur


# ---------------- CSV 导入导出 ----------------

def _roundtrip_rows(text: str) -> list[tuple]:
    """CSV 文本 → 数据行元组列表（忽略表头）。"""
    rows = [r for r in csv.reader(io.StringIO(text)) if r]
    return [tuple(r) for r in rows[1:]]


def test_csv_export_import_roundtrip(client, tmp_path):
    cash = _account_id(client, "现金")
    lunch = _cat_id(client, "工作餐", parent_name="餐饮")
    _tx(client, amount=100.5, occurred_on="2026-10-01", account_id=cash, category_id=lunch, note="午饭")
    _tx(client, kind="income", amount=8000, occurred_on="2026-10-05", account_id=_account_id(client, "银行卡"), category_id=_cat_id(client, "工资", kind="income"))
    _tx(client, amount=20, occurred_on="2026-10-06", account_id=cash, note="无分类,带逗号")

    resp = client.get(f"{BASE}/export/csv", params={"month": "2026-10"})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    text = resp.content.decode("utf-8-sig")
    header = text.splitlines()[0]
    assert header == "日期,类型,金额,账户,分类,备注"
    original = _roundtrip_rows(text)
    assert len(original) == 3

    # 清空后导入，往返一致
    for item in client.get(f"{BASE}/transactions", params={"page_size": 100}).json()["items"]:
        client.delete(f"{BASE}/transactions/{item['id']}")
    assert client.get(f"{BASE}/transactions").json()["total"] == 0

    resp = client.post(f"{BASE}/import/csv", files={"file": ("导出.csv", resp.content, "text/csv")})
    assert resp.status_code == 200, resp.text
    result = resp.json()
    assert result["imported"] == 3 and result["skipped"] == 0 and result["errors"] == []
    # 原件留档在 tmp_path 下的模块 inbox
    inbox = tmp_path / "finance" / "finance_ledger_personal" / "inbox"
    saved = list(inbox.glob("*_导出.csv"))
    assert len(saved) == 1

    resp2 = client.get(f"{BASE}/export/csv", params={"month": "2026-10"})
    assert _roundtrip_rows(resp2.content.decode("utf-8-sig")) == original


def test_import_creates_missing_account_and_category(client):
    content = _csv_bytes([
        ["2026-10-03", "支出", "66.6", "测试钱包X", "宠物>猫粮", "自动建档"],
        ["2026/10/04", "收入", "88", "测试钱包X", "外快", "斜杠日期"],
    ])
    resp = client.post(f"{BASE}/import/csv", files={"file": ("new.csv", content, "text/csv")})
    assert resp.status_code == 200
    result = resp.json()
    assert result["imported"] == 2 and result["skipped"] == 0
    assert result["created_accounts"] == ["测试钱包X"]
    assert set(result["created_categories"]) == {"宠物", "宠物>猫粮", "外快"}
    # 新分类类型随交易类型
    pet = next(c for c in _categories(client, "expense") if c["name"] == "宠物")
    child = next(c for c in _categories(client, "expense") if c["parent_id"] == pet["id"])
    assert child["name"] == "猫粮"
    r = client.get(f"{BASE}/transactions").json()
    assert r["total"] == 2


def test_import_bad_rows_skipped(client):
    content = _csv_bytes([
        ["2026-10-03", "支出", "10", "现金", "餐饮", "好行"],
        ["2026-13-40", "支出", "10", "现金", "餐饮", "坏日期"],
        ["2026-10-03", "转账", "10", "现金", "餐饮", "坏类型"],
        ["2026-10-03", "支出", "abc", "现金", "餐饮", "坏金额"],
        ["2026-10-03", "支出", "-5", "现金", "餐饮", "负金额"],
    ])
    resp = client.post(f"{BASE}/import/csv", files={"file": ("bad.csv", content, "text/csv")})
    assert resp.status_code == 200
    result = resp.json()
    assert result["imported"] == 1 and result["skipped"] == 4
    assert len(result["errors"]) == 4
    assert client.get(f"{BASE}/transactions").json()["total"] == 1


def test_import_guards(client, tmp_path):
    # 扩展名白名单
    resp = client.post(f"{BASE}/import/csv", files={"file": ("evil.txt", b"a,b", "text/plain")})
    assert resp.status_code == 400
    resp = client.post(f"{BASE}/import/csv", files={"file": ("evil.csv.exe", b"a,b", "text/plain")})
    assert resp.status_code == 400
    # 大小限制（5MB + 1 字节）
    big = b"x" * (service.MAX_IMPORT_BYTES + 1)
    resp = client.post(f"{BASE}/import/csv", files={"file": ("big.csv", big, "text/csv")})
    assert resp.status_code == 400
    # 缺必需列
    resp = client.post(f"{BASE}/import/csv", files={"file": ("h.csv", _csv_bytes([["2026-10-01"]], headers=["日期", "备注"]), "text/csv")})
    assert resp.status_code == 400
    # 路径穿越：文件名被清洗，留档仍在 tmp_path 的 inbox 内
    content = _csv_bytes([["2026-10-03", "支出", "10", "现金", "餐饮", ""]])
    resp = client.post(f"{BASE}/import/csv", files={"file": ("../../escape.csv", content, "text/csv")})
    assert resp.status_code == 200
    inbox = tmp_path / "finance" / "finance_ledger_personal" / "inbox"
    assert len(list(inbox.glob("*_escape.csv"))) == 1
    assert not (tmp_path / "escape.csv").exists()
    # GBK 编码兼容
    gbk = "日期,类型,金额,账户,分类,备注\n2026-10-03,支出,12.5,现金,餐饮,中文备注\n".encode("gbk")
    resp = client.post(f"{BASE}/import/csv", files={"file": ("gbk.csv", gbk, "text/csv")})
    assert resp.status_code == 200 and resp.json()["imported"] == 1


# ---------------- 边界 ----------------

def test_transaction_boundaries(client):
    cash = _account_id(client, "现金")
    base = {"kind": "expense", "occurred_on": "2026-10-01", "account_id": cash}
    # 负数 / 零 / 四舍五入后为 0
    assert client.post(f"{BASE}/transactions", json={**base, "amount": -5}).status_code == 422
    assert client.post(f"{BASE}/transactions", json={**base, "amount": 0}).status_code == 422
    assert client.post(f"{BASE}/transactions", json={**base, "amount": 0.001}).status_code == 422
    # 超大金额：上限内可记，越界 422
    resp = client.post(f"{BASE}/transactions", json={**base, "amount": 999999999999.99})
    assert resp.status_code == 201 and resp.json()["amount"] == 999999999999.99
    assert client.post(f"{BASE}/transactions", json={**base, "amount": 1000000000000}).status_code == 422
    # 非法日期
    assert client.post(f"{BASE}/transactions", json={**base, "amount": 1, "occurred_on": "2026-02-30"}).status_code == 422
    assert client.post(f"{BASE}/transactions", json={**base, "amount": 1, "occurred_on": "not-a-date"}).status_code == 422
    # 非法类型
    assert client.post(f"{BASE}/transactions", json={**base, "amount": 1, "kind": "transfer"}).status_code == 422
    # 空分类允许
    resp = client.post(f"{BASE}/transactions", json={**base, "amount": 1})
    assert resp.status_code == 201 and resp.json()["category_id"] is None and resp.json()["category_name"] == ""
    # 分类与交易类型不一致
    salary = _cat_id(client, "工资", kind="income")
    assert client.post(f"{BASE}/transactions", json={**base, "amount": 1, "category_id": salary}).status_code == 400
    # 账户 / 分类不存在
    assert client.post(f"{BASE}/transactions", json={**base, "amount": 1, "account_id": 99999}).status_code == 400
    assert client.post(f"{BASE}/transactions", json={**base, "amount": 1, "category_id": 99999}).status_code == 400
    # 非法月份参数
    assert client.get(f"{BASE}/transactions", params={"month": "2026-13"}).status_code == 400
    assert client.get(f"{BASE}/stats/monthly", params={"month": "abc"}).status_code == 400
    assert client.get(f"{BASE}/stats/monthly").status_code == 422  # 缺必填 month


# ---------------- 预算 ----------------

def test_budget_put_get_idempotent(client):
    month = date.today().strftime("%Y-%m")
    can_id = _cat_id(client, "餐饮")
    lunch = _cat_id(client, "工作餐", parent_name="餐饮")
    _tx(client, amount=150, occurred_on=f"{month}-01", category_id=lunch)
    _tx(client, amount=50, occurred_on=f"{month}-02", category_id=_cat_id(client, "交通"))

    resp = client.put(f"{BASE}/budgets", json={"month": month, "items": [
        {"category_id": None, "amount": 3000},
        {"category_id": can_id, "amount": 800},
    ]})
    assert resp.status_code == 200
    data = resp.json()
    assert data["month"] == month
    by_name = {i["name"]: i for i in data["items"]}
    assert by_name["总预算"]["amount"] == 3000.0 and by_name["总预算"]["spent"] == 200.0
    # 子分类支出归并到一级分类预算
    assert by_name["餐饮"]["amount"] == 800.0 and by_name["餐饮"]["spent"] == 150.0
    assert by_name["餐饮"]["percent"] == 18.8
    assert by_name["交通"]["amount"] == 0.0 and by_name["交通"]["spent"] == 50.0

    # 幂等：重复 PUT 覆盖而非累加
    resp = client.put(f"{BASE}/budgets", json={"month": month, "items": [{"category_id": can_id, "amount": 500}]})
    by_name = {i["name"]: i for i in resp.json()["items"]}
    assert by_name["餐饮"]["amount"] == 500.0
    assert by_name["总预算"]["amount"] == 0.0  # 未提供的项被清除

    # 非法预算目标：收入分类 / 子分类 / 不存在的分类
    salary = _cat_id(client, "工资", kind="income")
    assert client.put(f"{BASE}/budgets", json={"month": month, "items": [{"category_id": salary, "amount": 1}]}).status_code == 400
    assert client.put(f"{BASE}/budgets", json={"month": month, "items": [{"category_id": lunch, "amount": 1}]}).status_code == 400
    assert client.put(f"{BASE}/budgets", json={"month": month, "items": [{"category_id": 99999, "amount": 1}]}).status_code == 400
    assert client.put(f"{BASE}/budgets", json={"month": "2026-13", "items": []}).status_code == 422
    assert client.get(f"{BASE}/budgets", params={"month": "2026-13"}).status_code == 400


# ---------------- Agent 工具 ----------------

def test_agent_tools_registered_and_work(client):
    for name in ("pl_add_transaction", "pl_monthly_summary", "pl_recent"):
        assert name in TOOL_SPECS
    assert TOOL_SPECS["pl_add_transaction"]["readonly"] is False
    assert TOOL_SPECS["pl_monthly_summary"]["readonly"] is True
    assert TOOL_SPECS["pl_recent"]["readonly"] is True

    add = TOOL_SPECS["pl_add_transaction"]["func"]
    month = date.today().strftime("%Y-%m")
    r = add(kind="支出", amount=12.5, date=f"{month}-08", account="微信", category="餐饮>工作餐", note="工具记账", tags="工作,餐补")
    assert r["ok"] is True and r["transaction_id"] >= 1
    assert r["account"] == "微信" and r["category"] == "餐饮>工作餐"
    # 账户名缺省时落到排序第一的账户；类型别名兼容
    r = add(kind="收入", amount=100, date=f"{month}-09", category="工资")
    assert r["ok"] is True and r["account"] == "现金"
    # 非法输入不崩，返回 error
    assert add(kind="转账", amount=1)["ok"] is False
    assert add(kind="支出", amount="abc")["ok"] is False
    assert add(kind="支出", amount=-1)["ok"] is False
    assert add(kind="支出", amount=1, date="2026-13-40")["ok"] is False
    # 未匹配到的分类 → 未分类 + warning
    r = add(kind="支出", amount=1, category="不存在的分类")
    assert r["ok"] is True and r["category"] == "未分类" and "warning" in r

    summary = TOOL_SPECS["pl_monthly_summary"]["func"](month=month)
    assert summary["ok"] is True
    assert summary["expense"] == 13.5 and summary["income"] == 100.0 and summary["balance"] == 86.5
    assert summary["top_expense"][0]["category"] == "餐饮"
    assert TOOL_SPECS["pl_monthly_summary"]["func"](month="2026-13")["ok"] is False

    recent = TOOL_SPECS["pl_recent"]["func"](limit=5)
    assert len(recent) == 3  # 12.5（支出）+ 100（收入）+ 1（未匹配分类）
    hit = TOOL_SPECS["pl_recent"]["func"](limit=5, keyword="工具记账")
    assert len(hit) == 1 and hit[0]["金额"] == 12.5 and hit[0]["账户"] == "微信"
