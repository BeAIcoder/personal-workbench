# -*- coding: utf-8 -*-
"""2026-11-11 审计 T5A 回归：经营/合同/HR 数据完整性。

覆盖 R11-036（诉讼全量欠额）、R11-037（三类 xlsx 只读 + 20 万行上限）、
R11-038（导入确认事务/令牌/唯一冲突 409）、R11-039（HR 并发薪资）、
R11-040（同铺位 active 合同部分唯一索引 + 存量查重）、R11-041（周报批次粒度）。
全部使用临时 SQLite 与合成数据，不触碰真实 backend/data。
"""
from __future__ import annotations

import io
from datetime import date

import openpyxl
import pytest
from openpyxl import Workbook
from openpyxl.styles import Font
from sqlalchemy import MetaData, UniqueConstraint, create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.database import Base, SessionLocal, engine
from app.modules.business_hr import service as hr_service
from app.modules.business_hr.models import HrPayroll
from app.modules.business_manage import importer, service as mg_service
from app.modules.business_manage.models import MgReport
from app.modules.business_operations import service as ops_service
from app.modules.business_operations.models import OpsContract

MANAGE = "/api/business/manage"
HR = "/api/business/hr"
OPS = "/api/business/operations"


@pytest.fixture(autouse=True)
def _ensure_tables():
    """非 client 用例也要有表：conftest 只对 client 用例重建库。"""
    Base.metadata.create_all(bind=engine)
    yield


@pytest.fixture(autouse=True)
def _clear_previews():
    mg_service.PREVIEWS.clear()
    yield
    mg_service.PREVIEWS.clear()


def _workbook_bytes(rows: list[list], title: str = "数据") -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = title
    for row in rows:
        ws.append(row)
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def _weekly_bytes(start_day: int = 8, end_day: int = 14, receivable: float = 100.0) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws["A1"] = "项目：项目A"
    ws["E1"] = f"统计时间：2026年10月{start_day}日-10月{end_day}日"
    ws.append([])
    ws.append(["", "可出租面积", "出租率（%）", "环比增幅", "开业率（%）"])
    ws.append(["实际（期末值）", 1000, 0.9, 0.01, 0.85])
    ws.append([])
    ws.append(["1-10月累计应收款（万元）", "累计已收款（万元）", "累计未收款（万元）",
               "本周收回（万元）", "累计收缴率"])
    ws.append([receivable, receivable * 0.8, receivable * 0.2, 1, 0.8])
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def _preview(client, content: bytes, report_type: str, filename: str = "a.xlsx"):
    return client.post(
        f"{MANAGE}/imports/preview",
        files={"file": (filename, content)},
        data={"report_type": report_type},
    )


# ---------------- R11-036：诉讼欠额先全量求和再截 Top10 ----------------

def test_litigation_kpi_and_export_use_full_total(client):
    report_id = client.post(f"{MANAGE}/reports", json={
        "report_type": "control", "project": "云南区域", "period": "2026-10",
    }).json()["id"]
    balances = []
    amounts = []
    for index in range(12):
        balance = 1000 + index * 100
        amount = 100 + index
        balances.append(balance)
        amounts.append(amount)
        created = client.post(f"{MANAGE}/reports/{report_id}/records", json={
            "section": "litigation", "name": f"合成案件{index + 1}",
            "stage": "一审", "amount": amount, "balance": balance,
        })
        assert created.status_code == 200, created.text

    overview = client.get(f"{MANAGE}/overview").json()
    kpi = next(item for item in overview["kpis"] if item["key"] == "litigation_balance")
    assert kpi["value"] == pytest.approx(float(sum(balances)))
    assert len(overview["series"]["litigation_top"]) == 10

    exported = client.get(f"{MANAGE}/overview/export.xlsx")
    assert exported.status_code == 200
    workbook = openpyxl.load_workbook(io.BytesIO(exported.content), data_only=True)
    try:
        cards = {
            str(row[0]): row[1]
            for row in workbook["指标卡"].iter_rows(min_row=2, values_only=True)
        }
        assert cards["在诉案件还欠金额"] == pytest.approx(float(sum(balances)))
        stage_amount = sum(
            row[2] or 0 for row in workbook["诉讼"].iter_rows(min_row=2, values_only=True)
        )
        assert stage_amount == pytest.approx(float(sum(amounts)))
    finally:
        workbook.close()


# ---------------- R11-037：三类 xlsx 只读 + 统一行数上限 ----------------

