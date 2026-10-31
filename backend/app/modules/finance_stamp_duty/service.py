# -*- coding: utf-8 -*-
"""印花税计税服务：印花税法税目税率表 + 六税两费减半 + 申报表底稿导出。"""
from __future__ import annotations

from pathlib import Path

from ...config import get_finance_data_dir
from ...database import SessionLocal

# ---------- 税目税率表（印花税法 2022-07-01 施行，rate 为千分比） ----------

STAMP_DUTY_RATES: list[dict] = [
    {"tax_item": "借款合同", "rate": 0.05, "unit": "‰", "note": "借款金额的万分之零点五"},
    {"tax_item": "融资租赁合同", "rate": 0.05, "unit": "‰", "note": "租金的万分之零点五"},
    {"tax_item": "买卖合同（动产买卖）", "rate": 0.3, "unit": "‰", "note": "价款的万分之三"},
    {"tax_item": "承揽合同", "rate": 0.3, "unit": "‰", "note": "报酬的万分之三"},
    {"tax_item": "建设工程合同", "rate": 0.3, "unit": "‰", "note": "价款的万分之三"},
    {"tax_item": "运输合同", "rate": 0.3, "unit": "‰", "note": "运输费用的万分之三"},
    {"tax_item": "技术合同", "rate": 0.3, "unit": "‰", "note": "价款、报酬或使用费的万分之三"},
    {"tax_item": "租赁合同", "rate": 1.0, "unit": "‰", "note": "租金的千分之一"},
    {"tax_item": "保管合同", "rate": 1.0, "unit": "‰", "note": "保管费的千分之一"},
    {"tax_item": "仓储合同", "rate": 1.0, "unit": "‰", "note": "仓储费的千分之一"},
    {"tax_item": "财产保险合同", "rate": 1.0, "unit": "‰", "note": "保险费的千分之一"},
    {"tax_item": "产权转移书据（不含证券交易）", "rate": 0.5, "unit": "‰", "note": "价款的万分之五"},
    {"tax_item": "营业账簿（实收资本+资本公积）", "rate": 0.25, "unit": "‰", "note": "万分之二点五"},
]


def list_tax_items() -> list[dict]:
    """税目税率表（前端下拉与计算共用）。"""
    return list(STAMP_DUTY_RATES)


def _item_base(name: str) -> str:
    """税目名归一：去掉括号说明（「买卖合同（动产买卖）」→「买卖合同」）。"""
    text = str(name or "").strip()
    for sep in ("（", "("):
        if sep in text:
            text = text.split(sep, 1)[0]
    return text.strip()


def get_rate(tax_item: str) -> float:
    """按税目名取千分比税率；支持带/不带括号说明的写法；未知税目抛 ValueError。

    来源：印花税法税目税率表。电子税局申报表税目写作「买卖合同」「产权转移书据」，
    本地表为「买卖合同（动产买卖）」「产权转移书据（不含证券交易）」，
    两者必须都能命中（真实申报表对拍发现）。
    """
    target = str(tax_item or "").strip()
    base = _item_base(target)
    for row in STAMP_DUTY_RATES:
        if row["tax_item"] == target:
            return row["rate"]
    for row in STAMP_DUTY_RATES:
        if _item_base(row["tax_item"]) == base:
            return row["rate"]
    raise ValueError(f"未知税目：{tax_item}（请从税目税率表选择）")


def calc_filing(period: str, items: list[dict], half_discount: bool) -> dict:
    """印花税计税。

    items: [{tax_item, amount, exempt}]（amount 计税依据金额，exempt 免税标记）
    half_discount: 适用六税两费减半（小微/小规模/个体工商户，12号公告至 2027-12-31）
    返回 {total_tax, raw_total, half_saved, details, policy, warnings}
    """
    from ..finance_tax.config.policy_validity import policy_status

    year = str(period)[:4]
    warnings: list[str] = []
    policy = policy_status("six_tax_two_fee_half", year) if half_discount else None
    if half_discount and policy is not None and not policy["valid"]:
        # 减半政策过期/未生效：拒绝按减半计税，明确报错
        raise ValueError(policy["message"])
    if not items:
        raise ValueError("税目明细不能为空")

    details = []
    raw_total = 0.0
    for idx, it in enumerate(items, 1):
        name = str(it.get("tax_item") or "").strip()
        if not name:
            raise ValueError(f"第 {idx} 行税目为空")
        rate = get_rate(name)
        amount = it.get("amount")
        if amount is None or float(amount) < 0:
            raise ValueError(f"第 {idx} 行计税金额非法（须 ≥ 0）")
        amount = float(amount)
        exempt = bool(it.get("exempt", False))
        tax = 0.0 if exempt else amount * rate / 1000.0
        tax = round(tax, 2)
        raw_total += tax
        details.append({
            "tax_item": name, "amount": amount, "rate_permille": rate,
            "exempt": exempt, "tax": tax,
        })

    raw_total = round(raw_total, 2)
    total = raw_total
    half_saved = 0.0
    if half_discount:
        total = round(raw_total / 2, 2)
        half_saved = round(raw_total - total, 2)
    return {
        "total_tax": total,
        "raw_total": raw_total,
        "half_saved": half_saved,
        "details": details,
        "policy": policy or {"policy_key": "six_tax_two_fee_half", "status": "not_applied"},
        "warnings": warnings,
    }


def export_filing_xlsx(filing) -> Path:
    """生成申报表底稿 xlsx，返回路径（服务数据目录 stamp_duty/exports）。"""
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "印花税申报底稿"
    ws.append(["印花税纳税申报表（底稿）"])
    ws.append([f"所属期：{filing.period}    纳税人：{filing.taxpayer_name or '—'}"])
    ws.append([f"六税两费减半：{'是' if filing.half_discount else '否'}"])
    ws.append([])
    ws.append(["税目", "计税金额", "税率(‰)", "免税", "应纳税额"])
    calc = filing.calc_result or {}
    for d in calc.get("details", []):
        ws.append([d["tax_item"], d["amount"], d["rate_permille"],
                   "是" if d["exempt"] else "否", d["tax"]])
    ws.append([])
    ws.append(["减半前合计", calc.get("raw_total", 0)])
    ws.append(["减半优惠节省", calc.get("half_saved", 0)])
    ws.append(["应纳税额合计", calc.get("total_tax", 0)])
    ws.append([f"政策依据：{ (calc.get('policy') or {}).get('policy_basis', '《中华人民共和国印花税法》') }"])
    outdir = module_exports_dir()
    dest = outdir / f"stamp_duty_{filing.period}_{filing.id}.xlsx"
    wb.save(dest)
    return dest


def module_exports_dir() -> Path:
    """导出目录：财务数据目录/stamp_duty/exports（不存在则创建）。"""
    d = get_finance_data_dir() / "stamp_duty" / "exports"
    d.mkdir(parents=True, exist_ok=True)
    return d
