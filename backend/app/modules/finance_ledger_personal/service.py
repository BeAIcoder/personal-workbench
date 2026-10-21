"""finance_ledger_personal 模块业务逻辑与 Agent 工具函数。

约定：
- 所有对外 service 函数开头调用 ensure_seed：默认账户 / 分类仅在对应表为空时灌入（幂等）；
- 业务规则违规抛 ValueError（路由层转 400），按 id 找不到资源抛 LookupError（路由层转 404）；
- 金额入库为 Numeric(14, 2)，出口统一 round 两位小数的 float，保证统计口径稳定。
"""
import csv
import io
import re
from datetime import date, datetime

from sqlalchemy import func
from sqlalchemy.orm import Session

from ...database import SessionLocal
from .models import PlAccount, PlBudget, PlCategory, PlTransaction

MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
MAX_IMPORT_BYTES = 5 * 1024 * 1024  # CSV 导入上限 5MB

KINDS = ("expense", "income")
KIND_ZH = {"expense": "支出", "income": "收入"}
ZH_KIND = {v: k for k, v in KIND_ZH.items()}
CSV_HEADERS = ["日期", "类型", "金额", "账户", "分类", "备注"]

# 默认账户（首次使用灌入）
DEFAULT_ACCOUNTS = [
    ("现金", "cash"),
    ("银行卡", "bank"),
    ("微信", "ewallet"),
    ("支付宝", "ewallet"),
]

# 默认收支分类：(一级名, [子类名, ...])，支出 / 收入各 10 个一级分类
DEFAULT_EXPENSE_CATEGORIES = [
    ("餐饮", ["工作餐", "聚餐", "外卖"]),
    ("交通", ["公交地铁", "打车", "加油"]),
    ("购物", ["日用品", "服饰", "数码"]),
    ("居住", ["房租", "水电煤"]),
    ("娱乐", []),
    ("医疗", []),
    ("教育", []),
    ("通讯", []),
    ("人情往来", []),
    ("其他支出", []),
]
DEFAULT_INCOME_CATEGORIES = [
    ("工资", []),
    ("奖金", []),
    ("理财收益", []),
    ("兼职收入", []),
    ("报销", []),
    ("红包", []),
    ("退款", []),
    ("利息", []),
    ("二手闲置", []),
    ("其他收入", []),
]


# ---------------- 基础工具 ----------------

def validate_month(month: str) -> str:
    """校验并返回 YYYY-MM 月份串；非法抛 ValueError。"""
    m = (month or "").strip()
    if not MONTH_RE.fullmatch(m):
        raise ValueError(f"非法月份「{month}」，应为 YYYY-MM 格式")
    return m


def month_range(month: str) -> tuple[date, date]:
    """月份对应的 [起, 止) 日期区间。"""
    m = validate_month(month)
    y, mo = int(m[:4]), int(m[5:7])
    start = date(y, mo, 1)
    end = date(y + 1, 1, 1) if mo == 12 else date(y, mo + 1, 1)
    return start, end


def shift_month(month: str, delta: int) -> str:
    """月份平移：shift_month('2026-01', -1) -> '2025-12'。"""
    m = validate_month(month)
    idx = int(m[:4]) * 12 + (int(m[5:7]) - 1) + int(delta)
    return f"{idx // 12:04d}-{idx % 12 + 1:02d}"


def current_month() -> str:
    """当前月份 YYYY-MM。"""
    return date.today().strftime("%Y-%m")


def _money(value) -> float:
    """Decimal / None → 两位小数 float。"""
    if value is None:
        return 0.0
    return round(float(value), 2)


def _parse_day(value: str) -> date:
    """解析 YYYY-MM-DD 或 YYYY/M/D，失败抛 ValueError。"""
    v = (value or "").strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(v, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"无法解析日期「{value}」，应为 YYYY-MM-DD")


# ---------------- 默认数据 seed ----------------

def ensure_seed(db: Session) -> None:
    """首次使用灌入默认账户与收支分类；仅在对应表为空时灌入（幂等）。"""
    if db.query(PlAccount.id).count() == 0:
        for sort, (name, kind) in enumerate(DEFAULT_ACCOUNTS):
            db.add(PlAccount(name=name, kind=kind, sort=sort))
        db.commit()
    if db.query(PlCategory.id).count() == 0:
        sort = 0
        for kind, defs in (("expense", DEFAULT_EXPENSE_CATEGORIES), ("income", DEFAULT_INCOME_CATEGORIES)):
            for name, children in defs:
                parent = PlCategory(kind=kind, name=name, parent_id=None, sort=sort)
                sort += 1
                db.add(parent)
                db.flush()  # 先取 parent.id 供子分类引用
                for csort, cname in enumerate(children):
                    db.add(PlCategory(kind=kind, name=cname, parent_id=parent.id, sort=csort))
        db.commit()