def test_xlsx_loader_is_read_only_for_all_report_types(monkeypatch):
    real_loader = openpyxl.load_workbook
    calls: list[dict] = []

    def spy(*args, **kwargs):
        calls.append(dict(kwargs))
        return real_loader(*args, **kwargs)

    monkeypatch.setattr(importer.openpyxl, "load_workbook", spy)
    content = _workbook_bytes([["2026年10月"], ["合成内容"]])
    for report_type in ("control", "weekly", "monthly"):
        importer.parse(report_type, "合成.xlsx", content)
    assert calls
    assert all(item.get("read_only") is True for item in calls), calls


def test_row_limit_applies_to_all_report_types(client, monkeypatch):
    monkeypatch.setattr(importer, "MAX_ROWS", 4)
    for report_type in ("control", "weekly", "monthly"):
        # 内控只构建表1..表6；用对应页签名确保行数上限在构建时触发
        title = "表1" if report_type == "control" else "数据"
        content = _workbook_bytes([[f"第{index}行"] for index in range(1, 7)], title=title)
        response = _preview(client, content, report_type)
        assert response.status_code == 400, (report_type, response.text)
        assert "行数" in response.json()["detail"]


def test_row_limit_is_per_file_not_per_sheet(client, monkeypatch):
    monkeypatch.setattr(importer, "MAX_ROWS", 5)
    wb = Workbook()
    wb.active.title = "表1"
    for index in range(3):
        wb.active.append([f"表1-{index}", "2026年10月"])
    other = wb.create_sheet("表2")
    for index in range(3):
        other.append([f"表2-{index}"])
    buffer = io.BytesIO()
    wb.save(buffer)
    response = _preview(client, buffer.getvalue(), "control")
    assert response.status_code == 400, response.text
    assert "行数" in response.json()["detail"]


# ---------------- R11-038：导入确认事务 / 令牌 / 唯一冲突 409 ----------------

def test_confirm_failure_keeps_token_and_rolls_back(client, monkeypatch):
    first = client.post(f"{MANAGE}/imports/confirm", json={
        "token": _preview(client, _weekly_bytes(), "weekly").json()["token"],
    })
    assert first.status_code == 200, first.text
    first_id = first.json()["report_id"]
    before = client.get(f"{MANAGE}/reports/{first_id}").json()

    preview = _preview(client, _weekly_bytes(), "weekly").json()
    assert preview["replaces_report_id"] == first_id

    def boom(*args, **kwargs):
        raise RuntimeError("合成写入失败")

    with monkeypatch.context() as patch:
        patch.setattr(mg_service, "_insert_records", boom)
        with SessionLocal() as db:
            with pytest.raises(RuntimeError):
                mg_service.confirm(db, preview["token"])

    assert preview["token"] in mg_service.PREVIEWS
    with SessionLocal() as db:
        assert db.query(MgReport).count() == 1
    kept = client.get(f"{MANAGE}/reports/{first_id}").json()
    assert kept["source_file"] == before["source_file"]
    assert len(kept["metrics"]) == len(before["metrics"])
    assert len(kept["records"]) == len(before["records"])

    confirmed = client.post(f"{MANAGE}/imports/confirm", json={"token": preview["token"]})
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["replaced"] is True
    assert client.get(f"{MANAGE}/reports").json()["total"] == 1


def test_confirm_integrity_error_maps_to_chinese_409_and_keeps_token(client, monkeypatch):
    token = _preview(client, _weekly_bytes(), "weekly").json()["token"]

    def conflict(*args, **kwargs):
        raise IntegrityError("INSERT", {}, Exception("UNIQUE constraint failed"))

    with monkeypatch.context() as patch:
        patch.setattr(mg_service, "_insert_metrics", conflict)
        response = client.post(f"{MANAGE}/imports/confirm", json={"token": token})

    assert response.status_code == 409, response.text
    assert "已存在" in response.json()["detail"]
    assert token in mg_service.PREVIEWS

    retried = client.post(f"{MANAGE}/imports/confirm", json={"token": token})
    assert retried.status_code == 200, retried.text


def test_concurrent_same_scope_confirm_keeps_single_batch(client, monkeypatch):
    first = _preview(client, _weekly_bytes(8, 14), "weekly").json()["token"]
    second = _preview(client, _weekly_bytes(8, 14), "weekly").json()["token"]
    confirmed = client.post(f"{MANAGE}/imports/confirm", json={"token": first})
    assert confirmed.status_code == 200, confirmed.text

    # 模拟两个请求都在对方提交前通过了「是否已存在」检查
    with monkeypatch.context() as patch:
        patch.setattr(mg_service, "_existing", lambda *args, **kwargs: None)
        conflicted = client.post(f"{MANAGE}/imports/confirm", json={"token": second})

    assert conflicted.status_code == 409, conflicted.text
    assert "已存在" in conflicted.json()["detail"]
    assert second in mg_service.PREVIEWS
    assert client.get(f"{MANAGE}/reports").json()["total"] == 1

    retried = client.post(f"{MANAGE}/imports/confirm", json={"token": second})
    assert retried.status_code == 200, retried.text
    assert retried.json()["replaced"] is True
    assert client.get(f"{MANAGE}/reports").json()["total"] == 1


