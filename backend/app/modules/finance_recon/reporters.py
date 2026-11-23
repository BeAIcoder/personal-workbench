# -*- coding: utf-8 -*-
"""商户核销对账导出渲染核心（Task 4 拆分辅助文件）。

exporters.py 接近 800 行软上限时按项目规则拆分：本文件承载三格式共享的
常量（关键合计标签 / 状态与来源标签 / 空态说明 / 表头）、视图行构造器
（唯一输入 ReportViewModel）与 Markdown / Word 两个渲染器；xlsx 渲染与
公共 API（export_xlsx 等）留在 ``exporters.py``，并从这里再导出公共名字，
调用方与测试仍统一 ``from . import exporters`` 使用。

- 问题清单与人工待补资料来自当前 active issue（视图 issues.items），不含
  任何静态文案（原对账脚本 P01–P12 单案结论不进入产品）；
- 金额两位字符串 → Decimal 直写（不经 float）；未填为空/「—」，不按 0 补齐；
- 有待确认事项显著标注「草稿 · 待确认」，不伪装定案；
- 排版通用，不复制原对账脚本封面常量或任何真实样本数据（INV-006）。
"""
from __future__ import annotations

import io
import re
from decimal import ROUND_HALF_UP, Decimal

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Pt, RGBColor

from .reporting import ReportViewModel
from .schemas import (
    BILLING_METHOD_LABELS,
    DUE_RULE_LABELS,
    RECONSTRUCTION_CUTOFF_LABELS,
)

_CENT = Decimal("0.01")

# 关键合计展示标签（xlsx/Markdown/docx 三格式共用同一映射，保证口径一致）
KEY_TOTAL_LABELS: dict[str, str] = {
    "bill_total": "账单累计应付合计",
    "arrear_total": "欠费合计",
    "writeoff_receivable_total": "费用期应收合计（核销）",
    "writeoff_cleared_total": "费用期核销合计",
    "writeoff_left_total": "核销后欠费合计",
    "writeoff_prepaid_total": "预收款合计",
    "claim_recv_total": "认领收款合计",
    "claim_claimed_total": "本次认领合计",
    "claim_fee_total": "认领手续费合计",
    "estimated_receivable_total": "推算应收合计（核销+欠费）",
    "confirmed_credits_total": "已确认抵扣合计",
    "settlement_net_total": "结算净额合计",
    "penalty_reference_total": "违约金参考合计",
    "matched_confirmed_total": "已确认匹配分摊合计",
    "matched_suggested_total": "待确认建议分摊合计",
    "unmatched_claim_total": "认领未匹配余额合计",
}

CHECK_STATE_LABELS: dict[str, str] = {"pass": "通过", "diff": "不一致", "unknown": "未知", "info": "说明"}
ORIGIN_LABELS: dict[str, str] = {"machine": "机检", "human": "人工"}
SEVERITY_LABELS: dict[str, str] = {"info": "提示", "warn": "警告", "error": "严重"}

# 未决事项显著标注（xlsx 汇总勾稽 / Markdown / docx 共用同一文案）
DRAFT_WARNING = "【草稿 · 待确认】"
DRAFT_NOTE = "本报告包含未确认事项，全部勾稽结论为草稿口径，未经人工确认不得作为定案依据。"
PENDING_LINE = "待确认问题：{n} 项"
UNMATCHED_LINE = (
    "认领未匹配：{n} 笔，未匹配余额 {total} 元（另有待确认建议 {suggested} 元，未确认前不计入已核销）。"
)
FOOTER_NOTE = (
    "本报告由商户核销对账模块按批次数据统一生成（xlsx / Markdown / Word 同源，关键合计一致）；"
    "未确认事项以「待确认」标注，人工确认后方可作为定案口径。"
)

# 空态说明（无来源项不补 0：输出显式说明而不是编造 0 行）
EMPTY_WRITEOFF_NOTE = "本批次范围内无核销明细（不按 0 补齐）"
EMPTY_AGING_NOTE = "本批次范围内无欠费明细，无账龄分桶（不按 0 补齐）"
EMPTY_SETTLEMENT_NOTE = "本批次范围内无欠费与已确认抵扣，无结算草稿行（不按 0 补齐）"
EMPTY_SUPPLEMENT_NOTE = "无人工登记的待补资料事项。"
EMPTY_ISSUE_NOTE = "本批次无当前活动问题。"
EMPTY_CLAIM_NOTE = "本批次无认领记录。"

