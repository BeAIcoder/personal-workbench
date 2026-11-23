# -*- coding: utf-8 -*-
"""存量检查：同铺位多份 active 合同（默认只读，不删除任何数据）。

用法（backend 目录）：
    PYTHONIOENCODING=utf-8 venv/Scripts/python.exe -m app.modules.business_operations.duplicates
    PYTHONIOENCODING=utf-8 venv/Scripts/python.exe -m app.modules.business_operations.duplicates --apply-index

`--apply-index` 仅在「无 active 重复」时补建部分唯一索引；有重复时只输出清单。
返回码：0 = 无重复（或索引已补建）；1 = 存在重复，需人工核对后终止多余合同。
"""
from __future__ import annotations

import argparse
import json
import sys

from ...database import SessionLocal
from .models import OpsContract
from .service import ensure_active_unit_index, find_active_unit_duplicates


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="同铺位 active 合同重复检查")
    parser.add_argument(
        "--apply-index", action="store_true",
        help="无 active 重复时补建部分唯一索引（幂等；有重复不会建）",
    )
    args = parser.parse_args(argv)

    with SessionLocal() as db:
        duplicates = find_active_unit_duplicates(db)
        total = db.query(OpsContract).count()
        applied = None
        if args.apply_index:
            applied = ensure_active_unit_index(db)["created"]

    if not duplicates:
        message = f"OK：合同 {total} 份，同铺位 active 重复 0 组"
        if applied is not None:
            message += "；部分唯一索引已补建" if applied else "；部分唯一索引已存在"
        print(message)
        return 0

    print(json.dumps(duplicates, ensure_ascii=False, indent=2))
    print(
        f"发现 {len(duplicates)} 组同铺位 active 重复合同；本检查只读，未删除任何数据。"
        "请人工核对后终止多余合同，再重启服务（或加 --apply-index）让部分唯一索引生效。"
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
