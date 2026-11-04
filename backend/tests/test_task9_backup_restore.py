# -*- coding: utf-8 -*-
"""Task 9：备份遵循配置、WAL 热备份、敏感本地配置、恢复回滚与限额。

全部使用 tmp_path 合成数据与合成秘密值；绝不读取真实 .env/脱敏钥匙/银行映射。
"""
import importlib.util
import os
import shutil
import sqlite3
import subprocess
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
REPO = SCRIPTS.parent.parent


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"task9_{name}", SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sqlite_db(path: Path, value: int = 42, wal: bool = False) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    if wal:
        con.execute("PRAGMA journal_mode=WAL")
    con.execute("CREATE TABLE IF NOT EXISTS t (x INTEGER)")
    con.execute("INSERT INTO t VALUES (?)", (value,))
    con.commit()
    return con


def _db_bytes(path: Path) -> bytes:
    con = _sqlite_db(path, 1)
    con.close()
    return path.read_bytes()


def test_resolve_db_path_follows_settings():
    mod = _load("backup_db")
    url = "sqlite:///D:/tmp/elsewhere/my.db"
    assert mod.resolve_db_path(url) == Path("D:/tmp/elsewhere/my.db")
    assert mod.resolve_db_path("sqlite+pysqlite:///D:/tmp/x.db") == Path("D:/tmp/x.db")
    with pytest.raises(ValueError):
        mod.resolve_db_path("postgresql://localhost/db")
    with pytest.raises(ValueError):
        mod.resolve_db_path("sqlite:///:memory:")


def test_resolve_paths_uses_settings_database_and_finance_dir(tmp_path, monkeypatch):
    mod = _load("backup_db")
    db = tmp_path / "custom" / "workbench.db"
    data = tmp_path / "data-root"
    finance = tmp_path / "finance-outside"
    monkeypatch.setattr(mod, "settings", SimpleNamespace(database_url=f"sqlite:///{db.as_posix()}"))
    monkeypatch.setattr(mod, "DATA_DIR", data)
    monkeypatch.setattr(mod, "get_finance_data_dir", lambda: finance)
    assert mod.resolve_paths() == (db, data, finance)


def test_wal_hot_backup_contains_committed_rows(tmp_path):
    """WAL 中已提交但未 checkpoint 的数据也必须进备份。"""
    mod = _load("backup_db")
    db = tmp_path / "live" / "workbench.db"
    con = _sqlite_db(db, 7, wal=True)
    try:
        assert Path(str(db) + "-wal").exists(), "测试前置：WAL 文件应存在"
        backup = mod.backup_database(db, tmp_path / "backups", "20261104_120000")
    finally:
        con.close()
    got = sqlite3.connect(backup)
    try:
        assert got.execute("SELECT x FROM t").fetchone()[0] == 7
    finally:
        got.close()


def test_backup_includes_external_finance_dir_and_local_configs(tmp_path):
    mod = _load("backup_db")
    data = tmp_path / "data"
    (data / "uploads").mkdir(parents=True)
    (data / "uploads" / "a.txt").write_text("附件", encoding="utf-8")
    finance = tmp_path / "finance-external"
    (finance / "pos").mkdir(parents=True)
    (finance / "pos" / "a.xlsx").write_bytes(b"PK")

    db = data / "workbench.db"
    con = _sqlite_db(db, 11)
    db_backup = mod.backup_database(db, tmp_path / "backups", "20261104_120000")

    # 合成秘密：仅存在于 tmp_path，测试结束即丢弃
    project = tmp_path / "project"
    local_files = [
        project / "backend" / ".env",
        project / "backend" / "app" / "modules" / "finance_bankflow" / "config.local.yaml",
        project / "dev" / "sanitize-map.local.json",
    ]
    for path in local_files:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("SYNTHETIC_SECRET=not-real\n", encoding="utf-8")

    zip_path, warnings = mod.backup_data_zip(
        db_backup, data, tmp_path / "backups", "20261104_120000",
        finance_dir=finance, local_files=local_files, project_dir=project,
    )
    assert warnings == []
    with zipfile.ZipFile(zip_path) as zf:
        names = set(zf.namelist())
        comment = zf.comment.decode("utf-8", "replace")
    assert "uploads/a.txt" in names
    assert "finance/pos/a.xlsx" in names
    assert "backend/.env" in names
    assert "backend/app/modules/finance_bankflow/config.local.yaml" in names
    assert "dev/sanitize-map.local.json" in names
    # 备份自身标敏感（人类可见的副标记 + ZIP 注释）
    assert "SENSITIVE" in comment
    sidecar = zip_path.with_suffix(".SENSITIVE.txt")
    assert sidecar.is_file() and "敏感" in sidecar.read_text(encoding="utf-8")


