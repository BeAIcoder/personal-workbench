"""Agent 团队运行时可配置存储：模型供应商 → 模型 两级管理（参考 ZCode 配置方式）。

- `model_providers`：一家供应商一张卡片（名称/协议/base_url/api_key/启停）；
- `provider_models`：供应商下挂多个模型（上下文/输出上限/多模态标记/启停）；
- `agent_settings.active_model_id`：当前激活的模型，跨供应商选择；
- `.env` 的 LLM_* 仅在没有任何激活模型时兜底。

连通性/多模态测试与协议无关（经 build_model 分流）。
"""
import asyncio
import base64
import json
import struct
import time
import urllib.error
import urllib.request
import zlib
from datetime import datetime

from sqlalchemy.exc import OperationalError

from ..config import settings
from ..database import SessionLocal
from ..models import AgentSettings, ModelProvider, ProviderModel

# 前端回显密钥时使用的掩码哨兵：界面"未改动"传回该值 → 保留现值
MASK = "********"

# 供应商预设（界面选择后自动填充 base_url 与候选模型；均为 OpenAI 兼容。
# anthropic 协议供应商经 scripts/sync_zcode_models.py 从 ZCode 同步）
PRESETS: list[dict] = [
    {
        "name": "modelscope",
        "label": "ModelScope 推理",
        "protocol": "openai",
        "base_url": "https://api-inference.modelscope.cn/v1",
        "models": ["Qwen/Qwen3.5-122B-A10B", "Qwen/Qwen3-235B-A22B", "deepseek-ai/DeepSeek-V4-Flash-0731"],
        "note": "国内直连，含 DeepSeek/Qwen 全系",
    },
    {
        "name": "siliconflow",
        "label": "硅基流动 SiliconFlow",
        "protocol": "openai",
        "base_url": "https://api.siliconflow.cn/v1",
        "models": ["deepseek-ai/DeepSeek-V3.2", "Qwen/Qwen3.5-122B-A10B", "zai-org/GLM-5.3"],
        "note": "国内直连，DeepSeek 性价比高",
    },
    {
        "name": "deepseek",
        "label": "DeepSeek 官方",
        "protocol": "openai",
        "base_url": "https://api.deepseek.com/v1",
        "models": ["deepseek-chat", "deepseek-reasoner"],
        "note": "官方直连",
    },
    {
        "name": "volcengine",
        "label": "火山方舟 Volcengine",
        "protocol": "openai",
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "models": ["doubao-seed-2-0", "doubao-1-5-pro-32k"],
        "note": "需在火山控制台开通模型并创建推理接入点",
    },
    {
        "name": "dashscope",
        "label": "阿里 DashScope 兼容",
        "protocol": "openai",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "models": ["qwen-plus", "qwen-max", "qwen-vl-plus"],
        "note": "通义千问官方（vl 系列支持图片）",
    },
    {
        "name": "ollama",
        "label": "本地 Ollama",
        "protocol": "openai",
        "base_url": "http://127.0.0.1:11434/v1",
        "models": ["qwen2.5:7b", "llama3.1:8b"],
        "note": "完全本地、免费；需先安装 Ollama 并拉取模型",
    },
    {
        "name": "custom",
        "label": "自定义",
        "protocol": "openai",
        "base_url": "",
        "models": [],
        "note": "任意 OpenAI 兼容服务；anthropic 协议请用协议下拉选择",
    },
]

DEFAULTS: dict = {
    "protocol": "openai",
    "base_url": "",
    "api_key": "",
    "model": "",
    "context_size": 128000,
    "max_tokens": 2048,
    "multimodal": False,
    "max_iters": 5,
    "enable_memory": True,
    "history_inject": 12,
    "enable_skills": False,
    "skills_dir": "",
    "enable_bash": False,
    "enable_plugins": False,
    "plugins_dir": "",
    "reasoning_effort": "",
}

# 模型级字段（供应商/模型两表承载）
MODEL_FIELDS = ("protocol", "base_url", "api_key", "model", "context_size", "max_tokens", "multimodal")


def _row() -> AgentSettings | None:
    with SessionLocal() as db:
        return db.get(AgentSettings, 1)


def _active_model():
    """当前激活的 (provider, provider_model)；无激活或已停用返回 None。"""
    try:
        with SessionLocal() as db:
            row = db.get(AgentSettings, 1)
            if row is None or not row.active_model_id:
                return None
            pm = db.get(ProviderModel, row.active_model_id)
            if pm is None or not pm.enabled:
                return None
            prov = db.get(ModelProvider, pm.provider_id)
            if prov is None or not prov.enabled:
                return None
            return prov, pm
    except OperationalError:
        return None


