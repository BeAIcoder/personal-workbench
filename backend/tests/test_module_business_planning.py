# -*- coding: utf-8 -*-
"""business_planning 企划管理模块测试（合成数据，临时库）。"""
import pytest

from app.modules import get_loaded_modules

P = "/api/business/planning"


def _campaign(client, name="合成活动", **over):
    body = {
        "name": name, "year": 2026, "category": "promo",
        "start_date": "2026-05-01", "end_date": "2026-05-07",
        "owner": "企划甲", "channel": "公众号", "target": "客流提升 10%",
        "status": "running",
        "budget_items": [
            {"item": "物料", "budget": 10000, "actual": 12000},
            {"item": "演艺", "budget": 20000, "actual": 15000},
        ],
        "metrics": {"footfall_lift": 8.5, "sales_lift": 6.2, "roi": 1.8, "review": "复购一般"},
    }
    body.update(over)
    return client.post(f"{P}/campaigns", json=body)


def test_manifest_loaded():
    loaded = {m.name: m for m in get_loaded_modules()}
    assert "business_planning" in loaded
    m = loaded["business_planning"]
    assert m.prefix == "/api/business/planning"
    from app.modules.business_planning import models as plan_models

    assert {plan_models.PlanCampaign.__tablename__,
            plan_models.PlanVendor.__tablename__} == {"mod_plan_campaign", "mod_plan_vendor"}


def test_campaign_budget_summary_calendar(client):
    r = _campaign(client)
    assert r.status_code == 201, r.text
    c = r.json()
    cid = c["id"]
    assert c["budget_total"] == pytest.approx(30000.0)
    assert c["actual_total"] == pytest.approx(27000.0)
    assert c["over_budget"] is False
    assert c["variance"] == pytest.approx(3000.0)
    assert c["roi"] == pytest.approx(1.8)
    assert c["roi_source"] == "manual"

    second = _campaign(
        client, name="超支活动", start_date="2026-05-08", end_date="2026-05-09",
        budget_items=[{"item": "投放", "budget": 10000, "actual": 13000}],
    )
    assert second.status_code == 201, second.text

    lst = client.get(f"{P}/campaigns", params={"year": 2026}).json()
    assert lst["total"] == 2

    cal = client.get(f"{P}/campaigns/calendar", params={"year": 2026}).json()
    may = next(m for m in cal["months"] if m["month"] == 5)
    assert {x["name"] for x in may["items"]} == {"合成活动", "超支活动"}

    s = client.get(f"{P}/summary", params={"year": 2026}).json()
    assert s["campaigns"] == 2
    assert s["budget_total"] == pytest.approx(40000.0)
    assert s["actual_total"] == pytest.approx(40000.0)
    assert s["over_budget_count"] == 1
    assert s["running"] == 2
    assert s["avg_roi"] is None
    assert s["manual_roi_count"] == 2

    upd = client.put(f"{P}/campaigns/{cid}", json={"status": "done",
                                                  "metrics": {"roi": 2.0, "review": "收官"}})
    assert upd.status_code == 200, upd.text
    assert upd.json()["status"] == "done"
    assert upd.json()["roi"] == pytest.approx(2.0)

    assert client.delete(f"{P}/campaigns/{cid}").status_code == 200


def test_campaign_validation(client):
    # 结束早于开始
    r = _campaign(client, start_date="2026-06-10", end_date="2026-06-01")
    assert r.status_code == 400
    # 空预算科目（schema min_length 拦截为 422；服务层也有一道 400）
    r = _campaign(client, budget_items=[{"item": "", "budget": 1}])
    assert r.status_code in (400, 422)
    # 未知状态由 Literal 拦截
    r = _campaign(client, status="bogus")
    assert r.status_code == 422
    # 年度越界
    r = _campaign(client, year=1999)
    assert r.status_code == 422


def test_vendor_crud(client):
    r = client.post(f"{P}/vendors", json={
        "name": "合成美陈公司", "service": "美陈", "contact": "赵六",
        "phone": "13900000000", "rating": 5,
    })
    assert r.status_code == 201, r.text
    vid = r.json()["id"]

    lst = client.get(f"{P}/vendors", params={"service": "美陈"}).json()
    assert [v["id"] for v in lst] == [vid]

    upd = client.put(f"{P}/vendors/{vid}", json={"rating": 3})
    assert upd.status_code == 200
    assert upd.json()["rating"] == 3
    assert client.put(f"{P}/vendors/{vid}", json={"rating": 9}).status_code == 422
    assert client.delete(f"{P}/vendors/{vid}").status_code == 200


