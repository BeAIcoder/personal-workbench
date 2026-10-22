"""导入行数上限统一口径契约测试（Swarm 任务 import-limit-contract-tests，白名单内仅此一个文件）。

共享上限 app.import_limits.MAX_IMPORT_ROWS = 200_000（审计 A06）。覆盖六条导入路径：
- assistant 聊天 Excel（app/routers/assistant.py：_xlsx_first_sheet_to_csv + 上传端点）；
- 税务财务报表导入（finance_tax/services/financial_data_importer.py：标准模板 / 财务系统 XML 两条解析路径）；
- BIP 企业记账 Excel（finance_ledger_bip/service.py：read_bip_excel）；
- 个人记账 CSV（finance_ledger_personal/service.py：import_csv）；
- 数据仓 Excel/CSV 入仓（finance_vault/service.py：ingest_dataframe）；
- 费用分类 Excel（finance_expense/service.py：classify_file）。

每条路径三项契约：
1. 统一口径：消费侧引用同一共享常量（200_000，测试可 monkeypatch 小值）；
2. 超限无写库/半成品：超限可检测拒绝（ValueError 或 HTTP 400），不写任何业务数据、不留半成品；
3. 边界行数可接受：行数恰好等于上限时正常导入（不误拒）。

约定：限额类测试一律 monkeypatch 小值，不生成巨型文件；全部使用合成数据与临时目录
（conftest 已钉住临时 SQLite/调试目录），不碰真实业务数据；不调用真实 LLM。

说明：费用分类的行数护栏由并行实现任务在本次测试编写期间落地
（finance_expense/service.py：classify_file 前端 _guard_import_rows）；其计数口径为
各工作表非空行合计（含表头行、跳过标题空区），与 assistant 聊天 Excel 转换的
「含表头」口径一致，本文件按该口径 pin 边界。
"""
import csv
import io
from pathlib import Path

import openpyxl
import pandas as pd
import pytest

from app.config import settings
from app.database import SessionLocal
from app.import_limits import MAX_IMPORT_ROWS
from app.modules.finance_expense import service as expense_svc
from app.modules.finance_expense.models import ExpenseRow, ExpenseRun
from app.modules.finance_ledger_bip import service as bip_svc
from app.modules.finance_ledger_personal import service as personal_svc
from app.modules.finance_ledger_personal.models import PlTransaction
from app.modules.finance_tax.services import financial_data_importer as tax_importer
from app.modules.finance_vault import service as vault_svc
from app.modules.finance_vault.models import VaultDataset
from app.routers import assistant as assistant_router

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
SHARED_LIMIT = 200_000


# ---------------- 合成数据（小夹具，不生成巨型文件） ----------------

def _xlsx_bytes(rows: list, sheet: str = "Sheet") -> bytes:
    """openpyxl 合成 xlsx 字节；rows 支持 list[list] 与 list[dict]（dict 取键作表头）。"""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = sheet
    if rows and isinstance(rows[0], dict):
        header = list(rows[0].keys())
        ws.append(header)
        for row in rows:
            ws.append([row.get(h) for h in header])
    else:
        for row in rows:
            ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _write_xlsx(path: Path, rows: list, sheet: str = "Sheet") -> Path:
    path.write_bytes(_xlsx_bytes(rows, sheet))
    return path


def _aux_rows() -> list[dict]:
    """BIP 辅助明细账合成数据（科目编码覆盖 1002/1122/6602 前缀，与既有 BIP 测试同口径）。"""
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


def _income_rows(n: int) -> list[list]:
    """利润表两列合成行（项目名称 + 金额），n 为数据行数（不含表头）。"""
    return [["项目名称", "本期金额（万元）"]] + [[f"项目{i}", float(i)] for i in range(n)]


def _csv_bytes(rows: list[list]) -> bytes:
    """个人记账 CSV 合成字节（中文表头 + utf-8-sig）。"""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(personal_svc.CSV_HEADERS)
    w.writerows(rows)
    return buf.getvalue().encode("utf-8-sig")


_PL_ROWS = [
    ["2026-10-01", "支出", "10", "现金", "餐饮", "一"],
    ["2026-10-02", "支出", "20", "现金", "餐饮", "二"],
    ["2026-10-03", "支出", "30", "现金", "餐饮", "三"],
]

_VAULT_DF_ROWS = 3

