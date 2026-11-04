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


def test_bip_three_templates_end_to_end_merge(client, tmp_path, monkeypatch):
    """三份模板按真实主流程导入：凭证列表 → 辅助明细账合并 → 余额表按期间入库。

    凭证列表模板自带科目列（本次修复）：仅导凭证列表即可按科目汇总；随后同凭证
    同金额的辅助明细账只做合并补写，不新增凭证；余额表按期间 upsert。
    """
    from app.modules.finance_ledger_bip import service as bip_service

    monkeypatch.setattr(bip_service, "module_data_dir", lambda: tmp_path / "bip_data")

    def import_template(name: str, **payload) -> dict:
        with open(TPL / name, "rb") as f:
            content = f.read()
        r = client.post("/api/finance/ledger/bip/imports/preview",
                        files={"file": (name, content)})
        assert r.status_code == 200, r.text
        r2 = client.post("/api/finance/ledger/bip/imports/confirm",
                         json={"token": r.json()["token"], **payload})
        assert r2.status_code == 200, r2.text
        return r2.json()

    voucher = import_template("BIP账簿-凭证列表-模板.xlsx")
    assert voucher["inserted_vouchers"] == 1 and voucher["inserted_entries"] == 2

    summary = client.get("/api/finance/ledger/bip/balances/summary",
                         params={"period": "2026-01"}).json()
    assert summary["subject_count"] == 2  # 凭证列表自带科目同样能汇总
    assert summary["entries_without_subject"] == 0

    aux = import_template("BIP账簿-辅助明细账-模板.xlsx")
    assert aux["inserted_vouchers"] == 0 and aux["merged_vouchers"] == 1
    entries = client.get("/api/finance/ledger/bip/entries", params={"period": "2026-01"}).json()
    assert entries["total"] == 2  # 合并后仍 2 条，不因两来源翻倍

    with open(TPL / "BIP账簿-辅助余额表-模板.xlsx", "rb") as f:
        balance_preview = client.post("/api/finance/ledger/bip/imports/preview",
                                      files={"file": ("BIP账簿-辅助余额表-模板.xlsx", f.read())}).json()
    assert balance_preview["mapping"]["ledger"] == "核算账簿"
    assert balance_preview["mapping"]["opening_balance"] == "期初余额"
    assert balance_preview["mapping"]["closing_balance"] == "期末余额"

    balance = import_template("BIP账簿-辅助余额表-模板.xlsx", period="2026-01")
    assert balance["inserted_balances"] == 2 and balance["updated_balances"] == 0

    from app.database import SessionLocal
    from app.modules.finance_ledger_bip.models import BipBalance

    with SessionLocal() as db:
        rows = db.query(BipBalance).filter(BipBalance.period == "2026-01").all()
        by_code = {r.subject_code: r for r in rows}
        assert all(r.ledger == "示例账簿" for r in rows)
        assert by_code["100201"].opening_balance == 10000.0
        assert by_code["100201"].closing_balance == 10500.0
        assert by_code["220201"].opening_balance == 5000.0
        assert by_code["220201"].closing_balance == 3500.0

    again = import_template("BIP账簿-辅助余额表-模板.xlsx", period="2026-01")
    assert again["inserted_balances"] == 0
    assert again["updated_balances"] == 0  # 内容相同不谎报更正
    assert again["unchanged_balances"] == 2

    assert client.get("/api/finance/ledger/bip/imports").json()["total"] == 4


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
