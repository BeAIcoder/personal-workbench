"""finance_board 模块接口：/api/finance/board（前缀由注册器按 manifest.prefix 挂载）。"""
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from ...database import get_db
from . import service
from .schemas import RunResultOut, SnapshotListOut, SnapshotOut

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
    return FileResponse(
        str(path),
        media_type="text/html; charset=utf-8",
        headers={"Content-Security-Policy": "frame-ancestors 'self'"},
    )
