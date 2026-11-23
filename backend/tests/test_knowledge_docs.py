"""知识库文档测试：解析/切块、FTS5 检索、版本链、向量降级、API 与 Agent 工具。

全部使用合成文档与临时数据目录；embedding 一律 monkeypatch，不触达外部网络。
"""
from __future__ import annotations

import io
import struct
from pathlib import Path

import pytest
from sqlalchemy import text

from app.database import Base, SessionLocal, engine
from app.knowledge import embedding as embedding_svc
from app.knowledge import index as index_svc
from app.knowledge import ocr as ocr_svc
from app.knowledge import retrieval, service, store
from app.knowledge.chunk import build_chunks
from app.knowledge.extract import Block, Extraction, extract_blocks
from app.models import KbChunk, KbDoc

P = "/api/knowledge"

POLICY = """# 差旅管理制度

## 第三章 差旅管理

### 3.2 住宿标准
一线城市住宿标准为每人每晚 600 元，二线城市 450 元，凭发票据实报销。

### 3.3 交通标准
高铁二等座据实报销；飞机经济舱需提前审批。

## 第四章 报销流程
员工应在出差结束后 15 个工作日内提交报销单。
"""


def _reset_kb() -> None:
    with engine.begin() as conn:
        conn.exec_driver_sql("DROP TABLE IF EXISTS kb_fts")
        conn.exec_driver_sql("DROP TABLE IF EXISTS kb_vec")
    index_svc.ensure_schema()


@pytest.fixture(autouse=True)
def _kb_tables(request):
    """非 client 用例自建表；client 用例由 conftest 的 _isolate_database 负责。"""
    if "client" in request.fixturenames:
        yield
        return
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        db.query(KbChunk).delete()
        db.query(KbDoc).delete()
        db.commit()
    _reset_kb()
    yield


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        yield c


def _make_docx() -> bytes:
    from docx import Document

    document = Document()
    document.add_heading("差旅管理制度", level=1)
    document.add_heading("第三章 差旅管理", level=2)
    document.add_heading("3.2 住宿标准", level=3)
    document.add_paragraph("一线城市住宿标准为每人每晚 600 元，二线城市 450 元，凭发票据实报销。")
    document.add_heading("第四章 报销流程", level=2)
    document.add_paragraph("员工应在出差结束后 15 个工作日内提交报销单。")
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


# ---------------- 解析与切块 ----------------


def test_chunks_keep_heading_path_and_do_not_cross_sections(tmp_path: Path):
    path = tmp_path / "policy.md"
    path.write_text(POLICY, encoding="utf-8")
    extraction = extract_blocks(path)
    chunks = build_chunks(extraction.blocks)

    assert chunks, "应切出至少一个块"
    headings = [chunk.heading for chunk in chunks]
    assert any("住宿标准" in heading for heading in headings)
    assert any("报销流程" in heading for heading in headings)
    # 住宿与报销属于不同章节，不能落进同一块
    for chunk in chunks:
        assert not ("住宿标准" in chunk.heading and "报销单" in chunk.text)


def test_markdown_heading_levels(tmp_path: Path):
    path = tmp_path / "a.md"
    path.write_text("# 一级\n\n## 二级\n\n正文内容\n", encoding="utf-8")
    blocks = extract_blocks(path).blocks
    assert [(b.kind, b.level) for b in blocks[:2]] == [("heading", 1), ("heading", 2)]
    assert blocks[-1].kind == "text"


# ---------------- 入库与检索 ----------------


def test_ingest_and_two_char_keyword_search():
    with SessionLocal() as db:
        doc = service.ingest_text(db, title="差旅管理制度", content=POLICY, meta={"version": "v1.0"})
        assert doc.chunk_count >= 2
        assert doc.doc_key == "差旅管理制度"

        hits = retrieval.search_items("住宿", top_k=5)
        assert hits and hits[0]["source"] == "doc"
        assert "600" in hits[0]["snippet"]
        assert "v1.0" in hits[0]["citation"]
        assert "住宿标准" in hits[0]["citation"]


def test_single_char_query_falls_back_to_like():
    with SessionLocal() as db:
        service.ingest_text(db, title="税务制度", content="# 税务制度\n\n本办法所称税费包括增值税及附加。\n", meta={})
        hits = retrieval.search_items("税", top_k=5)
        assert any("税费" in hit["snippet"] for hit in hits)


