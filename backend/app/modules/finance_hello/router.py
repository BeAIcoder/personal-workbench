"""finance_hello 模块接口：/api/finance/hello（前缀由注册器按 manifest.prefix 挂载）。"""
from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from ...database import get_db
from . import service
from .schemas import HelloEntryCreate, HelloEntryOut

router = APIRouter(tags=["财务模块·示例"])


@router.get("/ping", summary="模块连通性自检（机制实证）")
def ping():
    return {"module": "finance_hello", "ok": True}


@router.get("/entries", response_model=list[HelloEntryOut], summary="样例条目列表（按 id 倒序）")
def list_entries(limit: int = Query(20, ge=1, le=100), db: Session = Depends(get_db)):
    return service.list_entries(db, limit)


@router.post("/entries", response_model=HelloEntryOut, status_code=status.HTTP_201_CREATED, summary="新建样例条目")
def create_entry(payload: HelloEntryCreate, db: Session = Depends(get_db)):
    return service.create_entry(db, payload.name)
