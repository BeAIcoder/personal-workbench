"""finance_board 报表看板模块：微信报表监控管线触发 + HTML 快照归档与嵌入。"""
from . import models  # noqa: F401
from .manifest import manifest  # noqa: F401

__all__ = ["manifest", "models"]