UNMEASURED = "—"

_ISSUE_HEADERS = ("编号", "来源", "类别", "级别", "状态", "金额影响（元）", "证据", "人工说明", "最近命中")
_SUPPLEMENT_HEADERS = ("编号", "事项（人工登记类别）", "状态", "金额影响（元）", "证据", "说明", "登记时间")
_SETTLEMENT_HEADERS = (
    "合同", "欠费合计（元）", "已到期（元）", "未到期（元）", "到期未标注（元）",
    "已确认抵扣（元）", "未确认抵扣（元）", "结算净额（元）", "违约金（参考测算，元）", "违约金说明",
)
_CLAIM_HEADERS = (
    "单据号", "交易日期", "认领金额（元）", "已确认分摊（元）",
    "待确认建议（元）", "未匹配余额（元）", "扣除建议后参考（元）",
)
_CHECK_HEADERS = ("检查", "范围", "结论", "左侧（元）", "右侧（元）", "差异（元）", "说明")


# ---------- 共享小工具与视图行构造 ----------

# XML 1.0 禁止的控制字符（保留合法 \t \n \r）：openpyxl 写 xlsx（IllegalCharacterError）
# 与 python-docx/lxml 写 docx（序列化 ValueError）遇到即抛错 → 导出 500；
# 在本模块导出边界统一清洗（全站 utils_xlsx 属共享模块，本轮不动）。
_XML_ILLEGAL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def strip_illegal_xml_chars(text: str) -> str:
    """去掉 XML 1.0 禁止控制字符（保留合法 \t/\n/\r）；xlsx/docx 字符串入口统一调用。"""
    return _XML_ILLEGAL_CHARS_RE.sub("", text)


def _num(text) -> Decimal | None:
    """两位字符串 → Decimal（不经 float）；空串/None → None（未填，不补 0）。"""
    if text is None:
        return None
    stripped = str(text).strip()
    if not stripped:
        return None
    return Decimal(stripped).quantize(_CENT, rounding=ROUND_HALF_UP)


def is_draft(view: ReportViewModel) -> bool:
    """是否按草稿口径呈现：存在待确认问题，或批次尚未通过勾稽检查（非 ready）。"""
    return bool(view.pending_count) or view.batch["status"] != "ready"


def pending_line(view: ReportViewModel) -> str:
    return PENDING_LINE.format(n=view.pending_count)


def unmatched_line(view: ReportViewModel) -> str:
    return UNMATCHED_LINE.format(
        n=len(view.matches.get("unmatched_claims") or []),
        total=view.totals.get("unmatched_claim_total") or "0.00",
        suggested=view.totals.get("matched_suggested_total") or "0.00",
    )


def issue_counts_line(view: ReportViewModel) -> str:
    counts = view.issues.get("counts") or {}
    return (
        f"{pending_line(view)}"
        f"（机检 {counts.get('machine_total', 0)} 项、人工 {counts.get('human_total', 0)} 项）；"
        f"已确认 {counts.get('confirmed', 0)} 项、已排除 {counts.get('dismissed', 0)} 项、"
        f"已解除 {counts.get('resolved', 0)} 项。"
    )


def _batch_meta(view: ReportViewModel) -> list[tuple[str, str]]:
    """批次信息键值对（xlsx / Markdown / docx 同序同值，保证三格式一致）。"""
    batch = view.batch
    return [
        ("批次名称", batch["name"]),
        ("对账对象", batch["object_tag"] or UNMEASURED),
        ("对账日", batch["as_of"]),
        ("金额容差", batch["tolerance"]),
        ("批次状态", batch["status_label"]),
        ("合同范围", "、".join(batch["contract_scope"]) or UNMEASURED),
        ("报告生成时间", view.generated_at),
    ]


