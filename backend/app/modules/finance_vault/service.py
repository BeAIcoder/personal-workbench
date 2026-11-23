"""finance_vault 数据仓服务：DuckDB 嵌入式存储 + SQLite 元数据登记。

设计要点：
- 库文件固定为 get_finance_data_dir()/vault/vault.duckdb（DuckDB 嵌入式，无服务进程）；
- 线程安全：全部 DuckDB 操作使用短连接（开→用→关），写操作经模块级写锁串行化，
  同进程多连接已由 DuckDB 支持，冲突时短退避重试兜底；
- 查询超时：DuckDB 无语句级超时配置，用独立线程对当前连接调用 interrupt() 实现；
- SQL 只读校验：先扫描生成「校验视图」（注释剥离、字符串内容置空）再做
  首关键词/禁词/分号/表名白名单检查，通过后在去注释 SQL 上执行（语义不变）；
- 强制 LIMIT：结果上限 MAX_QUERY_ROWS（内部取上限+1 行用于判断是否截断）。
"""
from __future__ import annotations

import copy
import logging
import math
import re
import threading
import time
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

from ...config import get_finance_data_dir
from ...database import SessionLocal
from ...utils import repair_upload_mojibake
from .models import VaultCleanLog, VaultDataset

try:
    # 单次入仓行数上限由 core 任务提供（app/import_limits.py）；缺失时失败关闭，
    # 绝不放行无上限入仓（与 BIP/税务/聊天 Excel 导入同口径）。
    from ...import_limits import MAX_IMPORT_ROWS
except ImportError:  # pragma: no cover - 集成共享模块后不存在该分支
    MAX_IMPORT_ROWS = None

# ---------- 可调常量（测试可 monkeypatch）----------

MAX_QUERY_ROWS = 1000                  # /query 结果强制上限
PREVIEW_ROWS = 100                     # 预览行数上限
QUERY_TIMEOUT_SEC = 5.0                # DuckDB 层查询超时（秒）
MAX_UPLOAD_BYTES = 20 * 1024 * 1024    # 上传文件大小上限
ALLOWED_SUFFIXES = (".csv", ".xlsx")   # 上传扩展名白名单
MAX_SQL_LEN = 20000                    # SQL 文本长度上限
QUALITY_SCAN_ROW_LIMIT = 50_000        # 质量画像逐表扫描行数上限（超过则抽样标记，不做昂贵 DISTINCT）
QUALITY_TABLE_BUDGET_SEC = 2.0         # 单表质量画像预算（秒），超过即跳过 DISTINCT/describe
QUALITY_TIME_BUDGET_SEC = 5.0          # 质量画像总时间预算（秒），超时即停止后续表
QUALITY_CACHE_TTL_SEC = 60.0           # 质量画像结果缓存（秒），避免 dq 摘要频繁全量扫描

# 表名白名单：仅小写字母/数字/下划线，1-64 字符
_TABLE_NAME_RE = re.compile(r"^[a-z0-9_]{1,64}$")
# 派生物理修订表名：{数据集名}__r{修订号}。基数仍 ≤64，但总长可达 64+__r+数字，
# 因此不能复用 _TABLE_NAME_RE（复核 I-1：61–64 字符数据集曾无法删除）。
_PHYSICAL_NAME_RE = re.compile(r"^[a-z0-9_]{1,64}__r\d{1,9}$")
# 数据引擎保留的表函数/元信息函数名（A09）：数据集名不得与之同名。
# 括号/点号已由 _TABLE_NAME_RE 排除，这里封堵「合法标识符形状的函数名」，
# 避免查询白名单放行后与表函数调用形态混淆（如 FROM read_csv 的歧义）。
_RESERVED_TABLE_FUNCTIONS = frozenset({
    "read_csv", "read_csv_auto", "read_parquet", "read_json", "read_json_auto",
    "read_ndjson", "read_ndjson_auto", "read_blob", "read_text",
    "sniff_csv", "sniff_parquet", "sniff_json",
    "parquet_scan", "parquet_schema", "parquet_metadata", "parquet_file_metadata",
    "delta_scan", "glob", "range", "generate_series", "unnest",
    "query", "query_table", "sqlite_scan", "sqlite_query",
    "postgres_scan", "postgres_query", "mysql_scan", "mysql_query",
    "iceberg_scan", "pragma_table_info",
    "duckdb_tables", "duckdb_columns", "duckdb_functions", "duckdb_settings",
    "duckdb_schemas", "duckdb_databases", "duckdb_types", "duckdb_views",
    "duckdb_constraints", "duckdb_indexes", "duckdb_sequences",
})
# 写操作 / DDL / 系统命令关键词（在校验视图上做词边界匹配；视图已剥离注释与字符串内容）
_FORBIDDEN_RE = re.compile(
    r"\b(?:insert|update|delete|drop|create|alter|truncate|attach|detach|pragma|set|reset|"
    r"copy|export|import|install|load|call|exec|execute|grant|revoke|vacuum|checkpoint|"
    r"show|describe|explain|use|pivot)\b",
    re.IGNORECASE,
)



def _string_literal_spans(text: str) -> list[tuple[int, int]]:
    """返回字符串字面量的字符区间（含首尾引号；'' 为转义引号）。"""
    spans: list[tuple[int, int]] = []
    i, n = 0, len(text)
    while i < n:
        if text[i] != "'":
            i += 1
            continue
        start = i
        i += 1
        while i < n:
            if text[i] == "'":
                if i + 1 < n and text[i + 1] == "'":
                    i += 2
                    continue
                i += 1
                break
            i += 1
        spans.append((start, i))
    return spans


def _paren_depths(text: str) -> tuple[list[int], list[tuple[int, int]]]:
    """返回每个字符位置的括号深度（字符串字面量内的括号不计）与字符串区间。"""
    spans = _string_literal_spans(text)
    depths: list[int] = []
    depth = 0
    span_idx = 0
    for i, ch in enumerate(text):
        while span_idx < len(spans) and i >= spans[span_idx][1]:
            span_idx += 1
        inside = span_idx < len(spans) and spans[span_idx][0] <= i < spans[span_idx][1]
        depths.append(depth)
        if inside:
            continue
        if ch == "(":
            depth += 1
        elif ch == ")" and depth > 0:
            depth -= 1
    return depths, spans


def _top_level_trailing_limit(clean: str) -> tuple[int, str] | None:
    """找括号深度 0 的末尾 LIMIT 子句，返回 (LIMIT 起始位置, 子句尾部文本)。

    子查询/派生表/`IN (SELECT ... LIMIT n)` 里的 LIMIT 深度 >0，不参与判定；
    只有顶层 LIMIT 才需要补/改/拒绝。字符串字面量里的 LIMIT 同样跳过。
    """
    depths, spans = _paren_depths(clean)
    for m in reversed(list(re.finditer(r"\blimit\b", clean, re.IGNORECASE))):
        if any(start <= m.start() < end for start, end in spans):
            continue
        if depths[m.start()] != 0:
            continue
        tail = clean[m.end():].strip()
        # 列名/别名恰好叫 limit（后接 FROM/AS/逗号等）不是 LIMIT 子句
        if not tail or (not re.match(r"[-+.\d]", tail) and tail.upper() != "ALL"):
            continue
        return m.start(), tail
    return None
