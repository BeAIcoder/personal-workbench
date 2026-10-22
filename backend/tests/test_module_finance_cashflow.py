"""finance_cashflow 模块测试（stub 引擎，不触碰真实 V1 数据）。

引擎以 tmp 里的假 cashflow_autofill.py 代替（解析 --source-dir/--output-dir/
--actual-month 并写一个假产物），验证薄壳机制：上传归集（含 ZIP 解包、
zip-slip/同前缀绕过与解压炸弹防护）、执行生命周期（含归档失败兜底回写
failed）、产物下载（单文件/打包）、引擎缺失 503。
数据目录全部 monkeypatch 到 tmp_path。

加固覆盖（swarm 任务 cashflow-hardening / cashflow-review-fix）：
- 上传经共享 read_upload_limited 分块限额读取（超限读取期即 400），
  路由顶层直连共享模块（fail-closed，无回退整读）；
- ZIP 成员限业务扩展名/数量/累计解压总量，成员名逐段拒绝非法 Windows
  文件名（含设备保留名，不静默净化），保留合法子目录与业务文件；
- list_runs 真实分页（真实 total + offset/limit）；建运行重活经线程池执行；
- 建运行失败回滚记录并清理半成品目录（含 ZIP 部分解包后失败）。
"""
import zipfile

import pytest

from app.modules import get_loaded_modules
from app.modules.finance_cashflow import service as svc

P = "/api/finance/cashflow"

STUB_ENGINE = '''
import argparse
from pathlib import Path

p = argparse.ArgumentParser()
p.add_argument("--source-dir", type=Path)
p.add_argument("--output-dir", type=Path)
p.add_argument("--actual-month", type=int)
p.add_argument("--bank-file", type=Path, default=None)
a = p.parse_args()
src = sorted(x.name for x in a.source_dir.iterdir()) if a.source_dir.is_dir() else []
out = a.output_dir
out.mkdir(parents=True, exist_ok=True)
(out / f"现金流量滚动预测_已填报_{a.actual_month}月.xlsx").write_text(
    f"stub-fill month={a.actual_month} inputs={src}", encoding="utf-8")
(out / "填报报告.md").write_text("# stub report", encoding="utf-8")
print("stub engine done")
'''


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture()
def cash_dir(monkeypatch, tmp_path):
    """数据目录 + 引擎目录（stub）都重定向到 tmp_path。"""
    base = tmp_path / "cash_data"
    engine = tmp_path / "engine"
    engine.mkdir()
    (engine / "cashflow_autofill.py").write_text(STUB_ENGINE, encoding="utf-8")
    monkeypatch.setattr(svc, "get_finance_data_dir", lambda: base)
    monkeypatch.setattr(svc, "engine_dir", lambda: engine)
    return base


def _xlsx(name: str) -> tuple[str, bytes]:
    return (name, b"stub-xlsx-content")


def test_module_registration():
    loaded = {m.name: m for m in get_loaded_modules()}
    assert "finance_cashflow" in loaded
    assert loaded["finance_cashflow"].prefix == "/api/finance/cashflow"


def test_engine_unavailable_503(client, monkeypatch):
    from pathlib import Path

    monkeypatch.setattr(svc, "engine_dir", lambda: Path("Z:/no/engine"))
    r = client.post(f"{P}/runs", data={"actual_month": 9},
                    files=[("files", _xlsx("a.xlsx"))])
    assert r.status_code == 503 and "引擎" in r.json()["detail"]


def test_full_flow_with_stub_engine(client, cash_dir):
    # 建运行（多文件 + 一个 ZIP）
    buf = __import__("io").BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("数据源/银行存款辅助明细账.xlsx", "bank-data")
        zf.writestr("数据源/模板.xlsx", "template")
    r = client.post(f"{P}/runs", data={"actual_month": 9},
                    files=[("files", _xlsx("凭证列表.xlsx")), ("files", ("inputs.zip", buf.getvalue()))])
    assert r.status_code == 201, r.text
    run = r.json()
    assert run["status"] == "uploaded" and run["actual_month"] == 9
    names = {f["name"] for f in run["input_files"]}
    assert "凭证列表.xlsx" in names and "银行存款辅助明细账.xlsx" in names  # ZIP 已解包

    # 执行（同步 stub，秒回）
    ex = client.post(f"{P}/runs/{run['id']}/execute")
    assert ex.status_code == 200, ex.text
    body = ex.json()
    assert body["status"] == "ok"
    arts = {a["name"] for a in body["artifacts"]}
    assert "现金流量滚动预测_已填报_9月.xlsx" in arts and "填报报告.md" in arts

    # 列表/详情
    lst = client.get(f"{P}/runs")
    assert lst.status_code == 200 and lst.json()["total"] >= 1

    # 单产物下载
    one = client.get(f"{P}/runs/{run['id']}/artifacts/填报报告.md")
    assert one.status_code == 200 and "stub report" in one.text

    # 打包下载
    allz = client.get(f"{P}/runs/{run['id']}/download")
    assert allz.status_code == 200 and allz.content[:2] == b"PK"

    # 越权产物名 → 404
    assert client.get(f"{P}/runs/{run['id']}/artifacts/..%2F..%2Fetc%2Fpasswd").status_code in (404, 400)


