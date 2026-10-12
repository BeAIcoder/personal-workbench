"""finance_pos POS 对账模块：L1 任务型薄壳（上传→跑原引擎→产物下载）。"""
from . import models  # noqa: F401
from .manifest import manifest  # noqa: F401

__all__ = ["manifest", "models"]
