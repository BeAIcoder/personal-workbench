"""知识库文档解析：把 PDF / DOCX / PPTX / Markdown / 纯文本拆成带层级的块。

与 `document_text.py` 的区别：那里服务于聊天附件，只求「一段能读的正文」；
这里服务于检索与引用，必须保留**标题层级**与**页码**，才能切出
「第三章 差旅管理 > 3.2 住宿标准 · 第 7 页」这样的定位信息。

复用已有的 pdfplumber / python-docx / python-pptx。扫描件 OCR 是可选增强：
只有开启 `ocr=True` 且 rapidocr 可用时，才对**无文本层**的 PDF 页做识别。
"""
from __future__ import annotations

import logging
import re
import statistics
from dataclasses import dataclass, field
from pathlib import Path

from . import ocr as ocr_svc

logger = logging.getLogger(__name__)

MAX_DOC_CHARS = 300_000
MAX_PDF_PAGES = 500
MAX_PPT_SLIDES = 500
MAX_HEADING_CHARS = 60
SUPPORTED_SUFFIXES = frozenset({".pdf", ".docx", ".doc", ".pptx", ".ppt", ".md", ".txt"})

_HAN = "一-鿿"
_CN_NUM = "一二三四五六七八九十百零〇两"

# 制度/公文常见标题形态；顺序即优先级（章 > 节/条 > 括号/顿号/数字编号）
_HEADING_RULES: tuple[tuple[int, re.Pattern[str]], ...] = (
    (1, re.compile(rf"^第[{_CN_NUM}\d]+[章篇]")),
    (2, re.compile(rf"^第[{_CN_NUM}\d]+节")),
    (2, re.compile(rf"^第[{_CN_NUM}\d]+条")),
    (3, re.compile(rf"^[（(][{_CN_NUM}\d]+[)）]")),
    (3, re.compile(rf"^[{_CN_NUM}]+、")),
    (3, re.compile(r"^\d+(\.\d+){1,2}[\s、.．]")),
)

# 句式正文标志：含句号/叹号/问号的内容是正文而不是标题
# （如「（2）除上述单据外，……系统会强制报错。」）
_SENTENCE_RE = re.compile(r"[。！？]")


def looks_like_sentence(text: str) -> bool:
    """含句号/叹号/问号视为完整句子；标题几乎从不带这些标点。"""
    return bool(_SENTENCE_RE.search(_clean(text)))


class DocumentParseError(ValueError):
    """解析失败（格式不支持/无文字/损坏），路由层转 400 中文提示。"""


@dataclass
class Block:
    kind: str  # heading / text
    text: str
    level: int = 0
    page_no: int | None = None


@dataclass
class Extraction:
    blocks: list[Block] = field(default_factory=list)
    page_count: int = 0
    chars: int = 0
    truncated: bool = False
    # 实际走 OCR 的页数 / 是否因页数上限被截断（界面提示与入库字段用）
    ocr_pages: int = 0
    ocr_truncated: bool = False


def _is_han(ch: str) -> bool:
    return "一" <= ch <= "鿿"


def _clean(text: str) -> str:
    return re.sub(r"[ \t\u3000]+", " ", str(text or "")).strip()


def heading_level(text: str) -> int:
    """按公文形态判断标题层级；0 = 不是标题。"""
    value = _clean(text)
    if not value or len(value) > MAX_HEADING_CHARS:
        return 0
    for level, pattern in _HEADING_RULES:
        if pattern.match(value):
            # 三级编号形态（（2）/一、/1.2.）最容易误伤长句正文；
            # 命中后又带句读标点的按正文处理，避免正文被吸进标题路径丢失
            if level >= 3 and looks_like_sentence(value):
                return 0
            return level
    return 0


def _merge_words(words: list[dict]) -> str:
    """把同一行的 pdfplumber 词块拼回文本；中文之间不加空格。"""
    words = sorted(words, key=lambda w: float(w.get("x0") or 0))
    joined = " ".join(str(w.get("text") or "") for w in words if w.get("text"))
    return re.sub(rf"(?<=[{_HAN}])\s+(?=[{_HAN}])", "", joined)


def _page_lines(page) -> list[tuple[str, float]]:
    """取一页的文本行与字号（pdfplumber Page）。"""
    try:
        words = page.extract_words(extra_attrs=["size"]) or []
    except Exception:
        words = []
    if not words:
        raw = page.extract_text() or ""
        return [(_clean(line), 0.0) for line in raw.splitlines() if _clean(line)]

    lines: list[tuple[str, float]] = []
    groups: dict[int, list[dict]] = {}
    for word in words:
        key = round(float(word.get("top") or 0) / 4)
        groups.setdefault(key, []).append(word)
    for key in sorted(groups):
        text = _clean(_merge_words(groups[key]))
        if not text:
            continue
        sizes = [float(word.get("size") or 0) for word in groups[key]]
        lines.append((text, statistics.median(sizes) if sizes else 0.0))
    return lines


