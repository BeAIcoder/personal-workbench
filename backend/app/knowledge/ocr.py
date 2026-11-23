"""扫描件 OCR：RapidOCR + onnxruntime（可选依赖，失败自动降级）。

设计边界：
- **只处理无文本层的 PDF 页**（由 extract.py 判断），有文本层就绝不 OCR；
- 模型随 rapidocr wheel 分发，运行时不联网；引擎懒加载单例 + 锁，单用户串行；
- 单文档 OCR 页数上限 `MAX_OCR_PAGES`，超出明确截断，不无限占 CPU；
- 任何一页失败只跳过该页，全部失败才由上层抛「未提取到文字」；
- 低置信度行直接丢弃（阈值 `MIN_SCORE`），避免把噪声写进知识库。
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

MIN_TEXT_CHARS = 20        # 单页文本少于该值才视为「无文本层」
MAX_OCR_PAGES = 50         # 单文档最多 OCR 页数
RENDER_SCALE = 2.5         # 约 180 DPI（72 * 2.5），中文印刷体足够
MIN_SCORE = 0.5

_engine = None
_engine_lock = threading.Lock()
_available: bool | None = None


class OcrError(ValueError):
    """OCR 不可用或单页识别失败（上层降级用，不直接展示给用户）。"""


def available() -> bool:
    """OCR 依赖是否可用；缺包时自动降级为原来的「请先 OCR」提示。"""
    global _available
    if _available is not None:
        return _available
    try:
        import onnxruntime  # noqa: F401
        import rapidocr  # noqa: F401

        _available = True
    except Exception:
        _available = False
    return _available


def _get_engine():
    global _engine
    if _engine is not None:
        return _engine
    with _engine_lock:
        if _engine is None:
            from rapidocr import RapidOCR

            # 降低 RapidOCR 每次加载模型的 INFO 噪声；异常仍会抛出由上层降级
            _engine = RapidOCR(params={"Global.log_level": "error"})
    return _engine


def _join_line(texts: list[str]) -> str:
    """同一行的片段拼接：中文直接相连，英文/数字之间补空格。"""
    out = ""
    for raw in texts:
        text = str(raw or "").strip()
        if not text:
            continue
        if out and out[-1].isascii() and text[0].isascii():
            out += " "
        out += text
    return out


def ocr_image(image) -> tuple[str, int]:
    """对 PIL 图片做 OCR，返回 (按阅读顺序的文本, 行数)。

    检测框按「先上后下、同行先左后右」排序；同一行按垂直中心聚类，
    避免 PDF 双栏/表格把一行拆成多行。
    """
    try:
        import numpy as np

        engine = _get_engine()
        result = engine(np.asarray(image.convert("RGB")))
    except Exception as exc:
        raise OcrError(f"OCR 识别失败：{type(exc).__name__}") from exc

    def _as_list(value):
        if value is None:
            return []
        try:
            return list(value)
        except TypeError:
            return []

    texts = _as_list(getattr(result, "txts", None))
    scores = _as_list(getattr(result, "scores", None))
    boxes = _as_list(getattr(result, "boxes", None))
    if not texts:
        return "", 0

    items: list[tuple[float, float, float, str]] = []
    for index, raw in enumerate(texts):
        score = float(scores[index]) if index < len(scores) else 1.0
        if score < MIN_SCORE:
            continue
        text = str(raw or "").strip()
        if not text:
            continue
        box = boxes[index] if index < len(boxes) else None
        if box is not None and len(box) >= 4:
            ys = [float(point[1]) for point in box]
            xs = [float(point[0]) for point in box]
            top, left, height = min(ys), min(xs), max(ys) - min(ys)
        else:
            top, left, height = float(index * 10), 0.0, 1.0
        items.append((top, left, height, text))

    if not items:
        return "", 0

    items.sort(key=lambda item: (item[0], item[1]))
    lines: list[str] = []
    current: list[tuple[float, str]] = []
    current_top: float | None = None
    current_height = 0.0
    for top, left, height, text in items:
        if current and current_top is not None:
            tolerance = max(current_height, height) * 0.6
            if abs(top - current_top) <= tolerance:
                current.append((left, text))
                current_height = max(current_height, height)
                continue
        if current:
            lines.append(_join_line([value for _, value in sorted(current)]))
        current = [(left, text)]
        current_top = top
        current_height = height
    if current:
        lines.append(_join_line([value for _, value in sorted(current)]))

    lines = [line for line in lines if line]
    return "\n".join(lines), len(lines)


class PdfPageRenderer:
    """按页渲染 PDF 为 PIL Image（pypdfium2，随 pdfplumber 已安装）。"""

    def __init__(self, path: Path):
        import pypdfium2 as pdfium

        self._pdf = pdfium.PdfDocument(str(path))

    def close(self) -> None:
        try:
            self._pdf.close()
        except Exception:
            pass

    def render(self, index: int):
        page = self._pdf[index]
        try:
            try:
                bitmap = page.render(scale=RENDER_SCALE, grayscale=True)
            except TypeError:
                bitmap = page.render(scale=RENDER_SCALE)
            try:
                return bitmap.to_pil()
            finally:
                bitmap.close()
        finally:
            page.close()


def ocr_pdf_page(path: Path, index: int) -> tuple[str, int]:
    """单页 PDF OCR 便捷入口（测试/聊天附件复用）。"""
    renderer = PdfPageRenderer(path)
    try:
        return ocr_image(renderer.render(index))
    finally:
        renderer.close()
