"""P2 长期记忆：召回 casefold / 多 token 噪声阈值 / source_doc_id 显式引用。

覆盖口径（TDD，先红后绿）：
- 召回 token 与 content 统一 casefold：英文查询不因大小写漏召回；
- 多 token 查询要求 ≥2 个有效 token 重叠，仅通用 bigram 重叠不再召回无关记忆；
  单 token / 全通用 token 的短查询回退宽松阈值（min_overlap=1），不静默失效；
- source_doc_id：显式引用 kb_docs（不自动从文档生成记忆），create/update/list/recall
  输出 source_doc_id 与可展示的 source_doc_title；非法/不存在 doc id 明确 400；
- 文档删除后记忆引用安全置空，记忆本体保留；
- 注入路径（recall_for_context）输出携带可溯源的来源文档名。
"""
import pytest

from app.database import SessionLocal
from app.models import Memory

P = "/api/knowledge"


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        yield c


def _create_memory(client, payload: dict) -> dict:
    r = client.post(f"{P}/memories", json=payload)
    assert r.status_code == 201, r.text
    return r.json()


def _create_doc(client, title: str, content: str = "这是用于记忆来源引用测试的知识库文档正文内容，长度超过十个字。") -> dict:
    r = client.post(f"{P}/docs", json={"title": title, "content": content})
    assert r.status_code == 201, r.text
    return r.json()


# ---------- 召回 casefold ----------

def test_recall_casefold_english(client):
    """英文记忆用任意大小写查询都应命中（token 与 content 统一 casefold）。"""
    _create_memory(client, {"content": "做账统一用 SAP Fiori 导出月度辅助余额表。"})
    _create_memory(client, {"content": "季度对账时 fiori 报表要重跑两次再核对。"})

    for query in ("sap", "SAP", "fiori", "FIORI"):
        hits = client.post(f"{P}/memories/recall", json={"query": query, "top_k": 5}).json()["items"]
        assert hits, f"查询 {query!r} 未命中任何记忆（casefold 缺失）"
        assert any("fiori" in (h["content"].casefold()) for h in hits), (
            f"查询 {query!r} 命中了无关记忆：{[h['content'] for h in hits]}"
        )


# ---------- 多 token 噪声阈值 ----------

def test_recall_multi_token_generic_noise_filtered(client):
    """多 token 查询：仅 1 个通用 bigram 重叠的无关记忆不再召回，相关记忆仍命中。"""
    _create_memory(client, {"content": "公司组织架构与各部门职责范围说明（噪声样本）。"})
    _create_memory(client, {"content": "发票报销需要附上发票抬头和税号，走线下审批流程。"})

    hits = client.post(
        f"{P}/memories/recall",
        json={"query": "公司的发票报销制度是什么流程", "top_k": 5},
    ).json()["items"]
    contents = [h["content"] for h in hits]
    assert not any("组织架构" in c for c in contents), f"无关记忆被通用词召回：{contents}"
    assert any("发票报销" in c for c in contents), f"相关记忆未被召回：{contents}"


def test_recall_single_token_and_short_query_not_silently_broken(client):
    """单 token / 短查询保持宽松阈值：只重叠 1 个 token 也命中，不静默失效。"""
    _create_memory(client, {"content": "免租期谈判经验：先争取装修期再谈经营免租。"})
    _create_memory(client, {"content": "所有对外报表金额单位统一用万元。"})

    for query in ("免租期", "万元", "谈判"):
        hits = client.post(f"{P}/memories/recall", json={"query": query, "top_k": 5}).json()["items"]
        assert hits, f"短查询 {query!r} 被阈值静默失效"
        assert any(query in h["content"] for h in hits), (
            f"短查询 {query!r} 未命中包含该词的记忆：{[h['content'] for h in hits]}"
        )

    # 纯空白/标点查询仍返回空（不返回任意结果）
    assert client.post(f"{P}/memories/recall", json={"query": "。", "top_k": 3}).json()["items"] == []


# ---------- source_doc_id 显式引用 ----------

