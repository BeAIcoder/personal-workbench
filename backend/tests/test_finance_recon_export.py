# -*- coding: utf-8 -*-
"""finance_recon 三格式导出测试（Task 4）。

覆盖 task-4-brief 与计划 139–150 行的全部要求（一律临时 SQLite + 合成数据，
不读真实样本、不复制原脚本封面常量或任何真实数据）：

- 三格式同源：xlsx / Markdown / docx 由同一 ``ReportViewModel`` 生成，关键合计、
  差异、未决数、口径（对账日/容差/状态/合同范围）在三种产物中一致；
- 未决事项显著标注「草稿 · 待确认」，不得伪装定案；无未决事项不出草稿标记；
- xlsx 保留五类工作表（汇总勾稽 / 核销逐笔 / 欠费账龄与违约金 / 问题清单 /
  待补资料）；当前 active issue 与人工待补资料来自视图，不含静态 P01–P12；
- 无来源项不补 0：无核销/无欠费/无待补资料时输出显式说明行，null 与 0 严格区分；
- 所有字符串单元格经 ``app.utils_xlsx.safe_cell`` 防公式注入；金额 Decimal 直写
  不经 float；下载响应带 ASCII filename + filename* UTF-8 中文名；
- docx / xlsx 可重新打开；核销逐笔可追溯到来源文件与源行号；
- 导出内容不含户名/完整账号/原脚本样本主体（敏感字段零出现）；
- 路由先 ``service.assert_exportable``（needs_recheck → 409），缺批次 404。
"""
from __future__ import annotations

import io
import re
from datetime import date
from decimal import Decimal
from urllib.parse import unquote

import openpyxl
import pytest
from docx import Document as load_docx

from app.database import SessionLocal
from app.modules.finance_recon import exporters, matching, service
from app.modules.finance_recon.models import ReconBatch, ReconRecord, ReconSource
from app.modules.finance_recon.reporting import build_report
from app.modules.finance_recon.recon_engine import run_checks

P = "/api/finance/recon"
AS_OF = date(2026, 11, 18)
CENT = Decimal("0.01")

# 合成数据（SYN/EX/NX/CL 前缀，与真实样本无任何关联；不复制 P01–P12 与原脚本主体）。
# 负向 token 全部用合成词，代表「真实样本主体识别词」这一类禁出内容，不嵌入真实词。
BANNED_SAMPLE_TOKENS = ("P01", "P12", "样本主体甲", "样本主体乙", "样本项目丙", "SYN-DZ-001")


@pytest.fixture(autouse=True)
def _ensure_tables():
    """非 client 直触库的用例保证 recon 六表存在（契约：test_test_isolation_contract）。"""
    from sqlalchemy import inspect

    from app.database import Base, engine

    if not inspect(engine).has_table("mod_recon_batch"):
        Base.metadata.create_all(bind=engine)
    yield


# ---------- 合成数据种子（直接落 ReconRecord，不经解析器与真实样本） ----------


def _seed_batch(
    scope=("HT-EX-001", "HT-EX-002"), name="导出合成批次", tag="合成商户", as_of=AS_OF
) -> tuple[int, dict]:
    with SessionLocal() as db:
        batch = ReconBatch(
            name=name,
            object_tag=tag,
            as_of=as_of,
            tolerance=Decimal("0.01"),
            contract_scope=list(scope),
            status="draft",
        )
        db.add(batch)
        db.flush()
        sources: dict[str, int] = {}
        for kind in ("writeoff", "claim", "arrear", "bill"):
            src = ReconSource(
                batch_id=batch.id,
                kind=kind,
                original_name=f"synthetic-{kind}.xlsx",
                sha256=(kind + "0" * 56),
                parse_status="parsed",
            )
            db.add(src)
            db.flush()
            sources[kind] = src.id
        db.commit()
        return batch.id, sources


