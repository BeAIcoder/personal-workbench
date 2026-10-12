"""finance_hello 模块清单：模块注册机制的实证样例。"""
from ..spec import ModuleManifest
from . import models  # noqa: F401  导入即把模块表注册进 Base.metadata（须先于 lifespan 的 create_all）
from .service import hello_ping

manifest = ModuleManifest(
    name="finance_hello",
    title="财务模块示例",
    version="0.1.0",
    prefix="/api/finance/hello",
    tables=["mod_hello_entry"],
    menu={"group": "财务", "icon": "Coin", "order": 99},
    agent_tools=[
        {
            "name": "hello_ping",
            "func": hello_ping,
            "description": "财务模块连通性自检：返回 finance_hello 模块状态与样例条目数",
            "params": {},
            "required": [],
            "readonly": True,
        }
    ],
    column_patches={},
    requires=[],
    preset_experts=["finance_specialist"],  # 工具自动预置进财税专员白名单
    enabled=True,
)
