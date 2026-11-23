# -*- coding: utf-8 -*-
"""finance_recon 勾稽引擎、候选匹配、人工复核与口径参数测试（Task 3）。

覆盖 task-3-brief 全部场景（一律临时 SQLite + 合成数据，不读真实样本）：
- 勾稽：逐合同与总计「账单=欠费」（0.01 容差 / 超容差）、「核销+欠费推算应收」、
  零金额/空费用、同秒混单、审核倒挂、账单月/费用期错位、账龄合计、
  保证金/预收差异（仅显式费用类型/参数）、缺参数 unknown、无数据不恒真；
- 重构/违约金：whole_month / actual_days / custom_lines 三种口径、违约金仅
  参考测算（无 30 天/1‰/20 日等默认）、waived、到期/未到期拆分、结算草稿
  只计 confirmed 抵扣；
- 匹配：精确单据引用优先、金额+日期确定性候选、未匹配保持 unmatched（禁止
  默认预收）、重跑建议唯一、rejected 不重提、confirmed 冻结、人工建议不覆盖、
  部分分摊/多对多、确认超额 409；
- 问题生命周期：指纹=类别+scope（不含金额参数）、pending 可更新、不再命中
  resolved+active=false、confirmed/dismissed 冻结仅 active 翻转、resolved 再
  命中回 pending、human 永不被机检触碰；
- terms：ready 批次修改后 needs_recheck=true、导出守卫 409、重跑恢复；
- 状态失败回收：引擎异常 → failed + 可诊断摘要，重试恢复 ready，无半提交；
- 报告视图模型（ReportViewModel）供 summary/导出同源消费，金额全部为字符串。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import inspect

from app.database import SessionLocal
from app.modules.finance_recon.models import (
    ReconBatch,
    ReconIssue,
    ReconMatch,
    ReconRecord,
    ReconSource,
    ReconTerm,
)

AS_OF = date(2026, 11, 18)
TOL = Decimal("0.01")


@pytest.fixture(autouse=True)
def _ensure_tables():
    """非 client 直触库的用例保证 recon 六表存在（契约：test_test_isolation_contract）。"""
    from app.database import Base, engine

    if not inspect(engine).has_table("mod_recon_batch"):
        Base.metadata.create_all(bind=engine)
    yield


# ---------- 合成数据种子（直接落 ReconRecord，不经解析器） ----------


def _seed_batch(scope=("HT-SYN-001", "HT-SYN-002"), as_of=AS_OF, tolerance="0.01", with_sources=True) -> tuple[int, dict]:
    with SessionLocal() as db:
        batch = ReconBatch(
            name="引擎合成批次",
            as_of=as_of,
            tolerance=Decimal(tolerance),
            contract_scope=list(scope),
            status="draft",
        )
        db.add(batch)
        db.flush()
        sources: dict[str, int] = {}
        if with_sources:
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


def _dec(value) -> str:
    if value is None:
        return ""
    return f"{Decimal(str(value)):.2f}"


def _seed_balanced(batch_id: int, sources: dict) -> None:
    """两合同全部对平：001 当月欠费已到期、002 远期欠费未到期；认领精确命中核销。"""
    with SessionLocal() as db:
        _rec(db, batch_id, sources["writeoff"], "writeoff", row_no=1, contract="HT-SYN-001",
             doc="DZ-SYN-0001", fee_type="固定租金", ps=date(2026, 9, 1), pe=date(2026, 9, 30),
             occur=date(2026, 10, 6), a1=200, a2=200, a3=0, details={"bill_month": "2026-09"})
        _rec(db, batch_id, sources["writeoff"], "writeoff", row_no=2, contract="HT-SYN-001",
             doc="DZ-SYN-0002", fee_type="固定租金", ps=date(2026, 10, 1), pe=date(2026, 10, 31),
             a1=100, a2=None, a3=100, details={"bill_month": "2026-10"})
        _rec(db, batch_id, sources["writeoff"], "writeoff", row_no=3, contract="HT-SYN-002",
             doc="DZ-SYN-0003", fee_type="综管费", ps=date(2026, 8, 1), pe=date(2026, 8, 31),
             a1=300, a2=None, a3=300, details={"bill_month": "2026-08"})
        _rec(db, batch_id, sources["claim"], "claim", row_no=1, doc="SK-SYN-0001",
             occur=date(2026, 10, 6), a1=200, a2=200,
             details={"txn_time": "2026-10-06 10:00:00", "audit_time": "2026-10-06 12:00:00"})
        _rec(db, batch_id, sources["arrear"], "arrear", row_no=1, contract="HT-SYN-001",
             ps=date(2026, 10, 1), pe=date(2026, 10, 31), due=date(2026, 11, 15),
             a1=100, a2=60, a3=40, details={"bill_month": "2026-10"})
        _rec(db, batch_id, sources["arrear"], "arrear", row_no=2, contract="HT-SYN-002",
             ps=date(2026, 8, 1), pe=date(2026, 8, 31), due=date(2026, 12, 20),
             a1=300, a2=200, a3=100, details={"bill_month": "2026-08"})
        _rec(db, batch_id, sources["bill"], "bill", row_no=1, contract="HT-SYN-001",
             doc="ZD-SYN-001", due=date(2026, 11, 15), a1=100, details={"bill_month": "2026-10"})
        _rec(db, batch_id, sources["bill"], "bill", row_no=2, contract="HT-SYN-002",
             doc="ZD-SYN-002", due=date(2026, 12, 20), a1=300, details={"bill_month": "2026-08"})
        db.commit()


def _seed_terms(batch_id: int, contract: str, terms: dict) -> None:
    with SessionLocal() as db:
        db.add(ReconTerm(batch_id=batch_id, contract_no=contract, terms=terms))
        db.commit()


def _upd_record(record_id: int, **fields) -> None:
    with SessionLocal() as db:
        row = db.get(ReconRecord, record_id)
        for key, value in fields.items():
            if key in ("a1", "a2", "a3", "a4"):
                setattr(row, f"amount_{key[1]}", None if value is None else Decimal(str(value)))
            elif key in ("ps", "pe"):
                setattr(row, "fee_period_start" if key == "ps" else "fee_period_end", value)
            else:
                setattr(row, key, value)
        db.commit()


def _run(batch_id: int):
    from app.modules.finance_recon.recon_engine import run_checks

    with SessionLocal() as db:
        return run_checks(db, batch_id)


def _checks_of(result: dict, key: str) -> list[dict]:
    return [c for c in result["checks"] if c["key"] == key]


def _check_scope(result: dict, key: str, scope: str) -> dict:
    hits = [c for c in _checks_of(result, key) if c["scope"] == scope]
    assert hits, f"缺少检查 {key}@{scope}：{[c['key'] + '@' + c['scope'] for c in result['checks']]}"
    return hits[0]


def _issue_rows(batch_id: int, category: str | None = None) -> list[dict]:
    with SessionLocal() as db:
        q = db.query(ReconIssue).filter(ReconIssue.batch_id == batch_id)
        if category:
            q = q.filter(ReconIssue.category == category)
        return [
            {
                "id": r.id,
                "origin": r.origin,
                "category": r.category,
                "severity": r.severity,
                "status": r.status,
                "active": r.active,
                "note": r.note,
                "last_seen": r.last_seen.isoformat(timespec="seconds") if r.last_seen else None,
                "fingerprint": r.fingerprint,
                "amount_impact": None if r.amount_impact is None else str(r.amount_impact),
                "evidence": r.evidence or [],
            }
            for r in q.order_by(ReconIssue.id).all()
        ]


def _match_rows(batch_id: int) -> list[dict]:
    with SessionLocal() as db:
        return [
            {
                "id": r.id,
                "claim_record_id": r.claim_record_id,
                "writeoff_record_id": r.writeoff_record_id,
                "allocated": None if r.allocated_amount is None else str(r.allocated_amount),
                "status": r.status,
                "origin": r.origin,
                "note": r.note,
            }
            for r in db.query(ReconMatch).filter(ReconMatch.batch_id == batch_id).order_by(ReconMatch.id).all()
        ]


def _batch(batch_id: int) -> dict:
    with SessionLocal() as db:
        b = db.get(ReconBatch, batch_id)
        return {
            "status": b.status,
            "needs_recheck": bool(b.needs_recheck),
            "error_summary": b.error_summary or "",
        }


# ---------- 勾稽主流程：对平样例 / 容差 / 总计 / unknown ----------


def test_check_balanced_batch_ready_and_all_pass():
    bid, sources = _seed_batch()
    _seed_balanced(bid, sources)
    result = _run(bid).as_dict()
    assert result["status"] == "ready"
    assert result["error_summary"] == ""
    assert _batch(bid)["status"] == "ready"
    assert _batch(bid)["needs_recheck"] is False

    for scope in ("HT-SYN-001", "HT-SYN-002", "总计"):
        assert _check_scope(result, "bill_vs_arrear", scope)["state"] == "pass"
        assert _check_scope(result, "receivable_identity", scope)["state"] == "pass"
    assert _check_scope(result, "bill_vs_arrear", "总计")["left"] == "400.00"
    assert _check_scope(result, "bill_vs_arrear", "总计")["right"] == "400.00"
    assert _check_scope(result, "receivable_identity", "总计")["left"] == "600.00"
    assert _check_scope(result, "receivable_identity", "总计")["right"] == "600.00"

    # 无口径参数：重构与违约金必须 unknown 并给出中文原因，绝不默认 30 天/1‰/20 日
    for scope in ("HT-SYN-001", "HT-SYN-002"):
        recon = _check_scope(result, "reconstruction", scope)
        assert recon["state"] == "unknown"
        assert "口径参数" in recon["message"]
        penalty = _check_scope(result, "penalty_reference", scope)
        assert penalty["state"] == "unknown"
    assert result["totals"]["penalty_reference_total"] == ""

    # 账龄（日历月分桶）与结算草稿为 info：到期/未到期拆分
    aging = _check_scope(result, "aging", "总计")
    assert aging["state"] == "info"
    assert result["totals"]["arrear_total"] == "400.00"
    assert result["totals"]["bill_total"] == "400.00"
    assert result["totals"]["writeoff_receivable_total"] == "600.00"
    assert result["totals"]["settlement_net_total"] == "400.00"

    # 认领 200 精确命中核销单据 DZ-SYN-0001：建议唯一；suggested 不计已覆盖（P2-3），
    # 未匹配余额只扣 confirmed → c1 仍有 200.00 未匹配余额，问题保持 pending
    matches = _match_rows(bid)
    assert len(matches) == 1
    assert matches[0]["status"] == "suggested" and matches[0]["origin"] == "algorithm"
    assert matches[0]["allocated"] == "200.00"
    unmatched = [i for i in _issue_rows(bid) if i["category"] == "claim_unmatched" and i["active"]]
    assert len(unmatched) == 1 and unmatched[0]["amount_impact"] == "200.00"
    assert result["totals"]["unmatched_claim_total"] == "200.00"
    assert result["totals"]["matched_confirmed_total"] == "0.00"
    assert result["totals"]["matched_suggested_total"] == "200.00"
    assert result["match_stats"]["suggested_upserted"] == 1


def test_tolerance_boundary_pass_and_beyond_creates_issue():
    # 容差内（差 0.01）→ pass；超容差（差 0.02）→ diff 且登记问题
    bid1, s1 = _seed_batch(scope=("HT-T01",))
    with SessionLocal() as db:
        _rec(db, bid1, s1["bill"], "bill", row_no=1, contract="HT-T01", doc="ZD-T1", a1=100)
        _rec(db, bid1, s1["arrear"], "arrear", row_no=1, contract="HT-T01", ps=date(2026, 10, 1),
             pe=date(2026, 10, 31), a1=100.01, details={"bill_month": "2026-10"})
        db.commit()
    result = _run(bid1).as_dict()
    assert _check_scope(result, "bill_vs_arrear", "HT-T01")["state"] == "pass"
    assert _issue_rows(bid1, "bill_arrear_mismatch") == []

    bid2, s2 = _seed_batch(scope=("HT-T01",))
    with SessionLocal() as db:
        _rec(db, bid2, s2["bill"], "bill", row_no=1, contract="HT-T01", doc="ZD-T1", a1=100)
        _rec(db, bid2, s2["arrear"], "arrear", row_no=1, contract="HT-T01", ps=date(2026, 10, 1),
             pe=date(2026, 10, 31), a1=100.02, details={"bill_month": "2026-10"})
        db.commit()
    result2 = _run(bid2).as_dict()
    check = _check_scope(result2, "bill_vs_arrear", "HT-T01")
    assert check["state"] == "diff"
    assert check["delta"] == "-0.02"  # 差值带符号：账单 − 欠费
    issues = _issue_rows(bid2, "bill_arrear_mismatch")
    assert len(issues) == 1
    assert issues[0]["status"] == "pending" and issues[0]["active"] is True
    assert issues[0]["amount_impact"] == "-0.02"
    # 总计检查同样 diff（单合同批次总计=合同）
    assert _check_scope(result2, "bill_vs_arrear", "总计")["state"] == "diff"


def test_out_of_scope_records_excluded_from_checks_and_totals():
    bid, sources = _seed_batch(scope=("HT-SYN-001",))
    with SessionLocal() as db:
        _rec(db, bid, sources["bill"], "bill", row_no=1, contract="HT-SYN-001", doc="ZD-1", a1=100)
        _rec(db, bid, sources["arrear"], "arrear", row_no=1, contract="HT-SYN-001",
             ps=date(2026, 10, 1), pe=date(2026, 10, 31), a1=100, details={"bill_month": "2026-10"})
        _rec(db, bid, sources["bill"], "bill", row_no=2, contract="HT-OTHER", doc="ZD-2", a1=999)
        _rec(db, bid, sources["arrear"], "arrear", row_no=2, contract="HT-OTHER",
             ps=date(2026, 10, 1), pe=date(2026, 10, 31), a1=50, details={"bill_month": "2026-10"})
        db.commit()
    result = _run(bid).as_dict()
    scopes = {c["scope"] for c in _checks_of(result, "bill_vs_arrear")}
    assert scopes == {"HT-SYN-001", "总计"}
    assert _check_scope(result, "bill_vs_arrear", "总计")["left"] == "100.00"
    assert result["totals"]["bill_total"] == "100.00"


def test_no_data_never_passes_unknown_states():
    """范围内合同无任何记录：账单=欠费、应收推算必须 unknown，绝不恒真通过。"""
    bid, sources = _seed_batch(scope=("HT-EMPTY",))
    result = _run(bid).as_dict()
    assert _check_scope(result, "bill_vs_arrear", "HT-EMPTY")["state"] == "unknown"
    assert _check_scope(result, "receivable_identity", "HT-EMPTY")["state"] == "unknown"

    # 只有账单、无欠费 → unknown（P1-3：不按 0 参与对比，不产生伪 mismatch）
    bid2, s2 = _seed_batch(scope=("HT-ONLY-BILL",))
    with SessionLocal() as db:
        _rec(db, bid2, s2["bill"], "bill", row_no=1, contract="HT-ONLY-BILL", doc="ZD-1", a1=88)
        db.commit()
    result2 = _run(bid2).as_dict()
    check2 = _check_scope(result2, "bill_vs_arrear", "HT-ONLY-BILL")
    assert check2["state"] == "unknown"
    assert "欠费" in check2["message"]
    assert _issue_rows(bid2, "bill_arrear_mismatch") == []


# ---------- 合同重构：三种计费口径，缺参 unknown ----------


def test_reconstruction_whole_month_actual_days_custom_lines():
    # whole_month：2026-01-01~2026-03-20 → 触及 3 个整月 × 1000 = 3000
    bid, sources = _seed_batch(scope=("HT-R1",))
    _seed_terms(bid, "HT-R1", {
        "contract_start": "2026-01-01", "contract_end": "2026-03-20",
        "billing_method": "whole_month",
        "reconstruction_cutoff": "contract_end",
        "monthly_fees": [{"fee_type": "固定租金", "monthly_amount": "1000.00"}],
    })
    with SessionLocal() as db:
        _rec(db, bid, sources["writeoff"], "writeoff", row_no=1, contract="HT-R1",
             doc="DZ-1", fee_type="固定租金", ps=date(2026, 1, 1), pe=date(2026, 3, 31), a1=3000)
        db.commit()
    result = _run(bid).as_dict()
    recon = _check_scope(result, "reconstruction", "HT-R1")
    assert recon["state"] == "pass"
    assert recon["left"] == "3000.00" and recon["right"] == "3000.00"

    # actual_days：2026-02-01~2026-02-14（14 天）× 112/28 = 56.00（按实际自然日，无 30 天默认）
    bid2, sources2 = _seed_batch(scope=("HT-R2",))
    _seed_terms(bid2, "HT-R2", {
        "contract_start": "2026-02-01", "contract_end": "2026-02-14",
        "billing_method": "actual_days",
        "reconstruction_cutoff": "contract_end",
        "monthly_fees": [{"fee_type": "固定租金", "monthly_amount": "112.00"}],
    })
    with SessionLocal() as db:
        _rec(db, bid2, sources2["writeoff"], "writeoff", row_no=1, contract="HT-R2",
             doc="DZ-2", fee_type="固定租金", ps=date(2026, 2, 1), pe=date(2026, 2, 28), a1=56)
        db.commit()
    result2 = _run(bid2).as_dict()
    recon2 = _check_scope(result2, "reconstruction", "HT-R2")
    assert recon2["state"] == "pass"
    assert recon2["right"] == "56.00"

    # custom_lines：完全来自用户明细（100 + 50 = 150），不引入任何内置口径
    bid3, sources3 = _seed_batch(scope=("HT-R3",))
    _seed_terms(bid3, "HT-R3", {
        "billing_method": "custom_lines",
        "reconstruction_cutoff": "contract_end",
        "custom_lines": [
            {"fee_type": "固定租金", "period_start": "2026-01-01", "period_end": "2026-01-31", "amount": "100.00"},
            {"fee_type": "综管费", "period_start": "2026-01-01", "period_end": "2026-01-31", "amount": "50.00"},
        ],
    })
    with SessionLocal() as db:
        _rec(db, bid3, sources3["writeoff"], "writeoff", row_no=1, contract="HT-R3",
             doc="DZ-3", fee_type="固定租金", ps=date(2026, 1, 1), pe=date(2026, 1, 31), a1=150)
        db.commit()
    result3 = _run(bid3).as_dict()
    recon3 = _check_scope(result3, "reconstruction", "HT-R3")
    assert recon3["state"] == "pass"
    assert recon3["right"] == "150.00"


def test_reconstruction_diff_and_partial_params_unknown():
    # 事实应收 2900 vs 重构 3000 → diff + 问题登记
    bid, sources = _seed_batch(scope=("HT-RD",))
    _seed_terms(bid, "HT-RD", {
        "contract_start": "2026-01-01", "contract_end": "2026-03-20",
        "billing_method": "whole_month",
        "reconstruction_cutoff": "contract_end",
        "monthly_fees": [{"fee_type": "固定租金", "monthly_amount": "1000.00"}],
    })
    with SessionLocal() as db:
        _rec(db, bid, sources["writeoff"], "writeoff", row_no=1, contract="HT-RD",
             doc="DZ-1", fee_type="固定租金", ps=date(2026, 1, 1), pe=date(2026, 3, 31), a1=2900)
        db.commit()
    result = _run(bid).as_dict()
    recon = _check_scope(result, "reconstruction", "HT-RD")
    assert recon["state"] == "diff"
    assert recon["delta"] == "-100.00"
    issues = _issue_rows(bid, "reconstruction_mismatch")
    assert len(issues) == 1 and issues[0]["amount_impact"] == "-100.00"

    # 只填计费方式、缺月费标准/合同起止：unknown 并列出缺失项
    bid2, _ = _seed_batch(scope=("HT-RP",))
    _seed_terms(bid2, "HT-RP", {"billing_method": "whole_month", "reconstruction_cutoff": "contract_end"})
    result2 = _run(bid2).as_dict()
    recon2 = _check_scope(result2, "reconstruction", "HT-RP")
    assert recon2["state"] == "unknown"
    for missing in ("合同起始日", "合同截止日", "月费标准"):
        assert missing in recon2["message"]

    # custom_lines 缺明细 → unknown
    bid3, _ = _seed_batch(scope=("HT-RC",))
    _seed_terms(bid3, "HT-RC", {"billing_method": "custom_lines", "reconstruction_cutoff": "contract_end"})
    result3 = _run(bid3).as_dict()
    assert "自定义" in _check_scope(result3, "reconstruction", "HT-RC")["message"]


# ---------- 违约金参考测算 / 到期未到期 / 结算草稿 ----------


def _penalty_batch(rate="1.00", start_rule="due_date", due_rule="source_due_date", due_day=None,
                   cap=None, waived=False, due=date(2026, 3, 10), as_of=AS_OF,
                   principals=(300,)):
    bid, sources = _seed_batch(scope=("HT-P1",), as_of=as_of)
    penalty: dict = {"waived": waived}
    if rate is not None:
        penalty["rate_per_day"] = rate
    if start_rule is not None:
        penalty["start_rule"] = start_rule
    if cap is not None:
        penalty["cap"] = cap
    terms = {
        "due_rule": due_rule,
        "reconstruction_cutoff": "contract_end",
        "penalty": penalty,
    }
    if due_day is not None:
        terms["due_day"] = due_day
    _seed_terms(bid, "HT-P1", terms)
    with SessionLocal() as db:
        for idx, principal in enumerate(principals, start=1):
            _rec(db, bid, sources["arrear"], "arrear", row_no=idx, contract="HT-P1",
                 ps=date(2026, 2, 1), pe=date(2026, 2, 28), due=due, a1=principal,
                 details={"bill_month": "2026-02"})
        db.commit()
    return bid


def test_penalty_reference_principal_times_rate_times_days():
    """P1-1：违约金 = 欠费本金 × 日费率 × 逾期天数；不同本金必须得出不同结果。"""
    days = Decimal((AS_OF - date(2026, 3, 10)).days)
    bid = _penalty_batch(principals=(300, 150))
    result = _run(bid).as_dict()
    penalty = _check_scope(result, "penalty_reference", "HT-P1")
    expected = Decimal(450) * Decimal("1.00") * days
    assert penalty["state"] == "info"
    assert penalty["right"] == f"{expected:.2f}"
    assert result["totals"]["penalty_reference_total"] == f"{expected:.2f}"

    # 同样天数、更小本金 → 违约金按本金等比缩小（证明本金参与，不是只乘天数）
    bid_small = _penalty_batch(principals=(300,))
    result_small = _run(bid_small).as_dict()
    expected_small = Decimal(300) * Decimal("1.00") * days
    assert _check_scope(result_small, "penalty_reference", "HT-P1")["right"] == f"{expected_small:.2f}"
    assert expected_small != expected

    # 未到期（缴费截止晚于对账日）→ 天数 0，不计违约金
    bid2 = _penalty_batch(due=date(2026, 12, 20))
    result2 = _run(bid2).as_dict()
    p2 = _check_scope(result2, "penalty_reference", "HT-P1")
    assert p2["state"] == "info"
    assert p2["right"] == "0.00"
    assert "未到期" in p2["message"]

    # cap 封顶（7 万级参考测算被 cap 压到 100）
    bid3 = _penalty_batch(cap="100.00")
    result3 = _run(bid3).as_dict()
    p3 = _check_scope(result3, "penalty_reference", "HT-P1")
    assert p3["right"] == "100.00"

    # waived → 0.00 + 豁免说明
    bid4 = _penalty_batch(waived=True)
    result4 = _run(bid4).as_dict()
    p4 = _check_scope(result4, "penalty_reference", "HT-P1")
    assert p4["right"] == "0.00"
    assert "豁免" in p4["message"]


def test_penalty_expense_month_day_and_period_end_rules():
    """四种起算/到期口径，全部按「本金 300 × 日费率 × 天数」测算。"""
    principal = Decimal(300)
    # expense_month_day + due_day=20：费用月 2026-02 → 到期 2026-02-20
    bid = _penalty_batch(due_rule="expense_month_day", due_day=20, due=None)
    result = _run(bid).as_dict()
    expected = principal * Decimal((AS_OF - date(2026, 2, 20)).days) * Decimal("1.00")
    assert _check_scope(result, "penalty_reference", "HT-P1")["right"] == f"{expected:.2f}"

    # due_day=31 在 2 月 → 收敛到月末 2026-02-28（日历事实，不是猜测）
    bid2 = _penalty_batch(due_rule="expense_month_day", due_day=31, due=None)
    result2 = _run(bid2).as_dict()
    expected2 = principal * Decimal((AS_OF - date(2026, 2, 28)).days) * Decimal("1.00")
    assert _check_scope(result2, "penalty_reference", "HT-P1")["right"] == f"{expected2:.2f}"

    # due_rule=period_end_next_day：到期 = 费用期结束次日 2026-03-01
    bid3 = _penalty_batch(due_rule="period_end_next_day", due=None)
    result3 = _run(bid3).as_dict()
    expected3 = principal * Decimal((AS_OF - date(2026, 3, 1)).days) * Decimal("1.00")
    assert _check_scope(result3, "penalty_reference", "HT-P1")["right"] == f"{expected3:.2f}"

    # start_rule=period_end_next_day（违约金起算独立于缴费到期）
    bid4 = _penalty_batch(start_rule="period_end_next_day")
    result4 = _run(bid4).as_dict()
    expected4 = principal * Decimal((AS_OF - date(2026, 3, 1)).days) * Decimal("1.00")
    assert _check_scope(result4, "penalty_reference", "HT-P1")["right"] == f"{expected4:.2f}"


def test_penalty_missing_params_unknown_and_settlement_excludes_unconfirmed():
    # 无违约金参数 → unknown；结算草稿仍 info
    bid, sources = _seed_batch(scope=("HT-S1",))
    _seed_terms(bid, "HT-S1", {
        "reconstruction_cutoff": "contract_end",
        "credits": [
            {"credit_type": "保证金抵扣", "amount": "30.00", "confirmed": True},
            {"credit_type": "预收抵扣", "amount": "20.00", "confirmed": False},
        ]
    })
    # 范围外合同的 confirmed 抵扣不得计入汇总（P3-6）
    _seed_terms(bid, "HT-OUT-OF-SCOPE", {
        "reconstruction_cutoff": "contract_end",
        "credits": [{"credit_type": "保证金抵扣", "amount": "999.00", "confirmed": True}],
    })
    with SessionLocal() as db:
        _rec(db, bid, sources["arrear"], "arrear", row_no=1, contract="HT-S1",
             ps=date(2026, 10, 1), pe=date(2026, 10, 31), due=date(2026, 11, 15), a1=100,
             details={"bill_month": "2026-10"})
        db.commit()
    result = _run(bid).as_dict()
    penalty = _check_scope(result, "penalty_reference", "HT-S1")
    assert penalty["state"] == "unknown"

    settlement = next(c for c in _checks_of(result, "settlement") if c["scope"] == "HT-S1")
    assert settlement["state"] == "info"
    row = result["settlement"][0]
    assert row["contract_no"] == "HT-S1"
    assert row["arrear_total"] == "100.00"
    assert row["due_receivable"] == "100.00" and row["undue_receivable"] == "0.00"
    assert row["confirmed_credits"] == "30.00"  # 只有 confirmed 抵扣进结算
    assert row["settlement_net"] == "70.00"
    assert row["penalty_state"] == "unknown"
    assert result["totals"]["settlement_net_total"] == "70.00"
    assert result["totals"]["confirmed_credits_total"] == "30.00"  # 范围外 999 不计入


# ---------- 异常场景分类：零金额/空费用、同秒、审核倒挂、期间错位 ----------


def test_zero_and_empty_fee_rows_flagged():
    """零金额核销看费用期核销 amount_2（P3-2）；应收为 0 但核销未填不再误报。"""
    bid, sources = _seed_batch(scope=("HT-Z1",))
    with SessionLocal() as db:
        _rec(db, bid, sources["writeoff"], "writeoff", row_no=1, contract="HT-Z1", doc="DZ-1",
             fee_type="", ps=date(2026, 10, 1), pe=date(2026, 10, 31), a1=0, a2=0, a3=0)
        _rec(db, bid, sources["writeoff"], "writeoff", row_no=2, contract="HT-Z1", doc="DZ-2",
             fee_type="固定租金", ps=date(2026, 10, 1), pe=date(2026, 10, 31), a4=20)
        _rec(db, bid, sources["writeoff"], "writeoff", row_no=3, contract="HT-Z1", doc="DZ-3",
             fee_type="固定租金", ps=date(2026, 10, 1), pe=date(2026, 10, 31), a2=0)
        _rec(db, bid, sources["writeoff"], "writeoff", row_no=4, contract="HT-Z1", doc="DZ-4",
             fee_type="固定租金", ps=date(2026, 10, 1), pe=date(2026, 10, 31), a1=0)
        _rec(db, bid, sources["claim"], "claim", row_no=1, doc="SK-1", a1=0, a2=0,
             details={"txn_time": "2026-11-06 10:00:00", "audit_time": "2026-11-06 11:00:00"})
        db.commit()
    result = _run(bid).as_dict()
    cats = {row["category"] for row in _issue_rows(bid)}
    assert "writeoff_zero_fee" in cats
    assert "writeoff_missing_fee_type" in cats
    assert "prepaid_residual" in cats
    assert "claim_zero_amount" in cats
    zero_rows = _issue_rows(bid, "writeoff_zero_fee")
    assert len(zero_rows) == 2  # 行 1（a2=0）与行 3（a2=0）；行 4 应收为 0 但核销未填 → 不再误报


def test_same_second_claims_pending():
    bid, sources = _seed_batch(scope=("HT-SS",))
    with SessionLocal() as db:
        _rec(db, bid, sources["claim"], "claim", row_no=1, doc="SK-1", a1=100, a2=100,
             details={"txn_time": "2026-11-06 10:00:00", "audit_time": "2026-11-06 12:00:00"})
        _rec(db, bid, sources["claim"], "claim", row_no=2, doc="SK-2", a1=50, a2=50,
             details={"txn_time": "2026-11-06 10:00:00", "audit_time": "2026-11-06 12:00:00"})
        db.commit()
    result = _run(bid).as_dict()
    issues = _issue_rows(bid, "claim_same_second")
    assert len(issues) == 1
    assert issues[0]["status"] == "pending" and issues[0]["severity"] == "warn"
    assert issues[0]["amount_impact"] == "150.00"

    # 不同秒不误报
    bid2, sources2 = _seed_batch(scope=("HT-SS",))
    with SessionLocal() as db:
        _rec(db, bid2, sources2["claim"], "claim", row_no=1, doc="SK-1", a1=100, a2=100,
             details={"txn_time": "2026-11-06 10:00:00", "audit_time": "2026-11-06 12:00:00"})
        _rec(db, bid2, sources2["claim"], "claim", row_no=2, doc="SK-2", a1=50, a2=50,
             details={"txn_time": "2026-11-06 10:00:01", "audit_time": "2026-11-06 12:00:00"})
        db.commit()
    _run(bid2)
    assert _issue_rows(bid2, "claim_same_second") == []


def test_audit_inversion_error_issue():
    bid, sources = _seed_batch(scope=("HT-AI",))
    with SessionLocal() as db:
        _rec(db, bid, sources["claim"], "claim", row_no=1, doc="SK-1", a1=100, a2=100,
             details={"txn_time": "2026-11-06 10:00:00", "audit_time": "2026-11-06 09:00:00"})
        db.commit()
    result = _run(bid).as_dict()
    issues = _issue_rows(bid, "claim_audit_inversion")
    assert len(issues) == 1
    assert issues[0]["severity"] == "error" and issues[0]["status"] == "pending"
    assert _check_scope(result, "claim_audit_inversion", "batch")["state"] == "diff"


def test_period_alignment_bill_month_vs_fee_period():
    bid, sources = _seed_batch(scope=("HT-PA",))
    with SessionLocal() as db:
        # 账单月 2026-10 vs 费用期 11 月 → 错位
        _rec(db, bid, sources["writeoff"], "writeoff", row_no=1, contract="HT-PA", doc="DZ-1",
             fee_type="固定租金", ps=date(2026, 11, 1), pe=date(2026, 11, 30), a1=100, a2=100, a3=0,
             details={"bill_month": "2026-10"})
        # 欠费行同样错位
        _rec(db, bid, sources["arrear"], "arrear", row_no=1, contract="HT-PA",
             ps=date(2026, 11, 1), pe=date(2026, 11, 30), a1=100, details={"bill_month": "2026-10"})
        db.commit()
    _run(bid)
    issues = _issue_rows(bid, "period_misaligned")
    assert len(issues) == 2

    # 对齐（账单月=费用期首月 或 末月）不误报
    bid2, sources2 = _seed_batch(scope=("HT-PA",))
    with SessionLocal() as db:
        _rec(db, bid2, sources2["writeoff"], "writeoff", row_no=1, contract="HT-PA", doc="DZ-1",
             fee_type="固定租金", ps=date(2026, 10, 1), pe=date(2026, 10, 31), a1=100, a2=100, a3=0,
             details={"bill_month": "2026-10"})
        db.commit()
    _run(bid2)
    assert _issue_rows(bid2, "period_misaligned") == []


# ---------- 账龄分桶与合计 ----------


def test_aging_buckets_and_unaged_rows():
    bid, sources = _seed_batch(scope=("HT-AG",))
    with SessionLocal() as db:
        _rec(db, bid, sources["arrear"], "arrear", row_no=1, contract="HT-AG", due=date(2026, 9, 15), a1=300)
        _rec(db, bid, sources["arrear"], "arrear", row_no=2, contract="HT-AG", a1=50)  # 无任何日期 → 无法归账龄
        db.commit()
    result = _run(bid).as_dict()
    aging = _check_scope(result, "aging", "总计")
    assert aging["state"] == "unknown"
    assert "1" in aging["message"]  # 1 行无法归账龄
    buckets = {b["key"]: b["total"] for b in result["aging"]["buckets"]}
    assert buckets["m1_3"] == "300.00"  # 2026-09 → 对账日 2026-11 差 2 个日历月
    assert result["aging"]["unaged_total"] == "50.00"
    assert result["aging"]["grand_total"] == "350.00"
    issues = _issue_rows(bid, "arrear_unaged")
    assert len(issues) == 1 and issues[0]["status"] == "pending"

    # 全部可归账龄 → info 且分桶合计=欠费合计
    bid2, sources2 = _seed_batch(scope=("HT-AG",))
    with SessionLocal() as db:
        _rec(db, bid2, sources2["arrear"], "arrear", row_no=1, contract="HT-AG", due=date(2026, 9, 15), a1=300)
        _rec(db, bid2, sources2["arrear"], "arrear", row_no=2, contract="HT-AG", due=date(2026, 12, 20), a1=50)
        db.commit()
    result2 = _run(bid2).as_dict()
    aging2 = _check_scope(result2, "aging", "总计")
    assert aging2["state"] == "info"
    buckets2 = {b["key"]: b["total"] for b in result2["aging"]["buckets"]}
    assert buckets2["undue"] == "50.00"
    assert result2["aging"]["unaged_total"] == "0.00"
    assert _issue_rows(bid2, "arrear_unaged") == []


# ---------- 保证金 / 预收差异：仅基于显式费用类型与参数 ----------


def test_deposit_duplicate_only_on_explicit_fee_type():
    bid, sources = _seed_batch(scope=("HT-DP",))
    with SessionLocal() as db:
        _rec(db, bid, sources["writeoff"], "writeoff", row_no=1, contract="HT-DP", doc="DZ-1",
             fee_type="保证金", ps=date(2026, 10, 1), pe=date(2026, 10, 31), a1=100, a2=100, a3=0)
        _rec(db, bid, sources["writeoff"], "writeoff", row_no=2, contract="HT-DP", doc="DZ-2",
             fee_type="保证金", ps=date(2026, 10, 1), pe=date(2026, 10, 31), a1=100, a2=100, a3=0)
        _rec(db, bid, sources["writeoff"], "writeoff", row_no=3, contract="HT-DP", doc="DZ-3",
             fee_type="固定租金", ps=date(2026, 10, 1), pe=date(2026, 10, 31), a1=50, a2=50, a3=0)
        db.commit()
    _run(bid)
    issues = _issue_rows(bid, "deposit_duplicate_writeoff")
    assert len(issues) == 1  # 只对显式「保证金」费用类型的重复名义核销报警
    assert "保证金" in issues[0]["evidence"][0] or issues[0]["evidence"]
    # 金额影响取费用期核销 amount_2 之和（100+100），不是应收 amount_1 口径变化
    assert Decimal(str(issues[0]["amount_impact"])) == Decimal("200.00")

    # 单行保证金、多行租金：不误报
    bid2, sources2 = _seed_batch(scope=("HT-DP",))
    with SessionLocal() as db:
        _rec(db, bid2, sources2["writeoff"], "writeoff", row_no=1, contract="HT-DP", doc="DZ-1",
             fee_type="保证金", ps=date(2026, 10, 1), pe=date(2026, 10, 31), a1=100, a2=100, a3=0)
        db.commit()
    _run(bid2)
    assert _issue_rows(bid2, "deposit_duplicate_writeoff") == []


# ---------- 匹配：精确引用 / 金额+日期 / 未匹配 / 生命周期 ----------


def _seed_matching_batch():
    bid, sources = _seed_batch(scope=("HT-M1",))
    with SessionLocal() as db:
        c1 = _rec(db, bid, sources["claim"], "claim", row_no=1, doc="SK-1", a1=88, a2=88,
                  occur=date(2026, 11, 6),
                  details={"txn_time": "2026-11-06 10:00:00", "audit_time": "2026-11-06 12:00:00"})
        w1 = _rec(db, bid, sources["writeoff"], "writeoff", row_no=1, contract="HT-M1", doc="SK-1",
                  fee_type="固定租金", ps=date(2026, 10, 1), pe=date(2026, 10, 31),
                  occur=date(2026, 11, 6), a1=88, a2=88, a3=0)
        c2 = _rec(db, bid, sources["claim"], "claim", row_no=2, doc="SK-9", a1=50, a2=50,
                  occur=date(2026, 11, 8),
                  details={"txn_time": "2026-11-08 10:00:00", "audit_time": "2026-11-08 12:00:00"})
        w2 = _rec(db, bid, sources["writeoff"], "writeoff", row_no=2, contract="HT-M1", doc="OT-7",
                  fee_type="固定租金", ps=date(2026, 10, 1), pe=date(2026, 10, 31),
                  occur=date(2026, 11, 8), a1=50, a2=50, a3=0)
        c3 = _rec(db, bid, sources["claim"], "claim", row_no=3, doc="SK-10", a1=5, a2=5,
                  occur=date(2026, 11, 9),
                  details={"txn_time": "2026-11-09 10:00:00", "audit_time": "2026-11-09 12:00:00"})
        db.commit()
    return bid, sources, {"c1": c1, "c2": c2, "c3": c3, "w1": w1, "w2": w2}


def test_matching_exact_doc_priority_and_amount_date_candidates():
    bid, sources, ids = _seed_matching_batch()
    result = _run(bid).as_dict()
    matches = {(m["claim_record_id"], m["writeoff_record_id"]): m for m in _match_rows(bid)}
    assert (ids["c1"], ids["w1"]) in matches
    assert matches[(ids["c1"], ids["w1"])]["origin"] == "algorithm"
    assert matches[(ids["c1"], ids["w1"])]["allocated"] == "88.00"
    assert (ids["c2"], ids["w2"]) in matches  # 金额+日期确定性候选
    # 无候选的认领保持 unmatched，绝不默认计入预收
    assert not any(pair[0] == ids["c3"] for pair in matches)
    # P2-3：suggested 不计已覆盖 → 三笔认领的未匹配问题均为 pending（confirmed/suggested 分列）
    unmatched = [i for i in _issue_rows(bid, "claim_unmatched") if i["active"]]
    assert len(unmatched) == 3
    assert sorted(Decimal(i["amount_impact"]) for i in unmatched) == [
        Decimal("5.00"), Decimal("50.00"), Decimal("88.00")
    ]
    assert result["totals"]["unmatched_claim_total"] == "143.00"
    assert result["totals"]["matched_confirmed_total"] == "0.00"
    assert result["totals"]["matched_suggested_total"] == "138.00"


def test_matching_rerun_unique_and_rejected_not_reproposed():
    bid, sources, ids = _seed_matching_batch()
    _run(bid)
    first = {(m["claim_record_id"], m["writeoff_record_id"]): m for m in _match_rows(bid)}
    _run(bid)
    second = {(m["claim_record_id"], m["writeoff_record_id"]): m for m in _match_rows(bid)}
    assert set(first) == set(second)  # 重跑建议唯一，不重复插入
    assert all(first[k]["id"] == second[k]["id"] for k in first)  # 同一行 upsert

    # 拒绝 (c2,w2) → 重跑不重提
    from app.modules.finance_recon import matching

    with SessionLocal() as db:
        match = db.get(ReconMatch, first[(ids["c2"], ids["w2"])]["id"])
        matching.patch_match(db, match.id, {"status": "rejected", "note": "金额巧合"})
    _run(bid)
    rows = {(m["claim_record_id"], m["writeoff_record_id"]): m for m in _match_rows(bid)}
    assert rows[(ids["c2"], ids["w2"])]["status"] == "rejected"
    assert rows[(ids["c2"], ids["w2"])]["origin"] == "algorithm" or rows[(ids["c2"], ids["w2"])]["note"] == "金额巧合"
    # 拒绝后重跑：不再出现新的建议行（三元组唯一）
    assert len([m for m in rows.values() if m["status"] == "suggested"]) == 1


def test_matching_confirmed_frozen_and_manual_not_overwritten():
    bid, sources, ids = _seed_matching_batch()
    _run(bid)
    rows = {(m["claim_record_id"], m["writeoff_record_id"]): m for m in _match_rows(bid)}
    w1_match_id = rows[(ids["c1"], ids["w1"])]["id"]
    w2_match_id = rows[(ids["c2"], ids["w2"])]["id"]

    from app.modules.finance_recon import matching

    with SessionLocal() as db:
        matching.patch_match(db, w1_match_id, {"status": "confirmed", "note": "已核对回单"})
        # 人工在机检建议上补说明 → 行归属转人工（origin=manual），重跑不被机检覆盖
        matching.patch_match(db, w2_match_id, {"note": "人工备注核对一致"})
    # 人工补一条建议（SK-10 ↔ W1）
    with SessionLocal() as db:
        created = matching.create_manual_match(
            db, bid, {"claim_record_id": ids["c3"], "writeoff_record_id": ids["w1"],
                      "allocated_amount": "5.00", "note": "人工挂接"}
        )
    manual_id = created["id"]
    _run(bid)
    after = {(m["claim_record_id"], m["writeoff_record_id"]): m for m in _match_rows(bid)}
    confirmed = after[(ids["c1"], ids["w1"])]
    assert confirmed["status"] == "confirmed" and confirmed["allocated"] == "88.00"
    assert confirmed["note"] == "已核对回单"
    touched = after[(ids["c2"], ids["w2"])]
    assert touched["id"] == w2_match_id and touched["origin"] == "manual"
    assert touched["allocated"] == "50.00" and touched["note"] == "人工备注核对一致"
    manual = after[(ids["c3"], ids["w1"])]
    assert manual["id"] == manual_id and manual["origin"] == "manual"
    assert manual["allocated"] == "5.00" and manual["status"] == "suggested"
    # P2-3：suggested 不计已覆盖 → 仅 suggested 覆盖的认领其未匹配问题保持 pending；
    # c1 已确认 88.00（余额 0）→ 问题解除（resolved + 不再活动，行保留）
    unmatched_active = [i for i in _issue_rows(bid, "claim_unmatched") if i["active"]]
    assert sorted(Decimal(i["amount_impact"]) for i in unmatched_active) == [
        Decimal("5.00"), Decimal("50.00")
    ]
    resolved = [i for i in _issue_rows(bid, "claim_unmatched") if not i["active"]]
    assert len(resolved) == 1 and resolved[0]["status"] == "resolved"
    assert _run(bid).as_dict()["match_stats"]["suggested_upserted"] == 0


def test_matching_partial_and_many_to_many_with_over_allocation_409():
    from app.modules.finance_recon import matching
    from app.modules.finance_recon.service import ReconConflictError

    bid, sources = _seed_batch(scope=("HT-MM",))
    with SessionLocal() as db:
        c1 = _rec(db, bid, sources["claim"], "claim", row_no=1, doc="SK-A", a1=100, a2=100,
                  occur=date(2026, 11, 6), details={"txn_time": "2026-11-06 10:00:00"})
        c2 = _rec(db, bid, sources["claim"], "claim", row_no=2, doc="SK-B", a1=50, a2=50,
                  occur=date(2026, 11, 7), details={"txn_time": "2026-11-07 10:00:00"})
        wa = _rec(db, bid, sources["writeoff"], "writeoff", row_no=1, contract="HT-MM", doc="WA",
                  fee_type="固定租金", ps=date(2026, 10, 1), pe=date(2026, 10, 31), a1=60, a2=60, a3=0)
        wb = _rec(db, bid, sources["writeoff"], "writeoff", row_no=2, contract="HT-MM", doc="WB",
                  fee_type="综管费", ps=date(2026, 10, 1), pe=date(2026, 10, 31), a1=60, a2=60, a3=0)
        db.commit()
    with SessionLocal() as db:
        m1 = matching.create_manual_match(db, bid, {"claim_record_id": c1, "writeoff_record_id": wa,
                                                    "allocated_amount": "60.00"})
        matching.patch_match(db, m1["id"], {"status": "confirmed"})
        # 多对多：认领 100 已确认 60，再分 60 超认领侧 → 409
        m2 = matching.create_manual_match(db, bid, {"claim_record_id": c1, "writeoff_record_id": wb,
                                                    "allocated_amount": "60.00"})
        with pytest.raises(ReconConflictError) as exc:
            matching.patch_match(db, m2["id"], {"status": "confirmed"})
        assert "分摊" in str(exc.value)
        matching.patch_match(db, m2["id"], {"allocated_amount": "40.00"})
        matching.patch_match(db, m2["id"], {"status": "confirmed"})
        # 核销侧：WB 60 已确认 40，认领 C2 只剩 50，再分 40 → WB 侧 40+40=80 > 60 → 409
        m3 = matching.create_manual_match(db, bid, {"claim_record_id": c2, "writeoff_record_id": wb,
                                                    "allocated_amount": "40.00"})
        with pytest.raises(ReconConflictError):
            matching.patch_match(db, m3["id"], {"status": "confirmed"})
        matching.patch_match(db, m3["id"], {"allocated_amount": "20.00"})
        matching.patch_match(db, m3["id"], {"status": "confirmed"})
        rows = {(m["claim_record_id"], m["writeoff_record_id"]): m for m in _match_rows(bid)}
        assert rows[(c1, wb)]["allocated"] == "40.00"
        assert rows[(c2, wb)]["allocated"] == "20.00"
        # 零/负分摊、非法记录引用
        with pytest.raises(ValueError):
            matching.create_manual_match(db, bid, {"claim_record_id": c1, "writeoff_record_id": wa,
                                                   "allocated_amount": "0.00"})
        with pytest.raises(matching.ReconNotFoundError if hasattr(matching, "ReconNotFoundError") else LookupError):
            matching.create_manual_match(db, bid, {"claim_record_id": 999999, "writeoff_record_id": wa,
                                                   "allocated_amount": "1.00"})


def test_matching_out_of_scope_writeoff_rejected():
    from app.modules.finance_recon import matching

    bid, sources = _seed_batch(scope=("HT-IN",))
    with SessionLocal() as db:
        claim = _rec(db, bid, sources["claim"], "claim", row_no=1, doc="SK-1", a1=10, a2=10,
                     details={"txn_time": "2026-11-06 10:00:00"})
        outside = _rec(db, bid, sources["writeoff"], "writeoff", row_no=1, contract="HT-OUT", doc="SK-1",
                       fee_type="固定租金", ps=date(2026, 10, 1), pe=date(2026, 10, 31), a1=10, a2=10, a3=0)
        db.commit()
    result = _run(bid).as_dict()
    # 建议只找范围内核销：范围外的单据即使单据号精确相同也不参与
    assert _match_rows(bid) == []
    with pytest.raises(ValueError):
        with SessionLocal() as db:
            matching.create_manual_match(db, bid, {"claim_record_id": claim, "writeoff_record_id": outside,
                                                   "allocated_amount": "10.00"})


# ---------- 问题生命周期：指纹稳定 / 重跑收敛 / human 不触碰 ----------


def _seed_issue_batch():
    bid, sources = _seed_batch(scope=("HT-IL",))
    with SessionLocal() as db:
        claim = _rec(db, bid, sources["claim"], "claim", row_no=1, doc="SK-BAD", a1=100, a2=100,
                     occur=date(2026, 11, 6),
                     details={"txn_time": "2026-11-06 10:00:00", "audit_time": "2026-11-06 09:00:00"})
        db.commit()
    return bid, sources, claim


def test_issue_lifecycle_pending_resolved_reopen():
    bid, sources, claim = _seed_issue_batch()
    _run(bid)
    issues = _issue_rows(bid, "claim_audit_inversion")
    assert len(issues) == 1 and issues[0]["status"] == "pending" and issues[0]["active"] is True
    fp = issues[0]["fingerprint"]
    issue_id = issues[0]["id"]

    # 修正数据 → 重跑不再命中 → resolved + active=false（行保留）
    _upd_record(claim, details={"txn_time": "2026-11-06 10:00:00", "audit_time": "2026-11-06 12:00:00"})
    _run(bid)
    rows = _issue_rows(bid, "claim_audit_inversion")
    assert rows[0]["id"] == issue_id and rows[0]["status"] == "resolved" and rows[0]["active"] is False

    # 再次出现（同一条记录的同类异常回归）→ resolved 转 pending（同一行复用，指纹一致）
    _upd_record(claim, details={"txn_time": "2026-11-06 10:00:00", "audit_time": "2026-11-06 09:00:00"})
    _run(bid)
    rows = _issue_rows(bid, "claim_audit_inversion")
    assert rows[0]["id"] == issue_id and rows[0]["status"] == "pending" and rows[0]["active"] is True


def test_issue_confirmed_dismissed_frozen_and_human_untouched():
    bid, sources, claim = _seed_issue_batch()
    _run(bid)
    inv = _issue_rows(bid, "claim_audit_inversion")[0]

    from app.modules.finance_recon import matching

    with SessionLocal() as db:
        matching.patch_issue(db, inv["id"], {"status": "confirmed", "note": "倒挂属系统时钟偏差，已人工核对"})
        human = matching.create_issue(db, bid, {"category": "待补资料", "severity": "info",
                                                "note": "补 2026-09 认领明细"})
    human_id = human["id"]
    frozen = _issue_rows(bid, "claim_audit_inversion")[0]
    assert frozen["status"] == "confirmed" and frozen["note"].startswith("倒挂属")

    # 条件仍然命中：confirmed 仅恢复 active=true 并刷新 last_seen，状态/说明冻结
    _run(bid)
    rows = _issue_rows(bid, "claim_audit_inversion")
    assert rows[0]["status"] == "confirmed" and rows[0]["active"] is True
    assert rows[0]["note"] == "倒挂属系统时钟偏差，已人工核对"
    assert rows[0]["last_seen"] is not None  # P3-3：冻结行再命中也刷新 last_seen
    assert rows[0]["last_seen"] >= frozen["last_seen"]
    # human issue 机检永不触碰
    hrows = [r for r in _issue_rows(bid) if r["id"] == human_id]
    assert hrows[0]["origin"] == "human" and hrows[0]["status"] == "pending" and hrows[0]["active"] is True
    assert hrows[0]["note"] == "补 2026-09 认领明细"

    # 条件消失：confirmed/dismissed 仅置 active=false，状态与说明不变
    _upd_record(claim, details={"txn_time": "2026-11-06 10:00:00", "audit_time": "2026-11-06 12:00:00"})
    with SessionLocal() as db:
        other = db.query(ReconIssue).filter(ReconIssue.batch_id == bid, ReconIssue.id != inv["id"],
                                            ReconIssue.origin == "machine").first()
        assert other is not None
    _run(bid)
    rows = _issue_rows(bid, "claim_audit_inversion")
    assert rows[0]["status"] == "confirmed" and rows[0]["active"] is False
    assert rows[0]["note"] == "倒挂属系统时钟偏差，已人工核对"
    hrows = [r for r in _issue_rows(bid) if r["id"] == human_id]
    assert hrows[0]["active"] is True and hrows[0]["status"] == "pending"  # human 永不被机检触碰


def test_issue_fingerprint_stable_across_amount_changes():
    bid, sources = _seed_batch(scope=("HT-FP",))
    with SessionLocal() as db:
        rid = _rec(db, bid, sources["bill"], "bill", row_no=1, contract="HT-FP", doc="ZD-1", a1=100)
        _rec(db, bid, sources["arrear"], "arrear", row_no=1, contract="HT-FP", ps=date(2026, 10, 1),
             pe=date(2026, 10, 31), a1=100.02, details={"bill_month": "2026-10"})
        db.commit()
    _run(bid)
    first = _issue_rows(bid, "bill_arrear_mismatch")
    assert len(first) == 1
    fp = first[0]["fingerprint"]
    # 金额大改（0.02 → 100.02）指纹不变：同一问题行被更新而不是新建议
    _upd_record(rid, a1=0)
    _run(bid)
    second = _issue_rows(bid, "bill_arrear_mismatch")
    assert len(second) == 1
    assert second[0]["fingerprint"] == fp
    assert second[0]["id"] == first[0]["id"]
    assert second[0]["amount_impact"] == "-100.02"  # pending 重跑可更新金额影响


# ---------- terms：ready 修改 → needs_recheck + 导出守卫 ----------


def test_terms_needs_recheck_and_export_guard():
    from app.modules.finance_recon import service

    bid, sources = _seed_batch()
    _seed_balanced(bid, sources)
    _run(bid)
    assert _batch(bid)["needs_recheck"] is False

    with SessionLocal() as db:
        saved = service.put_terms(db, bid, "HT-SYN-001", {
            "contract_start": "2026-01-01", "contract_end": "2026-12-31",
            "billing_method": "whole_month",
            "reconstruction_cutoff": "contract_end",
            "monthly_fees": [{"fee_type": "固定租金", "monthly_amount": "1000.00"}],
        })
    assert saved["contract_no"] == "HT-SYN-001"
    assert saved["terms"]["monthly_fees"][0]["monthly_amount"] == "1000.00"  # Decimal → 字符串
    assert _batch(bid)["needs_recheck"] is True

    with pytest.raises(service.ReconConflictError) as exc:
        with SessionLocal() as db:
            service.assert_exportable(db, bid)
    assert "重新执行" in str(exc.value) or "重跑" in str(exc.value)

    # 重跑恢复 needs_recheck=false 且导出守卫放行；重构检查用上参数
    result = _run(bid).as_dict()
    assert _batch(bid)["needs_recheck"] is False
    with SessionLocal() as db:
        service.assert_exportable(db, bid)
    recon = _check_scope(result, "reconstruction", "HT-SYN-001")
    assert recon["state"] in ("diff", "pass")  # 参数已生效（不再 unknown）
    assert recon["state"] == "diff"  # 事实应收 600 vs 重构 12000（12 个月×1000，截止=合同截止日）

    # 草稿批次改参数不置位 needs_recheck
    bid2, _ = _seed_batch()
    with SessionLocal() as db:
        service.put_terms(db, bid2, "HT-SYN-001", {"billing_method": "whole_month",
                                                   "reconstruction_cutoff": "contract_end"})
    assert _batch(bid2)["needs_recheck"] is False


def test_terms_roundtrip_serialization_and_completeness():
    from app.modules.finance_recon import service

    bid, _ = _seed_batch(scope=("HT-TR",))
    with SessionLocal() as db:
        saved = service.put_terms(db, bid, "HT-TR", {
            "contract_start": "2026-01-01", "contract_end": "2026-01-31",
            "billing_method": "actual_days",
            "reconstruction_cutoff": "through_as_of",
            "monthly_fees": [{"fee_type": "固定租金", "monthly_amount": "310.00"}],
            "custom_lines": [{"fee_type": "综管费", "period_start": "2026-01-01",
                              "period_end": "2026-01-31", "amount": "50.00"}],
            "additional_charges": [{"fee_type": "保证金", "amount": "30000.00",
                                    "period_start": "2026-01-01", "period_end": "2026-01-31",
                                    "note": "入场押金"}],
            "credits": [{"credit_type": "保证金抵扣", "amount": "30.00", "note": "押金转租",
                         "confirmed": True}],
            "due_rule": "expense_month_day", "due_day": 20,
            "penalty": {"rate_per_day": "0.001", "start_rule": "due_date", "cap": "500.00"},
        })
        items = service.get_terms(db, bid)
    item = items["items"][0]
    assert item["contract_no"] == "HT-TR"
    t = item["terms"]
    assert t["reconstruction_cutoff"] == "through_as_of"
    assert t["monthly_fees"][0]["monthly_amount"] == "310.00"
    assert t["additional_charges"][0]["amount"] == "30000.00"
    assert t["custom_lines"][0]["amount"] == "50.00"
    assert t["credits"][0]["amount"] == "30.00" and t["credits"][0]["confirmed"] is True
    assert t["penalty"]["rate_per_day"] == "0.001" and t["penalty"]["cap"] == "500.00"
    assert t["due_rule"] == "expense_month_day" and t["due_day"] == 20
    assert item["complete"] is True and item["missing"] == []
    # JSON 里不允许出现 float 金额（金额一律字符串）
    import json as _json

    text = _json.dumps(t)
    for needle in ("310.00", "0.001"):
        assert f'"{needle}"' in text


# ---------- 状态失败回收：引擎异常 → failed + 摘要 → 重试恢复 ----------


def test_engine_failure_marks_failed_and_recovers(monkeypatch):
    from app.modules.finance_recon import recon_engine

    bid, sources = _seed_batch()
    _seed_balanced(bid, sources)

    def boom(*args, **kwargs):
        raise RuntimeError("合成勾稽爆炸")

    monkeypatch.setattr(recon_engine, "compute_all", boom)
    result = _run(bid).as_dict()
    assert result["status"] == "failed"
    assert "合成勾稽爆炸" in result["error_summary"]
    assert _batch(bid)["status"] == "failed"  # 无半提交：不留 checking
    # 失败后问题/匹配不落库
    assert _match_rows(bid) == [] and _issue_rows(bid) == []

    monkeypatch.undo()
    result2 = _run(bid).as_dict()
    assert result2["status"] == "ready", result2["error_summary"]
    assert _batch(bid)["status"] == "ready"
    assert _match_rows(bid) != []


def test_engine_failure_in_match_sync_marks_failed(monkeypatch):
    from app.modules.finance_recon import matching, recon_engine

    bid, sources = _seed_batch()
    _seed_balanced(bid, sources)

    def boom(*args, **kwargs):
        raise RuntimeError("匹配写入失败")

    monkeypatch.setattr(matching, "sync_suggestions", boom)
    result = _run(bid).as_dict()
    assert result["status"] == "failed"
    assert "匹配写入失败" in result["error_summary"]
    assert _issue_rows(bid) == []
    monkeypatch.undo()
    assert _run(bid).as_dict()["status"] == "ready"


def test_run_checks_precheck_and_transition_guards():
    from app.modules.finance_recon import service
    from app.modules.finance_recon.recon_engine import run_checks

    bid, _ = _seed_batch(with_sources=False)
    with pytest.raises(ValueError) as exc:  # 缺四类来源 → 400 语义
        with SessionLocal() as db:
            run_checks(db, bid)
    assert "缺少源文件" in str(exc.value)
    assert _batch(bid)["status"] == "draft"

    # checking 批次重入 → 409 语义（transition 守卫）
    bid2, sources = _seed_batch()
    _seed_balanced(bid2, sources)
    with SessionLocal() as db:
        service.transition_batch(db, bid2, "checking")
    with pytest.raises(service.ReconConflictError):
        with SessionLocal() as db:
            run_checks(db, bid2)


# ---------- 报告视图模型 ----------


def test_report_view_model_shape_and_no_sensitive_fields():
    from app.modules.finance_recon.reporting import build_report

    bid, sources = _seed_batch()
    _seed_balanced(bid, sources)
    _run(bid)  # 先执行检查：匹配建议与问题随检查落库
    with SessionLocal() as db:
        view = build_report(db, bid)
    data = view.as_dict()
    assert data["batch"]["id"] == bid and data["batch"]["as_of"] == AS_OF.isoformat()
    assert data["totals"]["bill_total"] == "400.00"
    assert data["totals"]["writeoff_receivable_total"] == "600.00"
    assert data["totals"]["claim_claimed_total"] == "200.00"
    assert isinstance(data["checks"], list) and data["checks"]
    assert data["issues"]["counts"]["pending"] == 1  # suggested 未确认 → 未匹配问题保持 pending
    assert data["matches"]["counts"]["suggested"] == 1
    assert data["unknown_reasons"], "缺参检查必须给出 unknown 原因"
    # 敏感字段零出现（户名/账号不入报告视图）
    import json as _json

    text = _json.dumps(data, ensure_ascii=False)
    for banned in ("payer_name", "payee_name", "account_tail", "付款账号", "收款账号"):
        assert banned not in text

    # draft 批次（未检查）也能出报告：不崩溃、检查为 unknown
    bid2, _ = _seed_batch()
    with SessionLocal() as db:
        view2 = build_report(db, bid2)
    data2 = view2.as_dict()
    assert data2["batch"]["status"] == "draft"
    assert all(c["state"] in ("unknown", "info") for c in data2["checks"])


# ---------- P1-2：商户级显式保证金核对（跨合同同一笔保证金必须检出） ----------


def test_deposit_reconciliation_merchant_level():
    # 三份合同各核销一次保证金 30000，认领侧只有一笔明确保证金收款 30000 → 差额 60000
    bid, sources = _seed_batch(scope=("HT-D1", "HT-D2", "HT-D3"))
    with SessionLocal() as db:
        for idx, contract in enumerate(("HT-D1", "HT-D2", "HT-D3"), start=1):
            _rec(db, bid, sources["writeoff"], "writeoff", row_no=idx, contract=contract,
                 doc=f"DZ-D{idx}", fee_type="保证金", ps=date(2026, 1, 1), pe=date(2026, 1, 31),
                 a1=30000, a2=30000, a3=0)
        _rec(db, bid, sources["claim"], "claim", row_no=1, doc="SK-D1", a1=30000, a2=30000,
             occur=date(2026, 1, 15),
             details={"txn_time": "2026-01-15 10:00:00", "audit_time": "2026-01-15 12:00:00",
                      "purpose": "保证金"})
        db.commit()
    result = _run(bid).as_dict()
    check = _check_scope(result, "deposit_reconciliation", "batch")
    assert check["state"] == "diff"
    assert check["left"] == "90000.00" and check["right"] == "30000.00"
    assert check["delta"] == "60000.00"
    issues = _issue_rows(bid, "deposit_mismatch")
    assert len(issues) == 1
    assert issues[0]["status"] == "pending" and issues[0]["amount_impact"] == "60000.00"

    # 合计一致（一笔认领覆盖三合同保证金）→ pass，不误报
    bid2, sources2 = _seed_batch(scope=("HT-D1", "HT-D2", "HT-D3"))
    with SessionLocal() as db:
        for idx, contract in enumerate(("HT-D1", "HT-D2", "HT-D3"), start=1):
            _rec(db, bid2, sources2["writeoff"], "writeoff", row_no=idx, contract=contract,
                 doc=f"DZ-D{idx}", fee_type="保证金", ps=date(2026, 1, 1), pe=date(2026, 1, 31),
                 a1=30000, a2=30000, a3=0)
        _rec(db, bid2, sources2["claim"], "claim", row_no=1, doc="SK-D9", a1=90000, a2=90000,
             occur=date(2026, 1, 15),
             details={"txn_time": "2026-01-15 10:00:00", "audit_time": "2026-01-15 12:00:00",
                      "memo": "三店保证金合并收款"})
        db.commit()
    result2 = _run(bid2).as_dict()
    assert _check_scope(result2, "deposit_reconciliation", "batch")["state"] == "pass"
    assert _issue_rows(bid2, "deposit_mismatch") == []

    # 有保证金核销但认领侧无明确保证金记录 → 缺明确认领不得 pass
    bid3, sources3 = _seed_batch(scope=("HT-D1",))
    with SessionLocal() as db:
        _rec(db, bid3, sources3["writeoff"], "writeoff", row_no=1, contract="HT-D1",
             doc="DZ-D1", fee_type="保证金", ps=date(2026, 1, 1), pe=date(2026, 1, 31),
             a1=30000, a2=30000, a3=0)
        _rec(db, bid3, sources3["claim"], "claim", row_no=1, doc="SK-D2", a1=30000, a2=30000,
             details={"txn_time": "2026-01-15 10:00:00", "audit_time": "2026-01-15 12:00:00",
                      "purpose": "货款"})
        db.commit()
    result3 = _run(bid3).as_dict()
    check3 = _check_scope(result3, "deposit_reconciliation", "batch")
    assert check3["state"] == "diff"
    assert "无明确" in check3["message"]
    assert _issue_rows(bid3, "deposit_mismatch")


def test_deposit_duplicate_same_contract_still_detected():
    # 同合同重复名义核销检查保留（与商户级核对并行）
    bid, sources = _seed_batch(scope=("HT-DP",))
    with SessionLocal() as db:
        _rec(db, bid, sources["writeoff"], "writeoff", row_no=1, contract="HT-DP", doc="DZ-1",
             fee_type="保证金", ps=date(2026, 10, 1), pe=date(2026, 10, 31), a1=100, a2=100, a3=0)
        _rec(db, bid, sources["writeoff"], "writeoff", row_no=2, contract="HT-DP", doc="DZ-2",
             fee_type="保证金", ps=date(2026, 10, 1), pe=date(2026, 10, 31), a1=100, a2=100, a3=0)
        db.commit()
    _run(bid)
    assert len(_issue_rows(bid, "deposit_duplicate_writeoff")) == 1


# ---------- P2-7：重构截止口径（through_as_of / contract_end）与一次性费用 ----------


def test_reconstruction_cutoff_through_as_of_vs_contract_end():
    """合同期跨对账日：through_as_of 截断到对账日，contract_end 含未到期费用。"""
    bid, sources = _seed_batch(scope=("HT-CL",))
    _seed_terms(bid, "HT-CL", {
        "contract_start": "2026-01-01", "contract_end": "2026-12-31",
        "billing_method": "whole_month", "reconstruction_cutoff": "through_as_of",
        "monthly_fees": [{"fee_type": "固定租金", "monthly_amount": "1000.00"}],
    })
    with SessionLocal() as db:
        _rec(db, bid, sources["writeoff"], "writeoff", row_no=1, contract="HT-CL",
             doc="DZ-1", fee_type="固定租金", ps=date(2026, 1, 1), pe=date(2026, 11, 30), a1=11000)
        db.commit()
    result = _run(bid).as_dict()
    recon = _check_scope(result, "reconstruction", "HT-CL")
    assert recon["state"] == "pass"  # 2026-01~2026-11 共 11 个月 × 1000 = 11000
    assert recon["right"] == "11000.00"

    # 同一批次改截止口径为 contract_end → 12 个月 × 1000 = 12000 → diff
    with SessionLocal() as db:
        row = db.query(ReconTerm).filter(ReconTerm.batch_id == bid).one()
        row.terms["reconstruction_cutoff"] = "contract_end"
        from sqlalchemy.orm.attributes import flag_modified

        flag_modified(row, "terms")
        db.commit()
    result2 = _run(bid).as_dict()
    recon2 = _check_scope(result2, "reconstruction", "HT-CL")
    assert recon2["state"] == "diff"
    assert recon2["right"] == "12000.00"
    assert recon2["delta"] == "-1000.00"


def test_additional_charges_included_in_all_billing_methods():
    """一次性费用（保证金/燃气等）在各计费方式下均纳入重构；未来费用按截止口径过滤。"""
    bid, sources = _seed_batch(scope=("HT-AC",))
    _seed_terms(bid, "HT-AC", {
        "contract_start": "2026-01-01", "contract_end": "2026-12-31",
        "billing_method": "whole_month", "reconstruction_cutoff": "through_as_of",
        "monthly_fees": [{"fee_type": "固定租金", "monthly_amount": "1000.00"}],
        "additional_charges": [
            {"fee_type": "保证金", "amount": "30000.00", "period_start": "2026-01-01",
             "period_end": "2026-01-31", "note": "入场押金"},
            {"fee_type": "燃气费", "amount": "500.00"},
            {"fee_type": "撤场违约金", "amount": "2000.00", "period_start": "2026-12-01",
             "period_end": "2026-12-31", "note": "未来费用"},
        ],
    })
    with SessionLocal() as db:
        _rec(db, bid, sources["writeoff"], "writeoff", row_no=1, contract="HT-AC",
             doc="DZ-1", fee_type="固定租金", ps=date(2026, 1, 1), pe=date(2026, 11, 30), a1=41500)
        db.commit()
    result = _run(bid).as_dict()
    recon = _check_scope(result, "reconstruction", "HT-AC")
    # through_as_of：11000 + 30000 + 500 = 41500；period_start 晚于对账日的 2000 不计入
    assert recon["state"] == "pass"
    assert recon["right"] == "41500.00"

    # custom_lines 也纳入一次性费用；未来自定义明细按截止口径排除
    bid2, sources2 = _seed_batch(scope=("HT-AD",))
    _seed_terms(bid2, "HT-AD", {
        "billing_method": "custom_lines", "reconstruction_cutoff": "through_as_of",
        "custom_lines": [
            {"fee_type": "固定租金", "period_start": "2026-01-01", "period_end": "2026-01-31",
             "amount": "100.00"},
            {"fee_type": "未来费用", "period_start": "2026-12-01", "period_end": "2026-12-31",
             "amount": "999.00"},
        ],
        "additional_charges": [{"fee_type": "燃气费", "amount": "30.00"}],
    })
    with SessionLocal() as db:
        _rec(db, bid2, sources2["writeoff"], "writeoff", row_no=1, contract="HT-AD",
             doc="DZ-2", fee_type="固定租金", ps=date(2026, 1, 1), pe=date(2026, 1, 31), a1=130)
        db.commit()
    result2 = _run(bid2).as_dict()
    recon2 = _check_scope(result2, "reconstruction", "HT-AD")
    assert recon2["state"] == "pass"  # 100 + 30 = 130；未来明细 999 不计入
    assert recon2["right"] == "130.00"


# ---------- P2-1：ever_ready 审计留痕（曾 ready 批次重跑 failed 也不可删/改/换源） ----------


def test_ever_ready_batch_guards_after_failed_rerun():
    from app.modules.finance_recon import importer
    from app.modules.finance_recon.schemas import ReconBatchPatch

    bid, sources = _seed_batch()
    _seed_balanced(bid, sources)
    assert _run(bid).as_dict()["status"] == "ready"
    # 模拟重跑失败（如人工改坏口径后引擎异常）
    with SessionLocal() as db:
        batch = db.get(ReconBatch, bid)
        batch.status = "failed"
        batch.error_summary = "勾稽检查失败：合成"
        db.commit()
    assert _batch(bid)["status"] == "failed"
    assert _batch(bid)["needs_recheck"] is False

    from app.modules.finance_recon import service

    with pytest.raises(service.ReconConflictError) as exc_del:
        with SessionLocal() as db:
            service.delete_batch(db, bid)
    assert "审计留痕" in str(exc_del.value)
    with pytest.raises(service.ReconConflictError) as exc_patch:
        with SessionLocal() as db:
            service.update_batch(db, bid, ReconBatchPatch(contract_scope=["HT-OTHER"]))
    assert "审计留痕" in str(exc_patch.value)
    with pytest.raises(service.ReconConflictError):
        with SessionLocal() as db:
            service.delete_source(db, bid, sources["claim"])
    # 上传路径的 ever_ready 409 由 router 与 importer 服务层双重守卫
    # （importer 直调用例见解析器测试），此处覆盖删除路径即可

    # terms 仍可修改并重跑恢复
    with SessionLocal() as db:
        service.put_terms(db, bid, "HT-SYN-001", {
            "billing_method": "whole_month", "reconstruction_cutoff": "contract_end",
            "monthly_fees": [{"fee_type": "固定租金", "monthly_amount": "1000.00"}],
        })
    assert _batch(bid)["needs_recheck"] is True
    assert _run(bid).as_dict()["status"] == "ready"
    assert _batch(bid)["needs_recheck"] is False


# ---------- P2-6：checking 超 2 小时中断回收；未超时仍 409 ----------


def test_stale_checking_recovered_on_next_check(monkeypatch):
    from datetime import timedelta

    from app.modules.finance_recon import service

    bid, sources = _seed_batch(with_sources=False)
    with SessionLocal() as db:
        service.transition_batch(db, bid, "checking")
    # 未超时 → 409 防并发
    with pytest.raises(service.ReconConflictError) as exc_fresh:
        with SessionLocal() as db:
            from app.modules.finance_recon.recon_engine import run_checks as _rc

            _rc(db, bid)
    assert "检查中" in str(exc_fresh.value)
    assert _batch(bid)["status"] == "checking"

    # 超 2 小时 → 先回收 failed（含「上次检查中断」），precheck 失败时批次保持 failed
    monkeypatch.setattr(service, "_now", lambda: datetime.now() + timedelta(hours=3))
    with pytest.raises(ValueError) as exc_pre:
        with SessionLocal() as db:
            from app.modules.finance_recon.recon_engine import run_checks as _rc2

            _rc2(db, bid)
    assert _batch(bid)["status"] == "failed"
    assert "上次检查中断" in _batch(bid)["error_summary"]

    # 回收后允许重试（补齐来源 → ready）
    monkeypatch.undo()
    with SessionLocal() as db:
        for kind in ("writeoff", "claim", "arrear", "bill"):
            db.add(ReconSource(batch_id=bid, kind=kind, original_name=f"{kind}.xlsx",
                               sha256=kind + "1" * 56, parse_status="parsed"))
        db.commit()
    assert _run(bid).as_dict()["status"] == "ready"
    assert _batch(bid)["error_summary"] == ""


# ---------- P2-2 / P2-8：确认前金额必填与 scope 复查 ----------


def test_confirm_allocation_rejected_when_side_amounts_missing():
    """核销 amount_1/2 均缺 → 禁止确认分摊（409），不得跳过上限；认领金额缺失同理。"""
    from app.modules.finance_recon import matching
    from app.modules.finance_recon.service import ReconConflictError

    bid, sources = _seed_batch(scope=("HT-NC",))
    with SessionLocal() as db:
        claim = _rec(db, bid, sources["claim"], "claim", row_no=1, doc="SK-NC", a1=50, a2=50,
                     details={"txn_time": "2026-11-06 10:00:00"})
        wo_no_amount = _rec(db, bid, sources["writeoff"], "writeoff", row_no=1, contract="HT-NC",
                            doc="DZ-NC", fee_type="固定租金", ps=date(2026, 10, 1),
                            pe=date(2026, 10, 31), a3=10)  # 应收/核销均未填
        db.commit()
    with SessionLocal() as db:
        created = matching.create_manual_match(
            db, bid, {"claim_record_id": claim, "writeoff_record_id": wo_no_amount,
                      "allocated_amount": "10.00"}
        )
        with pytest.raises(ReconConflictError) as exc:
            matching.patch_match(db, created["id"], {"status": "confirmed"})
    assert "核销记录" in str(exc.value) and "未填" in str(exc.value)

    # 认领金额缺失同样拒绝
    bid2, sources2 = _seed_batch(scope=("HT-NC",))
    with SessionLocal() as db:
        claim_none = _rec(db, bid2, sources2["claim"], "claim", row_no=1, doc="SK-N2",
                          a1=50, a2=None, details={"txn_time": "2026-11-06 10:00:00"})
        wo_ok = _rec(db, bid2, sources2["writeoff"], "writeoff", row_no=1, contract="HT-NC",
                     doc="DZ-OK", fee_type="固定租金", ps=date(2026, 10, 1),
                     pe=date(2026, 10, 31), a1=50, a2=50, a3=0)
        db.commit()
    with SessionLocal() as db:
        created2 = matching.create_manual_match(
            db, bid2, {"claim_record_id": claim_none, "writeoff_record_id": wo_ok,
                       "allocated_amount": "10.00"}
        )
        with pytest.raises(ReconConflictError) as exc2:
            matching.patch_match(db, created2["id"], {"status": "confirmed"})
    assert "认领记录" in str(exc2.value) and "未填" in str(exc2.value)


def test_confirm_match_rechecks_scope_after_narrowing():
    """P2-8：draft 阶段建匹配 → PATCH 收窄合同范围 → 确认时 409（范围外）。"""
    from app.modules.finance_recon import matching
    from app.modules.finance_recon.schemas import ReconBatchPatch
    from app.modules.finance_recon.service import ReconConflictError

    bid, sources = _seed_batch(scope=("HT-A",))
    with SessionLocal() as db:
        claim = _rec(db, bid, sources["claim"], "claim", row_no=1, doc="SK-A", a1=10, a2=10,
                     details={"txn_time": "2026-11-06 10:00:00"})
        wo = _rec(db, bid, sources["writeoff"], "writeoff", row_no=1, contract="HT-A",
                  doc="DZ-A", fee_type="固定租金", ps=date(2026, 10, 1), pe=date(2026, 10, 31),
                  a1=10, a2=10, a3=0)
        db.commit()
    with SessionLocal() as db:
        created = matching.create_manual_match(
            db, bid, {"claim_record_id": claim, "writeoff_record_id": wo, "allocated_amount": "10.00"}
        )
        # 范围收窄（draft 允许 PATCH，ever_ready 为 False）
        service = matching.service
        service.update_batch(db, bid, ReconBatchPatch(contract_scope=["HT-B"]))
        with pytest.raises(ReconConflictError) as exc:
            matching.patch_match(db, created["id"], {"status": "confirmed"})
    assert "已不在本批次范围" in str(exc.value)


# ---------- P2-5：同轮同指纹草稿合并 + DB 唯一约束兜底 ----------


def test_sync_issues_merges_duplicate_fingerprints_and_constraint_backstop():
    from app.modules.finance_recon.recon_engine import IssueDraft, _sync_issues
    from sqlalchemy.exc import IntegrityError

    bid, _ = _seed_batch()
    # 同一轮两条同指纹草稿 → 合并为一条（证据去重拼接），不重复插入
    with SessionLocal() as db:
        stats = _sync_issues(db, bid, [
            IssueDraft("cat_x", "record:1", "warn", ["证据一"], Decimal("1.00")),
            IssueDraft("cat_x", "record:1", "warn", ["证据一", "证据二"], Decimal("2.00")),
        ])
        db.commit()
    assert stats["created"] == 1
    rows = _issue_rows(bid, "cat_x")
    assert len(rows) == 1
    assert sorted(rows[0]["evidence"]) == ["证据一", "证据二"]
    assert rows[0]["amount_impact"] == "1.00"  # 首见草稿的金额影响保留

    # DB 兜底：同 (batch, origin, fingerprint) 重复插入点火唯一约束
    from app.modules.finance_recon.models import ReconIssue as ReconIssueModel

    with SessionLocal() as db:
        db.add(ReconIssueModel(batch_id=bid, origin="machine", fingerprint=rows[0]["fingerprint"],
                               category="cat_x"))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()


# ---------- P1-2/I1：保证金核对列口径（核销=amount_2、认领=amount_1）与单边差异 ----------


def test_deposit_reconciliation_column_mapping_and_one_sided():
    """核销侧取费用期核销（amount_2）、认领侧取收款金额（amount_1）；
    单边数据（仅认领 / 仅核销）也必须 diff + pending，不得 info/pass。"""
    # a1≠a2：核销已核销 10000、认领已收 30000 → 差 -20000，不得误判一致
    bid, sources = _seed_batch(scope=("HT-DX",))
    with SessionLocal() as db:
        _rec(db, bid, sources["writeoff"], "writeoff", row_no=1, contract="HT-DX",
             doc="DZ-DX", fee_type="保证金", ps=date(2026, 1, 1), pe=date(2026, 1, 31),
             a1=30000, a2=10000, a3=20000)
        _rec(db, bid, sources["claim"], "claim", row_no=1, doc="SK-DX", a1=30000, a2=30000,
             details={"txn_time": "2026-01-15 10:00:00", "audit_time": "2026-01-15 12:00:00",
                      "purpose": "保证金"})
        db.commit()
    result = _run(bid).as_dict()
    check = _check_scope(result, "deposit_reconciliation", "batch")
    assert check["state"] == "diff"
    assert check["left"] == "10000.00" and check["right"] == "30000.00"
    assert check["delta"] == "-20000.00"
    issues = _issue_rows(bid, "deposit_mismatch")
    assert len(issues) == 1 and issues[0]["status"] == "pending"
    assert issues[0]["amount_impact"] == "-20000.00"

    # 仅认领（无明确保证金核销）→ diff + pending，不得 info/pass
    bid2, sources2 = _seed_batch(scope=("HT-DY",))
    with SessionLocal() as db:
        _rec(db, bid2, sources2["writeoff"], "writeoff", row_no=1, contract="HT-DY",
             doc="DZ-DY", fee_type="固定租金", ps=date(2026, 1, 1), pe=date(2026, 1, 31),
             a1=100, a2=100, a3=0)
        _rec(db, bid2, sources2["claim"], "claim", row_no=1, doc="SK-DY", a1=30000, a2=30000,
             details={"txn_time": "2026-01-15 10:00:00", "audit_time": "2026-01-15 12:00:00",
                      "purpose": "保证金"})
        db.commit()
    result2 = _run(bid2).as_dict()
    check2 = _check_scope(result2, "deposit_reconciliation", "batch")
    assert check2["state"] == "diff"
    assert check2["left"] == "0.00" and check2["right"] == "30000.00"
    assert _issue_rows(bid2, "deposit_mismatch")
    assert _issue_rows(bid2, "deposit_mismatch")[0]["status"] == "pending"
