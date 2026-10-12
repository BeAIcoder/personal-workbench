"""
教材考纲对照路由
提供教材知识点查询和系统功能对照API

注意：/{textbook_name} 动态路由必须保持在所有静态路径（/mappings、/full-reference 等）之后，
否则会拦截静态路径请求（FastAPI 按注册顺序匹配）。
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from fastapi import APIRouter, HTTPException
from typing import List, Optional

from ..services.textbook_reference import (
    get_textbook_references,
    get_textbook_by_name,
    get_system_mappings,
    get_knowledge_point_by_id,
    TextbookChapter,
    SystemFunctionMapping,
    KnowledgePoint,
)

router = APIRouter(prefix="/textbook-reference", tags=["教材考纲对照"])


@router.get("/", response_model=List[dict])
async def get_all_textbooks():
    """获取所有教材考纲参考列表"""
    textbooks = get_textbook_references()
    return [
        {
            "textbook_name": t.textbook_name,
            "chapter": t.chapter,
            "chapter_number": t.chapter_number,
            "ability_overview": t.ability_overview,
            "knowledge_point_count": len(t.knowledge_points),
            "key_difficulties": t.key_difficulties,
            "exam_frequency": t.exam_frequency,
        }
        for t in textbooks
    ]


@router.get("/mappings", response_model=List[dict])
async def get_system_function_mappings():
    """获取系统功能与教材知识点的对照映射"""
    mappings = get_system_mappings()
    return [m.model_dump() for m in mappings]


@router.get("/knowledge-points/{kp_id}", response_model=dict)
async def get_knowledge_point_detail(kp_id: str):
    """获取指定知识点的详细信息"""
    kp = get_knowledge_point_by_id(kp_id)
    if not kp:
        raise HTTPException(status_code=404, detail="知识点不存在")
    return kp.model_dump()


@router.get("/full-reference", response_model=dict)
async def get_full_reference():
    """获取完整的教材考纲对照数据（含所有教材和系统映射）"""
    textbooks = get_textbook_references()
    mappings = get_system_mappings()

    return {
        "textbooks": [t.model_dump() for t in textbooks],
        "system_mappings": [m.model_dump() for m in mappings],
        "statistics": {
            "total_textbooks": len(textbooks),
            "total_knowledge_points": sum(len(t.knowledge_points) for t in textbooks),
            "total_system_mappings": len(mappings),
            "by_ability_requirement": _count_by_ability(textbooks),
        },
    }


def _count_by_ability(textbooks: List[TextbookChapter]) -> dict:
    """按能力要求统计知识点数量"""
    counts = {"掌握": 0, "熟悉": 0, "了解": 0}
    for textbook in textbooks:
        for kp in textbook.knowledge_points:
            if kp.ability_requirement in counts:
                counts[kp.ability_requirement] += 1
    return counts


@router.get("/{textbook_name}", response_model=dict)
async def get_textbook_detail(textbook_name: str):
    """获取指定教材的详细考纲信息"""
    textbook = get_textbook_by_name(textbook_name)
    if not textbook:
        raise HTTPException(status_code=404, detail="教材不存在")
    return textbook.model_dump()
