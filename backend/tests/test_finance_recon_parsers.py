# -*- coding: utf-8 -*-
"""finance_recon 四类源文件解析与导入测试（Task 2）。

覆盖 brief 列出的场景：
- 合并单元格展开、核销表重复「应收/核销」表头按列组语义区分；
- 文本金额显式解析与两位小数精度、非法文本金额/公式无缓存必须报
  文件 + 工作表 + 行 + 单元格，不归零、不经 float；
- 空/合计/重复表头行跳过、无关合同全部导入、缺必需表头报列名、
  多个同构工作表拒绝、后缀与 kind 不符拒绝、仅表头无数据行拒绝；
- PDF 锚点集中声明、无文本层/缺关键字段/多锚点/锚点后非法金额明确失败；
- 导入：skipped_duplicates 幂等、固定落盘 UPLOAD_DIR/recon/<batch_id>/、
  解析失败保留可诊断摘要、details 不含金额与完整账号、
  check 前置校验（scope 非空 + 四类各至少一份 + 全部解析成功）、
  删除源级联 records + suggested matches、人工引用 409、文件安全删除。

一律临时 SQLite + 程序生成合成 xlsx/pdf（openpyxl/pymupdf），不复制真实样本。
"""
from __future__ import annotations

import hashlib
import io
import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import openpyxl
import pymupdf
import pytest

# ---------- 合成夹具（程序生成，全部 SYN 假数据） ----------

WO_HEADER = [
    "序号", "区域", "项目", "合同号", "合同类型", "账单月", "品牌", "铺位",
    "应收(账单)", "核销(账单)", "单据号", "核销日期", "取消日期", "核销类型",
    "费用类型", "费用期", "应收(费用期)", "核销(费用期)", "核销后欠费",
    "税率", "预收类别", "预收款金额",
]

# 重复裸表头变体：第 1 组应收/核销 = 账单级（列组语义），第 2 组 = 费用期级
BARE_WO_HEADER = [
    "序号", "合同号", "费用类型", "费用期", "应收", "核销", "单据号", "核销类型",
    "核销后欠费", "税率", "预收类别", "预收款金额", "应收", "核销",
]

RC_HEADER = [
    "来源", "单据类型", "父单据", "子单据", "传输id", "业务类型", "付款户名", "付款账号",
    "收款户名", "收款账号", "收款金额", "本次认领", "手续费", "交易类型", "用途",
    "交易时间", "摘要", "状态", "认领时间", "认领人", "审核时间", "审核人",
]

QF_HEADER = ["序号", "合同", "账单月", "缴费截止", "费用开始", "费用结束", "合计", "固定租金", "综管费"]

BILL_LINES = [
    "付款通知单",
    "账单编号：ZD-SYN-202610-01",
    "合同编号：HT-SYN-001",
    "账单月：2026-10",
    "出账日期：2026-11-01",
    "缴费截止：2026-11-15",
    "累计应付",
    "1,234.56",
]


def _wo_row(
    *,
    seq=1,
    contract="HT-SYN-001",
    doc="DZ-SYN-0001",
    fee="固定租金",
    period="2026.10.01-2026.10.31",
    ar_fee="100.00",
    cl_fee="100.00",
    left="0.00",
    doc_ar="100.00",
    doc_cl="100.00",
    prepay=None,
    bill_month="2026-10",
    wo_date=date(2026, 11, 5),
):
    return [
        seq, "华东", "项目甲", contract, "固定租金", bill_month, "品牌甲", "铺位甲",
        doc_ar, doc_cl, doc, wo_date, None, "正常核销",
        fee, period, ar_fee, cl_fee, left, "0.05", None, prepay,
    ]


def _rc_row(
    *,
    doc="SK-SYN-0001",
    recv="500.00",
    claim="500.00",
    fee=None,
    ts=datetime(2026, 11, 6, 10, 0, 0),
    payer="付款方甲",
    payer_acct="6222000011112222",
    payee="收款方乙",
    payee_acct="110002003456",
):
    return [
        "线下", "收款", doc, "", "T-SYN-1", "收款", payer, payer_acct,
        payee, payee_acct, recv, claim, fee, "转账", "货款",
        ts, "摘要甲", "已审核", datetime(2026, 11, 6, 11, 0, 0), "认领人甲",
        datetime(2026, 11, 6, 12, 0, 0), "审核人甲",
    ]


def _qf_row(
    *,
    seq=1,
    contract="HT-SYN-001",
    month="2026-10",
    due=date(2026, 11, 15),
    start=date(2026, 10, 1),
    end=date(2026, 10, 31),
    total="300.00",
    rent="200.00",
    mgmt="100.00",
):
    return [seq, contract, month, due, start, end, total, rent, mgmt]


def _xlsx_bytes(header, rows, *, sheet="数据", merges=()) -> bytes:
    bio = io.BytesIO()
    wb = openpyxl.Workbook()  # openpyxl.Workbook 不支持 with（项目坑位）
    ws = wb.active
    ws.title = sheet
    ws.append(header)
    for row in rows:
        ws.append(list(row))
    for rng in merges:
        ws.merge_cells(rng)
    wb.save(bio)
    wb.close()
    return bio.getvalue()


def _xlsx(path: Path, header, rows, *, sheet="数据", merges=()) -> Path:
    path.write_bytes(_xlsx_bytes(header, rows, sheet=sheet, merges=merges))
    return path


def _pdf_bytes(lines, *, with_text=True) -> bytes:
    doc = pymupdf.open()
    page = doc.new_page()
    if with_text:
        y = 72
        for line in lines:
            page.insert_text((72, y), line, fontname="china-s", fontsize=11)
            y += 18
    bio = io.BytesIO()
    doc.save(bio)
    doc.close()
    return bio.getvalue()


def _pdf(path: Path, lines, *, with_text=True) -> Path:
    path.write_bytes(_pdf_bytes(lines, with_text=with_text))
    return path


def _parse(path, kind):
    from app.modules.finance_recon.importer import parse_source

    return parse_source(path, kind)


