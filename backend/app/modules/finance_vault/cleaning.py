"""finance_vault 数据池清洗与整合分析服务。

设计借鉴（2026-11-07）：OpenRefine 配方复用 / ydata-profiling 画像先行 /
klib 默认规则包 / Data-Juicer 逐算子统计。全部 pandas + DuckDB 自实现，零新依赖。

约定：
- 原始数据永不动：清洗结果经 service 的锁内入仓实现写新数据集（DuckDB 修订表留底）；
- 配方 = JSON（算子开关 + invalid_policy + required_columns），落 mod_vault_clean_log 可复用重放；
- 聚合白名单 + 参数绑定 + 恒以 LIMIT 收尾，杜绝注入。
"""
from __future__ import annotations

import io
import json
import math
import re
import time
from datetime import date, datetime

import duckdb
import pandas as pd

from ...database import SessionLocal
from ...utils_xlsx import safe_cell
from openpyxl import Workbook
from . import service as base
from .models import VaultCleanLog

# ---------- 可调常量（测试可 monkeypatch） ----------

MAX_AGG_ROWS = 1000        # /aggregate 默认结果上限（组数）
MAX_AGG_LIMIT = 10000      # /aggregate limit 参数上限
PROFILE_TOP_VALUES = 5     # 画像 top 高频值条数
SAMPLE_ROWS = 10           # 报告样本行数上限
PROFILE_SCAN_ROW_LIMIT = base.QUALITY_SCAN_ROW_LIMIT  # 画像/清洗逐表扫描行数上限

# 脏值口径（business_manage importer 同款）：一律归一为空值
NULL_TEXTS = frozenset({
    "", "-", "—", "–", "/", "无", "空", "空值", "暂无", "待定", "tbd",
    "n/a", "na", "#n/a", "#div/0!", "#value!", "#ref!", "null", "none", "nan",
})

# 日期解析：保守白名单（超出一律按文本保留，绝不瞎猜）
_DATE_PATTERNS = (
    re.compile(r"^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})$"),
    re.compile(r"^(\d{4})年(\d{1,2})月(\d{1,2})日$"),
    re.compile(r"^(\d{8})$"),
)
_NUM_RE = re.compile(r"^-?\d{1,12}(\.\d+)?$")
_LEADING_ZERO_RE = re.compile(r"^-?0\d+$")

# DuckDB 数值类型（前缀匹配，DECIMAL(10,2) 带参数）
_NUMERIC_TYPE_PREFIXES = (
    "BIGINT", "INTEGER", "HUGEINT", "SMALLINT", "TINYINT", "UBIGINT",
    "UINTEGER", "USMALLINT", "UTINYINT", "DECIMAL", "DOUBLE", "FLOAT", "REAL", "INT",
)
_DATE_TYPE_NAMES = frozenset({"DATE", "TIMESTAMP", "TIMESTAMP WITH TIME ZONE", "TIMESTAMP_NS"})

# 聚合白名单
_AGG_SQL = {"sum": "SUM", "avg": "AVG", "count": "COUNT", "min": "MIN", "max": "MAX"}
_FILTER_OPS = {"eq", "ne", "gt", "ge", "lt", "le", "contains", "in", "empty", "not_empty"}
_VALUE_OPS = {"eq", "ne", "gt", "ge", "lt", "le", "contains"}


# ---------- 数据集读取与类型推断 ----------


def _require_registered(table: str) -> None:
    """数据集必须已注册（SQLite 登记表为唯一权威），否则 ValueError（路由层转 400）。"""
    if table not in base._registered_names():
        raise ValueError(f"数据集未注册：{table}")


def _read_dataset_frame(table: str) -> tuple[pd.DataFrame, dict[str, str]]:
    """读数据集全量到 pandas，并返回 {列名: DuckDB 类型名}；调用方持锁调用或自持锁。"""
    with base._write_lock:
        con = base._connect()
        try:
            rel = con.table(table)
            duck_types = {str(c): str(t) for c, t in zip(rel.columns, rel.types)}
            df = rel.df()
            return df, duck_types
        finally:
            con.close()


