"""数据初始化脚本。

用法（在 backend 目录下）：
    python -m app.init_db           # 建表；若为空库则写入示例数据
    python -m app.init_db --reset   # 清空并重建全部数据（含示例数据）
"""
import argparse

from .database import Base, SessionLocal, engine
from .seed import seed_if_empty


def main() -> None:
    parser = argparse.ArgumentParser(description="个人工作台数据初始化")
    parser.add_argument("--reset", action="store_true", help="清空已有数据并重建")
    args = parser.parse_args()

    if args.reset:
        Base.metadata.drop_all(bind=engine)
        print("已清空原有数据。")

    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        counts = seed_if_empty(db)

    if counts:
        print(f"已写入示例数据：任务 {counts['tasks']} 条、日程 {counts['schedules']} 条、笔记 {counts['notes']} 条。")
    else:
        print("数据库已有数据，跳过示例数据写入。")
    print("数据库初始化完成。")


if __name__ == "__main__":
    main()
