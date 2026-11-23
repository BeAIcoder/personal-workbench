# -*- coding: utf-8 -*-
"""发布脚本编码契约：根目录与 dev/ 下的 bat/vbs 必须 CRLF + ANSI(GBK)。

AGENTS.md 硬约束：VBScript/.bat 必须 CRLF + ANSI（GBK）；MSYS 工具会剥 CR，
LF 的 bat 在 cmd 括号块会错乱；UTF-8 BOM/编码会让中文提示乱码。
本用例只读字节，不执行任何脚本。
"""
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


def _scripts() -> list[Path]:
    files = sorted(REPO.glob("*.bat")) + sorted(REPO.glob("*.vbs"))
    files += sorted((REPO / "dev").glob("*.bat")) + sorted((REPO / "dev").glob("*.vbs"))
    return files


def _script_ids(path: Path) -> str:
    return path.relative_to(REPO).as_posix()


def test_discovers_root_and_dev_scripts():
    names = {_script_ids(path) for path in _scripts()}
    assert {"启动工作台.bat", "开机自启动.vbs", "dev/install_filter.bat"} <= names, names
    assert any(name.startswith("dev/") for name in names), "未纳入 dev/ 脚本"


@pytest.mark.parametrize("path", _scripts(), ids=_script_ids)
def test_script_is_crlf_without_bare_line_endings(path):
    raw = path.read_bytes()
    assert raw.count(b"\r\n") > 0, "没有 CRLF 换行"
    assert b"\n" not in raw.replace(b"\r\n", b""), "含裸 LF（cmd 解析括号块会错乱）"
    assert b"\r" not in raw.replace(b"\r\n", b""), "含裸 CR"


@pytest.mark.parametrize("path", _scripts(), ids=_script_ids)
def test_script_is_ansi_gbk_without_utf8_bom(path):
    raw = path.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf"), "带 UTF-8 BOM，cmd/cscript 会误读首行"
    text = raw.decode("gbk")
    assert text.strip(), "脚本为空"


def test_gitattributes_covers_dev_scripts():
    proc = subprocess.run(
        ["git", "check-attr", "eol", "--", "dev/install_filter.bat", "dev/install_hooks.bat"],
        cwd=REPO, capture_output=True, text=True, encoding="utf-8",
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.count("eol: crlf") == 2, proc.stdout
