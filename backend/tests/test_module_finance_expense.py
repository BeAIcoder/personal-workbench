"""finance_expense 模块测试：引擎直挂、分类结果与规则 JSON 推导对拍、503 故障隔离、上传校验。

约定：
- 全部使用合成数据（pandas 写入 tmp_path 的银行明细账 xlsx），不触碰任何真实业务文件；
- 模块数据目录（inbox/outbox/tmp）经 monkeypatch 指向 tmp_path，测试不写 tmp_path 之外的位置；
- 引擎目录缺失的环境中，引擎相关用例整体 skip（503 降级用例不受影响，照常运行）。
"""
import importlib
import sys
from pathlib import Path

import pandas as pd
import pytest

from app.config import settings
from app.modules import get_loaded_modules
from app.modules.finance_expense import service

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

# 引擎可用性（测试模块导入时探测一次；缺失则引擎用例 skip）
ENGINE_DIR = service.find_engine_dir()
requires_engine = pytest.mark.skipif(ENGINE_DIR is None, reason="费用分类引擎目录不存在，跳过引擎相关用例")

# 合成银行明细账：7 行覆盖 命中（多分类）/ 未命中；摘要均避开引擎特殊规则链关键词
# （押金/增值税/网络系统/改造/确认计提/项目名），保证分类只由规则 JSON 关键词首中优先决定
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
    """合成银行明细账 xlsx：引擎按 header=10（第 11 行）读取，模拟真实文件上方的标题区。"""
    df = pd.DataFrame(ROWS, columns=BANK_COLUMNS)
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, startrow=10, sheet_name="明细")


def _pure_rules() -> dict:
    """加载规则 JSON 权威源（config_module=None 确证无覆盖），用于推导期望分类。"""
    assert ENGINE_DIR is not None
    key = str(ENGINE_DIR)
    if key not in sys.path:
        sys.path.insert(0, key)
    rules_loader = importlib.import_module("rules_loader")
    return rules_loader.load_classification_rules(config_module=None)


def _expected_categories() -> list[str]:
    """按规则 JSON 对合成摘要做首中优先推导（含排除词语义），作为分类结果对拍基准。"""
    rules = _pure_rules()
    expected = []
    for row in ROWS:
        summary = row[3].lower()
        matched = "未分类"
        for category, rule in rules.items():
            if any(ex.lower() in summary for ex in rule.get("exclude_keywords", [])):
                continue
            if any(kw.lower() in summary for kw in rule.get("keywords", [])):
                matched = category
                break
        expected.append(matched)
    return expected


@pytest.fixture()
def expense_env(client, tmp_path, monkeypatch):
    """隔离模块数据目录到 tmp_path，并重置引擎缓存。"""
    monkeypatch.setattr(settings, "finance_data_dir", str(tmp_path / "finance"))
    monkeypatch.setattr(settings, "finance_expense_engine_path", "")
    service._reset_engine_state()
    yield client
    service._reset_engine_state()


def _post_classify(client, xlsx_path: Path, filename: str = "合成银行明细账.xlsx"):
    with open(xlsx_path, "rb") as f:
        return client.post("/api/finance/expense/classify", files={"file": (filename, f, XLSX_MIME)})


# ---------- 模块注册 ----------


def test_module_registered_and_mounted():
    """manifest 校验通过、路由挂载、Agent 工具注册，均在宿主启动时完成。"""
    loaded = {m.name: m for m in get_loaded_modules()}
    assert "finance_expense" in loaded
    manifest = loaded["finance_expense"]
    assert manifest.prefix == "/api/finance/expense"
    assert manifest.title == "费用分类"
    assert set(manifest.tables) == {"mod_expense_run", "mod_expense_row"}
    assert manifest.menu == {"group": "财务", "icon": "Histogram", "order": 10}

    from app.agents.tools import TOOL_SPECS

    assert "expense_runs_summary" in TOOL_SPECS
    spec = TOOL_SPECS["expense_runs_summary"]
    assert spec["readonly"] is True
    assert callable(spec["func"])


# ---------- 分类主流程（与规则 JSON 推导对拍） ----------


