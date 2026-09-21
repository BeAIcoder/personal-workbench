"""AI 助手接口：/api/assistant"""
import csv as _csv
import json
import re
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..agents import config_store, orchestrator
from ..agents.llm import llm_status
from ..config import UPLOAD_DIR

router = APIRouter(prefix="/assistant", tags=["AI 助手"])

# 会话 ID 由前端生成（s_ + base36），仅允许字母/数字/下划线/连字符，防止路径穿越
_SESSION_ID_RE = re.compile(r"[A-Za-z0-9_-]{6,64}")


class AttachmentItem(BaseModel):
    name: str = Field(max_length=200)
    path: str = Field(max_length=500)


class ChatRequest(BaseModel):
    session_id: str = Field(min_length=6, max_length=64, description="会话ID，前端生成并保持稳定")
    message: str = Field(min_length=1, max_length=8000, description="用户消息")
    agent: Optional[str] = Field(None, description="指定专家标识；留空自动路由")
    mode: Optional[str] = Field(None, description="工作模式：standard/readonly/deep")
    attachments: Optional[list[AttachmentItem]] = Field(None, description="附件列表（须为上传接口返回的路径）")


class GlobalsPayload(BaseModel):
    """Agent 工作参数（模型配置走供应商/模型接口）。"""

    max_iters: Optional[int] = Field(None, ge=1, le=20)
    enable_memory: Optional[bool] = None
    history_inject: Optional[int] = Field(None, ge=0, le=100)
    enable_skills: Optional[bool] = Field(None, description="启用本地技能池（SKILL.md）")
    skills_dir: Optional[str] = Field(None, max_length=300, description="技能池目录")
    enable_bash: Optional[bool] = Field(None, description="允许 Agent 使用 Bash 执行技能脚本")
    enable_plugins: Optional[bool] = Field(None, description="启用 QwenPaw 工具型插件（兼容层，实验性）")
    plugins_dir: Optional[str] = Field(None, max_length=300, description="QwenPaw 插件目录（data/plugins）")
    reasoning_effort: Optional[str] = Field(None, description="思考深度：''(跟随模型)/off/low/medium/high/max")


class TestRequest(BaseModel):
    config: Optional[dict] = Field(None, description="候选配置（未填则测试当前激活模型）")
    provider_id: Optional[int] = Field(None, description="测试指定供应商（用其第一个启用模型）")
    include_image: bool = Field(False, description="是否同时测试多模态图片识别")
    test_tools: bool = Field(False, description="是否同时测试工具调用能力")


class ProviderPayload(BaseModel):
    provider_id: Optional[int] = Field(None, description="供应商ID：缺省新建")
    name: str = Field(min_length=1, max_length=50)
    protocol: Optional[str] = Field(None, description="openai / anthropic")
    base_url: Optional[str] = None
    api_key: Optional[str] = Field(None, description="空或 ****** 表示保留现值")
    enabled: Optional[bool] = None


class ModelPayload(BaseModel):
    model_id: Optional[int] = Field(None, description="模型ID：缺省新建")
    provider_id: Optional[int] = Field(None, description="供应商ID（路径参数注入）")
    model: str = Field(min_length=1, max_length=120)
    context_size: Optional[int] = Field(None, ge=4096, le=2000000)
    max_tokens: Optional[int] = Field(None, ge=64, le=500000)
    multimodal: Optional[bool] = None
    enabled: Optional[bool] = None


def _config_view() -> dict:
    cfg = config_store.load()
    return {
        "config": config_store.masked(cfg),
        "presets": config_store.PRESETS,
        "providers": config_store.list_providers(),
        "models": config_store.models_flat(),
        "llm": llm_status(cfg),
    }


@router.get("/team", summary="Agent 团队名册、模型状态、配置与工作模式")
def team():
    return orchestrator.team_payload()


@router.get("/config", summary="读取 AI 助手配置（模型状态/供应商/激活模型）")
def get_config():
    return _config_view()


@router.put("/config", summary="保存 Agent 工作参数（工具轮次/记忆管理）")
def save_globals(payload: GlobalsPayload):
    config_store.save_globals(payload.model_dump(exclude_none=True))
    orchestrator.invalidate()
    return _config_view()


