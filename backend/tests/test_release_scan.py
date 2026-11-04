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


# ---------- Task 9：保留用户手册、docs/ 剔除、二进制白名单、真实结构契约 ----------

def _real_release(tmp_path, monkeypatch):
    """不替换 structural_check 的真实模块（合成词表/空映射，不读真实秘密）。"""
    mod = _load_package_release()
    monkeypatch.setattr(mod, "load_pii_terms", lambda: [])
    mapping = tmp_path / "map.json"
    mapping.write_text('{"mappings": []}', encoding="utf-8")
    monkeypatch.setattr(mod, "SANITIZE_MAP", mapping)
    return mod


def _complete_staging(mod, root: Path) -> None:
    for rel in mod.REQUIRED_FILES:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if rel == "backend/requirements.txt":
            path.write_text("fastapi\nuvicorn\nsqlalchemy\npydantic\n", encoding="utf-8")
        else:
            path.write_text("x", encoding="utf-8")
    (root / "backend" / "data").mkdir(parents=True, exist_ok=True)
    (root / "backend" / "data" / ".gitkeep").write_text("", encoding="utf-8")


def test_prune_keeps_data_transfer_guide_and_references(tmp_path, monkeypatch):
    mod = _real_release(tmp_path, monkeypatch)
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "使用说明.md").write_text(
        "十三、数据收传 → 见《数据收传使用说明.md》\n", encoding="utf-8")
    (staging / "数据收传使用说明.md").write_text("# 数据收传\n", encoding="utf-8")
    (staging / "AGENTS.md").write_text("# internal\n", encoding="utf-8")
    (staging / "docs" / "screenshots").mkdir(parents=True)
    (staging / "docs" / "screenshots" / "old.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    mod.prune_internal(staging)

    assert (staging / "数据收传使用说明.md").is_file(), "用户手册被误删"
    assert not (staging / "AGENTS.md").exists()
    assert not (staging / "docs").exists(), "docs/ 应整体剔除（旧截图是扫描盲区）"
    for kept in mod.KEEP_ROOT_MD:
        path = staging / kept
        if not path.is_file():
            continue
        for ref in __import__("re").findall(r"《([^《》]+?\.md)》", path.read_text(encoding="utf-8")):
            assert (staging / ref).is_file(), f"{kept} 引用的《{ref}》在包内不存在"


def test_real_structural_check_passes_complete_staging(tmp_path, monkeypatch):
    mod = _real_release(tmp_path, monkeypatch)
    staging = tmp_path / "staging"
    _complete_staging(mod, staging)
    assert mod.structural_check(staging) == []


def test_real_structural_check_rejects_unlisted_binary_and_local_config(tmp_path, monkeypatch):
    mod = _real_release(tmp_path, monkeypatch)
    staging = tmp_path / "staging"
    _complete_staging(mod, staging)
    (staging / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (staging / "backend" / "app" / "modules" / "finance_bankflow").mkdir(parents=True)
    (staging / "backend" / "app" / "modules" / "finance_bankflow" / "config.local.yaml").write_text(
        "accounts: {}\n", encoding="utf-8")
    (staging / "dev").mkdir(exist_ok=True)
    (staging / "dev" / "sanitize-map.local.json").write_text("{}", encoding="utf-8")

    problems = mod.structural_check(staging)
    joined = "\n".join(problems)
    assert "未登记二进制" in joined and "logo.png" in joined
    assert "config.local.yaml" in joined
    assert "sanitize-map.local.json" in joined


def test_real_structural_check_allows_registered_binaries(tmp_path, monkeypatch):
    mod = _real_release(tmp_path, monkeypatch)
    staging = tmp_path / "staging"
    _complete_staging(mod, staging)
    for rel in (
        "数据模板/数据仓-入仓模板.xlsx",
        "frontend/dist/assets/font.woff2",
        "backend/app/modules/finance_bankflow/template_skeleton.xlsx",
    ):
        path = staging / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"PK")
    assert mod.structural_check(staging) == []


def test_real_structural_check_rejects_missing_core_requirements(tmp_path, monkeypatch):
    mod = _real_release(tmp_path, monkeypatch)
    staging = tmp_path / "staging"
    _complete_staging(mod, staging)
    (staging / "backend" / "requirements.txt").write_text("fastapi\n", encoding="utf-8")
    problems = mod.structural_check(staging)
    assert any("requirements.txt 缺少核心依赖：uvicorn" in item for item in problems), problems


def test_scan_catches_two_char_mapping_value(release):
    """映射表 2 字真实值不能被 len>=3 阈值漏掉（与 pii_terms 的 >=2 同口径）。"""
    mod, staging = release
    mod.SANITIZE_MAP.write_text('{"mappings": [{"real": "张三"}]}', encoding="utf-8")
    (staging / "note.txt").write_text("负责人张三", encoding="utf-8")
    with pytest.raises(mod.PackageError) as exc:
        mod.scan_secrets(staging)
    assert "映射表真实值" in str(exc.value)


REPO = BACKEND.parent


def _doc_reference_problems(mod, staging: Path) -> list[str]:
    """I6：保留文档不得引用被 prune 掉的图片/文档（真实仓库里存在但包里不存在即悬空）。"""
    import re

    problems: list[str] = []
    for name in mod.KEEP_ROOT_MD:
        path = staging / name
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        for target in re.findall(r"!\[[^\]]*\]\(([^)\s]+)", text):
            if target.startswith(("http://", "https://", "#", "data:")):
                continue
            if (REPO / target).exists() and not (staging / target).exists():
                problems.append(f"{name} 图片引用悬空：{target}")
        if name != "使用说明.md":
            continue
        table = text.split("## 附：相关文档", 1)[-1]
        for line in table.splitlines():
            if not line.startswith("|"):
                continue
            for ref in re.findall(r"`([^`]+?\.md)`", line):
                if not (REPO / ref).is_file() or (staging / ref).exists():
                    continue
                if "不随包" not in line and "开发仓参考" not in line:
                    problems.append(f"{name} 文档表条目悬空：{ref}（需标注不随包/开发仓参考）")
    return problems


def test_kept_docs_have_no_references_to_pruned_files(tmp_path, monkeypatch):
    mod = _real_release(tmp_path, monkeypatch)
    staging = tmp_path / "staging"
    staging.mkdir()
    import shutil

    for name in mod.KEEP_ROOT_MD:
        if (REPO / name).is_file():
            shutil.copy2(REPO / name, staging / name)
    # 数据模板/README.md 随包（不在根目录 KEEP_ROOT_MD 内），补齐后再检查文档表引用
    template_readme = REPO / "数据模板" / "README.md"
    if template_readme.is_file():
        (staging / "数据模板").mkdir(parents=True, exist_ok=True)
        shutil.copy2(template_readme, staging / "数据模板" / "README.md")
    mod.prune_internal(staging)
    problems = _doc_reference_problems(mod, staging)
    assert not problems, "\n".join(problems)
