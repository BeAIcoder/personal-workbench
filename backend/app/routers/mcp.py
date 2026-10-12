"""MCP 服务器管理接口：/api/mcp（连接、工具清单、测试调用）。"""
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
def update(server_id: int, payload: McpServerUpdate, db: Session = Depends(get_db)):
    try:
        return update_server(db, server_id, payload.model_dump(exclude_unset=True))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/servers/{server_id}", summary="删除 MCP 服务器")
def delete(server_id: int, db: Session = Depends(get_db)):
    try:
        delete_server(db, server_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"ok": True}


@router.post("/servers/{server_id}/connect", summary="连接服务器并注册其工具")
def connect(server_id: int, db: Session = Depends(get_db)):
    try:
        return connect_server(db, server_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except McpError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/servers/{server_id}/disconnect", summary="断开服务器")
def disconnect(server_id: int, db: Session = Depends(get_db)):
    try:
        return disconnect_server(db, server_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/servers/{server_id}/test-call", summary="测试调用服务器工具")
def call(server_id: int, payload: TestCallIn, db: Session = Depends(get_db)):
    from ..models import McpServer

    row = db.get(McpServer, server_id)
    if row is None:
        raise HTTPException(status_code=404, detail="服务器不存在")
    try:
        return test_call(row.name, payload.tool, payload.arguments)
    except McpError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
