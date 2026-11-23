"""T2A 知识库安全/可靠性聚焦测试：KB-01、RAG-01、KB-02。

覆盖三个已确认缺陷：
- KB-01：自定义 embedding 地址必须配该地址专用 Key，绝不回退聊天模型 Key；
  所有 embedding 地址复用共享 URL 安全校验，非法地址零外呼。
- RAG-01：`rag.rebuild_index()` 在锁内回调 `stats()`/`_ensure_index()` 会重入死锁，
  重建期间有笔记写入时必须及时返回。
- KB-02：embedding 维度变更后必须有真实可用的向量重建入口；探测失败时不得
  破坏旧向量表。

全部使用临时 SQLite（conftest 已隔离 DATABASE_URL/上传目录）与合成数据，
不触达真实 backend/data、真实外部 HTTP 或真实 Key。
"""
from __future__ import annotations

import threading

import pytest
from sqlalchemy import text

from app.agents import rag
from app.agents.secrets_box import seal
from app.database import Base, SessionLocal, engine
from app.knowledge import embedding as embedding_svc
from app.knowledge import index as index_svc
from app.knowledge import retrieval, service
from app.models import AgentSettings, KbChunk, KbDoc, KbSettings, ModelProvider, Note, ProviderModel

P = "/api/knowledge"
CHAT_KEY = "***REDACTED***"
DEDICATED_KEY = "sk-embedding-dedicated"
CHAT_BASE = "https://chat.example.invalid/v1"
CUSTOM_BASE = "https://embed.example.invalid/v1"

POLICY = """# 差旅管理制度

## 第三章 差旅管理

### 3.2 住宿标准
一线城市住宿标准为每人每晚 600 元，二线城市 450 元，凭发票据实报销。

## 第四章 报销流程
员工应在出差结束后 15 个工作日内提交报销单。
"""


def _reset_kb_virtual_tables() -> None:
    with engine.begin() as conn:
        conn.exec_driver_sql("DROP TABLE IF EXISTS kb_fts")
        conn.exec_driver_sql("DROP TABLE IF EXISTS kb_vec")
    index_svc.ensure_schema()


def _purge_provider_rows() -> None:
    """只清理本文件创建的供应商/模型，避免影响同 worker 的其它用例。"""
    with SessionLocal() as db:
        ids = [row.id for row in db.query(ModelProvider).filter(ModelProvider.name == "T2A测试供应商").all()]
        if not ids:
            return
        model_ids = [row.id for row in db.query(ProviderModel).filter(ProviderModel.provider_id.in_(ids)).all()]
        row = db.get(AgentSettings, 1)
        if row is not None and row.active_model_id in model_ids:
            row.active_model_id = None
        db.query(ProviderModel).filter(ProviderModel.provider_id.in_(ids)).delete(synchronize_session=False)
        db.query(ModelProvider).filter(ModelProvider.id.in_(ids)).delete(synchronize_session=False)
        db.commit()


def _seed_chat_provider(base_url: str = CHAT_BASE, key: str = CHAT_KEY) -> int:
    """种一个激活的聊天供应商（带真实形态的密封 Key）。"""
    with SessionLocal() as db:
        prov = ModelProvider(name="T2A测试供应商", protocol="openai", base_url=base_url, api_key=seal(key))
        db.add(prov)
        db.flush()
        pm = ProviderModel(provider_id=prov.id, model="chat-model", context_size=8000, max_tokens=1024)
        db.add(pm)
        db.flush()
        row = db.get(AgentSettings, 1)
        if row is None:
            row = AgentSettings(id=1)
            db.add(row)
        row.active_model_id = pm.id
        db.commit()
        return pm.id


def _set_embedding_settings(
    db,
    *,
    enabled: bool = True,
    model: str = "fake-embed",
    base_url: str = "",
    api_key: str = "",
    dim: int = 0,
) -> KbSettings:
    row = embedding_svc.get_settings(db)
    row.embedding_enabled = enabled
    row.embedding_model = model
    row.embedding_base_url = base_url
    row.embedding_api_key = api_key
    row.embedding_dim = dim
    db.commit()
    db.refresh(row)
    return row


