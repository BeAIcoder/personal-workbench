"""finance_tax 模块：企业所得税填报（后端由「企业所得税计算」项目整体迁入，引擎零改动）。"""
from . import models  # noqa: F401  导入即把模块表注册进 Base.metadata（须先于 lifespan 的 create_all）

from .manifest import manifest  # noqa: F401

__all__ = ["manifest", "models"]