def test_backup_does_not_sweep_local_configs_by_default(tmp_path):
    mod = _load("backup_db")
    data = tmp_path / "data"
    db = data / "workbench.db"
    con = _sqlite_db(db, 1)
    db_backup = mod.backup_database(db, tmp_path / "backups", "20261104_120000")
    zip_path, _ = mod.backup_data_zip(db_backup, data, tmp_path / "backups", "20261104_120001")
    with zipfile.ZipFile(zip_path) as zf:
        names = set(zf.namelist())
        comment = zf.comment
    assert "backend/.env" not in names
    assert not any(name.endswith("sanitize-map.local.json") for name in names)
    # M3：没有本机配置时不得宣称含密钥
    assert comment == b""
    assert not zip_path.with_suffix(".SENSITIVE.txt").exists()


def test_backup_warns_when_local_config_missing(tmp_path):
    """M5：显式传入但不存在的本机配置要告警，不能静默丢弃。"""
    mod = _load("backup_db")
    data = tmp_path / "data"
    db = data / "workbench.db"
    _sqlite_db(db, 1).close()
    db_backup = mod.backup_database(db, tmp_path / "backups", "20261104_120000")
    _, warnings = mod.backup_data_zip(
        db_backup, data, tmp_path / "backups", "20261104_120001",
        local_files=[tmp_path / "missing.env"],
    )
    assert any("文件不存在" in item for item in warnings), warnings


def test_restore_routes_local_configs_to_project_root(tmp_path):
    mod = _load("restore_backup")
    project = tmp_path / "project"
    zip_path = tmp_path / "backup.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("workbench.db", _db_bytes(tmp_path / "mk.db"))
        zf.writestr("uploads/a.txt", b"x")
        zf.writestr("backend/.env", b"SYNTHETIC_SECRET=not-real\n")
    target = project / "backend" / "data"
    mod.restore(zip_path, target, project_root=project)
    assert (project / "backend" / ".env").read_text(encoding="utf-8") == "SYNTHETIC_SECRET=not-real\n"
    assert not (target / "backend" / ".env").exists()
    assert (target / "uploads" / "a.txt").is_file()


def test_restore_limits_actual_extracted_bytes(tmp_path, monkeypatch):
    """中央目录谎报 0 字节也不能绕过总额：按实际写入字节累计。"""
    mod = _load("restore_backup")
    db_bytes = _db_bytes(tmp_path / "mk.db")
    zip_path = tmp_path / "bomb.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("workbench.db", db_bytes)
        zf.writestr("big.bin", b"0" * 4096)

    real_infolist = zipfile.ZipFile.infolist

    def lying_infolist(self):
        import copy

        infos = []
        for info in real_infolist(self):
            # 必须复制：infolist() 返回内部 ZipInfo 对象，直接改会同时改变解压读取的长度
            clone = copy.copy(info)
            if clone.filename == "big.bin":
                clone.file_size = 0
            infos.append(clone)
        return infos

    monkeypatch.setattr(zipfile.ZipFile, "infolist", lying_infolist)
    monkeypatch.setattr(mod, "MAX_TOTAL_UNCOMPRESSED", len(db_bytes) + 1024)
    target = tmp_path / "target"
    target.mkdir()
    (target / "marker.txt").write_text("原数据", encoding="utf-8")
    with pytest.raises(ValueError) as exc:
        mod.restore(zip_path, target, force=True)
    assert "解压总量" in str(exc.value)
    assert (target / "marker.txt").read_text(encoding="utf-8") == "原数据"


