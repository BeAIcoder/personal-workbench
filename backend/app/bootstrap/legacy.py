"""一次性秘密迁移（显式脚本专用；启动路径不再执行写操作）。

2026-11-17 启动拆分：以下三项原本在 `main.lifespan` 里做，现已移到
`backend/scripts/migrate.py secrets`，由运维/升级流程显式触发：

- ``provider_keys``：``model_providers.api_key`` 明文 → DPAPI 密封（``secrets_box.migrate_seal_keys``）
- ``mcp_env``：MCP 服务器 env 明文 → DPAPI 密封（``agents.mcp.migrate_env_secrets``）
- ``agent_settings_keys``：旧版 ``agent_settings.api_key`` 明文残留 → 清空

为什么移出启动：这三步只在「老库首次升级」时有意义，放在每次启动里既拖慢启动，
也让启动路径带写语义（备份/只读演练会意外改写数据）。新写入一律走密封，
留在库里的明文仍能被兼容读取，所以不在启动时迁移不会丢功能，只是安全卫生问题。

启动路径只调 ``pending_secrets()`` 做只读计数并告警，不读取、不打印任何秘密值。
"""
from __future__ import annotations

import logging

from sqlalchemy import func, select

from ..database import SessionLocal
from ..models import AgentSettings, ModelProvider

logger = logging.getLogger(__name__)

# 密封值前缀：与 agents/secrets_box.py 的落盘格式一致（`dpapi:<base64>`）。
# 这里只用于 SQL 侧「是否为明文」的统计，不参与加解密。
SEALED_PREFIX = "dpapi:"


def migrate_mcp_env_secrets() -> int:
    """把存量明文 MCP env 一次性迁移为 DPAPI 格式，返回迁移的服务器条数。"""
    from ..agents import mcp as mcp_manager

    with SessionLocal() as db:
        return int(mcp_manager.migrate_env_secrets(db))


def migrate_provider_keys() -> int:
    """把 model_providers.api_key 存量明文一次性密封，返回迁移条数。"""
    from ..agents import secrets_box

    return int(secrets_box.migrate_seal_keys())


def purge_legacy_agent_settings_keys() -> int:
    """清除旧版 agent_settings.api_key 明文残留，返回清除行数（幂等）。"""
    from ..agents import secrets_box

    return int(secrets_box.purge_legacy_agent_settings_keys())


def pending_secrets() -> dict[str, int]:
    """只读统计仍为明文的存量秘密条数（只做 COUNT，不读取、不打印具体值）。

    覆盖 ``provider_keys`` 与旧的 ``agent_settings_keys``；MCP env 是 JSON 字典，
    「要不要迁移」取决于每个值的前缀，无法用 COUNT 判断，统一交给
    ``scripts/migrate.py secrets`` 处理（该步骤幂等，无明文时返回 0）。
    """
    counts = {"provider_keys": 0, "agent_settings_keys": 0}
    try:
        with SessionLocal() as db:
            counts["provider_keys"] = int(
                db.execute(
                    select(func.count())
                    .select_from(ModelProvider)
                    .where(
                        ModelProvider.api_key.isnot(None),
                        ModelProvider.api_key != "",
                        ModelProvider.api_key.notlike(f"{SEALED_PREFIX}%"),
                    )
                ).scalar_one()
            )
            counts["agent_settings_keys"] = int(
                db.execute(
                    select(func.count())
                    .select_from(AgentSettings)
                    .where(
                        AgentSettings.api_key.isnot(None),
                        AgentSettings.api_key != "",
                    )
                ).scalar_one()
            )
    except Exception:
        logger.warning("存量明文秘密统计失败（不影响启动）", exc_info=True)
    return counts


def report_pending_secrets() -> dict[str, int]:
    """启动路径调用：发现未迁移的明文秘密时给一条中文告警（不做任何写操作）。"""
    counts = pending_secrets()
    pending = {name: value for name, value in counts.items() if value}
    if pending:
        detail = "、".join(f"{name} {value} 条" for name, value in pending.items())
        logger.warning(
            "检测到未迁移的明文秘密（%s）。启动不再自动迁移，请停服后执行 "
            "`python scripts/migrate.py secrets` 完成 DPAPI 密封/清除。",
            detail,
        )
    return counts


def run_all() -> dict[str, int]:
    """依次执行三项一次性迁移，返回各自处理条数（幂等，重复执行返回 0）。"""
    from ..agents.secrets_box import ensure_dpapi_available

    # DPAPI 不可用时直接失败，绝不在缺少加密能力时改写密钥列
    ensure_dpapi_available()
    results = {
        "provider_keys": migrate_provider_keys(),
        "mcp_env": migrate_mcp_env_secrets(),
        "agent_settings_keys": purge_legacy_agent_settings_keys(),
    }
    return results


__all__ = [
    "SEALED_PREFIX",
    "migrate_mcp_env_secrets",
    "migrate_provider_keys",
    "purge_legacy_agent_settings_keys",
    "pending_secrets",
    "report_pending_secrets",
    "run_all",
]
