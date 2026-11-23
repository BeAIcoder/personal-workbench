# -*- coding: utf-8 -*-
"""finance_vat 模块清单：增值税填报。

小规模 1% 征收率与季度 30 万免征按 2023年第19号公告（至 2027-12-31），
政策有效期统一引用 finance_tax 的 policy_validity。
"""
from ..spec import ModuleManifest
from . import models  # noqa: F401

manifest = ModuleManifest(
    name="finance_vat",
    title="增值税填报",
    version="0.1.0",
    prefix="/api/finance/vat",
    agent_tools=[],
    column_patches={},
    requires=["finance_tax"],
    preset_experts=[],
    enabled=True,
)
