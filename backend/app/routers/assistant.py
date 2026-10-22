"""AI 助手接口：/api/assistant"""
import asyncio
import csv as _csv
import json
import logging
import os
import re
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

from ..agents import config_store, orchestrator
from ..agents import team as team_mod
from ..agents.llm import llm_status
from ..config import UPLOAD_DIR
from ..database import SessionLocal
from ..models import AgentSpecModel
from ..schemas import AgentSpecCreate, AgentSpecOut, AgentSpecUpdate
from ..utils import DbId

router = APIRouter(prefix="/assistant", tags=["AI 助手"])

logger = logging.getLogger(__name__)

# 共享上传读取器与导入行数上限由 core 任务提供（backend/app/upload.py、import_limits.py）。
# 缺失时相关端点明确 503 失败关闭：绝不退回无界 file.read()，也不放行无上限转换。
try:
    from ..upload import read_upload_limited
except ImportError as _exc:  # 集成窗口期 core 尚未落地
    read_upload_limited = None
    logger.warning("共享上传模块暂不可用，上传接口将返回 503：%s", _exc)

try:
    from ..import_limits import MAX_IMPORT_ROWS
except ImportError as _exc:
    MAX_IMPORT_ROWS = None
    logger.warning("共享导入行数上限暂不可用，Excel 转换将返回 503：%s", _exc)

# 聊天附件大小上限：路由级限额（全局请求体限额见 ASGI 中间件）
_MAX_UPLOAD_BYTES = 10 * 1024 * 1024

# 会话 ID 由前端生成（s_ + base36），仅允许字母/数字/下划线/连字符，防止路径穿越
_SESSION_ID_RE = re.compile(r"[A-Za-z0-9_-]{6,64}")


def _check_session_id(session_id: str) -> None:
    """session_id 字符集校验：不匹配返回 400（上传/导出/会话管理等同口径）。"""
    if not _SESSION_ID_RE.fullmatch(session_id):
        raise HTTPException(status_code=400, detail="非法的 session_id")


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
    api_key: Optional[str] = Field(None, description="空 / ******** / •••••• 开头的回显掩码 均表示保留现值")
    enabled: Optional[bool] = None


class ModelPayload(BaseModel):
    model_id: Optional[int] = Field(None, description="模型ID：缺省新建")
    provider_id: Optional[int] = Field(None, description="供应商ID（路径参数注入）")
    model: str = Field(min_length=1, max_length=120)
    context_size: Optional[int] = Field(None, ge=4096, le=2000000)
    max_tokens: Optional[int] = Field(None, ge=64, le=500000)
    multimodal: Optional[bool] = None
    # 视觉（图片理解）能力：开启后聊天可传图片（base64 注入多模态消息）
    supports_vision: Optional[bool] = Field(None, description="视觉（图片理解）能力；聊天传图需开启")
    enabled: Optional[bool] = None
    # 采样温度：空串 = 跟随系统默认（TEXT 存储，允许手动填写）
    temperature: Optional[str | float] = Field(None, description="采样温度；空/缺省 = 跟随系统默认")
    note: Optional[str] = Field(None, max_length=500, description="模型备注")
    # 思考能力：-1 未探测 / 0 否 / 1 是（探测接口自动写回，也可手动标记）
    supports_thinking: Optional[int] = Field(None, ge=-1, le=1, description="思考能力：-1 未探测 / 0 否 / 1 是")
    thinking_levels: Optional[list[str]] = Field(None, description="思考档位列表，如 ['low','medium','high']")


