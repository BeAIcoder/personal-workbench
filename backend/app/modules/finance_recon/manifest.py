"""finance_recon 模块清单：商户核销对账（批次、四源解析、勾稽匹配与报告）。

菜单唯一源是前端 src/modules/finance-recon/manifest.js；表清单以 models.py 的
__tablename__（mod_recon_* 前缀）为准。summary 工具按批次返回状态与计数，
后续任务（check/matches/issues）扩展其返回内容。
"""
from ..spec import ModuleManifest
from . import models  # noqa: F401  导入即把模块表注册进 Base.metadata（须先于 lifespan 的 create_all）
from .service import recon_summary_tool

manifest = ModuleManifest(
    name="finance_recon",
    title="商户核销对账",
    version="0.1.0",
    prefix="/api/finance/recon",
    # C1/I3：ever_ready 是修复轮新增的审计留痕列，历史库由宿主 ensure_columns
    # 按此补列（INTEGER NOT NULL DEFAULT 0，与 scripts/migrate_recon.py 的
    # ALTER DDL 完全一致，先执行者生效），避免老库 SELECT/INSERT 该列 500；
    # 历史回填（ready→1、legacy failed/checking 保守置 1）由
    # backend/scripts/migrate_recon.py 显式执行。
    column_patches={"mod_recon_batch": {"ever_ready": "INTEGER NOT NULL DEFAULT 0"}},
    agent_tools=[
        {
            "name": "recon_summary",
            "func": recon_summary_tool,
            "description": "商户核销对账摘要：批次数量、各状态数量与最新批次的源文件/事实/匹配/问题计数",
            "params": {},
            "required": [],
            "readonly": True,
        }
    ],
    preset_experts=["finance_specialist"],  # 工具自动预置进财税专员白名单
    requires=[],
    enabled=True,
)