@pytest.fixture(autouse=True)
def _kb_case(request):
    """非 client 用例自建表并清空知识库；每次替换 rag 的锁与索引单例。"""
    rag._index = None
    rag._index_lock = threading.Lock()
    if "client" in request.fixturenames:
        try:
            yield
        finally:
            rag._index = None
            rag._index_lock = threading.Lock()
        return

    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        db.query(Note).delete()
        db.query(KbChunk).delete()
        db.query(KbDoc).delete()
        db.query(KbSettings).delete()
        db.commit()
    _reset_kb_virtual_tables()
    try:
        yield
    finally:
        rag._index = None
        rag._index_lock = threading.Lock()
        _purge_provider_rows()
        with SessionLocal() as db:
            db.query(Note).delete()
            db.query(KbChunk).delete()
            db.query(KbDoc).delete()
            db.query(KbSettings).delete()
            db.commit()
        _reset_kb_virtual_tables()


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        yield c


class _OutboundGuard:
    """httpx 客户端替身：任何真实外呼都立即失败（证明零外呼）。"""

    def __init__(self, *args, **kwargs):
        raise AssertionError("非法/未授权地址不得发起任何外呼")


# ---------------- KB-01：专用 Key 与 URL 安全校验 ----------------


def test_custom_embedding_url_without_dedicated_key_is_disabled():
    """只给自定义地址、没有专用 Key 时禁用向量，绝不拿聊天 Key 顶上。"""
    _seed_chat_provider()
    with SessionLocal() as db:
        _set_embedding_settings(db, base_url=CUSTOM_BASE)
        cfg, reason = embedding_svc.resolve_config_with_reason(db)
        assert cfg is None
        assert "Key" in reason or "密钥" in reason
        assert embedding_svc.resolve_config(db) is None


def test_custom_embedding_url_uses_dedicated_key_not_chat_key():
    _seed_chat_provider()
    with SessionLocal() as db:
        _set_embedding_settings(db, base_url=CUSTOM_BASE, api_key=seal(DEDICATED_KEY))
        cfg = embedding_svc.resolve_config(db)
        assert cfg is not None
        assert cfg["base_url"] == CUSTOM_BASE
        assert cfg["api_key"] == DEDICATED_KEY
        assert cfg["api_key"] != CHAT_KEY


def test_changing_embedding_url_requires_key_for_new_address():
    """已保存的专用 Key 只属于它配对的地址，换地址不能自动沿用。"""
    _seed_chat_provider()
    with SessionLocal() as db:
        _set_embedding_settings(db, base_url=CUSTOM_BASE, api_key=seal(DEDICATED_KEY))
        cfg, reason = embedding_svc.resolve_config_with_reason(
            db, overrides={"base_url": "https://other.example.invalid/v1"}
        )
        assert cfg is None
        assert "Key" in reason or "密钥" in reason
        # 同一地址仍然可用
        same = embedding_svc.resolve_config(db, overrides={"base_url": CUSTOM_BASE})
        assert same is not None and same["api_key"] == DEDICATED_KEY


def test_following_active_provider_still_reuses_active_key():
    """没有自定义地址时，沿用激活供应商的 base_url/api_key 是既有正确行为。"""
    _seed_chat_provider()
    with SessionLocal() as db:
        _set_embedding_settings(db, base_url="")
        cfg = embedding_svc.resolve_config(db)
        assert cfg is not None
        assert cfg["base_url"] == CHAT_BASE
        assert cfg["api_key"] == CHAT_KEY


@pytest.mark.parametrize(
    "bad_url",
    [
        "file:///etc/passwd",
        "ftp://embed.example.invalid/v1",
        "https://user:pass@embed.example.invalid/v1",
        "https://[::1/v1",
    ],
)
def test_invalid_embedding_url_is_rejected_with_zero_outbound(monkeypatch, bad_url):
    monkeypatch.setattr(embedding_svc.httpx, "Client", _OutboundGuard)
    monkeypatch.setattr(embedding_svc.httpx, "AsyncClient", _OutboundGuard)
    cfg = {
        "base_url": bad_url,
        "api_key": CHAT_KEY,
        "model": "fake-embed",
        "dim": 0,
        "protocol": "openai",
    }
    with pytest.raises(embedding_svc.EmbeddingError) as sync_exc:
        embedding_svc.embed_texts_sync(["制度文本"], cfg)
    assert "地址" in str(sync_exc.value)


