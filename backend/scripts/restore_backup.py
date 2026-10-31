"""从数据整包备份恢复 backend/data。

用法：
    python backend/scripts/restore_backup.py backups/workbench_data_YYYYmmdd_HHMMSS.zip
    python backend/scripts/restore_backup.py <zip> --target <目录> --force

安全约束：
- ZIP 内条目必须是相对路径，拒绝 `..`/绝对路径/盘符（防 zip-slip）；
- 目标目录已有 workbench.db 且未加 --force 时拒绝覆盖（先停服务再恢复）；
- 恢复前把现有 workbench.db 复制为 workbench.db.before_restore_<时间戳>。
"""
import argparse
import shutil
import sys
import zipfile
from datetime import datetime
from pathlib import Path, PurePosixPath

BACKEND_DIR = Path(__file__).resolve().parent.parent
DEFAULT_TARGET = BACKEND_DIR / "data"


def _safe_entries(zf: zipfile.ZipFile) -> list[str]:
    names: list[str] = []
    for info in zf.infolist():
        name = info.filename.replace("\\", "/")
        pure = PurePosixPath(name)
        if pure.is_absolute() or ".." in pure.parts or (len(name) > 1 and name[1] == ":"):
            raise ValueError(f"备份包含非法路径：{info.filename}")
        if name.endswith("/"):
            continue
        names.append(name)
    return names


def restore(zip_path: Path, target: Path = DEFAULT_TARGET, force: bool = False) -> list[str]:
    if not zip_path.is_file():
        raise FileNotFoundError(f"备份文件不存在：{zip_path}")
    if (target / "workbench.db").exists() and not force:
        raise FileExistsError(
            f"目标目录已存在 workbench.db：{target}；请先停止服务，确认后用 --force 覆盖"
        )
    target.mkdir(parents=True, exist_ok=True)
    if (target / "workbench.db").exists():
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        shutil.copy2(target / "workbench.db", target / f"workbench.db.before_restore_{stamp}")
    restored: list[str] = []
    with zipfile.ZipFile(zip_path) as zf:
        for name in _safe_entries(zf):
            dest = (target / name).resolve()
            if not str(dest).startswith(str(target.resolve())):
                raise ValueError(f"备份条目越界：{name}")
            dest.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(name) as src, open(dest, "wb") as out:
                shutil.copyfileobj(src, out)
            restored.append(name)
    return restored


def main() -> int:
    parser = argparse.ArgumentParser(description="从 ZIP 备份恢复 backend/data")
    parser.add_argument("zip", type=Path, help="workbench_data_*.zip 路径")
    parser.add_argument("--target", type=Path, default=DEFAULT_TARGET, help="恢复目标目录（默认 backend/data）")
    parser.add_argument("--force", action="store_true", help="目标已有 workbench.db 时仍覆盖")
    args = parser.parse_args()
    try:
        names = restore(args.zip, args.target, args.force)
    except (FileNotFoundError, FileExistsError, ValueError) as exc:
        print(f"恢复失败：{exc}")
        return 1
    print(f"恢复完成：{len(names)} 个文件 → {args.target}")
    print("请重启服务；若恢复的是旧库，启动时的轻量迁移会自动补列/补索引。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