def _terms_lines(view: ReportViewModel) -> list[str]:
    """口径参数逐合同说明（未录入/缺失如实说明，不编造口径）。"""
    lines: list[str] = []
    for item in view.terms:
        name = item["contract_no"]
        terms = item.get("terms")
        if not terms:
            lines.append(f"{name}：未录入口径参数（合同应收重构/违约金检查按未知处理）")
            continue
        method = BILLING_METHOD_LABELS.get(terms.get("billing_method") or "", "未选择")
        cutoff = RECONSTRUCTION_CUTOFF_LABELS.get(terms.get("reconstruction_cutoff") or "", "未选择")
        if item["complete"]:
            state = "参数完整"
        else:
            state = "参数缺失（缺：" + "、".join(item["missing"]) + "）"
        line = f"{name}：计费方式 {method}；重构截止口径 {cutoff}；{state}"
        due_rule = terms.get("due_rule")
        if due_rule:
            line += f"；缴费到期规则 {DUE_RULE_LABELS.get(due_rule, due_rule)}"
        penalty = terms.get("penalty") or {}
        if penalty.get("waived"):
            line += "；违约金已豁免（人工标记 waived，仅作记录）"
        elif penalty.get("rate_per_day"):
            line += f"；违约金参考日费率 {penalty['rate_per_day']}"
        lines.append(line)
    return lines


def _scope_lines(view: ReportViewModel) -> list[str]:
    """口径说明固定段 + 逐合同口径参数（三格式共用同一段文字）。"""
    tolerance = view.batch["tolerance"]
    lines = [
        f"勾稽容差 {tolerance} 元：|差异| ≤ 容差视为一致；超出容差的差异需人工确认后才能作为报告口径。",
        "金额单位：元（两位小数，ROUND_HALF_UP）；未填金额显示为「—」或留空，不按 0 补齐。",
        "违约金为参考测算（欠费本金 × 日费率 × 逾期天数），不计入结算净额，cap 封顶，豁免只作记录。",
        "认领↔核销机检建议为待确认状态：未确认前不计入已核销，绝不默认归入预收。",
        "问题清单与待补资料来自当前活动问题（机检待确认 + 人工登记），不含任何静态文案。",
    ]
    lines.extend(_terms_lines(view))
    lines.append("三格式同源：本报告与 xlsx 工作底稿、Word 报告由同一份批次视图生成，关键合计一致。")
    return lines


def _check_rows(view: ReportViewModel) -> list[list]:
    rows = []
    for item in view.checks:
        rows.append(
            [
                item["label"],
                item["scope"],
                CHECK_STATE_LABELS.get(item["state"], item["state"]),
                _num(item.get("left")),
                _num(item.get("right")),
                _num(item.get("delta")),
                item["message"],
            ]
        )
    return rows


def _claim_rows(view: ReportViewModel) -> list[list]:
    rows = []
    for claim in view.matches.get("claims") or []:
        rows.append(
            [
                claim["doc_no"] or UNMEASURED,
                claim["occur_date"] or UNMEASURED,
                _num(claim["claimed_amount"]),
                _num(claim["confirmed_amount"]),
                _num(claim["suggested_amount"]),
                _num(claim["remainder"]),
                _num(claim["uncovered_after_suggestions"]),
            ]
        )
    return rows


def _issue_rows(view: ReportViewModel) -> list[list]:
    rows = []
    for item in view.issues.get("items") or []:
        rows.append(
            [
                int(item["id"]),
                ORIGIN_LABELS.get(item["origin"], item["origin"]),
                item["category_label"],
                SEVERITY_LABELS.get(item["severity"], item["severity"]),
                item["status_label"],
                _num(item["amount_impact"]),
                "；".join(item["evidence"]),
                item["note"],
                item["last_seen"] or "",
            ]
        )
    return rows


def _supplement_items(view: ReportViewModel) -> list[dict]:
    """待补资料 = 当前 active 的人工登记事项（机检问题永不进入本清单）。"""
    return [item for item in view.issues.get("items") or [] if item["origin"] == "human"]


def _settlement_rows(view: ReportViewModel) -> list[list]:
    rows = []
    for row in view.settlement:
        rows.append(
            [
                row["contract_no"],
                _num(row["arrear_total"]),
                _num(row["due_receivable"]),
                _num(row["undue_receivable"]),
                _num(row["unmarked_receivable"]),
                _num(row["confirmed_credits"]),
                _num(row["unconfirmed_credits"]),
                _num(row["settlement_net"]),
                _num(row["penalty_reference"]),
                row["penalty_note"],
            ]
        )
    return rows


# ---------- Markdown 渲染 ----------


