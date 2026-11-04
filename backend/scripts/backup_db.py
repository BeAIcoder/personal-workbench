"""SQLite 在线热备份 + 数据目录整包备份。

用法（在 backend 目录或项目根目录均可）：
    python backend/scripts/backup_db.py

产物（项目根目录 backups/，各保留最近 30 份）：
- workbench_<时间戳>.db        —— 仅数据库（在线一致快照，兼容旧用法）
- workbench_data_<时间戳>.zip  —— 数据库 + uploads/finance/skills/expert-packs/
                                  debug 与 data 根下的 JSON 配置
- workbench_data_<时间戳>.SENSITIVE.txt —— 仅当实际写入本机配置时生成的敏感标记

为什么还要 ZIP（BUGfix TL-03）：财务导入批次、聊天附件、自建技能包、专家包、
WorkBuddy 导入配置都在 backend/data 下的文件里，只备份 workbench.db 会在
磁盘故障或「只带 DB 迁移」时全部丢失。

配置来源（Task 9 / L-013、L-153）：
- 数据库路径从 `settings.database_url` 解析（不再硬编码 backend/data/workbench.db）；
- 数据根目录 `DATA_DIR` 与财务目录 `get_finance_data_dir()` 同源（支持
  WORKBENCH_DATA_DIR / FINANCE_DATA_DIR 覆盖）；
- 本机敏感配置（backend/.env、银行映射 config.local.yaml、脱敏钥匙、PII 词表）
  只在 CLI 主流程里按白名单收集；只在**实际写入**时给 ZIP 注释与同目录
  .SENSITIVE.txt 标记敏感；**这类备份只用于本机灾难恢复，绝不进入分发包**
  （发布扫描会拒绝这些文件名）。
"""
import sqlite3
import sys
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Iterable

BACKEND_DIR = Path(__file__).resolve().parent.parent
PROJECT_DIR = BACKEND_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.config import DATA_DIR, get_finance_data_dir, settings  # noqa: E402

BACKUP_DIR = PROJECT_DIR / "backups"
KEEP = 30

# 需要随包备份的数据子目录（财务/附件/技能/专家包/调试）
DATA_SUBDIRS = ("uploads", "finance", "skills", "expert-packs", "debug")
# 这些文件名后缀是运行时临时/锁文件，不进备份
SKIP_SUFFIXES = (".tmp", ".bak", ".wal", ".shm", ".lock")

# 本机敏感配置（相对仓库根）：只在本机备份中出现，分发包扫描会拒绝同名文件
LOCAL_CONFIG_FILES = (
    "backend/.env",
    "backend/app/modules/finance_bankflow/config.local.yaml",
    "dev/sanitize-map.local.json",
    "dev/pii-terms.local.txt",
)
SENSITIVE_COMMENT = (
    "SENSITIVE_LOCAL_BACKUP: contains local configuration/secrets; "
    "local restore only, never redistribute"
)
SENSITIVE_SIDECAR_TEXT = (
    "本备份包含本机配置/密钥（backend/.env、银行账号映射、脱敏钥匙、真实名称词表），"
    "属于敏感数据：只用于本机灾难恢复，禁止上传、禁止进入分发包。"
)


def resolve_db_path(database_url: str | None = None) -> Path:
    """从 SQLAlchemy URL 解析 SQLite 文件路径（备份只支持 SQLite）。"""
    from sqlalchemy.engine import make_url

    url = make_url(database_url or settings.database_url)
    if url.get_backend_name() != "sqlite":
        raise ValueError(f"备份仅支持 SQLite，当前 DATABASE_URL 为 {url.get_backend_name()}")
    database = url.database or ""
    if not database or database == ":memory:":
        raise ValueError("DATABASE_URL 未指向磁盘上的 SQLite 文件，无法备份")
    return Path(database)


def resolve_paths() -> tuple[Path, Path, Path]:
    """返回（数据库, 数据根目录, 财务目录），全部遵循当前配置。"""
    return resolve_db_path(), DATA_DIR, get_finance_data_dir()


def local_config_files(project_dir: Path = PROJECT_DIR) -> list[Path]:
    """按白名单收集真实存在的本机敏感配置（不读取内容，只交给 zipfile 写字节）。"""
    return [path for rel in LOCAL_CONFIG_FILES if (path := project_dir / rel).is_file()]


def backup_database(db_path: Path | None = None, backup_dir: Path | None = None,
                    stamp: str | None = None) -> Path:
    """在线热备份数据库，返回备份文件路径（源库只读，运行中也可安全备份）。"""
    db_path = Path(db_path) if db_path is not None else resolve_db_path()
    backup_dir = Path(backup_dir) if backup_dir is not None else BACKUP_DIR
    if not db_path.exists():
        raise FileNotFoundError(f"未找到数据库文件：{db_path}")
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = stamp or datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = backup_dir / f"workbench_{stamp}.db"
    src = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    try:
        dst = sqlite3.connect(dest)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    return dest