def test_resolve_rejects_invalid_saved_url(monkeypatch):
    monkeypatch.setattr(embedding_svc.httpx, "Client", _OutboundGuard)
    with SessionLocal() as db:
        _set_embedding_settings(db, base_url="https://user:pass@embed.example.invalid/v1", api_key=seal(DEDICATED_KEY))
        cfg, reason = embedding_svc.resolve_config_with_reason(db)
        assert cfg is None
        assert "地址" in reason


def test_settings_test_rejects_custom_url_without_key_with_zero_outbound(client, monkeypatch):
    _seed_chat_provider()
    calls: list[dict] = []

    def _record(texts, cfg):
        calls.append(dict(cfg))
        return [[1.0, 0.0, 0.0, 0.0] for _ in texts], 4

    monkeypatch.setattr(embedding_svc, "embed_texts_sync", _record)
    response = client.post(
        f"{P}/settings/test",
        json={"text": "制度检索", "embedding_model": "fake-embed", "embedding_base_url": CUSTOM_BASE},
    )
    assert response.status_code == 400, response.text
    assert "Key" in response.json()["detail"] or "密钥" in response.json()["detail"]
    assert calls == [], "未配专用 Key 的自定义地址必须零外呼"


def test_settings_test_accepts_custom_url_with_dedicated_key(client, monkeypatch):
    _seed_chat_provider()
    captured: dict = {}

    def _fake_embed(texts, cfg):
        captured.update(cfg)
        return [[0.1, 0.2, 0.3, 0.4] for _ in texts], 4

    monkeypatch.setattr(embedding_svc, "embed_texts_sync", _fake_embed)
    response = client.post(
        f"{P}/settings/test",
        json={
            "text": "制度检索",
            "embedding_model": "fake-embed",
            "embedding_base_url": CUSTOM_BASE,
            "embedding_api_key": DEDICATED_KEY,
        },
    )
    assert response.status_code == 200, response.text
    assert captured["base_url"] == CUSTOM_BASE
    assert captured["api_key"] == DEDICATED_KEY
    assert captured["api_key"] != CHAT_KEY


def test_saving_new_embedding_url_clears_stale_key():
    with SessionLocal() as db:
        _set_embedding_settings(db, base_url=CUSTOM_BASE, api_key=seal(DEDICATED_KEY))
        saved = embedding_svc.save_settings(
            db, {"embedding_base_url": "https://other.example.invalid/v1"}
        )
        assert embedding_svc.public_settings(saved)["has_api_key"] is False
        cfg, reason = embedding_svc.resolve_config_with_reason(db)
        assert cfg is None and ("Key" in reason or "密钥" in reason)

        # 同一地址重复保存（前端每次都会回传地址）不清空已保存 Key
        _set_embedding_settings(db, base_url=CUSTOM_BASE, api_key=seal(DEDICATED_KEY))
        saved_same = embedding_svc.save_settings(db, {"embedding_base_url": CUSTOM_BASE})
        assert embedding_svc.public_settings(saved_same)["has_api_key"] is True


# ---------------- RAG-01：重建期间写入不死锁 ----------------


def test_rebuild_index_returns_stats():
    with SessionLocal() as db:
        db.add(Note(title="差旅制度", content="一线城市住宿标准 600 元。", tags=[]))
        db.commit()
    result = rag.rebuild_index()
    assert result["notes_indexed"] == 1
    assert result["chunks"] >= 1


def test_rebuild_index_survives_concurrent_write_without_deadlock(monkeypatch):
    """重建过程中有笔记写入（stamp/count 变化）时，旧实现会在不可重入锁上自锁。"""
    with SessionLocal() as db:
        db.add(Note(title="初始笔记", content="差旅住宿标准。", tags=[]))
        db.commit()
    rag.rebuild_index()

    original = rag._build_index
    written = {"done": False}

    def _build_then_write(db):
        built = original(db)
        with SessionLocal() as writer:
            writer.add(Note(title="并发写入", content="免租期方案与租金阶梯。", tags=[]))
            writer.commit()
        written["done"] = True
        return built

    monkeypatch.setattr(rag, "_build_index", _build_then_write)

    finished = threading.Event()
    outcome: dict = {}

    def _run():
        try:
            outcome.update(rag.rebuild_index())
        except BaseException as exc:  # pragma: no cover - 旧实现死锁时不会走到这里
            outcome["error"] = exc
        finally:
            finished.set()

    worker = threading.Thread(target=_run, daemon=True)
    worker.start()
    try:
        assert finished.wait(10), "rebuild_index 在重建期间有写入时死锁"
        assert "error" not in outcome
        assert written["done"] is True
    finally:
        if worker.is_alive():
            # 旧实现会永久持有原锁；替换模块级锁，避免污染后续用例
            rag._index_lock = threading.Lock()
            rag._index = None

    # 并发写入的笔记在下一次检索时被自动纳入索引
    hits = rag.search("免租期方案", top_k=5)
    assert hits and hits[0]["title"] == "并发写入"


