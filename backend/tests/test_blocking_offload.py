"""Swarm 任务 offload-contract-tests 聚焦测试：慢解析/慢 service 不得阻塞事件循环。

覆盖六个入口（对应审计台账 A05：同步重活阻塞事件循环、拖垮并发请求）：
1. 税务财务报表导入入口一：POST /api/finance/tax/financial-import/import；
2. 税务财务报表导入入口二：POST /api/finance/tax/financial-import/import/{enterprise_id}；
3. BIP 导出文件预览：POST /api/finance/ledger/bip/imports/preview；
4. 个人记账 CSV 导入：POST /api/finance/ledger/personal/import/csv；
5. 现金流建运行：POST /api/finance/cashflow/runs；
6. POS 建运行：POST /api/finance/pos/runs。

验证方式（确定性门控，不依赖 sleep 时长阈值、不调用真实外部引擎）：
把入口内部的同步重活（解析/service 调用）包成门控替身——先登记执行线程标识、
置起 started 事件，再等待 release 放行。测试在慢活被挡住的期间断言：
- 重活执行线程不是事件循环线程（线程标识不同 → 已卸载到工作线程）；
- 事件循环仍可调度（call_soon 探针在放行之前完成 → 未被阻塞）。
最后一项为门控自检：模拟「同步跑在循环上」的未卸载实现，断言契约必然判红，
证明上述鉴别不是空转。

数据安全：全部合成数据 + 临时目录（conftest 钉住临时 SQLite/调试目录；
BIP 模块目录与个人 CSV 留档目录经 monkeypatch 指向 tmp_path）；数据库会话
仅按入口签名透传，慢活替身不落库，不写任何业务数据、不触碰真实引擎。
"""
import asyncio
import io
import threading

import pandas as pd
import pytest
from fastapi import UploadFile

from app.database import get_db
from app.modules.finance_cashflow import router as cashflow_router
from app.modules.finance_cashflow import service as cashflow_svc
from app.modules.finance_ledger_bip import router as bip_router
from app.modules.finance_ledger_bip import service as bip_svc
from app.modules.finance_ledger_personal import router as personal_router
from app.modules.finance_ledger_personal import service as personal_svc
from app.modules.finance_pos import router as pos_router
from app.modules.finance_pos import service as pos_svc
from app.modules.finance_tax.routers import financial_import as tax_router
from app.modules.finance_tax.services import financial_data_importer as tax_importer

GATE_TIMEOUT = 5.0   # 慢活等待放行的上限：安全网，实现未卸载时兜底放行避免挂死
PROBE_TIMEOUT = 3.0  # 循环探针等待上限：安全网，卸载正常时探针应远早于此完成


# ---------------- 确定性门控基础设施 ----------------

def _upload(name: str, payload: bytes) -> UploadFile:
    """内存构造上传文件（不走真实磁盘上传）。"""
    return UploadFile(filename=name, file=io.BytesIO(payload))


def _gated(real, started: threading.Event, release: threading.Event, idents: list):
    """把同步重活包成门控替身：登记线程标识 → 置起 started → 等 release 放行 → 委托 real。

    real：被替身的原函数（放行后真实执行，保证入口行为不变）。
    idents：慢活实际执行线程标识的收集列表（线程标识是「是否卸载」的确定性证据）。
    """

    def slow(*args, **kwargs):
        idents.append(threading.get_ident())
        started.set()
        release.wait(GATE_TIMEOUT)
        return real(*args, **kwargs)

    return slow


async def _run_gated(start_handler, started: threading.Event,
                     release: threading.Event, idents: list):
    """启动入口处理器，验证其把同步重活卸载到工作线程而非占住事件循环。

    start_handler：无参可调用，返回入口协程。慢活被门控挡住期间断言：
    1) 重活执行线程不是事件循环线程（线程标识不同）；
    2) 事件循环仍可调度（call_soon 探针在放行之前完成）。
    放行后返回处理器最终结果。
    """
    loop = asyncio.get_running_loop()
    loop_ident = threading.get_ident()
    task = asyncio.create_task(start_handler())
    # 在线程池里等 started：等待过程本身不占事件循环
    started_wait = asyncio.ensure_future(asyncio.to_thread(started.wait, GATE_TIMEOUT))
    try:
        done, _pending = await asyncio.wait(
            {started_wait, task}, timeout=GATE_TIMEOUT, return_when=asyncio.FIRST_COMPLETED)
        if idents:
            assert idents[0] != loop_ident, (
                f"同步重活运行在事件循环线程上（线程 {idents[0]}）：未卸载，"
                "慢活期间其它请求全部排队")
            # 慢活仍被挡住：循环必须还能调度回调，否则说明实现另有阻塞点
            probe = loop.create_future()
            loop.call_soon(probe.set_result, True)
            assert await asyncio.wait_for(probe, PROBE_TIMEOUT) is True, (
                "重活虽已卸载，但慢活期间事件循环仍被阻塞")
        elif task in done:
            task.result()  # 入口提前结束：异常原样抛出，否则明确判红
            raise AssertionError("入口未执行慢活替身即返回：卸载目标未被调用")
        else:
            raise AssertionError(f"慢活替身未在 {GATE_TIMEOUT}s 内开始执行")
    finally:
        release.set()
        started_wait.cancel()
    return await asyncio.wait_for(task, GATE_TIMEOUT)