def test_second_rename_failure_restores_old_target(tmp_path, monkeypatch):
    mod = _load("restore_backup")
    target = tmp_path / "data"
    con = _sqlite_db(target / "workbench.db", 7)
    con.close()
    (target / "old.txt").write_text("旧", encoding="utf-8")

    zip_path = tmp_path / "backup.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("workbench.db", _db_bytes(tmp_path / "mk.db"))

    real_rename = Path.rename
    calls = {"n": 0}

    def flaky(self, destination):
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("模拟换入失败")
        return real_rename(self, destination)

    monkeypatch.setattr(Path, "rename", flaky)
    with pytest.raises(OSError):
        mod.restore(zip_path, target, force=True)
    monkeypatch.setattr(Path, "rename", real_rename)

    assert (target / "old.txt").read_text(encoding="utf-8") == "旧"
    got = sqlite3.connect(target / "workbench.db")
    try:
        assert got.execute("SELECT x FROM t").fetchone()[0] == 7
    finally:
        got.close()
    assert not list(target.parent.glob("data.before_restore_*")), "回滚后不应留下悬空快照"


def test_restore_snapshots_are_bounded(tmp_path, monkeypatch):
    mod = _load("restore_backup")
    monkeypatch.setattr(mod, "MAX_SNAPSHOTS", 2)
    for index in range(4):
        old = tmp_path / f"data.before_restore_2026010{index}_000000"
        (old / "uploads").mkdir(parents=True)
        (old / "uploads" / "x.txt").write_text(str(index), encoding="utf-8")

    target = tmp_path / "data"
    con = _sqlite_db(target / "workbench.db", 5)
    con.close()
    zip_path = tmp_path / "backup.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("workbench.db", _db_bytes(tmp_path / "mk.db"))
    mod.restore(zip_path, target, force=True)

    snaps = sorted(target.parent.glob("data.before_restore_*"))
    assert len(snaps) <= 2, f"快照未保留有界: {snaps}"
    assert any((snap / "workbench.db").exists() for snap in snaps)


def test_backup_and_restore_local_config_lists_do_not_drift():
    backup = _load("backup_db")
    restore = _load("restore_backup")
    assert tuple(backup.LOCAL_CONFIG_FILES) == tuple(restore.LOCAL_CONFIG_ARCHIVES)


def test_backup_includes_finance_dir_inside_data_dir_with_other_name(tmp_path):
    """I1：FINANCE_DATA_DIR 在 DATA_DIR 内但不是 data/finance 时也要收进 finance/。"""
    mod = _load("backup_db")
    data = tmp_path / "data"
    finance = data / "custom-fin"
    (finance / "pos").mkdir(parents=True)
    (finance / "pos" / "a.xlsx").write_bytes(b"PK")
    (data / "uploads").mkdir(parents=True)
    (data / "uploads" / "a.txt").write_text("附件", encoding="utf-8")
    db = data / "workbench.db"
    _sqlite_db(db, 3).close()
    db_backup = mod.backup_database(db, tmp_path / "backups", "20261104_130000")
    zip_path, warnings = mod.backup_data_zip(
        db_backup, data, tmp_path / "backups", "20261104_130001", finance_dir=finance)
    assert warnings == []
    with zipfile.ZipFile(zip_path) as zf:
        names = set(zf.namelist())
    assert "finance/pos/a.xlsx" in names
    assert "uploads/a.txt" in names