@requires_engine
def test_classify_upload_end_to_end(expense_env, tmp_path):
    xlsx = tmp_path / "合成银行明细账.xlsx"
    _make_bank_xlsx(xlsx)
    expected = _expected_categories()
    expected_matched = sum(1 for c in expected if c != "未分类")
    assert 0 < expected_matched < len(ROWS)  # 合成数据须同时覆盖命中与未命中

    resp = _post_classify(expense_env, xlsx)
    assert resp.status_code == 201, resp.text
    run = resp.json()
    assert run["status"] == "success"
    assert run["original_filename"] == "合成银行明细账.xlsx"
    assert run["total_rows"] == len(ROWS)
    assert run["matched_rows"] == expected_matched
    assert run["duration_sec"] >= 0
    assert run["artifact_path"].startswith("finance_expense/outbox/")

    # 详情：逐行分类结果与规则 JSON 推导一致，金额与合成输入一致
    detail = expense_env.get(f"/api/finance/expense/runs/{run['id']}")
    assert detail.status_code == 200
    payload = detail.json()
    rows = sorted(payload["rows"], key=lambda r: r["row_no"])
    assert [r["category"] for r in rows] == expected
    assert [r["hit"] for r in rows] == [c != "未分类" for c in expected]
    assert [r["amount"] for r in rows] == pytest.approx([r[5] for r in ROWS])
    assert [r["summary"] for r in rows] == [r[3] for r in ROWS]

    # 汇总统计与明细一致
    stats = {s["category"]: s for s in payload["stats"]}
    assert set(stats) == set(expected)
    for s in payload["stats"]:
        assert s["count"] == expected.count(s["category"])
        assert s["amount"] == pytest.approx(
            sum(row[5] for row, c in zip(ROWS, expected) if c == s["category"])
        )

    # 产物文件真实落在模块 outbox（tmp_path 内）
    out_file = tmp_path / "finance" / run["artifact_path"]
    assert out_file.is_file() and out_file.suffix == ".xlsx"


@requires_engine
def test_download_rfc5987_chinese_filename(expense_env, tmp_path):
    xlsx = tmp_path / "合成银行明细账.xlsx"
    _make_bank_xlsx(xlsx)
    run_id = _post_classify(expense_env, xlsx).json()["id"]

    dl = expense_env.get(f"/api/finance/expense/runs/{run_id}/download")
    assert dl.status_code == 200
    assert dl.content[:2] == b"PK"  # xlsx 是 zip 容器
    disposition = dl.headers.get("content-disposition", "")
    assert "filename*=" in disposition  # RFC5987 编码的中文文件名
    from urllib.parse import unquote

    assert "费用分类结果_合成银行明细账.xlsx" in unquote(disposition)

    assert expense_env.get("/api/finance/expense/runs/99999/download").status_code == 404


@requires_engine
def test_runs_pagination(expense_env, tmp_path):
    xlsx = tmp_path / "合成银行明细账.xlsx"
    _make_bank_xlsx(xlsx)
    _post_classify(expense_env, xlsx)
    _post_classify(expense_env, xlsx)

    page1 = expense_env.get("/api/finance/expense/runs", params={"page": 1, "page_size": 1}).json()
    assert page1["total"] == 2 and len(page1["items"]) == 1
    page2 = expense_env.get("/api/finance/expense/runs", params={"page": 2, "page_size": 1}).json()
    assert len(page2["items"]) == 1
    assert page1["items"][0]["id"] > page2["items"][0]["id"]  # id 倒序

    assert expense_env.get("/api/finance/expense/runs/99999").status_code == 404


@requires_engine
def test_rules_summary_and_reload(expense_env):
    rules = _pure_rules()
    resp = expense_env.get("/api/finance/expense/rules/summary")
    assert resp.status_code == 200
    summary = resp.json()
    assert summary["rules_file"].endswith("classification_rules.json")
    assert Path(summary["rules_file"]).is_file()
    # 分类条数与权威源一致（引擎自带 config 不覆盖 CLASSIFICATION_RULES 时等于纯 JSON 条数）
    assert summary["total_categories"] == len(rules)
    assert summary["total_keywords"] == sum(len(r.get("keywords", [])) for r in rules.values())
    assert summary["categories"] == list(rules.keys())  # 顺序即匹配优先级

    reload_resp = expense_env.post("/api/finance/expense/rules/reload")
    assert reload_resp.status_code == 200
    body = reload_resp.json()
    assert body["ok"] is True
    assert body["summary"]["total_categories"] == len(rules)


