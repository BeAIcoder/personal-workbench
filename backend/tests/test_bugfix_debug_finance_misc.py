"""bugfix 批次 debug-finance-misc 回归测试。

逐 bug 对应：
- B1 debug_center：issues 的 create/update/delete 为 load→改→save 无锁，并发丢更新
  → 进程内锁串行化读-改-写；10 线程并发 create 最终 10 条都在。
- B2 finance_ledger_personal：Agent 记账工具账户名找不到时静默记入第一账户
  → 与分类行为对齐，返回带 warning 字段。
- B3 finance_tax/routers/financial_import.py（审计描述作 finance_expense/financial_import.py:46，
  实际文件在 finance_tax，46 行症状逐字吻合）：.XLSX 大写被拒 + 上传无大小上限
  → 扩展名大小写不敏感；加与既有模块一致的大小上限。
- B4 utils.parse_dt：接受带时区 datetime，绑定 naive SQLite 列后比较错乱（due_before 漏数据）
  → aware 输入统一转 naive 本地时间。
- B5 finance_expense classify：ensure_engine_ready（os.chdir + pandas import，秒级）
  在事件循环线程同步执行 → 移入线程池（asyncio.to_thread）。
- B6 finance_board /run：run_pipeline 无互斥，并发同秒共用 stamp 快照目录
  → 进行中第二次返回 409。
"""
import asyncio
import io
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.config import settings
from app.modules.debug_center import schemas as debug_schemas
from app.modules.debug_center import service as debug_svc
from app.modules.finance_board import service as board_svc
from app.modules.finance_expense import service as expense_svc
from app.modules.finance_ledger_personal import service as ledger_svc
from app.modules.finance_tax.routers import financial_import
from app.utils import parse_dt

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


# ---------- B1 debug_center 并发写丢更新 ----------


@pytest.fixture()
def debug_dir(monkeypatch, tmp_path):
    """DEBUG_DATA_DIR 重定向到 tmp_path（service 运行时读取环境变量）。"""
    base = tmp_path / "debug_data"
    monkeypatch.setenv("DEBUG_DATA_DIR", str(base))
    return base


def _issue_payload(i: int):
    return debug_schemas.IssueCreate(
        title=f"并发问题{i}", severity="P2", source="e2e", page="/tasks", description="d", evidence=""
    )


def _concurrent_create(count: int = 10) -> None:
    barrier = threading.Barrier(count)

    def worker(i: int):
        barrier.wait(timeout=10)
        debug_svc.create_issue(_issue_payload(i))

    with ThreadPoolExecutor(max_workers=count) as pool:
        list(pool.map(worker, range(count)))


def test_b1_concurrent_create_issues_no_lost_update(debug_dir):
    """10 线程并发 create：最终 10 条都在（原子写只防撕裂，不防丢更新）。"""
    _concurrent_create(10)
    issues = debug_svc.load_issues()
    assert len(issues) == 10
    assert len({i["id"] for i in issues}) == 10
    assert {i["title"] for i in issues} == {f"并发问题{i}" for i in range(10)}


def test_b1_concurrent_create_slow_io_no_lost_update(debug_dir, monkeypatch):
    """确定性复现：给 load 加 20ms 延迟模拟慢盘，放大 load→save 竞态窗口。"""
    real_load = debug_svc.load_issues

    def slow_load():
        data = real_load()
        time.sleep(0.02)
        return data

    monkeypatch.setattr(debug_svc, "load_issues", slow_load)
    _concurrent_create(10)
    assert len(debug_svc.load_issues()) == 10


# ---------- B2 记账工具账户名不存在时静默回退 ----------


def test_b2_agent_add_transaction_unknown_account_returns_warning(client):
    """账户名找不到：回退到排序第一账户，且返回 warning（与分类未命中行为对齐）。"""
    r = ledger_svc.pl_add_transaction(kind="支出", amount=1, account="不存在的账户")
    assert r["ok"] is True
    assert r["account"] == "现金"  # 回退到 sort 第一的默认账户
    assert "warning" in r
    assert "不存在的账户" in r["warning"]


