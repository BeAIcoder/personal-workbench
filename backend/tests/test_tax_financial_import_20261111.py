# -*- coding: utf-8 -*-
"""T3A：企业删除保护、财报导入持久化与严格期间解析（2026-11-11 审计 R11-006/007/021）。

覆盖：
- 删除存在 submitted 申报的企业 → 中文 409，申报完整保留；仅草稿的企业仍可删除；
- 财报导入真实落库：企业按税号 upsert、FinancialData 按企业/年/季度 upsert，
  单事务、重复导入幂等；
- 无企业身份时保持现有解析预览（persisted=false），不写库、不回退当前期间；
- 期间缺失/不可解析/13 月/quarter>4 → 中文 400，不落库。

全部合成数据 + conftest 临时 SQLite；不触真实 backend/data。
"""
from __future__ import annotations

import io

import openpyxl
import pytest
from openpyxl import Workbook

from app.database import Base, SessionLocal, engine
from app.modules.finance_tax.models import Enterprise, FinancialData, TaxReturn
from app.modules.finance_tax.services import financial_data_importer as importer_mod
from app.modules.finance_tax.services.financial_data_importer import (
    DataMapper,
    FinancialDataImporter,
    FinancialSystemExportParser,
    ParsedIncomeStatement,
    StandardTemplateParser,
    StatementType,
)

P = "/api/finance/tax"
IMPORT = f"{P}/financial-import/import"
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@pytest.fixture(autouse=True)
def _ensure_tables():
    """非 client 用例同样需要表（conftest 只对 client 用例建表）。"""
    engine.dispose()
    Base.metadata.create_all(bind=engine)
    yield


# ---------------------------------------------------------------- 工具


def _db_count(model) -> int:
    with SessionLocal() as db:
        return int(db.query(model).count())


def _db_all(model) -> list:
    with SessionLocal() as db:
        return list(db.query(model).all())


def _enterprise(client, tax_number: str = "91310000T3A000001", name: str = "T3A 合成企业", **kw):
    payload = {
        "name": name,
        "tax_number": tax_number,
        "industry_type": "一般行业",
        "employee_count": 50,
        "total_assets": 1000.0,
        "is_restricted_industry": False,
    }
    payload.update(kw)
    resp = client.post(f"{P}/enterprises/", json=payload)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _save_return(client, enterprise_id: int, **kw):
    payload = {
        "enterprise_id": enterprise_id,
        "tax_period": "2026",
        "quarter": "Q1",
        "report_type": "A类",
        "total_revenue": 100.0,
    }
    payload.update(kw)
    resp = client.post(f"{P}/tax-returns/", json=payload)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _income_xlsx(period=None, revenue_wan: float = 300.0, company=None,
                 tax_number=None, sheet: str = "Sheet1") -> bytes:
    """合成财务系统导出的利润表：A 列项目、C 列本期金额（元）。"""
    wb = Workbook()
    ws = wb.active
    ws.title = sheet
    ws["A1"] = "利润表"
    row = 2
    if company:
        ws.cell(row=row, column=1, value=f"编制单位：{company}")
        row += 1
    if tax_number:
        ws.cell(row=row, column=1, value=f"纳税人识别号：{tax_number}")
        row += 1
    if period is not None:
        ws.cell(row=row, column=1, value=f"时间：{period}")
        row += 1
    for label, value in (("营业收入", revenue_wan), ("营业成本", 150.0), ("利润总额", 100.0)):
        ws.cell(row=row, column=1, value=label)
        ws.cell(row=row, column=3, value=value * 10000.0)
        row += 1
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _balance_xlsx(total_assets_wan: float = 100.0, company=None, tax_number=None,
                  separate_header: bool = False, sheet: str = "Sheet1") -> bytes:
    """合成财务系统导出的资产负债表（separate_header=True 时标签/值分列）。"""
    wb = Workbook()
    ws = wb.active
    ws.title = sheet
    ws["A1"] = "资产负债表"
    if separate_header:
        ws["D1"] = "编制单位"
        ws["E1"] = company
        ws["F1"] = "纳税人识别号"
        ws["G1"] = tax_number
        row = 3
    else:
        row = 2
        if company:
            ws.cell(row=row, column=1, value=f"编制单位：{company}")
            row += 1
        if tax_number:
            ws.cell(row=row, column=1, value=f"纳税人识别号：{tax_number}")
            row += 1
    ws.cell(row=row, column=1, value="资产总计")
    ws.cell(row=row, column=2, value=total_assets_wan * 10000.0)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _upload(client, payload: bytes, url: str = IMPORT, data: dict | None = None):
    return client.post(
        url,
        files={"file": ("利润表.xlsx", payload, XLSX_MIME)},
        data=data or {},
    )