def _duck_type_kind(duck_type: str) -> str:
    """DuckDB 类型名 → 画像/清洗用的三类：numeric / date / text。"""
    upper = (duck_type or "").upper()
    if any(upper.startswith(p) for p in _NUMERIC_TYPE_PREFIXES):
        values = "numeric"
    elif upper in _DATE_TYPE_NAMES:
        values = "date"
    else:
        values = "text"
    return values


def infer_kinds(df: pd.DataFrame, duck_types: dict[str, str] | None = None) -> dict[str, str]:
    """列类型三态推断：DuckDB 原生类型优先，文本列按非空值采样（≥80% 可解析才判类）。

    numeric/date/text 三类喂给画像警告与整合分析页（数值列才能当指标）。
    """
    duck_types = duck_types or {}
    kinds: dict[str, str] = {}
    for col in df.columns:
        native = _duck_type_kind(duck_types.get(str(col), ""))
        if native in ("numeric", "date"):
            kinds[str(col)] = native
            continue
        series = df[col]
        values = [str(v).strip() for v in series if pd.notna(v) and str(v).strip()]
        if not values:
            kinds[str(col)] = "text"
            continue
        sampled = values[:500]
        date_hits = sum(1 for v in sampled if _parse_date(v) is not None)
        num_hits = sum(1 for v in sampled if not math.isnan(_parse_num(v)))
        if date_hits / len(sampled) >= 0.8 and date_hits >= num_hits:
            kinds[str(col)] = "date"
        elif num_hits / len(sampled) >= 0.8:
            kinds[str(col)] = "numeric"
        else:
            kinds[str(col)] = "text"
    return kinds


# ---------- 值解析器（清洗的原子操作） ----------


def _full_to_half(text: str) -> str:
    """全角数字/字母/句点转半角（仅转换，不改语义）。"""
    out = []
    for ch in text:
        code = ord(ch)
        if 0xFF10 <= code <= 0xFF19:  # ０-９
            out.append(chr(code - 0xFEE0))
        elif 0xFF0E == code:  # ．
            out.append(".")
        elif 0xFF0D == code:  # －
            out.append("-")
        else:
            out.append(ch)
    return "".join(out)


def _parse_num(value) -> float:
    """文本 → 数值：容千分位/全角/百分号；前导零整数与 >12 位整数判为编码保留文本。

    None/脏值 → NaN（中性，不算无效）；解析失败 → NaN。调用方用「非空但 NaN」判定无效值。
    """
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return math.nan
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    text = _full_to_half(str(value).strip())
    if not text or text.lower() in NULL_TEXTS:
        return math.nan
    text = text.replace(",", "").replace("，", "")
    is_pct = text.endswith("%")
    if is_pct:
        text = text[:-1].strip()
    if not _NUM_RE.fullmatch(text) or _LEADING_ZERO_RE.fullmatch(text):
        return math.nan
    number = float(text)
    return number / 100.0 if is_pct else number


def _parse_date(value) -> "pd.Timestamp | None":
    """文本 → 日期：保守白名单格式；失败 → None。datetime/date 原样通过。"""
    if isinstance(value, datetime):
        return pd.Timestamp(value)
    if isinstance(value, date):
        return pd.Timestamp(value)
    text = _full_to_half(str(value or "").strip())
    if not text or text.lower() in NULL_TEXTS:
        return None
    for pattern in _DATE_PATTERNS:
        m = pattern.fullmatch(text)
        if not m:
            continue
        try:
            if m.lastindex == 1:  # YYYYMMDD
                return pd.Timestamp(year=int(m.group(1)[:4]), month=int(m.group(1)[4:6]),
                                    day=int(m.group(1)[6:8]))
            return pd.Timestamp(year=int(m.group(1)), month=int(m.group(2)), day=int(m.group(3)))
        except ValueError:
            continue
    return None


# ---------- 列画像（ydata-profiling 式画像先行） ----------


