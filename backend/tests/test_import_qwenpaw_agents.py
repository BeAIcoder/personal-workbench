"""import_qwenpaw_agents 脚本的静态校验：不依赖本机是否安装 QwenPaw。"""
import importlib.util
import re
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "import_qwenpaw_agents.py"

spec = importlib.util.spec_from_file_location("import_qwenpaw_agents", SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def test_agent_names_follow_pattern():
    pattern = re.compile(r"^[a-z][a-z0-9_]*$")
    for name in mod.MERGE_MAP:
        assert pattern.match(name), name
    for name in mod.NEW_AGENTS:
        assert pattern.match(name), name


def test_new_agent_tuples_shape():
    for name, cfg in mod.NEW_AGENTS.items():
        src_ws, label, emoji, color, keywords, sort = cfg
        assert src_ws and label and emoji and color
        assert 0 < len(keywords) <= 50 and all(k.strip() for k in keywords)
        assert 0 <= sort <= 9999
        assert name not in mod.MERGE_MAP


def test_sanitize_masks_paths_and_nickname():
    text = "参考 D:/AI_Service/QwenPaw/data 与 D:\\x\\y，老头子注意安全。"
    out = mod.sanitize(text)
    assert "D:" not in out
    assert "老头子" not in out
    assert "用户" in out


def test_strip_imported_is_idempotent():
    original = "你是内置专家。"
    with_marker = original + "\n\n" + mod.MARKER + "导入内容"
    assert mod.strip_imported(with_marker) == original
    assert mod.strip_imported(original) == original
