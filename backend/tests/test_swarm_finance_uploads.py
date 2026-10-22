"""Swarm 任务 finance-standard-uploads 聚焦测试（本任务白名单内仅此一个测试文件）。

覆盖（对应审计台账 A01 无界上传 / A05 同步重活阻塞 / A06 行数上限 / A09 表函数名与查询绕过）：
- 费用/发票/数据仓/个人记账四个上传端点经共享 read_upload_limited 限额读取：
  超限映射 400、读取有界（超限即止，不读满全量）、超限不落 inbox 孤儿文件；
- 发票多文件批次在读取期即执行合计上限，超限后后续文件根本不被读取；
- 个人 CSV 的同步 service.import_csv 经 asyncio.to_thread 卸载，慢导入期间事件循环保持响应；
- 数据仓 Excel/CSV 入仓按共享 MAX_IMPORT_ROWS 行数护栏拒绝（monkeypatch 小值，不生成巨型文件）；
- 数据集名仅允许安全普通标识符（拒绝 read_csv 等表函数名/括号/点号），既有正常名字兼容；
- 只读查询白名单不被子查询/CTE 内的表函数绕过，正常子查询与 CTE 不受影响；
- 共享上传读取器缺失时四个端点明确 503 失败关闭，绝不退回无界 file.read()；
- 各端点正常上传行为不变（内容完整落档、结果正常返回）。

约定：共享接口（app.upload.read_upload_limited / app.import_limits.MAX_IMPORT_ROWS）由
core 任务提供；本文件用与契约一致的假上传替身和 monkeypatch 小值验证消费侧行为；
全部使用合成数据与临时目录（conftest 已钉住临时 SQLite），不碰真实业务文件。
"""
import asyncio
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.config import settings
from app.database import SessionLocal
from app.modules.finance_expense import router as expense_router
from app.modules.finance_expense import service as expense_svc
from app.modules.finance_invoice import router as invoice_router
from app.modules.finance_invoice import service as invoice_svc
from app.modules.finance_ledger_personal import router as personal_router
from app.modules.finance_ledger_personal import service as personal_svc
from app.modules.finance_vault import router as vault_router
from app.modules.finance_vault import service as vault_svc

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
PDF_MIME = "application/pdf"
FAKE_PDF = b"%PDF-1.4 synthetic-test-bytes"
PL_BASE = "/api/finance/ledger/personal"
_1MB = 1024 * 1024

# 合成 CSV（3 行数据）：数据仓入仓/行数护栏复用
_CSV_3_ROWS = "月份,分类,金额\n2026-09,办公费,100.0\n2026-09,办公费,200.0\n2026-10,差旅费,150.0\n"


class _FakeUpload:
    """假上传文件：记录已消费字节与读取规格（复现 A01 旧行为：读满全量才限长）。"""

    def __init__(self, data: bytes, filename: str = "upload.bin"):
        self._data = data
        self._pos = 0
        self.filename = filename
        self.consumed = 0
        self.read_sizes: list[int] = []

    async def read(self, size: int = -1) -> bytes:
        self.read_sizes.append(size)
        chunk = self._data[self._pos:] if size is None or size < 0 else self._data[self._pos:self._pos + size]
        self._pos += len(chunk)
        self.consumed += len(chunk)
        return chunk


def make_fields(**kw) -> dict:
    """构造一份合成 ParsedInvoice 字段（发票引擎全部被打桩，字节不真正解析）。"""
    base = dict(
        invoice_number="10000001",
        check_code="c001",
        buyer_name="示例购方公司",
        seller_name="示例销方公司",
        amount="100.00",
        tax_amount="13.00",
        tax_rate="0.13",
        invoice_date="2026年10月01日",
        invoice_type="普票",
        invoice_direction="正数发票",
        project_name="示例项目",
        brand_name="",
        remark="合成备注",
        extraction_success=True,
    )
    base.update(kw)
    return base


@pytest.fixture()
def finance_dirs(monkeypatch, tmp_path):
    """费用/发票/个人记账模块数据目录重定向到 tmp_path（不碰真实 finance 数据目录）。"""
    base = tmp_path / "finance"
    monkeypatch.setattr(settings, "finance_data_dir", str(base))
    return base


@pytest.fixture()
def vault_dir(monkeypatch, tmp_path, client):
    """DuckDB 库文件与 inbox 重定向到 tmp_path；client 夹具负责建表。"""
    base = tmp_path / "vault_data"
    monkeypatch.setattr(vault_svc, "get_finance_data_dir", lambda: base)
    return base


