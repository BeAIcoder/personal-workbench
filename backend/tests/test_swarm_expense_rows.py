"""Swarm 任务 expense-row-limit 聚焦测试（本任务白名单内新增测试文件）。

覆盖（费用分类 Excel 导入行数护栏，复用 app.import_limits.MAX_IMPORT_ROWS）：
- 超限在引擎执行与写库前失败：小上限 mock 下 classify_file 直接抛 ValueError（路由层 400 口径），
  引擎既未加载更未执行、无运行记录落库；HTTP 层 inbox 上传文件被既有失败清理路径删除；
- 恰好等于上限放行、超一即拒：标题空区不吃行数预算（表头第 11 行、上方 10 行空标题）；
- 未超限时正常分类引擎调用链与结果入库不受影响（假引擎全链路桩）；
- 共享行数上限缺失时失败关闭；service 接上的确是共享常量本身（非回退值）；
- 非 Excel 内容保持既有 400「银行明细账读取失败」口径（护栏前移不退化成 500）。

约定：共享上限由 core 任务提供（app/import_limits.py），本文件 monkeypatch 小值验证消费侧；
全部使用合成数据与临时目录（conftest 已钉住临时 SQLite 与 DEBUG_DATA_DIR），不碰真实业务文件。
"""
from pathlib import Path

import pandas as pd
import pytest

from app.config import settings
from app.database import SessionLocal
from app.modules.finance_expense import service
from app.modules.finance_expense.models import ExpenseRun

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

# 合成银行明细账：7 行数据，摘要避开引擎特殊规则链关键词（与模块测试同口径的合成数据）
BANK_COLUMNS = ["科目编码", "科目名称", "日期", "摘要", "借方本币", "贷方本币", "辅助核算"]
ROWS = [
    ("10020101", "银行存款-基本户", "2026-09-01", "支付2026年9月员工工资", 0.0, 1000.00, "合成测试项目"),
    ("10020101", "银行存款-基本户", "2026-09-02", "支付银行账户手续费", 0.0, 235.50, "合成测试项目"),
    ("10020101", "银行存款-基本户", "2026-09-03", "支付9月办公场所电费", 0.0, 680.75, "合成测试项目"),
    ("10020101", "银行存款-基本户", "2026-09-04", "支付办公室绿植租摆费", 0.0, 320.00, "合成测试项目"),
    ("10020101", "银行存款-基本户", "2026-09-05", "支付商铺租赁押金", 0.0, 5000.00, "合成测试项目"),
    ("10020101", "银行存款-基本户", "2026-09-06", "支付广告宣传活动物料费", 0.0, 888.80, "合成测试项目"),
    ("10020101", "其他应收款-往来单位", "2026-09-07", "支付杂项往来结算款", 0.0, 150.00, "合成测试项目"),
]


def _make_bank_xlsx(path: Path) -> None:
    """合成银行明细账 xlsx：引擎按 header=10（第 11 行）读取，上方 10 行模拟真实标题空区。"""
    df = pd.DataFrame(ROWS, columns=BANK_COLUMNS)
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, startrow=10, sheet_name="明细")


@pytest.fixture()
def expense_env(client, tmp_path, monkeypatch):
    """隔离模块数据目录到 tmp_path，并重置引擎缓存。"""
    monkeypatch.setattr(settings, "finance_data_dir", str(tmp_path / "finance"))
    service._reset_engine_state()
    yield client
    service._reset_engine_state()


def _post_classify(client, xlsx_path: Path, filename: str = "合成银行明细账.xlsx"):
    with open(xlsx_path, "rb") as f:
        return client.post("/api/finance/expense/classify", files={"file": (filename, f, XLSX_MIME)})


class _FakeClassifier:
    """假分类引擎：记录调用序列，返回最小可跑通 classify_file 全链路的合成对象。"""

    def __init__(self):
        self.calls: list[str] = []

    def read_bank_data(self, file_path):
        self.calls.append("read_bank_data")
        return object()

    def integrate_data(self):
        self.calls.append("integrate_data")
        return object()

    def clean_data(self):
        self.calls.append("clean_data")
        return object()

    def classify_expenses(self):
        self.calls.append("classify_expenses")
        return pd.DataFrame(
            {
                "摘要": ["合成摘要甲", "合成摘要乙"],
                "贷方本币": [10.5, 20.25],
                "费用类别": ["办公费", "未分类"],
            }
        )

    def summarize_expenses(self):
        self.calls.append("summarize_expenses")
        return object()

    def export_results(self, output_file):
        self.calls.append("export_results")
        Path(output_file).write_bytes(b"synthetic-xlsx")
        return object()


# ---------- 超限：引擎执行与写库前失败 ----------


def test_over_limit_fails_before_engine_execution_and_db_write(expense_env, tmp_path, monkeypatch):
    """小上限 mock：超限在引擎执行与写库前失败关闭（ValueError），无运行记录落库。"""
    monkeypatch.setattr(service, "MAX_IMPORT_ROWS", 3, raising=False)
    engine_touched: list[str] = []

    def spy_ensure_engine():
        engine_touched.append("ensure_engine")
        return {"classifier_cls": None, "load_rules": None, "app_config": None, "engine_dir": ""}

    monkeypatch.setattr(service, "_ensure_engine", spy_ensure_engine)

    xlsx = tmp_path / "合成银行明细账.xlsx"
    _make_bank_xlsx(xlsx)
    with SessionLocal() as db:
        with pytest.raises(ValueError, match="超过单次导入上限"):
            service.classify_file(db, xlsx.name, xlsx)
        assert db.query(ExpenseRun).count() == 0  # 写库前失败：无 running 运行记录
    assert engine_touched == []  # 引擎未被加载，更未执行


