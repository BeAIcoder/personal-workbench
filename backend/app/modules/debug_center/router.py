"""debug_center 模块接口：/api/debug（前缀由注册器按 manifest.prefix 挂载）。

首次请求时确保种子问题已写入（ensure_seed_issues 幂等，同时挂在 main.py lifespan）。
"""
import json

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import FileResponse

from . import service
from .schemas import IssueCreate, IssueUpdate

router = APIRouter(tags=["调试中心"])

# client-errors 上报体的局部上限（审计 A05）：全局 ASGI 限额之外的端点级护栏，
# 超出即 400；解析保持宽容——非法 JSON 一律归一记录，绝不 500
CLIENT_ERROR_MAX_BODY_BYTES = 1 * 1024 * 1024


# ---------- 问题记录 ----------


@router.get("/issues", summary="问题列表（可按状态/严重度筛选）")
def list_issues(status: str = "", severity: str = ""):
    service.ensure_seed_issues()
    items = service.list_issues(status=status, severity=severity)
    return {"total": len(items), "items": items}


@router.post("/issues", status_code=status.HTTP_201_CREATED, summary="新建问题")
def create_issue(payload: IssueCreate):
    return service.create_issue(payload)


@router.put("/issues/{issue_id}", summary="更新问题（自动刷新 updated_at）")
def update_issue(issue_id: str, payload: IssueUpdate):
    try:
        return service.update_issue(issue_id, payload)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete("/issues/{issue_id}", summary="删除问题")
def delete_issue(issue_id: str):
    try:
        service.delete_issue(issue_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"ok": True}


# ---------- 测试运行归档 ----------


@router.get("/runs", summary="测试运行列表（时间倒序）")
def list_runs():
    items = service.list_runs()
    return {"total": len(items), "items": items}


@router.get("/runs/{run_id}", summary="测试运行详情（含截图文件名列表）")
def get_run(run_id: str):
    try:
        return service.get_run(run_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/runs/{run_id}/shots/{filename}", summary="运行截图（防路径穿越）")
def get_run_shot(run_id: str, filename: str):
    try:
        path = service.shot_path(run_id, filename)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return FileResponse(str(path), media_type="image/png")


@router.delete("/runs/{run_id}", summary="删除一次测试运行（整目录）")
def delete_run(run_id: str):
    try:
        service.delete_run(run_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True}


# ---------- 前端报错采集 ----------


@router.get("/client-errors", summary="前端报错列表（尾部 limit 条）")
def list_client_errors(limit: int = Query(200, ge=1, le=1000)):
    items = service.list_client_errors(limit)
    return {"total": len(items), "items": items}


@router.post("/client-errors", status_code=status.HTTP_201_CREATED, summary="追加一条前端报错（有界宽容解析）")
async def append_client_error(request: Request):
    buf = bytearray()
    try:
        async for chunk in request.stream():
            buf.extend(chunk)
            if len(buf) > CLIENT_ERROR_MAX_BODY_BYTES:
                raise HTTPException(
                    status_code=400,
                    detail=f"请求体超过 {CLIENT_ERROR_MAX_BODY_BYTES} 字节上限",
                )
    except HTTPException:
        raise
    except Exception:  # noqa: BLE001  读取失败（如连接中断）也归一记录，绝不能 500
        buf = bytearray()
    try:
        payload = json.loads(bytes(buf)) if buf else None
    except ValueError:  # 含 UnicodeDecodeError：非法 JSON/编码同样归一记录
        payload = None
    return service.append_client_error(payload)


@router.delete("/client-errors", summary="清空前端报错")
def clear_client_errors():
    service.clear_client_errors()
    return {"ok": True}


# ---------- 汇总 ----------


@router.get("/summary", summary="调试中心总览")
def get_summary():
    service.ensure_seed_issues()
    return service.summary()
