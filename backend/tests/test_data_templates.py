"""数据模板回归：保证 数据模板/ 里的模板能被对应导入端点真实解析（模板失效=使用说明失效）。

模板清单与列结构依据：数据收传使用说明.md（同轮产出）；BIP 预览为整文件识别单一类型，
故 BIP 模板按真实工作流拆为三个独立文件（凭证列表/辅助明细账/辅助余额表）。
"""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.modules.finance_expense import service as expense_service

REPO = Path(__file__).resolve().parent.parent.parent
TPL = REPO / "数据模板"

pytestmark = pytest.mark.skipif(not TPL.is_dir(), reason="数据模板目录不存在（分发包可能已剔除）")


def _upload(client: TestClient, url: str, name: str, table: str = "") -> dict:
    with open(TPL / name, "rb") as f:
        files = {"file": (name, f.read())}
        data = {"table": table} if table else {}
        r = client.post(url, files=files, data=data)
    assert r.status_code == 200, r.text
    return r.json()


def test_vault_ingest_csv(client):
    out = _upload(client, "/api/finance/vault/ingest/file?table=t_tpl_csv", "数据仓-入仓模板.csv")
    assert out["rows"] == 2


def test_vault_ingest_xlsx(client):
    out = _upload(client, "/api/finance/vault/ingest/file?table=t_tpl_xlsx", "数据仓-入仓模板.xlsx")
    assert out["rows"] == 2


def test_bip_preview_detects_voucher_aux_balance(client):
    """三个 BIP 模板文件各自被识别为对应类型（预览端点为整文件识别）。"""
    expect = {
        "BIP账簿-凭证列表-模板.xlsx": "voucher",
        "BIP账簿-辅助明细账-模板.xlsx": "aux",
        "BIP账簿-辅助余额表-模板.xlsx": "balance",
    }
    for name, ftype in expect.items():
        out = _upload(client, "/api/finance/ledger/bip/imports/preview", name)
        assert out["file_type"] == ftype, (name, out.get("file_type"), out.get("type_label"))


def test_personal_csv_import(client):
    out = _upload(client, "/api/finance/ledger/personal/import/csv", "个人记账-导入模板.csv")
    assert out["imported"] == 2
    assert out["errors"] == []


def test_expense_header_row_located_on_template():
    """费用分类模板：后端表头定位函数在前 25 行内命中（行号 0 起算）。"""
    row = expense_service._locate_bank_header_row(TPL / "费用分类-银行存款辅助明细账-模板.xlsx")
    assert row is not None


def test_template_list_endpoint(client):
    """模板清单端点：10 份模板全部就位（与前端「下载模板」清单同源）。"""
    r = client.get("/api/templates")
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 10
    assert all(item["available"] for item in data["items"]), data["items"]


def test_template_download_whitelisted(client):
    """白名单模板可下载，且带中文文件名（RFC5987）与 xlsx 魔数。"""
    name = "数据仓-入仓模板.xlsx"
    r = client.get(f"/api/templates/{name}")
    assert r.status_code == 200
    assert r.content[:2] == b"PK"
    disposition = r.headers.get("content-disposition", "")
    assert "attachment" in disposition.lower()
    assert "filename*=utf-8''" in disposition.lower()


def test_template_download_rejects_unknown_name(client):
    """白名单外的名字一律 404（含路径穿越尝试），不做任何路径归一。"""
    assert client.get("/api/templates/不存在.xlsx").status_code == 404
    assert client.get("/api/templates/..%2FREADME.md").status_code == 404
