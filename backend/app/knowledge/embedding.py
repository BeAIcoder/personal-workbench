"""知识库向量化：走 OpenAI 兼容的 /embeddings 接口。

设计取舍：
- 不单独指定接口地址时**复用当前激活的模型供应商**（base_url + api_key），只单独
  指定 embedding 模型名；一旦指定自定义 base_url，**必须配该地址的专用 Key**
  （DPAPI 密封），绝不把聊天模型 Key 发往别的地址。
- 所有 embedding 地址在解析配置与发请求前都过 `config_store.validate_base_url`
  共享校验（仅 http(s)、禁止内嵌凭据/控制字符），失败即禁用且零外呼。
- embedding 是可选增强：未启用、未配置、协议不兼容、网络失败一律抛
  `EmbeddingError`，调用方降级为关键词检索，绝不影响上传与搜索主流程。
- 向量做 L2 归一化后存 vec0；归一化向量的 L2 距离与余弦距离单调等价。
"""
from __future__ import annotations

import math
import urllib.parse

import httpx
from sqlalchemy.orm import Session

from ..agents import config_store
from ..agents.secrets_box import seal, unseal
from ..models import KbSettings

EMBED_BATCH = 16
EMBED_TIMEOUT_SECONDS = 60.0
MAX_DIM = 4096


class EmbeddingError(ValueError):
    """向量化不可用/失败；消息可直接展示给用户。"""


def get_settings(db: Session) -> KbSettings:
    row = db.get(KbSettings, 1)
    if row is None:
        row = KbSettings(id=1)
        db.add(row)
        db.commit()
        db.refresh(row)
    return row


def _normalized_url(url: str) -> str:
    """地址配对用归一化（scheme/host 小写、去尾斜杠）；非法地址原样后缀比较。"""
    raw = str(url or "").strip()
    if not raw:
        return ""
    try:
        parts = urllib.parse.urlsplit(raw)
        hostname = parts.hostname
    except ValueError:
        return raw.rstrip("/")
    if not parts.scheme or not hostname:
        return raw.rstrip("/")
    query = f"?{parts.query}" if parts.query else ""
    return f"{parts.scheme.lower()}://{parts.netloc.lower()}{parts.path.rstrip('/')}{query}"


CUSTOM_URL_KEY_REQUIRED = "自定义向量接口地址必须单独填写该地址的 API Key（不会使用聊天模型 Key）"


def resolve_config_with_reason(
    db: Session, overrides: dict | None = None
) -> tuple[dict | None, str]:
    """解析 embedding 配置；返回 (配置, 不可用原因)。

    Key 只与它显式配对的地址一起使用：显式覆盖 Key > 与该地址配对的专用 Key >
    与该地址相同的激活供应商 Key。自定义地址没有专用 Key 时直接禁用（返回
    None），绝不把聊天模型 Key 发往该地址；地址不合法同样禁用且零外呼。

    专用 Key 必须保存了 `embedding_base_url` 才生效：留空（跟随激活供应商）
    时 Key 没有配对地址，不会随激活供应商切换而外发。
    """
    row = get_settings(db)
    overrides = overrides or {}
    enabled = bool(row.embedding_enabled or overrides)
    model = str(overrides.get("model") or row.embedding_model or "").strip()
    if not enabled or not model:
        return None, "向量检索未启用或未配置 embedding 模型"

    active = config_store.load()
    active_base_raw = str(active.get("base_url") or "").strip()
    saved_base_raw = str(row.embedding_base_url or "").strip()
    override_base_raw = str(overrides.get("base_url") or "").strip()
    base_raw = override_base_raw or saved_base_raw or active_base_raw
    if not base_raw:
        return None, "未配置向量接口地址（留空时需先激活聊天模型供应商）"
    try:
        base_url = config_store.validate_base_url(base_raw)
    except ValueError:
        return None, "向量接口地址不合法：仅支持 http(s)，且不允许内嵌用户名或密码"

    effective = _normalized_url(base_url)
    active_base = _normalized_url(active_base_raw)
    saved_base = _normalized_url(saved_base_raw)

    override_key = str(overrides.get("api_key") or "").strip()
    saved_key = unseal(row.embedding_api_key) if (row.embedding_api_key and saved_base) else ""
    active_key = str(active.get("api_key") or "").strip()
    if override_key:
        api_key = override_key
    elif saved_key and saved_base and effective == saved_base:
        # 专用 Key 只与显式保存的地址配对；留空地址的 Key 没有配对关系，
        # 不得随激活供应商切换而外发（I5）。
        api_key = saved_key
    elif effective == active_base:
        api_key = active_key
    else:
        api_key = ""
    if not api_key and effective != active_base:
        return None, CUSTOM_URL_KEY_REQUIRED

    dim = overrides.get("dim")
    if dim is None:
        dim = row.embedding_dim
    return {
        "base_url": base_url,
        "api_key": api_key,
        "model": model,
        "dim": int(dim or 0),
        # embedding 接口统一按 OpenAI 兼容协议（/embeddings）调用
        "protocol": "openai",
    }, ""


