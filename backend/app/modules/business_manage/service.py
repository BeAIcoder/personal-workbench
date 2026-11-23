# -*- coding: utf-8 -*-
"""经营管理模块服务层：批次 CRUD、预览缓存、确认入库、总览序列与导出。"""
from __future__ import annotations

import io
import json
import secrets
import time
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from openpyxl import Workbook
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ...utils_xlsx import safe_cell
from . import fields, importer
from .models import MgMetric, MgRecord, MgReport, MgText

PREVIEW_TTL = 1800
PREVIEW_MAX = 32
MAX_PAGE_SIZE = 200
PREVIEWS: dict[str, dict] = {}


class ImportConflict(ValueError):
    """导入确认撞唯一约束：路由层映射 409，区别于普通校验 400。"""


RECORD_ALLOWED = set(fields.RECORD_SPECS)


# ---------------- 校验 ----------------

def _period(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    try:
        year, month = text.split("-")
        if len(year) != 4 or not year.isdigit() or not 1 <= int(month) <= 12:
            raise ValueError
        return f"{int(year):04d}-{int(month):02d}"
    except (ValueError, AttributeError):
        raise ValueError("期间格式应为 YYYY-MM（如 2026-10）") from None


def _period_bounds(period: str) -> tuple[date, date]:
    year, month = (int(part) for part in period.split("-"))
    start = date(year, month, 1)
    end = date(year + (month == 12), month % 12 + 1, 1) - timedelta(days=1)
    return start, end


def _num(value: Any, label: str, limit: float = 1e12) -> float | None:
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{label} 必须是数字") from None
    if abs(number) > limit:
        raise ValueError(f"{label} 超出允许范围")
    return round(number, 6)


def _money(value: Any) -> float:
    """金额聚合只在最终结果取整：Decimal + ROUND_HALF_UP 两位，避免逐条 half-even 累积偏差。"""
    return float(Decimal(str(value or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _check_ratio(value: Any, label: str) -> float | None:
    number = _num(value, label, limit=10)
    if number is not None and not 0 <= number <= 5:
        raise ValueError(f"{label} 应在 0–5 之间（比率用小数，如 0.735）")
    return number


def _day(value: Any, label: str) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        raise ValueError(f"{label} 应为 YYYY-MM-DD") from None


def _clean_report_type(value: str) -> str:
    text = (value or "").strip()
    if text not in fields.METRIC_PREFIXES:
        raise ValueError("报表类型应为 control / weekly / monthly")
    return text


def _existing(db: Session, report_type: str, project: str, period: str, start: date) -> MgReport | None:
    """按「类型 + 项目 + 周期起始日」定位同粒度批次。

    周报的手工批次只有月初边界，导入批次用实际周起止；若只比 period_start，
    同一周会出现两条批次。这里先按起始日精确匹配，再为周报回退到**月初占位批**
    （source_file 为空且 period_start == 当月 1 日）：手工显式指定其他周的批次
    不能被另一周导入静默覆盖。
    """
    row = (
        db.query(MgReport)
        .filter(
            MgReport.report_type == report_type,
            MgReport.project == project,
            MgReport.period_start == start,
        )
        .first()
    )
    if row is None and report_type == "weekly":
        month_start = _period_bounds(period)[0]
        row = (
            db.query(MgReport)
            .filter(
                MgReport.report_type == report_type,
                MgReport.project == project,
                MgReport.period == period,
                MgReport.period_start == month_start,
                MgReport.source_file == "",
            )
            .order_by(MgReport.id)
            .first()
        )
    return row


# ---------------- 预览与确认 ----------------

def _prune_previews() -> None:
    now = time.time()
    for token in [key for key, item in PREVIEWS.items() if now - item["at"] > PREVIEW_TTL]:
        PREVIEWS.pop(token, None)
    while len(PREVIEWS) > PREVIEW_MAX:
        PREVIEWS.pop(next(iter(PREVIEWS)))


def preview(db: Session, report_type: str, filename: str, content: bytes) -> dict:
    report_type = _clean_report_type(report_type)
    parsed = importer.parse(report_type, filename, content)
    if not (parsed.get("metrics") or parsed.get("records") or parsed.get("texts")):
        raise ValueError(
            "未识别到可导入内容：请确认页签与报表类型一致（内控 / 周报 / 月度），并使用对应模板"
        )
    period = _period(parsed.get("period") or "")
    start = parsed.get("period_start")
    end = parsed.get("period_end")
    if period and (not start or not end):
        start, end = _period_bounds(period)
    if not period:
        period = datetime.now().strftime("%Y-%m")
        start, end = _period_bounds(period)
    project = parsed.get("project") or "云南区域"
    existing = _existing(db, report_type, project, period, start)
    token = secrets.token_urlsafe(18)
    _prune_previews()
    PREVIEWS[token] = {
        "at": time.time(),
        "report_type": report_type,
        "parsed": parsed,
        "filename": filename,
    }
    sections = _section_counts(parsed)
    return {
        "token": token,
        "report_type": report_type,
        "project": project,
        "period": period,
        "period_start": start.isoformat(),
        "period_end": end.isoformat(),
        "title": parsed.get("title") or "",
        "source_file": filename,
        "sections": sections,
        "warnings": list(parsed.get("warnings") or []),
        "replaces_report_id": existing.id if existing else None,
        "sample": {
            "metrics": parsed.get("metrics", [])[:10],
            "records": parsed.get("records", [])[:10],
            "texts": parsed.get("texts", [])[:5],
        },
    }


def _section_counts(parsed: dict) -> list[dict]:
    counts: dict[tuple[str, str], int] = {}
    labels: dict[str, str] = {}
    for kind, items in (
        ("metric", parsed.get("metrics", [])),
        ("record", parsed.get("records", [])),
        ("text", parsed.get("texts", [])),
    ):
        for item in items:
            section = item.get("section") or ""
            counts[(kind, section)] = counts.get((kind, section), 0) + 1
            if kind == "record":
                labels[section] = fields.RECORD_SPECS.get(section, {}).get("label", section)
            else:
                labels[section] = fields.SECTION_LABELS.get(section, section)
    return [
        {"section": section, "kind": kind, "count": count,
         "label": labels.get(section) or section}
        for (kind, section), count in counts.items()
    ]


def confirm(db: Session, token: str, project: str = "", period: str = "") -> dict:
    item = PREVIEWS.get(token)
    if item is None:
        raise ValueError("预览已过期，请重新上传")
    parsed = item["parsed"]
    report_type = item["report_type"]
    project = (project or parsed.get("project") or "云南区域").strip()
    period = _period(period or parsed.get("period") or "")
    if not period:
        raise ValueError("缺少报表期间（YYYY-MM）")
    start = parsed.get("period_start")
    end = parsed.get("period_end")
    if parsed.get("period") != period or not start or not end:
        start, end = _period_bounds(period)
    warnings = list(parsed.get("warnings") or [])

    try:
        old = _existing(db, report_type, project, period, start)
        replaced = old is not None
        if old is not None:
            db.query(MgMetric).filter(MgMetric.report_id == old.id).delete()
            db.query(MgRecord).filter(MgRecord.report_id == old.id).delete()
            db.query(MgText).filter(MgText.report_id == old.id).delete()
            db.delete(old)
            db.flush()

        report = MgReport(
            report_type=report_type,
            project=project,
            period=period,
            period_start=start,
            period_end=end,
            title=parsed.get("title") or item["filename"],
            source_file=item["filename"],
            imported_at=datetime.now(),
        )
        db.add(report)
        db.flush()

        metrics = _insert_metrics(db, report.id, report_type, parsed.get("metrics", []), warnings)
        records = _insert_records(db, report.id, parsed.get("records", []), warnings)
        texts = _insert_texts(db, report.id, parsed.get("texts", []))
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ImportConflict(
            "同类型、同项目、同周期的报表批次已存在（可能由并发导入产生）；"
            "请刷新后重试，重试会整批覆盖"
        ) from exc
    except Exception:
        db.rollback()
        raise
    PREVIEWS.pop(token, None)
    return {
        "report_id": report.id,
        "created": not replaced,
        "replaced": replaced,
        "metrics": metrics,
        "records": records,
        "texts": texts,
        "warnings": warnings,
    }


def _insert_metrics(db: Session, report_id: int, report_type: str, items: list, warnings: list) -> int:
    seen: set[str] = set()
    count = 0
    for index, item in enumerate(items):
        key = str(item.get("metric_key") or "").strip()
        if not key or not fields.metric_key_allowed(report_type, key):
            warnings.append(f"忽略未知指标键：{key or '(空)'}")
            continue
        if key in seen:
            warnings.append(f"指标键重复已跳过：{key}")
            continue
        seen.add(key)
        db.add(MgMetric(
            report_id=report_id,
            section=item.get("section") or fields.metric_section(report_type, key),
            metric_key=key[:200],
            label=str(item.get("label") or "")[:200],
            value_num=item.get("value_num"),
            value_text=str(item.get("value_text") or ""),
            unit=str(item.get("unit") or "")[:20],
            extra=json.dumps(item.get("extra") or {}, ensure_ascii=False),
            sort=index,
        ))
        count += 1
    db.flush()
    return count


def _insert_records(db: Session, report_id: int, items: list, warnings: list) -> int:
    count = 0
    for index, item in enumerate(items):
        section = str(item.get("section") or "").strip()
        if section not in RECORD_ALLOWED:
            warnings.append(f"忽略未知明细类型：{section or '(空)'}")
            continue
        try:
            record = _record_from_payload(section, item)
        except ValueError as exc:
            warnings.append(f"第 {index + 1} 行明细被跳过：{exc}")
            continue
        record.report_id = report_id
        record.sort = index
        db.add(record)
        count += 1
    db.flush()
    return count


def _insert_texts(db: Session, report_id: int, items: list) -> int:
    count = 0
    for index, item in enumerate(items):
        content = str(item.get("content") or "").strip()
        if not content:
            continue
        db.add(MgText(
            report_id=report_id,
            section=str(item.get("section") or "")[:40],
            subsection=str(item.get("subsection") or "")[:100],
            content=content[:20000],
            sort=int(item.get("sort") or index * 10),
        ))
        count += 1
    db.flush()
    return count


def _record_from_payload(section: str, data: dict) -> MgRecord:
    allowed = fields.record_fields(section)
    nested = data.get("payload") if isinstance(data.get("payload"), dict) else {}

    def value_of(name: str):
        return data[name] if name in data else nested.get(name)

    values: dict[str, Any] = {}
    extra: dict[str, Any] = {}
    payload_names = fields.payload_fields(section)
    for name in allowed - payload_names:
        value = value_of(name)
        if value in (None, ""):
            continue
        if name in fields.RECORD_NUMERIC_FIELDS:
            values[name] = _num(value, name)
        elif name in fields.RECORD_DATE_FIELDS:
            values[name] = _day(value, name)
        else:
            values[name] = str(value or "")[:2000]
    for name in payload_names:
        value = value_of(name)
        if value not in (None, ""):
            extra[name] = value
    values["payload"] = json.dumps(extra, ensure_ascii=False, default=str)
    values.setdefault("seq", int(data.get("seq") or 0))
    return MgRecord(section=section, **values)


# ---------------- 批次 CRUD ----------------

def list_reports(db: Session, report_type: str = "", project: str = "", period: str = "",
                 page: int = 1, page_size: int = 50) -> dict:
    query = db.query(MgReport)
    if report_type:
        query = query.filter(MgReport.report_type == _clean_report_type(report_type))
    if project:
        query = query.filter(MgReport.project == project)
    if period:
        query = query.filter(MgReport.period == _period(period))
    total = query.count()
    rows = (
        query.order_by(MgReport.period.desc(), MgReport.id.desc())
        .offset((page - 1) * page_size).limit(min(page_size, MAX_PAGE_SIZE)).all()
    )
    counts = _counts(db, [row.id for row in rows])
    return {
        "total": total,
        "items": [_report_brief(row, counts.get(row.id, (0, 0, 0))) for row in rows],
    }


def _counts(db: Session, ids: list[int]) -> dict[int, tuple[int, int, int]]:
    result: dict[int, tuple[int, int, int]] = {}
    for model, index in ((MgMetric, 0), (MgRecord, 1), (MgText, 2)):
        if not ids:
            continue
        rows = (
            db.query(model.report_id, func.count(model.id))
            .filter(model.report_id.in_(ids))
            .group_by(model.report_id)
            .all()
        )
        for report_id, count in rows:
            current = list(result.get(report_id, (0, 0, 0)))
            current[index] = count
            result[report_id] = tuple(current)
    return result


def _report_brief(row: MgReport, counts: tuple[int, int, int]) -> dict:
    return {
        "id": row.id,
        "report_type": row.report_type,
        "project": row.project,
        "period": row.period,
        "period_start": row.period_start.isoformat(),
        "period_end": row.period_end.isoformat(),
        "title": row.title,
        "source_file": row.source_file,
        "imported_at": row.imported_at.isoformat(timespec="seconds"),
        "updated_at": row.updated_at.isoformat(timespec="seconds"),
        "metrics": counts[0],
        "records": counts[1],
        "texts": counts[2],
    }


def get_report(db: Session, report_id: int) -> dict:
    row = db.get(MgReport, report_id)
    if row is None:
        raise LookupError("报表批次不存在")
    detail = _report_brief(row, (0, 0, 0))
    detail["note"] = row.note
    detail["metrics"] = [
        {
            "id": item.id, "section": item.section, "metric_key": item.metric_key,
            "label": item.label, "value_num": item.value_num, "value_text": item.value_text,
            "unit": item.unit, "extra": _json(item.extra), "sort": item.sort,
        }
        for item in db.query(MgMetric).filter(MgMetric.report_id == report_id)
        .order_by(MgMetric.sort, MgMetric.id).all()
    ]
    detail["records"] = [
        {
            "id": item.id, "section": item.section, "seq": item.seq, "name": item.name,
            "category": item.category, "stage": item.stage, "status": item.status,
            "area": item.area, "amount": item.amount, "amount2": item.amount2,
            "recovery": item.recovery, "balance": item.balance, "rate": item.rate,
            "occurred_on": item.occurred_on.isoformat() if item.occurred_on else None,
            "due_on": item.due_on.isoformat() if item.due_on else None,
            "owner": item.owner, "co_owner": item.co_owner, "content": item.content,
            "progress": item.progress, "note": item.note, "payload": _json(item.payload),
            "sort": item.sort,
        }
        for item in db.query(MgRecord).filter(MgRecord.report_id == report_id)
        .order_by(MgRecord.sort, MgRecord.id).all()
    ]
    detail["texts"] = [
        {"id": item.id, "section": item.section, "subsection": item.subsection,
         "content": item.content, "sort": item.sort}
        for item in db.query(MgText).filter(MgText.report_id == report_id)
        .order_by(MgText.sort, MgText.id).all()
    ]
    detail["metrics"] = [item for item in detail["metrics"]]
    detail["counts"] = {"metrics": len(detail["metrics"]), "records": len(detail["records"]),
                        "texts": len(detail["texts"])}
    return detail


def _json(text: str | None) -> dict:
    try:
        return json.loads(text or "{}")
    except ValueError:
        return {}


def create_report(db: Session, payload: dict) -> dict:
    report_type = _clean_report_type(payload.get("report_type", ""))
    project = str(payload.get("project") or "").strip()
    if not project:
        raise ValueError("项目名称不能为空")
    period = _period(payload.get("period", ""))
    start = _day(payload.get("period_start"), "开始日期") or _period_bounds(period)[0]
    end = _day(payload.get("period_end"), "结束日期") or _period_bounds(period)[1]
    if end < start:
        raise ValueError("结束日期不能早于开始日期")
    if _existing(db, report_type, project, period, start):
        raise ValueError("同类型、同项目、同周期起始日已存在批次；如需重来请先删除或直接导入覆盖")
    row = MgReport(
        report_type=report_type, project=project[:100], period=period,
        period_start=start, period_end=end,
        title=str(payload.get("title") or f"{project} {period}")[:200],
        source_file="", note=str(payload.get("note") or "")[:2000],
    )
    db.add(row)
    db.commit()
    return get_report(db, row.id)


def update_report(db: Session, report_id: int, payload: dict) -> dict:
    row = db.get(MgReport, report_id)
    if row is None:
        raise LookupError("报表批次不存在")
    if "title" in payload:
        row.title = str(payload["title"] or "")[:200]
    if "note" in payload:
        row.note = str(payload["note"] or "")[:2000]
    if payload.get("period"):
        period = _period(payload["period"])
        start = _day(payload.get("period_start"), "开始日期") or _period_bounds(period)[0]
        end = _day(payload.get("period_end"), "结束日期") or _period_bounds(period)[1]
        if end < start:
            raise ValueError("结束日期不能早于开始日期")
        row.period, row.period_start, row.period_end = period, start, end
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ValueError("同类型、同项目、同周期起始日已存在批次，请调整期间或先删除重复批次") from exc
    return get_report(db, report_id)


def delete_report(db: Session, report_id: int) -> dict:
    row = db.get(MgReport, report_id)
    if row is None:
        raise LookupError("报表批次不存在")
    db.query(MgMetric).filter(MgMetric.report_id == report_id).delete()
    db.query(MgRecord).filter(MgRecord.report_id == report_id).delete()
    db.query(MgText).filter(MgText.report_id == report_id).delete()
    db.delete(row)
    db.commit()
    return {"deleted": True}


# ---------------- 手工填报 ----------------

def _is_rate_key(key: str) -> bool:
    return ".rate." in key or key.endswith(("rate", "change", "ratio"))


def upsert_metrics(db: Session, report_id: int, items: list[dict]) -> dict:
    row = db.get(MgReport, report_id)
    if row is None:
        raise LookupError("报表批次不存在")
    saved = 0
    for index, item in enumerate(items):
        key = str(item.get("metric_key") or "").strip()
        if not fields.metric_key_allowed(row.report_type, key):
            raise ValueError(f"指标键不属于当前报表类型（{row.report_type}）：{key or '(空)'}")
        value_num = _check_ratio(item.get("value_num"), f"{key} 数值") if _is_rate_key(key) else _num(
            item.get("value_num"), f"{key} 数值"
        )
        target = (
            db.query(MgMetric)
            .filter(MgMetric.report_id == report_id, MgMetric.metric_key == key)
            .first()
        )
        if target is None:
            target = MgMetric(report_id=report_id, metric_key=key[:200],
                              section=item.get("section") or fields.metric_section(row.report_type, key))
            db.add(target)
        target.section = item.get("section") or target.section
        target.label = str(item.get("label") or target.label or "")[:200]
        target.value_num = value_num
        target.value_text = str(item.get("value_text") or "")[:2000]
        target.unit = str(item.get("unit") or target.unit or "")[:20]
        if item.get("extra") is not None:
            target.extra = json.dumps(item["extra"], ensure_ascii=False)
        target.sort = int(item.get("sort") or index)
        saved += 1
    db.commit()
    return {"upserted": saved}


def add_record(db: Session, report_id: int, payload: dict) -> dict:
    row = db.get(MgReport, report_id)
    if row is None:
        raise LookupError("报表批次不存在")
    section = str(payload.get("section") or "").strip()
    if section not in RECORD_ALLOWED:
        raise ValueError("明细类型不在允许范围")
    record = _record_from_payload(section, payload)
    record.report_id = report_id
    record.sort = int(payload.get("sort") or 0)
    db.add(record)
    db.commit()
    return _record_out(record)


def update_record(db: Session, record_id: int, payload: dict) -> dict:
    record = db.get(MgRecord, record_id)
    if record is None:
        raise LookupError("明细不存在")
    payload_names = fields.payload_fields(record.section)
    for name in fields.record_fields(record.section) - payload_names:
        if name not in payload:
            continue
        value = payload[name]
        if name in fields.RECORD_NUMERIC_FIELDS:
            setattr(record, name, _num(value, name))
        elif name in fields.RECORD_DATE_FIELDS:
            setattr(record, name, _day(value, name))
        else:
            setattr(record, name, str(value or "")[:2000])
    nested = payload.get("payload") if isinstance(payload.get("payload"), dict) else {}
    if any(name in nested for name in payload_names):
        extra = _json(record.payload)
        for name in payload_names:
            if name not in nested:
                continue
            if nested[name] in (None, ""):
                extra.pop(name, None)
            else:
                extra[name] = nested[name]
        record.payload = json.dumps(extra, ensure_ascii=False, default=str)
    db.commit()
    return _record_out(record)


def _record_out(record: MgRecord) -> dict:
    return {
        "id": record.id, "section": record.section, "seq": record.seq, "name": record.name,
        "category": record.category, "stage": record.stage, "status": record.status,
        "area": record.area, "amount": record.amount, "amount2": record.amount2,
        "recovery": record.recovery, "balance": record.balance, "rate": record.rate,
        "occurred_on": record.occurred_on.isoformat() if record.occurred_on else None,
        "due_on": record.due_on.isoformat() if record.due_on else None,
        "owner": record.owner, "co_owner": record.co_owner, "content": record.content,
        "progress": record.progress, "note": record.note, "payload": _json(record.payload),
        "sort": record.sort,
    }


def delete_record(db: Session, record_id: int) -> dict:
    record = db.get(MgRecord, record_id)
    if record is None:
        raise LookupError("明细不存在")
    db.delete(record)
    db.commit()
    return {"deleted": True}


def upsert_texts(db: Session, report_id: int, items: list[dict]) -> dict:
    row = db.get(MgReport, report_id)
    if row is None:
        raise LookupError("报表批次不存在")
    saved = 0
    for index, item in enumerate(items):
        section = str(item.get("section") or "").strip()
        if section not in fields.text_keys(row.report_type):
            raise ValueError(f"叙述小节不在允许范围：{section or '(空)'}")
        content = str(item.get("content") or "")[:20000]
        target = (
            db.query(MgText)
            .filter(MgText.report_id == report_id, MgText.section == section)
            .first()
        )
        if target is None:
            target = MgText(report_id=report_id, section=section, subsection=str(item.get("subsection") or "")[:100])
            db.add(target)
        target.content = content
        if item.get("subsection") is not None:
            target.subsection = str(item["subsection"])[:100]
        target.sort = int(item.get("sort") or index * 10)
        saved += 1
    db.commit()
    return {"upserted": saved}


# ---------------- 总览 ----------------

def _metric_value(metrics: dict[str, MgMetric], key: str) -> float | None:
    item = metrics.get(key)
    return item.value_num if item is not None else None


def _first_value(metrics: dict[str, MgMetric], *keys: str) -> float | None:
    for key in keys:
        value = _metric_value(metrics, key)
        if value is not None:
            return value
    return None


def _load_metrics(db: Session, report_id: int) -> dict[str, MgMetric]:
    rows = db.query(MgMetric).filter(MgMetric.report_id == report_id).all()
    return {row.metric_key: row for row in rows}


def overview(db: Session, project: str = "", months: int = 12) -> dict:
    months = max(1, min(int(months or 12), 36))
    since = (datetime.now() - timedelta(days=months * 31)).strftime("%Y-%m")
    query = db.query(MgReport).filter(MgReport.period >= since)
    if project:
        query = query.filter(MgReport.project == project)
    reports = query.order_by(MgReport.period, MgReport.id).all()
    by_type: dict[str, list[MgReport]] = {}
    for row in reports:
        by_type.setdefault(row.report_type, []).append(row)

    occupancy: list[dict] = []
    collection: list[dict] = []
    leasing: list[dict] = []
    project_compare: dict[str, dict] = {}
    warning_counts: dict[str, int] = {}
    # 同项目同期间可能有多份周报（一周一份）：趋势与构成只取最新批次，避免重复累加
    latest_weekly: dict[tuple[str, str], MgReport] = {}
    for row in by_type.get("weekly", []):
        key = (row.project, row.period)
        if key not in latest_weekly or row.id > latest_weekly[key].id:
            latest_weekly[key] = row
    weekly_reports = sorted(latest_weekly.values(), key=lambda item: (item.period, item.project))
    for row in weekly_reports:
        metrics = _load_metrics(db, row.id)
        rate = _metric_value(metrics, "weekly.lease.occupancy_rate")
        if rate is not None:
            occupancy.append({"period": row.period, "project": row.project, "rate": rate})
        receivable = _metric_value(metrics, "weekly.collection.receivable")
        received = _metric_value(metrics, "weekly.collection.received")
        collect_rate = _metric_value(metrics, "weekly.collection.rate")
        if receivable is not None or received is not None or collect_rate is not None:
            collection.append({
                "period": row.period, "project": row.project,
                "receivable": receivable, "received": received, "rate": collect_rate,
            })
        new_area = _metric_value(metrics, "weekly.leasing.ytd_new_area")
        state = project_compare.setdefault(row.project, {})
        state["project"] = row.project
        state["occupancy_rate"] = rate
        state["collection_rate"] = collect_rate
        state["new_area"] = new_area
        for record in db.query(MgRecord).filter(MgRecord.report_id == row.id, MgRecord.section == "warning_tenant"):
            reason = record.content or "未注明"
            warning_counts[reason] = warning_counts.get(reason, 0) + 1

    kpi_rows: list[dict] = []
    litigation_stage: dict[str, dict] = {}
    litigation_top: list[dict] = []
    litigation_total: Decimal | None = None
    stage_amounts: dict[str, Decimal] = {}
    control_reports = by_type.get("control", [])
    if control_reports:
        latest = control_reports[-1]
        metrics = _load_metrics(db, latest.id)
        plan = _first_value(metrics, "leasing.year.区域合计.plan", "leasing.year.云南区域.plan")
        done = _first_value(metrics, "leasing.year.区域合计.done_area", "leasing.year.云南区域.done_area")
        if plan is not None or done is not None:
            leasing.append({
                "period": latest.period, "project": latest.project,
                "plan": plan, "done": done,
                "rate": round(done / plan, 4) if plan and done is not None else None,
            })
        for record in db.query(MgRecord).filter(MgRecord.report_id == latest.id, MgRecord.section == "kpi"):
            payload = _json(record.payload)
            target = _parse_target(payload.get("target_text", ""))
            kpi_rows.append({
                "name": record.name, "target": target, "actual": record.amount,
                "rate": record.rate, "weight": payload.get("weight", ""),
            })
        for record in db.query(MgRecord).filter(MgRecord.report_id == latest.id, MgRecord.section == "litigation"):
            stage = record.stage or "未注明"
            bucket = litigation_stage.setdefault(stage, {"stage": stage, "count": 0, "amount": 0.0})
            bucket["count"] += 1
            stage_amounts[stage] = stage_amounts.get(stage, Decimal("0")) + Decimal(str(record.amount or 0))
            litigation_total = (litigation_total or Decimal("0")) + Decimal(str(record.balance or 0))
            litigation_top.append({
                "name": record.name, "amount": record.amount,
                "balance": record.balance, "stage": stage,
            })
    for stage, amount in stage_amounts.items():
        litigation_stage[stage]["amount"] = _money(amount)
    if litigation_total is not None:
        litigation_total = _money(litigation_total)
    # KPI 必须先按全量求和，再截 Top10 供列表展示；否则 12 条诉讼会少算 2 条
    litigation_top = sorted(litigation_top, key=lambda item: item.get("amount") or 0, reverse=True)[:10]

    for row in by_type.get("monthly", []):
        metrics = _load_metrics(db, row.id)
        for key, item in metrics.items():
            parts = key.split(".")
            if len(parts) < 4 or parts[0] != "monthly":
                continue
            project_name, domain, field_name = parts[1], parts[2], ".".join(parts[3:])
            state = project_compare.setdefault(project_name, {"project": project_name})
            if domain == "collection" and field_name in ("rate", "received", "receivable"):
                state[f"collection_{field_name}"] = item.value_num
            elif domain == "occupancy" and field_name in ("rate", "area", "tenants"):
                state[f"occupancy_{field_name}"] = item.value_num
            elif domain == "signing" and field_name in ("done", "total"):
                state[f"signing_{field_name}"] = item.value_num

    compare_rows = []
    for state in project_compare.values():
        compare_rows.append({
            "project": state.get("project"),
            "collection_rate": state.get("collection_rate", state.get("collection_rate")),
            "occupancy_rate": state.get("occupancy_rate"),
            "new_area": state.get("new_area"),
        })

    kpis = _build_kpis(occupancy, collection, leasing, litigation_total)
    return {
        "periods": sorted({row.period for row in reports}),
        "kpis": kpis,
        "series": {
            "occupancy_trend": occupancy,
            "collection_trend": collection,
            "leasing_progress": leasing,
            "kpi_completion": kpi_rows,
            "litigation_by_stage": list(litigation_stage.values()),
            "litigation_top": litigation_top,
            "warning_reasons": [{"reason": key, "count": value} for key, value in warning_counts.items()],
            "project_compare": compare_rows,
        },
    }


def _parse_target(text: str) -> float | None:
    import re

    match = re.search(r"[-+]?\d+(?:\.\d+)?", str(text or "").replace(",", ""))
    return float(match.group(0)) if match else None


def _build_kpis(occupancy, collection, leasing, litigation_total: float | None) -> list[dict]:
    result: list[dict] = []
    if occupancy:
        latest = occupancy[-1]
        result.append({"key": "occupancy_rate", "label": "期末出租率", "value": latest["rate"],
                       "unit": "", "project": latest["project"], "period": latest["period"]})
    if collection:
        latest = collection[-1]
        result.append({"key": "collection_rate", "label": "累计收缴率", "value": latest["rate"],
                       "unit": "", "project": latest["project"], "period": latest["period"]})
        receivable, received = latest.get("receivable"), latest.get("received")
        if receivable is not None and received is not None:
            result.append({"key": "unreceived", "label": "累计未收（万元）",
                           "value": round(receivable - received, 2),
                           "unit": "万元", "project": latest["project"], "period": latest["period"]})
    if leasing:
        latest = leasing[-1]
        result.append({"key": "leasing_area", "label": "年度已完成招商面积", "value": latest["done"],
                       "unit": "㎡", "project": latest["project"], "period": latest["period"]})
    if litigation_total is not None:
        result.append({"key": "litigation_balance", "label": "在诉案件还欠金额",
                       "value": _money(litigation_total), "unit": "元", "project": "", "period": ""})
    return result


# ---------------- 导出 ----------------

def export_report(db: Session, report_id: int) -> tuple[str, bytes]:
    detail = get_report(db, report_id)
    wb = Workbook()
    ws = wb.active
    ws.title = "指标"
    ws.append(["小节", "指标键", "指标名称", "数值", "文本值", "单位"])
    for item in detail["metrics"]:
        ws.append([safe_cell(item["section"]), safe_cell(item["metric_key"]),
                   safe_cell(item["label"]), item["value_num"],
                   safe_cell(item["value_text"]), safe_cell(item["unit"])])
    records = wb.create_sheet("明细")
    records.append(["小节", "序号", "名称", "分类", "阶段", "面积", "金额", "金额2",
                    "回款", "还欠", "比率", "发生日期", "计划日期", "主责人", "协办人",
                    "内容", "进度", "备注", "附加字段"])
    for item in detail["records"]:
        records.append([
            safe_cell(item["section"]), item["seq"], safe_cell(item["name"]),
            safe_cell(item["category"]), safe_cell(item["stage"]), item["area"],
            item["amount"], item["amount2"], item["recovery"], item["balance"],
            item["rate"], item["occurred_on"], item["due_on"], safe_cell(item["owner"]),
            safe_cell(item["co_owner"]), safe_cell(item["content"]),
            safe_cell(item["progress"]), safe_cell(item["note"]),
            safe_cell(json.dumps(item["payload"], ensure_ascii=False)),
        ])
    texts = wb.create_sheet("文本")
    texts.append(["小节", "子项", "内容"])
    for item in detail["texts"]:
        texts.append([safe_cell(item["section"]), safe_cell(item["subsection"]),
                      safe_cell(item["content"])])
    buffer = io.BytesIO()
    wb.save(buffer)
    name = f"经营管理-{detail['project']}-{detail['period']}.xlsx".replace("/", "-")
    return name, buffer.getvalue()


def export_overview(db: Session, project: str = "", months: int = 12) -> tuple[str, bytes]:
    data = overview(db, project=project, months=months)
    wb = Workbook()
    ws = wb.active
    ws.title = "指标卡"
    ws.append(["指标", "数值", "单位", "项目", "期间"])
    for item in data["kpis"]:
        ws.append([safe_cell(item["label"]), item["value"], safe_cell(item["unit"]),
                   safe_cell(item["project"]), safe_cell(item["period"])])
    for name, rows, columns in (
        ("出租率", data["series"]["occupancy_trend"], ("period", "project", "rate")),
        ("收缴", data["series"]["collection_trend"], ("period", "project", "receivable", "received", "rate")),
        ("招商", data["series"]["leasing_progress"], ("period", "project", "plan", "done", "rate")),
        ("诉讼", data["series"]["litigation_by_stage"], ("stage", "count", "amount")),
    ):
        sheet = wb.create_sheet(name)
        sheet.append(list(columns))
        for row in rows:
            sheet.append([safe_cell(row.get(column)) for column in columns])
    buffer = io.BytesIO()
    wb.save(buffer)
    return "经营管理-总览.xlsx", buffer.getvalue()


# ---------------- Agent 工具 ----------------

def summary(project: str = "", period: str = "") -> dict:
    """只读摘要：给 Agent 团队用。"""
    from ...database import SessionLocal

    with SessionLocal() as db:
        query = db.query(MgReport)
        if project:
            query = query.filter(MgReport.project == project)
        if period:
            query = query.filter(MgReport.period == _period(period))
        reports = query.order_by(MgReport.period.desc(), MgReport.id.desc()).all()
        if not reports:
            return {"reports": 0, "message": "暂无经营管理报表，请先在经营管理页导入或填报"}
        latest = reports[0]
        metrics = _load_metrics(db, latest.id)
        litigation = db.query(MgRecord).filter(
            MgRecord.report_id == latest.id, MgRecord.section == "litigation"
        ).all()
        warnings = db.query(MgRecord).filter(
            MgRecord.report_id == latest.id, MgRecord.section == "warning_tenant"
        ).all()
        return {
            "reports": len(reports),
            "latest": {"report_type": latest.report_type, "project": latest.project,
                       "period": latest.period, "title": latest.title},
            "occupancy_rate": _metric_value(metrics, "weekly.lease.occupancy_rate"),
            "collection_rate": _metric_value(metrics, "weekly.collection.rate"),
            "new_area": _metric_value(metrics, "weekly.leasing.ytd_new_area"),
            "litigation_open_amount": round(sum(row.balance or 0 for row in litigation), 2),
            "litigation_count": len(litigation),
            "warning_tenants": len(warnings),
        }
