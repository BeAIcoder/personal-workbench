# -*- coding: utf-8 -*-
"""经营管理报表解析器回归：锚点容忍、缺段只告警、异常数值不补 0。

合成工作簿在测试内生成（不依赖真实业务数据）；模板解析另由
test_data_templates.py 覆盖，这里只验证解析口径本身。
"""
from __future__ import annotations

import io
from pathlib import Path

import openpyxl
import pytest
from openpyxl import Workbook

from app.modules.business_manage import importer

REPO = Path(__file__).resolve().parents[2]
TPL = REPO / "数据模板"


def _bytes(wb: Workbook) -> bytes:
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def _weekly_workbook(shift: int = 0, with_collection: bool = True) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "周报"
    rows = [
        ["项目：项目A", "", "", "", "统计时间：2026年10月23日-10月29日"],
        [],
        ["", "可出租面积", "出租率（%）", "环比增幅", "开业率（%）"],
        ["实际（期末值）", 1000, 0.9, 0.01, 0.85],
        [],
        ["总租赁面积㎡", "总铺位数（家）", "空铺数（家）", "总空铺面积㎡", "空铺率%"],
        [1000, 10, 2, 200, 0.2],
    ]
    if with_collection:
        rows += [
            [],
            ["1-10月累计应收款（万元）", "累计已收款（万元）", "累计未收款（万元）", "本周收回（万元）", "累计收缴率"],
            [100, 80, 20, 5, 0.8],
        ]
    for offset, row in enumerate(rows):
        for index, value in enumerate(row, start=1):
            ws.cell(row=offset + 1 + shift, column=index, value=value)
    return _bytes(wb)


def _leasing_workbook() -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "表4、招商完成情况"
    ws["A1"] = "2026年10月第3周 招商完成情况"
    ws["C2"] = "年度"
    ws["H2"] = "月度"
    ws["C3"] = "区域合计"
    ws["D3"] = "项目C"
    ws["H3"] = "区域合计"
    ws["I3"] = "项目C"
    ws["B4"] = "1.1计划总体指标"
    ws["C4"] = 100
    ws["D4"] = 50
    ws["H4"] = 10
    ws["I4"] = 5
    ws["B5"] = "2.2总体指标完成率"
    ws["C5"] = "#DIV/0!"
    ws["D5"] = 0.5
    ws["H5"] = 0.25
    return _bytes(wb)


def _parse(kind: str, name: str, content: bytes) -> dict:
    return importer.parse(kind, name, content)


def test_weekly_parses_core_metrics():
    parsed = _parse("weekly", "云南项目周报.xlsx", _weekly_workbook())
    values = {item["metric_key"]: item["value_num"] for item in parsed["metrics"]}
    assert values["weekly.lease.rentable_area"] == 1000
    assert values["weekly.lease.occupancy_rate"] == 0.9
    assert values["weekly.vacancy.vacant_count"] == 2
    assert values["weekly.collection.received"] == 80
    assert values["weekly.collection.rate"] == 0.8
    assert parsed["period"] == "2026-10"
    assert parsed["period_start"].isoformat() == "2026-10-23"
    assert parsed["period_end"].isoformat() == "2026-10-29"


def test_anchor_shift_tolerated():
    shifted = _parse("weekly", "云南项目周报.xlsx", _weekly_workbook(shift=3))
    values = {item["metric_key"]: item["value_num"] for item in shifted["metrics"]}
    assert values["weekly.lease.occupancy_rate"] == 0.9
    assert values["weekly.collection.rate"] == 0.8


def test_missing_sections_warn_not_fail():
    parsed = _parse("weekly", "曲靖周报.xlsx", _weekly_workbook(with_collection=False))
    assert any("预警" in warning for warning in parsed["warnings"])
    assert any("收缴" in warning for warning in parsed["warnings"])
    assert parsed["metrics"]


def test_div_error_and_empty_cells_are_null():
    parsed = _parse("control", "内控.xlsx", _leasing_workbook())
    values = {item["metric_key"]: item["value_num"] for item in parsed["metrics"]}
    assert values["leasing.year.区域合计.plan"] == 100
    assert values["leasing.year.项目C.done_rate"] == 0.5
    assert "leasing.year.区域合计.done_rate" not in values
    assert all(item["value_num"] is not None for item in parsed["metrics"])