def resolve_config(db: Session, overrides: dict | None = None) -> dict | None:
    """解析 embedding 配置；未启用/缺模型/自定义地址缺专用 Key 时返回 None。"""
    cfg, _reason = resolve_config_with_reason(db, overrides)
    return cfg


def _endpoint(base_url: str) -> str:
    # 与聊天/探测侧共用同一套 http(s) 白名单（拒绝内嵌凭据与控制字符）
    base = config_store.validate_base_url(str(base_url or ""))
    for suffix in ("/chat/completions", "/completions"):
        if base.lower().endswith(suffix):
            base = base[: -len(suffix)]
            break
    if base.lower().endswith("/embeddings"):
        return base
    return f"{base}/embeddings"


def _endpoint_or_error(base_url: str) -> str:
    try:
        return _endpoint(base_url)
    except ValueError as exc:
        raise EmbeddingError("向量接口地址不合法：仅支持 http(s)，且不允许内嵌用户名或密码") from exc


def _normalize(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(float(v) * float(v) for v in vector))
    if not norm:
        return [0.0 for _ in vector]
    return [float(v) / norm for v in vector]


def _parse_response(payload: dict) -> tuple[list[list[float]], int]:
    data = payload.get("data")
    if isinstance(data, list):
        ordered = sorted(data, key=lambda item: int(item.get("index") or 0))
        vectors = [list(map(float, item.get("embedding") or [])) for item in ordered]
    elif isinstance(payload.get("embeddings"), list):
        vectors = [list(map(float, item or [])) for item in payload["embeddings"]]
    else:
        raise EmbeddingError("embedding 接口返回结构不可识别")
    if not vectors or any(not vector for vector in vectors):
        raise EmbeddingError("embedding 接口未返回向量")
    dim = len(vectors[0])
    if any(len(vector) != dim for vector in vectors):
        raise EmbeddingError("embedding 接口返回的向量维度不一致")
    if dim > MAX_DIM:
        raise EmbeddingError(f"向量维度 {dim} 超过上限 {MAX_DIM}")
    return [_normalize(vector) for vector in vectors], dim


