"""
企业信息路由
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import List
from ....database import get_db
from ..models import Enterprise
from pydantic import BaseModel
from datetime import datetime

router = APIRouter(prefix="/enterprises", tags=["企业信息"])

# Pydantic模型
class EnterpriseCreate(BaseModel):
    name: str
    tax_number: str
    legal_person: str | None = None
    contact_phone: str | None = None
    address: str | None = None
    industry: str | None = None
    tax_type: str = "查账征收"

class EnterpriseUpdate(BaseModel):
    name: str | None = None
    legal_person: str | None = None
    contact_phone: str | None = None
    address: str | None = None
    industry: str | None = None
    tax_type: str | None = None

class EnterpriseResponse(BaseModel):
    id: int
    name: str
    tax_number: str
    industry_type: str | None
    legal_person: str | None = None
    contact_phone: str | None = None
    address: str | None = None
    tax_type: str | None = None
    employee_count: int | None
    total_assets: float | None
    is_restricted_industry: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

@router.get("/", response_model=List[EnterpriseResponse])
async def get_enterprises(skip: int = Query(0, ge=0, description="跳过记录数"),
                          limit: int = Query(100, ge=1, le=200, description="返回记录数上限"),
                          db: Session = Depends(get_db)):
    """获取企业列表"""
    enterprises = db.query(Enterprise).offset(skip).limit(limit).all()
    return enterprises

@router.get("/{enterprise_id}", response_model=EnterpriseResponse)
async def get_enterprise(enterprise_id: int, db: Session = Depends(get_db)):
    """获取单个企业详情"""
    enterprise = db.query(Enterprise).filter(Enterprise.id == enterprise_id).first()
    if not enterprise:
        raise HTTPException(status_code=404, detail="企业不存在")
    return enterprise

@router.post("/", response_model=EnterpriseResponse)
async def create_enterprise(enterprise: EnterpriseCreate, db: Session = Depends(get_db)):
    """创建新企业"""
    # 检查税号是否已存在
    existing = db.query(Enterprise).filter(Enterprise.tax_number == enterprise.tax_number).first()
    if existing:
        raise HTTPException(status_code=400, detail="纳税人识别号已存在")

    # 过滤Pydantic模型中与SQLAlchemy模型不匹配的字段
    enterprise_data = enterprise.model_dump()
    db_enterprise = Enterprise(
        name=enterprise_data["name"],
        tax_number=enterprise_data["tax_number"],
        industry_type=enterprise_data.get("industry"),
        legal_person=enterprise_data.get("legal_person"),
        contact_phone=enterprise_data.get("contact_phone"),
        address=enterprise_data.get("address"),
        tax_type=enterprise_data.get("tax_type"),
    )
    db.add(db_enterprise)
    db.commit()
    db.refresh(db_enterprise)
    return db_enterprise

@router.put("/{enterprise_id}", response_model=EnterpriseResponse)
async def update_enterprise(
    enterprise_id: int,
    enterprise_update: EnterpriseUpdate,
    db: Session = Depends(get_db)
):
    """更新企业信息"""
    db_enterprise = db.query(Enterprise).filter(Enterprise.id == enterprise_id).first()
    if not db_enterprise:
        raise HTTPException(status_code=404, detail="企业不存在")

    update_data = enterprise_update.model_dump(exclude_unset=True)
    # 映射Pydantic字段名到SQLAlchemy字段名
    field_map = {
        "industry": "industry_type",
    }
    for key, value in update_data.items():
        db_key = field_map.get(key, key)
        if hasattr(db_enterprise, db_key):
            setattr(db_enterprise, db_key, value)

    db.commit()
    db.refresh(db_enterprise)
    return db_enterprise

@router.delete("/{enterprise_id}")
async def delete_enterprise(enterprise_id: int, db: Session = Depends(get_db)):
    """删除企业"""
    db_enterprise = db.query(Enterprise).filter(Enterprise.id == enterprise_id).first()
    if not db_enterprise:
        raise HTTPException(status_code=404, detail="企业不存在")
    
    db.delete(db_enterprise)
    db.commit()
    return {"message": "删除成功"}
