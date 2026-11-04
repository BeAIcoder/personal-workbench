"""应用配置：从环境变量 / backend/.env 文件读取，均提供合理默认值。"""
import os
import re
from pathlib import Path
from typing import Any

from pydantic import field_validator
from pydantic_settings import BaseSettings

# backend 目录
BASE_DIR = Path(__file__).resolve().parent.parent


def _env_file_path() -> Path | None:
    """与 Settings 相同的 .env 选择口径：WORKBENCH_ENV_FILE="" 表示禁用。"""
    raw = os.environ.get("WORKBENCH_ENV_FILE")
    if raw is None:
        return BASE_DIR / ".env"
    raw = raw.strip()
    return Path(raw) if raw else None


def _read_env_file_value(path: Path, key: str) -> str:
    """只为 WORKBENCH_DATA_DIR 预读 .env：DATA_DIR 是模块常量，必须早于 Settings。

    规则与常见 dotenv 一致：同名键以**最后一个**为准；未加引号的值剥离
    ` # 行内注释`；单/双引号内的 `#` 保留，引号后可有行内注释。
    """
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    found = ""
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        name, sep, value = line.partition("=")
        if not sep or name.strip() != key:
            continue
        value = value.strip()
        quoted = re.match(r"^([\"'])(.*?)\1(?:\s+#.*)?$", value)
        if quoted:
            found = quoted.group(2)
        else:
            found = re.split(r"\s+#", value, maxsplit=1)[0].strip()
    return found


def _bootstrap_data_dir() -> Path:
    """数据根目录：WORKBENCH_DATA_DIR（进程环境或 .env）优先，默认 backend/data。

    相对路径按 backend/ 解析，保证从任意工作目录启动都落到同一位置。
    """
    raw = os.environ.get("WORKBENCH_DATA_DIR", "").strip()
    if not raw:
        env_file = _env_file_path()
        if env_file is not None:
            raw = _read_env_file_value(env_file, "WORKBENCH_DATA_DIR").strip()
    if not raw:
        return BASE_DIR / "data"
    path = Path(raw).expanduser()
    return path if path.is_absolute() else BASE_DIR / path


# 默认数据目录（SQLite 数据库文件所在位置）；测试/探针用 WORKBENCH_DATA_DIR 整体隔离
DATA_DIR = _bootstrap_data_dir()
# 聊天附件上传目录
UPLOAD_DIR = DATA_DIR / "uploads"


# 本机外部资源默认值：集中在此，模块不再各自硬编码（settings/.env 优先）
DEFAULT_WORKBUDDY_HOME = Path.home() / ".workbuddy"
DEFAULT_QWENPAW_SKILL_POOL = Path("D:/AIApps/QwenPaw/data/skill_pool")


class Settings(BaseSettings):
    """应用配置项，支持通过环境变量或 backend/.env 覆盖。"""

    app_name: str = "个人工作台"
    version: str = "1.16.1"
    host: str = "127.0.0.1"
    port: int = 8000
    # 是否允许服务绑定在非回环地址（默认 False：无鉴权，局域网暴露风险高）。
    # 仅供明确知晓风险的高级用法；开启后仍建议配合 CORS_ORIGINS 与防火墙。
    allow_non_loopback: bool = False
    # 数据根目录：仅作为 .env 声明入口（真正的派生发生在模块导入期，
    # 见 _bootstrap_data_dir；进程环境变量优先级更高）。
    workbench_data_dir: str = ""
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

    @field_validator(
        "finance_data_dir",
        "finance_expense_engine_path",
        "finance_invoice_engine_path",
        "finance_board_pipeline_path",
        "finance_cashflow_engine_path",
        "finance_pos_engine_path",
        "finance_board_task_name",
        "finance_asset_anchor",
        "soffice_path",
        "pdf_font_path",
        mode="before",
    )
    @classmethod
    def _strip_text_paths(cls, value: Any) -> Any:
        """纯空白引擎/目录路径归一为未配置（否则 `None / script` 会抛 TypeError 变 500）。"""
        return value.strip() if isinstance(value, str) else value

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
