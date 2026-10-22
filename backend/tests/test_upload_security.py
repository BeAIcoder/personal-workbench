"""Swarm 任务 upload-contract-tests 聚焦测试（本任务白名单内仅此一个测试文件）。

跨模块上传契约汇总网：覆盖当前工作区全部 9 组/10 个上传端点——
聊天附件、费用分类、发票批次、现金流填报、POS 对账、数据仓入仓、BIP 导入预览、
个人记账 CSV 导入、税务财务报表导入两入口（普通 / 指定企业）。

每个端点验证三件事：
1. 调用共享有界读取：经 app.upload.read_upload_limited 分块限额读取
   （模块级同一对象接线；现金流为函数内延迟导入，功能上同样命中共享模块），
   超限即止不读满全量；
2. 超限 400 且无孤儿：路由本地映射或 main.py 全局 UploadTooLargeError 处理统一收口
   400，读取期即中断，inbox/批次目录/运行目录/tmp/临时文件零残留；
3. 正常小文件契约不回归：各端点既有成功行为（落档、解析、计数）不变。

约定：
- 使用 pytest 临时环境（conftest 已钉住临时 SQLite/调试目录；数据目录、引擎目录、
  上传目录全部 monkeypatch 到 tmp_path）与 monkeypatch 字节级小限额，
  不生成巨型文件、不碰真实业务数据；
- 有界读取（超限即止）用合成大字节 fake upload 直接驱动路由验证，
  fake 记录已消费字节数；
- 专项覆盖另见：test_swarm_finance_uploads.py（费用/发票/数据仓/个人）、
  test_swarm_assistant_security.py（聊天）、test_swarm_bip_tax.py（BIP/税务）、
  test_module_finance_pos.py（POS）、test_module_finance_cashflow.py（现金流）；
  本文件是跨模块汇总契约，不替代上述专项测试。
"""
import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app import upload as upload_module
from app.config import settings
from app.modules.finance_cashflow import router as cashflow_router
from app.modules.finance_cashflow import service as cashflow_svc
from app.modules.finance_expense import router as expense_router
from app.modules.finance_expense import service as expense_svc
from app.modules.finance_invoice import router as invoice_router
from app.modules.finance_invoice import service as invoice_svc
from app.modules.finance_ledger_bip import router as bip_router
from app.modules.finance_ledger_bip import service as bip_svc
from app.modules.finance_ledger_personal import router as personal_router
from app.modules.finance_ledger_personal import service as personal_svc
from app.modules.finance_pos import router as pos_router
from app.modules.finance_pos import service as pos_svc
from app.modules.finance_tax.routers import financial_import as tax_router
from app.modules.finance_vault import router as vault_router
from app.modules.finance_vault import service as vault_svc
from app.routers import assistant as assistant_router
from app.upload import UploadTooLargeError, read_upload_limited

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
PDF_MIME = "application/pdf"
FAKE_PDF = b"%PDF-1.4 synthetic-test-bytes"
XLSX_PAYLOAD = b"PK\x03\x04synthetic-xlsx-content"
PL_BASE = "/api/finance/ledger/personal"
TAX_IMPORT_BASE = "/api/finance/tax/financial-import/import"
_1MB = 1024 * 1024

# 合成 CSV（3 行数据）：数据仓入仓复用
_CSV_3_ROWS = "月份,分类,金额\n2026-09,办公费,100.0\n2026-09,办公费,200.0\n2026-10,差旅费,150.0\n"

# 个人记账合成 CSV（utf-8-sig，中文列名）
_PL_CSV = "日期,类型,金额,账户,分类,备注\n2026-10-03,支出,10,现金,餐饮,\n".encode("utf-8-sig")

# POS / 现金流 stub 引擎脚本（建运行只做存在性探测，不真正执行）
STUB_LEDGER = '''
import argparse
from pathlib import Path
p = argparse.ArgumentParser()
p.add_argument("--root", type=Path)
p.add_argument("--output", type=Path)
a = p.parse_args()
a.output.parent.mkdir(parents=True, exist_ok=True)
a.output.write_text("stub-pos-ledger", encoding="utf-8")
print("stub ledger done")
'''