# ---------------- KB-02：维度变更后的真实向量重建 ----------------


def _vectors(dim: int, count: int) -> list[list[float]]:
    base = [1.0] + [0.0] * (dim - 1)
    return [list(base) for _ in range(count)]


def _fake_embed(dim: int):
    def _fn(texts, cfg):
        return _vectors(dim, len(texts)), dim

    return _fn


def _seed_indexed_doc(dim: int = 4, dim_setting: int = 4) -> int:
    _seed_chat_provider()
    with SessionLocal() as db:
        _set_embedding_settings(db, dim=dim_setting)
        doc = service.ingest_text(db, title="差旅管理制度", content=POLICY, meta={"version": "v1.0"})
        rows = db.query(KbChunk).filter(KbChunk.doc_id == doc.id).count()
        assert rows >= 2
        assert index_svc.index_vectors(db, doc.id, _vectors(dim, rows))
        assert index_svc.vector_dim(db) == dim
        return doc.id


def test_vector_rebuild_succeeds_after_dimension_change(monkeypatch):
    doc_id = _seed_indexed_doc(dim=4, dim_setting=4)
    monkeypatch.setattr(embedding_svc, "embed_texts_sync", _fake_embed(3))

    result = service.rebuild_vectors()

    assert result["done"] == 1, result
    assert result["failed"] == 0, result
    assert result["dim"] == 3
    with SessionLocal() as db:
        assert index_svc.vector_dim(db) == 3
        assert index_svc.stats(db)["vector_dim"] == 3
        assert embedding_svc.get_settings(db).embedding_dim == 3
        assert db.get(KbDoc, doc_id).index_status == "ok"

    # 新维度的向量检索真实可用（纯向量命中：查询与正文无共同 bigram）
    hits = retrieval.search_items("量子引力波探测器", top_k=5, scope="docs")
    assert hits and hits[0]["doc"]["id"] == doc_id


def test_vector_rebuild_probe_failure_keeps_existing_vectors(monkeypatch):
    doc_id = _seed_indexed_doc(dim=4, dim_setting=4)

    def _boom(texts, cfg):
        raise embedding_svc.EmbeddingError("embedding 接口返回 401：鉴权失败")

    monkeypatch.setattr(embedding_svc, "embed_texts_sync", _boom)
    result = service.rebuild_vectors()

    assert result["done"] == 0
    assert result["failed"] >= 1
    assert result["error"]
    with SessionLocal() as db:
        assert index_svc.vector_dim(db) == 4, "探测失败不得删除旧向量表"

    monkeypatch.setattr(embedding_svc, "embed_texts_sync", _fake_embed(4))
    hits = retrieval.search_items("量子引力波探测器", top_k=5, scope="docs")
    assert hits and hits[0]["doc"]["id"] == doc_id


def test_rag_rebuild_endpoint_changes_vector_dimension(client, monkeypatch):
    _seed_chat_provider()
    with SessionLocal() as db:
        _set_embedding_settings(db, dim=4)
        doc = service.ingest_text(db, title="差旅管理制度", content=POLICY, meta={"version": "v1.0"})
        rows = db.query(KbChunk).filter(KbChunk.doc_id == doc.id).count()
        assert index_svc.index_vectors(db, doc.id, _vectors(4, rows))
        doc_id = doc.id

    monkeypatch.setattr(embedding_svc, "embed_texts_sync", _fake_embed(3))
    response = client.post(f"{P}/rag/rebuild", params={"with_vectors": "true"})
    assert response.status_code == 200, response.text
    vectors = response.json()["vectors"]
    assert vectors["dim"] == 3
    assert vectors["done"] == 1

    stats = client.get(f"{P}/rag/stats").json()
    assert stats["documents"]["vector_dim"] == 3
    assert client.get(f"{P}/settings").json()["embedding_dim"] == 3
    hits = client.post(f"{P}/rag/search", json={"query": "量子引力波探测器", "scope": "docs"}).json()["items"]
    assert hits and hits[0]["doc"]["id"] == doc_id


