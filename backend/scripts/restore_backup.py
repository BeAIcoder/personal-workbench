"""从数据整包备份恢复 backend/data（原子恢复 + 可回滚）。

用法：
    python backend/scripts/restore_backup.py backups/workbench_data_YYYYmmdd_HHMMSS.zip
    python backend/scripts/restore_backup.py <zip> --target <目录> --force

安全约束：
- ZIP 内条目必须是相对路径，拒绝 `..`/绝对路径/盘符/Windows ADS（`file:ads`）；
- 拒绝 Windows 保留设备名（CON/NUL/COM1…）与结尾点/空格条目；
- 条目数与**实际解压字节**都有上限（中央目录 file_size 可被谎报，不能作为唯一依据）；
- 数据库必须非空、`integrity_check=ok` 且至少含一张业务表或非零 user_version；
- **原子**：先整体解压到同卷临时目录 → 校验库完整性 → 本机配置/外部财务目录/
  数据目录依次换入；任何一步失败都把已完成步骤回滚，目标目录保持原状；
- 旧数据快照保留有界（MAX_SNAPSHOTS），前缀跟随 target/finance 目录名；
- 本机敏感配置落回 project_root 前先写 `backups/restore-local/local_*` 快照；
- 显式 `--finance-target` 的外部目录会分流 `finance/` 条目并保留旧目录快照；
  `target==WORKBENCH_DATA_DIR` 且 FINANCE_DATA_DIR 是 target 内自定义目录时默认分流向该目录，
  其它 target 内自定义配置在应用前 fail-closed 拒绝。
"""
import argparse
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path, PurePosixPath

BACKEND_DIR = Path(__file__).resolve().parent.parent
PROJECT_DIR = BACKEND_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.config import DATA_DIR, get_finance_data_dir, settings  # noqa: E402

DEFAULT_TARGET = DATA_DIR
# 防恶意/损坏备份包：条目数、实际解压总量、旧数据快照保留数
MAX_ENTRIES = 50_000
MAX_TOTAL_UNCOMPRESSED = 2 * 1024 ** 3  # 2 GiB（按实际写入字节累计）
MAX_SNAPSHOTS = 3
# 与 backup_db.LOCAL_CONFIG_FILES 保持一致（契约测试会校验两者不漂移）
LOCAL_CONFIG_ARCHIVES = (
    "backend/.env",
    "backend/app/modules/finance_bankflow/config.local.yaml",
    "dev/sanitize-map.local.json",
    "dev/pii-terms.local.txt",
)
_LOCAL_STAGING = ".local-config"
_FINANCE_STAGING = ".external-finance"
_LOCAL_SNAPSHOT_ROOT = Path("backups") / "restore-local"
_LOCAL_SNAPSHOT_PREFIX = "local_"
_DEVICE_NAME_RE = re.compile(
    r"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(\.|$)", re.IGNORECASE)