def test_memory_source_doc_crud_output(client):
    """create/update/list/recall 输出 source_doc_id 与可展示的来源文档名。"""
    doc = _create_doc(client, "报销制度汇编v3")
    mid = _create_memory(client, {
        "content": "跨部门报销口径以《报销制度汇编v3》第 4 条为准。",
        "source_doc_id": doc["id"],
    })["id"]

    row = next(i for i in client.get(f"{P}/memories").json()["items"] if i["id"] == mid)
    assert row["source_doc_id"] == doc["id"]
    assert row["source_doc_title"] == "报销制度汇编v3"

    # recall 输出同样携带来源引用与文档名
    hits = client.post(f"{P}/memories/recall", json={"query": "报销口径", "top_k": 5}).json()["items"]
    hit = next(h for h in hits if h["id"] == mid)
    assert hit["source_doc_id"] == doc["id"] and hit["source_doc_title"] == "报销制度汇编v3"

    # 更新为空 → 显式清空引用
    upd = client.put(f"{P}/memories/{mid}", json={"source_doc_id": None})
    assert upd.status_code == 200, upd.text
    assert upd.json()["source_doc_id"] is None and upd.json()["source_doc_title"] == ""

    # 再改回合法引用
    upd2 = client.put(f"{P}/memories/{mid}", json={"source_doc_id": doc["id"]})
    assert upd2.status_code == 200 and upd2.json()["source_doc_id"] == doc["id"]
    client.delete(f"{P}/memories/{mid}")


def test_memory_source_doc_invalid_id_rejected(client):
    """非法 / 不存在的 doc id：明确 400（中文原因），不静默忽略。"""
    for bad in (0, -1, 999999):
        r = client.post(f"{P}/memories", json={"content": "引用非法文档测试条目。", "source_doc_id": bad})
        assert r.status_code == 400, f"source_doc_id={bad} 应 400，实得 {r.status_code}"
        assert "文档" in r.json()["detail"]

    ok = _create_memory(client, {"content": "更新引用非法文档测试条目。"})
    r2 = client.put(f"{P}/memories/{ok['id']}", json={"source_doc_id": 999999})
    assert r2.status_code == 400 and "文档" in r2.json()["detail"]
    client.delete(f"{P}/memories/{ok['id']}")

    # 非整数类型：4xx（pydantic 校验）
    r3 = client.post(f"{P}/memories", json={"content": "类型非法测试条目。", "source_doc_id": "abc"})
    assert 400 <= r3.status_code < 500


def test_update_other_fields_keep_source_ref(client):
    """只更新 content/type/pinned/tags 时，既有来源 id/title 均保持不丢；
    显式换来源 / 显式清空仍按入参生效。"""
    doc_a = _create_doc(client, "口径文档A")
    doc_b = _create_doc(client, "口径文档B")
    mid = _create_memory(client, {
        "content": "记账口径以《口径文档A》为准。",
        "source_doc_id": doc_a["id"],
    })["id"]

    # 只更新非来源字段：来源 id 与 title 必须保持
    upd = client.put(f"{P}/memories/{mid}", json={
        "content": "记账口径以《口径文档A》为准，含更新说明。",
        "type": "project", "pinned": True, "tags": ["口径"],
    })
    assert upd.status_code == 200, upd.text
    assert upd.json()["source_doc_id"] == doc_a["id"], "未触碰来源字段时来源 id 丢失"
    assert upd.json()["source_doc_title"] == "口径文档A", "未触碰来源字段时来源文档名丢失"

    # 显式换来源
    upd2 = client.put(f"{P}/memories/{mid}", json={"source_doc_id": doc_b["id"]})
    assert upd2.json()["source_doc_id"] == doc_b["id"]
    assert upd2.json()["source_doc_title"] == "口径文档B"

    # 显式清空
    upd3 = client.put(f"{P}/memories/{mid}", json={"source_doc_id": None})
    assert upd3.json()["source_doc_id"] is None
    assert upd3.json()["source_doc_title"] == ""
    client.delete(f"{P}/memories/{mid}")


def test_doc_delete_clears_memory_reference(client):
    """删除知识库文档：引用它的记忆置空引用、保留本体，不出孤儿引用。"""
    doc = _create_doc(client, "将被删除的制度文档")
    created = _create_memory(client, {"content": "该记忆引用了将被删除的制度文档。", "source_doc_id": doc["id"]})
    mid = created["id"]

    assert client.delete(f"{P}/docs/{doc['id']}").status_code == 204
    row = next(i for i in client.get(f"{P}/memories").json()["items"] if i["id"] == mid)
    assert row["source_doc_id"] is None and row["source_doc_title"] == ""
    client.delete(f"{P}/memories/{mid}")


def test_injection_hits_carry_source_doc_title(client):
    """注入路径（recall_for_context）输出携带可溯源的来源文档名。"""
    doc = _create_doc(client, "租金口径指引")
    _create_memory(client, {"content": "租金口径以含税总额除以面积计算。", "source_doc_id": doc["id"]})

    from app.agents import memory as memory_svc

    hits = memory_svc.recall_for_context("租金口径怎么算", top_k=3)
    assert hits, "注入检索未命中"
    assert any(h.get("source_doc_title") == "租金口径指引" for h in hits), (
        f"注入命中缺少可溯源文档名：{hits}"
    )