def _rec(
    db,
    batch_id: int,
    source_id: int,
    kind: str,
    *,
    row_no: int,
    contract: str = "",
    doc: str = "",
    fee_type: str = "",
    ps=None,
    pe=None,
    occur=None,
    due=None,
    a1=None,
    a2=None,
    a3=None,
    a4=None,
    details=None,
) -> int:
    row = ReconRecord(
        batch_id=batch_id,
        source_id=source_id,
        row_no=row_no,
        kind=kind,
        contract_no=contract,
        doc_no=doc,
        fee_type=fee_type,
        fee_period_start=ps,
        fee_period_end=pe,
        occur_date=occur,
        due_date=due,
        amount_1=None if a1 is None else Decimal(str(a1)),
        amount_2=None if a2 is None else Decimal(str(a2)),
        amount_3=None if a3 is None else Decimal(str(a3)),
        amount_4=None if a4 is None else Decimal(str(a4)),
        fingerprint=f"synthetic-{kind}-{row_no}",
        details=details or {},
    )
    db.add(row)
    db.flush()
    return row.id


def _run(batch_id: int) -> dict:
    with SessionLocal() as db:
        return run_checks(db, batch_id).as_dict()


def _view(batch_id: int):
    with SessionLocal() as db:
        return build_report(db, batch_id)


def _seed_export_batch() -> int:
    """富场景批次：2 合同、4 行核销（含公式注入样本与全空金额行）、1 笔认领、
    2 行欠费、2 份账单；对账差 0.02（bill/receivable 双检查 diff）+ 1 条人工待补资料。
    认领 details 含户名/账号尾号等敏感字段，导出必须零出现。"""
    bid, sources = _seed_batch()
    with SessionLocal() as db:
        _rec(db, bid, sources["writeoff"], "writeoff", row_no=1, contract="HT-EX-001",
             doc="DZ-EX-0001", fee_type="固定租金", ps=date(2026, 9, 1), pe=date(2026, 9, 30),
             occur=date(2026, 10, 6), a1=200, a2=200, a3=0, details={"bill_month": "2026-09"})
        _rec(db, bid, sources["writeoff"], "writeoff", row_no=2, contract="HT-EX-001",
             doc="DZ-EX-0002", fee_type="固定租金", ps=date(2026, 10, 1), pe=date(2026, 10, 31),
             a1=100, a2=None, a3=100, details={"bill_month": "2026-10"})
        _rec(db, bid, sources["writeoff"], "writeoff", row_no=3, contract="HT-EX-002",
             doc="DZ-EX-0003", fee_type="综管费", ps=date(2026, 8, 1), pe=date(2026, 8, 31),
             a1=300, a2=None, a3=300, details={"bill_month": "2026-08"})
        _rec(db, bid, sources["writeoff"], "writeoff", row_no=4, contract="HT-EX-001",
             doc="=CMD", fee_type="维修费", ps=date(2026, 10, 1), pe=date(2026, 10, 31),
             details={"bill_month": "2026-10"})
        _rec(db, bid, sources["claim"], "claim", row_no=1, doc="SK-EX-0001",
             occur=date(2026, 10, 6), a1=200, a2=200,
             details={
                 "txn_time": "2026-10-06 10:00:00", "audit_time": "2026-10-06 12:00:00",
                 "payer_name": "敏感户名甲乙丙", "payer_account_tail": "53218899",
                 "payee_name": "敏感收款户名", "payee_account_tail": "77665544",
                 "purpose": "货款", "memo": "尾号53218899",
             })
        _rec(db, bid, sources["arrear"], "arrear", row_no=1, contract="HT-EX-001",
             ps=date(2026, 10, 1), pe=date(2026, 10, 31), due=date(2026, 11, 15),
             a1=100.02, a2=60.02, a3=40, details={"bill_month": "2026-10"})
        _rec(db, bid, sources["arrear"], "arrear", row_no=2, contract="HT-EX-002",
             ps=date(2026, 8, 1), pe=date(2026, 8, 31), due=date(2026, 12, 20),
             a1=300, a2=200, a3=100, details={"bill_month": "2026-08"})
        _rec(db, bid, sources["bill"], "bill", row_no=1, contract="HT-EX-001",
             doc="ZD-EX-001", due=date(2026, 11, 15), a1=100, details={"bill_month": "2026-10"})
        _rec(db, bid, sources["bill"], "bill", row_no=2, contract="HT-EX-002",
             doc="ZD-EX-002", due=date(2026, 12, 20), a1=300, details={"bill_month": "2026-08"})
        db.commit()
    assert _run(bid)["status"] == "ready"
    with SessionLocal() as db:
        matching.create_issue(
            db, bid,
            {"category": "待补资料-认领明细", "severity": "info",
             "note": "+请补充 2026-09 认领明细", "evidence": ["=HYPERLINK(认领明细)"]},
        )
    return bid


