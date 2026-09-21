"""应用配置：从环境变量 / backend/.env 文件读取，均提供合理默认值。"""
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
    version: str = "1.0.0"
    host: str = "127.0.0.1"
    port: int = 8000
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

    model_config = {"env_file": str(BASE_DIR / ".env"), "env_file_encoding": "utf-8"}


settings = Settings()