# 词法 token：带引号标识符 / 字符串 / 单词（含 schema.table 形式）/ 括号 / 逗号 / 其他单字符
_TOKEN_RE = re.compile(r'"(?:[^"]|"")*"|\'[^\']*\'|[a-z_][a-z0-9_.]*|\(|\)|,|\S')
_IDENT_RE = re.compile(r"[a-z_][a-z0-9_.]*")
# FROM/JOIN 上下文遇到这些子句关键词即结束
_CLAUSE_KEYWORDS = {
    "where", "group", "order", "having", "limit", "offset", "window", "qualify",
    "union", "intersect", "except", "on", "using", "returning",
}
# 连接修饰词：位于 FROM/JOIN 与真实表名之间（LEFT/OUTER/CROSS/LATERAL 等）。
# expect_table 遇到它们继续等待表名、after_table 遇到它们切回 expect_table——
# 漏掉会让 JOIN 之后的表引用（含派生表/子查询后的 JOIN）绕过白名单（A09）。
_JOIN_KEYWORDS = {
    "join", "inner", "outer", "left", "right", "full", "cross", "natural",
    "semi", "anti", "lateral", "positional", "asof",
}

# 写操作串行化（同进程多连接 DuckDB 允许，但并发写事务可能冲突，应用层串行最稳）
_write_lock = threading.Lock()

logger = logging.getLogger(__name__)


class SqlRejected(ValueError):
    """SQL 只读校验不通过。"""


class QueryTimeout(TimeoutError):
    """DuckDB 层查询超时（已被 interrupt 中断）。"""


# ---------- 路径与连接 ----------


def _db_path() -> Path:
    """DuckDB 库文件路径：get_finance_data_dir()/vault/vault.duckdb。"""
    return get_finance_data_dir() / "vault" / "vault.duckdb"


def _inbox_dir() -> Path:
    """上传文件留档目录：get_finance_data_dir()/finance_vault/inbox。"""
    return get_finance_data_dir() / "finance_vault" / "inbox"


def _connect() -> duckdb.DuckDBPyConnection:
    """开 DuckDB 短连接（库文件目录自动创建；瞬时锁冲突短退避重试约 6 秒）。"""
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    last: Exception | None = None
    for _ in range(60):
        try:
            return duckdb.connect(str(path))
        except (duckdb.IOException, duckdb.ConnectionException) as exc:
            msg = str(exc).lower()
            if "lock" not in msg and "same database" not in msg:
                raise
            last = exc
            time.sleep(0.1)
    raise duckdb.ConnectionException(f"数据仓文件被占用，请稍后重试（{last}）")


# ---------- 表名 / 数据集登记 ----------


def validate_table_name(table: str) -> str:
    """表名白名单校验：仅小写字母/数字/下划线，1-64 字符，且不得占用引擎表函数名。"""
    name = (table or "").strip()
    if not _TABLE_NAME_RE.fullmatch(name):
        raise ValueError(f"非法表名 {table!r}：仅允许小写字母/数字/下划线，1-64 字符")
    if name in _RESERVED_TABLE_FUNCTIONS:
        raise ValueError(f"非法表名 {table!r}：该名为数据引擎保留的表函数名")
    return name


def _registered_names() -> set[str]:
    """已注册数据集表名集合（SQLite 登记表为唯一权威）。"""
    with SessionLocal() as db:
        return {r.table_name for r in db.query(VaultDataset.table_name).all()}


def _register_dataset(table: str, source: str, rows: int, columns: list[dict]) -> None:
    """把数据集元数据 upsert 进 SQLite 登记表 mod_vault_dataset。"""
    with SessionLocal() as db:
        row = db.query(VaultDataset).filter(VaultDataset.table_name == table).first()
        if row is None:
            row = VaultDataset(table_name=table)
            db.add(row)
        row.source = source[:300]
        row.rows = int(rows)
        row.columns_json = columns
        row.updated_at = datetime.now()
        db.commit()


def _dataset_out(r: VaultDataset) -> dict:
    return {
        "table": r.table_name,
        "source": r.source,
        "rows": r.rows,
        "columns": r.columns_json or [],
        "created_at": r.created_at.strftime("%Y-%m-%d %H:%M:%S") if r.created_at else "",
        "updated_at": r.updated_at.strftime("%Y-%m-%d %H:%M:%S") if r.updated_at else "",
    }


def list_datasets() -> list[dict]:
    """登记表清单（含行数/列信息/注册时间），供 GET /tables 与 Agent 工具复用。"""
    with SessionLocal() as db:
        rows = db.query(VaultDataset).order_by(VaultDataset.table_name.asc()).all()
        return [_dataset_out(r) for r in rows]


# ---------- 数据集生命周期（使用情况 / 删除 / 重命名） ----------


class DatasetConflict(ValueError):
    """数据集状态冲突：目标已存在 / 仍被引用 / 登记与数据仓不一致（路由层转 409）。"""


def _catalog(con: duckdb.DuckDBPyConnection) -> dict[str, str]:
    """DuckDB main schema 目录快照 {对象名: VIEW / BASE TABLE}（常量 SQL，零拼接）。"""
    rows = con.execute(
        "SELECT table_name, table_type FROM information_schema.tables WHERE table_schema = 'main'"
    ).fetchall()
    return {str(name): str(kind) for name, kind in rows}


def _physical_tables(catalog: dict[str, str], table: str) -> list[str]:
    """{table}__r{n} 物理修订表名（按修订号升序）；与 _latest_physical 同口径。

    只认 BASE TABLE：名为 {table}__r9 的「视图」（另一个数据集）不是物理修订表，
    否则重命名会对视图执行 ALTER TABLE、删除会误删别的数据集（复核 M-10）。
    """
    prefix = table + "__r"
    revs = [
        name for name, kind in catalog.items()
        if kind == "BASE TABLE" and name.startswith(prefix) and name[len(prefix):].isdigit()
    ]
    return sorted(revs, key=lambda name: int(name[len(prefix):]))


def dataset_exists(table: str) -> bool:
    """数据集是否已存在：登记表或数据仓任一残留都算（聚合另存/清洗结果冲突判定）。"""
    name = validate_table_name(table)
    if name in _registered_names():
        return True
    con = _connect()
    try:
        catalog = _catalog(con)
    finally:
        con.close()
    return name in catalog or bool(_physical_tables(catalog, name))