def load() -> dict:
    """读取当前生效配置。

    优先级：激活模型（供应商+模型）> .env 兜底；
    Agent 工作参数（max_iters/记忆）始终取 agent_settings。
    """
    cfg = {**DEFAULTS}

    try:
        row = _row()
    except OperationalError:
        row = None
    if row is not None:
        cfg.update(
            {
                "max_iters": row.max_iters,
                "enable_memory": row.enable_memory,
                "history_inject": row.history_inject,
                "enable_skills": row.enable_skills,
                "skills_dir": row.skills_dir or "",
                "enable_bash": row.enable_bash,
                "enable_plugins": row.enable_plugins,
                "plugins_dir": row.plugins_dir or "",
                "reasoning_effort": row.reasoning_effort or "",
            }
        )

    active = _active_model()
    if active is not None:
        prov, pm = active
        cfg.update(
            {
                "protocol": prov.protocol,
                "base_url": prov.base_url,
                "api_key": prov.api_key,
                "model": pm.model,
                "context_size": pm.context_size,
                "max_tokens": pm.max_tokens,
                "multimodal": pm.multimodal,
                "provider_name": prov.name,
            }
        )
        return cfg

    # .env 兜底
    cfg.update(
        {
            "protocol": "openai",
            "base_url": settings.llm_base_url or "",
            "api_key": settings.llm_api_key or "",
            "model": settings.llm_model or "",
            "provider_name": "env兜底",
        }
    )
    return cfg


def masked(cfg: dict) -> dict:
    """回传给前端的配置：api_key 脱敏。"""
    out = {**cfg}
    key = (cfg.get("api_key") or "").strip()
    out["api_key"] = ("••••••" + key[-4:]) if key else ""
    out["api_key_set"] = bool(key)
    return out


def save_globals(data: dict) -> None:
    """保存 Agent 工作参数（max_iters / 记忆管理）；模型字段请走供应商/模型接口。"""
    with SessionLocal() as db:
        row = db.get(AgentSettings, 1)
        if row is None:
            row = AgentSettings(id=1)
            db.add(row)
            db.flush()
        if "max_iters" in data:
            row.max_iters = max(1, min(int(data["max_iters"] or 5), 20))
        if "enable_memory" in data:
            row.enable_memory = bool(data["enable_memory"])
        if "history_inject" in data:
            row.history_inject = max(0, min(int(data["history_inject"] or 12), 100))
        if "enable_skills" in data:
            row.enable_skills = bool(data["enable_skills"])
        if "skills_dir" in data:
            row.skills_dir = str(data.get("skills_dir") or "").strip()[:300]
        if "enable_bash" in data:
            row.enable_bash = bool(data["enable_bash"])
        if "enable_plugins" in data:
            row.enable_plugins = bool(data["enable_plugins"])
        if "plugins_dir" in data:
            row.plugins_dir = str(data.get("plugins_dir") or "").strip()[:300]
        if "reasoning_effort" in data:
            v = str(data.get("reasoning_effort") or "").strip().lower()
            row.reasoning_effort = v if v in ("", "off", "low", "medium", "high", "max") else ""
        row.updated_at = datetime.now()
        db.commit()


def current_api_key() -> str:
    """当前生效配置中的 API Key（供测试时回填掩码哨兵）。"""
    return load().get("api_key", "")


def provider_test_cfg(pid: int) -> dict:
    """取某供应商第一个启用模型的测试配置。"""
    for p in list_providers():
        if p["id"] == pid:
            models = [m for m in p["models"] if m["enabled"]]
            if not models:
                raise ValueError(f"供应商「{p['name']}」下没有启用中的模型")
            key = "sk-unknown"
            try:
                with SessionLocal() as db:
                    prov = db.get(ModelProvider, pid)
                    key = prov.api_key if prov else key
            except OperationalError:
                pass
            m = models[0]
            return {
                "protocol": p["protocol"],
                "base_url": p["base_url"],
                "api_key": key,
                "model": m["model"],
                "context_size": m["context_size"],
                "max_tokens": m["max_tokens"],
            }
    raise ValueError(f"供应商 {pid} 不存在")


# ---------------- 供应商 / 模型 CRUD（ZCode 式） ----------------

