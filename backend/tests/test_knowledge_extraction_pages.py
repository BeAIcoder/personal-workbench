"""知识库解析/切块的三类缺陷回归测试：PPTX 组合形状与表格、跨页 page_no、句式正文误判标题。

全部使用合成 docx/pptx/pdf 与内存 Block；不读真实数据目录。
"""
from __future__ import annotations

import io
from pathlib import Path

from docx import Document
from pptx import Presentation
from pptx.util import Pt

from app.knowledge.chunk import build_chunks
from app.knowledge.extract import Block, extract_blocks

SENTENCE = "（2）除上述单据外，如果在结算单不关联资金计划在生成总账凭证环节系统会强制报错。"


def _all_text(blocks) -> str:
    return "\n".join(block.text for block in blocks)


def _chunk_text(chunks) -> str:
    return "\n".join(chunk.text for chunk in chunks)


# ---------------- PPTX：组合形状递归 + 表格单元格 + slide 页码 ----------------


def _make_pptx() -> bytes:
    presentation = Presentation()
    first = presentation.slides.add_slide(presentation.slide_layouts[6])
    top = first.shapes.add_textbox(Pt(10), Pt(10), Pt(300), Pt(40))
    top.text_frame.text = "顶层文本框内容"

    group = first.shapes.add_group_shape()
    inner = group.shapes.add_textbox(Pt(20), Pt(60), Pt(300), Pt(40))
    inner.text_frame.text = "组合形状内部文本"

    table_shape = first.shapes.add_table(2, 2, Pt(10), Pt(120), Pt(400), Pt(80))
    table = table_shape.table
    table.cell(0, 0).text = "表头A"
    table.cell(0, 1).text = "表头B"
    table.cell(1, 0).text = "数据1"
    table.cell(1, 1).text = "数据2"

    second = presentation.slides.add_slide(presentation.slide_layouts[6])
    second_tb = second.shapes.add_textbox(Pt(10), Pt(10), Pt(300), Pt(40))
    second_tb.text_frame.text = "第二页的文本"

    buffer = io.BytesIO()
    presentation.save(buffer)
    return buffer.getvalue()


def test_pptx_group_shapes_and_table_cells_extracted_with_slide_no(tmp_path: Path):
    path = tmp_path / "deck.pptx"
    path.write_bytes(_make_pptx())

    extraction = extract_blocks(path)

    text = _all_text(extraction.blocks)
    assert "顶层文本框内容" in text
    assert "组合形状内部文本" in text, "组合形状（group shapes）内部文本必须被递归抽取"
    assert "表头A" in text and "数据2" in text, "表格单元格文本必须被抽取"

    for block in extraction.blocks:
        expected = 2 if block.text == "第二页的文本" else 1
        assert block.page_no == expected, f"块 {block.text!r} 应带 slide 页码 {expected}"


# ---------------- 跨页 section：page_no 不得被压成首页 ----------------


def test_cross_page_section_keeps_page_mapping():
    blocks = [
        Block("heading", "第三章 差旅管理", level=1, page_no=1),
        Block("text", "第一页正文甲，内容足够长一些。", page_no=1),
        Block("text", "第二页正文乙，内容足够长一些。", page_no=2),
        Block("text", "第三页正文丙，内容足够长一些。", page_no=3),
    ]

    chunks = build_chunks(blocks)

    assert chunks
    by_text = {chunk.text: chunk.page_no for chunk in chunks}
    assert any(page == 2 for page in by_text.values()), "第二页的正文必须映射到 page 2"
    assert any(page == 3 for page in by_text.values()), "第三页的正文必须映射到 page 3"
    # 不允许把全部内容压到第一页
    assert len({page for page in by_text.values()}) >= 2


def test_pdf_section_spanning_pages_keeps_both_pages(tmp_path: Path):
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.pdfgen import canvas

    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))

    pdf = tmp_path / "policy.pdf"
    document = canvas.Canvas(str(pdf))
    document.setFont("STSong-Light", 12)
    document.drawString(72, 720, "第三章 差旅管理")
    document.drawString(72, 690, "第一页正文甲：住宿标准为每人每晚600元。")
    document.showPage()
    document.setFont("STSong-Light", 12)
    document.drawString(72, 720, "第二页正文乙：交通标准为高铁二等座据实报销。")
    document.save()

    extraction = extract_blocks(pdf)
    chunks = build_chunks(extraction.blocks)

    pages = {chunk.text: chunk.page_no for chunk in chunks}
    assert any("第一页正文甲" in text for text in pages), "首页内容应切出"
    assert any("第二页正文乙" in text for text in pages), "次页内容应切出"
    second_pages = [page for text, page in pages.items() if "第二页正文乙" in text]
    assert second_pages and all(page == 2 for page in second_pages), "同章节第二页正文不得被压成第 1 页"


# ---------------- 句式正文不得被标题启发式吞掉 ----------------


def _make_docx_with_sentence() -> bytes:
    document = Document()
    document.add_heading("资金计划管理办法", level=1)
    document.add_paragraph(SENTENCE)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def test_docx_numbered_body_sentence_stays_text(tmp_path: Path):
    path = tmp_path / "policy.docx"
    path.write_bytes(_make_docx_with_sentence())

    extraction = extract_blocks(path)

    sentence_blocks = [block for block in extraction.blocks if SENTENCE in block.text]
    assert sentence_blocks, "句式正文必须保留为解析块"
    assert all(block.kind == "text" for block in sentence_blocks), "正文句子不应被识别为标题"

    chunks = build_chunks(extraction.blocks)
    assert any(SENTENCE in chunk.text for chunk in chunks), "句式正文必须进入切块正文"
    matched = [chunk for chunk in chunks if SENTENCE in chunk.text]
    assert all("资金计划管理办法" in chunk.heading for chunk in matched), "正文应挂在前置标题下"


def test_chunk_treats_sentence_like_heading_block_as_body():
    """兜底：即便上游把句式正文标成 heading，切块也不得只放进标题路径而丢失正文。"""
    blocks = [
        Block("heading", "资金计划管理办法", level=1),
        Block("heading", SENTENCE, level=3),
    ]

    chunks = build_chunks(blocks)

    assert any(SENTENCE in chunk.text for chunk in chunks), "句式 heading 的正文必须落入切块正文"