def _seed_clean_batch() -> int:
    """对平批次：认领精确匹配后人工确认并重跑 → 0 个待确认问题。"""
    bid, sources = _seed_batch(scope=("HT-CL-001",), name="对平合成批次")
    with SessionLocal() as db:
        _rec(db, bid, sources["writeoff"], "writeoff", row_no=1, contract="HT-CL-001",
             doc="DZ-CL-0001", fee_type="固定租金", ps=date(2026, 10, 1), pe=date(2026, 10, 31),
             occur=date(2026, 10, 6), a1=300, a2=200, a3=0, details={"bill_month": "2026-10"})
        _rec(db, bid, sources["claim"], "claim", row_no=1, doc="SK-CL-0001",
             occur=date(2026, 10, 6), a1=200, a2=200,
             details={"txn_time": "2026-10-06 10:00:00", "audit_time": "2026-10-06 12:00:00"})
        _rec(db, bid, sources["arrear"], "arrear", row_no=1, contract="HT-CL-001",
             ps=date(2026, 10, 1), pe=date(2026, 10, 31), due=date(2026, 11, 15),
             a1=100, details={"bill_month": "2026-10"})
        _rec(db, bid, sources["bill"], "bill", row_no=1, contract="HT-CL-001",
             doc="ZD-CL-001", due=date(2026, 11, 15), a1=100, details={"bill_month": "2026-10"})
        db.commit()
    _run(bid)
    with SessionLocal() as db:
        from app.modules.finance_recon.models import ReconMatch

        match = db.query(ReconMatch).filter(ReconMatch.batch_id == bid).one()
        matching.patch_match(db, match.id, {"status": "confirmed", "note": "已核对回单"})
    _run(bid)
    return bid


def _seed_sparse_batch() -> int:
    """只有账单的批次：核销/欠费/认领全缺 → 导出不得按 0 补齐任何明细行。"""
    bid, sources = _seed_batch(scope=("HT-NX-001",), name="缺资料合成批次")
    with SessionLocal() as db:
        _rec(db, bid, sources["bill"], "bill", row_no=1, contract="HT-NX-001",
             doc="ZD-NX-001", due=date(2026, 11, 15), a1=88, details={"bill_month": "2026-10"})
        db.commit()
    assert _run(bid)["status"] == "ready"
    return bid


def _seed_dirty_batch() -> int:
    """控制字符批次：批次名/对账对象/来源文件名/人工说明/证据均含 XML 禁止控制字符，
    证据同时以 \\x0b + = 开头 → 清洗后必须仍然命中公式注入防护（顺序验证）。"""
    bid, sources = _seed_batch(name="批次\x0b脏名", tag="脏\x01标签")
    with SessionLocal() as db:
        src = db.get(ReconSource, sources["writeoff"])
        src.original_name = "synthetic\x0b-writeoff.xlsx"
        _rec(db, bid, sources["writeoff"], "writeoff", row_no=1, contract="HT-EX-001",
             doc="DZ-EX-0001", fee_type="固定租金", ps=date(2026, 10, 1), pe=date(2026, 10, 31),
             a1=88, a2=88, a3=0, details={"bill_month": "2026-10"})
        _rec(db, bid, sources["bill"], "bill", row_no=1, contract="HT-EX-001",
             doc="ZD-EX-001", due=date(2026, 11, 15), a1=88, details={"bill_month": "2026-10"})
        db.commit()
    assert _run(bid)["status"] == "ready"
    with SessionLocal() as db:
        matching.create_issue(
            db, bid,
            {"category": "待补资料-认领明细", "severity": "info",
             "note": "说明\x0b甲", "evidence": ["\x0b=HYPERLINK(x)"]},
        )
    return bid


# ---------- 三产物读取助手 ----------