def test_restore_rejects_zero_byte_database(tmp_path):
    """I2：0 字节库的 PRAGMA integrity_check 可能返回 ok，必须额外判空与业务表。"""
    mod = _load("restore_backup")
    zip_path = tmp_path / "empty-db.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("workbench.db", b"")
        zf.writestr("uploads/a.txt", b"x")
    target = tmp_path / "target"
    target.mkdir()
    (target / "marker.txt").write_text("原数据", encoding="utf-8")
    with pytest.raises(ValueError) as exc:
        mod.restore(zip_path, target, force=True)
    assert "workbench.db" in str(exc.value) and ("为空" in str(exc.value) or "业务表" in str(exc.value))
    assert (target / "marker.txt").read_text(encoding="utf-8") == "原数据"


def test_restore_rejects_windows_ads_member(tmp_path):
    """M5：Windows 备用数据流（file:ads）必须拒绝，不能写到 NTFS ADS。"""
    mod = _load("restore_backup")
    zip_path = tmp_path / "ads.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("workbench.db", _db_bytes(tmp_path / "mk.db"))
        zf.writestr("a.txt:secret", b"x")
    with pytest.raises(ValueError) as exc:
        mod.restore(zip_path, tmp_path / "target")
    assert "非法路径" in str(exc.value)


def test_snapshot_prefix_follows_target_name(tmp_path):
    """M4：--target custom-data 的快照应为 custom-data.before_restore_*。"""
    mod = _load("restore_backup")
    target = tmp_path / "custom-data"
    con = _sqlite_db(target / "workbench.db", 9)
    con.close()
    zip_path = tmp_path / "backup.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("workbench.db", _db_bytes(tmp_path / "mk.db"))
    mod.restore(zip_path, target, force=True)
    snaps = sorted(target.parent.glob("custom-data.before_restore_*"))
    assert snaps, "快照前缀应跟随 target 名"
    assert not list(target.parent.glob("data.before_restore_*"))


def test_restore_routes_external_finance_dir(tmp_path):
    """I4：外部 FINANCE_DATA_DIR 分流到该目录，并保留旧目录快照。"""
    mod = _load("restore_backup")
    target = tmp_path / "data"
    external = tmp_path / "finance-external"
    (external / "old").mkdir(parents=True)
    (external / "old" / "keep.txt").write_text("旧财务", encoding="utf-8")
    zip_path = tmp_path / "backup.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("workbench.db", _db_bytes(tmp_path / "mk.db"))
        zf.writestr("finance/pos/a.xlsx", b"PK")
    mod.restore(zip_path, target, force=True, finance_target=external)
    assert (external / "pos" / "a.xlsx").is_file()
    assert not (target / "finance").exists()
    snaps = sorted(tmp_path.glob("finance-external.before_restore_*"))
    assert snaps, "外部财务目录替换前应留快照"
    assert (snaps[-1] / "old" / "keep.txt").read_text(encoding="utf-8") == "旧财务"


def test_local_configs_snapshot_before_overwrite(tmp_path):
    """I5：覆盖本机配置前先写 .before_restore_local_* 快照。"""
    mod = _load("restore_backup")
    project = tmp_path / "project"
    env_file = project / "backend" / ".env"
    env_file.parent.mkdir(parents=True)
    env_file.write_text("OLD_SECRET=1\n", encoding="utf-8")
    zip_path = tmp_path / "backup.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("workbench.db", _db_bytes(tmp_path / "mk.db"))
        zf.writestr("backend/.env", b"NEW_SECRET=2\n")
    mod.restore(zip_path, project / "backend" / "data", project_root=project)
    assert env_file.read_text(encoding="utf-8") == "NEW_SECRET=2\n"
    snapshot_root = project / "backups" / "restore-local"
    snaps = sorted(snapshot_root.glob("local_*"))
    assert snaps, "本机配置覆盖前应留快照"
    assert (snaps[-1] / "backend" / ".env").read_text(encoding="utf-8") == "OLD_SECRET=1\n"
    assert not list(project.glob(".before_restore_local_*")), "快照不得落在仓库可见根目录"


