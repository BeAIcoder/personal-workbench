# -*- coding: utf-8 -*-
"""总经理只读经营聚合：按域守卫导入，失败与缺源均不伪造零值。"""
from __future__ import annotations

import importlib
import logging
import re
from calendar import monthrange
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy.orm import Session

from ...business_metrics import METRIC_DICTIONARY, METRIC_SOURCES, MISSING_SOURCE_METRICS
from ...database import SessionLocal

logger = logging.getLogger(__name__)
PERIOD_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
DOMAIN_LABELS = {
    "leasing": "招商",
    "operations": "营运",
    "planning": "企划",
    "legal": "法务",
    "hr": "人事",
    "facility": "工程物业",
}
_MISSING = object()


def _period(value: str = "") -> str:
    result = str(value or "").strip() or date.today().strftime("%Y-%m")
    if not PERIOD_RE.match(result):
        raise ValueError("所属期格式非法，应为 YYYY-MM")
    return result


def _as_of(value=None) -> date:
    if value in (None, ""):
        return date.today()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise ValueError("as_of 格式非法，应为 YYYY-MM-DD") from exc


def _amount(value: Decimal) -> float:
    return float(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _ratio(value: Decimal) -> float:
    return float(value.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))


def _window_end(as_of: date) -> date:
    year = as_of.year + 1
    return date(year, as_of.month, min(as_of.day, monthrange(year, as_of.month)[1]))


def expiry_concentration(db: Session, as_of=None) -> tuple[dict, str | None]:
    """按固定截止日复算履约合同未来 12 个月到期面积占比。"""
    cutoff = _as_of(as_of)
    models = importlib.import_module("app.modules.business_operations.models")
    rows = db.query(models.OpsContract).filter(models.OpsContract.status == "active").all()
    window_end = _window_end(cutoff)
    active_area = sum((Decimal(str(row.area or 0)) for row in rows), Decimal("0"))
    expiring_area = sum(
        (Decimal(str(row.area or 0)) for row in rows
         if row.end_date and cutoff <= row.end_date <= window_end),
        Decimal("0"),
    )
    raw_ratio = expiring_area / active_area if active_area else None
    data = {
        "as_of": cutoff.isoformat(),
        "window_end": window_end.isoformat(),
        "active_area": _amount(active_area),
        "expiring_area": _amount(expiring_area),
        "area_ratio": _ratio(raw_ratio) if raw_ratio is not None else None,
        "warning_threshold": 0.25,
        "warning_threshold_exceeded": raw_ratio is not None and raw_ratio > Decimal("0.25"),
    }
    reason = "履约合同总面积为 0，无法计算 12 个月到期面积占比" if not active_area else None
    return data, reason


def _leasing(db: Session, period: str, as_of: date):
    service = importlib.import_module("app.modules.business_leasing.service")
    return {
        "units": service.unit_summary(db),
        "funnel": service.funnel(db),
        "due_followups": service.leads_due_count(db),
    }, []


def _operations(db: Session, period: str, as_of: date):
    service = importlib.import_module("app.modules.business_operations.service")
    data = service.ops_summary(db, period=period)
    expiry, reason = expiry_concentration(db, as_of=as_of)
    data["expiry_12m"] = expiry
    return data, [reason] if reason else []


def _planning(db: Session, period: str, as_of: date):
    service = importlib.import_module("app.modules.business_planning.service")
    return service.summary(db, year=int(period[:4])), []


def _legal(db: Session, period: str, as_of: date):
    service = importlib.import_module("app.modules.business_legal.service")
    return service.legal_summary(db), []


def _hr(db: Session, period: str, as_of: date):
    service = importlib.import_module("app.modules.business_hr.service")
    return service.hr_summary(db, month=period), []


def _facility(db: Session, period: str, as_of: date):
    try:
        service = importlib.import_module("app.modules.business_facility.service")
    except ModuleNotFoundError as exc:
        if exc.name and exc.name.startswith("app.modules.business_facility"):
            return None, ["工程物业模块尚未提供 facility_summary；Task 9 接入后自动汇总"]
        raise
    summary = getattr(service, "facility_summary", None)
    if not callable(summary):
        return None, ["工程物业模块尚未提供可调用的 facility_summary；当前不生成工程指标"]
    return summary(db, as_of=as_of), []


DOMAIN_LOADERS = {
    "leasing": _leasing,
    "operations": _operations,
    "planning": _planning,
    "legal": _legal,
    "hr": _hr,
    "facility": _facility,
}


def _path_value(data: dict, path: str):
    current = data
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return _MISSING
        current = current[part]
    return current


def _metric_items(domains: dict) -> list[dict]:
    items = []
    for key, (domain, path) in METRIC_SOURCES.items():
        state = domains[domain]
        value = None
        reason = None
        if not state["available"]:
            reason = state["gap_reasons"][0]
        else:
            value = _path_value(state["data"], path)
            if value is _MISSING:
                value = None
                reason = f"来源字段 {path} 缺失，无法计算 {key}"
            elif value is None:
                if key == "operations.expiry_12m_area_ratio" and state["gap_reasons"]:
                    reason = state["gap_reasons"][0]
                else:
                    reason = "来源 summary 当前无同口径可计算数据"
            if reason and reason not in state["gap_reasons"]:
                state["gap_reasons"].append(reason)
        items.append({"key": key, "domain": domain, "value": value, "reason": reason})
    items.extend(dict(item) for item in MISSING_SOURCE_METRICS)
    return items


def _reminders(domains: dict) -> list[dict]:
    operations = domains["operations"]
    if not operations["available"]:
        return []
    expiry = operations["data"]["expiry_12m"]
    if not expiry["warning_threshold_exceeded"]:
        return []
    return [{
        "code": "operations.expiry_concentration",
        "level": "warning",
        "metric_key": "operations.expiry_12m_area_ratio",
        "value": expiry["area_ratio"],
        "message": f"未来 12 个月到期合同面积占比 {expiry['area_ratio']:.2%}，超过 25% 警戒线",
    }]


def build_summary(db: Session, period: str = "", as_of=None) -> dict:
    """聚合各域只读 summary；as_of 可在服务层注入以稳定复算。"""
    normalized_period = _period(period)
    cutoff = _as_of(as_of)
    domains = {}
    for domain, loader in DOMAIN_LOADERS.items():
        try:
            data, gap_reasons = loader(db, normalized_period, cutoff)
            domains[domain] = {
                "available": data is not None,
                "data": data,
                "gap_reasons": list(gap_reasons),
            }
        except Exception as exc:
            logger.exception("GM 域 %s 读取失败，按不可用返回", domain)
            domains[domain] = {
                "available": False,
                "data": None,
                "gap_reasons": [f"{DOMAIN_LABELS[domain]}域读取失败（{type(exc).__name__}），未以 0 顶替"],
            }
    metrics = _metric_items(domains)
    return {
        "period": normalized_period,
        "as_of": cutoff.isoformat(),
        "domains": domains,
        "metrics": metrics,
        "metric_dictionary": dict(METRIC_DICTIONARY),
        "reminders": _reminders(domains),
    }


def gm_summary(period: str = "") -> dict:
    """总经理经营聚合只读 Agent 工具。"""
    with SessionLocal() as db:
        return {"ok": True, **build_summary(db, period=period)}