def test_export(client):
    _campaign(client)
    r = client.get(f"{P}/export/campaigns.xlsx", params={"year": 2026})
    assert r.status_code == 200 and r.content[:2] == b"PK"


def test_campaign_negative_roi_keeps_grade_and_explicit_target(client):
    r = _campaign(
        client, name="负收益活动", grade="S", sales_baseline=100000,
        sales_during=80000, merchant_share=5000, roi_target=0,
        budget_items=[{"item": "执行", "budget": 15000, "actual": 15000}],
        metrics={"roi": 88, "review": "历史手填值仍保留"},
    )
    assert r.status_code == 201, r.text
    c = r.json()
    assert c["roi"] == pytest.approx(-1.0)
    assert c["roi_source"] == "calculated"
    assert c["metrics"]["roi"] == pytest.approx(88.0)
    assert c["grade"] == "S"
    assert c["target_met"] is False

    targeted = client.put(f"{P}/campaigns/{c['id']}", json={"roi_target": -1})
    assert targeted.status_code == 200, targeted.text
    assert targeted.json()["target_met"] is True
    cleared = client.put(f"{P}/campaigns/{c['id']}", json={"roi_target": None})
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["roi_target"] is None
    assert cleared.json()["target_met"] is None
    assert cleared.json()["grade"] == "S"


def test_campaign_roi_uses_cent_based_decimal_inputs(client):
    r = _campaign(
        client, name="Decimal ROI", sales_baseline=100, sales_during=100.01,
        merchant_share=0, roi_target=0.00005,
        budget_items=[{"item": "执行", "budget": 200, "actual": 200}],
        metrics={"roi": 9},
    )
    assert r.status_code == 201, r.text
    assert r.json()["sales_during"] == 100.01
    assert r.json()["roi_target"] == 0.00005
    assert r.json()["roi"] == pytest.approx(0.0001)
    assert r.json()["target_met"] is True


def test_campaign_money_is_normalized_in_response_and_export(client):
    r = _campaign(
        client, name="金额精度归一", start_date="2026-06-01", end_date="2026-06-02",
        sales_baseline=100.005, sales_during=100.004, merchant_share=1.005,
        roi_target=0.00005, budget_items=[], metrics={"roi": 9},
    )
    assert r.status_code == 201, r.text
    c = r.json()
    assert c["sales_baseline"] == 100.01
    assert c["sales_during"] == 100.00
    assert c["merchant_share"] == 1.01
    assert c["roi_target"] == 0.00005

    import io
    from openpyxl import load_workbook

    response = client.get(f"{P}/export/campaigns.xlsx", params={"year": 2026})
    assert response.status_code == 200
    ws = load_workbook(io.BytesIO(response.content))["企划活动"]
    rows = list(ws.values)
    exported = dict(zip(rows[0], rows[1]))
    assert exported["基准销售"] == 100.01
    assert exported["活动期间销售"] == 100.00
    assert exported["商户分摊"] == 1.01
    assert exported["ROI目标"] == 0.00005


def test_campaign_zero_investment_returns_null_roi_and_warning(client):
    r = _campaign(
        client, name="零投入活动", sales_baseline=100, sales_during=200,
        merchant_share=0, roi_target=0.5, budget_items=[], metrics={"roi": 7},
    )
    assert r.status_code == 201, r.text
    c = r.json()
    assert c["actual_total"] == 0
    assert c["roi"] is None
    assert c["roi_source"] == "calculated"
    assert "总投入为 0" in c["roi_warning"]
    assert c["target_met"] is None
    assert c["metrics"]["roi"] == 7

    s = client.get(f"{P}/summary", params={"year": 2026}).json()
    assert s["avg_roi"] is None
    assert s["manual_roi_count"] == 0


