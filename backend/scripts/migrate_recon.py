# -*- coding: utf-8 -*-
"""商户核销对账（finance_recon）一次性升级脚本：ever_ready 补列回填 + 唯一索引补建。

用法（在 backend 目录下；数据库路径必须显式给出，本脚本不读 backend/.env、
不触碰 backend/data、绝不写真实业务数据之外的任何位置）：

    venv/Scripts/python scripts/migrate_recon.py --db <SQLite 路径>           # 全部（幂等）
    venv/Scripts/python scripts/migrate_recon.py --db <路径> --dry-run        # 只读预览回填计划
    venv/Scripts/python scripts/migrate_recon.py --db <路径> --status         # 只读统计

职责（Task 3 修复轮 2，对应独立复核 C1 / I3 / I2）：
- 补列：mod_recon_batch.ever_ready（老库缺列时 ALTER ADD COLUMN INTEGER NOT NULL
  DEFAULT 0；与 manifest.column_patches 的启动补列同 DDL，先执行者生效、幂等）；
- 回填（保守原则）：status='ready' → ever_ready=1（已有 ready 必须有审计标记）；
  无法证明未 ready 的 legacy failed / checking → 1（ready→checking/failed 无法反证）；
  draft → 保持 0（从未就绪）；每类数量如实输出；重复执行零改动（幂等）；
- 唯一索引：mod_recon_issue (batch_id, origin, fingerprint) unique 索引补建；
  建前查重：存在重复键 → 显式中文报错退出（不删任何数据，提示人工清理）；
  同名索引已存在 → 校验必须 unique 且列一致：非唯一或列不符显式中文报错
  （不自动删除/替换任何索引）；与模型一致 → 跳过（幂等）。bootstrap
  ensure_indexes 的既有语义：老库补建走 table.indexes（Index 对象），
  遇重复键/建失败告警跳过、不删数据；
- 事务：全部改动单事务提交，任一步失败整体回滚（SQLite DDL 可回滚）；
- 模块表尚不存在的库（finance_recon 未启用）→ 跳过并如实输出，不算失败。

退出码：0 = 成功；1 = 失败（中文原因打印到 stderr）。
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

INDEX_NAME = "uq_recon_issue_fingerprint"
INDEX_COLUMNS = ("batch_id", "origin", "fingerprint")
# 保守回填状态集：ready 必然曾就绪；legacy failed/checking 无法反证从未就绪
_BACKFILL_STATUSES = ("ready", "failed", "checking")


class MigrationError(Exception):
    """迁移失败（显式中文原因；调用方整体回滚后向上传播）。"""


def _connect(db_path: str) -> sqlite3.Connection:
    path = Path(db_path)
    if not path.exists():
        raise MigrationError(f"数据库文件不存在：{db_path}")
    if path.is_dir():
        raise MigrationError(f"数据库路径是目录而非文件：{db_path}")
    try:
        conn = sqlite3.connect(str(path))
    except sqlite3.Error as exc:
        raise MigrationError(
            f"无法打开数据库（文件被占用或不可访问）：{exc}"
        ) from exc
    conn.row_factory = sqlite3.Row
    return conn


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


def _columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()]


def _index_detail(
    conn: sqlite3.Connection, table: str, index_name: str
) -> tuple[bool, tuple[str, ...]] | None:
    """已有同名索引 → (是否唯一, 列序)；不存在 → None。"""
    for row in conn.execute(f"PRAGMA index_list({table})").fetchall():
        if row[1] == index_name:
            cols = tuple(
                r[2]
                for r in conn.execute(f"PRAGMA index_info({index_name})").fetchall()
            )
            return bool(row[2]), cols
    return None


def _index_exists(conn: sqlite3.Connection, table: str, index_name: str) -> bool:
    return _index_detail(conn, table, index_name) is not None


def ensure_column(conn: sqlite3.Connection) -> bool:
    """mod_recon_batch.ever_ready 补列；已存在返回 False（幂等）。"""
    if not _table_exists(conn, "mod_recon_batch"):
        return False
    if "ever_ready" in _columns(conn, "mod_recon_batch"):
        return False
    conn.execute(
        "ALTER TABLE mod_recon_batch ADD COLUMN ever_ready INTEGER NOT NULL DEFAULT 0"
    )
    return True


def plan_backfill(conn: sqlite3.Connection) -> dict[str, int]:
    """只读：按保守规则统计将回填的行数（dry-run/status 用）。

    ever_ready 列尚不存在的老库：列将由 ensure_column 以 DEFAULT 0 补出，
    因此各保守状态的全部行都计入回填计划。"""
    if not _table_exists(conn, "mod_recon_batch"):
        return {}
    has_column = "ever_ready" in _columns(conn, "mod_recon_batch")
    plan: dict[str, int] = {}
    for status_value in _BACKFILL_STATUSES:
        if has_column:
            row = conn.execute(
                "SELECT COUNT(*) FROM mod_recon_batch "
                "WHERE status = ? AND (ever_ready IS NULL OR ever_ready = 0)",
                (status_value,),
            ).fetchone()
            plan[status_value] = row[0] if row else 0
        else:
            row = conn.execute(
                "SELECT COUNT(*) FROM mod_recon_batch WHERE status = ?", (status_value,)
            ).fetchone()
            plan[status_value] = row[0] if row else 0
    return plan


def apply_backfill(conn: sqlite3.Connection) -> dict[str, int]:
    """回填 ever_ready：ready → 1；legacy failed/checking（无法证明未就绪）→ 1；
    draft 不动。返回 {status: 回填行数}（幂等：第二次执行全为 0）。"""
    if not _table_exists(conn, "mod_recon_batch"):
        return {}
    applied: dict[str, int] = {}
    for status in _BACKFILL_STATUSES:
        cursor = conn.execute(
            "UPDATE mod_recon_batch SET ever_ready = 1 "
            "WHERE status = ? AND (ever_ready IS NULL OR ever_ready = 0)",
            (status,),
        )
        applied[status] = cursor.rowcount if cursor.rowcount and cursor.rowcount > 0 else 0
    return applied


def ensure_unique_index(conn: sqlite3.Connection) -> bool:
    """mod_recon_issue (batch_id, origin, fingerprint) 唯一索引补建。

    建前查重：存在重复键 → MigrationError（显式中文报错，不删数据）；
    索引已存在 → False（幂等）；新建 → True。"""
    if not _table_exists(conn, "mod_recon_issue"):
        return False
    existing = _index_detail(conn, "mod_recon_issue", INDEX_NAME)
    if existing is not None:
        unique, cols = existing
        if not unique:
            raise MigrationError(
                f"mod_recon_issue 已存在同名非唯一索引 {INDEX_NAME}，无法保证 "
                "(batch_id, origin, fingerprint) 唯一；请人工核对其用途并处理"
                "（本脚本不自动删除或替换任何索引）"
            )
        if cols != INDEX_COLUMNS:
            raise MigrationError(
                f"mod_recon_issue 已存在同名索引 {INDEX_NAME}，但其列为 "
                f"{list(cols)}，与期望列 {list(INDEX_COLUMNS)} 不一致；"
                "请人工核对后处理（本脚本不自动删除或替换任何索引）"
            )
        return False
    cols = ", ".join(INDEX_COLUMNS)
    dup = conn.execute(
        f"SELECT COUNT(*) FROM (SELECT 1 FROM mod_recon_issue "
        f"GROUP BY {cols} HAVING COUNT(*) > 1)"
    ).fetchone()
    dup_groups = dup[0] if dup else 0
    if dup_groups:
        raise MigrationError(
            f"mod_recon_issue 存在 {dup_groups} 组重复的 (batch_id, origin, fingerprint)，"
            f"无法创建唯一索引 {INDEX_NAME}；请人工核对并清理重复问题记录后重新执行"
            "（本脚本不自动删除任何数据）"
        )
    conn.execute(
        f"CREATE UNIQUE INDEX {INDEX_NAME} ON mod_recon_issue ({cols})"
    )
    return True


def migrate(db_path: str, dry_run: bool = False) -> dict:
    """执行全部迁移（单事务，失败整体回滚）；dry_run=True 只读预览不写库。"""
    summary: dict = {"dry_run": dry_run, "column_added": False, "backfill": {}, "index_created": False}
    conn = _connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        summary["column_added"] = ensure_column(conn)
        summary["backfill"] = plan_backfill(conn) if dry_run else apply_backfill(conn)
        if dry_run:
            conn.rollback()  # 只读预览：回滚任何隐式改动
            conn.close()
            return summary
        summary["index_created"] = ensure_unique_index(conn)
        conn.commit()
    except Exception as exc:  # noqa: BLE001  整体回滚后向上传播（CLI 映射退出码 1）
        conn.rollback()
        conn.close()
        if isinstance(exc, MigrationError):
            raise
        if isinstance(exc, sqlite3.OperationalError):
            raise MigrationError(
                f"数据库连接/锁定错误（迁移已整体回滚）：{exc}；"
                "请确认没有其他进程占用该数据库后重试"
            ) from exc
        raise MigrationError(f"迁移失败已回滚：{type(exc).__name__}: {exc}") from exc
    conn.close()
    return summary


def status(db_path: str) -> dict:
    """只读统计：回填计划、ever_ready 已置真行数、唯一索引是否已存在。"""
    conn = _connect(db_path)
    try:
        info: dict = {
            "table_batch_exists": _table_exists(conn, "mod_recon_batch"),
            "table_issue_exists": _table_exists(conn, "mod_recon_issue"),
            "column_ready": "ever_ready" in _columns(conn, "mod_recon_batch")
            if _table_exists(conn, "mod_recon_batch")
            else False,
            "index_exists": _index_exists(conn, "mod_recon_issue", INDEX_NAME)
            if _table_exists(conn, "mod_recon_issue")
            else False,
            "backfill_plan": plan_backfill(conn),
        }
        if info["table_batch_exists"] and info["column_ready"]:
            row = conn.execute(
                "SELECT COUNT(*) FROM mod_recon_batch WHERE ever_ready = 1"
            ).fetchone()
            info["ever_ready_true_rows"] = row[0] if row else 0
        else:
            info["ever_ready_true_rows"] = 0
        return info
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="商户核销对账（finance_recon）一次性升级：ever_ready 补列回填 + 唯一索引补建"
    )
    parser.add_argument("--db", required=True, help="SQLite 数据库文件路径（显式给出，不硬编码）")
    parser.add_argument("--dry-run", action="store_true", help="只读预览回填计划，不写库")
    parser.add_argument("--status", action="store_true", help="只读统计，不写库")
    args = parser.parse_args(argv)
    try:
        if args.status:
            info = status(args.db)
            print(
                "finance_recon 迁移状态："
                f"批次表={info['table_batch_exists']} 问题表={info['table_issue_exists']} "
                f"ever_ready 列={info['column_ready']} 唯一索引={info['index_exists']} "
                f"已置真行数={info['ever_ready_true_rows']} "
                f"待回填计划={info['backfill_plan']}"
            )
        elif args.dry_run:
            summary = migrate(args.db, dry_run=True)
            print(f"dry-run 预览（不写库）：将补列={summary['column_added']}，回填计划={summary['backfill']}")
        else:
            summary = migrate(args.db)
            parts = [f"补列={summary['column_added']}"]
            parts.extend(f"{name}={count}" for name, count in summary["backfill"].items())
            parts.append(f"唯一索引={summary['index_created']}")
            print("finance_recon 迁移完成（幂等，可重复执行）：" + "、".join(parts))
    except MigrationError as exc:
        print(f"迁移失败：{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
