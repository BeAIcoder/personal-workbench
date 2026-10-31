"""SQLite 在线热备份 + 数据目录整包备份。

用法（在 backend 目录或项目根目录均可）：
    python backend/scripts/backup_db.py

产物（项目根目录 backups/，各保留最近 30 份）：
- workbench_<时间戳>.db        —— 仅数据库（在线一致快照，兼容旧用法）
- workbench_data_<时间戳>.zip  —— 数据库 + uploads/finance/skills/expert-packs/
                                  debug 与 data 根下的 JSON 配置

为什么还要 ZIP（BUGfix TL-03）：财务导入批次、聊天附件、自建技能包、专家包、
WorkBuddy 导入配置都在 backend/data 下的文件里，只备份 workbench.db 会在
磁盘故障或「只带 DB 迁移」时全部丢失。
"""
import sqlite3
import sys
import zipfile
from datetime import datetime
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
PROJECT_DIR = BACKEND_DIR.parent
DB_PATH = BACKEND_DIR / "data" / "workbench.db"
DATA_DIR = BACKEND_DIR / "data"
BACKUP_DIR = PROJECT_DIR / "backups"
KEEP = 30

# 需要随包备份的数据子目录（财务/附件/技能/专家包/调试）
DATA_SUBDIRS = ("uploads", "finance", "skills", "expert-packs", "debug")
# 这些文件名后缀是运行时临时/锁文件，不进备份
SKIP_SUFFIXES = (".tmp", ".bak", ".wal", ".shm", ".lock")


def backup_database(db_path: Path = DB_PATH, backup_dir: Path = BACKUP_DIR,
                    stamp: str | None = None) -> Path:
    """在线热备份数据库，返回备份文件路径（源库只读，运行中也可安全备份）。"""
    if not db_path.exists():
        raise FileNotFoundError(f"未找到数据库文件：{db_path}")
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = stamp or datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = backup_dir / f"workbench_{stamp}.db"
    src = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        dst = sqlite3.connect(dest)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    return dest


def _iter_data_files(data_dir: Path):
    """遍历需要备份的数据文件，跳过临时/锁文件。"""
    for sub in DATA_SUBDIRS:
        root = data_dir / sub
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*")):
            if path.is_file() and path.suffix.lower() not in SKIP_SUFFIXES:
                yield path, f"{sub}/{path.relative_to(root).as_posix()}"
    for path in sorted(data_dir.glob("*.json")):
        if path.is_file():
            yield path, path.name


def backup_data_zip(db_backup: Path, data_dir: Path = DATA_DIR,
                    backup_dir: Path = BACKUP_DIR, stamp: str | None = None) -> tuple[Path, list[str]]:
    """把数据库快照 + 数据文件打成 ZIP，返回（zip 路径, 跳过文件警告）。

    单个文件被占用时跳过并记录，不因为一个附件让整次备份失败。
    """
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = stamp or datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = backup_dir / f"workbench_data_{stamp}.zip"
    warnings: list[str] = []
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(db_backup, "workbench.db")
        for path, arcname in _iter_data_files(data_dir):
            try:
                zf.write(path, arcname)
            except OSError as exc:
                warnings.append(f"{arcname} 跳过（{exc}）")
    return dest, warnings


def prune(backup_dir: Path = BACKUP_DIR, keep: int = KEEP) -> list[str]:
    """按前缀各保留最近 keep 份，返回被清理的文件名。"""
    removed: list[str] = []
    for pattern in ("workbench_*.db", "workbench_data_*.zip"):
        files = sorted(backup_dir.glob(pattern))
        for old in files[:-keep]:
            old.unlink()
            removed.append(old.name)
    return removed


def main() -> int:
    if not DB_PATH.exists():
        print(f"未找到数据库文件：{DB_PATH}，请先启动一次服务让它生成数据。")
        return 1

    db_backup = backup_database()
    print(f"数据库备份成功：{db_backup}（{db_backup.stat().st_size // 1024} KB）")

    zip_path, warnings = backup_data_zip(db_backup)
    size_mb = zip_path.stat().st_size / 1024 / 1024
    print(f"数据整包备份成功：{zip_path}（{size_mb:.2f} MB，含数据库与文件资产）")
    for w in warnings:
        print(f"  警告：{w}")

    for name in prune():
        print(f"已清理旧备份：{name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
