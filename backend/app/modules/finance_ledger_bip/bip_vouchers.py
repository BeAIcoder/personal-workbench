"""凭证列表入库与多来源（凭证列表 / 辅助明细账）合并（由 service.py 机械拆分）。

凭证按（核算账簿, 期间, 凭证号）唯一：同来源重导幂等跳过；跨来源按金额签名
合并补写科目与摘要、不重复落金额（未匹配/歧义/冲突保留可重试，不标记已合并）。
"""
from __future__ import annotations

from typing import Any

import pandas as pd
from sqlalchemy.orm import Session

from .bip_aux import _entry_from_row
from .bip_common import (
    MAX_WARNINGS,
    TYPE_LABELS,
    TYPE_VOUCHER,
    _r2,
    _text,
    _to_date_text,
)
from .models import BipBatch, BipEntry, BipVoucher

# ---------------- 多来源合并 ----------------

def _voucher_sources(voucher: BipVoucher) -> set[str]:
    """凭证已合并的来源类型：优先读 sources 列；旧数据按分录 source_type 反推。"""
    raw = (voucher.sources or "").strip()
    if raw:
        return {s.strip() for s in raw.split(",") if s.strip()}
    return {e.source_type for e in voucher.entries if e.source_type}


def _pick_merge_target(
    candidates: list[BipEntry], src: dict[str, Any]
) -> tuple[BipEntry | None, str]:
    """同金额候选里选合并目标：唯一空科目 → 命中；多空科目按摘要消歧；
    仍多个 → ambiguous（不按顺序硬配）。返回 (target, state)，state:
    hit / filled（候选都已有科目）/ ambiguous / none（无同金额候选）。
    """
    if not candidates:
        return None, "none"
    empty = [e for e in candidates if not (e.subject_code or "").strip()]
    if not empty:
        return None, "filled"
    if len(empty) == 1:
        return empty[0], "hit"
    summary = (src.get("summary") or "").strip()
    if summary:
        same = [e for e in empty if (e.summary or "").strip() == summary]
        if len(same) == 1:
            return same[0], "hit"
    return None, "ambiguous"


