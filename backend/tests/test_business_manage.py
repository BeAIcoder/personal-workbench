# -*- coding: utf-8 -*-
"""经营管理模块接口回归：导入预览/确认、幂等替换、手工填报、总览与导出。"""
from __future__ import annotations

import io
from pathlib import Path

import openpyxl
import pytest

REPO = Path(__file__).resolve().parents[2]
TPL = REPO / "数据模板"
WEEKLY = TPL / "经营管理-云南项目周报-模板.xlsx"
MONTHLY = TPL / "经营管理-月度工作情况表-模板.xlsx"
CONTROL = TPL / "经营管理-内控管理表格-模板.xlsx"

pytestmark = pytest.mark.skipif(not TPL.is_dir(), reason="数据模板目录不存在")


def _preview(client, path: Path, report_type: str) -> dict:
    with open(path, "rb") as handle:
        response = client.post(
            "/api/business/manage/imports/preview",
            files={"file": (path.name, handle.read())},
            data={"report_type": report_type},
        )
    assert response.status_code == 200, response.text
    return response.json()


def _confirm(client, token: str, **extra) -> dict:
    response = client.post("/api/business/manage/imports/confirm", json={"token": token, **extra})
    assert response.status_code == 200, response.text
    return response.json()


def test_meta_covers_four_sections(client):
    body = client.get("/api/business/manage/meta").json()
    types = {item["value"] for item in body["report_types"]}
    assert types == {"control", "weekly", "monthly"}
    assert {item["key"] for item in body["sections"]["control"]["records"]} == {
        "key_work", "kpi", "litigation", "board_item"
    }
    assert {item["key"] for item in body["sections"]["weekly"]["texts"]} >= {
        "gap_analysis", "key_issues", "next_week"
    }


def test_preview_and_confirm_weekly(client):
    preview = _preview(client, WEEKLY, "weekly")
    assert preview["period"] == "2026-10"
    assert preview["report_type"] == "weekly"
    assert preview["replaces_report_id"] is None
    assert preview["sections"]
    confirmed = _confirm(client, preview["token"])
    assert confirmed["created"] is True and confirmed["replaced"] is False
    assert confirmed["metrics"] > 100 and confirmed["records"] >= 8

    detail = client.get(f"/api/business/manage/reports/{confirmed['report_id']}").json()
    assert detail["project"]
    assert detail["period_start"] < detail["period_end"]
    keys = {item["metric_key"] for item in detail["metrics"]}
    assert "weekly.lease.occupancy_rate" in keys
    assert any(key.startswith("weekly.collection.") for key in keys)
    sections = {item["section"] for item in detail["records"]}
    assert "warning_tenant" in sections
    promo = [item for item in detail["records"] if item["section"] == "brand_promo"]
    assert promo and any(item["payload"] for item in promo)


def test_control_revenue_scopes_are_distinct(client):
    confirmed = _confirm(client, _preview(client, CONTROL, "control")["token"])
    detail = client.get(f"/api/business/manage/reports/{confirmed['report_id']}").json()
    keys = {item["metric_key"] for item in detail["metrics"]}
    assert "revenue.项目C.year.target.current" in keys
    assert "revenue.项目C.ytd.target.current" in keys
    assert len(keys) == len(detail["metrics"])
    kpi = [item for item in detail["records"] if item["section"] == "kpi"]
    assert kpi and any(item["payload"].get("target_text") for item in kpi)


def test_reimport_replaces_same_scope(client):
    first = _confirm(client, _preview(client, WEEKLY, "weekly")["token"])
    preview = _preview(client, WEEKLY, "weekly")
    assert preview["replaces_report_id"] == first["report_id"]
    second = _confirm(client, preview["token"])
    assert second["replaced"] is True and second["created"] is False
    assert client.get("/api/business/manage/reports").json()["total"] == 1


def test_confirm_accepts_project_and_period_override(client):
    preview = _preview(client, WEEKLY, "weekly")
    confirmed = _confirm(client, preview["token"], project="曲靖项目", period="2026-11")
    detail = client.get(f"/api/business/manage/reports/{confirmed['report_id']}").json()
    assert detail["project"] == "曲靖项目"
    assert detail["period"] == "2026-11"
    assert detail["period_start"].endswith("-01") and detail["period_end"].endswith("-30")


def test_preview_rejects_non_xlsx(client):
    response = client.post(
        "/api/business/manage/imports/preview",
        files={"file": ("a.csv", b"x,y\n1,2")},
        data={"report_type": "weekly"},
    )
    assert response.status_code == 400
    assert "xlsx" in response.json()["detail"]


