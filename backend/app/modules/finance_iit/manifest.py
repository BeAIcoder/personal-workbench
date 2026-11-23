# -*- coding: utf-8 -*-
"""finance_iit 模块清单：个人所得税填报。

累计预扣法（个人所得税法 + 国发〔2023〕13号专项附加扣除标准）。
"""
from ..spec import ModuleManifest
from . import models  # noqa: F401

manifest = ModuleManifest(
    name="finance_iit",
    title="个人所得税填报",
    version="0.1.0",
    prefix="/api/finance/iit",
    agent_tools=[],
    column_patches={},
    requires=["finance_tax"],
    preset_experts=[],
    enabled=True,
)