STUB_ANALYZE = '''
import argparse
from pathlib import Path
p = argparse.ArgumentParser()
p.add_argument("--root", type=Path)
p.add_argument("--project", default=None)
a = p.parse_args()
(a.root / "POS差异分析_标注.xlsx").write_text("stub-diff", encoding="utf-8")
(a.root / "差异分析报告.md").write_text("# stub diff report", encoding="utf-8")
print("stub analyze done")
'''

STUB_CASHFLOW_ENGINE = '''
import argparse
from pathlib import Path

p = argparse.ArgumentParser()
p.add_argument("--source-dir", type=Path)
p.add_argument("--output-dir", type=Path)
p.add_argument("--actual-month", type=int)
p.add_argument("--bank-file", type=Path, default=None)
a = p.parse_args()
out = a.output_dir
out.mkdir(parents=True, exist_ok=True)
(out / f"现金流量滚动预测_已填报_{a.actual_month}月.xlsx").write_text("stub", encoding="utf-8")
(out / "填报报告.md").write_text("# stub report", encoding="utf-8")
print("stub engine done")
'''


class _FakeUpload:
    """假上传文件：记录已消费字节数（用于验证有界读取：超限即止，不读满全量）。"""

    def __init__(self, data: bytes, filename: str = "upload.bin"):
        self._data = data
        self._pos = 0
        self.filename = filename
        self.consumed = 0

    async def read(self, size: int = -1) -> bytes:
        chunk = self._data[self._pos:] if size is None or size < 0 else self._data[self._pos:self._pos + size]
        self._pos += len(chunk)
        self.consumed += len(chunk)
        return chunk


def make_fields(**kw) -> dict:
    """构造一份合成 ParsedInvoice 字段（发票引擎被打桩，字节不真正解析）。"""
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


def _bip_xlsx() -> bytes:
    """BIP 辅助明细账合成 xlsx（识别为 aux 类型）。"""
    import io

    import pandas as pd

    rows = [
        {"科目编码": "100201", "科目名称": "银行存款-基本户", "日期": "2026-09-05", "核算账簿": "测试账簿甲",
         "凭证号": "记-0011", "摘要": "收到货款", "借方本币": 800.0, "贷方本币": 0.0},
        {"科目编码": "112201", "科目名称": "应收账款-客户A", "日期": "2026-09-05", "核算账簿": "测试账簿甲",
         "凭证号": "记-0011", "摘要": "收到货款", "借方本币": 0.0, "贷方本币": 800.0},
    ]
    buf = io.BytesIO()
    pd.DataFrame(rows).to_excel(buf, index=False, sheet_name="Sheet", engine="openpyxl")
    return buf.getvalue()


def _tax_xlsx() -> bytes:
    """税务标准模板合成 xlsx（利润表，映射 total_revenue=300）。"""
    import io

    import pandas as pd

    rows = [["项目名称", "本期金额（万元）"], ["营业收入", 300.0], ["营业成本", 150.0], ["净利润", 100.0]]
    buf = io.BytesIO()
    pd.DataFrame(rows).to_excel(buf, index=False, sheet_name="利润表", engine="openpyxl")
    return buf.getvalue()


def _dir_empty(p: Path) -> bool:
    return not p.exists() or not any(p.iterdir())


def _spy_reader(monkeypatch, target_module):
    """把目标模块的共享读取器替换为记录限额的间谍（委托真实读取器，行为不变）。"""
    seen: list[int] = []

    async def spy(file, max_bytes, chunk_size=1024 * 1024):
        seen.append(max_bytes)
        return await read_upload_limited(file, max_bytes, chunk_size)

    monkeypatch.setattr(target_module, "read_upload_limited", spy, raising=False)
    return seen


