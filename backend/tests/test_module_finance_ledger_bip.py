"""finance_ledger_bip 模块测试。

全部使用合成数据：pandas 生成的三类 BIP 导出 xlsx（凭证列表 / 辅助明细账 / 科目余额表，
列名按实测契约）写入 tmp_path；模块数据目录经 monkeypatch 重定向到 tmp_path，
不写真实数据目录；不含任何真实公司名 / 账号。

覆盖：类型识别（正/误/未知）、人工列映射、上传安全（扩展名/大小/token）、
借贷平衡校验、重复凭证跳过、组合查询筛选、科目汇总、入仓联动降级与成功路径、
Agent 工具注册与行为。
"""
import sys
from pathlib import Path

import pandas as pd
import pytest

from app.agents.tools import TOOL_SPECS
from app.modules import get_loaded_modules
from app.modules.finance_ledger_bip import service as svc

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


# ---------------- 合成数据 ----------------

def _write_xlsx(path: Path, rows: list[dict], title_row: str | None = None) -> Path:
    """用 pandas 生成合成 xlsx；title_row 模拟 BIP 导出顶部的标题行。"""
    df = pd.DataFrame(rows)
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        if title_row is not None:
            pd.DataFrame([[title_row]]).to_excel(writer, index=False, header=False)
            df.to_excel(writer, index=False, startrow=1)
        else:
            df.to_excel(writer, index=False)
    return path


def _voucher_rows() -> list[dict]:
    """凭证列表合成数据：记-0001 平衡，记-0002 不平衡（贷方少 50）。"""
    return [
        {"日期": "2026-09-05", "核算账簿": "测试账簿甲", "凭证号": "记-0001", "摘要": "支付办公用品",
         "借方金额": 100.0, "贷方金额": 0.0, "会计凭证号": "PZ26090001"},
        {"日期": "2026-09-05", "核算账簿": "测试账簿甲", "凭证号": "记-0001", "摘要": "支付办公用品",
         "借方金额": 0.0, "贷方金额": 100.0, "会计凭证号": "PZ26090001"},
        {"日期": "2026-09-06", "核算账簿": "测试账簿甲", "凭证号": "记-0002", "摘要": "收到服务费",
         "借方金额": 500.0, "贷方金额": 0.0, "会计凭证号": "PZ26090002"},
        {"日期": "2026-09-06", "核算账簿": "测试账簿甲", "凭证号": "记-0002", "摘要": "收到服务费",
         "借方金额": 0.0, "贷方金额": 450.0, "会计凭证号": "PZ26090002"},
    ]


def _aux_rows() -> list[dict]:
    """辅助明细账合成数据：两凭证四行，科目编码覆盖 1002/1122/6602 前缀。"""
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


def _balance_rows() -> list[dict]:
    """科目余额表合成数据：列名用宽松变体（期初借方余额 / 本期借方发生额 …）。"""
    return [
        {"科目编码": "1002", "科目名称": "银行存款", "期初借方余额": 1000.0, "期初贷方余额": 0.0,
         "本期借方发生额": 800.0, "本期贷方发生额": 300.0, "期末借方余额": 1500.0, "期末贷方余额": 0.0},
        {"科目编码": "6602", "科目名称": "管理费用", "期初借方余额": 0.0, "期初贷方余额": 0.0,
         "本期借方发生额": 300.0, "本期贷方发生额": 0.0, "期末借方余额": 300.0, "期末贷方余额": 0.0},
    ]


# ---------------- 公共工具 ----------------

@pytest.fixture()
def bip_dir(monkeypatch, tmp_path):
    """模块数据目录重定向到 tmp_path（测试不写真实数据目录）。"""
    d = tmp_path / "bip_data"
    monkeypatch.setattr(svc, "module_data_dir", lambda: d)
    return d


def _preview(client, path: Path, name: str = "sample.xlsx"):
    with open(path, "rb") as f:
        return client.post("/api/finance/ledger/bip/imports/preview",
                           files={"file": (name, f, XLSX_MIME)})