def _is_within(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _finance_covered(finance_dir: Path, data_dir: Path) -> bool:
    """财务目录是否已被 DATA_SUBDIRS 的某个子目录覆盖（避免重复/漏备）。"""
    return any(_is_within(finance_dir, data_dir / sub) for sub in DATA_SUBDIRS)


def _iter_data_files(data_dir: Path, finance_dir: Path | None = None):
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
    # FINANCE_DATA_DIR 未被 DATA_SUBDIRS 覆盖时（在 data 内但叫 custom-fin，或在 data 外），
    # 统一以 finance/ 前缀收进备份；恢复时按 FINANCE_DATA_DIR 分流。
    if (finance_dir is not None and finance_dir.is_dir()
            and finance_dir.resolve() != data_dir.resolve()
            and not _finance_covered(finance_dir, data_dir)):
        for path in sorted(finance_dir.rglob("*")):
            if path.is_file() and path.suffix.lower() not in SKIP_SUFFIXES:
                yield path, f"finance/{path.relative_to(finance_dir).as_posix()}"


def backup_data_zip(db_backup: Path, data_dir: Path | None = None,
                    backup_dir: Path | None = None, stamp: str | None = None,
                    finance_dir: Path | None = None,
                    local_files: Iterable[Path] = (),
                    project_dir: Path = PROJECT_DIR,
                    stats: dict | None = None) -> tuple[Path, list[str]]:
    """把数据库快照 + 数据文件 (+ 本机敏感配置) 打成 ZIP，返回（zip 路径, 跳过警告）。

    单个文件被占用时跳过并记录，不因为一个附件让整次备份失败。
    `local_files` 默认为空：只有 CLI 主流程会按白名单传入真实本机配置，
    纯函数测试不会意外读取开发者的 .env / 脱敏钥匙。
    `stats["included_local"]` 返回**实际成功写入**的本机配置数（CLI 文案以此为准）；
    只有该数 > 0 才写 ZIP 注释与 `.SENSITIVE.txt`。
    """
    data_dir = Path(data_dir) if data_dir is not None else DATA_DIR
    backup_dir = Path(backup_dir) if backup_dir is not None else BACKUP_DIR
    if finance_dir is None and data_dir == DATA_DIR:
        finance_dir = get_finance_data_dir()
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = stamp or datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = backup_dir / f"workbench_data_{stamp}.zip"
    warnings: list[str] = []
    included_local = 0
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(db_backup, "workbench.db")
        for path, arcname in _iter_data_files(data_dir, finance_dir):
            try:
                zf.write(path, arcname)
            except OSError as exc:
                warnings.append(f"{arcname} 跳过（{exc}）")
        for path in local_files:
            path = Path(path)
            if not path.is_file():
                warnings.append(f"{path.name} 跳过（文件不存在）")
                continue
            try:
                arcname = path.resolve().relative_to(Path(project_dir).resolve()).as_posix()
            except ValueError:
                warnings.append(f"{path.name} 跳过（不在项目目录内）")
                continue
            try:
                zf.write(path, arcname)
                included_local += 1
            except OSError as exc:
                warnings.append(f"{arcname} 跳过（{exc}）")
        # M3：只有真的收进本机配置时才标敏感，避免空配置也宣称含密钥
        if included_local:
            zf.comment = SENSITIVE_COMMENT.encode("utf-8")
    sidecar = dest.with_suffix(".SENSITIVE.txt")
    if included_local:
        sidecar.write_text(SENSITIVE_SIDECAR_TEXT, encoding="utf-8")
    else:
        # 重跑同一 stamp 且不再含本机配置时，不能留下旧的敏感副文件
        try:
            sidecar.unlink()
        except FileNotFoundError:
            pass
    if stats is not None:
        stats["included_local"] = included_local
    return dest, warnings


def prune(backup_dir: Path | None = None, keep: int = KEEP) -> list[str]:
    """按前缀各保留最近 keep 份，返回被清理的文件名（连同敏感标记副文件）。"""
    backup_dir = Path(backup_dir) if backup_dir is not None else BACKUP_DIR
    removed: list[str] = []
    for pattern in ("workbench_*.db", "workbench_data_*.zip"):
        files = sorted(backup_dir.glob(pattern))
        for old in files[:-keep]:
            old.unlink()
            removed.append(old.name)
            sidecar = old.with_suffix(".SENSITIVE.txt")
            if sidecar.exists():
                sidecar.unlink()
    return removed


def main() -> int:
    db_path, data_dir, finance_dir = resolve_paths()
    if not db_path.exists():
        print(f"未找到数据库文件：{db_path}，请先启动一次服务让它生成数据。")
        return 1

    db_backup = backup_database(db_path)
    print(f"数据库备份成功：{db_backup}（{db_backup.stat().st_size // 1024} KB）")

    local_files = local_config_files()
    stats: dict = {}
    zip_path, warnings = backup_data_zip(
        db_backup, data_dir, finance_dir=finance_dir, local_files=local_files, stats=stats)
    size_mb = zip_path.stat().st_size / 1024 / 1024
    print(f"数据整包备份成功：{zip_path}（{size_mb:.2f} MB，含数据库与文件资产）")
    included_local = int(stats.get("included_local", 0))
    if included_local:
        print(f"  已包含 {included_local} 份本机敏感配置（标记为 SENSITIVE，禁止分发）")
    for w in warnings:
        print(f"  警告：{w}")

    for name in prune():
        print(f"已清理旧备份：{name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