def _merge_entries(
    db: Session,
    batch: BipBatch,
    voucher: BipVoucher,
    group: dict[str, Any],
    file_type: str,
    warnings: list[str],
) -> tuple[int, int, int, int]:
    """把新来源的分录合并进已有凭证：只补空缺，不新增金额行（台账 L-070）。

    按（借, 贷）金额签名匹配已有分录：同金额且科目为空的行补写科目/摘要；
    同金额多候选时用摘要二次匹配，仍歧义则记 ambiguous 并告警，绝不按顺序硬配；
    科目已存在则保留原值（编码不一致记冲突告警）；金额对不上记未匹配告警。
    无论哪种情况都不追加新金额行，因此同一凭证不会因多来源导入而翻倍。

    返回（补写分录数（按分录去重）, 未匹配分录数, 歧义分录数, 科目冲突数）。
    """
    entries = list(voucher.entries)
    if not entries:
        for i, src in enumerate(group["entries"], 1):
            db.add(BipEntry(voucher_id=voucher.id, batch_id=batch.id, line_no=i,
                            source_type=file_type, **src))
        return len(group["entries"]), 0, 0, 0

    pool: dict[tuple[float, float], list[BipEntry]] = {}
    for e in entries:
        pool.setdefault((_r2(e.debit), _r2(e.credit)), []).append(e)

    changed_ids: set[int] = set()
    unmatched = ambiguous = conflicts = 0
    for src in group["entries"]:
        sig = (_r2(src["debit"]), _r2(src["credit"]))
        candidates = pool.get(sig) or []
        target, state = _pick_merge_target(candidates, src)
        if state == "none":
            unmatched += 1
            continue
        if state == "ambiguous":
            ambiguous += 1
            continue
        if state == "filled":
            # 同金额分录已带科目：编码不同只告警不覆盖，编码相同仅补空名称
            if src["subject_code"] and all(
                (e.subject_code or "").strip() != src["subject_code"] for e in candidates
            ):
                conflicts += 1
            elif src["subject_name"]:
                for e in candidates:
                    if (e.subject_code or "").strip() == src["subject_code"] and not (e.subject_name or "").strip():
                        e.subject_name = src["subject_name"]
                        changed_ids.add(id(e))
                        break
            if len(candidates) == 1 and not (candidates[0].summary or "").strip() and src["summary"]:
                candidates[0].summary = src["summary"]
                changed_ids.add(id(candidates[0]))
            continue
        # state == "hit"：唯一（或摘要消歧后唯一）的空科目候选
        if target is None:
            continue
        candidates.remove(target)
        if src["subject_code"]:
            target.subject_code = src["subject_code"]
            target.subject_name = src["subject_name"]
            changed_ids.add(id(target))
        if not (target.summary or "").strip() and src["summary"]:
            target.summary = src["summary"]
            changed_ids.add(id(target))

    if not voucher.voucher_full_no and group["full_no"]:
        voucher.voucher_full_no = group["full_no"]
    if group["entries"] and not (voucher.summary or "").strip():
        voucher.summary = group["entries"][0]["summary"]
    label = TYPE_LABELS.get(file_type, file_type)
    if unmatched:
        warnings.append(
            f"凭证 {voucher.ledger}/{voucher.period}/{voucher.voucher_no} 有 {unmatched} 条"
            f"{label}分录金额与已入库凭证不匹配，未合并（未新增金额行；"
            "该来源未标记为已合并，可用更正后的文件重试）"
        )
    if ambiguous:
        warnings.append(
            f"凭证 {voucher.ledger}/{voucher.period}/{voucher.voucher_no} 有 {ambiguous} 条"
            f"{label}分录同金额存在多个候选科目且摘要无法区分，未合并"
            f"（ambiguous_entries={ambiguous}；该来源保留可重试）"
        )
    if conflicts:
        warnings.append(
            f"凭证 {voucher.ledger}/{voucher.period}/{voucher.voucher_no} 有 {conflicts} 条"
            f"分录科目与已入库数据不一致，保留原值（未覆盖；该来源保留可重试）"
        )
    return len(changed_ids), unmatched, ambiguous, conflicts


# ---------------- 凭证类入库 ----------------