def test_rag_rebuild_endpoint_reports_probe_failure(client, monkeypatch):
    _seed_chat_provider()
    with SessionLocal() as db:
        _set_embedding_settings(db, dim=4)
        doc = service.ingest_text(db, title="差旅管理制度", content=POLICY, meta={"version": "v1.0"})
        rows = db.query(KbChunk).filter(KbChunk.doc_id == doc.id).count()
        assert index_svc.index_vectors(db, doc.id, _vectors(4, rows))

    def _boom(texts, cfg):
        raise embedding_svc.EmbeddingError("embedding 接口返回 401：鉴权失败")

    monkeypatch.setattr(embedding_svc, "embed_texts_sync", _boom)
    response = client.post(f"{P}/rag/rebuild", params={"with_vectors": "true"})
    assert response.status_code == 400, response.text
    assert "向量重建失败" in response.json()["detail"]
    with SessionLocal() as db:
        assert index_svc.vector_dim(db) == 4


# ---------------- Fix round：真实 embedding 路径 / 数据保留 / Key 绑定 ----------------


class _FakeEmbedResponse:
    def __init__(self, dim: int, count: int):
        self.status_code = 200
        self.text = ""
        self._dim = dim
        self._count = count

    def json(self):
        return {
            "data": [
                {"index": index, "embedding": [1.0] + [0.0] * (self._dim - 1)}
                for index in range(self._count)
            ]
        }


class _FakeEmbedClient:
    """只替换 httpx 传输层，保留真实 embed_texts_sync 的维度/解析/归一化逻辑。"""

    dim = 3
    calls: list[dict] = []

    @classmethod
    def reset(cls, dim: int) -> None:
        cls.dim = dim
        cls.calls = []

    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def post(self, url, headers=None, json=None):
        inputs = list((json or {}).get("input") or [])
        type(self).calls.append({"url": url, "headers": dict(headers or {})})
        return _FakeEmbedResponse(type(self).dim, len(inputs))


def _seed_many_docs(count: int, dim: int = 4) -> list[int]:
    with SessionLocal() as db:
        ids: list[int] = []
        for index in range(count):
            doc = KbDoc(title=f"批量制度{index}", doc_key=f"批量制度{index}", index_status="embedding")
            db.add(doc)
            db.flush()
            db.add(KbChunk(doc_id=doc.id, seq=0, heading="", text=f"第 {index} 篇 差旅住宿标准 600 元。"))
            ids.append(doc.id)
        db.commit()
        for doc_id in ids:
            assert index_svc.index_vectors(db, doc_id, _vectors(dim, 1))
        return ids


def test_real_embedding_rebuild_survives_dimension_change(monkeypatch):
    """C1：真实 embed_texts_sync + 假 httpx，旧维度 4 保存值不得挡住 4→3 重建。"""
    _seed_chat_provider()
    with SessionLocal() as db:
        _set_embedding_settings(
            db, base_url=CUSTOM_BASE, api_key=seal(DEDICATED_KEY), dim=4
        )
        doc = service.ingest_text(db, title="差旅管理制度", content=POLICY, meta={"version": "v1.0"})
        rows = db.query(KbChunk).filter(KbChunk.doc_id == doc.id).count()
        assert index_svc.index_vectors(db, doc.id, _vectors(4, rows))
        doc_id = doc.id

    _FakeEmbedClient.reset(dim=3)
    monkeypatch.setattr(embedding_svc.httpx, "Client", _FakeEmbedClient)

    result = service.rebuild_vectors()

    assert result["done"] == 1, result
    assert result["dim"] == 3, result
    assert not result.get("error")
    assert _FakeEmbedClient.calls, "真实 embed_texts_sync 应经假 httpx 发出请求"
    assert _FakeEmbedClient.calls[0]["headers"]["Authorization"] == f"Bearer {DEDICATED_KEY}"
    with SessionLocal() as db:
        assert index_svc.vector_dim(db) == 3
        assert embedding_svc.get_settings(db).embedding_dim == 3
        assert db.get(KbDoc, doc_id).index_status == "ok"