def profile_table(table: str) -> dict:
    """数据集列画像：每列类型/缺失率/唯一率/高频值 + 表级警告，有界执行。

    - 行数超 PROFILE_SCAN_ROW_LIMIT 降级 sampled：只算缺失率（describe），不算唯一率/高频值；
    - 单连接短会话 + 写锁串行，与质量画像同口径；未注册抛 ValueError，视图缺失抛 duckdb.Error。
    """
    table = base.validate_table_name(table)
    _require_registered(table)
    con = base._connect()
    try:
        rel = con.table(table)
        duck_types = {str(c): str(t) for c, t in zip(rel.columns, rel.types)}
        columns = [str(c) for c in rel.columns]
        total = int(rel.aggregate("count(*)").fetchone()[0])
        sampled = total > PROFILE_SCAN_ROW_LIMIT
        dup_rows: int | None = None
        if not sampled:
            distinct_rows = int(rel.distinct().aggregate("count(*)").fetchone()[0])
            dup_rows = max(0, total - distinct_rows)
        df = None if sampled else rel.df()
        if sampled:  # 大表降级：describe 的 count 行给出每列非空计数
            desc = rel.describe().df()
            non_null_by_col = {}
            if "aggr" in desc.columns and not desc.empty:
                cnt_row = desc[desc["aggr"] == "count"].iloc[0]
                non_null_by_col = {c: float(cnt_row[c]) for c in desc.columns if c != "aggr"}
    finally:
        con.close()
    profiles: list[dict] = []
    for col in columns:
        native = _duck_type_kind(duck_types.get(col, ""))
        item: dict = {"name": col, "duckdb_type": duck_types.get(col, ""),
                      "kind": native, "null_pct": 0.0, "distinct_pct": None,
                      "top_values": [], "min": None, "max": None}
        if sampled:
            item["null_pct"] = round(max(0.0, 100 - (non_null_by_col.get(col, total) / total * 100)), 1) if total else 0.0
            profiles.append(item)
            continue
        series = df[col]
        if len(series):
            item["null_pct"] = round(float(series.isna().mean() * 100), 1)
            distinct = int(series.nunique(dropna=True))
            item["distinct_pct"] = round(distinct / len(series), 4) if len(series) else None
            vc = series.value_counts(dropna=True).head(PROFILE_TOP_VALUES)
            item["top_values"] = [{"value": str(v)[:40], "count": int(n)} for v, n in vc.items()]
            if native == "numeric" or (native == "text" and infer_kinds(series.to_frame(), {col: duck_types.get(col, "")})[col] == "numeric"):
                parsed = series.map(_parse_num)
                valid = parsed.dropna()
                if len(valid):
                    item["min"], item["max"] = float(valid.min()), float(valid.max())
        profiles.append(item)
    kinds = infer_kinds(df if df is not None else pd.DataFrame(columns=columns), duck_types)
    warnings: list[str] = []
    for p in profiles:
        if p["null_pct"] >= 100:
            warnings.append(f"列「{p['name']}」全空，建议清洗时剔除该列")
        elif p["distinct_pct"] is not None and round(p["distinct_pct"] * total) <= 1 and p["null_pct"] < 100 and total > 1:
            warnings.append(f"列「{p['name']}」疑似常量列，聚合分组价值低")
        elif p["null_pct"] > 30:
            warnings.append(f"列「{p['name']}」缺失率 {p['null_pct']}%，超过 30% 建议关注")
        elif p["kind"] == "text" and p["distinct_pct"] is not None and p["distinct_pct"] >= 0.99 and round(p["distinct_pct"] * total) >= 20:
            warnings.append(f"列「{p['name']}」唯一率≈100%，疑似 ID 列，不宜做分组维度")
    if dup_rows:
        warnings.append(f"整行重复 {dup_rows} 行，建议开启「整行去重」")
    if sampled:
        warnings.append(f"共 {total} 行超过扫描上限 {PROFILE_SCAN_ROW_LIMIT}，画像仅含缺失率（sampled）")
    return {
        "table": table, "rows": total, "dup_rows": dup_rows, "sampled": sampled,
        "kinds": kinds, "columns": profiles, "warnings": warnings,
    }


# ---------- 清洗（配方 = JSON，逐算子统计） ----------