def save_settings(db: Session, payload: dict) -> KbSettings:
    """保存设置；api_key 传空保留旧值（与供应商密钥同一约定）。

    专用 Key 必须绑定显式接口地址：地址留空（跟随激活供应商）时提交 Key 直接
    拒绝（ValueError→400），避免 Key 没有配对地址、随激活供应商切换外发。
    更换接口地址时旧 Key 自动清空：密钥只属于它保存时配对的地址，避免把旧地址
    的专用 Key 悄悄发往新地址；用户需为新地址重新填写 Key。
    """
    row = get_settings(db)
    new_key = str(payload.get("embedding_api_key") or "").strip()
    new_base: str | None = None
    if "embedding_base_url" in payload and payload["embedding_base_url"] is not None:
        new_base = str(payload["embedding_base_url"]).strip()[:300]
    effective_base = new_base if new_base is not None else str(row.embedding_base_url or "").strip()
    if new_key and not effective_base:
        raise ValueError("填写专用 API Key 时必须同时填写接口地址（留空将跟随激活供应商的 Key）")

    if "ocr_enabled" in payload and payload["ocr_enabled"] is not None:
        row.ocr_enabled = bool(payload["ocr_enabled"])
    if "embedding_enabled" in payload and payload["embedding_enabled"] is not None:
        row.embedding_enabled = bool(payload["embedding_enabled"])
    if "embedding_model" in payload and payload["embedding_model"] is not None:
        row.embedding_model = str(payload["embedding_model"]).strip()[:120]
    if new_base is not None:
        if _normalized_url(new_base) != _normalized_url(row.embedding_base_url or "") and not new_key:
            row.embedding_api_key = ""
        row.embedding_base_url = new_base
    if not str(row.embedding_base_url or "").strip() and not new_key:
        # 跟随激活供应商时专用 Key 没有配对地址：清掉遗留值，避免被误用
        row.embedding_api_key = ""
    if new_key:
        row.embedding_api_key = seal(new_key)
    if "embedding_dim" in payload and payload["embedding_dim"] is not None:
        row.embedding_dim = max(0, int(payload["embedding_dim"]))
    db.commit()
    db.refresh(row)
    return row


def public_settings(row: KbSettings) -> dict:
    from .ocr import available as ocr_available

    return {
        "ocr_enabled": bool(row.ocr_enabled),
        "ocr_available": ocr_available(),
        "embedding_enabled": bool(row.embedding_enabled),
        "embedding_model": row.embedding_model,
        "embedding_dim": int(row.embedding_dim or 0),
        "embedding_base_url": row.embedding_base_url,
        "has_api_key": bool(row.embedding_api_key),
    }


def embed_texts_sync(texts: list[str], cfg: dict) -> tuple[list[list[float]], int]:
    """同步版向量化：Agent 工具在同步调用栈里使用，逻辑与 async 版保持一致。"""
    if str(cfg.get("protocol") or "openai") != "openai":
        raise EmbeddingError("当前模型供应商协议不支持 /embeddings（仅支持 OpenAI 兼容接口）")
    cleaned = [str(text or "").strip() for text in texts]
    cleaned = [text for text in cleaned if text]
    if not cleaned:
        return [], int(cfg.get("dim") or 0)

    headers = {"Content-Type": "application/json"}
    if cfg.get("api_key"):
        headers["Authorization"] = f"Bearer {cfg['api_key']}"
    vectors: list[list[float]] = []
    dim = int(cfg.get("dim") or 0)
    url = _endpoint_or_error(str(cfg.get("base_url") or ""))
    try:
        with httpx.Client(timeout=EMBED_TIMEOUT_SECONDS) as client:
            for start in range(0, len(cleaned), EMBED_BATCH):
                batch = cleaned[start : start + EMBED_BATCH]
                response = client.post(url, headers=headers, json={"model": cfg.get("model"), "input": batch})
                if response.status_code >= 400:
                    detail = response.text[:200].replace("\n", " ")
                    raise EmbeddingError(f"embedding 接口返回 {response.status_code}：{detail}")
                batch_vectors, batch_dim = _parse_response(response.json())
                if len(batch_vectors) != len(batch):
                    raise EmbeddingError("embedding 接口返回条数与请求不一致")
                if dim and batch_dim != dim:
                    raise EmbeddingError(
                        f"向量维度与知识库已有索引不一致（已有 {dim}，本次 {batch_dim}），请重建向量索引"
                    )
                dim = batch_dim
                vectors.extend(batch_vectors)
    except EmbeddingError:
        raise
    except httpx.HTTPError as exc:
        raise EmbeddingError(f"embedding 请求失败：{type(exc).__name__}") from exc
    except ValueError as exc:
        raise EmbeddingError("embedding 接口返回的不是合法 JSON") from exc
    return vectors, dim
