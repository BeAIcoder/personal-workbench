# -*- coding: utf-8 -*-
"""WorkBuddy 同步导入接口：/api/assistant/workbuddy/*。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..agents import workbuddy_sync
from ..database import get_db

router = APIRouter(prefix="/assistant/workbuddy", tags=["WorkBuddy 同步"])


class ImportIn(BaseModel):
    home: str = Field(default="", max_length=400)
    skills: bool = True
    memories: bool = True
    identity: bool = True
    dry_run: bool = False


class SettingsIn(BaseModel):
    home: str = Field(default="", max_length=400)


@router.get("/scan", summary="扫描本机 WorkBuddy 数据（只读预览）")
def scan(home: str = ""):
    return workbuddy_sync.scan(home)


@router.post("/import", summary="执行导入（幂等 upsert；dry_run 只预览不写）")
def do_import(payload: ImportIn, db: Session = Depends(get_db)):
    try:
        result = workbuddy_sync.import_all(db, home=payload.home,
                                           skills=payload.skills,
                                           memories=payload.memories,
                                           identity=payload.identity,
                                           dry_run=payload.dry_run)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return result


@router.get("/settings", summary="查看导入设置与上次导入结果")
def get_settings():
    return workbuddy_sync.load_settings()


@router.put("/settings", summary="保存导入设置（WorkBuddy 数据目录）")
def put_settings(payload: SettingsIn):
    data = workbuddy_sync.load_settings()
    data["home"] = payload.home.strip()
    return workbuddy_sync.save_settings(data)