def normalize_recipe(raw: dict | None) -> tuple[dict, list[str]]:
    """校验并归一化清洗配方：未知键忽略并记警告（跨表复用配方的容错基础）。"""
    warnings: list[str] = []
    raw = raw if isinstance(raw, dict) else {}
    ops_in = raw.get("ops") if isinstance(raw.get("ops"), dict) else {}
    known_ops = ("normalize_dirty_text", "drop_all_empty_rows", "drop_all_empty_columns", "dedupe_rows")
    ops = {name: bool(ops_in.get(name, True)) for name in known_ops}
    unknown_keys = sorted(set(ops_in) - set(known_ops) - {"required_columns"})
    if unknown_keys:
        warnings.append(f"配方含未识别的算子键，已忽略：{'、'.join(unknown_keys)}")
    required = [str(c).strip() for c in (ops_in.get("required_columns") or []) if str(c).strip()]
    invalid_policy = raw.get("invalid_policy", "drop_row")
    if invalid_policy not in ("drop_row", "null_cell"):
        warnings.append(f"invalid_policy={invalid_policy!r} 不支持，已回退 drop_row")
        invalid_policy = "drop_row"
    recipe = {
        "ops": {**ops, "required_columns": required},
        "invalid_policy": invalid_policy,
        "mark_outliers_iqr": bool(raw.get("mark_outliers_iqr", False)),
    }
    return recipe, warnings


def _run_clean(table: str, recipe: dict) -> tuple[pd.DataFrame, dict]:
    """执行清洗：算子按固定顺序作用，行级首因剔除 + 逐算子统计 + 样本留痕。

    原始数据不动（只读源表），返回清洗后 DataFrame 与报告；调用方决定预览或落池。
    """
    df, duck_types = _read_dataset_frame(table)
    rows_in = len(df)
    kinds_before = infer_kinds(df, duck_types)
    null_before = {str(c): round(float(df[c].isna().mean() * 100), 1) if rows_in else 0.0 for c in df.columns}
    ops_report: list[dict] = []
    samples: list[dict] = []
    warnings: list[str] = list(recipe.get("warnings") or [])
    columns_dropped: list[str] = []
    outliers: list[dict] = []
    invalid_cells = 0

    def _collect_samples(mask, reason: str, frame: pd.DataFrame) -> None:
        nonlocal samples
        for _, row in frame[mask].head(SAMPLE_ROWS - len(samples)).iterrows():
            samples.append({
                "reason": reason,
                "values": {str(c)[:40]: str(v)[:40] for c, v in list(row.items())[:8]},
            })

    if rows_in == 0:
        return df, {"source_table": table, "rows_in": 0, "rows_kept": 0, "ops": [],
                    "samples": [], "warnings": warnings, "columns_dropped": [],
                    "outliers": [], "kinds_before": kinds_before, "kinds_after": {},
                    "null_pct_before": {}, "null_pct_after": {}}
    if rows_in > PROFILE_SCAN_ROW_LIMIT:
        warnings.append(f"源表 {rows_in} 行超过扫描上限 {PROFILE_SCAN_ROW_LIMIT}，逐行清洗改抽样执行")

    # 步骤 1：脏值归一（受影响格数，unit=cells）
    if recipe["ops"]["normalize_dirty_text"]:
        hits = 0
        for col in df.columns:
            s = df[col]
            mask = s.map(lambda v: isinstance(v, str) and v.strip() in NULL_TEXTS)
            if mask.any():
                hits += int(mask.sum())
                df.loc[mask, col] = None
        ops_report.append({"op": "normalize_dirty_text", "hits": hits, "unit": "cells"})

    # 步骤 2：剔除整行全空（脏值归一后「—/无」行也算全空行）
    if recipe["ops"]["drop_all_empty_rows"]:
        mask = df.isna().all(axis=1)
        _collect_samples(mask, "empty_row", df)
        ops_report.append({"op": "drop_all_empty_rows", "hits": int(mask.sum()), "unit": "rows"})
        df = df[~mask]

    # 步骤 3：剔除全空列
    if recipe["ops"]["drop_all_empty_columns"]:
        col_mask = df.isna().all(axis=0)
        columns_dropped = [str(c) for c in df.columns[col_mask]]
        df = df.loc[:, ~col_mask]
        ops_report.append({"op": "drop_all_empty_columns", "hits": len(columns_dropped), "unit": "cols"})

    # 步骤 4：整行去重（keep first）
    if recipe["ops"]["dedupe_rows"]:
        mask = df.duplicated(keep="first")
        _collect_samples(mask, "duplicate", df)
        ops_report.append({"op": "dedupe_rows", "hits": int(mask.sum()), "unit": "rows"})
        df = df[~mask]

    # 步骤 5：类型校验（文本列推断为 numeric/date 后，无效值按 invalid_policy 处理）
    kinds = infer_kinds(df, duck_types)
    invalid_num_total = 0
    invalid_date_total = 0
    for col in df.columns:
        kind = kinds[str(col)]
        native = _duck_type_kind(duck_types.get(str(col), ""))
        if native in ("numeric", "date"):
            continue  # DuckDB 原生数值/日期列必然合法
        if kind == "numeric":
            parsed = df[col].map(_parse_num)
            invalid = parsed.isna() & df[col].notna()
            invalid_num_total += int(invalid.sum())
            if recipe["invalid_policy"] == "drop_row" and invalid.any():
                _collect_samples(invalid, "invalid_numeric", df)
                df = df[~invalid]
                parsed = parsed[~invalid]
            df[col] = parsed
        elif kind == "date":
            parsed = df[col].map(_parse_date)
            invalid = parsed.isna() & df[col].notna()
            invalid_date_total += int(invalid.sum())
            if recipe["invalid_policy"] == "drop_row" and invalid.any():
                _collect_samples(invalid, "invalid_date", df)
                df = df[~invalid]
                parsed = parsed[~invalid]
            df[col] = parsed

    if invalid_num_total:
        ops_report.append({"op": "invalid_numeric", "hits": invalid_num_total, "unit": "cells"})
    if invalid_date_total:
        ops_report.append({"op": "invalid_date", "hits": invalid_date_total, "unit": "cells"})

    # 步骤 6：关键列必填（缺列自动跳过并记警告——配方跨表复用的容错）
    required = [c for c in recipe["ops"]["required_columns"] if c in df.columns]
    missing = [c for c in recipe["ops"]["required_columns"] if c not in df.columns]
    if missing:
        warnings.append(f"配方中的关键列在本表不存在，已跳过：{'、'.join(missing)}")
    if required:
        mask = df[required].isna().any(axis=1)
        _collect_samples(mask, "required_empty", df)
        ops_report.append({"op": "required_columns", "hits": int(mask.sum()), "unit": "rows"})
        df = df[~mask]

    # 步骤 7：IQR 离群标记（只进报告，不剔除行）
    if recipe["mark_outliers_iqr"]:
        for col in df.columns:
            if kinds.get(str(col)) != "numeric":
                continue
            s = pd.to_numeric(df[col], errors="coerce").dropna()
            if len(s) < 4:
                continue
            q1, q3 = s.quantile(0.25), s.quantile(0.75)
            iqr = q3 - q1
            if iqr <= 0:
                continue
            cnt = int(((s < q1 - 1.5 * iqr) | (s > q3 + 1.5 * iqr)).sum())
            if cnt:
                outliers.append({"column": str(col), "count": cnt})

    kinds_after = infer_kinds(df, duck_types)
    null_after = {str(c): round(float(df[c].isna().mean() * 100), 1) if len(df) else 0.0 for c in df.columns}
    report = {
        "source_table": table,
        "rows_in": rows_in,
        "rows_kept": len(df),
        "ops": ops_report,
        "samples": samples,
        "warnings": warnings,
        "columns_dropped": columns_dropped,
        "outliers": outliers,
        "kinds_before": kinds_before,
        "kinds_after": kinds_after,
        "null_pct_before": null_before,
        "null_pct_after": null_after,
    }
    return df, report


