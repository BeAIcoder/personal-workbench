"""personal-memory-integrity / personal-integrity-review 任务聚焦测试（Swarm 白名单内仅此一个测试文件）。

对应审计台账（2026-10-22 全面检查报告）与 Swarm 裁定：
- A08：长期记忆列表关键词按 LIKE 原样拼接，% / _ / 反斜杠被当通配符放大结果集
  → escape_like 转义 + 显式 escape，关键词按字面匹配；
- 个人账本 CSV 导入：无行数护栏，且整批共用单一事务——行内 flush 失败令会话事务
  失效，后续有效行全丢（500），坏行中途创建的账户/分类污染缓存并被最终提交
  → 每行 SAVEPOINT（begin_nested）隔离 flush 失败，坏行回滚不污染缓存/后续有效行，
    imported/skipped/errors 与实际提交一致，最终仅提交有效行；行数复用共享
    MAX_IMPORT_ROWS（超限可检测拒绝，无半成品）。
- A09（第二轮复核新增）：DB 级坏行的原始异常文本（含 SQL 语句/表名/绑定参数/驱动名）
  曾直接进入对外 errors 响应 → 现映射为行号 + 通用文案，SQL/本机路径不外泄；
  行前快照差集剔除在「分类解析相位失败」与「账户建档相位失败」两种注入下均锁定。

口径：限额类测试一律 monkeypatch 小值，不生成巨型文件；行级失败注入真实 DB 级
约束错误（NOT NULL），不用「手动 raise 不毒化事务」的假失败；全部使用合成数据
（conftest 已钉住临时 SQLite），不碰真实业务数据。
"""
import csv
import io

import pytest

from app.agents import memory
from app.modules.finance_ledger_personal import service
from app.modules.finance_ledger_personal.models import PlAccount

BASE = "/api/finance/ledger/personal"
P = "/api/knowledge"


def _csv_bytes(rows: list[list], headers=None) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(headers or service.CSV_HEADERS)
    w.writerows(rows)
    return buf.getvalue().encode("utf-8-sig")


# ---------------- 记忆关键词：LIKE 元字符按字面匹配 ----------------

def test_list_memories_keyword_wildcards_matched_literally(client):
    """% / _ / 反斜杠按字面匹配，不通配放大结果集。"""
    client.post(f"{P}/memories", json={"content": "变量名 5_0 表示测试标记"})
    client.post(f"{P}/memories", json={"content": "编号 510 的到货记录"})
    items = client.get(f"{P}/memories", params={"keyword": "5_0"}).json()["items"]
    assert [i["content"] for i in items] == ["变量名 5_0 表示测试标记"]

    client.post(f"{P}/memories", json={"content": "完成度 100% 达成"})
    client.post(f"{P}/memories", json={"content": "编号 1005 达成"})
    items = client.get(f"{P}/memories", params={"keyword": "100%"}).json()["items"]
    assert [i["content"] for i in items] == ["完成度 100% 达成"]

    client.post(f"{P}/memories", json={"content": r"路径 a\b 需要转义"})
    items = client.get(f"{P}/memories", params={"keyword": r"a\b"}).json()["items"]
    assert [i["content"] for i in items] == [r"路径 a\b 需要转义"]


def test_list_memories_service_level_literal_keyword(client):
    """service 层同一口径：含元字符的关键词只按字面命中（经 escape_like + escape）。"""
    from app.database import SessionLocal

    with SessionLocal() as db:
        memory.add_memory(db, "下划线标记 a_b 与百分号 50% 同现")
        memory.add_memory(db, "普通记录 axb 与 5099 无元字符")
        hits = memory.list_memories(db, keyword="a_b")
        assert [h["content"] for h in hits] == ["下划线标记 a_b 与百分号 50% 同现"]
        hits = memory.list_memories(db, keyword="50%")
        assert [h["content"] for h in hits] == ["下划线标记 a_b 与百分号 50% 同现"]


# ---------------- 编辑记忆：保持 exclude_unset 部分更新（置顶丢失为误报，不修） ----------------

def test_update_memory_keeps_partial_update(client):
    """部分更新回归：只改 pinned 不动内容，只改内容保留 pinned（既有行为保持）。"""
    created = client.post(f"{P}/memories", json={"content": "部分更新回归：只改置顶不动内容。", "tags": ["回归"]})
    mid = created.json()["id"]

    upd = client.put(f"{P}/memories/{mid}", json={"pinned": True})
    assert upd.status_code == 200
    body = upd.json()
    assert body["pinned"] is True
    assert body["content"] == "部分更新回归：只改置顶不动内容。"
    assert body["tags"] == ["回归"]

    upd2 = client.put(f"{P}/memories/{mid}", json={"content": "部分更新回归：只改内容保留置顶。"})
    assert upd2.status_code == 200
    assert upd2.json()["pinned"] is True
    assert upd2.json()["content"] == "部分更新回归：只改内容保留置顶。"