# ---------------- 账户 ----------------

def list_accounts(db: Session) -> list[PlAccount]:
    """全部账户（sort 升序）。"""
    ensure_seed(db)
    return db.query(PlAccount).order_by(PlAccount.sort, PlAccount.id).all()


def create_account(db: Session, payload) -> PlAccount:
    """新建账户；同名拒绝。"""
    ensure_seed(db)
    name = payload.name.strip()
    if db.query(PlAccount.id).filter(PlAccount.name == name).count():
        raise ValueError(f"账户「{name}」已存在")
    acc = PlAccount(name=name[:50], kind=(payload.kind or "other").strip()[:20] or "other", sort=payload.sort)
    db.add(acc)
    db.commit()
    db.refresh(acc)
    return acc


def update_account(db: Session, account_id: int, payload) -> PlAccount:
    """更新账户名称 / 类型 / 排序。"""
    acc = db.get(PlAccount, account_id)
    if acc is None:
        raise LookupError(f"账户 {account_id} 不存在")
    data = payload.model_dump(exclude_unset=True)
    new_name = (data.get("name") or "").strip()
    if new_name and new_name != acc.name:
        if db.query(PlAccount.id).filter(PlAccount.name == new_name, PlAccount.id != account_id).count():
            raise ValueError(f"账户「{new_name}」已存在")
        acc.name = new_name[:50]
    if data.get("kind"):
        acc.kind = str(data["kind"]).strip()[:20] or "other"
    if data.get("sort") is not None:
        acc.sort = int(data["sort"])
    db.commit()
    db.refresh(acc)
    return acc


def delete_account(db: Session, account_id: int) -> None:
    """删除账户；仍有交易引用的账户拒绝删除。"""
    acc = db.get(PlAccount, account_id)
    if acc is None:
        raise LookupError(f"账户 {account_id} 不存在")
    if db.query(PlTransaction.id).filter(PlTransaction.account_id == account_id).count():
        raise ValueError("该账户下还有交易记录，不能删除")
    db.delete(acc)
    db.commit()


# ---------------- 分类 ----------------

def list_categories(db: Session, kind: str | None = None) -> list[PlCategory]:
    """全部分类（可按 expense / income 过滤，sort 升序）。"""
    ensure_seed(db)
    q = db.query(PlCategory)
    if kind in KINDS:
        q = q.filter(PlCategory.kind == kind)
    return q.order_by(PlCategory.sort, PlCategory.id).all()


def create_category(db: Session, payload) -> PlCategory:
    """新建分类；最多两级，子分类类型须与父分类一致。"""
    ensure_seed(db)
    kind = payload.kind
    name = payload.name.strip()
    parent: PlCategory | None = None
    if payload.parent_id is not None:
        parent = db.get(PlCategory, payload.parent_id)
        if parent is None:
            raise ValueError("父分类不存在")
        if parent.parent_id is not None:
            raise ValueError("最多两级分类：不能在子分类下再建子分类")
        if parent.kind != kind:
            raise ValueError(f"子分类类型必须与父分类（{KIND_ZH[parent.kind]}）一致")
    q = db.query(PlCategory.id).filter(PlCategory.kind == kind, PlCategory.name == name)
    q = q.filter(PlCategory.parent_id.is_(None) if parent is None else PlCategory.parent_id == parent.id)
    if q.count():
        raise ValueError(f"分类「{name}」已存在")
    cat = PlCategory(kind=kind, name=name[:50], parent_id=parent.id if parent else None, sort=payload.sort)
    db.add(cat)
    db.commit()
    db.refresh(cat)
    return cat


