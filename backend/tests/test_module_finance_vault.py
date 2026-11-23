"""finance_vault 模块测试。

全部使用合成数据：pandas 生成的 DataFrame 与合成 xlsx/csv 写入 tmp_path；
DuckDB 库文件路径经 monkeypatch 重定向到 tmp_path，不写真实数据目录。
覆盖：ingest_dataframe 契约、上传入仓（白名单/大小/坏文件）、只读 SQL 校验
（注入防护）、预览、HTTP 端点、Agent 工具注册与行为。
"""
import asyncio
from pathlib import Path

import pandas as pd
import pytest

from app.agents.tools import TOOL_SPECS
from app.modules import get_loaded_modules
from app.modules.finance_vault import router as vault_router
from app.modules.finance_vault import service as svc

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _sample_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "月份": ["2026-09", "2026-09", "2026-10"],
            "分类": ["办公费", "办公费", "差旅费"],
            "金额": [100.0, 200.0, 150.0],
        }
    )


@pytest.fixture()
def vault_dir(monkeypatch, tmp_path, client):
    """DuckDB 库文件重定向到 tmp_path；client 夹具负责建表（测试不写真实数据目录）。"""
    base = tmp_path / "vault_data"
    monkeypatch.setattr(svc, "get_finance_data_dir", lambda: base)
    return base


# ---------------- ingest_dataframe 契约 ----------------

def test_ingest_dataframe_contract(vault_dir):
    r = svc.ingest_dataframe("t_expense", _sample_df())
    assert r["table"] == "t_expense" and r["rows"] == 3
    assert r["skipped_duplicates"] == 0
    tables = {t["table"] for t in svc.list_datasets()}
    assert "t_expense" in tables

    # append：整行重复的数据跳过（2026-11-02 清洗强化），返回 skipped_duplicates
    r_dup = svc.ingest_dataframe("t_expense", _sample_df().head(1), mode="append")
    assert r_dup["rows"] == 3 and r_dup["skipped_duplicates"] == 1
    # append：新行正常追加
    new_row = pd.DataFrame({"月份": ["2026-11"], "分类": ["办公费"], "金额": [300.0]})
    r_new = svc.ingest_dataframe("t_expense", new_row, mode="append")
    assert r_new["rows"] == 4 and r_new["skipped_duplicates"] == 0
    r2 = svc.run_query("SELECT count(*) AS n FROM t_expense")
    assert r2["rows"][0][0] == 4

    # 非法表名 / 非法模式
    with pytest.raises(ValueError):
        svc.ingest_dataframe("Bad-Table", _sample_df())
    with pytest.raises(ValueError):
        svc.ingest_dataframe("t_expense", _sample_df(), mode="upsert")


def test_vault_quality_overview(vault_dir):
    """数据仓质量画像（2026-11-02）：整行重复计数与高缺失列。"""
    df = pd.DataFrame({
        "a": [1, 1, 2, 3],
        "b": ["x", "x", "y", "z"],
        "c": [None, None, "k", "m"],
    })
    svc.ingest_dataframe("t_quality", df)
    q = svc.vault_quality_overview()
    assert q["available"] is True
    t = next(x for x in q["tables"] if x["table"] == "t_quality")
    assert t["rows"] == 4
    assert t["dup_rows"] == 1
    heavy_cols = {h["column"] for h in t["null_heavy"]}
    assert "c" in heavy_cols
    assert q["summary"]["dup_tables"] >= 1


def test_append_dedupe_semantics(vault_dir):
    base = pd.DataFrame({"a": [1, 2], "b": ["x", "y"]})
    r0 = svc.ingest_dataframe("t_dedupe", base)
    assert r0["rows"] == 2
    # 批内重复保留：只与库中已有行比较
    intra = pd.DataFrame({"a": [3, 3], "b": ["z", "z"]})
    r1 = svc.ingest_dataframe("t_dedupe", intra, mode="append")
    assert r1["rows"] == 4 and r1["skipped_duplicates"] == 0
    # 部分重叠：只补新行
    part = pd.DataFrame({"a": [1, 2, 5], "b": ["x", "y", "w"]})
    r2 = svc.ingest_dataframe("t_dedupe", part, mode="append")
    assert r2["rows"] == 5 and r2["skipped_duplicates"] == 2
    # 全重复：一行不增
    r3 = svc.ingest_dataframe("t_dedupe", base, mode="append")
    assert r3["rows"] == 5 and r3["skipped_duplicates"] == 2


