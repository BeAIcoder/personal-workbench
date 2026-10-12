"""finance_board 模块清单：微信报表监控看板（管线触发 + 快照嵌入）。"""
from ..spec import ModuleManifest
from . import models  # noqa: F401  导入即把模块表注册进 Base.metadata（须先于 lifespan 的 create_all）

manifest = ModuleManifest(
    name="finance_board",
    title="报表看板",
    version="0.1.0",
    prefix="/api/finance/board",
    tables=["mod_board_snapshot"],
    menu={"group": "财务", "icon": "DataBoard", "order": 70},
    agent_tools=[],
    column_patches={},
    requires=[],
    enabled=True,
)