def test_same_preview_token_cannot_be_confirmed_twice(client):
    token = _preview(client, _weekly_bytes(), "weekly").json()["token"]
    assert client.post(f"{MANAGE}/imports/confirm", json={"token": token}).status_code == 200
    again = client.post(f"{MANAGE}/imports/confirm", json={"token": token})
    assert again.status_code == 400


# ---------------- R11-039：HR 并发薪资 ----------------

def test_payroll_concurrent_generate_maps_integrity_to_409(client, monkeypatch):
    employee = client.post(f"{HR}/employees", json={
        "emp_no": "RACE-001", "name": "合成并发员工", "department": "营运部",
        "position": "主管", "hire_date": "2025-01-01", "status": "active",
        "base_salary": 10000.0, "social_base": 10000.0,
    })
    assert employee.status_code == 201, employee.text
    emp_id = employee.json()["id"]

    original = hr_service.calc_cumulative_withholding
    fired = {"done": False}

    def racing_withholding(*args, **kwargs):
        if not fired["done"]:
            fired["done"] = True
            with SessionLocal() as other:
                other.add(HrPayroll(
                    emp_id=emp_id, month="2026-01", base_salary=10000.0,
                    allowance=0.0, overtime_pay=0.0, deduction=0.0,
                    social_insurance=1050.0, housing_fund=700.0,
                    special_additional=0.0, tax=97.5, net_pay=8152.5,
                    status="draft",
                ))
                other.commit()
        return original(*args, **kwargs)

    monkeypatch.setattr(hr_service, "calc_cumulative_withholding", racing_withholding)
    response = client.post(f"{HR}/payroll/generate", json={"month": "2026-01"})
    assert response.status_code == 409, response.text
    assert "该月薪资已生成" in response.json()["detail"]
    with SessionLocal() as db:
        assert db.query(HrPayroll).filter(HrPayroll.month == "2026-01").count() == 1


# ---------------- R11-040：同铺位 active 合同部分唯一索引 ----------------

def _contract_body(no: str, unit_id: int | None = None) -> dict:
    body = {
        "contract_no": no, "tenant": "合成商户", "unit_no": "A-101",
        "area": 100.0, "start_date": "2026-01-01", "end_date": "2027-12-31",
        "rent_monthly": 10000.0, "property_fee_monthly": 0.0, "deposit": 0.0,
        "status": "active",
    }
    if unit_id is not None:
        body["unit_id"] = unit_id
    return body


def _contract_row(no: str, unit_id: int | None) -> dict:
    body = _contract_body(no, unit_id)
    body["start_date"] = date(2026, 1, 1)
    body["end_date"] = date(2027, 12, 31)
    return body


def test_active_contract_partial_unique_index_exists(client):
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT name, sql FROM sqlite_master WHERE type='index' "
            "AND name='uq_ops_contract_unit_active'"
        )).all()
    assert rows, "缺少同铺位 active 合同部分唯一索引"
    assert "status" in (rows[0][1] or "").lower()
    assert "active" in (rows[0][1] or "").lower()


def test_partial_unique_index_rejects_second_active_contract(client):
    with SessionLocal() as db:
        first = OpsContract(**_contract_row("RACE-C1", 777))
        db.add(first)
        db.commit()

        db.add(OpsContract(**_contract_row("RACE-C2", 777)))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()

        first = db.get(OpsContract, first.id)
        first.status = "terminated"
        db.commit()
        db.add(OpsContract(**_contract_row("RACE-C3", 777)))
        db.commit()
        assert db.query(OpsContract).filter(OpsContract.unit_id == 777).count() == 2


def test_contract_save_race_maps_to_409_with_unit_message(client, monkeypatch):
    monkeypatch.setattr(
        ops_service, "_resolve_unit",
        lambda db, unit_id, exclude_contract_id=None: (int(unit_id), "A-101"),
    )
    first = client.post(f"{OPS}/contracts", json=_contract_body("RACE-C11", unit_id=778))
    assert first.status_code == 201, first.text
    second = client.post(f"{OPS}/contracts", json=_contract_body("RACE-C12", unit_id=778))
    assert second.status_code == 409, second.text
    assert "生效合同" in second.json()["detail"]