def test_settings_test_real_embedding_ignores_saved_dim(client, monkeypatch):
    """C1：连接测试探测模型真实维度，不被表单/已保存的旧维度挡住。"""
    _seed_chat_provider()
    with SessionLocal() as db:
        _set_embedding_settings(
            db, base_url=CUSTOM_BASE, api_key=seal(DEDICATED_KEY), dim=4
        )

    _FakeEmbedClient.reset(dim=3)
    monkeypatch.setattr(embedding_svc.httpx, "Client", _FakeEmbedClient)
    response = client.post(
        f"{P}/settings/test",
        json={
            "text": "制度检索",
            "embedding_model": "fake-embed",
            "embedding_base_url": CUSTOM_BASE,
            "embedding_dim": 4,
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["dim"] == 3
    assert _FakeEmbedClient.calls[0]["headers"]["Authorization"] == f"Bearer {DEDICATED_KEY}"


def test_rag_rebuild_endpoint_real_embedding_dimension_change(client, monkeypatch):
    """C1：点击「重建向量索引」的真实 HTTP 路径也必须跨过旧维度。"""
    _seed_chat_provider()
    with SessionLocal() as db:
        _set_embedding_settings(
            db, base_url=CUSTOM_BASE, api_key=seal(DEDICATED_KEY), dim=4
        )
        doc = service.ingest_text(db, title="差旅管理制度", content=POLICY, meta={"version": "v1.0"})
        rows = db.query(KbChunk).filter(KbChunk.doc_id == doc.id).count()
        assert index_svc.index_vectors(db, doc.id, _vectors(4, rows))

    _FakeEmbedClient.reset(dim=3)
    monkeypatch.setattr(embedding_svc.httpx, "Client", _FakeEmbedClient)
    response = client.post(f"{P}/rag/rebuild", params={"with_vectors": "true"})

    assert response.status_code == 200, response.text
    vectors = response.json()["vectors"]
    assert vectors["dim"] == 3 and vectors["done"] == 1 and vectors["remaining"] == 0
    stats = client.get(f"{P}/rag/stats").json()
    assert stats["documents"]["vector_dim"] == 3


def test_rebuild_same_dim_write_failure_keeps_existing_vectors(monkeypatch):
    """I1：同维写入失败时不得 reset 仍有效的旧向量。"""
    doc_id = _seed_indexed_doc(dim=4, dim_setting=4)
    resets: list[int] = []
    monkeypatch.setattr(index_svc, "reset_vectors", lambda db: resets.append(1))
    monkeypatch.setattr(index_svc, "index_vectors", lambda db, doc_id, vectors: False)
    monkeypatch.setattr(embedding_svc, "embed_texts_sync", _fake_embed(4))

    result = service.rebuild_vectors()

    assert result["done"] == 0
    assert result["error"]
    assert resets == [], "同维失败不得删除旧向量表"
    with SessionLocal() as db:
        assert index_svc.vector_dim(db) == 4
        assert db.get(KbDoc, doc_id).index_status == "embedding_failed"

    hits = retrieval.search_items("量子引力波探测器", top_k=5, scope="docs")
    assert hits and hits[0]["doc"]["id"] == doc_id


def test_rebuild_over_200_docs_reindexes_everything(monkeypatch):
    """I2：维度变更后 >200 篇必须全量处理，不能静默丢失第 201 篇向量。"""
    _seed_chat_provider()
    with SessionLocal() as db:
        _set_embedding_settings(db, dim=4)
    ids = _seed_many_docs(201, dim=4)
    monkeypatch.setattr(embedding_svc, "embed_texts_sync", _fake_embed(3))

    result = service.rebuild_vectors()

    assert result["processed"] == 201, result
    assert result["done"] == 201, result
    assert result["remaining"] == 0, result
    with SessionLocal() as db:
        assert index_svc.vector_dim(db) == 3
        assert db.query(KbDoc).filter(KbDoc.index_status != "ok").count() == 0
        with engine.connect() as conn:
            assert conn.execute(text("SELECT count(*) FROM kb_vec")).scalar() == 201
    assert len(ids) == 201


def test_rebuild_limit_marks_remaining_for_resume(monkeypatch):
    """I2：显式批量上限时，未处理文档标 embedding_failed 并返回 remaining，可续跑。"""
    _seed_chat_provider()
    with SessionLocal() as db:
        _set_embedding_settings(db, dim=4)
    _seed_many_docs(201, dim=4)
    monkeypatch.setattr(embedding_svc, "embed_texts_sync", _fake_embed(3))

    first = service.rebuild_vectors(limit=200)
    assert first["processed"] == 200, first
    assert first["remaining"] == 1, first
    with SessionLocal() as db:
        pending = db.query(KbDoc).filter(KbDoc.index_status == "embedding_failed").all()
        assert len(pending) == 1
        assert pending[0].index_error

    second = service.rebuild_vectors()
    assert second["processed"] == 201, second
    assert second["done"] == 201, second
    assert second["remaining"] == 0, second
    with SessionLocal() as db:
        assert db.query(KbDoc).filter(KbDoc.index_status != "ok").count() == 0


def test_key_only_save_is_rejected():
    """I5：专用 Key 必须绑定显式地址，不能以“跟随激活供应商”形式落库。"""
    with SessionLocal() as db:
        _set_embedding_settings(db, base_url="", model="fake-embed")
        with pytest.raises(ValueError) as exc:
            embedding_svc.save_settings(db, {"embedding_api_key": DEDICATED_KEY})
        assert "接口地址" in str(exc.value)
        assert embedding_svc.public_settings(embedding_svc.get_settings(db))["has_api_key"] is False


def test_key_only_save_route_returns_400(client):
    _seed_chat_provider()
    response = client.put(
        f"{P}/settings",
        json={
            "embedding_enabled": True,
            "embedding_model": "fake-embed",
            "embedding_base_url": "",
            "embedding_api_key": DEDICATED_KEY,
        },
    )
    assert response.status_code == 400, response.text
    assert "接口地址" in response.json()["detail"]
    with SessionLocal() as db:
        assert embedding_svc.public_settings(embedding_svc.get_settings(db))["has_api_key"] is False


def test_saving_follow_mode_clears_legacy_unbound_key():
    """I5：地址留空时保存设置会清掉没有配对地址的遗留 Key。"""
    with SessionLocal() as db:
        row = _set_embedding_settings(db, base_url="", model="fake-embed")
        row.embedding_api_key = seal(DEDICATED_KEY)
        db.commit()
        assert embedding_svc.public_settings(row)["has_api_key"] is True

        saved = embedding_svc.save_settings(db, {"ocr_enabled": True})
        assert embedding_svc.public_settings(saved)["has_api_key"] is False


def test_key_only_legacy_key_does_not_follow_active_provider_switch():
    """I5：历史遗留的 key-only 专用 Key 不得随激活供应商切换外发。"""
    _seed_chat_provider(base_url=CHAT_BASE, key=CHAT_KEY)
    with SessionLocal() as db:
        row = _set_embedding_settings(db, base_url="", model="fake-embed")
        row.embedding_api_key = seal(DEDICATED_KEY)  # 模拟升级前遗留数据
        db.commit()
        cfg = embedding_svc.resolve_config(db)
        assert cfg is not None
        assert cfg["base_url"] == CHAT_BASE
        assert cfg["api_key"] == CHAT_KEY, "没有配对地址的专用 Key 不得替代激活供应商 Key"

    _seed_chat_provider(base_url="https://chat2.example.invalid/v1", key="sk-chat-second")
    with SessionLocal() as db:
        cfg = embedding_svc.resolve_config(db)
        assert cfg is not None
        assert cfg["base_url"] == "https://chat2.example.invalid/v1"
        assert cfg["api_key"] == "sk-chat-second"
        assert cfg["api_key"] != DEDICATED_KEY


def test_explicit_embedding_address_keeps_its_key_after_active_switch():
    _seed_chat_provider(base_url=CHAT_BASE, key=CHAT_KEY)
    with SessionLocal() as db:
        _set_embedding_settings(db, base_url=CUSTOM_BASE, api_key=seal(DEDICATED_KEY))
        assert embedding_svc.resolve_config(db)["api_key"] == DEDICATED_KEY

    _seed_chat_provider(base_url="https://chat2.example.invalid/v1", key="sk-chat-second")
    with SessionLocal() as db:
        cfg = embedding_svc.resolve_config(db)
        assert cfg["base_url"] == CUSTOM_BASE
        assert cfg["api_key"] == DEDICATED_KEY