@pytest.fixture()
def mock_engine(monkeypatch):
    """打桩发票引擎：_require_engine 放行；按文件名返回罐头解析结果。"""
    canned: dict[str, dict] = {}
    monkeypatch.setattr(invoice_svc, "_require_engine", lambda: None)

    def fake_parse(pdf_path):
        fields = canned.get(Path(pdf_path).name)
        return invoice_svc.ParsedInvoice(**fields) if fields is not None else None

    monkeypatch.setattr(invoice_svc, "_parse_pdf_with_engine", fake_parse)
    return canned


def _inbox_empty(*paths: Path) -> bool:
    return all(not p.exists() or not any(p.iterdir()) for p in paths)


# ---------------- A01：有界读取（超限即止，不读满全量） ----------------


def test_expense_upload_oversize_reads_bounded_and_leaves_no_inbox(client, finance_dirs, monkeypatch):
    """费用上传超限：经共享读取器有界读取（旧行为读满 4MB 才抛），且 inbox 不留孤儿。"""
    monkeypatch.setattr(expense_svc, "MAX_UPLOAD_BYTES", 1000)
    fake = _FakeUpload(b"x" * (4 * 1024 * 1024), filename="big.xlsx")
    with pytest.raises(HTTPException) as ei:
        asyncio.run(expense_router.classify(file=fake, db=SessionLocal()))
    assert ei.value.status_code == 400
    assert "上限" in str(ei.value.detail)
    assert fake.consumed <= 2 * _1MB  # 有界：分片读到一个即止
    assert _inbox_empty(finance_dirs / "finance_expense" / "inbox")


def test_invoice_single_file_oversize_reads_bounded(client, finance_dirs, monkeypatch):
    """发票单文件超限：有界读取 + 400，不落任何批次目录。"""
    monkeypatch.setattr(invoice_svc, "MAX_FILE_BYTES", 8)
    fake = _FakeUpload(b"x" * (4 * 1024 * 1024), filename="big.pdf")
    with pytest.raises(HTTPException) as ei:
        asyncio.run(invoice_router.create_batch(name="", files=[fake], db=SessionLocal()))
    assert ei.value.status_code == 400 and "上限" in str(ei.value.detail)
    assert fake.consumed <= 2 * _1MB
    assert _inbox_empty(finance_dirs / "finance_invoice" / "inbox")


def test_invoice_batch_aggregate_limit_stops_reading_early(client, finance_dirs, monkeypatch):
    """发票批次合计上限在读取期即执行：超限后后续文件根本不被读取（旧行为读满全部再校验）。"""
    monkeypatch.setattr(invoice_svc, "MAX_BATCH_BYTES", 20)
    f1 = _FakeUpload(b"a" * 12, filename="a.pdf")
    f2 = _FakeUpload(b"b" * 12, filename="b.pdf")
    f3 = _FakeUpload(b"c" * 12, filename="c.pdf")
    with pytest.raises(HTTPException) as ei:
        asyncio.run(invoice_router.create_batch(name="", files=[f1, f2, f3], db=SessionLocal()))
    assert ei.value.status_code == 400 and "上限" in str(ei.value.detail)
    assert (f1.consumed, f2.consumed, f3.consumed) == (12, 12, 0)
    assert _inbox_empty(finance_dirs / "finance_invoice" / "inbox")


def test_vault_upload_oversize_reads_bounded_and_leaves_no_inbox(client, vault_dir, monkeypatch):
    """数据仓上传超限：有界读取 + 400，inbox 不留孤儿。"""
    monkeypatch.setattr(vault_svc, "MAX_UPLOAD_BYTES", 1000)
    fake = _FakeUpload(b"x" * (4 * 1024 * 1024), filename="big.csv")
    with pytest.raises(HTTPException) as ei:
        asyncio.run(vault_router.ingest_file(file=fake, table="t_probe", mode="replace", sheet=""))
    assert ei.value.status_code == 400 and "上限" in str(ei.value.detail)
    assert fake.consumed <= 2 * _1MB
    assert _inbox_empty(vault_dir / "finance_vault" / "inbox")