def test_local_config_failure_rolls_back_target_and_configs(tmp_path, monkeypatch):
    """I5：本机配置已写、目标换入失败时，配置与目标都必须回滚。"""
    mod = _load("restore_backup")
    project = tmp_path / "project"
    env_file = project / "backend" / ".env"
    env_file.parent.mkdir(parents=True)
    env_file.write_text("OLD_SECRET=1\n", encoding="utf-8")
    target = tmp_path / "data"
    con = _sqlite_db(target / "workbench.db", 7)
    con.close()
    (target / "old.txt").write_text("旧", encoding="utf-8")
    zip_path = tmp_path / "backup.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("workbench.db", _db_bytes(tmp_path / "mk.db"))
        zf.writestr("backend/.env", b"NEW_SECRET=2\n")

    real_rename = Path.rename
    calls = {"n": 0}

    def flaky(self, destination):
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("模拟换入失败")
        return real_rename(self, destination)

    monkeypatch.setattr(Path, "rename", flaky)
    with pytest.raises(OSError):
        mod.restore(zip_path, target, force=True, project_root=project)
    monkeypatch.setattr(Path, "rename", real_rename)

    assert env_file.read_text(encoding="utf-8") == "OLD_SECRET=1\n", "本机配置必须回滚"
    snapshot_root = project / "backups" / "restore-local"
    if snapshot_root.exists():
        assert not list(snapshot_root.glob("local_*")), "回滚后不应留下临时快照"
    assert (target / "old.txt").read_text(encoding="utf-8") == "旧"
    got = sqlite3.connect(target / "workbench.db")
    try:
        assert got.execute("SELECT x FROM t").fetchone()[0] == 7
    finally:
        got.close()


def test_restore_leaves_no_local_config_staging(tmp_path):
    """I5：换入完成后 target 下不得残留 .local-config。"""
    mod = _load("restore_backup")
    project = tmp_path / "project"
    project.mkdir()
    zip_path = tmp_path / "backup.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("workbench.db", _db_bytes(tmp_path / "mk.db"))
        zf.writestr("backend/.env", b"NEW=1\n")
    target = project / "data"
    mod.restore(zip_path, target, project_root=project)
    assert not (target / ".local-config").exists()

def test_restore_snapshots_are_gitignored(tmp_path):
    """C1：快照副本与根目录旧前缀都必须被 git 忽略。"""
    for rel in (
        ".before_restore_local_20260101_000000/backend/notes.txt",
        "data.before_restore_20260101_000000/notes.txt",
        "backups/restore-local/local_20260101_000000/backend/notes.txt",
        "backend/notes.restore-tmp",
        ".restore_staging_20260101_000000/notes.txt",
        ".restore_staging_20260101_000000/backend/.env",
        ".restore_staging_20260101_000000/dev/sanitize-map.local.json",
        ".restore_staging_20260101_000000/backend/app/modules/finance_bankflow/config.local.yaml",
    ):
        proc = subprocess.run(
            ["git", "check-ignore", "--no-index", "--quiet", rel],
            cwd=REPO, capture_output=True, text=True, encoding="utf-8",
        )
        assert proc.returncode == 0, f"未被 gitignore: {rel}\n{proc.stderr}"


