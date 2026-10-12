"""finance_vault 模块清单：DuckDB 数据仓。"""
from ..spec import ModuleManifest
from . import models  # noqa: F401  导入即把模块表注册进 Base.metadata（须先于 lifespan 的 create_all）
from .service import vault_list_tables, vault_query

manifest = ModuleManifest(
    name="finance_vault",
    title="数据仓",
    version="0.1.0",
    prefix="/api/finance/vault",
    tables=["mod_vault_dataset"],
    menu={"group": "财务", "icon": "Coin", "order": 30},
    agent_tools=[
        {
            "name": "vault_list_tables",
            "func": vault_list_tables,
            "description": "列出数据仓已注册数据集（表名/行数/来源/注册时间）",
            "params": {},
            "required": [],
            "readonly": True,
        },
        {
            "name": "vault_query",
            "func": vault_query,
            "description": "在数据仓执行只读 SELECT 查询（仅允许引用已注册数据集，自动限流）",
            "params": {
                "sql": {"type": "string", "description": "只读 SELECT/WITH 查询语句"}
            },
            "required": ["sql"],
            "readonly": True,
        },
    ],
    column_patches={},
    requires=[],
    preset_experts=["finance_specialist"],  # 工具自动预置进财税专员白名单
    enabled=True,
)
