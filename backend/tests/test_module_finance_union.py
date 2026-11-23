# -*- coding: utf-8 -*-
"""finance_union 模型 / schema / 服务 / 真实 API 集成测试。

覆盖范围：

- 两张 ``mod_union_*`` 表、唯一索引与「派生值不落库」的列集合；
- 批次 CRUD、主体+期间唯一（预检 409 + ``IntegrityError`` → 中文 409）、
  期间/费率/工资基数严格校验（4xx）；
- 部门行整批保存、部门重复 409、缺数据 unknown 不得按 0 确认；
- 真实 API 链路：建批次 → 存行 → 预览/校验 → 确认 → 只读拦截 → xlsx 导出
  （openpyxl 回读真实值、公式注入防护、无空列）；
- ``union_summary`` 工具真实调用（含其 HTTP 端点）。

全部用例只用临时 SQLite 与合成主体/部门/金额，不写真实 data，也不依赖外部任务。
"""
from __future__ import annotations

import io
from decimal import Decimal

import pytest
from openpyxl import load_workbook
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from app.database import Base, SessionLocal, engine
from app.modules.finance_union import service
from app.modules.finance_union.models import UnionBatch, UnionLine
from app.modules.finance_union.schemas import (
    UnionBatchCreate,
    UnionBatchPatch,
    UnionLineIn,
    UnionLinesReplace,
)
from app.modules.finance_union.tools import union_summary_tool

API = "/api/finance/union"

BATCH_BODY = {"entity": "示例主体", "period": "2026-11", "rate": "0.02", "remark": "合成测试批次"}
CLEAN_LINES = [
    {"department": "行政部", "current_wage_base": "1000.00",
     "prior_accrual": "500.00", "prior_actual": "480.00"},
    {"department": "技术部", "current_wage_base": "2000.00",
     "prior_accrual": "300.00", "prior_actual": "420.00"},
]
UNKNOWN_LINES = [
    {"department": "行政部", "current_wage_base": "1000.00",
     "prior_accrual": "0.00", "prior_actual": "0.00"},
    {"department": "市场部", "current_wage_base": "1500.00",
     "prior_accrual": "0.00", "prior_actual": None},
]


@pytest.fixture(autouse=True)
def _ensure_tables():
    """非 client 直触库：保证本模块两表存在，并在每个用例前清空本模块数据。"""
    if not inspect(engine).has_table("mod_union_batch"):
        Base.metadata.create_all(bind=engine)
    with SessionLocal() as session:
        session.query(UnionLine).delete()
        session.query(UnionBatch).delete()
        session.commit()
    yield


@pytest.fixture()
def db():
    with SessionLocal() as session:
        yield session


def create_batch(db, **overrides) -> UnionBatch:
    body = {**BATCH_BODY, **overrides}
    return service.create_batch(db, UnionBatchCreate(**body))


def seed(db, lines=None, **overrides):
    batch = create_batch(db, **overrides)
    items = [UnionLineIn(**line) for line in (CLEAN_LINES if lines is None else lines)]
    service.replace_lines(db, batch.id, UnionLinesReplace(items=items))
    return batch