def test_local_snapshots_live_under_backups_and_are_pruned(tmp_path):
    """C1：快照放 backups/restore-local；按 MAX_SNAPSHOTS 清理。"""
    mod = _load("restore_backup")
    project = tmp_path / "project"
    snapshot_root = project / "backups" / "restore-local"
    for index in range(4):
        old = snapshot_root / f"local_2026010{index}_000000"
        (old / "backend").mkdir(parents=True)
        (old / "backend" / ".env").write_text(f"OLD={index}\n", encoding="utf-8")
    env_file = project / "backend" / ".env"
    env_file.parent.mkdir(parents=True)
    env_file.write_text("OLD_SECRET=1\n", encoding="utf-8")
    zip_path = tmp_path / "backup.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("workbench.db", _db_bytes(tmp_path / "mk.db"))
        zf.writestr("backend/.env", b"NEW_SECRET=2\n")
    mod.restore(zip_path, project / "data", project_root=project)
    snaps = sorted(snapshot_root.glob("local_*"))
    assert len(snaps) <= mod.MAX_SNAPSHOTS, f"本机配置快照未清理: {snaps}"
    assert not (snapshot_root / "local_20260100_000000").exists()


def test_local_config_copy_failure_keeps_old_and_no_temp(tmp_path, monkeypatch):
    """Minor：copy2 中途失败不得残留半写配置或 .restore-tmp。"""
    mod = _load("restore_backup")
    project = tmp_path / "project"
    env_file = project / "backend" / ".env"
    env_file.parent.mkdir(parents=True)
    env_file.write_text("OLD_ENV=1\n", encoding="utf-8")
    sanitize = project / "dev" / "sanitize-map.local.json"
    sanitize.parent.mkdir(parents=True)
    sanitize.write_text('{"old": 1}\n', encoding="utf-8")
    zip_path = tmp_path / "backup.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("workbench.db", _db_bytes(tmp_path / "mk.db"))
        zf.writestr("backend/.env", b"NEW_ENV=2\n")
        zf.writestr("dev/sanitize-map.local.json", b'{"new": 2}\n')

    real_copy = mod.shutil.copy2
    calls = {"apply": 0}

    def flaky(src, dst, *args, **kwargs):
        if str(dst).endswith(".restore-tmp"):
            calls["apply"] += 1
            if calls["apply"] == 2:
                Path(dst).write_text("PARTIAL", encoding="utf-8")
                raise OSError("模拟 copy2 失败")
        return real_copy(src, dst, *args, **kwargs)

    monkeypatch.setattr(mod.shutil, "copy2", flaky)
    with pytest.raises(OSError):
        mod.restore(zip_path, project / "data", project_root=project)
    monkeypatch.setattr(mod.shutil, "copy2", real_copy)
    assert env_file.read_text(encoding="utf-8") == "OLD_ENV=1\n"
    assert sanitize.read_text(encoding="utf-8") == '{"old": 1}\n'
    assert not list(project.rglob("*.restore-tmp"))


def test_keyboard_interrupt_still_rolls_back(tmp_path, monkeypatch):
    """I-A：Ctrl+C（BaseException）也必须回滚，不能被 except Exception 漏掉。"""
    mod = _load("restore_backup")
    project = tmp_path / "project"
    env_file = project / "backend" / ".env"
    env_file.parent.mkdir(parents=True)
    env_file.write_text("OLD=1\n", encoding="utf-8")
    target = tmp_path / "data"
    con = _sqlite_db(target / "workbench.db", 7)
    con.close()
    (target / "old.txt").write_text("旧", encoding="utf-8")
    zip_path = tmp_path / "backup.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("workbench.db", _db_bytes(tmp_path / "mk.db"))
        zf.writestr("backend/.env", b"NEW=2\n")

    real_rename = Path.rename
    calls = {"n": 0}

    def flaky(self, destination):
        calls["n"] += 1
        if calls["n"] == 2:
            raise KeyboardInterrupt()
        return real_rename(self, destination)

    monkeypatch.setattr(Path, "rename", flaky)
    with pytest.raises(KeyboardInterrupt):
        mod.restore(zip_path, target, force=True, project_root=project)
    monkeypatch.setattr(Path, "rename", real_rename)
    assert env_file.read_text(encoding="utf-8") == "OLD=1\n"
    assert (target / "old.txt").read_text(encoding="utf-8") == "旧"
    assert not list((project / "backups" / "restore-local").glob("local_*"))