def test_ingest_uploaded_file(vault_dir, tmp_path):
    path = tmp_path / "expense.xlsx"
    _sample_df().to_excel(path, index=False)
    result = svc.ingest_uploaded_file("t_from_file", "expense.xlsx", path.read_bytes())
    assert result["rows"] == 3 and result["source"] == "expense.xlsx"

    # 扩展名白名单
    with pytest.raises(ValueError):
        svc.ingest_uploaded_file("t_from_file", "evil.exe", b"MZ...")
    # 表名校验（非法字符）
    with pytest.raises(ValueError):
        svc.ingest_uploaded_file("bad-name", "expense.xlsx", path.read_bytes())


def test_ingest_uploaded_bad_content_not_archived(vault_dir, tmp_path):
    """解析失败：报错且 inbox 不留垃圾文件。"""
    with pytest.raises(Exception):
        svc.ingest_uploaded_file("t_bad", "bad.xlsx", b"not an excel file")
    inbox = vault_dir / "finance_vault" / "inbox"
    assert not inbox.exists() or not any(inbox.iterdir())


# ---------------- 只读 SQL 校验 ----------------

def test_readonly_sql_validation(vault_dir):
    svc.ingest_dataframe("t_v", _sample_df())

    # 正常查询
    r = svc.run_query("SELECT 分类, sum(金额) AS total FROM t_v GROUP BY 分类")
    assert r["row_count"] == 2
    assert r["rows"][0][0] in ("办公费", "差旅费")

    # 注入防护：全部应被拒
    for bad in [
        "DROP TABLE t_v",
        "DELETE FROM t_v",
        "SELECT * FROM t_v; DROP TABLE t_v",
        "SELECT * FROM not_registered",
        "INSERT INTO t_v VALUES ('x','y',1)",
        "UPDATE t_v SET 金额 = 0",
        "SELECT * FROM read_csv('/etc/passwd')",
        "",
    ]:
        with pytest.raises(svc.SqlRejected):
            svc.run_query(bad)

    # WITH + 注释 + 尾部 LIMIT 合法形态
    r2 = svc.run_query("-- 注释\nWITH c AS (SELECT * FROM t_v) SELECT count(*) AS n FROM c LIMIT 999999")
    assert r2["rows"][0][0] == 3
    assert r2["truncated"] is False


def test_preview_unknown_table(vault_dir):
    with pytest.raises(LookupError):
        svc.preview_table("not_here")


# ---------------- HTTP 端点 ----------------

def test_http_endpoints(client, vault_dir, tmp_path):
    path = tmp_path / "e.xlsx"
    _sample_df().to_excel(path, index=False)
    with open(path, "rb") as f:
        resp = client.post(
            "/api/finance/vault/ingest/file",
            params={"table": "t_http"},
            files={"file": ("e.xlsx", f, XLSX_MIME)},
        )
    assert resp.status_code == 200, resp.text
    assert resp.json()["rows"] == 3

    lst = client.get("/api/finance/vault/tables")
    assert lst.status_code == 200 and any(t["table"] == "t_http" for t in lst.json()["items"])

    q = client.post("/api/finance/vault/query", json={"sql": "SELECT * FROM t_http"})
    assert q.status_code == 200 and q.json()["row_count"] == 3

    bad = client.post("/api/finance/vault/query", json={"sql": "DROP TABLE t_http"})
    assert bad.status_code == 400

    pv = client.get("/api/finance/vault/tables/t_http/preview")
    assert pv.status_code == 200 and pv.json()["row_count"] == 3

    miss = client.get("/api/finance/vault/tables/nope/preview")
    assert miss.status_code == 404


def test_ingest_handler_remains_coroutine():
    """ingest_file 保持协程（await file.read 语义不变），解析入仓重活经 asyncio.to_thread 进线程池。"""
    assert asyncio.iscoroutinefunction(vault_router.ingest_file)


