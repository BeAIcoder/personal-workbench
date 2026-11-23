# -*- coding: utf-8 -*-
"""目录浏览接口（/api/fs/*）：盘符/根列表、浏览、白名单边界与 403。"""
import re
from pathlib import Path

import pytest

from app import config as app_config


@pytest.fixture(autouse=True)
def _browse_root(monkeypatch, tmp_path):
    """默认只允许测试临时根，避免依赖运行机器的用户主目录。"""
    monkeypatch.setattr(app_config.settings, "fs_browse_roots", str(tmp_path))
    return tmp_path


def test_drives_lists_allowed_roots(client, _browse_root):
    r = client.get("/api/fs/drives")
    assert r.status_code == 200
    drives = r.json()["drives"]
    assert [Path(item).resolve() for item in drives] == [_browse_root.resolve()]


def test_browse_lists_dirs_only(client, tmp_path):
    (tmp_path / "子目录A").mkdir()
    (tmp_path / "子目录B").mkdir()
    (tmp_path / "文件.txt").write_text("x", encoding="utf-8")
    (tmp_path / "$hidden").mkdir()
    r = client.get("/api/fs/browse", params={"path": str(tmp_path)})
    assert r.status_code == 200
    d = r.json()
    names = [e["name"] for e in d["entries"]]
    assert "子目录A" in names and "子目录B" in names
    assert "文件.txt" not in names and "$hidden" not in names
    assert all(e["is_dir"] for e in d["entries"])
    assert d["parent"] == ""  # 根白名单本身是浏览上界


def test_browse_root_lists_allowed_roots(client, _browse_root):
    r = client.get("/api/fs/browse")
    assert r.status_code == 200
    d = r.json()
    assert d["path"] == "" and d["parent"] == ""
    assert [Path(e["path"]).resolve() for e in d["entries"]] == [_browse_root.resolve()]


def test_browse_rejects_relative_and_missing(client, tmp_path):
    assert client.get("/api/fs/browse", params={"path": "rel/path"}).status_code == 400
    assert client.get("/api/fs/browse", params={"path": str(tmp_path / "不存在")}).status_code == 400


def test_browse_file_inside_root_reports_not_a_directory(client, tmp_path):
    """M10：白名单内的普通文件不能被误报为「目录不存在」。"""
    target = tmp_path / "普通文件.txt"
    target.write_text("x", encoding="utf-8")
    r = client.get("/api/fs/browse", params={"path": str(target)})
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert detail.startswith("不是目录"), detail
    assert "目录不存在" not in detail


def test_browse_outside_roots_is_403(client, tmp_path):
    outside = tmp_path.parent / f"wb_outside_{tmp_path.name}"
    outside.mkdir()

    # 绝对路径越界、以及用 .. 解析后越界，都必须是 403（不能回显存在性）
    for path in (str(outside), str(tmp_path / ".."), str(outside / "不存在")):
        r = client.get("/api/fs/browse", params={"path": path})
        assert r.status_code == 403, (path, r.status_code, r.text)
        assert "FS_BROWSE_ROOTS" in r.json()["detail"]


def test_browse_allows_multiple_configured_roots(client, monkeypatch, tmp_path):
    first = tmp_path / "root-a"
    second = tmp_path / "root-b"
    first.mkdir()
    second.mkdir()
    monkeypatch.setattr(
        app_config.settings, "fs_browse_roots", f"{first}, {second}"
    )
    for root in (first, second):
        r = client.get("/api/fs/browse", params={"path": str(root)})
        assert r.status_code == 200, r.text
    # 两个根之间的公共父目录不在白名单内
    assert client.get("/api/fs/browse", params={"path": str(tmp_path)}).status_code == 403


def test_parent_stops_at_allowed_root(client, tmp_path):
    child = tmp_path / "child"
    child.mkdir()
    r = client.get("/api/fs/browse", params={"path": str(child)})
    assert r.status_code == 200
    assert Path(r.json()["parent"]).resolve() == tmp_path.resolve()


def test_default_roots_are_home_and_data_dir(monkeypatch, tmp_path):
    from app.config import DATA_DIR, get_fs_browse_roots

    monkeypatch.setattr(app_config.settings, "fs_browse_roots", "")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    roots = get_fs_browse_roots()
    assert tmp_path.resolve() in roots
    assert DATA_DIR.resolve() in roots


def test_default_roots_include_finance_and_configured_pos_dirs(monkeypatch, tmp_path):
    """集成 M1：外部财务目录与已配置 POS 源/输出目录必须默认可浏览（且去重）。"""
    from app.config import get_fs_browse_roots

    finance = tmp_path / "外部财务数据"
    pos_source = tmp_path / "POS源目录"
    pos_output = tmp_path / "POS输出目录"
    for directory in (finance, pos_source, pos_output):
        directory.mkdir()

    monkeypatch.setattr(app_config.settings, "fs_browse_roots", "")
    monkeypatch.setattr(app_config.settings, "finance_data_dir", str(finance))
    monkeypatch.setattr(app_config.settings, "finance_pos_statement_dir", str(pos_source))
    monkeypatch.setattr(app_config.settings, "finance_pos_output_dir", str(pos_output))

    roots = get_fs_browse_roots()
    for directory in (finance, pos_source, pos_output):
        assert directory.resolve() in roots, f"默认根缺少 {directory}"
    assert len(roots) == len(set(roots)), "默认根必须去重"

    # 同一目录同时作为财务数据目录与 POS 源/输出时只保留一份
    monkeypatch.setattr(app_config.settings, "finance_pos_statement_dir", str(finance))
    monkeypatch.setattr(app_config.settings, "finance_pos_output_dir", str(finance))
    deduped = get_fs_browse_roots()
    assert deduped.count(finance.resolve()) == 1


def test_configured_roots_support_semicolon_and_relative(monkeypatch, tmp_path):
    from app.config import BASE_DIR, get_fs_browse_roots

    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.setattr(
        app_config.settings, "fs_browse_roots",
        f"{tmp_path};{other};relative-root",
    )
    roots = get_fs_browse_roots()
    assert tmp_path.resolve() in roots
    assert other.resolve() in roots
    assert (BASE_DIR / "relative-root").resolve() in roots


def test_settings_default_and_example_use_neutral_placeholder(monkeypatch):
    """配置默认必须为空；.env.example 的示例只能用中性占位，不得写具体盘符路径。"""
    from app.config import BASE_DIR, Settings

    monkeypatch.delenv("FS_BROWSE_ROOTS", raising=False)
    assert Settings(_env_file=None).fs_browse_roots == ""

    lines = (BASE_DIR / ".env.example").read_text(encoding="utf-8").splitlines()
    related = [line for line in lines if "FS_BROWSE_ROOTS" in line]
    assert "# FS_BROWSE_ROOTS=" in related, "必须保留留空默认项，默认逻辑不变"
    examples = [line for line in related if "示例" in line]
    assert examples, "必须提供配置示例"
    for line in examples:
        assert not re.search(r"[A-Za-z]:[\\/]", line), f"示例不得含具体盘符路径：{line}"
        assert "<" in line and ">" in line, f"示例必须使用中性占位：{line}"