def update_category(db: Session, category_id: int, payload) -> PlCategory:
    """更新分类名称 / 排序（不允许改类型与层级，保证引用稳定）。"""
    cat = db.get(PlCategory, category_id)
    if cat is None:
        raise LookupError(f"分类 {category_id} 不存在")
    data = payload.model_dump(exclude_unset=True)
    new_name = (data.get("name") or "").strip()
    if new_name and new_name != cat.name:
        q = db.query(PlCategory.id).filter(
            PlCategory.kind == cat.kind,
            PlCategory.name == new_name,
            PlCategory.id != category_id,
        )
        q = q.filter(PlCategory.parent_id.is_(None) if cat.parent_id is None else PlCategory.parent_id == cat.parent_id)
        if q.count():
            raise ValueError(f"分类「{new_name}」已存在")
        cat.name = new_name[:50]
    if data.get("sort") is not None:
        cat.sort = int(data["sort"])
    db.commit()
    db.refresh(cat)
    return cat


def delete_category(db: Session, category_id: int) -> None:
    """删除分类；有子分类或交易引用的拒绝删除。"""
    cat = db.get(PlCategory, category_id)
    if cat is None:
        raise LookupError(f"分类 {category_id} 不存在")
    if db.query(PlCategory.id).filter(PlCategory.parent_id == category_id).count():
        raise ValueError("请先删除该分类下的子分类")
    if db.query(PlTransaction.id).filter(PlTransaction.category_id == category_id).count():
        raise ValueError("该分类存在交易记录，不能删除")
    db.delete(cat)
    db.commit()


# ---------------- 交易 ----------------

def _category_maps(db: Session) -> tuple[dict[int, PlAccount], dict[int, PlCategory]]:
    """账户 / 分类 id → 对象 映射（个人账本数据量小，一次取全量）。"""
    accounts = {a.id: a for a in db.query(PlAccount).all()}
    cats = {c.id: c for c in db.query(PlCategory).all()}
    return accounts, cats


def category_path(cat: PlCategory | None, by_id: dict[int, PlCategory]) -> str:
    """分类显示路径：子分类返回 父>子，否则返回本名；空分类返回空串。"""
    if cat is None:
        return ""
    parent = by_id.get(cat.parent_id) if cat.parent_id else None
    return f"{parent.name}>{cat.name}" if parent else cat.name


def _root_of(cat: PlCategory | None, by_id: dict[int, PlCategory]) -> PlCategory | None:
    """向上找到一级分类（统计 / 预算按一级分类汇总）。"""
    while cat is not None and cat.parent_id is not None and cat.parent_id in by_id:
        cat = by_id[cat.parent_id]
    return cat


def _validate_refs(db: Session, kind: str, account_id: int, category_id: int | None) -> None:
    """校验账户存在、分类存在且类型与交易一致。"""
    if db.get(PlAccount, account_id) is None:
        raise ValueError(f"账户 {account_id} 不存在")
    if category_id is not None:
        cat = db.get(PlCategory, category_id)
        if cat is None:
            raise ValueError(f"分类 {category_id} 不存在")
        if cat.kind != kind:
            raise ValueError(f"分类「{cat.name}」是{KIND_ZH[cat.kind]}分类，与交易类型不一致")


def transaction_out(tx: PlTransaction, accounts: dict, cats: dict) -> dict:
    """ORM → API 输出 dict（附带账户名 / 分类路径）。"""
    acc = accounts.get(tx.account_id)
    return {
        "id": tx.id,
        "kind": tx.kind,
        "amount": _money(tx.amount),
        "occurred_on": tx.occurred_on,
        "account_id": tx.account_id,
        "account_name": acc.name if acc else "",
        "category_id": tx.category_id,
        "category_name": category_path(cats.get(tx.category_id), cats),
        "note": tx.note or "",
        "tags": list(tx.tags or []),
    }


def create_transaction(db: Session, payload) -> PlTransaction:
    """新建一笔交易。"""
    ensure_seed(db)
    _validate_refs(db, payload.kind, payload.account_id, payload.category_id)
    tx = PlTransaction(
        kind=payload.kind,
        amount=round(float(payload.amount), 2),
        occurred_on=payload.occurred_on,
        account_id=payload.account_id,
        category_id=payload.category_id,
        note=(payload.note or "")[:500],
        tags=list(payload.tags or []),
    )
    db.add(tx)
    db.commit()
    db.refresh(tx)
    return tx


