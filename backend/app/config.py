"""应用配置：从环境变量 / backend/.env 文件读取，均提供合理默认值。"""
import os
from pathlib import Path

from pydantic_settings import BaseSettings

# backend 目录
BASE_DIR = Path(__file__).resolve().parent.parent
# 默认数据目录（SQLite 数据库文件所在位置）
DATA_DIR = BASE_DIR / "data"
# 聊天附件上传目录
UPLOAD_DIR = DATA_DIR / "uploads"


class Settings(BaseSettings):
    """应用配置项，支持通过环境变量或 backend/.env 覆盖。"""

    app_name: str = "个人工作台"
    version: str = "1.16.1"
    host: str = "127.0.0.1"
    port: int = 8000
    # 是否允许服务绑定在非回环地址（默认 False：无鉴权，局域网暴露风险高）。
    # 仅供明确知晓风险的高级用法；开启后仍建议配合 CORS_ORIGINS 与防火墙。
    allow_non_loopback: bool = False
    # 默认使用 SQLite，无需安装任何数据库服务；可用 DATABASE_URL 覆盖
    database_url: str = f"sqlite:///{(DATA_DIR / 'workbench.db').as_posix()}"
    # 首次启动且数据库为空时，是否自动写入示例数据
    seed_on_startup: bool = True
    # 允许的跨域来源（开发模式下 Vite 前端运行在 5173 端口）
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # ---- AI 助手（AgentScope Agent 团队）----
    # 统一走 OpenAI 兼容协议：ModelScope / 硅基流动 / DeepSeek / 火山方舟 /
    # DashScope 兼容模式 / 本地 Ollama 均可，三项填齐即自动激活
    llm_provider: str = ""
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""

    # ---- 财务模块（Module Registry）----
    # 财务模块数据目录：留空时默认 backend/data/finance（见 get_finance_data_dir）
    finance_data_dir: str = ""
    # 跨项目引擎路径预留：费用报销引擎 / 发票引擎。
    # 留空时由各模块 service 按默认相对路径自行推导，config 只存值不做推导
    finance_expense_engine_path: str = ""
    finance_invoice_engine_path: str = ""
    # 微信报表监控管线目录（finance_board 模块）：留空按默认相对路径推导
    finance_board_pipeline_path: str = ""
    # 现金流 V1 引擎目录（finance_cashflow 模块）：留空按默认相对位置推导
    finance_cashflow_engine_path: str = ""
    # POS 台账引擎目录（finance_pos 模块）：留空按默认相对位置推导
    finance_pos_engine_path: str = ""
    # QwenPaw 技能池目录，留空用代码内置默认
    qwenpaw_skill_pool: str = ""
    # WorkBuddy 主目录，留空用代码内置默认
    workbuddy_home: str = ""
    # 报表看板管线的 Windows 计划任务名
    finance_board_task_name: str = "微信报表监控"
    # 财务引擎兄弟项目推导的锚点目录名
    finance_asset_anchor: str = "01-财税自动化"
    # LibreOffice soffice 可执行文件路径，留空自动探测
    soffice_path: str = ""
    # 税务 PDF 自定义字体文件路径，留空用平台默认候选
    pdf_font_path: str = ""

    # WORKBENCH_ENV_FILE="" 可禁用 .env（测试隔离用）；未设置时读 backend/.env
    model_config = {
        "env_file": (os.environ.get("WORKBENCH_ENV_FILE", str(BASE_DIR / ".env")) or None),
        "env_file_encoding": "utf-8",
    }


settings = Settings()


def get_finance_data_dir() -> Path:
    """财务模块数据目录：配置为空时默认 backend/data/finance。"""
    if settings.finance_data_dir:
        return Path(settings.finance_data_dir)
    return DATA_DIR / "finance"