def test_campaign_missing_inputs_fall_back_to_manual_or_none(client):
    manual = _campaign(
        client, name="手填兼容", sales_baseline=1000, merchant_share=100,
        grade="B", metrics={"roi": 1.8, "review": "旧复盘"},
    )
    assert manual.status_code == 201, manual.text
    assert manual.json()["roi"] == pytest.approx(1.8)
    assert manual.json()["roi_source"] == "manual"
    assert manual.json()["target_met"] is None
    assert manual.json()["grade"] == "B"

    missing = _campaign(
        client, name="缺失输入", start_date="2026-06-01", end_date="2026-06-02",
        metrics={"review": "尚未填写 ROI"},
    )
    assert missing.status_code == 201, missing.text
    assert missing.json()["roi"] is None
    assert missing.json()["roi_source"] == "none"
    assert missing.json()["roi_warning"] is None

    s = client.get(f"{P}/summary", params={"year": 2026}).json()
    assert s["avg_roi"] is None
    assert s["manual_roi_count"] == 1


def test_summary_avg_roi_excludes_manual_values(client):
    calculated = [
        ("统一口径一", "2026-02-01", "2026-02-02", 300),
        ("统一口径二", "2026-03-01", "2026-03-02", 500),
    ]
    for name, start, end, sales in calculated:
        r = _campaign(
            client, name=name, start_date=start, end_date=end,
            sales_baseline=100, sales_during=sales, merchant_share=0,
            budget_items=[{"item": "执行", "budget": 100, "actual": 100}],
            metrics={"roi": 99},
        )
        assert r.status_code == 201, r.text
    manual = _campaign(
        client, name="历史手填", start_date="2026-04-01", end_date="2026-04-02",
        metrics={"roi": 100},
    )
    assert manual.status_code == 201, manual.text

    s = client.get(f"{P}/summary", params={"year": 2026}).json()
    assert s["avg_roi"] == pytest.approx(3.0)
    assert s["manual_roi_count"] == 1


def test_summary_avg_roi_aggregates_raw_decimal_ratios(client):
    for name, start, end, sales in (
        ("原始比率一", "2026-02-01", "2026-02-02", 100.49),
        ("原始比率二", "2026-03-01", "2026-03-02", 100.50),
    ):
        r = _campaign(
            client, name=name, start_date=start, end_date=end,
            sales_baseline=100, sales_during=sales, merchant_share=0,
            budget_items=[{"item": "执行", "budget": 10000, "actual": 10000}],
            metrics={"roi": 99},
        )
        assert r.status_code == 201, r.text

    s = client.get(f"{P}/summary", params={"year": 2026}).json()
    assert s["avg_roi"] == 0.0


def test_campaign_date_conflict_override_and_cancelled(client):
    base = _campaign(
        client, name="原活动", start_date="2026-05-10", end_date="2026-05-20",
    )
    assert base.status_code == 201, base.text
    same = client.put(
        f"{P}/campaigns/{base.json()['id']}",
        json={"start_date": "2026-05-10", "end_date": "2026-05-20"},
    )
    assert same.status_code == 200, same.text

    conflict = _campaign(
        client, name="重叠活动", start_date="2026-05-15", end_date="2026-05-25",
    )
    assert conflict.status_code == 409
    assert "原活动" in conflict.json()["detail"]
    assert "allow_conflict=true" in conflict.json()["detail"]

    explicit_false = _campaign(
        client, name="仍冲突", start_date="2026-05-15", end_date="2026-05-25",
        allow_conflict=False,
    )
    assert explicit_false.status_code == 409
    allowed = _campaign(
        client, name="有意并行", start_date="2026-05-15", end_date="2026-05-25",
        allow_conflict=True,
    )
    assert allowed.status_code == 201, allowed.text

    cancelled = _campaign(
        client, name="已取消活动", status="cancelled",
        start_date="2026-06-01", end_date="2026-06-10",
    )
    assert cancelled.status_code == 201, cancelled.text
    unblocked = _campaign(
        client, name="取消不阻塞", start_date="2026-06-05", end_date="2026-06-06",
    )
    assert unblocked.status_code == 201, unblocked.text
    new_cancelled = _campaign(
        client, name="新建即取消", status="cancelled",
        start_date="2026-06-05", end_date="2026-06-06",
    )
    assert new_cancelled.status_code == 201, new_cancelled.text


