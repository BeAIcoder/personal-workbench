"""结构感知切块：按标题边界聚合段落，超长章节才窗口切分。

与笔记 RAG 的 240 字定长切块不同，制度文档必须保留章节归属：
- 命中块要能回答「这句话出自哪一章哪一节」；
- 检索时返回命中块 + 前后块即可还原完整上下文（small-to-big）；
- 切块不跨标题边界，避免把「第三章」和「第四章」拼进同一块。
"""
from __future__ import annotations

from dataclasses import dataclass

from .extract import Block, looks_like_sentence

TARGET_CHARS = 600
OVERLAP_CHARS = 80
MAX_SECTION_CHARS = 2000
MAX_HEADING_PATH = 300


@dataclass
class Chunk:
    seq: int
    heading: str
    page_no: int | None
    text: str


def _heading_path(stack: list[tuple[int, str]]) -> str:
    path = " > ".join(text for _, text in stack if text)
    return path[:MAX_HEADING_PATH]


def _split_section(text: str, target: int, overlap: int) -> list[str]:
    """超长章节窗口切分：步长 target-overlap，尽量在换行/句号处断开。"""
    if len(text) <= target:
        return [text]
    step = max(target - overlap, 1)
    pieces: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + target, len(text))
        if end < len(text):
            window = text[start:end]
            cut = max(window.rfind("\n"), window.rfind("。"), window.rfind("；"))
            if cut >= target // 2:
                end = start + cut + 1
        piece = text[start:end].strip()
        if piece:
            pieces.append(piece)
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return pieces


def build_chunks(
    blocks: list[Block],
    target: int = TARGET_CHARS,
    overlap: int = OVERLAP_CHARS,
    max_section: int = MAX_SECTION_CHARS,
) -> list[Chunk]:
    """把解析块切成带标题路径与页码的块序列。"""
    chunks: list[Chunk] = []
    stack: list[tuple[int, str]] = []
    pending: list[tuple[str, int | None]] = []
    seq = 0

    def flush() -> None:
        nonlocal pending, seq
        if not pending:
            return
        body = "\n".join(text for text, _ in pending).strip()
        page_no = next((page for _, page in pending if page), None)
        pending = []
        if not body:
            return
        for piece in _split_section(body, target, overlap):
            chunks.append(Chunk(seq=seq, heading=_heading_path(stack), page_no=page_no, text=piece))
            seq += 1

    for block in blocks:
        if block.kind == "heading":
            flush()
            # 兜底：句式 heading（含句号/叹号/问号）其实是正文，
            # 不能只放进标题路径导致检索正文丢失
            if looks_like_sentence(block.text):
                pending.append((block.text, block.page_no))
                continue
            level = block.level or 1
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, block.text))
            continue
        # 跨页 section：页码变化时先收尾，保证每块页码与实际所在页一致，
        # 而不是把整节压成首页
        if block.page_no is not None and pending and pending[-1][1] != block.page_no:
            flush()
        pending.append((block.text, block.page_no))

    flush()
    return chunks