def list_providers() -> list[dict]:
    try:
        row = _row()
        active_model_id = row.active_model_id if row else None
        with SessionLocal() as db:
            provs = db.query(ModelProvider).order_by(ModelProvider.id.asc()).all()
            models = db.query(ProviderModel).order_by(ProviderModel.id.asc()).all()
    except OperationalError:
        return []

    by_prov: dict[int, list] = {}
    for m in models:
        by_prov.setdefault(m.provider_id, []).append(m)

    return [
        {
            "id": p.id,
            "name": p.name,
            "protocol": p.protocol,
            "base_url": p.base_url,
            "api_key_set": bool(p.api_key),
            "enabled": p.enabled,
            "models": [
                {
                    "id": m.id,
                    "model": m.model,
                    "context_size": m.context_size,
                    "max_tokens": m.max_tokens,
                    "multimodal": m.multimodal,
                    "enabled": m.enabled,
                    "active": m.id == active_model_id,
                }
                for m in by_prov.get(p.id, [])
            ],
        }
        for p in provs
    ]


def models_flat() -> list[dict]:
    """所有启用中的（供应商, 模型）扁平列表，供快速切换下拉。"""
    out = []
    for p in list_providers():
        if not p["enabled"]:
            continue
        for m in p["models"]:
            if m["enabled"]:
                out.append(
                    {
                        "id": m["id"],
                        "label": f"{p['name']} · {m['model']}",
                        "provider": p["name"],
                        "model": m["model"],
                        "multimodal": m["multimodal"],
                        "active": m["active"],
                    }
                )
    return out


def save_provider(data: dict) -> dict:
    """新增/更新供应商；api_key 传空或掩码保留现值。"""
    pid = data.get("provider_id")
    name = str(data.get("name") or "").strip()[:50] or "未命名供应商"
    with SessionLocal() as db:
        prov = db.get(ModelProvider, int(pid)) if pid else None
        if prov is None:
            prov = ModelProvider(name=name)
            db.add(prov)
            db.flush()
        else:
            prov.name = name
        prov.protocol = str(data.get("protocol") or prov.protocol or "openai")[:20]
        if data.get("base_url") is not None:
            prov.base_url = str(data["base_url"]).strip().rstrip("/")[:300]
        key_in = str(data.get("api_key") or "").strip()
        if key_in and key_in != MASK:
            prov.api_key = key_in[:300]
        if data.get("enabled") is not None:
            prov.enabled = bool(data["enabled"])
        prov.updated_at = datetime.now()
        db.commit()
        return {"id": prov.id, "name": prov.name, "protocol": prov.protocol}


def delete_provider(pid: int) -> None:
    with SessionLocal() as db:
        prov = db.get(ModelProvider, int(pid))
        if prov is not None:
            db.delete(prov)
        row = db.get(AgentSettings, 1)
        if row is not None and row.active_model_id:
            pm = db.get(ProviderModel, row.active_model_id)
            if pm is not None and pm.provider_id == int(pid):
                row.active_model_id = None
        db.commit()


def save_provider_model(data: dict) -> dict:
    """新增/更新供应商下的模型。"""
    provider_id = int(data["provider_id"])
    model = str(data.get("model") or "").strip()[:120]
    if not model:
        raise ValueError("模型名不能为空")
    mid = data.get("model_id")
    with SessionLocal() as db:
        prov = db.get(ModelProvider, provider_id)
        if prov is None:
            raise ValueError(f"供应商 {provider_id} 不存在")
        pm = db.get(ProviderModel, int(mid)) if mid else None
        if pm is None:
            pm = ProviderModel(provider_id=provider_id, model=model)
            db.add(pm)
            db.flush()
        else:
            pm.model = model
        pm.context_size = max(4096, int(data.get("context_size") or pm.context_size or 128000))
        pm.max_tokens = max(64, int(data.get("max_tokens") or pm.max_tokens or 2048))
        if data.get("multimodal") is not None:
            pm.multimodal = bool(data["multimodal"])
        if data.get("enabled") is not None:
            pm.enabled = bool(data["enabled"])
        pm.updated_at = datetime.now()
        db.commit()
        return {"id": pm.id, "model": pm.model}


def delete_provider_model(mid: int) -> None:
    with SessionLocal() as db:
        pm = db.get(ProviderModel, int(mid))
        if pm is not None:
            db.delete(pm)
        row = db.get(AgentSettings, 1)
        if row is not None and row.active_model_id == int(mid):
            row.active_model_id = None
        db.commit()


