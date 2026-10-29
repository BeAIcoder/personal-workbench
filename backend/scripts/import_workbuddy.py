# -*- coding: utf-8 -*-
"""WorkBuddy 数据导入 CLI（Part D）：复用 workbuddy_sync 服务，形态与 import_qwenpaw_agents.py 一致。

用法（backend 目录）：
  venv/Scripts/python scripts/import_workbuddy.py --dry-run          # 预览
  venv/Scripts/python scripts/import_workbuddy.py                    # 正式导入
  venv/Scripts/python scripts/import_workbuddy.py --from <路径>      # 指定 WorkBuddy 家目录
  venv/Scripts/python scripts/import_workbuddy.py --skills-only      # 只导技能包
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.agents import workbuddy_sync as wb  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="把本机 WorkBuddy 数据导入工作台")
    parser.add_argument("--from", dest="home", default="", help="WorkBuddy 数据目录（默认设置/环境变量/内置默认）")
    parser.add_argument("--dry-run", action="store_true", help="只预览不写库")
    parser.add_argument("--skills-only", action="store_true", help="只导入技能包")
    args = parser.parse_args()

    plan = wb.scan(args.home)
    print(f"WorkBuddy 目录：{plan['home']}（可用：{plan['available']}）")
    print(f"技能包 {len(plan['skills'])} 个；记忆分节 {len(plan['memories'])} 节；"
          f"身份文件 {len(plan['identity_files'])} 个")
    for s in plan["skills"]:
        mark = "已在池" if s["exists_in_store"] else "新增"
        print(f"  [技能/{mark}] {s['name']}（{s['title']}）")
    for m in plan["memories"]:
        print(f"  [记忆] {m['title']}（{m['chars']} 字）")
    for f in plan["identity_files"]:
        print(f"  [身份] {f['file']}（{f['chars']} 字）")
    if args.dry_run:
        print("\n--dry-run：未写入任何数据。")
        return 0

    from app.database import Base, engine, SessionLocal
    import app.models  # noqa: F401
    import app.modules  # noqa: F401
    Base.metadata.create_all(bind=engine)

    with SessionLocal() as db:
        result = wb.import_all(
            db, home=args.home,
            skills=True, memories=not args.skills_only, identity=not args.skills_only)
    print("\n导入完成：")
    print(f"  技能包：新增 {len(result['skills']['added'])}，更新 {len(result['skills']['updated'])}，"
          f"失败 {len(result['skills']['failed'])}")
    print(f"  记忆：新增 {result['memories']['added']}，近重复跳过 {result['memories']['skipped']}，"
          f"失败 {len(result['memories']['failed'])}")
    print(f"  身份：新增 {len(result['identity']['added'])}，更新 {len(result['identity']['updated'])}，"
          f"失败 {len(result['identity']['failed'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
