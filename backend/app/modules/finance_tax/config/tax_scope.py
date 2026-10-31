# -*- coding: utf-8 -*-
"""税种准入登记（准入制）：哪些税种可计算、哪些明确不做，以及为什么。

背景：项目不对"全税制"做承诺。一个税种要进入计算入口，必须先满足三项准入条件：
1. 有官方政策原文（公告号/法条）；
2. 有可配置的地方口径（如房产税原值减除比例、土地等级单位税额）；
3. 有真实脱敏申报样本可对拍。

未满足的税种登记为 ``blocked`` 并给出原因；调用方应调用 :func:`require_tax_scope`
统一拒绝，而不是静默返回 0 或凭行业直觉实现一个"看起来合理"的模型。

新增税种时：先把 ``status`` 改为 ``admitted`` 并补齐 ``source``/``sample``，
再实现计算与测试；否则保持 ``blocked``。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TaxScope:
    """单个税种的准入状态。"""

    key: str
    name: str
    status: str  # admitted | blocked
    reason: str
    source: str = ""       # 政策依据（公告号/法条）
    sample: str = ""       # 真实脱敏样本编号/路径说明（admitted 时应非空）

    @property
    def admitted(self) -> bool:
        return self.status == "admitted"


TAX_SCOPE_REGISTRY: dict[str, TaxScope] = {
    # ---- 已准入：有政策依据、有计算实现、有测试或真实样本对拍 ----
    "corporate_income_tax": TaxScope(
        "corporate_income_tax", "企业所得税", "admitted",
        "已实现预缴/年度计算与 A200000 导出",
        "企业所得税法及实施条例；财政部 税务总局公告2023年第12号",
        "2025 A106000 真实申报对拍（脱敏）",
    ),
    "value_added_tax": TaxScope(
        "value_added_tax", "增值税", "admitted",
        "一般/小规模双模式，含附加税口径",
        "财政部 税务总局公告2023年第19号",
        "2026-09 真实申报对拍（脱敏）",
    ),
    "individual_income_tax": TaxScope(
        "individual_income_tax", "个人所得税", "admitted",
        "累计预扣法；专项附加扣除按国发〔2023〕13号标准",
        "个人所得税法；国发〔2023〕13号",
        "级距边界 12 点测试",
    ),
    "stamp_duty": TaxScope(
        "stamp_duty", "印花税", "admitted",
        "印花税法 13 税目；证券交易与部分产权转移子目尚未覆盖",
        "中华人民共和国印花税法（2022-07-01 施行）",
        "2026Q3 真实申报对拍 7,543.49 元（脱敏）",
    ),
    "disability_fund": TaxScope(
        "disability_fund", "残疾人就业保障金", "admitted",
        "30 人以下免征 + 分档减收",
        "财政部公告2023年第8号",
        "2026 真实测算表对拍 40,048.17 元（脱敏）",
    ),
    # ---- 未准入：明确不做，附原因（禁止静默实现） ----
    "property_tax": TaxScope(
        "property_tax", "房产税", "blocked",
        "需先确认纳税主体与计税方式（自用从价 1.2% / 出租从租 12% / 转租不征）"
        "以及地方原值减除比例（10%~30%）",
    ),
    "urban_land_use_tax": TaxScope(
        "urban_land_use_tax", "城镇土地使用税", "blocked",
        "需地方土地等级与每平方米年税额标准，且需确认实际占用面积口径",
    ),
    "land_appreciation_tax": TaxScope(
        "land_appreciation_tax", "土地增值税", "blocked",
        "一次性交易税，需项目级扣除项与清算口径；当前无真实样本",
    ),
    "deed_tax": TaxScope(
        "deed_tax", "契税", "blocked",
        "一次性交易税，需地方税率（3%~5%）与权属转移类型判定；当前无真实样本",
    ),
}


def tax_scope_status(key: str) -> dict:
    """返回单个税种的准入状态（未知税种按 blocked 处理，避免"没登记就默认可用"）。"""
    scope = TAX_SCOPE_REGISTRY.get(key)
    if scope is None:
        return {
            "key": key, "name": key, "status": "blocked",
            "reason": f"税种 {key!r} 未登记，请先在 tax_scope.py 完成准入登记",
            "source": "", "sample": "",
        }
    return {
        "key": scope.key, "name": scope.name, "status": scope.status,
        "reason": scope.reason, "source": scope.source, "sample": scope.sample,
    }


def list_tax_scopes() -> list[dict]:
    """全部税种准入清单（admitted 在前，便于前端先展示可用项）。"""
    rows = [tax_scope_status(k) for k in TAX_SCOPE_REGISTRY]
    rows.sort(key=lambda row: (row["status"] != "admitted", row["key"]))
    return rows


def require_tax_scope(key: str) -> None:
    """准入门禁：未准入税种抛中文 ValueError，由路由统一转 400。"""
    status = tax_scope_status(key)
    if status["status"] != "admitted":
        raise ValueError(
            f"{status['name']}尚未准入：{status['reason']}。"
            "如需启用，请先补齐政策依据、地方口径与脱敏样本后修改 tax_scope 登记"
        )