BANK_COLUMNS = ["科目编码", "科目名称", "日期", "摘要", "借方本币", "贷方本币", "辅助核算"]
_BANK_ROWS = [
    ("10020101", "银行存款-基本户", "2026-09-01", "支付2026年9月员工工资", 0.0, 1000.00, "合成测试项目"),
    ("10020101", "银行存款-基本户", "2026-09-02", "支付银行账户手续费", 0.0, 235.50, "合成测试项目"),
    ("10020101", "银行存款-基本户", "2026-09-03", "支付9月办公场所电费", 0.0, 680.75, "合成测试项目"),
    ("10020101", "银行存款-基本户", "2026-09-04", "支付办公室绿植租摆费", 0.0, 320.00, "合成测试项目"),
    ("10020101", "银行存款-基本户", "2026-09-05", "支付商铺租赁押金", 0.0, 5000.00, "合成测试项目"),
    ("10020101", "银行存款-基本户", "2026-09-06", "支付广告宣传活动物料费", 0.0, 888.80, "合成测试项目"),
    ("10020101", "其他应收款-往来单位", "2026-09-07", "支付杂项往来结算款", 0.0, 150.00, "合成测试项目"),
]


def _write_bank_xlsx(path: Path) -> Path:
    """合成银行明细账 xlsx：表头在第 11 行（镜像真实引擎 header=10 读取口径）。"""
    df = pd.DataFrame(_BANK_ROWS, columns=BANK_COLUMNS)
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, startrow=10, sheet_name="明细")
    return path


async def _fake_read_upload_limited(file, max_bytes, chunk_size=1024 * 1024):
    """与共享读取器契约一致的测试替身：分块累积读取，超限抛 ValueError（路由映射 400）。"""
    chunks, total = [], 0
    while True:
        chunk = await file.read(chunk_size)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise ValueError(f"文件超过大小上限（{max_bytes} 字节）")
        chunks.append(chunk)
    return b"".join(chunks)


class _StubClassifier:
    """费用分类引擎替身：镜像真实引擎接口（银行明细账表头第 11 行起读取）。"""

    def __init__(self) -> None:
        self.df = None

    def read_bank_data(self, path: str):
        self.df = pd.read_excel(path, header=10)
        return self.df if not self.df.empty else None

    def integrate_data(self):
        return self.df

    def clean_data(self):
        return self.df

    def classify_expenses(self):
        out = self.df.copy()
        out["费用类别"] = "合成分类"
        return out

    def summarize_expenses(self):
        return self.df

    def export_results(self, path: str) -> str:
        Path(path).write_bytes(b"stub-xlsx")
        return path


def _install_stub_engine(monkeypatch) -> list:
    """把费用引擎替身装进 service._ensure_engine；返回实例列表（观测引擎是否被使用）。

    行数护栏契约要求在引擎读取之前可检测拒绝，故用替身验证「超限时引擎零使用」；
    替身不依赖真实引擎目录，任何机器上结果确定。
    """
    created: list[_StubClassifier] = []

    class _Factory:
        def __call__(self) -> _StubClassifier:
            inst = _StubClassifier()
            created.append(inst)
            return inst

    monkeypatch.setattr(
        expense_svc,
        "_ensure_engine",
        lambda: {
            "classifier_cls": _Factory(),
            "load_rules": lambda **kw: {},
            "app_config": None,
            "engine_dir": "stub",
        },
    )
    return created


@pytest.fixture()
def upload_dir(tmp_path, monkeypatch):
    """助手上传目录改道临时目录：测试不碰 backend/data/uploads。"""
    target = tmp_path / "uploads"
    target.mkdir()
    monkeypatch.setattr(assistant_router, "UPLOAD_DIR", target)
    return target


@pytest.fixture()
def vault_dir(monkeypatch, tmp_path, client):
    """数据仓 DuckDB 库文件改道 tmp_path；client 夹具负责建表。"""
    base = tmp_path / "vault_data"
    monkeypatch.setattr(vault_svc, "get_finance_data_dir", lambda: base)
    return base


@pytest.fixture()
def expense_dir(monkeypatch, tmp_path, client):
    """费用分类模块数据目录改道 tmp_path；client 夹具负责建表。"""
    base = tmp_path / "finance"
    monkeypatch.setattr(settings, "finance_data_dir", str(base))
    return base


# ---------------- 共享口径：常量值与接线 ----------------