def test_zip_slip_rejected(client, cash_dir):
    import io

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("../evil.xlsx", "bad")
    r = client.post(f"{P}/runs", data={"actual_month": 9},
                    files=[("files", ("inputs.zip", buf.getvalue()))])
    assert r.status_code == 400 and "非法路径" in r.json()["detail"]


def test_bad_month_rejected(client, cash_dir):
    r = client.post(f"{P}/runs", data={"actual_month": 13},
                    files=[("files", _xlsx("a.xlsx"))])
    assert r.status_code == 422  # Form ge/le 校验


def test_zip_same_prefix_slip_rejected(client, cash_dir):
    """同前缀绕过：../input_evil 解析后仍以 input 开头，字符串前缀校验会放行，必须拒绝。"""
    import io

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("../input_evil/x.txt", "bad")
    r = client.post(f"{P}/runs", data={"actual_month": 9},
                    files=[("files", ("inputs.zip", buf.getvalue()))])
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


def test_zip_bomb_rejected(client, cash_dir):
    """解压炸弹：条目声明的解压后大小累计超过上限，整包拒绝。"""
    r = client.post(f"{P}/runs", data={"actual_month": 9},
                    files=[("files", ("inputs.zip", _fake_bomb_zip()))])
    assert r.status_code == 400 and "解压后总大小" in r.json()["detail"]


def test_upload_filename_sanitized_for_windows(client, cash_dir):
    """Windows 非法字符/超长名不得导致 500：清洗后正常建运行并落盘。"""
    illegal = '<>:"|?*'
    r = client.post(f"{P}/runs", data={"actual_month": 9},
                    files=[("files", ("bad<>:|?.xlsx", b"x"))])
    assert r.status_code == 201, r.text
    name = r.json()["input_files"][0]["name"]
    assert name.endswith(".xlsx") and not (set(name) & set(illegal)), name

    long_name = "a" * 240 + ".xlsx"
    r = client.post(f"{P}/runs", data={"actual_month": 9},
                    files=[("files", (long_name, b"x"))])
    assert r.status_code == 201, r.text
    name2 = r.json()["input_files"][0]["name"]
    assert name2.endswith(".xlsx") and len(name2) <= 120, name2

    stored = list((cash_dir / "finance_cashflow" / "runs").rglob("*.xlsx"))
    assert len(stored) == 2
    for path in stored:
        assert not (set(path.name) & set(illegal)), path.name
        assert len(path.name) <= 255


def test_zip_rejects_illegal_windows_member_names(client, cash_dir):
    """ZIP 成员名逐段严校验：Windows 非法字符/设备保留名（文件段或子目录段）
    整包拒绝 400，不做静默净化——ZIP 内容不可信（顶层上传名才走 _safe_name）。"""
    import io

    for bad in ("bad:name.xlsx", "CON.xlsx", "aux<>.xls", "数据<>:/x.xlsx", "CON/y.xlsx"):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr(bad, "x")
        r = client.post(f"{P}/runs", data={"actual_month": 9},
                        files=[("files", ("inputs.zip", buf.getvalue()))])
        assert r.status_code == 400, bad
        assert "非法文件名" in r.json()["detail"], (bad, r.json()["detail"])


def test_corrupt_zip_rejected_400_and_cleaned(client, cash_dir):
    """损坏 ZIP 应 400（BadZipFile 曾被漏捕获成 500），且不留下半成品运行。"""
    before_total = client.get(f"{P}/runs").json()["total"]
    root = cash_dir / "finance_cashflow" / "runs"
    before_dirs = {p.name for p in root.iterdir()} if root.exists() else set()
    r = client.post(f"{P}/runs", data={"actual_month": 9},
                    files=[("files", ("broken.zip", b"not-a-zip"))])
    assert r.status_code == 400 and "ZIP" in r.json()["detail"]
    assert client.get(f"{P}/runs").json()["total"] == before_total
    after_dirs = {p.name for p in root.iterdir()} if root.exists() else set()
    assert after_dirs == before_dirs