def _usage_snapshot(con: duckdb.DuckDBPyConnection, table: str) -> dict:
    """读取数据集使用情况快照；调用方提供已打开连接，便于在写锁内重取（复核 M-10）。

    - registered/view_exists/relation_kind/physical_tables 描述登记与数据仓实际对象；
    - references 汇总清洗日志对它的引用（作为源表/结果表）；
    - consistent=False + issues 表示登记与数据仓不一致（需重新入仓或 force 清理）；
    - requires_force=True 表示被引用或存在未登记残留，删除必须显式 force。
    """
    with SessionLocal() as db:
        row = db.query(VaultDataset).filter(VaultDataset.table_name == table).first()
        refs = db.query(VaultCleanLog).filter(
            (VaultCleanLog.source_table == table) | (VaultCleanLog.result_table == table)
        ).all()
        as_source = sum(1 for r in refs if r.source_table == table)
        as_result = sum(1 for r in refs if r.result_table == table)
        registered = row is not None
        rows = int(row.rows or 0) if row else 0
        columns = list(row.columns_json or []) if row else []
    catalog = _catalog(con)
    relation_kind = catalog.get(table, "")
    view_exists = relation_kind == "VIEW"
    physical = _physical_tables(catalog, table)
    issues: list[str] = []
    if registered and not view_exists:
        issues.append("元数据已登记但数据仓视图缺失（或同名对象不是视图），需重新入仓或删除该登记")
    if not registered and relation_kind:
        issues.append(f"数据仓存在同名对象（{relation_kind}）但未登记元数据，建议删除后重新入仓")
    if not registered and physical and not relation_kind:
        issues.append("数据仓存在未登记的物理修订表残留，建议 force 清理后重新入仓")
    if registered and view_exists and not physical:
        issues.append("已登记数据集未找到物理修订表，视图可能指向其他来源")
    return {
        "table": table,
        "registered": registered,
        "view_exists": view_exists,
        "relation_kind": relation_kind,
        "physical_tables": physical,
        "rows": rows,
        "columns": columns,
        "references": {"clean_logs": len(refs), "as_source": as_source, "as_result": as_result},
        "consistent": not issues,
        "issues": issues,
        "requires_force": bool(refs) or (
            not registered and (bool(relation_kind) or bool(physical))
        ),
        "confirm_name": table,
    }


def dataset_usage(table: str) -> dict:
    """数据集使用情况预检（删除/重命名前二次确认所需信息）；见 _usage_snapshot。"""
    table = validate_table_name(table)
    con = _connect()
    try:
        return _usage_snapshot(con, table)
    finally:
        con.close()


def _assert_relation_name(name: str) -> str:
    """校验要交给 DDL 的标识符：数据集名（≤64）或派生修订表名（base≤64 + __r<数字>）。

    派生物理名总长可超过 64（64 字符数据集 → 68 字符 {table}__r1），不能复用
    _TABLE_NAME_RE；DROP/ALTER 前一律先过这里，绝不把调用方原文拼进 SQL（复核 I-1）。
    """
    text = name or ""
    if _TABLE_NAME_RE.fullmatch(text) or _PHYSICAL_NAME_RE.fullmatch(text):
        return text
    raise ValueError(
        f"非法表名 {name!r}：仅允许小写字母/数字/下划线（≤64），或派生的 __r<数字> 物理修订表名"
    )


def _drop_relation(con: duckdb.DuckDBPyConnection, name: str, kind: str) -> None:
    """按校验后的标识符 DROP 视图/物理表（含派生修订名），绝不拼接调用方原文。"""
    _assert_relation_name(name)
    verb = "VIEW" if kind == "VIEW" else "TABLE"
    con.execute(f"DROP {verb} IF EXISTS {_quote_ident(name)}")


def _rename_physical_table(con: duckdb.DuckDBPyConnection, old_name: str, new_name: str) -> None:
    """把单个物理修订表改名为新名（两端标识符都须通过白名单）。"""
    _assert_relation_name(old_name)
    _assert_relation_name(new_name)
    con.execute(f"ALTER TABLE {_quote_ident(old_name)} RENAME TO {_quote_ident(new_name)}")


def _create_dataset_view(con: duckdb.DuckDBPyConnection, table: str) -> None:
    """以最新物理修订表（重）建数据集视图；replace=True 保证回滚幂等。"""
    latest = _latest_physical(con, table)
    if latest is None:
        raise DatasetConflict(f"重命名失败：未找到 {table} 的物理修订表")
    con.table(latest).to_view(table, replace=True)


def _name_token_pattern(name: str) -> re.Pattern:
    """按标识符边界匹配旧名（tok_src 不会命中 tok_src_extra）。"""
    return re.compile(rf"(?<![a-z0-9_]){re.escape(name)}(?![a-z0-9_])")


def _rename_metadata(table: str, new_table: str) -> tuple[int, int]:
    """SQLite 侧改名：登记行 + 清洗日志引用 + 其它数据集来源文案。

    返回 (改写的日志条数, 改写的来源文案数据集数)。登记行缺失抛 LookupError；
    唯一约束冲突等异常上抛，由 rename_dataset 反向补偿 DuckDB（复核 I-2）。
    """
    pattern = _name_token_pattern(table)
    with SessionLocal() as db:
        row = db.query(VaultDataset).filter(VaultDataset.table_name == table).first()
        if row is None:
            raise LookupError(f"数据集未注册：{table}")
        row.table_name = new_table
        row.updated_at = datetime.now()
        logs = db.query(VaultCleanLog).filter(
            (VaultCleanLog.source_table == table) | (VaultCleanLog.result_table == table)
        ).all()
        for log in logs:
            if log.source_table == table:
                log.source_table = new_table
            if log.result_table == table:
                log.result_table = new_table
        rewritten_sources = 0
        for ds in db.query(VaultDataset).all():
            text = ds.source or ""
            if table not in text:
                continue
            updated = pattern.sub(new_table, text)
            if updated != text:
                ds.source = updated[:300]
                rewritten_sources += 1
        db.commit()
        return len(logs), rewritten_sources


def _rollback_rename(
    con: duckdb.DuckDBPyConnection,
    table: str,
    new_table: str,
    renamed: list[tuple[str, str]],
    old_latest: str,
) -> list[str]:
    """反向补偿重命名：删新视图 → 物理表改回原名 → 用旧最新修订重建旧视图。

    尽力而为但绝不吞掉主异常：返回失败说明，调用方记录日志后继续上抛原异常。
    """
    errors: list[str] = []
    try:
        _drop_relation(con, new_table, "VIEW")
    except Exception as exc:  # 新视图可能还没建成，删除失败不阻断后续补偿
        errors.append(f"删除新视图 {new_table} 失败：{exc}")
    for old_phys, new_phys in reversed(renamed):
        try:
            _rename_physical_table(con, new_phys, old_phys)
        except Exception as exc:
            errors.append(f"还原物理表 {new_phys} 失败：{exc}")
    # 一律回到原「最新修订」：部分改名失败时，未改名的更高修订才是数据真相。
    if old_latest:
        try:
            con.table(old_latest).to_view(table, replace=True)
        except Exception as exc:
            errors.append(f"重建视图 {table} 失败：{exc}")
    return errors