def test_personal_import_oversize_reads_bounded_and_leaves_no_inbox(client, finance_dirs, monkeypatch):
    """个人 CSV 导入超限：有界读取 + 400，inbox 不留孤儿。"""
    monkeypatch.setattr(personal_svc, "MAX_IMPORT_BYTES", 1000)
    fake = _FakeUpload(b"x" * (4 * 1024 * 1024), filename="big.csv")
    with pytest.raises(HTTPException) as ei:
        asyncio.run(personal_router.import_csv(file=fake, db=SessionLocal()))
    assert ei.value.status_code == 400 and "上限" in str(ei.value.detail)
    assert fake.consumed <= 2 * _1MB
    assert _inbox_empty(finance_dirs / "finance_ledger_personal" / "inbox")


# ---------------- A01：HTTP 层超限回归（逐端点） ----------------


def test_expense_http_oversize_400(client, finance_dirs, monkeypatch):
    monkeypatch.setattr(expense_svc, "MAX_UPLOAD_BYTES", 16)
    resp = client.post(
        "/api/finance/expense/classify",
        files={"file": ("big.xlsx", b"x" * 64, XLSX_MIME)},
    )
    assert resp.status_code == 400 and "上限" in resp.json()["detail"]
    assert _inbox_empty(finance_dirs / "finance_expense" / "inbox")


def test_invoice_http_oversize_400(client, finance_dirs, monkeypatch):
    monkeypatch.setattr(invoice_svc, "MAX_FILE_BYTES", 8)
    resp = client.post(
        "/api/finance/invoice/batches",
        files=[("files", ("big.pdf", b"x" * 9, PDF_MIME))],
    )
    assert resp.status_code == 400 and "上限" in resp.json()["detail"]


def test_invoice_http_batch_aggregate_400_no_batch_dir(client, finance_dirs, monkeypatch):
    """批次合计超限：400 且不创建任何批次 inbox 目录。"""
    monkeypatch.setattr(invoice_svc, "MAX_BATCH_BYTES", 20)
    resp = client.post(
        "/api/finance/invoice/batches",
        files=[
            ("files", ("a.pdf", b"a" * 12, PDF_MIME)),
            ("files", ("b.pdf", b"b" * 12, PDF_MIME)),
        ],
    )
    assert resp.status_code == 400 and "上限" in resp.json()["detail"]
    inbox = finance_dirs / "finance_invoice" / "inbox"
    assert not inbox.exists() or not any(inbox.glob("batch_*"))


def test_vault_http_oversize_400(client, vault_dir, monkeypatch):
    monkeypatch.setattr(vault_svc, "MAX_UPLOAD_BYTES", 16)
    resp = client.post(
        "/api/finance/vault/ingest/file",
        params={"table": "t_probe"},
        files={"file": ("big.csv", b"x" * 64, "text/csv")},
    )
    assert resp.status_code == 400 and "上限" in resp.json()["detail"]
    assert _inbox_empty(vault_dir / "finance_vault" / "inbox")


def test_personal_http_oversize_400(client, finance_dirs, monkeypatch):
    monkeypatch.setattr(personal_svc, "MAX_IMPORT_BYTES", 16)
    resp = client.post(
        f"{PL_BASE}/import/csv",
        files={"file": ("big.csv", b"x" * 64, "text/csv")},
    )
    assert resp.status_code == 400 and "上限" in resp.json()["detail"]
    assert _inbox_empty(finance_dirs / "finance_ledger_personal" / "inbox")


# ---------------- A01：共享读取器接线与失败关闭 ----------------


def test_shared_reader_wired_into_four_routers():
    """四个上传路由确实接上共享读取器（而非缺失回退分支）。"""
    from app.upload import read_upload_limited as shared_reader

    for r in (expense_router, invoice_router, vault_router, personal_router):
        assert getattr(r, "read_upload_limited", None) is shared_reader