def _norm(value) -> str | None:
    """单元格值统一成两位小数字符串（int/float/Decimal），None 原样。"""
    if value is None:
        return None
    if isinstance(value, str):
        return value.strip()
    return str(Decimal(str(value)).quantize(CENT))


def _load_xlsx(content: bytes):
    return openpyxl.load_workbook(io.BytesIO(content))


def _xlsx_texts(wb) -> list[str]:
    out: list[str] = []
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for cell in row:
                if isinstance(cell.value, str):
                    out.append(cell.value)
    return out


def _xlsx_pair(wb, label: str):
    """在所有工作表中找 label 所在行的右侧值（原始值，供数量断言）。"""
    for ws in wb.worksheets:
        for row in ws.iter_rows(values_only=True):
            values = list(row)
            if values and values[0] == label and len(values) > 1:
                return values[1]
    raise AssertionError(f"xlsx 中找不到键值对标签：{label}")


def _xlsx_rows_of(wb, sheet: str) -> list[list]:
    return [list(row) for row in wb[sheet].iter_rows(values_only=True)]


def _md_pair(md: str, label: str) -> str:
    match = re.search(rf"^\| {re.escape(label)} \| (.+?) \|$", md, re.M)
    assert match, f"Markdown 中找不到键值对：{label}"
    return match.group(1)


def _docx(doc_bytes: bytes):
    return load_docx(io.BytesIO(doc_bytes))


def _docx_text(doc) -> str:
    parts = [p.text for p in doc.paragraphs]
    for table in doc.tables:
        for row in table.rows:
            parts.append(" | ".join(cell.text for cell in row.cells))
    return "\n".join(parts)


def _docx_rows(doc) -> list[list[str]]:
    """docx 全部表格行（按单元格文本），供跨表定位未测算/差异单元格。"""
    rows = []
    for table in doc.tables:
        for row in table.rows:
            rows.append([cell.text for cell in row.cells])
    return rows


def _docx_pairs(doc) -> dict[str, str]:
    out: dict[str, str] = {}
    for table in doc.tables:
        for row in table.rows:
            cells = [cell.text.strip() for cell in row.cells]
            if len(cells) >= 2 and cells[0]:
                out.setdefault(cells[0], cells[1])
    return out


def _three_products(batch_id: int):
    view = _view(batch_id)
    xlsx = _load_xlsx(exporters.export_xlsx(view).getvalue())
    md = exporters.export_markdown(view)
    docx = _docx(exporters.export_docx(view).getvalue())
    return view, xlsx, md, docx


# ---------- 三格式同源：关键合计 / 差异 / 未决数 / 口径一致 ----------

KEY_PAIRS = [
    ("账单累计应付合计", "400.00"),
    ("欠费合计", "400.02"),
    ("费用期应收合计（核销）", "600.00"),
    ("结算净额合计", "400.02"),
    ("认领未匹配余额合计", "200.00"),
    ("待确认建议分摊合计", "200.00"),
]


def test_three_formats_consistent_totals_diff_and_states():
    bid = _seed_export_batch()
    view, wb, md, docx = _three_products(bid)

    for label, value in KEY_PAIRS:
        assert _norm(_xlsx_pair(wb, label)) == value, f"xlsx 合计不一致：{label}"
        assert _md_pair(md, label) == value, f"Markdown 合计不一致：{label}"
        assert _docx_pairs(docx).get(label) == value, f"docx 合计不一致：{label}"

    # 差异（bill/应收推算两检查 -0.02）在三格式中一致出现
    for text in ("\n".join(_xlsx_texts(wb)), md, _docx_text(docx)):
        assert "-0.02" in text, "三格式必须都带出勾稽差异 -0.02"

    # 口径一致：对账日 / 容差 / 批次状态 / 合同范围
    for label, value in (("对账日", "2026-11-18"), ("金额容差", "0.01"), ("批次状态", "已就绪")):
        assert _norm(_xlsx_pair(wb, label)) == value, f"xlsx 口径不一致：{label}"
        assert _md_pair(md, label) == value, f"Markdown 口径不一致：{label}"
        assert _docx_pairs(docx).get(label) == value, f"docx 口径不一致：{label}"
    assert "HT-EX-001、HT-EX-002" in md and "HT-EX-001、HT-EX-002" in _docx_text(docx)
    assert _norm(_xlsx_pair(wb, "合同范围")) == "HT-EX-001、HT-EX-002"

    # 未决数一致：4 项待确认（3 条机检 + 1 条人工）
    assert view.pending_count == 4
    assert _xlsx_pair(wb, "待确认问题（项）") == 4
    assert "待确认问题：4 项" in md
    assert "待确认问题：4 项" in _docx_text(docx)
    issue_rows = [r for r in _xlsx_rows_of(wb, "问题清单") if isinstance(r[0], int)]
    assert len(issue_rows) == 4