def test_overview_keeps_latest_weekly_batch_per_period(client):
    """同月多份周报（不同周）只取最新一批，趋势不能把两周的应收相加。"""
    from openpyxl import Workbook

    def weekly_bytes(start_day: int, end_day: int, receivable: float) -> bytes:
        wb = Workbook()
        ws = wb.active
        ws["A1"] = "项目：项目A"
        ws["E1"] = f"统计时间：2026年10月{start_day}日-10月{end_day}日"
        ws.append([])
        ws.append(["", "可出租面积", "出租率（%）", "环比增幅", "开业率（%）"])
        ws.append(["实际（期末值）", 1000, 0.9, 0.01, 0.85])
        ws.append([])
        ws.append(["1-10月累计应收款（万元）", "累计已收款（万元）", "累计未收款（万元）", "本周收回（万元）", "累计收缴率"])
        ws.append([receivable, receivable * 0.8, receivable * 0.2, 1, 0.8])
        buffer = io.BytesIO()
        wb.save(buffer)
        return buffer.getvalue()

    for start, end, amount in ((1, 7, 100), (22, 28, 200)):
        preview = client.post(
            "/api/business/manage/imports/preview",
            files={"file": (f"w{start}.xlsx", weekly_bytes(start, end, amount))},
            data={"report_type": "weekly"},
        ).json()
        confirmed = _confirm(client, preview["token"])
        assert confirmed["created"] is True
    overview = client.get("/api/business/manage/overview").json()
    rows = [row for row in overview["series"]["collection_trend"] if row["project"] == "项目A"]
    assert len(rows) == 1
    assert rows[0]["receivable"] == 200


def test_preview_rejects_unknown_type(client):
    with open(WEEKLY, "rb") as handle:
        response = client.post(
            "/api/business/manage/imports/preview",
            files={"file": (WEEKLY.name, handle.read())},
            data={"report_type": "unknown"},
        )
    assert response.status_code == 400


def test_preview_rejects_workbook_without_content(client):
    """无关/空工作簿必须拒绝：否则空批次会覆盖同期间的好数据。"""
    from openpyxl import Workbook

    wb = Workbook()
    wb.active["A1"] = "无关内容"
    buffer = io.BytesIO()
    wb.save(buffer)
    response = client.post(
        "/api/business/manage/imports/preview",
        files={"file": ("other.xlsx", buffer.getvalue())},
        data={"report_type": "weekly"},
    )
    assert response.status_code == 400
    assert "未识别到可导入内容" in response.json()["detail"]


def test_confirm_expired_token_400(client):
    response = client.post("/api/business/manage/imports/confirm", json={"token": "nope"})
    assert response.status_code == 400


def test_manual_report_metrics_records_texts(client):
    created = client.post("/api/business/manage/reports", json={
        "report_type": "weekly", "project": "云南项目", "period": "2026-10",
    })
    assert created.status_code == 200, created.text
    report_id = created.json()["id"]

    saved = client.put(f"/api/business/manage/reports/{report_id}/metrics", json={"items": [
        {"section": "weekly.collection", "metric_key": "weekly.collection.rate", "label": "累计收缴率",
         "value_num": 0.65, "unit": ""},
        {"section": "weekly.lease", "metric_key": "weekly.lease.occupancy_rate", "label": "期末出租率",
         "value_num": 0.8846, "unit": ""},
    ]})
    assert saved.status_code == 200 and saved.json()["upserted"] == 2

    record = client.post(f"/api/business/manage/reports/{report_id}/records", json={
        "section": "warning_tenant", "name": "品牌001", "category": "主力店", "area": 120.5,
        "content": "提出降租", "note": "约谈中",
    })
    assert record.status_code == 200, record.text
    record_id = record.json()["id"]
    updated = client.put(f"/api/business/manage/records/{record_id}", json={"progress": "已约谈"})
    assert updated.status_code == 200 and updated.json()["progress"] == "已约谈"

    texts = client.put(f"/api/business/manage/reports/{report_id}/texts", json={"items": [
        {"section": "gap_analysis", "content": "缺口 3 户，计划 11 月清零。"},
    ]})
    assert texts.status_code == 200

    detail = client.get(f"/api/business/manage/reports/{report_id}").json()
    assert len(detail["metrics"]) == 2
    assert detail["records"][0]["area"] == 120.5
    assert detail["texts"][0]["content"].startswith("缺口")

    deleted = client.delete(f"/api/business/manage/records/{record_id}")
    assert deleted.status_code == 200
    assert client.get(f"/api/business/manage/reports/{report_id}").json()["records"] == []


