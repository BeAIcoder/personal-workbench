"""
企业信息路由
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import List
from ....database import get_db
from ..models import Enterprise, TaxReturn
from pydantic import BaseModel, ConfigDict, field_validator
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
    # 前端使用 industry_type 命名；两者都接收，落库统一到 industry_type
    industry_type: str | None = None
    tax_type: str = "查账征收"
    # 小型微利判定必需字段（BUGfix TAX-15 / FE-01）：原先 Schema 缺这三项，
    # 前端提交后被 Pydantic 静默丢弃，库里恒为 NULL → 恒判小微
    employee_count: int | None = None
    total_assets: float | None = None
    is_restricted_industry: bool | None = None
    # 高新技术企业资格（台账 L-050）：与计算/导出共用同一属性
    is_high_tech: bool | None = None

class EnterpriseUpdate(BaseModel):
    name: str | None = None
    legal_person: str | None = None
    contact_phone: str | None = None
    address: str | None = None
    industry: str | None = None
    industry_type: str | None = None
    tax_type: str | None = None
    employee_count: int | None = None
    total_assets: float | None = None
    is_restricted_industry: bool | None = None
    is_high_tech: bool | None = None

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
    # 历史行可能为 NULL：默认值与清洗器双保险，避免响应校验 500（复核 M-3）
    is_restricted_industry: bool = False
    is_high_tech: bool = False
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)

    @field_validator("is_restricted_industry", "is_high_tech", mode="before")
    @classmethod
    def _flag_none_to_false(cls, v):
        """库中历史 NULL 按「否」展示（布尔标记不存在「未知即真」的语义）。"""
        return bool(v) if v is not None else False

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
        industry_type=enterprise_data.get("industry_type") or enterprise_data.get("industry"),
        legal_person=enterprise_data.get("legal_person"),
        contact_phone=enterprise_data.get("contact_phone"),
        address=enterprise_data.get("address"),
        tax_type=enterprise_data.get("tax_type"),
        employee_count=enterprise_data.get("employee_count"),
        total_assets=enterprise_data.get("total_assets"),
        is_restricted_industry=enterprise_data.get("is_restricted_industry"),
        is_high_tech=bool(enterprise_data.get("is_high_tech") or False),
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
    # 映射Pydantic字段名到SQLAlchemy字段名（industry_type 与 industry 都归一到 industry_type）
    field_map = {
        "industry": "industry_type",
        "industry_type": "industry_type",
    }
    # 布尔标记显式传 null 视为「否」：直接写 NULL 会让响应 schema 校验失败 → 自伤 500，
    # 且模型中该列本就有 DEFAULT 0（复核 M-3）。
    boolean_flags = {"is_high_tech", "is_restricted_industry"}
    for key, value in update_data.items():
        db_key = field_map.get(key, key)
        if hasattr(db_enterprise, db_key):
            if db_key in boolean_flags and value is None:
                value = False
            setattr(db_enterprise, db_key, value)

    db.commit()
    db.refresh(db_enterprise)
    return db_enterprise

@router.delete("/{enterprise_id}")
async def delete_enterprise(enterprise_id: int, db: Session = Depends(get_db)):
    """删除企业（存在已提交申报时中文 409，禁止级联抹掉申报留痕）。"""
    db_enterprise = db.query(Enterprise).filter(Enterprise.id == enterprise_id).first()
    if not db_enterprise:
        raise HTTPException(status_code=404, detail="企业不存在")

    submitted_count = (
        db.query(TaxReturn)
        .filter(TaxReturn.enterprise_id == enterprise_id, TaxReturn.status == "submitted")
        .count()
    )
    if submitted_count:
        raise HTTPException(
            status_code=409,
            detail=(
                f"该企业存在 {submitted_count} 条已提交的申报记录，不能删除；"
                "已提交申报需保留留痕，如需变更请先联系主管税务机关办理更正申报"
            ),
        )

    db.delete(db_enterprise)
    db.commit()
    return {"message": "删除成功"}