# ---------------- 合成数据 ----------------

def _aux_rows() -> list[dict]:
    """BIP 辅助明细账合成数据：科目编码覆盖 1002/1122/6602 前缀（与既有 BIP 测试同口径）。"""
    return [
        {"科目编码": "100201", "科目名称": "银行存款-基本户", "日期": "2026-09-05", "核算账簿": "测试账簿甲",
         "凭证号": "记-0011", "摘要": "收到货款", "借方本币": 800.0, "贷方本币": 0.0},
        {"科目编码": "112201", "科目名称": "应收账款-客户A", "日期": "2026-09-05", "核算账簿": "测试账簿甲",
         "凭证号": "记-0011", "摘要": "收到货款", "借方本币": 0.0, "贷方本币": 800.0},
        {"科目编码": "660201", "科目名称": "管理费用-办公费", "日期": "2026-09-08", "核算账簿": "测试账簿甲",
         "凭证号": "记-0012", "摘要": "购买耗材", "借方本币": 300.0, "贷方本币": 0.0},
        {"科目编码": "100202", "科目名称": "银行存款-一般户", "日期": "2026-09-08", "核算账簿": "测试账簿甲",
         "凭证号": "记-0012", "摘要": "购买耗材", "借方本币": 0.0, "贷方本币": 300.0},
    ]


def _xlsx_bytes(rows) -> bytes:
    """pandas 合成 xlsx 字节（供 BIP 预览走真实解析路径）。"""
    buf = io.BytesIO()
    pd.DataFrame(rows).to_excel(buf, index=False, sheet_name="Sheet", engine="openpyxl")
    return buf.getvalue()


# ---------------- fixtures ----------------

@pytest.fixture()
def db_session():
    """透传数据库会话：慢活替身不落库，仅保证入口签名完整。"""
    gen = get_db()
    db = next(gen)
    try:
        yield db
    finally:
        gen.close()


@pytest.fixture()
def bip_dir(monkeypatch, tmp_path):
    """BIP 模块数据目录重定向到 tmp_path（预览半成品不落真实数据目录）。"""
    d = tmp_path / "bip_data"
    monkeypatch.setattr(bip_svc, "module_data_dir", lambda: d)
    return d


# ---------------- 税务两入口 ----------------

def test_tax_import_entry_parse_offloaded_event_loop_not_blocked(monkeypatch):
    """税务导入入口一（/import）：慢解析卸载，慢活期间循环保持响应、结果不变。"""
    started, release, idents = threading.Event(), threading.Event(), []

    def slow_import(file_path):
        return tax_importer.ImportResult(success=True, summary="合成导入完成")

    monkeypatch.setattr(tax_router.importer, "import_from_file",
                        _gated(slow_import, started, release, idents))
    upload = _upload("slow.xlsx", b"xlsx-bytes")

    async def scenario():
        return await _run_gated(lambda: tax_router.import_financial_statement(file=upload),
                                started, release, idents)

    result = asyncio.run(scenario())
    assert result.success is True
    assert result.summary == "合成导入完成"


def test_tax_import_to_enterprise_entry_parse_offloaded_event_loop_not_blocked(monkeypatch):
    """税务导入入口二（/import/{enterprise_id}）：同样卸载，慢活期间循环保持响应。"""
    started, release, idents = threading.Event(), threading.Event(), []

    def slow_import(file_path):
        return tax_importer.ImportResult(success=True, summary="合成导入完成")

    monkeypatch.setattr(tax_router.importer, "import_from_file",
                        _gated(slow_import, started, release, idents))
    upload = _upload("slow.xlsx", b"xlsx-bytes")

    async def scenario():
        return await _run_gated(
            lambda: tax_router.import_financial_statement_to_enterprise(
                enterprise_id=1, file=upload),
            started, release, idents)

    result = asyncio.run(scenario())
    assert result.success is True
    assert result.summary == "合成导入完成"