def delete_dataset(table: str, confirm: str = "", force: bool = False) -> dict:
    """安全删除数据集：二次确认 + 引用检查 + 物理表与元数据同步清理。

    - confirm 必须与数据集名完全一致（防止误删其他数据集）；
    - 被清洗日志引用、或仅有未登记残留时默认拒绝（DatasetConflict），force=True 才继续；
    - 引用/残留快照在 _write_lock 内重取，避免「校验后被并发入仓/清洗」的窗口（复核 M-10）；
    - 先对全部目标名做白名单校验再逐个 DROP：名字异常时整体失败，不留半删状态
      （复核 I-1）；物理对象全部删完才删 SQLite 登记行，半途失败可再次删除修复。
    """
    table = validate_table_name(table)
    confirm = (confirm or "").strip()
    if not confirm:
        raise ValueError("删除数据集需要二次确认：请提供 confirm=<数据集名>")
    if confirm != table:
        raise ValueError(f"删除确认不一致：confirm 必须等于数据集名（{table}）")
    with _write_lock:
        con = _connect()
        try:
            usage = _usage_snapshot(con, table)
            if (
                not usage["registered"]
                and not usage["relation_kind"]
                and not usage["physical_tables"]
            ):
                raise LookupError(f"数据集未注册：{table}")
            if usage["references"]["clean_logs"] and not force:
                raise DatasetConflict(
                    f"数据集被 {usage['references']['clean_logs']} 条清洗日志引用，"
                    "删除后历史配方将失去来源；确认后请带 force=true 重试"
                )
            if not usage["registered"] and not force:
                raise DatasetConflict(
                    f"数据集 {table} 仅有未登记的视图/物理表残留，清理请带 force=true 重试"
                )
            targets: list[tuple[str, str]] = []
            if usage["relation_kind"] in ("VIEW", "BASE TABLE"):
                targets.append((table, usage["relation_kind"]))
            targets.extend((phys, "BASE TABLE") for phys in usage["physical_tables"])
            for name, _kind in targets:  # 先全量校验，再 DROP
                _assert_relation_name(name)
            dropped_physical = 0
            for name, kind in targets:
                _drop_relation(con, name, kind)
                if kind != "VIEW":
                    dropped_physical += 1
            metadata_only = (
                usage["registered"] and not usage["relation_kind"] and not usage["physical_tables"]
            )
            with SessionLocal() as db:
                db.query(VaultDataset).filter(VaultDataset.table_name == table).delete()
                db.commit()
        finally:
            con.close()
    return {
        "deleted": True,
        "table": table,
        "dropped_physical": dropped_physical,
        "metadata_only": metadata_only,
    }


def rename_dataset(table: str, new_table: str) -> dict:
    """重命名数据集：视图/物理修订表/登记元数据/清洗日志引用/来源文案同步改名。

    - 不覆盖已有数据：目标已登记、有同名视图或 BASE TABLE、或有同名物理修订表一律
      DatasetConflict；
    - 源/目标状态与 DDL 都在 _write_lock 内用同一连接重取，缩小并发窗口（复核 M-10）；
    - DuckDB 阶段或 SQLite 元数据阶段失败都反向补偿（物理表改回 + 重建旧视图），
      旧名保持可查询、新名不留半成品（复核 I-2）。
    """
    table = validate_table_name(table)
    new_table = validate_table_name(new_table)
    if table == new_table:
        raise ValueError("新数据集名与原名称相同")
    renamed: list[tuple[str, str]] = []
    stage = "check"
    with _write_lock:
        con = _connect()
        try:
            usage = _usage_snapshot(con, table)
            if not usage["registered"]:
                raise LookupError(f"数据集未注册：{table}")
            if not usage["view_exists"]:
                raise DatasetConflict(f"数据集视图不可用（登记与数据仓不一致），请删除后重新入仓：{table}")
            if not usage["physical_tables"]:
                raise DatasetConflict(f"数据集缺少物理修订表，无法重命名：{table}")
            target = _usage_snapshot(con, new_table)
            if target["registered"] or target["relation_kind"] or target["physical_tables"]:
                raise DatasetConflict(f"目标数据集已存在：{new_table}（重命名不覆盖已有数据）")
            new_physicals = [new_table + phys[len(table):] for phys in usage["physical_tables"]]
            for name in [new_table, *new_physicals]:  # 派生长名也先全量校验
                _assert_relation_name(name)
            old_latest = usage["physical_tables"][-1]
            stage = "ddl"
            _drop_relation(con, table, "VIEW")
            for phys, new_phys in zip(usage["physical_tables"], new_physicals):
                _rename_physical_table(con, phys, new_phys)
                renamed.append((phys, new_phys))
            _create_dataset_view(con, new_table)
            stage = "metadata"
            rewritten_logs, rewritten_sources = _rename_metadata(table, new_table)
        except Exception as exc:
            if stage != "check":
                errors = _rollback_rename(con, table, new_table, renamed, old_latest)
                if errors:
                    logger.warning(
                        "重命名 %s→%s 回滚未完全成功：%s", table, new_table, "；".join(errors)
                    )
            if stage == "metadata":
                raise DatasetConflict(
                    f"重命名失败（元数据更新异常），已回滚为 {table}：{exc}"
                ) from exc
            raise
        finally:
            con.close()
    return {
        "table": new_table,
        "old_table": table,
        "physical_tables": new_physicals,
        "rewritten_logs": rewritten_logs,
        "rewritten_sources": rewritten_sources,
    }


_QUALITY_CACHE: dict[str, Any] = {"key": None, "at": 0.0, "value": None}


def _quality_cache_key(datasets: list[dict]) -> tuple:
    # updated_at 只到秒：同一秒内重建数据仓会命中旧缓存；叠加 DuckDB 文件 mtime_ns
    # 作为亚秒版本，文件不存在/不可读时退回 0（仍按登记表签名缓存）。
    try:
        mtime_ns = _db_path().stat().st_mtime_ns
    except OSError:
        mtime_ns = 0
    return (mtime_ns, tuple((d["table"], d.get("rows"), d.get("updated_at")) for d in datasets))


