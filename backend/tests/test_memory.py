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
    client.post(f"{P}/memories", json={"content": "项目背景：项目A公司，租金按季度预收。"})
    client.post(f"{P}/memories", json={"content": "免租期谈判经验：先争取装修期再谈经营免租。"})
    client.post(f"{P}/memories", json={"content": "置顶记忆：所有对外报表金额用万元。", "pinned": True})

    hits = client.post(f"{P}/memories/recall", json={"query": "免租期", "top_k": 3}).json()["items"]
    assert hits and "免租期" in hits[0]["content"]

    # 纯标点查询（空 token）返回空，不再返回任意 TopK（B8 修复后的口径）
    assert client.post(f"{P}/memories/recall", json={"query": "。", "top_k": 3}).json()["items"] == []

    # 同词命中时置顶优先（两条记忆都含「万元」，置顶层 +2 分）
    pin_hits = client.post(f"{P}/memories/recall", json={"query": "万元", "top_k": 3}).json()["items"]
    assert pin_hits[0]["content"].startswith("置顶记忆")

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


# ---------- Part E：类型/统计/导入导出/注入预算 ----------

def test_memory_type_roundtrip_and_filter(client):
    r = client.post(f"{P}/memories", json={
        "content": "用户偏好证据优先的结论呈现（PartE 测试）", "type": "feedback"})
    assert r.status_code == 201, r.text
    mid = r.json()["id"]
    assert r.json()["type"] == "feedback"

    # 非法类型归空
    r2 = client.post(f"{P}/memories", json={"content": "非法类型测试条目xyz", "type": "hack"})
    assert r2.status_code == 201 and r2.json()["type"] == ""

    # 类型过滤
    lst = client.get(f"{P}/memories", params={"type": "feedback"}).json()["items"]
    assert any(i["id"] == mid for i in lst)

    # 更新类型
    r3 = client.put(f"{P}/memories/{mid}", json={"type": "user"})
    assert r3.status_code == 200 and r3.json()["type"] == "user"
    client.delete(f"{P}/memories/{mid}")
    client.delete(f"{P}/memories/{r2.json()['id']}")


def test_memory_stats_and_export_import(client):
    r = client.post(f"{P}/memories", json={
        "content": "导出往返测试条目（PartE）", "type": "project", "pinned": True})
    mid = r.json()["id"]

    stats = client.get(f"{P}/memories/stats").json()
    assert stats["total"] >= 1
    assert "by_type" in stats and "by_source" in stats
    assert stats["pinned"] >= 1

    md = client.get(f"{P}/memories/export.md").text
    assert "## project" in md and "导出往返测试条目（PartE）" in md

    # 导入（近重复应跳过刚导出的条目）
    imp = client.post(f"{P}/memories/import.md", json={"text": md}).json()
    assert imp["skipped"] >= 1
    client.delete(f"{P}/memories/{mid}")


def test_memory_inject_budget(client):
    # 预算 0 → 注入不返回任何记忆
    r = client.put("/api/assistant/config", json={"memory_inject_max": 0})
    assert r.status_code in (200, 201), r.text
    try:
        from app.agents import memory as m
        assert m.recall_for_context("偏好 证据优先") == []
    finally:
        client.put("/api/assistant/config", json={"memory_inject_max": 3})
