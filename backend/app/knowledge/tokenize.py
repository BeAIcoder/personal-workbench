"""中文友好的轻量分词：字符二元组 + 英文/数字整词。

知识库与笔记 RAG 共用这一份实现（`agents/rag.py` 从这里导入），保证
「关键词检索」和「FTS5 索引」对同一段中文的切法一致，不会出现笔记能搜到、
制度搜不到的口径差异。

为什么是 bigram 而不是 trigram：SQLite FTS5 的 trigram 分词对 2 字中文词
（「报销」「调休」）完全无法命中；先切 bigram 再写入 unicode61 索引，
2 字/4 字中文与英文缩写都能正常召回。
"""
from __future__ import annotations

import re

_HAN = "一-鿿"


def bigrams(text: str) -> list[str]:
    """中文友好分词：连续二元组 + 英文/数字单词。

    含汉字的 alnum 连续段（如 "10月完成"）除整段 token 外，再补段内相邻
    bigram 与汉字单字，使 "10月"、"税" 这类混合词/单字查询可命中。
    """
    text = (text or "").lower()
    tokens: list[str] = []
    buf: list[str] = []

    def _flush() -> None:
        if not buf:
            return
        run = "".join(buf)
        tokens.append(run)
        if any("一" <= c <= "鿿" for c in run):
            tokens.extend(run[i : i + 2] for i in range(len(run) - 1))
            tokens.extend(c for c in run if "一" <= c <= "鿿")
        buf.clear()

    for ch in text:
        if ch.isalnum():
            buf.append(ch)
        else:
            _flush()
    _flush()
    han = re.sub(rf"[^{_HAN}]", "", text)
    tokens.extend(han[i : i + 2] for i in range(len(han) - 1))
    return tokens


def query_tokens(text: str) -> list[str]:
    """查询侧分词：存在多字 token 时丢弃单字 token。

    防单字泄漏误命中（「量子引力波探测器xyz」不应命中仅共享单字「子」的笔记）；
    单字查询（「税」）无多字 token 可丢弃，行为不变。
    """
    tokens = bigrams(text)
    if any(len(t) >= 2 for t in tokens):
        tokens = [t for t in tokens if len(t) >= 2]
    return tokens


def index_tokens(text: str) -> list[str]:
    """索引侧分词：只保留多字 token（整词 + bigram），不写单字。

    单字进 FTS 会显著放大索引并引入噪声；单字查询由 retrieval 的
    LIKE 回退兜底，行为对用户一致。
    """
    seen: set[str] = set()
    result: list[str] = []
    for token in bigrams(text):
        if len(token) < 2 or token in seen:
            continue
        seen.add(token)
        result.append(token)
    return result


def fts_text(text: str) -> str:
    """把原文转成 FTS5 unicode61 可索引的空格分隔 token 串。"""
    return " ".join(index_tokens(text))


def fts_query(text: str) -> str:
    """把查询串转成 FTS5 MATCH 表达式：每个 token 加引号后 OR 连接。

    token 只含字母数字（bigrams 的输出），额外做一次防御性过滤，
    杜绝把引号/星号/括号等 FTS5 语法字符带进查询。
    """
    tokens = query_tokens(text)
    safe = [re.sub(r"[^0-9A-Za-z" + _HAN + r"]", "", t) for t in tokens]
    safe = [t for t in safe if len(t) >= 2]
    if not safe:
        return ""
    return " OR ".join(f'"{t}"' for t in safe)
