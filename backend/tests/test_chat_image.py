"""v1.15.0 聊天传图与多模态理解测试：图片上传、扩展名白名单、非视觉模型 400、image_url 注入。"""
import asyncio
import base64

import pytest

from app.agents import config_store, orchestrator
from app.agents.llm import build_model
from app import config as app_config
from app.routers import assistant as assistant_router

# 一张合法的小 PNG（config_store 内置的纯红测试图）
_PNG_BYTES = base64.b64decode(config_store._red_png_data_uri().split(",", 1)[1])

VISION_ERROR = "当前模型不支持图片理解，请在设置中为该模型开启视觉能力或切换视觉模型"


@pytest.fixture(autouse=True)
def upload_dir(tmp_path, monkeypatch):
    """上传目录改道临时目录：测试不碰 backend/data/uploads，结束自动清理。"""
    target = tmp_path / "uploads"
    target.mkdir()
    monkeypatch.setattr(assistant_router, "UPLOAD_DIR", target)  # 路由层模块级引用
    monkeypatch.setattr(app_config, "UPLOAD_DIR", target)  # 编排层函数内 from ..config 引用
    return target


def _mk_provider(client, name="视觉供应商", base_url="https://x.com/v1", api_key="sk-x-1234"):
    body = client.post(
        "/api/assistant/providers",
        json={"name": name, "protocol": "openai", "base_url": base_url, "api_key": api_key},
    ).json()
    return body["provider"]["id"]


def _upload(client, session_id, filename, content):
    return client.post(
        "/api/assistant/upload",
        data={"session_id": session_id},
        files={"file": (filename, content)},
    )


# ---------------- 上传 ----------------

def test_upload_image_returns_kind_image(client):
    resp = _upload(client, "sess-img-01", "red.png", _PNG_BYTES)
    assert resp.status_code == 200
    body = resp.json()
    assert body["kind"] == "image"
    assert body["name"] == "red.png"
    assert body["size"] == len(_PNG_BYTES)


def test_upload_image_extensions_all_accepted(client):
    for name in ("a.png", "a.jpg", "a.jpeg", "a.webp", "a.gif"):
        resp = _upload(client, "sess-img-02", name, _PNG_BYTES)
        assert resp.status_code == 200, name
        assert resp.json()["kind"] == "image", name


def test_upload_rejects_non_whitelisted_extension(client):
    resp = _upload(client, "sess-img-03", "evil.exe", b"MZ...")
    assert resp.status_code == 400
    assert "不支持的文件类型" in resp.json()["detail"]


def test_upload_text_file_keeps_file_kind(client):
    resp = _upload(client, "sess-img-04", "notes.txt", "你好".encode("utf-8"))
    assert resp.status_code == 200
    assert resp.json()["kind"] == "file"


def test_upload_oversize_image_rejected(client, monkeypatch):
    resp = _upload(client, "sess-img-05", "big.png", b"x" * (10 * 1024 * 1024 + 1))
    assert resp.status_code == 400
    assert "10MB" in resp.json()["detail"]


# ---------------- 非视觉模型带图聊天 → 400 ----------------

