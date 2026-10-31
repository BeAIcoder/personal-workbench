# -*- coding: utf-8 -*-
"""分发包扫描/剔除回归（BUGfix TL-01/TL-02）。

用合成 staging 目录验证：
- dev/、backend/tests/、frontend/tests/ 与根级内部 md 被剔除；
- GBK 编码的 bat 也参与扫描（不再被当二进制跳过）；
- 命中真实名称词表时硬失败。
不运行完整打包（预检要求 git 干净），只测纯函数。
"""
import importlib.util
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
DEV = BACKEND.parent / "dev"


def _load_package_release():
    if str(DEV) not in sys.path:
        sys.path.insert(0, str(DEV))
    spec = importlib.util.spec_from_file_location("release_scan_mod", DEV / "package_release.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def release(tmp_path, monkeypatch):
    mod = _load_package_release()
    # 用合成词表与空映射表，测试不依赖本机真实名称
    monkeypatch.setattr(mod, "load_pii_terms", lambda: ["合成客户名称"])
    mapping = tmp_path / "map.json"
    mapping.write_text('{"mappings": []}', encoding="utf-8")
    monkeypatch.setattr(mod, "SANITIZE_MAP", mapping)
    # 结构红线针对完整分发包（要求 LICENSE/dist 等），本测试只验证剔除与文本扫描
    monkeypatch.setattr(mod, "structural_check", lambda staging: [])
    staging = tmp_path / "staging"
    staging.mkdir()
    return mod, staging


def test_prune_removes_internal_dirs_and_root_md(release):
    mod, staging = release
    (staging / "README.md").write_text("# readme", encoding="utf-8")
    (staging / "AGENTS.md").write_text("# internal", encoding="utf-8")
    for rel in ("dev", "backend/tests", "frontend/tests", "20261012New"):
        (staging / rel).mkdir(parents=True, exist_ok=True)
        (staging / rel / "x.py").write_text("x", encoding="utf-8")
    (staging / "backend" / "scripts").mkdir(parents=True, exist_ok=True)
    (staging / "backend" / "scripts" / "backup_db.py").write_text("x", encoding="utf-8")

    mod.prune_internal(staging)

    assert (staging / "README.md").is_file()
    assert not (staging / "AGENTS.md").exists()
    for rel in ("dev", "backend/tests", "frontend/tests", "20261012New"):
        assert not (staging / rel).exists(), rel
    # 运维脚本仍保留
    assert (staging / "backend" / "scripts" / "backup_db.py").is_file()
    assert (staging / "backend" / "data").is_dir()


def test_scan_catches_pii_in_gbk_bat(release):
    mod, staging = release
    (staging / "README.md").write_text("# readme", encoding="utf-8")
    # GBK 编码的 bat：旧扫描器严格 UTF-8 解码失败会直接跳过
    (staging / "启动.bat").write_bytes("echo 合成客户名称\r\n".encode("gbk"))
    with pytest.raises(mod.PackageError) as exc:
        mod.scan_secrets(staging)
    assert "真实客户/员工名称" in str(exc.value)


def test_scan_passes_clean_tree(release):
    mod, staging = release
    (staging / "README.md").write_text("# readme", encoding="utf-8")
    (staging / "app.py").write_text("print('ok')", encoding="utf-8")
    # 成功时只记日志、不抛异常（返回 None）
    assert mod.scan_secrets(staging) is None