def test_report_view_writeoff_rows_same_source_as_totals():
    """新增视图字段 writeoff_rows 与合计同源：逐列求和 = 视图 totals（含 null 不计入）。"""
    bid = _seed_export_batch()
    view = _view(bid)
    rows = view.writeoff_rows
    assert len(rows) == 4
    totals = view.totals

    def _sum_of(field: str, total_key: str) -> None:
        acc = Decimal("0")
        for row in rows:
            value = row[field]
            if value is not None:
                acc += Decimal(str(value))
        assert str(acc.quantize(CENT)) == totals[total_key], f"{field} 与 {total_key} 不同源"

    _sum_of("amount_receivable", "writeoff_receivable_total")
    _sum_of("amount_cleared", "writeoff_cleared_total")
    _sum_of("amount_left", "writeoff_left_total")
    # null 与 0 区分：行 2 费用期核销未填为 None，行 1 核销后欠费为 "0.00"
    by_doc = {row["doc_no"]: row for row in rows}
    assert by_doc["DZ-EX-0002"]["amount_cleared"] is None
    assert by_doc["DZ-EX-0001"]["amount_left"] == "0.00"
    assert by_doc["=CMD"]["amount_receivable"] is None


# ---------- 未决事项显著标注草稿/待确认 ----------


def test_pending_draft_markers_in_md_and_docx():
    bid = _seed_export_batch()
    _, wb, md, docx = _three_products(bid)
    text_md, text_docx = md, _docx_text(docx)
    for text in (text_md, text_docx):
        assert "【草稿 · 待确认】" in text, "有待确认事项必须显著标注草稿"
        assert "不得作为定案依据" in text
    # xlsx 汇总勾稽 sheet：同样直接断言草稿标记（fix round 1 Minor）
    xlsx_texts = _xlsx_texts(wb)
    assert "【草稿 · 待确认】" in xlsx_texts, "xlsx 汇总勾稽必须直接给出草稿标记"
    assert any(t == "本报告包含未确认事项，全部勾稽结论为草稿口径，未经人工确认不得作为定案依据。" for t in xlsx_texts)
    # xlsx 问题清单：4 行全部「待确认」状态列
    rows = [r for r in _xlsx_rows_of(wb, "问题清单") if isinstance(r[0], int)]
    assert all(r[4] == "待确认" for r in rows), [r[4] for r in rows]


def test_clean_batch_no_draft_marker_and_zero_pending():
    bid = _seed_clean_batch()
    view, wb, md, docx = _three_products(bid)
    assert view.pending_count == 0
    assert view.batch["status"] == "ready"
    for text in (md, _docx_text(docx)):
        assert "【草稿 · 待确认】" not in text, "无未决事项不得伪装成草稿"
        assert "待确认问题：0 项" in text
    # xlsx 同样不得出现草稿标记（fix round 1 Minor）
    assert "【草稿 · 待确认】" not in _xlsx_texts(wb)
    assert _xlsx_pair(wb, "待确认问题（项）") == 0


# ---------- 路由：assert_exportable 守卫（409）、404、恢复后 200 ----------


