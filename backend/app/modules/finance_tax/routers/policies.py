"""
政策管理路由
实现政策的CRUD操作、版本管理、有效政策查询等功能
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_, func
from typing import List, Optional
from datetime import datetime
from pydantic import BaseModel

from ....database import get_db
from ..models import Policy

router = APIRouter(prefix="/policies", tags=["政策管理"])

# ==================== Pydantic 模型 ====================

class PolicyCreate(BaseModel):
    """创建政策请求体"""
    policy_number: str
    title: str
    version: str = "1.0"
    effective_date: datetime
    expiry_date: Optional[datetime] = None
    content: Optional[str] = None
    summary: Optional[str] = None
    policy_type: str
    tags: Optional[str] = None
    is_active: bool = True


class PolicyUpdate(BaseModel):
    """更新政策请求体"""
    title: Optional[str] = None
    version: Optional[str] = None
    effective_date: Optional[datetime] = None
    expiry_date: Optional[datetime] = None
    content: Optional[str] = None
    summary: Optional[str] = None
    policy_type: Optional[str] = None
    tags: Optional[str] = None
    is_active: Optional[bool] = None


class PolicyResponse(BaseModel):
    """政策响应体"""
    id: int
    policy_number: str
    title: str
    version: str
    effective_date: datetime
    expiry_date: Optional[datetime]
    content: Optional[str]
    summary: Optional[str]
    policy_type: str
    tags: Optional[str]
    is_active: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class PolicyListResponse(BaseModel):
    """政策列表响应体（含分页）"""
    total: int
    items: List[PolicyResponse]


class PolicyTypeItem(BaseModel):
    """政策类型选项"""
    value: str
    label: str
    count: int


# ==================== 辅助函数 ====================

def _check_effective_date_range(effective_date: datetime, expiry_date: Optional[datetime]) -> None:
    """校验日期范围合理性"""
    if expiry_date and expiry_date <= effective_date:
        raise HTTPException(status_code=400, detail="失效日期必须晚于生效日期")


def _is_currently_active(policy: Policy) -> bool:
    """判断政策在当前时间点是否有效"""
    now = datetime.now()
    if not policy.is_active:
        return False
    if now < policy.effective_date:
        return False
    if policy.expiry_date and now > policy.expiry_date:
        return False
    return True


# ==================== API 端点 ====================

@router.get("/", response_model=PolicyListResponse)
async def get_policies(
    skip: int = Query(0, ge=0, description="跳过记录数"),
    limit: int = Query(50, ge=1, le=200, description="返回记录数上限"),
    policy_type: Optional[str] = Query(None, description="按政策类型筛选"),
    is_active: Optional[bool] = Query(None, description="按启用状态筛选"),
    keyword: Optional[str] = Query(None, description="按名称/文号/摘要关键词搜索"),
    db: Session = Depends(get_db)
):
    """
    获取政策列表（支持分页、筛选、搜索）
    """
    query = db.query(Policy)

    # 按类型筛选
    if policy_type:
        query = query.filter(Policy.policy_type == policy_type)

    # 按启用状态筛选
    if is_active is not None:
        query = query.filter(Policy.is_active == is_active)

    # 关键词搜索
    if keyword:
        kw = f"%{keyword}%"
        query = query.filter(
            or_(
                Policy.title.like(kw),
                Policy.policy_number.like(kw),
                Policy.summary.like(kw),
            )
        )

    # 统计总数
    total = query.count()

    # 分页查询
    policies = query.order_by(Policy.effective_date.desc()).offset(skip).limit(limit).all()

    return {"total": total, "items": policies}


@router.get("/active", response_model=List[PolicyResponse])
async def get_active_policies(
    policy_type: Optional[str] = Query(None, description="按政策类型筛选"),
    db: Session = Depends(get_db)
):
    """
    获取当前有效政策（在生效期内且已启用）
    """
    now = datetime.now()
    query = db.query(Policy).filter(
        and_(
            Policy.is_active == True,
            Policy.effective_date <= now,
            or_(
                Policy.expiry_date.is_(None),
                Policy.expiry_date >= now,
            ),
        )
    )

    if policy_type:
        query = query.filter(Policy.policy_type == policy_type)

    policies = query.order_by(Policy.effective_date.desc()).all()
    return policies


@router.get("/types", response_model=List[PolicyTypeItem])
async def get_policy_types(db: Session = Depends(get_db)):
    """
    获取所有政策类型及对应数量
    """
    policies = db.query(Policy.policy_type, func.count(Policy.id)).group_by(
        Policy.policy_type
    ).all()

    type_labels = {
        "小微企业优惠": "小型微利企业优惠",
        "研发费用": "研发费用加计扣除",
        "申报表修订": "申报表修订",
        "税收法律": "税收法律法规",
        "增值税": "增值税相关政策",
        "其他": "其他政策",
    }

    return [
        PolicyTypeItem(
            value=policy_type,
            label=type_labels.get(policy_type, policy_type),
            count=count
        )
        for policy_type, count in policies
    ]


@router.get("/{policy_id}", response_model=PolicyResponse)
async def get_policy(policy_id: int, db: Session = Depends(get_db)):
    """
    获取政策详情
    """
    policy = db.query(Policy).filter(Policy.id == policy_id).first()
    if not policy:
        raise HTTPException(status_code=404, detail="政策不存在")
    return policy


@router.post("/", response_model=PolicyResponse, status_code=201)
async def create_policy(policy: PolicyCreate, db: Session = Depends(get_db)):
    """
    创建新政策
    """
    # 校验日期
    _check_effective_date_range(policy.effective_date, policy.expiry_date)

    # 检查文号是否已存在
    existing = db.query(Policy).filter(
        Policy.policy_number == policy.policy_number
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail=f"政策文号 '{policy.policy_number}' 已存在")

    db_policy = Policy(**policy.model_dump())
    db.add(db_policy)
    db.commit()
    db.refresh(db_policy)
    return db_policy


@router.put("/{policy_id}", response_model=PolicyResponse)
async def update_policy(
    policy_id: int,
    policy_update: PolicyUpdate,
    db: Session = Depends(get_db)
):
    """
    更新政策信息
    """
    db_policy = db.query(Policy).filter(Policy.id == policy_id).first()
    if not db_policy:
        raise HTTPException(status_code=404, detail="政策不存在")

    # 校验日期
    effective = policy_update.effective_date or db_policy.effective_date
    expiry = policy_update.expiry_date if policy_update.expiry_date is not None else db_policy.expiry_date
    _check_effective_date_range(effective, expiry)

    # 更新字段
    update_data = policy_update.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(db_policy, key, value)

    db.commit()
    db.refresh(db_policy)
    return db_policy


@router.delete("/{policy_id}")
async def delete_policy(policy_id: int, db: Session = Depends(get_db)):
    """
    删除政策
    """
    db_policy = db.query(Policy).filter(Policy.id == policy_id).first()
    if not db_policy:
        raise HTTPException(status_code=404, detail="政策不存在")

    db.delete(db_policy)
    db.commit()
    return {"message": "政策已删除", "id": policy_id}


@router.post("/{policy_id}/deactivate")
async def deactivate_policy(policy_id: int, db: Session = Depends(get_db)):
    """
    停用政策（软删除，不改变生效/失效日期）
    """
    db_policy = db.query(Policy).filter(Policy.id == policy_id).first()
    if not db_policy:
        raise HTTPException(status_code=404, detail="政策不存在")

    db_policy.is_active = False
    db.commit()
    db.refresh(db_policy)
    return {"message": "政策已停用", "id": policy_id, "is_active": False}


@router.post("/{policy_id}/activate")
async def activate_policy(policy_id: int, db: Session = Depends(get_db)):
    """
    启用政策
    """
    db_policy = db.query(Policy).filter(Policy.id == policy_id).first()
    if not db_policy:
        raise HTTPException(status_code=404, detail="政策不存在")

    db_policy.is_active = True
    db.commit()
    db.refresh(db_policy)
    return {"message": "政策已启用", "id": policy_id, "is_active": True}


@router.post("/{policy_id}/clone", response_model=PolicyResponse, status_code=201)
async def clone_policy(
    policy_id: int,
    new_version: str = Query(..., description="新版本号"),
    db: Session = Depends(get_db)
):
    """
    基于现有政策创建新版本
    """
    source = db.query(Policy).filter(Policy.id == policy_id).first()
    if not source:
        raise HTTPException(status_code=404, detail="源政策不存在")

    # 生成新文号
    new_number = f"{source.policy_number}-v{new_version}"

    # 与 create_policy 同口径：文号唯一，重复克隆返回 400 而非唯一约束 500
    existing = db.query(Policy).filter(Policy.policy_number == new_number).first()
    if existing:
        raise HTTPException(status_code=400, detail=f"版本 '{new_version}' 已存在（政策文号 '{new_number}' 重复）")

    db_policy = Policy(
        policy_number=new_number,
        title=source.title,
        version=new_version,
        effective_date=source.effective_date,
        expiry_date=source.expiry_date,
        content=source.content,
        summary=source.summary,
        policy_type=source.policy_type,
        tags=source.tags,
        is_active=True,
    )
    db.add(db_policy)
    db.commit()
    db.refresh(db_policy)
    return db_policy