def _extract_pdf(source: Path, ocr: bool = False) -> Extraction:
    import pdfplumber

    result = Extraction()
    renderer = None
    if ocr and ocr_svc.available():
        try:
            renderer = ocr_svc.PdfPageRenderer(source)
        except Exception:
            logger.warning("PDF 渲染器初始化失败，本次不做 OCR", exc_info=True)
            renderer = None

    try:
        with pdfplumber.open(str(source)) as pdf:
            result.page_count = len(pdf.pages)
            for index, page in enumerate(pdf.pages):
                if index >= MAX_PDF_PAGES:
                    result.truncated = True
                    break
                page_no = index + 1
                lines = _page_lines(page)
                page_text = "\n".join(text for text, _ in lines).strip()

                # 只对「无文本层」的页 OCR；有文本层的页保持原解析结果
                if renderer is not None and len(page_text) < ocr_svc.MIN_TEXT_CHARS:
                    if result.ocr_pages >= ocr_svc.MAX_OCR_PAGES:
                        result.ocr_truncated = True
                    else:
                        try:
                            ocr_text, _ = ocr_svc.ocr_image(renderer.render(index))
                        except Exception:
                            logger.warning("第 %s 页 OCR 失败，跳过该页", page_no, exc_info=True)
                            ocr_text = ""
                        if ocr_text.strip():
                            lines = [
                                (line, 0.0)
                                for line in ocr_text.splitlines()
                                if line.strip()
                            ]
                            result.ocr_pages += 1

                medians = [size for _, size in lines if size > 0]
                body_size = statistics.median(medians) if medians else 0.0
                for text, size in lines:
                    if result.chars >= MAX_DOC_CHARS:
                        result.truncated = True
                        break
                    level = heading_level(text)
                    if not level and body_size and size >= body_size * 1.15:
                        if len(text) <= MAX_HEADING_CHARS and not text.endswith(("。", "；", "，", ".", ";", ",")):
                            level = 2
                    result.blocks.append(
                        Block("heading" if level else "text", text, level=level, page_no=page_no)
                    )
                    result.chars += len(text)
                if result.truncated:
                    break
    finally:
        if renderer is not None:
            renderer.close()
    return result


def _iter_docx_blocks(document):
    from docx.oxml.ns import qn
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    for child in document.element.body.iterchildren():
        if child.tag == qn("w:p"):
            yield Paragraph(child, document)
        elif child.tag == qn("w:tbl"):
            yield Table(child, document)


def _style_level(style_name: str) -> int:
    name = str(style_name or "").strip().lower().replace(" ", "")
    match = re.search(r"(heading|标题)(\d+)", name)
    if match:
        return max(1, min(int(match.group(2)), 6))
    if name in ("title", "标题"):
        return 1
    return 0


def _extract_docx(source: Path) -> Extraction:
    from docx import Document

    result = Extraction()
    document = Document(str(source))
    for item in _iter_docx_blocks(document):
        if result.chars >= MAX_DOC_CHARS:
            result.truncated = True
            break
        if hasattr(item, "rows"):  # Table
            for row in item.rows:
                cells = [_clean(cell.text) for cell in row.cells]
                line = " | ".join(cell for cell in cells if cell)
                if not line:
                    continue
                result.blocks.append(Block("text", line))
                result.chars += len(line)
                if result.chars >= MAX_DOC_CHARS:
                    result.truncated = True
                    break
            continue
        text = _clean(item.text)
        if not text:
            continue
        level = _style_level(getattr(item.style, "name", "")) or heading_level(text)
        result.blocks.append(Block("heading" if level else "text", text, level=level))
        result.chars += len(text)
    return result


def _walk_pptx_shapes(shapes, skip=None):
    """递归展开组合形状（group shapes）；skip 用于排除标题占位符。"""
    from pptx.enum.shapes import MSO_SHAPE_TYPE

    for shape in shapes:
        if shape is skip:
            continue
        if getattr(shape, "shape_type", None) == MSO_SHAPE_TYPE.GROUP:
            yield from _walk_pptx_shapes(shape.shapes)
            continue
        yield shape


def _pptx_shape_text(shape) -> str:
    """取形状文本；表格逐行取单元格，行内单元格用「 | 」连接（与 DOCX 表格一致）。"""
    if getattr(shape, "has_table", False):
        rows: list[str] = []
        for row in shape.table.rows:
            cells = [_clean(cell.text) for cell in row.cells]
            line = " | ".join(cell for cell in cells if cell)
            if line:
                rows.append(line)
        return "\n".join(rows)
    if getattr(shape, "has_text_frame", False):
        return _clean(shape.text_frame.text)
    return ""