# ---------------- CSV 导入：行级 SAVEPOINT 隔离 ----------------

def test_import_row_db_failure_isolated_by_savepoint(client, monkeypatch):
    """行内 flush 失败（真实 DB 级约束错误）被 SAVEPOINT 隔离：

    坏行跳过且其中途创建的账户/分类随行回滚（不污染缓存与建档记录），
    后续有效行照常入库；imported/skipped/errors 与实际提交一致。
    """
    real_resolve_category = service._resolve_category

    def flaky_resolve_category(db, cache, path, kind, created):
        if "毒" in path:
            # 行内 flush 落库失败：真实 DB 级 NOT NULL 约束错误。无行级 SAVEPOINT
            # 隔离时会话事务被毒化，后续有效行全丢（最终 commit 抛
            # PendingRollbackError → 500），坏行中途已建的账户还会污染缓存。
            db.add(PlAccount(name=None))
            db.flush()
        return real_resolve_category(db, cache, path, kind, created)

    monkeypatch.setattr(service, "_resolve_category", flaky_resolve_category)

    content = _csv_bytes([
        ["2026-10-01", "支出", "10", "现金", "餐饮", "好行1"],
        ["2026-10-02", "支出", "20", "毒账户", "毒分类", "坏行"],  # 先建 毒账户 再毒 fail
        ["2026-10-03", "支出", "30", "毒账户", "餐饮", "好行3"],  # 复用被回滚的账户名
    ])
    resp = client.post(f"{BASE}/import/csv", files={"file": ("poison.csv", content, "text/csv")})
    assert resp.status_code == 200, resp.text
    result = resp.json()
    assert result["imported"] == 2 and result["skipped"] == 1
    assert len(result["errors"]) == 1 and result["errors"][0].startswith("第3行：")
    # 建档记录只含最终提交的：坏行中途建的 毒账户 已随行回滚，由好行3 重新创建
    assert result["created_accounts"] == ["毒账户"]

    r = client.get(f"{BASE}/transactions").json()
    assert r["total"] == 2
    notes = {i["note"] for i in r["items"]}
    assert notes == {"好行1", "好行3"}
    # 坏行的副作用（未提交账户）不落库：全库只有好行3 重建的一个 毒账户
    poison = [a for a in client.get(f"{BASE}/accounts").json() if a["name"] == "毒账户"]
    assert len(poison) == 1


# ---------------- CSV 导入：行数复用共享 MAX_IMPORT_ROWS ----------------

def test_import_enforces_shared_row_limit(client, monkeypatch):
    """数据行数超过共享上限：可检测拒绝（400「上限」）且无半成品；限额内行为不变。"""
    content = _csv_bytes([
        ["2026-10-01", "支出", "10", "现金", "餐饮", "一"],
        ["2026-10-02", "支出", "20", "现金", "餐饮", "二"],
        ["2026-10-03", "支出", "30", "现金", "餐饮", "三"],
    ])
    monkeypatch.setattr(service, "MAX_IMPORT_ROWS", 2, raising=False)
    resp = client.post(f"{BASE}/import/csv", files={"file": ("many.csv", content, "text/csv")})
    assert resp.status_code == 400
    assert "上限" in resp.json()["detail"]
    # 无半成品：超限拒绝不写任何交易
    assert client.get(f"{BASE}/transactions").json()["total"] == 0

    monkeypatch.setattr(service, "MAX_IMPORT_ROWS", 100, raising=False)
    ok = client.post(f"{BASE}/import/csv", files={"file": ("many.csv", content, "text/csv")})
    assert ok.status_code == 200 and ok.json()["imported"] == 3


def test_shared_max_import_rows_wired_into_personal_service():
    """个人账本导入确实接上共享上限常量（而非回退值/重复定义）。"""
    from app.import_limits import MAX_IMPORT_ROWS

    assert service.MAX_IMPORT_ROWS is MAX_IMPORT_ROWS


# ---------------- CSV 导入：对外错误不得泄露 SQL / 本机路径 ----------------