def test_upload_handlers_fail_closed_when_shared_reader_missing(
    client, finance_dirs, vault_dir, monkeypatch
):
    """共享读取器缺失：四个端点明确 503 且零字节读取，绝不退回无界 file.read()。"""
    for r in (expense_router, invoice_router, vault_router, personal_router):
        monkeypatch.setattr(r, "read_upload_limited", None)
    # 桩掉下游重活：证明失败发生在读取阶段，而非引擎/解析阶段的偶发 503
    monkeypatch.setattr(expense_svc, "ensure_engine_ready", lambda: None)
    monkeypatch.setattr(expense_svc, "classify_file", lambda db, original, stored: SimpleNamespace(id=1))
    monkeypatch.setattr(expense_svc, "run_to_dict", lambda run: {"id": 1})
    monkeypatch.setattr(invoice_svc, "_require_engine", lambda: None)
    monkeypatch.setattr(
        invoice_svc, "_parse_pdf_with_engine", lambda p: invoice_svc.ParsedInvoice(**make_fields())
    )

    fake_xlsx = _FakeUpload(b"x" * 16, filename="a.xlsx")
    with pytest.raises(HTTPException) as ei:
        asyncio.run(expense_router.classify(file=fake_xlsx, db=SessionLocal()))
    assert ei.value.status_code == 503
    assert fake_xlsx.consumed == 0

    fake_pdf = _FakeUpload(FAKE_PDF, filename="a.pdf")
    with pytest.raises(HTTPException) as ei:
        asyncio.run(invoice_router.create_batch(name="", files=[fake_pdf], db=SessionLocal()))
    assert ei.value.status_code == 503
    assert fake_pdf.consumed == 0

    fake_csv = _FakeUpload(b"a,b\n1,2\n", filename="a.csv")
    with pytest.raises(HTTPException) as ei:
        asyncio.run(vault_router.ingest_file(file=fake_csv, table="t_fc", mode="replace", sheet=""))
    assert ei.value.status_code == 503
    assert fake_csv.consumed == 0

    fake_pl = _FakeUpload(b"a,b\n1,2\n", filename="a.csv")
    with pytest.raises(HTTPException) as ei:
        asyncio.run(personal_router.import_csv(file=fake_pl, db=SessionLocal()))
    assert ei.value.status_code == 503
    assert fake_pl.consumed == 0


# ---------------- A05：个人 CSV 同步重活卸载 ----------------


def test_personal_import_csv_runs_off_event_loop(client, finance_dirs, monkeypatch):
    """import_csv 经 asyncio.to_thread 卸载：慢导入期间事件循环保持响应（旧行为同步占循环）。"""
    entered, release = threading.Event(), threading.Event()
    ran: dict[str, threading.Thread] = {}

    def slow_import(db, content):
        ran["thread"] = threading.current_thread()
        entered.set()
        assert release.wait(5)
        return {
            "imported": 1,
            "skipped": 0,
            "errors": [],
            "created_accounts": [],
            "created_categories": [],
        }

    monkeypatch.setattr(personal_svc, "import_csv", slow_import)
    fake = _FakeUpload("日期,类型,金额,账户,分类,备注\n2026-10-03,支出,10,现金,餐饮,\n".encode("utf-8-sig"), filename="slow.csv")

    async def scenario():
        task = asyncio.create_task(personal_router.import_csv(file=fake, db=SessionLocal()))
        for _ in range(300):
            if entered.is_set():
                break
            await asyncio.sleep(0.01)
        assert entered.is_set(), "import_csv 未被调用"
        # 探针协程必须能立即推进：若导入同步跑在事件循环内，循环被占则探针永远排不上队
        probe = asyncio.Event()

        async def _touch():
            probe.set()

        await asyncio.wait_for(_touch(), timeout=5)
        release.set()
        return await asyncio.wait_for(task, timeout=5)

    result = asyncio.run(scenario())
    assert result["imported"] == 1
    assert ran["thread"] is not threading.main_thread()


# ---------------- A06：数据仓行数护栏 ----------------


def test_vault_ingest_enforces_max_import_rows(client, vault_dir, tmp_path, monkeypatch):
    """入仓按共享 MAX_IMPORT_ROWS 护栏拒绝（monkeypatch 小值）：超限无半成品，正常行数不变。"""
    path = tmp_path / "many.csv"
    path.write_text(_CSV_3_ROWS, encoding="utf-8")
    monkeypatch.setattr(vault_svc, "MAX_IMPORT_ROWS", 2, raising=False)
    with pytest.raises(ValueError, match="上限"):
        vault_svc.ingest_uploaded_file("t_many", "many.csv", path.read_bytes())
    assert _inbox_empty(vault_dir / "finance_vault" / "inbox")

    monkeypatch.setattr(vault_svc, "MAX_IMPORT_ROWS", 10, raising=False)
    result = vault_svc.ingest_uploaded_file("t_ok", "many.csv", path.read_bytes())
    assert result["rows"] == 3