def activate_model(mid: int) -> dict:
    """激活某个模型（跨供应商），立即生效。"""
    with SessionLocal() as db:
        pm = db.get(ProviderModel, int(mid))
        if pm is None or not pm.enabled:
            raise ValueError(f"模型 {mid} 不存在或未启用")
        prov = db.get(ModelProvider, pm.provider_id)
        if prov is None or not prov.enabled:
            raise ValueError(f"供应商未启用：{pm.provider_id}")
        row = db.get(AgentSettings, 1)
        if row is None:
            row = AgentSettings(id=1)
            db.add(row)
            db.flush()
        row.active_model_id = int(mid)
        db.commit()
        return {"model": pm.model, "provider": prov.name}


# ---------------- 连通性测试 ----------------

def _red_png_data_uri(size: int = 48) -> str:
    """生成一张纯红色 PNG 的 data URI（用于多模态能力测试）。"""
    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)  # 8bit RGB
    raw = b"".join(b"\x00" + b"\xef\x3d\x2f" * size for _ in range(size))  # 红色 #EF3D2F
    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")
    return "data:image/png;base64," + base64.b64encode(png).decode()


async def _consume_model_call(model, messages, tools=None):
    """统一消费模型调用结果：兼容 ChatResponse 与流式 async_generator。

    返回 (全部文本, tool_call 名称列表, usage)。
    """
    if tools:
        resp = await model(messages, tools=tools)
    else:
        resp = await model(messages)

    blocks, usage = [], None
    if hasattr(resp, "__aiter__"):  # 流式：逐 chunk 累积
        async for chunk in resp:
            blocks.extend(chunk.get("content") or [])
            if chunk.get("usage") is not None:
                usage = chunk.get("usage")
    else:
        blocks = resp.get("content") or []
        usage = resp.get("usage")

    texts, tool_calls = [], []
    for b in blocks:
        t = getattr(b, "type", None)
        if t == "text":
            texts.append(getattr(b, "text", ""))
        elif t == "tool_call":
            tool_calls.append(getattr(b, "name", "") or getattr(b, "tool_call_name", ""))
    return "".join(texts), tool_calls, usage


def _extract_reply(raw: str) -> str:
    """从模型原始输出里提取正式回答（剥离思考过程）。"""
    raw = raw.strip()
    for marker in ("Final Output:", "</think>"):
        idx = raw.rfind(marker)
        if idx >= 0:
            raw = raw[idx + len(marker):].strip()
            break
    return raw[:120] or "(空回复)"


async def test_connection(cfg: dict) -> dict:
    """文本连通性测试：构造模型发一条消息，返回延迟与回复（协议无关、兼容流式）。"""
    from ..agents.llm import build_model

    if not (cfg.get("api_key") and cfg.get("base_url") and cfg.get("model")):
        return {"ok": False, "error": "请先填写 Base URL / API Key / 模型名"}
    model = build_model(cfg)
    if model is None:
        return {"ok": False, "error": "配置不完整，无法构造模型"}
    from agentscope.message import Msg, TextBlock

    started = time.time()
    try:
        text, _, usage = await _consume_model_call(
            model, [Msg(name="user", role="user", content=[TextBlock(type="text", text="请只回复：连接成功")])]
        )
        latency = round((time.time() - started) * 1000)
        return {
            "ok": True,
            "latency_ms": latency,
            "reply": _extract_reply(text),
            "usage": {
                "input_tokens": getattr(usage, "input_tokens", None),
                "output_tokens": getattr(usage, "output_tokens", None),
            },
        }
    except Exception as exc:
        return {"ok": False, "latency_ms": round((time.time() - started) * 1000), "error": f"{type(exc).__name__}: {exc}"[:200]}


# 工具调用测试用的极简工具（OpenAI function 格式；AgentScope 两种协议的模型层都接受该格式并自行转换）
_TEST_TOOL_SPEC = {
    "name": "get_current_time",
    "description": "获取当前的日期和时间",
    "parameters": {
        "type": "object",
        "properties": {},
        "required": [],
    },
}