def _stub_expense_downstream(monkeypatch):
    """打桩费用分类下游重活：证明上传读取/落档阶段行为，不依赖真实引擎。"""
    monkeypatch.setattr(expense_svc, "ensure_engine_ready", lambda: None)
    monkeypatch.setattr(expense_svc, "classify_file", lambda db, original, stored: SimpleNamespace(id=7))
    monkeypatch.setattr(
        expense_svc,
        "run_to_dict",
        lambda run: {
            "id": 7, "original_filename": "a.xlsx", "total_rows": 0, "matched_rows": 0,
            "status": "success", "duration_sec": 0.0, "artifact_path": "",
            "created_at": "2026-10-22 10:00:00",
        },
    )


# ---------------- 夹具：数据目录/引擎目录全部改道 tmp_path ----------------


@pytest.fixture()
def finance_dirs(monkeypatch, tmp_path):
    """费用/发票/个人记账模块数据目录重定向到 tmp_path（不碰真实 finance 数据目录）。"""
    base = tmp_path / "finance"
    monkeypatch.setattr(settings, "finance_data_dir", str(base))
    return base


@pytest.fixture()
def vault_dir(monkeypatch, tmp_path):
    """数据仓 DuckDB 库文件与 inbox 重定向到 tmp_path。"""
    base = tmp_path / "vault_data"
    monkeypatch.setattr(vault_svc, "get_finance_data_dir", lambda: base)
    return base


@pytest.fixture()
def chat_dir(monkeypatch, tmp_path):
    """聊天附件上传目录改道临时目录：测试不碰 backend/data/uploads。"""
    target = tmp_path / "uploads"
    target.mkdir()
    monkeypatch.setattr(assistant_router, "UPLOAD_DIR", target)
    return target


@pytest.fixture()
def bip_dir(monkeypatch, tmp_path):
    """BIP 模块数据目录（inbox/outbox/tmp）重定向到 tmp_path。"""
    d = tmp_path / "bip_data"
    monkeypatch.setattr(bip_svc, "module_data_dir", lambda: d)
    return d


@pytest.fixture()
def pos_dir(monkeypatch, tmp_path):
    """POS 数据目录 + stub 引擎目录重定向到 tmp_path。"""
    base = tmp_path / "pos_data"
    engine = tmp_path / "pos_engine"
    engine.mkdir()
    (engine / "generate_pos_ledger.py").write_text(STUB_LEDGER, encoding="utf-8")
    (engine / "analyze_pos_reconciliation.py").write_text(STUB_ANALYZE, encoding="utf-8")
    monkeypatch.setattr(pos_svc, "get_finance_data_dir", lambda: base)
    monkeypatch.setattr(pos_svc, "engine_dir", lambda: engine)
    return base


@pytest.fixture()
def cash_dir(monkeypatch, tmp_path):
    """现金流数据目录 + stub 引擎目录重定向到 tmp_path。"""
    base = tmp_path / "cash_data"
    engine = tmp_path / "engine"
    engine.mkdir()
    (engine / "cashflow_autofill.py").write_text(STUB_CASHFLOW_ENGINE, encoding="utf-8")
    monkeypatch.setattr(cashflow_svc, "get_finance_data_dir", lambda: base)
    monkeypatch.setattr(cashflow_svc, "engine_dir", lambda: engine)
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


# ---------------- 共享读取器接线 ----------------


def test_all_upload_routers_wired_to_shared_bounded_reader():
    """8 个模块级导入的上传路由全部接上共享读取器（与 app.upload 同一对象，非回退分支）。"""
    for r in (assistant_router, expense_router, invoice_router, pos_router,
              vault_router, personal_router, bip_router, tax_router):
        assert r.read_upload_limited is read_upload_limited, r.__name__