def update_transaction(db: Session, tx_id: int, payload) -> PlTransaction:
    """按字段更新交易；校验更新后的账户 / 分类引用。"""
    tx = db.get(PlTransaction, tx_id)
    if tx is None:
        raise LookupError(f"交易 {tx_id} 不存在")
    data = payload.model_dump(exclude_unset=True)
    kind = data.get("kind", tx.kind)
    account_id = data.get("account_id", tx.account_id)
    category_id = data.get("category_id", tx.category_id)
    _validate_refs(db, kind, account_id, category_id)
    tx.kind = kind
    tx.account_id = account_id
    tx.category_id = category_id
    if "amount" in data:
        tx.amount = round(float(data["amount"]), 2)
    if "occurred_on" in data:
        tx.occurred_on = data["occurred_on"]
    if "note" in data:
        tx.note = (data["note"] or "")[:500]
    if "tags" in data:
        tx.tags = list(data["tags"] or [])
    tx.updated_at = datetime.now()
    db.commit()
    db.refresh(tx)
    return tx


def delete_transaction(db: Session, tx_id: int) -> None:
    """删除一笔交易。"""
    tx = db.get(PlTransaction, tx_id)
    if tx is None:
        raise LookupError(f"交易 {tx_id} 不存在")
    db.delete(tx)
    db.commit()


