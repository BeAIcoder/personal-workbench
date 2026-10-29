# -*- coding: utf-8 -*-
"""finance_dsf 模块清单：残保金填报。

政策：财政部公告2023年第8号（30 人以下免征 + 1%~规定比例减收 50% / 1% 以下减收 90%，
至 2027-12-31）；计征比例默认 1.5%（各地可不同，录入口径可调）。
"""
from ..spec import ModuleManifest
from . import models  # noqa: F401

manifest = ModuleManifest(
    name="finance_dsf",
    title="残保金填报",
    version="0.0.1",
    prefix="/api/finance/dsf",
    tables=["mod_dsf_filing"],
    menu={"group": "财务", "icon": "Coin", "order": 84},
    agent_tools=[],
    column_patches={},
    requires=["finance_tax"],
    preset_experts=[],
    enabled=True,
)