def test_cashflow_reads_through_shared_upload_module(cash_dir, monkeypatch):
    """现金流为函数内延迟导入：替换 app.upload.read_upload_limited 即作用于该端点。"""
    seen: list[int] = []

    async def spy(file, max_bytes, chunk_size=1024 * 1024):
        seen.append(max_bytes)
        return await read_upload_limited(file, max_bytes, chunk_size)

    monkeypatch.setattr(upload_module, "read_upload_limited", spy)
    fake = _FakeUpload(b"a,b\n1,2\n", filename="a.xlsx")
    content = asyncio.run(cashflow_router._read_upload(fake))
    assert content == b"a,b\n1,2\n"
    assert seen == [cashflow_svc.MAX_UPLOAD_BYTES]


# ---------------- 正常小文件：契约不回归 + 共享读取器限额口径 ----------------


def test_chat_upload_normal_uses_shared_reader(client, chat_dir, monkeypatch):
    """聊天附件正常上传：经共享读取器（10MB 口径），落盘且 kind=file。"""
    seen = _spy_reader(monkeypatch, assistant_router)
    resp = client.post(
        "/api/assistant/upload",
        data={"session_id": "sess-up-01"},
        files={"file": ("notes.txt", "你好".encode("utf-8"))},
    )
    assert resp.status_code == 200, resp.text
    assert seen == [assistant_router._MAX_UPLOAD_BYTES]
    assert assistant_router._MAX_UPLOAD_BYTES == 10 * 1024 * 1024
    body = resp.json()
    assert body["kind"] == "file" and body["name"] == "notes.txt"
    assert (chat_dir / "sess-up-01" / "notes.txt").is_file()


def test_expense_upload_normal_uses_shared_reader(client, finance_dirs, monkeypatch):
    """费用分类正常上传：经共享读取器，内容完整落档 inbox，201。"""
    seen = _spy_reader(monkeypatch, expense_router)
    _stub_expense_downstream(monkeypatch)
    resp = client.post(
        "/api/finance/expense/classify",
        files={"file": ("a.xlsx", XLSX_PAYLOAD, XLSX_MIME)},
    )
    assert resp.status_code == 201, resp.text
    assert seen == [expense_svc.MAX_UPLOAD_BYTES]
    inbox = finance_dirs / "finance_expense" / "inbox"
    stored = list(inbox.glob("*.xlsx"))
    assert len(stored) == 1 and stored[0].read_bytes() == XLSX_PAYLOAD


def test_invoice_upload_normal_uses_shared_reader(client, finance_dirs, mock_engine, monkeypatch):
    """发票批次正常上传：经共享读取器，解析落档行为不变。"""
    seen = _spy_reader(monkeypatch, invoice_router)
    mock_engine["a.pdf"] = make_fields(invoice_number="8001", check_code="c1")
    resp = client.post(
        "/api/finance/invoice/batches",
        files=[("files", ("a.pdf", FAKE_PDF, PDF_MIME))],
    )
    assert resp.status_code == 201, resp.text
    assert seen == [invoice_svc.MAX_FILE_BYTES]
    body = resp.json()
    assert body["file_count"] == 1 and body["items"][0]["status"] == "parsed"
    stored = finance_dirs / "finance_invoice" / "inbox" / f"batch_{body['id']}" / "a.pdf"
    assert stored.is_file() and stored.read_bytes() == FAKE_PDF


def test_cashflow_upload_normal_uses_shared_reader(client, cash_dir, monkeypatch):
    """现金流建运行正常上传：经共享读取器（延迟导入路径），归集行为不变。"""
    seen = _spy_reader(monkeypatch, upload_module)
    resp = client.post(
        "/api/finance/cashflow/runs",
        data={"actual_month": 9},
        files=[("files", ("a.xlsx", b"stub-xlsx-content"))],
    )
    assert resp.status_code == 201, resp.text
    assert seen == [cashflow_svc.MAX_UPLOAD_BYTES]
    assert resp.json()["input_files"][0]["name"] == "a.xlsx"


