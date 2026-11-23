"""SQLite 轻量 schema 迁移：补列 + 补索引（幂等、永不删数据）。

2026-11-17 启动拆分：实现从 `app.main` 迁到这里，`main` 仍以
`_ensure_sqlite_columns` / `_ensure_sqlite_indexes` / `_ensure_indexes_on` 等
原名 re-export 兼容既有脚本与测试。

失败策略保持原样：
- 列补丁逐列 try，单列失败只告警、其余列继续（不允许整表回滚）；
- 唯一索引建前先查历史重复，有重复只告警、不建、不删数据；
- 索引创建失败逐条告警，下次启动重试；历史索引名只在新索引确认存在后清理。
"""
from __future__ import annotations

import logging
from collections.abc import Mapping

from sqlalchemy import inspect as sa_inspect
from sqlalchemy import text

from ..config import settings
from ..database import Base, SessionLocal, engine

logger = logging.getLogger(__name__)

# 宿主内置列补丁：老库（create_all 不改已有表）需要补齐的列。
# 各模块的 manifest.column_patches 由 merged_column_patches() 合并进来。
CORE_COLUMN_PATCHES: dict[str, dict[str, str]] = {
    "chat_messages": {"thinking": "TEXT DEFAULT ''"},
    "agent_sessions": {"archived": "INTEGER DEFAULT 0", "summary": "TEXT DEFAULT ''", "summary_until": "INTEGER"},
    "memories": {"type": "TEXT DEFAULT ''", "source_doc_id": "INTEGER"},
    "agent_settings": {
        "active_profile_id": "INTEGER",
        "active_model_id": "INTEGER",
        "enable_skills": "INTEGER DEFAULT 0",
        "skills_dir": "TEXT DEFAULT ''",
        "enable_bash": "INTEGER DEFAULT 0",
        "enable_plugins": "INTEGER DEFAULT 0",
        "plugins_dir": "TEXT DEFAULT ''",
        "reasoning_effort": "TEXT DEFAULT ''",
        "memory_inject_max": "INTEGER DEFAULT 3",
        "routing_model_id": "INTEGER",
    },
    "scheduled_jobs": {
        "cron_expr": "TEXT DEFAULT ''",
        "action": "TEXT DEFAULT ''",
        "running_token": "TEXT DEFAULT ''",
        "running_since": "DATETIME",
    },
    "kb_docs": {"ocr_pages": "INTEGER DEFAULT 0"},
    "kb_settings": {"ocr_enabled": "INTEGER DEFAULT 0"},
    "model_providers": {
        "preset_key": "TEXT DEFAULT ''",
        "models_url": "TEXT DEFAULT ''",
        "sync_credentials": "TEXT DEFAULT ''",
    },
    "provider_models": {
        "temperature": "TEXT DEFAULT ''",
        "note": "TEXT DEFAULT ''",
        "supports_thinking": "INTEGER DEFAULT -1",
        "supports_vision": "INTEGER DEFAULT 0",
        "thinking_levels": "TEXT DEFAULT ''",
        "probed_context": "INTEGER DEFAULT 0",
        "probed_max_output": "INTEGER DEFAULT 0",
        "probe_result": "TEXT DEFAULT ''",
        "probed_at": "TEXT DEFAULT ''",
        "max_input_tokens": "INTEGER DEFAULT 0",
        "supports_tools": "INTEGER DEFAULT -1",
        "capability_meta": "TEXT DEFAULT '{}'",
        "synced_at": "TEXT DEFAULT ''",
    },
}

# 历史索引清理：旧名 -> 新名。仅当「新名索引已存在且列/唯一性与模型一致」时才删除
# 旧名；新名未建成（重复/失败）时保留旧索引。标识符均为模块迁移常量，不接收用户输入。
OBSOLETE_INDEXES: dict[str, dict[str, str]] = {
    "mod_bip_balance": {"uq_bip_balance_period_subject": "uq_bip_balance_scope"},
}


def merged_column_patches(
    module_patches: Mapping[str, Mapping[str, str]] | None = None,
) -> dict[str, dict[str, str]]:
    """宿主内置补丁 + 模块 manifest 补丁合并（模块补丁优先覆盖同名列）。"""
    patches = {table: dict(columns) for table, columns in CORE_COLUMN_PATCHES.items()}
    for table, columns in (module_patches or {}).items():
        patches.setdefault(table, {}).update(columns)
    return patches


