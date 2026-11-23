"""finance_invoice 模块测试：合成数据 + mock 解析器，不触碰真实业务文件、不写 tmp_path 之外的位置。

覆盖：
- 文本字段解析（真实引擎 + 示例配置直喂合成文本，引擎缺失自动 skip）与销方名补充提取；
- 去重幂等键 / 批内 + 跨批次去重 / 冲红一对一配对（纯逻辑，ORM  detached 对象）；
- 上传建批次状态机 uploaded→parsed→exported（monkeypatch 引擎解析器）、ZIP 解包拍平与路径穿越拦截、
  扩展名/大小/ZIP 混传校验；
- 引擎缺失时 POST /batches 503 降级（不留半成品批次）；
- PATCH 人工修正、导出清单 xlsx + ZIP、下载与未导出 409；
- 单项目 PDF 即时合并（真实合成 PDF，无需先导出）、pdf_name 命名、缺件/损坏/加密/越界整次失败、
  响应后临时文件清理、批次未就绪中文提示；
- invoice_batch_stats 只读 Agent 工具。
"""
import asyncio
import io
import sys
import types
import zipfile
from pathlib import Path
from urllib.parse import quote

import pytest
from openpyxl import load_workbook
from pypdf import PdfReader, PdfWriter
from sqlalchemy import text

from app.config import settings
from app.database import SessionLocal, engine
from app.modules.finance_invoice import engine as engine_mod
from app.modules.finance_invoice import invoice_batch, invoice_export, invoice_parse
from app.modules.finance_invoice import projects as project_rules
from app.modules.finance_invoice import router as invoice_router
from app.modules.finance_invoice import service
from app.modules.finance_invoice.models import InvoiceBatch, InvoiceItem

# 合成的假 PDF 内容：解析器全部被 mock，字节不会被真正解析
FAKE_PDF = b"%PDF-1.4 synthetic-test-bytes"


def make_fields(**kw) -> dict:
    """构造一份 ParsedInvoice 字段（全为虚构示例值）。"""
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
def finance_dir(tmp_path, monkeypatch):
    """模块数据目录重定向到 tmp_path（不碰真实 finance 数据目录）。"""
    monkeypatch.setattr(settings, "finance_data_dir", str(tmp_path / "finance"))
    return tmp_path / "finance"


@pytest.fixture()
def mock_engine(monkeypatch):
    """打桩引擎：_require_engine 放行；按文件名返回罐头解析结果（未登记的文件返回 None=解析失败）。"""
    canned: dict[str, dict] = {}
    monkeypatch.setattr(invoice_parse, "_require_engine", lambda: None)

    def fake_parse(pdf_path):
        fields = canned.get(Path(pdf_path).name)
        return service.ParsedInvoice(**fields) if fields is not None else None

    monkeypatch.setattr(invoice_parse, "_parse_pdf_with_engine", fake_parse)
    return canned


def upload(client, files: list[tuple[str, bytes]], name: str = ""):
    """files: [(filename, content), ...]；走 POST /batches multipart。"""
    multipart = [("files", (fname, content, "application/pdf")) for fname, content in files]
    data = {"name": name} if name else {}
    return client.post("/api/finance/invoice/batches", files=multipart, data=data)


# ---------------- 模块注册与引擎自检 ----------------

def test_module_registered_and_tables_created(client):
    from app.modules import get_loaded_modules

    loaded = {m.name: m for m in get_loaded_modules()}
    assert "finance_invoice" in loaded
    manifest = loaded["finance_invoice"]
    assert manifest.prefix == "/api/finance/invoice"
    with engine.connect() as conn:
        names = {r[0] for r in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))}
    assert {"mod_invoice_batch", "mod_invoice_item"} <= names