# ---------------- 模块注册与 Agent 工具 ----------------

def test_module_registration():
    loaded = {m.name: m for m in get_loaded_modules()}
    assert "finance_vault" in loaded
    assert "vault_list_tables" in TOOL_SPECS and TOOL_SPECS["vault_list_tables"]["readonly"] is True
    assert "vault_query" in TOOL_SPECS and TOOL_SPECS["vault_query"]["readonly"] is True


def test_agent_tools_behavior(vault_dir):
    svc.ingest_dataframe("t_agent", _sample_df())
    lst = TOOL_SPECS["vault_list_tables"]["func"]()
    assert lst["ok"] and any(t["table"] == "t_agent" for t in lst["tables"])
    q = TOOL_SPECS["vault_query"]["func"]("SELECT count(*) AS n FROM t_agent")
    assert q["ok"] and q["rows"][0][0] == 3
    bad = TOOL_SPECS["vault_query"]["func"]("DELETE FROM t_agent")
    assert bad["ok"] is False and "error" in bad


# ---------------- 数据池清洗与整合分析（2026-11-07） ----------------

import math  # noqa: E402

from app.modules.finance_vault import cleaning as cl  # noqa: E402


def _dirty_df() -> pd.DataFrame:
    return pd.DataFrame({
        "门店": ["A店", "A店", "B店", "B店", "C店", "—", None],
        "日期": ["2026-01-05", "2026-01-06", "2026/01/07", "not-a-date", "2026年1月8日", "2026-01-09", None],
        "金额": ["1,200.50", "800", "abc", "90.5", "45%", "—", None],
    })


def test_parse_helpers():
    assert cl._parse_num("1,234.5") == 1234.5
    assert cl._parse_num("45%") == 0.45
    assert cl._parse_num("１２３") == 123.0
    assert math.isnan(cl._parse_num("007"))       # 前导零编码保留文本
    assert math.isnan(cl._parse_num("310101199001011234"))  # 18位账号保留文本
    assert cl._parse_date("2026-01-05") is not None
    assert cl._parse_date("2026/1/7") is not None
    assert cl._parse_date("20260107") is not None
    assert cl._parse_date("2026年1月7日") is not None
    assert cl._parse_date("not-a-date") is None


def test_infer_kinds():
    df = pd.DataFrame({"d": ["2026-01-05", "2026-01-06", None],
                       "n": ["1", "2", "3"],
                       "t": ["甲", "乙", "丙"]})
    assert cl.infer_kinds(df) == {"d": "date", "n": "numeric", "t": "text"}


def test_profile_table(vault_dir):
    svc.ingest_dataframe("t_prof", _dirty_df())
    p = cl.profile_table("t_prof")
    assert p["table"] == "t_prof" and p["rows"] == 7
    col = {c["name"]: c for c in p["columns"]}
    assert col["金额"]["null_pct"] == 14.3  # 原表金额列本身有 1 个真空值（7 行里 1 个 None）
    assert p["dup_rows"] == 0
    assert len(p["columns"]) == 3 and all("top_values" in c for c in p["columns"])


def test_clean_preview_and_apply(vault_dir):
    svc.ingest_dataframe("t_dirty", _dirty_df())
    report = cl.clean_preview("t_dirty", None)
    assert report["rows_in"] == 7
    ops = {o["op"]: o for o in report["ops"]}
    assert ops["normalize_dirty_text"]["hits"] == 2      # 门店与金额各一处「—」
    assert ops["drop_all_empty_rows"]["hits"] == 1
    assert ops.get("invalid_date", {}).get("hits") == 1
    assert ops.get("invalid_numeric", {}).get("hits") == 1
    assert report["rows_kept"] == 4
    assert report["samples"], "被剔行必须留样本"
    # preview 不写库
    assert "t_dirty_clean" not in {t["table"] for t in svc.list_datasets()}

    result = cl.clean_apply("t_dirty", None, target_table="t_clean1")
    assert result["result_table"] == "t_clean1"
    assert svc.run_query("SELECT count(*) AS n FROM t_clean1")["rows"][0][0] == 4
    # 原表永远不动
    assert svc.run_query("SELECT count(*) AS n FROM t_dirty")["rows"][0][0] == 7
    # 清洗后金额列可求和（45% → 0.45，千分位归一）
    r = cl.aggregate_query("t_clean1", {"metrics": [{"column": "金额", "agg": "sum"}]})
    assert r["rows"][0][0] == pytest.approx(2000.95)