def clean_preview(table: str, recipe_raw: dict | None) -> dict:
    """清洗预览（dry-run）：不写库，返回报告 + 默认结果表名 + 归一化后的配方。"""
    table = base.validate_table_name(table)
    _require_registered(table)
    recipe, warnings = normalize_recipe(recipe_raw)
    recipe["warnings"] = warnings
    _, report = _run_clean(table, recipe)
    report["recipe"] = {k: v for k, v in recipe.items() if k != "warnings"}
    report["target_table_default"] = f"{table}_clean"
    return report


def _assert_target_available(source: str, target: str, allow_overwrite: bool) -> None:
    """目标可用性判定（锁外预判与锁内重取必须同一口径）。

    未显式允许覆盖时：目标=源表、或目标已存在（登记/视图/物理表任一残留）一律
    DatasetConflict；锁内重取保证并发调用不会双双通过「目标不存在」的检查。
    """
    if allow_overwrite:
        return
    if target == source:
        raise base.DatasetConflict("结果表名与源表相同，需显式允许覆盖（allow_overwrite=true）")
    if base.dataset_exists(target):
        raise base.DatasetConflict(f"结果表已存在：{target}（请改名，或显式允许覆盖）")


def _assert_source_present(table: str) -> None:
    """锁内重取源数据集是否仍在（清洗/聚合计算期间可能被并发删除）。"""
    if table not in base._registered_names():
        raise base.DatasetConflict(f"源数据集已被删除，结果未落库：{table}")