# ---------- 直触库用例的建表保障（契约：test_test_isolation_contract） ----------


@pytest.fixture(autouse=True)
def _ensure_tables():
    from sqlalchemy import inspect

    from app.database import Base, engine

    if not inspect(engine).has_table("mod_recon_batch"):
        Base.metadata.create_all(bind=engine)
    yield


@pytest.fixture()
def upload_root(tmp_path, monkeypatch):
    from app import config as app_config

    root = tmp_path / "uploads"
    monkeypatch.setattr(app_config, "UPLOAD_DIR", root)
    return root


def _make_batch(status="draft", scope=("HT-SYN-001", "HT-SYN-002")) -> int:
    from app.database import SessionLocal
    from app.modules.finance_recon.models import ReconBatch

    with SessionLocal() as db:
        batch = ReconBatch(
            name="解析测试批次",
            as_of=date(2026, 11, 18),
            contract_scope=list(scope),
            status=status,
        )
        db.add(batch)
        db.commit()
        return batch.id


def _import_all(bid) -> dict[str, int]:
    """四类各导一份合成文件，返回 kind → source_id。"""
    from app.database import SessionLocal
    from app.modules.finance_recon import importer

    payload = {
        "writeoff": (
            "核销明细样本.xlsx",
            _xlsx_bytes(WO_HEADER, [_wo_row(seq=1), _wo_row(seq=2, contract="HT-SYN-002", doc="DZ-SYN-0002")]),
        ),
        "claim": (
            "认领记录样本.xlsx",
            _xlsx_bytes(RC_HEADER, [_rc_row(), _rc_row(doc="SK-SYN-0002", recv=200.0, claim=150.0, fee="2.00")]),
        ),
        "arrear": (
            "欠费明细样本.xlsx",
            _xlsx_bytes(QF_HEADER, [_qf_row(), _qf_row(seq=2, contract="HT-SYN-002")]),
        ),
        "bill": ("账单通知单样本.pdf", _pdf_bytes(BILL_LINES)),
    }
    ids: dict[str, int] = {}
    with SessionLocal() as db:
        for kind, (name, blob) in payload.items():
            res = importer.import_source(db, bid, kind, (name, blob))
            ids[kind] = res.source_id
    return ids


# ---------- 核销 writeoff 解析 ----------


def test_writeoff_maps_four_amount_columns_and_drops_doc_level(tmp_path):
    rows = [
        _wo_row(seq=1, ar_fee="100.00", cl_fee="100.00", left="0.00", doc_ar="120.00", doc_cl="118.00"),
        _wo_row(
            seq=2,
            contract="HT-SYN-002",
            doc="DZ-SYN-0002",
            ar_fee=88.5,
            cl_fee=88.5,
            left=0,
            doc_ar=90.0,
            doc_cl=90.0,
            prepay="10.00",
        ),
    ]
    recs = _parse(_xlsx(tmp_path / "核销样本.xlsx", WO_HEADER, rows), "writeoff")
    assert len(recs) == 2
    r1 = recs[0]
    assert r1.kind == "writeoff"
    assert r1.row_no == 2  # Excel 实际行号（表头在第 1 行）
    assert (r1.contract_no, r1.doc_no, r1.fee_type) == ("HT-SYN-001", "DZ-SYN-0001", "固定租金")
    assert r1.fee_period_start == date(2026, 10, 1)
    assert r1.fee_period_end == date(2026, 10, 31)
    assert r1.amount_1 == Decimal("100.00")  # 费用期应收
    assert r1.amount_2 == Decimal("100.00")  # 费用期核销
    assert r1.amount_3 == Decimal("0.00")  # 核销后欠费
    assert r1.amount_4 is None  # 预收款金额未填 → None，不归零
    assert r1.occur_date == date(2026, 11, 5)  # 核销日期
    # 单据级应收/核销只随解析结果携带（供结构检查），不属于四个事实金额栏
    assert r1.doc_amount_1 == Decimal("120.00")
    assert r1.doc_amount_2 == Decimal("118.00")
    assert r1.details["bill_month"] == "2026-10"
    assert r1.details["tax_rate"] == "0.05"
    assert r1.details["writeoff_date"] == "2026-11-05"  # details 为 JSON，日期序列化为 ISO 字符串
    r2 = recs[1]
    assert r2.amount_1 == Decimal("88.50")
    assert r2.amount_4 == Decimal("10.00")
    assert r2.doc_amount_1 == Decimal("90.00")


def test_writeoff_partial_amounts_allowed_for_structural_checks(tmp_path):
    """核销行允许只有核销后欠费（应收/核销为空）：P2-2 的「两侧皆空」前提由解析器锁定。"""
    rows = [_wo_row(seq=1, ar_fee=None, cl_fee=None, left="10.00", doc_ar=None, doc_cl=None)]
    recs = _parse(_xlsx(tmp_path / "核销样本.xlsx", WO_HEADER, rows), "writeoff")
    assert len(recs) == 1
    r = recs[0]
    assert r.amount_1 is None and r.amount_2 is None  # 不归零：未填就是未填
    assert r.amount_3 == Decimal("10.00")
    assert r.doc_amount_1 is None and r.doc_amount_2 is None


def test_writeoff_duplicate_bare_headers_resolved_by_column_group(tmp_path):
    row = [
        1, "HT-SYN-001", "固定租金", "2026.10.01-2026.10.31", "120.00", "118.00",
        "DZ-SYN-0001", "正常核销", "0.00", "0.05", None, None, "100.00", "100.00",
    ]
    recs = _parse(_xlsx(tmp_path / "核销副本.xlsx", BARE_WO_HEADER, [row]), "writeoff")
    assert len(recs) == 1
    r = recs[0]
    # 列组语义：第 1 组「应收/核销」= 账单级（结构检查用），第 2 组 = 费用期级（事实金额）
    assert r.amount_1 == Decimal("100.00")
    assert r.amount_2 == Decimal("100.00")
    assert r.doc_amount_1 == Decimal("120.00")
    assert r.doc_amount_2 == Decimal("118.00")
    assert r.doc_no == "DZ-SYN-0001"