def _confirm(client, token: str, **kw):
    return client.post("/api/finance/ledger/bip/imports/confirm", json={"token": token, **kw})


def _import_vouchers(client, tmp_path) -> dict:
    """走通 预览→确认 全流程导入凭证列表，返回 confirm 响应体。"""
    path = _write_xlsx(tmp_path / "vouchers.xlsx", _voucher_rows(), title_row="凭证列表")
    token = _preview(client, path).json()["token"]
    resp = _confirm(client, token)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _import_aux(client, tmp_path) -> dict:
    path = _write_xlsx(tmp_path / "aux.xlsx", _aux_rows())
    token = _preview(client, path).json()["token"]
    resp = _confirm(client, token)
    assert resp.status_code == 200, resp.text
    return resp.json()


# ---------------- manifest 与 Agent 工具注册 ----------------

def test_manifest_loaded_and_tools_registered(client):
    loaded = {m.name: m for m in get_loaded_modules()}
    assert "finance_ledger_bip" in loaded
    m = loaded["finance_ledger_bip"]
    assert m.prefix == "/api/finance/ledger/bip"
    assert m.menu == {"group": "财务", "icon": "OfficeBuilding", "order": 50}
    assert m.tables == ["mod_bip_batch", "mod_bip_voucher", "mod_bip_entry", "mod_bip_balance"]
    for name in ("bip_voucher_query", "bip_balance_query"):
        spec = TOOL_SPECS.get(name)
        assert spec is not None and spec["readonly"] is True and callable(spec["func"])
        # 禁止删除类：工具名与描述不含删除语义
        assert "删除" not in spec["description"]


# ---------------- 预览：类型识别与列映射 ----------------

def test_preview_detects_voucher_type_with_title_row(client, bip_dir, tmp_path):
    """凭证列表（含顶部标题行）→ 识别为 voucher，列映射齐全。"""
    path = _write_xlsx(tmp_path / "v.xlsx", _voucher_rows(), title_row="凭证列表")
    resp = _preview(client, path)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["file_type"] == "voucher"
    assert data["type_label"] == "凭证列表"
    assert data["row_count"] == 4 and len(data["preview_rows"]) == 4
    m = data["mapping"]
    assert m["date"] == "日期" and m["ledger"] == "核算账簿" and m["voucher_no"] == "凭证号"
    assert m["debit"] == "借方金额" and m["credit"] == "贷方金额"
    assert m["voucher_full_no"] == "会计凭证号"
    assert set(data["required_fields"]) == {"date", "ledger", "voucher_no"}


def test_preview_detects_aux_type(client, bip_dir, tmp_path):
    """辅助明细账 → 识别为 aux，借方/贷方本币列正确映射。"""
    path = _write_xlsx(tmp_path / "a.xlsx", _aux_rows())
    data = _preview(client, path).json()
    assert data["file_type"] == "aux"
    m = data["mapping"]
    assert m["subject_code"] == "科目编码" and m["subject_name"] == "科目名称"
    assert m["debit"] == "借方本币" and m["credit"] == "贷方本币"


def test_preview_detects_balance_type_variant_headers(client, bip_dir, tmp_path):
    """科目余额表（宽松变体列名）→ 识别为 balance，期初/本期/期末借贷全部命中。"""
    path = _write_xlsx(tmp_path / "b.xlsx", _balance_rows())
    data = _preview(client, path).json()
    assert data["file_type"] == "balance"
    m = data["mapping"]
    assert m["opening_debit"] == "期初借方余额" and m["opening_credit"] == "期初贷方余额"
    assert m["period_debit"] == "本期借方发生额" and m["period_credit"] == "本期贷方发生额"
    assert m["closing_debit"] == "期末借方余额" and m["closing_credit"] == "期末贷方余额"


def test_preview_unknown_type_goes_manual(client, bip_dir, tmp_path):
    """识别不了的列签名 → unknown + 空映射（进入人工列映射）。"""
    path = _write_xlsx(tmp_path / "u.xlsx", [{"业务日期": "2026-09-01", "账簿": "测试账簿甲",
                                              "编号": "记-100", "借": 10.0, "贷": 10.0, "说明": "测试"}])
    data = _preview(client, path).json()
    assert data["file_type"] == "unknown"
    assert data["mapping"] == {}


