"""批次 finance-tax-invoice 的 bugfix 回归测试（B1-B5，四路审计挖出）。

每条用例对应一个已确认 bug：TDD 流程下先有此测试（RED），修复后转绿（GREEN）。
- B1：申报记录导出 Excel/PDF 引用不存在的 tax_period 属性 + 中文文件名 latin-1 崩溃 → 500；
- B2：创建申报记录 tax_period/quarter 无校验 → ValueError/CheckConstraint 500；
- B3：企业创建静默丢 legal_person/contact_phone/address/tax_type（模型无列、创建不映射）；
- B4：发票批次解析异常状态永留 uploaded + 接口 500；
- B5：clone_policy 同一 new_version 克隆两次 → policy_number 唯一约束 500。
"""
from urllib.parse import quote

import pytest

from app.config import settings
from app.modules.finance_invoice import service as invoice_service

P = "/api/finance/tax"
PI = "/api/finance/invoice"


def _create_enterprise(client, tax_number="91110108BUGFIX0001", **kw):
    payload = {"name": "测试修复科技有限公司", "tax_number": tax_number}
    payload.update(kw)
    r = client.post(f"{P}/enterprises/", json=payload)
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


def _create_return(client, enterprise_id, **kw):
    payload = {"enterprise_id": enterprise_id, "tax_period": "2026", "report_type": "A类"}
    payload.update(kw)
    r = client.post(f"{P}/tax-returns/", json=payload)
    assert r.status_code == 200, r.text
    return r.json()["id"]


# ---------------- B1：申报记录导出 Excel/PDF 500 ----------------

def test_b1_export_excel_ok_and_rfc5987_filename(client):
    """B1：导出 Excel 应 200，Content-Disposition 携带 RFC5987 编码的中文文件名 + ASCII 回退。"""
    eid = _create_enterprise(client)
    rid = _create_return(client, eid)
    r = client.get(f"{P}/tax-returns/{rid}/export/excel")
    assert r.status_code == 200, r.text
    assert r.content[:2] == b"PK"  # xlsx 即 zip 魔数
    cd = r.headers["content-disposition"]
    assert cd.startswith("attachment;")
    assert 'filename="' in cd  # ASCII 回退文件名
    assert "filename*=UTF-8''" in cd  # RFC5987 编码的中文文件名
    assert quote("测试修复科技有限公司") in cd


def test_b1_export_pdf_ok_and_rfc5987_filename(client, monkeypatch):
    """B1：导出 PDF 应 200（reportlab 非本环境依赖，stub 掉真实渲染，只验证路由与头部逻辑）。"""
    from app.modules.finance_tax.routers import tax_returns as tax_returns_router

    monkeypatch.setattr(
        tax_returns_router,
        "export_a200000_pdf",
        lambda form_data, output_path=None: b"%PDF-1.4 fake-bytes",
    )
    eid = _create_enterprise(client, tax_number="91110108BUGFIX0002")
    rid = _create_return(client, eid)
    r = client.get(f"{P}/tax-returns/{rid}/export/pdf")
    assert r.status_code == 200, r.text
    assert r.content.startswith(b"%PDF")
    cd = r.headers["content-disposition"]
    assert "filename*=UTF-8''" in cd
    assert quote("测试修复科技有限公司") in cd


def test_b1_export_pdf_real_renderer_available(client):
    """PDF 导出依赖回归：reportlab 必须在 requirements 中（缺失时接口 500）。"""
    eid = _create_enterprise(client, tax_number="91110108BUGFIX0003")
    rid = _create_return(client, eid)
    r = client.get(f"{P}/tax-returns/{rid}/export/pdf")
    assert r.status_code == 200, r.text
    assert r.content.startswith(b"%PDF-")
    assert "filename*=UTF-8''" in r.headers["content-disposition"]


def test_b1_download_headers_strips_crlf_injection():
    """CR/LF 是 ASCII，不会被 encode('ascii','ignore') 丢掉；进响应头即头注入。"""
    from app.modules.finance_tax.routers.tax_returns import _download_headers

    cd = _download_headers('A200000_x\r\nX-Injected: 1.xlsx')["Content-Disposition"]
    assert "\r" not in cd and "\n" not in cd
    assert cd.count("attachment;") == 1
    assert 'filename="A200000_xX-Injected: 1.xlsx"' in cd
    # 引号/反斜杠同样剔除，避免闭合 filename 引号
    cd2 = _download_headers('a"b\\c.xlsx')["Content-Disposition"]
    assert 'filename="abc.xlsx"' in cd2
    # 纯中文且无扩展名：回退名必须是纯 ASCII（原实现会把中文拼回去再崩 latin-1）
    cd3 = _download_headers("年度报表")["Content-Disposition"]
    assert 'filename="A200000.xlsx"' in cd3
    assert quote("年度报表") in cd3


