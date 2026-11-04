# -*- coding: utf-8 -*-
"""税法政策有效期登记（Part B）：内置计税政策的政策依据与生效/失效期统一登记。

口径（2026-10 核实，来源：国家税务总局政策法规库等）：
- 小微 5% 实际税负与六税两费减半：财政部 税务总局公告2023年第12号，2023-01-01 ~ 2027-12-31；
- 增值税小规模 1% 与月销售额 10 万免征：2023年第19号，2023-01-01 ~ 2027-12-31；
- 研发加计 100%：2023年第7号，制度性安排长期实施（无失效期）；
- 集成电路/工业母机 120%：2023年第44号，2023-01-01 ~ 2027-12-31；
- 残保金 30 人以下免征与分档减收：财政部公告2023年第8号，2023-01-01 ~ 2027-12-31；
- 个税专项附加扣除标准：国发〔2023〕13号，长期。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PolicyValidity:
    """单条政策的依据与有效期（expires_date=None 表示长期）。"""

    policy_basis: str
    effective_date: str
    expires_date: str | None


POLICY_REGISTRY: dict[str, PolicyValidity] = {
    "small_micro_5pct": PolicyValidity(
        "财政部 税务总局公告2023年第12号", "2023-01-01", "2027-12-31"),
    "six_tax_two_fee_half": PolicyValidity("财政部 税务总局公告2023年第12号", "2023-01-01", "2027-12-31"),
    "vat_small_scale_1pct": PolicyValidity("财政部 税务总局公告2023年第19号", "2023-01-01", "2027-12-31"),
    "vat_small_scale_exempt10w": PolicyValidity("财政部 税务总局公告2023年第19号", "2023-01-01", "2027-12-31"),
    "rd_deduction_100": PolicyValidity("财政部 税务总局公告2023年第7号", "2023-01-01", None),
    "rd_deduction_120": PolicyValidity(
        "财政部 税务总局 国家发展改革委 工业和信息化部公告2023年第44号", "2023-01-01", "2027-12-31"),
    "disability_fund_exemption": PolicyValidity("财政部公告2023年第8号", "2023-01-01", "2027-12-31"),
    "disability_fund_tiered": PolicyValidity("财政部公告2023年第8号", "2023-01-01", "2027-12-31"),
    "iit_special_deductions": PolicyValidity("国发〔2023〕13号", "2023-01-01", None),
}


def policy_status(policy_key: str, tax_year: str | int) -> dict:
    """按所属年度判定政策状态，返回中文可读结果。

    返回：{policy_key, status(valid|not_started|expired|unknown), valid: bool,
          message: 中文提示, policy_basis, effective_date, expires_date}
    tax_year 取 4 位年度字符串/数字；无法解析年度时按 not_started 处理并提示。
    """
    pv = POLICY_REGISTRY.get(policy_key)
    if pv is None:
        return {
            "policy_key": policy_key, "status": "unknown", "valid": False,
            "message": f"政策 {policy_key!r} 未登记，请核实最新政策后使用",
            "policy_basis": "", "effective_date": "", "expires_date": None,
        }
    year = str(tax_year if tax_year is not None else "").strip()
    if not (year.isdigit() and len(year) == 4):
        return {
            "policy_key": policy_key, "status": "not_started", "valid": False,
            "message": f"所属年度 {tax_year!r} 无法解析（须 4 位年度），不能确认政策 {pv.policy_basis} 已生效",
            "policy_basis": pv.policy_basis, "effective_date": pv.effective_date,
            "expires_date": pv.expires_date,
        }
    if year < pv.effective_date[:4]:
        return {
            "policy_key": policy_key, "status": "not_started", "valid": False,
            "message": f"政策 {pv.policy_basis} 自 {pv.effective_date} 起生效，所属年度 {year} 尚未适用",
            "policy_basis": pv.policy_basis, "effective_date": pv.effective_date,
            "expires_date": pv.expires_date,
        }
    if pv.expires_date and year > pv.expires_date[:4]:
        return {
            "policy_key": policy_key, "status": "expired", "valid": False,
            "message": f"政策 {pv.policy_basis} 已于 {pv.expires_date} 到期，所属年度 {year} 不能适用，"
                       "请核实最新政策",
            "policy_basis": pv.policy_basis, "effective_date": pv.effective_date,
            "expires_date": pv.expires_date,
        }
    return {
        "policy_key": policy_key, "status": "valid", "valid": True,
        "message": f"政策 {pv.policy_basis} 在所属年度 {year} 有效",
        "policy_basis": pv.policy_basis, "effective_date": pv.effective_date,
        "expires_date": pv.expires_date,
    }