def _config_view() -> dict:
    cfg = config_store.load()
    return {
        "config": config_store.masked(cfg),
        # 旧版配置抽屉只自动填充 OpenAI 兼容预设；anthropic 协议预设（如 Anthropic 官方）
        # 经 GET /api/providers/presets 或供应商表单的协议下拉使用
        "presets": [p for p in config_store.PRESETS if p.get("protocol") == "openai"],
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
        if payload.config:
            # 自定义候选配置：空/掩码 key 必须显式提供新值，绝不借用当前激活模型的真实密钥
            # （否则等于把激活密钥发往任意自定义 base_url）；base_url 走共享网关校验。
            # config 为自由 dict：非字符串入参（JSON 数字/列表/字典/布尔）先按字符串口径
            # 归一再走同一套校验——真值视为显式新 key，非法 base_url 400，守卫内绝不 500。
            key_in = str(cfg.get("api_key") or "").strip()
            if not key_in or key_in == config_store.MASK or key_in.startswith(config_store.MASK_PREFIX):
                # 空/掩码 key 明确 400 拒绝：测试自定义配置不会沿用当前激活模型的密钥
                raise HTTPException(status_code=400, detail="请填写新的 API Key：测试自定义配置不会沿用当前激活模型的密钥")
            # 归一化后的显式新 key 回写 cfg：下游各测试（SDK/工具/图片）拿到的与守卫校验过的同一字符串
            cfg = {**cfg, "api_key": key_in}
            base_url = str(cfg.get("base_url") or "").strip()
            if base_url:
                try:
                    config_store._safe_gateway_url(base_url, "/chat/completions")
                except ValueError as exc:
                    raise HTTPException(status_code=400, detail=str(exc))
        elif (cfg.get("api_key") or "").strip() == config_store.MASK or not (cfg.get("api_key") or "").strip():
            # 测试当前激活模型：掩码/空 key 回填真实密钥（既有行为）
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
def delete_provider(provider_id: DbId):
    config_store.delete_provider(provider_id)
    orchestrator.invalidate()
    return {"providers": config_store.list_providers()}


@router.post("/providers/{provider_id}/models", summary="新增/更新供应商下的模型")
def save_provider_model(provider_id: DbId, payload: ModelPayload):
    data = payload.model_dump(exclude_none=True)
    data["provider_id"] = provider_id
    try:
        saved = config_store.save_provider_model(data)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    orchestrator.invalidate()
    return {"model": saved, "providers": config_store.list_providers()}


@router.delete("/providers/{provider_id}/models/{model_id}", summary="删除模型")
def delete_provider_model(provider_id: DbId, model_id: DbId):
    config_store.delete_provider_model(model_id)
    orchestrator.invalidate()
    return {"providers": config_store.list_providers()}


@router.post("/models/{model_id}/activate", summary="激活模型（跨供应商，立即生效）")
def activate_model(model_id: DbId):
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


def _normalize_dir(raw: str) -> str:
    """目录规范化：展开 ~、解析符号链接与相对段、统一大小写/分隔符（Windows 大小写不敏感）。"""
    try:
        return os.path.normcase(str(Path(raw).expanduser().resolve()))
    except OSError:
        return os.path.normcase(str(Path(raw).expanduser().absolute()))


def _is_saved_dir(requested: str, saved: str) -> bool:
    """请求目录必须是配置中显式保存且规范化相等的目录。

    发现接口只允许已保存目录：任意目录不得触发 load_qwenpaw_plugin
    （会执行插件后端代码）；合法的外部保存目录（非仓库内）同样可用。
    """
    requested = (requested or "").strip()
    saved = (saved or "").strip()
    if not requested or not saved:
        return False
    return _normalize_dir(requested) == _normalize_dir(saved)


def _saved_config_dir(key: str) -> str:
    """config_store.load() 中显式保存的目录（skills_dir/plugins_dir）；未保存返回空串。"""
    try:
        return str(config_store.load().get(key) or "").strip()
    except Exception:
        logger.warning("读取已保存目录失败（%s）", key, exc_info=True)
        return ""


@router.get("/skills/discover", summary="扫描技能池目录（SKILL.md 格式；仅限已保存目录）")
async def discover_skills(dir: str = Query(..., max_length=300, alias="dir")):
    from agentscope.skill import LocalSkillLoader

    if not _is_saved_dir(dir, _saved_config_dir("skills_dir")):
        # 安全边界：仅配置中显式保存的目录可扫描，未保存先提示保存
        return {"count": 0, "names": [], "error": "目录未保存：请先在设置中保存该技能池目录再检测"}
    path = Path(dir)
    if not path.is_dir():
        return {"count": 0, "names": [], "error": "目录不存在"}
    loader = LocalSkillLoader(directory=str(path), scan_subdir=True)
    skills = await loader.list_skills()
    return {"count": len(skills), "names": [getattr(s, "name", "?") for s in skills][:30]}


@router.get("/plugins/discover", summary="扫描 QwenPaw 插件目录（工具型插件兼容层；仅限已保存目录）")
def discover_plugins(dir: str = Query(..., max_length=300, alias="dir")):
    from ..agents.qwenpaw_compat import load_qwenpaw_plugin

    if not _is_saved_dir(dir, _saved_config_dir("plugins_dir")):
        # 安全边界：load_qwenpaw_plugin 会执行插件后端代码，仅已保存目录可触发
        return {"loadable": 0, "plugins": [], "error": "目录未保存：请先在设置中保存该插件目录再检测"}
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

# 附件扩展名白名单：文本/数据类（可按文本注入）+ Excel + 图片（多模态理解）
_UPLOAD_SUFFIXES = {
    ".txt", ".md", ".csv", ".json", ".log", ".py", ".html", ".xml", ".yaml", ".yml", ".sql",
    ".xlsx", ".xls",
    ".png", ".jpg", ".jpeg", ".webp", ".gif",
}
# 图片扩展名（多模态注入）
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif"}


class _RowLimitExceeded(ValueError):
    """Excel 首表行数超过 MAX_IMPORT_ROWS：上传整体拒绝并清理，不遗留半成品。"""


def _remove_quietly(*paths: Path) -> None:
    """尽力删除上传残留文件（失败仅记日志，不掩盖原始错误）。"""
    for p in paths:
        try:
            p.unlink(missing_ok=True)
        except OSError:
            logger.warning("清理上传残留文件失败：%s", p, exc_info=True)


def _xlsx_first_sheet_to_csv(src: Path, dst: Path, max_rows: int) -> int:
    """把 xlsx 第一张工作表流式写为 CSV（utf-8-sig），返回写入行数。

    同步函数：经 asyncio.to_thread 卸载，避免慢转换阻塞事件循环；
    行数（含表头）超过 max_rows 立即中止，不产出巨型/半成品 CSV。
    """
    import openpyxl

    wb = openpyxl.load_workbook(src, read_only=True, data_only=True)
    written = 0
    try:
        ws = wb.active
        with open(dst, "w", newline="", encoding="utf-8-sig") as f:
            writer = _csv.writer(f)
            for row in ws.iter_rows(values_only=True):
                written += 1
                if written > max_rows:
                    raise _RowLimitExceeded(f"工作表行数超过上限（{max_rows} 行）")
                writer.writerow(["" if c is None else c for c in row])
    finally:
        wb.close()
    return written


@router.post("/upload", summary="上传聊天附件（图片/文本/Excel；xlsx 自动转 CSV；上限 10MB）")
async def upload_attachment(
    session_id: str = Form(...),
    file: UploadFile = File(...),
):
    _check_session_id(session_id)
    safe_name = Path(file.filename or "file.bin").name
    if safe_name in ("", ".", ".."):
        safe_name = "file.bin"
    suffix = Path(safe_name).suffix.lower()
    # 扩展名白名单（无扩展名按普通文件放行）；图片单独标记 kind=image
    if suffix and suffix not in _UPLOAD_SUFFIXES:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的文件类型：{suffix}（支持：{', '.join(sorted(_UPLOAD_SUFFIXES))}）",
        )
    dest_dir = UPLOAD_DIR / session_id
    dest = dest_dir / safe_name
    if dest.resolve().parent != dest_dir.resolve():
        raise HTTPException(status_code=400, detail="非法的文件名")

    # 共享限额读取（分块、有界）：失败/超限发生在落盘之前，不遗留任何文件
    if read_upload_limited is None:
        raise HTTPException(status_code=503, detail="上传读取服务暂不可用，请稍后重试")
    try:
        content = await read_upload_limited(file, _MAX_UPLOAD_BYTES)
    except HTTPException:
        raise
    except Exception:
        logger.warning("上传读取失败或超限：session_id=%s", session_id, exc_info=True)
        raise HTTPException(
            status_code=400,
            detail=f"文件读取失败或超过 {_MAX_UPLOAD_BYTES // (1024 * 1024)}MB 上限",
        )

    dest_dir.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(content)

    kind, note = "file", ""
    if suffix in _IMAGE_SUFFIXES:
        kind = "image"
    elif suffix in (".xlsx", ".xls"):
        if MAX_IMPORT_ROWS is None:
            _remove_quietly(dest)
            raise HTTPException(status_code=503, detail="Excel 行数上限服务暂不可用，请稍后重试")
        csv_path = dest.with_suffix(".csv")
        try:
            # 慢转换卸载到线程（事件循环不被阻塞）；行数超限即中止，不产出半成品
            await asyncio.to_thread(_xlsx_first_sheet_to_csv, dest, csv_path, MAX_IMPORT_ROWS)
            dest = csv_path
            kind, note = "csv_converted", f"已转换第一张工作表为 CSV：{dest.name}"
        except _RowLimitExceeded as exc:
            # 行数超限：上传整体拒绝，xlsx 与半成品 CSV 一并清理
            _remove_quietly(dest, csv_path)
            raise HTTPException(status_code=400, detail=str(exc))
        except Exception as exc:
            # 转换失败降级：清半成品 CSV，保留 xlsx 本体与说明（既有契约）
            _remove_quietly(csv_path)
            note = f"Excel 转 CSV 失败：{exc}"

    return {"name": dest.name, "path": str(dest), "kind": kind, "size": dest.stat().st_size, "note": note}