def _import_voucher_like(
    db: Session, batch: BipBatch, df: pd.DataFrame, mapping: dict[str, str], file_type: str
) -> dict[str, Any]:
    """凭证列表 / 辅助明细账入库：按（核算账簿, 期间, 凭证号）聚合分录。

    - 新建凭证：借贷平衡校验（不平衡记 warning 不阻断）；
    - 已存在且来源不同：按金额签名合并补写科目/摘要，不新增重复凭证或金额行；
      同金额多候选先按摘要消歧，仍歧义计入 ambiguous_entries 并告警；
    - 已存在且来源相同：幂等跳过并计数（同文件重导不翻倍）；
    - 合并有未匹配/歧义/科目冲突时不把该来源记为已合并，可用更正文件重试补齐。
    """
    date_c, ledger_c, vno_c = mapping["date"], mapping["ledger"], mapping["voucher_no"]
    summary_c = mapping.get("summary")
    debit_c, credit_c = mapping.get("debit"), mapping.get("credit")
    full_c = mapping.get("voucher_full_no")
    scode_c, sname_c = mapping.get("subject_code"), mapping.get("subject_name")
    subject_c = mapping.get("subject")

    if file_type == TYPE_VOUCHER:
        # 用友凭证列表的续行不重复凭证日期/凭证号（仅首行填写）：按账簿分组前向填充，
        # 否则续行分录会被当作无效行跳过（外部验证第 3 章 B1 缺口修复的一部分）。
        # 核算账簿列本身也可能在续行为空：先对账簿 ffill，再按账簿分组填日期/凭证号。
        df = df.copy()
        df[ledger_c] = df[ledger_c].ffill()
        df[[date_c, vno_c]] = df.groupby(ledger_c, dropna=False)[[date_c, vno_c]].ffill()
        if full_c:
            df[full_c] = df.groupby(ledger_c, dropna=False)[full_c].ffill()

    # 判重/聚合键必须含期间：真实 BIP 导出是多个月份，凭证号在各月重复
    # （如 1–10 月都有「记-1」）。只用（账簿, 凭证号）会把十个月合成一张凭证，
    # 且下月文件重导时被误判为重复而静默丢弃（BUGfix BIP-01）。
    groups: dict[tuple[str, str, str], dict[str, Any]] = {}
    order: list[tuple[str, str, str]] = []
    warnings: list[str] = []
    skipped_rows = invalid_amount_rows = 0
    for idx, row in df.iterrows():
        ledger, vno = _text(row[ledger_c]), _text(row[vno_c])
        dtext = _to_date_text(row[date_c])
        if not ledger or not vno or not dtext:
            skipped_rows += 1
            continue
        try:
            entry = _entry_from_row(
                row, summary_c=summary_c, scode_c=scode_c, sname_c=sname_c,
                subject_c=subject_c, debit_c=debit_c, credit_c=credit_c,
            )
        except ValueError as exc:
            skipped_rows += 1
            invalid_amount_rows += 1
            warnings.append(f"数据第 {idx + 1} 行（凭证 {vno}）{exc}，已跳过该行")
            continue
        key = (ledger, dtext[:7], vno)
        g = groups.get(key)
        if g is None:
            g = {"date": dtext, "full_no": "", "entries": []}
            groups[key] = g
            order.append(key)
        if full_c and not g["full_no"]:
            g["full_no"] = _text(row[full_c])
        g["entries"].append(entry)

    existing: dict[tuple[str, str, str], BipVoucher] = {}
    periods = {key[1] for key in groups}
    if periods:
        for v in db.query(BipVoucher).filter(BipVoucher.period.in_(periods)).all():
            existing[(v.ledger, v.period, v.voucher_no)] = v

    inserted_v = skipped_v = inserted_e = merged_v = merged_e = ambiguous_e = 0
    for key in order:
        g = groups[key]
        voucher = existing.get(key)
        if voucher is not None:
            if file_type in _voucher_sources(voucher):
                skipped_v += 1
                continue
            changed, unmatched, ambiguous, conflicts = _merge_entries(
                db, batch, voucher, g, file_type, warnings)
            merged_v += 1
            merged_e += changed
            ambiguous_e += ambiguous
            if unmatched == 0 and ambiguous == 0 and conflicts == 0:
                # 只有全部候选都被现有数据覆盖（含零改动）才把来源记为已合并；
                # 匹配失败/歧义/冲突保留未标记，允许用更正后的文件重试补齐。
                voucher.sources = ",".join(sorted(_voucher_sources(voucher) | {file_type}))
            continue
        debit_sum = _r2(sum(e["debit"] for e in g["entries"]))
        credit_sum = _r2(sum(e["credit"] for e in g["entries"]))
        if abs(debit_sum - credit_sum) > 0.005:
            warnings.append(
                f"凭证 {key[0]}/{key[1]}/{key[2]} 借贷不平衡："
                f"借方合计 {debit_sum:.2f}，贷方合计 {credit_sum:.2f}"
            )
        voucher = BipVoucher(
            batch_id=batch.id,
            period=key[1],
            voucher_date=g["date"],
            ledger=key[0],
            voucher_no=key[2],
            voucher_full_no=g["full_no"],
            summary=g["entries"][0]["summary"] if g["entries"] else "",
            sources=file_type,
        )
        db.add(voucher)
        db.flush()
        for i, e in enumerate(g["entries"], 1):
            db.add(BipEntry(voucher_id=voucher.id, batch_id=batch.id, line_no=i,
                            source_type=file_type, **e))
        inserted_v += 1
        inserted_e += len(g["entries"])
        existing[key] = voucher

    if len(warnings) > MAX_WARNINGS:
        warnings = warnings[:MAX_WARNINGS] + [f"……其余 {len(warnings) - MAX_WARNINGS} 条告警从略"]
    return {
        "inserted_vouchers": inserted_v,
        "skipped_vouchers": skipped_v,
        "inserted_entries": inserted_e,
        "merged_vouchers": merged_v,
        "merged_entries": merged_e,
        "ambiguous_entries": ambiguous_e,
        "skipped_rows": skipped_rows,
        "invalid_amount_rows": invalid_amount_rows,
        "warnings": warnings,
    }
