"""finance_pos 模块测试（stub 引擎，不触碰真实 POS 数据）。

以 tmp 里的假 generate_pos_ledger.py / analyze_pos_reconciliation.py 代替真实
引擎，验证薄壳机制：上传归集（ZIP 解包、zip-slip/同前缀绕过与解压炸弹防护）、
两步执行生命周期（含归档失败兜底回写 failed）、产物差集归集（output + input
新生成文件）、打包下载、引擎缺失 503。

加固覆盖（swarm 任务 pos-hardening）：
- 上传经共享 read_upload_limited 分块限额读取（超限 400）+ 批次总量上限；
- 建运行重活经线程池执行，不阻塞事件循环；
- ZIP 成员限业务扩展名/数量/累计解压总量，拒绝 .py、plugin.json、
  非法 Windows 文件名与路径穿越，保留合法子目录与业务文件；
- 顶层上传文件名安全净化；建运行失败回滚记录并清理半成品目录。
"""
import asyncio
import io
import threading
import zipfile

import pytest

from app.modules import get_loaded_modules
from app.modules.finance_pos import router as pos_router
from app.modules.finance_pos import service as svc
from app.upload import UploadTooLargeError

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
p.add_argument("--pos", type=Path, default=None)
p.add_argument("--ledger", type=Path, default=None)
p.add_argument("--claims", type=Path, default=None)
p.add_argument("--vouchers", type=Path, default=None)
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


def test_zip_same_prefix_slip_rejected(client, pos_dir):
    """同前缀绕过：../input_evil 解析后仍以 input 开头，字符串前缀校验会放行，必须拒绝。"""
    import io

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("../input_evil/x.txt", "bad")
    r = client.post(f"{P}/runs", files=[("files", ("inputs.zip", buf.getvalue()))])
    assert r.status_code == 400 and "非法路径" in r.json()["detail"]


def _fake_bomb_zip(name: str = "big.xlsx") -> bytes:
    """构造「声明解压后 300MB、实际内容极小」的 ZIP（篡改 local/central 目录的 usize 字段）。"""
    import io
    import struct

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(name, b"tiny")
    data = bytearray(buf.getvalue())
    fake = 300 * 1024 * 1024
    struct.pack_into("<I", data, 22, fake)       # local file header 的 uncompressed size
    cd = data.find(b"PK\x01\x02")               # central directory 首记录起点
    struct.pack_into("<I", data, cd + 24, fake)  # central directory 的 uncompressed size
    return bytes(data)


def test_zip_bomb_rejected(client, pos_dir):
    """解压炸弹：条目声明的解压后大小累计超过上限，整包拒绝。"""
    r = client.post(f"{P}/runs", files=[("files", ("inputs.zip", _fake_bomb_zip()))])
    assert r.status_code == 400 and "解压后总大小" in r.json()["detail"]


def test_execute_archive_failure_marks_failed_and_reruns(client, pos_dir, monkeypatch):
    """产物归档阶段抛错：状态落为 failed 并可重跑（不得卡在 running）。"""
    import pathlib

    r = client.post(f"{P}/runs", files=[("files", _xlsx("SHOP对账单.xlsx"))])
    assert r.status_code == 201, r.text
    run_id = r.json()["id"]

    state = {"fail": True}
    orig_write = pathlib.Path.write_bytes

    def boom(self, data):
        if state["fail"] and "artifacts" in self.parts:
            raise OSError("stub: disk full")
        return orig_write(self, data)

    monkeypatch.setattr(pathlib.Path, "write_bytes", boom)
    ex = client.post(f"{P}/runs/{run_id}/execute")
    assert ex.status_code == 503
    detail = client.get(f"{P}/runs/{run_id}").json()
    assert detail["status"] == "failed" and detail["error"]

    # 恢复归档写盘后可直接重跑（若卡在 running，此处会被 400 拒绝）
    state["fail"] = False
    ex2 = client.post(f"{P}/runs/{run_id}/execute")
    assert ex2.status_code == 200 and ex2.json()["status"] == "ok"


# ---------------- 加固：上传分块限额 + 批次总量 ----------------


async def _fake_read_upload_limited(file, max_bytes, chunk_size=1024 * 1024):
    """与 core 契约一致的测试替身：分块累积读取，超限抛真实 UploadTooLargeError。"""
    chunks, total = [], 0
    while True:
        chunk = await file.read(chunk_size)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise UploadTooLargeError(max_bytes)
        chunks.append(chunk)
    return b"".join(chunks)


def test_upload_reads_via_shared_limited_reader(client, pos_dir, monkeypatch):
    """建运行上传经共享 read_upload_limited 分块限额读取，传入模块 50MB 口径。"""
    seen: list[int] = []

    async def spy(file, max_bytes, chunk_size=1024 * 1024):
        seen.append(max_bytes)
        return await _fake_read_upload_limited(file, max_bytes, chunk_size)

    monkeypatch.setattr(pos_router, "read_upload_limited", spy, raising=False)
    r = client.post(f"{P}/runs", files=[("files", _xlsx("SHOP对账单.xlsx"))])
    assert r.status_code == 201, r.text
    assert seen == [svc.MAX_UPLOAD_BYTES]


def test_upload_over_limit_rejected_400(client, pos_dir, monkeypatch):
    """单文件超限：读取期即中断并 400（共享读取器真实生效，非 service 事后校验）。"""
    monkeypatch.setattr(svc, "MAX_UPLOAD_BYTES", 16)
    r = client.post(f"{P}/runs", files=[("files", _xlsx("big.xlsx"))])
    assert r.status_code == 400
    assert "上传内容超过" in r.json()["detail"]