def test_export_endpoints_guard_409_404_and_recover(client):
    bid = _seed_export_batch()
    for suffix in ("xlsx", "report.md", "report.docx"):
        resp = client.get(f"{P}/batches/{bid}/export/{suffix}")
        assert resp.status_code == 200, (suffix, resp.status_code, resp.text[:200])

    # ready 批次修改口径参数 → needs_recheck → 三端点一律 409
    with SessionLocal() as db:
        service.put_terms(db, bid, "HT-EX-001", {
            "contract_start": "2026-01-01", "contract_end": "2026-12-31",
            "billing_method": "whole_month", "reconstruction_cutoff": "contract_end",
            "monthly_fees": [{"fee_type": "固定租金", "monthly_amount": "1000.00"}],
        })
    for suffix in ("xlsx", "report.md", "report.docx"):
        resp = client.get(f"{P}/batches/{bid}/export/{suffix}")
        assert resp.status_code == 409, (suffix, resp.status_code)
        assert "重新执行勾稽检查" in resp.json()["detail"]

    # 重跑恢复 needs_recheck=false → 200（参数生效，重构检查不再 unknown）
    _run(bid)
    resp = client.get(f"{P}/batches/{bid}/export/xlsx")
    assert resp.status_code == 200

    # 批次不存在 → 404
    assert client.get(f"{P}/batches/999999/export/xlsx").status_code == 404
    assert client.get(f"{P}/batches/999999/export/report.md").status_code == 404
    assert client.get(f"{P}/batches/999999/export/report.docx").status_code == 404


# ---------- 下载响应：中文名双头、媒体类型 ----------


