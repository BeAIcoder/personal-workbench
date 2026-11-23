# -*- coding: utf-8 -*-
"""finance_stamp_duty 模块清单：印花税填报（按税目录入 → 计税 → 申报表底稿导出）。

政策依据统一引用 finance_tax 的 policy_validity 登记（六税两费减半：12号公告至 2027-12-31；
印花税法税率表：《中华人民共和国印花税法》2022-07-01 施行）。
"""
from ..spec import ModuleManifest
from . import models  # noqa: F401  导入即把模块表注册进 Base.metadata（须先于 lifespan 的 create_all）

manifest = ModuleManifest(
    name="finance_stamp_duty",
    title="印花税填报",
    version="0.1.0",
    prefix="/api/finance/stamp-duty",
    agent_tools=[],
    column_patches={},
    requires=["finance_tax"],
    preset_experts=[],
    enabled=True,
)