def test_detect_file_type_unit():
    """类型识别纯函数：三类签名正判 + 无关列负判。"""
    assert svc.detect_file_type(["日期", "核算账簿", "凭证号", "摘要", "借方金额", "贷方金额"]) == "voucher"
    assert svc.detect_file_type(["科目编码", "科目名称", "日期", "凭证号", "借方本币", "贷方本币"]) == "aux"
    assert svc.detect_file_type(["科目编码", "科目名称", "期初借方", "期末贷方"]) == "balance"
    assert svc.detect_file_type(["姓名", "城市"]) == "unknown"


# ---------------- 上传安全 ----------------

def test_upload_rejects_bad_extension(client, bip_dir, tmp_path):
    resp = client.post("/api/finance/ledger/bip/imports/preview",
                       files={"file": ("notes.txt", b"hello", "text/plain")})
    assert resp.status_code == 400
    assert "Excel" in resp.json()["detail"]


def test_upload_rejects_oversize(client, bip_dir, tmp_path, monkeypatch):
    monkeypatch.setattr(svc, "MAX_UPLOAD_BYTES", 16)
    path = _write_xlsx(tmp_path / "v.xlsx", _voucher_rows())
    resp = _preview(client, path)
    assert resp.status_code == 400
    assert "上限" in resp.json()["detail"]


def test_confirm_rejects_bad_or_expired_token(client, bip_dir):
    assert _confirm(client, "../etc/passwd").status_code == 400
    assert _confirm(client, "a" * 32 + ".xlsx").status_code == 404  # 格式合法但文件不存在


# ---------------- 确认入库：平衡校验 / 重复检测 / 批次 ----------------

def test_confirm_balance_check_and_batch_listing(client, bip_dir, tmp_path):
    """不平衡凭证记 warning 不阻断入库；批次列表可见统计与警告。"""
    result = _import_vouchers(client, tmp_path)
    assert result["inserted_vouchers"] == 2
    assert result["skipped_vouchers"] == 0
    assert result["inserted_entries"] == 4
    assert any("记-0002" in w and "不平衡" in w for w in result["warnings"])

    batches = client.get("/api/finance/ledger/bip/imports").json()
    assert batches["total"] == 1
    b = batches["items"][0]
    assert b["file_type"] == "voucher" and b["filename"] == "sample.xlsx"
    assert b["inserted_vouchers"] == 2 and b["inserted_entries"] == 4
    assert len(b["warnings"]) == 1
    # 原文件已留档到模块 inbox
    assert b["stored_path"].endswith("sample.xlsx")
    assert Path(b["stored_path"]).is_file()
    assert str(tmp_path) in b["stored_path"]


def test_confirm_duplicate_vouchers_skipped(client, bip_dir, tmp_path):
    """重复（核算账簿+凭证号）跳过并计数；同库已有凭证不重复入库。"""
    first = _import_vouchers(client, tmp_path)
    assert first["inserted_vouchers"] == 2
    second = _import_vouchers(client, tmp_path)
    assert second["inserted_vouchers"] == 0
    assert second["skipped_vouchers"] == 2
    total = client.get("/api/finance/ledger/bip/vouchers").json()["total"]
    assert total == 2