def test_vault_ingest_row_limit_http_400_no_partial(client, vault_dir, tmp_path, monkeypatch):
    """HTTP 层行数超限：400、数据集未注册、inbox 不留档。"""
    path = tmp_path / "many.csv"
    path.write_text(_CSV_3_ROWS, encoding="utf-8")
    monkeypatch.setattr(vault_svc, "MAX_IMPORT_ROWS", 2, raising=False)
    with open(path, "rb") as f:
        resp = client.post(
            "/api/finance/vault/ingest/file",
            params={"table": "t_big"},
            files={"file": ("many.csv", f, "text/csv")},
        )
    assert resp.status_code == 400 and "上限" in resp.json()["detail"]
    tables = client.get("/api/finance/vault/tables").json()["items"]
    assert all(t["table"] != "t_big" for t in tables)
    assert _inbox_empty(vault_dir / "finance_vault" / "inbox")


def test_vault_ingest_row_limit_fails_closed_when_shared_limit_missing(
    client, vault_dir, tmp_path, monkeypatch
):
    """共享行数上限缺失：失败关闭抛 ValueError，绝不放行无上限入仓。"""
    path = tmp_path / "ok.csv"
    path.write_text("a,b\n1,2\n", encoding="utf-8")
    monkeypatch.setattr(vault_svc, "MAX_IMPORT_ROWS", None, raising=False)
    with pytest.raises(ValueError, match="暂不可用"):
        vault_svc.ingest_uploaded_file("t_fc", "ok.csv", path.read_bytes())


def test_vault_service_wired_to_shared_import_rows():
    """数据仓 service 确实接上共享上限（与 app.import_limits 同一对象，非回退值）。"""
    from app.import_limits import MAX_IMPORT_ROWS

    assert vault_svc.MAX_IMPORT_ROWS is MAX_IMPORT_ROWS


# ---------------- A09：数据集名安全标识符 ----------------


def test_vault_dataset_name_rejects_table_functions_and_unsafe_idents(vault_dir):
    """数据集名仅允许安全普通标识符：拒绝表函数名/括号/点号等，既有正常名字兼容。"""
    reserved = [
        "read_csv", "read_csv_auto", "read_parquet", "read_json", "read_json_auto",
        "read_ndjson", "read_blob", "read_text", "sniff_csv", "sniff_parquet",
        "parquet_scan", "parquet_schema", "delta_scan", "glob", "range",
        "generate_series", "unnest", "query", "query_table", "sqlite_scan",
        "postgres_scan", "mysql_scan", "iceberg_scan", "duckdb_tables",
    ]
    unsafe = ["bad.name", "tbl(1)", "t-v", "Tbl", "a b", "", " ", "a" * 65, "t;drop", "t/x"]
    for bad in reserved + unsafe:
        with pytest.raises(ValueError):
            vault_svc.validate_table_name(bad)
    for ok in ["t_expense", "sales2026", "a", "_x9", "t_2026_10", "a" * 64]:
        assert vault_svc.validate_table_name(ok) == ok


def test_vault_ingest_rejects_reserved_table_name_without_archive(client, vault_dir, tmp_path):
    """保留表函数名入仓：校验即拒，inbox 不留档。"""
    path = tmp_path / "e.csv"
    path.write_text("a,b\n1,2\n", encoding="utf-8")
    with pytest.raises(ValueError, match="非法表名"):
        vault_svc.ingest_uploaded_file("read_csv", "e.csv", path.read_bytes())
    assert _inbox_empty(vault_dir / "finance_vault" / "inbox")


# ---------------- A09：查询白名单不被子查询表函数绕过 ----------------


def test_vault_query_whitelist_resists_subquery_table_functions(client, vault_dir):
    """派生表/CTE/IN 子查询/UNION 内的表函数同样命中白名单校验，全部拒绝。"""
    vault_svc.ingest_dataframe("t_v", _sample_df())
    for bad in [
        "SELECT * FROM (SELECT * FROM read_csv('/etc/passwd'))",
        "WITH c AS (SELECT * FROM read_csv('/etc/passwd')) SELECT * FROM c",
        "SELECT * FROM t_v WHERE 金额 IN (SELECT * FROM read_csv('x.csv'))",
        "SELECT * FROM t_v UNION ALL SELECT * FROM read_csv('x.csv')",
        "SELECT * FROM read_csv('x.csv')",
        "SELECT * FROM (SELECT * FROM t_v) x JOIN read_csv('y.csv') ON true",
    ]:
        with pytest.raises(vault_svc.SqlRejected):
            vault_svc.run_query(bad)