# ---------------- B2：创建申报记录 tax_period/quarter 无校验 500 ----------------

def test_b2_create_return_invalid_tax_period_422(client):
    """B2：tax_period 非 4 位数字（如 "2024年度"）应 422，而非 ValueError 500。"""
    eid = _create_enterprise(client, tax_number="91110108BUGFIX0003")
    r = client.post(f"{P}/tax-returns/", json={
        "enterprise_id": eid, "tax_period": "2024年度", "report_type": "A类",
    })
    assert r.status_code == 422, r.text


def test_b2_create_return_invalid_quarter_422(client):
    """B2：quarter 超出 Q1-Q4（如 "Q5"）应 422，而非 CheckConstraint 500。"""
    eid = _create_enterprise(client, tax_number="91110108BUGFIX0004")
    r = client.post(f"{P}/tax-returns/", json={
        "enterprise_id": eid, "tax_period": "2026", "quarter": "Q5", "report_type": "A类",
    })
    assert r.status_code == 422, r.text


def test_b2_create_return_valid_period_and_quarter_200(client):
    """B2：合法输入（4 位年度 + Q1-Q4）正常创建，quarter 映射为 int。"""
    eid = _create_enterprise(client, tax_number="91110108BUGFIX0005")
    r = client.post(f"{P}/tax-returns/", json={
        "enterprise_id": eid, "tax_period": "2026", "quarter": "Q3", "report_type": "A类",
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["year"] == 2026 and body["quarter"] == 3


# ---------------- B3：企业创建静默丢字段 ----------------

def test_b3_enterprise_create_keeps_all_fields(client):
    """B3：创建企业时 legal_person/contact_phone/address/tax_type 应入库并在详情可读。"""
    r = client.post(f"{P}/enterprises/", json={
        "name": "全字段测试企业",
        "tax_number": "91110108BUGFIX0006",
        "legal_person": "张三",
        "contact_phone": "13800000000",
        "address": "北京市海淀区示例路1号",
        "industry": "软件",
        "tax_type": "核定征收",
    })
    assert r.status_code in (200, 201), r.text
    eid = r.json()["id"]

    detail = client.get(f"{P}/enterprises/{eid}")
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["legal_person"] == "张三"
    assert body["contact_phone"] == "13800000000"
    assert body["address"] == "北京市海淀区示例路1号"
    assert body["tax_type"] == "核定征收"


# ---------------- B4：发票批次解析异常状态永留 uploaded + 接口 500 ----------------

@pytest.fixture()
def invoice_tmp_dir(tmp_path, monkeypatch):
    """模块数据目录重定向到 tmp_path（不碰真实 finance 数据目录）。"""
    monkeypatch.setattr(settings, "finance_data_dir", str(tmp_path / "finance"))
    return tmp_path / "finance"


def test_b4_batch_parse_failure_marks_failed_and_no_5xx(client, invoice_tmp_dir, monkeypatch):
    """B4：解析流程抛异常 → 批次置 failed+error 留痕，接口返回明确错误而非 500。"""
    monkeypatch.setattr(invoice_service, "_require_engine", lambda: None)

    def boom(pdf_path):
        raise OSError("disk gone")

    monkeypatch.setattr(invoice_service, "_parse_pdf_with_engine", boom)

    resp = client.post(
        f"{PI}/batches",
        files=[("files", ("a.pdf", b"%PDF-1.4 synthetic", "application/pdf"))],
    )
    assert resp.status_code < 500, resp.text
    assert resp.status_code == 400
    assert "解析失败" in resp.json()["detail"]

    batches = client.get(f"{PI}/batches").json()
    assert len(batches) == 1
    assert batches[0]["status"] == "failed"  # 不再永留 uploaded
    assert "OSError" in batches[0]["error"] and "disk gone" in batches[0]["error"]


# ---------------- B5：clone_policy 唯一约束崩 ----------------

def test_b5_clone_policy_duplicate_version_400(client):
    """B5：同一 new_version 重复克隆应 400 明确提示，而非 policy_number 唯一约束 500。"""
    r = client.post(f"{P}/policies/", json={
        "policy_number": "财税〔2026〕BUGFIX01号",
        "title": "bugfix 测试政策",
        "effective_date": "2026-01-01T00:00:00",
        "policy_type": "其他",
    })
    assert r.status_code == 201, r.text
    pid = r.json()["id"]

    first = client.post(f"{P}/policies/{pid}/clone", params={"new_version": "2.0"})
    assert first.status_code == 201, first.text
    assert first.json()["version"] == "2.0"

    second = client.post(f"{P}/policies/{pid}/clone", params={"new_version": "2.0"})
    assert second.status_code == 400, second.text
    assert "已存在" in second.json()["detail"]