def test_b2_agent_add_transaction_known_or_empty_account_no_warning(client):
    """账户名命中或留空（原有合理回退）：不带 warning。"""
    r = ledger_svc.pl_add_transaction(kind="支出", amount=1, account="微信")
    assert r["ok"] is True and r["account"] == "微信" and "warning" not in r
    r = ledger_svc.pl_add_transaction(kind="支出", amount=1, account="")
    assert r["ok"] is True and r["account"] == "现金" and "warning" not in r


def test_b2_agent_add_transaction_unknown_account_and_category_warnings_merge(client):
    """账户与分类同时未命中：两处 warning 都保留，不互相覆盖。"""
    r = ledger_svc.pl_add_transaction(kind="支出", amount=1, account="不存在的账户", category="不存在的分类")
    assert r["ok"] is True
    assert "warning" in r
    assert "不存在的账户" in r["warning"]
    assert "不存在的分类" in r["warning"]


# ---------- B3 财务报表导入：.XLSX 大写被拒 + 无大小上限 ----------


def _real_xlsx_bytes() -> bytes:
    """合法 xlsx 容器（openpyxl 空表），让请求穿过扩展名门禁进入解析阶段。"""
    from openpyxl import Workbook

    buf = io.BytesIO()
    Workbook().save(buf)
    return buf.getvalue()


def test_b3_financial_import_accepts_uppercase_xlsx(client):
    """大写 .XLSX 应通过扩展名门禁（后续解析失败是另一回事，拒绝原因不得是「仅支持」）。"""
    resp = client.post(
        "/api/finance/tax/financial-import/import",
        files={"file": ("报表.XLSX", _real_xlsx_bytes(), XLSX_MIME)},
    )
    assert "仅支持" not in resp.text


def test_b3_financial_import_enterprise_accepts_uppercase_xls(client):
    """按企业导入端点同样大小写不敏感。"""
    resp = client.post(
        "/api/finance/tax/financial-import/import/1",
        files={"file": ("报表.XLS", _real_xlsx_bytes(), XLSX_MIME)},
    )
    assert "仅支持" not in resp.text


def test_b3_financial_import_size_limit(client, monkeypatch):
    """上传大小上限（与其他模块一致）：超限 400。"""
    monkeypatch.setattr(financial_import, "MAX_UPLOAD_BYTES", 16, raising=False)
    resp = client.post(
        "/api/finance/tax/financial-import/import",
        files={"file": ("big.xlsx", b"x" * 64, XLSX_MIME)},
    )
    assert resp.status_code == 400
    assert "上限" in resp.json()["detail"]


# ---------- B4 parse_dt 带时区输入致 SQLite 比较错乱 ----------


def test_b4_parse_dt_aware_input_normalized_to_naive_local():
    """带时区输入统一转 naive 本地时间（入库列是 naive DateTime）。"""
    dt = parse_dt("2026-09-07T04:00:00+00:00")
    assert dt.tzinfo is None
    expected = datetime(2026, 9, 7, 4, 0, tzinfo=timezone.utc).astimezone().replace(tzinfo=None)
    assert dt == expected
    # naive 输入原样返回；纯日期 + end_of_day 语义不变
    assert parse_dt("2026-09-07T04:00:00") == datetime(2026, 9, 7, 4, 0, 0)
    assert parse_dt("2026-09-07", end_of_day=True) == datetime(2026, 9, 7, 23, 59, 59, 999999)


def test_b4_tasks_due_before_with_timezone_offset(client):
    """due_before 传带时区时间：过滤按本地时刻比较，不漏数据。

    界限取「任务截止后 30 分钟」的同一时刻、用 UTC-5 表达：正确语义应命中任务；
    若把 aware 值直接绑给 naive 列（丢弃/附带时区），比较口径错乱导致漏数据。
    """
    due = datetime(2026, 9, 7, 12, 0, 0)  # naive 本地
    local_tz = datetime.now().astimezone().tzinfo
    bound_local = datetime(2026, 9, 7, 12, 30, tzinfo=local_tz)
    bound_alt = bound_local.astimezone(timezone(timedelta(hours=-5)))
    resp = client.post("/api/tasks", json={"title": "B4样本任务", "due_date": due.isoformat()})
    assert resp.status_code == 201

    resp = client.get("/api/tasks", params={"due_before": bound_alt.isoformat()})
    assert resp.status_code == 200
    titles = [i["title"] for i in resp.json()["items"]]
    assert "B4样本任务" in titles


# ---------- B5 ensure_engine_ready 阻塞事件循环 ----------