def _period_error_class():
    return getattr(importer_mod, "PeriodParseError", ValueError)


# ---------------------------------------------------------------- I-1 表头解析（纯标签不得当值）


def test_parse_header_pure_labels_are_not_values():
    rows = [{"A1": "资产负债表", "D1": "编制单位", "F1": "纳税人识别号"}]
    info = FinancialSystemExportParser._parse_header(rows)
    assert info["company_name"] == ""
    assert info["tax_number"] == ""


def test_parse_header_right_cell_that_is_another_label_is_not_value():
    rows = [{"D1": "编制单位", "E1": "纳税人识别号", "F1": "91310000T3A000019"}]
    info = FinancialSystemExportParser._parse_header(rows)
    assert info["company_name"] == ""
    assert info["tax_number"] == "91310000T3A000019"


def test_parse_header_separate_cells_and_currency():
    rows = [{
        "A1": "资产负债表",
        "D1": "编制单位", "E1": "分列公司",
        "F1": "纳税人识别号", "G1": "91310000T3A000019",
        "H1": "金额单位", "I1": "元",
        "A2": "时间", "B2": "2026年1季度",
    }]
    info = FinancialSystemExportParser._parse_header(rows)
    assert info["company_name"] == "分列公司"
    assert info["tax_number"] == "91310000T3A000019"
    assert info["period"] == "2026年1季度"
    assert info["currency"] == "元"


def test_parse_header_currency_not_confused_with_company():
    rows = [{
        "A1": "资产负债表",
        "A2": "金额单位：万元",
        "A3": "编制单位：合成公司",
        "A4": "时间：2026年1季度",
    }]
    info = FinancialSystemExportParser._parse_header(rows)
    assert info["company_name"] == "合成公司"
    assert info["currency"] == "万元"


def test_separate_cell_header_persists_correct_enterprise(client):
    """标签/值分列表头：不得把「编制单位」「纳税人识别号」当值建档。"""
    payload = _balance_xlsx(
        100.0, company="分列公司", tax_number="91310000T3A000019", separate_header=True
    )
    resp = _upload(client, payload)
    assert resp.status_code == 200, resp.text
    assert resp.json()["persisted"] is True

    ents = _db_all(Enterprise)
    assert len(ents) == 1
    assert ents[0].name == "分列公司"
    assert ents[0].tax_number == "91310000T3A000019"
    assert ents[0].total_assets == pytest.approx(100.0)


def test_invalid_tax_number_format_rejected_without_enterprise(client):
    data = {"tax_number": "1234", "enterprise_name": "格式错误企业"}
    resp = _upload(client, _income_xlsx("2026年1季度", company="格式错误企业"), data=data)
    assert resp.status_code == 400, resp.text
    assert "格式" in resp.json()["detail"]
    assert _db_count(Enterprise) == 0
    assert _db_count(FinancialData) == 0


def test_tax_number_without_name_rejected_without_enterprise(client):
    resp = _upload(
        client, _income_xlsx("2026年1季度", company=None),
        data={"tax_number": "91310000T3A000022"},
    )
    assert resp.status_code == 400, resp.text
    assert "企业名称" in resp.json()["detail"]
    assert _db_count(Enterprise) == 0
    assert _db_count(FinancialData) == 0


