"""finance_bankflow（银行流水→BIP 导入模板）回归：全合成数据，不碰真实账号/H 编码。"""
import io

import openpyxl
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.modules.finance_bankflow import service as bf

client = TestClient(app)


# ---------------- 合成流水构造器（照真实 26 列表头形态） ----------------

_FLOW_HEADERS = [
    "组织", "银行", "开户银行", "账号", "币种", "交易日期", "收入", "支出",
    "当前余额", "用途", "对方户名", "对方账号", "来源", "资金交易生成状态",
    "附言(对账码)", "是否已对账", "银行交易流水号", "单证号码", "备注", "导出标识",
    "账户名称", "资金系统单据号", "被代理账号", "被代理账户开户行名", "被代理行号", "被代理账户名",
]


def _flow_row(account, d, income, expense, bal, use, counter, seq, currency="CNY-人民币"):
    row = ["P1-合成项目", "CCB-建设银行", "测试银行测试支行", account, currency, d,
           income, expense, bal, use, counter, "6222000000000000" + str(abs(hash(d)) % 10),
           "银企直联", "未生成", "", "×", seq, "", "", "已导出", "合成户名", "", "", "", "", ""]
    return row + [""] * (26 - len(row))


def _flow_xlsx(rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "可操作组织明细查看"
    ws.append(_FLOW_HEADERS)
    for r in rows:
        ws.append(r)
    out = io.BytesIO()
    wb.save(out)
    wb.close()
    return out.getvalue()


_ACC_A = "5500010000001111"
_ACC_B = "5500010000002222"


@pytest.fixture(autouse=True)
def _acc_map(tmp_path, monkeypatch):
    cfg = tmp_path / "config.local.yaml"
    cfg.write_text(
        'bank_accounts:\n  "%s": { ent: "TESTH001", name: "合成甲" }\n  "%s": { ent: "TESTH002", name: "合成乙" }\n'
        % (_ACC_A, _ACC_B),
        encoding="utf-8",
    )
    monkeypatch.setattr(bf, "_CONFIG_PATH", cfg)
    yield cfg


_ROWS_A = [
    _flow_row(_ACC_A, "2026-10-05", "100.5", "-", 1000.0, "合成收入甲一", "合成对手户名X", "SEQ-A1"),
    _flow_row(_ACC_A, "2026-10-12", "-", "50", 950.5, "合成支出甲二", "合成对手户名Y", "SEQ-A2"),
    _flow_row(_ACC_A, "2026-10-20", "33.33", "-", 983.83, "合成收入甲三", "合成对手户名Z", "SEQ-A3"),
    _flow_row(_ACC_A, "2026-10-18", "10", "-", 993.83, "无流水号行", "合成对手户名W", ""),
]


def test_parse_and_summarize():
    data = _flow_xlsx(_ROWS_A + [_flow_row(_ACC_B, "2026-10-08", "7", "-", 700.0, "乙一", "户B", "SEQ-B1")])
    out = bf.summarize_flow(data)
    assert out["total"] == 5
    # 预览只回掩码账号 + 稳定令牌，不再暴露完整账号
    acc_a = next(a for a in out["accounts"] if a["account"] == bf.mask_account(_ACC_A))
    assert acc_a["tail"] == "1111" and acc_a["account_token"].startswith("BK")
    assert acc_a["ent"] == "TESTH001" and acc_a["rows"] == 4
    assert acc_a["date_min"] == "20261005" and acc_a["date_max"] == "20261020"


def test_template_build_filters_and_mapping():
    data = _flow_xlsx(_ROWS_A)
    rows = bf.parse_flow(data)
    content, exported = bf.build_template(rows, _ACC_A, "TESTH001", "20261005", "20261020")
    assert exported == 4  # 无流水号行不再跳过（已合成稳定流水号）
    wb = openpyxl.load_workbook(io.BytesIO(content))
    ws = wb["银行对账单"]
    assert ws.cell(row=4, column=4).value == "accentity_code"
    assert ws.cell(row=10, column=4).value == "TESTH001"
    assert ws.cell(row=10, column=5).value == "20261005"
    assert ws.cell(row=10, column=7).value == "人民币##CNY"
    assert ws.cell(row=10, column=8).value == "收入"
    assert ws.cell(row=10, column=9).value == 100.5
    assert ws.cell(row=11, column=8).value == "支出"
    assert ws.cell(row=12, column=5).value == "20261020"
    # 10-18 无流水号行：bank_seq_no 为合成值（SYN 前缀）
    assert str(ws.cell(row=13, column=12).value).startswith("SYN")
    assert ws.max_row == 13  # 9 头 + 4 数据
    wb.close()


def test_template_date_range_exclusive():
    data = _flow_xlsx(_ROWS_A)
    rows = bf.parse_flow(data)
    content, exported = bf.build_template(rows, _ACC_A, "TESTH001", "20261001", "20261010")
    assert exported == 1
    wb = openpyxl.load_workbook(io.BytesIO(content))
    assert wb["银行对账单"].max_row == 10
    wb.close()


def test_export_filename_format():
    assert (
        bf.export_filename(_ACC_A, "2026-10-01", "2026-10-23", "CCB-建设银行")
        == "建行1111_银行流水导入模板_20261001-20261023.xlsx"
    )
    assert (
        bf.export_filename("135670445971", "2026-10-01", "2026-10-30", "BOC-中国银行")
        == "中行5971_银行流水导入模板_20261001-20261030.xlsx"
    )
    assert bf.bank_label("ICBC-工商银行") == "工行"
    assert bf.bank_label("ABC-农业银行") == "农行"


def test_api_preview_and_template_download():
    data = _flow_xlsx(_ROWS_A)
    r = client.post("/api/finance/bankflow/preview", files={"file": ("flow.xlsx", data)})
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 4
    acc = body["accounts"][0]
    assert acc["ent"] == "TESTH001" and acc["name"] == "合成甲"
    r2 = client.post(
        "/api/finance/bankflow/template",
        files={"file": ("flow.xlsx", data)},
        data={"account_token": acc["account_token"], "date_start": "2026-10-01", "date_end": "2026-10-23"},
    )
    assert r2.status_code == 200
    assert r2.headers["content-type"] == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    disp = r2.headers["content-disposition"]
    assert "filename*=" in disp and "%E5%BB%BA%E8%A1%8C1111" in disp
    wb = openpyxl.load_workbook(io.BytesIO(r2.content))
    assert wb["银行对账单"].max_row == 13
    wb.close()


def test_api_unmapped_account_400():
    """流水里的账号未登记映射：令牌可反查到账号，但映射校验必须拒绝。"""
    unmapped = "9999000000009999"
    data = _flow_xlsx([_flow_row(unmapped, "2026-10-05", "1", "-", 1.0, "未登记", "户X", "SEQ-U1")])
    body = client.post("/api/finance/bankflow/preview", files={"file": ("flow.xlsx", data)}).json()
    token = body["accounts"][0]["account_token"]
    r = client.post(
        "/api/finance/bankflow/template",
        files={"file": ("flow.xlsx", data)},
        data={"account_token": token, "date_start": "", "date_end": ""},
    )
    assert r.status_code == 400
    assert "未在映射配置中登记" in r.json()["detail"]


def test_api_bad_date_400():
    data = _flow_xlsx(_ROWS_A)
    r = client.post(
        "/api/finance/bankflow/template",
        files={"file": ("flow.xlsx", data)},
        data={"account_token": "BK000000000000000000000000", "date_start": "2026/13/99", "date_end": ""},
    )
    assert r.status_code == 400
    assert "YYYY-MM-DD" in r.json()["detail"]


def test_api_no_config_400(tmp_path, monkeypatch):
    monkeypatch.setattr(bf, "_CONFIG_PATH", tmp_path / "missing.yaml")
    data = _flow_xlsx(_ROWS_A)
    r = client.post(
        "/api/finance/bankflow/template",
        files={"file": ("flow.xlsx", data)},
        data={"account_token": "BK000000000000000000000000", "date_start": "", "date_end": ""},
    )
    assert r.status_code == 400
    assert "映射" in r.json()["detail"]


def test_api_accounts_endpoint():
    r = client.get("/api/finance/bankflow/accounts")
    assert r.status_code == 200
    items = r.json()["items"]
    assert any(i["ent"] == "TESTH001" and i["tail"] == "1111" for i in items)


def test_boc_style_flow_without_seq_and_counter_account():
    """中行形态（无「银行交易流水号」、无「对方账号」，多「对账批号/退票状态/备注」）可解析。"""
    headers = [
        "组织", "银行", "开户银行", "账号", "币种", "交易日期", "收入", "支出",
        "当前余额", "用途", "对方户名", "来源", "资金交易生成状态", "附言(对账码)",
        "是否已对账", "单证号码", "备注", "导出标识", "对账批号", "资金系统单据号",
        "账户名称", "退票状态",
    ]
    row = [
        "P1-合成项目", "BOC-中国银行", "测试支行", "5500010000003333", "CNY-人民币",
        "2026-10-30", "53967.16", "-", 1189709.13, "合成用途", "合成对方", "银企直联",
        "未生成", "", "×", "", "合成用途", "已导出", "", "", "合成户名", "未退票",
    ]
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(headers)
    ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    wb.close()
    rows = bf.parse_flow(buf.getvalue())
    assert len(rows) == 1
    r = rows[0]
    assert r["account"] == "5500010000003333"
    assert r["tran_date"] == "20261030"
    assert r["income"] == 53967.16 and r["expense"] is None
    assert r["counter_account"] == ""  # 中行无对方账号列
    assert r["seq"].startswith("SYN") and len(r["seq"]) == 23


def test_synthetic_seq_stable_and_distinct():
    """合成流水号：同内容幂等、不同内容不撞。"""
    data = _flow_xlsx(_ROWS_A)
    first = bf.parse_flow(data)
    second = bf.parse_flow(data)
    assert [r["seq"] for r in first] == [r["seq"] for r in second]
    synth = [r["seq"] for r in first if r["seq"].startswith("SYN")]
    assert len(synth) == 1  # 仅无流水号那行合成
    assert len(set(r["seq"] for r in first)) == len(first)  # 全行唯一


def test_account_tail_mapping(tmp_path, monkeypatch):
    """配置 key 可为尾号（≥4 位）：未命中全号时按后缀匹配。"""
    cfg = tmp_path / "config.local.yaml"
    cfg.write_text('bank_accounts:\n  "5971": { ent: "TESTH004", name: "尾号合成" }\n', encoding="utf-8")
    acc_map = bf.load_account_map(cfg)
    meta = bf.resolve_account(acc_map, "135670445971")
    assert meta == {"ent": "TESTH004", "name": "尾号合成"}
    assert bf.resolve_account(acc_map, "9999000000000000") is None


def test_generated_template_preserves_official_format():
    """格式保留回归（用户实测反馈：格式丢失会导致 BIP 导入失败）：
    列宽/表头填充/数据验证下拉/文本格式 @/字体/辅助 sheet 顺序必须与官方骨架一致。"""
    data = _flow_xlsx(_ROWS_A)
    rows = bf.parse_flow(data)
    content, exported = bf.build_template(rows, _ACC_A, "TESTH001", "", "")
    assert exported == 4
    wb = openpyxl.load_workbook(io.BytesIO(content))
    # 辅助 sheet 齐全且顺序与官方一致（ReadMe 在首位）
    assert wb.sheetnames == ["ReadMe", "银行对账单", "importDropDownContent", "参照", "importTemplateInfo"]
    ws = wb["银行对账单"]
    # 列宽保留
    assert abs(ws.column_dimensions["A"].width - 25.0) < 0.01
    assert abs(ws.column_dimensions["K"].width - 62.9) < 0.1  # Excel 列宽存储有舍入
    # 表头填充色保留（r6 灰底）
    assert ws.cell(row=6, column=4).fill.fgColor.rgb == "FFF2F2F2"
    # 数据验证下拉保留：币种（G 列）/收付方向（H 列）覆盖数据行
    dvs = list(ws.data_validations.dataValidation)
    assert len(dvs) == 2
    refs = " ".join(str(dv.sqref) for dv in dvs)
    assert "G10" in refs and "H10" in refs
    # 数据行样式与官方示例行一致：文本格式 @ + SimSun
    for col in (5, 6, 9, 12):
        cell = ws.cell(row=10, column=col)
        assert cell.number_format == "@", f"c{col} 应为文本格式"
        assert cell.font.name == "SimSun"
    # 数据值仍是正确的文本/数值
    assert ws.cell(row=10, column=4).value == "TESTH001"
    assert ws.cell(row=10, column=12).value == "SEQ-A1"
    wb.close()