async def test_tool_calling(cfg: dict) -> dict:
    """工具调用能力测试：给模型一个测试工具，验证它是否会发起工具调用。"""
    from ..agents.llm import build_model

    if not (cfg.get("api_key") and cfg.get("base_url") and cfg.get("model")):
        return {"ok": False, "error": "请先填写 Base URL / API Key / 模型名"}
    model = build_model(cfg)
    if model is None:
        return {"ok": False, "error": "配置不完整，无法构造模型"}

    tools = [{"type": "function", "function": _TEST_TOOL_SPEC}]

    from agentscope.message import Msg, TextBlock

    started = time.time()
    try:
        text, tool_calls, _ = await _consume_model_call(
            model,
            [Msg(name="user", role="user", content=[TextBlock(type="text", text="请调用 get_current_time 工具查询当前时间")])],
            tools=tools,
        )
        latency = round((time.time() - started) * 1000)
        ok = any("get_current_time" in (tc or "") for tc in tool_calls)
        return {
            "ok": ok,
            "latency_ms": latency,
            "tool_calls": [tc for tc in tool_calls if tc],
            "reply": _extract_reply(text),
            "error": None if ok else "模型未发起工具调用",
        }
    except Exception as exc:
        return {"ok": False, "latency_ms": round((time.time() - started) * 1000), "error": f"{type(exc).__name__}: {exc}"[:200]}


def _http_anthropic_multimodal_test(cfg: dict) -> dict:
    """anthropic 协议图片识别测试：/v1/messages + base64 图片块。"""
    started = time.time()
    b64 = _red_png_data_uri().split(",", 1)[1]
    base = cfg["base_url"].rstrip("/")
    if base.lower().endswith("/v1"):  # SDK 同款问题：避免 /v1/v1/messages
        base = base[: -len("/v1")]
    payload = {
        "model": cfg["model"],
        "max_tokens": 32,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "这张图片是什么颜色？请只回答颜色名称（如：红色）。"},
                    {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64}},
                ],
            }
        ],
    }
    req = urllib.request.Request(
        base + "/v1/messages",
        data=json.dumps(payload).encode(),
        headers={
            "x-api-key": cfg["api_key"],
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        },
    )
    try:
        resp = json.load(urllib.request.urlopen(req, timeout=60))
        latency = round((time.time() - started) * 1000)
        content = "".join(b.get("text", "") for b in resp.get("content", []) if isinstance(b, dict))
        ok = ("红" in content) or ("red" in content.lower())
        return {"ok": ok, "latency_ms": latency, "reply": content.strip()[:120] or "(空回复)", "supported": True}
    except urllib.error.HTTPError as exc:
        return {"ok": False, "latency_ms": round((time.time() - started) * 1000), "supported": False, "error": f"HTTP {exc.code}"}
    except Exception as exc:
        return {"ok": False, "latency_ms": round((time.time() - started) * 1000), "supported": False, "error": f"{type(exc).__name__}: {exc}"[:160]}


async def test_multimodal_anthropic(cfg: dict) -> dict:
    """anthropic 协议图片识别测试（异步包装同步 HTTP）。"""
    return await asyncio.to_thread(_http_anthropic_multimodal_test, cfg)


def _http_multimodal_test(cfg: dict) -> dict:
    """直接以 OpenAI 兼容协议发送「文字 + 红色图片」，让模型回答颜色（仅 openai 协议）。"""
    started = time.time()
    payload = {
        "model": cfg["model"],
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "这张图片是什么颜色？请只回答颜色名称（如：红色）。"},
                    {"type": "image_url", "image_url": {"url": _red_png_data_uri()}},
                ],
            }
        ],
        "max_tokens": 32,
    }
    req = urllib.request.Request(
        cfg["base_url"].rstrip("/") + "/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Authorization": "Bearer " + cfg["api_key"], "Content-Type": "application/json"},
    )
    try:
        resp = json.load(urllib.request.urlopen(req, timeout=60))
        latency = round((time.time() - started) * 1000)
        content = (resp["choices"][0]["message"].get("content") or "")
        ok = ("红" in content) or ("red" in content.lower())
        return {"ok": ok, "latency_ms": latency, "reply": content.strip()[:120] or "(空回复)", "supported": True}
    except urllib.error.HTTPError as exc:
        latency = round((time.time() - started) * 1000)
        return {"ok": False, "latency_ms": latency, "supported": False, "error": f"HTTP {exc.code}"}
    except Exception as exc:
        latency = round((time.time() - started) * 1000)
        return {"ok": False, "latency_ms": latency, "supported": False, "error": f"{type(exc).__name__}: {exc}"[:160]}


async def test_multimodal(cfg: dict) -> dict:
    """多模态能力测试（异步包装同步 HTTP）。"""
    return await asyncio.to_thread(_http_multimodal_test, cfg)
