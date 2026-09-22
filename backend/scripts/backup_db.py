"""SQLite 在线热备份：运行中也可安全备份 workbench.db。

用法（在 backend 目录或项目根目录均可）：
    python backend/scripts/backup_db.py

- 使用 sqlite3 Connection.backup API，不锁库、不拷半成品文件；
- 产物带时间戳，放到项目根目录 backups/，自动清理只保留最近 30 份。
"""
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
PROJECT_DIR = BACKEND_DIR.parent
DB_PATH = BACKEND_DIR / "data" / "workbench.db"
BACKUP_DIR = PROJECT_DIR / "backups"
KEEP = 30


def main() -> int:
    if not DB_PATH.exists():
        print(f"未找到数据库文件：{DB_PATH}，请先启动一次服务让它生成数据。")
        return 1

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = BACKUP_DIR / f"workbench_{stamp}.db"

    # 在线备份：源库只读打开，逐页拷贝，运行中的服务不受影响
    src = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    try:
        dst = sqlite3.connect(dest)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()

    size_kb = dest.stat().st_size // 1024
    print(f"备份成功：{dest}（{size_kb} KB）")

    # 只保留最近 KEEP 份
    backups = sorted(BACKUP_DIR.glob("workbench_*.db"))
    for old in backups[:-KEEP]:
        old.unlink()
        print(f"已清理旧备份：{old.name}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
