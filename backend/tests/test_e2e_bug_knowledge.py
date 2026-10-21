"""knowledge 区域深度 E2E 探测发现的 bug 回归测试（TDD：先 RED 后 GREEN）。

BUG（P2，检索精度）：查询侧单字 token 泄漏进多字查询——
- RAG：「量子引力波探测器xyz」与「数电发票常见问题记录」仅共享单字「子」，
  BM25 仍给出正分并作为命中返回；用户搜一个词库里没有的词，看到一堆
  不相关笔记带 score 展示，属于误导性结果。
- recall（长期记忆检索）同源：多字查询命中仅共享单字的记忆并累计 hit_count。

修复口径：查询侧存在多字 token 时丢弃单字 token；单字查询（「税」）与
中文数字混合词（「10月」）行为不变——这两个是上轮修复点，一并回归。
"""
import pytest

from app.agents import memory, rag
from app.database import SessionLocal
from app.models import Memory, Note

P = "/api/knowledge"

# client fixture 用 conftest 的版本（每个测试 drop_all/create_all 全量重置，
# 本文件的用例会写 notes/memories，不能残留给后续测试文件）


def _add_note(title: str, content: str) -> int:
    with SessionLocal() as db:
        note = Note(title=title, content=content, tags=[])
        db.add(note)
        db.commit()
        db.refresh(note)
        return note.id


def test_multi_char_query_not_matched_by_shared_single_char(client):
    _add_note("数电发票常见问题记录",
              "- 授信额度不足：可在电子税务局申请调整\n- 红字发票：对方已入账的需对方确认后才能开具")
    rag.rebuild_index()
    # 与全库仅共享单字「子」，不得作为命中返回
    assert rag.search("量子引力波探测器xyz", top_k=8) == []


def test_search_single_char_token_still_hits(client):
    """上轮修复点回归：单字查询「税」仍可命中。"""
    _add_note("发票报销审核要点", "1. 发票抬头、税号是否正确\n2. 数电票需查验真伪")
    rag.rebuild_index()
    hits = rag.search("税", top_k=8)
    assert hits and any("税号" in h["snippet"] for h in hits)


def test_search_mixed_digit_han_token_still_hits(client):
    """上轮修复点回归：中文数字混合词「10月」仍可命中且居首。"""
    _add_note("10月税务申报纪要", "10月增值税申报：进项发票需在360天内认证。")
    rag.rebuild_index()
    hits = rag.search("10月", top_k=8)
    assert hits and hits[0]["title"] == "10月税务申报纪要"


def test_recall_multi_char_query_not_matched_by_shared_single_char(client):
    client.post(f"{P}/memories", json={"content": "授信额度不足：可在电子税务局申请调整。"})
    # 仅共享单字「子」：不得命中，更不得累计 hit_count
    assert client.post(f"{P}/memories/recall",
                       json={"query": "量子引力波探测器xyz", "top_k": 3}).json()["items"] == []
    with SessionLocal() as db:
        row = db.query(Memory).filter(Memory.content.like("授信额度%")).first()
        assert row.hit_count == 0

    # 正常查询不受影响，且只累计一次
    hits = client.post(f"{P}/memories/recall", json={"query": "额度", "top_k": 3}).json()["items"]
    assert hits and "额度" in hits[0]["content"]
    with SessionLocal() as db:
        row = db.query(Memory).filter(Memory.content.like("授信额度%")).first()
        assert row.hit_count == 1


def test_recall_single_char_token_still_hits(client):
    """单字查询在记忆检索同样保留。"""
    client.post(f"{P}/memories", json={"content": "发票偏好：进项税发票按季度集中认证。"})
    hits = client.post(f"{P}/memories/recall", json={"query": "税", "top_k": 3}).json()["items"]
    assert hits and any("税" in h["content"] for h in hits)
