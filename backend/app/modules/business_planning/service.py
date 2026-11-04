# -*- coding: utf-8 -*-
"""企划管理服务：活动台账、预算明细、复盘指标、供应商、日历与导出。"""
from __future__ import annotations

import io
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy.orm import Session

from ...database import SessionLocal
from ...utils_xlsx import safe_cell
from .models import PlanCampaign, PlanVendor

CATEGORIES = ("festival", "promo", "brand", "community")
CATEGORY_LABELS = {
    "festival": "节庆", "promo": "促销", "brand": "品牌", "community": "社区",
}
STATUSES = ("planning", "approved", "running", "done", "cancelled")
STATUS_LABELS = {
    "planning": "策划中", "approved": "已立项", "running": "进行中",
    "done": "已结束", "cancelled": "已取消",
}
VENDOR_SERVICES = ("物料", "美陈", "演艺", "投放", "执行")


class ConflictError(ValueError):
    pass


def money(value) -> float:
    return float(Decimal(str(value or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _optional_money(value) -> float | None:
    return None if value is None else money(value)


def _decimal_or_none(value) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        result = Decimal(str(value))
    except (TypeError, ValueError, ArithmeticError):
        return None
    return result if result.is_finite() else None


def _optional_number(value, field: str) -> Decimal | None:
    if value in (None, ""):
        return None
    result = _decimal_or_none(value)
    if result is None:
        raise ValueError(f"{field}必须是有效数字")
    return result


def _roi_number(value: Decimal) -> float:
    return float(value.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))


def _raw_calculated_roi(c: PlanCampaign) -> Decimal | None:
    baseline = _decimal_or_none(c.sales_baseline)
    during = _decimal_or_none(c.sales_during)
    share = _decimal_or_none(c.merchant_share)
    if any(value is None for value in (baseline, during, share)):
        return None
    total = Decimal(str(c.actual_total or 0)) + share
    if total == 0:
        return None
    return (during - baseline) / total


def _roi_details(c: PlanCampaign) -> dict:
    metrics = c.metrics or {}
    baseline = _decimal_or_none(c.sales_baseline)
    during = _decimal_or_none(c.sales_during)
    share = _decimal_or_none(c.merchant_share)
    target = _decimal_or_none(c.roi_target)
    warning = None
    if all(value is not None for value in (baseline, during, share)):
        source = "calculated"
        raw_roi = _raw_calculated_roi(c)
        if raw_roi is None:
            warning = "总投入为 0（实际投入 + 商户分摊），无法计算 ROI"
        roi = _roi_number(raw_roi) if raw_roi is not None else None
    else:
        manual = metrics.get("roi")
        raw_roi = _decimal_or_none(manual)
        roi = manual if manual not in (None, "") else None
        source = "manual" if roi is not None else "none"
    return {
        "roi": roi,
        "roi_source": source,
        "roi_warning": warning,
        "target_met": None if target is None or raw_roi is None else raw_roi >= target,
    }


def _as_date(value, field: str = "日期") -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise ValueError(f"{field}格式非法，应为 YYYY-MM-DD") from exc


def _normalize_items(items) -> tuple[list[dict], float, float]:
    if items is None:
        return [], 0.0, 0.0
    if not isinstance(items, list):
        raise ValueError("预算明细必须是列表")
    out: list[dict] = []
    budget_total = Decimal("0")
    actual_total = Decimal("0")
    for idx, item in enumerate(items, 1):
        if not isinstance(item, dict):
            raise ValueError(f"第 {idx} 条预算明细格式非法")
        name = str(item.get("item") or "").strip()
        if not name:
            raise ValueError(f"第 {idx} 条预算科目为空")
        budget = money(item.get("budget"))
        actual = money(item.get("actual"))
        if budget < 0 or actual < 0:
            raise ValueError(f"第 {idx} 条预算/实际不能为负数")
        out.append({"item": name[:100], "budget": budget, "actual": actual})
        budget_total += Decimal(str(budget))
        actual_total += Decimal(str(actual))
    return out, money(budget_total), money(actual_total)


def campaign_to_dict(c: PlanCampaign) -> dict:
    metrics = c.metrics or {}
    return {
        "id": c.id,
        "name": c.name,
        "year": c.year,
        "category": c.category,
        "category_label": CATEGORY_LABELS.get(c.category, c.category),
        "start_date": c.start_date.isoformat() if c.start_date else None,
        "end_date": c.end_date.isoformat() if c.end_date else None,
        "owner": c.owner or "",
        "channel": c.channel or "",
        "target": c.target or "",
        "status": c.status,
        "status_label": STATUS_LABELS.get(c.status, c.status),
        "budget_items": c.budget_items or [],
        "budget_total": money(c.budget_total),
        "actual_total": money(c.actual_total),
        "over_budget": bool(money(c.actual_total) > money(c.budget_total) + 0.005),
        "variance": money(money(c.budget_total) - money(c.actual_total)),
        "metrics": metrics,
        "grade": c.grade,
        "sales_baseline": _optional_money(c.sales_baseline),
        "sales_during": _optional_money(c.sales_during),
        "merchant_share": _optional_money(c.merchant_share),
        "roi_target": None if c.roi_target is None else float(c.roi_target),
        **_roi_details(c),
        "note": c.note or "",
        "created_at": str(c.created_at),
    }


def campaign_list(db: Session, year: int | None = None, status: str = "",
                  category: str = "", keyword: str = "",
                  page: int = 1, page_size: int = 20) -> dict:
    q = db.query(PlanCampaign)
    if year:
        q = q.filter(PlanCampaign.year == year)
    if status:
        q = q.filter(PlanCampaign.status == status)
    if category:
        q = q.filter(PlanCampaign.category == category)
    if keyword:
        q = q.filter(PlanCampaign.name.contains(keyword, autoescape=True))
    total = q.count()
    rows = (q.order_by(PlanCampaign.start_date.desc(), PlanCampaign.id.desc())
            .offset((page - 1) * page_size).limit(page_size).all())
    return {"total": total, "items": [campaign_to_dict(c) for c in rows],
            "page": page, "page_size": page_size}


def campaign_save(db: Session, data: dict, campaign_id: int | None = None) -> dict:
    c = db.get(PlanCampaign, campaign_id) if campaign_id else PlanCampaign()
    if c is None:
        raise LookupError("活动不存在")
    for field in ("name", "owner", "channel", "target", "note"):
        if field in data and data[field] is not None:
            setattr(c, field, str(data[field]).strip())
    if "year" in data and data["year"] is not None:
        year = int(data["year"])
        if not (2000 <= year <= 2100):
            raise ValueError("年度非法（2000-2100）")
        c.year = year
    if "category" in data and data["category"] is not None:
        if data["category"] not in CATEGORIES:
            raise ValueError(f"未知活动类型：{data['category']}")
        c.category = data["category"]
    if "status" in data and data["status"] is not None:
        if data["status"] not in STATUSES:
            raise ValueError(f"未知活动状态：{data['status']}")
        c.status = data["status"]
    if "start_date" in data:
        c.start_date = _as_date(data["start_date"], "开始日期")
    if "end_date" in data:
        c.end_date = _as_date(data["end_date"], "结束日期")
    if "budget_items" in data:
        items, budget_total, actual_total = _normalize_items(data["budget_items"])
        c.budget_items = items
        c.budget_total = budget_total
        c.actual_total = actual_total
    if "metrics" in data and data["metrics"] is not None:
        metrics = data["metrics"]
        if not isinstance(metrics, dict):
            raise ValueError("复盘指标必须是对象")
        normalized = {}
        for key in ("footfall_lift", "sales_lift", "roi"):
            if metrics.get(key) in (None, ""):
                normalized[key] = None
            else:
                try:
                    normalized[key] = money(metrics[key])
                except (TypeError, ValueError) as exc:
                    raise ValueError(f"复盘指标 {key} 必须是数字") from exc
        normalized["review"] = str(metrics.get("review") or "").strip()[:2000]
        c.metrics = normalized
    if "grade" in data:
        grade = data["grade"]
        c.grade = None if grade is None else str(grade).strip()[:20] or None
    for field, label in (
        ("sales_baseline", "基准销售"),
        ("sales_during", "活动期间销售"),
        ("merchant_share", "商户分摊"),
        ("roi_target", "ROI 目标"),
    ):
        if field not in data:
            continue
        value = _optional_number(data[field], label)
        if field != "roi_target" and value is not None and value < 0:
            raise ValueError(f"{label}不能为负数")
        if value is None:
            setattr(c, field, None)
        elif field == "roi_target":
            c.roi_target = float(value)
        else:
            setattr(c, field, money(value))
    if not c.name:
        raise ValueError("活动名称不能为空")
    if c.start_date is None or c.end_date is None:
        raise ValueError("活动起止日期不能为空")
    if c.end_date < c.start_date:
        raise ValueError("活动结束日期不能早于开始日期")
    if c.year is None:
        c.year = c.start_date.year
    elif any(key in data for key in ("year", "start_date", "end_date")) and not (
            c.start_date.year <= c.year <= c.end_date.year):
        # 年度决定预算归属与汇总口径，必须落在档期覆盖的年份内（日历按年度取数）。
        # 只有本次提交触及年度/档期时才校验，避免历史库里的错位行连备注都改不了。
        raise ValueError(
            f"活动年度 {c.year} 与档期 {c.start_date.isoformat()}~{c.end_date.isoformat()} 不一致；"
            "年度需落在档期覆盖年份内（如跨年活动填结束年份）"
        )
    if c.status != "cancelled" and data.get("allow_conflict") is not True:
        q = db.query(PlanCampaign).filter(
            PlanCampaign.status != "cancelled",
            PlanCampaign.start_date <= c.end_date,
            PlanCampaign.end_date >= c.start_date,
        )
        if campaign_id is not None:
            q = q.filter(PlanCampaign.id != campaign_id)
        names = [row.name for row in q.order_by(PlanCampaign.start_date, PlanCampaign.id).all()]
        if names:
            raise ConflictError(
                f"档期冲突：{'、'.join(names)}；如确认有意并行，请设置 allow_conflict=true"
            )
    if campaign_id is None:
        db.add(c)
    db.commit()
    db.refresh(c)
    return campaign_to_dict(c)


def campaign_delete(db: Session, campaign_id: int) -> None:
    c = db.get(PlanCampaign, campaign_id)
    if c is None:
        raise LookupError("活动不存在")
    db.delete(c)
    db.commit()


def calendar(db: Session, year: int) -> dict:
    year_start = date(year, 1, 1)
    year_end = date(year, 12, 31)
    # 与 summary(year) 同口径：按活动年度取数，避免跨年活动在日历里出现、汇总里缺席
    rows = (db.query(PlanCampaign).filter(
        PlanCampaign.year == year,
        PlanCampaign.start_date <= year_end,
        PlanCampaign.end_date >= year_start,
    ).order_by(PlanCampaign.start_date, PlanCampaign.id).all())
    months = {m: [] for m in range(1, 13)}
    for c in rows:
        item = {
            "id": c.id, "name": c.name, "start_date": c.start_date.isoformat(),
            "end_date": c.end_date.isoformat(), "status": c.status,
            "status_label": STATUS_LABELS.get(c.status, c.status),
            "budget_total": money(c.budget_total),
        }
        first = max(c.start_date, year_start)
        last = min(c.end_date, year_end)
        for month in range(first.month, last.month + 1):
            months[month].append(dict(item))
    return {"year": year,
            "months": [{"month": m, "items": months[m]} for m in range(1, 13)]}


def summary(db: Session, year: int | None = None) -> dict:
    q = db.query(PlanCampaign)
    if year:
        q = q.filter(PlanCampaign.year == year)
    rows = q.all()
    budget = money(sum(float(c.budget_total or 0) for c in rows))
    actual = money(sum(float(c.actual_total or 0) for c in rows))
    over = [c for c in rows if money(c.actual_total) > money(c.budget_total) + 0.005]
    details = [_roi_details(c) for c in rows]
    rois = []
    for c, item in zip(rows, details):
        if item["roi_source"] != "calculated":
            continue
        raw_roi = _raw_calculated_roi(c)
        if raw_roi is not None:
            rois.append(raw_roi)
    manual_roi_count = sum(
        1 for item in details if item["roi_source"] == "manual" and item["roi"] is not None
    )
    return {
        "year": year,
        "campaigns": len(rows),
        "budget_total": budget,
        "actual_total": actual,
        "variance": money(budget - actual),
        "over_budget_count": len(over),
        "over_budget_names": [c.name for c in over][:10],
        "running": sum(1 for c in rows if c.status == "running"),
        "done": sum(1 for c in rows if c.status == "done"),
        "avg_roi": _roi_number(sum(rois, Decimal("0")) / len(rois)) if rois else None,
        "manual_roi_count": manual_roi_count,
        "by_status": [
            {"status": s, "label": STATUS_LABELS[s],
             "count": sum(1 for c in rows if c.status == s)}
            for s in STATUSES
        ],
    }


# ---------------- 供应商 ----------------

def vendor_to_dict(v: PlanVendor) -> dict:
    return {
        "id": v.id, "name": v.name, "service": v.service,
        "contact": v.contact or "", "phone": v.phone or "",
        "rating": int(v.rating or 0), "note": v.note or "",
        "created_at": str(v.created_at),
    }


def vendor_list(db: Session, service: str = "", keyword: str = "") -> list[dict]:
    q = db.query(PlanVendor)
    if service:
        q = q.filter(PlanVendor.service == service)
    if keyword:
        q = q.filter(PlanVendor.name.contains(keyword, autoescape=True))
    return [vendor_to_dict(v) for v in q.order_by(PlanVendor.rating.desc(),
                                                  PlanVendor.id.desc()).all()]


def vendor_save(db: Session, data: dict, vendor_id: int | None = None) -> dict:
    v = db.get(PlanVendor, vendor_id) if vendor_id else PlanVendor()
    if v is None:
        raise LookupError("供应商不存在")
    for field in ("name", "contact", "phone", "note"):
        if field in data and data[field] is not None:
            setattr(v, field, str(data[field]).strip())
    if "service" in data and data["service"] is not None:
        if data["service"] not in VENDOR_SERVICES:
            raise ValueError(f"未知服务类型：{data['service']}")
        v.service = data["service"]
    if "rating" in data and data["rating"] is not None:
        rating = int(data["rating"])
        if not (1 <= rating <= 5):
            raise ValueError("评分须在 1-5 之间")
        v.rating = rating
    if not v.name:
        raise ValueError("供应商名称不能为空")
    if vendor_id is None:
        db.add(v)
    db.commit()
    db.refresh(v)
    return vendor_to_dict(v)


def vendor_delete(db: Session, vendor_id: int) -> None:
    v = db.get(PlanVendor, vendor_id)
    if v is None:
        raise LookupError("供应商不存在")
    db.delete(v)
    db.commit()


# ---------------- 导出 / 工具 ----------------

def campaigns_export(db: Session, year: int | None = None) -> bytes:
    from openpyxl import Workbook

    q = db.query(PlanCampaign)
    if year:
        q = q.filter(PlanCampaign.year == year)
    rows = q.order_by(PlanCampaign.start_date.desc()).all()

    wb = Workbook()
    ws = wb.active
    ws.title = "企划活动"
    ws.append([
        "年度", "活动名称", "类型", "开始日期", "结束日期", "负责人", "渠道", "状态",
        "预算合计", "实际合计", "差异", "等级", "基准销售", "活动期间销售", "商户分摊",
        "ROI目标", "ROI", "手填ROI", "ROI来源", "目标达成", "ROI警告", "复盘结论",
    ])
    for c in rows:
        details = _roi_details(c)
        target_met = details["target_met"]
        ws.append([safe_cell(v) for v in [
            c.year, c.name, CATEGORY_LABELS.get(c.category, c.category),
            c.start_date.isoformat() if c.start_date else "",
            c.end_date.isoformat() if c.end_date else "",
            c.owner, c.channel, STATUS_LABELS.get(c.status, c.status),
            money(c.budget_total), money(c.actual_total),
            money(money(c.budget_total) - money(c.actual_total)),
            c.grade or "", _optional_money(c.sales_baseline), _optional_money(c.sales_during),
            _optional_money(c.merchant_share), c.roi_target, details["roi"],
            (c.metrics or {}).get("roi"), details["roi_source"],
            "是" if target_met is True else "否" if target_met is False else "",
            details["roi_warning"] or "", (c.metrics or {}).get("review", ""),
        ]])
    ws.freeze_panes = "A2"

    methodology = wb.create_sheet("口径说明")
    methodology.append(["项目", "说明"])
    for item, text in (
        ("统一 ROI 公式", "(活动期间销售 - 基准销售) / (实际投入 + 商户分摊)"),
        ("输入要求", "基准销售、活动期间销售、商户分摊三项齐全时由服务端计算"),
        ("零投入", "总投入为 0 时 ROI 留空并返回警告"),
        ("历史手填", "手填 ROI 不参与平均，仅单列计数"),
        ("目标与等级", "未设 ROI 目标时不判定达成；等级仅人工记录，不自动套阈值"),
        ("汇总", "平均 ROI 仅汇总 calculated 且 ROI 非空的活动"),
    ):
        methodology.append([item, text])
    methodology.freeze_panes = "A2"

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def planning_summary_tool() -> dict:
    """企划看板摘要（只读）：年度预算/实际/超支/平均 ROI。"""
    with SessionLocal() as db:
        return {"ok": True, **summary(db)}
