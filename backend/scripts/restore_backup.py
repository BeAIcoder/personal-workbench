"""从数据整包备份恢复 backend/data（原子恢复）。

用法：
    python backend/scripts/restore_backup.py backups/workbench_data_YYYYmmdd_HHMMSS.zip
    python backend/scripts/restore_backup.py <zip> --target <目录> --force

安全约束：
- ZIP 内条目必须是相对路径，拒绝 `..`/绝对路径/盘符（防 zip-slip）；
- 拒绝 Windows 保留设备名（CON/NUL/COM1…）与结尾点/空格条目；
- 条目数与解压总量有上限（防 zip bomb）；
- **原子**：先整体解压到同卷临时目录 → 校验库完整性 → 再整目录换入。
  换入前旧数据原样保留（整体改名为 .before_restore_*），任何一步失败
  目标目录都保持原状，不会出现「库已覆盖、文件没写完」的半恢复状态。
"""
import argparse
import re
import shutil
import sqlite3
import sys
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path, PurePosixPath

BACKEND_DIR = Path(__file__).resolve().parent.parent
DEFAULT_TARGET = BACKEND_DIR / "data"
# 防恶意/损坏备份包：条目数与解压总量上限
MAX_ENTRIES = 50_000
MAX_TOTAL_UNCOMPRESSED = 2 * 1024 ** 3  # 2 GiB
_DEVICE_NAME_RE = re.compile(
    r"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(\.|$)", re.IGNORECASE)


def _safe_entries(zf: zipfile.ZipFile) -> list[str]:
    names: list[str] = []
    for info in zf.infolist():
        name = info.filename.replace("\\", "/")
        pure = PurePosixPath(name)
        if pure.is_absolute() or ".." in pure.parts or (len(name) > 1 and name[1] == ":"):
            raise ValueError(f"备份包含非法路径：{info.filename}")
        stem = pure.name
        if _DEVICE_NAME_RE.match(stem) or stem != stem.rstrip(". "):
            raise ValueError(f"备份包含非法文件名：{info.filename}")
        if name.endswith("/"):
            continue
        names.append(name)
    if len(names) > MAX_ENTRIES:
        raise ValueError(f"备份条目数超限（>{MAX_ENTRIES}），疑似 zip bomb，拒绝恢复")
    total = sum(info.file_size for info in zf.infolist() if not info.filename.endswith("/"))
    if total > MAX_TOTAL_UNCOMPRESSED:
        raise ValueError(f"备份解压总量超限（>{MAX_TOTAL_UNCOMPRESSED // (1024**2)} MiB），拒绝恢复")
    return names


def _check_db_integrity(db_path: Path) -> None:
    """恢复前校验库完整性：坏库一律拒绝，不判「恢复完成」。"""
    try:
        con = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
        try:
            row = con.execute("PRAGMA integrity_check").fetchone()
            if row is None or row[0] != "ok":
                raise ValueError(f"备份中的 workbench.db 完整性校验失败：{row[0] if row else '无结果'}")
        finally:
            con.close()
    except sqlite3.DatabaseError as exc:
        # 坏库/加密/截断：connect 或查询时抛 DatabaseError，统一转中文 ValueError
        raise ValueError(f"备份中的 workbench.db 无法读取（{exc}）") from exc


def restore(zip_path: Path, target: Path = DEFAULT_TARGET, force: bool = False) -> list[str]:
    zip_path = Path(zip_path)
    target = Path(target)
    if not zip_path.is_file():
        raise FileNotFoundError(f"备份文件不存在：{zip_path}")
    if (target / "workbench.db").exists() and not force:
        raise FileExistsError(
            f"目标目录已存在 workbench：{target}；请先停止服务，确认后用 --force 覆盖")

    target.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    # 与目标同卷的临时目录，保证最后的 rename 是同卷原子操作
    staging = Path(tempfile.mkdtemp(prefix=".restore_staging_", dir=target.parent))
    snapshot: Path | None = None
    try:
        with zipfile.ZipFile(zip_path) as zf:
            names = _safe_entries(zf)
            if "workbench.db" not in names:
                raise ValueError("备份包中缺少 workbench.db，无法恢复")
            for name in names:
                dest = (staging / name).resolve()
                try:
                    dest.relative_to(staging.resolve())
                except ValueError as exc:
                    raise ValueError(f"备份条目越界：{name}") from exc
                dest.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(name) as src, dest.open("wb") as out:
                    shutil.copyfileobj(src, out)
        _check_db_integrity(staging / "workbench.db")

        if target.exists():
            snapshot = target.parent / f"data.before_restore_{stamp}"
            # 整目录快照：任何文件被占用（服务未停）都会在这里失败，
            # 此时 staging 只是临时目录，目标目录原样保留
            target.rename(snapshot)
        staging.rename(target)
        return names
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="从 ZIP 备份原子恢复 backend/data")
    parser.add_argument("zip", type=Path, help="workbench_data_19700101_000000.zip 路径")
    parser.add_argument("--target", type=Path, default=DEFAULT_TARGET, help="恢复目标目录（默认 backend/data）")
    parser.add_argument("--force", action="store_true", help="目标已有 workbench.db 时仍覆盖")
    args = parser.parse_args()
    try:
        names = restore(args.zip, args.target, args.force)
    except (FileNotFoundError, FileExistsError, ValueError, OSError) as exc:
        print(f"恢复失败：{exc}")
        return 1
    print(f"恢复完成：{len(names)} 个文件 → {args.target}")
    prev = sorted(target.parent.glob("data.before_restore_*"))
    if prev:
        print(f"旧数据完整快照：{prev[-1]}")
    print("请重启服务；若恢复的是旧库，启动时的轻量迁移会自动补列/补索引。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
