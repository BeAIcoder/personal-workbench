"""finance_invoice 项目归类：入库归类两遍判定与按当前规则重算。

规则本体在 projects.py（引擎 config.local.yaml 的地址关键词/强制映射）；
本模块只负责「把规则应用到批次明细」，含同批同购方继承与人工修正保护。
"""
from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy.orm import Session

from . import projects as project_rules
from .invoice_parse import _ACTIVE_ITEM_STATUS, _pdf_text
from .models import InvoiceBatch, InvoiceItem

logger = logging.getLogger(__name__)


def classify_items(
    entries: list[tuple[InvoiceItem, str]],
    rules: list[project_rules.ProjectRule],
    text_loader=None,
    seed_peers: Optional[dict[str, set[str]]] = None,
) -> dict[InvoiceItem, tuple[str, str]]:
    """两遍归类：先按项目名/备注/全文判定，再用同批同购方（唯一项目）补齐。

    entries: [(明细, 全文)]，全文可为空（为空时经 text_loader 懒加载）；
    seed_peers: 预先已知的购方→项目集合（人工修正过的发票，作为继承证据）；
    返回 {明细对象: (项目名, 依据)}。rules 为空时全部返回「不变」。
    """
    peers: dict[str, set[str]] = {buyer: set(projects) for buyer, projects in (seed_peers or {}).items()}
    resolved: dict[InvoiceItem, tuple[str, str]] = {}
    pending: list[InvoiceItem] = []
    for item, text in entries:
        project, source = project_rules.resolve_project(
            rules=rules, project_name=item.project_name, remark=item.remark, text=text
        )
        if source == project_rules.SOURCE_UNCLASSIFIED and not text and text_loader is not None:
            extra = text_loader(item) or ""
            if extra:
                project, source = project_rules.resolve_project(
                    rules=rules, project_name=item.project_name, remark=item.remark, text=extra
                )
        if source == project_rules.SOURCE_UNCLASSIFIED:
            pending.append(item)
            continue
        resolved[item] = (project, source)
        if project:
            peers.setdefault(item.buyer_name, set()).add(project)

    for item in pending:
        project, source = project_rules.resolve_project(
            rules=rules, peer_projects=peers.get(item.buyer_name, set())
        )
        resolved[item] = (project, source)
    return resolved


def reclassify_batch(db: Session, batch: InvoiceBatch) -> dict:
    """按当前规则重算批次内项目归类；人工修正（corrected）的明细永不覆盖。

    已导出批次会回落 parsed 并清空 export_file：旧 ZIP 与旧归类不一致，须重新导出。
    规则不可用（引擎/配置缺失）抛 ValueError，不改动任何数据。
    """
    # 循环依赖：batch/export 反向依赖本模块，产物清理与项目汇总只在调用期回取
    from .invoice_batch import _remove_export_artifacts, project_summary

    rules = project_rules.load_project_rules()
    if not rules:
        raise ValueError("项目归类规则不可用：请确认发票解析引擎及其配置可读取")
    items = (
        db.query(InvoiceItem)
        .filter(InvoiceItem.batch_id == batch.id, InvoiceItem.status.in_(_ACTIVE_ITEM_STATUS))
        .order_by(InvoiceItem.id.asc())
        .all()
    )
    manual = [item for item in items if item.corrected]
    targets = [item for item in items if not item.corrected]
    seed: dict[str, set[str]] = {}
    for item in manual:
        if item.project_name:
            seed.setdefault(item.buyer_name, set()).add(item.project_name)

    resolved = classify_items([(item, "") for item in targets], rules, text_loader=_pdf_text, seed_peers=seed)

    changed = 0
    sources: dict[str, int] = {}
    for item in targets:
        project, source = resolved[item]
        sources[source] = sources.get(source, 0) + 1
        if (item.project_name or "") != project:
            item.project_name = project[:100]
            changed += 1
    if batch.status == InvoiceBatch.STATUS_EXPORTED:
        export_file = batch.export_file or ""
        batch.status = InvoiceBatch.STATUS_PARSED
        batch.export_file = ""
    else:
        export_file = ""
    db.commit()
    db.refresh(batch)
    if export_file:
        _remove_export_artifacts(batch.id, export_file)
    summary = project_summary(db, batch)
    unclassified = sum(
        1 for item in items if (item.project_name or "").strip() in ("", project_rules.UNCLASSIFIED)
    )
    return {
        "ok": True,
        "batch_id": batch.id,
        "status": batch.status,
        "changed": changed,
        "unchanged": len(targets) - changed,
        "manual_kept": len(manual),
        "unclassified": unclassified,
        "sources": sources,
        "projects": summary,
    }
