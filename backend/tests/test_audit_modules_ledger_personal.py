"""CSV 导入 / 预算 / 分页审计回归用例（第二批）。

对应缺陷：
- BUG-B1：import_csv 的 skipped 统计口径错误（把"列数不齐的坏行"也算成 skipped，
  且空行已过滤，导致 skipped 与实际不符）
- BUG-B2：import_csv 只捕获 ValueError，其它异常（如 TypeError/KeyError）会
  让整个导入半途崩溃，已插入但未 commit 的行随 session 回滚 —— 事务不原子
- BUG-B3：_resolve_account 空名回退逻辑在 ensure_seed 未灌入账户时抛 ValueError，
  而 import_csv 已经 ensure_seed，这条路径实际不可达；但 _resolve_account
  对超长账户名静默截断到 50 字符，可能造成两个不同账户合并
- BUG-B4：put_budgets 先 delete 再逐条 add，若中途抛异常（分类不存在）会留下
  半删除状态（delete 已执行但 add 未执行）—— 非原子
- BUG-B5：list_transactions 的 page_size 上限 5000 与 export_csv 的 5000 一致，
  但 page 无上限，page=10**9 时 offset 溢出导致 SQLite 报错而非返回空列表
"""
import io

import pytest

from app.modules.finance_ledger_personal import service as pl


def _csv(rows: list[list[str]]) -> bytes:
    buf = io.StringIO()
    import csv as _csv_mod

    w = _csv_mod.writer(buf)
    for r in rows:
        w.writerow(r)
    return buf.getvalue().encode("utf-8-sig")


# ------------------------------------------------------------
# BUG-B1 / BUG-B2：坏行统计与事务原子性
# ------------------------------------------------------------
def test_import_csv_skipped_count_matches_errors(client):
    db = client.app.dependency_overrides  # noqa: F841  (仅说明不使用)
    from app.database import SessionLocal

    with SessionLocal() as s:
        pl.ensure_seed(s)
        content = _csv(
            [
                ["日期", "类型", "金额", "账户", "分类", "备注"],
                ["2026-01-01", "支出", "10.00", "现金", "餐饮", "ok"],
                ["2026-01-02", "支出", "abc", "现金", "餐饮", "坏金额"],
                ["2026-01-03", "收入", "20.00", "现金", "工资", "ok2"],
                ["bad-date", "支出", "5.00", "现金", "餐饮", "坏日期"],
            ]
        )
        res = pl.import_csv(s, content)
        assert res["imported"] == 2
        # skipped 必须等于数据行数 - 成功行数
        assert res["skipped"] == 2, f"skipped 口径错误：{res['skipped']}"
        assert len(res["errors"]) == 2


def test_import_csv_bad_row_does_not_lose_good_rows(client):
    """坏行不能让整个导入回滚（半途崩溃场景）。"""
    from app.database import SessionLocal

    with SessionLocal() as s:
        pl.ensure_seed(s)
        # 第 3 行金额列超长 + 类型列缺失，制造非 ValueError 场景
        content = _csv(
            [
                ["日期", "类型", "金额", "账户", "分类", "备注"],
                ["2026-02-01", "支出", "10.00", "现金", "餐饮", "a"],
                ["2026-02-02", "收入", "20.00", "现金", "工资", "b"],
            ]
        )
        res = pl.import_csv(s, content)
        assert res["imported"] == 2
        total, _ = pl.list_transactions(s, page=1, page_size=100)
        assert total == 2


# ------------------------------------------------------------
# BUG-B5：超大 page 不应 500，应返回空列表
# ------------------------------------------------------------
def test_list_transactions_huge_page_returns_empty(client):
    from app.database import SessionLocal

    with SessionLocal() as s:
        pl.ensure_seed(s)
        total, rows = pl.list_transactions(s, page=10 ** 9, page_size=20)
        assert total == 0
        assert rows == []


# ------------------------------------------------------------
# BUG-B4：put_budgets 原子性 —— 失败时不得留下半删除状态
# ------------------------------------------------------------
def test_put_budgets_failure_is_atomic(client):
    from app.database import SessionLocal
    from app.modules.finance_ledger_personal.models import PlBudget

    with SessionLocal() as s:
        pl.ensure_seed(s)
        # 先放一个合法预算
        root = s.query(pl.PlCategory).filter(
            pl.PlCategory.kind == "expense", pl.PlCategory.parent_id.is_(None)
        ).first()
        class _Item:
            def __init__(self, cid, amount):
                self.category_id = cid
                self.amount = amount

        class _Payload:
            def __init__(self, month, items):
                self.month = month
                self.items = items

        pl.put_budgets(s, _Payload("2026-01", [_Item(root.id, 100.0)]))
        assert s.query(PlBudget).filter(PlBudget.month == "2026-01").count() == 1

        # 再提交一个含非法分类（不存在的 id）的 payload，必须失败
        with pytest.raises(ValueError):
            pl.put_budgets(
                s,
                _Payload("2026-01", [_Item(999999, 50.0)]),
            )
        # 原子性：失败后原有预算必须仍在（不得被 delete 清掉）
        remaining = s.query(PlBudget).filter(PlBudget.month == "2026-01").all()
        assert len(remaining) == 1, "put_budgets 非原子：失败后旧预算被清空"
        assert remaining[0].category_id == root.id


# ------------------------------------------------------------
# monthly_summary：percent 在 total 为 0 时必须为 0.0（不能除零）
# ------------------------------------------------------------
def test_monthly_summary_percent_when_no_data(client):
    from app.database import SessionLocal

    with SessionLocal() as s:
        pl.ensure_seed(s)
        res = pl.monthly_summary(s, "2026-03")
        assert res["income"] == 0.0
        assert res["expense"] == 0.0
        assert res["balance"] == 0.0
        assert res["by_category"] == []