def test_writeoff_duplicate_bare_headers_need_two_groups(tmp_path):
    # 必需列齐全、但只有一组裸「应收/核销」→ 列组语义无法区分账单级与费用期级
    header = ["序号", "合同号", "费用类型", "费用期", "应收", "核销", "单据号", "核销后欠费", "预收款金额"]
    row = [1, "HT-SYN-001", "固定租金", "2026.10.01-2026.10.31", "100.00", "100.00", "DZ-SYN-0001", "0.00", None]
    with pytest.raises(ValueError) as exc:
        _parse(_xlsx(tmp_path / "单组.xlsx", header, [row]), "writeoff")
    assert "应收" in str(exc.value) and "列组" in str(exc.value)


def test_writeoff_merged_cells_expanded(tmp_path):
    rows = [_wo_row(seq=1), _wo_row(seq=2, contract="HT-SYN-002", doc="DZ-SYN-0002")]
    rows[1][14] = None  # 费用类型（O 列）合并区非左上格
    recs = _parse(_xlsx(tmp_path / "合并.xlsx", WO_HEADER, rows, merges=("O2:O3",)), "writeoff")
    assert [r.fee_type for r in recs] == ["固定租金", "固定租金"]


def test_amount_text_variants_and_precision(tmp_path):
    rows = [
        _wo_row(seq=1, ar_fee="1,234.50", cl_fee="¥88.10", left=0, doc_ar="1,200.00", doc_cl="1,199.99"),
        _wo_row(
            seq=2,
            contract="HT-SYN-002",
            doc="DZ-SYN-0002",
            ar_fee="1234.565",
            cl_fee="-5.00",
            left="0",
        ),
    ]
    recs = _parse(_xlsx(tmp_path / "文本金额.xlsx", WO_HEADER, rows), "writeoff")
    assert recs[0].amount_1 == Decimal("1234.50")  # 千分位文本显式解析
    assert recs[0].amount_2 == Decimal("88.10")  # 货币符号剥离
    assert recs[1].amount_1 == Decimal("1234.57")  # ROUND_HALF_UP 两位量化
    assert recs[1].amount_2 == Decimal("-5.00")
    assert recs[1].amount_3 == Decimal("0.00")


def test_illegal_amount_text_reports_file_sheet_row_cell(tmp_path):
    path = _xlsx(tmp_path / "坏样本.xlsx", WO_HEADER, [_wo_row(seq=1, ar_fee="12.3元")])
    with pytest.raises(ValueError) as exc:
        _parse(path, "writeoff")
    msg = str(exc.value)
    assert "坏样本.xlsx" in msg  # 文件
    assert "数据" in msg  # 工作表
    assert "第2行" in msg  # Excel 行号
    assert "Q2" in msg  # 单元格坐标
    assert "12.3元" in msg  # 原文摘录（可诊断）


def test_uncached_formula_reports_cell(tmp_path):
    path = _xlsx(tmp_path / "公式样本.xlsx", WO_HEADER, [_wo_row(seq=1, ar_fee="=SUM(A2:A2)")])
    with pytest.raises(ValueError) as exc:
        _parse(path, "writeoff")
    msg = str(exc.value)
    assert "公式" in msg and "未缓存" in msg
    assert "第2行" in msg and "Q2" in msg
    assert "公式样本.xlsx" in msg


def test_blank_total_and_repeated_header_rows_skipped(tmp_path):
    rows = [
        _wo_row(seq=1),
        [None] * 22,  # 空行
        [
            "合计", None, None, None, None, None, None, None, "220.00", "218.00", None,
            None, None, None, None, None, "220.00", "218.00", "0.00", None, None, None,
        ],  # 合计行
        list(WO_HEADER),  # 重复表头行
        _wo_row(seq=2, contract="HT-SYN-002", doc="DZ-SYN-0002"),
    ]
    recs = _parse(_xlsx(tmp_path / "跳行.xlsx", WO_HEADER, rows), "writeoff")
    assert len(recs) == 2
    assert [r.contract_no for r in recs] == ["HT-SYN-001", "HT-SYN-002"]
    assert recs[1].row_no == 6  # 实际 Excel 行号，而非数据序号


def test_unrelated_contracts_all_imported(tmp_path):
    rows = [_wo_row(seq=1, contract="HT-SYN-001"), _wo_row(seq=2, contract="XX-OTHER-777", doc="DZ-SYN-0009")]
    recs = _parse(_xlsx(tmp_path / "无关合同.xlsx", WO_HEADER, rows), "writeoff")
    # 无关合同（不在 scope）也必须导入；是否纳入汇总由批次 scope 决定，不得静默丢弃
    assert {r.contract_no for r in recs} == {"HT-SYN-001", "XX-OTHER-777"}


def test_missing_required_header_lists_column(tmp_path):
    header = [h for h in WO_HEADER if h != "核销后欠费"]
    row = [v for i, v in enumerate(_wo_row(seq=1)) if i != 18]
    with pytest.raises(ValueError) as exc:
        _parse(_xlsx(tmp_path / "缺列.xlsx", header, [row]), "writeoff")
    assert "核销后欠费" in str(exc.value)


def test_multiple_matching_sheets_rejected(tmp_path):
    bio = io.BytesIO()
    wb = openpyxl.Workbook()
    ws1 = wb.active
    ws1.title = "第一页"
    ws1.append(WO_HEADER)
    ws1.append(_wo_row(seq=1))
    ws2 = wb.create_sheet("第二页")
    ws2.append(WO_HEADER)
    ws2.append(_wo_row(seq=2))
    wb.save(bio)
    wb.close()
    path = tmp_path / "双表.xlsx"
    path.write_bytes(bio.getvalue())
    with pytest.raises(ValueError) as exc:
        _parse(path, "writeoff")
    msg = str(exc.value)
    assert "第一页" in msg and "第二页" in msg  # 不静默取第一个，防止丢数据