def _is_within(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _safe_entries(zf: zipfile.ZipFile) -> list[str]:
    names: list[str] = []
    for info in zf.infolist():
        name = info.filename.replace("\\", "/")
        pure = PurePosixPath(name)
        # 任意位置的 ":" 都拒绝：盘符与 Windows 备用数据流（file:ads）都不能落盘
        if pure.is_absolute() or ".." in pure.parts or ":" in name:
            raise ValueError(f"备份包含非法路径：{info.filename}")
        stem = pure.name
        if _DEVICE_NAME_RE.match(stem) or stem != stem.rstrip(". "):
            raise ValueError(f"备份包含非法文件名：{info.filename}")
        if name.endswith("/"):
            continue
        names.append(name)
    if len(names) > MAX_ENTRIES:
        raise ValueError(f"备份条目数超限（>{MAX_ENTRIES}），疑似 zip bomb，拒绝恢复")
    # 元数据预检只用于快速失败；真正的限额在 _extract_member 按实际字节执行
    total = sum(info.file_size for info in zf.infolist() if not info.filename.endswith("/"))
    if total > MAX_TOTAL_UNCOMPRESSED:
        raise ValueError(f"备份解压总量超限（>{MAX_TOTAL_UNCOMPRESSED // (1024**2)} MiB），拒绝恢复")
    return names


def _check_db_integrity(db_path: Path) -> None:
    """恢复前校验库：0 字节、坏库、无业务表的占位库一律拒绝。"""
    if db_path.stat().st_size <= 0:
        raise ValueError("备份中的 workbench.db 为空（0 字节），无法恢复")
    try:
        con = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
        try:
            row = con.execute("PRAGMA integrity_check").fetchone()
            if row is None or row[0] != "ok":
                raise ValueError(f"备份中的 workbench.db 完整性校验失败：{row[0] if row else '无结果'}")
            tables = con.execute(
                "SELECT count(*) FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%'").fetchone()[0]
            version = con.execute("PRAGMA user_version").fetchone()[0]
            if not tables and not version:
                raise ValueError("备份中的 workbench.db 缺少业务表，不是可用的工作台库")
        finally:
            con.close()
    except sqlite3.DatabaseError as exc:
        # 坏库/加密/截断：connect 或查询时抛 DatabaseError，统一转中文 ValueError
        raise ValueError(f"备份中的 workbench.db 无法读取（{exc}）") from exc


def _extract_member(zf: zipfile.ZipFile, name: str, dest: Path, budget: dict) -> None:
    """逐个成员写盘，按实际读到的字节累计限额（不信任中央目录 file_size）。"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    with zf.open(name) as src, dest.open("wb") as out:
        while True:
            chunk = src.read(1024 * 1024)
            if not chunk:
                break
            budget["used"] += len(chunk)
            if budget["used"] > budget["limit"]:
                raise ValueError(
                    f"备份解压总量超限（>{budget['limit'] // (1024**2)} MiB），拒绝恢复")
            out.write(chunk)


def _unique_path(parent: Path, stem: str) -> Path:
    candidate = parent / stem
    suffix = 2
    while candidate.exists():
        candidate = parent / f"{stem}_{suffix}"
        suffix += 1
    return candidate


def _unique_snapshot(parent: Path, name: str, stamp: str) -> Path:
    return _unique_path(parent, f"{name}.before_restore_{stamp}")


def _prune_snapshots(parent: Path, name: str, keep: int | None = None) -> list[str]:
    """旧数据/财务目录快照只保留最近 keep 份，避免每次 --force 恢复无限堆积。"""
    keep = MAX_SNAPSHOTS if keep is None else keep
    snaps = sorted(parent.glob(f"{name}.before_restore_*"), key=lambda p: p.name)
    removed: list[str] = []
    for old in snaps[:-keep]:
        shutil.rmtree(old, ignore_errors=True)
        removed.append(old.name)
    return removed


def _snapshot_local_configs(source_root: Path, project_root: Path, stamp: str):
    """覆盖前把已存在的本机配置复制到 backups/restore-local/local_*，返回 (快照目录, 将写列表)。"""
    snapshot_root = project_root / _LOCAL_SNAPSHOT_ROOT
    snapshot = _unique_path(snapshot_root, f"{_LOCAL_SNAPSHOT_PREFIX}{stamp}")
    targets: list[str] = []
    for arcname in LOCAL_CONFIG_ARCHIVES:
        if not (source_root / arcname).is_file():
            continue
        targets.append(arcname)
        current = project_root / arcname
        if current.is_file():
            snapshot.mkdir(parents=True, exist_ok=True)
            dest = snapshot / arcname
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(current, dest)
    return (snapshot if snapshot.exists() else None), targets


def _prune_local_snapshots(project_root: Path, keep: int | None = None) -> list[str]:
    """本机配置快照只保留最近 keep 份（目录在 backups/restore-local 下）。"""
    keep = MAX_SNAPSHOTS if keep is None else keep
    root = project_root / _LOCAL_SNAPSHOT_ROOT
    if not root.exists():
        return []
    snaps = sorted(root.glob(f"{_LOCAL_SNAPSHOT_PREFIX}*"), key=lambda p: p.name)
    removed: list[str] = []
    for old in snaps[:-keep]:
        shutil.rmtree(old, ignore_errors=True)
        removed.append(old.name)
    return removed


def _apply_local_configs(source_root: Path, project_root: Path, targets: list[str],
                         applied: list[Path]) -> None:
    for arcname in targets:
        src = source_root / arcname
        dest = project_root / arcname
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".restore-tmp")
        applied.append(dest)  # 先记录：即使 copy2 中途失败，回滚也能清理
        try:
            shutil.copy2(src, tmp)
            os.replace(tmp, dest)
        except BaseException:
            try:
                tmp.unlink()
            except FileNotFoundError:
                pass
            raise


def _rollback_local_configs(snapshot: Path | None, applied: list[Path], project_root: Path) -> None:
    for dest in applied:
        for candidate in (dest, dest.with_name(dest.name + ".restore-tmp")):
            try:
                candidate.unlink()
            except FileNotFoundError:
                pass
    if snapshot is not None and snapshot.exists():
        for old in snapshot.rglob("*"):
            if old.is_file():
                dest = project_root / old.relative_to(snapshot)
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(old, dest)
        shutil.rmtree(snapshot, ignore_errors=True)


def _snapshot_dir(dest: Path, stamp: str) -> Path | None:
    if not dest.exists():
        return None
    snapshot = _unique_snapshot(dest.parent, dest.name, stamp)
    dest.rename(snapshot)
    return snapshot


def _rollback_dir(dest: Path, snapshot: Path | None) -> None:
    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)
    if snapshot is not None and snapshot.exists():
        snapshot.rename(dest)


def _install_dir(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dest))


def restore(zip_path: Path, target: Path = DEFAULT_TARGET, force: bool = False,
            project_root: Path | None = None,
            finance_target: Path | None = None) -> list[str]:
    zip_path = Path(zip_path)
    target = Path(target)
    project_root = Path(project_root) if project_root is not None else PROJECT_DIR
    default_finance = target / "finance"
    target_resolved = target.resolve()
    default_finance_resolved = default_finance.resolve()
    configured_finance = (
        get_finance_data_dir() if settings.finance_data_dir.strip() else None
    )
    explicit_finance = finance_target is not None
    if finance_target is None:
        if configured_finance is not None and _is_within(configured_finance, target) \
                and configured_finance.resolve() != default_finance_resolved:
            # target==WORKBENCH_DATA_DIR 时，内嵌自定义财务目录默认可安全分流
            if target_resolved == DATA_DIR.resolve():
                finance_target = configured_finance
            else:
                raise ValueError(
                    f"FINANCE_DATA_DIR={configured_finance} 位于 target 内且不是 target/finance；"
                    "请修改 FINANCE_DATA_DIR，或显式指定外部 --finance-target 目录"
                )
        else:
            finance_target = default_finance
    finance_target = Path(finance_target)
    finance_resolved = finance_target.resolve()
    if finance_resolved == target_resolved or _is_within(target, finance_target):
        raise ValueError("finance_target 不能是 target 自身或其祖先目录")
    internal_custom_finance = (
        _is_within(finance_target, target) and finance_resolved != default_finance_resolved
    )
    if internal_custom_finance and (explicit_finance or target_resolved != DATA_DIR.resolve()):
        raise ValueError(
            "finance_target 位于 target 内时只能是 target/finance；"
            "请修改 FINANCE_DATA_DIR，或显式指定外部 --finance-target 目录"
        )
    external_finance = not _is_within(finance_target, target)
    internal_finance_rel = (
        finance_resolved.relative_to(target_resolved) if internal_custom_finance else None
    )
    if not zip_path.is_file():
        raise FileNotFoundError(f"备份文件不存在：{zip_path}")
    if (target / "workbench.db").exists() and not force:
        raise FileExistsError(
            f"目标目录已存在 workbench：{target}；请先停止服务，确认后用 --force 覆盖")

    target.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    # 与目标同卷的临时目录，保证最后的 rename 是同卷原子操作
    staging = Path(tempfile.mkdtemp(prefix=".restore_staging_", dir=target.parent))
    local_snapshot: Path | None = None
    local_applied: list[Path] = []
    finance_snapshot: Path | None = None
    finance_installed = False
    target_snapshot: Path | None = None
    budget = {"used": 0, "limit": MAX_TOTAL_UNCOMPRESSED}
    try:
        with zipfile.ZipFile(zip_path) as zf:
            names = _safe_entries(zf)
            if "workbench.db" not in names:
                raise ValueError("备份包中缺少 workbench.db，无法恢复")
            for name in names:
                if name in LOCAL_CONFIG_ARCHIVES:
                    dest = (staging / _LOCAL_STAGING / name).resolve()
                elif internal_custom_finance and name.startswith("finance/"):
                    dest = (staging / internal_finance_rel / name[len("finance/"):]).resolve()
                elif external_finance and name.startswith("finance/"):
                    dest = (staging / _FINANCE_STAGING / name[len("finance/"):]).resolve()
                else:
                    dest = (staging / name).resolve()
                try:
                    dest.relative_to(staging.resolve())
                except ValueError as exc:
                    raise ValueError(f"备份条目越界：{name}") from exc
                _extract_member(zf, name, dest, budget)
        _check_db_integrity(staging / "workbench.db")

        try:
            # 1) 本机敏感配置：先快照、再写；失败或后续步骤失败时回滚
            local_source = staging / _LOCAL_STAGING
            if local_source.is_dir():
                local_snapshot, local_targets = _snapshot_local_configs(
                    local_source, project_root, stamp)
                _apply_local_configs(local_source, project_root, local_targets, local_applied)
                shutil.rmtree(local_source, ignore_errors=True)
            # 2) 外部财务目录：先快照、再换入
            finance_source = staging / _FINANCE_STAGING
            if external_finance and finance_source.is_dir():
                finance_snapshot = _snapshot_dir(finance_target, stamp)
                finance_installed = True
                _install_dir(finance_source, finance_target)
            # 3) 数据目录最后换入
            if target.exists():
                target_snapshot = _unique_snapshot(target.parent, target.name, stamp)
                target.rename(target_snapshot)
            try:
                staging.rename(target)
            except OSError:
                # 第二次 rename 失败：把旧数据原样换回，避免目标目录凭空消失
                if target_snapshot is not None and target_snapshot.exists() and not target.exists():
                    target_snapshot.rename(target)
                    target_snapshot = None
                raise
        except BaseException as exc:
            # I-A：Ctrl+C/SystemExit 也要回滚；各步骤独立 try，回滚失败不掩盖原异常
            rollback_errors: list[str] = []
            if finance_installed:
                try:
                    _rollback_dir(finance_target, finance_snapshot)
                except BaseException as rollback_exc:
                    rollback_errors.append(f"财务目录回滚失败：{rollback_exc}")
            if local_applied:
                try:
                    _rollback_local_configs(local_snapshot, local_applied, project_root)
                except BaseException as rollback_exc:
                    rollback_errors.append(f"本机配置回滚失败：{rollback_exc}")
            if target_snapshot is not None and target_snapshot.exists() and not target.exists():
                try:
                    target_snapshot.rename(target)
                except BaseException as rollback_exc:
                    rollback_errors.append(f"数据目录回滚失败：{rollback_exc}")
            if rollback_errors:
                snapshots = [str(p) for p in (finance_snapshot, target_snapshot, local_snapshot) if p]
                raise RuntimeError(
                    f"恢复失败且回滚未完成：{exc}；半完成状态："
                    + "；".join(rollback_errors)
                    + f"；快照：{', '.join(snapshots) or '无'}"
                ) from exc
            raise

        _prune_snapshots(target.parent, target.name)
        if finance_installed:
            _prune_snapshots(finance_target.parent, finance_target.name)
        _prune_local_snapshots(project_root)
        return [name for name in names if name not in LOCAL_CONFIG_ARCHIVES]
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="从 ZIP 备份原子恢复 backend/data")
    parser.add_argument("zip", type=Path, help="workbench_data_19700101_000000.zip 路径")
    parser.add_argument("--target", type=Path, default=DEFAULT_TARGET, help="恢复目标目录（默认 WORKBENCH_DATA_DIR/backend/data）")
    parser.add_argument("--project", type=Path, default=PROJECT_DIR, help="本机配置落回的项目根目录")
    parser.add_argument("--finance-target", type=Path, default=None,
                        help="显式外部财务数据目录（默认 <target>/finance；不指定时不分流）")
    parser.add_argument("--force", action="store_true", help="目标已有 workbench.db 时仍覆盖")
    args = parser.parse_args()
    if args.finance_target is None and settings.finance_data_dir.strip():
        configured = get_finance_data_dir()
        target_for_hint = Path(args.target)
        default_finance = target_for_hint / "finance"
        inside_custom = (
            _is_within(configured, target_for_hint)
            and configured.resolve() != default_finance.resolve()
        )
        if inside_custom:
            if target_for_hint.resolve() != DATA_DIR.resolve():
                print(
                    f"错误：FINANCE_DATA_DIR={configured} 位于 target 内且不是 target/finance；"
                    "请修改 FINANCE_DATA_DIR，或显式指定外部 --finance-target 目录",
                    file=sys.stderr,
                )
                return 1
            # target==WORKBENCH_DATA_DIR：restore 会自动分流向该内嵌目录，不打印误导提示
        elif configured.resolve() != default_finance.resolve():
            print(
                f"提示：检测到 FINANCE_DATA_DIR={configured}，但未指定 --finance-target；"
                f"本次 finance/ 将恢复到 {default_finance}。"
                f"如需分流，请加 --finance-target \"{configured}\"",
                file=sys.stderr,
            )
    try:
        names = restore(args.zip, args.target, args.force, project_root=args.project,
                        finance_target=args.finance_target)
    except (FileNotFoundError, FileExistsError, ValueError, OSError, RuntimeError) as exc:
        print(f"恢复失败：{exc}")
        return 1
    print(f"恢复完成：{len(names)} 个数据文件 → {args.target}")
    prev = sorted(args.target.parent.glob(f"{args.target.name}.before_restore_*"))
    if prev:
        print(f"旧数据完整快照：{prev[-1]}（仅保留最近 {MAX_SNAPSHOTS} 份）")
    print("请重启服务；若恢复的是旧库，启动时的轻量迁移会自动补列/补索引。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