def test_confirm_unknown_type_with_manual_mapping(client, bip_dir, tmp_path):
    """未知类型：不指定类型 → 400；人工指定类型 + 列映射 → 正常入库。"""
    path = _write_xlsx(tmp_path / "u.xlsx", [
        {"业务日期": "2026-09-01", "账簿": "测试账簿甲", "编号": "记-100", "借": 10.0, "贷": 10.0, "说明": "手工映射"},
        {"业务日期": "2026-09-01", "账簿": "测试账簿甲", "编号": "记-100", "借": 0.0, "贷": 10.0, "说明": "手工映射2"},
    ])
    token = _preview(client, path).json()["token"]

    bad = _confirm(client, token)
    assert bad.status_code == 400 and "类型" in bad.json()["detail"]

    # 显式取消必需字段（ledger 置空）→ 400 并指出中文字段名
    missing = _confirm(client, token, file_type="voucher",
                       mapping={"date": "业务日期", "voucher_no": "编号", "debit": "借",
                                "credit": "贷", "ledger": ""})
    assert missing.status_code == 400
    assert "核算账簿" in missing.json()["detail"]

    ok = _confirm(client, token, file_type="voucher",
                  mapping={"date": "业务日期", "ledger": "账簿", "voucher_no": "编号",
                           "summary": "说明", "debit": "借", "credit": "贷"})
    assert ok.status_code == 200, ok.text
    assert ok.json()["inserted_vouchers"] == 1
    assert ok.json()["inserted_entries"] == 2


def test_confirm_balance_import_requires_period(client, bip_dir, tmp_path):
    """科目余额表：缺 period → 400；带 period → 正常入库且批次记录期间。"""
    path = _write_xlsx(tmp_path / "b.xlsx", _balance_rows())
    token = _preview(client, path).json()["token"]

    bad = _confirm(client, token)
    assert bad.status_code == 400 and "期间" in bad.json()["detail"]

    bad2 = _confirm(client, token, period="2026-13")
    assert bad2.status_code == 400

    ok = _confirm(client, token, period="2026-09")
    assert ok.status_code == 200, ok.text
    assert ok.json()["inserted_balances"] == 2
    assert ok.json()["inserted_vouchers"] == 0
    b = client.get("/api/finance/ledger/bip/imports").json()["items"][0]
    assert b["file_type"] == "balance" and b["period"] == "2026-09"


# ---------------- 查询 ----------------

def test_vouchers_combined_filters_and_pagination(client, bip_dir, tmp_path):
    _import_vouchers(client, tmp_path)
    base = "/api/finance/ledger/bip/vouchers"

    assert client.get(base, params={"period": "2026-09"}).json()["total"] == 2
    assert client.get(base, params={"period": "2026-10"}).json()["total"] == 0
    assert client.get(base, params={"ledger": "账簿甲"}).json()["total"] == 2
    assert client.get(base, params={"ledger": "不存在的账簿"}).json()["total"] == 0
    assert client.get(base, params={"voucher_no": "0001"}).json()["total"] == 1
    assert client.get(base, params={"keyword": "服务费"}).json()["total"] == 1
    combo = client.get(base, params={"period": "2026-09", "voucher_no": "0002", "keyword": "服务"}).json()
    assert combo["total"] == 1 and combo["items"][0]["voucher_no"] == "记-0002"

    page1 = client.get(base, params={"page": 1, "page_size": 1}).json()
    page2 = client.get(base, params={"page": 2, "page_size": 1}).json()
    assert page1["total"] == 2 and len(page1["items"]) == 1
    assert len(page2["items"]) == 1 and page1["items"][0]["id"] != page2["items"][0]["id"]
    # 列表行带分录统计与平衡标记
    item = {i["voucher_no"]: i for i in client.get(base).json()["items"]}["记-0002"]
    assert item["entry_count"] == 2
    assert item["debit_total"] == 500.0 and item["credit_total"] == 450.0
    assert item["balanced"] is False


def test_voucher_detail_symmetric_entries(client, bip_dir, tmp_path):
    """凭证详情：借贷两栏对称，合计与平衡标记正确；不存在 → 404。"""
    _import_vouchers(client, tmp_path)
    items = client.get("/api/finance/ledger/bip/vouchers").json()["items"]
    vid = {i["voucher_no"]: i["id"] for i in items}["记-0001"]

    detail = client.get(f"/api/finance/ledger/bip/vouchers/{vid}").json()
    assert detail["voucher_no"] == "记-0001" and detail["voucher_full_no"] == "PZ26090001"
    assert detail["debit_total"] == 100.0 and detail["credit_total"] == 100.0
    assert detail["balanced"] is True
    assert len(detail["entries"]) == 2
    debits = sorted(e["debit"] for e in detail["entries"])
    credits = sorted(e["credit"] for e in detail["entries"])
    assert debits == [0.0, 100.0] and credits == [0.0, 100.0]
    assert all(e["line_no"] >= 1 for e in detail["entries"])

    assert client.get("/api/finance/ledger/bip/vouchers/99999").status_code == 404


