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
from .models import VaultDataset

# ---------- 可调常量（测试可 monkeypatch）----------

MAX_QUERY_ROWS = 1000                  # /query 结果强制上限
PREVIEW_ROWS = 100                     # 预览行数上限
QUERY_TIMEOUT_SEC = 5.0                # DuckDB 层查询超时（秒）
MAX_UPLOAD_BYTES = 20 * 1024 * 1024    # 上传文件大小上限
ALLOWED_SUFFIXES = (".csv", ".xlsx")   # 上传扩展名白名单
MAX_SQL_LEN = 20000                    # SQL 文本长度上限

# 表名白名单：仅小写字母/数字/下划线，1-64 字符
_TABLE_NAME_RE = re.compile(r"^[a-z0-9_]{1,64}$")
# 写操作 / DDL / 系统命令关键词（在校验视图上做词边界匹配；视图已剥离注释与字符串内容）
_FORBIDDEN_RE = re.compile(
    r"\b(?:insert|update|delete|drop|create|alter|truncate|attach|detach|pragma|set|reset|"
    r"copy|export|import|install|load|call|exec|execute|grant|revoke|vacuum|checkpoint|"
    r"show|describe|explain|use|pivot)\b",
    re.IGNORECASE,
)
# 语句末尾的顶层 LIMIT（可选 OFFSET）
_TRAILING_LIMIT_RE = re.compile(r"\blimit\s+(\d+)(\s+offset\s+\d+)?\s*$", re.IGNORECASE)
# 词法 token：带引号标识符 / 字符串 / 单词（含 schema.table 形式）/ 括号 / 逗号 / 其他单字符
_TOKEN_RE = re.compile(r'"(?:[^"]|"")*"|\'[^\']*\'|[a-z_][a-z0-9_.]*|\(|\)|,|\S')
_IDENT_RE = re.compile(r"[a-z_][a-z0-9_.]*")
# FROM/JOIN 上下文遇到这些子句关键词即结束
_CLAUSE_KEYWORDS = {
    "where", "group", "order", "having", "limit", "offset", "window", "qualify",
    "union", "intersect", "except", "on", "using", "returning",
}

# 写操作串行化（同进程多连接 DuckDB 允许，但并发写事务可能冲突，应用层串行最稳）
_write_lock = threading.Lock()


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
    """表名白名单校验：仅小写字母/数字/下划线，1-64 字符。"""
    name = (table or "").strip()
    if not _TABLE_NAME_RE.fullmatch(name):
        raise ValueError(f"非法表名 {table!r}：仅允许小写字母/数字/下划线，1-64 字符")
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


def ingest_dataframe(table: str, df, mode: str = "replace", source: str = "") -> dict:
    """模块间契约：把 DataFrame 入仓为 DuckDB 表，并登记到 mod_vault_dataset。

    - table：目标表名（仅小写字母/数字/下划线，1-64 字符）；
    - df：pandas.DataFrame（空列名/重名自动规范化；非 DataFrame 输入尝试转换）；
    - mode：replace（整表重建，默认）/ append（按列名 BY NAME 追加，表不存在则创建）；
    - source：来源说明（写入登记表，留空记为 dataframe）。

    返回 {"table": table, "rows": 入仓后总行数}；参数非法抛 ValueError。
    """
    table = validate_table_name(table)
    if mode not in ("replace", "append"):
        raise ValueError(f"不支持的入仓模式 {mode!r}（仅 replace / append）")
    if not isinstance(df, pd.DataFrame):
        df = pd.DataFrame(df)
    df = _normalize_columns(df)
    with _write_lock:
        con = _connect()
        try:
            con.register("staging_df", df)
            exists = bool(
                con.execute(
                    "SELECT 1 FROM information_schema.tables WHERE table_name = ?", [table]
                ).fetchone()
            )
            if mode == "append" and exists:
                con.execute(f'INSERT INTO "{table}" BY NAME SELECT * FROM staging_df')
            else:
                con.execute(f'CREATE OR REPLACE TABLE "{table}" AS SELECT * FROM staging_df')
            rows = con.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]
            cols = [{"name": r[0], "type": str(r[1])} for r in con.execute(f'DESCRIBE "{table}"').fetchall()]
        except ValueError:
            raise
        except duckdb.Error as exc:
            # 列不匹配等 DuckDB 层错误属于数据/参数问题：降级为 ValueError（路由层转 400），不裸抛 500
            raise ValueError(f"入仓失败：{exc}") from exc
        finally:
            con.close()
    _register_dataset(table, source=source or "dataframe", rows=rows, columns=cols)
    return {"table": table, "rows": rows}