def test_clean_dedupe_and_null_cell(vault_dir):
    df = pd.DataFrame({
        "k": ["a", "a", "b", "b", "b", "b"],
        "v": ["10", "10", "20", "30", "40x", "50"],
        "全空列": [None] * 6,
    })
    svc.ingest_dataframe("t_dedupe2", df)
    recipe = {"ops": {"dedupe_rows": True, "drop_all_empty_columns": True},
              "invalid_policy": "null_cell"}
    report = cl.clean_preview("t_dedupe2", recipe)
    assert report["columns_dropped"] == ["全空列"]
    assert any(o["op"] == "dedupe_rows" and o["hits"] == 1 for o in report["ops"])
    # null_cell：40x 置空不剔行
    assert report["rows_kept"] == 5
    cl.clean_apply("t_dedupe2", recipe, target_table="t_dedupe2_out")
    q = svc.run_query("SELECT k, sum(v) AS s FROM t_dedupe2_out GROUP BY k ORDER BY k")
    assert q["rows"] == [["a", 10.0], ["b", 100.0]]


def test_clean_recipe_reuse_skips_missing_columns(vault_dir):
    svc.ingest_dataframe("t_r1", _dirty_df())
    recipe = cl.clean_preview("t_r1", None)["recipe"]
    other = pd.DataFrame({"门店": ["A", "B"], "金额": ["10", "20"]})
    svc.ingest_dataframe("t_other", other)
    result = cl.clean_apply("t_other", {"ops": {"required_columns": ["日期", "门店"]}},
                            target_table="t_other_clean")
    assert result["rows"] == 2
    assert any("日期" in w for w in result["report"]["warnings"])


def test_clean_log_recorded(vault_dir):
    svc.ingest_dataframe("t_log", _dirty_df())
    cl.clean_apply("t_log", None, target_table="t_log_out")
    logs = cl.get_clean_logs("t_log")
    assert logs and logs[0]["source_table"] == "t_log" and logs[0]["result_table"] == "t_log_out"
    assert logs[0]["recipe"]["invalid_policy"] == "drop_row"


def test_aggregate_query_group_sum(vault_dir):
    svc.ingest_dataframe("t_agg", _sample_df())
    r = cl.aggregate_query("t_agg", {
        "group_by": ["月份"],
        "metrics": [{"column": "金额", "agg": "sum"}, {"column": "*", "agg": "count"}],
    })
    assert r["columns"] == ["月份", "金额_sum", "总行数_count"]
    by_month = {row[0]: row[1] for row in r["rows"]}
    assert by_month["2026-09"] == 300.0 and by_month["2026-10"] == 150.0
    assert {row[0]: row[2] for row in r["rows"]}["2026-09"] == 2
    assert r["total_groups"] == 2 and r["truncated"] is False


def test_aggregate_filters_ops(vault_dir):
    svc.ingest_dataframe("t_aggf", _sample_df())
    spec = {"group_by": ["分类"], "metrics": [{"column": "金额", "agg": "sum"}]}
    r_eq = cl.aggregate_query("t_aggf", {**spec, "filters": [{"column": "月份", "op": "eq", "value": "2026-09"}]})
    assert {row[0]: row[1] for row in r_eq["rows"]} == {"办公费": 300.0}
    r_ct = cl.aggregate_query("t_aggf", {**spec, "filters": [{"column": "分类", "op": "contains", "value": "办公"}]})
    assert {row[0]: row[1] for row in r_ct["rows"]} == {"办公费": 300.0}
    r_in = cl.aggregate_query("t_aggf", {**spec, "filters": [{"column": "月份", "op": "in", "value": ["2026-10"]}]})
    assert {row[0]: row[1] for row in r_in["rows"]} == {"差旅费": 150.0}
    r_gt = cl.aggregate_query("t_aggf", {**spec, "filters": [{"column": "金额", "op": "gt", "value": 120}]})
    assert {row[0]: row[1] for row in r_gt["rows"]} == {"办公费": 200.0, "差旅费": 150.0}


