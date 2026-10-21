"""debug_center 模块清单：调试中心（问题记录 / 测试运行 / 前端报错）。

纯文件存储，无模块表（tables=[]）；prefix 走 /api/debug/ 族（spec.py 已放开）。
"""
from ..spec import ModuleManifest

manifest = ModuleManifest(
    name="debug_center",
    title="调试中心",
    version="0.1.0",
    prefix="/api/debug",
    tables=[],                                # 纯 JSON 文件存储，不建表
    menu={"group": "调试", "icon": "Monitor", "order": 80},
    agent_tools=[],
    column_patches={},
    requires=[],
    enabled=True,
)
