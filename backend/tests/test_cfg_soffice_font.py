"""分项E：LibreOffice 路径与 PDF 字体配置化的聚焦测试。"""
from pathlib import Path

import pytest

from app import document_text
from app.config import settings
from app.modules.finance_tax.services import tax_form_generator


def test_find_soffice_hits_configured_path(tmp_path, monkeypatch):
    """settings.soffice_path 指向存在的文件时优先命中。"""
    fake = tmp_path / "soffice.exe"
    fake.write_text("fake", encoding="utf-8")
    monkeypatch.delenv("SOFFICE_PATH", raising=False)
    monkeypatch.setattr(settings, "soffice_path", str(fake))
    assert document_text._find_soffice() == str(fake)


def test_find_soffice_empty_config_keeps_fallback(tmp_path, monkeypatch):
    """配置为空时行为不变：跳过配置项走原有候选链。"""
    monkeypatch.delenv("SOFFICE_PATH", raising=False)
    monkeypatch.setattr(settings, "soffice_path", "")
    fake_which = tmp_path / "soffice"
    fake_which.write_text("fake", encoding="utf-8")
    monkeypatch.setattr(document_text.shutil, "which", lambda name: str(fake_which) if name == "soffice" else None)
    assert document_text._find_soffice() == str(fake_which)


def test_find_soffice_missing_configured_file_falls_through(tmp_path, monkeypatch):
    """配置的文件不存在时不命中，继续走后续候选。"""
    monkeypatch.delenv("SOFFICE_PATH", raising=False)
    monkeypatch.setattr(settings, "soffice_path", str(tmp_path / "not-exist.exe"))
    fake_which = tmp_path / "soffice"
    fake_which.write_text("fake", encoding="utf-8")
    monkeypatch.setattr(document_text.shutil, "which", lambda name: str(fake_which) if name == "soffice" else None)
    assert document_text._find_soffice() == str(fake_which)


class _DummyTTFont:
    """拦截 TTFont 构造，避免对假字体文件做真实解析。"""

    def __init__(self, name, filename):
        self.name = name
        self.filename = filename


@pytest.fixture
def font_recorder(monkeypatch):
    """拦截 registerFont 与 TTFont，返回注册调用记录列表。"""
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase import ttfonts

    calls = []
    monkeypatch.setattr(pdfmetrics, "registerFont", lambda font: calls.append(font))
    monkeypatch.setattr(ttfonts, "TTFont", _DummyTTFont)
    return calls


def test_register_fonts_custom_font_first(tmp_path, monkeypatch, font_recorder):
    """配置字体存在时第一个被尝试注册。"""
    fake_font = tmp_path / "custom.ttf"
    fake_font.write_text("fake-font", encoding="utf-8")
    monkeypatch.setattr(settings, "pdf_font_path", str(fake_font))

    tax_form_generator._register_chinese_fonts()

    ttfonts_registered = [f for f in font_recorder if isinstance(f, _DummyTTFont)]
    assert ttfonts_registered, "应至少尝试注册一个 TTFont"
    assert ttfonts_registered[0].name == "CustomFont"
    assert ttfonts_registered[0].filename == str(fake_font)


def test_register_fonts_missing_custom_font_skipped(tmp_path, monkeypatch, font_recorder):
    """配置字体文件不存在时不插入候选，不注册 CustomFont。"""
    monkeypatch.setattr(settings, "pdf_font_path", str(tmp_path / "missing.ttf"))

    tax_form_generator._register_chinese_fonts()

    assert not any(isinstance(f, _DummyTTFont) and f.name == "CustomFont" for f in font_recorder)


def test_register_fonts_empty_config_unchanged(monkeypatch, font_recorder):
    """配置为空时行为不变：不出现 CustomFont。"""
    monkeypatch.setattr(settings, "pdf_font_path", "")

    tax_form_generator._register_chinese_fonts()

    assert not any(isinstance(f, _DummyTTFont) and f.name == "CustomFont" for f in font_recorder)