def test_export_endpoints_filenames_and_media_types(client):
    bid = _seed_export_batch()
    cases = {
        "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "report.md": "text/markdown",
        "report.docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }
    for path, media in cases.items():
        resp = client.get(f"{P}/batches/{bid}/export/{path}")
        assert resp.status_code == 200
        assert media in resp.headers["content-type"]
        disposition = resp.headers["content-disposition"]
        assert "attachment" in disposition
        ascii_name = re.search(r'filename="([^"]+)"', disposition).group(1)
        assert re.fullmatch(r"recon-report-b\d+-2026-11-18\.(xlsx|md|docx)", ascii_name), ascii_name
        utf8_name = re.search(r"filename\*=utf-8''([^;]+)", disposition, re.I).group(1)
        cn_name = unquote(utf8_name)
        assert cn_name.startswith("商户核销对账报告_")
        assert "导出合成批次" in cn_name and "2026-11-18" in cn_name
        suffix = path.rsplit(".", 1)[-1]
        assert cn_name.endswith("." + suffix)


# ---------- 无来源项不补 0（缺资料批次） ----------


def test_missing_data_not_zero_filled():
    bid = _seed_sparse_batch()
    view, wb, md, docx = _three_products(bid)
    texts = {"md": md, "docx": _docx_text(docx)}
    for kind, text in texts.items():
        assert exporters.EMPTY_SETTLEMENT_NOTE in text, kind
        assert exporters.EMPTY_AGING_NOTE in text, kind
        assert exporters.EMPTY_SUPPLEMENT_NOTE in text, kind

    ws3 = wb["欠费账龄与违约金"]
    cells3 = [c.value for row in ws3.iter_rows() for c in row]
    assert exporters.EMPTY_SETTLEMENT_NOTE in cells3
    assert exporters.EMPTY_AGING_NOTE in cells3
    assert all(v != "HT-NX-001" for v in cells3), "结算草稿不得补 0 行"

    ws2 = wb["核销逐笔"]
    cells2 = [c.value for row in ws2.iter_rows() for c in row]
    assert exporters.EMPTY_WRITEOFF_NOTE in cells2
    assert all("HT-NX-001" not in str(v) for v in cells2 if v is not None), "核销逐笔不得补 0 行"

    ws5 = wb["待补资料"]
    cells5 = [c.value for row in ws5.iter_rows() for c in row]
    assert exporters.EMPTY_SUPPLEMENT_NOTE in cells5
    assert not any(isinstance(v, int) for v in cells5), "无待补资料不得编造编号行"

    # null 与 0：未测算合计为空单元格（None），真实为零的合计为数值 0
    assert _xlsx_pair(wb, "费用期应收合计（核销）") is None
    assert _md_pair(md, "费用期应收合计（核销）") == "—"
    prepaid = _xlsx_pair(wb, "预收款合计")
    assert prepaid is not None and _norm(prepaid) == "0.00"
    assert _md_pair(md, "预收款合计") == "0.00"


# ---------- safe_cell 公式注入 + xlsx/docx 可重新打开 + 五表结构 ----------


def test_safe_cell_injection_and_five_sheets_reopenable():
    bid = _seed_export_batch()
    content = exporters.export_xlsx(_view(bid)).getvalue()
    wb = _load_xlsx(content)  # 可重新打开
    assert tuple(wb.sheetnames) == exporters.SHEET_TITLES

    wo_rows = [r for r in _xlsx_rows_of(wb, "核销逐笔") if isinstance(r[0], int)]
    injected = [r for r in wo_rows if r[4] == "'=CMD"]
    assert injected, "以 = 开头的单据号必须经 safe_cell 加前导单引号"
    plain = [r for r in wo_rows if r[4] == "DZ-EX-0001"]
    assert plain, "普通单据号不得被 safe_cell 改写"

    issue_rows = [r for r in _xlsx_rows_of(wb, "问题清单") if isinstance(r[0], int)]
    evidences = [str(r[6]) for r in issue_rows]
    assert any(e.startswith("'=HYPERLINK") for e in evidences), "证据单元格同样经 safe_cell"

    supp_rows = [r for r in _xlsx_rows_of(wb, "待补资料") if isinstance(r[0], int)]
    assert any(r[5] == "'+请补充 2026-09 认领明细" for r in supp_rows), "人工说明单元格经 safe_cell"

    doc_bytes = exporters.export_docx(_view(bid)).getvalue()
    doc = _docx(doc_bytes)  # docx 可重新打开
    assert "商户核销对账报告" in _docx_text(doc)
    md = exporters.export_markdown(_view(bid))
    assert md.startswith("# 商户核销对账报告")


# ---------- 证据来源行可追溯 ----------


def test_evidence_source_row_traceable():
    bid = _seed_export_batch()
    wb = _load_xlsx(exporters.export_xlsx(_view(bid)).getvalue())
    wo_rows = [r for r in _xlsx_rows_of(wb, "核销逐笔") if isinstance(r[0], int)]
    target = next(r for r in wo_rows if r[4] == "DZ-EX-0001")
    # 序号 + 来源文件 + 源行号 + 合同 + 金额：追溯到 synthetic-writeoff.xlsx 第 1 行
    assert target[1] == "synthetic-writeoff.xlsx"
    assert target[2] == 1
    assert target[3] == "HT-EX-001"
    assert _norm(target[10]) == "200.00" and _norm(target[11]) == "200.00" and _norm(target[12]) == "0.00"

    issue_rows = [r for r in _xlsx_rows_of(wb, "问题清单") if isinstance(r[0], int)]
    bill_mismatch = next(r for r in issue_rows if r[2] == "账单与欠费不一致")
    assert bill_mismatch[1] == "机检"
    evidence = str(bill_mismatch[6])
    assert "100.00" in evidence and "100.02" in evidence, evidence
    assert not str(bill_mismatch[7] or "")  # 机检问题未人工说明 → 空单元格（openpyxl 读回 None），不是 0


# ---------- 敏感字段与样本主体零出现 ----------


def test_no_sensitive_or_sample_fields_in_exports():
    bid = _seed_export_batch()
    view = _view(bid)
    xlsx_bytes = exporters.export_xlsx(view).getvalue()
    md = exporters.export_markdown(view)
    docx_bytes = exporters.export_docx(view).getvalue()

    wb = _load_xlsx(xlsx_bytes)
    xlsx_text = "\n".join(_xlsx_texts(wb))
    docx_text = _docx_text(_docx(docx_bytes))
    for text in (xlsx_text, md, docx_text):
        for banned in ("敏感户名甲乙丙", "敏感收款户名", "53218899", "77665544",
                       "payer_name", "payee_name", "account_tail"):
            assert banned not in text, f"导出含敏感字段：{banned}"
        for token in BANNED_SAMPLE_TOKENS:
            assert token not in text, f"导出复制了原脚本样本主体：{token}"

    # 认领敏感细节在视图层也不外泄（writeoff_rows 不含 claim 行，matches 无户名）
    assert all("payer" not in str(row) and "account" not in str(row) for row in view.writeoff_rows)


def test_markdown_and_docx_carry_same_unsettled_and_settlement():
    """md / docx 除批次/口径/合计外，还必须携带账龄、结算草稿、认领覆盖与未决明细。"""
    bid = _seed_export_batch()
    _, _, md, docx = _three_products(bid)
    text_docx = _docx_text(docx)
    for text in (md, text_docx):
        assert "违约金" in text and "参考测算" in text, "违约金必须显著标注参考测算"
        assert "结算草稿" in text and "HT-EX-002" in text
        assert "认领未匹配" in text, "未决事项必须包含认领未匹配"
        assert "待补资料" in text
    assert "| m0 |" not in md  # 分桶键不直接暴露，展示中文区间名
    assert "当月" in md and "未到期" in md, "账龄区间用中文标签"


# ---------- fix round 1：docx 未测算与 md 一致用「—」；XML 控制字符边界清洗 ----------


def test_docx_unmeasured_matches_md_dash():
    """Minor：docx 表格中 None/未测算单元格与 md 一致显示「—」（人工事项金额影响、
    未测算违约金两处入口），不再用空字符串。"""
    bid = _seed_export_batch()
    _, _, md, docx = _three_products(bid)
    rows = _docx_rows(docx)
    # 未决事项表：人工事项金额影响未填 → 「—」
    human = next(r for r in rows if len(r) >= 6 and r[2] == "待补资料-认领明细")
    assert human[5] == "—", human
    # 结算草稿表：未录参数的违约金参考列 → 「—」
    settlement = next(r for r in rows if len(r) >= 10 and r[0] == "HT-EX-001" and r[1] == "100.02")
    assert settlement[8] == "—", settlement
    # md 侧同样为「—」（两格式一致）
    assert re.search(r"^\| \d+ \| 人工 \| 待补资料-认领明细 \| 提示 \| 待确认 \| — \|", md, re.M)
    assert "| HT-EX-001 | 100.02 | 100.02 | 0.00 | 0.00 | 0.00 | 0.00 | 100.02 | — |" in md


def test_illegal_control_chars_cleaned_at_export_boundary():
    """Important：XML 禁止控制字符（\\x0b 等）在导出边界统一清洗，xlsx 写入不再
    IllegalCharacterError→500；docx/md 同边界清洗；清洗后才以 = 开头的值仍命中
    safe_cell（先清洗后防护的顺序验证），三个入口：批次名/来源文件名/证据。"""
    bid = _seed_dirty_batch()
    view = _view(bid)
    xlsx_bytes = exporters.export_xlsx(view).getvalue()  # 成功写完 = 无 IllegalCharacterError
    wb = _load_xlsx(xlsx_bytes)  # 真实写入 + 重开
    texts = _xlsx_texts(wb)
    assert not any("\x0b" in t or "\x01" in t for t in texts), "xlsx 单元格残留 XML 禁止控制字符"

    summary = _xlsx_rows_of(wb, "汇总勾稽")
    assert [r[1] for r in summary if r[0] == "批次名称"] == ["批次脏名"]
    assert [r[1] for r in summary if r[0] == "对账对象"] == ["脏标签"]
    # 入口 2：来源文件名（核销逐笔）
    wo_rows = [r for r in _xlsx_rows_of(wb, "核销逐笔") if isinstance(r[0], int)]
    assert [r[1] for r in wo_rows] == ["synthetic-writeoff.xlsx"]
    # 入口 3：证据 —— \x0b 清洗后以 = 开头仍被 safe_cell 加前导引号（防护不回归）
    supp_rows = [r for r in _xlsx_rows_of(wb, "待补资料") if isinstance(r[0], int)]
    assert [r[4] for r in supp_rows] == ["'=HYPERLINK(x)"]
    assert [r[5] for r in supp_rows] == ["说明甲"]
    # 该批次仍 1 项人工待确认 → xlsx 直接有草稿标记
    assert "【草稿 · 待确认】" in texts

    # docx 同边界清洗且可重新打开；md 纯文本同样无控制字符
    docx_bytes = exporters.export_docx(view).getvalue()
    docx_text = _docx_text(_docx(docx_bytes))
    assert "\x0b" not in docx_text and "\x01" not in docx_text
    assert "批次脏名" in docx_text
    md = exporters.export_markdown(view)
    assert "\x0b" not in md and "\x01" not in md
    assert "批次脏名" in md