def ensure_columns(
    bind=None,
    module_patches: Mapping[str, Mapping[str, str]] | None = None,
) -> None:
    """为历史库补齐后加的列（SQLite 的 create_all 不会改已有表）。

    非 SQLite 方言直接返回（PRAGMA 只对 SQLite 有意义）。
    """
    target = engine if bind is None else bind
    if getattr(getattr(target, "dialect", None), "name", "") != "sqlite":
        return
    if module_patches is None:
        from ..modules import collect_column_patches

        module_patches = collect_column_patches()
    for table, columns in merged_column_patches(module_patches).items():
        try:
            with target.connect() as conn:
                existing = [row[1] for row in conn.execute(text(f"PRAGMA table_info({table})"))]
        except Exception:
            logger.warning("SQLite 表 %s 的列信息读取失败，已跳过该表", table, exc_info=True)
            continue
        for col, ddl in columns.items():
            if col in existing:
                continue
            try:
                with target.begin() as conn:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}"))
            except Exception:
                logger.warning("SQLite 列迁移失败：%s.%s（其余列继续）", table, col, exc_info=True)


def duplicate_sample(bind, table, column_names: list[str], where_clause=None) -> tuple | None:
    """返回唯一索引列上的第一条重复键样本（无重复返回 None）。

    仅用于「建唯一索引前先查历史重复」：动态列名全部来自 ORM 元数据常量，
    不接收任何用户输入。检查失败时返回 None（交由建索引时的数据库报错兜底并告警）。

    - `where_clause` 是部分唯一索引的谓词（如 `status='active'`）：只检查真正会
      落入索引的行，避免把已终止合同/空 unit_id 误判成重复而永远跳过建索引；
    - 唯一索引允许多个 NULL 键，分组前先排除任一索引列为 NULL 的行。
    """
    from sqlalchemy import func, select

    cols = [table.c[name] for name in column_names]
    stmt = (
        select(*cols, func.count().label("cnt"))
        .where(*[col.isnot(None) for col in cols])
        .group_by(*cols)
        .having(func.count() > 1)
        .limit(1)
    )
    if where_clause is not None:
        stmt = stmt.where(where_clause)
    try:
        with bind.connect() as conn:
            row = conn.execute(stmt).first()
    except Exception:
        logger.warning("重复键检查失败：%s", table.name, exc_info=True)
        return None
    return tuple(row) if row is not None else None


def drop_obsolete_indexes(bind, metadata) -> list[str]:
    """新索引建成功后再清理历史索引名；建新失败/跳过时旧索引原样保留。"""
    inspector = sa_inspect(bind)
    dropped: list[str] = []
    for table_name, mapping in OBSOLETE_INDEXES.items():
        table = metadata.tables.get(table_name)
        if table is None or not inspector.has_table(table_name):
            continue
        live = {i["name"]: i for i in inspector.get_indexes(table_name)}
        model_indexes = {i.name: i for i in table.indexes}
        for old_name, new_name in mapping.items():
            if old_name not in live:
                continue
            new_index = model_indexes.get(new_name)
            new_live = live.get(new_name)
            if new_index is None or new_live is None:
                continue  # 新索引未建成：保留旧索引，下次启动重试
            wanted = [c.name for c in new_index.columns]
            if list(new_live.get("column_names") or []) != wanted:
                continue
            if bool(new_live.get("unique")) != bool(new_index.unique):
                continue
            try:
                with bind.begin() as conn:
                    # 索引名来自上方常量表，不接收用户输入
                    conn.exec_driver_sql(f'DROP INDEX IF EXISTS "{old_name}"')
                dropped.append(f"{table_name}.{old_name}(dropped)")
            except Exception:
                logger.warning(
                    "历史索引 %s 清理失败（新索引已存在；两者并存不会放宽唯一约束，下次启动重试）",
                    old_name, exc_info=True,
                )
    if dropped:
        logger.info("SQLite 历史索引清理：%s", ", ".join(dropped))
    return dropped