def test_vault_query_whitelist_allows_normal_subqueries(client, vault_dir):
    """正常派生表/CTE/JOIN 子查询不受影响。"""
    vault_svc.ingest_dataframe("t_v", _sample_df())
    r = vault_svc.run_query("SELECT count(*) AS n FROM (SELECT * FROM t_v) sub")
    assert r["rows"][0][0] == 3
    r2 = vault_svc.run_query("WITH c AS (SELECT * FROM t_v) SELECT count(*) AS n FROM c")
    assert r2["rows"][0][0] == 3
    r3 = vault_svc.run_query("SELECT count(*) AS n FROM t_v a JOIN (SELECT 1 AS one) b ON true")
    assert r3["rows"][0][0] == 3


def _sample_df():
    import pandas as pd

    return pd.DataFrame(
        {
            "月份": ["2026-09", "2026-09", "2026-10"],
            "分类": ["办公费", "办公费", "差旅费"],
            "金额": [100.0, 200.0, 150.0],
        }
    )


# ---------------- 正常上传行为不变（回归） ----------------


def test_expense_http_normal_upload_archives_inbox(client, finance_dirs, monkeypatch):
    """正常上传：内容经共享读取器完整落档 inbox，流程照常返回 201。"""
    monkeypatch.setattr(expense_svc, "ensure_engine_ready", lambda: None)
    monkeypatch.setattr(expense_svc, "classify_file", lambda db, original, stored: SimpleNamespace(id=7))
    monkeypatch.setattr(
        expense_svc,
        "run_to_dict",
        lambda run: {
            "id": 7,
            "original_filename": "a.xlsx",
            "total_rows": 0,
            "matched_rows": 0,
            "status": "success",
            "duration_sec": 0.0,
            "artifact_path": "",
            "created_at": "2026-10-22 10:00:00",
        },
    )
    payload = b"PK\x03\x04synthetic-xlsx-content"
    resp = client.post(
        "/api/finance/expense/classify",
        files={"file": ("a.xlsx", payload, XLSX_MIME)},
    )
    assert resp.status_code == 201, resp.text
    inbox = finance_dirs / "finance_expense" / "inbox"
    stored = list(inbox.glob("*.xlsx"))
    assert len(stored) == 1 and stored[0].read_bytes() == payload


def test_invoice_http_normal_upload_unchanged(client, finance_dirs, mock_engine):
    """正常发票批次：多文件上传、解析、落档行为不变。"""
    mock_engine["a.pdf"] = make_fields(invoice_number="8001", check_code="c1")
    resp = client.post(
        "/api/finance/invoice/batches",
        files=[("files", ("a.pdf", FAKE_PDF, PDF_MIME))],
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["file_count"] == 1 and body["items"][0]["status"] == "parsed"
    stored = finance_dirs / "finance_invoice" / "inbox" / f"batch_{body['id']}" / "a.pdf"
    assert stored.is_file() and stored.read_bytes() == FAKE_PDF


def test_vault_http_normal_csv_ingest_unchanged(client, vault_dir, tmp_path):
    """正常 CSV 入仓：行数/留档/登记行为不变。"""
    path = tmp_path / "e.csv"
    path.write_text(_CSV_3_ROWS, encoding="utf-8")
    with open(path, "rb") as f:
        resp = client.post(
            "/api/finance/vault/ingest/file",
            params={"table": "t_http_mine"},
            files={"file": ("e.csv", f, "text/csv")},
        )
    assert resp.status_code == 200, resp.text
    assert resp.json()["rows"] == 3
    inbox = vault_dir / "finance_vault" / "inbox"
    assert len(list(inbox.glob("*_e.csv"))) == 1


def test_personal_http_normal_import_unchanged(client, finance_dirs):
    """正常个人 CSV 导入：计数正确且原件留档 inbox。"""
    content = "日期,类型,金额,账户,分类,备注\n2026-10-03,支出,10,现金,餐饮,\n".encode("utf-8-sig")
    resp = client.post(
        f"{PL_BASE}/import/csv",
        files={"file": ("ok.csv", content, "text/csv")},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["imported"] == 1
    inbox = finance_dirs / "finance_ledger_personal" / "inbox"
    assert len(list(inbox.glob("*_ok.csv"))) == 1