def test_version_supersede_and_inactive_filter():
    with SessionLocal() as db:
        v1 = service.ingest_text(db, title="差旅管理制度", content=POLICY, meta={"version": "v1.0"})
        v2 = service.ingest_text(
            db,
            title="差旅管理制度",
            content="# 差旅管理制度\n\n## 3.2 住宿标准\n一线城市住宿标准调整为每人每晚 700 元。\n",
            meta={"version": "v2.0", "effective_from": "2026-11-01"},
        )
        db.refresh(v1)
        assert v1.status == "superseded"
        assert v1.effective_to == "2026-10-31"

        current = retrieval.search_items("住宿标准", top_k=5)
        assert [hit["doc"]["version"] for hit in current] == ["v2.0"]

        history = retrieval.search_items("住宿标准", top_k=5, include_inactive=True)
        assert {hit["doc"]["version"] for hit in history} == {"v1.0", "v2.0"}


def test_duplicate_version_rejected():
    with SessionLocal() as db:
        service.ingest_text(db, title="差旅管理制度", content=POLICY, meta={"version": "v1.0"})
        with pytest.raises(store.DuplicateDoc):
            service.ingest_text(db, title="差旅管理制度", content=POLICY, meta={"version": "v1.0"})


# ---------------- 向量检索（mock embedding） ----------------


def _fake_embed(texts, cfg):
    return [[1.0, 0.0, 0.0, 0.0] for _ in texts], 4


def test_vector_only_hit(monkeypatch):
    monkeypatch.setattr(embedding_svc, "resolve_config", lambda db: {
        "base_url": "http://127.0.0.1:9/v1", "api_key": "", "model": "fake", "dim": 4, "protocol": "openai",
    })
    monkeypatch.setattr(embedding_svc, "embed_texts_sync", _fake_embed)
    with SessionLocal() as db:
        doc = service.ingest_text(db, title="差旅管理制度", content=POLICY, meta={"version": "v1.0"})
        rows = db.query(KbChunk).filter(KbChunk.doc_id == doc.id).all()
        assert index_svc.index_vectors(db, doc.id, [[1.0, 0.0, 0.0, 0.0] for _ in rows])

        hits = retrieval.search_items("酒店住宿额度", top_k=5, scope="docs")
        assert hits and hits[0]["source"] == "doc"
        assert hits[0]["doc"]["id"] == doc.id


def test_vector_dimension_mismatch_is_refused():
    with SessionLocal() as db:
        doc = service.ingest_text(db, title="差旅管理制度", content=POLICY, meta={})
        rows = db.query(KbChunk).filter(KbChunk.doc_id == doc.id).count()
        assert index_svc.index_vectors(db, doc.id, [[1.0, 0.0, 0.0, 0.0] for _ in range(rows)])
        assert index_svc.index_vectors(db, doc.id, [[1.0, 0.0] for _ in range(rows)]) is False


# ---------------- API ----------------