def test_entries_query_by_subject_prefix(client, bip_dir, tmp_path):
    """分录查询：科目编码前缀 + 期间组合筛选。"""
    _import_aux(client, tmp_path)
    base = "/api/finance/ledger/bip/entries"

    r = client.get(base, params={"subject_prefix": "1002"}).json()
    assert r["total"] == 2
    assert all(i["subject_code"].startswith("1002") for i in r["items"])
    assert {i["subject_name"] for i in r["items"]} == {"银行存款-基本户", "银行存款-一般户"}
    # 分录行携带凭证上下文
    assert all(i["voucher_no"] and i["ledger"] == "测试账簿甲" for i in r["items"])

    assert client.get(base, params={"subject_prefix": "6602"}).json()["total"] == 1
    assert client.get(base, params={"subject_prefix": "1002", "period": "2026-10"}).json()["total"] == 0
    assert client.get(base, params={"period": "2026-09", "keyword": "货款"}).json()["total"] == 2


def test_balances_summary(client, bip_dir, tmp_path):
    """科目汇总：按期间聚合辅助明细账分录，借贷合计正确。"""
    _import_aux(client, tmp_path)
    resp = client.get("/api/finance/ledger/bip/balances/summary", params={"period": "2026-09"})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    by_code = {i["subject_code"]: i for i in data["items"]}
    assert by_code["100201"]["debit_total"] == 800.0 and by_code["100201"]["credit_total"] == 0.0
    assert by_code["112201"]["credit_total"] == 800.0
    assert by_code["660201"]["debit_total"] == 300.0
    assert by_code["100202"]["credit_total"] == 300.0
    assert data["debit_total"] == 1100.0 and data["credit_total"] == 1100.0
    assert data["subject_count"] == 4

    # 期间格式非法 → 400；无数据期间 → 空列表
    assert client.get("/api/finance/ledger/bip/balances/summary", params={"period": "2026-9"}).status_code == 400
    empty = client.get("/api/finance/ledger/bip/balances/summary", params={"period": "2026-10"}).json()
    assert empty["items"] == [] and empty["debit_total"] == 0.0


# ---------------- 数据仓联动 ----------------

def test_export_to_vault_unavailable_returns_503(client, bip_dir, tmp_path, monkeypatch):
    """finance_vault 导入失败（sys.modules 污染）→ 503 中文提示，绝不崩溃。"""
    _import_vouchers(client, tmp_path)
    # 让 vault.service 的 import 失败：子模块路径置 None 触发 ImportError
    monkeypatch.setitem(sys.modules, "app.modules.finance_vault.service", None)

    resp = client.post("/api/finance/ledger/bip/vouchers/export-to-vault", json={})
    assert resp.status_code == 503
    detail = resp.json()["detail"]
    assert "数据仓" in detail and "finance_vault" in detail


def test_export_to_vault_not_loaded_also_503(client, bip_dir, tmp_path, monkeypatch):
    """finance_vault 未加载（_load_vault_ingest 返回 None）→ 同样 503。"""
    _import_vouchers(client, tmp_path)
    monkeypatch.setattr(svc, "_load_vault_ingest", lambda: None)
    resp = client.post("/api/finance/ledger/bip/vouchers/export-to-vault", json={"period": "2026-09"})
    assert resp.status_code == 503


