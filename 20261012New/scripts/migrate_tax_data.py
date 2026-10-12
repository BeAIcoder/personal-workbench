"""一次性数据迁移：企业所得税计算（原项目）→ 工作台 finance_tax 模块。

用法（在 personal-workbench/backend 目录）：
    venv/Scripts/python.exe ../../20261012New/scripts/migrate_tax_data.py [--source <db路径>] [--dry-run]

- 源库默认：D:\\AssetLib\\01-财税自动化\\企业所得税计算\\企业所得税计算\\data\\enterprise_tax.db
- 目标库：工作台 backend/data/workbench.db（DATABASE_URL 决定）
- 表映射：enterprises→mod_tax_enterprise，financial_data→mod_tax_financial_data，
  tax_adjustments→mod_tax_adjustment，tax_returns→mod_tax_return，policies→mod_tax_policy
- 幂等：按业务唯一键（企业税号 / 政策文号）判重，已存在则跳过；可重复执行
- 原项目保留原位独立运行，本脚本只读源库、只追加目标库
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

DEFAULT_SOURCE = Path(r"D:\AssetLib\01-财税自动化\企业所得税计算\企业所得税计算\data\enterprise_tax.db")

# 源表 → (目标表, 业务唯一键列)
TABLE_MAP = {
    "enterprises": ("mod_tax_enterprise", "tax_number"),
    "financial_data": ("mod_tax_financial_data", None),   # 无业务唯一键，按企业+年度+季度判重
    "tax_adjustments": ("mod_tax_adjustment", None),
    "tax_returns": ("mod_tax_return", None),
    "policies": ("mod_tax_policy", "policy_number"),
}


def _target_db() -> str:
    backend = Path(__file__).resolve().parents[2] / "backend"
    sys.path.insert(0, str(backend))
    from app.config import settings  # noqa: E402

    url = settings.database_url
    if not url.startswith("sqlite:///"):
        raise SystemExit(f"仅支持 SQLite 目标库，当前 DATABASE_URL={url}")
    return url.replace("sqlite:///", "", 1)


def _columns(con, table: str) -> list[str]:
    return [r[1] for r in con.execute(f"PRAGMA table_info({table})")]


def migrate(source: Path, target: str, dry_run: bool = False) -> dict:
    src = sqlite3.connect(str(source))
    dst = sqlite3.connect(target)
    report: dict[str, dict] = {}
    try:
        for src_table, (dst_table, unique_key) in TABLE_MAP.items():
            try:
                cols = _columns(src, src_table)
            except sqlite3.OperationalError:
                report[src_table] = {"error": "源表不存在"}
                continue
            rows = src.execute(f"SELECT {', '.join(cols)} FROM {src_table}").fetchall()
            tgt_cols = _columns(dst, dst_table)
            common = [c for c in cols if c in tgt_cols]
            inserted = skipped = 0
            for row in rows:
                data = dict(zip(cols, row))
                if unique_key and unique_key in data:
                    exists = dst.execute(
                        f"SELECT 1 FROM {dst_table} WHERE {unique_key} = ? LIMIT 1",
                        (data[unique_key],),
                    ).fetchone()
                    if exists:
                        skipped += 1
                        continue
                elif src_table == "financial_data":
                    exists = dst.execute(
                        "SELECT 1 FROM mod_tax_financial_data WHERE enterprise_id=? AND year=? AND quarter=? LIMIT 1",
                        (data.get("enterprise_id"), data.get("year"), data.get("quarter")),
                    ).fetchone()
                    if exists:
                        skipped += 1
                        continue
                if not dry_run:
                    placeholders = ", ".join("?" for _ in common)
                    dst.execute(
                        f"INSERT INTO {dst_table} ({', '.join(common)}) VALUES ({placeholders})",
                        [data[c] for c in common],
                    )
                inserted += 1
            if not dry_run:
                dst.commit()
            report[src_table] = {"rows": len(rows), "inserted": inserted, "skipped": skipped}
    finally:
        src.close()
        dst.close()
    return report


def main() -> None:
    ap = argparse.ArgumentParser(description="企业所得税数据迁移到工作台 finance_tax 模块")
    ap.add_argument("--source", default=str(DEFAULT_SOURCE), help="源 SQLite 路径")
    ap.add_argument("--dry-run", action="store_true", help="只统计不写入")
    args = ap.parse_args()

    source = Path(args.source)
    if not source.is_file():
        raise SystemExit(f"源库不存在：{source}")
    target = _target_db()
    if not Path(target).is_file():
        raise SystemExit(f"目标库不存在（请先启动一次工作台建表）：{target}")

    report = migrate(source, target, dry_run=args.dry_run)
    print(f"源库：{source}")
    print(f"目标库：{target}")
    for table, r in report.items():
        print(f"  {table}: {r}")
    if args.dry_run:
        print("（dry-run，未写入）")


if __name__ == "__main__":
    main()