def test_execute_archive_failure_marks_failed_and_reruns(client, cash_dir, monkeypatch):
    """产物归档阶段抛错：状态落为 failed 并可重跑（不得卡在 running）。"""
    import pathlib

    r = client.post(f"{P}/runs", data={"actual_month": 9},
                    files=[("files", _xlsx("a.xlsx"))])
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


# ==================== cashflow-hardening 新增用例 ====================

def test_zip_rejects_py_member(client, cash_dir):
    """ZIP 成员含 .py：必须整包拒绝（不得解出可执行脚本）。"""
    import io

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("数据源/银行存款辅助明细账.xlsx", "bank")
        zf.writestr("evil.py", "import os; os.system('x')")
    r = client.post(f"{P}/runs", data={"actual_month": 9},
                    files=[("files", ("inputs.zip", buf.getvalue()))])
    assert r.status_code == 400, r.text
    assert "不支持" in r.json()["detail"], r.json()["detail"]


def test_zip_rejects_plugin_json_member(client, cash_dir):
    """ZIP 成员含 plugin.json：必须整包拒绝（非业务数据格式）。"""
    import io

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("plugin.json", '{"hooks": []}')
    r = client.post(f"{P}/runs", data={"actual_month": 9},
                    files=[("files", ("inputs.zip", buf.getvalue()))])
    assert r.status_code == 400, r.text
    assert "不支持" in r.json()["detail"], r.json()["detail"]


def test_zip_rejects_too_many_members(client, cash_dir, monkeypatch):
    """ZIP 成员数量超上限：整包拒绝（monkeypatch 小阈值，不造大文件）。"""
    import io

    monkeypatch.setattr(svc, "MAX_ZIP_MEMBERS", 3, raising=False)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for i in range(4):
            zf.writestr(f"文件{i}.xlsx", "x")
    r = client.post(f"{P}/runs", data={"actual_month": 9},
                    files=[("files", ("inputs.zip", buf.getvalue()))])
    assert r.status_code == 400, r.text
    assert "成员" in r.json()["detail"], r.json()["detail"]


def test_zip_allows_business_formats_and_subdirs(client, cash_dir):
    """回归保护：合法业务格式 + 子目录仍正常解包（白名单不得误杀 xlsx/csv）。"""
    import io

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("数据源/明细账.xlsx", "bank")
        zf.writestr("模板/预测.csv", "a,b\n1,2")
    r = client.post(f"{P}/runs", data={"actual_month": 9},
                    files=[("files", ("inputs.zip", buf.getvalue()))])
    assert r.status_code == 201, r.text
    names = {f["name"] for f in r.json()["input_files"]}
    assert "明细账.xlsx" in names and "预测.csv" in names


def test_upload_over_limit_rejected(client, cash_dir, monkeypatch):
    """单文件超限：读取期即中断并 400（共享读取器真实生效，非 service 事后校验）。"""
    monkeypatch.setattr(svc, "MAX_UPLOAD_BYTES", 8)
    r = client.post(f"{P}/runs", data={"actual_month": 9},
                    files=[("files", _xlsx("a.xlsx"))])  # 内容长度 > 8
    assert r.status_code == 400, r.text
    assert "上传内容超过" in r.json()["detail"], r.json()["detail"]


def test_list_runs_pagination_real_total(client, cash_dir):
    """list_runs 支持 page/page_size，total 为数据库真实计数（非当页条数）。

    注：本文件的 client 夹具不复库（同文件既有测试沿用增量口径），故用 before/after 差值断言。
    """
    after0 = client.get(f"{P}/runs").json()["total"]
    ids = []
    for _ in range(5):
        r = client.post(f"{P}/runs", data={"actual_month": 9},
                        files=[("files", _xlsx("a.xlsx"))])
        assert r.status_code == 201, r.text
        ids.append(r.json()["id"])
    ids_desc = sorted(ids, reverse=True)  # 我新建的 5 条即全表最新（id 最大）
    after = client.get(f"{P}/runs").json()["total"]
    assert after == after0 + 5  # total 随新建 +5：是全量真实计数

    p1 = client.get(f"{P}/runs?page=1&page_size=2").json()
    assert p1["total"] == after               # total=全量真实计数，而非当页 2 条
    assert p1["page"] == 1 and p1["page_size"] == 2
    assert [i["id"] for i in p1["items"]] == ids_desc[:2]  # 首页即最新的 2 条

    p3 = client.get(f"{P}/runs?page=3&page_size=2").json()
    assert p3["total"] == after
    assert p3["items"][0]["id"] == ids_desc[4]  # 第 3 页首条 = 我第 5 新的那条（offset 跳过前 4 条）
    assert all(i["id"] not in ids_desc[:4] for i in p3["items"])

    # 默认兼容：不带分页参数仍 200
    d = client.get(f"{P}/runs").json()
    assert d["total"] == after and isinstance(d["items"], list)