def test_export_to_vault_success_with_fake_ingest(client, bip_dir, tmp_path, monkeypatch):
    """vault 可用（注入假 ingest）：DataFrame 按契约传入，期间导出用 append、全量用 replace。"""
    _import_vouchers(client, tmp_path)
    calls = []

    def fake_ingest(table: str, df, mode: str = "replace") -> dict:
        calls.append({"table": table, "rows": len(df), "mode": mode,
                      "columns": list(df.columns)})
        return {"ok": True, "written": len(df)}

    monkeypatch.setattr(svc, "_load_vault_ingest", lambda: fake_ingest)

    resp = client.post("/api/finance/ledger/bip/vouchers/export-to-vault", json={"period": "2026-09"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True and body["rows"] == 2 and body["mode"] == "append"
    assert body["table"] == "bip_voucher"
    assert calls[0]["table"] == "bip_voucher" and calls[0]["mode"] == "append"
    assert {"voucher_no", "ledger", "debit_total", "credit_total"} <= set(calls[0]["columns"])

    resp_all = client.post("/api/finance/ledger/bip/vouchers/export-to-vault", json={})
    assert resp_all.json()["mode"] == "replace"

    bad = client.post("/api/finance/ledger/bip/vouchers/export-to-vault", json={"period": "2026-9"})
    assert bad.status_code == 400


# ---------------- Agent 工具行为 ----------------

def test_agent_tools_query_real_data(client, bip_dir, tmp_path):
    """bip_voucher_query / bip_balance_query 对真实导入数据可查、参数生效。"""
    _import_aux(client, tmp_path)

    vouchers = TOOL_SPECS["bip_voucher_query"]["func"](period="2026-09")
    assert vouchers["total"] == 2
    one = TOOL_SPECS["bip_voucher_query"]["func"](keyword="货款", limit=5)
    assert one["total"] == 1 and one["items"][0]["voucher_no"] == "记-0011"

    summary = TOOL_SPECS["bip_balance_query"]["func"](period="2026-09")
    assert summary["subject_count"] == 4 and summary["debit_total"] == 1100.0
    filtered = TOOL_SPECS["bip_balance_query"]["func"](period="2026-09", subject_prefix="1002")
    assert filtered["subject_count"] == 2
    # 期间缺省时取最近期间；非法期间不抛异常、返回 error
    assert TOOL_SPECS["bip_balance_query"]["func"]()["period"] == "2026-09"
    errored = TOOL_SPECS["bip_balance_query"]["func"](period="bad")
    assert "error" in errored and errored["items"] == []


# ---------------- 摘要关键词 LIKE 通配符转义 ----------------

def test_keyword_filter_escapes_like_wildcards(client, bip_dir, tmp_path):
    """关键词中的 % / _ 按字面匹配，不得作为 LIKE 通配符放大结果集。"""
    rows = [
        {"日期": "2026-09-05", "核算账簿": "测试账簿甲", "凭证号": "记-1001", "摘要": "支付办公用品",
         "借方金额": 100.0, "贷方金额": 0.0},
        {"日期": "2026-09-05", "核算账簿": "测试账簿甲", "凭证号": "记-1001", "摘要": "支付办公用品",
         "借方金额": 0.0, "贷方金额": 100.0},
        {"日期": "2026-09-06", "核算账簿": "测试账簿甲", "凭证号": "记-1002", "摘要": "按5%计提利息",
         "借方金额": 50.0, "贷方金额": 0.0},
        {"日期": "2026-09-06", "核算账簿": "测试账簿甲", "凭证号": "记-1002", "摘要": "按5%计提利息",
         "借方金额": 0.0, "贷方金额": 50.0},
    ]
    path = _write_xlsx(tmp_path / "kw.xlsx", rows)
    token = _preview(client, path).json()["token"]
    assert _confirm(client, token).status_code == 200

    vouchers = "/api/finance/ledger/bip/vouchers"
    entries = "/api/finance/ledger/bip/entries"

    # "%" 按字面匹配：只命中摘要真实含 % 的凭证/分录（修复前会匹配全部）
    v = client.get(vouchers, params={"keyword": "%"}).json()
    assert v["total"] == 1 and v["items"][0]["voucher_no"] == "记-1002"
    e = client.get(entries, params={"keyword": "%"}).json()
    assert e["total"] == 2 and all("%" in i["summary"] for i in e["items"])

    # "_" 同样按字面匹配：数据中无下划线 → 0 条
    assert client.get(vouchers, params={"keyword": "_"}).json()["total"] == 0
    assert client.get(entries, params={"keyword": "_"}).json()["total"] == 0

    # 普通关键词语义不变
    assert client.get(vouchers, params={"keyword": "办公用品"}).json()["total"] == 1
    assert client.get(entries, params={"keyword": "计提"}).json()["total"] == 2


# ---------- 表头定位：真实用友导出元数据块（外部验证 B2 回归） ----------

def test_locate_header_row_real_bip_metadata_block():
    """用友辅助明细账导出的元数据块有 12 行，其中第 7 行「对方科目/显示对方科目」
    含 2 个「科目」关键词命中——修复前扫描窗口只有 10 行，该行成为窗口内最高命中，
    真正的表头行（第 13 行）被挤出窗口导致类型识别失败（unknown）。"""
    meta = [
        ["账簿", "脱敏单位A,脱敏单位B"],
        ["期间", "2026-01 至 2026-12"],
        ["科目版本基准日期", "2026-12-31"],
        ["科目级次", "一至末级"],
        ["币种范围", "本币"],
        ["返回币种", "本币"],
        ["对方科目", "显示对方科目"],  # 关键词碰撞行（2 命中）
        ["凭证范围", "包含未记账,包含损益结转,包含暂存"],
        ["其他", "本月合计本年累计无发生不显示"],
        ["排序方式", "查询对象+制单日期"],
        ["展示方向", "双向展示"],
        ["辅助明细账"],
        ["科目编码", "科目名称", "日期", "核算账簿", "凭证号", "摘要",
         "对方科目编码", "对方科目名称", "借方本币", "贷方本币", "方向", "本币余额", "来源应用"],
    ]
    raw = pd.DataFrame(meta)
    assert svc._locate_header_row(raw) == 12  # 0-based：第 13 行


def test_preview_real_bip_aux_metadata_block(client, bip_dir, tmp_path):
    """端到端：带 12 行元数据块的辅助明细账（真实导出形状）预览应识别为 aux。"""
    rows = [["科目编码", "科目名称", "日期", "核算账簿", "凭证号", "摘要", "对方科目编码", "对方科目名称", "借方本币", "贷方本币", "方向", "本币余额", "来源应用"]]
    rows += [["1002", "银行存款", "2026-01-05", "脱敏单位A", "记-1", "测试摘要", "6601", "销售费用", 100.0, None, "借", 100.0, "凭证管理"]]
    meta = [
        ["账簿", "脱敏单位A"], ["期间", "2026-01 至 2026-12"], ["科目版本基准日期", "2026-12-31"],
        ["科目级次", "一至末级"], ["币种范围", "本币"], ["返回币种", "本币"],
        ["对方科目", "显示对方科目"], ["凭证范围", "包含未记账"], ["其他", "无"],
        ["排序方式", "查询对象+制单日期"], ["展示方向", "双向展示"], ["辅助明细账"],
    ]
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    for r in meta + rows:
        ws.append(r)
    path = tmp_path / "real-aux.xlsx"
    wb.save(path)
    body = _preview(client, path).json()
    assert body["file_type"] == "aux", body
    assert body["mapping"].get("ledger") == "核算账簿"


def test_locate_header_row_two_level_header():
    """余额表两级表头：子表头行（借方本币×4/贷方本币×4，总命中 8）不得击败
    主表头行（科目/账簿/期初/本期/期末，5 类）——按关键词类别数评分。"""
    meta = [["账簿", "脱敏单位A"], ["期间", "2026-09 至 2026-09"], ["科目版本基准日期", "2026-09-30"],
            ["科目级次", "所有末级"], ["币种范围", "本币"], ["返回币种", "本币"], ["凭证范围", "包含未记账"],
            ["其他", "无"], ["余额方向", "双向"], ["本年累计", "显示本年累计"], ["余额显示", "双向显示"],
            ["余额借贷方", "余额借贷方显示"], ["展示方式", "核算账簿显示"], ["表体展示", "树形展示"],
            ["科目辅助余额表"],
            ["科目编码", "科目名称", "核算账簿", "期初余额", "", "本期发生", "", "本年累计", "", "期末余额"],
            ["", "", "", "方向", "本币金额", "借方本币", "贷方本币", "借方本币", "贷方本币", "方向", "本币金额"]]
    raw = pd.DataFrame(meta)
    assert svc._locate_header_row(raw) == 15  # 0-based：第 16 行主表头


# ---------- 真实用友凭证列表格式（外部验证第 3 章 B1 缺口修复回归） ----------

def test_detect_file_type_real_voucher_and_aux_shapes():
    """真实用友导出形状：凭证列表（科目列+分录摘要+制单人，无账簿列）→ voucher；
    辅助明细账（对方科目列）→ aux。修复前凭证列表落入 aux。"""
    real_voucher_cols = ["期间", "凭证日期", "凭证号", "分录摘要", "科目", "辅助核算", "币种",
                         "借方金额", "贷方金额", "制单人", "审批人", "记账人", "凭证状态"]
    real_aux_cols = ["科目编码", "科目名称", "日期", "核算账簿", "凭证号", "摘要",
                     "对方科目编码", "对方科目名称", "借方本币", "贷方本币", "方向", "本币余额"]
    assert svc.detect_file_type(real_voucher_cols) == "voucher"
    assert svc.detect_file_type(real_aux_cols) == "aux"
    # 无对方科目也无分录摘要/制单人的旧形状仍保守判 aux
    assert svc.detect_file_type(["科目编码", "日期", "凭证号", "借方本币"]) == "aux"


def test_import_real_voucher_multisheet_sheet_name_as_ledger(client, bip_dir, tmp_path):
    """端到端：真实凭证列表格式（多 sheet 按账簿分、无核算账簿列、续行不重复凭证号）
    预览识别 voucher 且账簿映射取 sheet 名（合成列），确认入库后续行进组、sheet 名落账。"""
    import openpyxl
    header = ["期间", "凭证日期", "凭证号", "分录摘要", "科目", "借方金额", "贷方金额", "制单人"]
    sheets = {
        "脱敏单位A": [
            header,
            ["2026-01", "2026-01-05", "记-1", "支付办公用品", "1001 库存现金", 100.0, None, "员工001"],
            ["", "", "", "支付办公用品", "6601 销售费用", None, 100.0, ""],  # 续行：凭证号空
        ],
        "脱敏单位B": [
            header,
            ["2026-01", "2026-01-06", "记-1", "收到服务费", "1002 银行存款", 200.0, None, "员工002"],
            ["", "", "", "收到服务费", "6001 主营业务收入", None, 200.0, ""],
        ],
    }
    wb = openpyxl.Workbook()
    for name, rows in sheets.items():
        ws = wb.create_sheet(name)
        for r in rows:
            ws.append(r)
    wb.remove(wb.active)
    path = tmp_path / "real-vouchers.xlsx"
    wb.save(path)

    prev = _preview(client, path).json()
    assert prev["file_type"] == "voucher", prev
    assert prev["mapping"]["ledger"] == svc.SHEET_LEDGER_COL
    assert prev["row_count"] == 4  # 两个 sheet 各 2 行（含续行）

    resp = _confirm(client, prev["token"])
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["inserted_vouchers"] == 2  # 两账簿各 1 张（凭证号同名但账簿不同不去重）
    assert body["inserted_entries"] == 4   # 续行分录计入
    assert body["skipped_rows"] == 0

    vs = client.get("/api/finance/ledger/bip/vouchers", params={"size": 50}).json()
    ledgers = sorted(v["ledger"] for v in vs["items"])
    assert ledgers == ["脱敏单位A", "脱敏单位B"]
    # 重复导入：相同（账簿+凭证号）跳过
    prev2 = _preview(client, path).json()
    body2 = _confirm(client, prev2["token"]).json()
    assert body2["inserted_vouchers"] == 0 and body2["skipped_vouchers"] == 2
