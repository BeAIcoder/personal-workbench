"""finance_board 模块接口：/api/finance/board（前缀由注册器按 manifest.prefix 挂载）。"""
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from ...database import get_db
from . import runs
from . import service
from .schemas import RunAcceptedOut, RunResultOut, RunStatusOut, SnapshotListOut, SnapshotOut

router = APIRouter(tags=["财务模块·报表看板"])


@router.get("/snapshots", response_model=SnapshotListOut, summary="看板快照列表（按时间倒序）")
def list_snapshots(db: Session = Depends(get_db)):
    items = service.list_snapshots(db)
    return {"total": len(items), "items": items}


@router.post("/run", response_model=RunResultOut, summary="运行一次报表监控管线并归档快照")
def run_pipeline():
    try:
        snap = service.run_pipeline()
    except service.PipelineBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except service.PipelineUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {**snap, "message": "管线运行完成，快照已归档"}


@router.post("/runs", response_model=RunAcceptedOut, status_code=202,
             summary="异步运行报表监控管线（202 + run_id，轮询 GET /runs/{run_id}）")
def start_run():
    """异步入口：受理即返回 202；忙态 409，运行结果经 GET /runs/{run_id} 轮询。

    同步 POST /run 保留兼容（既有调用方与定时任务动作不变）。
    """
    try:
        return runs.start_run()
    except service.PipelineBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/runs/{run_id}", response_model=RunStatusOut, summary="查询异步运行状态")
def run_status(run_id: str):
    record = runs.get_run(run_id)
    if record is None:
        # 记录只在进程内：重启/过期/拼错都会到这里，让调用方重新发起而不是假装在跑
        raise HTTPException(
            status_code=404,
            detail="运行记录不存在（可能已过期或服务已重启），请重新发起运行",
        )
    return record


@router.get("/verify", summary="看板产物内容级巡检（HTML 内嵌数据 vs 管线数据源）")
def verify_snapshot(snapshot_id: int | None = Query(default=None, ge=1)):
    return service.verify_snapshot(snapshot_id)


@router.get("/latest", response_model=SnapshotOut, summary="最新成功快照（无则 404）")
def latest(db: Session = Depends(get_db)):
    snap = service.latest_snapshot(db)
    if snap is None:
        raise HTTPException(status_code=404, detail="暂无成功快照，请先运行管线")
    return service._out(snap)


@router.get("/snapshots/{snapshot_id}/html", summary="看板 HTML（自包含单文件，可直接 iframe 嵌入）")
def snapshot_html(snapshot_id: int, db: Session = Depends(get_db)):
    snap = service.get_snapshot(db, snapshot_id)
    path = Path(snap.html_path)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="快照文件缺失，请重新运行管线")
    # 快照路径必须落在本模块快照根目录内：数据库/管线产物被篡改时不得借该接口
    # 以 text/html 同源吐出任意本机文件
    snapshots_root = service._snapshots_dir().resolve()
    resolved = path.resolve()
    if not resolved.is_relative_to(snapshots_root):
        raise HTTPException(status_code=403, detail="快照路径无效")
    return FileResponse(
        str(path),
        media_type="text/html; charset=utf-8",
        headers={
            "Content-Security-Policy": "frame-ancestors 'self'",
            "X-Content-Type-Options": "nosniff",
        },
    )
