"""技能包管理接口：/api/skills（SKILL.md 技能包的清单/启停/新建/删除/读文件）。"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..agents import orchestrator
from ..agents.skill_store import (
    create_pack,
    delete_pack,
    ensure_seed_packs,
    read_pack_file,
    scan_packs,
    set_enabled,
)

router = APIRouter(prefix="/skills", tags=["技能包"])


class PackCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=50, description="技能包名称（小写字母/数字/连字符）")
    description: str = Field(min_length=1, max_length=500, description="技能描述（AI 助手据此决定何时启用）")
    body: str = Field(default="", max_length=20000, description="SKILL.md 正文（留空用内置模板）")


class PackEnabledIn(BaseModel):
    enabled: bool


@router.get("/packs", summary="技能包清单（含启停与校验状态）")
def list_packs():
    return {"items": scan_packs()}


@router.post("/packs", status_code=201, summary="新建技能包（内置模板生成 SKILL.md）")
def create(payload: PackCreateIn):
    try:
        result = create_pack(payload.name, payload.description, payload.body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    orchestrator.invalidate()  # 技能池已变：失效已缓存 Agent，下次对话重建
    return result


@router.delete("/packs/{name}", summary="删除技能包（整目录）")
def delete(name: str):
    try:
        delete_pack(name)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    orchestrator.invalidate()
    return {"ok": True, "deleted": name}


@router.put("/packs/{name}/enabled", summary="启用/停用技能包")
def set_pack_enabled(name: str, payload: PackEnabledIn):
    try:
        result = set_enabled(name, payload.enabled)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    orchestrator.invalidate()
    return result


@router.get("/packs/{name}/files/{filename:path}", summary="读取技能包内文件内容")
def read_file(name: str, filename: str):
    try:
        content = read_pack_file(name, filename)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"name": name, "filename": filename, "content": content}


@router.post("/rescan", summary="重新扫描（并补齐种子技能包）")
def rescan():
    seeded = ensure_seed_packs()
    return {"items": scan_packs(), "seeded": seeded}