def clean_apply(table: str, recipe_raw: dict | None, target_table: str = "", allow_overwrite: bool = False) -> dict:
    """应用清洗：结果经入仓契约落新数据集（原始数据不动）+ 写清洗日志。

    - 结果表已存在时默认拒绝（DatasetConflict → 409），allow_overwrite=True 才覆盖；
    - 锁外先预判目标（避免为已存在的目标白跑清洗），随后在**同一写锁临界区**内重取
      目标状态、复核源仍存在、入仓并写清洗日志：并发双清理不会都通过检查，日志也
      不可能指向一个已被并发删除的结果表（复核残余边界）。
    """
    table = base.validate_table_name(table)
    _require_registered(table)
    recipe, warnings = normalize_recipe(recipe_raw)
    recipe["warnings"] = warnings
    target = (target_table or "").strip() or f"{table}_clean"
    base.validate_table_name(target)
    _assert_target_available(table, target, allow_overwrite)
    clean_df, report = _run_clean(table, recipe)
    with base._write_lock:
        _assert_target_available(table, target, allow_overwrite)
        _assert_source_present(table)
        base._ingest_dataframe_locked(target, clean_df, mode="replace", source=f"清洗自 {table}")
        with SessionLocal() as db:
            db.add(VaultCleanLog(
                source_table=table, result_table=target,
                rows_in=int(report.get("rows_in", 0)), rows_kept=int(report.get("rows_kept", 0)),
                config_json=recipe, report_json=report,
            ))
            db.commit()
    report["result_table"] = target
    return {"result_table": target, "rows": report.get("rows_kept", 0), "report": report}


def get_clean_logs(table: str = "", limit: int = 20) -> list[dict]:
    """清洗日志（按 id 倒序）：配方 + 逐算子报告，供前端「历史配方一键套用」。"""
    lim = max(1, min(int(limit), 50))
    with SessionLocal() as db:
        query = db.query(VaultCleanLog)
        if table:
            query = query.filter(VaultCleanLog.source_table == table)
        rows = query.order_by(VaultCleanLog.id.desc()).limit(lim).all()
        return [
            {
                "id": r.id,
                "source_table": r.source_table,
                "result_table": r.result_table,
                "rows_in": r.rows_in,
                "rows_kept": r.rows_kept,
                "recipe": r.config_json or {},
                "ops": (r.report_json or {}).get("ops", []),
                "warnings": (r.report_json or {}).get("warnings", []),
                "created_at": r.created_at.strftime("%Y-%m-%d %H:%M:%S") if r.created_at else "",
            }
            for r in rows
        ]


# ---------- 整合分析（单表聚合，白名单 + 参数绑定） ----------


def _dataset_columns(table: str) -> dict[str, str]:
    """{列名: DuckDB 类型名}（来自 SQLite 登记表 columns_json），供列白名单校验。"""
    for ds in base.list_datasets():
        if ds["table"] == table:
            return {str(c.get("name")): str(c.get("type", "")) for c in (ds.get("columns") or [])}
    return {}