# ---------------- BIP 预览 ----------------

def test_bip_preview_parse_offloaded_event_loop_not_blocked(bip_dir, monkeypatch):
    """BIP 预览：慢解析卸载，慢活期间循环保持响应，预览结果不变。"""
    real_read = bip_svc.read_bip_excel
    started, release, idents = threading.Event(), threading.Event(), []
    monkeypatch.setattr(bip_svc, "read_bip_excel",
                        _gated(real_read, started, release, idents))
    upload = _upload("aux.xlsx", _xlsx_bytes(_aux_rows()))

    async def scenario():
        return await _run_gated(lambda: bip_router.preview_import(file=upload),
                                started, release, idents)

    result = asyncio.run(scenario())
    assert result["file_type"] == "aux"
    assert result["row_count"] == len(_aux_rows())


# ---------------- 个人 CSV ----------------

def test_personal_csv_import_offloaded_event_loop_not_blocked(db_session, monkeypatch, tmp_path):
    """个人记账 CSV 导入：慢解析入库卸载，慢活期间循环保持响应；留档目录在 tmp。"""
    monkeypatch.setattr(personal_router, "get_finance_data_dir", lambda: tmp_path)
    started, release, idents = threading.Event(), threading.Event(), []

    def slow_import(db, content):
        return {"imported": 1, "skipped": 0, "errors": [],
                "created_accounts": [], "created_categories": []}

    monkeypatch.setattr(personal_svc, "import_csv",
                        _gated(slow_import, started, release, idents))
    upload = _upload("ledger.csv",
                     "日期,类型,金额,账户\n2026-09-01,支出,10,现金\n".encode("utf-8-sig"))

    async def scenario():
        return await _run_gated(lambda: personal_router.import_csv(file=upload, db=db_session),
                                started, release, idents)

    assert asyncio.run(scenario()) == {
        "imported": 1, "skipped": 0, "errors": [],
        "created_accounts": [], "created_categories": [],
    }


# ---------------- 现金流 / POS 建运行 ----------------

def test_cashflow_create_run_offloaded_event_loop_not_blocked(db_session, monkeypatch):
    """现金流建运行：慢 service 卸载，慢活期间循环保持响应。"""
    started, release, idents = threading.Event(), threading.Event(), []

    def slow_create_run(db, uploads, actual_month):
        return {"id": 1, "status": "uploaded", "actual_month": actual_month,
                "input_files": [], "artifacts": []}

    monkeypatch.setattr(cashflow_svc, "create_run",
                        _gated(slow_create_run, started, release, idents))
    upload = _upload("bank.xlsx", b"stub-bank")

    async def scenario():
        return await _run_gated(
            lambda: cashflow_router.create_run(actual_month=9, files=[upload], db=db_session),
            started, release, idents)

    result = asyncio.run(scenario())
    assert result["status"] == "uploaded" and result["actual_month"] == 9


def test_pos_create_run_offloaded_event_loop_not_blocked(db_session, monkeypatch):
    """POS 建运行：慢 service 卸载，慢活期间循环保持响应。"""
    started, release, idents = threading.Event(), threading.Event(), []

    def slow_create_run(db, uploads):
        return {"id": 2, "status": "uploaded", "input_files": [], "artifacts": []}

    monkeypatch.setattr(pos_svc, "create_run",
                        _gated(slow_create_run, started, release, idents))
    upload = _upload("shop.xlsx", b"stub-shop")

    async def scenario():
        return await _run_gated(lambda: pos_router.create_run(files=[upload], db=db_session),
                                started, release, idents)

    assert asyncio.run(scenario())["status"] == "uploaded"


# ---------------- 门控自检：鉴别力证明 ----------------

def test_offload_detector_rejects_sync_blocking_handler():
    """自检：若实现把慢活同步跑在事件循环上，门控断言必须判红（证明契约测试有鉴别力）。"""
    started, release, idents = threading.Event(), threading.Event(), []

    def slow():
        idents.append(threading.get_ident())
        started.set()
        release.wait(0.5)  # 自检专用短门控：足以让鉴别断言判红
        return "done"

    async def sync_handler():
        return slow()  # 模拟未卸载的实现：直接在循环线程上同步执行

    async def scenario():
        with pytest.raises(AssertionError, match="未卸载"):
            await _run_gated(sync_handler, started, release, idents)

    asyncio.run(scenario())
