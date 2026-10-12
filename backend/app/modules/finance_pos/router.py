"""finance_pos 模块接口：/api/finance/pos（前缀由注册器按 manifest.prefix 挂载）。"""
import zipfile
from io import BytesIO
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy.orm import Session

from ...database import get_db
from . import service
from .schemas import PosRunListOut, PosRunOut

router = APIRouter(tags=["财务模块·POS 对账"])


@router.post("/runs", response_model=PosRunOut, status_code=201,
             summary="上传输入文件建运行（SHOP 对账单 + 认领记录 + 辅助明细账 + 凭证列表，或一个 ZIP）")
async def create_run(files: list[UploadFile] = File(...), db: Session = Depends(get_db)):
    uploads = [(f.filename or "file.xlsx", await f.read()) for f in files]
    try:
        return service.create_run(db, uploads)
    except service.EngineUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/runs", response_model=PosRunListOut, summary="运行记录列表（按 id 倒序）")
def list_runs(db: Session = Depends(get_db)):
    items = service.list_runs(db)
    return {"total": len(items), "items": items}


@router.get("/runs/{run_id}", response_model=PosRunOut, summary="运行记录详情")
def run_detail(run_id: int, db: Session = Depends(get_db)):
    return service._out(service.get_run(db, run_id))


@router.post("/runs/{run_id}/execute", response_model=PosRunOut,
             summary="执行运行（台账生成 + 四源差异分析）")
def execute_run(run_id: int, db: Session = Depends(get_db)):
    try:
        return service.execute_run(db, run_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except service.EngineUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/runs/{run_id}/artifacts/{name}", summary="下载单个产物")
def download_artifact(run_id: int, name: str, db: Session = Depends(get_db)):
    run = service.get_run(db, run_id)
    path = service.artifact_path(run, name)
    return FileResponse(str(path), filename=Path(name).name)


@router.get("/runs/{run_id}/download", summary="打包下载全部产物（ZIP）")
def download_all(run_id: int, db: Session = Depends(get_db)):
    run = service.get_run(db, run_id)
    if not run.artifacts:
        raise HTTPException(status_code=409, detail="该运行暂无产物")
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for a in run.artifacts:
            p = Path(a["path"])
            if p.is_file():
                zf.write(p, arcname=a["name"])
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename=pos_run_{run_id}.zip"},
    )