def test_pos_upload_normal_uses_shared_reader(client, pos_dir, monkeypatch):
    """POS 建运行正常上传：经共享读取器，归集行为不变。"""
    seen = _spy_reader(monkeypatch, pos_router)
    resp = client.post(
        "/api/finance/pos/runs",
        files=[("files", ("SHOP对账单.xlsx", b"stub-xlsx-content"))],
    )
    assert resp.status_code == 201, resp.text
    assert seen == [pos_svc.MAX_UPLOAD_BYTES]
    assert resp.json()["input_files"][0]["name"] == "SHOP对账单.xlsx"


def test_vault_upload_normal_uses_shared_reader(client, vault_dir, monkeypatch):
    """数据仓正常入仓：经共享读取器，行数/留档/登记行为不变。"""
    seen = _spy_reader(monkeypatch, vault_router)
    resp = client.post(
        "/api/finance/vault/ingest/file",
        params={"table": "t_up_ok"},
        files={"file": ("e.csv", _CSV_3_ROWS.encode("utf-8"), "text/csv")},
    )
    assert resp.status_code == 200, resp.text
    assert seen == [vault_svc.MAX_UPLOAD_BYTES]
    assert resp.json()["rows"] == 3
    inbox = vault_dir / "finance_vault" / "inbox"
    assert len(list(inbox.glob("*_e.csv"))) == 1


def test_bip_upload_normal_uses_shared_reader(client, bip_dir, monkeypatch):
    """BIP 预览正常上传：经共享读取器，类型识别/预览行为不变。"""
    seen = _spy_reader(monkeypatch, bip_router)
    resp = client.post(
        "/api/finance/ledger/bip/imports/preview",
        files={"file": ("s.xlsx", _bip_xlsx(), XLSX_MIME)},
    )
    assert resp.status_code == 200, resp.text
    assert seen == [bip_svc.MAX_UPLOAD_BYTES]
    body = resp.json()
    assert body["file_type"] == "aux" and body["row_count"] == 2


def test_personal_upload_normal_uses_shared_reader(client, finance_dirs, monkeypatch):
    """个人记账正常导入：经共享读取器，计数正确且原件留档 inbox。"""
    seen = _spy_reader(monkeypatch, personal_router)
    resp = client.post(
        f"{PL_BASE}/import/csv",
        files={"file": ("ok.csv", _PL_CSV, "text/csv")},
    )
    assert resp.status_code == 200, resp.text
    assert seen == [personal_svc.MAX_IMPORT_BYTES]
    assert resp.json()["imported"] == 1
    inbox = finance_dirs / "finance_ledger_personal" / "inbox"
    assert len(list(inbox.glob("*_ok.csv"))) == 1


@pytest.mark.parametrize("enterprise_id", [None, 7])
def test_tax_upload_normal_uses_shared_reader(client, monkeypatch, enterprise_id):
    """税务两入口（普通/指定企业）正常导入行为不变，且均经共享读取器。"""
    seen = _spy_reader(monkeypatch, tax_router)
    url = TAX_IMPORT_BASE if enterprise_id is None else f"{TAX_IMPORT_BASE}/{enterprise_id}"
    resp = client.post(url, files={"file": ("r.xlsx", _tax_xlsx(), XLSX_MIME)})
    assert resp.status_code == 200, resp.text
    assert seen == [tax_router.MAX_UPLOAD_BYTES]
    body = resp.json()
    assert body["success"] is True
    assert body["format_detected"] == "standard_template"
    assert body["financial_data"]["total_revenue"] == 300.0


# ---------------- 有界读取：超限即止，不读满全量（fake upload 直接驱动路由） ----------------


def test_chat_upload_oversize_reads_bounded(client, chat_dir, monkeypatch):
    """聊天附件超限：有界读取（分片读到一个即止）+ 400，且不落任何文件。"""
    monkeypatch.setattr(assistant_router, "_MAX_UPLOAD_BYTES", 1000)
    fake = _FakeUpload(b"x" * (4 * _1MB), filename="big.txt")
    with pytest.raises(HTTPException) as ei:
        asyncio.run(assistant_router.upload_attachment(session_id="sess-bnd-01", file=fake))
    assert ei.value.status_code == 400
    assert fake.consumed <= 2 * _1MB
    assert not any(p.is_file() for p in chat_dir.rglob("*"))


