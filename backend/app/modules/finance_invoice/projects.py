"""项目归类规则：以发票工具引擎配置为唯一真值来源，供本模块统一归口。

规则来源（运行时只读，不在本仓库复制任何真实名称）：
- 引擎 ``ConfigManager`` 的 ``extraction.address_keywords``（地址/项目关键词）
- ``extraction.address_force_mapping``（关键词 → 项目名的强制映射，如「脱敏单位 → 脱敏单位」）

归类优先级（高 → 低）：
1. 引擎已给出的项目名本身是规则内项目名 → 原样保留；
2. 引擎项目名命中关键词 → 映射后的项目名（地址口径最可靠）；
3. 发票备注命中关键词 → 映射后的项目名（例：项目备注里写明项目名）；
4. 发票全文命中关键词 → 映射后的项目名（仅在前三步都未命中时补取，代价高）；
5. 同批次同购方其余发票的项目唯一 → 继承（红冲票等无地址发票）；
6. 其余 → 「未归类」，由人工在页面修正（人工修正永不被重新归类覆盖）。

关键词按长度倒序匹配（长词优先），避免短词抢先命中；规则为空时一律返回「不变」，
绝不把全部发票清成未归类（引擎缺失/换机时安全降级）。
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Iterable, Optional

from . import engine as engine_mod

logger = logging.getLogger(__name__)

UNCLASSIFIED = "未归类"  # 人工待修正的项目占位

# 归类依据（返回给接口，便于前端/排障说明每张发票为什么这样归类）
SOURCE_PROJECT_NAME = "project_name"
SOURCE_REMARK = "remark"
SOURCE_TEXT = "text"
SOURCE_PEER = "peer"
SOURCE_UNCLASSIFIED = "unclassified"
SOURCE_UNCHANGED = "unchanged"


@dataclass(frozen=True)
class ProjectRule:
    """一条关键词 → 项目 规则。"""

    keyword: str
    project: str


def load_project_rules() -> list[ProjectRule]:
    """读取引擎配置的关键词映射；引擎不可用/无配置返回空列表（调用方安全降级）。"""
    try:
        handle = engine_mod.get_engine()
    except engine_mod.EngineUnavailableError as exc:
        logger.warning("项目归类规则不可用（引擎未挂载）：%s", exc)
        return []
    keywords = handle.config.get("extraction.address_keywords", []) or []
    mapping = handle.config.get("extraction.address_force_mapping", {}) or {}
    if not isinstance(keywords, (list, tuple)):
        return []
    rules: list[ProjectRule] = []
    seen: set[str] = set()
    for raw in keywords:
        keyword = re.sub(r"\s+", "", str(raw or ""))
        if not keyword or keyword in seen:
            continue
        seen.add(keyword)
        project = re.sub(r"\s+", "", str(mapping.get(keyword, keyword) or "")) or keyword
        rules.append(ProjectRule(keyword=keyword, project=project))
    # 长词优先：避免「脱敏单位」被更短的包含词抢先命中
    rules.sort(key=lambda rule: len(rule.keyword), reverse=True)
    return rules


def project_names(rules: Iterable[ProjectRule]) -> set[str]:
    """规则中出现的全部项目名（用于判断引擎项目名是否已是合法项目）。"""
    return {rule.project for rule in rules}


def match_keyword(text: Optional[str], rules: list[ProjectRule]) -> Optional[ProjectRule]:
    """在文本中按长词优先找第一个命中的关键词规则。"""
    if not text:
        return None
    for rule in rules:
        if rule.keyword in text:
            return rule
    return None


def resolve_project(
    *,
    rules: list[ProjectRule],
    project_name: str = "",
    remark: str = "",
    text: str = "",
    peer_projects: Optional[Iterable[str]] = None,
) -> tuple[str, str]:
    """按优先级归类单张发票，返回 (项目名, 依据)。

    ``rules`` 为空表示规则不可用 → 返回 (原项目名, unchanged)，调用方不得据此改写数据。
    """
    original = (project_name or "").strip()
    if not rules:
        return original, SOURCE_UNCHANGED

    known = project_names(rules)
    if original in known:
        return original, SOURCE_PROJECT_NAME

    rule = match_keyword(original, rules)
    if rule is not None:
        return rule.project, SOURCE_PROJECT_NAME

    rule = match_keyword(remark, rules)
    if rule is not None:
        return rule.project, SOURCE_REMARK

    rule = match_keyword(text, rules)
    if rule is not None:
        return rule.project, SOURCE_TEXT

    peers = {p for p in (peer_projects or ()) if p}
    if len(peers) == 1:
        return next(iter(peers)), SOURCE_PEER

    return UNCLASSIFIED, SOURCE_UNCLASSIFIED