def vault_quality_overview(
    max_tables: int = 30,
    null_threshold: float = 0.3,
    time_budget: float | None = None,
) -> dict:
    """数据仓质量画像（只读、有界执行，2026-11-02 / Task 5 加固）。

    对每个已登记数据集做三件事：整行重复计数（总行数 - DISTINCT 行数）、
    高缺失列（describe 的 null_percentage 超阈值）、空表标记。
    全部走 DuckDB 关系 API 与常量 SQL，不拼接任何列名/数据值；单表失败不影响其他表。

    有界性：单表超过 QUALITY_SCAN_ROW_LIMIT 只取行数并标记 sampled（不做昂贵
    DISTINCT/describe）；单表耗时超过 QUALITY_TABLE_BUDGET_SEC 同样跳过昂贵统计；
    总耗时超过 time_budget（默认 QUALITY_TIME_BUDGET_SEC）立即停止后续表并给出
    gap_reasons；完整结果按登记表签名缓存 TTL 秒且返回深拷贝，避免调用方改写
    污染缓存（L-090 / M9）。
    """
    datasets = list_datasets()[:max_tables]
    if not datasets:
        return {"available": False, "gap_reasons": ["数据仓暂无登记数据集"], "tables": [],
                "summary": {"datasets": 0, "empty_tables": 0, "dup_tables": 0, "null_heavy_tables": 0}}
    budget = QUALITY_TIME_BUDGET_SEC if time_budget is None else max(0.0, time_budget)
    key = _quality_cache_key(datasets)
    now = time.monotonic()
    if (
        budget >= QUALITY_TIME_BUDGET_SEC
        and _QUALITY_CACHE["key"] == key
        and (now - _QUALITY_CACHE["at"]) < QUALITY_CACHE_TTL_SEC
    ):
        return copy.deepcopy(_QUALITY_CACHE["value"])
    deadline = now + budget
    out: list[dict] = []
    gap_reasons: list[str] = []
    empty = dup_tables = null_heavy = 0
    truncated = False
    with _write_lock:
        con = _connect()
        try:
            for ds in datasets:
                if time.monotonic() > deadline:
                    truncated = True
                    gap_reasons.append(
                        f"质量画像超过 {budget:g} 秒预算，剩余 {len(datasets) - len(out)} 个数据集未扫描"
                    )
                    break
                item = {"table": ds["table"], "rows": ds.get("rows") or 0,
                        "dup_rows": 0, "null_heavy": [], "error": "", "sampled": False}
                table_start = time.monotonic()
                try:
                    rel = con.table(ds["table"])
                    total = int(rel.aggregate("count(*)").fetchone()[0])
                    item["rows"] = total
                    table_elapsed = time.monotonic() - table_start
                    over_budget = table_elapsed > QUALITY_TABLE_BUDGET_SEC or time.monotonic() > deadline
                    if total > QUALITY_SCAN_ROW_LIMIT or over_budget:
                        item["sampled"] = True
                        item["dup_rows"] = None
                        truncated = True
                        reason = (
                            f"共 {total} 行，超过单表扫描上限 {QUALITY_SCAN_ROW_LIMIT}"
                            if total > QUALITY_SCAN_ROW_LIMIT
                            else f"单表画像耗时 {table_elapsed:.2f}s 超过预算 {QUALITY_TABLE_BUDGET_SEC:g}s"
                        )
                        gap_reasons.append(f"数据集 {ds['table']} {reason}，已跳过重复/缺失统计")
                    else:
                        distinct_rows = int(rel.distinct().aggregate("count(*)").fetchone()[0])
                        item["dup_rows"] = max(0, total - distinct_rows)
                        desc = rel.describe().df()
                        if "aggr" in desc.columns:
                            cnt_row = desc[desc["aggr"] == "count"]
                            if not cnt_row.empty:
                                for col in [c for c in desc.columns if c != "aggr"]:
                                    non_null = cnt_row.iloc[0][col]
                                    if non_null is None:
                                        continue
                                    null_pct = max(0.0, (1 - float(non_null) / total) * 100) if total else 0.0
                                    if null_pct > null_threshold * 100:
                                        item["null_heavy"].append({"column": str(col), "null_pct": round(null_pct, 1)})
                except Exception as exc:  # 单表失败不拖垮整体画像
                    item["error"] = type(exc).__name__
                if item["rows"] == 0:
                    empty += 1
                if item["dup_rows"] and item["dup_rows"] > 0:
                    dup_tables += 1
                if item["null_heavy"]:
                    null_heavy += 1
                out.append(item)
        finally:
            con.close()
    result = {
        "available": True,
        "gap_reasons": gap_reasons,
        "truncated": truncated,
        "tables": out,
        "summary": {"datasets": len(out), "empty_tables": empty,
                    "dup_tables": dup_tables, "null_heavy_tables": null_heavy},
    }
    if not truncated and budget >= QUALITY_TIME_BUDGET_SEC:
        _QUALITY_CACHE.update(key=key, at=time.monotonic(), value=copy.deepcopy(result))
    return result


# ---------- 入仓 ----------


def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """列名规范化：转字符串、去空白、空名补 col_<序号>、重名补 _2/_3 后缀。"""
    used: set[str] = set()
    cols: list[str] = []
    for i, c in enumerate(df.columns):
        base = str(c).strip() or f"col_{i + 1}"
        name, k = base, 1
        while name in used:
            k += 1
            name = f"{base}_{k}"
        used.add(name)
        cols.append(name)
    out = df.copy()
    out.columns = cols
    return out


def _restore_after_register_failure(
    con: duckdb.DuckDBPyConnection,
    table: str,
    mode: str,
    previous_latest: str | None,
    created_physical: str | None,
) -> None:
    """登记写失败时回滚 DuckDB 侧（调用方持写锁），避免「数据在、元数据不在」。

    - replace / 新建：视图指回旧修订（若有），并删除本次新建的物理修订表；
    - append 到既有表：追加已写入数据，保留（重试按整行去重），仅告警行数待修正。
    """
    try:
        if mode == "append" and previous_latest:
            logger.warning("数据仓登记失败：追加数据已写入 %s，登记行数待下次成功入仓修正", table)
            return
        if previous_latest:
            con.table(previous_latest).to_view(table, replace=True)
        else:
            _drop_relation(con, table, "VIEW")
        if created_physical:
            _drop_relation(con, created_physical, "BASE TABLE")
    except Exception as exc:  # 回滚尽力而为，绝不掩盖登记失败的原始异常
        logger.warning("数据仓登记失败后的回滚未完全成功（%s）：%s", table, exc)


def _discard_unreferenced_revision(con: duckdb.DuckDBPyConnection, physical: str) -> None:
    """丢弃本次新建但未被视图引用的物理修订表（失败只记日志，不掩盖主异常）。"""
    try:
        _drop_relation(con, physical, "BASE TABLE")
    except Exception as exc:  # pragma: no cover - 防御路径
        logger.warning("清理未引用的物理修订表 %s 失败：%s", physical, exc)


def ingest_dataframe(table: str, df, mode: str = "replace", source: str = "") -> dict:
    """模块间契约：把 DataFrame 入仓为 DuckDB 表，并登记到 mod_vault_dataset。

    - table：目标表名（仅小写字母/数字/下划线，1-64 字符，且不得占用引擎表函数名）；
    - df：pandas.DataFrame（空列名/重名自动规范化；非 DataFrame 输入尝试转换）；
    - mode：replace（整表重建，默认）/ append（按列名 BY NAME 追加，表不存在则创建）；
    - source：来源说明（写入登记表，留空记为 dataframe）；
    - 行数护栏：数据行数超过共享上限 MAX_IMPORT_ROWS 即拒绝，不落库、不留半成品。

    参数校验在锁外完成，DDL 与登记在 `_ingest_dataframe_locked` 的同一写锁内完成；
    已有写锁的调用方（clean_apply / save_aggregate_as_dataset）应直接调内部 helper。

    返回 {"table": table, "rows": 入仓后总行数, "columns": 列信息}；参数非法抛 ValueError。
    """
    table = validate_table_name(table)
    if mode not in ("replace", "append"):
        raise ValueError(f"不支持的入仓模式 {mode!r}（仅 replace / append）")
    if not isinstance(df, pd.DataFrame):
        df = pd.DataFrame(df)
    df = _normalize_columns(df)
    if MAX_IMPORT_ROWS is None:
        raise ValueError("导入行数上限服务暂不可用，请稍后重试")
    if len(df) > MAX_IMPORT_ROWS:
        raise ValueError(f"数据行数（{len(df)}）超过单次导入上限（{MAX_IMPORT_ROWS} 行），请拆分后导入")
    with _write_lock:
        return _ingest_dataframe_locked(table, df, mode, source)