def test_shared_import_rows_limit_is_200000():
    """共享上限为 200_000 行（A06 统一口径）。"""
    assert MAX_IMPORT_ROWS == SHARED_LIMIT


def test_wired_consumers_reference_shared_import_rows():
    """五个已接线消费侧引用同一共享常量对象（而非重复定义/回退值/导入层级写错）。"""
    assert assistant_router.MAX_IMPORT_ROWS is MAX_IMPORT_ROWS
    assert tax_importer.MAX_IMPORT_ROWS is MAX_IMPORT_ROWS
    assert bip_svc.MAX_IMPORT_ROWS is MAX_IMPORT_ROWS
    assert personal_svc.MAX_IMPORT_ROWS is MAX_IMPORT_ROWS
    assert vault_svc.MAX_IMPORT_ROWS is MAX_IMPORT_ROWS


def test_expense_service_wired_to_shared_import_rows():
    """费用分类确实接入共享上限（与 app.import_limits 同一对象，非回退值）。"""
    assert expense_svc.MAX_IMPORT_ROWS is MAX_IMPORT_ROWS


# ---------------- assistant 聊天 Excel：行数上限 ----------------

def test_assistant_xlsx_over_limit_aborts_conversion(tmp_path, monkeypatch):
    """首表行数（含表头）超上限：转换函数可检测中止（_RowLimitExceeded）。"""
    src = _write_xlsx(tmp_path / "many.xlsx", [["a", "b"], [1, 2], [3, 4], [5, 6]])
    dst = tmp_path / "many.csv"
    monkeypatch.setattr(assistant_router, "MAX_IMPORT_ROWS", 3, raising=False)
    with pytest.raises(assistant_router._RowLimitExceeded, match="上限"):
        assistant_router._xlsx_first_sheet_to_csv(src, dst, assistant_router.MAX_IMPORT_ROWS)


def test_assistant_xlsx_at_limit_converts(tmp_path, monkeypatch):
    """首表行数恰好等于上限（含表头）：正常转换并返回写入行数（边界不误拒）。"""
    src = _write_xlsx(tmp_path / "ok.xlsx", [["a", "b"], [1, 2], [3, 4], [5, 6]])
    dst = tmp_path / "ok.csv"
    monkeypatch.setattr(assistant_router, "MAX_IMPORT_ROWS", 4, raising=False)
    written = assistant_router._xlsx_first_sheet_to_csv(src, dst, assistant_router.MAX_IMPORT_ROWS)
    assert written == 4
    assert len(dst.read_text(encoding="utf-8-sig").splitlines()) == 4


def test_assistant_upload_over_limit_http_400_leaves_no_file(client, upload_dir, monkeypatch):
    """上传端点行数超限：400「上限」，xlsx 与半成品 CSV 都不遗留（无半成品）。"""
    monkeypatch.setattr(assistant_router, "read_upload_limited", _fake_read_upload_limited, raising=False)
    monkeypatch.setattr(assistant_router, "MAX_IMPORT_ROWS", 2, raising=False)
    resp = client.post(
        "/api/assistant/upload",
        data={"session_id": "sess-limit-01"},
        files={"file": ("many.xlsx", _xlsx_bytes([["a"], [1], [2], [3]]), XLSX_MIME)},
    )
    assert resp.status_code == 400
    assert "上限" in resp.json()["detail"]
    assert not any(p.is_file() for p in upload_dir.rglob("*")), "行数超限不得遗留 xlsx/CSV 半成品"