def ensure_indexes(bind, metadata) -> list[str]:
    """为已有表补齐缺失索引（幂等）；唯一索引遇历史重复时跳过并告警。

    - 只创建缺失索引，不改/不删已有同名索引——避免「DROP 后 CREATE 失败」丢索引；
    - 唯一索引建前先查重复，有重复只告警、不建、不删任何数据；
    - 历史索引名由 drop_obsolete_indexes 在**新索引确认存在后**清理；
    - 新索引创建失败时旧索引保持原样，下次启动重试。
    """
    inspector = sa_inspect(bind)
    created: list[str] = []
    for table in metadata.sorted_tables:
        if not inspector.has_table(table.name):
            continue
        existing = {i["name"] for i in inspector.get_indexes(table.name)}
        for index in table.indexes:
            if index.name in existing:
                continue
            if index.unique:
                cols = [c.name for c in index.columns]
                where_clause = None
                dialect_name = getattr(bind.dialect, "name", "")
                if dialect_name and dialect_name in index.dialect_options:
                    where_clause = index.dialect_options[dialect_name].get("where")
                dup = duplicate_sample(bind, table, cols, where_clause)
                if dup:
                    logger.warning(
                        "表 %s 存在重复键 %s（样本 %s），已跳过唯一索引 %s 的自动迁移"
                        "（已有索引保持原样；不会自动删除任何数据；请人工核对并清理重复后再重启）",
                        table.name, tuple(cols), dup, index.name,
                    )
                    continue
            try:
                # 逐条 try：某张表建失败只跳过它，不能连带丢掉后面的索引
                index.create(bind=bind, checkfirst=True)
                created.append(f"{table.name}.{index.name}")
            except Exception:
                logger.warning("索引 %s 补齐失败（不影响启动）", index.name, exc_info=True)
    drop_obsolete_indexes(bind, metadata)
    return created


def ensure_active_contract_index(session_factory=None) -> None:
    """启动路径显式注册「同铺位仅一份 active 合同」部分唯一索引。

    通用索引迁移只按索引列查重；部分唯一索引的谓词（status='active'）与 NULL
    语义已由 `duplicate_sample` 修正，这里再走一次模块内的专用检查作为兜底：
    - 无 active 重复 → 补建缺失索引；
    - 有 active 重复 → 只告警，不建索引、不删任何数据，应用层继续以 409 拒绝新重复。
    """
    try:
        from ..modules.business_operations.service import ensure_active_unit_index
    except ImportError:
        logger.warning("营运模块不可用，跳过同铺位 active 合同部分唯一索引检查")
        return
    factory = SessionLocal if session_factory is None else session_factory
    try:
        with factory() as db:
            result = ensure_active_unit_index(db)
    except Exception:
        logger.warning("同铺位 active 合同部分唯一索引检查跳过（不影响启动）", exc_info=True)
        return
    if result["duplicates"]:
        logger.warning(
            "同铺位存在 %d 组重复 active 合同，部分唯一索引 uq_ops_contract_unit_active 未创建；"
            "应用层仍会拒绝新重复（409）；本检查不删除任何数据，"
            "请人工核对并终止多余合同后再重启",
            len(result["duplicates"]),
        )
    elif result["created"]:
        logger.info("同铺位 active 合同部分唯一索引已补建（uq_ops_contract_unit_active）")


def ensure_sqlite_indexes(
    bind=None,
    metadata=None,
    session_factory=None,
    database_url: str | None = None,
) -> list[str]:
    """轻量迁移：为历史库补齐后加的索引（非 SQLite 直接返回空）。

    create_all 只会建新表，不会给「已存在的表」补索引；所以模型里后加的 index=True /
    Index() 在老库上永远不生效——排序仍走全表扫描 + 临时 B 树。本函数按元数据逐条建
    缺失索引（checkfirst=True，等价于 CREATE INDEX IF NOT EXISTS），对新库/老库都幂等。
    唯一索引在历史重复未清理前不会创建（见 ensure_indexes）。
    """
    url = settings.database_url if database_url is None else database_url
    if not url.startswith("sqlite"):
        return []
    target = engine if bind is None else bind
    meta = Base.metadata if metadata is None else metadata
    created = ensure_indexes(target, meta)
    if created:
        logger.info("SQLite 索引补齐：新建 %d 个（历史库升级后首次启动）：%s", len(created), ", ".join(created))
    ensure_active_contract_index(session_factory)
    return created


def create_schema() -> None:
    """启动建表编排：建缺失表 → 补列 → 补索引（全部幂等）。"""
    Base.metadata.create_all(bind=engine)
    ensure_columns()
    ensure_sqlite_indexes()