def _ingest_dataframe_locked(table: str, df: pd.DataFrame, mode: str, source: str) -> dict:
    """入仓的锁内实现：**调用方必须已持有 _write_lock**（内部不再取锁，避免重入死锁）。

    table / df / mode 由调用方完成校验（表名白名单、列名规范化、行数护栏）；本函数负责
    视图/物理表写入 + 登记，两者同处调用方的写锁临界区，删除/重命名无法插进中间。
    """
    con = _connect()
    latest: str | None = None
    created_physical: str | None = None
    try:
        con.register("staging_df", df)
        latest = _latest_physical(con, table)
        if mode == "append" and latest:
            # append：按列名追加；整行相同的已在库行跳过（2026-11-02 数据清洗强化）
            skipped = _append_deduped(con, latest, df)
        else:
            skipped = 0
            # 物理“修订表”版本化 + 用户名固定为视图：数据落到新物理修订表（表名作
            # API 参数），用户名经 to_view(replace=True) 原子换装——全程零 DDL 文本
            # 拼接。修订号自目录枚举推算；旧修订暂不回收（嵌入式单文件库，量级可忽略）。
            # 注：库目录为空时上线，无同名 TABLE 迁移问题（视图不能替换同名 TABLE）。
            rev = _physical_rev(latest) + 1
            phys = f"{table}__r{rev}"
            con.sql("SELECT * FROM staging_df").to_table(phys)
            created_physical = phys
            con.table(phys).to_view(table, replace=True)
        rel = con.table(table)
        rows = rel.aggregate("count(*)").fetchone()[0]
        cols = [{"name": c, "type": str(t)} for c, t in zip(rel.columns, rel.types)]
        # 登记与视图/物理表写入同处一个写锁临界区：删除/重命名无法插进
        # 「数据已写好、登记还没落库」的窗口（复核残余边界）；登记失败回滚 DuckDB。
        try:
            _register_dataset(table, source=source or "dataframe", rows=rows, columns=cols)
        except Exception:
            _restore_after_register_failure(con, table, mode, latest, created_physical)
            raise
    except ValueError:
        raise
    except duckdb.Error as exc:
        # 列不匹配等 DuckDB 层错误属于数据/参数问题：降级为 ValueError（路由层转 400），不裸抛 500；
        # DDL 可能已建出新修订表（如视图名被 BASE TABLE 占用），失败时丢弃该未引用修订
        if created_physical:
            _discard_unreferenced_revision(con, created_physical)
        raise ValueError(f"入仓失败：{exc}") from exc
    except Exception:
        if created_physical:
            _discard_unreferenced_revision(con, created_physical)
        raise
    finally:
        con.close()
    return {"table": table, "rows": rows, "columns": cols, "skipped_duplicates": skipped}


def _quote_ident(name: str) -> str:
    """SQL 标识符安全引用：双引号包裹，内嵌双引号转义为两个。"""
    return '"' + str(name).replace('"', '""') + '"'


def _append_deduped(con, physical: str, df) -> int:
    """按列名追加并跳过整行重复数据；返回跳过行数。

    列集合与库中不一致时退回 by_name 直追（历史兼容），不去重。
    去重用 DuckDB 关系 API 的抗连接（anti join），条件里的列名一律经
    _quote_ident 转义，不拼接任何数据值。
    """
    stg = con.sql("SELECT * FROM staging_df")
    cur = con.table(physical)
    cols = list(stg.columns)
    if set(cols) != set(cur.columns):
        con.append(physical, df, by_name=True)
        return 0
    cond = " AND ".join(f'"__stg".{_quote_ident(c)} = "__cur".{_quote_ident(c)}' for c in cols)
    left = stg.set_alias("__stg")
    right = cur.set_alias("__cur")
    diff = left.join(right, cond, how="anti")
    add_rows = int(diff.aggregate("count(*)").fetchone()[0])
    skipped = max(0, len(df) - add_rows)
    if add_rows:
        con.append(physical, diff.df(), by_name=True)
    return skipped


def _physical_rev(phys: str | None) -> int:
    """物理修订表名 {table}__r{n} 的修订号；无物理表时返回 0。"""
    if not phys:
        return 0
    return int(phys.rsplit("__r", 1)[1])


def _latest_physical(con: duckdb.DuckDBPyConnection, table: str) -> "str | None":
    """枚举目录推算最新物理修订表（常量 SQL + Python 侧过滤，零拼接）。

    用户名在目录中恒为视图（{table}），物理数据表为 {table}__r{n}；
    只认 BASE TABLE：名为 {table}__r9 的「视图」（另一数据集）不算修订（复核 M-10）。
    """
    rows = con.execute(
        "SELECT table_name, table_type FROM information_schema.tables WHERE table_schema = 'main'"
    ).fetchall()
    prefix = table + "__r"
    revs = [
        str(name) for name, kind in rows
        if str(kind) == "BASE TABLE" and str(name).startswith(prefix)
        and str(name)[len(prefix):].isdigit()
    ]
    return max(revs, key=lambda t: int(t[len(prefix):])) if revs else None


def _excel_sheet_names(path: Path) -> list[str]:
    with pd.ExcelFile(path) as xls:
        return [str(name) for name in xls.sheet_names]


def _resolve_sheet(sheet_names: list[str], sheet: str) -> tuple[int | str, str]:
    """工作表参数解析：名字优先（含纯数字名），其次 0-based 序号。

    旧实现把纯数字参数一律当索引，名为「2026」的工作表无法直接指定（L-091）。
    """
    text = (sheet or "").strip()
    if not text:
        return 0, sheet_names[0] if sheet_names else ""
    if text in sheet_names:
        return text, text
    if text.isdigit():
        index = int(text)
        if 0 <= index < len(sheet_names):
            return index, sheet_names[index]
    raise ValueError(
        f"工作表不存在：{text}（可选：{'、'.join(sheet_names[:20]) or '无'}）"
    )