def test_same_name_two_enterprises_returns_409(client):
    _enterprise(client, tax_number="91310000T3A000023", name="同名冲突企业")
    _enterprise(client, tax_number="91310000T3A000024", name="同名冲突企业")
    resp = _upload(client, _income_xlsx("2026年1季度", company="同名冲突企业"))
    assert resp.status_code == 409, resp.text
    assert "名称相同" in resp.json()["detail"]
    assert _db_count(FinancialData) == 0


# ---------------------------------------------------------------- 复审 N-1/N-2/N-3


def test_legacy_nonconforming_tax_number_existing_enterprise_can_import(client):
    """N-1：历史不合规税号先按精确匹配命中既有企业，格式护栏只拦新建。"""
    ent = _enterprise(client, tax_number="TEST001", name="历史税号企业")
    resp = _upload(
        client,
        _income_xlsx("2026年1季度", 300.0, company="历史税号企业", tax_number="TEST001"),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["persisted"] is True
    assert body["enterprise_id"] == ent["id"]
    assert _db_count(Enterprise) == 1
    assert _db_count(FinancialData) == 1


def test_legacy_lowercase_tax_number_matches_case_insensitively(client):
    """N-1：既有小写税号经 upper 兜底匹配，不重复建档、不被格式护栏拦截。"""
    ent = _enterprise(client, tax_number="legacy001", name="小写税号企业")
    resp = _upload(
        client,
        _income_xlsx("2026年1季度", 300.0, company="小写税号企业", tax_number="LEGACY001"),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["enterprise_id"] == ent["id"]
    assert _db_count(Enterprise) == 1
    assert _db_count(FinancialData) == 1


def test_import_to_enterprise_rejects_mismatched_tax_number(client):
    """N-2：文件表头税号与所选企业不同 → 中文 400，绝不把数据写到错误企业。"""
    ent = _enterprise(client, tax_number="91310000T3A000030", name="甲企业")
    resp = _upload(
        client,
        _income_xlsx("2026年1季度", 300.0, company="甲企业", tax_number="91310000T3A000031"),
        url=f"{IMPORT}/{ent['id']}",
    )
    assert resp.status_code == 400, resp.text
    detail = resp.json()["detail"]
    assert "不一致" in detail and "纳税人识别号" in detail
    assert _db_count(FinancialData) == 0


def test_import_to_enterprise_rejects_mismatched_name(client):
    """N-2：文件只有编制单位且与所选企业不同 → 中文 400。"""
    ent = _enterprise(client, tax_number="91310000T3A000032", name="甲企业")
    resp = _upload(
        client,
        _income_xlsx("2026年1季度", 300.0, company="乙企业", tax_number=None),
        url=f"{IMPORT}/{ent['id']}",
    )
    assert resp.status_code == 400, resp.text
    detail = resp.json()["detail"]
    assert "不一致" in detail and "编制单位" in detail
    assert _db_count(FinancialData) == 0


def test_import_to_enterprise_allows_matching_identity(client):
    """N-2：文件身份与所选企业一致（税号大小写不敏感）时正常落库。"""
    ent = _enterprise(client, tax_number="91310000T3A000033", name="甲企业")
    resp = _upload(
        client,
        _income_xlsx("2026年1季度", 300.0, company="甲企业", tax_number="91310000t3a000033"),
        url=f"{IMPORT}/{ent['id']}",
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["persisted"] is True
    assert _db_count(FinancialData) == 1


def test_import_to_enterprise_allows_file_without_identity(client):
    """N-2：文件未明确身份时不做比对，按所选企业落库。"""
    ent = _enterprise(client, tax_number="91310000T3A000034", name="甲企业")
    resp = _upload(
        client,
        _income_xlsx("2026年1季度", 300.0, company=None, tax_number=None),
        url=f"{IMPORT}/{ent['id']}",
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["persisted"] is True
    assert _db_count(FinancialData) == 1


def test_scan_meta_sheet_ignores_right_cell_label(tmp_path):
    """N-3：标准模板元数据扫描与 _parse_header 同口径，右格是标签时不取值。"""
    wb = Workbook()
    ws = wb.active
    ws.title = "利润表"
    ws["D1"] = "编制单位"
    ws["E1"] = "纳税人识别号"
    ws["F1"] = "91310000T3A000035"
    ws["H1"] = "报表期间"
    ws["I1"] = "2026年1季度"
    path = tmp_path / "meta.xlsx"
    wb.save(path)

    meta = StandardTemplateParser._read_metadata(str(path))
    assert meta.get("company_name", "") == ""
    assert meta["tax_number"] == "91310000T3A000035"
    assert meta["period"] == "2026年1季度"


# ---------------------------------------------------------------- R11-006 企业删除保护


def test_delete_enterprise_with_submitted_return_returns_409_and_keeps_return(client):
    ent = _enterprise(client)
    ret = _save_return(client, ent["id"])
    assert client.put(f"{P}/tax-returns/{ret['id']}/submit").status_code == 200

    resp = client.delete(f"{P}/enterprises/{ent['id']}")
    assert resp.status_code == 409, resp.text
    detail = resp.json()["detail"]
    assert "已提交" in detail and "不能删除" in detail

    assert client.get(f"{P}/enterprises/{ent['id']}").status_code == 200
    kept = client.get(f"{P}/tax-returns/{ret['id']}")
    assert kept.status_code == 200
    assert kept.json()["status"] == "submitted"
    assert _db_count(TaxReturn) == 1
    assert _db_count(Enterprise) == 1


def test_delete_enterprise_with_draft_return_cascades(client):
    ent = _enterprise(client, tax_number="91310000T3A000002", name="草稿企业")
    ret = _save_return(client, ent["id"])

    resp = client.delete(f"{P}/enterprises/{ent['id']}")
    assert resp.status_code == 200, resp.text
    assert client.get(f"{P}/enterprises/{ent['id']}").status_code == 404
    assert client.get(f"{P}/tax-returns/{ret['id']}").status_code == 404
    assert _db_count(TaxReturn) == 0


# ---------------------------------------------------------------- R11-007 财报导入持久化


def test_import_to_enterprise_persists_financial_data(client):
    ent = _enterprise(client)
    resp = _upload(client, _income_xlsx("2026年1季度", 300.0), url=f"{IMPORT}/{ent['id']}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["success"] is True
    assert body["persisted"] is True
    assert body["enterprise_id"] == ent["id"]
    assert body["financial_data_id"]

    rows = _db_all(FinancialData)
    assert len(rows) == 1
    row = rows[0]
    assert (row.enterprise_id, row.year, row.quarter) == (ent["id"], 2026, 1)
    assert row.total_revenue == pytest.approx(300.0)
    assert row.total_cost == pytest.approx(150.0)
    assert row.profit == pytest.approx(100.0)


def test_import_to_enterprise_validation_error_does_not_persist(client):
    ent = _enterprise(client)
    resp = _upload(
        client, _income_xlsx("2026年1季度", revenue_wan=-100.0),
        url=f"{IMPORT}/{ent['id']}",
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["success"] is False
    assert body["persisted"] is False
    assert _db_count(FinancialData) == 0


def test_persist_is_private_dependency_not_public_query(client):
    """落库开关是私有依赖，不是公开查询参数；外部客户端不能绕过。"""
    import inspect

    from fastapi.params import Depends as DependsMarker
    from fastapi.params import Query as QueryMarker

    from app.modules.finance_tax.routers import financial_import as fi

    resolve = getattr(fi, "_resolve_persist", None)
    assert resolve is not None, "缺少显式 persist 接缝"
    assert resolve(True) is True
    assert resolve(False) is False
    assert resolve(None) is False

    for fn in (fi.import_financial_statement, fi.import_financial_statement_to_enterprise):
        default = inspect.signature(fn).parameters["persist"].default
        assert isinstance(default, DependsMarker), "persist 应由私有依赖注入"
        assert not isinstance(default, QueryMarker), "不得暴露为公开查询参数"

    schema = client.get("/openapi.json").json()
    for path in (
        "/api/finance/tax/financial-import/import",
        "/api/finance/tax/financial-import/import/{enterprise_id}",
    ):
        params = schema["paths"][path]["post"].get("parameters", [])
        assert "persist" not in {p["name"] for p in params}


def test_identity_from_result_ignores_form_markers():
    """直接调用端点时 tax_number/enterprise_name 是 Form 默认对象，不得进 re.sub。"""
    from fastapi import Form

    from app.modules.finance_tax.routers import financial_import as fi

    result = importer_mod.ImportResult(success=True, summary="x")
    result.mapped_data = {
        "enterprise_info": {"name": "雷雨公司", "tax_number": "91310000t3a000026"}
    }
    tax, name = fi._identity_from_result(result, Form(None), Form(None))
    assert tax == "91310000T3A000026"
    assert name == "雷雨公司"


def test_direct_endpoint_call_with_form_defaults_persists(client, monkeypatch):
    """直接调用端点（未经 FastAPI 注入）时 Form 默认对象不得崩溃，显式 persist 可落库。"""
    import asyncio

    from fastapi import UploadFile

    from app.modules.finance_tax.routers import financial_import as fi

    def fake_import(_path):
        result = importer_mod.ImportResult(success=True, summary="合成导入完成")
        result.mapped_data = {
            "format": "standard_template",
            "enterprise_info": {"name": "直接调用企业", "tax_number": "91310000T3A000026"},
            "financial_data": {
                "year": 2026,
                "quarter": 1,
                "total_revenue": 300.0,
                "total_cost": 150.0,
                "total_expense": 20.0,
                "profit": 130.0,
                "non_taxable_income": 0.0,
                "prior_year_loss": 0.0,
            },
        }
        return result

    monkeypatch.setattr(fi.importer, "import_from_file", fake_import)
    upload = UploadFile(filename="direct.xlsx", file=io.BytesIO(b"xlsx-bytes"))
    with SessionLocal() as db:
        response = asyncio.run(
            fi.import_financial_statement(file=upload, db=db, persist=True)
        )

    assert response.persisted is True
    assert response.enterprise_id
    ents = _db_all(Enterprise)
    assert len(ents) == 1
    assert ents[0].name == "直接调用企业"
    assert ents[0].tax_number == "91310000T3A000026"
    assert _db_count(FinancialData) == 1


def test_http_cannot_disable_persist_via_query(client):
    """外部客户端传 ?persist=false 也不得绕过落库（参数不属于 API 契约）。"""
    ent = _enterprise(client)
    resp = client.post(
        f"{IMPORT}/{ent['id']}?persist=false",
        files={"file": ("利润表.xlsx", _income_xlsx("2026年1季度"), XLSX_MIME)},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["persisted"] is True
    assert _db_count(FinancialData) == 1


def test_repeat_import_upserts_same_enterprise_year_quarter(client):
    ent = _enterprise(client)
    url = f"{IMPORT}/{ent['id']}"
    assert _upload(client, _income_xlsx("2026年1季度", 300.0), url).json()["persisted"] is True
    first = _db_all(FinancialData)
    assert len(first) == 1

    resp2 = _upload(client, _income_xlsx("2026年1季度", 420.0), url)
    assert resp2.status_code == 200, resp2.text
    rows = _db_all(FinancialData)
    assert len(rows) == 1
    assert rows[0].id == first[0].id
    assert rows[0].total_revenue == pytest.approx(420.0)

    # 不同季度是另一条事实，不是覆盖
    assert _upload(client, _income_xlsx("2026年2季度", 500.0), url).json()["persisted"] is True
    assert _db_count(FinancialData) == 2


def test_bare_import_upserts_enterprise_by_tax_number(client):
    payload = _income_xlsx("2026-03", 300.0, company="税号建档企业")
    data = {"tax_number": "91310000T3A000009", "enterprise_name": "税号建档企业"}
    resp = _upload(client, payload, data=data)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["persisted"] is True
    assert body["enterprise_id"]

    ents = _db_all(Enterprise)
    assert len(ents) == 1
    assert ents[0].tax_number == "91310000T3A000009"
    assert _db_count(FinancialData) == 1

    # 重复导入：企业不重复、财务数据同行更新
    resp2 = _upload(client, _income_xlsx("2026-03", 360.0, company="税号建档企业"), data=data)
    assert resp2.status_code == 200, resp2.text
    assert _db_count(Enterprise) == 1
    rows = _db_all(FinancialData)
    assert len(rows) == 1
    assert rows[0].total_revenue == pytest.approx(360.0)


def test_bare_import_uses_header_tax_number_and_company(client):
    """财务系统导出表头自带税号/编制单位时，无需表单参数也能建档落库。"""
    payload = _income_xlsx(
        "2026年1季度", 300.0, company="表头企业", tax_number="91310000T3A000015"
    )
    resp = _upload(client, payload)
    assert resp.status_code == 200, resp.text
    assert resp.json()["persisted"] is True

    ents = _db_all(Enterprise)
    assert len(ents) == 1
    assert ents[0].tax_number == "91310000T3A000015"
    assert ents[0].name == "表头企业"
    assert _db_count(FinancialData) == 1


def test_bare_import_matches_existing_enterprise_by_name(client):
    """仅有编制单位且已存在同名唯一企业时，把财务数据落到该企业。"""
    ent = _enterprise(client, tax_number="91310000T3A000016", name="同名已建档企业")
    resp = _upload(client, _income_xlsx("2026年1季度", 300.0, company="同名已建档企业"))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["persisted"] is True
    assert body["enterprise_id"] == ent["id"]
    assert _db_count(Enterprise) == 1
    assert _db_count(FinancialData) == 1


def test_bare_import_name_only_without_match_is_preview(client):
    resp = _upload(client, _income_xlsx("2026年1季度", 300.0, company="未建档企业"))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["persisted"] is False
    assert any(w["field"] == "enterprise" for w in body["warnings"])
    assert _db_count(Enterprise) == 0
    assert _db_count(FinancialData) == 0


def test_map_to_financial_data_quantizes_half_up_once():
    """M-4：映射层一次 HALF_UP（2.00005 → 2.0001），不用 Python 银行家 round。"""
    inc = ParsedIncomeStatement(
        operating_revenue=2.00005,
        operating_cost=2.00005,
        taxes_and_surcharges=0.0,
        total_profit=2.00005,
    )
    data = DataMapper.map_to_financial_data(inc, 2026, 1)
    assert data["total_revenue"] == 2.0001
    assert data["total_cost"] == 2.0001
    assert data["profit"] == 2.0001


def test_import_amount_response_matches_db_after_half_up(client):
    template = FinancialDataImporter.generate_template(
        include_sheets=[StatementType.INCOME_STATEMENT]
    )
    wb = openpyxl.load_workbook(io.BytesIO(template))
    ws = wb["利润表"]
    ws["D1"], ws["E1"] = "编制单位", "金额一致性企业"
    ws["F1"], ws["G1"] = "纳税人识别号", "91310000T3A000025"
    ws["H1"], ws["I1"] = "报表期间", "2026年1季度"
    for row in range(1, ws.max_row + 1):
        if ws.cell(row=row, column=1).value == "利润总额":
            ws.cell(row=row, column=2, value=2.00005)
            break
    buf = io.BytesIO()
    wb.save(buf)

    resp = _upload(client, buf.getvalue())
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["persisted"] is True
    row = _db_all(FinancialData)[0]
    assert body["financial_data"]["profit"] == 2.0001
    assert body["financial_data"]["profit"] == row.profit
    assert body["financial_data"]["total_revenue"] == row.total_revenue


def test_standard_template_metadata_persists(client):
    template = FinancialDataImporter.generate_template(
        include_sheets=[StatementType.INCOME_STATEMENT]
    )
    wb = openpyxl.load_workbook(io.BytesIO(template))
    ws = wb["利润表"]
    ws["D1"] = "编制单位"
    ws["E1"] = "标准模板企业"
    ws["F1"] = "纳税人识别号"
    ws["G1"] = "91310000T3A000010"
    ws["H1"] = "报表期间"
    ws["I1"] = "2026年1季度"
    for row in range(1, ws.max_row + 1):
        if ws.cell(row=row, column=1).value == "营业收入":
            ws.cell(row=row, column=2, value=300.0)
            break
    buf = io.BytesIO()
    wb.save(buf)

    resp = _upload(client, buf.getvalue())
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["format_detected"] == "standard_template"
    assert body["persisted"] is True

    rows = _db_all(FinancialData)
    assert len(rows) == 1
    assert (rows[0].year, rows[0].quarter) == (2026, 1)
    assert rows[0].total_revenue == pytest.approx(300.0)


def test_standard_template_blank_cells_persist_as_zero_not_nan(client):
    """模板只填部分项目时，空白单元格不得以 NaN 落库。"""
    template = FinancialDataImporter.generate_template(
        include_sheets=[StatementType.INCOME_STATEMENT]
    )
    wb = openpyxl.load_workbook(io.BytesIO(template))
    ws = wb["利润表"]
    ws["D1"], ws["E1"] = "编制单位", "部分填报企业"
    ws["F1"], ws["G1"] = "纳税人识别号", "91310000T3A000012"
    ws["H1"], ws["I1"] = "报表期间", "2026年1季度"
    for row in range(1, ws.max_row + 1):
        if ws.cell(row=row, column=1).value == "营业收入":
            ws.cell(row=row, column=2, value=300.0)
            break
    buf = io.BytesIO()
    wb.save(buf)

    resp = _upload(client, buf.getvalue())
    assert resp.status_code == 200, resp.text
    assert resp.json()["persisted"] is True
    row = _db_all(FinancialData)[0]
    assert row.total_cost == 0.0
    assert row.total_expense == 0.0
    assert row.profit == 0.0


def test_standard_template_metadata_from_any_sheet_is_used(client):
    """三表模板只在「利润表」页填元数据时同样能识别企业与期间。"""
    template = FinancialDataImporter.generate_template()
    wb = openpyxl.load_workbook(io.BytesIO(template))
    ws = wb["利润表"]
    ws["D1"], ws["E1"] = "编制单位", "跨页元数据企业"
    ws["F1"], ws["G1"] = "纳税人识别号", "91310000T3A000013"
    ws["H1"], ws["I1"] = "报表期间", "2026年2季度"
    for row in range(1, ws.max_row + 1):
        if ws.cell(row=row, column=1).value == "营业收入":
            ws.cell(row=row, column=2, value=180.0)
            break
    buf = io.BytesIO()
    wb.save(buf)

    resp = _upload(client, buf.getvalue())
    assert resp.status_code == 200, resp.text
    assert resp.json()["persisted"] is True
    row = _db_all(FinancialData)[0]
    assert (row.year, row.quarter) == (2026, 2)
    assert row.total_revenue == pytest.approx(180.0)


def test_import_without_identity_is_preview_without_period_fallback(client):
    resp = _upload(client, _income_xlsx(period=""))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["success"] is True
    assert body["persisted"] is False
    assert body["financial_data"]["year"] is None
    assert body["financial_data"]["quarter"] is None
    assert any(w["field"] == "period" for w in body["warnings"])
    assert _db_count(Enterprise) == 0
    assert _db_count(FinancialData) == 0


# ---------------------------------------------------------------- R11-021 严格期间


def test_parse_period_rejects_empty_unknown_and_out_of_range():
    error = _period_error_class()
    with pytest.raises(error, match="期间"):
        FinancialDataImporter._parse_period("")
    with pytest.raises(error, match="期间"):
        FinancialDataImporter._parse_period("2026")
    with pytest.raises(error, match="月份"):
        FinancialDataImporter._parse_period("2026年13月")
    with pytest.raises(error, match="月份"):
        FinancialDataImporter._parse_period("2026-13")
    with pytest.raises(error, match="季度"):
        FinancialDataImporter._parse_period("2026年Q5")
    with pytest.raises(error, match="期间"):
        FinancialDataImporter._parse_period("2026年度")
    with pytest.raises(error, match="期间"):
        FinancialDataImporter._parse_period("2026年第五季度")

    assert FinancialDataImporter._parse_period("2026年1季度") == (2026, 1)
    assert FinancialDataImporter._parse_period("2026年Q3") == (2026, 3)
    assert FinancialDataImporter._parse_period("2026Q4") == (2026, 4)
    assert FinancialDataImporter._parse_period("2026-03") == (2026, 1)
    assert FinancialDataImporter._parse_period("2026年12月") == (2026, 4)
    # M-7：真实报表常见中文数字季度
    assert FinancialDataImporter._parse_period("2026年第一季度") == (2026, 1)
    assert FinancialDataImporter._parse_period("2026年二季度") == (2026, 2)
    assert FinancialDataImporter._parse_period("2026年第三季度") == (2026, 3)
    assert FinancialDataImporter._parse_period("2026年四季度") == (2026, 4)


def test_import_rejects_bad_periods_without_writes(client):
    ent = _enterprise(client)
    for period in ("2026年13月", "2026-13", "2026年Q5", "2026年度", "2026"):
        resp = _upload(client, _income_xlsx(period=period), url=f"{IMPORT}/{ent['id']}")
        assert resp.status_code == 400, (period, resp.text)
        detail = resp.json()["detail"]
        assert any(word in detail for word in ("期间", "月份", "季度")), detail
    assert _db_count(FinancialData) == 0


def test_missing_period_with_identity_rejected_before_any_write(client):
    data = {"tax_number": "91310000T3A000011", "enterprise_name": "无期间企业"}
    resp = _upload(client, _income_xlsx(period=None, company="无期间企业"), data=data)
    assert resp.status_code == 400, resp.text
    assert "期间" in resp.json()["detail"]
    # 单事务：期间校验在写库之前，企业也不得残留
    assert _db_count(Enterprise) == 0
    assert _db_count(FinancialData) == 0


def test_import_to_missing_enterprise_is_preview_only(client):
    """既有契约：指定企业不存在时仍返回解析结果（200），但不落库且明确提示。"""
    resp = _upload(client, _income_xlsx("2026年1季度"), url=f"{IMPORT}/999999")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["persisted"] is False
    assert any(
        w["field"] == "enterprise" and "不存在" in w["message"]
        for w in body["warnings"]
    )
    assert _db_count(FinancialData) == 0
    assert _db_count(Enterprise) == 0


def test_financial_persist_failure_rolls_back_enterprise(client, monkeypatch):
    """单事务：财务数据写入失败时同一事务内新建的企业一并回滚。"""
    from app.modules.finance_tax.routers import financial_import as fi

    def boom(db, enterprise, financial):
        raise RuntimeError("synthetic db failure")

    monkeypatch.setattr(fi, "_upsert_financial_data", boom)
    data = {"tax_number": "91310000T3A000014", "enterprise_name": "回滚企业"}
    resp = _upload(client, _income_xlsx("2026年1季度", company="回滚企业"), data=data)
    assert resp.status_code == 500, resp.text
    assert "保存失败" in resp.json()["detail"]
    assert _db_count(Enterprise) == 0
    assert _db_count(FinancialData) == 0
