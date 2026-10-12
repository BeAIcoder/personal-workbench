"""长期记忆测试：CRUD、近重复去重、检索加权、上下文注入、API。

extract_from_conversation 需要真实 LLM，不在单测覆盖（手动路径全覆盖）。
"""
import pytest

from app.agents import memory
from app.agents.tools import TOOL_SPECS
from app.database import SessionLocal
from app.models import Memory

P = "/api/knowledge"


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        yield c


def _count() -> int:
    with SessionLocal() as db:
        return db.query(Memory).count()


def test_tool_registration():
    assert "recall_memories" in TOOL_SPECS
    assert TOOL_SPECS["recall_memories"]["readonly"] is True


def test_crud_and_dedupe(client):
    created = client.post(f"{P}/memories", json={"content": "用户偏好：报表金额单位统一用万元，保留两位小数。"})
    assert created.status_code == 201
    mid = created.json()["id"]
    assert _count() == 1

    # 近重复（前 80 字相同）→ 400
    dup = client.post(f"{P}/memories", json={"content": "用户偏好：报表金额单位统一用万元，保留两位小数。补充说明。"})
    assert dup.status_code == 400 and "相同" in dup.json()["detail"]

    # 更新（置顶）
    upd = client.put(f"{P}/memories/{mid}", json={"pinned": True})
    assert upd.json()["pinned"] is True

    # 列表与删除
    lst = client.get(f"{P}/memories")
    assert lst.json()["items"][0]["pinned"] is True
    client.delete(f"{P}/memories/{mid}")
    assert _count() == 0
    assert client.delete(f"{P}/memories/{mid}").status_code == 404


def test_recall_keyword_and_pinned_boost(client):
    client.post(f"{P}/memories", json={"content": "项目背景：某商业管理公司，租金按季度预收。"})
    client.post(f"{P}/memories", json={"content": "免租期谈判经验：先争取装修期再谈经营免租。"})
    client.post(f"{P}/memories", json={"content": "置顶记忆：所有对外报表金额用万元。", "pinned": True})

    hits = client.post(f"{P}/memories/recall", json={"query": "免租期", "top_k": 3}).json()["items"]
    assert hits and "免租期" in hits[0]["content"]

    # 无关键词时置顶优先（三条记忆都含句号，用「。」做共同 token）
    allhits = client.post(f"{P}/memories/recall", json={"query": "。", "top_k": 3}).json()["items"]
    assert allhits[0]["content"].startswith("置顶记忆")

    # 命中累计 hit_count
    with SessionLocal() as db:
        row = db.query(Memory).filter(Memory.content.like("免租期%")).first()
        assert row.hit_count >= 1

    # Agent 工具
    tool_hits = TOOL_SPECS["recall_memories"]["func"]("免租期")
    assert tool_hits and "免租期" in tool_hits[0]["content"]


def test_memory_context_injection(client):
    """编排器上下文注入：命中时消息带前缀，无命中/异常时原样返回。"""
    from app.agents.orchestrator import _with_memory_context

    client.post(f"{P}/memories", json={"content": "沟通偏好：先结论后细节，控制在三段以内。"})
    # 查询与记忆有 bigram 重叠（结论/细节）→ 命中注入
    out = _with_memory_context("帮我起草一封先结论后细节的催租函")
    assert out.startswith("[长期记忆参考]") and "先结论后细节" in out

    # 无命中：原样返回
    plain = _with_memory_context("量子引力波探测器")
    assert plain == "量子引力波探测器"
