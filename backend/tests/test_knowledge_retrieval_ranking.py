"""知识库检索排序回归：RRF 每通道每文档只计最佳 rank；展示块在两通道最佳候选中按通道 rank 择优。

全部使用临时测试库与合成数据；embedding 一律 monkeypatch，不触达外部网络。

排序控制手段（保证确定性）：
- 单字查询（「税」）让关键词通道走 LIKE 回退，排序固定为 (doc_id, seq)；
- 向量通道用 4 维 mock embedding，查询向量恒为 [1,0,0,0]，块向量 0 距离=rank 1。
"""
from __future__ import annotations

import pytest
from sqlalchemy import text

from app.database import Base, SessionLocal, engine
from app.knowledge import embedding as embedding_svc
from app.knowledge import index as index_svc
from app.knowledge import retrieval
from app.models import KbChunk, KbDoc

QUERY = "税"
NEAR = [1.0, 0.0, 0.0, 0.0]
FAR = [0.0, 1.0, 0.0, 0.0]


def _reset_kb() -> None:
    with engine.begin() as conn:
        conn.exec_driver_sql("DROP TABLE IF EXISTS kb_fts")
        conn.exec_driver_sql("DROP TABLE IF EXISTS kb_vec")
    index_svc.ensure_schema()


@pytest.fixture(autouse=True)
def _kb_tables():
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        db.query(KbChunk).delete()
        db.query(KbDoc).delete()
        db.commit()
    _reset_kb()
    yield


@pytest.fixture()
def fake_vector_query(monkeypatch):
    monkeypatch.setattr(
        embedding_svc,
        "resolve_config",
        lambda db: {
            "base_url": "http://127.0.0.1:9/v1",
            "api_key": "",
            "model": "fake",
            "dim": 4,
            "protocol": "openai",
        },
    )
    monkeypatch.setattr(
        embedding_svc,
        "embed_texts_sync",
        lambda texts, cfg: ([list(NEAR) for _ in texts], 4),
    )


def _make_doc(db, title: str, texts: list[str], *, vec: list[list[float]] | None = None) -> KbDoc:
    """建文档与块；texts 为各块正文（按 seq 顺序），vec 非空时按同顺序写向量索引。"""
    doc = KbDoc(title=title, doc_key=title, version="v1.0", status="effective")
    db.add(doc)
    db.flush()
    for seq, body in enumerate(texts, start=1):
        db.add(KbChunk(doc_id=doc.id, seq=seq, text=body, char_len=len(body)))
    db.commit()
    if vec is not None:
        assert index_svc.index_vectors(db, doc.id, vec), "向量索引应写入成功"
    return doc


# ---------------- RRF 融合：每通道每文档只计最佳 rank ----------------


def test_rrf_does_not_accumulate_multi_chunks_of_same_doc(fake_vector_query):
    """同一文档在关键词通道多个块命中时，RRF 只计该文档最佳 rank，不得重复累加。

    关键词通道（LIKE 稳定排序）：堆分文档A 4 个块占 rank 1-4，证据文档B 单块 rank 5；
    向量通道：证据文档B rank 1。
    修复前：A = 1/61+1/62+1/63+1/64 ≈ 0.063 压过 B = 1/61+1/65 ≈ 0.032；
    修复后：A = 1/61 ≈ 0.016 < B，向量最佳证据不应被多块堆分压制。
    """
    with SessionLocal() as db:
        _make_doc(db, "堆分文档A", ["税费条款甲", "税费条款乙", "税费条款丙", "税费条款丁"])
        _make_doc(db, "证据文档B", ["税费唯一条款"], vec=[list(NEAR)])

    hits = retrieval.search_items(QUERY, top_k=5, scope="docs")
    assert hits, "应有命中"
    assert [hit["title"] for hit in hits] == ["证据文档B", "堆分文档A"]


def test_rrf_uses_best_keyword_rank_across_docs(fake_vector_query):
    """文档 B 关键词 rank 更差但向量 rank 更好时，其综合得分按两通道各自最佳 rank 计算。"""
    with SessionLocal() as db:
        _make_doc(db, "堆分文档A", ["税费条款甲", "税费条款乙", "税费条款丙", "税费条款丁"])
        doc_b = _make_doc(db, "证据文档B", ["税费唯一条款"], vec=[list(NEAR)])

    hits = retrieval.search_items(QUERY, top_k=5, scope="docs")
    best = hits[0]
    assert best["doc"]["id"] == doc_b.id
    # 向量 rank 1 必须计入 B 的得分：0.516 ≈ (1/61) / (1/61+1/65)，若向量项丢失则为纯关键词比
    assert abs(best["score"] - 1.0) < 0.01
    assert 0.45 < hits[1]["score"] < 0.60