def list_transactions(
    db: Session,
    month: str | None = None,
    category_id: int | None = None,
    account_id: int | None = None,
    keyword: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[int, list[PlTransaction]]:
    """交易列表：月份 / 分类（含子分类）/ 账户 / 关键词筛选 + 分页，按日期倒序。"""
    ensure_seed(db)
    q = db.query(PlTransaction)
    if month:
        start, end = month_range(month)
        q = q.filter(PlTransaction.occurred_on >= start, PlTransaction.occurred_on < end)
    if category_id is not None:
        cat = db.get(PlCategory, category_id)
        if cat is None:
            raise ValueError(f"分类 {category_id} 不存在")
        ids = [category_id] + [c.id for c in db.query(PlCategory).filter(PlCategory.parent_id == category_id).all()]
        q = q.filter(PlTransaction.category_id.in_(ids))
    if account_id is not None:
        q = q.filter(PlTransaction.account_id == account_id)
    if keyword and keyword.strip():
        q = q.filter(PlTransaction.note.contains(keyword.strip(), autoescape=True))
    total = q.count()
    page = max(1, int(page))
    page_size = max(1, min(int(page_size), 5000))
    rows = (
        q.order_by(PlTransaction.occurred_on.desc(), PlTransaction.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return total, rows


# ---------------- 统计 ----------------

def monthly_summary(db: Session, month: str) -> dict:
    """月度汇总：收入 / 支出 / 结余 + 按一级分类的占比（子分类归并到一级）。"""
    ensure_seed(db)
    m = validate_month(month)
    start, end = month_range(m)
    base = db.query(PlTransaction).filter(PlTransaction.occurred_on >= start, PlTransaction.occurred_on < end)
    totals = dict(base.with_entities(PlTransaction.kind, func.sum(PlTransaction.amount)).group_by(PlTransaction.kind).all())
    income, expense = _money(totals.get("income")), _money(totals.get("expense"))
    rows = (
        base.with_entities(PlTransaction.kind, PlTransaction.category_id, func.sum(PlTransaction.amount))
        .group_by(PlTransaction.kind, PlTransaction.category_id)
        .all()
    )
    cats = {c.id: c for c in db.query(PlCategory).all()}
    agg: dict[tuple[str, int | None], float] = {}
    for kind, cid, amt in rows:
        root = _root_of(cats.get(cid), cats)
        key = (kind, root.id if root else None)
        agg[key] = agg.get(key, 0.0) + float(amt or 0)
    kind_total = {"expense": expense, "income": income}
    by_category = []
    for (kind, rid), amt in agg.items():
        amt = round(amt, 2)
        total = kind_total[kind]
        by_category.append(
            {
                "category_id": rid,
                "name": cats[rid].name if rid in cats else "未分类",
                "kind": kind,
                "amount": amt,
                "percent": round(amt / total * 100, 1) if total else 0.0,
            }
        )
    by_category.sort(key=lambda x: (-x["amount"], x["name"]))
    return {
        "month": m,
        "income": income,
        "expense": expense,
        "balance": round(income - expense, 2),
        "by_category": by_category,
    }


def trend(db: Session, months: int = 6) -> list[dict]:
    """最近 N 个月收支趋势（含当月，缺月补零），按月份升序。"""
    ensure_seed(db)
    months = max(1, min(int(months), 36))
    end_m = current_month()
    start_m = shift_month(end_m, -(months - 1))
    start, _ = month_range(start_m)
    month_col = func.strftime("%Y-%m", PlTransaction.occurred_on)
    rows = (
        db.query(month_col, PlTransaction.kind, func.sum(PlTransaction.amount))
        .filter(PlTransaction.occurred_on >= start)
        .group_by(month_col, PlTransaction.kind)
        .all()
    )
    data: dict[str, dict[str, float]] = {}
    for m, kind, amt in rows:
        data.setdefault(m, {})[kind] = _money(amt)
    result = []
    for i in range(months):
        m = shift_month(start_m, i)
        d = data.get(m, {})
        inc, exp = d.get("income", 0.0), d.get("expense", 0.0)
        result.append({"month": m, "income": inc, "expense": exp, "balance": round(inc - exp, 2)})
    return result


# ---------------- 月度预算 ----------------

def get_budgets(db: Session, month: str) -> dict:
    """某月预算视图：总预算 + 各一级支出分类的预算 / 已花 / 进度。"""
    ensure_seed(db)
    m = validate_month(month)
    start, end = month_range(m)
    budgets = {b.category_id: _money(b.amount) for b in db.query(PlBudget).filter(PlBudget.month == m).all()}
    cats = {c.id: c for c in db.query(PlCategory).all()}
    spent_rows = (
        db.query(PlTransaction.category_id, func.sum(PlTransaction.amount))
        .filter(PlTransaction.kind == "expense", PlTransaction.occurred_on >= start, PlTransaction.occurred_on < end)
        .group_by(PlTransaction.category_id)
        .all()
    )
    spent_by_root: dict[int | None, float] = {}
    for cid, amt in spent_rows:
        root = _root_of(cats.get(cid), cats)
        key = root.id if root else None
        spent_by_root[key] = spent_by_root.get(key, 0.0) + float(amt or 0)
    total_spent = round(sum(spent_by_root.values()), 2)

    def _item(cid: int | None, name: str, spent: float) -> dict:
        amount = budgets.get(cid, 0.0)
        spent = round(spent, 2)
        return {
            "category_id": cid,
            "name": name,
            "amount": amount,
            "spent": spent,
            "percent": round(spent / amount * 100, 1) if amount > 0 else 0.0,
        }

    items = [_item(None, "总预算", total_spent)]
    top_expense = [c for c in cats.values() if c.kind == "expense" and c.parent_id is None]
    for cat in sorted(top_expense, key=lambda c: (c.sort, c.id)):
        items.append(_item(cat.id, cat.name, spent_by_root.get(cat.id, 0.0)))
    return {"month": m, "items": items}


def put_budgets(db: Session, payload) -> dict:
    """整月预算整体替换（幂等）：amount<=0 视为清除该项。"""
    ensure_seed(db)
    m = validate_month(payload.month)
    amounts: dict[int | None, float] = {}
    for item in payload.items:
        cid = item.category_id
        if cid is not None:
            cat = db.get(PlCategory, cid)
            if cat is None:
                raise ValueError(f"分类 {cid} 不存在")
            if cat.kind != "expense" or cat.parent_id is not None:
                raise ValueError("预算仅支持一级支出分类（或总预算）")
        amounts[cid] = round(float(item.amount), 2)
    db.query(PlBudget).filter(PlBudget.month == m).delete()
    for cid, amt in amounts.items():
        if amt > 0:
            db.add(PlBudget(month=m, category_id=cid, amount=amt))
    db.commit()
    return get_budgets(db, m)


# ---------------- CSV 导入 / 导出 ----------------

def export_csv(
    db: Session,
    month: str | None = None,
    category_id: int | None = None,
    account_id: int | None = None,
    keyword: str | None = None,
) -> str:
    """导出交易为 CSV 文本（中文列名，日期倒序，上限 5000 行）。"""
    _, rows = list_transactions(db, month=month, category_id=category_id, account_id=account_id, keyword=keyword, page=1, page_size=5000)
    accounts, cats = _category_maps(db)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(CSV_HEADERS)
    for tx in rows:
        acc = accounts.get(tx.account_id)
        writer.writerow(
            [
                tx.occurred_on.isoformat(),
                KIND_ZH.get(tx.kind, tx.kind),
                f"{_money(tx.amount):.2f}",
                acc.name if acc else "",
                category_path(cats.get(tx.category_id), cats),
                tx.note or "",
            ]
        )
    return buf.getvalue()


def _decode_csv(content: bytes) -> str:
    """CSV 字节解码：优先 UTF-8（含 BOM），回退 GBK。"""
    for enc in ("utf-8-sig", "gbk"):
        try:
            return content.decode(enc)
        except UnicodeDecodeError:
            continue
    raise ValueError("无法识别文件编码（仅支持 UTF-8 / GBK）")


def _resolve_account(db: Session, cache: dict[str, PlAccount], name: str, created: set[str]) -> PlAccount:
    """按名找账户；不存在则创建（kind=other）；空名回退到排序第一的默认账户。"""
    name = (name or "").strip()
    if not name:
        first = db.query(PlAccount).order_by(PlAccount.sort, PlAccount.id).first()
        if first is None:
            raise ValueError("没有任何可用账户")
        return first
    if name in cache:
        return cache[name]
    acc = db.query(PlAccount).filter(PlAccount.name == name).first()
    if acc is None:
        acc = PlAccount(name=name[:50], kind="other", sort=100)
        db.add(acc)
        db.flush()
        created.add(name)
    cache[name] = acc
    return acc


def _resolve_category(db: Session, cache: dict, path: str, kind: str, created: set[str]) -> int | None:
    """按 父>子 路径解析分类；不存在则按交易类型自动创建；空路径返回 None。"""
    parts = [p.strip() for p in re.split(r"[>/]", (path or "").strip()) if p.strip()]
    if not parts:
        return None
    parent_id: int | None = None
    for depth, name in enumerate(parts[:2]):  # 最多两级，更深部分忽略
        key = (kind, parent_id, name)
        if key in cache:
            cat = cache[key]
        else:
            q = db.query(PlCategory).filter(PlCategory.kind == kind, PlCategory.name == name)
            q = q.filter(PlCategory.parent_id.is_(None) if parent_id is None else PlCategory.parent_id == parent_id)
            cat = q.first()
            if cat is None:
                cat = PlCategory(kind=kind, name=name[:50], parent_id=parent_id, sort=100)
                db.add(cat)
                db.flush()
                created.add(">".join(parts[: depth + 1]))
            cache[key] = cat
        parent_id = cat.id
    return parent_id


def import_csv(db: Session, content: bytes) -> dict:
    """导入 CSV：中文列名 日期/类型/金额/账户/分类/备注；坏行跳过并记录原因。"""
    ensure_seed(db)
    text = _decode_csv(content)
    rows = [r for r in csv.reader(io.StringIO(text)) if any((c or "").strip() for c in r)]
    if not rows:
        raise ValueError("文件内容为空")
    header = [(h or "").strip() for h in rows[0]]
    col = {name: header.index(name) for name in CSV_HEADERS if name in header}
    missing = [n for n in ("日期", "类型", "金额") if n not in col]
    if missing:
        raise ValueError(f"CSV 缺少必需列：{'、'.join(missing)}")

    account_cache: dict[str, PlAccount] = {}
    category_cache: dict = {}
    created_accounts: set[str] = set()
    created_categories: set[str] = set()
    imported, errors = 0, []

    def cell(row: list[str], name: str) -> str:
        idx = col.get(name)
        return (row[idx] or "").strip() if idx is not None and idx < len(row) else ""

    for line_no, row in enumerate(rows[1:], start=2):
        try:
            day = _parse_day(cell(row, "日期"))
            zh = cell(row, "类型")
            kind = ZH_KIND.get(zh, zh.lower())
            if kind not in KINDS:
                raise ValueError(f"类型「{zh}」应为 收入 或 支出")
            try:
                amount = round(float(cell(row, "金额").replace(",", "")), 2)
            except ValueError:
                raise ValueError(f"金额「{cell(row, '金额')}」不是数字") from None
            if amount <= 0:
                raise ValueError("金额必须大于 0")
            acc = _resolve_account(db, account_cache, cell(row, "账户"), created_accounts)
            cid = _resolve_category(db, category_cache, cell(row, "分类"), kind, created_categories)
            db.add(
                PlTransaction(
                    kind=kind,
                    amount=amount,
                    occurred_on=day,
                    account_id=acc.id,
                    category_id=cid,
                    note=cell(row, "备注")[:500],
                    tags=[],
                )
            )
            imported += 1
        except ValueError as e:
            if len(errors) < 10:
                errors.append(f"第{line_no}行：{e}")
    db.commit()
    return {
        "imported": imported,
        "skipped": len(rows) - 1 - imported,
        "errors": errors,
        "created_accounts": sorted(created_accounts),
        "created_categories": sorted(created_categories),
    }


# ---------------- Agent 工具 ----------------

def pl_add_transaction(
    kind: str,
    amount: float,
    date: str = "",
    account: str = "",
    category: str = "",
    note: str = "",
    tags: str = "",
) -> dict:
    """Agent 工具（创建类）：记一笔收入或支出。"""
    k = ZH_KIND.get((kind or "").strip(), (kind or "").strip().lower())
    if k not in KINDS:
        return {"ok": False, "error": f"类型应为 收入 或 支出，收到「{kind}」"}
    try:
        amt = round(float(amount), 2)
    except (TypeError, ValueError):
        return {"ok": False, "error": f"金额「{amount}」不是数字"}
    if amt <= 0:
        return {"ok": False, "error": "金额必须大于 0"}
    try:
        day = _parse_day(date) if (date or "").strip() else datetime.now().date()
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    tag_list = [t.strip()[:20] for t in (tags or "").split(",") if t.strip()][:10]
    with SessionLocal() as db:
        ensure_seed(db)
        accounts, cats = _category_maps(db)
        warnings: list[str] = []
        acc = next((a for a in accounts.values() if a.name == (account or "").strip()), None)
        if acc is None:
            acc = db.query(PlAccount).order_by(PlAccount.sort, PlAccount.id).first()
            aname = (account or "").strip()
            if aname:
                warnings.append(f"未找到账户「{aname}」，已记入「{acc.name}」")
        cid = None
        cname = (category or "").strip()
        if cname:
            # 只匹配已存在的分类（父>子），找不到按未分类记账，避免误建分类
            parent_id = None
            for part in [p.strip() for p in re.split(r"[>/]", cname) if p.strip()][:2]:
                q = db.query(PlCategory).filter(PlCategory.kind == k, PlCategory.name == part)
                q = q.filter(PlCategory.parent_id.is_(None) if parent_id is None else PlCategory.parent_id == parent_id)
                cat = q.first()
                if cat is None:
                    cid = None
                    warnings.append(f"未找到分类「{cname}」，已按未分类记账")
                    break
                parent_id = cat.id
                cid = cat.id
        tx = PlTransaction(
            kind=k,
            amount=amt,
            occurred_on=day,
            account_id=acc.id,
            category_id=cid,
            note=(note or "")[:500],
            tags=tag_list,
        )
        db.add(tx)
        db.commit()
        db.refresh(tx)
        result = {
            "ok": True,
            "transaction_id": tx.id,
            "date": tx.occurred_on.isoformat(),
            "kind": KIND_ZH[k],
            "amount": _money(tx.amount),
            "account": acc.name,
            "category": category_path(cats.get(cid), cats) or "未分类",
        }
        if warnings:
            result["warning"] = "；".join(warnings)
        return result


def pl_monthly_summary(month: str = "") -> dict:
    """Agent 工具（只读）：查询某月收支汇总与支出分类 TOP5，month 默认当月。"""
    m = (month or "").strip() or current_month()
    try:
        validate_month(m)
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    with SessionLocal() as db:
        s = monthly_summary(db, m)
    top = [c for c in s["by_category"] if c["kind"] == "expense"][:5]
    return {
        "ok": True,
        "month": s["month"],
        "income": s["income"],
        "expense": s["expense"],
        "balance": s["balance"],
        "top_expense": [{"category": c["name"], "amount": c["amount"], "percent": c["percent"]} for c in top],
    }


def pl_recent(limit: int = 10, keyword: str = "") -> list[dict]:
    """Agent 工具（只读）：最近的交易流水，可按备注关键词过滤。"""
    limit = max(1, min(int(limit), 50))
    with SessionLocal() as db:
        _, rows = list_transactions(db, keyword=keyword or None, page=1, page_size=limit)
        accounts, cats = _category_maps(db)
        result = []
        for tx in rows:
            acc = accounts.get(tx.account_id)
            result.append(
                {
                    "id": tx.id,
                    "日期": tx.occurred_on.isoformat(),
                    "类型": KIND_ZH.get(tx.kind, tx.kind),
                    "金额": _money(tx.amount),
                    "账户": acc.name if acc else "",
                    "分类": category_path(cats.get(tx.category_id), cats) or "未分类",
                    "备注": tx.note or "",
                }
            )
        return result
