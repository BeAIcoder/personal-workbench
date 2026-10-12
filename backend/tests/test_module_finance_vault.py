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
    tables = {t["table"] for t in svc.list_datasets()}
    assert "t_expense" in tables

    # append：按列名追加
    svc.ingest_dataframe("t_expense", _sample_df().head(1), mode="append")
    assert svc.ingest_dataframe.__name__  # 契约函数存在
    r2 = svc.run_query("SELECT count(*) AS n FROM t_expense")
    assert r2["rows"][0][0] == 4

    # 非法表名 / 非法模式
    with pytest.raises(ValueError):
        svc.ingest_dataframe("Bad-Table", _sample_df())
    with pytest.raises(ValueError):
        svc.ingest_dataframe("t_expense", _sample_df(), mode="upsert")


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