def test_record_payload_fields_roundtrip(client):
    """payload 字段（如上周进度）必须能写入、读出、清空，不能被当成未知列丢弃。"""
    report_id = client.post("/api/business/manage/reports", json={
        "report_type": "control", "project": "云南区域", "period": "2026-10",
    }).json()["id"]
    created = client.post(f"/api/business/manage/reports/{report_id}/records", json={
        "section": "key_work", "name": "品牌清缴", "content": "策略",
        "payload": {"prev_progress": "上周已完成立案", "remaining_days": 31},
    })
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["payload"]["prev_progress"] == "上周已完成立案"
    record_id = body["id"]
    cleared = client.put(f"/api/business/manage/records/{record_id}", json={"payload": {"prev_progress": ""}})
    assert cleared.status_code == 200
    assert "prev_progress" not in cleared.json()["payload"]


def test_manual_validation(client):
    report_id = client.post("/api/business/manage/reports", json={
        "report_type": "weekly", "project": "云南项目", "period": "2026-10",
    }).json()["id"]
    bad_key = client.put(f"/api/business/manage/reports/{report_id}/metrics", json={"items": [
        {"metric_key": "wrong.key", "value_num": 1},
    ]})
    assert bad_key.status_code == 400
    bad_rate = client.put(f"/api/business/manage/reports/{report_id}/metrics", json={"items": [
        {"metric_key": "weekly.collection.rate", "value_num": 9},
    ]})
    assert bad_rate.status_code == 400
    bad_text = client.put(f"/api/business/manage/reports/{report_id}/texts", json={"items": [
        {"section": "not_exist", "content": "x"},
    ]})
    assert bad_text.status_code == 400
    duplicate = client.post("/api/business/manage/reports", json={
        "report_type": "weekly", "project": "云南项目", "period": "2026-10",
    })
    assert duplicate.status_code == 400


def test_overview_and_export(client):
    _confirm(client, _preview(client, WEEKLY, "weekly")["token"])
    _confirm(client, _preview(client, CONTROL, "control")["token"])
    overview = client.get("/api/business/manage/overview").json()
    assert overview["kpis"]
    assert overview["series"]["occupancy_trend"]
    assert overview["series"]["litigation_top"]
    assert overview["series"]["kpi_completion"]

    report_id = client.get("/api/business/manage/reports").json()["items"][0]["id"]
    exported = client.get(f"/api/business/manage/reports/{report_id}/export.xlsx")
    assert exported.status_code == 200
    workbook = openpyxl.load_workbook(io.BytesIO(exported.content))
    assert workbook.sheetnames == ["指标", "明细", "文本"]
    workbook.close()

    total = client.get("/api/business/manage/overview/export.xlsx")
    assert total.status_code == 200 and len(total.content) > 1000


def test_export_escapes_formula_cells(client):
    report_id = client.post("/api/business/manage/reports", json={
        "report_type": "weekly", "project": "云南项目", "period": "2026-10",
    }).json()["id"]
    client.post(f"/api/business/manage/reports/{report_id}/records", json={
        "section": "warning_tenant", "name": "=1+1", "content": "@SUM(A1)",
    })
    data = client.get(f"/api/business/manage/reports/{report_id}/export.xlsx").content
    workbook = openpyxl.load_workbook(io.BytesIO(data))
    values = [cell.value for row in workbook["明细"].iter_rows() for cell in row]
    assert "'=1+1" in values
    assert "'@SUM(A1)" in values
    workbook.close()


def test_empty_overview_returns_empty_series(client):
    body = client.get("/api/business/manage/overview").json()
    assert body["kpis"] == []
    assert all(value == [] for value in body["series"].values())


def test_agent_tool_summary(client):
    from app.modules.business_manage.manifest import manifest

    tool = next(item for item in manifest.agent_tools if item["name"] == "manage_summary")
    assert tool["readonly"] is True
    assert manifest.preset_experts == ["operations"]
    empty = tool["func"]()
    assert empty["ok"] is True and empty["reports"] == 0
    bad = tool["func"](period="bad")
    assert bad["ok"] is False and "YYYY-MM" in bad["message"]
    _confirm(client, _preview(client, WEEKLY, "weekly")["token"])
    filled = tool["func"]()
    assert filled["reports"] >= 1
    assert filled["occupancy_rate"] is not None
