# -*- coding: utf-8 -*-
"""总经理只读经营聚合：按域守卫导入，失败与缺源均不伪造零值。

各经营域通过 ``register_provider`` 显式注册 ``DomainProvider``（导入路径 +
汇总口径），``build_summary`` 只遍历注册表，不再为每个域写死分支；新增域时
只追加一条注册，不改主流程。
"""
from __future__ import annotations

import importlib
import logging
import re
from calendar import monthrange
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Callable

from sqlalchemy.orm import Session

from ...business_metrics import METRIC_DICTIONARY, METRIC_SOURCES, MISSING_SOURCE_METRICS
from ...database import SessionLocal

logger = logging.getLogger(__name__)
PERIOD_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
_MISSING = object()

DomainBuilder = Callable[[Any, Session, str, date], "tuple[dict | None, list[str]]"]


@dataclass(frozen=True)
class DomainProvider:
    """一个经营域的只读聚合入口：导入哪个 service、按什么口径汇总。"""

    domain: str
    label: str
    module: str
    builder: DomainBuilder
    optional: bool = False
    missing_reason: str | None = None


_REGISTRY: dict[str, DomainProvider] = {}
DOMAIN_LABELS: dict[str, str] = {}


def register_provider(provider: DomainProvider, *, replace: bool = False) -> DomainProvider:
    """注册经营域；重复注册必须显式 ``replace=True``，避免静默覆盖。"""
    if provider.domain in _REGISTRY and not replace:
        raise ValueError(f"经营域 {provider.domain} 已注册；如需替换请显式 replace=True")
    _REGISTRY[provider.domain] = provider
    DOMAIN_LABELS[provider.domain] = provider.label
    return provider


def registered_providers() -> tuple[DomainProvider, ...]:
    """当前已注册域（保持注册顺序，决定 summary 的 domains 顺序）。"""
    return tuple(_REGISTRY.values())


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


def _leasing(service, db: Session, period: str, as_of: date):
    return {
        "units": service.unit_summary(db),
        "funnel": service.funnel(db),
        "due_followups": service.leads_due_count(db),
    }, []


def _operations(service, db: Session, period: str, as_of: date):
    data = service.ops_summary(db, period=period)
    expiry, reason = expiry_concentration(db, as_of=as_of)
    data["expiry_12m"] = expiry
    return data, [reason] if reason else []


def _planning(service, db: Session, period: str, as_of: date):
    return service.summary(db, year=int(period[:4])), []


def _legal(service, db: Session, period: str, as_of: date):
    return service.legal_summary(db), []


def _hr(service, db: Session, period: str, as_of: date):
    return service.hr_summary(db, month=period), []


def _facility(service, db: Session, period: str, as_of: date):
    summary = getattr(service, "facility_summary", None)
    if not callable(summary):
        return None, ["工程物业模块尚未提供可调用的 facility_summary；当前不生成工程指标"]
    return summary(db, as_of=as_of), []


for _provider in (
    DomainProvider(
        domain="leasing", label="招商",
        module="app.modules.business_leasing.service", builder=_leasing,
    ),
    DomainProvider(
        domain="operations", label="营运",
        module="app.modules.business_operations.service", builder=_operations,
    ),
    DomainProvider(
        domain="planning", label="企划",
        module="app.modules.business_planning.service", builder=_planning,
    ),
    DomainProvider(
        domain="legal", label="法务",
        module="app.modules.business_legal.service", builder=_legal,
    ),
    DomainProvider(
        domain="hr", label="人事",
        module="app.modules.business_hr.service", builder=_hr,
    ),
    DomainProvider(
        domain="facility", label="工程物业",
        module="app.modules.business_facility.service", builder=_facility,
        optional=True,
        missing_reason="工程物业模块尚未提供 facility_summary；Task 9 接入后自动汇总",
    ),
):
    register_provider(_provider)
del _provider


def _is_missing_own_module(exc: ModuleNotFoundError, module: str) -> bool:
    """只在“该域自己的包缺失”时降级；缺第三方依赖仍照常抛错。"""
    package = module.rsplit(".", 1)[0]
    name = exc.name or ""
    return bool(name) and (name == module or name == package or name.startswith(package + "."))


def _load_domain(provider: DomainProvider, db: Session, period: str, as_of: date):
    try:
        service = importlib.import_module(provider.module)
    except ModuleNotFoundError as exc:
        if not (provider.optional and _is_missing_own_module(exc, provider.module)):
            raise
        return None, [provider.missing_reason or f"{provider.label}模块尚未接入"]
    return provider.builder(service, db, period, as_of)


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
    for provider in registered_providers():
        try:
            data, gap_reasons = _load_domain(provider, db, normalized_period, cutoff)
            domains[provider.domain] = {
                "available": data is not None,
                "data": data,
                "gap_reasons": list(gap_reasons),
            }
        except Exception as exc:
            logger.exception("GM 域 %s 读取失败，按不可用返回", provider.domain)
            domains[provider.domain] = {
                "available": False,
                "data": None,
                "gap_reasons": [
                    f"{provider.label}域读取失败（{type(exc).__name__}），未以 0 顶替",
                ],
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