def test_header_only_file_rejected(tmp_path):
    with pytest.raises(ValueError) as exc:
        _parse(_xlsx(tmp_path / "空数据.xlsx", WO_HEADER, []), "writeoff")
    assert "数据行" in str(exc.value)


def test_row_missing_contract_rejected(tmp_path):
    row = _wo_row(seq=1)
    row[3] = None  # 合同号空
    with pytest.raises(ValueError) as exc:
        _parse(_xlsx(tmp_path / "缺合同.xlsx", WO_HEADER, [row]), "writeoff")
    assert "第2行" in str(exc.value) and "合同" in str(exc.value)


def test_suffix_mismatch_rejected(tmp_path):
    xlsx_path = _xlsx(tmp_path / "x.xlsx", WO_HEADER, [_wo_row()])
    with pytest.raises(ValueError):
        _parse(xlsx_path, "bill")
    pdf_path = _pdf(tmp_path / "b.pdf", BILL_LINES)
    with pytest.raises(ValueError):
        _parse(pdf_path, "writeoff")


# ---------- 认领 claim 解析 ----------


def test_claim_maps_amounts_and_masks_account(tmp_path):
    rows = [
        _rc_row(),
        _rc_row(doc="SK-SYN-0002", recv=200.0, claim=150.0, fee="2.00"),
    ]
    recs = _parse(_xlsx(tmp_path / "认领样本.xlsx", RC_HEADER, rows), "claim")
    assert len(recs) == 2
    r1 = recs[0]
    assert r1.kind == "claim"
    assert r1.doc_no == "SK-SYN-0001"  # 父单据作为单据号
    assert r1.amount_1 == Decimal("500.00")  # 收款金额
    assert r1.amount_2 == Decimal("500.00")  # 本次认领
    assert r1.amount_3 is None and r1.amount_4 is None  # 手续费未填 → None
    assert r1.occur_date == date(2026, 11, 6)  # 交易时间日期部分
    assert r1.details["payer_account_tail"] == "2222"
    assert r1.details["payee_account_tail"] == "3456"
    assert r1.details["payer_name"] == "付款方甲"
    assert r1.details["memo"] == "摘要甲"
    blob = json.dumps(r1.details, ensure_ascii=False)
    assert "6222000011112222" not in blob  # 完整账号不入 JSON，只留尾号
    r2 = recs[1]
    assert r2.amount_1 == Decimal("200.00")
    assert r2.amount_2 == Decimal("150.00")
    assert r2.amount_3 == Decimal("2.00")


def test_claim_missing_required_amount_reports_cell(tmp_path):
    row = _rc_row(recv=None)
    with pytest.raises(ValueError) as exc:
        _parse(_xlsx(tmp_path / "认领缺金额.xlsx", RC_HEADER, [row]), "claim")
    msg = str(exc.value)
    assert "第2行" in msg and "收款金额" in msg and "K2" in msg


# ---------- 欠费 arrear 解析 ----------


def test_arrear_maps_dates_amounts_and_imports_all_contracts(tmp_path):
    rows = [
        _qf_row(),
        _qf_row(seq=2, contract="QQ-OTHER-1", total="55.55", rent=None, mgmt=None),
        ["合计", None, None, None, None, None, "355.55", None, None],  # 合计行跳过
    ]
    recs = _parse(_xlsx(tmp_path / "欠费样本.xlsx", QF_HEADER, rows), "arrear")
    assert len(recs) == 2  # 无关前缀合同也导入（不按合同号前缀过滤）
    r1 = recs[0]
    assert r1.contract_no == "HT-SYN-001"
    assert r1.due_date == date(2026, 11, 15)  # 缴费截止
    assert r1.fee_period_start == date(2026, 10, 1)
    assert r1.fee_period_end == date(2026, 10, 31)
    assert (r1.amount_1, r1.amount_2, r1.amount_3) == (Decimal("300.00"), Decimal("200.00"), Decimal("100.00"))
    assert r1.amount_4 is None
    assert r1.details["bill_month"] == "2026-10"
    r2 = recs[1]
    assert r2.amount_1 == Decimal("55.55")
    assert r2.amount_2 is None and r2.amount_3 is None  # 未填 → None，不归零


def test_arrear_bad_date_reports_cell(tmp_path):
    row = _qf_row(due="2026年11月32日")
    with pytest.raises(ValueError) as exc:
        _parse(_xlsx(tmp_path / "坏日期.xlsx", QF_HEADER, [row]), "arrear")
    msg = str(exc.value)
    assert "第2行" in msg and "缴费截止" in msg


# ---------- 账单 bill PDF 解析 ----------


def test_bill_pdf_parses_anchors(tmp_path):
    recs = _parse(_pdf(tmp_path / "账单.pdf", BILL_LINES), "bill")
    assert len(recs) == 1
    r = recs[0]
    assert r.kind == "bill"
    assert r.row_no == 1
    assert r.doc_no == "ZD-SYN-202610-01"
    assert r.contract_no == "HT-SYN-001"
    assert r.amount_1 == Decimal("1234.56")  # 累计应付（锚点行下一行金额）
    assert r.occur_date == date(2026, 11, 1)
    assert r.due_date == date(2026, 11, 15)
    assert r.details["bill_month"] == "2026-10"


def test_bill_pdf_same_line_amount(tmp_path):
    lines = ["账单编号：ZD-SYN-2", "合同编号：HT-SYN-002", "累计应付 88.10"]
    recs = _parse(_pdf(tmp_path / "同行.pdf", lines), "bill")
    assert recs[0].amount_1 == Decimal("88.10")


def test_bill_pdf_no_text_layer(tmp_path):
    with pytest.raises(ValueError) as exc:
        _parse(_pdf(tmp_path / "扫描件.pdf", [], with_text=False), "bill")
    msg = str(exc.value)
    assert "文本层" in msg and "OCR" in msg  # 明确不支持扫描件


def test_bill_pdf_missing_required_label(tmp_path):
    lines = [line for line in BILL_LINES if not line.startswith("账单编号")]
    with pytest.raises(ValueError) as exc:
        _parse(_pdf(tmp_path / "缺编号.pdf", lines), "bill")
    assert "账单编号" in str(exc.value)