def api_create(client, **overrides) -> dict:
    body = {**BATCH_BODY, **overrides}
    response = client.post(f"{API}/batches", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def api_seed(client, lines=None, **overrides) -> dict:
    batch = api_create(client, **overrides)
    items = CLEAN_LINES if lines is None else lines
    response = client.put(f"{API}/batches/{batch['id']}/lines", json={"items": items})
    assert response.status_code == 200, response.text
    return batch


# ---------- 表、约束与列集合 ----------


def test_two_tables_and_unique_indexes_exist():
    names = set(inspect(engine).get_table_names())
    assert {"mod_union_batch", "mod_union_line"} <= names
    batch_indexes = {item["name"] for item in inspect(engine).get_indexes("mod_union_batch")}
    line_indexes = {item["name"] for item in inspect(engine).get_indexes("mod_union_line")}
    assert "uq_union_batch_entity_period" in batch_indexes
    assert "uq_union_line_batch_department" in line_indexes


def test_line_table_has_no_derived_amount_columns():
    """派生值不落库：行表只有三个输入金额，避免存储值与输入不一致。"""
    columns = {column["name"] for column in inspect(engine).get_columns("mod_union_line")}
    assert {"current_wage_base", "prior_accrual", "prior_actual"} <= columns
    assert not columns & {"current_accrued", "prior_variance", "current_book_amount"}


def test_database_rejects_duplicate_batch_and_line(db):
    create_batch(db)
    db.add(UnionBatch(entity="示例主体", period="2026-11", rate=Decimal("0.02"), status="draft"))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()

    batch = create_batch(db, period="2026-12")
    db.add(UnionLine(batch_id=batch.id, department="行政部"))
    db.commit()
    db.add(UnionLine(batch_id=batch.id, department="行政部"))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_integrity_error_maps_to_chinese_conflict(db):
    """_commit 是唯一 IntegrityError 出口，必须抛中文 UnionConflictError（路由 409）。"""
    create_batch(db)
    db.add(UnionBatch(entity="示例主体", period="2026-11", rate=Decimal("0.02"), status="draft"))
    with pytest.raises(service.UnionConflictError) as excinfo:
        service._commit(db, "主体与期间已存在（唯一约束冲突）")
    assert "唯一约束冲突" in str(excinfo.value)


# ---------- 批次 CRUD 与唯一性 ----------


def test_create_batch_is_draft_with_explicit_rate(db):
    batch = create_batch(db)
    assert batch.status == "draft" and batch.status_label == "草稿"
    assert batch.rate == Decimal("0.020000")
    assert batch.line_count == 0 and batch.confirmable is False


def test_duplicate_entity_period_conflicts(db):
    create_batch(db)
    with pytest.raises(service.UnionConflictError) as excinfo:
        create_batch(db)
    assert "2026-11" in str(excinfo.value)


def test_same_period_different_entity_allowed(db):
    create_batch(db)
    other = create_batch(db, entity="另一示例主体")
    assert other.period == "2026-11"


def test_rate_change_in_draft_recomputes_preview(db):
    batch = seed(db)
    preview = service.preview(db, batch.id)
    assert preview.totals.current_accrued == Decimal("60.00")
    service.update_batch(db, batch.id, UnionBatchPatch(rate=Decimal("0.03")))
    updated = service.preview(db, batch.id)
    assert updated.totals.current_accrued == Decimal("90.00")


def test_list_batches_filters_and_pagination(db):
    create_batch(db)
    create_batch(db, period="2026-10")
    listing = service.list_batches(db, period="2026-10")
    assert listing.total == 1 and listing.items[0].period == "2026-10"
    with pytest.raises(service.UnionValidationError):
        service.list_batches(db, status="unknown-status")


def test_delete_draft_batch_removes_lines(db):
    batch = seed(db)
    service.delete_batch(db, batch.id)
    assert db.query(UnionBatch).count() == 0
    assert db.query(UnionLine).count() == 0


def test_missing_batch_is_not_found(db):
    with pytest.raises(service.UnionNotFoundError):
        service.get_batch_detail(db, 999999)


# ---------- 部门行 ----------


def test_replace_lines_computes_two_percent_and_variance(db):
    batch = seed(db)
    lines = service.list_lines(db, batch.id)
    by_name = {line.department: line for line in lines}
    assert by_name["行政部"].current_accrued == Decimal("20.00")
    assert by_name["行政部"].prior_variance == Decimal("-20.00")
    assert by_name["行政部"].current_book_amount == Decimal("0.00")
    assert by_name["技术部"].current_book_amount == Decimal("160.00")


def test_duplicate_department_in_payload_conflicts(db):
    batch = create_batch(db)
    duplicated = [UnionLineIn(**CLEAN_LINES[0]), UnionLineIn(**CLEAN_LINES[0])]
    with pytest.raises(service.UnionConflictError) as excinfo:
        service.replace_lines(db, batch.id, UnionLinesReplace(items=duplicated))
    assert "重复" in str(excinfo.value)
    # 冲突不落半套：原有行保持不变（此处为空）
    assert db.query(UnionLine).count() == 0


def test_replace_lines_is_full_replace(db):
    batch = seed(db)
    service.replace_lines(db, batch.id, UnionLinesReplace(items=[UnionLineIn(**CLEAN_LINES[0])]))
    assert db.query(UnionLine).filter(UnionLine.batch_id == batch.id).count() == 1


def test_unknown_line_keeps_none_not_zero(db):
    batch = seed(db, lines=UNKNOWN_LINES)
    lines = {line.department: line for line in service.list_lines(db, batch.id)}
    assert lines["市场部"].line_status == "unknown"
    assert lines["市场部"].line_status_label == "缺数据"
    assert lines["市场部"].current_accrued is None
    assert lines["市场部"].missing_fields == ["prior_actual"]


# ---------- 预览 / 校验 / 确认 ----------


def test_preview_is_read_only(db):
    batch = seed(db)
    before = db.query(UnionBatch).filter_by(id=batch.id).one().status
    service.preview(db, batch.id)
    db.expire_all()
    assert db.query(UnionBatch).filter_by(id=batch.id).one().status == before


def test_checks_report_ok_for_complete_batch(db):
    batch = seed(db)
    result = service.checks(db, batch.id)
    assert result.all_passed is True
    assert result.check_counts == {"ok": 4, "failed": 0, "unknown": 0}
    assert result.confirmable is True


def test_confirm_rejected_when_unknown(db):
    batch = seed(db, lines=UNKNOWN_LINES)
    with pytest.raises(service.UnionConflictError) as excinfo:
        service.confirm_batch(db, batch.id)
    assert "缺数据" in str(excinfo.value)
    assert db.query(UnionBatch).filter_by(id=batch.id).one().status == "draft"


def test_confirm_then_batch_is_read_only(db):
    batch = seed(db)
    confirmed = service.confirm_batch(db, batch.id)
    assert confirmed.status == "confirmed" and confirmed.confirmed_at is not None
    for action in (
        lambda: service.update_batch(db, batch.id, UnionBatchPatch(remark="改")),
        lambda: service.replace_lines(db, batch.id, UnionLinesReplace(items=[])),
        lambda: service.confirm_batch(db, batch.id),
        lambda: service.delete_batch(db, batch.id),
    ):
        with pytest.raises(service.UnionConflictError):
            action()


def test_word_only_confirmation_requires_lines(db):
    batch = create_batch(db)
    with pytest.raises(service.UnionConflictError) as excinfo:
        service.confirm_batch(db, batch.id)
    assert "没有任何部门行" in str(excinfo.value)


# ---------- 真实 API 链路 ----------


def test_api_rejects_illegal_period_rate_and_wage(client):
    assert client.post(f"{API}/batches", json={**BATCH_BODY, "period": "2026-13"}).status_code == 422
    missing_rate = {"entity": "示例主体", "period": "2026-11"}
    assert client.post(f"{API}/batches", json=missing_rate).status_code == 422
    assert client.post(f"{API}/batches", json={**BATCH_BODY, "rate": "2"}).status_code == 422
    assert client.post(f"{API}/batches", json={**BATCH_BODY, "rate": "0"}).status_code == 422
    batch = api_create(client)
    negative = {"items": [{**CLEAN_LINES[0], "current_wage_base": "-1.00"}]}
    assert client.put(f"{API}/batches/{batch['id']}/lines", json=negative).status_code == 422


def test_api_duplicate_batch_and_department_return_409(client):
    first = api_create(client)
    duplicate = client.post(f"{API}/batches", json=BATCH_BODY)
    assert duplicate.status_code == 409
    assert "已存在批次" in duplicate.json()["detail"]

    same_twice = {"items": [CLEAN_LINES[0], {**CLEAN_LINES[0]}]}
    response = client.put(f"{API}/batches/{first['id']}/lines", json=same_twice)
    assert response.status_code == 409
    assert "重复" in response.json()["detail"]


def test_api_full_flow_preview_confirm_export(client):
    batch = api_seed(client)
    preview = client.get(f"{API}/batches/{batch['id']}/preview")
    assert preview.status_code == 200
    body = preview.json()
    assert body["totals"]["current_accrued"] == "60.00"
    assert body["totals"]["prior_variance"] == "100.00"
    assert body["totals"]["current_book_amount"] == "160.00"
    assert body["confirmable"] is True

    confirmed = client.post(f"{API}/batches/{batch['id']}/confirm")
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["status"] == "confirmed"

    for response in (
        client.post(f"{API}/batches/{batch['id']}/confirm"),
        client.patch(f"{API}/batches/{batch['id']}", json={"remark": "改"}),
        client.put(f"{API}/batches/{batch['id']}/lines", json={"items": []}),
        client.delete(f"{API}/batches/{batch['id']}"),
    ):
        assert response.status_code == 409, response.text


def test_api_unknown_line_blocks_confirm_with_chinese_reason(client):
    batch = api_seed(client, lines=UNKNOWN_LINES)
    response = client.post(f"{API}/batches/{batch['id']}/confirm")
    assert response.status_code == 409
    assert "缺数据" in response.json()["detail"]
    checks = client.get(f"{API}/batches/{batch['id']}/checks").json()
    assert checks["has_unknown"] is True and checks["all_passed"] is False


def test_api_missing_batch_is_404(client):
    assert client.get(f"{API}/batches/999999").status_code == 404
    assert client.get(f"{API}/batches/999999/preview").status_code == 404


# ---------- 导出：真实值、公式防护、无空列 ----------


def _export(client, batch_id) -> tuple[object, str]:
    response = client.get(f"{API}/batches/{batch_id}/export.xlsx")
    assert response.status_code == 200, response.text
    disposition = response.headers["content-disposition"]
    assert disposition.startswith('attachment; filename="')
    assert "filename*=UTF-8''" in disposition
    return load_workbook(io.BytesIO(response.content)), disposition


def _rows(ws):
    return [list(row) for row in ws.iter_rows(values_only=True)]


def test_export_contains_real_values_in_every_column(client):
    batch = api_seed(client)
    client.post(f"{API}/batches/{batch['id']}/confirm")
    workbook, disposition = _export(client, batch["id"])
    assert workbook.sheetnames == ["批次信息", "部门明细", "校验"]
    assert "union_" in disposition and ".xlsx" in disposition

    detail = _rows(workbook["部门明细"])
    header_index = next(i for i, row in enumerate(detail) if row[0] == "部门")
    header = detail[header_index]
    assert header == ["部门", "本期工资基数", "上期已计提", "上期实际数", "本期计提",
                      "上期差异（实际−已计提）", "本期建议入账", "行状态", "说明"]
    data_rows = [row for row in detail[header_index + 1:] if row[0]]
    assert [row[0] for row in data_rows] == ["行政部", "技术部"]
    assert data_rows[0][1:7] == [1000, 500, 480, 20, -20, 0]
    assert data_rows[1][1:7] == [2000, 300, 420, 40, 120, 160]
    assert [row[7] for row in data_rows] == ["可计算", "可计算"]

    # 每一列（含说明列）都必须有真实内容，不能有整列为空的“占位表头”
    for index, name in enumerate(header):
        assert any(row[index] not in (None, "") for row in data_rows), f"导出列「{name}」整列为空"

    batch_sheet = _rows(workbook["批次信息"])
    flat = [" ".join(str(cell) for cell in row if cell is not None) for row in batch_sheet]
    assert any("示例主体" in line and "2026-11" in line for line in flat)
    assert any("手工录入" in line and "60/40" in line for line in flat)


def test_export_marks_unknown_line_without_fake_zero(client):
    batch = api_seed(client, lines=UNKNOWN_LINES)
    workbook, _ = _export(client, batch["id"])
    detail = _rows(workbook["部门明细"])
    header_index = next(i for i, row in enumerate(detail) if row[0] == "部门")
    rows = {row[0]: row for row in detail[header_index + 1:] if row[0]}
    assert rows["市场部"][4] is None and rows["市场部"][6] is None
    assert rows["市场部"][7] == "缺数据"
    assert "缺" in rows["市场部"][8]

    checks = _rows(workbook["校验"])
    flat = [" ".join(str(cell) for cell in row if cell is not None) for row in checks]
    assert any("未知" in line for line in flat)
    assert not any(line.strip() == "通过" for line in flat if "总结论" not in line)


def test_export_escapes_formula_injection(client):
    batch = api_create(client, remark="=1+1")
    client.put(
        f"{API}/batches/{batch['id']}/lines",
        json={"items": [{"department": "=SUM(A1:A2)", "current_wage_base": "10.00",
                         "prior_accrual": "0.00", "prior_actual": "0.00"}]},
    )
    workbook, _ = _export(client, batch["id"])
    detail = _rows(workbook["部门明细"])
    header_index = next(i for i, row in enumerate(detail) if row[0] == "部门")
    data_rows = [row for row in detail[header_index + 1:] if row[0]]
    assert [row[0] for row in data_rows] == ["'=SUM(A1:A2)"]
    batch_sheet = _rows(workbook["批次信息"])
    remark_cells = [cell for row in batch_sheet for cell in row if cell == "'=1+1"]
    assert remark_cells, "批次备注必须 safe_cell 转义，不能写成活公式"


def test_export_filename_and_ascii_fallback(db):
    batch = seed(db)
    file = service.export_xlsx(db, batch.id)
    assert file.filename.startswith("工会经费计提_示例主体_2026-11_")
    assert file.filename.endswith(".xlsx")
    assert file.filename_ascii.startswith("union_") and file.filename_ascii.endswith(".xlsx")
    assert file.filename_ascii.isascii()


# ---------- 摘要（HTTP + Agent 工具） ----------


def test_summary_endpoint_and_tool(db):
    confirmed_batch = seed(db)
    service.confirm_batch(db, confirmed_batch.id)
    seed(db, lines=UNKNOWN_LINES, period="2026-10")

    summary = service.union_summary(db)
    assert summary.total_batches == 2
    assert summary.draft_count == 1 and summary.confirmed_count == 1
    assert summary.unknown_line_total == 1
    # 已确认批次 60.00 / 100.00 / 160.00 + 缺数据批次里唯一可计算行 20.00 / 0.00 / 20.00
    assert summary.current_accrued_total == Decimal("80.00")
    assert summary.prior_variance_total == Decimal("100.00")
    assert summary.current_book_amount_total == Decimal("180.00")

    filtered = service.union_summary(db, period="2026-10")
    assert filtered.total_batches == 1 and filtered.batches[0].period == "2026-10"

    tool = union_summary_tool(period="2026-11")
    assert tool["ok"] is True
    assert tool["total_batches"] == 1 and tool["confirmed_count"] == 1

    bad = union_summary_tool(period="2026-13")
    assert bad["ok"] is False and "YYYY-MM" in bad["error"]


def test_summary_endpoint_via_api(client):
    api_seed(client)
    response = client.get(f"{API}/summary", params={"entity": "示例"})
    assert response.status_code == 200
    body = response.json()
    assert body["total_batches"] == 1
    assert body["batches"][0]["current_accrued_total"] == "60.00"