def test_upload_batch_total_limit_rejected_400(client, pos_dir, monkeypatch):
    """批次总量上限：多文件合计超限即 400，且不遗留任何运行目录。"""
    monkeypatch.setattr(svc, "MAX_BATCH_BYTES", 24, raising=False)
    r = client.post(f"{P}/runs", files=[("files", _xlsx("a.xlsx")), ("files", _xlsx("b.xlsx"))])
    assert r.status_code == 400
    assert "总大小" in r.json()["detail"]
    runs_root = pos_dir / "finance_pos" / "runs"
    assert not runs_root.exists() or not any(runs_root.iterdir()), "批次超限不得遗留运行目录"


def test_create_run_offloads_sync_work_to_thread(monkeypatch):
    """建运行重活（service.create_run）必须在线程池执行，不占事件循环线程。"""
    seen = {}

    class _FakeUpload:
        filename = "a.xlsx"

        async def read(self, size=-1):
            if "read_thread" not in seen:
                seen["read_thread"] = threading.current_thread()
                return b"data"
            return b""

    def _fake_create_run(db, uploads):
        seen["work_thread"] = threading.current_thread()
        return {"id": 1, "status": "uploaded", "input_files": [], "artifacts": [],
                "error": "", "elapsed_sec": 0.0, "log_tail": "", "created_at": ""}

    monkeypatch.setattr(pos_router.service, "create_run", _fake_create_run)
    result = asyncio.run(pos_router.create_run(files=[_FakeUpload()], db=object()))
    assert result["id"] == 1
    # 重活线程必须不同于读上传所在（事件循环）线程
    assert seen["work_thread"] is not seen["read_thread"]


# ---------------- 加固：ZIP 成员限制 ----------------


def _zip_bytes(entries: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in entries.items():
            zf.writestr(name, content)
    return buf.getvalue()


def test_zip_rejects_py_member(client, pos_dir):
    """ZIP 成员含 .py：整包拒绝 400，不解包落盘。"""
    r = client.post(f"{P}/runs", files=[("files", ("inputs.zip", _zip_bytes({"evil.py": "print(1)"})))])
    assert r.status_code == 400
    assert "不支持的文件类型" in r.json()["detail"]


def test_zip_rejects_plugin_json_member(client, pos_dir):
    """ZIP 成员含 plugin.json：整包拒绝 400（插件载荷不得进运行目录）。"""
    r = client.post(f"{P}/runs", files=[("files", ("inputs.zip", _zip_bytes({"plugin.json": "{}"})))])
    assert r.status_code == 400
    assert "不支持的文件类型" in r.json()["detail"]


def test_zip_rejects_too_many_members(client, pos_dir, monkeypatch):
    """ZIP 成员数量上限：monkeypatch 小阈值验证，超限 400（不生成巨型 ZIP）。"""
    monkeypatch.setattr(svc, "MAX_ZIP_MEMBERS", 3, raising=False)
    entries = {f"f{i}.xlsx": "x" for i in range(4)}
    r = client.post(f"{P}/runs", files=[("files", ("inputs.zip", _zip_bytes(entries)))])
    assert r.status_code == 400
    assert "成员过多" in r.json()["detail"]


def test_zip_allows_business_formats_and_subdirs(client, pos_dir):
    """合法业务格式与子目录保留：xlsx/xls 成员带子目录正常解包。"""
    payload = _zip_bytes({"月份/SHOP对账单.xlsx": "data", "子目录/认领记录.xls": "data"})
    r = client.post(f"{P}/runs", files=[("files", ("inputs.zip", payload))])
    assert r.status_code == 201, r.text
    names = {f["name"] for f in r.json()["input_files"]}
    assert names == {"SHOP对账单.xlsx", "认领记录.xls"}


def test_zip_rejects_illegal_windows_member_names(client, pos_dir):
    """ZIP 成员名非法（Windows 非法字符/设备保留名）：整包拒绝 400。"""
    for bad in ("bad:name.xlsx", "CON.xlsx", "aux<>.xls"):
        r = client.post(f"{P}/runs", files=[("files", ("inputs.zip", _zip_bytes({bad: "x"})))])
        assert r.status_code == 400, bad
        assert "非法文件名" in r.json()["detail"], bad


# ---------------- 加固：安全文件名与失败清理 ----------------


def test_upload_filename_sanitized_for_windows(client, pos_dir):
    """顶层上传文件名净化：非法字符替换、设备保留名加前缀，安全落盘。"""
    r = client.post(f"{P}/runs", files=[("files", _xlsx("a<b>.xlsx"))])
    assert r.status_code == 201, r.text
    assert [f["name"] for f in r.json()["input_files"]] == ["a_b_.xlsx"]

    r2 = client.post(f"{P}/runs", files=[("files", _xlsx("CON.xlsx"))])
    assert r2.status_code == 201, r2.text
    assert [f["name"] for f in r2.json()["input_files"]] == ["_CON.xlsx"]


def test_create_run_failure_cleans_up_run_dir(client, pos_dir):
    """建运行失败（ZIP 成员类型被拒）：400、不新增运行记录、无半成品目录。"""
    before = client.get(f"{P}/runs").json()["total"]
    r = client.post(f"{P}/runs", files=[("files", ("inputs.zip", _zip_bytes({"evil.py": "x"})))])
    assert r.status_code == 400
    assert client.get(f"{P}/runs").json()["total"] == before
    runs_root = pos_dir / "finance_pos" / "runs"
    assert not runs_root.exists() or not any(runs_root.iterdir()), "失败后不得遗留半成品目录"


def test_shared_upload_reader_wired_into_pos_router():
    """POS 路由确实接上共享读取器（与 app.upload 同一对象，非回退分支）。"""
    from app.upload import read_upload_limited as shared_reader

    assert pos_router.read_upload_limited is shared_reader