def test_cashflow_upload_oversize_reads_bounded(cash_dir, monkeypatch):
    """现金流上传超限：有界读取即抛 UploadTooLargeError（全局处理收口 400），无运行目录。"""
    monkeypatch.setattr(cashflow_svc, "MAX_UPLOAD_BYTES", 1000)
    fake = _FakeUpload(b"x" * (4 * _1MB), filename="big.xlsx")
    with pytest.raises(UploadTooLargeError):
        asyncio.run(cashflow_router.create_run(actual_month=9, files=[fake], db=object()))
    assert fake.consumed <= 2 * _1MB
    runs_root = cash_dir / "finance_cashflow" / "runs"
    assert not runs_root.exists() or not any(runs_root.iterdir())


def test_pos_upload_oversize_reads_bounded(pos_dir, monkeypatch):
    """POS 上传超限：有界读取即抛 UploadTooLargeError，无运行目录。"""
    monkeypatch.setattr(pos_svc, "MAX_UPLOAD_BYTES", 1000)
    fake = _FakeUpload(b"x" * (4 * _1MB), filename="big.xlsx")
    with pytest.raises(UploadTooLargeError):
        asyncio.run(pos_router.create_run(files=[fake], db=object()))
    assert fake.consumed <= 2 * _1MB
    runs_root = pos_dir / "finance_pos" / "runs"
    assert not runs_root.exists() or not any(runs_root.iterdir())


def test_bip_upload_oversize_reads_bounded(bip_dir, monkeypatch):
    """BIP 预览超限：有界读取 + 400，tmp 不留预览文件。"""
    monkeypatch.setattr(bip_svc, "MAX_UPLOAD_BYTES", 1000)
    fake = _FakeUpload(b"x" * (4 * _1MB), filename="big.xlsx")
    with pytest.raises(HTTPException) as ei:
        asyncio.run(bip_router.preview_import(file=fake))
    assert ei.value.status_code == 400
    assert fake.consumed <= 2 * _1MB
    assert _dir_empty(bip_dir / "tmp")


@pytest.mark.parametrize("enterprise_id", [None, 7])
def test_tax_upload_oversize_reads_bounded(monkeypatch, enterprise_id):
    """税务两入口超限：有界读取 + 400，解析器零调用（临时文件从未创建）。"""
    monkeypatch.setattr(tax_router, "MAX_UPLOAD_BYTES", 1000)
    called: list[str] = []
    monkeypatch.setattr(tax_router.importer, "import_from_file", lambda p: called.append(p))
    fake = _FakeUpload(b"x" * (4 * _1MB), filename="big.xlsx")
    if enterprise_id is None:
        coro = tax_router.import_financial_statement(file=fake)
    else:
        coro = tax_router.import_financial_statement_to_enterprise(enterprise_id=enterprise_id, file=fake)
    with pytest.raises(HTTPException) as ei:
        asyncio.run(coro)
    assert ei.value.status_code == 400
    assert fake.consumed <= 2 * _1MB
    assert called == []


# ---------------- HTTP 层超限回归：400 + 无孤儿（真实共享读取器 + 字节级小限额） ----------------


def test_chat_http_oversize_400_no_leftover(client, chat_dir, monkeypatch):
    """聊天附件超限：400「上限」口径，读取失败发生在落盘之前，零残留。"""
    monkeypatch.setattr(assistant_router, "_MAX_UPLOAD_BYTES", 16)
    resp = client.post(
        "/api/assistant/upload",
        data={"session_id": "sess-big-01"},
        files={"file": ("big.txt", b"x" * 64)},
    )
    assert resp.status_code == 400 and "上限" in resp.json()["detail"]
    assert not any(p.is_file() for p in chat_dir.rglob("*"))