def test_assistant_upload_at_limit_converts_csv(client, upload_dir, monkeypatch):
    """上传端点行数在上限内：正常转换第一张工作表为 CSV（边界不误拒）。"""
    monkeypatch.setattr(assistant_router, "read_upload_limited", _fake_read_upload_limited, raising=False)
    monkeypatch.setattr(assistant_router, "MAX_IMPORT_ROWS", 100, raising=False)
    resp = client.post(
        "/api/assistant/upload",
        data={"session_id": "sess-limit-02"},
        files={"file": ("ok.xlsx", _xlsx_bytes([["科目", "金额"], ["现金", 1], ["存款", 2]]), XLSX_MIME)},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["kind"] == "csv_converted" and body["name"] == "ok.csv"
    assert (upload_dir / "sess-limit-02" / "ok.csv").is_file()


# ---------------- 税务导入：模板 / XML 两条解析路径 ----------------

def test_tax_template_parser_over_limit_aborts(tmp_path, monkeypatch):
    """标准模板解析：单 sheet 数据行数超限即抛 ImportRowLimitExceeded（解析阶段中止）。"""
    path = _write_xlsx(tmp_path / "t.xlsx", _income_rows(6), sheet="利润表")
    monkeypatch.setattr(tax_importer, "MAX_IMPORT_ROWS", 3, raising=False)
    with pytest.raises(tax_importer.ImportRowLimitExceeded, match="上限"):
        tax_importer.StandardTemplateParser.parse(str(path))


def test_tax_template_parser_at_limit_accepts(tmp_path, monkeypatch):
    """标准模板解析：数据行数恰好等于上限时正常解析（边界不误拒）。"""
    path = _write_xlsx(tmp_path / "t.xlsx", _income_rows(6), sheet="利润表")
    monkeypatch.setattr(tax_importer, "MAX_IMPORT_ROWS", 6, raising=False)
    parsed = tax_importer.StandardTemplateParser.parse(str(path))
    assert len(parsed["income_statement"]) == 6


def test_tax_xml_parser_over_limit_aborts_streaming(tmp_path, monkeypatch):
    """财务系统导出解析：流式逐行计数（含表头），超限即中止，不在内存累积全表。"""
    path = _write_xlsx(tmp_path / "x.xlsx", _income_rows(50), sheet="Sheet")
    monkeypatch.setattr(tax_importer, "MAX_IMPORT_ROWS", 50, raising=False)
    with pytest.raises(tax_importer.ImportRowLimitExceeded, match="上限"):
        tax_importer.FinancialSystemXmlParser.parse(str(path))


def test_tax_xml_parser_at_limit_accepts(tmp_path, monkeypatch):
    """财务系统导出解析：含表头总行数恰好等于上限时正常解析（边界不误拒）。"""
    path = _write_xlsx(tmp_path / "x.xlsx", _income_rows(50), sheet="Sheet")
    monkeypatch.setattr(tax_importer, "MAX_IMPORT_ROWS", 51, raising=False)
    _shared, rows = tax_importer.FinancialSystemXmlParser.parse(str(path))
    assert len(rows) == 51


# ---------------- BIP 企业记账 Excel：行数上限 ----------------

def test_bip_read_excel_over_limit_rejects(tmp_path, monkeypatch):
    """BIP 解析阶段可检测行数溢出：数据行数超限抛 ValueError（预览/确认入口映射 400）。"""
    path = _write_xlsx(tmp_path / "aux.xlsx", _aux_rows())
    monkeypatch.setattr(bip_svc, "MAX_IMPORT_ROWS", 3, raising=False)
    with pytest.raises(ValueError, match="上限"):
        bip_svc.read_bip_excel(path)


def test_bip_read_excel_at_limit_accepts(tmp_path, monkeypatch):
    """BIP 数据行数恰好等于上限：正常解析（边界不误拒）。"""
    path = _write_xlsx(tmp_path / "aux.xlsx", _aux_rows())
    monkeypatch.setattr(bip_svc, "MAX_IMPORT_ROWS", 4, raising=False)
    assert len(bip_svc.read_bip_excel(path)) == 4


# ---------------- 个人记账 CSV：行数上限 ----------------

def test_personal_import_csv_over_limit_writes_no_transaction(client, monkeypatch):
    """CSV 数据行数超限：可检测拒绝（ValueError「上限」）且不写任何交易（无半成品）。"""
    monkeypatch.setattr(personal_svc, "MAX_IMPORT_ROWS", 2, raising=False)
    with SessionLocal() as db:
        with pytest.raises(ValueError, match="上限"):
            personal_svc.import_csv(db, _csv_bytes(_PL_ROWS))
        db.rollback()
    with SessionLocal() as db:
        assert db.query(PlTransaction).count() == 0


def test_personal_import_csv_at_limit_imports_all(client, monkeypatch):
    """CSV 数据行数恰好等于上限：全部正常导入（边界不误拒）。"""
    monkeypatch.setattr(personal_svc, "MAX_IMPORT_ROWS", 3, raising=False)
    with SessionLocal() as db:
        result = personal_svc.import_csv(db, _csv_bytes(_PL_ROWS))
    assert result["imported"] == 3
    with SessionLocal() as db:
        assert db.query(PlTransaction).count() == 3


# ---------------- 数据仓入仓：行数上限 ----------------

def test_vault_ingest_over_limit_registers_nothing(client, vault_dir, monkeypatch):
    """入仓数据行数超限：可检测拒绝，数据集未注册、DuckDB 库文件未创建（无半成品）。"""
    df = pd.DataFrame({"月份": ["2026-09"] * _VAULT_DF_ROWS, "金额": [1.0, 2.0, 3.0]})
    monkeypatch.setattr(vault_svc, "MAX_IMPORT_ROWS", 2, raising=False)
    with pytest.raises(ValueError, match="上限"):
        vault_svc.ingest_dataframe("t_over", df)
    with SessionLocal() as db:
        assert db.query(VaultDataset).count() == 0
    assert not (vault_dir / "vault" / "vault.duckdb").exists(), "超限不得创建 DuckDB 库文件"


def test_vault_ingest_at_limit_accepts(client, vault_dir, monkeypatch):
    """入仓数据行数恰好等于上限：正常入仓并登记（边界不误拒）。"""
    df = pd.DataFrame({"月份": ["2026-09"] * _VAULT_DF_ROWS, "金额": [1.0, 2.0, 3.0]})
    monkeypatch.setattr(vault_svc, "MAX_IMPORT_ROWS", 3, raising=False)
    result = vault_svc.ingest_dataframe("t_ok", df)
    assert result["rows"] == _VAULT_DF_ROWS
    with SessionLocal() as db:
        assert db.query(VaultDataset).filter(VaultDataset.table_name == "t_ok").count() == 1


# ---------------- 费用分类 Excel：行数上限 ----------------

# 护栏口径（_guard_import_rows/_excel_row_count）：各工作表非空行合计，含表头行、
# 跳过标题空区（与 assistant 聊天 Excel 转换「含表头」同口径）；超限早停于
# limit+1 行，不完整解析无限行；护栏先于引擎执行与运行记录写库。
_BANK_NONEMPTY_ROWS = len(_BANK_ROWS) + 1  # 1 表头 + 7 数据行


def test_expense_classify_over_limit_aborts_before_engine_and_db(client, expense_dir, tmp_path, monkeypatch):
    """费用分类非空行数（含表头口径）超限：在引擎读取与任何写库之前可检测拒绝。

    边界取「含表头非空行数 - 1」：同时验证超限拒绝、零写库、引擎零使用三项，
    且计数口径少一行即拒（表头行计入上限，与 assistant 口径一致）。
    """
    stored = _write_bank_xlsx(tmp_path / "合成银行明细账.xlsx")
    created = _install_stub_engine(monkeypatch)
    monkeypatch.setattr(expense_svc, "MAX_IMPORT_ROWS", _BANK_NONEMPTY_ROWS - 1, raising=False)

    raised = None
    with SessionLocal() as db:
        try:
            expense_svc.classify_file(db, "合成银行明细账.xlsx", stored)
        except ValueError as exc:
            raised = exc
        db.rollback()
    with SessionLocal() as db:
        runs = db.query(ExpenseRun).count()
        rows = db.query(ExpenseRow).count()
    problems = []
    if raised is None or "上限" not in str(raised):
        problems.append(f"超限未拒绝：classify_file 未抛行数上限错误（raised={raised!r}）")
    if runs or rows:
        problems.append(f"超限不得写库：ExpenseRun={runs}, ExpenseRow={rows}")
    if created:
        problems.append(f"超限不得驱动引擎：引擎被实例化 {len(created)} 次")
    assert not problems, "；".join(problems)


def test_expense_classify_at_limit_runs(client, expense_dir, tmp_path, monkeypatch):
    """费用分类非空行数（含表头）恰好等于上限：正常分类入库（边界不误拒）。"""
    stored = _write_bank_xlsx(tmp_path / "合成银行明细账.xlsx")
    _install_stub_engine(monkeypatch)
    monkeypatch.setattr(expense_svc, "MAX_IMPORT_ROWS", _BANK_NONEMPTY_ROWS, raising=False)
    with SessionLocal() as db:
        run = expense_svc.classify_file(db, "合成银行明细账.xlsx", stored)
    assert run.status == "success" and run.total_rows == len(_BANK_ROWS)
    with SessionLocal() as db:
        assert db.query(ExpenseRow).filter(ExpenseRow.run_id == run.id).count() == len(_BANK_ROWS)