@router.post("/config/test", summary="测试模型连通性（可选多模态，仅 openai 协议）")
async def test_config(payload: TestRequest):
    if payload.provider_id:
        try:
            cfg = config_store.provider_test_cfg(payload.provider_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
    else:
        cfg = payload.config or config_store.load()
        if (cfg.get("api_key") or "").strip() == config_store.MASK or not (cfg.get("api_key") or "").strip():
            cfg = {**cfg, "api_key": config_store.current_api_key()}

    text_result = await config_store.test_connection(cfg)
    result = {"text": text_result}
    if text_result.get("ok"):
        if payload.test_tools:
            result["tools"] = await config_store.test_tool_calling(cfg)
        if payload.include_image:
            if cfg.get("protocol") == "anthropic":
                result["image"] = await config_store.test_multimodal_anthropic(cfg)
            else:
                result["image"] = await config_store.test_multimodal(cfg)
    return result


# ---------------- 供应商 / 模型（ZCode 式两级管理） ----------------

@router.get("/providers", summary="供应商列表（含各自模型，api_key 脱敏）")
def get_providers():
    return {"providers": config_store.list_providers(), "presets": config_store.PRESETS}


@router.post("/providers", summary="新增/更新供应商")
def save_provider(payload: ProviderPayload):
    try:
        saved = config_store.save_provider(payload.model_dump(exclude_none=True))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    orchestrator.invalidate()
    return {"provider": saved, "providers": config_store.list_providers()}


@router.delete("/providers/{provider_id}", summary="删除供应商（含其模型）")
def delete_provider(provider_id: int):
    config_store.delete_provider(provider_id)
    orchestrator.invalidate()
    return {"providers": config_store.list_providers()}


@router.post("/providers/{provider_id}/models", summary="新增/更新供应商下的模型")
def save_provider_model(provider_id: int, payload: ModelPayload):
    data = payload.model_dump(exclude_none=True)
    data["provider_id"] = provider_id
    try:
        saved = config_store.save_provider_model(data)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    orchestrator.invalidate()
    return {"model": saved, "providers": config_store.list_providers()}


@router.delete("/providers/{provider_id}/models/{model_id}", summary="删除模型")
def delete_provider_model(provider_id: int, model_id: int):
    config_store.delete_provider_model(model_id)
    orchestrator.invalidate()
    return {"providers": config_store.list_providers()}


@router.post("/models/{model_id}/activate", summary="激活模型（跨供应商，立即生效）")
def activate_model(model_id: int):
    try:
        activated = config_store.activate_model(model_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    orchestrator.invalidate()
    cfg = config_store.load()
    return {
        "activated": activated,
        "config": config_store.masked(cfg),
        "providers": config_store.list_providers(),
        "models": config_store.models_flat(),
        "llm": llm_status(cfg),
    }


@router.get("/models", summary="启用中的模型扁平列表（快速切换用）")
def get_models():
    return {"models": config_store.models_flat()}


@router.get("/skills/discover", summary="扫描技能池目录（SKILL.md 格式）")
async def discover_skills(dir: str = Query(..., max_length=300, alias="dir")):
    from pathlib import Path

    from agentscope.skill import LocalSkillLoader

    path = Path(dir)
    if not path.is_dir():
        return {"count": 0, "names": [], "error": "目录不存在"}
    loader = LocalSkillLoader(directory=str(path), scan_subdir=True)
    skills = await loader.list_skills()
    return {"count": len(skills), "names": [getattr(s, "name", "?") for s in skills][:30]}


@router.get("/plugins/discover", summary="扫描 QwenPaw 插件目录（工具型插件兼容层）")
def discover_plugins(dir: str = Query(..., max_length=300, alias="dir")):
    from pathlib import Path

    from ..agents.qwenpaw_compat import load_qwenpaw_plugin

    path = Path(dir)
    if not path.is_dir():
        return {"loadable": 0, "plugins": [], "error": "目录不存在"}
    results, loadable = [], 0
    for manifest in sorted(path.glob("*/plugin.json")):
        try:
            info = load_qwenpaw_plugin(str(manifest.parent))
            results.append({"name": info["name"], "ok": True, "tools": [t["name"] for t in info["tools"]]})
            loadable += 1
        except Exception as exc:
            results.append({"name": manifest.parent.name, "ok": False, "error": f"{type(exc).__name__}: {exc}"[:60]})
    return {"loadable": loadable, "plugins": results[:30]}


# ---------------- 聊天附件 ----------------

@router.post("/upload", summary="上传聊天附件（xlsx 自动转 CSV；上限 10MB）")
async def upload_attachment(
    session_id: str = Form(...),
    file: UploadFile = File(...),
):
    if not _SESSION_ID_RE.fullmatch(session_id):
        raise HTTPException(status_code=400, detail="非法的 session_id")
    safe_name = Path(file.filename or "file.bin").name
    dest_dir = UPLOAD_DIR / session_id
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / safe_name

    content = await file.read()
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="文件超过 10MB 上限")
    dest.write_bytes(content)

    kind, note = "file", ""
    suffix = dest.suffix.lower()
    if suffix in (".xlsx", ".xls"):
        try:
            import openpyxl

            wb = openpyxl.load_workbook(dest, read_only=True, data_only=True)
            ws = wb.active
            csv_path = dest.with_suffix(".csv")
            with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
                writer = _csv.writer(f)
                for row in ws.iter_rows(values_only=True):
                    writer.writerow(["" if c is None else c for c in row])
            dest = csv_path
            kind, note = "csv_converted", f"已转换第一张工作表为 CSV：{dest.name}"
        except Exception as exc:
            note = f"Excel 转 CSV 失败：{exc}"

    return {"name": dest.name, "path": str(dest), "kind": kind, "size": dest.stat().st_size, "note": note}


# ---------------- 会话管理 ----------------

class SessionRequest(BaseModel):
    session_id: str = Field(min_length=6, max_length=64)
    title: Optional[str] = Field(None, max_length=100)


@router.get("/sessions", summary="会话列表（按更新时间倒序）")
def get_sessions(limit: int = Query(50, ge=1, le=200)):
    return {"sessions": orchestrator.sessions_list(limit)}


@router.post("/sessions", summary="新建会话")
def create_session(payload: SessionRequest):
    return orchestrator.session_create(payload.session_id, payload.title or "")


@router.put("/sessions/{session_id}", summary="会话重命名")
def rename_session(session_id: str, payload: SessionRequest):
    return orchestrator.session_rename(session_id, payload.title or "")


@router.delete("/sessions/{session_id}", summary="删除会话（含全部消息）")
def delete_session(session_id: str):
    orchestrator.session_delete(session_id)
    return {"ok": True}


# ---------------- 对话 ----------------

@router.post("/chat", summary="与 Agent 团队对话（非流式）")
async def chat(payload: ChatRequest):
    try:
        atts = [a.model_dump() for a in (payload.attachments or [])]
        return await orchestrator.chat(
            payload.session_id, payload.message, payload.agent, payload.mode or "standard", atts
        )
    except Exception as exc:  # 模型网络/鉴权等运行时错误，回传给对话界面渲染
        return {"ok": False, "message": f"Agent 执行失败：{type(exc).__name__}: {exc}"}


@router.post("/chat/stream", summary="与 Agent 团队对话（SSE 流式）")
async def chat_stream(payload: ChatRequest):
    atts = [a.model_dump() for a in (payload.attachments or [])]

    async def sse():
        try:
            async for event in orchestrator.chat_stream(
                payload.session_id,
                payload.message,
                payload.agent,
                payload.mode or "standard",
                atts,
            ):
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        except Exception as exc:
            yield f"data: {json.dumps({'type': 'error', 'message': f'{type(exc).__name__}: {exc}'}, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        sse(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


@router.get("/history/{session_id}", summary="会话历史（时间正序）")
def history(session_id: str, limit: int = Query(50, ge=1, le=200)):
    return {"session_id": session_id, "messages": orchestrator.history(session_id, limit)}
