"""finance-invoice-vault 区域 E2E 探测发现的 bug 回归测试（后端）。

覆盖本轮探测发现并修复的两个 500 降级缺陷：
- 损坏 xlsx（ZIP 魔数但残缺）抛 zipfile.BadZipFile（非 ValueError）→ 路由层漏捕 → 500；
- append 模式列不匹配抛 duckdb.BinderException（非 ValueError）→ 路由层漏捕 → 500。

隔离设施沿用 conftest：client 夹具建独立 SQLite 库；vault_dir 把 DuckDB 库文件
重定向到 tmp_path（不写真实财务数据目录）。
"""
import io

import pandas as pd
import pytest

from app.modules.finance_vault import service as svc

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@pytest.fixture()
def vault_dir(monkeypatch, tmp_path, client):
    """DuckDB 库文件重定向到 tmp_path；client 夹具负责建表。"""
    base = tmp_path / "vault_data"
    monkeypatch.setattr(svc, "get_finance_data_dir", lambda: base)
    return base


def _xlsx_bytes(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    df.to_excel(buf, index=False)
    return buf.getvalue()


def test_corrupt_xlsx_zip_magic_returns_400(client, vault_dir):
    """损坏 xlsx（ZIP 魔数但内容残缺）：BadZipFile 必须降级为 400，而不是裸 500。"""
    resp = client.post(
        "/api/finance/vault/ingest/file",
        params={"table": "t_corrupt"},
        files={"file": ("corrupt.xlsx", b"PK\x03\x04broken zip magic but not a real xlsx", XLSX_MIME)},
    )
    assert resp.status_code == 400, f"应返回 400，实际 {resp.status_code}: {resp.text[:200]}"
    assert "解析失败" in resp.json()["detail"]
    # 未入仓：登记表与表清单都不应出现该表
    assert client.get("/api/finance/vault/tables").json()["items"] == []


def test_ingest_append_column_mismatch_returns_400(client, vault_dir):
    """append 模式列不匹配：duckdb.BinderException 必须降级为 400，而不是裸 500。"""
    svc.ingest_dataframe("t_app", pd.DataFrame({"月份": ["2026-09"], "金额": [100.0]}))
    other = _xlsx_bytes(pd.DataFrame({"完全不同的列": ["x"], "另一个": [1]}))
    resp = client.post(
        "/api/finance/vault/ingest/file",
        params={"table": "t_app", "mode": "append"},
        files={"file": ("other.xlsx", other, XLSX_MIME)},
    )
    assert resp.status_code == 400, f"应返回 400，实际 {resp.status_code}: {resp.text[:200]}"
    assert "入仓失败" in resp.json()["detail"]
    # 原表数据不受失败 append 影响
    result = svc.run_query("SELECT count(*) AS n FROM t_app")
    assert result["rows"][0][0] == 1


def test_ingest_plain_garbage_xlsx_returns_400(client, vault_dir):
    """非 ZIP 魔数的纯垃圾 xlsx：pandas 的 ValueError 也应稳定落到 400。"""
    resp = client.post(
        "/api/finance/vault/ingest/file",
        params={"table": "t_garbage"},
        files={"file": ("garbage.xlsx", b"not an excel file at all", XLSX_MIME)},
    )
    assert resp.status_code == 400, f"应返回 400，实际 {resp.status_code}: {resp.text[:200]}"
    inbox = vault_dir / "finance_vault" / "inbox"
    leftovers = list(inbox.iterdir()) if inbox.exists() else []
    assert not leftovers, f"失败上传不应在 inbox 留档：{leftovers}"