def test_bill_pdf_label_with_empty_value(tmp_path):
    lines = ["账单编号：", "合同编号：HT-SYN-001", "累计应付", "1.00"]
    with pytest.raises(ValueError) as exc:
        _parse(_pdf(tmp_path / "空值.pdf", lines), "bill")
    assert "账单编号" in str(exc.value)


def test_bill_pdf_multiple_total_anchors_rejected(tmp_path):
    lines = BILL_LINES + ["累计应付", "2.00"]
    with pytest.raises(ValueError) as exc:
        _parse(_pdf(tmp_path / "多锚点.pdf", lines), "bill")
    assert "累计应付" in str(exc.value) and "多" in str(exc.value)


def test_bill_pdf_amount_after_anchor_not_numeric(tmp_path):
    lines = ["账单编号：ZD-SYN-3", "合同编号：HT-SYN-001", "累计应付", "（详见附表）"]
    with pytest.raises(ValueError) as exc:
        _parse(_pdf(tmp_path / "坏金额.pdf", lines), "bill")
    msg = str(exc.value)
    assert "详见附表" in msg  # 指出无法解析的行内容位置


# ---------- 导入 importer ----------


def test_import_source_persists_records_and_file_layout(upload_root):
    from app.database import SessionLocal
    from app.modules.finance_recon import importer
    from app.modules.finance_recon.models import ReconRecord, ReconSource

    bid = _make_batch()
    data = _xlsx_bytes(WO_HEADER, [_wo_row(seq=1), _wo_row(seq=2, contract="HT-SYN-002", doc="DZ-SYN-0002")])
    with SessionLocal() as db:
        result = importer.import_source(db, bid, "writeoff", ("核销明细样本.xlsx", data))
    assert result.skipped_duplicates == 0
    assert result.row_count == 2
    assert result.contract_count == 2
    assert result.amount_totals["amount_1"] == "200.00"
    stored_dir = upload_root / "recon" / str(bid)
    files = list(stored_dir.iterdir())
    assert len(files) == 1 and files[0].name.endswith(".xlsx")
    with SessionLocal() as db:
        source = db.query(ReconSource).filter_by(batch_id=bid).one()
        assert source.parse_status == "parsed"
        assert source.row_count == 2
        assert source.sha256 == hashlib.sha256(data).hexdigest()
        assert Path(source.stored_path) == files[0].resolve()
        recs = db.query(ReconRecord).filter_by(batch_id=bid).order_by(ReconRecord.id).all()
        assert len(recs) == 2
        assert [r.row_no for r in recs] == [2, 3]  # Excel 实际行号
        assert recs[0].amount_1 == Decimal("100.00")
        assert len(recs[0].fingerprint) == 32
        assert recs[0].details["bill_month"] == "2026-10"
        # 单据级应收/核销不入事实表
        assert "doc_amount" not in json.dumps(recs[0].details, ensure_ascii=False)


def test_import_source_duplicate_content_skipped(upload_root):
    from app.database import SessionLocal
    from app.modules.finance_recon import importer
    from app.modules.finance_recon.models import ReconRecord, ReconSource

    bid = _make_batch()
    data = _xlsx_bytes(WO_HEADER, [_wo_row(seq=1)])
    with SessionLocal() as db:
        first = importer.import_source(db, bid, "writeoff", ("核销明细样本.xlsx", data))
        dup = importer.import_source(db, bid, "writeoff", ("换名字再传.xlsx", data))
    assert first.skipped_duplicates == 0
    assert dup.skipped_duplicates == 1
    assert dup.source_id == first.source_id
    stored_dir = upload_root / "recon" / str(bid)
    assert len(list(stored_dir.iterdir())) == 1  # 未重复落盘
    with SessionLocal() as db:
        assert db.query(ReconSource).filter_by(batch_id=bid).count() == 1
        assert db.query(ReconRecord).filter_by(batch_id=bid).count() == 1


def test_import_source_guards(upload_root):
    from app.database import SessionLocal
    from app.modules.finance_recon import importer, service

    bid = _make_batch()
    with SessionLocal() as db, pytest.raises(ValueError):
        importer.import_source(db, bid, "magic", ("a.xlsx", b"not-xlsx"))
    with SessionLocal() as db, pytest.raises(ValueError) as exc:
        importer.import_source(db, bid, "writeoff", ("空.xlsx", b""))
    assert "为空" in str(exc.value)
    with SessionLocal() as db, pytest.raises(ValueError) as exc:
        importer.import_source(db, bid, "writeoff", ("后缀.xlsx", _pdf_bytes(BILL_LINES)))
    assert "xlsx" in str(exc.value)
    # ready 批次不可上传
    with SessionLocal() as db:
        service.transition_batch(db, bid, "checking")
        service.transition_batch(db, bid, "ready")
    with SessionLocal() as db, pytest.raises(service.ReconConflictError) as exc:
        importer.import_source(db, bid, "writeoff", ("a.xlsx", _xlsx_bytes(WO_HEADER, [_wo_row()])))
    assert "不可替换" in str(exc.value)  # P2-1：ever_ready 守卫优先


def test_import_failure_keeps_failed_source_and_summary(upload_root):
    from app.database import SessionLocal
    from app.modules.finance_recon import importer, service
    from app.modules.finance_recon.models import ReconBatch, ReconRecord, ReconSource

    bid = _make_batch()
    bad = _xlsx_bytes(WO_HEADER, [_wo_row(seq=1, ar_fee="12.3元")])
    with SessionLocal() as db, pytest.raises(ValueError) as exc:
        importer.import_source(db, bid, "writeoff", ("坏样本.xlsx", bad))
    with SessionLocal() as db:
        src = db.query(ReconSource).filter_by(batch_id=bid).one()
        assert src.parse_status == "failed"
        assert "12.3元" in (src.error or "")
        assert src.row_count == 0
        batch = db.get(ReconBatch, bid)
        assert batch.status == "draft"  # 批次回收为 draft，不悬空
        assert "坏样本.xlsx" in (batch.error_summary or "")  # 可诊断摘要
        assert db.query(ReconRecord).filter_by(batch_id=bid).count() == 0
    # 删除失败源后（draft 且无 failed 源）摘要清空
    with SessionLocal() as db:
        service.delete_source(db, bid, src.id)
    with SessionLocal() as db:
        assert db.get(ReconBatch, bid).error_summary == ""
        assert db.query(ReconSource).filter_by(batch_id=bid).count() == 0