def _extract_pptx(source: Path) -> Extraction:
    from pptx import Presentation

    result = Extraction()
    presentation = Presentation(str(source))
    result.page_count = len(presentation.slides)
    for index, slide in enumerate(presentation.slides):
        if index >= MAX_PPT_SLIDES:
            result.truncated = True
            break
        if result.chars >= MAX_DOC_CHARS:
            result.truncated = True
            break
        page_no = index + 1
        title_shape = slide.shapes.title
        title = _clean(title_shape.text) if title_shape is not None else ""
        if title:
            result.blocks.append(Block("heading", title, level=1, page_no=page_no))
            result.chars += len(title)
        for shape in _walk_pptx_shapes(slide.shapes, skip=title_shape):
            text = _pptx_shape_text(shape)
            if not text:
                continue
            result.blocks.append(Block("text", text, page_no=page_no))
            result.chars += len(text)
            if result.chars >= MAX_DOC_CHARS:
                result.truncated = True
                break
    return result


def _extract_text(source: Path) -> Extraction:
    result = Extraction()
    try:
        raw = source.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raw = source.read_text(encoding="gb18030", errors="replace")
    lines = raw.splitlines()
    paragraph: list[str] = []

    def flush() -> None:
        nonlocal paragraph
        text = _clean(" ".join(paragraph))
        paragraph = []
        if text and not result.truncated:
            result.blocks.append(Block("text", text))
            result.chars += len(text)
            if result.chars >= MAX_DOC_CHARS:
                result.truncated = True

    in_fence = False
    for raw_line in lines:
        if result.truncated:
            break
        line = raw_line.rstrip()
        if line.strip().startswith("```"):
            in_fence = not in_fence
            flush()
            continue
        if in_fence:
            paragraph.append(line)
            continue
        stripped = line.strip()
        if not stripped:
            flush()
            continue
        match = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if match:
            flush()
            heading = _clean(match.group(2))
            if heading:
                result.blocks.append(Block("heading", heading, level=len(match.group(1))))
                result.chars += len(heading)
            continue
        level = heading_level(stripped)
        if level and len(stripped) <= MAX_HEADING_CHARS:
            flush()
            result.blocks.append(Block("heading", stripped, level=level))
            result.chars += len(stripped)
            continue
        paragraph.append(stripped)
    flush()
    return result


def _convert_legacy(source: Path) -> Path:
    """旧版 .doc/.ppt 复用聊天附件的 LibreOffice 转换（同一套错误文案）。"""
    import tempfile

    from ..document_text import _convert_legacy_to_ooxml

    temp_dir = Path(tempfile.mkdtemp(prefix="kb-convert-"))
    try:
        return _convert_legacy_to_ooxml(source, temp_dir)
    except Exception as exc:
        raise DocumentParseError(str(exc)) from exc


def extract_blocks(source: Path, ocr: bool = False) -> Extraction:
    """解析文档为块序列；失败抛 DocumentParseError（中文可读）。

    `ocr=True` 且 rapidocr 可用时，仅对无文本层的 PDF 页做 OCR；
    其它格式（DOCX/PPTX/MD/TXT）忽略该参数。
    """
    source = Path(source)
    suffix = source.suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise DocumentParseError(f"不支持的知识库文档类型：{suffix or '无扩展名'}")
    if not source.is_file():
        raise DocumentParseError("源文件不存在或已被删除")

    temp_source: Path | None = None
    try:
        if suffix in {".doc", ".ppt"}:
            temp_source = _convert_legacy(source)
            suffix = temp_source.suffix.lower()
            actual = temp_source
        else:
            actual = source
        if suffix == ".pdf":
            result = _extract_pdf(actual, ocr=ocr)
        elif suffix == ".docx":
            result = _extract_docx(actual)
        elif suffix == ".pptx":
            result = _extract_pptx(actual)
        elif suffix in {".md", ".txt"}:
            result = _extract_text(actual)
        else:
            raise DocumentParseError(f"转换后的格式不受支持：{suffix}")
    except DocumentParseError:
        raise
    except Exception as exc:
        raise DocumentParseError(f"无法解析 {suffix} 文档，请确认文件格式正确且未损坏") from exc
    finally:
        if temp_source is not None:
            import shutil

            shutil.rmtree(temp_source.parent, ignore_errors=True)

    result.blocks = [b for b in result.blocks if b.text]
    if not result.blocks:
        if ocr and ocr_svc.available():
            raise DocumentParseError(
                "文档未提取到文字；已尝试 OCR 但未识别出内容，请确认扫描清晰度，或改用文字版文档"
            )
        raise DocumentParseError("文档未提取到文字；扫描件或纯图片文档请先进行 OCR")
    result.chars = sum(len(b.text) for b in result.blocks)
    return result