def test_monthly_extracts_numbers_from_text():
    wb = Workbook()
    ws = wb.active
    ws["A1"] = "云南项目月度工作情况表（2026年10月）"
    ws["A7"] = "一、问题线索处置及案件办理情况：\n（一）在诉案件\n1.某案件审理中。"
    ws["A8"] = (
        "二、开展重点工作推进情况：\n（一）项目A\n"
        "1.收缴情况：合同口径累计应收1076.37万元，实收790.80万元，收缴率73.5%。\n"
        "（四）项目D\n2.项目目前整体出租率：76 %，总体在营商户 69户，经营面积约 16595.25平方米。\n"
        "（五）招商重点工作\n3.10月新签品牌 1家，面积合计 134.55㎡；续签品牌 3家，面积合计 244.65㎡。"
    )
    ws["A9"] = "三、自身建设和队伍建设情况\n1.自身建设情况：略。"
    ws["A10"] = "四、亮点工作\n企划工作：略。"
    parsed = _parse("monthly", "月度.xlsx", _bytes(wb))
    values = {item["metric_key"]: item["value_num"] for item in parsed["metrics"]}
    assert values["monthly.项目A.collection.receivable"] == 1076.37
    assert values["monthly.项目A.collection.rate"] == 0.735
    assert values["monthly.项目D.occupancy.rate"] == 0.76
    assert values["monthly.项目D.occupancy.tenants"] == 69
    labels = {item["metric_key"]: item["label"] for item in parsed["metrics"]}
    assert "新签面积" in labels.get("monthly.leasing.new_area", "")
    assert {item["section"] for item in parsed["texts"]} == {"issues", "key_work", "team", "highlights"}


def test_kpi_rate_derivation_rejects_percent_target():
    """目标值是百分数（2.40%）时，用它推导完成率会放大成 3690%，必须放弃推导。"""
    wb = Workbook()
    ws = wb.active
    ws.title = "表2、2026年度经营业绩责任书进度表"
    ws.append(["指标类型", "指标名称", "考核权重", "分值区间", "年度目标值", "完成率", "完成情况"])
    ws.append(["发展类指标（25）", "平均出租率增长值（%）", 5, "[0,6]", "2.40%", None, 0.886])
    parsed = _parse("control", "内控.xlsx", _bytes(wb))
    kpi = [item for item in parsed["records"] if item["section"] == "kpi"]
    assert kpi and kpi[0].get("rate") is None


def test_monthly_without_numbers_only_warns():
    wb = Workbook()
    ws = wb.active
    ws["A1"] = "月度工作情况表（2026年10月）"
    ws["A7"] = "一、问题线索处置及案件办理情况：\n暂无。"
    parsed = _parse("monthly", "月度.xlsx", _bytes(wb))
    assert parsed["metrics"] == []
    assert any("未抽到" in warning for warning in parsed["warnings"])


@pytest.mark.skipif(not TPL.is_dir(), reason="数据模板目录不存在")
def test_weekly_promo_stops_at_key_brand_block():
    """「新品牌」区块必须在「重点品牌」表头前停下，不能把竞品/维修基金等后续小节吞成品牌。"""
    path = TPL / "经营管理-云南项目周报-模板.xlsx"
    parsed = _parse("weekly", path.name, path.read_bytes())
    promo = [item for item in parsed["records"] if item["section"] == "brand_promo"]
    assert len(promo) == 3, [item["name"] for item in promo]
    assert {item["category"] for item in promo} == {"new", "key"}
    assert not any(
        "维修基金" in item.get("name", "") or item.get("name", "").startswith(("商业面积", "出租率", "表1"))
        for item in promo
    )


@pytest.mark.skipif(not TPL.is_dir(), reason="数据模板目录不存在")
def test_weekly_fund_metrics_parsed():
    """维修基金表的「项/预算」子表头不能把数据行挡掉（曾整块静默丢失）。"""
    path = TPL / "经营管理-云南项目周报-模板.xlsx"
    parsed = _parse("weekly", path.name, path.read_bytes())
    fund = [item for item in parsed["metrics"] if item["metric_key"].startswith("weekly.fund.2025.")]
    assert fund, parsed["warnings"]
    values = {item["metric_key"]: item["value_num"] for item in fund}
    assert values["weekly.fund.2025.翻新.annual_amount"] == 100000
    assert "weekly.fund.." not in {item["metric_key"] for item in parsed["metrics"]}


@pytest.mark.skipif(not TPL.is_dir(), reason="数据模板目录不存在")
def test_control_template_parses_all_sections():
    path = TPL / "经营管理-内控管理表格-模板.xlsx"
    parsed = _parse("control", path.name, path.read_bytes())
    sections = {item["section"] for item in parsed["records"]}
    assert {"key_work", "kpi", "litigation"} <= sections
    metric_sections = {item["section"] for item in parsed["metrics"]}
    assert {"revenue", "leasing"} <= metric_sections
    assert len(parsed["metrics"]) >= 50
    assert not parsed["warnings"]