def test_engine_status_endpoint_shape(client):
    resp = client.get("/api/finance/invoice/engine/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["module"] == "finance_invoice"
    assert isinstance(body["available"], bool)
    assert isinstance(body["root"], str) and body["root"]


def test_create_batch_handler_remains_coroutine():
    """create_batch 保持协程（逐文件 await f.read 语义不变），PDF 解析重活经 asyncio.to_thread 进线程池。"""
    assert asyncio.iscoroutinefunction(invoice_router.create_batch)


# ---------------- 文本解析（真实引擎，缺失则 skip） ----------------

def _real_processor(monkeypatch):
    """加载真实引擎并强制示例配置（INVOICE_CONFIG_EXAMPLE=1 跳过引擎目录的本机 local 配置）。"""
    root = engine_mod.default_engine_root()
    if not (root / "invoice_processor" / "src" / "pdf_processor.py").is_file():
        pytest.skip("发票引擎不在默认位置")
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    monkeypatch.setenv("INVOICE_CONFIG_EXAMPLE", "1")
    try:
        from invoice_processor.src.config_manager import ConfigManager
        from invoice_processor.src.pdf_processor import PDFInvoiceProcessor
    except Exception as exc:
        pytest.skip(f"发票引擎不可导入：{exc}")
    return PDFInvoiceProcessor(ConfigManager())


def test_parse_text_with_real_engine(monkeypatch):
    processor = _real_processor(monkeypatch)
    text = (
        "电子发票（增值税专用发票） 发票号码：26937000000123 校验码：12345678901234567890 "
        "开票日期：2026年10月01日 购 名称：示例科技有限公司 销 名称：云岭商贸有限公司 "
        "价税合计 ￥113.00 税率：13% 备注：办公用品采购"
    )
    parsed = service.parse_invoice_text(processor, text)
    assert parsed.invoice_number == "26937000000123"
    assert parsed.buyer_name == "示例科技有限公司"
    assert parsed.seller_name == "云岭商贸有限公司"  # 引擎不解析销方，本模块补齐
    assert parsed.amount == "113.00"
    assert parsed.tax_amount == "13.00"  # 引擎按税率价外折算
    assert parsed.invoice_date == "2026年10月01日"
    assert parsed.invoice_type == "专票"
    assert parsed.invoice_direction == "正数发票"
    assert parsed.project_name == "办公用品采购"  # 引擎回退：取备注前 10 字
    assert parsed.extraction_success is True


def test_parse_text_red_invoice_with_real_engine(monkeypatch):
    processor = _real_processor(monkeypatch)
    text = (
        "电子发票（普通发票） 发票号码：26937000000999 开票日期：2026年09月15日 "
        "购 名称：示例科技有限公司 销 名称：云岭商贸有限公司 价税合计 ￥-113.00 冲红"
    )
    parsed = service.parse_invoice_text(processor, text)
    assert parsed.invoice_direction == "冲红发票"
    assert service.parse_amount(parsed.amount) == -113.00
    assert parsed.invoice_type == "普票"


def test_extract_seller_name_patterns():
    assert service.extract_seller_name("购 名称：甲公司 销 名称：乙有限责任公司 价税合计") == "乙有限责任公司"
    assert service.extract_seller_name("销售方 名称：丙供应商") == "丙供应商"
    assert service.extract_seller_name("没有任何销方字段") == ""
    # 命中停用词时不应采信
    assert service.extract_seller_name("销 名称：发票号码") == ""


# ---------------- 去重与冲红配对（纯逻辑） ----------------

def test_dedup_key_rule():
    assert service.dedup_key("123", "ABC") == "123|ABC"
    assert service.dedup_key("123", "") == "123|"
    assert service.dedup_key("", "x") == ""
    assert service.dedup_key("未知", "") == ""


def test_mark_duplicates_within_batch():
    a = InvoiceItem(invoice_number="1001", check_code="c1", status=InvoiceItem.STATUS_PARSED)
    b = InvoiceItem(invoice_number="1001", check_code="c1", status=InvoiceItem.STATUS_PARSED)
    c = InvoiceItem(invoice_number="1002", check_code="", status=InvoiceItem.STATUS_PARSED)
    f = InvoiceItem(invoice_number="1001", check_code="c1", status=InvoiceItem.STATUS_FAILED)
    dup = service.mark_duplicates([a, b, c, f], existing_keys=set())
    assert dup == 1
    assert a.status == InvoiceItem.STATUS_PARSED
    assert b.status == InvoiceItem.STATUS_DUPLICATE  # 后来者被标记
    assert c.status == InvoiceItem.STATUS_PARSED
    assert f.status == InvoiceItem.STATUS_FAILED  # 失败明细不参与去重


def test_mark_duplicates_against_existing_keys():
    item = InvoiceItem(invoice_number="1001", check_code="c1", status=InvoiceItem.STATUS_PARSED)
    assert service.mark_duplicates([item], existing_keys={"1001|c1"}) == 1
    assert item.status == InvoiceItem.STATUS_DUPLICATE


def test_pair_red_invoices_one_to_one():
    pos = InvoiceItem(id=1, buyer_name="示例购方", amount="113.00", invoice_direction="正数发票", status="parsed")
    red = InvoiceItem(id=2, buyer_name="示例购方", amount="-113.00", invoice_direction="冲红发票", status="parsed")
    red2 = InvoiceItem(id=3, buyer_name="示例购方", amount="-113.00", invoice_direction="冲红发票", status="parsed")
    matched, unmatched = service.pair_red_invoices([pos, red, red2])
    assert (matched, unmatched) == (1, 1)  # 一对一：一张原票只能配一张红票
    assert red.paired_item_id == 1 and pos.paired_item_id == 2
    assert red2.paired_item_id is None


def test_pair_red_invoices_buyer_mismatch_and_duplicate_excluded():
    pos = InvoiceItem(id=1, buyer_name="购方甲", amount="100.00", invoice_direction="正数发票", status="parsed")
    red_other = InvoiceItem(id=2, buyer_name="购方乙", amount="-100.00", invoice_direction="冲红发票", status="parsed")
    red_dup = InvoiceItem(id=3, buyer_name="购方甲", amount="-100.00", invoice_direction="冲红发票", status="duplicate")
    matched, unmatched = service.pair_red_invoices([pos, red_other, red_dup])
    assert (matched, unmatched) == (0, 1)  # 购方不同不配；重复红票不参与也不计入未配对
    assert pos.paired_item_id is None and red_other.paired_item_id is None


# ---------------- 上传与状态机（mock 解析器） ----------------

def test_upload_pdfs_state_machine(client, finance_dir, mock_engine):
    mock_engine["a.pdf"] = make_fields(invoice_number="8001", check_code="c1")
    mock_engine["b.pdf"] = make_fields(invoice_number="8001", check_code="c1")  # 与 a 同号同校验码 → 重复
    mock_engine["red.pdf"] = make_fields(invoice_number="8002", amount="-100.00", invoice_direction="冲红发票")
    # c.pdf 未登记 → 解析失败
    resp = upload(client, [("a.pdf", FAKE_PDF), ("b.pdf", FAKE_PDF), ("red.pdf", FAKE_PDF), ("c.pdf", FAKE_PDF)], name="十月票")
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "十月票"
    assert body["status"] == "parsed"  # uploaded → parsed
    assert (body["file_count"], body["item_count"]) == (4, 4)
    assert (body["parsed_count"], body["duplicate_count"], body["failed_count"]) == (2, 1, 1)
    assert (body["red_matched_count"], body["red_unmatched_count"]) == (1, 0)
    assert body["total_amount"] == 0.0  # 100 + (-100)
    items = {i["source_file"]: i for i in body["items"]}
    assert items["b.pdf"]["status"] == "duplicate"
    assert items["c.pdf"]["status"] == "failed"
    assert items["red.pdf"]["paired_item_id"] == items["a.pdf"]["id"]
    assert items["red.pdf"]["paired_invoice_number"] == "8001"
    assert items["a.pdf"]["seller_name"] == "示例销方公司"
    assert "stored_path" not in items["a.pdf"]  # 服务端内部路径不对外
    # PDF 落盘在 tmp 指向的模块 inbox，不碰真实目录
    stored = finance_dir / "finance_invoice" / "inbox" / f"batch_{body['id']}" / "a.pdf"
    assert stored.is_file() and stored.read_bytes() == FAKE_PDF


def test_upload_cross_batch_duplicate(client, finance_dir, mock_engine):
    mock_engine["a.pdf"] = make_fields(invoice_number="8001", check_code="c1")
    assert upload(client, [("a.pdf", FAKE_PDF)]).status_code == 201
    # 第二批再次上传同号同校验码 → 跨批次去重
    resp = upload(client, [("a.pdf", FAKE_PDF)])
    assert resp.status_code == 201
    body = resp.json()
    assert body["duplicate_count"] == 1 and body["parsed_count"] == 0
    assert body["items"][0]["status"] == "duplicate"


def test_upload_zip_extract_and_flatten(client, finance_dir, mock_engine):
    mock_engine["x.pdf"] = make_fields(invoice_number="9001")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("子目录/x.pdf", FAKE_PDF)
        zf.writestr("y.pdf", FAKE_PDF)  # 未登记 → failed
        zf.writestr("说明.txt", b"not a pdf")  # 非 PDF 成员跳过
    resp = upload(client, [("pack.zip", buf.getvalue())])
    assert resp.status_code == 201
    body = resp.json()
    assert body["file_count"] == 2 and body["item_count"] == 2
    by_src = {i["source_file"]: i for i in body["items"]}
    assert by_src["pack.zip:子目录/x.pdf"]["status"] == "parsed"
    assert by_src["pack.zip:y.pdf"]["status"] == "failed"
    inbox = finance_dir / "finance_invoice" / "inbox" / f"batch_{body['id']}"
    assert (inbox / "x.pdf").is_file()  # 目录被拍平


def test_upload_zip_path_traversal_rejected(client, finance_dir, mock_engine):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("../evil.pdf", FAKE_PDF)
    resp = upload(client, [("evil.zip", buf.getvalue())])
    assert resp.status_code == 400
    assert "非法路径" in resp.json()["detail"]


def test_upload_validation(client, finance_dir, mock_engine):
    # 扩展名白名单
    resp = upload(client, [("a.txt", b"hi")])
    assert resp.status_code == 400 and "不支持的文件类型" in resp.json()["detail"]
    # ZIP 必须单传
    resp = upload(client, [("a.pdf", FAKE_PDF), ("b.zip", b"PK\x03\x04")])
    assert resp.status_code == 400 and "ZIP" in resp.json()["detail"]


def test_upload_oversize_rejected(client, finance_dir, mock_engine, monkeypatch):
    monkeypatch.setattr(service, "MAX_FILE_BYTES", 8)
    resp = upload(client, [("big.pdf", b"x" * 9)])
    assert resp.status_code == 400 and "上限" in resp.json()["detail"]


def test_upload_503_when_engine_missing(client, finance_dir, monkeypatch, tmp_path):
    """引擎路径无效 → 503 降级，且不留半成品批次。"""
    monkeypatch.setattr(settings, "finance_invoice_engine_path", str(tmp_path / "no_such_engine"))
    engine_mod.reset_engine_cache()
    try:
        resp = upload(client, [("a.pdf", FAKE_PDF)])
        assert resp.status_code == 503
        assert "引擎不可用" in resp.json()["detail"]
        assert client.get("/api/finance/invoice/batches").json() == {"total": 0, "items": [], "page": 1, "page_size": 50}
        status = client.get("/api/finance/invoice/engine/status").json()
        assert status["available"] is False
    finally:
        engine_mod.reset_engine_cache()


def test_batch_detail_404(client):
    assert client.get("/api/finance/invoice/batches/9999").status_code == 404


def test_batch_list_pagination_reports_total(client):
    """批次列表必须返回 total 并支持 page/page_size（否则第 51 条起不可见）。"""
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        for index in range(51):
            db.add(InvoiceBatch(name=f"批次{index + 1}", status="parsed"))
        db.commit()
    finally:
        db.close()

    first = client.get("/api/finance/invoice/batches", params={"page": 1, "page_size": 25}).json()
    assert first["total"] == 51
    assert first["page"] == 1 and first["page_size"] == 25
    assert len(first["items"]) == 25
    # 按 id 倒序：page1 是 51..27，page2 是 26..2，page3 只剩 1
    assert first["items"][0]["name"] == "批次51"
    assert first["items"][-1]["name"] == "批次27"

    second = client.get("/api/finance/invoice/batches", params={"page": 2, "page_size": 25}).json()
    assert len(second["items"]) == 25
    assert second["items"][0]["name"] == "批次26"

    third = client.get("/api/finance/invoice/batches", params={"page": 3, "page_size": 25}).json()
    assert third["total"] == 51
    assert [b["name"] for b in third["items"]] == ["批次1"]

    default = client.get("/api/finance/invoice/batches").json()
    assert default["total"] == 51
    assert len(default["items"]) == 50
    assert default["page"] == 1 and default["page_size"] == 50


# ---------------- 人工修正 ----------------

def test_patch_item(client, finance_dir, mock_engine):
    mock_engine["a.pdf"] = make_fields()
    batch = upload(client, [("a.pdf", FAKE_PDF)]).json()
    item_id = batch["items"][0]["id"]
    resp = client.patch(f"/api/finance/invoice/items/{item_id}", json={"project_name": "修正后项目", "remark": "人工备注"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["project_name"] == "修正后项目" and body["remark"] == "人工备注"
    assert body["corrected"] is True
    assert client.patch("/api/finance/invoice/items/9999", json={"remark": "x"}).status_code == 404


def test_patch_blank_project_clears_and_invalidates_export(client, finance_dir, mock_engine):
    """空字符串是「清空」指令，不是「未提供」（前端清空输入框走这里）。"""
    batch = _exported_batch(client, mock_engine)
    item_id = batch["items"][0]["id"]
    resp = client.patch(f"/api/finance/invoice/items/{item_id}", json={"project_name": ""})
    assert resp.status_code == 200
    body = resp.json()
    assert body["project_name"] == ""
    assert body["corrected"] is True
    detail = client.get(f"/api/finance/invoice/batches/{batch['id']}").json()
    assert detail["status"] == "parsed"
    assert detail["export_file"] == ""
    assert client.get(f"/api/finance/invoice/batches/{batch['id']}/download").status_code == 409


def test_patch_blank_remark_clears_field(client, finance_dir, mock_engine):
    mock_engine["a.pdf"] = make_fields(remark="合成备注")
    batch = upload(client, [("a.pdf", FAKE_PDF)]).json()
    item_id = batch["items"][0]["id"]
    resp = client.patch(f"/api/finance/invoice/items/{item_id}", json={"remark": "   "})
    assert resp.status_code == 200
    assert resp.json()["remark"] == ""
    assert resp.json()["corrected"] is True


# ---------------- 导出与下载 ----------------

def _exported_batch(client, mock_engine) -> dict:
    mock_engine["a.pdf"] = make_fields(invoice_number="8001", amount="100.00", invoice_type="专票")
    mock_engine["b.pdf"] = make_fields(invoice_number="8002", amount="50.00", project_name="另一项目")
    batch = upload(client, [("a.pdf", FAKE_PDF), ("b.pdf", FAKE_PDF)]).json()
    resp = client.post(f"/api/finance/invoice/batches/{batch['id']}/export")
    assert resp.status_code == 200
    result = resp.json()
    assert result["ok"] and result["projects"] == 2 and result["zip_size"] > 0
    return client.get(f"/api/finance/invoice/batches/{batch['id']}").json()


def test_export_generates_xlsx_and_zip(client, finance_dir, mock_engine, tmp_path):
    batch = _exported_batch(client, mock_engine)
    assert batch["status"] == "exported"  # parsed → exported
    zip_path = finance_dir / "finance_invoice" / "outbox" / batch["export_file"]
    assert zip_path.is_file()
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        # 引擎重命名规则：{购方}{金额}{类型}.pdf，目录 {年月}{项目}
        assert "26年10月示例项目/示例购方公司100.00专票.pdf" in names
        assert any(name.endswith("发票清单.xlsx") for name in names)
        assert "汇总清单.xlsx" in names
        zf.extract("汇总清单.xlsx", tmp_path)
    wb = load_workbook(tmp_path / "汇总清单.xlsx")
    ws = wb.active
    rows = [[c.value for c in row] for row in ws.iter_rows(min_row=1, max_row=4)]
    assert rows[0] == ["序号", "项目名称", "发票张数", "发票总额", "专票张数", "普票张数"]
    assert rows[3][1] == "合计" and rows[3][2] == 2 and rows[3][3] == 150.0


def test_export_empty_batch_400(client, finance_dir, mock_engine):
    # c.pdf 未登记罐头 → 全部解析失败 → 无可导出明细
    batch = upload(client, [("c.pdf", FAKE_PDF)]).json()
    resp = client.post(f"/api/finance/invoice/batches/{batch['id']}/export")
    assert resp.status_code == 400 and "无可导出" in resp.json()["detail"]


def test_download_zip(client, finance_dir, mock_engine):
    batch = _exported_batch(client, mock_engine)
    resp = client.get(f"/api/finance/invoice/batches/{batch['id']}/download")
    assert resp.status_code == 200
    assert resp.content[:2] == b"PK"  # ZIP 魔数


def test_download_before_export_409(client, finance_dir, mock_engine):
    mock_engine["a.pdf"] = make_fields()
    batch = upload(client, [("a.pdf", FAKE_PDF)]).json()
    resp = client.get(f"/api/finance/invoice/batches/{batch['id']}/download")
    assert resp.status_code == 409


# ---------------- Agent 工具 ----------------

def test_invoice_batch_stats_tool(client, finance_dir, mock_engine):
    from app.agents.tools import TOOL_SPECS

    assert "invoice_batch_stats" in TOOL_SPECS
    spec = TOOL_SPECS["invoice_batch_stats"]
    assert spec["readonly"] is True
    mock_engine["a.pdf"] = make_fields()
    upload(client, [("a.pdf", FAKE_PDF)])
    result = spec["func"]()
    assert result["ok"] is True
    assert result["module"] == "finance_invoice"
    assert result["batches"] >= 1 and result["items"] >= 1
    assert result["net_amount"] == 100.0


# ---------------- 项目归类与拆分打包 ----------------

_ORIGINAL_LOAD_RULES = project_rules.load_project_rules


class _FakeConfig:
    """引擎 ConfigManager 的最小替身：只实现 get('a.b', default)。"""

    def __init__(self, data: dict):
        self._data = data

    def get(self, key: str, default=None):
        current = self._data
        for part in key.split("."):
            if not isinstance(current, dict) or part not in current:
                return default
            current = current[part]
        return current


@pytest.fixture(autouse=True)
def _isolate_project_rules(monkeypatch):
    """默认不读本机真实引擎配置（随机器变化），需要规则的用例自行打桩。"""
    monkeypatch.setattr(project_rules, "load_project_rules", lambda: [])


def pin_rules(monkeypatch, keywords: list[str], mapping: dict | None = None) -> None:
    """按合成关键词/映射打桩引擎配置，并恢复真实的规则读取实现。"""
    handle = types.SimpleNamespace(
        config=_FakeConfig(
            {"extraction": {"address_keywords": keywords, "address_force_mapping": mapping or {}}}
        )
    )
    monkeypatch.setattr(engine_mod, "get_engine", lambda: handle)
    monkeypatch.setattr(project_rules, "load_project_rules", _ORIGINAL_LOAD_RULES)


def test_load_project_rules_longest_first_and_force_mapping(monkeypatch):
    pin_rules(monkeypatch, ["示例路", "示例北路", "品牌"], {"示例北路": "项目B"})
    rules = project_rules.load_project_rules()
    assert [rule.keyword for rule in rules] == ["示例北路", "示例路", "品牌"]  # 长词优先
    assert rules[0].project == "项目B"
    assert rules[1].project == "示例路"  # 无映射 → 关键词即项目名


def test_resolve_project_priority_chain():
    rules = [
        project_rules.ProjectRule("示例北路", "项目B"),
        project_rules.ProjectRule("示例路", "示例路"),
    ]
    # 1) 引擎项目名命中关键词
    assert project_rules.resolve_project(rules=rules, project_name="示例北路") == (
        "项目B",
        project_rules.SOURCE_PROJECT_NAME,
    )
    # 2) 项目名不可用（类别/购方兜底）→ 备注命中
    assert project_rules.resolve_project(
        rules=rules, project_name="场地服务费", remark="示例北路项目场地服务"
    ) == ("项目B", project_rules.SOURCE_REMARK)
    # 3) 前两步不命中 → 全文命中
    assert project_rules.resolve_project(
        rules=rules, project_name="某租户", text="地址：示例北路600号"
    ) == ("项目B", project_rules.SOURCE_TEXT)
    # 4) 同批同购方唯一项目 → 继承
    assert project_rules.resolve_project(rules=rules, project_name="某租户", peer_projects={"项目B"}) == (
        "项目B",
        project_rules.SOURCE_PEER,
    )
    # 5) 无任何证据 → 未归类
    assert project_rules.resolve_project(rules=rules, project_name="某租户") == (
        project_rules.UNCLASSIFIED,
        project_rules.SOURCE_UNCLASSIFIED,
    )
    # 6) 规则不可用 → 原样保留，绝不改写
    assert project_rules.resolve_project(rules=[], project_name="示例项目") == (
        "示例项目",
        project_rules.SOURCE_UNCHANGED,
    )


def test_parse_applies_project_rules(client, finance_dir, mock_engine, monkeypatch):
    """上传即归类：地址关键词映射、类别备注改写、同购方继承。"""
    pin_rules(monkeypatch, ["示例北路"], {"示例北路": "项目B"})
    mock_engine["a.pdf"] = make_fields(invoice_number="7001", project_name="示例北路", buyer_name="示例购方")
    mock_engine["b.pdf"] = make_fields(
        invoice_number="7002", project_name="场地服务费", remark="示例北路项目场地服务", buyer_name="另一购方"
    )
    mock_engine["c.pdf"] = make_fields(invoice_number="7003", project_name="无地址租户", buyer_name="示例购方")
    resp = upload(client, [("a.pdf", FAKE_PDF), ("b.pdf", FAKE_PDF), ("c.pdf", FAKE_PDF)])
    assert resp.status_code == 201
    body = resp.json()
    projects = {item["project_name"] for item in body["items"]}
    assert projects == {"项目B"}  # c 经同购方继承

    summary = client.get(f"/api/finance/invoice/batches/{body['id']}/projects").json()
    assert summary == [
        {
            "project": "项目B",
            "count": 3,
            "amount": 300.0,
            "manual": 0,
            "pdf_name": "项目B2026年10月发票.pdf",
            "zip_file": "",
            "zip_ready": False,
        }
    ]


def test_reclassify_keeps_manual_and_marks_unclassified(client, finance_dir, mock_engine, monkeypatch):
    pin_rules(monkeypatch, ["示例北路"], {"示例北路": "项目B"})
    mock_engine["a.pdf"] = make_fields(invoice_number="7101", project_name="示例北路", buyer_name="购方甲")
    mock_engine["b.pdf"] = make_fields(invoice_number="7102", project_name="无地址租户", buyer_name="购方乙")
    body = upload(client, [("a.pdf", FAKE_PDF), ("b.pdf", FAKE_PDF)]).json()
    by_src = {item["source_file"]: item for item in body["items"]}
    assert by_src["b.pdf"]["project_name"] == "未归类"

    # 人工把 a 改成自有项目 → 重新归类不得覆盖
    patched = client.patch(
        f"/api/finance/invoice/items/{by_src['a.pdf']['id']}", json={"project_name": "人工项目"}
    )
    assert patched.status_code == 200
    resp = client.post(f"/api/finance/invoice/batches/{body['id']}/reclassify")
    assert resp.status_code == 200
    result = resp.json()
    assert result["manual_kept"] == 1 and result["unclassified"] == 1
    detail = client.get(f"/api/finance/invoice/batches/{body['id']}").json()
    projects = {item["source_file"]: item["project_name"] for item in detail["items"]}
    assert projects == {"a.pdf": "人工项目", "b.pdf": "未归类"}


def test_reclassify_requires_rules(client, finance_dir, mock_engine):
    mock_engine["a.pdf"] = make_fields()
    body = upload(client, [("a.pdf", FAKE_PDF)]).json()
    resp = client.post(f"/api/finance/invoice/batches/{body['id']}/reclassify")
    assert resp.status_code == 400
    assert "规则不可用" in resp.json()["detail"]


def test_export_splits_project_zips_and_download(client, finance_dir, mock_engine, monkeypatch):
    pin_rules(monkeypatch, ["示例北路", "示例南路"], {"示例北路": "项目B", "示例南路": "项目C"})
    mock_engine["a.pdf"] = make_fields(invoice_number="7201", project_name="示例北路", amount="100.00")
    mock_engine["b.pdf"] = make_fields(invoice_number="7202", project_name="示例南路", amount="50.00")
    body = upload(client, [("a.pdf", FAKE_PDF), ("b.pdf", FAKE_PDF)]).json()

    before = client.get(f"/api/finance/invoice/batches/{body['id']}/projects/download?project=项目B")
    assert before.status_code == 409  # 未导出

    result = client.post(f"/api/finance/invoice/batches/{body['id']}/export").json()
    assert set(result["project_zips"]) == {"项目B", "项目C"}
    # 下载文件名 = 项目 + 月份
    assert result["project_zips"]["项目B"] == "项目B2026年10月.zip"

    summary = client.get(f"/api/finance/invoice/batches/{body['id']}/projects").json()
    rows = {row["project"]: row for row in summary}
    assert rows["项目B"]["zip_ready"] is True and rows["项目B"]["count"] == 1
    assert rows["项目C"]["amount"] == 50.0
    assert rows["项目B"]["zip_file"] == "项目B2026年10月.zip"

    detail = client.get(f"/api/finance/invoice/batches/{body['id']}").json()
    assert detail["download_name"] == "发票汇总2026年10月.zip"

    resp = client.get(f"/api/finance/invoice/batches/{body['id']}/projects/download?project=项目B")
    assert resp.status_code == 200 and resp.content[:2] == b"PK"
    # 中文名同时给 filename*（UTF-8）与 ASCII 兜底，避免浏览器丢名成 UUID
    disposition = resp.headers["content-disposition"]
    assert "filename*=utf-8''" in disposition and "project_invoices.zip" in disposition
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        names = zf.namelist()
    assert any(name.startswith("26年10月项目B/") for name in names)
    assert any(name.endswith("发票清单.xlsx") for name in names)
    assert not any("项目C" in name for name in names)  # 拆分后互不混装

    assert client.get(
        f"/api/finance/invoice/batches/{body['id']}/projects/download?project=不存在项目"
    ).status_code == 404


def test_project_zip_name_uses_project_and_month(client, finance_dir, mock_engine, monkeypatch):
    """跨月批次：项目 ZIP 名与整批名都带月份区间（文件名口径 项目+月份）。"""
    pin_rules(monkeypatch, ["示例北路"], {"示例北路": "项目B"})
    mock_engine["a.pdf"] = make_fields(invoice_number="7301", project_name="示例北路", invoice_date="2026年10月01日")
    mock_engine["b.pdf"] = make_fields(invoice_number="7302", project_name="示例北路", invoice_date="2026年11月02日")
    body = upload(client, [("a.pdf", FAKE_PDF), ("b.pdf", FAKE_PDF)]).json()
    result = client.post(f"/api/finance/invoice/batches/{body['id']}/export").json()
    assert result["project_zips"]["项目B"] == "项目B2026年10-11月.zip"
    detail = client.get(f"/api/finance/invoice/batches/{body['id']}").json()
    assert detail["download_name"] == "发票汇总2026年10-11月.zip"
    # 整批下载响应同样带中文名
    resp = client.get(f"/api/finance/invoice/batches/{body['id']}/download")
    assert resp.status_code == 200
    assert "invoice_batch_" in resp.headers["content-disposition"]
    assert "filename*=utf-8''" in resp.headers["content-disposition"]


def test_months_suffix_variants():
    assert service._months_suffix([]) == ""
    assert service._months_suffix([(2026, 10)]) == "2026年10月"
    assert service._months_suffix([(2026, 10), (2026, 11)]) == "2026年10-11月"
    assert service._months_suffix([(2026, 12), (2027, 1)]) == "2026年12月-2027年1月"


def test_reclassify_invalidates_export(client, finance_dir, mock_engine, monkeypatch):
    pin_rules(monkeypatch, ["示例北路"], {"示例北路": "项目B"})
    mock_engine["a.pdf"] = make_fields(project_name="示例北路")
    body = upload(client, [("a.pdf", FAKE_PDF)]).json()
    assert client.post(f"/api/finance/invoice/batches/{body['id']}/export").status_code == 200
    resp = client.post(f"/api/finance/invoice/batches/{body['id']}/reclassify")
    assert resp.status_code == 200
    assert resp.json()["status"] == "parsed"  # 旧导出作废，需重新导出
    detail = client.get(f"/api/finance/invoice/batches/{body['id']}").json()
    assert detail["status"] == "parsed" and detail["export_file"] == ""
    assert client.get(
        f"/api/finance/invoice/batches/{body['id']}/projects/download?project=项目B"
    ).status_code == 409


# ---------------- 单项目 PDF 即时合并（真实合成 PDF） ----------------

def make_pdf(*widths: float) -> bytes:
    """合成真实 PDF：每页一个空白页，页宽作为页序指纹，合并顺序可精确断言。"""
    writer = PdfWriter()
    for width in widths:
        writer.add_blank_page(width=float(width), height=200.0)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def encrypted_pdf(width: float = 500.0, password: str = "secret") -> bytes:
    """合成带打开密码的 PDF：合并时必须判为不可读，不得静默跳过。"""
    writer = PdfWriter()
    writer.add_blank_page(width=float(width), height=200.0)
    writer.encrypt(password)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def page_widths(content: bytes) -> list[float]:
    return [round(float(page.mediabox.width), 2) for page in PdfReader(io.BytesIO(content)).pages]


def _upload_pdf_batch(client, mock_engine, spec: list[tuple[str, bytes, dict | None]]) -> dict:
    """spec: [(文件名, PDF 字节, 罐头解析字段)]；字段为 None 表示不登记（该文件解析失败）。"""
    files: list[tuple[str, bytes]] = []
    for fname, content, fields in spec:
        if fields is not None:
            mock_engine[fname] = make_fields(**fields)
        files.append((fname, content))
    resp = upload(client, files)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _project_pdf(client, batch_id: int, project: str):
    return client.get(
        f"/api/finance/invoice/batches/{batch_id}/projects/pdf", params={"project": project}
    )


def _tmp_pdfs(finance_dir: Path) -> list[Path]:
    return sorted((finance_dir / "finance_invoice" / "tmp").glob("*.pdf"))


def _inbox(finance_dir: Path, batch_id: int) -> Path:
    return finance_dir / "finance_invoice" / "inbox" / f"batch_{batch_id}"


def test_project_pdf_merges_pages_in_item_order(client, finance_dir, mock_engine):
    body = _upload_pdf_batch(client, mock_engine, [
        ("a.pdf", make_pdf(101, 102), dict(invoice_number="8101", project_name="项目B")),
        ("b.pdf", make_pdf(201), dict(invoice_number="8102", project_name="项目B")),
        ("c.pdf", make_pdf(301), dict(invoice_number="8103", project_name="项目C")),
    ])
    pdf_name = "项目B2026年10月发票.pdf"
    resp = _project_pdf(client, body["id"], "项目B")
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"] == "application/pdf"
    disposition = resp.headers["content-disposition"]
    assert 'filename="project_invoices.pdf"' in disposition  # ASCII 兜底
    assert f"filename*=utf-8''{quote(pdf_name)}" in disposition  # 中文名
    # 明细 ID 升序 + 每个原件保持原页序，不插封面/分隔页
    assert page_widths(resp.content) == [101.0, 102.0, 201.0]

    # 项目之间互不混装
    other = _project_pdf(client, body["id"], "项目C")
    assert other.status_code == 200
    assert page_widths(other.content) == [301.0]

    # 合并只读：批次状态/明细不变，且不在 outbox 留下导出产物
    detail = client.get(f"/api/finance/invoice/batches/{body['id']}").json()
    assert detail["status"] == "parsed" and detail["export_file"] == ""
    assert detail["items"] == body["items"]
    outbox = finance_dir / "finance_invoice" / "outbox"
    assert not any(path.is_file() for path in outbox.rglob("*"))


def test_project_pdf_name_single_and_cross_month(client, finance_dir, mock_engine):
    single = _upload_pdf_batch(client, mock_engine, [
        ("a.pdf", make_pdf(101), dict(invoice_number="8111", project_name="项目B", invoice_date="2026年10月01日")),
    ])
    rows = client.get(f"/api/finance/invoice/batches/{single['id']}/projects").json()
    assert rows[0]["pdf_name"] == "项目B2026年10月发票.pdf"
    assert rows[0]["zip_file"] == "" and rows[0]["zip_ready"] is False  # 未导出也有 pdf_name

    cross = _upload_pdf_batch(client, mock_engine, [
        ("c.pdf", make_pdf(102), dict(invoice_number="8112", project_name="项目B", invoice_date="2026年10月01日")),
        ("d.pdf", make_pdf(103), dict(invoice_number="8113", project_name="项目B", invoice_date="2026年11月02日")),
    ])
    rows = client.get(f"/api/finance/invoice/batches/{cross['id']}/projects").json()
    assert rows[0]["pdf_name"] == "项目B2026年10-11月发票.pdf"
    resp = _project_pdf(client, cross["id"], "项目B")
    assert resp.status_code == 200
    assert f"filename*=utf-8''{quote('项目B2026年10-11月发票.pdf')}" in resp.headers["content-disposition"]


def test_project_pdf_available_without_export(client, finance_dir, mock_engine):
    """未导出即可合并；导出后行为不变（同项目 PDF 与 ZIP 并存）。"""
    body = _upload_pdf_batch(client, mock_engine, [
        ("a.pdf", make_pdf(101), dict(invoice_number="8121", project_name="项目B")),
    ])
    assert client.get(f"/api/finance/invoice/batches/{body['id']}/projects/download?project=项目B").status_code == 409
    assert _project_pdf(client, body["id"], "项目B").status_code == 200

    assert client.post(f"/api/finance/invoice/batches/{body['id']}/export").status_code == 200
    exported = _project_pdf(client, body["id"], "项目B")
    assert exported.status_code == 200 and page_widths(exported.content) == [101.0]
    # 导出后状态仍保留，ZIP 下载不受影响
    zipped = client.get(f"/api/finance/invoice/batches/{body['id']}/projects/download?project=项目B")
    assert zipped.status_code == 200 and zipped.content[:2] == b"PK"


def test_project_pdf_excludes_duplicate_and_failed_keeps_red(client, finance_dir, mock_engine):
    body = _upload_pdf_batch(client, mock_engine, [
        ("a.pdf", make_pdf(101), dict(invoice_number="8201", check_code="c1", project_name="项目B")),
        ("b.pdf", make_pdf(102), dict(invoice_number="8201", check_code="c1", project_name="项目B")),
        ("red.pdf", make_pdf(103), dict(
            invoice_number="8202", amount="-100.00", invoice_direction="冲红发票", project_name="项目B"
        )),
        ("bad.pdf", make_pdf(104), None),
    ])
    status = {item["source_file"]: item["status"] for item in body["items"]}
    assert status["b.pdf"] == "duplicate" and status["bad.pdf"] == "failed"
    resp = _project_pdf(client, body["id"], "项目B")
    assert resp.status_code == 200
    # duplicate / failed 排除；红冲票保留在原顺序中
    assert page_widths(resp.content) == [101.0, 103.0]


def test_project_pdf_unclassified_group(client, finance_dir, mock_engine):
    body = _upload_pdf_batch(client, mock_engine, [
        ("a.pdf", make_pdf(111), dict(invoice_number="8301", project_name="")),
    ])
    rows = client.get(f"/api/finance/invoice/batches/{body['id']}/projects").json()
    assert rows[0]["project"] == "未归类"
    resp = _project_pdf(client, body["id"], "未归类")
    assert resp.status_code == 200 and page_widths(resp.content) == [111.0]
    assert f"filename*=utf-8''{quote('未归类2026年10月发票.pdf')}" in resp.headers["content-disposition"]


def test_project_pdf_missing_source_fails_whole_merge(client, finance_dir, mock_engine):
    body = _upload_pdf_batch(client, mock_engine, [
        ("a.pdf", make_pdf(101), dict(invoice_number="8401", project_name="项目B")),
        ("b.pdf", make_pdf(102), dict(invoice_number="8402", project_name="项目B")),
    ])
    (_inbox(finance_dir, body["id"]) / "b.pdf").unlink()
    resp = _project_pdf(client, body["id"], "项目B")
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert "8402" in detail and "b.pdf" in detail and "不存在" in detail  # 列明票号/源文件
    assert "8401" not in detail  # 只列真正有问题的原件
    assert _tmp_pdfs(finance_dir) == []  # 缺件不生成半成品


def test_project_pdf_corrupt_source_fails(client, finance_dir, mock_engine):
    body = _upload_pdf_batch(client, mock_engine, [
        ("a.pdf", make_pdf(101), dict(invoice_number="8501", project_name="项目B")),
    ])
    (_inbox(finance_dir, body["id"]) / "a.pdf").write_bytes(b"%PDF-1.7 not really a pdf")
    resp = _project_pdf(client, body["id"], "项目B")
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert "8501" in detail and "损坏" in detail
    assert _tmp_pdfs(finance_dir) == []


def test_project_pdf_encrypted_source_fails(client, finance_dir, mock_engine):
    body = _upload_pdf_batch(client, mock_engine, [
        ("a.pdf", encrypted_pdf(), dict(invoice_number="8601", project_name="项目B")),
    ])
    resp = _project_pdf(client, body["id"], "项目B")
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert "8601" in detail and "加密" in detail
    assert _tmp_pdfs(finance_dir) == []


def test_project_pdf_rejects_path_outside_batch_inbox(client, finance_dir, mock_engine, tmp_path):
    first = _upload_pdf_batch(client, mock_engine, [
        ("a.pdf", make_pdf(101), dict(invoice_number="8701", project_name="项目B")),
    ])
    second = _upload_pdf_batch(client, mock_engine, [
        ("b.pdf", make_pdf(102), dict(invoice_number="8702", project_name="项目B")),
    ])
    outside = tmp_path / "outside.pdf"
    outside.write_bytes(make_pdf(999))
    samples = {
        first["items"][0]["id"]: str(outside),  # 任意本机路径
        second["items"][0]["id"]: str(_inbox(finance_dir, first["id"]) / "a.pdf"),  # 其他批次入库文件
    }
    db = SessionLocal()
    try:
        for item_id, stored in samples.items():
            db.get(InvoiceItem, item_id).stored_path = stored
        db.commit()
    finally:
        db.close()
    for batch_id in (first["id"], second["id"]):
        resp = _project_pdf(client, batch_id, "项目B")
        assert resp.status_code == 409
        assert "超出本批次入库目录" in resp.json()["detail"]
    assert _tmp_pdfs(finance_dir) == []


def test_project_pdf_temp_file_cleaned_after_response(client, finance_dir, mock_engine):
    body = _upload_pdf_batch(client, mock_engine, [
        ("a.pdf", make_pdf(101), dict(invoice_number="8801", project_name="项目B")),
    ])
    assert _tmp_pdfs(finance_dir) == []
    resp = _project_pdf(client, body["id"], "项目B")
    assert resp.status_code == 200 and resp.content[:5] == b"%PDF-"
    # FileResponse 后台任务在响应结束后删除临时文件（不留长期缓存）
    assert _tmp_pdfs(finance_dir) == []


def test_project_pdf_404_for_unknown_batch_or_project(client, finance_dir, mock_engine):
    assert _project_pdf(client, 9999, "项目B").status_code == 404
    body = _upload_pdf_batch(client, mock_engine, [
        ("a.pdf", make_pdf(101), dict(invoice_number="8901", project_name="项目B")),
    ])
    resp = _project_pdf(client, body["id"], "不存在项目")
    assert resp.status_code == 404 and "项目" in resp.json()["detail"]


def test_project_pdf_batch_not_ready_409(client, finance_dir, mock_engine):
    db = SessionLocal()
    try:
        uploading = InvoiceBatch(name="解析中", status=InvoiceBatch.STATUS_UPLOADED)
        broken = InvoiceBatch(name="解析失败", status=InvoiceBatch.STATUS_FAILED)
        db.add_all([uploading, broken])
        db.commit()
        db.refresh(uploading)
        db.refresh(broken)
        uploading_id, broken_id = uploading.id, broken.id
    finally:
        db.close()
    resp = _project_pdf(client, uploading_id, "项目B")
    assert resp.status_code == 409 and "解析" in resp.json()["detail"]
    resp = _project_pdf(client, broken_id, "项目B")
    assert resp.status_code == 409 and "重新上传" in resp.json()["detail"]


def test_project_pdf_requires_project_param(client, finance_dir, mock_engine):
    body = _upload_pdf_batch(client, mock_engine, [
        ("a.pdf", make_pdf(101), dict(invoice_number="9001", project_name="项目B")),
    ])
    url = f"/api/finance/invoice/batches/{body['id']}/projects/pdf"
    assert client.get(url).status_code == 422  # 必填
    assert client.get(url, params={"project": ""}).status_code == 422  # 1–100 字
    assert client.get(url, params={"project": "x" * 101}).status_code == 422


def test_project_pdf_data_dir_unavailable_uses_chinese_error(client, finance_dir, mock_engine, monkeypatch):
    body = _upload_pdf_batch(client, mock_engine, [
        ("a.pdf", make_pdf(101), dict(invoice_number="9101", project_name="项目B")),
    ])
    db = SessionLocal()
    try:
        batch = db.get(InvoiceBatch, body["id"])
        def unavailable_dirs():
            raise OSError("synthetic disk unavailable")
        monkeypatch.setattr(invoice_export, "_ensure_dirs", unavailable_dirs)
        with pytest.raises(service.ProjectPdfError, match="数据目录不可用"):
            service.merge_project_pdf(db, batch, "项目B")
    finally:
        db.close()
