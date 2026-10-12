"""RAG 知识库测试：BM25 检索（中文 bigram）、索引失效重建、Agent 工具注册。

全部用合成笔记；索引为模块级单例，用例间通过「增删笔记触发 stamp 变化」
自然失效，必要时显式 rebuild。
"""
import pytest

from app.agents import rag
from app.agents.tools import TOOL_SPECS
from app.models import Note
from app.database import SessionLocal

P = "/api/knowledge"


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        yield c


def _add_note(title: str, content: str) -> int:
    with SessionLocal() as db:
        note = Note(title=title, content=content, tags=[])
        db.add(note)
        db.commit()
        db.refresh(note)
        return note.id


def test_tool_registration():
    assert "search_knowledge" in TOOL_SPECS
    assert TOOL_SPECS["search_knowledge"]["readonly"] is True
    assert TOOL_SPECS["search_knowledge"]["required"] == ["query"]


def test_bm25_search_hits_relevant_note(client):
    _add_note("招商跟进纪要", "本周拜访了三个品牌客户，重点沟通租金阶梯与免租期方案，对方希望争取更长免租期。")
    _add_note("买菜清单", "鸡蛋、牛奶、土豆、西红柿，周末家用。")
    rag.rebuild_index()

    hits = rag.search("免租期方案", top_k=5)
    assert hits, "应命中招商纪要"
    assert "免租期" in hits[0]["snippet"]
    assert hits[0]["title"] == "招商跟进纪要"
    assert all(h["score"] > 0 for h in hits)


def test_search_no_hit_returns_empty(client):
    _add_note("空主题笔记", "完全无关的内容一二三四五。")
    rag.rebuild_index()
    assert rag.search("量子引力波探测器") == []


def test_index_auto_invalidate_on_new_note(client):
    rag.rebuild_index()
    before = rag.stats()
    _add_note("新笔记-索引失效验证", "索引应当在新笔记入库后自动重建，这条包含独特词：虹膜闸机。")
    hits = rag.search("虹膜闸机", top_k=3)
    assert any("虹膜闸机" in h["snippet"] for h in hits)
    after = rag.stats()
    assert after["chunks"] >= before["chunks"]


def test_api_endpoints(client):
    _add_note("接口验证笔记", "通过 HTTP 接口验证知识库检索与索引统计。")
    stats = client.get(f"{P}/rag/stats")
    assert stats.status_code == 200 and stats.json()["notes_indexed"] >= 1

    search = client.post(f"{P}/rag/search", json={"query": "知识库检索", "top_k": 5})
    assert search.status_code == 200
    assert any("接口验证笔记" == h["title"] for h in search.json()["items"])

    rebuild = client.post(f"{P}/rag/rebuild")
    assert rebuild.status_code == 200


def test_agent_tool_callable(client):
    _add_note("工具调用笔记", "Agent 工具 search_knowledge 应能检索到这条：钨丝灯报价。")
    rag.rebuild_index()
    hits = TOOL_SPECS["search_knowledge"]["func"]("钨丝灯报价")
    assert hits and any("钨丝灯" in h["snippet"] for h in hits)