def _apply_filter(df: pd.DataFrame, item: dict, colmap: dict[str, str]) -> pd.DataFrame:
    """单条过滤条件作用到 DataFrame；非法 op/column 抛 ValueError。"""
    column = str(item.get("column", ""))
    op = str(item.get("op", ""))
    if column not in colmap:
        raise ValueError(f"过滤列不存在：{column}")
    if op not in _FILTER_OPS:
        raise ValueError(f"不支持的过滤操作符：{op}")
    series = df[column]
    if op == "empty":
        return df[series.isna() | (series.astype(str).str.strip().isin(["", "nan", "None"]))]
    if op == "not_empty":
        return df[~(series.isna() | (series.astype(str).str.strip().isin(["", "nan", "None"])))]
    value = item.get("value")
    if value is None or (isinstance(value, str) and not value.strip()):
        raise ValueError(f"过滤条件 {column} {op} 缺少比较值")
    if op == "in":
        if not isinstance(value, list) or not value:
            raise ValueError(f"过滤条件 {column} IN 需要非空数组")
        return df[series.isin(value)]
    if op == "contains":
        return df[series.astype(str).str.contains(str(value), regex=False)]
    if op in ("gt", "ge", "lt", "le"):
        numeric = pd.to_numeric(series, errors="coerce")
        use_numeric = isinstance(value, (int, float)) and not isinstance(value, bool)
        if use_numeric:
            mask = {"gt": numeric > value, "ge": numeric >= value,
                    "lt": numeric < value, "le": numeric <= value}[op]
            return df[mask]
        text = str(value)
        mask = {"gt": series > text, "ge": series >= text,
                "lt": series < text, "le": series <= text}[op]
        return df[mask]
    # eq / ne
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        numeric = pd.to_numeric(series, errors="coerce")
        return df[numeric == value] if op == "eq" else df[~((numeric == value) | series.isna())]
    return df[series == value] if op == "eq" else df[series != value]


def _metric_label(column: str, agg: str) -> str:
    """指标结果列名：金额_sum / 总行数_count 形态（前端表格与图表直接用）。"""
    return "总行数_count" if column == "*" else f"{column}_{agg}"


def aggregate_query(table: str, spec: dict) -> dict:
    """单表聚合：维度分组 + 指标汇总 + 过滤 + 排序（白名单 + 参数绑定 + 恒 LIMIT）。

    无维度有指标 → 单行汇总；有维度无指标 → 去重分组表。抛 ValueError（路由层转 400）。
    """
    table = base.validate_table_name(table)
    _require_registered(table)
    colmap = _dataset_columns(table)
    if not colmap:
        raise ValueError(f"数据集列信息缺失，请重新入仓：{table}")
    group_by: list[str] = []
    for c in (spec.get("group_by") or []):
        name = str(c)
        if name not in colmap:
            raise ValueError(f"分组维度不存在：{name}")
        if name not in group_by:
            group_by.append(name)
    metrics: list[tuple[str, str]] = []
    for m in (spec.get("metrics") or []):
        column, agg = str(m.get("column", "")), str(m.get("agg", ""))
        if agg not in _AGG_SQL:
            raise ValueError(f"不支持的聚合方式：{agg}（仅 {'/'.join(_AGG_SQL)}）")
        if column != "*" and column not in colmap:
            raise ValueError(f"指标列不存在：{column}")
        if agg in ("sum", "avg") and column == "*":
            raise ValueError("sum/avg 需要指定数值列（* 仅用于 count）")
        if agg in ("sum", "avg") and column != "*":
            if not any(colmap[column].upper().startswith(p) for p in _NUMERIC_TYPE_PREFIXES):
                raise ValueError(f"指标 {column} 不是数值列，不能做 {agg}")
        metrics.append((column, agg))
    if not group_by and not metrics:
        raise ValueError("至少需要分组维度或聚合指标")
    if len(metrics) + len(group_by) > 40:
        raise ValueError("分组维度 + 指标合计不超过 40 个")
    filters = list(spec.get("filters") or [])
    if len(filters) > 20:
        raise ValueError("过滤条件不超过 20 条")
    # 聚合在 pandas 侧完成（项目惯例：聚合走内存分组，不拼 SQL，规避注入面）
    t0 = time.perf_counter()
    df, _ = _read_dataset_frame(table)
    for f in filters:
        df = _apply_filter(df, f, colmap)
    labels = [_metric_label(c, a) for c, a in metrics]
    result_columns = list(group_by) + labels

    # sum/avg 作用在文本型数值列时先做数值化（与清洗的类型转换同口径）
    need_numeric = any(a in ("sum", "avg") and c != "*" for c, a in metrics)
    if need_numeric and len(df):
        df = df.copy()
        for column, agg in metrics:
            if agg in ("sum", "avg") and column != "*":
                df[column] = pd.to_numeric(df[column], errors="coerce")
    agg_frame: pd.DataFrame
    if metrics:
        if group_by:
            grouped = df.groupby(group_by, dropna=False, sort=True)
            parts: list[pd.Series] = []
            for column, agg in metrics:
                if agg == "count":
                    parts.append(grouped.size() if column == "*" else grouped[column].count())
                elif agg == "sum":
                    parts.append(grouped[column].sum())
                elif agg == "avg":
                    parts.append(grouped[column].mean())
                elif agg == "min":
                    parts.append(grouped[column].min())
                else:
                    parts.append(grouped[column].max())
            agg_frame = pd.concat(parts, axis=1)
            agg_frame.columns = labels
            agg_frame = agg_frame.reset_index()
        else:
            row: dict = {}
            for column, agg in metrics:
                series = df[column] if column != "*" else None
                if agg == "count":
                    value = len(df) if column == "*" else int(series.count())
                elif agg == "sum":
                    value = float(pd.to_numeric(series, errors="coerce").sum())
                elif agg == "avg":
                    value = float(pd.to_numeric(series, errors="coerce").mean())
                elif agg == "min":
                    value = series.min()
                else:
                    value = series.max()
                row[_metric_label(column, agg)] = value
            agg_frame = pd.DataFrame([row], columns=result_columns)
    else:
        agg_frame = df[group_by].drop_duplicates().reset_index(drop=True)

    order_by = str(spec.get("order_by") or "").strip()
    order_dir = "DESC" if str(spec.get("order_dir", "desc")).lower() == "desc" else "ASC"
    valid_order_names = {c for c in group_by} | {_metric_label(c, a) for c, a in metrics}
    if order_by and order_by not in valid_order_names:
        raise ValueError(f"排序字段必须是分组维度或指标结果列：{order_by}")
    limit = max(1, min(int(spec.get("limit") or MAX_AGG_ROWS), MAX_AGG_LIMIT))
    total_groups = int(len(agg_frame))
    truncated = total_groups > limit
    if order_by and order_by in agg_frame.columns and len(agg_frame):
        try:
            agg_frame = agg_frame.sort_values(
                order_by, ascending=(order_dir == "ASC"), na_position="last")
        except TypeError:
            agg_frame = agg_frame.astype({order_by: str}).sort_values(
                order_by, ascending=(order_dir == "ASC"), na_position="last")
    rows = [[base._cell(v) for v in r] for r in agg_frame.head(limit).to_numpy().tolist()]
    return {
        "columns": result_columns,
        "rows": rows,
        "row_count": len(rows),
        "total_groups": total_groups,
        "truncated": truncated,
        "elapsed_ms": int((time.perf_counter() - t0) * 1000),
    }