def _legacy_ops_engine(tmp_path):
    legacy = create_engine(f"sqlite:///{(tmp_path / 'legacy.db').as_posix()}")
    Base.metadata.create_all(bind=legacy)
    with legacy.begin() as conn:
        conn.exec_driver_sql("DROP INDEX IF EXISTS uq_ops_contract_unit_active")
    return legacy


def test_stock_duplicate_check_reports_without_deleting_data(tmp_path):
    legacy = _legacy_ops_engine(tmp_path)
    session_factory = sessionmaker(bind=legacy, expire_on_commit=False)
    try:
        with session_factory() as db:
            db.add(OpsContract(**_contract_row("LEGACY-C1", 999)))
            db.add(OpsContract(**_contract_row("LEGACY-C2", 999)))
            db.commit()
            duplicates = ops_service.find_active_unit_duplicates(db)
            assert duplicates and duplicates[0]["unit_id"] == 999
            assert duplicates[0]["count"] == 2
            assert db.query(OpsContract).count() == 2
    finally:
        legacy.dispose()


def test_apply_index_skips_when_active_duplicates_exist(tmp_path):
    legacy = _legacy_ops_engine(tmp_path)
    session_factory = sessionmaker(bind=legacy, expire_on_commit=False)
    try:
        with session_factory() as db:
            db.add(OpsContract(**_contract_row("LEGACY-F1", 3001)))
            db.add(OpsContract(**_contract_row("LEGACY-F2", 3001)))
            db.commit()
            result = ops_service.ensure_active_unit_index(db)
            assert result["created"] is False
            assert result["duplicates"][0]["count"] == 2
            assert db.query(OpsContract).count() == 2
        with legacy.connect() as conn:
            index = conn.execute(text(
                "SELECT name FROM sqlite_master WHERE type='index' "
                "AND name='uq_ops_contract_unit_active'"
            )).first()
            assert index is None
    finally:
        legacy.dispose()


def test_legacy_terminated_duplicates_do_not_block_partial_index(tmp_path):
    legacy = _legacy_ops_engine(tmp_path)
    session_factory = sessionmaker(bind=legacy, expire_on_commit=False)
    try:
        with session_factory() as db:
            terminated = OpsContract(**_contract_row("LEGACY-E2", 2001))
            terminated.status = "terminated"
            terminated_b = OpsContract(**_contract_row("LEGACY-E3", 2001))
            terminated_b.status = "terminated"
            db.add(OpsContract(**_contract_row("LEGACY-E1", 2001)))
            db.add(terminated)
            db.add(terminated_b)
            db.add(OpsContract(**_contract_row("LEGACY-E4", None)))
            db.add(OpsContract(**_contract_row("LEGACY-E5", None)))
            db.commit()
            result = ops_service.ensure_active_unit_index(db)
            assert result["created"] is True
            assert result["duplicates"] == []
        with legacy.connect() as conn:
            index = conn.execute(text(
                "SELECT sql FROM sqlite_master WHERE type='index' "
                "AND name='uq_ops_contract_unit_active'"
            )).first()
            assert index is not None and "active" in (index[0] or "").lower()
        with session_factory() as db:
            db.add(OpsContract(**_contract_row("LEGACY-E6", 2001)))
            with pytest.raises(IntegrityError):
                db.commit()
    finally:
        legacy.dispose()


def test_legacy_duplicates_warn_and_keep_data_on_index_migration(tmp_path, caplog):
    import logging

    from app import main as app_main

    legacy = _legacy_ops_engine(tmp_path)
    session_factory = sessionmaker(bind=legacy, expire_on_commit=False)
    try:
        with session_factory() as db:
            db.add(OpsContract(**_contract_row("LEGACY-D1", 1001)))
            db.add(OpsContract(**_contract_row("LEGACY-D2", 1001)))
            db.commit()
        with caplog.at_level(logging.WARNING):
            app_main._ensure_indexes_on(legacy, Base.metadata)
        assert "已跳过唯一索引" in caplog.text
        with legacy.connect() as conn:
            assert conn.execute(text("SELECT COUNT(*) FROM mod_ops_contract")).scalar() == 2
            index = conn.execute(text(
                "SELECT name FROM sqlite_master WHERE type='index' "
                "AND name='uq_ops_contract_unit_active'"
            )).first()
            assert index is None
    finally:
        legacy.dispose()


# ---------------- R11-040 启动路径：索引注册必须真正生效 ----------------

