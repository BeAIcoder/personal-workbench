# -*- coding: utf-8 -*-
"""四税种填报 router 行为契约（参数化）：防重 409、受控更正、已提交不可删、
导出产物清理、纳税人名归一。

增值税/个税/印花税/残保金四个 router 是同构实现（架构复盘 5.3）。本文件用同一组
参数化用例锁住公共行为，避免「一个税种修了、另一个没修」（R11-008 即四税种同时
缺防重）。各税种的计税口径、字段与底稿格式不在此覆盖。

全部使用临时 SQLite（conftest client 隔离）与合成数据；不触真实 backend/data。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import pytest

from app.database import SessionLocal
from app.modules.finance_dsf import service as dsf_service
from app.modules.finance_dsf.models import DsfFiling
from app.modules.finance_iit import service as iit_service
from app.modules.finance_iit.models import IitFiling
from app.modules.finance_stamp_duty import service as stamp_service
from app.modules.finance_stamp_duty.models import StampDutyFiling
from app.modules.finance_vat import service as vat_service
from app.modules.finance_vat.models import VatFiling

VAT_P = "/api/finance/vat"
IIT_P = "/api/finance/iit"
SD_P = "/api/finance/stamp-duty"
DSF_P = "/api/finance/dsf"

NAME = "合成纳税人甲"
OTHER_NAME = "合成纳税人乙"
MISSING_ID = 999999


# ---------- 载荷 ----------

def _vat_payload(**over):
    payload = {
        "period": "2026-09", "taxpayer_type": "general", "taxpayer_name": NAME,
        "output_items": [{"business_type": "销售货物", "amount": 100_000.0, "rate": 0.13}],
        "input_vat": 1_000.0,
    }
    payload.update(over)
    return payload


def _iit_payload(**over):
    payload = {
        "year": "2026", "taxpayer_name": NAME,
        "rows": [{"month": 1, "name": "员工甲", "salary": 10_000.0}],
    }
    payload.update(over)
    return payload


def _stamp_payload(**over):
    payload = {
        "period": "2026-09", "taxpayer_name": NAME,
        "items": [{"tax_item": "买卖合同（动产买卖）", "amount": 100_000.0}],
    }
    payload.update(over)
    return payload


def _dsf_payload(**over):
    payload = {
        "year": "2026", "taxpayer_name": NAME,
        "total_employees": 100, "total_salary": 10_000_000.0, "disabled_employees": 0,
    }
    payload.update(over)
    return payload


def _row(model, fields: dict, over: dict):
    fields.update(over)
    return model(**fields)


def _vat_row(period: str, name: str, **over) -> VatFiling:
    return _row(VatFiling, {"period": period, "taxpayer_type": "general",
                            "taxpayer_name": name, "inputs": {},
                            "calc_result": {}, "status": "calculated"}, over)


def _iit_row(period: str, name: str, **over) -> IitFiling:
    return _row(IitFiling, {"year": period, "taxpayer_name": name, "rows": [],
                            "calc_result": {}, "status": "calculated"}, over)


def _stamp_row(period: str, name: str, **over) -> StampDutyFiling:
    return _row(StampDutyFiling, {"period": period, "taxpayer_name": name,
                                  "half_discount": False, "items": [],
                                  "calc_result": {}, "status": "calculated"}, over)


def _dsf_row(period: str, name: str, **over) -> DsfFiling:
    return _row(DsfFiling, {"year": period, "taxpayer_name": name, "inputs": {},
                            "calc_result": {}, "status": "calculated"}, over)


@dataclass(frozen=True)
class TaxCase:
    """一个税种在公共契约里的差异项（其余行为必须一致）。"""

    tag: str
    prefix: str
    model: type
    payload: Callable[..., dict]
    period_field: str            # 更正时代表所属期间的字段名
    period_value: str            # 载荷里的默认期间值
    next_period: str             # 另一个合法期间（用于「换期间」用例）
    period_display: Callable[[str], str]   # 409 文案里的期间展示
    exports_dir: Callable[[], object]
    artifact_name: Callable[[str, int], str]
    new_row: Callable[..., object]


TAXES = [
    pytest.param(TaxCase(
        tag="vat", prefix=VAT_P, model=VatFiling, payload=_vat_payload,
        period_field="period", period_value="2026-09", next_period="2026-10",
        period_display=lambda period: period,
        exports_dir=vat_service.exports_dir,
        artifact_name=lambda period, fid: f"vat_{period}_{fid}.xlsx",
        new_row=_vat_row,
    ), id="vat"),
    pytest.param(TaxCase(
        tag="iit", prefix=IIT_P, model=IitFiling, payload=_iit_payload,
        period_field="year", period_value="2026", next_period="2027",
        period_display=lambda period: f"{period} 年度",
        exports_dir=iit_service.exports_dir,
        artifact_name=lambda period, fid: f"iit_{period}_{fid}.xlsx",
        new_row=_iit_row,
    ), id="iit"),
    pytest.param(TaxCase(
        tag="stamp", prefix=SD_P, model=StampDutyFiling, payload=_stamp_payload,
        period_field="period", period_value="2026-09", next_period="2026-10",
        period_display=lambda period: period,
        exports_dir=stamp_service.module_exports_dir,
        artifact_name=lambda period, fid: f"stamp_duty_{period}_{fid}.xlsx",
        new_row=_stamp_row,
    ), id="stamp"),
    pytest.param(TaxCase(
        tag="dsf", prefix=DSF_P, model=DsfFiling, payload=_dsf_payload,
        period_field="year", period_value="2026", next_period="2027",
        period_display=lambda period: f"{period} 年度",
        exports_dir=dsf_service.exports_dir,
        artifact_name=lambda period, fid: f"dsf_{period}_{fid}.xlsx",
        new_row=_dsf_row,
    ), id="dsf"),
]


# ---------- 辅助 ----------

def _create(client, case: TaxCase, expect: int = 201, **over) -> dict:
    resp = client.post(f"{case.prefix}/filings", json=case.payload(**over))
    assert resp.status_code == expect, resp.text
    return resp.json()


def _create_id(client, case: TaxCase, **over) -> int:
    return _create(client, case, **over)["id"]


def _set_status(case: TaxCase, filing_id: int, status: str) -> None:
    with SessionLocal() as db:
        db.get(case.model, filing_id).status = status
        db.commit()


def _plant(case: TaxCase, period: str, name: str = NAME, **over) -> int:
    with SessionLocal() as db:
        row = case.new_row(period, name, **over)
        db.add(row)
        db.commit()
        db.refresh(row)
        return row.id


def _artifact(case: TaxCase, period: str, filing_id: int):
    return case.exports_dir() / case.artifact_name(period, filing_id)


# ---------- 同期间 409（纳税人 + 期间唯一） ----------

@pytest.mark.parametrize("case", TAXES)
def test_duplicate_period_conflict(client, case):
    created = _create(client, case)
    fid = created["id"]

    dup = client.post(f"{case.prefix}/filings", json=case.payload())
    assert dup.status_code == 409, dup.text
    detail = dup.json()["detail"]
    assert f"id={fid}" in detail
    assert "已存在" in detail
    assert f"PUT {case.prefix}/filings/{fid}" in detail  # 受控更正路径可直达

    # 另一个纳税人 / 另一个期间不受影响
    _create(client, case, taxpayer_name=OTHER_NAME)
    _create(client, case, **{case.period_field: case.next_period})
    assert client.get(f"{case.prefix}/filings").json()["total"] == 3


@pytest.mark.parametrize("case", TAXES)
def test_duplicate_conflict_after_export_mentions_artifact(client, case):
    """已导出记录被重复 POST：409 说明「删记录会删底稿」，不误导走更正接口。"""
    fid = _create_id(client, case)
    assert client.post(f"{case.prefix}/filings/{fid}/export").status_code == 200

    dup = client.post(f"{case.prefix}/filings", json=case.payload())
    assert dup.status_code == 409, dup.text
    detail = dup.json()["detail"]
    assert f"id={fid}" in detail
    assert "底稿" in detail and "一并删除" in detail


@pytest.mark.parametrize("case", TAXES)
def test_taxpayer_name_normalized(client, case):
    """空白纳税人名归一为「未命名」：空格不能绕过防重，入库值必须已 strip。"""
    created = _create(client, case, taxpayer_name=f"  {NAME}  ")
    assert created["taxpayer_name"] == NAME

    _create(client, case, taxpayer_name="   ")
    dup = client.post(f"{case.prefix}/filings", json=case.payload(taxpayer_name=""))
    assert dup.status_code == 409, dup.text
    names = [row["taxpayer_name"] for row in client.get(f"{case.prefix}/filings").json()["items"]]
    assert "" in names


# ---------- 受控更正 ----------

@pytest.mark.parametrize("case", TAXES)
def test_revise_updates_same_record(client, case):
    fid = _create_id(client, case)
    revised = client.put(f"{case.prefix}/filings/{fid}", json=case.payload())
    assert revised.status_code == 200, revised.text
    assert revised.json()["id"] == fid
    assert revised.json()["status"] == "calculated"
    assert client.get(f"{case.prefix}/filings").json()["total"] == 1


@pytest.mark.parametrize("case", TAXES)
def test_revise_rejects_identity_change(client, case):
    fid = _create_id(client, case)
    other_name = client.put(f"{case.prefix}/filings/{fid}", json=case.payload(taxpayer_name=OTHER_NAME))
    assert other_name.status_code == 400, other_name.text
    assert "不允许更换" in other_name.json()["detail"]

    other_period = client.put(f"{case.prefix}/filings/{fid}",
                              json=case.payload(**{case.period_field: case.next_period}))
    assert other_period.status_code == 400, other_period.text
    assert "不允许更换" in other_period.json()["detail"]


@pytest.mark.parametrize("case", TAXES)
def test_revise_blocked_after_export(client, case):
    fid = _create_id(client, case)
    assert client.post(f"{case.prefix}/filings/{fid}/export").status_code == 200

    blocked = client.put(f"{case.prefix}/filings/{fid}", json=case.payload())
    assert blocked.status_code == 409, blocked.text
    assert "已导出" in blocked.json()["detail"]
    assert client.get(f"{case.prefix}/filings/{fid}").json()["status"] == "exported"


@pytest.mark.parametrize("case", TAXES)
def test_revise_blocked_when_submitted(client, case):
    fid = _create_id(client, case)
    _set_status(case, fid, "submitted")

    blocked = client.put(f"{case.prefix}/filings/{fid}", json=case.payload())
    assert blocked.status_code == 409, blocked.text
    detail = blocked.json()["detail"]
    assert "已提交" in detail and "更正申报" in detail
    assert client.get(f"{case.prefix}/filings/{fid}").json()["status"] == "submitted"


# ---------- 已提交不可删（留痕 + 底稿保留） ----------

@pytest.mark.parametrize("case", TAXES)
def test_delete_submitted_keeps_record_and_artifact(client, case):
    fid = _create_id(client, case)
    assert client.post(f"{case.prefix}/filings/{fid}/export").status_code == 200
    _set_status(case, fid, "submitted")

    blocked = client.delete(f"{case.prefix}/filings/{fid}")
    assert blocked.status_code == 409, blocked.text
    detail = blocked.json()["detail"]
    assert "已提交" in detail and "删除" in detail and "更正申报" in detail

    kept = client.get(f"{case.prefix}/filings/{fid}")
    assert kept.status_code == 200, kept.text
    assert kept.json()["status"] == "submitted"
    assert kept.json()["has_export"] is True
    download = client.get(f"{case.prefix}/filings/{fid}/download")
    assert download.status_code == 200 and len(download.content) > 1000
    assert _artifact(case, case.period_value, fid).is_file()


# ---------- 删除时的导出产物清理（L-065） ----------

@pytest.mark.parametrize("case", TAXES)
def test_delete_exported_removes_artifact(client, case):
    fid = _create_id(client, case)
    assert client.post(f"{case.prefix}/filings/{fid}/export").status_code == 200
    artifact = _artifact(case, case.period_value, fid)
    assert artifact.is_file(), artifact

    removed = client.delete(f"{case.prefix}/filings/{fid}")
    assert removed.status_code == 200, removed.text
    assert removed.json() == {"ok": True, "export_removed": True}
    assert not artifact.exists()
    assert client.get(f"{case.prefix}/filings/{fid}").status_code == 404


@pytest.mark.parametrize("case", TAXES)
def test_delete_unexported_reports_no_artifact(client, case):
    fid = _create_id(client, case)
    removed = client.delete(f"{case.prefix}/filings/{fid}")
    assert removed.status_code == 200, removed.text
    assert removed.json() == {"ok": True, "export_removed": False}


# ---------- 导出 / 下载守卫 ----------

@pytest.mark.parametrize("case", TAXES)
def test_export_requires_calculation(client, case):
    fid = _plant(case, case.period_value, calc_result={}, status="draft")
    resp = client.post(f"{case.prefix}/filings/{fid}/export")
    assert resp.status_code == 409, resp.text
    detail = resp.json()["detail"]
    assert "尚未完成计算" in detail and "无法导出" in detail


@pytest.mark.parametrize("case", TAXES)
def test_download_requires_export(client, case):
    fid = _create_id(client, case)
    resp = client.get(f"{case.prefix}/filings/{fid}/download")
    assert resp.status_code == 409, resp.text
    assert "尚未导出" in resp.json()["detail"]


@pytest.mark.parametrize("case", TAXES)
def test_download_missing_artifact_is_gone(client, case):
    fid = _create_id(client, case)
    assert client.post(f"{case.prefix}/filings/{fid}/export").status_code == 200
    _artifact(case, case.period_value, fid).unlink()

    resp = client.get(f"{case.prefix}/filings/{fid}/download")
    assert resp.status_code == 410, resp.text
    assert "重新导出" in resp.json()["detail"]


# ---------- 记录不存在 ----------

@pytest.mark.parametrize("case", TAXES)
def test_missing_record_404(client, case):
    base = f"{case.prefix}/filings/{MISSING_ID}"
    assert client.get(base).status_code == 404
    assert client.put(base, json=case.payload()).status_code == 404
    assert client.delete(base).status_code == 404
    assert client.post(f"{base}/export").status_code == 404
    assert client.get(f"{base}/download").status_code == 404