def export_aggregate_xlsx(table: str, spec: dict) -> tuple[bytes, str]:
    """聚合结果导出 xlsx（safe_cell 防公式注入）；返回 (文件字节, 建议文件名)。"""
    result = aggregate_query(table, {**spec, "limit": MAX_AGG_LIMIT})
    wb = Workbook()
    ws = wb.active
    ws.title = "聚合分析"
    ws.append(result["columns"])
    for row in result["rows"]:
        ws.append([safe_cell(v) for v in row])
    ws.freeze_panes = "A2"
    buf = io.BytesIO()
    wb.save(buf)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return buf.getvalue(), f"{table}_聚合分析_{stamp}.xlsx"


def save_aggregate_as_dataset(
    table: str, spec: dict, target_table: str, allow_overwrite: bool = False,
) -> dict:
    """聚合结果另存为数据池数据集（登记后可查询、可再清洗）。

    目标已存在时默认拒绝（DatasetConflict → 409），allow_overwrite=True 才覆盖；
    锁外预判目标、锁内重取目标与源（并发双写同一目标只会有一个成功），
    入仓在持锁状态经 `_ingest_dataframe_locked` 完成，绝不静默 replace 已有数据集。
    """
    source = base.validate_table_name(table)
    target_table = base.validate_table_name(target_table)
    _assert_target_available(source, target_table, allow_overwrite)
    result = aggregate_query(source, {**spec, "limit": MAX_AGG_LIMIT})
    if not result["rows"]:
        raise ValueError("聚合结果为空，无法另存")
    df = pd.DataFrame(result["rows"], columns=result["columns"])
    with base._write_lock:
        _assert_target_available(source, target_table, allow_overwrite)
        _assert_source_present(source)
        base._ingest_dataframe_locked(target_table, df, mode="replace", source=f"聚合自 {source}")
    return {"result_table": target_table, "rows": len(df), "total_groups": result["total_groups"]}