def _coerce_text_frame(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """保留文本读取后的安全类型推断：只把「纯数值且无前导零/未超 15 位」的列转数值。

    这样账号（18 位）、编码（007）、期间（2026-10）等保持文本，金额/数量列仍可
    直接 SUM；无法判断的一律保守保留为文本。
    """
    out = df.copy()
    text_columns: list[str] = []
    for col in out.columns:
        series = out[col]
        values = [str(v).strip() for v in series if pd.notna(v) and str(v).strip() != ""]
        if not values:
            continue
        # 前导零规则只适用于纯整数编码（007 / 0012345）；0.13 / 0.5 是正常小数，必须可 SUM
        if any(
            re.fullmatch(r"-?\d+", v) and v.lstrip("-").startswith("0") and len(v.lstrip("-")) > 1
            for v in values
        ):
            text_columns.append(str(col))
            continue
        if all(re.fullmatch(r"-?\d{1,15}(\.\d+)?", v) for v in values):
            out[col] = pd.to_numeric(series, errors="coerce")
        else:
            text_columns.append(str(col))
    return out, text_columns


def _read_table_file(
    path: Path,
    sheet: str = "",
    keep_text: bool = True,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """把 xlsx/csv 读成 DataFrame；CSV 依次尝试 utf-8-sig / gbk 编码。

    keep_text=True（默认）：先按文本读取，保留前导零/长账号/期间等编码，再对
    纯数值列做保守类型推断（见 _coerce_text_frame），避免 pandas 自动推断把
    `007` 变 7、18 位账号变科学计数、`2026-10` 变日期（L-086）。

    解析异常统一降级为 ValueError（路由层转 400）：损坏文件（如 ZIP 魔数残缺的
    BadZipFile、pandas 引擎异常）属于客户端错误，不应裸抛成 500。
    """
    suffix = path.suffix.lower()
    info: dict[str, Any] = {"sheet": "", "sheets": [], "sheet_count": 0, "text_columns": []}
    try:
        if suffix == ".xlsx":
            sheet_names = _excel_sheet_names(path)
            selector, used = _resolve_sheet(sheet_names, sheet)
            df = pd.read_excel(path, sheet_name=selector, dtype=str if keep_text else None)
            info.update(sheet=used, sheets=sheet_names, sheet_count=len(sheet_names))
        elif suffix == ".csv":
            df = None
            for enc in ("utf-8-sig", "gbk"):
                try:
                    if keep_text:
                        df = pd.read_csv(path, encoding=enc, dtype=str, keep_default_na=False)
                    else:
                        df = pd.read_csv(path, encoding=enc)
                    break
                except UnicodeDecodeError:
                    continue
            if df is None:
                raise ValueError("无法识别 CSV 编码（仅支持 UTF-8 / GBK）")
        else:
            raise ValueError(f"不支持的文件类型：{suffix}")
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(f"文件解析失败：{type(exc).__name__}: {exc}") from exc
    if keep_text:
        df, text_columns = _coerce_text_frame(df)
        info["text_columns"] = text_columns
    else:
        info["text_columns"] = []
    return df, info


def ingest_uploaded_file(
    table: str,
    filename: str,
    content: bytes,
    mode: str = "replace",
    sheet: str = "",
    keep_text: bool = True,
) -> dict:
    """上传文件入仓：扩展名白名单 + 大小限制 + 防路径穿越 + 落 inbox 留档后解析。

    keep_text=True（默认）保留编码/账号等文本形态（见 _read_table_file）。
    """
    table = validate_table_name(table)
    if mode not in ("replace", "append"):
        raise ValueError(f"不支持的入仓模式 {mode!r}（仅 replace / append）")
    safe_name = Path(repair_upload_mojibake(filename or "")).name
    if safe_name in ("", ".", ".."):
        raise ValueError("非法的文件名")
    suffix = Path(safe_name).suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise ValueError(f"仅支持 {' / '.join(ALLOWED_SUFFIXES)} 文件")
    if len(content) > MAX_UPLOAD_BYTES:
        raise ValueError(f"文件超过 {MAX_UPLOAD_BYTES // 1024 // 1024}MB 上限")

    inbox = _inbox_dir()
    inbox.mkdir(parents=True, exist_ok=True)
    dest = inbox / f"{datetime.now():%Y%m%d_%H%M%S_%f}_{safe_name}"
    if dest.resolve().parent != inbox.resolve():
        raise ValueError("非法的文件名")
    dest.write_bytes(content)
    try:
        df, info = _read_table_file(dest, sheet=sheet, keep_text=keep_text)
        result = ingest_dataframe(table, df, mode=mode, source=safe_name)
    except Exception:
        dest.unlink(missing_ok=True)  # 失败不留档，避免 inbox 堆积垃圾
        raise
    return {
        "table": result["table"],
        "rows": result["rows"],
        "source": safe_name,
        "mode": mode,
        # 列信息复用入仓返回值：不再锁外二次查登记表（并发删除时不会读到空）
        "columns": result.get("columns") or [],
        "keep_text": keep_text,
        **info,
    }


# ---------- 只读 SQL 校验 ----------


def _scan_sql(sql: str) -> tuple[str, str]:
    """一遍扫描产出两个变体（位置不保证对齐，仅供各自独立分析/执行）：

    - 校验视图：注释剥离、字符串内容置空（保留引号），供关键词/表名检查；
    - 执行文本：仅剥离注释（字符串原样保留），供实际执行（语义不变）。
    """
    view: list[str] = []
    clean: list[str] = []
    i, n = 0, len(sql)
    while i < n:
        ch = sql[i]
        nxt = sql[i + 1] if i + 1 < n else ""
        if ch == "-" and nxt == "-":  # 行注释：剥到行尾（换行符留给常规分支）
            while i < n and sql[i] != "\n":
                i += 1
            continue
        if ch == "/" and nxt == "*":  # 块注释：剥到 */，补一个空格防粘连
            i += 2
            while i + 1 < n and not (sql[i] == "*" and sql[i + 1] == "/"):
                i += 1
            i = min(i + 2, n)
            view.append(" ")
            clean.append(" ")
            continue
        if ch == "'":  # 字符串字面量：视图置空内容（'' 为转义），执行文本原样
            view.append("'")
            clean.append("'")
            i += 1
            closed = False
            while i < n:
                if sql[i] == "'":
                    if i + 1 < n and sql[i + 1] == "'":
                        view.append("  ")
                        clean.append("''")
                        i += 2
                        continue
                    view.append("'")
                    clean.append("'")
                    i += 1
                    closed = True
                    break
                view.append("\n" if sql[i] == "\n" else " ")
                clean.append(sql[i])
                i += 1
            if not closed:  # 未闭合：补齐引号，交给 DuckDB 解析器报错
                view.append("'")
                clean.append("'")
            continue
        view.append(ch)
        clean.append(ch)
        i += 1
    return "".join(view), "".join(clean)


def _collect_cte_names(tokens: list[str]) -> set[str]:
    """识别 CTE 定义名：`WITH x AS (`、`WITH x(a) AS (`、`WITH x AS MATERIALIZED (`。"""
    ctes: set[str] = set()
    n = len(tokens)
    for i, tok in enumerate(tokens):
        if tok != "as":
            continue
        j = i + 1
        if j < n and tokens[j] == "materialized":
            j += 1
        elif j + 1 < n and tokens[j] == "not" and tokens[j + 1] == "materialized":
            j += 2
        if j >= n or tokens[j] != "(":
            continue
        k = i - 1
        if k >= 0 and tokens[k] == ")":  # 列清单 x(a, b)：向前找匹配的 (
            depth = 0
            while k >= 0:
                if tokens[k] == ")":
                    depth += 1
                elif tokens[k] == "(":
                    depth -= 1
                    if depth == 0:
                        break
                k -= 1
            k -= 1
        if k >= 0 and _IDENT_RE.fullmatch(tokens[k]):
            ctes.add(tokens[k].split(".")[-1])
    return ctes


def _extract_refs_and_ctes(view: str) -> tuple[set[str], set[str]]:
    """从校验视图（转小写后）提取 FROM/JOIN 表引用集合与 CTE 别名集合。

    逐 token 走状态机（idle → expect_table → after_table），覆盖：
    显式 JOIN（含 LEFT/RIGHT/FULL/CROSS/NATURAL/LATERAL 等修饰词）、逗号连接
    （FROM a, b）、派生表/子查询（括号栈恢复上下文）、FROM-first 语法。
    FROM/JOIN 后直接跟字符串（文件路径表）抛 SqlRejected。
    """
    tokens = _TOKEN_RE.findall(view.lower())
    refs: set[str] = set()
    ctes = _collect_cte_names(tokens)
    paren_stack: list[str] = []
    state = "idle"
    i, n = 0, len(tokens)
    while i < n:
        tok = tokens[i]
        if tok == "(":
            # CTE 别名定义：<ident> AS (
            if i >= 2 and tokens[i - 1] == "as" and _IDENT_RE.fullmatch(tokens[i - 2]):
                ctes.add(tokens[i - 2].split(".")[-1])
            paren_stack.append(state)
            if state == "expect_table":
                state = "idle"  # 派生表/子查询内部独立走状态机
            i += 1
            continue
        if tok == ")":
            prev = paren_stack.pop() if paren_stack else "idle"
            # 子查询结束后回到「刚消费完一个表」状态（外层逗号可继续列举表）
            state = "after_table" if prev == "expect_table" else prev
            i += 1
            continue
        if state == "expect_table":
            if tok in _JOIN_KEYWORDS:
                i += 1
                continue
            if tok.startswith("'"):
                raise SqlRejected("不允许从文件路径读取，仅允许已注册数据集")
            if tok.startswith('"'):
                refs.add(tok.strip('"').split(".")[-1])
                state = "after_table"
            elif _IDENT_RE.fullmatch(tok):
                refs.add(tok.split(".")[-1])
                state = "after_table"
            else:
                state = "idle"
            i += 1
            continue
        if state == "after_table":
            if tok == ",":
                state = "expect_table"
            elif tok in _JOIN_KEYWORDS:
                state = "expect_table"
            elif tok in _CLAUSE_KEYWORDS:
                state = "idle"
            # 其余 token（表别名、AS 等）忽略
            i += 1
            continue
        # idle：发现 FROM / JOIN 进入 expect_table
        if tok in ("from", "join"):
            state = "expect_table"
        i += 1
    return refs, ctes


def validate_readonly_sql(sql: str, allowed_tables: set[str]) -> str:
    """校验只读 SQL，返回可执行文本（去注释、必要时补/改 LIMIT）。

    规则：单条 SELECT/WITH；禁分号多语句；禁写操作/DDL/系统命令关键词与文件路径表；
    FROM/JOIN 引用必须命中已注册数据集（或本语句定义的 CTE 别名）；
    强制 LIMIT ≤ MAX_QUERY_ROWS（取上限+1 供截断判断）。校验失败抛 SqlRejected。
    """
    raw = (sql or "").strip()
    if not raw:
        raise SqlRejected("SQL 不能为空")
    if len(raw) > MAX_SQL_LEN:
        raise SqlRejected(f"SQL 过长（上限 {MAX_SQL_LEN} 字符）")
    view, clean = _scan_sql(raw)
    view = view.strip()
    clean = clean.strip()
    # 仅允许末尾一个分号（其后不得再有内容）
    if view.endswith(";"):
        view = view[:-1].rstrip()
        clean = clean[:-1].rstrip()
    if ";" in view:
        raise SqlRejected("只允许单条查询语句（禁止分号多语句）")
    head = re.match(r"[a-zA-Z]+", view)
    if not head or head.group(0).lower() not in ("select", "with", "from"):
        raise SqlRejected("只允许 SELECT / WITH / FROM 开头的只读查询")
    hit = _FORBIDDEN_RE.search(view)
    if hit:
        raise SqlRejected(f"只允许只读查询，检测到禁止的关键词：{hit.group(0).upper()}")
    refs, ctes = _extract_refs_and_ctes(view)
    allowed = {t.lower() for t in allowed_tables}
    unknown = sorted(refs - allowed - ctes)
    if unknown:
        raise SqlRejected(f"引用了未注册的数据集：{'、'.join(unknown)}")
    # 强制 LIMIT：无顶层 LIMIT 补上；超出上限改写为上限+1。
    # 只在括号深度 0 的顶层判定：子查询/派生表里已有的 LIMIT 不参与，
    # 否则 SELECT * FROM (SELECT ... LIMIT 5) 会被误判并被拒绝（Fix round 1 / I1）。
    info = _top_level_trailing_limit(clean)
    if info is None:
        return f"{clean}\nLIMIT {MAX_QUERY_ROWS + 1}"
    start, tail = info
    tail_match = re.fullmatch(r"(\d+)(\s+offset\s+\d+)?", tail, re.IGNORECASE)
    if tail_match is None:
        raise SqlRejected(
            "末尾 LIMIT 只支持整数常量（如 LIMIT 100）：请把 "
            f"LIMIT {tail[:40]} 改写为整数后重试"
        )
    if int(tail_match.group(1)) <= MAX_QUERY_ROWS:
        return clean
    return clean[:start] + f"LIMIT {MAX_QUERY_ROWS + 1}{tail_match.group(2) or ''}"


# ---------- 查询 / 预览 ----------


def _cell(v: Any) -> Any:
    """把 DuckDB 值转成 JSON 友好类型；NaN/Infinity 返回 None（JSON 不支持）。"""
    if v is None or isinstance(v, (bool, int, str)):
        return v
    if isinstance(v, float):
        return v if math.isfinite(v) else None
    if isinstance(v, datetime):
        return v.isoformat(sep=" ")
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, Decimal):
        if not v.is_finite():
            return None
        return float(v)
    if isinstance(v, (bytes, bytearray)):
        return bytes(v).hex()
    return str(v)


def run_query(sql: str, timeout: float | None = None) -> dict:
    """执行只读查询：校验 → 短连接执行（interrupt 超时）→ JSON 化结果。

    抛 SqlRejected（校验失败）/ QueryTimeout（超时中断）/ duckdb.Error（执行错误）。
    """
    final_sql = validate_readonly_sql(sql, _registered_names())
    timeout = QUERY_TIMEOUT_SEC if timeout is None else timeout
    t0 = time.perf_counter()
    con = _connect()
    try:
        timer = threading.Timer(max(timeout, 0.05), con.interrupt)
        timer.start()
        try:
            cur = con.execute(final_sql)
            columns = [d[0] for d in cur.description]
            fetched = cur.fetchall()
        finally:
            timer.cancel()
    except duckdb.InterruptException as exc:
        raise QueryTimeout(f"查询超过 {timeout:g} 秒上限，已中断") from exc
    finally:
        con.close()
    truncated = len(fetched) > MAX_QUERY_ROWS
    rows = [[_cell(v) for v in row] for row in fetched[:MAX_QUERY_ROWS]]
    return {
        "columns": columns,
        "rows": rows,
        "row_count": len(rows),
        "truncated": truncated,
        "elapsed_ms": int((time.perf_counter() - t0) * 1000),
    }


def preview_table(table: str, limit: int = PREVIEW_ROWS) -> dict:
    """返回已注册表前 limit 行（默认 PREVIEW_ROWS 行）；未注册抛 LookupError。"""
    table = validate_table_name(table)
    if table not in _registered_names():
        raise LookupError(f"数据集未注册：{table}")
    limit = max(1, min(int(limit), PREVIEW_ROWS))
    con = _connect()
    try:
        cur = con.execute(f'SELECT * FROM "{table}" LIMIT ?', [limit])
        columns = [d[0] for d in cur.description]
        rows = [[_cell(v) for v in r] for r in cur.fetchall()]
    finally:
        con.close()
    return {"table": table, "columns": columns, "rows": rows, "row_count": len(rows)}


# ---------- Agent 工具（只读） ----------


def vault_list_tables() -> dict:
    """Agent 工具（readonly）：列出数据仓已注册数据集（表名/行数/来源/注册时间）。"""
    tables = list_datasets()
    return {"ok": True, "count": len(tables), "tables": tables}


def vault_query(sql: str = "") -> dict:
    """Agent 工具（readonly）：在数据仓执行只读 SELECT 查询（与 /query 同一套校验）。"""
    try:
        result = run_query(sql)
    except (SqlRejected, QueryTimeout, duckdb.Error, ValueError) as exc:
        return {"ok": False, "error": str(exc)[:200]}
    return {"ok": True, **result}