def _drop_active_contract_index() -> None:
    with engine.begin() as conn:
        conn.exec_driver_sql("DROP INDEX IF EXISTS uq_ops_contract_unit_active")


def test_startup_index_migration_creates_partial_index_despite_legacy_rows(client, caplog):
    import logging

    from app import main as app_main

    _drop_active_contract_index()
    with SessionLocal() as db:
        terminated = OpsContract(**_contract_row("START-E2", 4201))
        terminated.status = "terminated"
        terminated_b = OpsContract(**_contract_row("START-E3", 4201))
        terminated_b.status = "terminated"
        db.add(OpsContract(**_contract_row("START-E1", 4201)))
        db.add(terminated)
        db.add(terminated_b)
        db.add(OpsContract(**_contract_row("START-N1", None)))
        db.add(OpsContract(**_contract_row("START-N2", None)))
        db.commit()

    with caplog.at_level(logging.WARNING):
        app_main._ensure_sqlite_indexes()

    assert "已跳过唯一索引" not in caplog.text
    with engine.connect() as conn:
        index = conn.execute(text(
            "SELECT sql FROM sqlite_master WHERE type='index' "
            "AND name='uq_ops_contract_unit_active'"
        )).first()
        assert index is not None and "active" in (index[0] or "").lower()
        assert conn.execute(text("SELECT COUNT(*) FROM mod_ops_contract")).scalar() == 5


def test_startup_index_migration_warns_and_keeps_app_level_409(client, caplog):
    import logging

    from app import main as app_main

    unit = client.post("/api/business/leasing/units", json={
        "building": "A栋", "floor": "1F", "unit_no": "START-101",
        "area": 100, "rent_price": 80, "property_fee": 12, "status": "vacant",
    })
    assert unit.status_code == 201, unit.text
    unit_id = unit.json()["id"]

    _drop_active_contract_index()
    with SessionLocal() as db:
        db.add(OpsContract(**_contract_row("START-D1", unit_id)))
        db.add(OpsContract(**_contract_row("START-D2", unit_id)))
        db.commit()

    with caplog.at_level(logging.WARNING):
        app_main._ensure_sqlite_indexes()

    assert "同铺位" in caplog.text
    with engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM mod_ops_contract")).scalar() == 2
        index = conn.execute(text(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND name='uq_ops_contract_unit_active'"
        )).first()
        assert index is None

    blocked = client.post(f"{OPS}/contracts", json=_contract_body("START-D3", unit_id=unit_id))
    assert blocked.status_code == 409, blocked.text
    assert "生效合同" in blocked.json()["detail"]


@pytest.mark.skipif(
    __import__("sys").platform != "win32",
    reason="启动 lifespan 依赖 Windows DPAPI 校验",
)
def test_real_lifespan_startup_creates_index_for_legacy_db(tmp_path):
    """真实启动路径：预置历史库（终止合同 + 空 unit_id 重复）后跑 lifespan，索引必须建成。"""
    import json
    import os
    import subprocess
    import sys
    from pathlib import Path

    db_path = tmp_path / "legacy_startup.db"
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    script = r"""
import json
from datetime import date

from sqlalchemy import text

from app.database import Base, engine, SessionLocal
from app.modules.business_operations.models import OpsContract

Base.metadata.create_all(bind=engine)
with engine.begin() as conn:
    conn.exec_driver_sql("DROP INDEX IF EXISTS uq_ops_contract_unit_active")
with SessionLocal() as db:
    for no, unit_id, status in (
        ("SMOKE-E1", 5201, "active"),
        ("SMOKE-E2", 5201, "terminated"),
        ("SMOKE-E3", 5201, "terminated"),
        ("SMOKE-N1", None, "active"),
        ("SMOKE-N2", None, "active"),
    ):
        db.add(OpsContract(
            contract_no=no, tenant="合成", unit_no="S-101", unit_id=unit_id,
            area=1.0, start_date=date(2026, 1, 1), end_date=date(2026, 12, 31),
            status=status,
        ))
    db.commit()

from fastapi.testclient import TestClient
from app.main import app

with TestClient(app):
    pass
with engine.connect() as conn:
    index = conn.execute(text(
        "SELECT sql FROM sqlite_master WHERE type='index' "
        "AND name='uq_ops_contract_unit_active'"
    )).first()
    count = conn.execute(text("SELECT COUNT(*) FROM mod_ops_contract")).scalar()
print(json.dumps({"index": index[0] if index else None, "count": count}))
"""
    env = os.environ.copy()
    env.update({
        "WORKBENCH_DATA_DIR": str(data_dir),
        "DATABASE_URL": f"sqlite:///{db_path.as_posix()}",
        "FINANCE_DATA_DIR": str(tmp_path / "finance"),
        "DEBUG_DATA_DIR": str(tmp_path / "debug"),
        "SEED_ON_STARTUP": "0",
        "WORKBENCH_ENV_FILE": "",
        "PYTHONIOENCODING": "utf-8",
    })
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(Path(__file__).resolve().parents[1]),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    assert result.returncode == 0, result.stderr[-3000:]
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload["index"] is not None and "active" in payload["index"].lower()
    assert payload["count"] == 5