def test_create_run_offloads_sync_work_to_thread(monkeypatch):
    """建运行的重活（service.create_run）必须在线程池执行，不阻塞事件循环。"""
    import asyncio
    import threading

    from app.modules.finance_cashflow import router as cf_router

    seen = {}

    class _FakeUpload:
        filename = "a.xlsx"

        async def read(self, size=-1):
            # 共享读取器按分块调用 read(size)：首块返回数据，随后空串终止循环
            if "read_thread" not in seen:
                seen["read_thread"] = threading.current_thread()
                return b"data"
            return b""

    def _fake_create_run(db, uploads, actual_month):
        seen["work_thread"] = threading.current_thread()
        return {"id": 1, "actual_month": actual_month, "status": "uploaded",
                "input_files": [], "artifacts": [], "error": "",
                "elapsed_sec": 0.0, "log_tail": "", "created_at": ""}

    monkeypatch.setattr(cf_router.service, "create_run", _fake_create_run)
    result = asyncio.run(
        cf_router.create_run(actual_month=9, files=[_FakeUpload()], db=object())
    )
    assert result["id"] == 1
    # 重活线程必须不同于事件循环线程（读上传所在线程）
    assert seen["work_thread"] is not seen["read_thread"]


async def _fake_read_upload_limited(file, max_bytes, chunk_size=1024 * 1024):
    """与 core 契约一致的测试替身：分块累积读取，超限抛真实 UploadTooLargeError。"""
    from app.upload import UploadTooLargeError

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


def test_upload_reads_via_shared_limited_reader(client, cash_dir, monkeypatch):
    """建运行上传经共享 read_upload_limited 分块限额读取（经 app.upload 补丁点），
    传入模块 50MB 口径。"""
    from app import upload as upload_module

    seen: list[int] = []

    async def spy(file, max_bytes, chunk_size=1024 * 1024):
        seen.append(max_bytes)
        return await _fake_read_upload_limited(file, max_bytes, chunk_size)

    monkeypatch.setattr(upload_module, "read_upload_limited", spy)
    r = client.post(f"{P}/runs", data={"actual_month": 9},
                    files=[("files", _xlsx("a.xlsx"))])
    assert r.status_code == 201, r.text
    assert seen == [svc.MAX_UPLOAD_BYTES]


def test_read_upload_fails_closed_when_shared_module_missing(monkeypatch):
    """共享上传模块缺失时 _read_upload 直接抛错（fail-closed）：

    改前 ImportError 被吞、回退无界 await file.read()；现在导入失败即向上抛。"""
    import asyncio
    import sys

    from app.modules.finance_cashflow import router as cf_router

    class _FakeUpload:
        filename = "a.xlsx"

        async def read(self, size=-1):
            return b"unbounded-fallback-content"

    monkeypatch.setitem(sys.modules, "app.upload", None)
    with pytest.raises(ImportError):
        asyncio.run(cf_router._read_upload(_FakeUpload()))


def test_create_run_failure_cleans_up_partial_extraction(client, cash_dir):
    """上传失败清理（回归保护）：ZIP 解出两个合法成员后第三个被拒时，
    400、不新增运行记录、不遗留半成品目录（覆盖「已部分落盘」的清理路径）。"""
    import io

    before_total = client.get(f"{P}/runs").json()["total"]
    root = cash_dir / "finance_cashflow" / "runs"
    before_dirs = {p.name for p in root.iterdir()} if root.exists() else set()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("数据源/明细账.xlsx", "bank")
        zf.writestr("模板/预测.csv", "a,b\n1,2")
        zf.writestr("evil.py", "import os")
    r = client.post(f"{P}/runs", data={"actual_month": 9},
                    files=[("files", ("inputs.zip", buf.getvalue()))])
    assert r.status_code == 400 and "不支持" in r.json()["detail"]
    assert client.get(f"{P}/runs").json()["total"] == before_total
    after_dirs = {p.name for p in root.iterdir()} if root.exists() else set()
    assert after_dirs == before_dirs, "失败后不得遗留半成品目录"