# ---------------- 展示块：两通道最佳候选按通道 rank 择优 ----------------


def test_display_chunk_prefers_better_vector_rank_over_worse_keyword_chunk(fake_vector_query):
    """向量 rank 1 的块不应被关键词 rank 2 的较差块无条件覆盖。

    探测制度：chunk1 命中关键词（关键词通道 rank 2，rank 1 被占位文档Z 占走），
    chunk3 是向量距离 0 的最佳证据（向量通道 rank 1）。
    修复前展示 chunk1（关键词无条件优先）；修复后按通道 rank 取 chunk3。
    """
    with SessionLocal() as db:
        _make_doc(db, "占位文档Z", ["税费相关规定占位"])
        doc = _make_doc(
            db,
            "探测制度",
            ["税费条款罗列占位甲", "无关填充内容乙", "量子引力波探测器独有正文丙"],
            vec=[list(FAR), list(FAR), list(NEAR)],
        )

    hits = retrieval.search_items(QUERY, top_k=5, scope="docs")
    assert hits and hits[0]["title"] == "探测制度"
    assert hits[0]["doc"]["chunk_id"] != 0
    assert "丙" in hits[0]["snippet"]
    # 引用定位应指向向量最佳块（第 3 块）
    with SessionLocal() as db:
        row = db.get(KbChunk, hits[0]["doc"]["chunk_id"])
        assert row is not None and row.doc_id == doc.id and row.seq == 3


def test_display_chunk_keeps_keyword_when_keyword_rank_not_worse(fake_vector_query):
    """关键词 rank ≤ 向量 rank 时维持关键词优先（兼容既有行为）。"""
    with SessionLocal() as db:
        doc = _make_doc(
            db,
            "并列制度",
            ["税费条款并列甲", "无关填充内容乙", "量子引力探测器正文丙"],
            vec=[list(FAR), list(FAR), list(NEAR)],
        )

    hits = retrieval.search_items(QUERY, top_k=5, scope="docs")
    assert hits and hits[0]["title"] == "并列制度"
    assert "甲" in hits[0]["snippet"]
    with SessionLocal() as db:
        row = db.get(KbChunk, hits[0]["doc"]["chunk_id"])
        assert row is not None and row.doc_id == doc.id and row.seq == 1


def test_display_chunk_keeps_keyword_chunk_when_doc_vector_only_worse(fake_vector_query):
    """向量命中同一文档更差块（rank 2）时，关键词 rank 1 的块仍应展示。"""
    with SessionLocal() as db:
        doc = _make_doc(
            db,
            "关键词优先制度",
            ["税费条款优先甲", "量子引力探测器正文乙"],
            vec=[list(NEAR), list(NEAR)],
        )

    hits = retrieval.search_items(QUERY, top_k=5, scope="docs")
    assert hits and hits[0]["title"] == "关键词优先制度"
    assert "甲" in hits[0]["snippet"]
    with SessionLocal() as db:
        row = db.get(KbChunk, hits[0]["doc"]["chunk_id"])
        assert row is not None and row.doc_id == doc.id and row.seq == 1


# ---------------- 响应 schema / top_k / 分数归一化兼容 ----------------


def test_response_schema_and_score_normalization_compat(fake_vector_query):
    with SessionLocal() as db:
        _make_doc(db, "堆分文档A", ["税费条款甲", "税费条款乙", "税费条款丙", "税费条款丁"])
        _make_doc(db, "证据文档B", ["税费唯一条款"], vec=[list(NEAR)])

    hits = retrieval.search_items(QUERY, top_k=2, scope="docs")
    assert len(hits) <= 2
    first = hits[0]
    assert set(first) == {"source", "id", "title", "snippet", "score", "citation", "doc"}
    assert first["source"] == "doc"
    assert set(first["doc"]) == {
        "id", "version", "status", "category", "department",
        "doc_no", "effective_from", "heading", "page_no", "chunk_id",
    }
    assert first["score"] == 1.0
    assert all(0.0 <= hit["score"] <= 1.0 for hit in hits)


def test_no_hit_returns_empty_list():
    assert retrieval.search_items("量子引力波探测器xyz", top_k=5, scope="docs") == []