# ---------------- R11-041：手工/导入周报批次粒度 ----------------

def test_manual_and_imported_weekly_share_batch_granularity(client):
    manual = client.post(f"{MANAGE}/reports", json={
        "report_type": "weekly", "project": "项目A", "period": "2026-10",
    })
    assert manual.status_code == 200, manual.text
    manual_id = manual.json()["id"]

    preview = _preview(client, _weekly_bytes(8, 14), "weekly").json()
    assert preview["replaces_report_id"] == manual_id
    confirmed = client.post(f"{MANAGE}/imports/confirm", json={"token": preview["token"]})
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["created"] is False
    assert confirmed.json()["replaced"] is True

    reports = client.get(f"{MANAGE}/reports").json()
    assert reports["total"] == 1
    detail = client.get(f"{MANAGE}/reports/{confirmed.json()['report_id']}").json()
    assert detail["period_start"] == "2026-10-08"
    assert detail["period_end"] == "2026-10-14"

    # 同月不同周仍各自保留批次（总览会取最新一批）
    other = _preview(client, _weekly_bytes(22, 28, 200), "weekly").json()
    assert other["replaces_report_id"] is None
    another = client.post(f"{MANAGE}/imports/confirm", json={"token": other["token"]})
    assert another.status_code == 200, another.text
    assert another.json()["created"] is True
    assert client.get(f"{MANAGE}/reports").json()["total"] == 2


# ---------------- T5 独立评审 Minor：M1/M2/M3/M4/M8/M9 ----------------

def test_weekly_import_does_not_overwrite_manual_other_week(client):
    """M1：回退只覆盖月初占位批，手工指定其他周的批次不能被另一周导入覆盖。"""
    manual = client.post(f"{MANAGE}/reports", json={
        "report_type": "weekly", "project": "项目A", "period": "2026-10",
        "period_start": "2026-10-15", "period_end": "2026-10-21",
    })
    assert manual.status_code == 200, manual.text
    manual_id = manual.json()["id"]

    preview = _preview(client, _weekly_bytes(22, 28), "weekly").json()
    assert preview["replaces_report_id"] is None
    confirmed = client.post(f"{MANAGE}/imports/confirm", json={"token": preview["token"]})
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["created"] is True
    assert client.get(f"{MANAGE}/reports").json()["total"] == 2
    kept = client.get(f"{MANAGE}/reports/{manual_id}").json()
    assert kept["period_start"] == "2026-10-15"


def _weekly_with_aux_sheet(aux_rows: int) -> bytes:
    workbook = openpyxl.load_workbook(io.BytesIO(_weekly_bytes()))
    aux = workbook.create_sheet("填写说明")
    for index in range(aux_rows):
        aux.append([f"填写说明第{index}行"])
    buffer = io.BytesIO()
    workbook.save(buffer)
    workbook.close()
    return buffer.getvalue()


def _control_with_aux_sheet(aux_rows: int) -> bytes:
    workbook = Workbook()
    table = workbook.active
    table.title = "表1"
    table.append(["序号", "工作内容", "主责人"])
    table.append([1, "合成重点工作", "张三"])
    aux = workbook.create_sheet("填写说明")
    for index in range(aux_rows):
        aux.append([f"填写说明第{index}行"])
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def test_weekly_import_only_builds_data_sheet(client, monkeypatch):
    """M2：周报只应构建数据页；辅助页的大行数不应触发行数上限。"""
    monkeypatch.setattr(importer, "MAX_ROWS", 8)
    response = _preview(client, _weekly_with_aux_sheet(10), "weekly")
    assert response.status_code == 200, response.text


def test_control_import_only_builds_needed_sheets(client, monkeypatch):
    """M2：内控只构建表1..表6；填写说明页不参与解析与行数上限。"""
    monkeypatch.setattr(importer, "MAX_ROWS", 5)
    response = _preview(client, _control_with_aux_sheet(10), "control")
    assert response.status_code == 200, response.text