def _read_table_file(path: Path, sheet: str = "") -> pd.DataFrame:
    """把 xlsx/csv 读成 DataFrame；CSV 依次尝试 utf-8-sig / gbk 编码。

    解析异常统一降级为 ValueError（路由层转 400）：损坏文件（如 ZIP 魔数残缺的
    BadZipFile、pandas 引擎异常）属于客户端错误，不应裸抛成 500。
    """
    suffix = path.suffix.lower()
    try:
        if suffix == ".xlsx":
            s = (sheet or "").strip()
            sheet_name: str | int = int(s) if s.isdigit() else (s or 0)
            return pd.read_excel(path, sheet_name=sheet_name)
        if suffix == ".csv":
            for enc in ("utf-8-sig", "gbk"):
                try:
                    return pd.read_csv(path, encoding=enc)
                except UnicodeDecodeError:
                    continue
            raise ValueError("无法识别 CSV 编码（仅支持 UTF-8 / GBK）")
        raise ValueError(f"不支持的文件类型：{suffix}")
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(f"文件解析失败：{type(exc).__name__}: {exc}") from exc


def ingest_uploaded_file(table: str, filename: str, content: bytes, mode: str = "replace", sheet: str = "") -> dict:
    """上传文件入仓：扩展名白名单 + 大小限制 + 防路径穿越 + 落 inbox 留档后解析。"""
    table = validate_table_name(table)
    if mode not in ("replace", "append"):
        raise ValueError(f"不支持的入仓模式 {mode!r}（仅 replace / append）")
    safe_name = Path(filename or "").name
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
        df = _read_table_file(dest, sheet=sheet)
        result = ingest_dataframe(table, df, mode=mode, source=safe_name)
    except Exception:
        dest.unlink(missing_ok=True)  # 失败不留档，避免 inbox 堆积垃圾
        raise
    with SessionLocal() as db:  # 补充列信息到返回值（ingest_dataframe 已登记）
        row = db.query(VaultDataset).filter(VaultDataset.table_name == table).first()
        columns = row.columns_json if row else []
    return {"table": result["table"], "rows": result["rows"], "source": safe_name, "mode": mode, "columns": columns}


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


def _extract_refs_and_ctes(view: str) -> tuple[set[str], set[str]]:
    """从校验视图（转小写后）提取 FROM/JOIN 表引用集合与 CTE 别名集合。

    逐 token 走状态机（idle → expect_table → after_table），覆盖：
    显式 JOIN、逗号连接（FROM a, b）、派生表/子查询（括号栈恢复上下文）。
    FROM/JOIN 后直接跟字符串（文件路径表）抛 SqlRejected。
    """
    tokens = _TOKEN_RE.findall(view.lower())
    refs: set[str] = set()
    ctes: set[str] = set()
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
    if not head or head.group(0).lower() not in ("select", "with"):
        raise SqlRejected("只允许 SELECT / WITH 开头的只读查询")
    hit = _FORBIDDEN_RE.search(view)
    if hit:
        raise SqlRejected(f"只允许只读查询，检测到禁止的关键词：{hit.group(0).upper()}")
    refs, ctes = _extract_refs_and_ctes(view)
    allowed = {t.lower() for t in allowed_tables}
    unknown = sorted(refs - allowed - ctes)
    if unknown:
        raise SqlRejected(f"引用了未注册的数据集：{'、'.join(unknown)}")
    # 强制 LIMIT：无顶层 LIMIT 补上；超出上限改写为上限+1
    m = _TRAILING_LIMIT_RE.search(view)
    if m is None:
        return f"{clean}\nLIMIT {MAX_QUERY_ROWS + 1}"
    if int(m.group(1)) <= MAX_QUERY_ROWS:
        return clean
    return _TRAILING_LIMIT_RE.sub(lambda mo: f"LIMIT {MAX_QUERY_ROWS + 1}{mo.group(2) or ''}", clean, count=1)


# ---------- 查询 / 预览 ----------


def _cell(v: Any) -> Any:
    """把 DuckDB 值转成 JSON 友好类型。"""
    if v is None or isinstance(v, (bool, int, float, str)):
        return v
    if isinstance(v, datetime):
        return v.isoformat(sep=" ")
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, Decimal):
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