def test_over_limit_http_400_cleans_inbox_upload(expense_env, tmp_path, monkeypatch):
    """HTTP 层超限：400「上限」、无运行记录、inbox 上传文件被既有清理路径删除。"""
    monkeypatch.setattr(service, "MAX_IMPORT_ROWS", 3, raising=False)
    monkeypatch.setattr(service, "ensure_engine_ready", lambda: None)
    engine_touched: list[str] = []
    monkeypatch.setattr(service, "_ensure_engine", lambda: engine_touched.append("engine") or {})

    xlsx = tmp_path / "合成银行明细账.xlsx"
    _make_bank_xlsx(xlsx)
    resp = _post_classify(expense_env, xlsx)
    assert resp.status_code == 400
    assert "上限" in resp.json()["detail"]
    assert engine_touched == []

    runs = expense_env.get("/api/finance/expense/runs").json()
    assert runs["total"] == 0
    inbox = tmp_path / "finance" / "finance_expense" / "inbox"
    assert not inbox.exists() or not any(inbox.iterdir())


# ---------- 边界与计数口径 ----------


def test_row_count_skips_empty_title_area_and_early_stops(tmp_path, monkeypatch):
    """计数口径：非空行合计（含表头、跳过标题空区），恰好等于上限不拒、超一即拒。"""
    xlsx = tmp_path / "合成银行明细账.xlsx"
    _make_bank_xlsx(xlsx)  # 10 行空标题区 + 1 表头 + 7 数据 = 8 非空行
    assert service._excel_row_count(xlsx, 100) == 8
    assert service._excel_row_count(xlsx, 8) == 8
    assert service._excel_row_count(xlsx, 7) == 8  # 超限：早停于 limit+1，不再解析后续行
    assert service._excel_row_count(xlsx, 0) == 1  # 空标题区不吃预算：首个非空行即判超

    monkeypatch.setattr(service, "MAX_IMPORT_ROWS", 8, raising=False)
    service._guard_import_rows(xlsx)  # 恰好等于上限：放行
    monkeypatch.setattr(service, "MAX_IMPORT_ROWS", 7, raising=False)
    with pytest.raises(ValueError, match="超过单次导入上限"):
        service._guard_import_rows(xlsx)


# ---------- 未超限：正常分类引擎调用不受影响 ----------


def test_in_limit_still_runs_engine_and_writes_run(expense_env, tmp_path, monkeypatch):
    """未超限：护栏放行，正常分类引擎调用链与结果入库不受影响（假引擎全链路桩）。"""
    monkeypatch.setattr(service, "MAX_IMPORT_ROWS", 100, raising=False)
    fake = _FakeClassifier()
    monkeypatch.setattr(
        service,
        "_ensure_engine",
        lambda: {"classifier_cls": lambda: fake, "load_rules": None, "app_config": None, "engine_dir": ""},
    )

    xlsx = tmp_path / "合成银行明细账.xlsx"
    _make_bank_xlsx(xlsx)
    with SessionLocal() as db:
        run = service.classify_file(db, xlsx.name, xlsx)

    assert run.status == "success"
    assert run.total_rows == 2
    assert run.matched_rows == 1
    assert run.artifact_path.startswith("finance_expense/outbox/")
    assert (tmp_path / "finance" / run.artifact_path).is_file()
    assert fake.calls == [
        "read_bank_data",
        "integrate_data",
        "clean_data",
        "classify_expenses",
        "summarize_expenses",
        "export_results",
    ]


# ---------- 失败关闭与接线 ----------


def test_row_limit_fails_closed_when_shared_limit_missing(expense_env, tmp_path, monkeypatch):
    """共享行数上限缺失：失败关闭（ValueError「暂不可用」），绝不放行无上限文件。"""
    monkeypatch.setattr(service, "MAX_IMPORT_ROWS", None, raising=False)
    xlsx = tmp_path / "合成银行明细账.xlsx"
    _make_bank_xlsx(xlsx)
    with SessionLocal() as db:
        with pytest.raises(ValueError, match="暂不可用"):
            service.classify_file(db, xlsx.name, xlsx)


def test_expense_service_wired_to_shared_import_rows():
    """费用 service 确实接上共享上限（与 app.import_limits 同一对象，非回退值）。"""
    from app.import_limits import MAX_IMPORT_ROWS

    assert service.MAX_IMPORT_ROWS is MAX_IMPORT_ROWS


# ---------- 既有口径回归 ----------


def test_corrupt_xlsx_keeps_bank_ledger_400_detail(expense_env, tmp_path, monkeypatch):
    """非 Excel 内容：护栏读取失败保持既有 400「银行明细账」口径，不退化成 500、不留 inbox 孤儿。"""

    def boom():
        raise AssertionError("引擎不应被触碰")

    monkeypatch.setattr(service, "ensure_engine_ready", lambda: None)
    monkeypatch.setattr(service, "_ensure_engine", boom)
    resp = expense_env.post(
        "/api/finance/expense/classify",
        files={"file": ("坏文件.xlsx", b"not-a-zip-content", XLSX_MIME)},
    )
    assert resp.status_code == 400
    assert "银行明细账" in resp.json()["detail"]

    inbox = tmp_path / "finance" / "finance_expense" / "inbox"
    assert not inbox.exists() or not any(inbox.iterdir())