def test_sheet_view_caps_cached_columns():
    """M2：内存缓存列数截到解析器实际读取上限（60 列）。"""
    workbook = Workbook()
    sheet = workbook.active
    sheet.append([f"c{index}" for index in range(1, 81)])
    buffer = io.BytesIO()
    workbook.save(buffer)
    read_only = openpyxl.load_workbook(io.BytesIO(buffer.getvalue()), read_only=True)
    try:
        sheet = read_only.worksheets[0]
        sheet.reset_dimensions()
        view = importer._SheetView(sheet, 1000, [0])
        assert view.max_column <= 60
        assert len(view._rows[0]) <= 60
        assert view.cell(row=1, column=61).value is None
    finally:
        read_only.close()


def test_row_limit_trims_styled_trailing_blank_rows(client, monkeypatch):
    """M3：尾部只有样式的空行不计数；真实数据行仍受硬上限约束。"""
    monkeypatch.setattr(importer, "MAX_ROWS", 10)
    workbook = openpyxl.load_workbook(io.BytesIO(_weekly_bytes()))
    sheet = workbook.worksheets[0]
    for row in range(sheet.max_row + 1, 21):
        sheet.cell(row=row, column=1).font = Font(bold=True)
    buffer = io.BytesIO()
    workbook.save(buffer)
    workbook.close()
    response = _preview(client, buffer.getvalue(), "weekly")
    assert response.status_code == 200, response.text


def test_row_limit_still_rejects_many_real_rows(client, monkeypatch):
    """M3：剔除空行后，真实数据行超过上限仍必须拒绝。"""
    monkeypatch.setattr(importer, "MAX_ROWS", 3)
    rows = [["2026年10月"]] + [[f"真实行{index}"] for index in range(4)]
    response = _preview(client, _workbook_bytes(rows), "monthly")
    assert response.status_code == 400
    assert "行数" in response.json()["detail"]


def test_litigation_amounts_use_decimal_half_up(client):
    """M4：诉讼金额按 Decimal ROUND_HALF_UP 最后取整，不逐条 half-even 累加。"""
    report_id = client.post(f"{MANAGE}/reports", json={
        "report_type": "control", "project": "云南区域", "period": "2026-10",
    }).json()["id"]
    for index in range(3):
        created = client.post(f"{MANAGE}/reports/{report_id}/records", json={
            "section": "litigation", "name": f"合成小数案件{index + 1}",
            "stage": "一审", "amount": 1.005, "balance": 1.005,
        })
        assert created.status_code == 200, created.text

    overview = client.get(f"{MANAGE}/overview").json()
    kpi = next(item for item in overview["kpis"] if item["key"] == "litigation_balance")
    assert kpi["value"] == pytest.approx(3.02)

    exported = client.get(f"{MANAGE}/overview/export.xlsx")
    workbook = openpyxl.load_workbook(io.BytesIO(exported.content), data_only=True)
    try:
        cards = {
            str(row[0]): row[1]
            for row in workbook["指标卡"].iter_rows(min_row=2, values_only=True)
        }
        assert cards["在诉案件还欠金额"] == pytest.approx(3.02)
        stage_amount = sum(
            row[2] or 0 for row in workbook["诉讼"].iter_rows(min_row=2, values_only=True)
        )
        assert stage_amount == pytest.approx(3.02)
    finally:
        workbook.close()


def test_ensure_active_unit_index_refuses_pending_session(tmp_path):
    """M8：不得把调用方未提交事务一起 commit；有 pending 写操作时明确拒绝。"""
    legacy = _legacy_ops_engine(tmp_path)
    session_factory = sessionmaker(bind=legacy, expire_on_commit=False)
    try:
        with session_factory() as db:
            db.add(OpsContract(**_contract_row("PENDING-1", 6101)))
            with pytest.raises(ValueError, match="未提交"):
                ops_service.ensure_active_unit_index(db)
            db.rollback()
        with session_factory() as db:
            assert db.query(OpsContract).count() == 0
            index = db.connection().exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type='index' "
                "AND name='uq_ops_contract_unit_active'"
            ).first()
            assert index is None
    finally:
        legacy.dispose()