def test_details_guard_no_amounts_no_full_account(upload_root):
    from app.database import SessionLocal
    from app.modules.finance_recon.models import ReconRecord

    bid = _make_batch()
    _import_all(bid)
    with SessionLocal() as db:
        recs = db.query(ReconRecord).filter_by(batch_id=bid).all()
    assert len(recs) == 7  # 核销2 + 认领2 + 欠费2 + 账单1
    writeoff_claims = {"seq_no", "region", "project", "contract_type", "bill_month", "brand",
                       "unit_no", "writeoff_type", "writeoff_date", "cancel_date", "tax_rate", "prepaid_type"}
    claim_keys = {"doc_type", "sub_doc_no", "transfer_id", "biz_type", "payer_name", "payer_account_tail",
                  "payee_name", "payee_account_tail", "txn_type", "purpose", "txn_time", "memo",
                  "status", "claim_time", "claimant", "audit_time", "auditor", "source"}
    for record in recs:
        blob = json.dumps(record.details, ensure_ascii=False)
        assert "amount" not in blob  # 详情 JSON 不放金额
        if record.kind == "writeoff":
            assert set(record.details) <= writeoff_claims
        if record.kind == "claim":
            assert set(record.details) <= claim_keys
            assert "6222000011112222" not in blob  # 完整账号不入 JSON
            assert record.details["payee_account_tail"] == "3456"


# ---------- check 前置校验 ----------


def test_precheck_requires_scope_and_four_kinds(upload_root):
    from app.database import SessionLocal
    from app.modules.finance_recon import service

    empty_scope = _make_batch(scope=())
    with SessionLocal() as db, pytest.raises(ValueError) as exc:
        service.precheck_for_check(db, empty_scope)
    assert "合同范围" in str(exc.value)

    bid = _make_batch()
    with SessionLocal() as db, pytest.raises(ValueError) as exc:
        service.precheck_for_check(db, bid)
    msg = str(exc.value)
    for label in ("核销", "认领", "欠费", "账单"):
        assert label in msg


def test_precheck_requires_all_sources_parsed(upload_root):
    from app.database import SessionLocal
    from app.modules.finance_recon import service
    from app.modules.finance_recon.models import ReconSource

    bid = _make_batch()
    _import_all(bid)
    with SessionLocal() as db:
        src = db.query(ReconSource).filter_by(batch_id=bid, kind="bill").one()
        src.parse_status = "failed"
        src.error = "合成注入的解析失败"
        db.commit()
        bad_id = src.id
    with SessionLocal() as db, pytest.raises(ValueError) as exc:
        service.precheck_for_check(db, bid)
    msg = str(exc.value)
    assert "解析失败" in msg and "合成注入的解析失败" in msg
    with SessionLocal() as db:
        src = db.get(ReconSource, bad_id)
        src.parse_status = "parsed"
        src.error = ""
        db.commit()
        info = service.precheck_for_check(db, bid)
    assert info["sources_by_kind"] == {"writeoff": 1, "claim": 1, "arrear": 1, "bill": 1}
    assert info["record_count"] == 7
    assert info["contract_scope"] == ["HT-SYN-001", "HT-SYN-002"]


# ---------- 删除源文件 ----------


def test_delete_source_cascades_records_matches_and_file(upload_root):
    from app.database import SessionLocal
    from app.modules.finance_recon import service
    from app.modules.finance_recon.models import ReconMatch, ReconRecord, ReconSource

    bid = _make_batch()
    ids = _import_all(bid)
    stored_dir = upload_root / "recon" / str(bid)
    assert len(list(stored_dir.iterdir())) == 4
    with SessionLocal() as db:
        claim = db.query(ReconRecord).filter_by(batch_id=bid, kind="claim").first()
        writeoff = db.query(ReconRecord).filter_by(batch_id=bid, kind="writeoff").first()
        db.add(
            ReconMatch(
                batch_id=bid,
                claim_record_id=claim.id,
                writeoff_record_id=writeoff.id,
                allocated_amount=Decimal("1.00"),
                status="suggested",
                origin="manual",
            )
        )
        db.commit()
        out = service.delete_source(db, bid, ids["writeoff"])
    assert out["ok"] is True
    with SessionLocal() as db:
        assert db.query(ReconSource).filter_by(id=ids["writeoff"]).count() == 0
        assert db.query(ReconRecord).filter_by(source_id=ids["writeoff"]).count() == 0
        assert db.query(ReconRecord).filter_by(batch_id=bid).count() == 5
        assert db.query(ReconMatch).filter_by(batch_id=bid).count() == 0  # suggested 级联删除
    assert len(list(stored_dir.iterdir())) == 3  # 文件同步删除


def test_delete_source_blocked_by_human_match(upload_root):
    from app.database import SessionLocal
    from app.modules.finance_recon import service
    from app.modules.finance_recon.models import ReconMatch, ReconRecord, ReconSource

    bid = _make_batch()
    ids = _import_all(bid)
    stored_dir = upload_root / "recon" / str(bid)
    before_files = len(list(stored_dir.iterdir()))
    with SessionLocal() as db:
        claim = db.query(ReconRecord).filter_by(batch_id=bid, kind="claim").first()
        writeoff = db.query(ReconRecord).filter_by(batch_id=bid, kind="writeoff").first()
        db.add(
            ReconMatch(
                batch_id=bid,
                claim_record_id=claim.id,
                writeoff_record_id=writeoff.id,
                allocated_amount=Decimal("1.00"),
                status="confirmed",
                origin="manual",
            )
        )
        db.commit()
    with SessionLocal() as db, pytest.raises(service.ReconConflictError) as exc:
        service.delete_source(db, bid, ids["writeoff"])
    msg = str(exc.value)
    assert "人工" in msg or "确认" in msg
    with SessionLocal() as db:
        assert db.query(ReconSource).filter_by(id=ids["writeoff"]).count() == 1  # 未删除
        assert db.query(ReconRecord).filter_by(batch_id=bid).count() == 7
    assert len(list(stored_dir.iterdir())) == before_files  # 文件保留