def test_rollback_failure_reports_half_state_and_snapshot(tmp_path, monkeypatch):
    """I-A：回滚阶段失败时保留原异常，并明确列出半状态与快照路径。"""
    mod = _load("restore_backup")
    target = tmp_path / "data"
    con = _sqlite_db(target / "workbench.db", 7)
    con.close()
    external = tmp_path / "finance-external"
    (external / "old").mkdir(parents=True)
    (external / "old" / "keep.txt").write_text("旧财务", encoding="utf-8")
    zip_path = tmp_path / "backup.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("workbench.db", _db_bytes(tmp_path / "mk.db"))
        zf.writestr("finance/pos/a.xlsx", b"PK")

    real_rename = Path.rename
    calls = {"n": 0}

    def flaky(self, destination):
        calls["n"] += 1
        if calls["n"] == 3:
            raise OSError("模拟目标换入失败")
        if calls["n"] == 5:
            raise OSError("模拟财务回滚失败")
        return real_rename(self, destination)

    monkeypatch.setattr(Path, "rename", flaky)
    with pytest.raises(RuntimeError) as exc:
        mod.restore(zip_path, target, force=True, finance_target=external)
    monkeypatch.setattr(Path, "rename", real_rename)
    message = str(exc.value)
    assert "回滚未完成" in message and "finance-external.before_restore" in message
    assert isinstance(exc.value.__cause__, OSError)
    got = sqlite3.connect(target / "workbench.db")
    try:
        assert got.execute("SELECT x FROM t").fetchone()[0] == 7
    finally:
        got.close()
    assert not (external / "pos").exists()
    assert list(tmp_path.glob("finance-external.before_restore_*")), "失败时必须保留可人工恢复的快照"


def test_default_and_custom_target_finance_dir_follow_target(tmp_path):
    """I-B A/B：未显式 finance-target 时，无论默认/自定义 target 都用 <target>/finance。"""
    mod = _load("restore_backup")
    for name in ("data", "custom-data"):
        target = tmp_path / name
        zip_path = tmp_path / f"{name}.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("workbench.db", _db_bytes(tmp_path / f"{name}-mk.db"))
            zf.writestr("finance/pos/a.xlsx", b"PK")
        mod.restore(zip_path, target)
        assert (target / "finance" / "pos" / "a.xlsx").is_file(), name


def test_finance_target_must_not_be_target_or_ancestor(tmp_path):
    """I-B D：finance_target 为 target 自身/祖先/内部自定义路径必须拒绝。"""
    mod = _load("restore_backup")
    target = tmp_path / "data"
    zip_path = tmp_path / "backup.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("workbench.db", _db_bytes(tmp_path / "mk.db"))
    for bad in (target, target.parent, target / "custom-fin"):
        with pytest.raises(ValueError) as exc:
            mod.restore(zip_path, target, finance_target=bad)
        assert "finance_target" in str(exc.value)


def test_restore_help_documents_finance_target():
    """I-B：帮助文案必须说明默认 <target>/finance 与显式分流。"""
    env = os.environ.copy()
    env["WORKBENCH_ENV_FILE"] = ""
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS / "restore_backup.py"), "--help"],
        cwd=REPO, env=env, capture_output=True, text=True, encoding="utf-8",
    )
    assert proc.returncode == 0, proc.stderr
    assert "--finance-target" in proc.stdout
    assert "<target>/finance" in proc.stdout


def test_internal_custom_finance_auto_splits_when_target_is_data_dir(tmp_path, monkeypatch):
    """Important：target==WORKBENCH_DATA_DIR 且 FINANCE_DATA_DIR 在 target 内自定义时自动分流且真实可见。"""
    mod = _load("restore_backup")
    target = tmp_path / "data"
    custom = target / "custom-fin"
    monkeypatch.setattr(mod, "DATA_DIR", target)
    monkeypatch.setattr(mod.settings, "finance_data_dir", str(custom))
    zip_path = tmp_path / "backup.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("workbench.db", _db_bytes(tmp_path / "mk.db"))
        zf.writestr("finance/pos/a.xlsx", b"PK")
    mod.restore(zip_path, target)
    assert (custom / "pos" / "a.xlsx").is_file(), "target 内自定义财务目录应真实恢复"
    assert not (target / "finance").exists()


