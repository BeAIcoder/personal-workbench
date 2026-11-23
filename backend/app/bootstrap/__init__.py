"""应用启动编排（lifespan 只做调度，步骤在这里）。

拆分来源（2026-11-17「启动/迁移拆分」）：
- ``schema``：建表 + 轻量补列/补索引（SQLite）；
- ``seeds``：幂等启动种子（知识库索引、专家/技能包、调试中心、模块预置、示例数据）；
- ``legacy``：一次性秘密迁移——**只由 `backend/scripts/migrate.py secrets` 显式触发**，
  启动路径只做只读统计与告警。
"""
from __future__ import annotations

from .legacy import (
    migrate_mcp_env_secrets,
    migrate_provider_keys,
    pending_secrets,
    purge_legacy_agent_settings_keys,
    report_pending_secrets,
    run_all as run_legacy_migrations,
)
from .schema import (
    CORE_COLUMN_PATCHES,
    OBSOLETE_INDEXES,
    create_schema,
    duplicate_sample,
    drop_obsolete_indexes,
    ensure_active_contract_index,
    ensure_columns,
    ensure_indexes,
    ensure_sqlite_indexes,
    merged_column_patches,
)
from .seeds import run_startup_seeds

__all__ = [
    "CORE_COLUMN_PATCHES",
    "OBSOLETE_INDEXES",
    "create_schema",
    "duplicate_sample",
    "drop_obsolete_indexes",
    "ensure_active_contract_index",
    "ensure_columns",
    "ensure_indexes",
    "ensure_sqlite_indexes",
    "merged_column_patches",
    "migrate_mcp_env_secrets",
    "migrate_provider_keys",
    "pending_secrets",
    "purge_legacy_agent_settings_keys",
    "report_pending_secrets",
    "run_legacy_migrations",
    "run_startup_seeds",
]
