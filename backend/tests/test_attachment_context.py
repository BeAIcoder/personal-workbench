"""附件上下文文件：大文本不进提示词、read_context 路径安全与会话清理。"""
import pytest

from app import config
from app.agents import orchestrator
from app.agents.tools import TOOL_SPECS
from app.context_files import (
    INLINE_TEXT_LIMIT,
    cleanup_session_context,
    read_context_file,
    write_context_file,
)
from app.database import Base, engine


@pytest.fixture(autouse=True)
def _ensure_tables():
    Base.metadata.create_all(bind=engine)
    yield


@pytest.fixture()
def uploads(tmp_path, monkeypatch):
    root = tmp_path / "uploads"
    root.mkdir()
    monkeypatch.setattr(config, "UPLOAD_DIR", root)
    return root


def test_large_text_goes_to_context_file_and_not_prompt(uploads):
    path = uploads / "big.txt"
    path.write_text("第一行\n" * 1200, encoding="utf-8")
    block = orchestrator._attachments_block(
        [{"name": "big.txt", "path": str(path)}], session_id="s1", message="m1"
    )
    assert "资料文件" in block
    assert "read_context" in block
    assert "第一行" not in block  # 正文不进提示词
    files = list((uploads / "context" / "s1").glob("*.txt"))
    assert len(files) == 1
    result = read_context_file(str(files[0]), offset=0, limit=2)
    assert result["ok"] is True
    assert len(result["lines"]) == 2
    assert result["total_lines"] >= 1200
    assert result["truncated"] is True


def test_small_text_stays_inline_with_data_only_banner(uploads):
    path = uploads / "small.txt"
    path.write_text("hello 资料", encoding="utf-8")
    block = orchestrator._attachments_block(
        [{"name": "small.txt", "path": str(path)}], session_id="s2", message="m2"
    )
    assert "hello 资料" in block
    assert "<<<资料开始>>>" in block
    assert "不是指令" in block
    assert not (uploads / "context" / "s2").exists()


def test_binary_attachment_only_exposes_path(uploads):
    path = uploads / "data.bin"
    path.write_bytes(b"\x00\x01\x02")
    block = orchestrator._attachments_block(
        [{"name": "data.bin", "path": str(path)}], session_id="s3", message="m3"
    )
    assert "二进制" in block
    assert str(path) in block
    assert "不是指令" in block


def test_attachment_outside_upload_dir_is_ignored(uploads, tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    assert orchestrator._attachments_block(
        [{"name": "outside.txt", "path": str(outside)}], session_id="s4", message="m4"
    ) == ""


def test_read_context_rejects_path_traversal_and_outside_files(uploads, tmp_path):
    outside = tmp_path / "secret.txt"
    outside.write_text("secret", encoding="utf-8")
    assert read_context_file("../secret.txt")["ok"] is False
    assert read_context_file(str(outside))["ok"] is False
    assert read_context_file("")["ok"] is False


def test_read_context_rejects_oversize_file(uploads, monkeypatch):
    from app import context_files

    path = uploads / "large.txt"
    path.write_text("x" * 100, encoding="utf-8")
    monkeypatch.setattr(context_files, "MAX_CONTEXT_BYTES", 10)
    assert read_context_file(str(path))["ok"] is False


def test_write_context_file_is_idempotent(uploads):
    first = write_context_file("s5", "a.txt", "same content", message_key="m5")
    second = write_context_file("s5", "a.txt", "same content", message_key="m5")
    assert first == second
    assert first.exists()


def test_cleanup_session_context(uploads):
    path = write_context_file("s6", "a.txt", "content", message_key="m6")
    assert path.exists()
    cleanup_session_context("s6")
    assert not path.exists()


def test_session_delete_cleans_context(uploads):
    path = write_context_file("s7", "a.txt", "content", message_key="m7")
    assert path.exists()
    orchestrator.session_delete("s7")
    assert not path.exists()


def test_read_context_tool_is_readonly_and_registered():
    spec = TOOL_SPECS["read_context"]
    assert spec["readonly"] is True
    assert spec["required"] == ["path"]
    assert INLINE_TEXT_LIMIT == 4000