def test_calendar_expands_activity_to_every_covered_month(client):
    r = _campaign(
        client, name="跨月活动", start_date="2026-05-30", end_date="2026-06-02",
    )
    assert r.status_code == 201, r.text
    cal = client.get(f"{P}/campaigns/calendar", params={"year": 2026}).json()
    names_by_month = {
        m["month"]: [item["name"] for item in m["items"]] for m in cal["months"]
    }
    assert names_by_month[5] == ["跨月活动"]
    assert names_by_month[6] == ["跨月活动"]
    assert "跨月活动" not in names_by_month[4]
    assert "跨月活动" not in names_by_month[7]


def test_planning_column_patches_migration_is_idempotent(tmp_path, monkeypatch):
    import sqlite3
    from sqlalchemy import create_engine, text
    from app import main as app_main

    path = tmp_path / "old_planning.db"
    raw = sqlite3.connect(path)
    raw.execute("CREATE TABLE mod_plan_campaign (id INTEGER PRIMARY KEY, name VARCHAR(200), metrics JSON)")
    raw.execute("INSERT INTO mod_plan_campaign VALUES (1, '历史活动', '{\"roi\": 1.8}')")
    raw.commit()
    raw.close()

    old_engine = create_engine(f"sqlite:///{path.as_posix()}")
    monkeypatch.setattr(app_main, "engine", old_engine)
    expected = {"grade", "sales_baseline", "sales_during", "merchant_share", "roi_target"}
    try:
        app_main._ensure_sqlite_columns()
        with old_engine.connect() as conn:
            info = {row[1]: row for row in conn.execute(text("PRAGMA table_info(mod_plan_campaign)"))}
            assert expected <= info.keys()
            assert all(info[name][3] == 0 for name in expected)
            first_schema = conn.execute(text(
                "SELECT sql FROM sqlite_master WHERE name='mod_plan_campaign'"
            )).scalar()
            first_rows = conn.execute(text(
                "SELECT id, name, metrics FROM mod_plan_campaign"
            )).fetchall()

        app_main._ensure_sqlite_columns()
        with old_engine.connect() as conn:
            second_schema = conn.execute(text(
                "SELECT sql FROM sqlite_master WHERE name='mod_plan_campaign'"
            )).scalar()
            second_rows = conn.execute(text(
                "SELECT id, name, metrics FROM mod_plan_campaign"
            )).fetchall()
        assert second_schema == first_schema
        assert second_rows == first_rows == [(1, "历史活动", '{"roi": 1.8}')]
    finally:
        old_engine.dispose()


def test_export_contains_roi_values_methodology_and_safe_text(client):
    calculated = _campaign(
        client, name="=2+3", sales_baseline=100, sales_during=300,
        merchant_share=0, roi_target=1.5,
        budget_items=[{"item": "执行", "budget": 100, "actual": 100}],
        metrics={"roi": 99, "review": "统一口径"},
    )
    assert calculated.status_code == 201, calculated.text
    manual = _campaign(
        client, name="手填活动", start_date="2026-06-01", end_date="2026-06-02",
        metrics={"roi": 1.25, "review": "历史口径"},
    )
    assert manual.status_code == 201, manual.text

    import io
    from openpyxl import load_workbook

    response = client.get(f"{P}/export/campaigns.xlsx", params={"year": 2026})
    assert response.status_code == 200
    wb = load_workbook(io.BytesIO(response.content))
    assert wb.sheetnames == ["企划活动", "口径说明"]
    ws = wb["企划活动"]
    rows = list(ws.values)
    headers = rows[0]
    records = [dict(zip(headers, row)) for row in rows[1:]]
    assert len(records) == 2
    calc = next(row for row in records if row["活动名称"] == "'=2+3")
    manual_row = next(row for row in records if row["活动名称"] == "手填活动")
    assert calc["ROI"] == pytest.approx(2.0)
    assert calc["手填ROI"] == pytest.approx(99.0)
    assert calc["ROI来源"] == "calculated"
    assert calc["目标达成"] == "是"
    assert manual_row["ROI"] == pytest.approx(1.25)
    assert manual_row["ROI来源"] == "manual"

    methodology = "\n".join(
        str(cell.value) for row in wb["口径说明"].iter_rows() for cell in row
        if cell.value is not None
    )
    assert "(活动期间销售 - 基准销售) / (实际投入 + 商户分摊)" in methodology
    assert "手填 ROI 不参与平均" in methodology
    assert "总投入为 0" in methodology