def test_delete_source_guards(upload_root):
    from app.database import SessionLocal
    from app.modules.finance_recon import service

    bid = _make_batch()
    ids = _import_all(bid)
    with SessionLocal() as db:
        service.transition_batch(db, bid, "checking")
        service.transition_batch(db, bid, "ready")
    with SessionLocal() as db, pytest.raises(service.ReconConflictError) as exc:
        service.delete_source(db, bid, ids["arrear"])
    assert "不可替换" in str(exc.value)  # P2-1：ever_ready 守卫优先
    with SessionLocal() as db, pytest.raises(service.ReconNotFoundError):
        service.delete_source(db, bid, 999999)
    # 其他批次的源文件不可跨批删除
    other = _make_batch()
    with SessionLocal() as db, pytest.raises(service.ReconNotFoundError):
        service.delete_source(db, other, ids["arrear"])


def test_delete_source_missing_file_still_ok(upload_root):
    from app.database import SessionLocal
    from app.modules.finance_recon import service
    from app.modules.finance_recon.models import ReconSource

    bid = _make_batch()
    ids = _import_all(bid)
    with SessionLocal() as db:
        src = db.get(ReconSource, ids["bill"])
        Path(src.stored_path).unlink()  # 模拟文件已被清理
    with SessionLocal() as db:
        out = service.delete_source(db, bid, ids["bill"])
    assert out["ok"] is True
    with SessionLocal() as db:
        assert db.query(ReconSource).filter_by(id=ids["bill"]).count() == 0


# ---------- 修复轮 1（review Important/Minor 回归） ----------


def test_import_source_race_duplicate_sha_maps_conflict_and_cleans_new_file(upload_root, tmp_path, monkeypatch):
    """并发同批同 sha：去重检查后、落库 flush 前对手请求抢先提交同内容源记录。

    对手行经独立连接真实提交（非 mock 成功）；本侧 flush 撞真实唯一约束 →
    中文 409（ReconConflictError），且只清理本次新落盘文件，不触碰对手
    canonical 文件。
    """
    import hashlib

    from app.database import SessionLocal
    from app.modules.finance_recon import importer, service
    from app.modules.finance_recon.models import ReconRecord, ReconSource

    bid = _make_batch()
    data = _xlsx_bytes(WO_HEADER, [_wo_row(seq=1)])
    sha = hashlib.sha256(data).hexdigest()
    rival_path = tmp_path / "rival-canonical.xlsx"
    rival_path.write_bytes(data)
    real_parse = importer.parse_source

    def racing_parse(path, kind):
        # 去重检查已过、flush 未到：并发请求抢先导入同批同 sha（独立连接提交）
        with SessionLocal() as other:
            other.add(
                ReconSource(
                    batch_id=bid,
                    kind=kind,
                    original_name="对手样本.xlsx",
                    stored_path=str(rival_path),
                    sha256=sha,
                    parse_status="parsed",
                    row_count=1,
                )
            )
            other.commit()
        return real_parse(path, kind)

    monkeypatch.setattr(importer, "parse_source", racing_parse)
    with SessionLocal() as db, pytest.raises(service.ReconConflictError) as exc:
        importer.import_source(db, bid, "writeoff", ("并发样本.xlsx", data))
    assert "唯一约束冲突" in str(exc.value)

    with SessionLocal() as db:
        assert db.query(ReconSource).filter_by(batch_id=bid).count() == 1  # 仅对手记录
        assert db.query(ReconRecord).filter_by(batch_id=bid).count() == 0  # 本次事实行未入库
        rival = db.query(ReconSource).filter_by(original_name="对手样本.xlsx").one()
        assert rival.sha256 == sha and rival.parse_status == "parsed"
    stored_dir = upload_root / "recon" / str(bid)
    assert list(stored_dir.iterdir()) == []  # 本次新文件已清理，不留孤儿
    assert rival_path.exists()  # 对手 canonical 文件未被触碰


def test_fee_period_accepts_zhi_and_rejects_multi_segment(tmp_path):
    # 中文「至」分隔（与报错文案一致）
    recs = _parse(
        _xlsx(tmp_path / "至分隔.xlsx", WO_HEADER, [_wo_row(seq=1, period="2026.10.01至2026.10.31")]),
        "writeoff",
    )
    assert recs[0].fee_period_start == date(2026, 10, 1)
    assert recs[0].fee_period_end == date(2026, 10, 31)
    # 非法多段：显式报「多段分隔」，不静默截断
    with pytest.raises(ValueError) as exc:
        _parse(
            _xlsx(
                tmp_path / "多段.xlsx",
                WO_HEADER,
                [_wo_row(seq=1, period="2026.10.01-2026.10.15-2026.10.31")],
            ),
            "writeoff",
        )
    assert "多段分隔" in str(exc.value)


def test_bill_pdf_rejects_bracketed_and_punctuation_values(tmp_path):
    # 括号说明不是标识：视同「账单编号」后无值，显式失败
    bracketed = ["账单编号：（详见附表）", "合同编号：HT-SYN-001", "累计应付", "1.00"]
    with pytest.raises(ValueError) as exc:
        _parse(_pdf(tmp_path / "括号说明.pdf", bracketed), "bill")
    assert "账单编号" in str(exc.value) and "详见附表" not in str(exc.value)
    # 纯标点/符号串不是标识
    dashed = ["账单编号：——", "合同编号：HT-SYN-001", "累计应付", "1.00"]
    with pytest.raises(ValueError) as exc:
        _parse(_pdf(tmp_path / "纯标点.pdf", dashed), "bill")
    assert "账单编号" in str(exc.value)
    # 合法值后随括号说明：只取括号前的标识部分
    suffixed = ["账单编号：B202611-001（含税）", "合同编号：HT-SYN-001", "累计应付", "1.00"]
    recs = _parse(_pdf(tmp_path / "括号后缀.pdf", suffixed), "bill")
    assert recs[0].doc_no == "B202611-001"


