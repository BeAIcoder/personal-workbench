"""finance_pos 模块测试（stub 引擎，不触碰真实 POS 数据）。

以 tmp 里的假 generate_pos_ledger.py / analyze_pos_reconciliation.py 代替真实
引擎，验证薄壳机制：上传归集（ZIP 解包/zip-slip）、两步执行生命周期、
产物差集归集（output + input 新生成文件）、打包下载、引擎缺失 503。
"""
import zipfile

import pytest

from app.modules import get_loaded_modules
from app.modules.finance_pos import service as svc

P = "/api/finance/pos"

STUB_LEDGER = '''
import argparse
from pathlib import Path
p = argparse.ArgumentParser()
p.add_argument("--root", type=Path)
p.add_argument("--output", type=Path)
a = p.parse_args()
a.output.parent.mkdir(parents=True, exist_ok=True)
a.output.write_text("stub-pos-ledger", encoding="utf-8")
print("stub ledger done")
'''

STUB_ANALYZE = '''
import argparse
from pathlib import Path
p = argparse.ArgumentParser()
p.add_argument("--root", type=Path)
p.add_argument("--project", default=None)
a = p.parse_args()
(a.root / "POS差异分析_标注.xlsx").write_text("stub-diff", encoding="utf-8")
(a.root / "差异分析报告.md").write_text("# stub diff report", encoding="utf-8")
print("stub analyze done")
'''


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture()
def pos_dir(monkeypatch, tmp_path):
    base = tmp_path / "pos_data"
    engine = tmp_path / "pos_engine"
    engine.mkdir()
    (engine / "generate_pos_ledger.py").write_text(STUB_LEDGER, encoding="utf-8")
    (engine / "analyze_pos_reconciliation.py").write_text(STUB_ANALYZE, encoding="utf-8")
    monkeypatch.setattr(svc, "get_finance_data_dir", lambda: base)
    monkeypatch.setattr(svc, "engine_dir", lambda: engine)
    return base


def _xlsx(name: str) -> tuple[str, bytes]:
    return (name, b"stub-xlsx-content")


def test_module_registration():
    loaded = {m.name: m for m in get_loaded_modules()}
    assert "finance_pos" in loaded
    assert loaded["finance_pos"].prefix == "/api/finance/pos"


def test_engine_unavailable_503(client, monkeypatch):
    from pathlib import Path

    monkeypatch.setattr(svc, "engine_dir", lambda: Path("Z:/no/pos"))
    r = client.post(f"{P}/runs", files=[("files", _xlsx("a.xlsx"))])
    assert r.status_code == 503 and "引擎" in r.json()["detail"]


def test_full_flow_with_stub_engine(client, pos_dir):
    r = client.post(f"{P}/runs", files=[
        ("files", _xlsx("SHOP对账单.xlsx")),
        ("files", _xlsx("认领记录.xlsx")),
        ("files", _xlsx("辅助明细账.xlsx")),
    ])
    assert r.status_code == 201, r.text
    run = r.json()
    assert run["status"] == "uploaded" and len(run["input_files"]) == 3

    ex = client.post(f"{P}/runs/{run['id']}/execute")
    assert ex.status_code == 200, ex.text
    body = ex.json()
    assert body["status"] == "ok"
    names = {a["name"] for a in body["artifacts"]}
    # output 的台账 + input 下新生成的差异文件都在产物里
    assert "POS项目台账.xlsx" in names
    assert "POS差异分析_标注.xlsx" in names and "差异分析报告.md" in names

    one = client.get(f"{P}/runs/{run['id']}/artifacts/差异分析报告.md")
    assert one.status_code == 200 and "stub diff report" in one.text

    allz = client.get(f"{P}/runs/{run['id']}/download")
    assert allz.status_code == 200 and allz.content[:2] == b"PK"

    lst = client.get(f"{P}/runs")
    assert lst.json()["total"] >= 1


def test_zip_input_and_slip(client, pos_dir):
    import io

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("月份/SHOP对账单.xlsx", "data")
    r = client.post(f"{P}/runs", files=[("files", ("inputs.zip", buf.getvalue()))])
    assert r.status_code == 201
    assert any(f["name"] == "SHOP对账单.xlsx" for f in r.json()["input_files"])

    bad = io.BytesIO()
    with zipfile.ZipFile(bad, "w") as zf:
        zf.writestr("../evil.xlsx", "x")
    r2 = client.post(f"{P}/runs", files=[("files", ("inputs.zip", bad.getvalue()))])
    assert r2.status_code == 400 and "非法路径" in r2.json()["detail"]
