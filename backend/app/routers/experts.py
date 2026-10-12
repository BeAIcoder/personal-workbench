"""专家团文件化接口：/api/experts（专家包 .md 的清单/导入/导出/查看/删除）。"""
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..agents import expert_packs
from ..database import get_db

router = APIRouter(prefix="/experts", tags=["专家团"])

logger = logging.getLogger(__name__)


@router.get("/packs", summary="专家包文件清单（含与数据库的一致性比对）")
def list_packs(db: Session = Depends(get_db)):
    return {"items": expert_packs.list_packs(db)}


@router.post("/packs/import-all", summary="导入全部有效专家包（upsert）")
def import_all(db: Session = Depends(get_db)):
    return expert_packs.import_all(db)


@router.post("/packs/import/{name}", summary="导入指定专家包（upsert 进 agent_specs）")
def import_one(name: str, db: Session = Depends(get_db)):
    try:
        return expert_packs.import_pack(name, db)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/packs/reset", summary="从内置种子重置专家包目录（覆盖同名文件）")
def reset_packs():
    return expert_packs.reset_from_seed()


@router.post("/packs/export/{expert_name}", summary="把数据库专家导出为 .md 文件（覆盖同名文件）")
def export_one(expert_name: str, db: Session = Depends(get_db)):
    try:
        return expert_packs.export_pack(expert_name, db)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/packs/{name}/content", summary="查看专家包 .md 原文")
def get_content(name: str):
    try:
        return expert_packs.read_pack_file(name)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.delete("/packs/{name}", summary="删除专家包文件（不影响数据库中的专家）")
def delete_one(name: str):
    try:
        return expert_packs.delete_pack(name)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