def test_internal_custom_finance_fail_closed_for_custom_target(tmp_path, monkeypatch):
    """Important：自定义 --target 下配置内嵌自定义财务目录必须应用前拒绝，不谎报成功。"""
    mod = _load("restore_backup")
    target = tmp_path / "custom-target"
    monkeypatch.setattr(mod.settings, "finance_data_dir", str(target / "custom-fin"))
    zip_path = tmp_path / "backup.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("workbench.db", _db_bytes(tmp_path / "mk.db"))
        zf.writestr("finance/pos/a.xlsx", b"PK")
    with pytest.raises(ValueError) as exc:
        mod.restore(zip_path, target)
    message = str(exc.value)
    assert "FINANCE_DATA_DIR" in message and "外部 --finance-target" in message
    assert not target.exists(), "fail-closed 时不得创建/成功写入目标"


def test_cli_catches_runtime_error_without_traceback(tmp_path, monkeypatch, capsys):
    """Minor：CLI 捕获回滚 RuntimeError，中文输出且不裸栈。"""
    mod = _load("restore_backup")

    def boom(*args, **kwargs):
        raise RuntimeError("回滚未完成：半完成状态")

    monkeypatch.setattr(mod, "restore", boom)
    monkeypatch.setattr(sys, "argv", ["restore_backup.py", str(tmp_path / "x.zip")])
    rc = mod.main()
    out = capsys.readouterr()
    assert rc == 1
    assert "回滚未完成" in (out.out + out.err)
    assert "Traceback" not in (out.out + out.err)


def test_backup_reports_actual_included_local_count(tmp_path):
    """CLI 统计必须用实际写入数，而不是候选文件数。"""
    mod = _load("backup_db")
    data = tmp_path / "data"
    db = data / "workbench.db"
    _sqlite_db(db, 1).close()
    db_backup = mod.backup_database(db, tmp_path / "backups", "20261104_150000")
    project = tmp_path / "project"
    existing = project / "backend" / ".env"
    existing.parent.mkdir(parents=True)
    existing.write_text("SYNTHETIC=1\n", encoding="utf-8")
    stats: dict = {}
    mod.backup_data_zip(
        db_backup, data, tmp_path / "backups", "20261104_150001",
        local_files=[existing, tmp_path / "missing.env"],
        project_dir=project, stats=stats,
    )
    assert stats["included_local"] == 1


def test_backup_removes_stale_sensitive_sidecar(tmp_path):
    """Minor：同一 stamp 重跑且不再含本机配置时，旧 SENSITIVE 副文件必须清理。"""
    mod = _load("backup_db")
    data = tmp_path / "data"
    db = data / "workbench.db"
    _sqlite_db(db, 1).close()
    db_backup = mod.backup_database(db, tmp_path / "backups", "20261104_140000")
    local = tmp_path / "project" / "backend" / ".env"
    local.parent.mkdir(parents=True)
    local.write_text("SYNTHETIC=1\n", encoding="utf-8")
    first, _ = mod.backup_data_zip(
        db_backup, data, tmp_path / "backups", "20261104_140001",
        local_files=[local], project_dir=tmp_path / "project",
    )
    sidecar = first.with_suffix(".SENSITIVE.txt")
    assert sidecar.is_file()
    second, _ = mod.backup_data_zip(
        db_backup, data, tmp_path / "backups", "20261104_140001", local_files=())
    assert second == first
    assert not sidecar.exists(), "无本机配置时必须删除旧敏感副文件"