def test_import_db_error_does_not_leak_sql_or_paths(client, monkeypatch):
    """DB 级坏行的对外 errors 只含行号与通用文案，不回带 SQL 语句/表名/参数/本机路径。

    行级 SAVEPOINT 隔离后，原始 SQLAlchemy 异常文本（含 [SQL: INSERT INTO ...]、
    表名、绑定参数）若直接进入响应即构成 schema 泄露（A09 口径：对外错误不得
    回带 SQL 与本机路径）。注入与 test_import_row_db_failure_isolated_by_savepoint
    同口径：真实 NOT NULL 约束失败，不是手动 raise 的假失败。
    """
    real_resolve_category = service._resolve_category

    def flaky_resolve_category(db, cache, path, kind, created):
        if "毒" in path:
            db.add(PlAccount(name=None))
            db.flush()
        return real_resolve_category(db, cache, path, kind, created)

    monkeypatch.setattr(service, "_resolve_category", flaky_resolve_category)

    content = _csv_bytes([
        ["2026-10-01", "支出", "10", "现金", "餐饮", "好行1"],
        ["2026-10-02", "支出", "20", "毒账户", "毒分类", "坏行"],
        ["2026-10-03", "支出", "30", "毒账户", "餐饮", "好行3"],
    ])
    resp = client.post(f"{BASE}/import/csv", files={"file": ("poison.csv", content, "text/csv")})
    assert resp.status_code == 200, resp.text
    result = resp.json()
    assert result["imported"] == 2 and result["skipped"] == 1
    err = result["errors"][0]
    assert err.startswith("第3行：")
    # 对外文案不含 SQL 语句、表名、驱动名、绑定参数、库文件与本机路径
    leaked = [s for s in ("INSERT", "SELECT", "UPDATE", "SQL", "mod_pl_", "sqlite3",
                          "parameters", ".db", "backend", "D:\\", "D:/") if s in err]
    assert not leaked, f"对外错误泄露内部信息：{leaked} → {err}"
    # 行号与失败语义保留，用户能定位到具体行
    assert "第3行" in err and "失败" in err


# ---------------- CSV 导入：建档失败（账户创建即失败）的行级隔离契约 ----------------

def test_import_account_creation_failure_isolated(client, monkeypatch):
    """账户建档本身 flush 失败时：本行回滚、缓存/建档记录干净、后续有效行可重建同名账户。

    与 test_import_row_db_failure_isolated_by_savepoint 互补——那个用例失败点在
    分类解析（账户已建、再毒 fail），本用例失败点在账户建档（flush 立即失败），
    锁定行前快照差集剔除在两种失败相位下都准确。
    """
    real_resolve_account = service._resolve_account
    poisoned = {"done": False}

    def flaky_resolve_account(db, cache, name, created):
        if name == "毒账户" and not poisoned["done"]:
            poisoned["done"] = True  # 只毒第一行：后续有效行须能重建同名账户
            db.add(PlAccount(name=None))
            db.flush()
        return real_resolve_account(db, cache, name, created)

    monkeypatch.setattr(service, "_resolve_account", flaky_resolve_account)

    content = _csv_bytes([
        ["2026-10-01", "支出", "10", "现金", "餐饮", "好行1"],
        ["2026-10-02", "支出", "20", "毒账户", "餐饮", "坏行"],
        ["2026-10-03", "支出", "30", "毒账户", "餐饮", "好行3"],
    ])
    resp = client.post(f"{BASE}/import/csv", files={"file": ("poison.csv", content, "text/csv")})
    assert resp.status_code == 200, resp.text
    result = resp.json()
    assert result["imported"] == 2 and result["skipped"] == 1
    assert len(result["errors"]) == 1 and result["errors"][0].startswith("第3行：")
    # 建档失败行不留下建档记录；好行3 重建后如实反映
    assert result["created_accounts"] == ["毒账户"]
    r = client.get(f"{BASE}/transactions").json()
    assert r["total"] == 2
    notes = {i["note"] for i in r["items"]}
    assert notes == {"好行1", "好行3"}
    poison = [a for a in client.get(f"{BASE}/accounts").json() if a["name"] == "毒账户"]
    assert len(poison) == 1


# ---------------- 记忆关键词：单关键词含全部 LIKE 元字符按字面匹配 ----------------

def test_list_memories_keyword_with_all_metachars(client):
    """单个关键词同时含 % _ \\ 时按整体字面匹配，近似串不被通配放大命中。"""
    client.post(f"{P}/memories", json={"content": "混合元字符 a%b_c\\d 标记"})
    client.post(f"{P}/memories", json={"content": "近似但不等 aXbYcd 标记"})
    items = client.get(f"{P}/memories", params={"keyword": r"a%b_c\d"}).json()["items"]
    assert [i["content"] for i in items] == ["混合元字符 a%b_c\\d 标记"]
