"""MCP 服务器管理接口：/api/mcp（连接、工具清单、测试调用、内置服务器目录）。"""
import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..agents.mcp import (
    McpError,
    connect_server,
    create_server,
    delete_server,
    disconnect_server,
    list_servers,
    test_call,
    update_server,
)
from ..database import get_db
from ..utils import DbId

router = APIRouter(prefix="/mcp", tags=["MCP 工具"])


class McpServerIn(BaseModel):
    name: str = Field(min_length=1, max_length=50, description="服务器名（工具前缀）")
    command: str = Field(min_length=1, max_length=500, description="启动命令，如 npx / python / uv")
    args: list[str] = Field(default_factory=list, description="命令参数")
    env: dict = Field(default_factory=dict, description="环境变量（敏感值仅存本机库）")
    enabled: bool = True


class McpServerUpdate(BaseModel):
    command: str | None = None
    args: list[str] | None = None
    env: dict | None = None
    enabled: bool | None = None


class TestCallIn(BaseModel):
    tool: str = Field(min_length=1, description="工具名（服务器上的原名）")
    arguments: dict = Field(default_factory=dict)


# ---------- 服务器目录（v1.18.0：移植 Qoder/官方 MCP 市场形态，离线内置） ----------

_CATALOG_PATH = Path(__file__).resolve().parent.parent / "mcp_catalog.json"


def _load_catalog() -> dict:
    try:
        return json.loads(_CATALOG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"version": 0, "servers": []}


class CatalogAddIn(BaseModel):
    name: str = Field(min_length=1, max_length=50, description="服务器名（默认目录 id，可改）")
    command: str | None = Field(None, max_length=500, description="覆盖命令（默认用目录模板）")
    args: list[str] | None = None
    env: dict | None = None
    enabled: bool = False


@router.get("/catalog", summary="MCP 服务器目录（内置常用服务器模板，一键添加）")
def catalog_list(db: Session = Depends(get_db)):
    """内置离线目录 + 已添加状态（按同名 server 判断）。"""
    data = _load_catalog()
    from ..models import McpServer
    existing = {r.name for r in db.query(McpServer).all()}
    return {"version": data.get("version", 1),
            "servers": [{**s, "installed": s.get("name") in existing}
                        for s in data.get("servers", [])]}


@router.post("/catalog/{catalog_id}/add", status_code=201,
             summary="按目录模板一键添加 MCP 服务器（默认停用，可先编辑再启用）")
def catalog_add(catalog_id: str, payload: CatalogAddIn, db: Session = Depends(get_db)):
    data = _load_catalog()
    entry = next((s for s in data.get("servers", []) if s.get("id") == catalog_id), None)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"目录里没有这个服务器：{catalog_id}")
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="服务器名不能为空")
    try:
        row = create_server(db, name=name, command=payload.command or entry["command"],
                            args=payload.args if payload.args is not None else list(entry.get("args") or []),
                            env=payload.env if payload.env is not None else dict(entry.get("env") or {}),
                            enabled=payload.enabled)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except McpError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "server": row, "catalog_id": catalog_id}


@router.get("/servers", summary="MCP 服务器列表（含工具清单，env 脱敏）")
def list_all(db: Session = Depends(get_db)):
    return {"items": list_servers(db)}


@router.post("/servers", status_code=201, summary="新增 MCP 服务器")
def create(payload: McpServerIn, db: Session = Depends(get_db)):
    try:
        return create_server(db, payload.name, payload.command, payload.args, payload.env, payload.enabled)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.put("/servers/{server_id}", summary="更新 MCP 服务器")
def update(server_id: DbId, payload: McpServerUpdate, db: Session = Depends(get_db)):
    try:
        return update_server(db, server_id, payload.model_dump(exclude_unset=True))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/servers/{server_id}", summary="删除 MCP 服务器")
def delete(server_id: DbId, db: Session = Depends(get_db)):
    try:
        delete_server(db, server_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"ok": True}


@router.post("/servers/{server_id}/connect", summary="连接服务器并注册其工具")
def connect(server_id: DbId, db: Session = Depends(get_db)):
    try:
        return connect_server(db, server_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except McpError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/servers/{server_id}/disconnect", summary="断开服务器")
def disconnect(server_id: DbId, db: Session = Depends(get_db)):
    try:
        return disconnect_server(db, server_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/servers/{server_id}/test-call", summary="测试调用服务器工具")
def call(server_id: DbId, payload: TestCallIn, db: Session = Depends(get_db)):
    from ..models import McpServer

    row = db.get(McpServer, server_id)
    if row is None:
        raise HTTPException(status_code=404, detail="服务器不存在")
    try:
        return test_call(row.name, payload.tool, payload.arguments)
    except McpError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
