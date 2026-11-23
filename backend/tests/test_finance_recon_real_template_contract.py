# -*- coding: utf-8 -*-
"""finance_recon 真实模板表头契约测试（合成数据；真实样本 E2E 缺陷回归锁定）。

真实样本 E2E 暴露、经审查确认的表头变体（仅表头文本被确认；单元格数据一律
程序合成，不打印、不保存真实单元格内容，不依赖文件名/固定行号/合同号前缀）：
- writeoff：重复「应收金额/核销金额」（列组语义：第 1 组=账单级、第 2 组=费用期级）
  +「核销后欠费金额」；
- claim：「本次认领金额」；
- arrear：「欠费合计」与费用列「合计」并存（优先精确「合计」费用列）、「固定点位-固定租金」
  「固定点位-综合管理费」；只有「欠费合计」的变体仍兼容；精确同名重复列仍拒绝。

契约以解析器公共行为为准：无关合同全部导入，金额 Decimal 两位，非法结构显式失败。
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

# ---------- 已确认的真实表头变体（文本精确契约，数据合成） ----------

REAL_WO_HEADER = [
    "序号", "区域", "项目", "合同号", "合同类型", "账单月", "品牌", "铺位",
    "应收金额", "核销金额", "单据号", "核销日期", "取消日期", "核销类型",
    "费用类型", "费用期", "应收金额", "核销金额", "核销后欠费金额",
    "税率", "预收类别", "预收款金额",
]

REAL_RC_HEADER = [
    "来源", "单据类型", "父单据", "子单据", "传输id", "业务类型", "付款户名", "付款账号",
    "收款户名", "收款账号", "收款金额", "本次认领金额", "手续费", "交易类型", "用途",
    "交易时间", "摘要", "状态", "认领时间", "认领人", "审核时间", "审核人",
]

# 「欠费合计」与费用列「合计」并存的真实版式（费用三列：合计/固定点位-固定租金/固定点位-综合管理费）
REAL_QF_HEADER = [
    "序号", "合同", "账单月", "缴费截止", "费用开始", "费用结束",
    "合计", "固定点位-固定租金", "固定点位-综合管理费", "欠费合计",
]
QF_HEJI_ONLY_HEADER = [
    "序号", "合同", "账单月", "缴费截止", "费用开始", "费用结束", "欠费合计",
]


def _real_wo_row(*, seq=1, contract="HT-SYN-001", doc="DZ-SYN-0001"):
    # 列组：第 1 组应收/核销金额（第 9/10 列）=账单级；第 2 组（第 17/18 列）=费用期级
    return [
        seq, "华东", "项目甲", contract, "固定租金", "2026-10", "品牌甲", "铺位甲",
        "120.00", "118.00", doc, date(2026, 11, 5), None, "正常核销",
        "固定租金", "2026.10.01-2026.10.31", "100.00", "100.00", "0.00",
        "0.05", None, None,
    ]


def _real_rc_row(*, doc="SK-SYN-0001", recv="500.00", claim="500.00"):
    return [
        "线下", "收款", doc, "", "T-SYN-1", "收款", "付款方甲", "6222000011112222",
        "收款方乙", "110002003456", recv, claim, None, "转账", "货款",
        "2026-11-06 10:00:00", "摘要甲", "已审核", "2026-11-06 11:00:00", "认领人甲",
        "2026-11-06 12:00:00", "审核人甲",
    ]


def _real_qf_row(*, seq=1, contract="HT-SYN-001", heji="300.00", rent="200.00", mgmt="100.00", qf_heji=""):
    # 列序对应 REAL_QF_HEADER：合计 / 固定点位-固定租金 / 固定点位-综合管理费 / 欠费合计
    return [seq, contract, "2026-10", "2026-11-15", "2026-10-01", "2026-10-31", heji, rent, mgmt, qf_heji]


def _parse(path, kind):
    from app.modules.finance_recon.importer import parse_source

    return parse_source(path, kind)


def _xlsx(path, header, rows, *, sheet="数据"):
    import io

    import openpyxl

    bio = io.BytesIO()
    wb = openpyxl.Workbook()  # openpyxl.Workbook 不支持 with（项目坑位）
    ws = wb.active
    ws.title = sheet
    ws.append(header)
    for row in rows:
        ws.append(list(row))
    wb.save(bio)
    wb.close()
    path.write_bytes(bio.getvalue())
    return path


def test_real_writeoff_headers_duplicate_amount_columns(tmp_path):
    """重复「应收金额/核销金额」按列组语义区分 +「核销后欠费金额」映射事实金额栏。"""
    rows = [
        _real_wo_row(seq=1),
        _real_wo_row(seq=2, contract="HT-SYN-002", doc="DZ-SYN-0002"),
    ]
    recs = _parse(_xlsx(tmp_path / "核销真实表头.xlsx", REAL_WO_HEADER, rows), "writeoff")
    assert len(recs) == 2
    r1 = recs[0]
    assert r1.row_no == 2
    assert (r1.contract_no, r1.doc_no, r1.fee_type) == ("HT-SYN-001", "DZ-SYN-0001", "固定租金")
    # 第 2 组「应收金额/核销金额」= 费用期级（事实金额栏）
    assert r1.amount_1 == Decimal("100.00")  # 费用期应收金额
    assert r1.amount_2 == Decimal("100.00")  # 费用期核销金额
    assert r1.amount_3 == Decimal("0.00")  # 核销后欠费金额
    assert r1.amount_4 is None
    # 第 1 组 = 账单级（单据级，仅随解析结果携带，不入事实表）
    assert r1.doc_amount_1 == Decimal("120.00")
    assert r1.doc_amount_2 == Decimal("118.00")
    assert recs[1].contract_no == "HT-SYN-002"


def test_real_claim_header_this_claim_amount(tmp_path):
    """「本次认领金额」映射 amount_2；其余认领字段不回归。"""
    recs = _parse(
        _xlsx(tmp_path / "认领真实表头.xlsx", REAL_RC_HEADER, [_real_rc_row()]),
        "claim",
    )
    r = recs[0]
    assert r.doc_no == "SK-SYN-0001"
    assert r.amount_1 == Decimal("500.00")  # 收款金额
    assert r.amount_2 == Decimal("500.00")  # 本次认领金额
    assert r.amount_3 is None
    assert r.occur_date == date(2026, 11, 6)
    assert r.details["payer_account_tail"] == "2222"


def test_real_arrear_prefers_exact_heji_over_qianfei_heji(tmp_path):
    """「欠费合计」与费用列「合计」并存：amount_1 取精确「合计」，租金/综管费取点位列。"""
    recs = _parse(
        _xlsx(
            tmp_path / "欠费真实表头.xlsx",
            REAL_QF_HEADER,
            [_real_qf_row(heji="300.00", rent="200.00", mgmt="100.00", qf_heji="888.88")],
        ),
        "arrear",
    )
    r = recs[0]
    assert r.contract_no == "HT-SYN-001"
    assert r.amount_1 == Decimal("300.00")  # 精确「合计」费用列（不是欠费合计 888.88）
    assert r.amount_2 == Decimal("200.00")  # 固定点位-固定租金
    assert r.amount_3 == Decimal("100.00")  # 固定点位-综合管理费
    assert r.due_date == date(2026, 11, 15)
    assert r.details["bill_month"] == "2026-10"


def test_real_arrear_qianfei_heji_only_variant(tmp_path):
    """只有「欠费合计」的变体仍兼容：amount_1 取欠费合计。"""
    row = ["1", "HT-SYN-001", "2026-10", "2026-11-15", "2026-10-01", "2026-10-31", "55.55"]
    recs = _parse(
        _xlsx(tmp_path / "欠费仅欠费合计.xlsx", QF_HEJI_ONLY_HEADER, [row]),
        "arrear",
    )
    r = recs[0]
    assert r.amount_1 == Decimal("55.55")
    assert r.amount_2 is None and r.amount_3 is None  # 无点位列 → 未填，不归零


def test_real_arrear_duplicate_exact_heji_rejected(tmp_path):
    """两列精确同名「合计」→ 结构歧义显式拒绝（不猜列）。"""
    header = ["序号", "合同", "账单月", "合计", "合计", "欠费合计"]
    row = [1, "HT-SYN-001", "2026-10", "300.00", "999.00", "888.88"]
    with pytest.raises(ValueError) as exc:
        _parse(_xlsx(tmp_path / "重复合计.xlsx", header, [row]), "arrear")
    assert "合计" in str(exc.value)


def test_real_writeoff_mixed_bare_amount_groups_rejected(tmp_path):
    """「应收金额」出现 3 列（超过账单级+费用期级两组）→ 列组歧义显式拒绝。"""
    header = [
        "序号", "合同号", "费用类型", "费用期", "应收金额", "核销金额", "单据号",
        "核销类型", "核销后欠费", "税率", "预收类别", "预收款金额",
        "应收金额", "核销金额", "应收金额", "核销金额",
    ]
    row = [
        1, "HT-SYN-001", "固定租金", "2026.10.01-2026.10.31", "120.00", "118.00",
        "DZ-SYN-0001", "正常核销", "0.00", "0.05", None, "10.00",
        "100.00", "100.00", "100.00", "100.00",
    ]
    with pytest.raises(ValueError) as exc:
        _parse(_xlsx(tmp_path / "超量列组.xlsx", header, [row]), "writeoff")
    assert "应收" in str(exc.value)


def test_real_writeoff_mixed_bare_alias_two_columns_resolved(tmp_path):
    """「应收」与「应收金额」各一列（共两组）→ 列组语义仍可确定（第 1 组=账单级）。"""
    header = [
        "序号", "合同号", "费用类型", "费用期", "应收", "核销", "单据号", "核销类型",
        "核销后欠费金额", "税率", "预收类别", "预收款金额", "应收金额", "核销金额",
    ]
    row = [
        1, "HT-SYN-001", "固定租金", "2026.10.01-2026.10.31", "120.00", "118.00",
        "DZ-SYN-0001", "正常核销", "0.00", "0.05", None, "10.00", "100.00", "100.00",
    ]
    recs = _parse(_xlsx(tmp_path / "混用两组.xlsx", header, [row]), "writeoff")
    r = recs[0]
    assert r.doc_amount_1 == Decimal("120.00")  # 第 1 组（应收）
    assert r.doc_amount_2 == Decimal("118.00")  # 第 1 组（核销）
    assert r.amount_1 == Decimal("100.00")  # 第 2 组（应收金额）= 费用期应收
    assert r.amount_2 == Decimal("100.00")  # 第 2 组（核销金额）= 费用期核销
    assert r.amount_3 == Decimal("0.00")  # 核销后欠费金额


# ---------- 修复轮 4（真实样本根因：ISO 日期连字符误判 + arrear 总计/说明行误报） ----------


def _wo_with_period(period):
    row = list(_real_wo_row())
    row[15] = period  # 费用期列
    return row


def test_real_fee_period_iso_wavy_zhi_and_hyphen_ranges(tmp_path):
    """ISO 日期内部连字符不算区间分隔；～/~/至/单连字符区间均可解析。"""
    cases = {
        "2025-09-01～2025-09-30": (date(2025, 9, 1), date(2025, 9, 30)),  # 全角波浪号
        "2025-09-01~2025-09-30": (date(2025, 9, 1), date(2025, 9, 30)),  # 半角波浪号
        "2025-09-01至2025-09-30": (date(2025, 9, 1), date(2025, 9, 30)),  # 至
        "2025-09-01-2025-09-30": (date(2025, 9, 1), date(2025, 9, 30)),  # 单连字符区间
        "2025.09.01-2025.09.30": (date(2025, 9, 1), date(2025, 9, 30)),  # 点日期 + 连字符区间
        "2025年9月1日~2025年9月30日": (date(2025, 9, 1), date(2025, 9, 30)),  # 年月日 + 波浪号
    }
    for period, (start, end) in cases.items():
        recs = _parse(
            _xlsx(tmp_path / f"期间-{abs(hash(period))}.xlsx", REAL_WO_HEADER, [_wo_with_period(period)]),
            "writeoff",
        )
        assert recs[0].fee_period_start == start, period
        assert recs[0].fee_period_end == end, period


def test_real_fee_period_multi_segment_and_single_value_rejected(tmp_path):
    """非法多段与单值费用期仍显式拒绝（不静默取第一段）。"""
    for period in (
        "2025-09-01～2025-09-30～2025-10-31",  # 两个显式分隔符
        "2025-09-01-2025-09-30-2025-10-31",  # 三段连字符
        "2025-09-01",  # 单值无起止
        "2025-09-01~",  # 缺止
    ):
        with pytest.raises(ValueError) as exc:
            _parse(
                _xlsx(tmp_path / f"坏期间-{abs(hash(period))}.xlsx", REAL_WO_HEADER, [_wo_with_period(period)]),
                "writeoff",
            )
        assert "多段" in str(exc.value) or "无法解析" in str(exc.value), period


def _real_qf_row_empty_identity(heji="1500.00", qf_heji="5000.00"):
    # 总计行：合同/账单月/缴费截止/费用起止全空，仅合计/欠费合计有汇总值（首列不是「合计」）
    return [None, None, None, None, None, None, heji, None, None, qf_heji]


def test_real_arrear_total_row_without_identity_skipped(tmp_path):
    """无合同且无费用期的聚合行（总计行）显式跳过，不误报缺合同；明细行不受影响。"""
    rows = [
        _real_qf_row(seq=1),
        _real_qf_row_empty_identity(),
        _real_qf_row(seq=2, contract="HT-SYN-002"),
    ]
    recs = _parse(
        _xlsx(tmp_path / "欠费含总计行.xlsx", REAL_QF_HEADER, rows),
        "arrear",
    )
    assert len(recs) == 2
    assert [r.contract_no for r in recs] == ["HT-SYN-001", "HT-SYN-002"]
    assert recs[0].row_no == 2 and recs[1].row_no == 4  # 总计行（第 3 行）未入库


def test_real_arrear_footer_note_rows_skipped(tmp_path):
    """底部制表/查询说明非数据行跳过（不依赖固定行号；覆盖说明文本落在合同列与序号列两种形态）。"""
    note_in_contract = ["制表人：合成测试员  查询日期：2025-10-20", None, None, None, None, None, None, None, None, None]
    note_in_seq = [None, "查询日期：2025-10-20", None, None, None, None, None, None, None, None]
    rows = [
        _real_qf_row(seq=1),
        note_in_contract,
        note_in_seq,
        _real_qf_row(seq=2, contract="HT-SYN-002"),
    ]
    recs = _parse(
        _xlsx(tmp_path / "欠费含底部说明.xlsx", REAL_QF_HEADER, rows),
        "arrear",
    )
    assert len(recs) == 2
    assert [r.contract_no for r in recs] == ["HT-SYN-001", "HT-SYN-002"]


def test_real_arrear_missing_contract_with_period_still_errors(tmp_path):
    """有费用期但缺合同 = 明细行缺身份 → 必须报错，不能当聚合行吞掉。"""
    row = [None, None, "2026-10", "2026-11-15", "2025-09-01", "2025-09-30", "300.00", "200.00", "100.00", None]
    with pytest.raises(ValueError) as exc:
        _parse(
            _xlsx(tmp_path / "缺合同有费用期.xlsx", REAL_QF_HEADER, [row]),
            "arrear",
        )
    assert "合同" in str(exc.value)


# ---------- 修复轮 5（诊断确认：费用期「-」为无费用期占位符，非分隔变体） ----------


def test_real_fee_period_dash_placeholder_means_absent(tmp_path):
    """真实模板以「-」（含空格包围）标注无费用期（如保证金行）：
    纯连字符/波浪占位与空白同口径 → 起止为 None，不报错、不归零。"""
    for period in ("-", " - ", "—", "–", "－", "﹣", "～", "~"):
        recs = _parse(
            _xlsx(tmp_path / f"占位-{abs(hash(period))}.xlsx", REAL_WO_HEADER, [_wo_with_period(period)]),
            "writeoff",
        )
        assert recs[0].fee_period_start is None, period
        assert recs[0].fee_period_end is None, period
        assert recs[0].contract_no == "HT-SYN-001"  # 行本身仍是有效事实行


def test_real_fee_period_non_placeholder_text_still_rejected(tmp_path):
    """占位符之外的费用期文本不豁免：混入日期成分仍按严格口径报错。"""
    for period in ("-2025.09.01", "2025.09.01-", "无", "-至-"):
        with pytest.raises(ValueError):
            _parse(
                _xlsx(tmp_path / f"非占位-{abs(hash(period))}.xlsx", REAL_WO_HEADER, [_wo_with_period(period)]),
                "writeoff",
            )