def test_expense_http_oversize_400_no_inbox_orphan(client, finance_dirs, monkeypatch):
    """费用上传超限：400，inbox 不留孤儿。"""
    monkeypatch.setattr(expense_svc, "MAX_UPLOAD_BYTES", 16)
    resp = client.post(
        "/api/finance/expense/classify",
        files={"file": ("big.xlsx", b"x" * 64, XLSX_MIME)},
    )
    assert resp.status_code == 400 and "上限" in resp.json()["detail"]
    assert _dir_empty(finance_dirs / "finance_expense" / "inbox")


def test_invoice_http_oversize_400_no_batch_dir(client, finance_dirs, monkeypatch):
    """发票上传超限：400，不创建任何批次目录。"""
    monkeypatch.setattr(invoice_svc, "MAX_FILE_BYTES", 8)
    resp = client.post(
        "/api/finance/invoice/batches",
        files=[("files", ("big.pdf", b"x" * 9, PDF_MIME))],
    )
    assert resp.status_code == 400 and "上限" in resp.json()["detail"]
    inbox = finance_dirs / "finance_invoice" / "inbox"
    assert not inbox.exists() or not any(inbox.glob("batch_*"))


def test_cashflow_http_oversize_400_no_run_dir(client, cash_dir, monkeypatch):
    """现金流上传超限：400（全局 UploadTooLargeError 处理），不创建运行目录。"""
    monkeypatch.setattr(cashflow_svc, "MAX_UPLOAD_BYTES", 16)
    resp = client.post(
        "/api/finance/cashflow/runs",
        data={"actual_month": 9},
        files=[("files", ("big.xlsx", b"x" * 64))],
    )
    assert resp.status_code == 400 and "上限" in resp.json()["detail"]
    runs_root = cash_dir / "finance_cashflow" / "runs"
    assert not runs_root.exists() or not any(runs_root.iterdir())


def test_pos_http_oversize_400_no_run_dir(client, pos_dir, monkeypatch):
    """POS 上传超限：400（全局 UploadTooLargeError 处理），不创建运行目录。"""
    monkeypatch.setattr(pos_svc, "MAX_UPLOAD_BYTES", 16)
    resp = client.post(
        "/api/finance/pos/runs",
        files=[("files", ("big.xlsx", b"x" * 64))],
    )
    assert resp.status_code == 400 and "上限" in resp.json()["detail"]
    runs_root = pos_dir / "finance_pos" / "runs"
    assert not runs_root.exists() or not any(runs_root.iterdir())


def test_vault_http_oversize_400_no_inbox_orphan(client, vault_dir, monkeypatch):
    """数据仓上传超限：400，inbox 不留孤儿。"""
    monkeypatch.setattr(vault_svc, "MAX_UPLOAD_BYTES", 16)
    resp = client.post(
        "/api/finance/vault/ingest/file",
        params={"table": "t_big"},
        files={"file": ("big.csv", b"x" * 64, "text/csv")},
    )
    assert resp.status_code == 400 and "上限" in resp.json()["detail"]
    assert _dir_empty(vault_dir / "finance_vault" / "inbox")


def test_bip_http_oversize_400_no_tmp_orphan(client, bip_dir, monkeypatch):
    """BIP 预览超限：400，tmp 不留预览文件。"""
    monkeypatch.setattr(bip_svc, "MAX_UPLOAD_BYTES", 16)
    resp = client.post(
        "/api/finance/ledger/bip/imports/preview",
        files={"file": ("big.xlsx", b"x" * 64, XLSX_MIME)},
    )
    assert resp.status_code == 400 and "上限" in resp.json()["detail"]
    assert _dir_empty(bip_dir / "tmp")