@pytest.fixture()
def expense_env(client, tmp_path, monkeypatch):
    """模块数据目录隔离到 tmp_path，并重置引擎缓存（对齐 test_module_finance_expense.py）。"""
    monkeypatch.setattr(settings, "finance_data_dir", str(tmp_path / "finance"))
    monkeypatch.setattr(settings, "finance_expense_engine_path", "")
    expense_svc._reset_engine_state()
    yield client
    expense_svc._reset_engine_state()


def test_b5_ensure_engine_ready_runs_via_thread_pool(expense_env, monkeypatch):
    """classify 处理器中引擎可用性探测（首载含 os.chdir + pandas import）须经线程池执行。"""
    threaded_calls: list[str] = []
    real_to_thread = asyncio.to_thread

    async def spy_to_thread(func, /, *args, **kwargs):
        threaded_calls.append(getattr(func, "__name__", ""))
        return await real_to_thread(func, *args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", spy_to_thread)

    ready_calls: list[str] = []

    def fake_ready() -> None:
        ready_calls.append(threading.current_thread().name)

    monkeypatch.setattr(expense_svc, "ensure_engine_ready", fake_ready)

    def boom(db, original, stored):
        raise ValueError("合成：分类失败")

    monkeypatch.setattr(expense_svc, "classify_file", boom)

    resp = expense_env.post(
        "/api/finance/expense/classify",
        files={"file": ("明细.xlsx", b"fake-bytes", XLSX_MIME)},
    )
    assert resp.status_code == 400
    assert ready_calls  # 探测确实被调用
    assert "fake_ready" in threaded_calls  # 关键断言：经 asyncio.to_thread 进线程池


# ---------- B6 finance_board 并发 run 冲突 ----------


@pytest.fixture()
def board_env(client, tmp_path, monkeypatch):
    """管线目录 / 快照目录重定向到 tmp_path；scripts 四步脚本与看板产物就位。"""
    pdir = tmp_path / "微信报表监控"
    (pdir / "scripts").mkdir(parents=True)
    for step in board_svc.PIPELINE_STEPS:
        (pdir / "scripts" / step).write_text("# fake", encoding="utf-8")
    (pdir / "output").mkdir()
    (pdir / "output" / board_svc.DASHBOARD_NAME).write_text("<html>ok</html>", encoding="utf-8")
    monkeypatch.setattr(board_svc, "pipeline_dir", lambda: pdir)
    monkeypatch.setattr(board_svc, "get_finance_data_dir", lambda: tmp_path / "finance")
    return client


def test_b6_concurrent_run_returns_409(board_env, monkeypatch):
    """并发两次 /run：第一次运行中，第二次 409 + 中文提示。"""
    started = threading.Event()
    release = threading.Event()
    step_calls: list[str] = []

    def fake_step(script: Path) -> str:
        step_calls.append(script.name)
        if len(step_calls) == 1:
            started.set()
            release.wait(15)  # 第一次运行卡在管线里
        return "ok"

    monkeypatch.setattr(board_svc, "_run_step", fake_step)

    box: dict = {}
    t = threading.Thread(target=lambda: box.__setitem__("r1", board_env.post("/api/finance/board/run")), daemon=True)
    t.start()
    try:
        assert started.wait(5), "第一次运行未进入管线"

        r2 = board_env.post("/api/finance/board/run")
        assert r2.status_code == 409
        assert "运行" in r2.json()["detail"]
    finally:
        # 无论断言成败都放行并回收线程：避免泄漏的线程在 fixture 拆除后写真实数据目录
        release.set()
        t.join(15)
    assert not t.is_alive()
    assert box["r1"].status_code == 200


def test_b6_sequential_runs_both_ok(board_env, monkeypatch):
    """互斥不破坏串行：两次先后 /run 都应成功（锁正确释放）。

    stamp 精度到秒且列有 UNIQUE 约束：同秒串行两次运行本就撞 stamp（既有口径，
    不在本 bug 修复范围），故第二次等待跨过秒边界。
    """
    monkeypatch.setattr(board_svc, "_run_step", lambda script: "ok")
    r1 = board_env.post("/api/finance/board/run")
    time.sleep(1.05)
    r2 = board_env.post("/api/finance/board/run")
    assert r1.status_code == 200
    assert r2.status_code == 200