def test_import_source_duplicate_failed_source_message(upload_root):
    """重传与 failed 源同内容的文件：skipped_duplicates 消息明确指引先删除。"""
    from app.database import SessionLocal
    from app.modules.finance_recon import importer

    bid = _make_batch()
    bad = _xlsx_bytes(WO_HEADER, [_wo_row(seq=1, ar_fee="12.3元")])
    with SessionLocal() as db, pytest.raises(ValueError):
        importer.import_source(db, bid, "writeoff", ("坏样本.xlsx", bad))
    with SessionLocal() as db:
        dup = importer.import_source(db, bid, "writeoff", ("再传一次.xlsx", bad))
    assert dup.skipped_duplicates == 1
    assert "解析失败" in dup.message
    assert "请先删除再重传" in dup.message


# ---------- 修复轮 2（同行累计应付金额不再按标识截断） ----------


def test_bill_pdf_same_line_amount_with_thousands_separators(tmp_path):
    """同行「累计应付：12,345.67」→ 12345.67（首轮缺陷：在首个逗号截断成 12）。"""
    half = ["账单编号：ZD-SYN-9", "合同编号：HT-SYN-001", "累计应付：12,345.67"]
    recs = _parse(_pdf(tmp_path / "同行千分位.pdf", half), "bill")
    assert recs[0].amount_1 == Decimal("12345.67")
    # 全角逗号与统一金额口径一致（剥千分位后 Decimal 严格解析）
    full = ["账单编号：ZD-SYN-9", "合同编号：HT-SYN-001", "累计应付：12，345.67"]
    recs_full = _parse(_pdf(tmp_path / "同行全角逗号.pdf", full), "bill")
    assert recs_full[0].amount_1 == Decimal("12345.67")
    # 空格分隔同行取值不回归
    spaced = ["账单编号：ZD-SYN-9", "合同编号：HT-SYN-001", "累计应付 88.10"]
    assert _parse(_pdf(tmp_path / "同行空格.pdf", spaced), "bill")[0].amount_1 == Decimal("88.10")


def test_bill_pdf_same_line_amount_illegal_suffix_fails_explicitly(tmp_path):
    """同行金额带「元」后缀：显式失败（即使下一行是合法金额也不回退、不错值）。"""
    lines = ["账单编号：ZD-SYN-9", "合同编号：HT-SYN-001", "累计应付：1,234.56元", "1,234.56"]
    with pytest.raises(ValueError) as exc:
        _parse(_pdf(tmp_path / "同行元后缀.pdf", lines), "bill")
    msg = str(exc.value)
    assert "同行金额" in msg and "1,234.56元" in msg


def test_bill_pdf_same_line_note_falls_to_next_line(tmp_path):
    """同行纯说明（无数字）→ 保留锚点后下一非空行金额路径；多锚点拒绝不回归。"""
    lines = ["账单编号：ZD-SYN-9", "合同编号：HT-SYN-001", "累计应付（详见附表）", "2,468.00"]
    recs = _parse(_pdf(tmp_path / "同行说明.pdf", lines), "bill")
    assert recs[0].amount_1 == Decimal("2468.00")
    # 下一行金额路径不回归（锚点独占一行 + 千分位）
    classic = list(BILL_LINES)
    recs2 = _parse(_pdf(tmp_path / "下一行.pdf", classic), "bill")
    assert recs2[0].amount_1 == Decimal("1234.56")
    with pytest.raises(ValueError) as exc:
        _parse(_pdf(tmp_path / "多锚点回归.pdf", BILL_LINES + ["累计应付", "2.00"]), "bill")
    assert "多" in str(exc.value)


def test_import_source_rejects_ever_ready_batch_direct_call(upload_root):
    """P2-1 编程入口：import_source 在服务层拒绝曾就绪批次（failed/ready 均拒），
    不落文件、不重复记录；draft/failed 且从未 ready 仍可写。"""
    from app.database import SessionLocal
    from app.modules.finance_recon import importer, service
    from app.modules.finance_recon.models import ReconBatch, ReconSource

    bid = _make_batch()
    ids = _import_all(bid)
    with SessionLocal() as db:
        service.transition_batch(db, bid, "checking")
        service.transition_batch(db, bid, "ready")
    with SessionLocal() as db:
        batch = db.get(ReconBatch, bid)
        batch.status = "failed"
        db.commit()

    data = _xlsx_bytes(WO_HEADER, [_wo_row(seq=9)])
    batch_dir = upload_root / "recon" / str(bid)
    before_files = sorted(p.name for p in batch_dir.iterdir())
    with SessionLocal() as db, pytest.raises(service.ReconConflictError) as exc:
        importer.import_source(db, bid, "writeoff", ("直调再传.xlsx", data))
    assert "不可替换" in str(exc.value) and "审计留痕" in str(exc.value)
    assert sorted(p.name for p in batch_dir.iterdir()) == before_files  # 不落文件
    with SessionLocal() as db:
        assert db.query(ReconSource).filter(ReconSource.batch_id == bid).count() == len(ids)

    # ready 状态同样拒绝（守卫与当前状态无关）
    with SessionLocal() as db:
        batch = db.get(ReconBatch, bid)
        batch.status = "ready"
        db.commit()
    with SessionLocal() as db, pytest.raises(service.ReconConflictError):
        importer.import_source(db, bid, "writeoff", ("直调再传2.xlsx", data))

    # 对照：从未 ready 的 failed 批次在服务层仍可写
    other = _make_batch(status="failed")
    with SessionLocal() as db:
        result = importer.import_source(db, other, "writeoff", ("直调可写.xlsx", data))
    assert result.row_count == 1 and result.skipped_duplicates == 0
