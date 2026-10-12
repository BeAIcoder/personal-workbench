"""模块路由模板：router 不带前缀，注册器按 manifest.prefix 挂载（最终 /api/finance/<模块名>/...）。"""
from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from ...database import get_db
from . import service
from .schemas import ExampleEntryCreate, ExampleEntryOut

router = APIRouter(tags=["财务模块·示例"])


@router.get("/ping", summary="模块连通性自检")
def ping():
    return {"module": "finance_example", "ok": True}


@router.get("/entries", response_model=list[ExampleEntryOut], summary="条目列表（按 id 倒序）")
def list_entries(limit: int = Query(20, ge=1, le=100), db: Session = Depends(get_db)):
    return service.list_entries(db, limit)


@router.post("/entries", response_model=ExampleEntryOut, status_code=status.HTTP_201_CREATED, summary="新建条目")
def create_entry(payload: ExampleEntryCreate, db: Session = Depends(get_db)):
    return service.create_entry(db, payload.name)