def test_legacy_mg_report_unique_index_is_migrated(tmp_path, caplog):
    """M9：老库 4 列唯一约束之外，启动迁移应补建 3 列唯一索引；重复时只告警。"""
    import logging

    from app import main as app_main

    legacy = create_engine(f"sqlite:///{(tmp_path / 'legacy_mg.db').as_posix()}")
    metadata = MetaData()
    table = MgReport.__table__.to_metadata(metadata)
    for constraint in list(table.constraints):
        if isinstance(constraint, UniqueConstraint):
            table.constraints.discard(constraint)
    for index in list(table.indexes):
        table.indexes.discard(index)
    table.append_constraint(UniqueConstraint(
        "report_type", "project", "period", "period_start", name="uq_mg_report_legacy",
    ))
    metadata.create_all(bind=legacy)
    session_factory = sessionmaker(bind=legacy, expire_on_commit=False)
    try:
        with session_factory() as db:
            # 老 4 列键允许（period 不同），新 3 列键视为重复
            db.add(MgReport(
                report_type="weekly", project="项目L", period="2026-10",
                period_start=date(2026, 10, 1), period_end=date(2026, 10, 7),
            ))
            db.add(MgReport(
                report_type="weekly", project="项目L", period="2026-11",
                period_start=date(2026, 10, 1), period_end=date(2026, 10, 7),
            ))
            db.commit()

        with caplog.at_level(logging.WARNING):
            app_main._ensure_indexes_on(legacy, Base.metadata)
        assert "已跳过唯一索引" in caplog.text
        assert "mod_mg_report" in caplog.text
        with legacy.connect() as conn:
            assert conn.execute(text("SELECT COUNT(*) FROM mod_mg_report")).scalar() == 2
            index = conn.execute(text(
                "SELECT name FROM sqlite_master WHERE type='index' "
                "AND name='uq_mg_report_scope'"
            )).first()
            assert index is None

        with session_factory() as db:
            db.query(MgReport).filter(MgReport.period == "2026-11").delete()
            db.commit()

        caplog.clear()
        with caplog.at_level(logging.WARNING):
            app_main._ensure_indexes_on(legacy, Base.metadata)
        with legacy.connect() as conn:
            index = conn.execute(text(
                "SELECT sql FROM sqlite_master WHERE type='index' "
                "AND name='uq_mg_report_scope'"
            )).first()
            assert index is not None and "unique" in (index[0] or "").lower()

        with session_factory() as db:
            db.add(MgReport(
                report_type="weekly", project="项目L", period="2026-12",
                period_start=date(2026, 10, 1), period_end=date(2026, 10, 7),
            ))
            with pytest.raises(IntegrityError):
                db.commit()
    finally:
        legacy.dispose()


# ---------------- T5 复审 N1：缺失 dimension 时的数据页选择 ----------------

def _strip_dimensions(content: bytes) -> bytes:
    import re
    import zipfile

    source = zipfile.ZipFile(io.BytesIO(content))
    output = io.BytesIO()
    try:
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as target:
            for item in source.infolist():
                payload = source.read(item.filename)
                if item.filename.startswith("xl/worksheets/") and item.filename.endswith(".xml"):
                    payload = re.sub(rb"<dimension[^>]*/>", b"", payload)
                target.writestr(item, payload)
    finally:
        source.close()
    return output.getvalue()


def _weekly_dimensionless_workbook() -> bytes:
    workbook = Workbook()
    overview = workbook.active
    overview.title = "概览"
    overview.append(["合成概览"])
    data = workbook.create_sheet("数据")
    data["A1"] = "项目：项目A"
    data["E1"] = "统计时间：2026年10月8日-10月14日"
    data.append([])
    data.append(["", "可出租面积", "出租率（%）", "环比增幅", "开业率（%）"])
    data.append(["实际（期末值）", 1000, 0.9, 0.01, 0.85])
    data.append([])
    data.append(["1-10月累计应收款（万元）", "累计已收款（万元）", "累计未收款（万元）",
                 "本周收回（万元）", "累计收缴率"])
    data.append([100, 80, 20, 1, 0.8])
    buffer = io.BytesIO()
    workbook.save(buffer)
    workbook.close()
    return _strip_dimensions(buffer.getvalue())


def test_data_sheet_missing_dimension_falls_back_to_real_row_count(client):
    """N1：缺 <dimension> 时不能退化为第一页，应按实际非空行数选数据页。"""
    content = _weekly_dimensionless_workbook()
    read_only = openpyxl.load_workbook(io.BytesIO(content), read_only=True)
    try:
        assert all(ws.max_row is None for ws in read_only.worksheets)
    finally:
        read_only.close()

    response = _preview(client, content, "weekly")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["period"] == "2026-10"
    assert body["sections"]


def test_data_sheet_missing_dimension_still_enforces_row_limit(client, monkeypatch):
    """N1：缺 dimension 的退页计数也受 20 万行硬上限约束。"""
    monkeypatch.setattr(importer, "MAX_ROWS", 4)
    response = _preview(client, _weekly_dimensionless_workbook(), "weekly")
    assert response.status_code == 400
    assert "行数" in response.json()["detail"]