def test_personal_http_oversize_400_no_inbox_orphan(client, finance_dirs, monkeypatch):
    """个人记账导入超限：400，inbox 不留孤儿。"""
    monkeypatch.setattr(personal_svc, "MAX_IMPORT_BYTES", 16)
    resp = client.post(
        f"{PL_BASE}/import/csv",
        files={"file": ("big.csv", b"x" * 64, "text/csv")},
    )
    assert resp.status_code == 400 and "上限" in resp.json()["detail"]
    assert _dir_empty(finance_dirs / "finance_ledger_personal" / "inbox")


@pytest.mark.parametrize("enterprise_id", [None, 7])
def test_tax_http_oversize_400_no_temp_file(client, monkeypatch, enterprise_id):
    """税务两入口超限：400 且解析器零调用（临时文件从未创建，无孤儿）。"""
    monkeypatch.setattr(tax_router, "MAX_UPLOAD_BYTES", 16)
    called: list[str] = []
    monkeypatch.setattr(tax_router.importer, "import_from_file", lambda p: called.append(p))
    url = TAX_IMPORT_BASE if enterprise_id is None else f"{TAX_IMPORT_BASE}/{enterprise_id}"
    resp = client.post(url, files={"file": ("big.xlsx", b"x" * 64, XLSX_MIME)})
    assert resp.status_code == 400 and "上限" in resp.json()["detail"]
    assert called == []


# ---------------- 失败关闭：共享读取器缺失时明确 503，绝不退回无界 file.read() ----------------


def test_upload_endpoints_fail_closed_503_when_shared_reader_missing(
    client, finance_dirs, vault_dir, chat_dir, bip_dir, monkeypatch
):
    """共享读取器缺失（模块级 None 回退态的 8 个端点）：明确 503 且零落盘。

    现金流为函数内延迟导入（无 None 分支，共享模块缺失时 ImportError 分支在当前
    工作区不可达——main.py 启动即强依赖 app.upload）；POS 为模块级强导入
    （缺失时应用直接起不来）。两者的接线由上面的间谍测试单独保证。
    """
    for r in (assistant_router, expense_router, invoice_router, vault_router,
              personal_router, bip_router, tax_router):
        monkeypatch.setattr(r, "read_upload_limited", None, raising=False)

    r1 = client.post("/api/assistant/upload", data={"session_id": "sess-fc-01"},
                     files={"file": ("a.txt", b"hi")})
    assert r1.status_code == 503

    r2 = client.post("/api/finance/expense/classify",
                     files={"file": ("a.xlsx", b"x", XLSX_MIME)})
    assert r2.status_code == 503

    r3 = client.post("/api/finance/invoice/batches",
                     files=[("files", ("a.pdf", FAKE_PDF, PDF_MIME))])
    assert r3.status_code == 503

    r4 = client.post("/api/finance/vault/ingest/file", params={"table": "t_fc"},
                     files={"file": ("a.csv", b"a,b\n", "text/csv")})
    assert r4.status_code == 503

    r5 = client.post(f"{PL_BASE}/import/csv",
                     files={"file": ("a.csv", b"a,b\n", "text/csv")})
    assert r5.status_code == 503

    r6 = client.post("/api/finance/ledger/bip/imports/preview",
                     files={"file": ("a.xlsx", b"x", XLSX_MIME)})
    assert r6.status_code == 503

    r7 = client.post(TAX_IMPORT_BASE, files={"file": ("a.xlsx", b"x", XLSX_MIME)})
    assert r7.status_code == 503

    r8 = client.post(f"{TAX_IMPORT_BASE}/7", files={"file": ("a.xlsx", b"x", XLSX_MIME)})
    assert r8.status_code == 503

    # 失败发生在读取阶段：任何目录都不落文件
    assert not any(p.is_file() for p in chat_dir.rglob("*"))
    assert _dir_empty(finance_dirs / "finance_expense" / "inbox")
    assert _dir_empty(finance_dirs / "finance_invoice" / "inbox")
    assert _dir_empty(finance_dirs / "finance_ledger_personal" / "inbox")
    assert _dir_empty(vault_dir / "finance_vault" / "inbox")
    assert _dir_empty(bip_dir / "tmp")
