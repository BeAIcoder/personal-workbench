# -*- coding: utf-8 -*-
"""restore_backup.py 原子恢复与防恶意备份包回归（2026-10-31 第 3 批）。

覆盖：
- 原子性：坏库在校验阶段失败时，目标目录保持原状
- zip-slip、Windows 设备名、条目数/解压总量上限
- 正常恢复后目标内容完整、force 留整目录快照

全部使用 tmp_path 合成数据。
"""
import importlib.util
import sqlite3
import zipfile
from io import BytesIO
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def _load_script(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _make_zip(tmp_path: Path, entries: dict[str, bytes]) -> Path:
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    path = tmp_path / "backup.zip"
    path.write_bytes(buf.getvalue())
    return path


def _good_db_bytes(tmp_path: Path) -> bytes:
    db = tmp_path / "mk.db"
    if db.exists():
        db.unlink()
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE t (x INTEGER)")
    con.execute("INSERT INTO t VALUES (1)")
    con.commit()
    con.close()
    return db.read_bytes()


def test_corrupt_db_rejected_and_target_untouched(tmp_path):
    """坏库在校验阶段拒绝：目标目录保持原状，不留半恢复状态。"""
    mod = _load_script("restore_backup")
    target = tmp_path / "data"
    target.mkdir()
    (target / "marker.txt").write_text("原数据", encoding="utf-8")
    broken = tmp_path / "broken.db"
    broken.write_bytes(b"not a sqlite database at all" * 4)

    zpath = _make_zip(tmp_path, {
        "workbench.db": broken.read_bytes(),
        "uploads/a.txt": b"x",
    })
    with pytest.raises(ValueError) as exc:
        mod.restore(zpath, target, force=True)
    assert "workbench.db" in str(exc.value)  # 坏库在校验阶段被拒（无法读取/完整性失败）
    assert (target / "marker.txt").read_text(encoding="utf-8") == "原数据"
    assert not list(target.parent.glob("data.before_restore_*"))


def test_device_name_and_slip_rejected(tmp_path):
    mod = _load_script("restore_backup")
    zpath = _make_zip(tmp_path, {"CON": b"x", "workbench.db": _good_db_bytes(tmp_path)})
    with pytest.raises(ValueError):
        mod.restore(zpath, tmp_path / "t1")

    zpath = _make_zip(tmp_path, {"../evil.txt": b"x"})
    with pytest.raises(ValueError):
        mod.restore(zpath, tmp_path / "t2")


def test_entry_and_total_limits_rejected(tmp_path, monkeypatch):
    mod = _load_script("restore_backup")
    monkeypatch.setattr(mod, "MAX_ENTRIES", 2)
    zpath = _make_zip(tmp_path, {
        "workbench.db": _good_db_bytes(tmp_path),
        "a.txt": b"1", "b.txt": b"2",
    })
    with pytest.raises(ValueError):
        mod.restore(zpath, tmp_path / "t3")

    monkeypatch.setattr(mod, "MAX_TOTAL_UNCOMPRESSED", 1)
    zpath = _make_zip(tmp_path, {
        "workbench.db": _good_db_bytes(tmp_path),
        "big.bin": b"0" * 16,
    })
    with pytest.raises(ValueError):
        mod.restore(zpath, tmp_path / "t4")


def test_successful_restore_is_complete(tmp_path):
    mod = _load_script("restore_backup")
    zpath = _make_zip(tmp_path, {
        "workbench.db": _good_db_bytes(tmp_path),
        "uploads/a.txt": "附件".encode("utf-8"),
        "skills/s/SKILL.md": b"demo",
    })
    target = tmp_path / "data_ok"
    names = mod.restore(zpath, target)
    assert set(names) == {"workbench.db", "uploads/a.txt", "skills/s/SKILL.md"}
    con = sqlite3.connect(target / "workbench.db")
    assert con.execute("SELECT x FROM t").fetchone()[0] == 1
    con.close()
    assert (target / "uploads/a.txt").read_text(encoding="utf-8") == "附件"


def test_force_restore_keeps_full_snapshot(tmp_path):
    mod = _load_script("restore_backup")
    target = tmp_path / "data2"
    target.mkdir()
    (target / "old.txt").write_text("旧", encoding="utf-8")
    con = sqlite3.connect(target / "workbench.db")
    con.execute("CREATE TABLE t (x INTEGER)")
    con.execute("INSERT INTO t VALUES (7)")
    con.commit()
    con.close()

    zpath = _make_zip(tmp_path, {"workbench.db": _good_db_bytes(tmp_path)})
    mod.restore(zpath, target, force=True)
    snapshots = sorted(target.parent.glob("data2.before_restore_*"))
    assert snapshots
    prev = sqlite3.connect(snapshots[-1] / "workbench.db")
    assert prev.execute("SELECT x FROM t").fetchone()[0] == 7
    prev.close()
    assert (snapshots[-1] / "old.txt").read_text(encoding="utf-8") == "旧"