def test_chat_with_image_non_vision_model_400(client):
    pid = _mk_provider(client)
    mid = client.post(f"/api/assistant/providers/{pid}/models", json={"model": "text-only"}).json()["model"]["id"]
    client.post(f"/api/assistant/models/{mid}/activate")
    orchestrator.invalidate()

    uploaded = _upload(client, "sess-img-06", "red.png", _PNG_BYTES).json()
    resp = client.post(
        "/api/assistant/chat",
        json={
            "session_id": "sess-img-06",
            "message": "这张图是什么颜色？",
            "attachments": [{"name": uploaded["name"], "path": uploaded["path"]}],
        },
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == VISION_ERROR


def test_chat_stream_with_image_non_vision_model_400(client):
    pid = _mk_provider(client)
    mid = client.post(f"/api/assistant/providers/{pid}/models", json={"model": "text-only"}).json()["model"]["id"]
    client.post(f"/api/assistant/models/{mid}/activate")
    orchestrator.invalidate()

    uploaded = _upload(client, "sess-img-07", "red.png", _PNG_BYTES).json()
    resp = client.post(
        "/api/assistant/chat/stream",
        json={
            "session_id": "sess-img-07",
            "message": "这张图是什么颜色？",
            "attachments": [{"name": uploaded["name"], "path": uploaded["path"]}],
        },
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == VISION_ERROR


def test_vision_check_ignores_non_image_attachments(client):
    pid = _mk_provider(client)
    mid = client.post(f"/api/assistant/providers/{pid}/models", json={"model": "text-only"}).json()["model"]["id"]
    client.post(f"/api/assistant/models/{mid}/activate")
    orchestrator.invalidate()
    # 纯文本附件不触发视觉校验（无异常即通过）
    orchestrator.assert_vision_supported([{"name": "a.txt", "path": "whatever"}], None)
    orchestrator.assert_vision_supported([], None)
    orchestrator.assert_vision_supported(None, None)
    # 图片附件才会抛 ValueError
    try:
        orchestrator.assert_vision_supported([{"name": "a.png", "path": "whatever"}], None)
        raise AssertionError("应当抛出 ValueError")
    except ValueError as exc:
        assert str(exc) == VISION_ERROR


# ---------------- 视觉模型：图片注入为 image_url ----------------

def test_user_content_blocks_inject_image_url(client):
    pid = _mk_provider(client)
    mid = client.post(
        f"/api/assistant/providers/{pid}/models",
        json={"model": "vision-model", "supports_vision": True},
    ).json()["model"]["id"]
    client.post(f"/api/assistant/models/{mid}/activate")
    orchestrator.invalidate()

    uploaded = _upload(client, "sess-img-09", "red.png", _PNG_BYTES).json()
    blocks = orchestrator._user_content_blocks(
        "这张图是什么颜色？",
        [{"name": uploaded["name"], "path": uploaded["path"]}],
        None,
    )
    # 文本块 + 图片 DataBlock（base64）
    assert blocks[0].type == "text" and "这张图是什么颜色" in blocks[0].text
    image_blocks = [b for b in blocks if b.type == "data"]
    assert len(image_blocks) == 1
    src = image_blocks[0].source
    assert src.media_type == "image/png"
    assert base64.b64decode(src.data) == _PNG_BYTES

    # 经 AgentScope OpenAI formatter 转换后，正文含 OpenAI 多模态 image_url 结构
    from agentscope.formatter import OpenAIChatFormatter
    from agentscope.message import Msg

    formatted = asyncio.run(OpenAIChatFormatter().format([Msg(name="用户", role="user", content=blocks)]))
    content = formatted[-1]["content"]
    assert any(
        isinstance(part, dict) and part.get("type") == "image_url"
        and part["image_url"]["url"].startswith("data:image/png;base64,")
        for part in content
    )


def test_user_content_blocks_text_attachment_unchanged(client):
    uploaded = _upload(client, "sess-img-10", "notes.txt", "科目余额表数据".encode("utf-8")).json()
    blocks = orchestrator._user_content_blocks(
        "汇总一下",
        [{"name": uploaded["name"], "path": uploaded["path"]}],
        None,
    )
    assert len(blocks) == 1 and blocks[0].type == "text"
    assert "科目余额表数据" in blocks[0].text
    assert "【附件文件" in blocks[0].text


# ---------------- 模型视觉字段 / 预设 ----------------

def test_provider_model_supports_vision_flag(client):
    pid = _mk_provider(client)
    mid = client.post(
        f"/api/assistant/providers/{pid}/models",
        json={"model": "vision-model", "supports_vision": True},
    ).json()["model"]["id"]
    prov = next(p for p in client.get("/api/assistant/providers").json()["providers"] if p["id"] == pid)
    model = next(m for m in prov["models"] if m["id"] == mid)
    assert model["supports_vision"] is True

    # 更新接口可关闭
    client.post(
        f"/api/assistant/providers/{pid}/models",
        json={"model_id": mid, "model": "vision-model", "supports_vision": False},
    )
    prov = next(p for p in client.get("/api/assistant/providers").json()["providers"] if p["id"] == pid)
    assert next(m for m in prov["models"] if m["id"] == mid)["supports_vision"] is False

    # 默认关闭
    mid2 = client.post(f"/api/assistant/providers/{pid}/models", json={"model": "plain"}).json()["model"]["id"]
    prov = next(p for p in client.get("/api/assistant/providers").json()["providers"] if p["id"] == pid)
    assert next(m for m in prov["models"] if m["id"] == mid2)["supports_vision"] is False


def test_active_model_cfg_carries_supports_vision(client):
    pid = _mk_provider(client)
    mid = client.post(
        f"/api/assistant/providers/{pid}/models",
        json={"model": "vision-model", "supports_vision": True},
    ).json()["model"]["id"]
    client.post(f"/api/assistant/models/{mid}/activate")
    orchestrator.invalidate()
    try:
        assert config_store.load()["supports_vision"] is True
        assert build_model(config_store.load()) is not None
    finally:
        client.delete(f"/api/assistant/providers/{pid}")
        orchestrator.invalidate()


def test_presets_include_vision_models():
    by_name = {p["name"]: p for p in config_store.PRESETS}
    for name in ("openai", "anthropic", "zhipu", "dashscope", "volcengine", "moonshot"):
        assert by_name[name]["vision_models"], name
    # openai 预设含 gpt-4o 系列；dashscope 含 qwen-vl 系列
    assert "gpt-4o" in by_name["openai"]["vision_models"]
    assert any("qwen-vl" in m for m in by_name["dashscope"]["vision_models"])