def _md_cell(value) -> str:
    """表格单元格转义：竖线改全角、换行并句；None → 「—」（未填不补 0）。"""
    if value is None:
        return UNMEASURED
    if isinstance(value, Decimal):
        return str(value)
    return str(value).replace("|", "｜").replace("\r", "").replace("\n", "；")


def _md_table(headers, rows) -> list[str]:
    out = ["| " + " | ".join(str(h) for h in headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    for row in rows:
        out.append("| " + " | ".join(_md_cell(v) for v in row) + " |")
    return out


def export_markdown(view: ReportViewModel) -> str:
    """与 xlsx / docx 同源的 Markdown 报告；未决事项显著标注草稿/待确认。"""
    batch = view.batch
    lines: list[str] = [f"# 商户核销对账报告：{batch['name']}", ""]
    if is_draft(view):
        lines.append(f"> **{DRAFT_WARNING}** 本报告包含 {view.pending_count} 项待确认问题，{DRAFT_NOTE}")
        lines.append("")

    lines.append("## 一、批次信息")
    lines.append("")
    lines.extend(_md_table(("项目", "内容"), _batch_meta(view)))
    lines.append("")

    lines.append("## 二、口径说明")
    lines.append("")
    for line in _scope_lines(view):
        lines.append(f"- {line}")
    lines.append("")

    lines.append("## 三、关键合计")
    lines.append("")
    lines.extend(
        _md_table(
            ("指标", "金额（元）"),
            [(label, _num(view.totals.get(key))) for key, label in KEY_TOTAL_LABELS.items()],
        )
    )
    lines.append("")

    lines.append("## 四、勾稽检查结论")
    lines.append("")
    lines.extend(_md_table(_CHECK_HEADERS, _check_rows(view)))
    lines.append("")

    lines.append("## 五、欠费账龄")
    lines.append("")
    aging = view.aging
    buckets = aging.get("buckets") or []
    if not buckets:
        lines.append(f"- {EMPTY_AGING_NOTE}")
    else:
        lines.extend(
            _md_table(("账龄区间", "金额（元）"), [(b["label"], _num(b["total"])) for b in buckets])
        )
        lines.append(
            f"- 无法归账龄金额：{aging.get('unaged_total', '0.00')} 元；"
            f"账龄合计：{aging.get('grand_total', '0.00')} 元；"
            f"账龄完整性：{'完整' if aging.get('complete') else '不完整（存在无法归账龄行）'}"
        )
        for item in aging.get("by_contract") or []:
            parts = [f"{b['label']} {_num(b['total'])} 元" for b in item["buckets"]]
            if item.get("unaged_total") not in (None, "", "0.00"):
                parts.append(f"无法归账龄 {_num(item['unaged_total'])} 元")
            lines.append(f"- {item['contract_no']}：{'、'.join(parts) or '无可归集欠费'}")
    lines.append("")

    lines.append("## 六、结算草稿（当前欠费 − 已确认抵扣；违约金列为参考测算，不计入结算净额）")
    lines.append("")
    settlement = _settlement_rows(view)
    if not settlement:
        lines.append(f"- {EMPTY_SETTLEMENT_NOTE}")
    else:
        lines.extend(_md_table(_SETTLEMENT_HEADERS, settlement))
    lines.append("")

    lines.append("## 七、认领覆盖")
    lines.append("")
    claims = _claim_rows(view)
    if not claims:
        lines.append(f"- {EMPTY_CLAIM_NOTE}")
    else:
        lines.extend(_md_table(_CLAIM_HEADERS, claims))
    lines.append(f"- {unmatched_line(view)}")
    lines.append("")

    lines.append("## 八、未决事项")
    lines.append("")
    lines.append(f"- {issue_counts_line(view)}")
    issues = _issue_rows(view)
    if not issues:
        lines.append(f"- {EMPTY_ISSUE_NOTE}")
    else:
        lines.extend(
            _md_table(
                ("编号", "来源", "类别", "级别", "状态", "金额影响（元）", "证据与说明"),
                [[r[0], r[1], r[2], r[3], r[4], r[5], _issue_evidence_cell(r)] for r in issues],
            )
        )
    lines.append("")

    lines.append("## 九、待补资料（人工登记）")
    lines.append("")
    supplements = _supplement_items(view)
    if not supplements:
        lines.append(f"- {EMPTY_SUPPLEMENT_NOTE}")
    else:
        lines.extend(
            _md_table(
                ("编号", "事项", "状态", "金额影响（元）", "说明"),
                [
                    [item["id"], item["category_label"], item["status_label"],
                     _num(item["amount_impact"]), item["note"] or UNMEASURED]
                    for item in supplements
                ],
            )
        )
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(f"*{FOOTER_NOTE}（批次 {batch['id']}，生成时间 {view.generated_at}）*")
    # md 为纯文本不存在 XML 序列化问题，但统一在导出边界清洗，三格式行为一致
    return strip_illegal_xml_chars("\n".join(lines) + "\n")


def _issue_evidence_cell(row: list) -> str:
    """未决事项表的「证据与说明」：证据 + 人工说明合并（说明为空时不拼接）。"""
    note = str(row[7]) if row[7] else ""
    evidence = str(row[6])
    return evidence + ("；人工说明：" + note) if note else evidence


# ---------- Word（docx）渲染 ----------


def _docx_setup(doc: Document) -> None:
    """通用排版：中文正文宋体 + 黑体标题；通用样式，不携带任何样本主体内容。"""
    normal = doc.styles["Normal"]
    normal.font.name = "宋体"
    normal.font.size = Pt(10.5)
    normal.element.get_or_add_rPr().get_or_add_rFonts().set(qn("w:eastAsia"), "宋体")
    for name in ("Title", "Heading 1", "Heading 2"):
        style = doc.styles[name]
        style.font.name = "黑体"
        style.element.get_or_add_rPr().get_or_add_rFonts().set(qn("w:eastAsia"), "黑体")
        style.font.color.rgb = RGBColor(0x20, 0x20, 0x20)
        style.font.bold = True


def _docx_cell(value) -> str:
    """docx 表格单元格：None/未测算与 md 一致显示「—」；字符串统一清洗控制字符。"""
    if value is None:
        return UNMEASURED
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, str):
        return strip_illegal_xml_chars(value).replace("\r", "").replace("\n", "；")
    return str(value)


def _docx_table(doc: Document, headers, rows) -> None:
    table = doc.add_table(rows=1, cols=len(headers))
    table.style = "Table Grid"
    for col, text in enumerate(headers):
        cell = table.rows[0].cells[col]
        cell.text = ""
        run = cell.paragraphs[0].add_run(str(text))
        run.bold = True
    for values in rows:
        cells = table.add_row().cells
        for col, value in enumerate(values):
            cells[col].text = _docx_cell(value)


def _docx_bullets(doc: Document, lines) -> None:
    for line in lines:
        doc.add_paragraph(strip_illegal_xml_chars(line), style="List Bullet")


def _docx_note(doc: Document, text: str, *, bold: bool = False, red: bool = False) -> None:
    paragraph = doc.add_paragraph()
    run = paragraph.add_run(strip_illegal_xml_chars(text))
    run.bold = bold
    if red:
        run.font.color.rgb = RGBColor(0xB0, 0x00, 0x00)


def export_docx(view: ReportViewModel) -> io.BytesIO:
    """通用排版 Word 报告：批次/口径/关键合计/检查状态/账龄/结算草稿/未决事项，
    与 xlsx、Markdown 同源；排版不复制原对账脚本的封面常量或样本主体。"""
    batch = view.batch
    doc = Document()
    _docx_setup(doc)

    doc.add_heading("商户核销对账报告", 0)
    subtitle = doc.add_paragraph()
    run = subtitle.add_run(
        strip_illegal_xml_chars(f"批次：{batch['name']}（编号 {batch['id']}）· 生成时间 {view.generated_at}")
    )
    run.font.color.rgb = RGBColor(0x59, 0x59, 0x59)
    if is_draft(view):
        _docx_note(
            doc,
            f"{DRAFT_WARNING} 本报告包含 {view.pending_count} 项待确认问题，{DRAFT_NOTE}",
            bold=True,
            red=True,
        )

    doc.add_heading("一、批次信息", level=1)
    _docx_table(doc, ("项目", "内容"), _batch_meta(view))

    doc.add_heading("二、口径说明", level=1)
    _docx_bullets(doc, _scope_lines(view))

    doc.add_heading("三、关键合计（元）", level=1)
    _docx_table(
        doc,
        ("指标", "金额（元）"),
        [(label, _num(view.totals.get(key))) for key, label in KEY_TOTAL_LABELS.items()],
    )

    doc.add_heading("四、勾稽检查结论", level=1)
    _docx_table(doc, _CHECK_HEADERS, _check_rows(view))

    doc.add_heading("五、欠费账龄", level=1)
    aging = view.aging
    buckets = aging.get("buckets") or []
    if not buckets:
        _docx_note(doc, EMPTY_AGING_NOTE)
    else:
        _docx_table(doc, ("账龄区间", "金额（元）"), [(b["label"], _num(b["total"])) for b in buckets])
        _docx_note(
            doc,
            f"无法归账龄金额 {aging.get('unaged_total', '0.00')} 元；账龄合计 "
            f"{aging.get('grand_total', '0.00')} 元；账龄完整性："
            f"{'完整' if aging.get('complete') else '不完整（存在无法归账龄行）'}",
        )
        for item in aging.get("by_contract") or []:
            parts = [f"{b['label']} {_num(b['total'])} 元" for b in item["buckets"]]
            if item.get("unaged_total") not in (None, "", "0.00"):
                parts.append(f"无法归账龄 {_num(item['unaged_total'])} 元")
            _docx_bullets(doc, [f"{item['contract_no']}：{'、'.join(parts) or '无可归集欠费'}"])

    doc.add_heading("六、结算草稿（违约金列为参考测算，不计入结算净额）", level=1)
    settlement = _settlement_rows(view)
    if not settlement:
        _docx_note(doc, EMPTY_SETTLEMENT_NOTE)
    else:
        _docx_table(doc, _SETTLEMENT_HEADERS, settlement)

    doc.add_heading("七、认领覆盖", level=1)
    claims = _claim_rows(view)
    if not claims:
        _docx_note(doc, EMPTY_CLAIM_NOTE)
    else:
        _docx_table(doc, _CLAIM_HEADERS, claims)
    _docx_note(doc, unmatched_line(view))

    doc.add_heading("八、未决事项", level=1)
    _docx_note(doc, issue_counts_line(view), bold=True)
    issues = _issue_rows(view)
    if not issues:
        _docx_note(doc, EMPTY_ISSUE_NOTE)
    else:
        _docx_table(
            doc,
            ("编号", "来源", "类别", "级别", "状态", "金额影响（元）", "证据与说明"),
            [[r[0], r[1], r[2], r[3], r[4], r[5], _issue_evidence_cell(r)] for r in issues],
        )

    doc.add_heading("九、待补资料（人工登记）", level=1)
    supplements = _supplement_items(view)
    if not supplements:
        _docx_note(doc, EMPTY_SUPPLEMENT_NOTE)
    else:
        _docx_table(
            doc,
            ("编号", "事项", "状态", "金额影响（元）", "说明"),
            [
                [item["id"], item["category_label"], item["status_label"],
                 _num(item["amount_impact"]), item["note"] or UNMEASURED]
                for item in supplements
            ],
        )

    footer = doc.add_paragraph()
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
    foot_run = footer.add_run(f"{FOOTER_NOTE}（批次 {batch['id']}，生成时间 {view.generated_at}）")
    foot_run.italic = True
    foot_run.font.size = Pt(9)
    foot_run.font.color.rgb = RGBColor(0x59, 0x59, 0x59)

    buf = io.BytesIO()
    doc.save(buf)
    buf.seek(0)
    return buf


# ---------- 下载文件名（router 拼 Content-Disposition 双头） ----------

_FORBIDDEN_FILENAME_CHARS = '\\/:*?"<>|\r\n\t'


def download_names(view: ReportViewModel, suffix: str) -> tuple[str, str]:
    """返回 (中文文件名, 纯 ASCII 兜底名)：中文里用户内容只来自批次名（已清洗）。"""
    batch = view.batch
    raw = strip_illegal_xml_chars(f"商户核销对账报告_{batch['name'] or '未命名批次'}_{batch['as_of']}")
    cleaned = "".join("-" if ch in _FORBIDDEN_FILENAME_CHARS else ch for ch in raw).strip("- ")
    cn_name = (cleaned[:80] or "商户核销对账报告") + "." + suffix
    ascii_name = f"recon-report-b{batch['id']}-{batch['as_of']}.{suffix}"
    return cn_name, ascii_name