def test_upload_docx_search_and_delete(client):
    response = client.post(
        f"{P}/docs/upload",
        files={"file": ("policy.docx", _make_docx(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
        data={"title": "差旅管理制度", "version": "v1.0", "category": "制度规范"},
    )
    assert response.status_code == 201, response.text
    doc = response.json()
    assert doc["chunk_count"] >= 2
    assert doc["source_name"] == "policy.docx"

    search = client.post(f"{P}/rag/search", json={"query": "住宿", "top_k": 5, "scope": "docs"})
    assert search.status_code == 200
    items = search.json()["items"]
    assert items and items[0]["doc"]["id"] == doc["id"]

    detail = client.get(f"{P}/docs/{doc['id']}")
    assert detail.status_code == 200
    assert detail.json()["outline"]

    download = client.get(f"{P}/docs/{doc['id']}/file")
    assert download.status_code == 200
    assert len(download.content) > 0

    assert client.delete(f"{P}/docs/{doc['id']}").status_code == 204
    after = client.post(f"{P}/rag/search", json={"query": "住宿", "top_k": 5, "scope": "docs"})
    assert after.json()["items"] == []


def test_upload_duplicate_hash_returns_409(client):
    payload = _make_docx()
    first = client.post(
        f"{P}/docs/upload",
        files={"file": ("policy.docx", payload, "application/octet-stream")},
        data={"title": "差旅管理制度"},
    )
    assert first.status_code == 201, first.text
    second = client.post(
        f"{P}/docs/upload",
        files={"file": ("policy-copy.docx", payload, "application/octet-stream")},
        data={"title": "差旅管理制度副本"},
    )
    assert second.status_code == 409
    assert "已入库" in second.json()["detail"]


def test_settings_roundtrip(client):
    response = client.get(f"{P}/settings")
    assert response.status_code == 200
    assert response.json()["embedding_enabled"] is False

    saved = client.put(
        f"{P}/settings",
        json={"embedding_enabled": True, "embedding_model": "fake-embed", "embedding_base_url": "http://127.0.0.1:9/v1"},
    )
    assert saved.status_code == 200
    assert saved.json()["embedding_model"] == "fake-embed"
    # 自定义地址没有专用 Key：禁用而不是回退聊天 Key
    assert saved.json()["resolved"] is False
    assert "Key" in saved.json()["resolved_detail"]

    with_key = client.put(f"{P}/settings", json={"embedding_api_key": "sk-test-only"})
    assert with_key.status_code == 200
    assert with_key.json()["has_api_key"] is True
    assert with_key.json()["resolved"] is True


# ---------------- Agent 工具 ----------------


def test_agent_tools_are_registered_and_callable():
    from app.agents.tools import TOOL_SPECS

    assert TOOL_SPECS["search_knowledge"]["readonly"] is True
    assert TOOL_SPECS["read_knowledge_doc"]["readonly"] is True

    with SessionLocal() as db:
        doc = service.ingest_text(db, title="差旅管理制度", content=POLICY, meta={})
        doc_id = doc.id

    hits = TOOL_SPECS["search_knowledge"]["func"]("住宿标准", 3, scope="docs")
    assert hits and hits[0]["doc"]["id"] == doc_id

    detail = TOOL_SPECS["read_knowledge_doc"]["func"](doc_id, 0, 20)
    assert detail["ok"] is True
    assert "600" in detail["lines"]


# ---------------- 扫描件 OCR ----------------

_CJK_FONT_CANDIDATES = (
    "C:/Windows/Fonts/simsun.ttc",
    "C:/Windows/Fonts/msyh.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/System/Library/Fonts/PingFang.ttc",
)


def _cjk_font() -> str | None:
    return next((path for path in _CJK_FONT_CANDIDATES if Path(path).is_file()), None)


def _image_pdf(path: Path, pages: int = 1, text: str = "") -> Path:
    """生成「只有图片、无文本层」的 PDF；text 非空时用中文字体写一行。"""
    from PIL import Image, ImageDraw, ImageFont
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas

    font = None
    font_path = _cjk_font()
    if text and font_path:
        font = ImageFont.truetype(font_path, 34)
    document = canvas.Canvas(str(path))
    for _ in range(pages):
        image = Image.new("RGB", (900, 260), "white")
        draw = ImageDraw.Draw(image)
        if text and font:
            draw.text((30, 60), text, font=font, fill="black")
        else:
            draw.rectangle([30, 60, 600, 160], fill="black")
        document.drawImage(ImageReader(image), 40, 520, width=450, height=130)
        document.showPage()
    document.save()
    return path


class _FakeRenderer:
    def __init__(self, _path):
        pass

    def close(self):
        pass

    def render(self, _index):
        from PIL import Image

        return Image.new("RGB", (20, 20), "white")


def _enable_fake_ocr(monkeypatch, text="第三章 差旅管理\n一线城市住宿标准为每人每晚600元。", pages=1):
    monkeypatch.setattr(ocr_svc, "available", lambda: True)
    monkeypatch.setattr(ocr_svc, "PdfPageRenderer", _FakeRenderer)
    monkeypatch.setattr(ocr_svc, "ocr_image", lambda _image: (text, len(text.splitlines())))
    monkeypatch.setattr(ocr_svc, "MAX_OCR_PAGES", pages)


def test_image_only_pdf_fails_without_ocr(tmp_path: Path):
    pdf = _image_pdf(tmp_path / "scan.pdf")
    with pytest.raises(Exception) as exc:
        extract_blocks(pdf)
    assert "OCR" in str(exc.value)


def test_pdf_ocr_fallback_for_image_only_page(monkeypatch, tmp_path: Path):
    pdf = _image_pdf(tmp_path / "scan.pdf")
    _enable_fake_ocr(monkeypatch)

    result = extract_blocks(pdf, ocr=True)
    text = "\n".join(block.text for block in result.blocks)
    assert "住宿标准" in text
    assert result.ocr_pages == 1
    assert result.blocks[0].page_no == 1


def test_pdf_with_text_layer_never_calls_ocr(monkeypatch, tmp_path: Path):
    from reportlab.pdfgen import canvas

    pdf = tmp_path / "text.pdf"
    document = canvas.Canvas(str(pdf))
    document.drawString(72, 720, "TEXT LAYER ONLY - THIS PAGE HAS REAL TEXT CONTENT")
    document.showPage()
    document.save()

    calls: list[int] = []
    monkeypatch.setattr(ocr_svc, "available", lambda: True)
    monkeypatch.setattr(ocr_svc, "PdfPageRenderer", _FakeRenderer)
    monkeypatch.setattr(ocr_svc, "ocr_image", lambda _image: calls.append(1) or ("不该被调用", 1))

    result = extract_blocks(pdf, ocr=True)
    assert calls == []
    assert "TEXT LAYER" in "\n".join(block.text for block in result.blocks)


def test_pdf_ocr_respects_page_cap(monkeypatch, tmp_path: Path):
    pdf = _image_pdf(tmp_path / "scan.pdf", pages=2)
    _enable_fake_ocr(monkeypatch, pages=1)

    result = extract_blocks(pdf, ocr=True)
    assert result.ocr_pages == 1
    assert result.ocr_truncated is True


def test_pdf_ocr_failure_keeps_clear_error(monkeypatch, tmp_path: Path):
    pdf = _image_pdf(tmp_path / "scan.pdf")
    monkeypatch.setattr(ocr_svc, "available", lambda: True)
    monkeypatch.setattr(ocr_svc, "PdfPageRenderer", _FakeRenderer)

    def _boom(_image):
        raise ocr_svc.OcrError("mock failure")

    monkeypatch.setattr(ocr_svc, "ocr_image", _boom)
    with pytest.raises(Exception) as exc:
        extract_blocks(pdf, ocr=True)
    assert "OCR" in str(exc.value)


def test_service_records_ocr_pages(monkeypatch, tmp_path: Path):
    pdf = tmp_path / "scan.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    monkeypatch.setattr(
        service,
        "extract_blocks",
        lambda _path, ocr=False: Extraction(
            blocks=[Block("text", "一线城市住宿标准为每人每晚600元。")],
            page_count=1,
            chars=20,
            ocr_pages=2,
        ),
    )
    with SessionLocal() as db:
        doc = service.ingest_file(
            db,
            path=pdf,
            filename="scan.pdf",
            source_rel_path="",
            source_size=1,
            source_hash="ocr-test-hash",
            meta={"title": "扫描制度"},
        )
        assert doc.ocr_pages == 2
        assert store.doc_out(doc)["ocr_pages"] == 2


def test_settings_include_ocr(client):
    saved = client.put(f"{P}/settings", json={"ocr_enabled": True})
    assert saved.status_code == 200
    assert saved.json()["ocr_enabled"] is True
    assert "ocr_available" in saved.json()


def test_embedding_endpoint_builder():
    base = "https://api.siliconflow.cn/v1"
    assert embedding_svc._endpoint(base) == f"{base}/embeddings"
    assert embedding_svc._endpoint(f"{base}/") == f"{base}/embeddings"
    assert embedding_svc._endpoint(f"{base}/embeddings") == f"{base}/embeddings"


def test_settings_test_uses_unsaved_form_values(monkeypatch, client):
    """「测试连接」必须用弹窗里当前的 model/base_url/key，而不是只读已保存配置。

    典型场景：用户刚粘贴 SiliconFlow 的 Key 就点测试（还没保存）；旧实现回退到
    当前激活供应商（DeepSeek）的 Key，打到 SiliconFlow 必然 401。
    """
    captured: dict = {}

    def _fake_embed(texts, cfg):
        captured.update(cfg)
        return [[0.1, 0.2, 0.3, 0.4]], 4

    monkeypatch.setattr(embedding_svc, "embed_texts_sync", _fake_embed)
    response = client.post(
        f"{P}/settings/test",
        json={
            "text": "制度检索",
            "embedding_model": "Qwen/Qwen3-Embedding-8B",
            "embedding_base_url": "https://api.siliconflow.cn/v1",
            "embedding_api_key": "sk-test-only",
        },
    )
    assert response.status_code == 200, response.text
    assert captured["model"] == "Qwen/Qwen3-Embedding-8B"
    assert captured["base_url"] == "https://api.siliconflow.cn/v1"
    assert captured["api_key"] == "sk-test-only"


@pytest.mark.skipif(not ocr_svc.available() or not _cjk_font(), reason="OCR 依赖或中文字体不可用")
def test_real_ocr_reads_scanned_pdf(tmp_path: Path):
    pdf = _image_pdf(tmp_path / "scan.pdf", text="差旅管理制度 一线城市住宿标准600元")
    result = extract_blocks(pdf, ocr=True)
    text = "\n".join(block.text for block in result.blocks)
    assert result.ocr_pages == 1
    assert "住宿" in text
    assert "600" in text