def test_aggregate_validation_errors(vault_dir):
    svc.ingest_dataframe("t_badagg", _sample_df())
    for bad in (
        {"group_by": ["不存在"]},
        {"metrics": [{"column": "金额", "agg": "median"}]},
        {"metrics": [{"column": "分类", "agg": "sum"}]},
        {"metrics": [{"column": "*", "agg": "sum"}]},
        {"filters": [{"column": "月份", "op": "like", "value": "x"}]},
        {"filters": [{"column": "月份", "op": "eq"}]},
        {},
    ):
        with pytest.raises(ValueError):
            cl.aggregate_query("t_badagg", bad)


def test_aggregate_export_xlsx(vault_dir):
    from io import BytesIO
    from openpyxl import load_workbook

    svc.ingest_dataframe("t_export", pd.DataFrame({"类别": ["=SUM(A1)", "正常"], "v": [1, 2]}))
    data, filename = cl.export_aggregate_xlsx(
        "t_export", {"group_by": ["类别"], "metrics": [{"column": "*", "agg": "count"}]})
    assert filename.endswith(".xlsx") and "t_export" in filename
    ws = load_workbook(BytesIO(data)).active
    assert ws.cell(1, 1).value == "类别"
    cells = [ws.cell(r, 1).value for r in range(2, ws.max_row + 1)]
    assert any(str(v).startswith("'") for v in cells)  # = 开头文本被 safe_cell 转义


def test_http_clean_and_aggregate_endpoints(client, vault_dir, tmp_path):
    svc.ingest_dataframe("t_http2", _dirty_df())
    pv = client.post("/api/finance/vault/tables/t_http2/clean/preview", json={})
    assert pv.status_code == 200 and pv.json()["rows_in"] == 7
    ap = client.post("/api/finance/vault/tables/t_http2/clean/apply", json={"target_table": "t_http2_out"})
    assert ap.status_code == 200 and ap.json()["result_table"] == "t_http2_out"
    lg = client.get("/api/finance/vault/tables/t_http2/clean/log")
    assert lg.status_code == 200 and len(lg.json()["items"]) == 1
    pf = client.get("/api/finance/vault/tables/t_http2/profile")
    assert pf.status_code == 200 and pf.json()["rows"] == 7
    ag = client.post("/api/finance/vault/tables/t_http2_out/aggregate", json={"group_by": ["门店"], "metrics": [{"column": "*", "agg": "count"}]})
    assert ag.status_code == 200 and ag.json()["total_groups"] == 3
    ex = client.post("/api/finance/vault/tables/t_http2_out/aggregate/export", json={"metrics": [{"column": "*", "agg": "count"}]})
    assert ex.status_code == 200 and ex.headers["content-type"].startswith(XLSX_MIME)
    bad_agg = client.post("/api/finance/vault/tables/t_http2_out/aggregate", json={"group_by": ["不存在"]})
    assert bad_agg.status_code == 400
    sv = client.post("/api/finance/vault/tables/t_http2_out/aggregate/save",
                     json={"target_table": "t_http2_saved", "metrics": [{"column": "*", "agg": "count"}]})
    assert sv.status_code == 200 and sv.json()["result_table"] == "t_http2_saved"
    assert sv.json()["rows"] == 1  # 无分组 count(*) → 单行汇总
    # 目标已存在默认 409（不静默 replace），显式 allow_overwrite 才覆盖
    dup = client.post("/api/finance/vault/tables/t_http2_out/aggregate/save",
                      json={"target_table": "t_http2_saved", "metrics": [{"column": "*", "agg": "count"}]})
    assert dup.status_code == 409
    over = client.post("/api/finance/vault/tables/t_http2_out/aggregate/save",
                       json={"target_table": "t_http2_saved", "allow_overwrite": True,
                             "metrics": [{"column": "*", "agg": "count"}]})
    assert over.status_code == 200 and over.json()["result_table"] == "t_http2_saved"