@requires_engine
def test_agent_tool_expense_runs_summary(expense_env, tmp_path):
    from app.agents.tools import TOOL_SPECS

    xlsx = tmp_path / "合成银行明细账.xlsx"
    _make_bank_xlsx(xlsx)
    _post_classify(expense_env, xlsx)

    result = TOOL_SPECS["expense_runs_summary"]["func"](5)
    assert result["ok"] is True
    assert result["total_runs"] == 1
    item = result["recent"][0]
    assert item["total_rows"] == len(ROWS)
    assert item["status"] == "success"


@requires_engine
def test_bad_content_xlsx_marks_run_failed(expense_env, tmp_path):
    """合法 xlsx 但不是银行明细账（表头位置不符）：400 + 运行记录落库 failed。"""
    bad = tmp_path / "普通表.xlsx"
    pd.DataFrame({"a": [1, 2], "b": [3, 4]}).to_excel(bad, index=False)
    resp = _post_classify(expense_env, bad, filename="普通表.xlsx")
    assert resp.status_code == 400
    assert "银行明细账" in resp.json()["detail"]

    runs = expense_env.get("/api/finance/expense/runs").json()
    assert runs["total"] == 1
    assert runs["items"][0]["status"] == "failed"
    assert runs["items"][0]["total_rows"] == 0


# ---------- 上传约束（扩展名白名单 / 大小限制 / 防路径穿越） ----------


def test_upload_rejects_bad_extension_and_empty(expense_env, tmp_path):
    resp = expense_env.post(
        "/api/finance/expense/classify",
        files={"file": ("notes.txt", b"hello", "text/plain")},
    )
    assert resp.status_code == 400
    assert "格式" in resp.json()["detail"]

    resp = expense_env.post(
        "/api/finance/expense/classify",
        files={"file": ("empty.xlsx", b"", XLSX_MIME)},
    )
    assert resp.status_code == 400
    assert "空" in resp.json()["detail"]


def test_upload_size_limit(expense_env, monkeypatch):
    monkeypatch.setattr(service, "MAX_UPLOAD_BYTES", 16)
    resp = expense_env.post(
        "/api/finance/expense/classify",
        files={"file": ("big.xlsx", b"x" * 64, XLSX_MIME)},
    )
    assert resp.status_code == 400
    assert "上限" in resp.json()["detail"]


@requires_engine
def test_upload_filename_path_traversal_neutralized(expense_env, tmp_path):
    """文件名带路径穿越片段时只取 basename 入库，落盘存储名与原名无关。"""
    xlsx = tmp_path / "合成银行明细账.xlsx"
    _make_bank_xlsx(xlsx)
    resp = _post_classify(expense_env, xlsx, filename="../../穿越/合成银行明细账.xlsx")
    assert resp.status_code == 201, resp.text
    assert resp.json()["original_filename"] == "合成银行明细账.xlsx"
    inbox = tmp_path / "finance" / "finance_expense" / "inbox"
    stored = list(inbox.glob("*.xlsx"))
    assert len(stored) == 1
    assert ".." not in stored[0].name and stored[0].name != "合成银行明细账.xlsx"


# ---------- 故障隔离：引擎目录缺失时模块仍可用，引擎接口 503 ----------


def test_engine_unavailable_degrades_503(client, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "finance_data_dir", str(tmp_path / "finance"))
    monkeypatch.setattr(settings, "finance_expense_engine_path", str(tmp_path / "不存在的引擎目录"))
    service._reset_engine_state()
    try:
        xlsx = tmp_path / "x.xlsx"
        _make_bank_xlsx(xlsx)
        resp = _post_classify(client, xlsx)
        assert resp.status_code == 503
        assert "费用分类引擎" in resp.json()["detail"]

        summary = client.get("/api/finance/expense/rules/summary")
        assert summary.status_code == 503
        assert "费用分类引擎" in summary.json()["detail"]

        reload_resp = client.post("/api/finance/expense/rules/reload")
        assert reload_resp.status_code == 503

        # 与引擎无关的接口不受影响（故障隔离）
        assert client.get("/api/finance/expense/runs").status_code == 200
        assert client.get("/api/finance/hello/ping").status_code == 200
    finally:
        service._reset_engine_state()