# ---------------- 专家团队配置（agent_specs 表） ----------------

def _invalidate_agents() -> None:
    """专家配置变更后：清团队缓存 + 已构建的会话 Agent（提示词/模型可能已变）。"""
    team_mod.invalidate_team_cache()
    orchestrator.invalidate()


def _spec_out(row: AgentSpecModel) -> dict:
    return AgentSpecOut.model_validate(row).model_dump()


@router.get("/agents", summary="专家团队列表（含停用，按 sort 排序）")
def list_agents():
    with SessionLocal() as db:
        rows = db.query(AgentSpecModel).order_by(AgentSpecModel.sort.asc(), AgentSpecModel.id.asc()).all()
        return {"agents": [_spec_out(r) for r in rows]}


@router.post("/agents", summary="新增专家", status_code=201)
def create_agent(payload: AgentSpecCreate):
    with SessionLocal() as db:
        if db.query(AgentSpecModel).filter(AgentSpecModel.name == payload.name).first():
            raise HTTPException(status_code=400, detail=f"专家标识已存在：{payload.name}")
        row = AgentSpecModel(
            name=payload.name,
            display_name=payload.display_name,
            emoji=payload.emoji,
            color=payload.color,
            description=payload.description,
            role_prompt=payload.role_prompt,
            keywords=payload.keywords,
            tools=payload.tools,
            model_id=payload.model_id,
            skills=payload.skills,
            enabled=payload.enabled,
            sort=payload.sort,
            is_builtin=False,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        out = _spec_out(row)
    _invalidate_agents()
    return {"agent": out}


@router.put("/agents/{agent_id}", summary="更新专家（显式传 null 可清空 model_id/tools/skills）")
def update_agent(agent_id: int, payload: AgentSpecUpdate):
    with SessionLocal() as db:
        row = db.get(AgentSpecModel, agent_id)
        if row is None:
            raise HTTPException(status_code=404, detail="专家不存在")
        data = payload.model_dump(exclude_unset=True)
        if "name" in data and data["name"] != row.name:
            if db.query(AgentSpecModel).filter(AgentSpecModel.name == data["name"]).first():
                raise HTTPException(status_code=400, detail=f"专家标识已存在：{data['name']}")
        for key, value in data.items():
            setattr(row, key, value)
        db.commit()
        db.refresh(row)
        out = _spec_out(row)
    _invalidate_agents()
    return {"agent": out}


@router.delete("/agents/{agent_id}", summary="删除专家（内置专家允许删除，前端需自行警告）")
def delete_agent(agent_id: int):
    with SessionLocal() as db:
        row = db.get(AgentSpecModel, agent_id)
        if row is None:
            raise HTTPException(status_code=404, detail="专家不存在")
        name = row.name
        db.delete(row)
        db.commit()
    _invalidate_agents()
    return {"ok": True, "deleted": name}


class RouteTestRequest(BaseModel):
    text: str = Field(min_length=1, max_length=2000, description="待测试的用户消息")


@router.post("/agents/route-test", summary="关键词路由预览：返回会命中的专家与命中词")
def route_test(payload: RouteTestRequest):
    team = team_mod.load_team(force=True)
    matches = team_mod.route_scores(payload.text, team)
    return {
        "routed": matches[0]["name"] if matches else team_mod.default_agent_name(team),
        "matched_by": "关键词" if matches else "默认",
        "matches": matches,
    }


@router.post("/agents/reset", summary="内置专家恢复出厂定义（按 name 覆盖/补插，不影响自定义专家）")
def reset_agents():
    from ..agents.team import TEAM

    restored = []
    with SessionLocal() as db:
        for spec in sorted(TEAM.values(), key=lambda s: s.sort):
            row = db.query(AgentSpecModel).filter(AgentSpecModel.name == spec.name).first()
            if row is None:
                row = AgentSpecModel(name=spec.name)
                db.add(row)
            row.display_name = spec.label
            row.emoji = spec.emoji
            row.color = spec.color
            row.description = spec.description
            row.role_prompt = spec.system_prompt
            row.keywords = spec.keywords
            row.tools = spec.tools
            row.model_id = None
            row.skills = None
            row.enabled = True
            row.sort = spec.sort
            row.is_builtin = True
            restored.append(spec.name)
        db.commit()
    # 静态 TEAM.tools 不含模块预置工具；恢复出厂后按已加载模块 manifest 幂等补回，
    # 否则 finance_specialist 会丢掉财务模块工具、专家包一致性检查出现伪差异
    from ..modules.registry import apply_loaded_presets

    apply_loaded_presets()
    _invalidate_agents()
    return {"ok": True, "restored": restored}


# ---------------- 会话管理 ----------------

class SessionRequest(BaseModel):
    session_id: str = Field(min_length=6, max_length=64)
    title: Optional[str] = Field(None, max_length=100)


@router.get("/sessions", summary="会话列表（按更新时间倒序；archived 参数按归档状态过滤）")
def get_sessions(limit: int = Query(50, ge=1, le=200), archived: Optional[bool] = Query(None, description="true=仅归档 / false=仅未归档；不传=全部")):
    return {"sessions": orchestrator.sessions_list(limit, archived)}


@router.post("/sessions", summary="新建会话")
def create_session(payload: SessionRequest):
    _check_session_id(payload.session_id)
    return orchestrator.session_create(payload.session_id, payload.title or "")


@router.put("/sessions/{session_id}", summary="会话重命名")
def rename_session(session_id: str, payload: SessionRequest):
    _check_session_id(session_id)
    return orchestrator.session_rename(session_id, payload.title or "")


@router.delete("/sessions/{session_id}", summary="删除会话（含全部消息）")
def delete_session(session_id: str):
    _check_session_id(session_id)
    orchestrator.session_delete(session_id)
    return {"ok": True}


@router.post("/sessions/{session_id}/archive", summary="归档会话（不存在返回 404）")
def archive_session(session_id: str):
    _check_session_id(session_id)
    try:
        orchestrator.session_set_archived(session_id, True)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {"ok": True}


@router.post("/sessions/{session_id}/unarchive", summary="恢复会话（不存在返回 404）")
def unarchive_session(session_id: str):
    _check_session_id(session_id)
    try:
        orchestrator.session_set_archived(session_id, False)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {"ok": True}


@router.get("/sessions/{session_id}/export", summary="导出会话为 Markdown 附件（不存在返回 404）")
def export_session(session_id: str):
    _check_session_id(session_id)
    try:
        data = orchestrator.session_export(session_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return Response(
        content=data["markdown"],
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="session-{session_id}.md"'},
    )


# ---------------- 对话 ----------------

@router.post("/chat", summary="与 Agent 团队对话（非流式）")
async def chat(payload: ChatRequest):
    _check_session_id(payload.session_id)
    atts = [a.model_dump() for a in (payload.attachments or [])]
    try:
        orchestrator.assert_vision_supported(atts, payload.agent)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    try:
        return await orchestrator.chat(
            payload.session_id, payload.message, payload.agent, payload.mode or "standard", atts
        )
    except ValueError as exc:
        # 编排层业务校验失败（如路由后按专家绑定模型复核视觉能力）：与前置校验同口径 400
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception:
        # 模型网络/鉴权等运行时错误：记录完整堆栈，对外不回传内部细节
        logger.exception("chat 接口执行失败：session_id=%s", payload.session_id)
        raise HTTPException(status_code=502, detail="助手服务暂时不可用，请稍后重试")


@router.post("/chat/stream", summary="与 Agent 团队对话（SSE 流式）")
async def chat_stream(payload: ChatRequest):
    _check_session_id(payload.session_id)
    atts = [a.model_dump() for a in (payload.attachments or [])]
    try:
        orchestrator.assert_vision_supported(atts, payload.agent)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

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
        except Exception:
            # 流式端点保持 SSE 协议：记录日志后在流内发 error 事件，不回传内部细节
            logger.exception("chat/stream 接口执行失败：session_id=%s", payload.session_id)
            yield f"data: {json.dumps({'type': 'error', 'message': '助手服务暂时不可用，请稍后重试'}, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        sse(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


@router.get("/history/{session_id}", summary="会话历史（时间正序）")
def history(session_id: str, limit: int = Query(50, ge=1, le=200)):
    _check_session_id(session_id)
    return {"session_id": session_id, "messages": orchestrator.history(session_id, limit)}
